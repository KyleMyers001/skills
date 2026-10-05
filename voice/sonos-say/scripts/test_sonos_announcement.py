"""Announcement transaction budgets, isolation, and state restoration."""
import contextlib
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

# Existing suite owns its SoCo stub; actual SDK checks run in subprocesses.
import test_sonos_say
import sonos_say as say
import sonos_topology as topology
from sonos_test_support import (
    FakeSpeaker, FakeSnapshot, TEST_CONFIG, make_bonded_room,
)


_REAL_WAIT = say.wait_for_playback
_REAL_EVENTS = say._TransportEvents
_REAL_READ_DEVICE_UID = topology._read_device_uid


class TestAnnouncementLifecycle(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, TEST_CONFIG, clear=True))
        self.speaker = FakeSpeaker()
        self.stack.enter_context(patch.object(
            topology, '_read_device_uid', side_effect=lambda sp: sp.device_uid,
        ))
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

    def test_ordinary_preplayback_drift_without_mutation_never_restores(self):
        self.recorder_factory.return_value.start.side_effect = self.add_room
        with self.assertRaises(say.SonosSayError) as raised:
            say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_TOPOLOGY_FAILED')
        self.assertEqual(self.snapshots[0].restores, 0)
        self.speaker.play_uri.assert_not_called()
        self.assertEqual(self.speaker.volume, 30)
        self.assertTrue(self.speaker.mute)

    def test_ordinary_cleanup_regrouping_never_restores_transport(self):
        for detached in (False, True):
            with self.subTest(detached=detached):
                self.speaker = FakeSpeaker()
                if detached:
                    self.group()
                def regroup(*args, **kwargs):
                    other = self.add_room()
                    self.speaker.group.coordinator = other
                    return True
                self.wait.side_effect = regroup
                with self.assertRaises(say.SonosSayError) as raised:
                    say.announce('Hello')
                self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
                self.assertEqual(self.snapshots[-1].restores, 0)
                self.assertEqual(self.speaker.volume, 30)
                self.assertTrue(self.speaker.mute)
                self.speaker.stop.assert_not_called()
                self.speaker.avTransport.SetAVTransportURI.assert_not_called()
                self.speaker.join.assert_not_called()

    def test_detached_read_only_refusal_performs_regroup_only(self):
        coord = self.group()
        self.recorder_factory.return_value.start.side_effect = RuntimeError('recorder')
        with self.assertRaises(say.SonosSayError) as raised:
            say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_PLAYBACK_FAILED')
        self.assertEqual(self.snapshots[0].restores, 0)
        self.speaker.join.assert_called_once_with(coord)
        self.speaker.play_uri.assert_not_called()
        self.assertEqual((self.speaker.mute, self.speaker.volume), (True, 30))

    def test_cleanup_retries_transient_pre_and_post_restore_samples(self):
        for failing_sample in (1, 2):
            with self.subTest(failing_sample=failing_sample):
                self.speaker = FakeSpeaker()
                make_bonded_room(self.speaker)
                calls = []
                def clear():
                    calls.append(True)
                    if len(calls) == failing_sample:
                        raise TimeoutError('temporary topology read failure')
                def finish(*args, **kwargs):
                    self.speaker.zone_group_state.on_poll = clear
                    return True
                self.wait.side_effect = finish
                with patch.object(topology.time, 'sleep') as sleep:
                    say.announce('Hello')
                self.assertEqual(len(calls), 4)
                sleep.assert_called_once_with(0.5)
                self.assertEqual(self.snapshots[-1].restores, 1)
                self.assertEqual((self.speaker.mute, self.speaker.volume), (True, 30))

    def test_healthy_cleanup_includes_final_isolation_sample(self):
        make_bonded_room(self.speaker)
        self.speaker.uri = ''
        calls, mute_writes = [], []
        mute = [True]
        def finish(*args, **kwargs):
            self.speaker.zone_group_state.on_poll = lambda: calls.append(True)
            return True
        self.wait.side_effect = finish
        def set_mute(value):
            mute_writes.append(value)
            mute[0] = value
        with patch.object(FakeSpeaker, 'mute', create=True,
                          new=property(lambda sp: mute[0],
                                       lambda sp, value: set_mute(value))):
            say.announce('Hello')
        self.assertEqual(len(calls), 3)
        self.assertEqual(mute_writes, [False, True])
        self.speaker.avTransport.SetAVTransportURI.assert_called_once()

    def test_replacement_after_restore_with_unavailable_topology_skips_local_writes(self):
        make_bonded_room(self.speaker)
        restore = FakeSnapshot.restore
        def unavailable():
            raise TimeoutError()
        def changed(snapshot, **kwargs):
            restore(snapshot, **kwargs)
            self.speaker.device_uid = 'replacement'
            self.speaker.volume, self.speaker.mute = 77, False
            self.speaker.zone_group_state.on_poll = unavailable
        now = [0.0]
        with patch.object(FakeSnapshot, 'restore', changed), \
                patch.object(topology.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(topology.time, 'sleep', side_effect=lambda dt: now.__setitem__(0, now[0] + dt)):
            with self.assertRaises(say.SonosSayError) as raised:
                say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
        self.assertEqual((self.speaker.mute, self.speaker.volume), (False, 77))
        self.speaker.avTransport.SetAVTransportURI.assert_not_called()

    def test_topology_and_live_identity_failure_never_write_to_replacement(self):
        make_bonded_room(self.speaker)
        def unavailable():
            raise TimeoutError()
        def finish(*args, **kwargs):
            self.speaker.device_uid = 'replacement'
            self.speaker.zone_group_state.on_poll = unavailable
            return True
        self.wait.side_effect = finish
        with self.assertRaises(say.SonosSayError) as raised:
            say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
        self.assertEqual(self.speaker.uid, 'target')  # SDK identity stays cached.
        self.assertEqual(self.snapshots[0].restores, 0)
        self.assertEqual((self.speaker.mute, self.speaker.volume), (False, 35))

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
            self.speaker.zone_group_state.on_poll = fail_cache
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
            self.speaker._uid = 'replacement-device'
            return True
        self.wait.side_effect = changed
        with self.assertRaises(say.SonosSayError) as raised:
            say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
        self.assertEqual(self.snapshots[0].restores, 0)
        self.assertEqual(self.speaker.volume, 30)
        self.assertTrue(self.speaker.mute)
        self.speaker.join.assert_not_called()
        self.speaker.avTransport.SetAVTransportURI.assert_not_called()

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

    def test_description_timeout_does_not_block_matching_topology(self):
        coord = self.group()
        reads = []

        def timeout(speaker):
            reads.append(speaker)
            raise TimeoutError('description timed out')

        with patch.object(topology, '_read_device_uid', side_effect=timeout):
            say.announce('Hello')
        self.assertEqual(reads, [])
        self.assertEqual(self.snapshots[-1].restores, 1)
        self.assertEqual((self.speaker.mute, self.speaker.volume), (True, 30))
        self.speaker.join.assert_called_once_with(coord)

    def test_regroup_only_cleanup_does_not_read_description(self):
        coord = self.group()
        self.recorder_factory.return_value.start.side_effect = RuntimeError('recorder')
        reads = []

        def timeout(speaker):
            reads.append(speaker)
            raise TimeoutError('description timed out')

        with patch.object(topology, '_read_device_uid', side_effect=timeout):
            with self.assertRaises(say.SonosSayError) as raised:
                say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_PLAYBACK_FAILED')
        self.assertEqual(reads, [])
        self.speaker.join.assert_called_once_with(coord)

    def test_device_uid_change_alone_still_restores_matching_topology(self):
        coord = self.group()

        def changed(*args, **kwargs):
            self.speaker.device_uid = 'replacement'
            return True

        self.wait.side_effect = changed
        say.announce('Hello')
        self.assertEqual(self.snapshots[-1].restores, 1)
        self.assertEqual((self.speaker.mute, self.speaker.volume), (True, 30))
        self.speaker.join.assert_called_once_with(coord)

    def test_unavailable_topology_and_bad_description_writes_nothing(self):
        for failure in ('timeout', 'mismatch'):
            with self.subTest(failure=failure):
                self.speaker = FakeSpeaker()
                self.group()

                def finish(*args, **kwargs):
                    def unavailable():
                        raise TimeoutError('topology')
                    self.speaker.zone_group_state.on_poll = unavailable
                    return True

                self.wait.side_effect = finish

                def read(speaker):
                    if failure == 'timeout':
                        raise TimeoutError('description')
                    return 'replacement'

                now = [0.0]
                with patch.object(topology, '_read_device_uid', side_effect=read), \
                        patch.object(topology.time, 'monotonic',
                                     side_effect=lambda: now[0]), \
                        patch.object(topology.time, 'sleep',
                                     side_effect=lambda dt: now.__setitem__(0, now[0] + dt)):
                    with self.assertRaises(say.SonosSayError) as raised:
                        say.announce('Hello')
                self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
                self.assertEqual(self.snapshots[-1].restores, 0)
                self.speaker.join.assert_not_called()
                self.speaker.avTransport.SetAVTransportURI.assert_not_called()
                self.assertEqual((self.speaker.mute, self.speaker.volume), (False, 35))

    def test_unavailable_topology_matching_description_is_local_only(self):
        self.group()

        def finish(*args, **kwargs):
            self.speaker.zone_group_state.on_poll = (
                lambda: (_ for _ in ()).throw(TimeoutError('topology'))
            )
            return True

        self.wait.side_effect = finish
        now = [0.0]
        with patch.object(topology.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(topology.time, 'sleep',
                             side_effect=lambda dt: now.__setitem__(0, now[0] + dt)):
            with self.assertRaises(say.SonosSayError) as raised:
                say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
        self.assertEqual(self.snapshots[-1].restores, 0)
        self.speaker.join.assert_not_called()
        self.assertEqual((self.speaker.mute, self.speaker.volume), (True, 30))

    def test_encoded_entity_cannot_authorize_fallback_rendering_writes(self):
        def unsafe(*args, **kwargs):
            self.add_room()
            return True

        self.wait.side_effect = unsafe
        xml = ('<?xml version="1.0" encoding="utf-16"?>'
               '<!DOCTYPE root [<!ENTITY identity "target">]>'
               '<root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
               '<UDN>uuid:&identity;</UDN></device></root>').encode('utf-16')
        with patch.object(topology, '_read_device_uid', _REAL_READ_DEVICE_UID), \
                patch('http.client.HTTPConnection') as connection:
            response = connection.return_value.getresponse.return_value
            response.status, response.read.return_value = 200, xml
            with self.assertRaises(say.SonosSayError) as raised:
                say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
        connection.return_value.request.assert_called_once()
        self.assertEqual((self.speaker.mute, self.speaker.volume), (False, 35))
        self.assertEqual(self.snapshots[-1].restores, 0)
        self.speaker.join.assert_not_called()

    def test_rpc_budget_failure_rejoins_once_and_wins_over_playback_interrupt(self):
        coord = self.group()
        self.wait.side_effect = KeyboardInterrupt()
        now, dispatches = [0.0], []

        def dispatch(action, args, timeout=5):
            dispatches.append((action, now[0], timeout))
            duration = min(4.9, timeout)
            now[0] += duration
            if duration < 4.9:
                raise TimeoutError('request timed out')

        self.speaker.avTransport.send_command = dispatch

        def slow_restore(snapshot, **kwargs):
            snapshot.restores += 1
            for _ in range(20):
                snapshot.device.avTransport.send_command('RestorePart', [])

        with patch.object(FakeSnapshot, 'restore', slow_restore), \
                patch.object(say.time, 'monotonic', side_effect=lambda: now[0]):
            with self.assertRaises(say.SonosSayError) as raised:
                say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
        self.assertEqual(self.snapshots[-1].restores, 1)
        self.assertTrue(dispatches)
        self.assertTrue(all(start < 30 for _, start, _ in dispatches))
        self.assertLessEqual(now[0], 30)
        self.speaker.join.assert_called_once_with(coord)
        self.assertIs(self.speaker.avTransport.send_command, dispatch)

    def test_grouped_after_snapshot_restore_is_not_uri_cleared(self):
        self.speaker.uri = ''
        restore = FakeSnapshot.restore

        def regroup(snapshot, **kwargs):
            restore(snapshot, **kwargs)
            self.add_room()

        with patch.object(FakeSnapshot, 'restore', regroup):
            with self.assertRaises(say.SonosSayError) as raised:
                say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
        self.speaker.avTransport.SetAVTransportURI.assert_not_called()
        self.speaker.join.assert_not_called()

    def test_later_regroup_after_post_restore_check_fails_without_join(self):
        class Tracking(FakeSpeaker):
            def __init__(self):
                self._volume = 30
                self._restoring = False
                super().__init__()

            @property
            def volume(self):
                return self._volume

            @volume.setter
            def volume(self, value):
                self._volume = value
                if (value == 30 and not self._restoring
                        and getattr(self, 'group', None) is not None):
                    other = FakeSpeaker('another-room')
                    other.group = self.group
                    self.group.members.append(other)

        self.speaker = Tracking()
        restore = FakeSnapshot.restore

        def wrapped(snapshot, **kwargs):
            snapshot.device._restoring = True
            try:
                restore(snapshot, **kwargs)
            finally:
                snapshot.device._restoring = False

        with patch.object(FakeSnapshot, 'restore', wrapped):
            with self.assertRaises(say.SonosSayError) as raised:
                say.announce('Hello')
        self.assertEqual(raised.exception.code, 'SONOS_RESTORE_FAILED')
        self.speaker.join.assert_not_called()
