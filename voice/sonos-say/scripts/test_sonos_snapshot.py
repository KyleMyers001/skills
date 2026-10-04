"""Installed Snapshot source contracts, isolated from parent SDK stubs."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class TestInstalledSnapshotContracts(unittest.TestCase):
    def test_bonded_primary_tv_and_empty_sources_with_real_sdk(self):
        code = r'''
from unittest.mock import MagicMock
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
