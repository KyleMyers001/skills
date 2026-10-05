"""Installed Snapshot source contracts, isolated from parent SDK stubs."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


_DEADLINE_CONTRACT = r'''
import html
import sys
from types import SimpleNamespace
from unittest.mock import patch
import xml.etree.ElementTree as ET
import soco
import soco.config
import soco.services
from soco.snapshot import Snapshot
import sonos_say as say
import sonos_topology as topology
assert soco.__version__ == '0.31.2'
mode = sys.argv[1]
now, phase, layout = 0.0, 'setup', 'original'
calls, group_attempts = [], []
uri, transport = 'http://example.invalid/radio', 'PLAYING'
rendering = {'Volume': '30', 'Mute': '1', 'Bass': '0', 'Treble': '0',
             'Loudness': '1'}
target, coordinator = soco.SoCo('192.0.2.2'), soco.SoCo('192.0.2.3')
services = [getattr(target, name) for name in (
    'avTransport', 'renderingControl', 'zoneGroupTopology', 'deviceProperties')]
for speaker in (target, coordinator):
    speaker._household_id = 'deadline-test'
    # Model previously cached service metadata for the red cumulative-delay case.
    speaker.zoneGroupTopology._actions = [
        SimpleNamespace(name='GetZoneGroupState', in_args=[])]
# Cache a real SDK dispatcher before installing the per-instance guard.
cached_mute = target.renderingControl.SetMute

def member(uid, ip):
    return (f'<ZoneGroupMember UUID="{uid}" ZoneName="{uid}" '
            f'Location="http://{ip}:1400/xml/device_description.xml"/>')

def topology_xml():
    t, c = member('target', '192.0.2.2'), member('coord', '192.0.2.3')
    groups = (f'<ZoneGroup Coordinator="coord" ID="coord:1">{c}{t}</ZoneGroup>'
              if layout == 'original' else
              f'<ZoneGroup Coordinator="coord" ID="coord:1">{c}</ZoneGroup>'
              f'<ZoneGroup Coordinator="target" ID="target:1">{t}</ZoneGroup>')
    if layout == 'unsafe':
        groups = groups.replace(t, t + member('intruder', '192.0.2.4'))
    return '<ZoneGroupState><ZoneGroups>' + groups + '</ZoneGroups></ZoneGroupState>'

def network(url, *, headers, data, timeout):
    global now, layout, uri, transport
    action = headers['SOAPACTION'].split('#')[-1]
    node = ET.fromstring(data).find('{http://schemas.xmlsoap.org/soap/envelope/}Body')[0]
    args = {child.tag: child.text or '' for child in node}
    calls.append((phase, now, action, timeout, args))
    if phase != 'setup':
        assert 0 < timeout <= 5
        duration = (4.9 if phase == 'local' and mode not in ('healthy', 'interrupt')
                    else 0.01)
        now += min(duration, timeout)
        if duration > timeout:
            raise TimeoutError('bounded SOAP timeout')
        if mode == 'interrupt' and phase == 'local' and action == 'GetTransportInfo':
            raise KeyboardInterrupt()
    result = {}
    if action == 'GetZoneGroupState':
        result = {'ZoneGroupState': topology_xml()}
    elif action == 'GetMediaInfo':
        result = {'CurrentURI': uri, 'CurrentURIMetaData': ''}
    elif action == 'GetTransportInfo':
        result = {'CurrentTransportState': transport, 'CurrentTransportStatus': 'OK',
                  'CurrentSpeed': '1'}
    elif action.startswith('Get') and action[3:] in rendering:
        result = {'Current' + action[3:]: rendering[action[3:]]}
    elif action.startswith('Set') and action[3:] in rendering:
        rendering[action[3:]] = args['Desired' + action[3:]]
    elif action == 'SetAVTransportURI':
        if args['CurrentURI'] == 'x-rincon:coord':
            if mode == 'join-failure':
                raise TimeoutError('join failed')
            layout = 'original'
        else:
            uri = args['CurrentURI']
    elif action in ('Pause', 'Stop', 'Play'):
        transport = {'Pause': 'PAUSED_PLAYBACK', 'Stop': 'STOPPED',
                     'Play': 'PLAYING'}[action]
    else:
        raise AssertionError('unexpected SOAP action: ' + action)
    body = ''.join(f'<{key}>{html.escape(value)}</{key}>' for key, value in result.items())
    text = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
            f'<s:Body><u:{action}Response xmlns:u="urn:test">{body}'
            f'</u:{action}Response></s:Body></s:Envelope>')
    return SimpleNamespace(status_code=200, headers={}, text=text)

real_regroup = say.restore_target_group

def regroup(plan):
    global phase, layout
    group_attempts.append(now)
    phase = 'group'
    if mode == 'unsafe':
        layout = 'unsafe'
    elif mode == 'already':
        layout = 'original'
    return real_regroup(plan)

previous_config = (soco.config.REQUEST_TIMEOUT, soco.config.ZGT_EVENT_FALLBACK)
with patch.object(topology.time, 'monotonic', side_effect=lambda: now), \
     patch.object(soco.services.requests, 'post', side_effect=network), \
     patch.object(soco.services.requests, 'get', side_effect=AssertionError('unexpected GET')), \
     patch.object(say, 'restore_target_group', side_effect=regroup):
    plan = topology.validate_announcement_target(target)
    layout = 'isolated'
    topology.verify_isolated_target(plan)
    snapshot = Snapshot(target)
    snapshot.snapshot()
    target.play_uri('http://example.invalid/announcement')
    target.volume, target.mute = 35, False
    phase = 'local'
    failed, interrupt = say._restore_after_announcement(
        target, snapshot, True, 30, plan, True)
    assert group_attempts, ('rejoin was skipped', now, calls)
    assert len(group_attempts) == 1 and group_attempts[0] <= 30.5, group_attempts
    joins = [call for call in calls if call[2] == 'SetAVTransportURI'
             and call[4]['CurrentURI'] == 'x-rincon:coord']
    assert len(joins) == (0 if mode in ('unsafe', 'already') else 1), joins
    assert all(call[1] < 30 for call in calls if call[0] == 'local'), calls
    assert failed == (mode != 'healthy'), (mode, failed)
    assert isinstance(interrupt, KeyboardInterrupt) == (mode == 'interrupt')
    if mode not in ('unsafe', 'join-failure'):
        assert layout == 'original'
    if mode == 'healthy':
        assert rendering['Mute'] == '1' and rendering['Volume'] == '30'
        assert uri == snapshot.media_uri and transport == snapshot.transport_state
    assert any(call[2] == 'GetZoneGroupState' for call in calls if call[0] == 'group')
    assert all('send_command' not in vars(service) for service in services)
    assert cached_mute is target.renderingControl.SetMute
assert (soco.config.REQUEST_TIMEOUT, soco.config.ZGT_EVENT_FALLBACK) == previous_config
print('snapshot deadline and safe regrouping verified:', mode)
'''


class TestInstalledSnapshotContracts(unittest.TestCase):
    def test_slow_snapshot_reserves_safe_regrouping_at_actual_rpc_boundaries(self):
        for mode in ('slow', 'unsafe', 'already', 'join-failure', 'interrupt', 'healthy'):
            with self.subTest(mode=mode):
                result = subprocess.run(
                    [sys.executable, '-c', _DEADLINE_CONTRACT, mode],
                    env={'PATH': os.defpath, 'PYTHONPATH': str(Path(__file__).parent),
                         'PYTHONDONTWRITEBYTECODE': '1'},
                    capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('snapshot deadline and safe regrouping verified',
                              result.stdout)

    def test_bonded_primary_tv_and_empty_sources_with_real_sdk(self):
        code = r'''
from unittest.mock import MagicMock, patch
import soco
from soco.snapshot import Snapshot
import sonos_say as say
from sonos_test_support import FakeSpeaker, make_bonded_room
assert soco.__version__ == '0.31.2'
for uri, state in (('x-sonos-htastream:primary:spdif', 'PLAYING'), ('', 'STOPPED')):
    primary, satellites = make_bonded_room(FakeSpeaker('primary'))
    primary.bass, primary.treble, primary.loudness = 0, 0, True
    primary.pause = MagicMock(side_effect=lambda: setattr(primary, 'state', 'PAUSED_PLAYBACK'))
    primary.play = MagicMock(side_effect=lambda: setattr(primary, 'state', 'PLAYING'))
    primary.uri, primary.state = uri, state
    plan = say.validate_announcement_target(primary)
    snapshot = Snapshot(primary)
    snapshot.snapshot()
    primary.play_uri('http://example.invalid/announcement')
    primary.volume, primary.mute = 35, False
    with patch('sonos_topology._read_device_uid', return_value=primary.device_uid):
        failed, interrupt = say._restore_after_announcement(
            primary, snapshot, True, 30, plan, False, state_mutated=True)
    assert not failed and interrupt is None
    assert primary.uri == uri and primary.state == state
    assert primary.volume == 30 and primary.mute is True
    for member in (primary, *satellites):
        member.unjoin.assert_not_called()
        member.join.assert_not_called()
    for satellite in satellites:
        satellite.play_uri.assert_not_called()
        assert satellite._satellite_parent is primary
    if uri:
        assert primary.play_uri.call_args.kwargs['start'] is False
        primary.play.assert_called_once()
    else:
        primary.avTransport.SetAVTransportURI.assert_called_once()
print('bonded TV-input and empty-URI SDK restoration verified; firmware not tested')
'''
        result = subprocess.run(
            [sys.executable, '-c', code],
            env={'PATH': os.defpath, 'PYTHONPATH': str(Path(__file__).parent),
                 'PYTHONDONTWRITEBYTECODE': '1'},
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('bonded TV-input and empty-URI SDK restoration verified', result.stdout)

    def test_cloud_noop_and_supported_source_restoration_with_real_sdk(self):
        code = r'''
from soco.snapshot import Snapshot
import soco
import sonos_say as say
from sonos_test_support import FakeService
assert soco.__version__ == '0.31.2'
class Device:
    is_coordinator = True
    volume, mute, bass, treble, loudness = 30, True, 0, 0, True
    play_mode, cross_fade = 'NORMAL', False
    def __init__(self, uri):
        self.uri, self.state = uri, 'PLAYING'
        self.position, self.restores = 2, 0
        self.renderingControl = FakeService()
        self.zoneGroupTopology = FakeService()
        self.deviceProperties = FakeService()
        self.avTransport = FakeService(GetMediaInfo=lambda args:
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
