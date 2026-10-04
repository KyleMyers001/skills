import io
import os
import http.client
import http.server
import subprocess
import threading
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from contextlib import ExitStack
from unittest.mock import patch

import airpods_say
from airpods_test_support import loopback_server

_REAL_TTS = airpods_say.stream_tts
_REAL_KEYCHAIN = airpods_say.get_keychain_password


class TestVoiceCli(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.stderr = stack.enter_context(patch("sys.stderr", io.StringIO()))
        self.find_ffplay = stack.enter_context(
            patch("airpods_say.find_ffplay", return_value="/fake/ffplay")
        )
        self.device = stack.enter_context(
            patch(
                "airpods_say.get_default_output_device_name",
                return_value="Test AirPods",
            )
        )
        self.keychain = stack.enter_context(
            patch("airpods_say.get_keychain_password", return_value="test-key")
        )
        self.tts = stack.enter_context(patch("airpods_say.stream_tts"))
        self.player = stack.enter_context(
            patch("airpods_say.play_stream", side_effect=self._play)
        )

    @staticmethod
    def _play(**kwargs):
        kwargs["resp"].close()
        return 0

    def configure(self):
        os.environ.update(
            ELEVENLABS_VOICE_ID="test-voice",
            ELEVENLABS_KEYCHAIN_SERVICE="test-tts-service",
            ELEVENLABS_KEYCHAIN_ACCOUNT="test-tts-account",
        )

    def assert_no_sensitive_io(self):
        self.keychain.assert_not_called()
        self.tts.assert_not_called()
        self.player.assert_not_called()

    def test_post_header_detection_error_closes_response_without_player(self):
        self.configure()
        for failure in (airpods_say.CoreAudioError('unavailable'), OSError('unavailable'), None):
            with self.subTest(failure=failure):
                self.tts.return_value.close.reset_mock()
                self.device.side_effect = ['Test AirPods', failure]
                self.assertEqual(airpods_say.main(['Hello']), 2)
                self.tts.return_value.close.assert_called_once()
                self.player.assert_not_called()

    def test_timeout_reaches_request_and_playback(self):
        self.configure()
        self.assertEqual(airpods_say.main(["--timeout", "7", "Hello"]), 0)
        self.assertEqual(self.tts.call_args.kwargs["timeout"], 7)
        self.assertEqual(self.player.call_args.kwargs["timeout"], 7)

    def test_raw_request_failures_return_5_without_sensitive_details(self):
        self.configure()
        for error in (TimeoutError("dummy-key private-body"),
                      http.client.RemoteDisconnected("private-body"),
                      http.client.BadStatusLine("private-body")):
            with self.subTest(error=type(error).__name__):
                self.stderr.seek(0)
                self.stderr.truncate()
                # Exercise the real normalizer rather than mocking its result.
                with patch("elevenlabs_voice.tts_opener", create=True) as opener, \
                        patch("urllib.request.urlopen", side_effect=error):
                    opener.return_value.open.side_effect = error
                    self.tts.side_effect = _REAL_TTS
                    self.assertEqual(airpods_say.main(["Hello", "--debug"]), 5)
                output = self.stderr.getvalue()
                for value in ("dummy-key", "private-body", "Traceback"):
                    self.assertNotIn(value, output)
        self.player.assert_not_called()

    def test_keychain_selectors_and_stderr_are_private(self):
        self.configure()
        os.environ.update(ELEVENLABS_KEYCHAIN_SERVICE="service@example.invalid",
                          ELEVENLABS_KEYCHAIN_ACCOUNT="account@example.invalid")
        self.keychain.side_effect = _REAL_KEYCHAIN
        for debug in ([], ["--debug"]):
            with self.subTest(debug=debug):
                self.stderr.seek(0)
                self.stderr.truncate()
                with patch("elevenlabs_voice.subprocess.run", return_value=MagicMock(
                        returncode=44, stdout="dummy-key", stderr="private-stderr")):
                    self.assertEqual(airpods_say.main(["Hello"] + debug), 4)
                for value in ("service@example.invalid", "account@example.invalid",
                              "dummy-key", "private-stderr"):
                    self.assertNotIn(value, self.stderr.getvalue())
        self.tts.assert_not_called()

    def test_parser_defaults_to_v4_with_implicit_stability(self):
        args = airpods_say.build_parser().parse_args(["Hello"])

        self.assertEqual(args.model, "eleven_v4")
        self.assertIsNone(args.stability)

    def test_parser_has_no_bundled_voice(self):
        args = airpods_say.build_parser().parse_args(["Hello"])

        self.assertIsNone(args.voice_id)

    def test_parser_accepts_all_stability_presets(self):
        parser = airpods_say.build_parser()

        for name in ("creative", "natural", "robust"):
            with self.subTest(name=name):
                args = parser.parse_args(["--stability", name, "Hello"])
                self.assertEqual(args.stability, name)

    def test_parser_preserves_model_and_voice_overrides(self):
        args = airpods_say.build_parser().parse_args(
            [
                "--model",
                "eleven_flash_v2_5",
                "--voice-id",
                "AnotherVoice",
                "Hello",
            ]
        )

        self.assertEqual(args.model, "eleven_flash_v2_5")
        self.assertEqual(args.voice_id, "AnotherVoice")

    def test_invalid_identifiers_never_reach_device_or_sensitive_io(self):
        for value in ('eleven_v3\n', 'a\r\n', 'a\nb', ' a', '', 'a' * 65):
            for option in ('--voice-id', '--model'):
                with self.subTest(value=value, option=option):
                    self.configure()
                    self.assertEqual(airpods_say.main([option, value, 'Hello']), 1)
        self.device.assert_not_called()
        self.find_ffplay.assert_not_called()
        self.assert_no_sensitive_io()
        for value in (None, 1, []):
            for args in ((value, 'eleven_v4'), ('voice', value)):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    airpods_say.validate_ids(*args)
        airpods_say.validate_ids('v' * 64, 'm' * 64)

    def test_newline_environment_voice_is_not_stripped(self):
        self.configure()
        os.environ['ELEVENLABS_VOICE_ID'] = 'test-voice\n'
        self.assertEqual(airpods_say.main(['Hello']), 1)
        self.assert_no_sensitive_io()

    def test_model_validation_accepts_dots(self):
        airpods_say.validate_ids("test-voice", "eleven.v3-preview_1")

    def test_non_v3_stability_fails_before_io(self):
        self.configure()
        result = airpods_say.main(
            [
                "--model",
                "eleven_flash_v2_5",
                "--stability",
                "natural",
                "Hello",
            ]
        )

        self.assertEqual(result, 1)
        self.assertIn("only supported with eleven_v3", self.stderr.getvalue())
        self.find_ffplay.assert_not_called()
        self.assert_no_sensitive_io()

    def test_environment_config_reaches_keychain_and_synthesis(self):
        self.configure()

        self.assertEqual(airpods_say.main(["Hello"]), 0)

        self.keychain.assert_called_once_with(
            "test-tts-service", "test-tts-account"
        )
        self.assertEqual(self.tts.call_args.kwargs["voice_id"], "test-voice")
        self.player.assert_called_once()
        self.tts.return_value.close.assert_called_once()

    def test_cli_voice_overrides_environment(self):
        self.configure()

        result = airpods_say.main(["--voice-id", "override-voice", "Hello"])

        self.assertEqual(result, 0)
        self.assertEqual(self.tts.call_args.kwargs["voice_id"], "override-voice")

    def test_missing_or_blank_voice_fails_before_io(self):
        for value in (None, "", "  "):
            with self.subTest(value=value):
                os.environ.clear()
                if value is not None:
                    os.environ["ELEVENLABS_VOICE_ID"] = value

                self.assertEqual(airpods_say.main(["Hello"]), 1)

        self.assertIn("ELEVENLABS_VOICE_ID", self.stderr.getvalue())
        self.find_ffplay.assert_not_called()
        self.device.assert_not_called()
        self.assert_no_sensitive_io()

    def test_blank_cli_voice_does_not_fall_back_to_environment(self):
        self.configure()

        self.assertEqual(airpods_say.main(["--voice-id", "", "Hello"]), 1)

        self.find_ffplay.assert_not_called()
        self.assert_no_sensitive_io()

    def test_invalid_voice_error_does_not_echo_value(self):
        os.environ["ELEVENLABS_VOICE_ID"] = "invalid/private/voice"

        self.assertEqual(airpods_say.main(["Hello"]), 1)

        self.assertNotIn("invalid/private/voice", self.stderr.getvalue())
        self.find_ffplay.assert_not_called()
        self.assert_no_sensitive_io()

    def test_missing_or_blank_keychain_selector_fails_before_lookup(self):
        for name in (
            "ELEVENLABS_KEYCHAIN_SERVICE", "ELEVENLABS_KEYCHAIN_ACCOUNT"
        ):
            for value in (None, "", "  "):
                with self.subTest(name=name, value=value):
                    self.configure()
                    if value is None:
                        del os.environ[name]
                    else:
                        os.environ[name] = value
                    self.stderr.seek(0)
                    self.stderr.truncate()

                    self.assertEqual(airpods_say.main(["Hello"]), 1)
                    self.assertIn(name, self.stderr.getvalue())
                    self.assertNotIn("test-tts-", self.stderr.getvalue())

        self.assert_no_sensitive_io()

    def test_inactive_output_blocks_lookup_even_without_selectors(self):
        os.environ["ELEVENLABS_VOICE_ID"] = "test-voice"
        self.device.return_value = "Built-in Speakers"

        self.assertEqual(airpods_say.main(["Hello"]), 2)

        self.assert_no_sensitive_io()

    def test_keychain_failure_keeps_exit_4(self):
        self.configure()
        self.keychain.side_effect = airpods_say.KeychainError("Lookup failed")

        self.assertEqual(airpods_say.main(["Hello"]), 4)

        self.tts.assert_not_called()
        self.player.assert_not_called()

    def test_dry_run_reports_policy_without_sensitive_io(self):
        os.environ["ELEVENLABS_VOICE_ID"] = "test-voice"

        self.assertEqual(airpods_say.main(["--dry-run", "Hello"]), 0)

        self.assertIn("model='eleven_v4'", self.stderr.getvalue())
        self.assertIn("stability=legacy (unset)", self.stderr.getvalue())
        self.assert_no_sensitive_io()

    def test_dry_run_requires_voice_configuration(self):
        self.assertEqual(airpods_say.main(["--dry-run", "Hello"]), 1)

        self.find_ffplay.assert_not_called()
        self.assert_no_sensitive_io()

    def test_help_needs_no_configuration(self):
        with patch("sys.stdout", io.StringIO()) as stdout:
            with self.assertRaises(SystemExit) as raised:
                airpods_say.main(["--help"])

        self.assertEqual(raised.exception.code, 0)
        self.assertIn("ELEVENLABS_VOICE_ID", stdout.getvalue())
        self.find_ffplay.assert_not_called()
        self.assert_no_sensitive_io()

    def test_help_does_not_echo_configured_voice(self):
        os.environ["ELEVENLABS_VOICE_ID"] = "private-voice-example"
        help_text = airpods_say.build_parser().format_help()

        self.assertNotIn("private-voice-example", help_text)


class TestSymlinkInstallation(unittest.TestCase):
    def test_copied_consumers_with_sibling_policy_keep_entry_points(self):
        import shutil
        voice_dir = Path(airpods_say.__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = root / 'soco'; stub.mkdir()
            (stub / '__init__.py').write_text('class SoCo: pass\n')
            (stub / 'snapshot.py').write_text('class Snapshot: pass\n')
            installed = root / 'installed'
            for skill in ('airpods-say', 'sonos-say', 'elevenlabs-voice-policy'):
                shutil.copytree(voice_dir / skill, installed / skill,
                                ignore=shutil.ignore_patterns('__pycache__'))
            for skill, script in (('airpods-say', 'airpods_say.py'), ('sonos-say', 'sonos_say.py')):
                with self.subTest(skill=skill):
                    result = subprocess.run([sys.executable,
                        str(installed / skill / 'scripts' / script), '--help'],
                        cwd=root, env={'PATH':os.defpath, 'PYTHONPATH':str(root)},
                        capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('--voice-id', result.stdout)

    def test_both_consumers_find_checkout_policy_from_a_lone_skill_symlink(self):
        voice_dir = Path(airpods_say.__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Optional SoCo must not be required for this import-path test.
            stub = root / "soco"
            stub.mkdir()
            (stub / "__init__.py").write_text("class SoCo: pass\n")
            (stub / "snapshot.py").write_text("class Snapshot: pass\n")
            for skill, script in (("airpods-say", "airpods_say.py"),
                                  ("sonos-say", "sonos_say.py")):
                with self.subTest(skill=skill):
                    installed = root / "installed" / skill
                    installed.parent.mkdir(exist_ok=True)
                    installed.symlink_to(voice_dir / skill, target_is_directory=True)
                    result = subprocess.run(
                        [sys.executable, str(installed / "scripts" / script), "--help"],
                        cwd=root, env={"PATH": os.defpath, "PYTHONPATH": str(root)},
                        capture_output=True, text=True, timeout=5,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("--voice-id", result.stdout)


class TestPostHeaderOutputGate(unittest.TestCase):
    def test_switch_during_withheld_headers_blocks_playback(self):
        headers_waiting, release = threading.Event(), threading.Event()
        state = ['Test AirPods']

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                headers_waiting.set()
                release.wait(2)
                self.send_response(200)
                self.send_header('Content-Type', 'audio/mpeg')
                self.send_header('Content-Length', '0')
                self.end_headers()

        def switch():
            if headers_waiting.wait(2):
                state[0] = 'Built-in Speakers'
            release.set()

        thread = threading.Thread(target=switch)
        thread.start()
        try:
            with loopback_server(Handler) as port, \
                    patch.dict(os.environ, {'ELEVENLABS_VOICE_ID': 'dummy-voice',
                        'ELEVENLABS_KEYCHAIN_SERVICE': 'dummy-service',
                        'ELEVENLABS_KEYCHAIN_ACCOUNT': 'dummy-account'}, clear=True), \
                    patch.object(airpods_say, 'ELEVENLABS_STREAM_URL',
                                 f'http://127.0.0.1:{port}/{{voice_id}}'), \
                    patch.object(airpods_say, 'get_keychain_password', return_value='dummy-key'), \
                    patch.object(airpods_say, 'find_ffplay', return_value='/fake/player'), \
                    patch.object(airpods_say, 'get_default_output_device_name',
                                 side_effect=lambda: state[0]), \
                    patch.object(airpods_say, 'play_stream') as player, \
                    patch('sys.stderr', io.StringIO()):
                self.assertEqual(airpods_say.main(['Hello']), 2)
                player.assert_not_called()
        finally:
            release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
