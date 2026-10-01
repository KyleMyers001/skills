"""Bounded pipe reads and cleanup of real, short-lived Python children."""

import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from herdr_launch import process  # noqa: E402


class ReadCappedTests(unittest.TestCase):
    def test_under_at_and_over_cap(self):
        for data, limit, expected, truncated in (
                (b"abc", 4, b"abc", False),
                (b"abcd", 4, b"abcd", False),
                (b"abcde", 4, b"abcd", True),
                (b"", 0, b"", False), (b"x", 0, b"", True),
                (b"x" * (process.READ_CHUNK + 2), process.READ_CHUNK + 1,
                 b"x" * (process.READ_CHUNK + 1), True)):
            with self.subTest(size=len(data), limit=limit):
                self.assertEqual(process.read_capped(io.BytesIO(data), limit),
                                 (expected, truncated))

    def test_read_errors_keep_prefix_and_mark_it_truncated(self):
        for error in (OSError, ValueError):
            with self.subTest(error=error):
                stream = Mock()
                stream.read.side_effect = [b"abc", error("PRIVATE_PAYLOAD")]
                self.assertEqual(process.read_capped(stream, 10),
                                 (b"abc", True))

    def test_error_at_overflow_probe_marks_full_prefix_truncated(self):
        stream = Mock()
        stream.read.side_effect = [b"abc", ValueError("PRIVATE_PAYLOAD")]
        self.assertEqual(process.read_capped(stream, 3), (b"abc", True))


class RunBoundedTests(unittest.TestCase):
    def setUp(self):
        self.children = []
        self.real_popen = subprocess.Popen
        self.addCleanup(self.cleanup_children)

    def cleanup_children(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            for stream in (child.stdout, child.stderr):
                if stream is not None:
                    stream.close()

    def spawn(self, *args, **kwargs):
        child = self.real_popen(*args, **kwargs)
        self.children.append(child)
        return child

    def run_child(self, source, timeout=3, max_stdout=1024):
        with patch.object(process.subprocess, "Popen", side_effect=self.spawn):
            return process.run_bounded([sys.executable, "-c", source],
                                       timeout=timeout, max_stdout=max_stdout)

    def assert_reaped(self):
        self.assertEqual(len(self.children), 1)
        child = self.children[0]
        self.assertIsNotNone(child.poll())
        self.assertTrue(child.stdout.closed)
        self.assertTrue(child.stderr.closed)

    def test_normal_output_and_nonzero_exit(self):
        result = self.run_child(
            "import sys; print('café'); print('warning', file=sys.stderr); "
            "sys.exit(7)")
        self.assertEqual(result, process.CommandResult(7, "café\n", "warning\n"))
        self.assert_reaped()

    def test_invalid_utf8_is_replaced(self):
        result = self.run_child("import sys; sys.stdout.buffer.write(b'\\xff')")
        self.assertEqual(result.stdout, "\ufffd")
        self.assertFalse(result.stdout_truncated)
        self.assert_reaped()

    def test_stdout_overflow_kills_child_and_keeps_capped_prefix(self):
        result = self.run_child(
            "import sys,time; sys.stdout.buffer.write(b'x'*4096); "
            "sys.stdout.flush(); time.sleep(30)", max_stdout=64)
        self.assertEqual(result.stdout, "x" * 64)
        self.assertTrue(result.stdout_truncated)
        self.assertLess(result.returncode, 0)
        self.assert_reaped()

    def test_stderr_overflow_is_also_bounded_and_kills_child(self):
        result = self.run_child(
            "import sys,time; sys.stderr.buffer.write(b'x'*131072); "
            "sys.stderr.flush(); time.sleep(30)")
        self.assertEqual(len(result.stderr), process.STDERR_LIMIT)
        self.assertFalse(result.stdout_truncated)
        self.assertLess(result.returncode, 0)
        self.assert_reaped()

    def test_timeout_kills_and_reaps_child_before_raising(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_child("import time; time.sleep(30)", timeout=0.1)
        self.assert_reaped()
        self.assertLess(self.children[0].returncode, 0)

    def test_interrupted_wait_kills_and_reaps_child_before_raising(self):
        real_wait = subprocess.Popen.wait
        calls = []

        def interrupt_once(child, *args, **kwargs):
            calls.append(child)
            if len(calls) == 1:
                raise KeyboardInterrupt
            return real_wait(child, *args, **kwargs)

        with patch.object(subprocess.Popen, "wait", new=interrupt_once):
            with self.assertRaises(KeyboardInterrupt):
                self.run_child("import time; time.sleep(30)")
        self.assert_reaped()
        self.assertLess(self.children[0].returncode, 0)


if __name__ == "__main__":
    unittest.main()
