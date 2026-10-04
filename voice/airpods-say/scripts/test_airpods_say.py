#!/usr/bin/env python3
"""
Tests for airpods_say.py — focused on the play_stream() drain bug fix.

Bug fixed: play_stream() previously closed ffplay stdin at HTTP-stream EOF,
then called proc.wait(timeout=5).  If ffplay had buffered audio that took
>5 s to play, subprocess.TimeoutExpired triggered proc.kill(), but the
function still returned exit_code 0 (false success).

Fix: after stdin EOF, _drain_ffplay() is called.  It:
  - polls ffplay at _DRAIN_POLL_SECS intervals
  - continues monitoring the AirPods device (device_changed event)
  - uses a duration-aware safety timeout (not a fixed 5 s)
  - returns 0 only on natural exit, 1 on timeout, 3 on device change
"""

from __future__ import annotations

import http.client
import io
import socket
import subprocess
import sys
import threading
import time
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch
import os

# Ensure the script directory is on sys.path
sys.path.insert(0, os.path.dirname(__file__))

import airpods_say
from airpods_say import (
    _drain_ffplay,
    _Timing,
    _MP3_128K_BYTES_PER_SEC,
    _DRAIN_TIMEOUT_MIN_SECS,
    _DRAIN_TIMEOUT_MAX_SECS,
    _DRAIN_TIMEOUT_SCALE,
    _DRAIN_POLL_SECS,
    play_stream,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proc(poll_results: list) -> MagicMock:
    """
    Create a mock subprocess.Popen whose poll() returns successive values
    from poll_results (cycling the last value once exhausted).
    """
    proc = MagicMock(spec=subprocess.Popen)
    results = list(poll_results)
    call_index = [0]

    def _poll():
        i = call_index[0]
        val = results[min(i, len(results) - 1)]
        call_index[0] += 1
        proc.returncode = val
        return val

    proc.poll.side_effect = _poll
    proc.terminate.return_value = None
    proc.kill.return_value = None
    proc.wait.return_value = 0
    proc.stderr = io.BytesIO()
    proc.stdin = MagicMock()
    proc.stdin.write.return_value = None
    proc.stdin.flush.return_value = None
    proc.stdin.close.return_value = None
    return proc


def _no_timing() -> _Timing:
    return _Timing(enabled=False)


# ---------------------------------------------------------------------------
# _drain_ffplay unit tests
# ---------------------------------------------------------------------------

class TestDrainFfplayNaturalExit(unittest.TestCase):
    """ffplay exits naturally → _drain_ffplay returns 0."""

    def test_exits_immediately(self):
        """proc.poll() returns 0 on the very first check."""
        proc = _make_proc([0])
        dc = threading.Event()
        rc = _drain_ffplay(proc, dc, bytes_written=8000, debug=False)
        self.assertEqual(rc, 0)
        proc.kill.assert_not_called()
        proc.terminate.assert_not_called()

    def test_exits_after_several_polls(self):
        """proc.poll() returns None a few times then exits."""
        proc = _make_proc([None, None, None, 0])
        dc = threading.Event()
        # Use tiny poll interval to keep test fast
        orig_poll = airpods_say._DRAIN_POLL_SECS
        airpods_say._DRAIN_POLL_SECS = 0.01
        try:
            rc = _drain_ffplay(proc, dc, bytes_written=16000, debug=False)
        finally:
            airpods_say._DRAIN_POLL_SECS = orig_poll
        self.assertEqual(rc, 0)
        proc.kill.assert_not_called()

    def test_buffered_audio_longer_than_5s_not_killed(self):
        """
        Regression: drain representing >5 s of simulated buffered audio must
        not kill ffplay.  Uses mocked time.monotonic and time.sleep so the
        test completes instantly yet exercises simulated elapsed time well
        beyond the old hard-coded 5-second proc.wait timeout.

        Setup:
          - safety_secs = _DRAIN_TIMEOUT_MIN_SECS = 30 s
          - Each call to time.monotonic() advances simulated clock by 2 s
          - deadline = 0 + 30 = 30 s
          - proc.poll() returns None for 5 iterations (~10 s simulated)
            then exits naturally
          - Old code (proc.wait timeout=5) would have killed at ~5 s;
            new drain (30 s safety) lets it finish → return 0
        """
        simulated_time = [0.0]

        def _mono():
            t = simulated_time[0]
            simulated_time[0] += 2.0   # advance 2 s per call
            return t

        # poll() returns None 5 times (10 s simulated) then exits naturally.
        poll_count = [0]

        def _poll():
            poll_count[0] += 1
            return None if poll_count[0] <= 5 else 0

        proc = MagicMock(spec=subprocess.Popen)
        proc.stderr = io.BytesIO()
        proc.poll.side_effect = _poll
        proc.kill.return_value = None
        dc = threading.Event()

        orig_min = airpods_say._DRAIN_TIMEOUT_MIN_SECS
        airpods_say._DRAIN_TIMEOUT_MIN_SECS = 30.0
        try:
            with patch("airpods_say.time.monotonic", side_effect=_mono):
                with patch("airpods_say.time.sleep"):   # no-op; no wall time spent
                    rc = _drain_ffplay(
                        proc, dc, bytes_written=0, debug=False
                    )
        finally:
            airpods_say._DRAIN_TIMEOUT_MIN_SECS = orig_min

        self.assertEqual(rc, 0,
                         "Drain >5 s simulated must succeed; must not kill ffplay")
        proc.kill.assert_not_called()


class TestDrainFfplayDeviceChange(unittest.TestCase):
    """Device changes during drain → _drain_ffplay returns 3."""

    def test_device_already_changed_at_entry(self):
        """device_changed is set before we even start polling."""
        proc = _make_proc([None])  # never exits naturally
        dc = threading.Event()
        dc.set()
        rc = _drain_ffplay(proc, dc, bytes_written=16000, debug=False)
        self.assertEqual(rc, 3)
        proc.terminate.assert_called_once()

    def test_device_changes_mid_drain(self):
        """Device changes after a couple of polls."""
        proc = MagicMock(spec=subprocess.Popen)
        proc.stderr = io.BytesIO()
        proc.poll.return_value = None  # never exits naturally
        proc.terminate.return_value = None
        proc.kill.return_value = None
        proc.wait.return_value = 0

        dc = threading.Event()

        orig_poll = airpods_say._DRAIN_POLL_SECS
        airpods_say._DRAIN_POLL_SECS = 0.01
        try:
            # Set device_changed after a short delay in a thread
            def _trigger():
                time.sleep(0.03)
                dc.set()

            t = threading.Thread(target=_trigger, daemon=True)
            t.start()
            rc = _drain_ffplay(proc, dc, bytes_written=16000, debug=False)
            t.join(timeout=1)
        finally:
            airpods_say._DRAIN_POLL_SECS = orig_poll

        self.assertEqual(rc, 3)
        proc.terminate.assert_called_once()

    def test_device_change_returns_3_not_0(self):
        """Regression: device change during drain must never return 0."""
        proc = _make_proc([None, None, None, None, 0])  # would exit naturally
        dc = threading.Event()
        dc.set()  # already changed

        rc = _drain_ffplay(proc, dc, bytes_written=16000, debug=False)

        self.assertNotEqual(rc, 0, "Device change must not be reported as success")
        self.assertEqual(rc, 3)

    def test_device_change_terminates_then_kills_on_timeout(self):
        """If terminate doesn't stop ffplay in 3 s, kill is called."""
        proc = MagicMock(spec=subprocess.Popen)
        proc.stderr = io.BytesIO()
        proc.poll.return_value = None
        proc.terminate.return_value = None
        # Simulate proc.wait raising TimeoutExpired
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="ffplay", timeout=3)
        proc.kill.return_value = None

        dc = threading.Event()
        dc.set()

        rc = _drain_ffplay(proc, dc, bytes_written=1000, debug=False)
        self.assertEqual(rc, 3)
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()


class TestDrainFfplaySafetyTimeout(unittest.TestCase):
    """Safety timeout reached → _drain_ffplay kills ffplay and returns 1."""

    def _drain_with_tiny_timeout(self, bytes_written: int) -> int:
        """Run drain with _DRAIN_TIMEOUT_MIN_SECS patched to 0.05 s."""
        proc = MagicMock(spec=subprocess.Popen)
        proc.stderr = io.BytesIO()
        proc.poll.return_value = None  # never exits
        proc.kill.return_value = None
        dc = threading.Event()

        orig_min = airpods_say._DRAIN_TIMEOUT_MIN_SECS
        orig_poll = airpods_say._DRAIN_POLL_SECS
        airpods_say._DRAIN_TIMEOUT_MIN_SECS = 0.05
        airpods_say._DRAIN_POLL_SECS = 0.01
        try:
            rc = _drain_ffplay(proc, dc, bytes_written=bytes_written, debug=False)
        finally:
            airpods_say._DRAIN_TIMEOUT_MIN_SECS = orig_min
            airpods_say._DRAIN_POLL_SECS = orig_poll

        proc.kill.assert_called_once()
        return rc

    def test_timeout_returns_1(self):
        """Safety timeout returns 1, not 0."""
        rc = self._drain_with_tiny_timeout(bytes_written=0)
        self.assertEqual(rc, 1)

    def test_timeout_is_not_success(self):
        """Regression: safety timeout must never be reported as success (0)."""
        rc = self._drain_with_tiny_timeout(bytes_written=0)
        self.assertNotEqual(rc, 0, "Drain timeout must not return 0 (false success)")

    def test_timeout_kills_process(self):
        """After timeout, proc.kill() must have been called."""
        proc = MagicMock(spec=subprocess.Popen)
        proc.stderr = io.BytesIO()
        proc.poll.return_value = None
        proc.kill.return_value = None
        dc = threading.Event()

        orig_min = airpods_say._DRAIN_TIMEOUT_MIN_SECS
        orig_poll = airpods_say._DRAIN_POLL_SECS
        airpods_say._DRAIN_TIMEOUT_MIN_SECS = 0.05
        airpods_say._DRAIN_POLL_SECS = 0.01
        try:
            _drain_ffplay(proc, dc, bytes_written=0, debug=False)
        finally:
            airpods_say._DRAIN_TIMEOUT_MIN_SECS = orig_min
            airpods_say._DRAIN_POLL_SECS = orig_poll

        proc.kill.assert_called_once()


class TestDrainFfplayDurationAwareTimeout(unittest.TestCase):
    """Safety timeout scales with bytes written (duration-aware)."""

    def _compute_safety_secs(self, bytes_written: int) -> float:
        """Replicate the safety_secs formula from _drain_ffplay."""
        estimated = bytes_written / _MP3_128K_BYTES_PER_SEC
        return max(
            _DRAIN_TIMEOUT_MIN_SECS,
            min(estimated * _DRAIN_TIMEOUT_SCALE, _DRAIN_TIMEOUT_MAX_SECS),
        )

    def test_zero_bytes_uses_minimum(self):
        secs = self._compute_safety_secs(0)
        self.assertEqual(secs, _DRAIN_TIMEOUT_MIN_SECS)

    def test_short_audio_uses_minimum(self):
        """1 second of audio → safety_secs still at minimum."""
        one_sec = _MP3_128K_BYTES_PER_SEC  # 16000 bytes = 1 s
        secs = self._compute_safety_secs(one_sec)
        self.assertEqual(secs, _DRAIN_TIMEOUT_MIN_SECS)

    def test_long_audio_scales_beyond_minimum(self):
        """30 s of audio → safety_secs = 30 * 2 = 60 (> min of 30)."""
        thirty_sec = 30 * _MP3_128K_BYTES_PER_SEC
        secs = self._compute_safety_secs(thirty_sec)
        expected = 30.0 * _DRAIN_TIMEOUT_SCALE  # 60.0
        self.assertAlmostEqual(secs, expected, places=5)
        self.assertGreater(secs, _DRAIN_TIMEOUT_MIN_SECS)

    def test_very_long_audio_capped_at_maximum(self):
        """
        Very large bytes_written → safety_secs capped at _DRAIN_TIMEOUT_MAX_SECS.
        """
        huge = 10_000 * _MP3_128K_BYTES_PER_SEC  # 10000 s of audio
        secs = self._compute_safety_secs(huge)
        self.assertEqual(secs, _DRAIN_TIMEOUT_MAX_SECS)

    def test_timeout_actually_scales_in_drain(self):
        """
        Patch constants; verify that drain waits longer for more bytes.
        Proc exits after a fixed number of polls; with enough bytes the
        drain should complete naturally (return 0) rather than timing out.
        """
        # 20 seconds of audio bytes → estimated = 20 s → safety = 40 s
        bytes_written = 20 * _MP3_128K_BYTES_PER_SEC

        orig_min  = airpods_say._DRAIN_TIMEOUT_MIN_SECS
        orig_max  = airpods_say._DRAIN_TIMEOUT_MAX_SECS
        orig_scale = airpods_say._DRAIN_TIMEOUT_SCALE
        orig_poll = airpods_say._DRAIN_POLL_SECS

        # Set a very short poll so the test is fast, but keep scale high
        airpods_say._DRAIN_TIMEOUT_MIN_SECS = 0.0   # no minimum floor
        airpods_say._DRAIN_TIMEOUT_MAX_SECS = 9999.0
        airpods_say._DRAIN_TIMEOUT_SCALE    = 2.0
        airpods_say._DRAIN_POLL_SECS        = 0.005

        try:
            # With the above, safety_secs = 20 * 2.0 = 40 s.
            # Proc exits after ~5 polls (0.025 s) → should return 0.
            call_count = [0]
            def _poll():
                call_count[0] += 1
                return None if call_count[0] < 5 else 0

            proc = MagicMock(spec=subprocess.Popen)
            proc.stderr = io.BytesIO()
            proc.poll.side_effect = _poll
            proc.kill.return_value = None
            dc = threading.Event()

            rc = _drain_ffplay(proc, dc, bytes_written=bytes_written, debug=False)
        finally:
            airpods_say._DRAIN_TIMEOUT_MIN_SECS = orig_min
            airpods_say._DRAIN_TIMEOUT_MAX_SECS = orig_max
            airpods_say._DRAIN_TIMEOUT_SCALE    = orig_scale
            airpods_say._DRAIN_POLL_SECS        = orig_poll

        self.assertEqual(rc, 0)
        proc.kill.assert_not_called()


# ---------------------------------------------------------------------------
# play_stream integration tests (mocked subprocess + http response)
# ---------------------------------------------------------------------------

class _FakeResponse:
    """
    Minimal fake http.client.HTTPResponse that yields `chunks` then EOF (b"").
    Provides read1() and a real fileno() via an os.pipe so select() works.
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self._data = b"".join(chunks)
        self._pos = 0
        # Create a real pipe; write all data immediately so select sees it ready.
        self._r, self._w = os.pipe()
        os.write(self._w, self._data)
        os.close(self._w)
        self.fp = True  # non-None: stream not exhausted yet

    def fileno(self) -> int:
        return self._r

    def read1(self, size: int) -> bytes:
        chunk = os.read(self._r, size) if self._pos < len(self._data) else b""
        if not chunk:
            self.fp = None  # mimic http.client EOF behaviour
        self._pos += len(chunk)
        return chunk

    def read(self, size: int = -1) -> bytes:
        return self.read1(size)

    def close(self) -> None:
        try:
            os.close(self._r)
        except OSError:
            pass


class TestPlayStreamDrainIntegration(unittest.TestCase):
    """
    Integration tests for play_stream() exercising the drain path.

    We patch:
      - subprocess.Popen          → controlled mock
      - get_default_output_device_name → stable AirPods name
      - signal.signal             → no-op (avoids signal handler side effects)
    """

    _AIRPODS_NAME = "Test AirPods Pro"

    def _run_play_stream(
        self,
        chunks: list[bytes],
        proc_mock: MagicMock,
        device_name_side_effect=None,
    ) -> int:
        """Helper: run play_stream() with mocked subprocess and device."""
        resp = _FakeResponse(chunks)
        if isinstance(proc_mock, MagicMock) and proc_mock.stdin.write.side_effect is None:
            proc_mock.stdin.write.side_effect = lambda data: len(data)
        device_fn = device_name_side_effect or (lambda: self._AIRPODS_NAME)

        with ExitStack() as stack:
            stack.enter_context(
                patch("airpods_say.subprocess.Popen", return_value=proc_mock)
            )
            stack.enter_context(
                patch("airpods_say.get_default_output_device_name",
                      side_effect=device_fn)
            )
            stack.enter_context(patch("airpods_say.signal.signal"))
            stack.enter_context(patch("airpods_say.os.set_blocking"))
            try:
                rc = play_stream(
                    resp=resp,
                    ffplay_path="/usr/bin/ffplay",
                    expected_device_name=self._AIRPODS_NAME,
                    debug=False,
                    poll_interval=0.05,
                )
            finally:
                resp.close()

        return rc

    def test_stream_eof_natural_drain_returns_0(self):
        """
        Stream ends, drain completes naturally → play_stream returns 0.
        """
        proc = _Player()

        orig_poll = airpods_say._DRAIN_POLL_SECS
        orig_min  = airpods_say._DRAIN_TIMEOUT_MIN_SECS
        airpods_say._DRAIN_POLL_SECS = 0.01
        airpods_say._DRAIN_TIMEOUT_MIN_SECS = 5.0
        try:
            rc = self._run_play_stream(
                chunks=[b"audio-data-chunk"],
                proc_mock=proc,
            )
        finally:
            airpods_say._DRAIN_POLL_SECS = orig_poll
            airpods_say._DRAIN_TIMEOUT_MIN_SECS = orig_min

        self.assertEqual(rc, 0)
        self.assertFalse(proc.killed)
        self.assertEqual(proc.audio, b"audio-data-chunk")

    def test_device_changes_during_drain_returns_3(self):
        """
        AirPods change after stream EOF but during drain → play_stream returns 3.
        """
        proc = MagicMock(spec=subprocess.Popen)
        proc.stderr = io.BytesIO()
        proc.poll.return_value = None  # never exits naturally
        proc.stdin = MagicMock()
        proc.kill.return_value = None
        proc.terminate.return_value = None
        proc.wait.return_value = 0

        # Device changes to non-AirPods on the 3rd call
        call_count = [0]
        def _device():
            call_count[0] += 1
            if call_count[0] >= 3:
                return "MacBook Pro Speakers"
            return self._AIRPODS_NAME

        orig_poll = airpods_say._DRAIN_POLL_SECS
        orig_min  = airpods_say._DRAIN_TIMEOUT_MIN_SECS
        airpods_say._DRAIN_POLL_SECS = 0.01
        airpods_say._DRAIN_TIMEOUT_MIN_SECS = 5.0
        try:
            rc = self._run_play_stream(
                chunks=[b"audio-data-chunk"],
                proc_mock=proc,
                device_name_side_effect=_device,
            )
        finally:
            airpods_say._DRAIN_POLL_SECS = orig_poll
            airpods_say._DRAIN_TIMEOUT_MIN_SECS = orig_min

        self.assertEqual(rc, 3)

    def test_drain_timeout_not_success(self):
        """
        Drain safety timeout exceeded → play_stream does NOT return 0.
        """
        proc = MagicMock(spec=subprocess.Popen)
        proc.stderr = io.BytesIO()
        proc.poll.return_value = None  # hung forever
        proc.stdin = MagicMock()
        proc.kill.return_value = None
        proc.terminate.return_value = None
        proc.wait.return_value = 0

        orig_poll = airpods_say._DRAIN_POLL_SECS
        orig_min  = airpods_say._DRAIN_TIMEOUT_MIN_SECS
        airpods_say._DRAIN_POLL_SECS = 0.01
        airpods_say._DRAIN_TIMEOUT_MIN_SECS = 0.05  # very short for test
        try:
            rc = self._run_play_stream(
                chunks=[b"audio-data-chunk"],
                proc_mock=proc,
            )
        finally:
            airpods_say._DRAIN_POLL_SECS = orig_poll
            airpods_say._DRAIN_TIMEOUT_MIN_SECS = orig_min

        self.assertNotEqual(rc, 0, "Drain timeout must not be reported as success")
        self.assertEqual(rc, 1)
        proc.kill.assert_called()

    def test_bytes_written_tracked_nonzero(self):
        """
        When audio chunks are piped to ffplay, bytes_written is non-zero
        so that the duration-aware drain timeout scales up from the minimum.

        We verify indirectly: with a 30-byte chunk at 16000 B/s the estimated
        audio is < 0.002 s → safety_secs rounds to DRAIN_TIMEOUT_MIN_SECS.
        This test just confirms play_stream completes without error.
        """
        proc = _Player()

        orig_poll = airpods_say._DRAIN_POLL_SECS
        orig_min  = airpods_say._DRAIN_TIMEOUT_MIN_SECS
        airpods_say._DRAIN_POLL_SECS = 0.01
        airpods_say._DRAIN_TIMEOUT_MIN_SECS = 5.0
        try:
            rc = self._run_play_stream(
                chunks=[b"x" * 30],
                proc_mock=proc,
            )
        finally:
            airpods_say._DRAIN_POLL_SECS = orig_poll
            airpods_say._DRAIN_TIMEOUT_MIN_SECS = orig_min

        self.assertEqual(rc, 0)
        self.assertEqual(proc.audio, b"x" * 30)

    def test_ffplay_unexpected_death_returns_1(self):
        """
        If ffplay exits on its own before stream EOF (e.g., crashed), the
        function must return 1 with a concise stderr error, not false success 0.
        """
        # proc.poll() immediately non-None → triggers the unexpected-death path
        # before any chunk is read from the response.
        proc = _make_proc([1])  # exit code 1 from the start; cycles 1 forever
        proc.stdin = MagicMock()

        rc = self._run_play_stream(chunks=[b"audio-data"], proc_mock=proc)

        self.assertEqual(rc, 1)
        proc.kill.assert_not_called()   # already dead; no kill needed

    def test_ffplay_unexpected_death_not_zero(self):
        """Regression: unexpected ffplay death must never be reported as success."""
        proc = _make_proc([2])  # non-zero exit; cycles forever
        proc.stdin = MagicMock()

        rc = self._run_play_stream(chunks=[b"audio-data"], proc_mock=proc)

        self.assertNotEqual(rc, 0, "Unexpected ffplay death must not return 0")

    def test_broken_pipe_returns_1(self):
        """
        If ffplay's stdin pipe breaks while writing audio data, the function
        must return 1 with a concise stderr error, not false success 0.
        """
        proc = MagicMock(spec=subprocess.Popen)
        proc.stderr = io.BytesIO()
        proc.poll.return_value = None        # proc is nominally alive
        proc.stdin = MagicMock()
        proc.stdin.write.side_effect = BrokenPipeError("broken pipe")
        proc.kill.return_value = None
        proc.terminate.return_value = None
        proc.wait.return_value = 0

        rc = self._run_play_stream(chunks=[b"audio-data"], proc_mock=proc)

        self.assertEqual(rc, 1)

    def test_broken_pipe_not_zero(self):
        """Regression: BrokenPipeError must never be reported as success."""
        proc = MagicMock(spec=subprocess.Popen)
        proc.stderr = io.BytesIO()
        proc.poll.return_value = None
        proc.stdin = MagicMock()
        proc.stdin.write.side_effect = BrokenPipeError("broken pipe")
        proc.kill.return_value = None
        proc.terminate.return_value = None
        proc.wait.return_value = 0

        rc = self._run_play_stream(chunks=[b"audio-data"], proc_mock=proc)

        self.assertNotEqual(rc, 0, "BrokenPipeError must not return 0 (false success)")


# ---------------------------------------------------------------------------
# Existing behaviour: non-drain paths still work
# ---------------------------------------------------------------------------

class TestPlayStreamDeviceChangeInLoop(unittest.TestCase):
    """Device change detected in the main streaming loop (before EOF)."""

    _AIRPODS_NAME = "Test AirPods Pro"

    def test_device_change_mid_stream_returns_3(self):
        """
        If the device changes while data is still streaming (before EOF),
        play_stream returns 3 and does not enter the drain path.
        """
        proc = MagicMock(spec=subprocess.Popen)
        proc.stderr = io.BytesIO()
        proc.poll.return_value = None
        proc.stdin = MagicMock()
        proc.kill.return_value = None
        proc.terminate.return_value = None
        proc.wait.return_value = 0

        # Provide an infinite stream of data — device change must stop it
        # We'll trigger the device change via the monitor thread.
        class _InfiniteResp:
            def __init__(self):
                self._r, self._w = os.pipe()
                # Write some data so select() sees it ready immediately
                os.write(self._w, b"a" * 512)
                self.fp = True
                self._sent = 0

            def fileno(self):
                return self._r

            def read1(self, size):
                # Return a chunk; after 3 reads trigger device change
                self._sent += 1
                if self._sent > 100:
                    return b""  # eventual EOF safety
                return b"a" * min(size, 32)

            def read(self, size=-1):
                return self.read1(size)

            def close(self):
                try:
                    os.close(self._r)
                except OSError:
                    pass
                try:
                    os.close(self._w)
                except OSError:
                    pass

        resp = _InfiniteResp()

        # Device changes after 2 successful queries
        call_count = [0]
        def _device():
            call_count[0] += 1
            if call_count[0] > 2:
                return "MacBook Pro Speakers"
            return self._AIRPODS_NAME

        with patch("airpods_say.subprocess.Popen", return_value=proc):
            with patch("airpods_say.get_default_output_device_name",
                       side_effect=_device):
                with patch("airpods_say.signal.signal"), \
                        patch("airpods_say.os.set_blocking"):
                    proc.stdin.write.side_effect = lambda data: len(data)
                    try:
                        rc = play_stream(
                            resp=resp,
                            ffplay_path="/usr/bin/ffplay",
                            expected_device_name=self._AIRPODS_NAME,
                            debug=False,
                            poll_interval=0.02,
                        )
                    finally:
                        resp.close()

        self.assertEqual(rc, 3)


# ---------------------------------------------------------------------------
# Real HTTP framing, buffering, inactivity, and cancellation regressions
# ---------------------------------------------------------------------------

class _Player:
    """State changes follow stdin EOF/termination, not poll call counts."""

    def __init__(self, eof_status=0):
        self.returncode = None
        self.eof_status = eof_status
        self.audio = bytearray()
        self.closed = False
        self.reaped = False
        self.killed = False
        self.on_write = None
        self.stdin = self
        self.stderr = io.BytesIO()

    def fileno(self):
        return 9999  # os.set_blocking is patched; no real player is launched

    def write(self, data):
        if self.on_write:
            self.on_write(data)
        self.audio.extend(data)
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True
        if self.returncode is None:
            self.returncode = self.eof_status

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        self.reaped = True
        return self.returncode


class TestStreamReader(unittest.TestCase):
    def setUp(self):
        self.name = "Test AirPods"
        self.sockets = []
        self.threads = []
        self.release = threading.Event()
        self.queue_full = threading.Event()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.release.set()
        for sock in self.sockets:
            sock.close()
        for thread in self.threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

    def response(self, initial=b"", chunked=False):
        client, server = socket.socketpair()
        client.settimeout(2)
        server.settimeout(2)
        self.sockets.extend((client, server))
        header = (b"Transfer-Encoding: chunked\r\n" if chunked else
                  b"Content-Length: 1000000\r\n")
        server.sendall(b"HTTP/1.1 200 OK\r\n" + header + b"\r\n" + initial)
        resp = http.client.HTTPResponse(client)
        resp.begin()
        return resp, server

    def play(self, resp, proc=None, device=None, timeout=0.08):
        proc = proc or _Player()
        reader_type = airpods_say._StreamReader

        def observe_reader(response):
            reader = reader_type(response)
            self.assertIsInstance(reader.socket, socket.socket)
            put = reader.chunks.put

            def observed_put(chunk, **kwargs):
                put(chunk, **kwargs)
                if reader.chunks.full():
                    self.queue_full.set()

            reader.chunks.put = observed_put
            return reader

        with patch("airpods_say._StreamReader", side_effect=observe_reader), \
                patch("airpods_say.subprocess.Popen", return_value=proc), \
                patch("airpods_say.get_default_output_device_name",
                      side_effect=device or (lambda: self.name)), \
                patch("airpods_say.os.set_blocking"), \
                patch("airpods_say.signal.signal"), \
                patch("sys.stderr", io.StringIO()) as stderr:
            rc = play_stream(resp, "/fake/ffplay", self.name, False,
                             poll_interval=0.01, timeout=timeout)
        self.assertTrue(proc.closed)
        self.assertTrue(proc.reaped)
        self.assertTrue(resp.closed)
        self.assertFalse(any(t.name == "airpods-stream-reader" and t.is_alive()
                             for t in threading.enumerate()))
        self.assertNotIn("Traceback", stderr.getvalue())
        return rc, proc

    def test_headers_then_no_body_times_out_and_cancels_reader(self):
        resp, _ = self.response()
        start = time.monotonic()
        rc, _ = self.play(resp)
        self.assertEqual(rc, 5)
        self.assertLess(time.monotonic() - start, 1)

    def test_partial_body_then_stall_delivers_audio_and_returns_5(self):
        resp, _ = self.response(b"audio-prefix")
        rc, proc = self.play(resp)
        self.assertEqual(rc, 5)
        self.assertEqual(proc.audio, b"audio-prefix")

    def test_buffered_chunked_bytes_arrive_without_another_packet(self):
        audio = b"x" * 6000
        resp, _ = self.response(b"1770\r\n" + audio + b"\r\n", chunked=True)
        # begin() buffered the body while parsing headers. The raw descriptor
        # is not readable: gating read1 on select would strand these bytes.
        import select
        self.assertEqual(select.select([resp], [], [], 0)[0], [])
        rc, proc = self.play(resp)
        self.assertEqual(rc, 5)  # no terminal chunk; audio still must be delivered
        self.assertEqual(proc.audio, audio)

    def test_chunked_response_larger_than_read_size_is_decoded(self):
        size = airpods_say.STREAM_CHUNK * 3 + 31
        audio = b"a" * size
        resp, _ = self.response(f"{size:x}\r\n".encode() + audio +
                                b"\r\n0\r\n\r\n", chunked=True)
        rc, proc = self.play(resp)
        self.assertEqual(rc, 0)
        self.assertEqual(proc.audio, audio)

    def test_regular_progress_can_exceed_total_timeout(self):
        resp, server = self.response(chunked=True)
        finished = threading.Event()

        def send():
            try:
                for _ in range(8):
                    server.sendall(b"1\r\nx\r\n")
                    if self.release.wait(0.02):
                        return
                server.sendall(b"0\r\n\r\n")
            finally:
                finished.set()

        thread = threading.Thread(target=send)
        self.threads.append(thread)
        thread.start()
        start = time.monotonic()
        rc, proc = self.play(resp, timeout=0.12)
        self.assertEqual(rc, 0)
        self.assertEqual(proc.audio, b"x" * 8)
        self.assertGreater(time.monotonic() - start, 0.12)
        self.assertTrue(finished.wait(1))

    def test_network_worker_error_is_5_and_sanitized(self):
        resp, _ = self.response(b"invalid-chunk\r\n", chunked=True)
        rc, _ = self.play(resp)
        self.assertEqual(rc, 5)

    def test_device_change_wins_while_reader_is_blocked(self):
        resp, _ = self.response()
        rc, _ = self.play(resp, device=lambda: "Built-in Speakers")
        self.assertEqual(rc, 3)

    def test_player_failure_after_eof_is_not_success(self):
        for status in (1, -9):
            with self.subTest(status=status):
                resp, _ = self.response(b"1\r\nx\r\n0\r\n\r\n", chunked=True)
                rc, _ = self.play(resp, proc=_Player(eof_status=status))
                self.assertEqual(rc, 1)

    def test_queue_backpressure_does_not_turn_healthy_audio_into_timeout(self):
        # More audio than a bounded queue can hold; pause the consumer while
        # the worker fills it. Pending EOF must survive queue pressure too.
        audio = b"x" * 32000
        resp, server = self.response(chunked=True)
        finished = threading.Event()

        def send():
            server.sendall(f"{len(audio):x}\r\n".encode() + audio +
                           b"\r\n0\r\n\r\n")
            finished.set()

        thread = threading.Thread(target=send)
        self.threads.append(thread)
        thread.start()
        proc = _Player()
        paused = []

        def pause_once(data):
            if not paused:
                paused.append(True)
                self.assertTrue(self.queue_full.wait(1))
                self.release.wait(0.15)

        proc.on_write = pause_once
        rc, _ = self.play(resp, proc=proc, timeout=0.1)
        self.assertEqual(rc, 0)
        self.assertEqual(proc.audio, audio)
        self.assertTrue(finished.wait(1))

    def test_worker_error_after_full_queue_is_not_lost(self):
        audio = b"x" * 32000
        resp, server = self.response(chunked=True)

        def send():
            server.sendall(f"{len(audio):x}\r\n".encode() + audio +
                           b"\r\ninvalid-chunk\r\n")

        thread = threading.Thread(target=send)
        self.threads.append(thread)
        thread.start()
        proc = _Player()
        paused = []

        def pause_once(data):
            if not paused:
                paused.append(True)
                self.assertTrue(self.queue_full.wait(1))
                self.release.wait(0.12)

        proc.on_write = pause_once
        rc, _ = self.play(resp, proc=proc, timeout=0.1)
        self.assertEqual(rc, 5)
        self.assertEqual(proc.audio, audio)

    def test_nonblocking_player_stall_still_allows_device_cancellation(self):
        resp, server = self.response(chunked=True)

        def send():
            try:
                server.sendall(b"7d00\r\n" + b"x" * 32000 + b"\r\n")
            except OSError:
                pass

        thread = threading.Thread(target=send)
        self.threads.append(thread)
        thread.start()
        proc = _Player()
        with patch.object(proc, "write", return_value=None):
            rc, _ = self.play(resp, proc=proc, timeout=0.5,
                              device=lambda: "Built-in Speakers"
                              if self.queue_full.is_set() else self.name)
        self.assertEqual(rc, 3)
        self.assertTrue(self.queue_full.is_set())

    def test_cancellation_with_full_queue_releases_reader(self):
        resp, server = self.response(chunked=True)
        changed = threading.Event()

        def send():
            try:
                server.sendall(b"7d00\r\n" + b"x" * 32000 + b"\r\n")
            except OSError:
                pass  # cancellation also closes the test-owned sender

        thread = threading.Thread(target=send)
        self.threads.append(thread)
        thread.start()
        proc = _Player()

        def pause(data):
            self.assertTrue(self.queue_full.wait(1))
            self.release.wait(0.08)
            changed.set()
            self.release.wait(0.03)

        proc.on_write = pause
        rc, _ = self.play(resp, proc=proc, timeout=0.5,
                          device=lambda: "Built-in Speakers" if changed.is_set()
                          else self.name)
        self.assertEqual(rc, 3)
        # The peer's send may remain blocked until test-owned socket teardown;
        # _cleanup closes both ends and asserts every sender thread has stopped.

    def test_late_enqueue_at_empty_timeout_is_not_lost_to_eof(self):
        import queue
        resp, _ = self.response(b"1\r\nx\r\n0\r\n\r\n", chunked=True)
        real_queue = queue.Queue
        reader_finished = threading.Event()
        real_reader = airpods_say._StreamReader

        class RacingQueue(real_queue):
            def get(self, block=True, timeout=None):
                if block:
                    # Model a get timeout followed immediately by a producer
                    # enqueue+EOF, before the consumer examines done.
                    if not reader_finished.wait(1):
                        raise AssertionError("reader did not finish")
                    raise queue.Empty
                return super().get(block=block, timeout=timeout)

        class FinishedReader(real_reader):
            def _read(self):
                try:
                    super()._read()
                finally:
                    reader_finished.set()

        with patch("airpods_say.queue.Queue", RacingQueue), \
                patch("airpods_say._StreamReader", FinishedReader):
            rc, proc = self.play(resp)
        self.assertEqual(rc, 0)
        self.assertEqual(proc.audio, b"x")

    def test_truncated_fixed_length_body_is_not_clean_eof(self):
        resp, server = self.response(b"partial")
        server.shutdown(socket.SHUT_WR)
        rc, proc = self.play(resp)
        self.assertEqual(rc, 5)
        self.assertEqual(proc.audio, b"partial")

    def test_real_inert_player_pipe_is_nonblocking_closed_and_reaped(self):
        # An inert Python child stands in for ffplay: no decoder, audio output,
        # or physical device is used. Exercise real FileIO/OS pipe semantics.
        for status in (0, 1):
            with self.subTest(status=status):
                audio = b"x" * 4096
                resp, _ = self.response(b"1000\r\n" + audio +
                                        b"\r\n0\r\n\r\n", chunked=True)
                proc = subprocess.Popen(
                    [sys.executable, "-c",
                     f"import sys; data=sys.stdin.buffer.read(); "
                     f"print(len(data)); sys.exit({status})"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                )
                try:
                    with patch("airpods_say.subprocess.Popen", return_value=proc), \
                            patch("airpods_say.get_default_output_device_name",
                                  return_value=self.name), \
                            patch("sys.stderr", io.StringIO()):
                        rc = play_stream(resp, "/fake/ffplay", self.name, False,
                                         timeout=0.5, poll_interval=0.01)
                    self.assertEqual(rc, status)
                    self.assertEqual(proc.returncode, status)
                    self.assertTrue(proc.stdin.closed)
                    self.assertTrue(resp.closed)
                    self.assertEqual(proc.stdout.read().strip(), b"4096")
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait(timeout=2)
                    proc.stdin.close()
                    proc.stdout.close()

    def test_loopback_http_stall_is_bounded_and_closes_real_response(self):
        import http.server
        from test_airpods_voice_payload import loopback_server, request
        finished = threading.Event()
        release = self.release

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.flush()
                release.wait(2)
                finished.set()

        with loopback_server(Handler) as port:
            with patch("airpods_say.ELEVENLABS_STREAM_URL",
                       f"http://127.0.0.1:{port}/{{voice_id}}"):
                resp = request(timeout=1)
                rc, _ = self.play(resp)
                self.assertEqual(rc, 5)
            release.set()
            self.assertTrue(finished.wait(1))


class TestDrainFailedStatus(unittest.TestCase):
    def test_termination_timeout_kills_and_reaps(self):
        proc = _make_proc([None])
        proc.wait.side_effect = [subprocess.TimeoutExpired("fake-player", 3), -9]
        airpods_say._stop_ffplay(proc)
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
        self.assertEqual(proc.wait.call_count, 2)
        proc.wait.assert_called_with(timeout=3)

    def test_nonzero_and_signal_statuses_fail_immediately_or_later(self):
        for status in (1, 42, -9, -15):
            for polls in ([status], [None, None, status]):
                with self.subTest(status=status, polls=polls), \
                        patch("airpods_say._DRAIN_POLL_SECS", 0.001):
                    self.assertEqual(_drain_ffplay(
                        _make_proc(polls), threading.Event(), 8000, False), 1)


# ---------------------------------------------------------------------------
# Constant sanity checks
# ---------------------------------------------------------------------------

class TestDrainConstants(unittest.TestCase):
    def test_bytes_per_sec_matches_128kbps(self):
        self.assertEqual(_MP3_128K_BYTES_PER_SEC, 128_000 // 8)
        self.assertEqual(_MP3_128K_BYTES_PER_SEC, 16_000)

    def test_min_less_than_max(self):
        self.assertLess(_DRAIN_TIMEOUT_MIN_SECS, _DRAIN_TIMEOUT_MAX_SECS)

    def test_scale_greater_than_one(self):
        self.assertGreater(_DRAIN_TIMEOUT_SCALE, 1.0,
                           "Scale < 1 would not give buffer margin")

    def test_poll_secs_reasonable(self):
        self.assertGreater(_DRAIN_POLL_SECS, 0)
        self.assertLess(_DRAIN_POLL_SECS, 1.0,
                        "Poll interval >= 1 s is too coarse for device-change detection")


if __name__ == "__main__":
    unittest.main(verbosity=2)
