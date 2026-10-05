"""
Unit tests for sonos_say.py.

Tests cover:
- CLI argument parsing and validation
- Voice-id / model identifier validation
- Pure helpers (local_ip_toward, get_api_key)
- Discovery (mock soco.discover)
- Orchestration: happy path, error paths, snapshot guard, SIGTERM
- HTTP server: correct file served, 404 for wrong paths, bounded shutdown
- wait_for_playback: two-phase startup race guard
- API-key secrecy regression

Run with:
    python -m pytest test_sonos_say.py -v
or:
    python -m unittest test_sonos_say -v
"""

from __future__ import annotations

import io
import contextlib
import logging
import os
import signal
import socket
import tempfile
import threading
import subprocess
import sys
import types
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Stub soco before importing the module under test
# ---------------------------------------------------------------------------

_soco_stub = types.ModuleType("soco")
_soco_stub.SoCo = MagicMock()
_soco_stub.discover = MagicMock(return_value=[])
_config_stub = types.ModuleType("soco.config")
_config_stub.REQUEST_TIMEOUT = 20.0
_config_stub.ZGT_EVENT_FALLBACK = True
_soco_stub.config = _config_stub
_exceptions_stub = types.ModuleType("soco.exceptions")


class _SoCoException(Exception):
    pass


_exceptions_stub.SoCoException = _SoCoException
_soco_stub.exceptions = _exceptions_stub

_snapshot_stub = types.ModuleType("soco.snapshot")


class _SnapshotStub:
    def __init__(self, device):
        self.device = device
        self._taken = False

    def snapshot(self):
        self._taken = True

    def restore(self, fade=False):
        pass


_snapshot_stub.Snapshot = _SnapshotStub

sys.modules.setdefault("soco", _soco_stub)
sys.modules.setdefault("soco.config", _config_stub)
sys.modules.setdefault("soco.exceptions", _exceptions_stub)
sys.modules.setdefault("soco.snapshot", _snapshot_stub)

import sonos_say  # noqa: E402


_TEST_ENV = patch.dict(os.environ, {
    "ELEVENLABS_VOICE_ID": "test_voice_123",
    "SONOS_ROOM": "Test Room",
    "ELEVENLABS_KEYCHAIN_SERVICE": "test-tts-service",
    "ELEVENLABS_KEYCHAIN_ACCOUNT": "test-tts-account",
}, clear=True)


_IDENTITY_FIXTURE = patch(
    'sonos_topology._read_device_uid', side_effect=lambda sp, **kw: sp._uid,
)


def setUpModule():
    _TEST_ENV.start()
    _EVENT_FIXTURE.start()
    _IDENTITY_FIXTURE.start()


def tearDownModule():
    _IDENTITY_FIXTURE.stop()
    _EVENT_FIXTURE.stop()
    _TEST_ENV.stop()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _set_standalone(sp: MagicMock) -> MagicMock:
    """Give a mock speaker an explicit, valid standalone topology."""
    for name in ('avTransport', 'renderingControl', 'zoneGroupTopology',
                 'deviceProperties'):
        getattr(sp, name).send_command = MagicMock()
    sp.uid = sp._uid = "RINCON_TEST_TARGET"
    sp._is_satellite = False
    sp._has_satellites = False
    sp.zone_group_state.visible_zones = {sp}
    sp.zone_group_state.has_subscriptions = False
    sp.zone_group_state.processed_count = 0
    sp.is_coordinator = True
    sp.mute = False
    sp.avTransport.GetMediaInfo.return_value = {"CurrentURI": "prior-uri"}
    sp.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}
    sp.group = SimpleNamespace(coordinator=sp, members=[sp])
    return sp


def _make_volume_tracked_speaker(
    initial_volume: int = 30,
) -> tuple[MagicMock, list[int]]:
    """
    Return (mock_speaker, volume_set_log).

    All speaker.volume assignments are recorded in volume_set_log.
    Reads always return initial_volume.
    """
    log: list[int] = []
    sp = _set_standalone(MagicMock())
    sp.ip_address = "192.168.1.50"
    type(sp).volume = property(
        lambda self: initial_volume,
        lambda self, v: log.append(v),
    )
    return sp, log


def _synthesise_fixture(*args, **kwargs):
    args[4].write_bytes(b"synthetic-mp3")


@contextlib.contextmanager
def _snapshot_patch():
    # Keep call assertions while supplying Snapshot's real public field shapes.
    with patch("sonos_say.Snapshot") as factory:
        snap = factory.return_value
        snap.media_uri, snap.mute, snap.transport_state = "prior-uri", False, "STOPPED"
        snap.is_playing_queue = snap.is_playing_cloud_queue = False

        def create(speaker):
            snap.mute = speaker.mute
            effect = snap.restore.side_effect
            def restore(**kwargs):
                if callable(effect):
                    effect(**kwargs)
                elif isinstance(effect, BaseException):
                    raise effect
                elif effect is not None:
                    result = next(effect)
                    if isinstance(result, BaseException):
                        raise result
                _restore_fixture(speaker, snap)
            snap.restore.side_effect = restore
            return snap

        factory.side_effect = create
        yield factory


def _restore_fixture(speaker, snapshot):
    speaker.mute = snapshot.mute
    speaker.avTransport.GetMediaInfo.return_value = {"CurrentURI": snapshot.media_uri or ""}
    if snapshot.media_uri:
        speaker.get_current_transport_info.side_effect = None
        speaker.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}


class _EventFixture:
    def __init__(self, speaker, uri):
        self.speaker = speaker
        self.queue = sonos_say._TransportEventQueue(uri)

    def start(self):
        pass

    def arm(self):
        self.queue.arm("test-attempt")
        self.speaker.avTransport.GetMediaInfo.return_value = {"CurrentURI": self.queue.uri}

    def close(self):
        self.queue.close()


_EVENT_FIXTURE = patch("sonos_say._TransportEvents", _EventFixture)


# ---------------------------------------------------------------------------
# Identifier validation
# ---------------------------------------------------------------------------

class TestElevenLabsIdValidation(unittest.TestCase):

    def test_valid_voice_id_accepted(self):
        sonos_say._validate_elevenlabs_id("test_voice_123", "voice-id")

    def test_valid_model_accepted(self):
        sonos_say._validate_elevenlabs_id("eleven_flash_v2_5", "model")

    def test_hyphens_and_underscores_allowed(self):
        sonos_say._validate_elevenlabs_id("my-model_v1", "model")

    def test_dots_allowed_in_model(self):
        sonos_say._validate_elevenlabs_id("eleven.v3-preview_1", "model")

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            sonos_say._validate_elevenlabs_id("", "voice-id")

    def test_too_long_raises(self):
        with self.assertRaises(ValueError):
            sonos_say._validate_elevenlabs_id("a" * 65, "voice-id")

    def test_exactly_64_chars_accepted(self):
        sonos_say._validate_elevenlabs_id("a" * 64, "voice-id")

    def test_slash_rejected(self):
        with self.assertRaises(ValueError):
            sonos_say._validate_elevenlabs_id("../../etc/passwd", "voice-id")

    def test_space_rejected(self):
        with self.assertRaises(ValueError):
            sonos_say._validate_elevenlabs_id("hello world", "voice-id")

    def test_at_sign_rejected(self):
        with self.assertRaises(ValueError):
            sonos_say._validate_elevenlabs_id("bad@id", "voice-id")


class TestAnnounceValidatesIds(unittest.TestCase):
    """announce() must validate voice_id and model before any I/O."""

    def test_bad_voice_id_raises_before_keychain(self):
        with patch("sonos_say.get_api_key") as mk:
            with self.assertRaises(ValueError):
                sonos_say.announce("Hello", voice_id="bad/id", dry_run=False)
        mk.assert_not_called()

    def test_bad_model_raises_before_keychain(self):
        with patch("sonos_say.get_api_key") as mk:
            with self.assertRaises(ValueError):
                sonos_say.announce("Hello", model="bad model!", dry_run=False)
        mk.assert_not_called()

    def test_valid_ids_dry_run(self):
        # Should not raise
        sonos_say.announce(
            "Hello",
            voice_id="test_voice_123",
            model="eleven_flash_v2_5",
            dry_run=True,
        )


# ---------------------------------------------------------------------------
# CLI / argument-parsing
# ---------------------------------------------------------------------------

class TestParser(unittest.TestCase):

    def _parse(self, *args):
        return sonos_say.build_parser().parse_args(list(args))

    def test_defaults(self):
        args = self._parse("Hello world")
        self.assertEqual(args.text, "Hello world")
        self.assertEqual(args.room, "Test Room")
        self.assertEqual(args.volume, sonos_say.DEFAULT_VOLUME)
        self.assertEqual(args.model, sonos_say.DEFAULT_MODEL)
        self.assertEqual(args.voice_id, "test_voice_123")
        self.assertEqual(args.timeout, sonos_say.DEFAULT_TIMEOUT)
        self.assertFalse(args.dry_run)
        self.assertFalse(args.debug)

    def test_room_override(self):
        self.assertEqual(self._parse("Hi", "--room", "Office").room, "Office")

    def test_volume_override(self):
        self.assertEqual(self._parse("Hi", "--volume", "44").volume, 44)

    def test_voice_id_override(self):
        self.assertEqual(self._parse("Hi", "--voice-id", "NEWVOICE123").voice_id, "NEWVOICE123")

    def test_dry_run_flag(self):
        self.assertTrue(self._parse("Hi", "--dry-run").dry_run)

    def test_stdin_dash(self):
        self.assertEqual(self._parse("-").text, "-")

    def test_no_text_no_flag(self):
        self.assertIsNone(self._parse().text)


class TestMainValidation(unittest.TestCase):

    def _run(self, *args):
        try:
            return sonos_say.main(list(args))
        except SystemExit as exc:
            return exc.code

    def test_blank_text_returns_error(self):
        with patch("sys.stdin", io.StringIO("   ")):
            self.assertNotEqual(self._run("-"), 0)

    def test_volume_out_of_range_high(self):
        self.assertNotEqual(self._run("Hello", "--volume", "101"), 0)

    def test_volume_out_of_range_low(self):
        self.assertNotEqual(self._run("Hello", "--volume", "-1"), 0)

    def test_timeout_zero(self):
        self.assertNotEqual(self._run("Hello", "--timeout", "0"), 0)

    def test_dry_run_succeeds(self):
        self.assertEqual(self._run("Hello, world!", "--dry-run"), 0)

    def test_dry_run_prints_room_and_text(self):
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            sonos_say.main(["Test message", "--dry-run", "--room", "Kitchen"])
        out = captured.getvalue()
        self.assertIn("Kitchen", out)
        self.assertIn("Test message", out)


class TestMainErrorReporting(unittest.TestCase):

    def test_debug_does_not_enable_dependency_payload_logging(self):
        previous = sonos_say.log.level
        self.addCleanup(sonos_say.log.setLevel, previous)
        with (
            patch("sonos_say.logging.basicConfig") as config,
            patch("sonos_say.announce"),
        ):
            self.assertEqual(sonos_say.main(["Hello", "--debug"]), 0)
        self.assertEqual(config.call_args.kwargs["level"], logging.INFO)
        self.assertEqual(sonos_say.log.level, logging.DEBUG)

    def test_restore_and_playback_failures_use_fixed_nonzero_exit(self):
        for code in ("SONOS_RESTORE_FAILED", "SONOS_PLAYBACK_FAILED"):
            with (
                self.subTest(code=code),
                patch("sonos_say.announce", side_effect=sonos_say.SonosSayError(code)),
                patch("sys.stderr", io.StringIO()) as captured,
            ):
                self.assertEqual(sonos_say.main(["Hello"]), 2)
            self.assertEqual(captured.getvalue(), f"SONOS_SAY_ERROR={code}\n")

    def test_phase_error_prints_only_fixed_marker(self):
        captured = io.StringIO()
        error = sonos_say.SonosSayError("ELEVENLABS_SYNTHESIS_FAILED")
        error.__cause__ = RuntimeError("secret API response body")

        with (
            patch("sonos_say.announce", side_effect=error),
            patch("sys.stderr", captured),
        ):
            result = sonos_say.main(["Hello"])

        self.assertEqual(result, 2)
        self.assertEqual(
            captured.getvalue(),
            "SONOS_SAY_ERROR=ELEVENLABS_SYNTHESIS_FAILED\n",
        )
        self.assertNotIn("secret", captured.getvalue())


# ---------------------------------------------------------------------------
# Text-validation (announce() level)
# ---------------------------------------------------------------------------

class TestTextValidation(unittest.TestCase):

    def test_empty_text_raises(self):
        with self.assertRaises(ValueError):
            sonos_say.announce("", dry_run=False)

    def test_text_too_long_raises(self):
        with self.assertRaises(ValueError):
            sonos_say.announce("a" * (sonos_say.MAX_TEXT_CHARS + 1), dry_run=False)

    def test_at_limit_accepted(self):
        with patch("sys.stdout", io.StringIO()):
            sonos_say.announce("a" * sonos_say.MAX_TEXT_CHARS, dry_run=True)

    def test_dry_run_does_not_call_keychain(self):
        with patch("sonos_say.get_api_key") as mk:
            sonos_say.announce("Hello!", dry_run=True)
        mk.assert_not_called()


# ---------------------------------------------------------------------------
# local_ip_toward
# ---------------------------------------------------------------------------

class TestLocalIpToward(unittest.TestCase):

    def test_returns_valid_ipv4(self):
        from ipaddress import IPv4Address
        with patch("socket.socket") as socket_type:
            sock = socket_type.return_value.__enter__.return_value
            sock.getsockname.return_value = ("192.0.2.1", 0)
            IPv4Address(sonos_say.local_ip_toward("192.0.2.2"))
            sock.connect.assert_called_once_with(("192.0.2.2", 80))

    def test_loopback_for_localhost(self):
        self.assertEqual(sonos_say.local_ip_toward("127.0.0.1"), "127.0.0.1")


# ---------------------------------------------------------------------------
# Keychain
# ---------------------------------------------------------------------------

class TestGetApiKey(unittest.TestCase):

    def test_success(self):
        with patch("subprocess.run") as mr:
            mr.return_value = MagicMock(returncode=0, stdout="sk-testkey\n")
            self.assertEqual(
                sonos_say.get_api_key("test-service", "test-account"),
                "sk-testkey",
            )
            mr.assert_called_once_with(
                ["/usr/bin/security", "find-generic-password", "-s", "test-service",
                 "-a", "test-account", "-w"],
                capture_output=True, text=True, timeout=10,
            )

    def test_empty_result_raises(self):
        with patch("subprocess.run") as mr:
            mr.return_value = MagicMock(stdout="   \n")
            with self.assertRaises(RuntimeError):
                sonos_say.get_api_key("test-service", "test-account")

    def test_timeout_and_missing_binary_are_sanitized(self):
        for error in (subprocess.TimeoutExpired("private-selector", 10),
                      FileNotFoundError("private-selector")):
            with self.subTest(error=type(error).__name__):
                with patch("subprocess.run", side_effect=error):
                    with self.assertRaises(RuntimeError) as raised:
                        sonos_say.get_api_key("service@example.invalid",
                                              "account@example.invalid")
                self.assertNotIn("private-selector", str(raised.exception))
                self.assertNotIn("example.invalid", str(raised.exception))

    def test_subprocess_error_raises(self):
        with patch("subprocess.run") as mr:
            mr.side_effect = subprocess.CalledProcessError(44, "security")
            with self.assertRaises(RuntimeError):
                sonos_say.get_api_key("test-service", "test-account")


# ---------------------------------------------------------------------------
# API-key secrecy regression
# ---------------------------------------------------------------------------

class TestApiKeySecrecy(unittest.TestCase):
    """
    The fake API key must never appear in logged output or raised error
    messages when ElevenLabs returns an HTTP error.
    """

    FAKE_KEY = "sk-SUPER-SECRET-0000"

    def _run_synthesis_with_http_error(self, status: int) -> str:
        """
        Attempt synthesis with a mocked HTTP error; return all text that
        appears in the raised RuntimeError message.
        """
        import urllib.error as _ue

        fake_resp = MagicMock()
        fake_resp.read.return_value = b"Bad request body"

        http_error = _ue.HTTPError(
            url="https://api.elevenlabs.io/...",
            code=status,
            msg="Bad Request",
            hdrs={},  # type: ignore[arg-type]
            fp=fake_resp,
        )

        with patch("elevenlabs_voice.tts_opener") as opener:
            opener.return_value.open.side_effect = http_error
            try:
                sonos_say.synthesise_mp3(
                    text="hello",
                    voice_id="test_voice_123",
                    model="eleven_flash_v2_5",
                    api_key=self.FAKE_KEY,
                    dest_path=Path("/tmp/nonexistent.mp3"),
                )
            except RuntimeError as exc:
                return str(exc)
        return ""

    def test_api_key_not_in_error_message(self):
        msg = self._run_synthesis_with_http_error(400)
        self.assertNotIn(self.FAKE_KEY, msg)

    def test_api_key_not_logged_on_error(self):
        log_records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                log_records.append(record)

        handler = _Capture()
        logger = logging.getLogger("sonos_say")
        logger.addHandler(handler)
        try:
            self._run_synthesis_with_http_error(401)
        finally:
            logger.removeHandler(handler)

        all_log_text = " ".join(r.getMessage() for r in log_records)
        self.assertNotIn(self.FAKE_KEY, all_log_text)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

class TestDiscoverSpeaker(unittest.TestCase):

    def _sp(self, name, ip="192.168.1.100"):
        sp = MagicMock()
        sp.player_name = name
        sp.ip_address = ip
        return sp

    def test_finds_matching_room(self):
        sp = self._sp("Test Room")
        with patch("soco.discover", return_value=[sp]):
            self.assertIs(sonos_say.discover_speaker("Test Room"), sp)

    def test_case_insensitive(self):
        sp = self._sp("test room")
        with patch("soco.discover", return_value=[sp]):
            self.assertIs(sonos_say.discover_speaker("TEST ROOM"), sp)

    def test_no_speakers_raises(self):
        with patch("soco.discover", return_value=None):
            with self.assertRaises(RuntimeError):
                sonos_say.discover_speaker("Test Room")

    def test_wrong_room_raises(self):
        sp = self._sp("Office")
        with patch("soco.discover", return_value=[sp]):
            with self.assertRaises(RuntimeError):
                sonos_say.discover_speaker("Test Room")

    def test_duplicate_room_names_fail_closed(self):
        first = self._sp("Test Room", "192.0.2.10")
        second = self._sp("test room", "192.0.2.11")
        with patch("soco.discover", return_value=[first, second]):
            with self.assertRaises(RuntimeError):
                sonos_say.discover_speaker("Test Room")

    def test_error_lists_available(self):
        sp = self._sp("Office")
        with patch("soco.discover", return_value=[sp]):
            try:
                sonos_say.discover_speaker("Test Room")
            except RuntimeError as exc:
                self.assertIn("Office", str(exc))


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

class TestAnnounceOrchestration(unittest.TestCase):

    def _patched_announce(self, mock_speaker, transport_states, **kw):
        mock_speaker.get_current_transport_info.side_effect = [
            {"current_transport_state": s} for s in transport_states
        ]
        with (
            patch("sonos_say.get_api_key", return_value="sk-fake"),
            patch("sonos_say.synthesise_mp3", side_effect=_synthesise_fixture),
            patch("sonos_say.discover_speaker", return_value=mock_speaker),
            patch("sonos_say.local_ip_toward", return_value="192.168.1.10"),
            patch("sonos_say.serve_file") as mock_serve,
            _snapshot_patch() as MockSnapshot,
            patch("time.sleep"),
        ):
            mock_serve.return_value.__enter__ = MagicMock(
                return_value="http://192.168.1.10:9999/announcement.mp3"
            )
            mock_serve.return_value.__exit__ = MagicMock(return_value=False)
            snap = MockSnapshot.return_value
            sonos_say.announce(dry_run=False, **kw)
        return snap

    def test_happy_path_volume_and_snapshot(self):
        sp, vol = _make_volume_tracked_speaker(30)
        snap = self._patched_announce(
            sp,
            ["PLAYING", "STOPPED"],
            text="Test announcement",
            room="Test Room",
            volume=35,
        )
        snap.snapshot.assert_called_once()
        snap.restore.assert_called_once()
        self.assertIn(35, vol, "Announcement volume must be set")
        self.assertIn(30, vol, "Original volume must be restored")

    def test_lost_telemetry_is_playback_failure_after_cleanup(self):
        sp, volumes = _make_volume_tracked_speaker(30)
        with self.assertRaises(sonos_say.SonosSayError) as raised:
            self._patched_announce(sp, ["PLAYING", "UNKNOWN"], text="Hello")
        self.assertEqual(raised.exception.code, "SONOS_PLAYBACK_FAILED")
        self.assertEqual(volumes, [35, 30])

    def test_volume_restored_on_play_uri_exception(self):
        sp, vol = _make_volume_tracked_speaker(30)
        sp.play_uri.side_effect = RuntimeError("Sonos unreachable")

        with (
            patch("sonos_say.get_api_key", return_value="sk-fake"),
            patch("sonos_say.synthesise_mp3", side_effect=_synthesise_fixture),
            patch("sonos_say.discover_speaker", return_value=sp),
            patch("sonos_say.local_ip_toward", return_value="192.168.1.10"),
            patch("sonos_say.serve_file") as mock_serve,
            _snapshot_patch() as MockSnapshot,
            patch("time.sleep"),
        ):
            mock_serve.return_value.__enter__ = MagicMock(
                return_value="http://192.168.1.10:9999/announcement.mp3"
            )
            mock_serve.return_value.__exit__ = MagicMock(return_value=False)
            snap = MockSnapshot.return_value

            with self.assertRaises(RuntimeError):
                sonos_say.announce("Boom", room="Test Room", volume=35, dry_run=False)

        self.assertIn(35, vol, "Announcement volume should be set")
        self.assertIn(30, vol, "Original volume must be restored after exception")
        snap.restore.assert_called_once()

    def test_snapshot_not_restored_if_snapshot_never_taken(self):
        """
        If Snapshot.snapshot() raises, restore must NOT be called but
        volume (if already read) also should not be restored.
        """
        sp, vol = _make_volume_tracked_speaker(30)

        with (
            patch("sonos_say.get_api_key", return_value="sk-fake"),
            patch("sonos_say.synthesise_mp3", side_effect=_synthesise_fixture),
            patch("sonos_say.discover_speaker", return_value=sp),
            patch("sonos_say.local_ip_toward", return_value="192.168.1.10"),
            patch("sonos_say.serve_file") as mock_serve,
            _snapshot_patch() as MockSnapshot,
            patch("time.sleep"),
        ):
            mock_serve.return_value.__enter__ = MagicMock(
                return_value="http://192.168.1.10:9999/announcement.mp3"
            )
            mock_serve.return_value.__exit__ = MagicMock(return_value=False)
            snap = MockSnapshot.return_value
            # Make snapshot() raise before anything else happens
            snap.snapshot.side_effect = RuntimeError("snapshot failed")

            with self.assertRaises(RuntimeError):
                sonos_say.announce("Hello", room="Test Room", volume=35, dry_run=False)

        snap.restore.assert_not_called()

    def test_volume_getter_failure_before_mutation_does_not_restore(self):
        """A failed read creates no transport/rendering recovery obligation."""
        sp = _set_standalone(MagicMock())
        sp.ip_address = "192.168.1.50"
        type(sp).volume = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("volume unavailable"))
        )

        with (
            patch("sonos_say.get_api_key", return_value="sk-fake"),
            patch("sonos_say.synthesise_mp3", side_effect=_synthesise_fixture),
            patch("sonos_say.discover_speaker", return_value=sp),
            patch("sonos_say.local_ip_toward", return_value="192.168.1.10"),
            patch("sonos_say.serve_file") as mock_serve,
            _snapshot_patch() as MockSnapshot,
            patch("time.sleep"),
        ):
            mock_serve.return_value.__enter__ = MagicMock(
                return_value="http://192.168.1.10:9999/announcement.mp3"
            )
            mock_serve.return_value.__exit__ = MagicMock(return_value=False)
            snap = MockSnapshot.return_value

            with self.assertRaises(RuntimeError):
                sonos_say.announce("Hello", room="Test Room", volume=35, dry_run=False)

        snap.restore.assert_not_called()
        sp.play_uri.assert_not_called()


# ---------------------------------------------------------------------------
# Group-aware orchestration
# ---------------------------------------------------------------------------

class TestGroupedAnnouncement(unittest.TestCase):

    def _grouped_speakers(self):
        events: list[str] = []
        target = _set_standalone(MagicMock())
        coordinator = _set_standalone(MagicMock())
        target.uid = target._uid = "RINCON_TARGET"
        target._is_satellite = False
        target._has_satellites = False
        coordinator.uid = coordinator._uid = "RINCON_COORDINATOR"
        coordinator._is_satellite = False
        coordinator._has_satellites = False
        target.zone_group_state.has_subscriptions = False
        coordinator.zone_group_state.has_subscriptions = False
        target.zone_group_state.processed_count = 0
        coordinator.zone_group_state.processed_count = 0
        target.ip_address = "192.168.1.50"
        target.volume = 30
        target.mute = coordinator.mute = False
        for sp in (target, coordinator):
            sp.avTransport.GetMediaInfo.return_value = {"CurrentURI": "prior-uri"}

        def _group(coord, members):
            group = SimpleNamespace(
                coordinator=coord,
                members=members,
            )
            for member in members:
                member.group = group
                member.zone_group_state.visible_zones = set(members)

        _group(coordinator, [coordinator, target])

        def _unjoin():
            events.append("detach")
            _group(coordinator, [coordinator])
            _group(target, [target])

        def _join(prior):
            events.append("rejoin")
            _group(prior, [prior, target])

        target.unjoin.side_effect = _unjoin
        target.join.side_effect = _join
        return coordinator, events, target

    def _announce(
        self,
        target,
        wait_effect=True,
        synthesise_effect=None,
        snapshot_restore_effect=None,
        snapshot_capture=None,
    ):
        with (
            patch("sonos_say.get_api_key", return_value="sk-fake"),
            patch(
                "sonos_say.synthesise_mp3",
                side_effect=(lambda *a, **kw: (
                    synthesise_effect(*a, **kw) if callable(synthesise_effect) else None,
                    _synthesise_fixture(*a, **kw))) if not isinstance(synthesise_effect, BaseException)
                else synthesise_effect,
            ),
            patch("sonos_say.discover_speaker", return_value=target),
            patch(
                "sonos_say.local_ip_toward",
                return_value="192.168.1.10",
            ),
            patch("sonos_say.serve_file") as mock_serve,
            _snapshot_patch() as snapshot_type,
            patch(
                "sonos_say.wait_for_playback",
                side_effect=wait_effect
                if (
                    isinstance(wait_effect, BaseException)
                    or callable(wait_effect)
                )
                else None,
                return_value=wait_effect
                if not (
                    isinstance(wait_effect, BaseException)
                    or callable(wait_effect)
                )
                else None,
            ),
        ):
            mock_serve.return_value.__enter__.return_value = (
                "http://192.168.1.10:9999/announcement.mp3"
            )
            mock_serve.return_value.__exit__.return_value = False
            snapshot_type.return_value.media_uri = "prior-uri"
            if snapshot_capture is not None:
                snapshot_capture.append(snapshot_type.return_value)
            if snapshot_restore_effect is not None:
                snapshot_type.return_value.restore.side_effect = (
                    snapshot_restore_effect
                )
            sonos_say.announce("Hello", room="Target", volume=35)
            return snapshot_type.return_value

    def test_grouped_member_detaches_plays_only_target_and_rejoins(self):
        coordinator, events, target = self._grouped_speakers()

        snapshot = self._announce(target)

        self.assertEqual(events, ["detach", "rejoin"])
        target.play_uri.assert_called_once()
        coordinator.play_uri.assert_not_called()
        snapshot.restore.assert_called_once_with(fade=False)
        target.join.assert_called_once_with(coordinator)

    def test_noncompletion_still_rejoins_and_reports_playback_failure(self):
        coordinator, events, target = self._grouped_speakers()
        with self.assertRaises(sonos_say.SonosSayError) as raised:
            self._announce(target, wait_effect=False)
        self.assertEqual(raised.exception.code, "SONOS_PLAYBACK_FAILED")
        self.assertEqual(events, ["detach", "rejoin"])
        target.join.assert_called_once_with(coordinator)

    def test_grouped_coordinator_refused_before_mutation(self):
        coordinator, events, target = self._grouped_speakers()
        coordinator.ip_address = "192.0.2.5"
        coordinator._is_satellite = False
        coordinator._has_satellites = False
        snapshots = []
        with self.assertRaises(sonos_say.SonosSayError) as raised:
            self._announce(coordinator, snapshot_capture=snapshots)
        self.assertEqual(raised.exception.code, "SONOS_TOPOLOGY_FAILED")
        snapshots[0].snapshot.assert_not_called()
        self.assertEqual(events, [])
        coordinator.play_uri.assert_not_called()
        coordinator.unjoin.assert_not_called()
        target.play_uri.assert_not_called()

    def test_failed_preplayback_recheck_prevents_playback_and_restores_group(self):
        coordinator, events, target = self._grouped_speakers()
        snapshots = []
        with (
            patch("sonos_say.verify_isolated_target",
                  side_effect=[None, sonos_say.TopologyError("changed")]),
            self.assertRaises(sonos_say.SonosSayError) as raised,
        ):
            self._announce(target, snapshot_capture=snapshots)
        self.assertEqual(raised.exception.code, "SONOS_TOPOLOGY_FAILED")
        target.play_uri.assert_not_called()
        snapshots[0].restore.assert_not_called()
        self.assertEqual(target.volume, 30)
        self.assertEqual(events, ["detach", "rejoin"])
        target.join.assert_called_once_with(coordinator)

    def test_failed_presnapshot_recheck_only_restores_group(self):
        coordinator, events, target = self._grouped_speakers()
        snapshots = []
        with (
            patch("sonos_say.verify_isolated_target",
                  side_effect=sonos_say.TopologyError("changed")),
            self.assertRaises(sonos_say.SonosSayError) as raised,
        ):
            self._announce(target, snapshot_capture=snapshots)
        self.assertEqual(raised.exception.code, "SONOS_TOPOLOGY_FAILED")
        target.play_uri.assert_not_called()
        snapshots[0].snapshot.assert_not_called()
        snapshots[0].restore.assert_not_called()
        self.assertEqual(events, ["detach", "rejoin"])
        target.join.assert_called_once_with(coordinator)

    def test_synthesis_finishes_before_detach(self):
        _, events, target = self._grouped_speakers()

        def _synthesise(*args, **kwargs):
            events.append("synthesise")
            _synthesise_fixture(*args, **kwargs)

        self._announce(target, synthesise_effect=_synthesise)

        self.assertEqual(events[:2], ["synthesise", "detach"])

    def test_synthesis_failure_never_detaches(self):
        _, _, target = self._grouped_speakers()
        with (
            patch("sonos_say.get_api_key", return_value="sk-fake"),
            patch(
                "sonos_say.synthesise_mp3",
                side_effect=RuntimeError("API body"),
            ),
            patch("sonos_say.discover_speaker", return_value=target) as discover,
            patch("sonos_say.local_ip_toward", return_value="192.0.2.1"),
        ):
            with self.assertRaises(sonos_say.SonosSayError) as raised:
                sonos_say.announce("Hello", room="Target", volume=35)

        self.assertEqual(
            raised.exception.code,
            "ELEVENLABS_SYNTHESIS_FAILED",
        )
        discover.assert_called_once_with("Target")
        target.unjoin.assert_not_called()
        target.join.assert_not_called()

    def test_precondition_drift_does_not_arm_recovery(self):
        _, events, target = self._grouped_speakers()
        other = _set_standalone(MagicMock())
        other.uid = other._uid = "other"

        def regroup(*args, **kwargs):
            group = SimpleNamespace(coordinator=other, members=[other, target])
            target.group = other.group = group

        with patch("sonos_say.restore_target_group") as restore:
            with self.assertRaises(sonos_say.SonosSayError) as raised:
                self._announce(target, synthesise_effect=regroup)
        self.assertEqual(raised.exception.code, "SONOS_TOPOLOGY_FAILED")
        self.assertEqual(events, [])
        target.unjoin.assert_not_called()
        target.join.assert_not_called()
        restore.assert_not_called()

    def test_rpc_mutation_then_timeout_still_restores_group(self):
        coordinator, events, target = self._grouped_speakers()
        isolate = target.unjoin.side_effect

        def mutate_then_timeout():
            isolate()
            raise TimeoutError("timed out after applying mutation")

        target.unjoin.side_effect = mutate_then_timeout
        with self.assertRaises(sonos_say.SonosSayError) as raised:
            self._announce(target)
        self.assertEqual(raised.exception.code, "SONOS_TOPOLOGY_FAILED")
        self.assertEqual(events, ["detach", "rejoin"])
        target.join.assert_called_once_with(coordinator)

    def test_read_only_preflight_precedes_keychain_and_synthesis(self):
        _, events, target = self._grouped_speakers()
        validate = sonos_say.validate_announcement_target

        def discovery(room):
            events.append("discover")
            return target

        def validation(speaker):
            events.append("validate")
            return validate(speaker)

        with patch("sonos_say.discover_speaker", side_effect=discovery), \
                patch("sonos_say.validate_announcement_target", side_effect=validation):
            # _announce's discovery patch would mask ordering; run full pipeline.
            with patch("sonos_say.get_api_key", side_effect=lambda *a:
                       events.append("key") or "dummy-key"), \
                    patch("sonos_say.synthesise_mp3", side_effect=lambda *a, **kw:
                          (events.append("synth"), _synthesise_fixture(*a, **kw))), \
                    patch("sonos_say.local_ip_toward", return_value="192.0.2.1"), \
                    patch("sonos_say.serve_file") as serve, \
                    _snapshot_patch() as snapshot, \
                    patch("sonos_say.wait_for_playback", return_value=True):
                serve.return_value.__enter__.return_value = "http://192.0.2.1/audio"
                snapshot.return_value.media_uri = "prior-uri"
                target.play_uri.side_effect = lambda *a, **kw: events.append("play")
                sonos_say.announce("Hello")
        self.assertEqual(events[:6], ["discover", "validate", "key", "synth",
                                     "detach", "play"])

    def test_invalid_room_or_topology_never_accesses_credentials(self):
        for phase in ("missing", "ambiguous", "topology"):
            with self.subTest(phase=phase):
                coord, _, target = self._grouped_speakers()
                speaker = coord if phase == "topology" else target
                with patch("sonos_say.discover_speaker", return_value=speaker,
                           side_effect=None if phase == "topology" else
                           RuntimeError(phase)), \
                        patch("sonos_say.local_ip_toward", return_value="192.0.2.1"), \
                        patch("sonos_say.get_api_key") as key, \
                        patch("sonos_say.synthesise_mp3") as synth, \
                        patch("sonos_say.serve_file") as serve:
                    with self.assertRaises(sonos_say.SonosSayError) as raised:
                        sonos_say.announce("Hello")
                self.assertEqual(raised.exception.code, "SONOS_TOPOLOGY_FAILED"
                                 if phase == "topology" else "SONOS_DISCOVERY_FAILED")
                for operation in (key, synth, serve, target.unjoin, target.join):
                    operation.assert_not_called()

    def test_play_failure_still_rejoins(self):
        coordinator, events, target = self._grouped_speakers()
        target.play_uri.side_effect = RuntimeError("play failed")

        with self.assertRaises(sonos_say.SonosSayError) as raised:
            self._announce(target)

        self.assertEqual(raised.exception.code, "SONOS_PLAYBACK_FAILED")
        self.assertEqual(events, ["detach", "rejoin"])
        target.join.assert_called_once_with(coordinator)

    def test_keyboard_interrupt_still_rejoins(self):
        coordinator, events, target = self._grouped_speakers()

        with self.assertRaises(KeyboardInterrupt):
            self._announce(target, KeyboardInterrupt())

        self.assertEqual(events, ["detach", "rejoin"])
        target.join.assert_called_once_with(coordinator)

    def test_sigterm_still_rejoins(self):
        coordinator, events, target = self._grouped_speakers()

        with self.assertRaises(SystemExit) as raised:
            self._announce(target, SystemExit(143))

        self.assertEqual(raised.exception.code, 143)
        self.assertEqual(events, ["detach", "rejoin"])
        target.join.assert_called_once_with(coordinator)

    def test_successful_play_with_rejoin_failure_is_failure(self):
        _, _, target = self._grouped_speakers()
        target.join.side_effect = RuntimeError("join failed")

        with self.assertRaises(sonos_say.SonosSayError) as raised:
            self._announce(target)

        self.assertEqual(raised.exception.code, "SONOS_RESTORE_FAILED")

    def test_sigterm_during_cleanup_is_deferred_until_rejoin(self):
        import os

        coordinator, events, target = self._grouped_speakers()

        def _sigterm_during_restore(*args, **kwargs):
            os.kill(os.getpid(), signal.SIGTERM)

        with self.assertRaises(SystemExit) as raised:
            self._announce(
                target,
                snapshot_restore_effect=_sigterm_during_restore,
            )

        self.assertEqual(raised.exception.code, 143)
        self.assertEqual(events, ["detach", "rejoin"])
        target.join.assert_called_once_with(coordinator)

    def test_keyboard_interrupt_during_cleanup_is_deferred(self):
        import os

        coordinator, events, target = self._grouped_speakers()

        def _sigint_during_restore(*args, **kwargs):
            os.kill(os.getpid(), signal.SIGINT)

        with self.assertRaises(KeyboardInterrupt):
            self._announce(
                target,
                snapshot_restore_effect=_sigint_during_restore,
            )

        self.assertEqual(events, ["detach", "rejoin"])
        target.join.assert_called_once_with(coordinator)

    def test_playback_and_cleanup_failure_reports_restore_failure(self):
        _, events, target = self._grouped_speakers()

        with self.assertRaises(sonos_say.SonosSayError) as raised:
            self._announce(
                target,
                wait_effect=RuntimeError("playback failed"),
                snapshot_restore_effect=RuntimeError("restore failed"),
            )

        self.assertEqual(raised.exception.code, "SONOS_RESTORE_FAILED")
        self.assertEqual(events, ["detach", "rejoin"])

    def test_pending_interrupt_and_restore_failure_prefers_restore(self):
        _, events, target = self._grouped_speakers()

        with self.assertRaises(sonos_say.SonosSayError) as raised:
            self._announce(
                target,
                wait_effect=KeyboardInterrupt(),
                snapshot_restore_effect=RuntimeError("restore failed"),
            )

        self.assertEqual(raised.exception.code, "SONOS_RESTORE_FAILED")
        self.assertEqual(events, ["detach", "rejoin"])

    def test_cleanup_interrupt_then_failure_prefers_restore(self):
        _, events, target = self._grouped_speakers()

        def interrupted_failure(**kwargs):
            os.kill(os.getpid(), signal.SIGINT)
            raise RuntimeError("restore failed")

        with self.assertRaises(sonos_say.SonosSayError) as raised:
            self._announce(target, snapshot_restore_effect=interrupted_failure)

        self.assertEqual(raised.exception.code, "SONOS_RESTORE_FAILED")
        self.assertEqual(events, ["detach", "rejoin"])

    def test_interrupt_at_cleanup_deferral_entry_still_restores(self):
        coordinator, events, target = self._grouped_speakers()
        snapshots = []
        real_begin = sonos_say._MutationSignalGuard.begin_cleanup

        def boundary(guard):
            os.kill(os.getpid(), signal.SIGTERM)
            real_begin(guard)

        with patch.object(sonos_say._MutationSignalGuard, "begin_cleanup", boundary):
            with self.assertRaises(SystemExit) as raised:
                self._announce(target, snapshot_capture=snapshots)

        self.assertEqual(raised.exception.code, 143)
        snapshots[0].restore.assert_called_once_with(fade=False)
        self.assertEqual(target.volume, 30)
        self.assertEqual(events, ["detach", "rejoin"])
        target.join.assert_called_once_with(coordinator)

    def test_cleanup_signal_precedes_pending_playback_error(self):
        import os

        cases = (
            (signal.SIGINT, KeyboardInterrupt, None),
            (signal.SIGTERM, SystemExit, 143),
        )
        for signum, error_type, exit_code in cases:
            with self.subTest(signum=signum):
                coordinator, events, target = self._grouped_speakers()

                def _signal_during_restore(*args, **kwargs):
                    os.kill(os.getpid(), signum)

                with self.assertRaises(error_type) as raised:
                    self._announce(
                        target,
                        wait_effect=RuntimeError("playback failed"),
                        snapshot_restore_effect=_signal_during_restore,
                    )

                if exit_code is not None:
                    self.assertEqual(raised.exception.code, exit_code)
                self.assertEqual(events, ["detach", "rejoin"])
                target.join.assert_called_once_with(coordinator)

    def test_interrupt_at_begin_cleanup_restores_exactly_once(self):
        coordinator, events, target = self._grouped_speakers()
        snapshots = []

        real_begin = sonos_say._MutationSignalGuard.begin_cleanup
        def boundary(guard):
            os.kill(os.getpid(), signal.SIGTERM)
            real_begin(guard)

        with patch.object(sonos_say._MutationSignalGuard, "begin_cleanup", boundary):
            with self.assertRaises(SystemExit) as raised:
                self._announce(target, snapshot_capture=snapshots)

        self.assertEqual(raised.exception.code, 143)
        snapshots[0].restore.assert_called_once_with(fade=False)
        self.assertEqual(target.volume, 30)
        self.assertEqual(events, ["detach", "rejoin"])
        target.join.assert_called_once_with(coordinator)

    def test_signal_handlers_restored_on_every_exit_path(self):
        import os

        original_sigint = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)

        def _sentinel_sigint(signum, frame):
            return None

        def _sentinel_sigterm(signum, frame):
            return None

        cases = (
            (True, None),
            (RuntimeError("playback failed"), sonos_say.SonosSayError),
            (
                lambda *args, **kwargs: os.kill(os.getpid(), signal.SIGINT),
                KeyboardInterrupt,
            ),
            (
                lambda *args, **kwargs: os.kill(os.getpid(), signal.SIGTERM),
                SystemExit,
            ),
        )
        try:
            for wait_effect, error_type in cases:
                with self.subTest(error_type=error_type):
                    signal.signal(signal.SIGINT, _sentinel_sigint)
                    signal.signal(signal.SIGTERM, _sentinel_sigterm)
                    _, _, target = self._grouped_speakers()

                    if error_type is None:
                        self._announce(target, wait_effect=wait_effect)
                    else:
                        with self.assertRaises(error_type):
                            self._announce(target, wait_effect=wait_effect)

                    self.assertIs(
                        signal.getsignal(signal.SIGINT),
                        _sentinel_sigint,
                    )
                    self.assertIs(
                        signal.getsignal(signal.SIGTERM),
                        _sentinel_sigterm,
                    )
        finally:
            signal.signal(signal.SIGINT, original_sigint)
            signal.signal(signal.SIGTERM, original_sigterm)


# ---------------------------------------------------------------------------
# SIGTERM handling
# ---------------------------------------------------------------------------

class TestSigtermHandling(unittest.TestCase):

    def test_sigterm_raises_system_exit_143(self):
        """
        The scoped guard converts SIGTERM to SystemExit(143) and restores
        the original handler, without installing a permanent global helper.
        """
        import os

        guard = sonos_say._MutationSignalGuard()
        prior = signal.getsignal(signal.SIGTERM)
        guard.install()
        try:
            with self.assertRaises(SystemExit) as cm:
                os.kill(os.getpid(), signal.SIGTERM)
        finally:
            guard.restore()
        self.assertIs(signal.getsignal(signal.SIGTERM), prior)

        self.assertEqual(cm.exception.code, 143)

    def test_sigterm_triggers_restore_and_preserves_exit_143(self):
        """
        When SIGTERM fires during the announce() playback wait:
        - Volume and snapshot must be restored.
        - announce() must re-raise SystemExit(143) — NOT KeyboardInterrupt.
        - The shell/orchestrator must therefore see exit code 143.
        """
        sp, vol = _make_volume_tracked_speaker(30)

        def _fake_wait(*_a, **_kw):
            # Simulate SIGTERM arriving mid-wait
            raise SystemExit(143)

        with (
            patch("sonos_say.get_api_key", return_value="sk-fake"),
            patch("sonos_say.synthesise_mp3", side_effect=_synthesise_fixture),
            patch("sonos_say.discover_speaker", return_value=sp),
            patch("sonos_say.local_ip_toward", return_value="192.168.1.10"),
            patch("sonos_say.serve_file") as mock_serve,
            _snapshot_patch() as MockSnapshot,
            patch("sonos_say.wait_for_playback", side_effect=_fake_wait),
            patch("time.sleep"),
        ):
            mock_serve.return_value.__enter__ = MagicMock(
                return_value="http://192.168.1.10:9999/announcement.mp3"
            )
            mock_serve.return_value.__exit__ = MagicMock(return_value=False)
            snap = MockSnapshot.return_value

            # announce() must re-raise SystemExit(143), not convert to KeyboardInterrupt
            with self.assertRaises(SystemExit) as cm:
                sonos_say.announce("Hello", room="Test Room", volume=35, dry_run=False)

        self.assertEqual(cm.exception.code, 143, "SIGTERM must exit with code 143")
        self.assertIn(30, vol, "Volume must be restored after SIGTERM")
        snap.restore.assert_called_once()

    def test_non_143_systemexit_propagates_with_cleanup(self):
        """
        A SystemExit with a code other than 143 (e.g. from an inner
        sys.exit() call) must propagate unchanged after cleanup runs.
        Volume and snapshot must still be restored.
        """
        sp, vol = _make_volume_tracked_speaker(30)

        def _fake_wait(*_a, **_kw):
            raise SystemExit(1)  # not a SIGTERM

        with (
            patch("sonos_say.get_api_key", return_value="sk-fake"),
            patch("sonos_say.synthesise_mp3", side_effect=_synthesise_fixture),
            patch("sonos_say.discover_speaker", return_value=sp),
            patch("sonos_say.local_ip_toward", return_value="192.168.1.10"),
            patch("sonos_say.serve_file") as mock_serve,
            _snapshot_patch() as MockSnapshot,
            patch("sonos_say.wait_for_playback", side_effect=_fake_wait),
            patch("time.sleep"),
        ):
            mock_serve.return_value.__enter__ = MagicMock(
                return_value="http://192.168.1.10:9999/announcement.mp3"
            )
            mock_serve.return_value.__exit__ = MagicMock(return_value=False)
            snap = MockSnapshot.return_value

            with self.assertRaises(SystemExit) as cm:
                sonos_say.announce("Hello", room="Test Room", volume=35, dry_run=False)

        self.assertEqual(cm.exception.code, 1, "Non-143 SystemExit code must be preserved")
        self.assertIn(30, vol, "Volume must be restored even for non-143 SystemExit")
        snap.restore.assert_called_once()

    def test_keyboard_interrupt_propagates_as_keyboard_interrupt(self):
        """
        Ctrl-C (KeyboardInterrupt) must still propagate as KeyboardInterrupt
        (not SystemExit), so main() returns 130.
        """
        sp, vol = _make_volume_tracked_speaker(30)

        def _fake_wait(*_a, **_kw):
            raise KeyboardInterrupt

        with (
            patch("sonos_say.get_api_key", return_value="sk-fake"),
            patch("sonos_say.synthesise_mp3", side_effect=_synthesise_fixture),
            patch("sonos_say.discover_speaker", return_value=sp),
            patch("sonos_say.local_ip_toward", return_value="192.168.1.10"),
            patch("sonos_say.serve_file") as mock_serve,
            _snapshot_patch() as MockSnapshot,
            patch("sonos_say.wait_for_playback", side_effect=_fake_wait),
            patch("time.sleep"),
        ):
            mock_serve.return_value.__enter__ = MagicMock(
                return_value="http://192.168.1.10:9999/announcement.mp3"
            )
            mock_serve.return_value.__exit__ = MagicMock(return_value=False)
            snap = MockSnapshot.return_value

            with self.assertRaises(KeyboardInterrupt):
                sonos_say.announce("Hello", room="Test Room", volume=35, dry_run=False)

        self.assertIn(30, vol, "Volume must be restored after Ctrl-C")
        snap.restore.assert_called_once()


# ---------------------------------------------------------------------------
# HTTP server: path restriction and bounded shutdown
# ---------------------------------------------------------------------------

class TestAnnouncementHandler(unittest.TestCase):
    """
    Test tokenized, client-restricted serving and bounded cleanup.
    """

    def setUp(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.tmp = Path(tmpdir.name) / "audio.mp3"
        self.tmp.write_bytes(b"\xff\xfb\x00" * 10)  # fake MP3 bytes

    def _serve(self, client_ip="127.0.0.1"):
        return sonos_say.serve_file(self.tmp, "127.0.0.1", client_ip)

    def test_unpredictable_path_and_head_get_access(self):
        urls = []
        with patch("sonos_say.secrets.token_urlsafe", wraps=__import__(
            "secrets").token_urlsafe) as token:
            for _ in range(2):
                with self._serve() as url:
                    urls.append(url)
                    self.assertNotEqual(urllib.parse.urlsplit(url).path,
                                        "/announcement.mp3")
                    for method in ("HEAD", "GET"):
                        request = urllib.request.Request(url, method=method)
                        with urllib.request.urlopen(request, timeout=2) as resp:
                            self.assertEqual(resp.status, 200)
                            self.assertEqual(resp.headers["Cache-Control"], "no-store")
                            self.assertEqual(int(resp.headers["Content-Length"]),
                                             self.tmp.stat().st_size)
                            body = resp.read()
                            self.assertEqual(body, b"" if method == "HEAD"
                                             else self.tmp.read_bytes())
        self.assertEqual(token.call_count, 2)
        token.assert_called_with(32)
        self.assertNotEqual(urllib.parse.urlsplit(urls[0]).path,
                            urllib.parse.urlsplit(urls[1]).path)

    def test_wrong_token_and_legacy_path_never_read_file(self):
        with self._serve() as url:
            parts = urllib.parse.urlsplit(url)
            base = f"{parts.scheme}://{parts.netloc}"
            for path in ("/announcement.mp3", "/bad/announcement.mp3", "/",
                         "/../audio.mp3", "/%2e%2e/audio.mp3"):
                for method in ("GET", "HEAD"):
                    with (
                        self.subTest(path=path, method=method),
                        patch.object(Path, "read_bytes") as read,
                        patch.object(Path, "stat") as stat,
                        self.assertRaises(urllib.error.HTTPError) as raised,
                    ):
                        urllib.request.urlopen(urllib.request.Request(
                            base + path, method=method), timeout=2)
                    self.assertEqual(raised.exception.code, 404)
                    raised.exception.close()
                    read.assert_not_called()
                    stat.assert_not_called()

    def test_wrong_client_never_reads_file_even_with_token(self):
        with self._serve(client_ip="192.0.2.99") as url:
            for method in ("GET", "HEAD"):
                with (
                    self.subTest(method=method),
                    patch.object(Path, "read_bytes") as read,
                    patch.object(Path, "stat") as stat,
                    self.assertRaises(urllib.error.HTTPError) as raised,
                ):
                    urllib.request.urlopen(urllib.request.Request(
                        url, method=method), timeout=2)
                self.assertEqual(raised.exception.code, 404)
                raised.exception.close()
                read.assert_not_called()
                stat.assert_not_called()

    def test_capability_not_logged_at_debug_level(self):
        with self.assertLogs("sonos_say", level="DEBUG") as captured:
            with self._serve() as url:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    resp.read()
        output = "\n".join(captured.output)
        self.assertNotIn(urllib.parse.urlsplit(url).path, output)
        self.assertNotIn(url, output)

    def test_shutdown_revokes_token_on_already_accepted_connection(self):
        accepted = threading.Event()
        real_setup = sonos_say._AnnouncementHandler.setup

        def setup(handler):
            real_setup(handler)
            accepted.set()

        with patch.object(sonos_say._AnnouncementHandler, "setup", setup):
            with self._serve() as url:
                parts = urllib.parse.urlsplit(url)
                connection = socket.create_connection((parts.hostname, parts.port), 2)
                self.addCleanup(connection.close)
                # Hold a request open while shutdown closes the listening socket.
                connection.sendall(f"GET {parts.path} HTTP/1.0\r\n".encode())
                self.assertTrue(accepted.wait(2))
            with patch.object(Path, "read_bytes") as read:
                connection.sendall(b"\r\n")
                response = connection.recv(4096)
                self.assertIn(b"404 Not Found", response)
                read.assert_not_called()

    def test_expired_token_is_not_valid_for_next_announcement(self):
        with self._serve() as old_url:
            old_path = urllib.parse.urlsplit(old_url).path
        with self._serve() as new_url:
            parts = urllib.parse.urlsplit(new_url)
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(
                    f"{parts.scheme}://{parts.netloc}{old_path}", timeout=2)
            self.assertEqual(raised.exception.code, 404)
            raised.exception.close()

    def test_serves_announcement_mp3(self):
        with self._serve() as url:
            resp = urllib.request.urlopen(url, timeout=5)
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), self.tmp.read_bytes())

    def test_404_for_other_path(self):
        with self._serve() as url:
            base = url.rsplit("/announcement.mp3", 1)[0]
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(f"{base}/etc/passwd", timeout=5)
            self.assertEqual(cm.exception.code, 404)

    def test_404_for_root(self):
        with self._serve() as url:
            base = url.rsplit("/announcement.mp3", 1)[0]
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(f"{base}/", timeout=5)
            self.assertEqual(cm.exception.code, 404)

    def test_query_string_ignored(self):
        """Sonos may append ?X-Sonos-something; request must still return 200."""
        with self._serve() as url:
            resp = urllib.request.urlopen(url + "?X-Sonos-foo=bar", timeout=5)
            self.assertEqual(resp.status, 200)

    def test_server_stops_and_socket_released(self):
        with self._serve() as url:
            port = int(url.split(":")[2].split("/")[0])
            urllib.request.urlopen(url, timeout=5)
        # Port must be released after context exit
        with self.assertRaises(Exception):
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/announcement.mp3", timeout=2
            )


# ---------------------------------------------------------------------------
# wait_for_playback (two-phase)
# ---------------------------------------------------------------------------

class TestWaitForPlayback(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        clock = patch("sonos_say.time.monotonic", side_effect=lambda: self.now)
        sleep = patch("sonos_say.time.sleep", side_effect=self._advance)
        clock.start()
        sleep.start()
        self.addCleanup(clock.stop)
        self.addCleanup(sleep.stop)

    def _advance(self, seconds):
        self.now += seconds

    def _mock_states(self, states):
        sp = MagicMock()
        remaining = iter(states)
        last = states[-1]

        def read_state():
            value = next(remaining, last)
            if isinstance(value, Exception):
                raise value
            return {"current_transport_state": value}

        sp.get_current_transport_info.side_effect = read_state
        return sp

    def test_never_playing_returns_false(self):
        sp = self._mock_states(["STOPPED"])
        self.assertFalse(sonos_say.wait_for_playback(sp, 10, start_timeout=1))
        self.assertEqual(self.now, 1)

    def test_stopped_before_playing_can_still_start(self):
        sp = self._mock_states(["STOPPED", "PLAYING", "STOPPED"])
        self.assertTrue(sonos_say.wait_for_playback(sp, 10))

    def test_transitioning_does_not_prove_playback_started(self):
        sp = self._mock_states(["TRANSITIONING", "STOPPED"])
        self.assertFalse(sonos_say.wait_for_playback(sp, 10, start_timeout=1))

    def test_transitioning_then_playing_then_stopped_succeeds(self):
        sp = self._mock_states(["TRANSITIONING", "PLAYING", "STOPPED"])
        self.assertTrue(sonos_say.wait_for_playback(sp, 10))

    def test_playing_then_stopped_returns_true(self):
        sp = self._mock_states(["PLAYING", "PLAYING", "STOPPED"])
        self.assertTrue(sonos_say.wait_for_playback(sp, 10))

    def test_playing_then_paused_is_failure(self):
        sp = self._mock_states(["PLAYING", "PAUSED_PLAYBACK"])
        self.assertFalse(sonos_say.wait_for_playback(sp, 10))

    def test_lost_or_unknown_telemetry_is_failure_in_either_phase(self):
        for bad in ("UNKNOWN", "NO_MEDIA_PRESENT", None, RuntimeError("offline")):
            for prior in ([], ["PLAYING"]):
                with self.subTest(bad=bad, prior=prior):
                    sp = self._mock_states(prior + [bad])
                    self.assertFalse(sonos_say.wait_for_playback(sp, 10))

    def test_missing_transport_state_is_failure(self):
        sp = MagicMock()
        sp.get_current_transport_info.side_effect = [
            {"current_transport_state": "PLAYING"}, {},
        ]
        self.assertFalse(sonos_say.wait_for_playback(sp, 10))

    def test_phase2_timeout_returns_false(self):
        sp = self._mock_states(["PLAYING"])
        self.assertFalse(sonos_say.wait_for_playback(sp, 1))
        self.assertEqual(self.now, 1)

    def test_overall_timeout_caps_start_timeout(self):
        sp = self._mock_states(["STOPPED"])
        self.assertFalse(sonos_say.wait_for_playback(sp, 0.1, start_timeout=99))
        self.assertLessEqual(self.now, 0.1)

    def test_terminal_response_after_deadline_is_not_success(self):
        sp = MagicMock()
        calls = []

        def read_state():
            calls.append(True)
            if len(calls) == 1:
                return {"current_transport_state": "PLAYING"}
            self.now += 2
            return {"current_transport_state": "STOPPED"}

        sp.get_current_transport_info.side_effect = read_state
        self.assertFalse(sonos_say.wait_for_playback(sp, 1))

    def test_nonfinite_start_timeout_is_rejected(self):
        for value in (float("nan"), float("inf"), 0, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sonos_say.wait_for_playback(MagicMock(), 10, start_timeout=value)


# ---------------------------------------------------------------------------
# HTTP handler: BrokenPipeError / ConnectionResetError suppression
# ---------------------------------------------------------------------------

class TestBrokenPipeAndConnectionReset(unittest.TestCase):
    """
    wfile.write() raising BrokenPipeError or ConnectionResetError must be
    caught and logged at DEBUG level — never re-raised or traceback-printed.
    """

    def _make_handler(self, mp3_bytes: bytes = b"\xff\xfb\x00" * 4) -> object:
        """Return an _AnnouncementHandler instance with all base-class attrs mocked."""
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        tmp = Path(tmpdir.name) / "audio.mp3"
        tmp.write_bytes(mp3_bytes)

        handler = object.__new__(sonos_say._AnnouncementHandler)
        handler.mp3_path = tmp
        handler.path = "/synthetic-token/announcement.mp3"
        handler.announcement_path = handler.path
        handler.allowed_client_ip = "127.0.0.1"
        handler.active = threading.Event()
        handler.active.set()
        handler.client_address = ("127.0.0.1", 12345)
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.send_error = MagicMock()
        handler.wfile = MagicMock()
        return handler

    def _capture_debug(self):
        """Return log_records list after installing a DEBUG-level capturing handler.

        Also temporarily lowers the logger's own level to DEBUG so that
        debug-level records are not filtered before reaching the handler.
        """
        records: list[logging.LogRecord] = []

        class _Cap(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger = logging.getLogger("sonos_say")
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        h = _Cap(level=logging.DEBUG)
        logger.addHandler(h)
        self.addCleanup(logger.removeHandler, h)
        self.addCleanup(logger.setLevel, old_level)
        return records

    def test_broken_pipe_is_suppressed(self):
        """BrokenPipeError from wfile.write is caught; do_GET does not raise."""
        handler = self._make_handler()
        handler.wfile.write.side_effect = BrokenPipeError("[Errno 32] Broken pipe")
        try:
            handler.do_GET()  # must not raise
        except BrokenPipeError:
            self.fail("BrokenPipeError was not suppressed")

    def test_connection_reset_is_suppressed(self):
        """ConnectionResetError from wfile.write is caught; do_GET does not raise."""
        handler = self._make_handler()
        handler.wfile.write.side_effect = ConnectionResetError("Connection reset by peer")
        try:
            handler.do_GET()  # must not raise
        except ConnectionResetError:
            self.fail("ConnectionResetError was not suppressed")

    def test_broken_pipe_debug_logged_not_as_warning(self):
        """Early disconnect is debug-logged (not WARNING/ERROR) and no traceback string."""
        records = self._capture_debug()
        handler = self._make_handler()
        handler.wfile.write.side_effect = BrokenPipeError("pipe broken")
        handler.do_GET()

        disconnect_records = [
            r for r in records
            if r.levelno == logging.DEBUG and (
                "disconnect" in r.getMessage().lower()
                or "broken pipe" in r.getMessage().lower()
                or "client" in r.getMessage().lower()
                or "pipe broken" in r.getMessage().lower()
            )
        ]
        self.assertTrue(disconnect_records, "Expected a DEBUG log about early disconnect")
        # Must not appear at WARNING or higher
        high_records = [r for r in records if r.levelno >= logging.WARNING]
        self.assertFalse(
            any("pipe" in r.getMessage().lower() for r in high_records),
            "BrokenPipeError must not be logged at WARNING or above",
        )

    def test_other_write_errors_still_propagate(self):
        """OSError (not BrokenPipe/ConnReset) during write propagates normally."""
        handler = self._make_handler()
        handler.wfile.write.side_effect = OSError("disk full")
        with self.assertRaises(OSError):
            handler.do_GET()


# ---------------------------------------------------------------------------
# Restore ordering: fade=False and volume after snapshot
# ---------------------------------------------------------------------------

class TestRestoreOrderingAndFade(unittest.TestCase):
    """snapshot.restore(fade=False) must be called before original volume is restored."""

    def _run_announce_tracking_order(self, saved_media_uri="http://server/song.mp3"):
        """
        Run announce() and return a call_order list recording every
        'restore(fade=...)' and 'volume=N' event in the order they happen.
        """
        call_order: list[str] = []

        sp = _set_standalone(MagicMock())
        sp.ip_address = "192.168.1.50"
        sp.is_coordinator = True

        def _vol_get(self):
            return 30

        def _vol_set(self, v):
            call_order.append(f"volume={v}")

        type(sp).volume = property(_vol_get, _vol_set)

        sp.get_current_transport_info.side_effect = [
            {"current_transport_state": "PLAYING"},
            {"current_transport_state": "STOPPED"},
        ]

        with (
            patch("sonos_say.get_api_key", return_value="sk-fake"),
            patch("sonos_say.synthesise_mp3", side_effect=_synthesise_fixture),
            patch("sonos_say.discover_speaker", return_value=sp),
            patch("sonos_say.local_ip_toward", return_value="192.168.1.10"),
            patch("sonos_say.serve_file") as mock_serve,
            _snapshot_patch() as MockSnapshot,
            patch("time.sleep"),
        ):
            mock_serve.return_value.__enter__ = MagicMock(
                return_value="http://192.168.1.10:9999/announcement.mp3"
            )
            mock_serve.return_value.__exit__ = MagicMock(return_value=False)
            snap = MockSnapshot.return_value
            snap.media_uri = saved_media_uri
            snap.restore.side_effect = lambda fade=True: call_order.append(
                f"restore(fade={fade})"
            )

            sonos_say.announce("Hello", room="Test Room", volume=35, dry_run=False)

        return call_order, snap

    def test_restore_called_with_fade_false(self):
        call_order, snap = self._run_announce_tracking_order()
        self.assertIn(
            "restore(fade=False)", call_order,
            "snapshot.restore must be called with fade=False",
        )
        # Must NOT be called with fade=True
        self.assertNotIn(
            "restore(fade=True)", call_order,
            "snapshot.restore must not use fade=True",
        )

    def test_snapshot_restored_before_original_volume(self):
        call_order, _ = self._run_announce_tracking_order()
        self.assertIn("restore(fade=False)", call_order)
        self.assertIn("volume=30", call_order, "Original volume must be restored")
        restore_pos = call_order.index("restore(fade=False)")
        vol30_pos = call_order.index("volume=30")
        self.assertLess(
            restore_pos,
            vol30_pos,
            f"snapshot.restore must come before volume=30; got order: {call_order}",
        )

    def test_announcement_volume_set_during_playback(self):
        call_order, _ = self._run_announce_tracking_order()
        self.assertIn("volume=35", call_order, "Announcement volume must be set")
        vol35_pos = call_order.index("volume=35")
        restore_pos = call_order.index("restore(fade=False)")
        # Announcement volume is set before restore
        self.assertLess(
            vol35_pos, restore_pos,
            "Announcement volume must be set before restore",
        )


# ---------------------------------------------------------------------------
# Empty URI clearing
# ---------------------------------------------------------------------------

class TestEmptyUriClearing(unittest.TestCase):
    """When the pre-announcement media URI was empty, clear the AV transport URI."""

    def _run_with_saved_uri(self, saved_uri, transport_states=None):
        """Run announce() and return the mock speaker."""
        sp = _set_standalone(MagicMock())
        sp.ip_address = "192.168.1.50"
        sp.volume = 30
        sp.get_current_track_info.return_value = {"uri": ""}

        if transport_states is None:
            transport_states = [
                "PLAYING",   # wait_for_playback phase1
                "STOPPED",   # wait_for_playback phase2
                "STOPPED",   # pre-clear state check in finally
            ]
        sp.get_current_transport_info.side_effect = [
            {"current_transport_state": s} for s in transport_states
        ]

        with (
            patch("sonos_say.get_api_key", return_value="sk-fake"),
            patch("sonos_say.synthesise_mp3", side_effect=_synthesise_fixture),
            patch("sonos_say.discover_speaker", return_value=sp),
            patch("sonos_say.local_ip_toward", return_value="192.168.1.10"),
            patch("sonos_say.serve_file") as mock_serve,
            _snapshot_patch() as MockSnapshot,
            patch("time.sleep"),
        ):
            mock_serve.return_value.__enter__ = MagicMock(
                return_value="http://192.168.1.10:9999/announcement.mp3"
            )
            mock_serve.return_value.__exit__ = MagicMock(return_value=False)
            snap = MockSnapshot.return_value
            snap.media_uri = saved_uri

            sonos_say.announce("Hello", room="Test Room", volume=35, dry_run=False)

        return sp

    def test_empty_saved_uri_calls_set_av_transport_uri(self):
        """Empty saved URI + coordinator → SetAVTransportURI called with empty args."""
        sp = self._run_with_saved_uri(saved_uri="")
        sp.avTransport.SetAVTransportURI.assert_called_once_with(
            [("InstanceID", 0), ("CurrentURI", ""), ("CurrentURIMetaData", "")]
        )

    def test_none_saved_uri_treated_as_empty(self):
        """None saved URI is treated the same as empty string."""
        sp = self._run_with_saved_uri(saved_uri=None)
        sp.avTransport.SetAVTransportURI.assert_called_once_with(
            [("InstanceID", 0), ("CurrentURI", ""), ("CurrentURIMetaData", "")]
        )

    def test_nonempty_saved_uri_does_not_clear(self):
        """Nonempty saved URI → SetAVTransportURI must NOT be called."""
        sp = self._run_with_saved_uri(
            saved_uri="x-file-cifs://server/music.mp3",
            transport_states=["PLAYING", "STOPPED"],  # no 3rd call needed
        )
        sp.avTransport.SetAVTransportURI.assert_not_called()

    def test_unplanned_non_coordinator_does_not_clear(self):
        """Without a room plan, a group member's empty URI is left alone."""
        for coordinator in (False, None):
            with self.subTest(coordinator=coordinator):
                sp = _set_standalone(MagicMock())
                sp.is_coordinator = coordinator
                snapshot = MagicMock(media_uri="", mute=False)
                failed, _ = sonos_say._restore_after_announcement(
                    sp, snapshot, True, 30, None, False,
                )
                self.assertIs(failed, coordinator is None)
                sp.stop.assert_not_called()
                sp.avTransport.SetAVTransportURI.assert_not_called()

    def test_unplanned_nonempty_snapshot_ignores_coordinator_flag(self):
        """Installed Snapshot callers pass topology=None and a nonempty URI."""
        sp = _set_standalone(MagicMock())
        type(sp).is_coordinator = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("offline")),
        )
        snapshot = MagicMock(
            media_uri="prior-uri", mute=False, transport_state="STOPPED",
        )
        failed, interrupt = sonos_say._restore_after_announcement(
            sp, snapshot, True, 30, None, False,
        )
        self.assertFalse(failed)
        self.assertIsNone(interrupt)
        snapshot.restore.assert_called_once_with(fade=False)
        sp.avTransport.SetAVTransportURI.assert_not_called()
        self.assertEqual(sp.volume, 30)

    def test_stop_called_before_clear_if_playing(self):
        """If transport is PLAYING when URI clearing starts, speaker.stop() is called."""
        sp = self._run_with_saved_uri(
            saved_uri="",
            transport_states=[
                "PLAYING",        # wait_for_playback phase1
                "STOPPED",        # wait_for_playback phase2
                "PLAYING",        # still playing at clear-check in finally
            ],
        )
        sp.stop.assert_called_once()

    def test_stop_not_called_if_already_stopped(self):
        """If transport is already STOPPED, speaker.stop() is not called."""
        sp = self._run_with_saved_uri(
            saved_uri="",
            transport_states=["PLAYING", "STOPPED", "STOPPED"],
        )
        sp.stop.assert_not_called()

    def test_set_av_transport_uri_failure_is_restore_failure(self):
        """A URI cleanup failure is reported as a restoration failure."""
        sp = _set_standalone(MagicMock())
        sp.ip_address = "192.168.1.50"
        sp.is_coordinator = True
        sp.volume = 30
        sp.get_current_track_info.return_value = {"uri": ""}
        sp.get_current_transport_info.side_effect = [
            {"current_transport_state": "PLAYING"},
            {"current_transport_state": "STOPPED"},
            {"current_transport_state": "STOPPED"},
        ]
        sp.avTransport.SetAVTransportURI.side_effect = RuntimeError("SOAP fault")

        warn_records: list[logging.LogRecord] = []

        class _Cap(logging.Handler):
            def emit(self, record):
                warn_records.append(record)

        h = _Cap()
        logging.getLogger("sonos_say").addHandler(h)
        try:
            with (
                patch("sonos_say.get_api_key", return_value="sk-fake"),
                patch("sonos_say.synthesise_mp3", side_effect=_synthesise_fixture),
                patch("sonos_say.discover_speaker", return_value=sp),
                patch("sonos_say.local_ip_toward", return_value="192.168.1.10"),
                patch("sonos_say.serve_file") as mock_serve,
                _snapshot_patch() as MockSnapshot,
                patch("time.sleep"),
            ):
                mock_serve.return_value.__enter__ = MagicMock(
                    return_value="http://192.168.1.10:9999/announcement.mp3"
                )
                mock_serve.return_value.__exit__ = MagicMock(return_value=False)
                snap = MockSnapshot.return_value
                snap.media_uri = ""

                with self.assertRaises(sonos_say.SonosSayError) as raised:
                    sonos_say.announce(
                        "Hello",
                        room="Test Room",
                        volume=35,
                        dry_run=False,
                    )
        finally:
            logging.getLogger("sonos_say").removeHandler(h)

        self.assertEqual(raised.exception.code, "SONOS_RESTORE_FAILED")
        self.assertTrue(
            any(
                r.levelno >= logging.WARNING and "transport" in r.getMessage().lower()
                for r in warn_records
            ),
            "Expected a warning about the URI-clear failure",
        )


# ---------------------------------------------------------------------------
# Cleanup on interrupt / exception: new ordering assertions
# ---------------------------------------------------------------------------

class TestInterruptCleanupOrdering(unittest.TestCase):
    """Verify restore order and fade=False are preserved under interrupt/exception."""

    def _run_with_interrupt(self, interrupt_exc):
        """Inject *interrupt_exc* during wait_for_playback; return (call_order, sp)."""
        call_order: list[str] = []

        sp = _set_standalone(MagicMock())
        sp.ip_address = "192.168.1.50"
        sp.is_coordinator = True

        def _vol_get(self):
            return 30

        def _vol_set(self, v):
            call_order.append(f"volume={v}")

        type(sp).volume = property(_vol_get, _vol_set)
        sp.get_current_transport_info.return_value = {"current_transport_state": "STOPPED"}

        def _fake_wait(*_a, **_kw):
            raise interrupt_exc

        with (
            patch("sonos_say.get_api_key", return_value="sk-fake"),
            patch("sonos_say.synthesise_mp3", side_effect=_synthesise_fixture),
            patch("sonos_say.discover_speaker", return_value=sp),
            patch("sonos_say.local_ip_toward", return_value="192.168.1.10"),
            patch("sonos_say.serve_file") as mock_serve,
            _snapshot_patch() as MockSnapshot,
            patch("sonos_say.wait_for_playback", side_effect=_fake_wait),
            patch("time.sleep"),
        ):
            mock_serve.return_value.__enter__ = MagicMock(
                return_value="http://192.168.1.10:9999/announcement.mp3"
            )
            mock_serve.return_value.__exit__ = MagicMock(return_value=False)
            snap = MockSnapshot.return_value
            snap.media_uri = "http://server/existing.mp3"  # nonempty, no URI clear
            snap.restore.side_effect = lambda fade=True: call_order.append(
                f"restore(fade={fade})"
            )

            with self.assertRaises(type(interrupt_exc)):
                sonos_say.announce("Hello", room="Test Room", volume=35, dry_run=False)

        return call_order, sp

    def test_keyboard_interrupt_restore_fade_false(self):
        call_order, _ = self._run_with_interrupt(KeyboardInterrupt())
        self.assertIn("restore(fade=False)", call_order)

    def test_keyboard_interrupt_volume_after_restore(self):
        call_order, _ = self._run_with_interrupt(KeyboardInterrupt())
        restore_pos = call_order.index("restore(fade=False)")
        vol30_pos = call_order.index("volume=30")
        self.assertLess(restore_pos, vol30_pos)

    def test_sigterm_restore_fade_false(self):
        call_order, _ = self._run_with_interrupt(SystemExit(143))
        self.assertIn("restore(fade=False)", call_order)

    def test_sigterm_volume_after_restore(self):
        call_order, _ = self._run_with_interrupt(SystemExit(143))
        restore_pos = call_order.index("restore(fade=False)")
        vol30_pos = call_order.index("volume=30")
        self.assertLess(restore_pos, vol30_pos)

    def test_generic_exception_restore_fade_false(self):
        """An unhandled exception in the try block still triggers fade=False restore."""
        sp = _set_standalone(MagicMock())
        sp.ip_address = "192.168.1.50"
        sp.volume = 30
        sp.is_coordinator = True
        sp.play_uri.side_effect = RuntimeError("network error")

        call_order: list[str] = []

        with (
            patch("sonos_say.get_api_key", return_value="sk-fake"),
            patch("sonos_say.synthesise_mp3", side_effect=_synthesise_fixture),
            patch("sonos_say.discover_speaker", return_value=sp),
            patch("sonos_say.local_ip_toward", return_value="192.168.1.10"),
            patch("sonos_say.serve_file") as mock_serve,
            _snapshot_patch() as MockSnapshot,
            patch("time.sleep"),
        ):
            mock_serve.return_value.__enter__ = MagicMock(
                return_value="http://192.168.1.10:9999/announcement.mp3"
            )
            mock_serve.return_value.__exit__ = MagicMock(return_value=False)
            snap = MockSnapshot.return_value
            snap.media_uri = "http://server/song.mp3"
            snap.restore.side_effect = lambda fade=True: call_order.append(
                f"restore(fade={fade})"
            )

            with self.assertRaises(RuntimeError):
                sonos_say.announce("Hello", room="Test Room", volume=35, dry_run=False)

        self.assertIn("restore(fade=False)", call_order)


class TestRestoreVerification(unittest.TestCase):
    def _restore(self, speaker, snapshot, detached=True):
        snapshot.mute = False
        with patch("sonos_say.restore_target_group") as regroup:
            result = sonos_say._restore_after_announcement(
                speaker, snapshot, True, 30,
                sonos_say.validate_announcement_target(speaker), detached,
            )
        if detached:
            regroup.assert_called_once()
        else:
            regroup.assert_not_called()
        snapshot.restore.assert_called_once_with(fade=False)
        return result

    def test_volume_readback_error_is_failure(self):
        sp = _set_standalone(MagicMock())
        snapshot = MagicMock(media_uri="prior-uri")
        type(sp).volume = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("offline")),
            lambda self, value: None,
        )
        failed, _ = self._restore(sp, snapshot)
        self.assertTrue(failed)

    def test_volume_mismatch_is_failure(self):
        sp = _set_standalone(MagicMock())
        type(sp).volume = property(lambda self: 31, lambda self, value: None)
        failed, _ = self._restore(sp, MagicMock(media_uri="prior-uri"))
        self.assertTrue(failed)

    def test_uri_verification_error_or_missing_uri_is_failure(self):
        for result in (RuntimeError("offline"), {}, {"uri": None}, {"uri": "stale"}):
            with self.subTest(result=result):
                sp = _set_standalone(MagicMock())
                sp.get_current_transport_info.return_value = {
                    "current_transport_state": "STOPPED",
                }
                if isinstance(result, Exception):
                    sp.get_current_track_info.side_effect = result
                else:
                    sp.get_current_track_info.return_value = result
                failed, _ = self._restore(sp, MagicMock(media_uri=""), detached=False)
                self.assertTrue(failed)
                sp.avTransport.SetAVTransportURI.assert_called_once()

    def test_cleanup_starts_no_further_step_after_45_seconds(self):
        sp = _set_standalone(MagicMock())
        writes = []
        type(sp).volume = property(
            lambda self: 30, lambda self, value: writes.append(value),
        )
        snapshot = MagicMock(media_uri="prior-uri", mute=False)
        clock = [0.0]

        def restore(fade=False):
            clock[0] = 45.0

        snapshot.restore.side_effect = restore
        self.assertEqual(getattr(sonos_say, "CLEANUP_BUDGET_SECONDS", None), 45.0)
        with patch.object(sonos_say.time, "monotonic", side_effect=lambda: clock[0]):
            failed, interrupt = sonos_say._restore_after_announcement(
                sp, snapshot, True, 30,
                sonos_say.validate_announcement_target(sp), False,
            )
        self.assertTrue(failed)
        self.assertIsNone(interrupt)
        self.assertEqual(writes, [])
        snapshot.restore.assert_called_once_with(fade=False)

    def test_slow_local_restoration_cannot_starve_rejoin(self):
        sp = _set_standalone(MagicMock())
        writes = []
        type(sp).volume = property(
            lambda self: 30, lambda self, value: writes.append(value),
        )
        snapshot = MagicMock(media_uri="prior-uri", mute=False)
        clock = [0.0]

        def slow_restore(fade=False):
            clock[0] = sonos_say.CLEANUP_BUDGET_SECONDS - 1

        snapshot.restore.side_effect = slow_restore
        plan = sonos_say.validate_announcement_target(sp)
        with patch.object(sonos_say.time, "monotonic", side_effect=lambda: clock[0]), \
                patch("sonos_say.restore_target_group") as regroup:
            failed, _ = sonos_say._restore_after_announcement(
                sp, snapshot, True, 30, plan, True,
            )
        self.assertTrue(failed)
        self.assertEqual(writes, [])  # Local steps stop at the reserve.
        regroup.assert_called_once_with(plan)

    def test_cleanup_rpc_caps_soco_timeout(self):
        import soco.config as config
        sp = _set_standalone(MagicMock())
        seen = []
        snapshot = MagicMock(media_uri="prior-uri", mute=False)

        def restore(fade=False):
            seen.append((config.REQUEST_TIMEOUT, config.ZGT_EVENT_FALLBACK))

        snapshot.restore.side_effect = restore
        sonos_say._restore_after_announcement(
            sp, snapshot, True, 30,
            sonos_say.validate_announcement_target(sp), False,
        )
        self.assertIn((5.0, False), seen)
        self.assertEqual(config.REQUEST_TIMEOUT, 20.0)
        self.assertIs(config.ZGT_EVENT_FALLBACK, True)

    def test_uri_cleanup_attempts_clear_but_does_not_hide_transport_error(self):
        for result in (RuntimeError("offline"), {},
                       {"current_transport_state": "UNKNOWN"}):
            with self.subTest(result=result):
                sp = _set_standalone(MagicMock())
                sp.get_current_track_info.return_value = {"uri": ""}
                if isinstance(result, Exception):
                    sp.get_current_transport_info.side_effect = result
                else:
                    sp.get_current_transport_info.return_value = result
                failed, _ = self._restore(sp, MagicMock(media_uri=""), detached=False)
                self.assertTrue(failed)
                sp.avTransport.SetAVTransportURI.assert_called_once()

    def test_interrupt_during_verification_cannot_count_as_success(self):
        sp = _set_standalone(MagicMock())
        type(sp).volume = property(
            lambda self: (_ for _ in ()).throw(KeyboardInterrupt()),
            lambda self, value: None,
        )
        failed, interrupt = self._restore(sp, MagicMock(media_uri="prior-uri"))
        self.assertTrue(failed)
        self.assertIsInstance(interrupt, KeyboardInterrupt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
