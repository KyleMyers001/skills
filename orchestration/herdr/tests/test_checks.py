"""Validation boundaries and startup gates, independent of the CLI transport."""

import argparse
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from herdr_launch import checks  # noqa: E402
from herdr_launch.errors import LaunchUnavailable  # noqa: E402


def arguments(**changes):
    values = dict(name="worker", name_prefix=None, kind="pi", workspace=None,
                  timeout=30000, prompt=None, prompt_file=None,
                  wait_timeout=None, expect_startup=[])
    return argparse.Namespace(**{**values, **changes})


class ValidationTests(unittest.TestCase):
    def assert_invalid(self, **changes):
        with self.assertRaises(LaunchUnavailable) as caught:
            checks.validate_args(arguments(**changes))
        self.assertNotIn("PRIVATE_PAYLOAD", str(caught.exception))

    def test_name_syntax_and_32_character_boundary(self):
        for name in ("a", "a" * 32, "a0_-"):
            with self.subTest(name=name):
                checks.validate_args(arguments(name=name))
        for name in ("", "a" * 33, "0name", "Upper", "a.b", "a\n", "é",
                     "PRIVATE_PAYLOAD"):
            with self.subTest(name=name):
                self.assert_invalid(name=name)

    def test_prefix_19_character_boundary(self):
        checks.validate_args(arguments(name=None, name_prefix="a" * 19))
        self.assert_invalid(name=None, name_prefix="a" * 20)
        self.assert_invalid(name=None, name_prefix="PRIVATE_PAYLOAD")

    def test_identifier_syntax_and_128_character_boundary(self):
        for value in ("A", "a" * 128, "w1:t_2.p-3"):
            with self.subTest(value=value):
                self.assertTrue(checks.identifier(value))
                checks.validate_args(arguments(kind=value, workspace=value))
        for value in (None, 1, "", "a" * 129, "w/1", "w\n", "é"):
            with self.subTest(value=value):
                self.assertFalse(checks.identifier(value))
        for field in ("kind", "workspace"):
            for value in ("", "a" * 129, "invalid PRIVATE_PAYLOAD"):
                with self.subTest(field=field, value=value):
                    self.assert_invalid(**{field: value})

    def test_startup_timeout_boundaries(self):
        for timeout in (3001, 300000):
            checks.validate_args(arguments(timeout=timeout))
        for timeout in (3000, 300001):
            self.assert_invalid(timeout=timeout)

    def test_expectations_require_a_prompt_source(self):
        self.assert_invalid(expect_startup=["model"])
        for source in ({"prompt": "task"}, {"prompt_file": "task.txt"}):
            checks.validate_args(arguments(expect_startup=["model"], **source))

    def test_expectation_character_and_blank_boundaries(self):
        for text in ("a", "é" * 200):
            checks.validate_args(arguments(prompt="task", expect_startup=[text]))
        for text in ("", " \n\t", "a" * 201):
            self.assert_invalid(prompt="PRIVATE_PAYLOAD", expect_startup=[text])


class PromptTests(unittest.TestCase):
    def assert_invalid(self, **changes):
        with self.assertRaises(LaunchUnavailable) as caught:
            checks.load_prompt(arguments(**changes))
        self.assertNotIn("PRIVATE_PAYLOAD", str(caught.exception))

    def test_no_prompt_and_plain_prompt_preservation(self):
        self.assertIsNone(checks.load_prompt(arguments()))
        self.assertEqual(checks.load_prompt(arguments(prompt=" é\n")), " é\n")

    def test_blank_prompts_are_rejected(self):
        for text in ("", " \n\t"):
            self.assert_invalid(prompt=text)

    def test_wait_timeout_boundaries(self):
        for timeout in (1, 3600000):
            self.assertEqual(checks.load_prompt(arguments(
                prompt="task", wait_timeout=timeout)), "task")
        for timeout in (0, 3600001):
            self.assert_invalid(prompt="PRIVATE_PAYLOAD", wait_timeout=timeout)
        self.assert_invalid(wait_timeout=1)

    def test_inline_prompt_cap_counts_utf8_bytes(self):
        self.assertEqual(checks.load_prompt(arguments(prompt="é" * 100000)),
                         "é" * 100000)
        self.assert_invalid(prompt="é" * 100000 + "a")

    def test_file_prompt_cap_and_oversize_rejection_before_read(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "prompt.txt"
            path.write_text("é" * 100000, encoding="utf-8")
            self.assertEqual(checks.load_prompt(arguments(prompt_file=str(path))),
                             "é" * 100000)
            path.write_bytes(b"x" * 200001)
            with patch.object(Path, "read_text") as read:
                self.assert_invalid(prompt_file=str(path))
            read.assert_not_called()

    def test_file_missing_directory_and_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "prompt.txt"
            self.assert_invalid(prompt_file=str(path))
            self.assert_invalid(prompt_file=temp)
            path.write_bytes(b"\xffPRIVATE_PAYLOAD")
            self.assert_invalid(prompt_file=str(path))

    def test_file_read_failure_is_bounded(self):
        with tempfile.NamedTemporaryFile() as file:
            with patch.object(Path, "read_text",
                              side_effect=OSError("PRIVATE_PAYLOAD")):
                self.assert_invalid(prompt_file=file.name)

    def test_file_is_rechecked_after_read(self):
        with tempfile.NamedTemporaryFile() as file:
            with patch.object(Path, "read_text", return_value="é" * 100001):
                self.assert_invalid(prompt_file=file.name)
            with patch.object(Path, "read_text", return_value=" \n"):
                self.assert_invalid(prompt_file=file.name)


class EvidenceTests(unittest.TestCase):
    def test_directory_requires_absolute_string_evidence(self):
        for value in (None, 1, Path("/tmp"), "relative PRIVATE_PAYLOAD"):
            with self.subTest(value=value):
                with self.assertRaises(LaunchUnavailable) as caught:
                    checks.directory(value)
                self.assertNotIn("PRIVATE_PAYLOAD", str(caught.exception))

    def test_directory_resolves_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "repo"
            target.mkdir()
            alias = Path(temp) / "alias"
            alias.symlink_to(target, target_is_directory=True)
            self.assertEqual(checks.directory(str(alias)), target.resolve())

    def test_directory_resolution_failure_is_bounded(self):
        for error in (OSError, ValueError, RuntimeError):
            with self.subTest(error=error):
                with patch.object(Path, "resolve",
                                  side_effect=error("PRIVATE_PAYLOAD")):
                    with self.assertRaises(LaunchUnavailable) as caught:
                        checks.directory("/tmp/repo")
                    self.assertNotIn("PRIVATE_PAYLOAD", str(caught.exception))

    def test_records_requires_a_list_of_objects(self):
        values = [{"name": "one"}, {}]
        self.assertEqual(checks.records({"agents": values}, "agents"), values)
        self.assertEqual(checks.records({"agents": []}, "agents"), [])
        for result in ({}, {"agents": None}, {"agents": {}},
                       {"agents": [{}, "PRIVATE_PAYLOAD"]}):
            with self.subTest(result=result):
                with self.assertRaises(LaunchUnavailable) as caught:
                    checks.records(result, "agents")
                self.assertNotIn("PRIVATE_PAYLOAD", str(caught.exception))


class StartupTests(unittest.TestCase):
    def test_line_cap_is_conservative_at_exactly_80_lines(self):
        for count, truncated in ((79, False), (80, True), (81, True)):
            with self.subTest(count=count):
                self.assertEqual(checks.trim_startup("x\n" * count, False),
                                 ("x\n" * min(count, 80), truncated))

    def test_character_cap_and_transport_cap(self):
        for count, truncated in ((7999, False), (8000, False), (8001, True)):
            with self.subTest(count=count):
                self.assertEqual(checks.trim_startup("é" * count, False),
                                 ("é" * min(count, 8000), truncated))
        self.assertEqual(checks.trim_startup("ready", True), ("ready", True))

    def test_submission_block_precedence(self):
        cases = [(" \n", True, "startup_empty"),
                 ("LOGIN REQUIRED", True, "startup_truncated"),
                 ("LOGIN REQUIRED", False, "startup_unsafe"),
                 ("ready", False, "startup_unexpected")]
        for text, truncated, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(checks.submission_block(
                    text, truncated, ["missing"]), reason)

    def test_all_blockers_are_case_insensitive(self):
        for phrase in checks.STARTUP_BLOCKERS:
            with self.subTest(phrase=phrase):
                self.assertEqual(checks.submission_block(
                    f"Ready: {phrase.upper()}", False, []), "startup_unsafe")

    def test_all_expectations_are_casefolded_and_required(self):
        self.assertIsNone(checks.submission_block(
            "MODEL ready STRASSE", False, ["model", "Straße"]))
        self.assertEqual(checks.submission_block(
            "model ready", False, ["MODEL", "high"]), "startup_unexpected")
        self.assertIsNone(checks.submission_block("Ready", False, []))

    def test_astra_model_alone_does_not_satisfy_high_thinking_gate(self):
        expected = ["gpt-6-astra", "thinking: high"]
        for level in ("low", "medium", "off"):
            with self.subTest(level=level):
                self.assertEqual(checks.submission_block(
                    f"gpt-6-astra • thinking: {level}", False, expected),
                    "startup_unexpected")
        self.assertIsNone(checks.submission_block(
            "GPT-6-ASTRA • THINKING: HIGH", False, expected))


if __name__ == "__main__":
    unittest.main()
