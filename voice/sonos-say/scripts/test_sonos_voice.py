import contextlib
import http.server
import io
import json
import os
import subprocess
import sys
import threading
import types
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

_soco = types.ModuleType("soco")
_soco.SoCo = MagicMock()
_soco.discover = MagicMock(return_value=[])
_snapshot = types.ModuleType("soco.snapshot")
_snapshot.Snapshot = MagicMock()
sys.modules.setdefault("soco", _soco)
sys.modules.setdefault("soco.snapshot", _snapshot)

import sonos_say  # noqa: E402


TEST_CONFIG = {
    "ELEVENLABS_VOICE_ID": "test_voice_123",
    "SONOS_ROOM": "Test Room",
    "ELEVENLABS_KEYCHAIN_SERVICE": "test-tts-service",
    "ELEVENLABS_KEYCHAIN_ACCOUNT": "test-tts-account",
}


class ConfiguredTestCase(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, TEST_CONFIG, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        # Key/provider failures now follow a valid, read-only preflight.
        speaker = MagicMock(uid="test-target", ip_address="192.0.2.2",
                            is_visible=True, is_satellite=False,
                            has_satellites=False)
        speaker.zone_group_state.has_subscriptions = False
        speaker.group = SimpleNamespace(coordinator=speaker, members=[speaker])
        discovery = patch("sonos_say.discover_speaker", return_value=speaker)
        routing = patch("sonos_say.local_ip_toward", return_value="192.0.2.1")
        discovery.start()
        routing.start()
        self.addCleanup(discovery.stop)
        self.addCleanup(routing.stop)


class TestVoiceCli(ConfiguredTestCase):
    def test_parser_defaults_to_v4_with_implicit_stability(self):
        args = sonos_say.build_parser().parse_args(["Hello"])

        self.assertEqual(args.model, "eleven_v4")
        self.assertIsNone(args.stability)

    def test_parser_accepts_override(self):
        args = sonos_say.build_parser().parse_args(
            ["Hello", "--stability", "robust"]
        )

        self.assertEqual(args.stability, "robust")

    def test_parser_preserves_model_override(self):
        args = sonos_say.build_parser().parse_args(
            ["Hello", "--model", "eleven_flash_v2_5"]
        )

        self.assertEqual(args.model, "eleven_flash_v2_5")

    def test_dry_run_reports_policy_without_external_io(self):
        captured = io.StringIO()

        with patch("sys.stdout", captured):
            with patch("sonos_say.get_api_key") as get_api_key:
                result = sonos_say.main(["Test message", "--dry-run"])

        self.assertEqual(result, 0)
        self.assertIn("model='eleven_v4'", captured.getvalue())
        self.assertIn("stability=legacy (unset)", captured.getvalue())
        get_api_key.assert_not_called()

    def test_non_v3_stability_fails_before_io(self):
        with patch("sonos_say.get_api_key") as get_api_key:
            result = sonos_say.main(
                [
                    "Hello",
                    "--model",
                    "eleven_flash_v2_5",
                    "--stability",
                    "natural",
                ]
            )

        self.assertEqual(result, 1)
        get_api_key.assert_not_called()


class TestVoiceConfiguration(ConfiguredTestCase):
    def test_voice_and_room_cli_override_environment(self):
        args = sonos_say.build_parser().parse_args([
            "Hello", "--voice-id", "cli_voice", "--room", "CLI Room",
        ])
        self.assertEqual(args.voice_id, "cli_voice")
        self.assertEqual(args.room, "CLI Room")

    def test_environment_defaults(self):
        args = sonos_say.build_parser().parse_args(["Hello"])
        self.assertEqual(args.voice_id, TEST_CONFIG["ELEVENLABS_VOICE_ID"])
        self.assertEqual(args.room, TEST_CONFIG["SONOS_ROOM"])

    def test_missing_or_blank_settings_fail_before_external_io(self):
        for name in TEST_CONFIG:
            for value in (None, "", "   "):
                with self.subTest(name=name, value=value):
                    settings = dict(TEST_CONFIG)
                    if value is None:
                        settings.pop(name)
                    else:
                        settings[name] = value
                    with (
                        patch.dict(os.environ, settings, clear=True),
                        patch("sonos_say.get_api_key") as key,
                        patch("sonos_say.synthesise_mp3") as synth,
                        patch("sonos_say.discover_speaker") as discover,
                        patch("sys.stderr", io.StringIO()),
                    ):
                        self.assertEqual(sonos_say.main(["Hello"]), 1)
                    key.assert_not_called()
                    synth.assert_not_called()
                    discover.assert_not_called()

    def test_dry_run_needs_no_keychain_and_performs_no_external_io(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("sonos_say.get_api_key") as key,
            patch("sonos_say.synthesise_mp3") as synth,
            patch("sonos_say.discover_speaker") as discover,
            patch("sonos_say.serve_file") as serve,
            patch("sys.stdout", io.StringIO()),
        ):
            self.assertEqual(sonos_say.main([
                "Hello", "--dry-run", "--voice-id", "test_voice",
                "--room", "Test Room",
            ]), 0)
        for operation in (key, synth, discover, serve):
            operation.assert_not_called()

    def test_dry_run_still_requires_voice_and_room(self):
        for missing in ("ELEVENLABS_VOICE_ID", "SONOS_ROOM"):
            settings = dict(TEST_CONFIG)
            settings.pop(missing)
            with (
                self.subTest(missing=missing),
                patch.dict(os.environ, settings, clear=True),
                patch("sys.stderr", io.StringIO()),
            ):
                self.assertEqual(sonos_say.main(["Hello", "--dry-run"]), 1)

    def test_help_needs_no_configuration(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("sys.stdout", io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            sonos_say.main(["--help"])
        self.assertEqual(raised.exception.code, 0)

    def test_callable_resolves_configuration_and_keychain_selectors(self):
        with (
            patch("sonos_say.get_api_key", side_effect=RuntimeError) as key,
            self.assertRaises(sonos_say.SonosSayError) as raised,
        ):
            sonos_say.announce("Hello")
        key.assert_called_once_with("test-tts-service", "test-tts-account")
        self.assertEqual(raised.exception.code, "ELEVENLABS_KEY_FAILED")

    def test_keychain_subprocess_failures_emit_only_fixed_cli_marker(self):
        errors = (
            subprocess.TimeoutExpired("private-selector", 10,
                                      stderr=b"private-stderr"),
            FileNotFoundError("private-selector"),
            subprocess.CalledProcessError(44, "private-selector",
                                          stderr=b"private-stderr"),
            MagicMock(stdout="   "),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                with patch("sonos_say.subprocess.run",
                           side_effect=error if isinstance(error, Exception) else None,
                           return_value=error), \
                        patch("sys.stderr", io.StringIO()) as stderr:
                    self.assertEqual(sonos_say.main(["Hello", "--debug"]), 2)
                self.assertEqual(stderr.getvalue(),
                                 "SONOS_SAY_ERROR=ELEVENLABS_KEY_FAILED\n")

    def test_callable_rejects_explicit_blank_configuration(self):
        for override in ({"voice_id": ""}, {"room": " "}):
            with (
                self.subTest(override=override),
                patch("sonos_say.get_api_key") as key,
                self.assertRaises(ValueError),
            ):
                sonos_say.announce("Hello", **override)
            key.assert_not_called()

    def test_nonfinite_or_nonpositive_timeout_fails_before_io(self):
        for value in ("nan", "inf", "-inf", "0", "-1"):
            with self.subTest(value=value):
                with (
                    patch("sonos_say.get_api_key") as key,
                    patch("sys.stderr", io.StringIO()),
                    self.assertRaises(SystemExit) as raised,
                ):
                    sonos_say.main(["Hello", "--dry-run", "--timeout=" + value])
                self.assertEqual(raised.exception.code, 2)
                key.assert_not_called()
                with self.assertRaises(ValueError):
                    sonos_say.announce("Hello", timeout=float(value), dry_run=True)
                speaker = MagicMock()
                with self.assertRaises(ValueError):
                    sonos_say.wait_for_playback(speaker, float(value))
                speaker.get_current_transport_info.assert_not_called()


class TestVoicePayload(ConfiguredTestCase):
    def _payload(self, model, stability, text="Hello"):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"mp3"
        response.__enter__.return_value.status = 200

        with patch("sonos_say._tts_opener") as opener:
            open_url = opener.return_value.open
            open_url.return_value = response
            sonos_say.synthesise_mp3(
                text=text,
                voice_id="test_voice_123",
                model=model,
                api_key="secret",
                dest_path=MagicMock(),
                stability=stability,
            )

        return json.loads(open_url.call_args.args[0].data)

    def test_v3_payload_preserves_tags_and_only_adds_stability(self):
        text = "[whispers] Bonjour. [laughs]"
        preset = sonos_say.resolve_stability("eleven_v3", "robust")

        self.assertEqual(
            self._payload("eleven_v3", preset, text),
            {
                "model_id": "eleven_v3",
                "text": text,
                "voice_settings": {"stability": 1.0},
            },
        )

    def test_non_v3_payload_remains_legacy(self):
        self.assertEqual(
            self._payload("eleven_flash_v2_5", None),
            {"model_id": "eleven_flash_v2_5", "text": "Hello"},
        )

    def test_default_v4_payload_omits_voice_settings(self):
        model = sonos_say.DEFAULT_MODEL

        self.assertEqual(model, "eleven_v4")
        self.assertEqual(
            self._payload(
                model,
                sonos_say.resolve_stability(model, None),
            ),
            {"model_id": "eleven_v4", "text": "Hello"},
        )

    def test_http_error_excludes_body_text_and_key(self):
        body = io.BytesIO(b"provider body with [request text] and secret")
        error = urllib.error.HTTPError(
            "https://example.invalid",
            400,
            "Bad Request",
            {},
            body,
        )

        with patch("sonos_say._tts_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(RuntimeError) as raised:
                sonos_say.synthesise_mp3(
                    text="[request text]",
                    voice_id="test_voice_123",
                    model="eleven_v3",
                    api_key="secret",
                    dest_path=Path("unused.mp3"),
                    stability=sonos_say.resolve_stability(
                        "eleven_v3",
                        None,
                    ),
                )

        self.assertEqual(
            str(raised.exception),
            "ElevenLabs request failed",
        )


class TestRedirectRejection(ConfiguredTestCase):
    @contextlib.contextmanager
    def _server(self, handler):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_redirects_never_reach_destination(self):
        received = []
        initial_keys = []
        settings = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                received.append((self.path, self.headers.get("xi-api-key")))
                self.send_response(200)
                self.end_headers()

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if self.path.startswith("/target"):
                    return self.do_GET()
                initial_keys.append(self.headers.get("xi-api-key"))
                self.send_response(settings["status"], "provider-sensitive-reason")
                self.send_header("Location", settings["location"])
                self.end_headers()
                self.wfile.write(b"provider-sensitive-body")

        with self._server(Handler) as origin, self._server(Handler) as dest:
            locations = (
                f"http://localhost:{dest}/target",  # different host and port
                f"http://127.0.0.1:{origin}/target",
                "/target",
                "../../target",
                f"//localhost:{dest}/target",
            )
            for status in (301, 302, 303, 307, 308, 300, 304):
                for location in locations:
                    settings.update(status=status, location=location)
                    with (
                        self.subTest(status=status, location=location),
                        patch("sonos_say.ELEVENLABS_TTS_BASE",
                              f"http://127.0.0.1:{origin}/v1"),
                    ):
                        destination = MagicMock()
                        with self.assertRaises(RuntimeError) as raised:
                            sonos_say.synthesise_mp3(
                                "private-test-text", "test_voice", "eleven_v4",
                                "dummy-api-key", destination,
                            )
                        self.assertEqual(str(raised.exception),
                                         "ElevenLabs request failed")
                        destination.write_bytes.assert_not_called()
                        self.assertEqual(received, [])
                        self.assertEqual(initial_keys[-1], "dummy-api-key")

    def test_nonredirected_success_saves_audio(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(200)
                self.send_header("Content-Length", "8")
                self.end_headers()
                self.wfile.write(b"test-mp3")

        with self._server(Handler) as port:
            with patch("sonos_say.ELEVENLABS_TTS_BASE", f"http://127.0.0.1:{port}"):
                destination = MagicMock()
                sonos_say.synthesise_mp3(
                    "Hello", "test_voice", "eleven_v4", "dummy-api-key", destination,
                )
                destination.write_bytes.assert_called_once_with(b"test-mp3")

    def test_provider_error_details_are_not_logged_or_printed(self):
        error = urllib.error.HTTPError(
            "https://example.invalid/dummy-api-key", 302,
            "dummy-api-key private-test-text", {"Location": "private-location"},
            io.BytesIO(b"private-body"),
        )
        with (
            patch("sonos_say._tts_opener") as opener,
            patch("sonos_say.get_api_key", return_value="dummy-api-key"),
            patch("sys.stderr", io.StringIO()) as stderr,
            patch("sonos_say.log") as logger,
        ):
            opener.return_value.open.side_effect = error
            self.assertEqual(sonos_say.main(["private-test-text", "--debug"]), 2)
        self.assertEqual(stderr.getvalue(),
                         "SONOS_SAY_ERROR=ELEVENLABS_SYNTHESIS_FAILED\n")
        logged = str(logger.mock_calls)
        for value in ("dummy-api-key", "private-test-text", "private-location",
                      "private-body"):
            self.assertNotIn(value, logged)
