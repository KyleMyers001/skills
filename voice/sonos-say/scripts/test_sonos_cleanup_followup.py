"""Offline follow-up contracts; real SDK probes run in clean subprocesses."""
import os
import subprocess
import sys
import unittest
from unittest.mock import patch, Mock

import test_sonos_say
import sonos_topology as topology


class FollowupTests(unittest.TestCase):
    def test_convergence_does_not_retry_permanent_errors(self):
        for message in ('polling unsupported', 'active subscriptions', 'malformed XML'):
            now = [0.0]
            read = Mock(side_effect=topology.TopologyError(message))
            with self.subTest(message=message), \
                    patch.object(topology.time, 'monotonic', side_effect=lambda: now[0]), \
                    patch.object(topology.time, 'sleep', side_effect=lambda dt: now.__setitem__(0, now[0] + dt)):
                with self.assertRaisesRegex(topology.TopologyError, message):
                    topology._poll(read, 2, retry_structural=True, wait_if_rejected=True)
            self.assertEqual(read.call_count, 1)

    def test_real_sdk_only_network_errors_are_temporary(self):
        self.sdk('''
from requests.exceptions import ConnectionError, Timeout, HTTPError, InvalidURL
from soco.exceptions import SoCoException, SoCoUPnPException, NotSupportedException
import sonos_topology as t
for error in (RuntimeError(), NotImplementedError(), SoCoException(),
              NotSupportedException(), InvalidURL(), HTTPError(),
              SoCoUPnPException('invalid action', '401', ''),
              SoCoUPnPException('invalid args', '402', ''),
              SoCoUPnPException('unsupported poll', '501', '')):
    assert not t._temporary_observation_error(error), type(error)
for error in (TimeoutError(), ConnectionError(), Timeout(), ConnectionResetError()):
    assert t._temporary_observation_error(error), type(error)
''')

    def test_subminimum_budget_never_dispatches_a_topology_read(self):
        from sonos_test_support import FakeSpeaker
        speaker = FakeSpeaker()
        speaker.zoneGroupTopology.GetZoneGroupState = Mock()
        with patch.object(topology.time, 'monotonic', return_value=0):
            with self.assertRaises(topology.TopologyUnavailable):
                topology.inspect_topology(speaker, timeout=0.2)
        speaker.zoneGroupTopology.GetZoneGroupState.assert_not_called()

    def test_on_time_response_accepts_subminimum_remainder(self):
        from sonos_test_support import FakeSpeaker
        speaker, now = FakeSpeaker(), [0.0]

        def respond(args, timeout):
            self.assertEqual(timeout, 0.7)
            now[0] = 0.6
            return {'ZoneGroupState': ''}

        speaker.zoneGroupTopology.GetZoneGroupState = respond
        with patch.object(topology.time, 'monotonic', side_effect=lambda: now[0]):
            plan = topology.inspect_topology(speaker, timeout=0.7)
        self.assertEqual(plan.speaker_uid, 'target')

    def test_detached_real_snapshot_restores_rendering_not_transport(self):
        self.sdk('''
from types import SimpleNamespace
from unittest.mock import patch, Mock
from soco.snapshot import Snapshot
import sonos_say as say
from sonos_test_support import FakeSpeaker
s, coord = FakeSpeaker(), FakeSpeaker('coord')
s.group = coord.group = SimpleNamespace(coordinator=coord, members=[coord,s])
plan = say.validate_announcement_target(s)
s.isolate()
s.bass, s.treble, s.loudness = 3, -2, True
snap = Snapshot(s)
snap.snapshot()
s.volume, s.mute, s.bass, s.treble, s.loudness = 35, False, 0, 0, False
with patch.object(snap, '_restore_coordinator', side_effect=AssertionError('detached transport')), \\
     patch.object(s, 'get_current_transport_info', side_effect=AssertionError('detached transport read')):
    failed, interrupt = say._restore_after_announcement(s, snap, True, 30, plan, True)
assert not failed and interrupt is None
assert snap.is_coordinator is True, 'local restore must preserve the captured role'
assert (s.volume,s.mute,s.bass,s.treble,s.loudness) == (30,True,3,-2,True)
s.join.assert_called_once_with(coord)
s.avTransport.SetAVTransportURI.assert_not_called()
''')

    def test_nonempty_restore_keeps_final_check_after_local_writes(self):
        import sonos_say as say
        from sonos_test_support import FakeSpeaker, FakeSnapshot
        speaker = FakeSpeaker()
        plan = topology.validate_announcement_target(speaker)
        snapshot = FakeSnapshot(speaker)
        snapshot.snapshot()
        with patch.object(say, 'verify_isolated_target', wraps=topology.verify_isolated_target) as verify:
            failed, _ = say._restore_after_announcement(speaker, snapshot, True, 30, plan, False)
        self.assertFalse(failed)
        self.assertEqual(verify.call_count, 3)  # Final check also follows local writes.

    def test_missing_plan_cannot_authorize_cleanup_writes(self):
        import sonos_say as say
        from sonos_test_support import FakeSpeaker, FakeSnapshot
        speaker = FakeSpeaker()
        snapshot = FakeSnapshot(speaker)
        snapshot.snapshot()
        speaker.volume, speaker.mute = 35, False
        failed, interrupt = say._restore_after_announcement(speaker, snapshot, True, 30, None, False)
        self.assertTrue(failed)
        self.assertIsNone(interrupt)
        self.assertEqual((speaker.volume, speaker.mute, snapshot.restores), (35, False, 0))

    def test_real_sdk_convergence_retries_only_missing_group(self):
        self.sdk('''
from unittest.mock import patch
from soco import SoCo
from soco.exceptions import SoCoUPnPException, NotSupportedException
import sonos_topology as t
s = SoCo('127.0.0.1')
s._household_id = 'convergence-contract'
xml = '<ZoneGroupState><ZoneGroups><ZoneGroup Coordinator="TARGET" ID="TARGET:1"><ZoneGroupMember UUID="TARGET" ZoneName="offline" Location="http://127.0.0.1:1400/xml/device_description.xml"/></ZoneGroup></ZoneGroups></ZoneGroupState>'
now = [0.0]
def pause(dt): now[0] += dt
with patch.object(t.time, 'monotonic', side_effect=lambda: now[0]), patch.object(t.time, 'sleep', side_effect=pause):
    with patch.object(s.zoneGroupTopology, 'GetZoneGroupState', side_effect=[
        {'ZoneGroupState': '<ZoneGroupState><ZoneGroups/></ZoneGroupState>'},
        {'ZoneGroupState': xml}]) as read:
        result = t._wait_for_topology(s, 'TARGET', frozenset({'TARGET'}), 2)
        assert result.speaker_uid == 'TARGET' and read.call_count == 2
        assert now[0] == 0.5
    for error in (SoCoUPnPException('unsupported', '501', ''),
                  SoCoUPnPException('action', '401', ''),
                  SoCoUPnPException('args', '402', ''),
                  NotSupportedException(), NotImplementedError()):
        before = now[0]
        with patch.object(s.zoneGroupTopology, 'GetZoneGroupState', side_effect=error) as read:
            try: t._wait_for_topology(s, 'TARGET', frozenset({'TARGET'}), 2)
            except t.TopologyError: pass
            else: raise AssertionError('permanent fault accepted')
        assert read.call_count == 1 and now[0] == before
    with patch.object(s.zoneGroupTopology, 'GetZoneGroupState', return_value={'ZoneGroupState': '<broken'}) as read:
        try: t._wait_for_topology(s, 'TARGET', frozenset({'TARGET'}), 2)
        except t.TopologyError: pass
        else: raise AssertionError('malformed XML accepted')
        assert read.call_count == 1
    with patch.object(type(s.zone_group_state), 'has_subscriptions', new=property(lambda self: True)), patch.object(s.zoneGroupTopology, 'GetZoneGroupState') as read:
        try: t._wait_for_topology(s, 'TARGET', frozenset({'TARGET'}), 2)
        except t.TopologyError: pass
        else: raise AssertionError('subscription accepted')
        read.assert_not_called()
''')

    def sdk(self, source):
        result = subprocess.run([sys.executable, '-B', '-c', source],
                                cwd=os.path.dirname(__file__), capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_snapshot_queue_never_fetches_unbounded_metadata(self):
        self.sdk('''
import time
from unittest.mock import patch
from soco import SoCo
from soco.snapshot import Snapshot
import sonos_topology as topology
s = SoCo('127.0.0.1')
s._household_id = 'offline-followup'
s.speaker_info.clear()
snap = Snapshot(s)
snap.is_coordinator = True
snap.is_playing_queue = True
snap.playlist_position = 2
snap.track_position = ''
snap.play_mode = 'NORMAL'
snap.cross_fade = False
snap.transport_state = 'STOPPED'
snap.volume = 20
snap.mute = False
snap.bass = snap.treble = 0
snap.loudness = True
calls = []
def dispatch(action, args=None, **kw):
    calls.append((action, args, kw))
    if action == 'GetZoneGroupState':
        return {'ZoneGroupState': '<ZoneGroupState><ZoneGroups><ZoneGroup Coordinator="RINCON_TEST" ID="RINCON_TEST:1"><ZoneGroupMember UUID="RINCON_TEST" ZoneName="offline" Location="http://127.0.0.1:1400/xml/device_description.xml"/></ZoneGroup></ZoneGroups></ZoneGroupState>'}
    return {'CurrentTransportState': 'STOPPED', 'CurrentTransportStatus': 'OK', 'CurrentSpeed': '1'}
with patch.object(s.zoneGroupTopology, 'send_command', side_effect=dispatch), \\
     patch.object(s.avTransport, 'send_command', side_effect=dispatch), \\
     patch.object(s.renderingControl, 'send_command', side_effect=dispatch), \\
     patch('soco.core.requests.get', side_effect=AssertionError('raw metadata HTTP')), \\
     patch.object(topology, '_read_device_uid', return_value='RINCON_TEST') as identity:
    plan = topology.validate_announcement_target(s)
    assert plan.speaker_uid == 'RINCON_TEST'  # Real SDK processed XML, not a UID stub.
    with topology.soco_deadline(s, time.monotonic() + 5):
        snap.restore()
    identity.assert_not_called()  # The validated topology UID suffices for #0.
    assert not any(a == 'Play' for a,args,k in calls), 'start=False must not play'
    with topology.soco_deadline(s, time.monotonic() + 5):
        s.play_from_queue(0)
    assert any(a == 'Play' for a,args,k in calls), 'default start must play'
    count = len(calls)
    try:
        with topology.soco_deadline(s, time.monotonic() - 1):
            s.play_from_queue(0)
    except TimeoutError:
        pass
    else:
        raise AssertionError('expired queue restoration dispatched')
    assert len(calls) == count
    assert 'play_from_queue' not in vars(s), 'failure must restore the SDK method'
assert any(a == 'SetAVTransportURI' and ('CurrentURI', 'x-rincon-queue:RINCON_TEST#0') in args for a,args,k in calls)
assert any(a == 'Seek' and ('Target', 2) in args for a,args,k in calls)
assert s.speaker_info == {}, 'must not poison the SDK metadata cache'
assert 'play_from_queue' not in vars(s), 'guard must be scoped'
''')
