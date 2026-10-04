import io
import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import airpods_say


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
            patch("airpods_say.play_stream", return_value=0)
        )

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
