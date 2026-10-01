"""Prompt hand-off contracts; all calls and answer extraction are stubbed."""

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from herdr_launch import submission  # noqa: E402
from herdr_launch.errors import HerdrFailure, LaunchUnavailable  # noqa: E402


class ResultTests(unittest.TestCase):
    def test_startup_fields_preserve_text_and_report_blankness(self):
        self.assertEqual(submission.startup_fields(" \n", True), {
            "startup": " \n", "startup_truncated": True, "startup_empty": True})
        self.assertEqual(submission.startup_fields("Ready\n", False), {
            "startup": "Ready\n", "startup_truncated": False,
            "startup_empty": False})

    def test_blocked_result_requires_inspection_and_never_claims_submission(self):
        self.assertEqual(submission.blocked_result("startup_unsafe", "Ready", False),
                         {"submitted": False, "submission_blocked": "startup_unsafe",
                          "startup": "Ready", "startup_truncated": False,
                          "startup_empty": False, "inspection_required": True})

    def test_submitted_result_inspection_depends_on_state(self):
        for state in submission.AGENT_STATUSES:
            with self.subTest(state=state):
                self.assertEqual(submission.submitted_result(
                    state, None, waited=True, wait_timed_out=False), {
                        "submitted": True, "submission_blocked": None,
                        "startup_checked": True,
                        "inspection_required": state in ("blocked", "unknown"),
                        "state": state, "session": None, "waited": True,
                        "wait_timed_out": False})

    def test_read_startup_uses_bounded_recent_unwrapped_text(self):
        client = Mock()
        client.text_command.return_value = ("é" * 8001, False)
        self.assertEqual(submission.read_startup(client, "worker"),
                         ("é" * 8000, True))
        client.text_command.assert_called_once_with(
            ["agent", "read", "worker", "--source", "recent-unwrapped",
             "--lines", "80", "--format", "text"], max_bytes=32000)


class SubmitPromptTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.json_command.return_value = {}
        self.session = {"agent": "pi", "kind": "path", "value": "/tmp/old.jsonl"}
        self.agent = {"agent_status": "idle", "agent_session": self.session}
        extraction = patch.object(submission, "extract_answer", return_value="Answer")
        self.extract = extraction.start()
        self.addCleanup(extraction.stop)

    def submit(self, wait=None):
        result = submission.submit_prompt(
            self.client, name="worker", kind="pi", prompt="PRIVATE_PAYLOAD",
            wait_timeout=wait, agent=self.agent)
        self.assertNotIn("PRIVATE_PAYLOAD", repr(result))
        return result

    def test_no_wait_argv_timeout_and_no_answer_extraction(self):
        result = self.submit()
        self.client.json_command.assert_called_once_with(
            ["agent", "prompt", "worker", "PRIVATE_PAYLOAD"], "agent_prompted",
            mutation=True, timeout=20)
        self.assertFalse(result["waited"])
        self.assertFalse(result["wait_timed_out"])
        self.assertEqual(result["session"], self.session)
        self.assertNotIn("response", result)
        self.extract.assert_not_called()

    def test_wait_argv_and_timeout_arithmetic(self):
        result = self.submit(5001)
        self.client.json_command.assert_called_once_with(
            ["agent", "prompt", "worker", "PRIVATE_PAYLOAD", "--wait",
             "--timeout", "5001"], "agent_prompted", mutation=True, timeout=25.001)
        self.assertTrue(result["waited"])
        self.assertEqual(result["response"], "Answer")

    def test_wait_timeout_above_stall_window_reports_working(self):
        self.client.json_command.side_effect = HerdrFailure("timeout")
        result = self.submit(5001)
        self.assertEqual(result["state"], "working")
        self.assertEqual(result["session"], self.session)
        self.assertTrue(result["waited"])
        self.assertTrue(result["wait_timed_out"])
        self.assertFalse(result["inspection_required"])
        self.assertNotIn("response", result)
        self.extract.assert_not_called()
        self.client.json_command.assert_called_once()

    def test_short_wait_or_non_timeout_failure_is_never_reclassified(self):
        cases = [(None, HerdrFailure("timeout")),
                 (5000, HerdrFailure("timeout")),
                 (5001, HerdrFailure("agent_stalled")),
                 (5001, LaunchUnavailable("Herdr command timed out."))]
        for wait, error in cases:
            with self.subTest(wait=wait, error=error):
                self.client.reset_mock()
                self.client.json_command.side_effect = error
                with self.assertRaises(type(error)) as caught:
                    self.submit(wait)
                self.assertIs(caught.exception, error)
                self.assertNotIn("PRIVATE_PAYLOAD", str(caught.exception))
                self.client.json_command.assert_called_once()
        self.extract.assert_not_called()

    def test_prompted_agent_overrides_initial_state_and_session(self):
        new_session = {**self.session, "value": "/tmp/new.jsonl"}
        self.client.json_command.return_value = {"agent": {
            "agent_status": "done", "agent_session": new_session}}
        result = self.submit(6000)
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["session"], new_session)
        self.extract.assert_called_once_with(new_session, "pi")

    def test_invalid_prompted_session_falls_back_to_started_agent(self):
        self.client.json_command.return_value = {"agent": {
            "agent_status": "done", "agent_session": "PRIVATE_PAYLOAD"}}
        self.assertEqual(self.submit(6000)["session"], self.session)
        self.extract.assert_called_once_with(self.session, "pi")

    def test_unrecognized_prompted_agent_does_not_override_known_evidence(self):
        for agent in (None, "PRIVATE_PAYLOAD", {"agent_status": "new-state"}):
            with self.subTest(agent=agent):
                self.client.json_command.return_value = {"agent": agent}
                result = self.submit()
                self.assertEqual(result["state"], "idle")
                self.assertEqual(result["session"], self.session)

    def test_response_is_only_extracted_for_waited_settled_states(self):
        for state in submission.AGENT_STATUSES:
            for wait in (None, 6000):
                with self.subTest(state=state, wait=wait):
                    self.extract.reset_mock()
                    self.client.json_command.return_value = {
                        "agent": {"agent_status": state}}
                    result = self.submit(wait)
                    settled = wait is not None and state in ("idle", "done")
                    self.assertEqual("response" in result, settled)
                    self.assertEqual(self.extract.call_count, int(settled))

    def test_missing_extracted_answer_does_not_add_response(self):
        self.extract.return_value = None
        result = self.submit(6000)
        self.assertNotIn("response", result)
        self.assertEqual(result["state"], "idle")


if __name__ == "__main__":
    unittest.main()
