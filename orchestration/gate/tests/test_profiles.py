"""Validate Gate's BB profiles without launching model workers."""

import json
from pathlib import Path
import unittest


GATE = Path(__file__).resolve().parents[1]


class GateProfileTests(unittest.TestCase):
    def test_profiles_pin_bb_providers_models_and_reasoning(self):
        profiles = json.loads(
            (GATE / "references/default-models.json").read_text(encoding="utf-8"))
        expected = {
            "reviewer": ("codex", "gpt-6.1-sol", "xhigh"),
            "qa": ("codex", "gpt-6.1-sol", "xhigh"),
            "auditor": ("codex", "gpt-6.1-sol", "high"),
            "developer": ("codex", "gpt-6.1-sol", "high"),
            "astra_reviewer": ("codex", "gpt-6-astra", "high"),
            "fable_reviewer": ("claude-code", "claude-fable-5-1", "high"),
        }
        self.assertEqual(set(profiles), set(expected))
        for role, (provider, model, reasoning) in expected.items():
            with self.subTest(role=role):
                self.assertEqual(profiles[role], {
                    "provider": provider, "model": model,
                    "reasoning": reasoning,
                })

    def test_active_setup_uses_bb_not_legacy_pi_discovery(self):
        skill = (GATE / "SKILL.md").read_text(encoding="utf-8")
        setup = (GATE / "references/bb-threads.md").read_text(encoding="utf-8")
        self.assertIn("references/bb-threads.md", skill)
        self.assertNotIn("references/pi-subagents.md", skill)
        for text in (skill, setup):
            self.assertNotIn("subagents_enable", text)
            self.assertNotIn("agentScanDirs", text)
            self.assertNotIn("subagent({", text)
        self.assertIn("Legacy", (GATE / "references/pi-subagents.md")
                      .read_text(encoding="utf-8"))

    def test_named_reviewers_keep_auth_and_review_contract(self):
        setup = (GATE / "references/bb-threads.md").read_text(encoding="utf-8")
        for requirement in (
            "claude auth status", "subscription", "Critical/High/Medium/Low",
            "inspect the checkout", "not the Herdr-backed",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, setup)
        self.assertNotIn("has no checkout tools", setup)


if __name__ == "__main__":
    unittest.main()
