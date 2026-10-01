"""Session evidence and transcript discovery without real agent state."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from herdr_launch import session  # noqa: E402
from herdr_launch.process import CommandResult  # noqa: E402


CLAUDE_ID = "12345678-abcd-1234-abcd-123456789abc"


class SessionMappingTests(unittest.TestCase):
    def test_mapping_filters_untrusted_extra_fields(self):
        for kind in ("path", "id"):
            with self.subTest(kind=kind):
                source = {"agent": "pi", "kind": kind, "value": "session",
                          "source": "PRIVATE_PAYLOAD"}
                mapped = session.session_mapping({"agent_session": source}, "pi")
                self.assertEqual(mapped, {"agent": "pi", "kind": kind,
                                          "value": "session"})
                self.assertNotIn("PRIVATE_PAYLOAD", repr(mapped))
                mapped["value"] = "changed"
                self.assertEqual(source["value"], "session")

    def test_missing_and_malformed_agent_session(self):
        for agent in (None, [], {}, {"agent_session": None},
                      {"agent_session": "PRIVATE_PAYLOAD"}):
            with self.subTest(agent=agent):
                self.assertIsNone(session.session_mapping(agent, "pi"))

    def test_mapping_value_limits_and_required_fields(self):
        base = {"agent": "pi", "kind": "path", "value": "x"}
        for value in ("x", "x" * 4096):
            self.assertIsNotNone(session.session_mapping(
                {"agent_session": {**base, "value": value}}, "pi"))
        for field, value in (("value", ""), ("value", "x" * 4097),
                             ("value", 123), ("kind", "url"),
                             ("agent", "claude")):
            with self.subTest(field=field, value=value):
                self.assertIsNone(session.session_mapping(
                    {"agent_session": {**base, field: value}}, "pi"))
        for field in base:
            incomplete = {key: value for key, value in base.items() if key != field}
            self.assertIsNone(session.session_mapping(
                {"agent_session": incomplete}, "pi"))


class TranscriptPathTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        environment = patch.dict(os.environ, {"HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)
        self.mapping = {"agent": "claude", "kind": "id", "value": CLAUDE_ID}

    def transcript(self, project):
        path = self.home / ".claude" / "projects" / project / f"{CLAUDE_ID}.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("{}\n", encoding="utf-8")
        return path

    def test_path_mapping_does_not_require_file_to_exist(self):
        path = self.home / "missing.jsonl"
        self.assertEqual(session.transcript_path({
            "agent": "pi", "kind": "path", "value": str(path)}), path)

    def test_claude_id_requires_exactly_one_matching_project(self):
        self.assertIsNone(session.transcript_path(self.mapping))
        first = self.transcript("project-one")
        self.assertEqual(session.transcript_path(self.mapping), first)
        self.transcript("project-two")
        self.assertIsNone(session.transcript_path(self.mapping))

    def test_invalid_or_nonclaude_ids_are_not_used_as_globs(self):
        self.transcript("project-one")
        for value in ("*", "../PRIVATE_PAYLOAD", CLAUDE_ID.upper(),
                      CLAUDE_ID + "\n", CLAUDE_ID[:-1], "not-a-uuid"):
            with self.subTest(value=value):
                with patch.object(Path, "glob") as glob:
                    self.assertIsNone(session.transcript_path(
                        {**self.mapping, "value": value}))
                    glob.assert_not_called()
        self.assertIsNone(session.transcript_path({**self.mapping, "agent": "pi"}))


class ExtractAnswerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "transcript.jsonl"
        self.path.write_text("{}\n", encoding="utf-8")
        self.mapping = {"agent": "pi", "kind": "path", "value": str(self.path)}
        replacement = patch.object(session, "run_bounded")
        self.run = replacement.start()
        self.addCleanup(replacement.stop)

    def test_extractor_command_and_answer(self):
        self.run.return_value = CommandResult(0, "Answer\n", "")
        self.assertEqual(session.extract_answer(self.mapping, "pi"), "Answer\n")
        self.run.assert_called_once_with(
            [sys.executable, str(session.EXTRACTOR), "--kind", "pi", str(self.path)],
            timeout=session.COMMAND_TIMEOUT)

    def test_unavailable_mapping_kind_or_file_does_not_run_extractor(self):
        cases = [(None, "pi"), (self.mapping, "custom"),
                 ({**self.mapping, "value": str(self.path.parent / "missing")}, "pi"),
                 ({"agent": "pi", "kind": "id", "value": "unknown"}, "pi")]
        for mapping, kind in cases:
            with self.subTest(mapping=mapping, kind=kind):
                self.assertIsNone(session.extract_answer(mapping, kind))
        self.run.assert_not_called()

    def test_failed_empty_or_truncated_extraction_returns_none(self):
        for result in (CommandResult(1, "PRIVATE_PAYLOAD", ""),
                       CommandResult(0, "PRIVATE_PAYLOAD", "", True),
                       CommandResult(0, "", "PRIVATE_PAYLOAD")):
            with self.subTest(result=result):
                self.run.return_value = result
                self.assertIsNone(session.extract_answer(self.mapping, "pi"))

    def test_transport_errors_return_none(self):
        for error in (OSError("PRIVATE_PAYLOAD"), ValueError("PRIVATE_PAYLOAD"),
                      subprocess.TimeoutExpired(["PRIVATE_PAYLOAD"], 1)):
            with self.subTest(error=error):
                self.run.side_effect = error
                self.assertIsNone(session.extract_answer(self.mapping, "pi"))


if __name__ == "__main__":
    unittest.main()
