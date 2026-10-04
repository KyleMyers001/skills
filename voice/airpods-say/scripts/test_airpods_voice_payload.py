import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

import airpods_say


class TestVoicePayload(unittest.TestCase):
    def _payload(self, model, stability, text="Hello"):
        response = MagicMock()

        with patch("urllib.request.urlopen", return_value=response) as open_url:
            airpods_say.stream_tts(
                api_key="secret",
                text=text,
                voice_id="test-voice",
                model_id=model,
                output_format=airpods_say.DEFAULT_OUTPUT_FORMAT,
                timeout=30,
                debug=False,
                stability=stability,
            )

        return json.loads(open_url.call_args.args[0].data)

    def test_v3_payload_preserves_tags_and_only_adds_stability(self):
        text = "[whispers] Bonjour. [laughs]"
        stability = airpods_say.resolve_stability(
            "eleven_v3",
            "creative",
        )

        self.assertEqual(
            self._payload("eleven_v3", stability, text),
            {
                "model_id": "eleven_v3",
                "output_format": airpods_say.DEFAULT_OUTPUT_FORMAT,
                "text": text,
                "voice_settings": {"stability": 0.0},
            },
        )

    def test_non_v3_payload_remains_legacy(self):
        self.assertEqual(
            self._payload("eleven_flash_v2_5", None),
            {
                "model_id": "eleven_flash_v2_5",
                "output_format": airpods_say.DEFAULT_OUTPUT_FORMAT,
                "text": "Hello",
            },
        )

    def test_default_v4_payload_omits_voice_settings(self):
        model = airpods_say.DEFAULT_MODEL_ID

        self.assertEqual(model, "eleven_v4")
        self.assertEqual(
            self._payload(
                model,
                airpods_say.resolve_stability(model, None),
            ),
            {
                "model_id": "eleven_v4",
                "output_format": airpods_say.DEFAULT_OUTPUT_FORMAT,
                "text": "Hello",
            },
        )

    def test_http_failure_exposes_status_and_reason_only(self):
        body = io.BytesIO(b"provider body with [request text] and secret")
        error = urllib.error.HTTPError(
            "https://example.invalid",
            400,
            "Bad Request",
            {},
            body,
        )

        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(airpods_say.ElevenLabsError) as raised:
                airpods_say.stream_tts(
                    api_key="secret",
                    text="[request text]",
                    voice_id="test-voice",
                    model_id="eleven_v3",
                    output_format=airpods_say.DEFAULT_OUTPUT_FORMAT,
                    timeout=30,
                    debug=False,
                    stability=airpods_say.resolve_stability(
                        "eleven_v3",
                        None,
                    ),
                )

        self.assertEqual(
            str(raised.exception),
            "ElevenLabs API returned HTTP 400: Bad Request",
        )
