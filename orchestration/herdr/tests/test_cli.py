"""Argument parsing and locally authored recovery records."""

import contextlib
import io
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from herdr_launch import checks, cli, client, process  # noqa: E402
from herdr_launch.errors import HerdrFailure, LaunchUnavailable  # noqa: E402


BASE = ["--cwd", "/tmp/repo", "--name", "worker", "--kind", "pi"]


class ParseArgsTests(unittest.TestCase):
    def parse_failure(self, argv):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                cli.parse_args(argv)
        self.assertEqual(caught.exception.code, 2)

    def test_defaults_and_native_separator(self):
        argv = [*BASE, "--", "--model", "astra", "--", "--timeout", "1"]
        original = list(argv)
        args = cli.parse_args(argv)
        self.assertEqual(args.cwd, "/tmp/repo")
        self.assertEqual(args.name, "worker")
        self.assertEqual(args.kind, "pi")
        self.assertEqual(args.timeout, 30000)
        self.assertIsNone(args.wait_timeout)
        self.assertIsNone(args.prompt)
        self.assertIsNone(args.prompt_file)
        self.assertEqual(args.agent_args,
                         ["--model", "astra", "--", "--timeout", "1"])
        self.assertEqual(argv, original)

    def test_expectation_defaults_and_native_lists_have_no_shared_state(self):
        first = cli.parse_args(BASE)
        first.expect_startup.append("model")
        first.agent_args.append("--model")
        second = cli.parse_args(BASE)
        self.assertEqual(second.expect_startup, [])
        self.assertEqual(second.agent_args, [])
        self.assertEqual(cli.parse_args([*BASE, "--expect-startup", "Model",
                                         "--expect-startup", "thinking: high"])
                         .expect_startup, ["Model", "thinking: high"])

    def test_abbreviations_and_unknown_flags_are_rejected(self):
        for flag in ("--time", "--expect-start", "--wait-time", "--unknown"):
            with self.subTest(flag=flag):
                self.parse_failure([*BASE, flag, "1"])

    def test_required_fields_exclusive_names_and_prompt_sources(self):
        for argv in ([], BASE[:4], [*BASE, "--name-prefix", "worker"],
                     [*BASE, "--prompt", "task", "--prompt-file", "task.txt"],
                     [*BASE, "--timeout", "not-an-integer"]):
            with self.subTest(argv=argv):
                self.parse_failure(argv)
        args = cli.parse_args(["--cwd", "/tmp/repo", "--name-prefix", "review",
                               "--kind", "pi", "--prompt-file", "task.txt",
                               "--wait-timeout", "6000", "--timeout", "4000"])
        self.assertIsNone(args.name)
        self.assertEqual(args.name_prefix, "review")
        self.assertEqual(args.prompt_file, "task.txt")
        self.assertEqual(args.wait_timeout, 6000)
        self.assertEqual(args.timeout, 4000)


class ErrorRecordTests(unittest.TestCase):
    def launcher(self, mutated=False, name="worker"):
        return SimpleNamespace(client=SimpleNamespace(mutation_attempted=mutated),
                               stage="start", resources={"pane_id": "p1"}, name=name)

    def test_preflight_error_advises_resolution_without_inspection(self):
        record = cli.error_record(self.launcher(), LaunchUnavailable("Invalid input."))
        self.assertEqual(record, {
            "status": "error", "stage": "start", "code": "launch_unavailable",
            "herdr_code": None, "reason": "Invalid input.",
            "resources": {"pane_id": "p1"}, "requested_name": "worker",
            "mutation_may_have_taken_effect": False,
            "next": "No mutations were attempted. Resolve this preflight "
                    "issue before trying again."})

    def test_name_collision_advises_new_name_without_recovery_inspection(self):
        record = cli.error_record(self.launcher(), LaunchUnavailable(
            "Agent name already exists.", "name_exists"))
        self.assertEqual(record["next"], "No mutations were attempted. Choose "
                         "--name-prefix or a different --name; no recovery "
                         "inspection is needed.")

    def test_mutation_overrides_collision_guidance_and_keeps_known_resources(self):
        record = cli.error_record(self.launcher(mutated=True), LaunchUnavailable(
            "Agent name already exists.", "name_exists"))
        self.assertTrue(record["mutation_may_have_taken_effect"])
        self.assertEqual(record["resources"], {"pane_id": "p1"})
        self.assertEqual(record["next"], "Do not relaunch blindly. Inspect known "
                         "resources; nothing was cleaned up.")

    def test_signal_and_interrupt_records_use_local_reasons(self):
        for error, code, reason in (
                (cli.Terminated(), "terminated", "Terminated by signal."),
                (KeyboardInterrupt("PRIVATE_PAYLOAD"), "interrupted", "Interrupted.")):
            with self.subTest(code=code):
                record = cli.error_record(self.launcher(), error)
                self.assertEqual(record["code"], code)
                self.assertEqual(record["reason"], reason)
                self.assertNotIn("PRIVATE_PAYLOAD", repr(record))

    def test_herdr_code_is_preserved_and_invalid_name_is_not_echoed(self):
        record = cli.error_record(self.launcher(name="invalid PRIVATE_PAYLOAD"),
                                  HerdrFailure("timeout"))
        self.assertEqual(record["code"], "herdr_error")
        self.assertEqual(record["herdr_code"], "timeout")
        self.assertIsNone(record["requested_name"])
        self.assertNotIn("PRIVATE_PAYLOAD", repr(record))

    def test_termination_handler_raises_for_launcher_cleanup(self):
        with self.assertRaises(cli.Terminated):
            cli.terminate(None, None)


class ReviewRunbookTests(unittest.TestCase):
    def launch_args(self, reviewer):
        path = SCRIPTS.parents[1] / reviewer / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        command = text.split('   python3 "$herdr_dir/scripts/launch-agent.py"', 1)[1]
        command = command.split("```", 1)[0].replace("\\\n", " ")
        return cli.parse_args(shlex.split(command))

    def test_astra_runbook_requires_high_thinking_before_submission(self):
        args = self.launch_args("astra-review")
        self.assertEqual(args.agent_args[:4],
                         ["--model", "openai-codex/gpt-6-astra", "--thinking", "high"])
        self.assertIn("gpt-6-astra", args.expect_startup)
        self.assertIn("thinking: high", args.expect_startup)
        for text, blocked in (("gpt-6-astra • thinking: high", None),
                              ("gpt-6-astra • thinking: low", "startup_unexpected"),
                              ("gpt-6-astra", "startup_unexpected")):
            with self.subTest(text=text):
                self.assertEqual(checks.submission_block(
                    text, False, args.expect_startup), blocked)

    def test_initial_review_waits_fit_full_default_launch_budget(self):
        # Include server/list/create/read and an optional agent-get, a busy
        # shell retry, answer extraction, and both reader-thread joins per call.
        reader_cleanup = 2 * process.REAP_TIMEOUT
        command_budget = client.COMMAND_TIMEOUT + reader_cleanup
        for reviewer in ("astra-review", "fable-review"):
            with self.subTest(reviewer=reviewer):
                args = self.launch_args(reviewer)
                checks.validate_args(args)
                total = (5 * command_budget + args.timeout / 1000
                         + command_budget + client.SHELL_READY_TIMEOUT
                         + args.wait_timeout / 1000 + command_budget
                         + command_budget)
                self.assertLessEqual(total, 575)


if __name__ == "__main__":
    unittest.main()
