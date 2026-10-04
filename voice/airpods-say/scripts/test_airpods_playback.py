"""Playback readiness, write progress, completion, and child boundaries."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

import airpods_say as say
from airpods_test_support import _Player


class TestPlaybackLifecycle(unittest.TestCase):
    def play(self, data=b'audio', proc=None, device=None, timeout=0.1):
        proc = proc or _Player()
        proc.stderr = io.BytesIO(b'')
        response = io.BytesIO(data)
        with patch.object(say.subprocess, 'Popen', return_value=proc), \
                patch.object(say, 'get_default_output_device_name',
                             side_effect=device or (lambda: 'Test AirPods')), \
                patch.object(say.os, 'set_blocking'), \
                patch('sys.stderr', io.StringIO()) as diagnostic:
            result = say.play_stream(response, '/fake/player', 'Test AirPods',
                                     False, poll_interval=0.01, timeout=timeout)
        self.assertTrue(response.closed)
        self.assertTrue(proc.closed)
        self.assertTrue(proc.reaped)
        return result, diagnostic.getvalue()

    def test_empty_body_is_api_failure_even_when_player_exits_zero(self):
        self.assertEqual(self.play(b'')[0], 5)

    def test_first_monitor_sample_gates_all_writes(self):
        entered, release = threading.Event(), threading.Event()
        proc = _Player()
        wrote = threading.Event()
        proc.on_write = lambda data: wrote.set()
        assertions = []

        def device():
            entered.set()
            release.wait(1)
            return 'Test AirPods'

        def observe():
            if entered.wait(1):
                self.assertFalse(wrote.wait(0.03))
                assertions.append(bytes(proc.audio))
            release.set()

        thread = threading.Thread(target=observe)
        thread.start()
        try:
            self.assertEqual(self.play(proc=proc, device=device)[0], 0)
            self.assertEqual(assertions, [b''])
        finally:
            release.set()
            thread.join(2)

    def test_monitor_readiness_timeout_fails_closed(self):
        release = threading.Event()
        proc = _Player()

        def device():
            release.wait(0.3)
            return 'Test AirPods'

        try:
            result, diagnostic = self.play(proc=proc, device=device, timeout=0.03)
            self.assertEqual(result, 3)
            self.assertEqual(proc.audio, b'')
            self.assertEqual(diagnostic.count('AirPods output changed'), 1)
        finally:
            release.set()

    def test_partial_progress_resets_write_clock(self):
        proc = _Player()
        original = proc.write
        now = [0.0]

        def write(data):
            now[0] += 0.02
            return original(data[:1])

        with patch.object(proc, 'write', side_effect=write), \
                patch.object(say.time, 'monotonic', side_effect=lambda: now[0]):
            self.assertEqual(self.play(data=b'12345678', proc=proc, timeout=0.03)[0], 0)
        self.assertEqual(proc.audio, b'12345678')

    def test_completion_latched_before_reader_teardown(self):
        real_reader = say._StreamReader
        changed = threading.Event()

        class Reader(real_reader):
            def stop(self):
                changed.set()
                super().stop()

        with patch.object(say, '_StreamReader', Reader):
            result, diagnostic = self.play(device=lambda: 'Built-in Speakers'
                                           if changed.is_set() else 'Test AirPods')
        self.assertEqual(result, 0)
        self.assertNotIn('AirPods output changed', diagnostic)

    def test_device_change_at_drain_completion_wins_with_one_diagnostic(self):
        proc = _Player()
        changed = threading.Event()
        original = proc.close

        def close():
            changed.set()
            original()

        with patch.object(proc, 'close', side_effect=close):
            # Synchronize the monitor's failed sample before drain checks.
            def drain(process, event, *args):
                self.assertTrue(event.wait(1))
                return 3
            with patch.object(say, '_drain_ffplay', side_effect=drain):
                result, diagnostic = self.play(proc=proc, device=lambda:
                    'Built-in Speakers' if changed.is_set() else 'Test AirPods')
        self.assertEqual(result, 3)
        self.assertEqual(diagnostic.count('AirPods output changed'), 1)


class TestRealPlayerBoundaries(unittest.TestCase):
    def child(self, code, timeout=5):
        result = subprocess.run([sys.executable, '-c', code], capture_output=True,
                                text=True, timeout=timeout,
                                env={'PATH': os.defpath, 'PYTHONPATH': str(Path(__file__).parent)})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_alive_child_never_reading_real_pipe_is_bounded(self):
        result = self.child(r'''
import io, subprocess, sys
from unittest.mock import patch
import airpods_say as say
proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                        stdin=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
try:
    with patch.object(say.subprocess, 'Popen', return_value=proc), \
         patch.object(say, 'get_default_output_device_name', return_value='Test AirPods'):
        print(say.play_stream(io.BytesIO(b'x'*1000000), '/fake', 'Test AirPods', False,
                              poll_interval=.01, timeout=.1))
    assert proc.poll() is not None and proc.stdin.closed and proc.stderr.closed
finally:
    if proc.poll() is None: proc.kill()
    proc.wait(timeout=1)
''')
        self.assertEqual(result, '1')

    def test_noisy_stderr_exit_zero_latches_failure_and_never_prints_raw(self):
        result = self.child(r'''
import io, subprocess, sys
from unittest.mock import patch
import airpods_say as say
proc = subprocess.Popen([sys.executable, '-c',
    "import sys; sys.stdin.buffer.read(); sys.stderr.buffer.write(b'private decoder error'*100000)"],
    stdin=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
with patch.object(say.subprocess, 'Popen', return_value=proc), \
     patch.object(say, 'get_default_output_device_name', return_value='Test AirPods'), \
     patch('sys.stderr', io.StringIO()) as diagnostics:
    rc = say.play_stream(io.BytesIO(b'audio'), '/fake', 'Test AirPods', True, timeout=.5)
assert 'private decoder' not in diagnostics.getvalue()
assert proc.returncode == 0 and proc.stderr.closed
print(rc)
''')
        self.assertEqual(result, '1')

    @unittest.skipUnless(shutil.which('ffplay') and shutil.which('ffmpeg'),
                         'ffplay/ffmpeg unavailable')
    def test_real_decoder_dummy_audio_valid_garbage_and_truncated(self):
        # This is synthetic silence and explicitly dummy SDL output, never a device.
        fixture = subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi',
            '-i', 'anullsrc=r=44100:cl=mono', '-t', '0.3', '-f', 'mp3', 'pipe:1'],
            capture_output=True, timeout=5)
        self.assertEqual(fixture.returncode, 0)
        for data, expected in ((fixture.stdout, 0), (b'not an mp3', 1),
                               (fixture.stdout[:100], 1)):
            with self.subTest(size=len(data)), \
                    patch.dict(os.environ, {'SDL_AUDIODRIVER': 'dummy'}), \
                    patch.object(say, 'get_default_output_device_name',
                                 return_value='Test AirPods'), \
                    patch('sys.stderr', io.StringIO()):
                self.assertEqual(say.play_stream(io.BytesIO(data), shutil.which('ffplay'),
                    'Test AirPods', False, timeout=1), expected)

    def test_no_progress_variants_fail_as_player_stall(self):
        # A finite parent timeout also bounds a regression that removes the clock.
        for variant in ('none', 'zero', 'would-block'):
            with self.subTest(variant=variant):
                code = r'''
import io
from unittest.mock import patch
from airpods_test_support import _Player
import airpods_say as say
proc = _Player()
value = VARIANT
with patch.object(proc, 'write', return_value=0 if value == 'zero' else None,
                  side_effect=BlockingIOError() if value == 'would-block' else None), \
     patch.object(say.subprocess, 'Popen', return_value=proc), \
     patch.object(say, 'get_default_output_device_name', return_value='Test AirPods'), \
     patch.object(say.os, 'set_blocking'), patch('sys.stderr', io.StringIO()) as err:
    rc = say.play_stream(io.BytesIO(b'audio'), '/fake', 'Test AirPods', False, timeout=.03)
assert rc == 1 and proc.closed and proc.reaped
assert 'Playback write stalled.' in err.getvalue()
print(rc)
'''.replace('VARIANT', repr(variant))
                self.assertEqual(self.child(code), '1')
