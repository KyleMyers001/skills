"""Early and late real signals, isolated from the unittest runner."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


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
mutations, paths, resources = [], [], []
target, coord = MagicMock(), MagicMock()
for uid, sp in (("target", target), ("coord", coord)):
    sp.uid, sp.is_visible = uid, True
    sp.is_satellite, sp.has_satellites = False, False
    sp.zone_group_state.has_subscriptions = False
    sp.volume, sp.ip_address = 30, "192.0.2.2"

def group(coordinator, members):
    view = types.SimpleNamespace(coordinator=coordinator, members=members)
    for member in members:
        member.group = view

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
    mutations.append("rejoin")
    group(prior, [prior, target])

target.unjoin.side_effect, target.join.side_effect = unjoin, join

def playback(*args):
    interrupt("playback")
    return True

def server_exit(*args):
    interrupt("server-exit")
    resources.append("server-closed")
    return False

def restoration(**kwargs):
    interrupt("restoration")
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
with patch("sonos_say.discover_speaker", discover), \
     patch("sonos_say.validate_announcement_target", validation), \
     patch("sonos_say.get_api_key", return_value="dummy-key"), \
     patch("sonos_say.local_ip_toward", return_value="192.0.2.1"), \
     patch("sonos_say.synthesise_mp3", synthesis), \
     patch("sonos_say.wait_for_playback", playback), \
     patch("sonos_say.tempfile.TemporaryDirectory", Temporary), \
     patch("sonos_say.serve_file") as serve, \
     patch("sonos_say.Snapshot") as snapshot:
    serve.return_value.__enter__.return_value = "http://192.0.2.1/audio"
    serve.return_value.__exit__.side_effect = server_exit
    snapshot.return_value.media_uri = "prior-uri"
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
        return evidence

    def test_signals_across_live_resource_lifetime(self):
        for phase in ("discovery", "validation", "synthesis", "playback",
                      "server-exit", "restoration", "temporary-exit"):
            for signame, outcome in (("SIGINT", 130), ("SIGTERM", 143)):
                with self.subTest(phase=phase, signal=signame):
                    result = self.run_child(phase, signame)
                    self.assertEqual(result["outcome"], outcome)
                    self.assertEqual(result["mutations"], [] if phase in (
                        "discovery", "validation", "synthesis") else
                        ["detach", "rejoin"])
                    if phase == "synthesis":
                        self.assertEqual(len(result["paths"]), 1)
                    if phase in ("playback", "server-exit", "restoration",
                                 "temporary-exit"):
                        self.assertEqual(result["resources"], ["server-closed"])

    def test_restoration_failure_wins_even_over_temporary_exit_signal(self):
        for signame in ("SIGINT", "SIGTERM"):
            with self.subTest(signal=signame):
                result = self.run_child("temporary-exit", signame, failure="yes")
                self.assertEqual(result["outcome"], "SONOS_RESTORE_FAILED")
                self.assertEqual(result["mutations"], ["detach", "rejoin"])
