"""CLI tests using synthetic transcripts only; no agents are launched."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/extract-response.py"


def text(value):
    return {"type": "text", "text": value}


def assistant(kind, content, **metadata):
    message = {"role": "assistant", "content": content, **metadata}
    return {"type": "message" if kind == "pi" else "assistant",
            "message": message}


class ExtractResponseTests(unittest.TestCase):
    def run_extractor(self, kind, records=(), raw=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session with spaces.jsonl"
            if raw is None:
                raw = "\n".join(json.dumps(record) for record in records)
            path.write_bytes(raw if isinstance(raw, bytes)
                             else raw.encode("utf-8"))
            return subprocess.run(
                [sys.executable, str(SCRIPT), "--kind", kind, str(path)],
                capture_output=True, text=True, cwd=directory,
            )

    def assert_answer(self, result, expected):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected)
        self.assertEqual(result.stderr, "")

    def assert_fallback(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("terminal", result.stderr)
        self.assertIn("do not rerun", result.stderr)
        self.assertNotIn("PRIVATE_PAYLOAD", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertLess(len(result.stderr), 300)

    def test_last_pi_text_preserves_blocks_and_ignores_other_content(self):
        records = [
            assistant("pi", [text("Earlier commentary")]),
            {"type": "message", "message": {
                "role": "toolResult", "content": [text("PRIVATE_PAYLOAD")]}},
            assistant("pi", [
                {"type": "thinking", "thinking": "PRIVATE_PAYLOAD"},
                text("## Findings\n"),
                {"type": "toolCall", "arguments": {"secret": "PRIVATE_PAYLOAD"}},
                text("Café — vérifier ✅\n"),
            ]),
            assistant("pi", [{"type": "thinking", "thinking": "hidden"}]),
            {"type": "usage", "kind": "future-kind", "usage": {}},
        ]
        self.assert_answer(self.run_extractor("pi", records),
                           "## Findings\nCafé — vérifier ✅\n")

    def test_claude_collects_split_records_sharing_selected_message_id(self):
        records = [
            assistant("claude", [text("Earlier")], id="old"),
            assistant("claude", [text("First\n")], id="answer"),
            {"type": "user", "message": {
                "content": [{"type": "tool_result", "content": "hidden"}]}},
            assistant("claude", [text("Unrelated")], id="other"),
            assistant("claude", [text("Second\n")], id="answer"),
            assistant("claude", [{"type": "thinking", "thinking": "hidden"}],
                      id="trailing"),
            {"type": "progress", "data": {"text": "hidden"}},
        ]
        self.assert_answer(self.run_extractor("claude", records),
                           "First\nSecond\n")

    def test_claude_missing_id_does_not_combine_unrelated_messages(self):
        records = [assistant("claude", [text("Old")]),
                   assistant("claude", [text("Answer")])]
        self.assert_answer(self.run_extractor("claude", records), "Answer")

    def test_changed_handoff_text_and_metadata_are_not_validation_gates(self):
        for kind in ("pi", "claude"):
            for answer in ("Looks fine.", "## New handoff\n- Finding",
                           '{"new_response_format": "review"}'):
                for metadata in ({}, {"stopReason": "future-marker",
                                      "model": "changed", "id": {"new": 1}}):
                    with self.subTest(kind=kind, answer=answer,
                                      metadata=metadata):
                        records = [
                            {"type": "user", "message": {"content":
                             "<pasted_content>wrapped task</pasted_content>"}},
                            assistant(kind, [text(answer)], **metadata),
                            {"type": "new-bookkeeping-event", "data": {}},
                        ]
                        self.assert_answer(self.run_extractor(kind, records),
                                           answer)

    def test_plain_string_content_is_also_supported(self):
        for kind in ("pi", "claude"):
            with self.subTest(kind=kind):
                self.assert_answer(self.run_extractor(
                    kind, [assistant(kind, "Plain answer")]), "Plain answer")

    def test_unknown_blocks_do_not_block_recognized_text(self):
        records = [assistant("claude", [
            {"type": "future-block", "payload": "hidden"}, text("Answer")])]
        self.assert_answer(self.run_extractor("claude", records), "Answer")

    def test_candidate_need_not_claim_completion(self):
        records = [assistant("pi", [text("Still checking the tests.")],
                             stopReason="toolUse")]
        self.assert_answer(self.run_extractor("pi", records),
                           "Still checking the tests.")

    def test_large_tool_payload_is_excluded_and_answer_is_not_truncated(self):
        answer = "Finding\n" * 10000
        records = [
            {"type": "message", "message": {
                "role": "toolResult", "content": [text("X" * 1000000)]}},
            assistant("pi", [text(answer)]),
        ]
        self.assert_answer(self.run_extractor("pi", records), answer)

    def test_blank_lines_are_ignored(self):
        raw = "\n" + json.dumps(assistant("pi", [text("Answer")])) + "\n\n"
        self.assert_answer(self.run_extractor("pi", raw=raw), "Answer")

    def test_empty_or_textless_transcript_requests_terminal_fallback(self):
        for records in ([], [{"type": "new-event"}],
                        [assistant("pi", [text("")])]):
            with self.subTest(records=records):
                self.assert_fallback(self.run_extractor("pi", records))

    def test_unreadable_shape_requests_terminal_fallback(self):
        for content in (None, {"new_format": "PRIVATE_PAYLOAD"},
                        [{"type": "text", "text": 123}]):
            with self.subTest(content=content):
                records = [assistant("claude", [text("Older candidate")]),
                           assistant("claude", content)]
                self.assert_fallback(self.run_extractor("claude", records))

    def test_malformed_record_does_not_emit_stale_text_or_raw_error(self):
        candidate = json.dumps(assistant("pi", [text("Older candidate")]))
        for raw in (candidate + '\n{"PRIVATE_PAYLOAD":',
                    '{"PRIVATE_PAYLOAD":\n' + candidate,
                    candidate + '\n["PRIVATE_PAYLOAD"]'):
            with self.subTest(raw=raw):
                self.assert_fallback(self.run_extractor("pi", raw=raw))

    def test_missing_path_requests_terminal_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--kind", "pi",
                 str(Path(directory) / "missing.jsonl")],
                capture_output=True, text=True,
            )
        self.assert_fallback(result)

    def test_unreadable_message_requests_terminal_fallback(self):
        for kind in ("pi", "claude"):
            records = [assistant(kind, [text("Older candidate")]),
                       assistant(kind, [])]
            records[-1]["message"] = "PRIVATE_PAYLOAD"
            with self.subTest(kind=kind):
                self.assert_fallback(self.run_extractor(kind, records))

    def test_invalid_encoding_requests_terminal_fallback(self):
        self.assert_fallback(self.run_extractor("pi", raw=b"\xffPRIVATE_PAYLOAD"))

    def test_invalid_harness_is_a_usage_error(self):
        result = self.run_extractor("unknown")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("invalid choice", result.stderr)


if __name__ == "__main__":
    unittest.main()
