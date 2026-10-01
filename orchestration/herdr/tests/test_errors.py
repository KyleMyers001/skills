"""Focused error-envelope tests; upstream payloads must stay private."""

import json
from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from herdr_launch import errors  # noqa: E402
from herdr_launch.process import CommandResult  # noqa: E402


class ErrorTests(unittest.TestCase):
    def envelope(self, code="agent_pane_busy", **fields):
        return json.dumps({"id": "cli:agent:start", "error": {
            "code": code, "message": "PRIVATE_PAYLOAD"}, **fields})

    def parse(self, stdout="", stderr="", returncode=1):
        failure = errors.parse_herdr_failure(
            CommandResult(returncode, stdout, stderr))
        self.assertEqual(failure.code, "herdr_error")
        self.assertNotIn("PRIVATE_PAYLOAD", str(failure))
        self.assertNotIn("PRIVATE_PAYLOAD", repr(vars(failure)))
        return failure

    def test_zero_or_two_nonempty_streams_discard_codes(self):
        for stdout, stderr in (("", " \n"),
                               (self.envelope(), self.envelope())):
            with self.subTest(stdout=bool(stdout)):
                failure = self.parse(stdout, stderr)
                self.assertIsNone(failure.herdr_code)
                self.assertFalse(failure.pre_start_busy)

    def test_one_nonempty_stream_keeps_only_code(self):
        for stdout, stderr in ((self.envelope("timeout"), " \n"),
                               (" \n", self.envelope("timeout"))):
            with self.subTest(stdout=bool(stdout.strip())):
                failure = self.parse(stdout, stderr)
                self.assertEqual(failure.herdr_code, "timeout")
                self.assertFalse(failure.pre_start_busy)

    def test_nonjson_and_unsupported_envelopes_discard_codes(self):
        for text in ("PRIVATE_PAYLOAD", "null", "[]", '"PRIVATE_PAYLOAD"',
                     self.envelope(result=None), '{}',
                     '{"error": "PRIVATE_PAYLOAD"}',
                     '{"error": {"code": 1}}'):
            with self.subTest(text=text):
                failure = self.parse(stderr=text)
                self.assertIsNone(failure.herdr_code)
                self.assertFalse(failure.pre_start_busy)

    def test_code_length_and_syntax_boundaries(self):
        for code, expected in (("a", "a"), ("a" * 64, "a" * 64),
                               ("a_0", "a_0"), ("a" * 65, None),
                               ("", None), ("0bad", None), ("UPPER", None),
                               ("bad-code", None), ("bad\n", None),
                               ("é", None), ("PRIVATE_PAYLOAD", None)):
            with self.subTest(code=code):
                self.assertEqual(self.parse(stderr=self.envelope(code))
                                 .herdr_code, expected)

    def test_exact_pre_start_busy_envelope(self):
        failure = self.parse(stdout=" \n", stderr=self.envelope())
        self.assertEqual(failure.herdr_code, "agent_pane_busy")
        self.assertTrue(failure.pre_start_busy)

    def test_busy_retry_requires_every_envelope_condition(self):
        cases = [
            CommandResult(0, "", self.envelope()),
            CommandResult(2, "", self.envelope()),
            CommandResult(1, self.envelope(), ""),
            CommandResult(1, "", self.envelope(id="cli:agent:prompt")),
            CommandResult(1, "", self.envelope(id=None)),
            CommandResult(1, "", self.envelope(error={
                "code": "agent_pane_busy", "message": None})),
            CommandResult(1, "", self.envelope(error={
                "code": "agent_pane_busy"})),
            CommandResult(1, "", self.envelope(result={})),
        ]
        for result in cases:
            with self.subTest(result=result):
                self.assertFalse(self.parse(result.stdout, result.stderr,
                                            result.returncode).pre_start_busy)


if __name__ == "__main__":
    unittest.main()
