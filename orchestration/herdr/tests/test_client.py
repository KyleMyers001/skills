"""Herdr transport contracts with fake results and deterministic clocks."""

import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, call, patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from herdr_launch import client  # noqa: E402
from herdr_launch.errors import HerdrFailure, LaunchUnavailable  # noqa: E402
from herdr_launch.process import CommandResult, JSON_STDOUT_LIMIT  # noqa: E402


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = client.HerdrClient()
        self.run = Mock(return_value=CommandResult(0, '{"ok": true}', ""))
        replacement = patch.object(client, "run_bounded", self.run)
        replacement.start()
        self.addCleanup(replacement.stop)

    def assert_failure(self, result, message, error=LaunchUnavailable):
        self.run.return_value = result
        with self.assertRaisesRegex(error, message) as caught:
            self.client.json_command(["status"], "agent_list")
        self.assertNotIn("PRIVATE_PAYLOAD", str(caught.exception))
        return caught.exception

    def test_json_command_scopes_session_and_timeout(self):
        self.assertEqual(self.client.json_command(["status"], timeout=7),
                         {"ok": True})
        self.run.assert_called_once_with(
            ["herdr", "--session", "Subagents", "status"],
            timeout=7, max_stdout=JSON_STDOUT_LIMIT)
        self.assertFalse(self.client.mutation_attempted)

    def test_typed_json_command_returns_payload(self):
        self.run.return_value = CommandResult(0, json.dumps({"result": {
            "type": "agent_list", "agents": []}}), "")
        self.assertEqual(self.client.json_command(["agent", "list"],
                                                 "agent_list"),
                         {"type": "agent_list", "agents": []})

    def test_mutation_flag_is_set_before_a_failing_call(self):
        for error in (OSError, ValueError, subprocess.TimeoutExpired):
            self.client = client.HerdrClient()

            def fail(*args, **kwargs):
                self.assertTrue(self.client.mutation_attempted)
                if error is subprocess.TimeoutExpired:
                    raise error(["PRIVATE_PAYLOAD"], 1)
                raise error("PRIVATE_PAYLOAD")

            with self.subTest(error=error):
                self.run.side_effect = fail
                with self.assertRaises(LaunchUnavailable) as caught:
                    self.client.json_command(["workspace", "create"],
                                             mutation=True)
                self.assertNotIn("PRIVATE_PAYLOAD", str(caught.exception))
                self.assertTrue(self.client.mutation_attempted)
        self.run.side_effect = None
        self.client.json_command(["status"])
        self.assertTrue(self.client.mutation_attempted)

    def test_nonmutation_failure_does_not_mark_mutation(self):
        self.run.side_effect = OSError("PRIVATE_PAYLOAD")
        with self.assertRaises(LaunchUnavailable):
            self.client.json_command(["status"])
        self.assertFalse(self.client.mutation_attempted)

    def test_json_oversize_precedes_exit_or_parsing_errors(self):
        self.assert_failure(CommandResult(1, "PRIVATE_PAYLOAD", "", True),
                            "oversized")

    def test_nonzero_and_error_key_return_sanitized_herdr_failures(self):
        envelope = json.dumps({"error": {
            "code": "timeout", "message": "PRIVATE_PAYLOAD"}})
        for result in (CommandResult(1, "", envelope),
                       CommandResult(0, envelope, "")):
            with self.subTest(returncode=result.returncode):
                failure = self.assert_failure(result, "Herdr command failed",
                                              HerdrFailure)
                self.assertEqual(failure.herdr_code, "timeout")

    def test_unreadable_nondict_and_wrong_type_json(self):
        for stdout, message in (
                ("PRIVATE_PAYLOAD", "unreadable JSON"),
                ('["PRIVATE_PAYLOAD"]', "unsupported response"),
                ('{"result": null}', "unsupported response type"),
                ('{"result": {"type": "wrong"}}', "unsupported response type")):
            with self.subTest(stdout=stdout):
                self.assert_failure(CommandResult(0, stdout, ""), message)

    def test_text_command_returns_normal_text_and_byte_limit(self):
        self.run.return_value = CommandResult(0, "café\n", "")
        self.assertEqual(self.client.text_command(["read"], max_bytes=123,
                                                 timeout=9), ("café\n", False))
        self.run.assert_called_once_with(
            ["herdr", "--session", "Subagents", "read"],
            timeout=9, max_stdout=123)

    def test_text_capped_prefix_survives_reader_kill(self):
        self.run.return_value = CommandResult(-9, "prefix", "", True)
        self.assertEqual(self.client.text_command(["read"], max_bytes=6),
                         ("prefix", True))

    def test_empty_truncated_text_does_not_hide_failed_exit(self):
        self.run.return_value = CommandResult(-9, "", "PRIVATE_PAYLOAD", True)
        with self.assertRaises(HerdrFailure) as caught:
            self.client.text_command(["read"], max_bytes=6)
        self.assertNotIn("PRIVATE_PAYLOAD", str(caught.exception))

    def test_empty_successful_text_is_not_reported_as_capped_prefix(self):
        self.run.return_value = CommandResult(0, "", "", True)
        self.assertEqual(self.client.text_command(["read"], max_bytes=6),
                         ("", False))


class StartWhenReadyTests(unittest.TestCase):
    def setUp(self):
        self.client = client.HerdrClient()
        self.argv = ["agent", "start", "worker"]

    def test_pre_start_busy_retries_same_mutation_until_ready(self):
        busy = HerdrFailure("agent_pane_busy", pre_start_busy=True)
        with patch.object(self.client, "json_command",
                          side_effect=[busy, {"agent": {}}]) as command:
            with patch.object(client, "time") as clock:
                clock.monotonic.side_effect = [10, 10.01, 10.11]
                self.assertEqual(self.client.start_when_ready(self.argv, 50),
                                 {"agent": {}})
        self.assertEqual(command.call_count, 2)
        for invocation in command.call_args_list:
            self.assertEqual(invocation, call(
                self.argv, "agent_started", mutation=True, timeout=50))
        clock.sleep.assert_called_once_with(client.SHELL_POLL_INTERVAL)

    def test_unverified_busy_and_other_failures_never_retry(self):
        for error in (HerdrFailure("agent_pane_busy"), HerdrFailure("timeout"),
                      LaunchUnavailable("Transport failed.")):
            with self.subTest(error=error):
                with patch.object(self.client, "json_command",
                                  side_effect=error) as command:
                    with patch.object(client, "time") as clock:
                        clock.monotonic.return_value = 10
                        with self.assertRaises(type(error)) as caught:
                            self.client.start_when_ready(self.argv, 50)
                        self.assertIs(caught.exception, error)
                    command.assert_called_once()
                    clock.sleep.assert_not_called()

    def test_expired_deadline_prevents_retry(self):
        with patch.object(self.client, "json_command", side_effect=HerdrFailure(
                "agent_pane_busy", pre_start_busy=True)) as command:
            with patch.object(client, "time") as clock:
                clock.monotonic.side_effect = [10, 15]
                with self.assertRaises(HerdrFailure):
                    self.client.start_when_ready(self.argv, 50)
            command.assert_called_once()
            clock.sleep.assert_not_called()

    def test_final_poll_is_clamped_and_does_not_start_after_deadline(self):
        with patch.object(self.client, "json_command", side_effect=HerdrFailure(
                "agent_pane_busy", pre_start_busy=True)) as command:
            with patch.object(client, "time") as clock:
                clock.monotonic.side_effect = [10, 14.95, 15]
                with self.assertRaises(HerdrFailure):
                    self.client.start_when_ready(self.argv, 50)
            command.assert_called_once()
            clock.sleep.assert_called_once()
            self.assertAlmostEqual(clock.sleep.call_args.args[0], 0.05)


if __name__ == "__main__":
    unittest.main()
