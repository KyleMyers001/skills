"""Announcement transaction budgets, isolation, and state restoration."""
import contextlib
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

# Existing suite owns its SoCo stub; actual SDK checks run in subprocesses.
import test_sonos_say
import sonos_say as say
from sonos_test_support import (
    FakeSpeaker, FakeSnapshot, TEST_CONFIG, make_bonded_room,
)


_REAL_WAIT = say.wait_for_playback
_REAL_EVENTS = say._TransportEvents


class TestAnnouncementLifecycle(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, TEST_CONFIG, clear=True))
        self.speaker = FakeSpeaker()
        self.snapshots = []
        self.key = self.stack.enter_context(patch.object(say, 'get_api_key', return_value='dummy-key'))
        self.stack.enter_context(patch.object(say, 'discover_speaker', side_effect=lambda room: self.speaker))
        self.stack.enter_context(patch.object(say, 'local_ip_toward', return_value='192.0.2.1'))
        self.synth = self.stack.enter_context(patch.object(say, 'synthesise_mp3', side_effect=self.synthesis))
        self.stack.enter_context(patch.object(say, 'Snapshot', side_effect=self.snapshot))
        self.stack.enter_context(patch.object(say, 'serve_file')).return_value.__enter__.return_value = 'http://192.0.2.1/dummy/audio'
        self.recorder_factory = self.stack.enter_context(
            patch.object(say, '_TransportEvents', create=True))
        self.wait = self.stack.enter_context(patch.object(say, 'wait_for_playback', return_value=True))

    def synthesis(self, text, voice, model, key, path, **kwargs):
        path.write_bytes(b'audio')

    def snapshot(self, speaker):
        snap = FakeSnapshot(speaker)
        self.snapshots.append(snap)
        return snap

    def group(self):
        coord = FakeSpeaker('coord')
        self.speaker.group = coord.group = SimpleNamespace(coordinator=coord, members=[coord, self.speaker])
        return coord

    def test_bonded_primary_announces_without_group_or_satellite_rpcs(self):
        primary, satellites = make_bonded_room(self.speaker)
        original_group = primary.group
        original_uri, original_state = primary.uri, primary.state

        say.announce('Hello')

        primary.play_uri.assert_called_once()
        self.assertIs(primary.group, original_group)
        self.assertEqual((primary.uri, primary.state),
                         (original_uri, original_state))
        self.assertEqual(primary.volume, 30)
        self.assertTrue(primary.mute)
        for member in (primary, *satellites):
            member.unjoin.assert_not_called()
            member.join.assert_not_called()
        for satellite in satellites:
            satellite.play_uri.assert_not_called()
            self.assertIs(satellite._satellite_parent, primary)

    def add_room(self):
        other = FakeSpeaker('another-room')
        self.speaker.group.members.append(other)
        other.group = self.speaker.group
        return other

    def test_bonded_drift_during_synthesis_refused_before_snapshot(self):
        make_bonded_room(self.speaker)
        synth = self.synth.side_effect
        def regroup(*args, **kwargs):
            synth(*args, **kwargs)
            self.add_room()
        self.synth.side_effect = regroup

        with self.assertRaises(say.SonosSayError) as raised:
            say.announce('Hello')

        self.assertEqual(raised.exception.code, 'SONOS_TOPOLOGY_FAILED')
        self.assertEqual(self.snapshots, [])
        self.speaker.play_uri.assert_not_called()
        self.assertEqual(self.speaker.volume, 30)
        self.assertTrue(self.speaker.mute)

    def test_bonded_preplayback_drift_without_mutation_never_restores(self):
        make_bonded_room(self.speaker)
        self.recorder_factory.return_value.start.side_effect = self.add_room
        with self.assertRaises(say.SonosSayError) as raised:
            say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_TOPOLOGY_FAILED')
        self.assertEqual(self.snapshots[0].restores, 0)
        self.speaker.play_uri.assert_not_called()
        self.speaker.unjoin.assert_not_called()
        self.speaker.join.assert_not_called()
        self.assertTrue(self.speaker.mute)

    def test_bonded_cleanup_drift_avoids_transport_and_recovers_local_state(self):
        make_bonded_room(self.speaker)
        def regroup(*args, **kwargs):
            self.add_room()
            return True
        self.wait.side_effect = regroup
        with self.assertRaises(say.SonosSayError) as raised:
            say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
        self.assertEqual(self.snapshots[0].restores, 0)
        self.assertEqual(self.speaker.volume, 30)
        self.assertTrue(self.speaker.mute)
        self.speaker.stop.assert_not_called()
        self.speaker.avTransport.SetAVTransportURI.assert_not_called()
        self.speaker.unjoin.assert_not_called()
        self.speaker.join.assert_not_called()

    def test_bonded_cleanup_unavailable_topology_recovers_local_state(self):
        make_bonded_room(self.speaker)
        def fail_cache():
            raise RuntimeError('unavailable')
        def unavailable(*args, **kwargs):
            self.speaker.zone_group_state.clear_cache = fail_cache
            return True
        self.wait.side_effect = unavailable
        with self.assertRaises(say.SonosSayError) as raised:
            say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
        self.assertEqual(self.snapshots[0].restores, 0)
        self.assertEqual(self.speaker.volume, 30)
        self.assertTrue(self.speaker.mute)

    def test_bonded_changed_identity_does_not_restore_another_device(self):
        make_bonded_room(self.speaker)
        def changed(*args, **kwargs):
            self.speaker.uid = 'replacement-device'
            return True
        self.wait.side_effect = changed
        with self.assertRaises(say.SonosSayError) as raised:
            say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
        self.assertEqual(self.snapshots[0].restores, 0)
        self.assertEqual(self.speaker.volume, 35)
        self.assertFalse(self.speaker.mute)

    def test_bonded_restoration_rechecks_parent_links(self):
        primary, satellites = make_bonded_room(self.speaker)
        restore = FakeSnapshot.restore
        def change_parent(snapshot, **kwargs):
            restore(snapshot, **kwargs)
            satellites[0]._satellite_parent = FakeSpeaker('foreign-primary')
        with patch.object(FakeSnapshot, 'restore', change_parent):
            with self.assertRaises(say.SonosSayError) as raised:
                say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
        self.assertEqual(self.snapshots[0].restores, 1)
        self.assertEqual(primary.volume, 30)
        self.assertTrue(primary.mute)
        primary.unjoin.assert_not_called()
        primary.join.assert_not_called()

    def test_bonded_success_failure_and_signals_restore_the_original_state(self):
        primary, satellites = make_bonded_room(self.speaker)
        original = (primary.uri, primary.state, primary.mute, primary.volume)
        effects = (True, False, RuntimeError('playback'),
                   KeyboardInterrupt(), SystemExit(143))
        for effect in effects:
            with self.subTest(effect=effect):
                self.wait.side_effect = (
                    effect if isinstance(effect, BaseException) else None)
                self.wait.return_value = effect
                if effect is True:
                    say.announce('Hello')
                else:
                    with self.assertRaises(BaseException) as raised:
                        say.announce('Hello')
                    if isinstance(effect, KeyboardInterrupt):
                        self.assertIsInstance(raised.exception, KeyboardInterrupt)
                    elif isinstance(effect, SystemExit):
                        self.assertEqual(raised.exception.code, 143)
                    else:
                        self.assertEqual(raised.exception.code, 'SONOS_PLAYBACK_FAILED')
                self.assertEqual(
                    (primary.uri, primary.state, primary.mute, primary.volume),
                    original,
                )
                self.assertEqual(self.snapshots[-1].restores, 1)
                for satellite in satellites:
                    self.assertIs(satellite._satellite_parent, primary)
                    satellite.play_uri.assert_not_called()
                primary.unjoin.assert_not_called()
                primary.join.assert_not_called()

    def test_bonded_restore_failure_takes_precedence_over_playback_and_signals(self):
        make_bonded_room(self.speaker)
        for effect in (False, KeyboardInterrupt(), SystemExit(143)):
            with self.subTest(effect=effect):
                self.wait.side_effect = (
                    effect if isinstance(effect, BaseException) else None)
                self.wait.return_value = False
                with patch.object(FakeSnapshot, 'restore',
                                  side_effect=RuntimeError('restore')):
                    with self.assertRaises(say.SonosSayError) as raised:
                        say.announce('Hello')
                self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
                self.assertEqual(self.speaker.volume, 30)
                self.assertTrue(self.speaker.mute)

    def test_bonded_volume_rpc_mutating_then_failing_still_restores(self):
        make_bonded_room(self.speaker)
        volume = [30]
        def setter(value):
            volume[0] = value
            if value == 35:
                raise TimeoutError('volume applied before timeout')
        with patch.object(FakeSpeaker, 'volume', create=True,
                          new=property(lambda speaker: volume[0],
                                       lambda speaker, value: setter(value))):
            with self.assertRaises(say.SonosSayError) as raised:
                say.announce('Hello')
            self.assertEqual(raised.exception.code, 'SONOS_PLAYBACK_FAILED')
            self.assertEqual(self.speaker.volume, 30)
            self.assertTrue(self.speaker.mute)
        self.speaker.play_uri.assert_not_called()
        self.assertEqual(self.snapshots[0].restores, 1)

    def test_bonded_drift_after_restore_prevents_empty_uri_clear(self):
        primary, satellites = make_bonded_room(self.speaker)
        primary.uri = ''
        restore = FakeSnapshot.restore
        def changed(snapshot, **kwargs):
            restore(snapshot, **kwargs)
            satellites[0]._satellite_parent = FakeSpeaker('foreign')
        with patch.object(FakeSnapshot, 'restore', changed):
            with self.assertRaises(say.SonosSayError) as raised:
                say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
        primary.avTransport.SetAVTransportURI.assert_not_called()
        self.assertEqual(primary.volume, 30)
        self.assertTrue(primary.mute)

    def test_bonded_mute_restoration_failure_does_not_skip_volume(self):
        make_bonded_room(self.speaker)
        mute = [True]
        def setter(value):
            if value is True:
                raise RuntimeError('mute restoration failed')
            mute[0] = value
        with patch.object(FakeSpeaker, 'mute', create=True,
                          new=property(lambda speaker: mute[0],
                                       lambda speaker, value: setter(value))):
            with self.assertRaises(say.SonosSayError) as raised:
                say.announce('Hello')
            self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
            self.assertEqual(self.speaker.volume, 30)
        self.speaker.join.assert_not_called()
        self.speaker.unjoin.assert_not_called()

    def test_bonded_cloud_source_refused_before_credentials(self):
        make_bonded_room(self.speaker)
        self.speaker.uri = 'x-rincon-queue:target#6'
        with self.assertRaises(say.SonosSayError) as raised:
            say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_SOURCE_UNSUPPORTED')
        self.key.assert_not_called()
        self.synth.assert_not_called()
        self.assertEqual(self.snapshots, [])

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
        with patch.object(FakeSnapshot, 'snapshot', side_effect=RuntimeError('snapshot')):
            with self.assertRaises(say.SonosSayError):
                say.announce('Hello')
        self.assertTrue(self.speaker.mute)
        self.speaker.play_uri.assert_not_called()

    def test_mute_readback_mismatch_is_restore_failure(self):
        original = FakeSnapshot.restore
        def restore(snap, **kw):
            original(snap, **kw)
            snap.device.mute = False
        with patch.object(FakeSnapshot, 'restore', restore):
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
        original = FakeSnapshot.restore
        def restore(snap, **kw):
            original(snap, **kw)
            snap.device.uri = 'http://example.invalid/announcement'
        with patch.object(FakeSnapshot, 'restore', restore):
            with self.assertRaises(say.SonosSayError) as raised:
                say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
