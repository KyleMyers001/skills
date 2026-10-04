"""Offline request-boundary regressions; every credential is a dummy."""

import http.client
import http.server
import io
import json
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import MagicMock, patch

import airpods_say
from airpods_test_support import loopback_server, request

class TestVoicePayload(unittest.TestCase):
    def setUp(self):
        # Guard the old global boundary too: red tests must never contact TTS.
        self.real_urlopen = urllib.request.urlopen
        guard = patch("urllib.request.urlopen", return_value=MagicMock())
        guard.start()
        self.addCleanup(guard.stop)

    def _payload(self, model, stability, text="Hello"):
        with patch("elevenlabs_voice.tts_opener", create=True) as opener:
            opener.return_value.open.return_value.status = 200
            opener.return_value.open.return_value.headers.get.return_value = "audio/mpeg"
            request(model_id=model, stability=stability, text=text)
        req = opener.return_value.open.call_args.args[0]
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(
            req.full_url).query), {"output_format": ["mp3_44100_128"]})
        self.assertEqual(req.get_header("Xi-api-key"), "dummy-key")
        return json.loads(req.data)

    def test_v3_payload_preserves_tags_and_only_adds_stability(self):
        text = "[whispers] Bonjour. [laughs]"
        preset = airpods_say.resolve_stability("eleven_v3", "creative")
        self.assertEqual(self._payload("eleven_v3", preset, text), {
            "model_id": "eleven_v3", "text": text,
            "voice_settings": {"stability": 0.0},
        })

    def test_non_v3_payload_remains_legacy(self):
        self.assertEqual(self._payload("eleven_flash_v2_5", None), {
            "model_id": "eleven_flash_v2_5", "text": "Hello",
        })

    def test_default_v4_payload_omits_voice_settings(self):
        model = airpods_say.DEFAULT_MODEL_ID
        self.assertEqual(model, "eleven_v4")
        self.assertEqual(self._payload(model, airpods_say.resolve_stability(
            model, None)), {"model_id": "eleven_v4", "text": "Hello"})

    def test_query_is_url_encoded(self):
        with patch("elevenlabs_voice.tts_opener", create=True) as opener:
            opener.return_value.open.return_value.status = 200
            opener.return_value.open.return_value.headers.get.return_value = "audio/mpeg"
            request(output_format="mp3&other=value")
        req = opener.return_value.open.call_args.args[0]
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(
            req.full_url).query), {"output_format": ["mp3&other=value"]})
        self.assertNotIn("output_format", json.loads(req.data))

    def test_request_failures_are_sanitized_and_http_errors_closed(self):
        body = io.BytesIO(b"private-body dummy-key")
        errors = (
            urllib.error.HTTPError("https://private-location", 400,
                                   "private-reason", {}, body),
            urllib.error.URLError("private-reason dummy-key"),
            TimeoutError("private-reason dummy-key"),
            http.client.RemoteDisconnected("private-reason"),
            http.client.BadStatusLine("private-reason"),
            OSError("private-reason"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                with patch("elevenlabs_voice.tts_opener", create=True) as opener:
                    opener.return_value.open.side_effect = error
                    with self.assertRaises(airpods_say.ElevenLabsError) as raised:
                        request()
                message = str(raised.exception)
                for value in ("private-body", "dummy-key", "private-reason",
                              "private-location"):
                    self.assertNotIn(value, message)
        self.assertTrue(body.closed)

    def test_header_stall_finishes_with_normalized_failure(self):
        release = threading.Event()
        finished = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                release.wait(3)
                finished.set()

        try:
            with loopback_server(Handler) as port:
                with patch("airpods_say.ELEVENLABS_STREAM_URL",
                           f"http://127.0.0.1:{port}/{{voice_id}}"), patch(
                               "urllib.request.urlopen", self.real_urlopen):
                    started = time.monotonic()
                    with self.assertRaises(airpods_say.ElevenLabsError):
                        request(timeout=0.1)
                    self.assertLess(time.monotonic() - started, 1)
        finally:
            release.set()
            self.assertTrue(finished.wait(2))


class TestRedirectRejection(unittest.TestCase):
    def test_redirects_never_reach_destination(self):
        destinations, keys, settings = [], [], {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                destinations.append(self.path)
                self.send_response(200)
                self.end_headers()

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if self.path.startswith("/target"):
                    return self.do_GET()
                keys.append(self.headers.get("xi-api-key"))
                self.send_response(settings["status"], "private-reason")
                self.send_header("Location", settings["location"])
                self.end_headers()

        with loopback_server(Handler) as origin, loopback_server(Handler) as dest:
            for status in (301, 302, 303, 307, 308):
                for location in (f"http://localhost:{dest}/target",
                                 f"http://127.0.0.1:{origin}/target",
                                 "/target", "../target",
                                 f"//localhost:{dest}/target"):
                    settings.update(status=status, location=location)
                    with self.subTest(status=status, location=location):
                        with patch("airpods_say.ELEVENLABS_STREAM_URL",
                                   f"http://127.0.0.1:{origin}/{{voice_id}}"):
                            with self.assertRaises(airpods_say.ElevenLabsError):
                                request()
                        self.assertEqual(destinations, [])
                        self.assertEqual(keys[-1], "dummy-key")

    def test_https_downgrade_is_rejected_at_handler_boundary(self):
        req = urllib.request.Request("https://example.invalid/tts",
                                     data=b"{}", headers={"xi-api-key": "dummy-key"})
        body = io.BytesIO(b"private-body")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            airpods_say.voice_policy._RejectRedirects().redirect_request(
                req, body, 302, "private-reason", {}, "http://example.invalid/target")
        raised.exception.close()
        self.assertTrue(body.closed)


class TestAudioResponseHeaders(unittest.TestCase):
    def test_missing_and_wrong_mime_close_before_player(self):
        for mime in (None, 'application/json', 'text/html'):
            response = MagicMock(status=200)
            response.headers.get.return_value = mime
            with self.subTest(mime=mime), patch.object(airpods_say.voice_policy, 'tts_opener') as opener:
                opener.return_value.open.return_value = response
                with self.assertRaises(airpods_say.ElevenLabsError):
                    request()
                response.close.assert_called_once()

    def test_case_insensitive_mpeg_with_parameters(self):
        response = MagicMock(status=200)
        response.headers.get.return_value = 'Audio/MPEG; charset=binary'
        with patch.object(airpods_say.voice_policy, 'tts_opener') as opener:
            opener.return_value.open.return_value = response
            self.assertIs(request(), response)
        response.close.assert_not_called()
