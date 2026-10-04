import os
import http.server
import io
import json
import subprocess
import threading
import urllib.error
from unittest.mock import MagicMock, patch
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import elevenlabs_voice


class TestResolveStability(unittest.TestCase):
    def test_v4_is_default_without_stability(self):
        self.assertEqual(elevenlabs_voice.DEFAULT_MODEL_ID, "eleven_v4")
        self.assertIsNone(
            elevenlabs_voice.resolve_stability(
                elevenlabs_voice.DEFAULT_MODEL_ID,
                None,
            )
        )

    def test_v3_defaults_to_natural(self):
        preset = elevenlabs_voice.resolve_stability("eleven_v3", None)

        self.assertEqual(preset.name, "natural")
        self.assertEqual(preset.value, 0.5)

    def test_all_named_presets_resolve(self):
        expected = {
            "creative": 0.0,
            "natural": 0.5,
            "robust": 1.0,
        }

        for name, value in expected.items():
            with self.subTest(name=name):
                preset = elevenlabs_voice.resolve_stability(
                    "eleven_v3",
                    name,
                )
                self.assertEqual(preset.name, name)
                self.assertEqual(preset.value, value)

    def test_non_v3_without_stability_preserves_legacy(self):
        self.assertIsNone(
            elevenlabs_voice.resolve_stability(
                "eleven_flash_v2_5",
                None,
            )
        )

    def test_non_v3_models_reject_explicit_stability(self):
        message = "only supported with eleven_v3"
        for model_id in ("eleven_v4", "eleven_flash_v2_5"):
            with self.subTest(model_id=model_id):
                with self.assertRaisesRegex(ValueError, message):
                    elevenlabs_voice.resolve_stability(
                        model_id,
                        "natural",
                    )

    def test_unknown_preset_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown stability preset"):
            elevenlabs_voice.resolve_stability("eleven_v3", "dramatic")


class TestValidateModelId(unittest.TestCase):
    def test_accepts_dots_hyphens_and_underscores(self):
        model_id = "eleven.v3-preview_1"

        self.assertEqual(
            elevenlabs_voice.validate_model_id(model_id),
            model_id,
        )

    def test_strict_fullmatch_and_type_boundaries(self):
        for value in ('eleven_v3\n', 'a\r\n', 'a\nb', ' a', 'a ', None, 42, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                elevenlabs_voice.validate_model_id(value)
        self.assertEqual(elevenlabs_voice.validate_model_id('m' * 64), 'm' * 64)

    def test_rejects_path_syntax(self):
        with self.assertRaisesRegex(ValueError, "Invalid model ID"):
            elevenlabs_voice.validate_model_id("../eleven_v3")

    def test_rejects_empty_and_overlong_ids(self):
        for model_id in ("", "a" * 65):
            with self.subTest(model_id=model_id):
                with self.assertRaises(ValueError):
                    elevenlabs_voice.validate_model_id(model_id)


class TestSharedRequestRules(unittest.TestCase):
    def test_voice_ids_are_strict_and_not_normalized(self):
        for value in ('v\n', 'v\r\n', 'v\nv', ' v', 'v ', '', 'v'*65, None, 1, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                elevenlabs_voice.validate_voice_id(value)
        self.assertEqual(elevenlabs_voice.validate_voice_id('v'*64), 'v'*64)

    def test_payload_preserves_text_and_v3_settings(self):
        self.assertEqual(elevenlabs_voice.tts_payload('[whispers] Bonjour', 'eleven_v3',
            elevenlabs_voice.resolve_stability('eleven_v3', 'robust')), {
                'text': '[whispers] Bonjour', 'model_id': 'eleven_v3',
                'voice_settings': {'stability': 1.0}})
        self.assertEqual(elevenlabs_voice.tts_payload('Hello', 'eleven_v4', None),
                         {'text': 'Hello', 'model_id': 'eleven_v4'})

    def test_keychain_boundary_is_bounded_absolute_and_sanitized(self):
        with patch('elevenlabs_voice.subprocess.run', return_value=MagicMock(
                returncode=0, stdout='dummy-key\n')) as run:
            self.assertEqual(elevenlabs_voice.keychain_password('dummy-service', 'dummy-account'), 'dummy-key')
            self.assertEqual(run.call_args.args[0][0], '/usr/bin/security')
            self.assertEqual(run.call_args.kwargs['timeout'], 10)
        for error in (TimeoutError('private'), subprocess.TimeoutExpired('private', 10),
                      FileNotFoundError('private'), MagicMock(returncode=44, stdout='private')):
            with patch('elevenlabs_voice.subprocess.run',
                       side_effect=error if isinstance(error, BaseException) else None,
                       return_value=error):
                with self.assertRaises(elevenlabs_voice.KeychainLookupError) as raised:
                    elevenlabs_voice.keychain_password('dummy-service', 'dummy-account')
                self.assertNotIn('private', str(raised.exception))

    def test_http_status_mime_and_errors_close_and_sanitize(self):
        for status, mime in ((201, 'audio/mpeg'), (200, None), (200, 'application/json')):
            resp = MagicMock(status=status)
            resp.headers.get.return_value = mime
            with patch('elevenlabs_voice.tts_opener') as opener:
                opener.return_value.open.return_value = resp
                with self.assertRaises(elevenlabs_voice.TTSRequestError):
                    elevenlabs_voice.open_tts('http://example.invalid/voice', 'dummy-key',
                        'Hello', 'eleven_v4', 'mp3_44100_128', 1)
            resp.close.assert_called_once()

    def test_real_redirect_and_success_boundary(self):
        settings = {'status': 302}
        received = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                received.append((self.path, json.loads(body)))
                self.send_response(settings['status'])
                self.send_header('Location', '/destination')
                self.send_header('Content-Type', 'Audio/MPEG; charset=binary')
                self.send_header('Content-Length', '4')
                self.end_headers()
                self.wfile.write(b'audio'[:4])
            def do_GET(self):
                received.append(('destination', {}))
                self.send_response(200); self.end_headers()
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval':0.01})
        thread.start()
        try:
            endpoint = f'http://127.0.0.1:{server.server_port}/voice'
            with self.assertRaises(elevenlabs_voice.TTSRequestError):
                elevenlabs_voice.open_tts(endpoint, 'dummy-key', 'Hello', 'eleven_v4', 'mp3_44100_128', 1)
            self.assertEqual(len(received), 1)
            settings['status'] = 200
            with elevenlabs_voice.open_tts(endpoint, 'dummy-key', 'Hello', 'eleven_v4', 'mp3_44100_128', 1) as response:
                self.assertEqual(response.read(), b'audi')
            self.assertIn('output_format=mp3_44100_128', received[0][0])
        finally:
            server.shutdown(); server.server_close(); thread.join(2)
            self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
