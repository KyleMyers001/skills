"""Early and late real signals, isolated from the unittest runner."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

import test_sonos_say  # installs only this process's SoCo fixtures
import sonos_say


_CHILD = r'''
import json, os, signal, sys, tempfile, types
from pathlib import Path
from unittest.mock import MagicMock, patch
soco = types.ModuleType("soco")
soco.SoCo = MagicMock()
snap = types.ModuleType("soco.snapshot")
snap.Snapshot = MagicMock()
sys.modules["soco"] = soco
sys.modules["soco.snapshot"] = snap
import sonos_say as say
phase, signame, root, failure = sys.argv[1:]
signum = getattr(signal, signame)
previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
mutations, paths, resources, restore_calls = [], [], [], []
restoring = False
mute_state, volume_state = [True], [30]
target, coord = MagicMock(), MagicMock()
for uid, sp in (("target", target), ("coord", coord)):
    sp.uid = sp._uid = uid
    sp._is_satellite, sp._has_satellites = False, False
    sp.zone_group_state.has_subscriptions = False
    sp.zone_group_state.processed_count = 0
    sp.volume, sp.ip_address, sp.mute = 30, "192.0.2.2", False
    sp.avTransport.GetMediaInfo.return_value = {"CurrentURI": "prior-uri"}

def set_mute(value):
    if restoring:
        interrupt("mute-restoration")
    mute_state[0] = value

def set_volume(value):
    if restoring:
        interrupt("volume-restoration")
    volume_state[0] = value

type(target).mute = property(lambda self: mute_state[0], lambda self, v: set_mute(v))
type(target).volume = property(lambda self: volume_state[0], lambda self, v: set_volume(v))

def group(coordinator, members):
    view = types.SimpleNamespace(coordinator=coordinator, members=members)
    for member in members:
        member.group = view
        member.zone_group_state.visible_zones = set(members)

group(coord, [coord, target])
def interrupt(at):
    if phase == at:
        os.kill(os.getpid(), signum)

def discover(room):
    interrupt("discovery")
    return target

validate = say.validate_announcement_target
def validation(sp):
    interrupt("validation")
    return validate(sp)

def synthesis(text, voice, model, key, dest, **kwargs):
    dest.write_bytes(b"partial-audio")
    paths.append(str(dest))
    interrupt("synthesis")

def unjoin():
    mutations.append("detach")
    group(coord, [coord])
    group(target, [target])

def join(prior):
    interrupt("regrouping")
    mutations.append("rejoin")
    group(prior, [prior, target])

target.unjoin.side_effect, target.join.side_effect = unjoin, join

def playback(*args, **kwargs):
    interrupt("playback")
    return True

def server_exit(*args):
    interrupt("server-exit")
    resources.append("server-closed")
    return False

def event_exit():
    interrupt("event-exit")
    resources.append("event-closed")

def restoration(**kwargs):
    global restoring
    restore_calls.append(True)
    restoring = True
    interrupt("restoration")
    target.mute = True
    target.volume = 30
    if failure == "yes":
        raise RuntimeError("restore failed")

class Temporary(tempfile.TemporaryDirectory):
    def __init__(self, **kwargs):
        super().__init__(dir=root, **kwargs)
    def __exit__(self, *args):
        # Signal at context exit before deletion: must be deferred until the
        # real context has removed the speech and outcome has been evaluated.
        interrupt("temporary-exit")
        return super().__exit__(*args)

outcome = 0
with patch("sonos_topology._read_device_uid", side_effect=lambda sp: sp._uid), \
     patch("sonos_say.discover_speaker", discover), \
     patch("sonos_say.validate_announcement_target", validation), \
     patch("sonos_say.get_api_key", return_value="dummy-key"), \
     patch("sonos_say.local_ip_toward", return_value="192.0.2.1"), \
     patch("sonos_say.synthesise_mp3", synthesis), \
     patch("sonos_say.wait_for_playback", playback), \
     patch("sonos_say.tempfile.TemporaryDirectory", Temporary), \
     patch("sonos_say._TransportEvents") as event_type, \
     patch("sonos_say.serve_file") as serve, \
     patch("sonos_say.Snapshot") as snapshot:
    serve.return_value.__enter__.return_value = "http://192.0.2.1/audio"
    serve.return_value.__exit__.side_effect = server_exit
    snapshot.return_value.media_uri = "prior-uri"
    snapshot.return_value.mute = True
    event_type.return_value.close.side_effect = event_exit
    snapshot.return_value.restore.side_effect = restoration
    try:
        say.announce("Hello")
    except SystemExit as exc:
        outcome = exc.code
    except KeyboardInterrupt:
        outcome = 130
    except say.SonosSayError as exc:
        outcome = exc.code
print(json.dumps({"outcome": outcome, "mutations": mutations,
                  "paths": paths, "resources": resources,
                  "restore_calls": len(restore_calls), "mute": target.mute,
                  "volume": target.volume,
                  "files": list(Path(root).rglob("*")),
                  "handlers_restored": all(signal.getsignal(s) == h
                                           for s, h in previous.items())},
                 default=str))
'''


class TestScopedLifecycleSignals(unittest.TestCase):
    def run_child(self, phase, signame, failure="no"):
        with tempfile.TemporaryDirectory() as root:
            env = {
                "PATH": os.defpath,
                "PYTHONPATH": str(Path(__file__).resolve().parent),
                "ELEVENLABS_VOICE_ID": "test-voice",
                "SONOS_ROOM": "Test Room",
                "ELEVENLABS_KEYCHAIN_SERVICE": "test-service",
                "ELEVENLABS_KEYCHAIN_ACCOUNT": "test-account",
            }
            result = subprocess.run(
                [sys.executable, "-c", _CHILD, phase, signame, root, failure],
                env=env, capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            evidence = json.loads(result.stdout)
            self.assertEqual(evidence["files"], [])
            self.assertTrue(evidence["handlers_restored"])
            self.assertTrue(evidence["mute"])
            self.assertEqual(evidence["volume"], 30)
        return evidence

    def test_signals_across_live_resource_lifetime(self):
        for phase in ("discovery", "validation", "synthesis", "playback",
                      "event-exit", "server-exit", "restoration", "mute-restoration",
                      "volume-restoration", "regrouping", "temporary-exit"):
            for signame, outcome in (("SIGINT", 130), ("SIGTERM", 143)):
                with self.subTest(phase=phase, signal=signame):
                    result = self.run_child(phase, signame)
                    self.assertEqual(result["outcome"], outcome)
                    self.assertEqual(result["mutations"], [] if phase in (
                        "discovery", "validation", "synthesis") else
                        ["detach", "rejoin"])
                    if phase == "synthesis":
                        self.assertEqual(len(result["paths"]), 1)
                    early = phase in ("discovery", "validation", "synthesis")
                    self.assertEqual(result["restore_calls"], 0 if early else 1)
                    if not early:
                        self.assertEqual(result["resources"], ["event-closed", "server-closed"])

    def test_restoration_failure_wins_even_over_temporary_exit_signal(self):
        for signame in ("SIGINT", "SIGTERM"):
            with self.subTest(signal=signame):
                result = self.run_child("temporary-exit", signame, failure="yes")
                self.assertEqual(result["outcome"], "SONOS_RESTORE_FAILED")
                self.assertEqual(result["mutations"], ["detach", "rejoin"])


class TestCleanupIdempotence(unittest.TestCase):
    def test_cleanup_action_is_not_replayed_after_interrupt(self):
        action = MagicMock(side_effect=[KeyboardInterrupt(), None])
        interrupts = []
        self.assertFalse(sonos_say._run_cleanup_step(action, interrupts, 'fixed'))
        action.assert_called_once()
        self.assertEqual(len(interrupts), 1)
