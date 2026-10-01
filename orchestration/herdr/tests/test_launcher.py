"""Focused launch-stage evidence tests; the client never invokes Herdr."""

import argparse
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from herdr_launch import launcher  # noqa: E402
from herdr_launch.client import HerdrClient  # noqa: E402
from herdr_launch.errors import LaunchUnavailable  # noqa: E402


class LauncherTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cwd = Path(temp.name).resolve()
        self.client = Mock(spec=HerdrClient)
        args = argparse.Namespace(name="worker", name_prefix=None, kind="pi",
                                  workspace="w1", timeout=30000, agent_args=[])
        self.launcher = launcher.Launcher(args, self.client)
        self.launcher.cwd = self.cwd
        self.ids = {"workspace_id": "w1", "tab_id": "t1", "pane_id": "p1"}
        self.agent = {**self.ids, "name": "worker", "agent": "pi",
                      "foreground_cwd": str(self.cwd), "interactive_ready": True,
                      "agent_status": "idle"}

    def assert_failure(self, method, message, *args):
        with self.assertRaisesRegex(LaunchUnavailable, message) as caught:
            method(*args)
        self.assertNotIn("PRIVATE_PAYLOAD", str(caught.exception))

    def test_verify_agent_accepts_complete_idle_or_done_evidence(self):
        self.launcher.resources.update(self.ids)
        for state in ("idle", "done"):
            with self.subTest(state=state):
                self.assertTrue(self.launcher.verify_agent({
                    **self.agent, "agent_status": state, "extra": "PRIVATE_PAYLOAD"}))

    def test_verify_agent_missing_fields_requests_more_evidence(self):
        self.launcher.resources.update(self.ids)
        for key in self.agent:
            with self.subTest(key=key):
                self.assertFalse(self.launcher.verify_agent({
                    field: value for field, value in self.agent.items() if field != key}))
        self.assertFalse(self.launcher.verify_agent({}))

    def test_verify_agent_contradictions_are_not_treated_as_missing(self):
        self.launcher.resources.update(self.ids)
        for key in ("name", "agent", "workspace_id", "tab_id", "pane_id"):
            with self.subTest(key=key):
                self.assert_failure(self.launcher.verify_agent, "mismatch",
                                    {**self.agent, key: "PRIVATE_PAYLOAD"})
        for value in (False, 1, None):
            with self.subTest(ready=value):
                self.assert_failure(self.launcher.verify_agent, "mismatch",
                                    {**self.agent, "interactive_ready": value})

    def test_verify_agent_rejects_nonobjects_wrong_cwd_and_unsettled_states(self):
        for value in (None, [], "PRIVATE_PAYLOAD"):
            self.assert_failure(self.launcher.verify_agent, "Unsupported", value)
        self.assert_failure(self.launcher.verify_agent, "unexpected directory",
                            {**self.agent, "foreground_cwd": str(self.cwd / "other")})
        for state in ("working", "blocked", "unknown", "PRIVATE_PAYLOAD"):
            with self.subTest(state=state):
                self.assert_failure(self.launcher.verify_agent, "idle state",
                                    {**self.agent, "agent_status": state})

    def test_create_new_workspace_uses_returned_root_identities(self):
        self.client.json_command.return_value = {"root_pane": self.ids}
        self.launcher.create(None)
        self.assertEqual(self.launcher.resources, self.ids)
        self.assertEqual(self.launcher.stage, "create")
        self.client.json_command.assert_called_once_with(
            ["workspace", "create", "--cwd", str(self.cwd), "--label", "worker",
             "--no-focus"], "workspace_created", mutation=True)

    def test_create_tab_preserves_requested_workspace(self):
        self.client.json_command.return_value = {"root_pane": self.ids}
        self.launcher.create("w1")
        self.assertEqual(self.launcher.resources, self.ids)
        self.client.json_command.assert_called_once_with(
            ["tab", "create", "--workspace", "w1", "--cwd", str(self.cwd),
             "--label", "worker", "--no-focus"], "tab_created", mutation=True)

    def test_create_conflicting_parent_and_root_ids_are_rejected(self):
        for field, key in (("workspace", "workspace_id"), ("tab", "tab_id")):
            with self.subTest(field=field):
                self.launcher.resources.clear()
                self.client.json_command.return_value = {
                    field: {key: "parent-id"}, "root_pane": self.ids}
                self.assert_failure(self.launcher.create, "conflicting IDs", None)
                self.assertEqual(self.launcher.resources[key], "parent-id")

    def test_create_cannot_replace_explicit_workspace_id(self):
        for result, message in (({"workspace": {"workspace_id": "other"}},
                                 "different workspace"),
                                ({"root_pane": {**self.ids, "workspace_id": "other"}},
                                 "conflicting IDs")):
            with self.subTest(result=result):
                self.launcher.resources.clear()
                self.client.json_command.return_value = result
                self.assert_failure(self.launcher.create, message, "w1")
                self.assertEqual(self.launcher.resources["workspace_id"], "w1")

    def test_create_retains_valid_ids_when_other_identities_are_missing(self):
        self.client.json_command.return_value = {
            "workspace": {"workspace_id": "w1"}, "tab": {"tab_id": "t1"}}
        self.assert_failure(self.launcher.create, "root pane", None)
        self.assertEqual(self.launcher.resources, {"workspace_id": "w1", "tab_id": "t1"})
        self.launcher.resources.clear()
        self.client.json_command.return_value = {"root_pane": {
            "workspace_id": "w1", "tab_id": "invalid PRIVATE_PAYLOAD", "pane_id": "p1"}}
        self.assert_failure(self.launcher.create, "incomplete", None)
        self.assertEqual(self.launcher.resources, {"workspace_id": "w1", "pane_id": "p1"})
        self.assertNotIn("PRIVATE_PAYLOAD", repr(self.launcher.resources))

    def test_workspace_matches_live_foreground_cwd_without_global_discovery(self):
        self.client.json_command.return_value = {"panes": [
            {"workspace_id": "w1", "foreground_cwd": str(self.cwd)}]}
        self.assertEqual(self.launcher.workspace(), "w1")
        self.assertEqual(self.launcher.stage, "workspace")
        self.client.json_command.assert_called_once_with(
            ["pane", "list", "--workspace", "w1"], "pane_list")

    def test_workspace_empty_or_unknown_evidence_is_ambiguous(self):
        for panes in ([], [{"workspace_id": "w1"}],
                      [{"workspace_id": "w1", "foreground_cwd": "relative"}]):
            with self.subTest(panes=panes):
                self.client.json_command.return_value = {"panes": panes}
                self.assert_failure(self.launcher.workspace, "ambiguous")

    def test_workspace_known_nonmatch_and_contradictory_ids_are_rejected(self):
        self.client.json_command.return_value = {"panes": [
            {"workspace_id": "w1", "foreground_cwd": str(self.cwd / "other")}]}
        self.assert_failure(self.launcher.workspace, "does not match")
        for workspace in (None, "other"):
            self.client.json_command.return_value = {"panes": [
                {"workspace_id": workspace, "foreground_cwd": str(self.cwd)}]}
            self.assert_failure(self.launcher.workspace, "unexpected workspace")

    def test_workspace_match_can_coexist_with_unknown_or_nonmatching_panes(self):
        self.client.json_command.return_value = {"panes": [
            {"workspace_id": "w1"},
            {"workspace_id": "w1", "foreground_cwd": str(self.cwd / "other")},
            {"workspace_id": "w1", "foreground_cwd": str(self.cwd)}]}
        self.assertEqual(self.launcher.workspace(), "w1")

    def test_start_forwards_native_args_and_timeout_to_same_pane(self):
        self.launcher.resources.update(self.ids)
        self.launcher.args.agent_args = ["--thinking", "high"]
        self.client.start_when_ready.return_value = {"agent": self.agent}
        self.assertEqual(self.launcher.start(), self.agent)
        self.client.start_when_ready.assert_called_once_with(
            ["agent", "start", "worker", "--kind", "pi", "--pane", "p1",
             "--timeout", "30000", "--", "--thinking", "high"], timeout=50)
        self.client.json_command.assert_not_called()

    def test_server_requires_true_running_compatible_and_correct_session(self):
        status = {"running": True, "compatible": True, "session": "Subagents"}
        self.client.json_command.return_value = status
        self.launcher.check_server()
        self.assertEqual(self.launcher.stage, "server")
        for field, value in (("running", 1), ("compatible", False),
                             ("session", "PRIVATE_PAYLOAD")):
            with self.subTest(field=field):
                self.client.json_command.return_value = {**status, field: value}
                self.assert_failure(self.launcher.check_server, "unavailable")

    def test_choose_name_rejects_unusable_registry_before_creation(self):
        for name in (None, "", 1):
            with self.subTest(name=name):
                self.client.json_command.return_value = {"agents": [{"name": name}]}
                self.assert_failure(self.launcher.choose_name, "names")
        self.assertEqual(self.launcher.stage, "name")


if __name__ == "__main__":
    unittest.main()
