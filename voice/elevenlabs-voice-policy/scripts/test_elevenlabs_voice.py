import os
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

    def test_rejects_path_syntax(self):
        with self.assertRaisesRegex(ValueError, "Invalid model ID"):
            elevenlabs_voice.validate_model_id("../eleven_v3")

    def test_rejects_empty_and_overlong_ids(self):
        for model_id in ("", "a" * 65):
            with self.subTest(model_id=model_id):
                with self.assertRaises(ValueError):
                    elevenlabs_voice.validate_model_id(model_id)


if __name__ == "__main__":
    unittest.main()
