"""Offline follow-up regressions using stateful SDK-shaped speakers."""
import contextlib
import http.client
import http.server
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

# Existing suite owns its SoCo stub; actual SDK checks run in subprocesses.
import test_sonos_say
import sonos_say as say
import sonos_topology as topology
from test_sonos_topology import _Household, _CachedSpeaker
from test_sonos_voice import TEST_CONFIG
import test_sonos_voice


class Speaker:
    is_visible = True
    is_satellite = False
    has_satellites = False
    ip_address = '192.0.2.2'

    def __init__(self, uid='target'):
        self.uid = uid
        self.volume = 30
        self.mute = True
        self.uri = 'http://example.invalid/radio'
        self.state = 'STOPPED'
        self.zone_group_state = SimpleNamespace(has_subscriptions=False,
                                               clear_cache=lambda: None)
        self.group = SimpleNamespace(coordinator=self, members=[self])
        self.avTransport = SimpleNamespace(GetMediaInfo=self.media,
                                           SetAVTransportURI=MagicMock(side_effect=self.set_uri))
        self.play_uri = MagicMock(side_effect=self.play)
        self.join = MagicMock(side_effect=self.regroup)
        self.unjoin = MagicMock(side_effect=self.isolate)
        self.stop = MagicMock(side_effect=lambda: setattr(self, 'state', 'STOPPED'))
        self.before_play = None

    @property
    def is_coordinator(self):
        return self.group.coordinator is self

    def media(self, args):
        return {'CurrentURI': self.uri, 'CurrentURIMetaData': ''}

    def set_uri(self, args):
        self.uri = dict(args)['CurrentURI']

    def play(self, uri, **kwargs):
        if self.before_play:
            self.before_play()
        self.uri, self.state = uri, 'PLAYING'

    def get_current_track_info(self):
        return {'uri': self.uri}

    def get_current_transport_info(self):
        return {'current_transport_state': self.state}

    def isolate(self):
        coord = self.group.coordinator
        coord.group = SimpleNamespace(coordinator=coord, members=[coord])
        self.group = SimpleNamespace(coordinator=self, members=[self])

    def regroup(self, coord):
        self.group = coord.group = SimpleNamespace(coordinator=coord, members=[coord, self])


class Snapshot:
    def __init__(self, speaker):
        self.device = speaker
        self.restores = 0
        self.is_playing_queue = False
        self.is_playing_cloud_queue = False

    def snapshot(self):
        self.media_uri = self.device.uri
        self.volume, self.mute = self.device.volume, self.device.mute
        self.transport_state = self.device.state

    def restore(self, fade=False):
        self.restores += 1
        self.device.uri = self.media_uri
        self.device.volume, self.device.mute = self.volume, self.mute
        self.device.state = self.transport_state


class TestAnnouncementRepairs(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, TEST_CONFIG, clear=True))
        self.speaker = Speaker()
        self.snapshots = []
        self.key = self.stack.enter_context(patch.object(say, 'get_api_key', return_value='dummy-key'))
        self.stack.enter_context(patch.object(say, 'discover_speaker', side_effect=lambda room: self.speaker))
        self.stack.enter_context(patch.object(say, 'local_ip_toward', return_value='192.0.2.1'))
        self.synth = self.stack.enter_context(patch.object(say, 'synthesise_mp3', side_effect=self.synthesis))
        self.stack.enter_context(patch.object(say, 'Snapshot', side_effect=self.snapshot))
        self.stack.enter_context(patch.object(say, 'serve_file')).return_value.__enter__.return_value = 'http://192.0.2.1/dummy/audio'
        self.stack.enter_context(patch.object(say, '_TransportEvents', create=True))
        self.wait = self.stack.enter_context(patch.object(say, 'wait_for_playback', return_value=True))

    def synthesis(self, text, voice, model, key, path, **kwargs):
        path.write_bytes(b'audio')

    def snapshot(self, speaker):
        snap = Snapshot(speaker)
        self.snapshots.append(snap)
        return snap

    def group(self):
        coord = Speaker('coord')
        self.speaker.group = coord.group = SimpleNamespace(coordinator=coord, members=[coord, self.speaker])
        return coord

    def test_automatic_budget_allows_five_minute_clip(self):
        self.synth.side_effect = lambda *a, **kw: a[4].write_bytes(b'x' * 4800000)
        real_wait = _REAL_WAIT
        now = [0.0]
        self.speaker.get_current_transport_info = lambda: {
            'current_transport_state': 'PLAYING' if now[0] < 300 else 'STOPPED'}
        def wait(sp, budget, **kw):
            self.assertEqual(budget, 330)
            return real_wait(sp, budget)
        self.wait.side_effect = wait
        with patch.object(say.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(say.time, 'sleep', side_effect=lambda dt: now.__setitem__(0, now[0] + dt)):
            say.announce('Hello')
        self.assertEqual(now[0], 300)

    def test_explicit_short_timeout_remains_hard_override(self):
        now = [0.0]
        self.speaker.get_current_transport_info = lambda: {'current_transport_state':
            'PLAYING' if self.speaker.uri.startswith('http://192.0.2.1/') else 'STOPPED'}
        self.wait.side_effect = lambda sp, budget, **kw: _REAL_WAIT(sp, budget)
        with patch.object(say.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(say.time, 'sleep', side_effect=lambda dt: now.__setitem__(0, now[0] + dt)):
            with self.assertRaises(say.SonosSayError) as raised:
                say.announce('Hello', timeout=2)
        self.assertEqual(raised.exception.code, 'SONOS_PLAYBACK_FAILED')
        self.assertEqual(now[0], 2)

    def test_separate_tts_timeout_reaches_synthesis(self):
        say.announce('Hello', tts_timeout=47)
        self.assertEqual(self.synth.call_args.kwargs['timeout'], 47)
        self.assertEqual(self.wait.call_args.args[1], 31)

    def test_complete_snapshot_precedes_unmute_and_restores_mute(self):
        self.speaker.before_play = lambda: self.assertIs(self.speaker.mute, False)
        say.announce('Hello')
        self.assertTrue(self.speaker.mute)
        self.assertEqual(self.speaker.volume, 30)
        self.assertEqual(self.snapshots[0].restores, 1)

    def test_mute_restored_on_timeout_play_error_and_signal(self):
        for effect in (False, RuntimeError('play failure'), KeyboardInterrupt(), SystemExit(143)):
            with self.subTest(effect=effect):
                self.wait.side_effect = effect if isinstance(effect, BaseException) else None
                self.wait.return_value = False
                with self.assertRaises(BaseException):
                    say.announce('Hello')
                self.assertTrue(self.speaker.mute)
                self.assertEqual(self.speaker.volume, 30)
                self.assertEqual(self.snapshots[-1].restores, 1)

    def test_short_playing_interval_during_play_rpc_is_retained(self):
        order = []
        queue = []
        class Subscription:
            sid, time_left, is_subscribed = 'test-attempt', 600, False
            def __init__(self, service, event_queue):
                queue.append(event_queue)
            def subscribe(self, **kwargs):
                order.append('subscribe'); self.is_subscribed = True
            def unsubscribe(self):
                order.append('unsubscribe'); self.is_subscribed = False
        class Listener:
            is_running = False
            _listener_thread = None
            def stop(self): self.is_running = False
        backend = SimpleNamespace(Subscription=Subscription, EventListener=Listener)
        def play(uri, **kwargs):
            order.append('play')
            self.speaker.uri, self.speaker.state = uri, 'STOPPED'
            import time
            for seq, state in ((1, 'PLAYING'), (2, 'STOPPED')):
                queue[0].put(SimpleNamespace(seq=str(seq), sid='test-attempt', timestamp=time.time(),
                    variables={'av_transport_uri':uri, 'transport_state':state}))
        self.speaker.play_uri.side_effect = play
        self.wait.side_effect = _REAL_WAIT
        with patch.object(say, '_TransportEvents', _REAL_EVENTS), \
                patch.object(say, '_threaded_events_backend', return_value=backend):
            say.announce('Hello')
        self.assertEqual(order, ['subscribe', 'play', 'unsubscribe'])
        self.assertTrue(queue[0].closed)
        self.assertEqual(self.snapshots[0].restores, 1)

    def test_snapshot_failure_does_not_unmute(self):
        with patch.object(Snapshot, 'snapshot', side_effect=RuntimeError('snapshot')):
            with self.assertRaises(say.SonosSayError):
                say.announce('Hello')
        self.assertTrue(self.speaker.mute)
        self.speaker.play_uri.assert_not_called()

    def test_mute_readback_mismatch_is_restore_failure(self):
        original = Snapshot.restore
        def restore(snap, **kw):
            original(snap, **kw)
            snap.device.mute = False
        with patch.object(Snapshot, 'restore', restore):
            with self.assertRaises(say.SonosSayError) as raised:
                say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')

    def test_unjoin_failure_without_mutation_preserves_phase_error(self):
        self.group()
        self.speaker.unjoin.side_effect = TimeoutError('before mutation')
        with self.assertRaises(say.SonosSayError) as raised:
            say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_TOPOLOGY_FAILED')
        self.speaker.join.assert_not_called()

    def test_detached_empty_uri_is_not_cleared(self):
        coord = self.group()
        self.speaker.uri = ''
        self.speaker.avTransport.SetAVTransportURI.side_effect = RuntimeError('firmware rejects empty')
        say.announce('Hello')
        self.speaker.avTransport.SetAVTransportURI.assert_not_called()
        self.speaker.join.assert_called_once_with(coord)
        self.assertEqual(self.speaker.group.members, [coord, self.speaker])

    def test_idle_standalone_rejected_clear_is_failure(self):
        self.speaker.uri = ''
        self.speaker.avTransport.SetAVTransportURI.side_effect = RuntimeError('reject')
        with self.assertRaises(say.SonosSayError) as raised:
            say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')

    def test_cloud_preflight_checks_playback_owner_before_sensitive_io(self):
        coord = self.group()
        coord.uri = 'x-rincon-queue:coord#6'
        with self.assertRaises(say.SonosSayError) as raised:
            say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_SOURCE_UNSUPPORTED')
        self.key.assert_not_called()
        self.synth.assert_not_called()
        self.speaker.unjoin.assert_not_called()
        self.speaker.play_uri.assert_not_called()
        self.assertTrue(self.speaker.mute)

    def test_cloud_changed_after_isolation_only_regroups(self):
        coord = self.group()
        isolate = self.speaker.unjoin.side_effect
        def detach():
            isolate()
            self.speaker.uri = 'x-rincon-queue:target#9'
        self.speaker.unjoin.side_effect = detach
        with self.assertRaises(say.SonosSayError) as raised:
            say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_SOURCE_UNSUPPORTED')
        self.assertTrue(self.speaker.mute)
        self.assertEqual(self.speaker.volume, 30)
        self.speaker.play_uri.assert_not_called()
        self.speaker.join.assert_called_once_with(coord)
        self.assertTrue(all(s.restores == 0 for s in self.snapshots))

    def test_cloud_changed_after_snapshot_is_not_restored_or_unmuted(self):
        coord = self.group()
        speaker = self.speaker
        class Recorder:
            def __init__(self, *args): pass
            def start(self): speaker.uri = 'x-rincon-queue:target#6'
            def close(self): pass
        with patch.object(say, '_TransportEvents', Recorder):
            with self.assertRaises(say.SonosSayError) as raised:
                say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_SOURCE_UNSUPPORTED')
        self.assertEqual(self.snapshots[0].restores, 0)
        self.assertTrue(speaker.mute)
        self.assertEqual(speaker.volume, 30)
        speaker.play_uri.assert_not_called()
        speaker.join.assert_called_once_with(coord)

    def test_cloud_changed_during_synthesis_refused_before_detach(self):
        self.group()
        synth = self.synth.side_effect
        def change(*args, **kw):
            synth(*args, **kw)
            self.speaker.group.coordinator.uri = 'x-rincon-queue:coord#6'
        self.synth.side_effect = change
        with self.assertRaises(say.SonosSayError) as raised:
            say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_SOURCE_UNSUPPORTED')
        self.speaker.unjoin.assert_not_called()

    def test_source_readback_mismatch_is_restore_failure(self):
        original = Snapshot.restore
        def restore(snap, **kw):
            original(snap, **kw)
            snap.device.uri = 'http://example.invalid/announcement'
        with patch.object(Snapshot, 'restore', restore):
            with self.assertRaises(say.SonosSayError) as raised:
                say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')


_REAL_WAIT = say.wait_for_playback
_REAL_EVENTS = say._TransportEvents


class TestIdempotentTopology(unittest.TestCase):
    def setUp(self):
        self.h = _Household()
        self.target, self.coord = _CachedSpeaker('target', self.h), _CachedSpeaker('coord', self.h)
        self.h.group_members(self.coord, [self.coord, self.target])
        self.clock = patch.object(topology.time, 'monotonic', side_effect=lambda: self.h.now)
        self.sleep = patch.object(topology.time, 'sleep', side_effect=self.h.sleep)
        self.clock.start(); self.sleep.start()
        self.addCleanup(self.clock.stop); self.addCleanup(self.sleep.stop)
        self.plan = topology.inspect_topology(self.target)

    def test_already_original_returns_without_join_or_delay(self):
        samples = self.h.samples
        topology.restore_target_group(self.plan)
        self.target.join.assert_not_called()
        self.assertEqual(self.h.now, 0)
        self.assertGreater(self.h.samples, samples)

    def test_recovery_armed_then_interrupt_before_rpc_is_noop(self):
        with self.assertRaises(KeyboardInterrupt):
            topology.detach_target(self.plan, before_unjoin=lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
        topology.restore_target_group(self.plan)
        self.target.unjoin.assert_not_called()
        self.target.join.assert_not_called()

    def test_wait_cycles_are_half_second_and_deadline_bounded(self):
        with self.assertRaises(topology.TopologyError):
            topology._wait_for_topology(self.target, 'target', frozenset({'target'}), 5)
        self.assertEqual(self.h.now, 5)
        self.assertEqual(self.h.samples, 12)  # plan plus initial sample and ten cycles
        self.assertEqual(topology.TOPOLOGY_POLL_SECONDS, 0.5)
        self.h.now = 0
        with self.assertRaises(topology.TopologyError):
            topology._wait_for_topology(self.target, 'target', frozenset({'target'}), 0.2)
        self.assertEqual(self.h.now, 0.2)

    def test_unrelated_target_group_is_not_rejoined(self):
        other = _CachedSpeaker('other', self.h)
        self.h.group_members(self.coord, [self.coord])
        self.h.group_members(other, [other, self.target])
        with self.assertRaises(topology.TopologyError):
            topology.restore_target_group(self.plan, timeout=0)
        self.target.join.assert_not_called()


class TestSynthesisBoundaries(unittest.TestCase):
    def test_raw_network_and_body_errors_are_sanitized(self):
        for error in (TimeoutError('private'), http.client.RemoteDisconnected('private'),
                      http.client.IncompleteRead(b'private')):
            with self.subTest(error=type(error).__name__), patch.object(say.voice_policy, 'tts_opener') as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaisesRegex(RuntimeError, '^ElevenLabs request failed$'):
                    say.synthesise_mp3('Hello', 'voice', 'eleven_v4', 'dummy', MagicMock())

    def test_empty_body_rejected_before_file_write(self):
        with patch.object(say.voice_policy, 'tts_opener') as opener:
            resp = opener.return_value.open.return_value
            resp.__enter__.return_value = resp
            resp.status = 200
            resp.headers.get.return_value = 'audio/mpeg'
            resp.read.return_value = b''
            dest = MagicMock()
            with self.assertRaises(RuntimeError):
                say.synthesise_mp3('Hello', 'voice', 'eleven_v4', 'dummy', dest)
            dest.write_bytes.assert_not_called()

    def test_delayed_headers_and_body_use_tts_budget(self):
        # Real loopback boundaries; every handler is released and joined.
        for phase in ('headers', 'body'):
            release, finished = threading.Event(), threading.Event()
            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self, *args): pass
                def do_POST(self):
                    try:
                        self.rfile.read(int(self.headers['Content-Length']))
                        if phase == 'body':
                            self.send_response(200)
                            self.send_header('Content-Type', 'audio/mpeg')
                            self.send_header('Content-Length', '4')
                            self.end_headers()
                            self.wfile.flush()
                        release.wait(2)
                    finally:
                        finished.set()
            server = test_sonos_voice.TestRedirectRejection()._server
            try:
                with server(Handler) as port, patch.object(say, 'ELEVENLABS_TTS_BASE', f'http://127.0.0.1:{port}'):
                    with self.assertRaisesRegex(RuntimeError, '^ElevenLabs request failed$'):
                        say.synthesise_mp3('Hello', 'voice', 'eleven_v4', 'dummy', MagicMock(), timeout=0.05)
            finally:
                release.set()
                self.assertTrue(finished.wait(2))


class TestInstalledSnapshotContracts(unittest.TestCase):
    def test_cloud_noop_and_supported_source_restoration_with_real_sdk(self):
        code = r'''
from types import SimpleNamespace
from soco.snapshot import Snapshot
import soco
import sonos_say as say
assert soco.__version__ == '0.31.2'
class Device:
    is_coordinator = True
    volume, mute, bass, treble, loudness = 30, True, 0, 0, True
    play_mode, cross_fade = 'NORMAL', False
    def __init__(self, uri):
        self.uri, self.state = uri, 'PLAYING'
        self.position, self.restores = 2, 0
        self.avTransport = SimpleNamespace(GetMediaInfo=lambda args:
            {'CurrentURI':self.uri, 'CurrentURIMetaData':''})
    def get_current_transport_info(self):
        return {'current_transport_state':self.state}
    def get_current_track_info(self):
        return {'uri':'http://example.invalid/per-track', 'playlist_position':str(self.position), 'position':'00:00:10'}
    def play_uri(self, uri, metadata='', start=True):
        self.uri, self.state = uri, 'PLAYING' if start else 'STOPPED'
    def play_from_queue(self, index, start=True):
        self.position = index + 1
        self.uri = 'x-rincon-queue:target#0'
        self.state = 'PLAYING' if start else 'STOPPED'
    def seek(self, position): pass
    def pause(self): self.state = 'PAUSED_PLAYBACK'
    def stop(self): self.state = 'STOPPED'
    def play(self): self.state = 'PLAYING'
for uri in ('x-rincon-queue:target#0', 'http://example.invalid/radio', 'x-file-cifs://example.invalid/file'):
    device = Device(uri)
    snapshot = Snapshot(device); snapshot.snapshot()
    device.play_uri('http://example.invalid/announcement')
    device.mute = False; device.volume = 35
    failed, interrupt = say._restore_after_announcement(device, snapshot, True, 30, None, False)
    assert not failed and interrupt is None
    assert device.uri == uri and device.state == 'PLAYING' and device.mute and device.volume == 30
    if '#0' in uri: assert device.position == 2
cloud = Device('x-rincon-queue:target#6')
snapshot = Snapshot(cloud); snapshot.snapshot()
cloud.play_uri('http://example.invalid/announcement')
snapshot.restore(fade=False)
assert snapshot.is_playing_cloud_queue
assert cloud.uri == 'http://example.invalid/announcement'  # SDK's known unsupported no-op
try: say._check_source_uri(snapshot.media_uri)
except say.SonosSayError as error: assert error.code == 'SONOS_SOURCE_UNSUPPORTED'
else: raise AssertionError('cloud source accepted')
print('cloud-noop reproduced; local queue/radio/file restored')
'''
        result = subprocess.run([sys.executable, '-c', code], env={
            'PATH': os.defpath, 'PYTHONPATH': str(Path(__file__).parent)},
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('cloud-noop reproduced', result.stdout)


class TestCleanupOnce(unittest.TestCase):
    def test_cleanup_action_is_not_replayed_after_interrupt(self):
        action = MagicMock(side_effect=[KeyboardInterrupt(), None])
        interrupts = []
        self.assertFalse(say._run_cleanup_step(action, interrupts, 'fixed'))
        action.assert_called_once()
        self.assertEqual(len(interrupts), 1)
