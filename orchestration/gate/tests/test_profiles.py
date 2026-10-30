"""Validate Gate's pi-subagents profiles without launching model children."""

import json
from pathlib import Path
import unittest


GATE = Path(__file__).resolve().parents[1]


class GateProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = json.loads(
            (GATE / "references/default-models.json").read_text(encoding="utf-8"))

    def agent_fields(self, role):
        agent = self.profiles[role]["agent"]
        text = (GATE / "agents" / f"{agent}.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        frontmatter = text.split("---", 2)[1]
        # Inspect only the plain scalar fields used by these bundled agents;
        # nested YAML (such as acceptance) belongs to the extension's parser.
        fields = dict(line.split(": ", 1) for line in frontmatter.splitlines()
                      if ": " in line and not line.startswith(" "))
        self.assertEqual(fields["name"], agent)
        return fields

    def test_profiles_pin_pi_subagents_roles_models_and_thinking(self):
        expected = {
            "reviewer": ("gate-reader", "openai-codex/gpt-6.1-sol", "xhigh"),
            "qa": ("gate-reader", "openai-codex/gpt-6.1-sol", "xhigh"),
            "auditor": ("gate-reader", "openai-codex/gpt-6.1-sol", "high"),
            "developer": ("gate-developer", "openai-codex/gpt-6.1-sol", "high"),
            "astra_reviewer": ("gate-reader", "openai-codex/gpt-6-astra", "high"),
            "fable_reviewer": ("claude-code", "fable", "high"),
        }
        self.assertEqual(set(self.profiles), set(expected))
        for role, (agent, model, thinking) in expected.items():
            with self.subTest(role=role):
                self.assertEqual(self.profiles[role], {
                    "agent": agent, "model": model, "thinking": thinking,
                })

    def test_native_roles_resolve_to_fresh_safety_inheriting_agents(self):
        for role in self.profiles:
            if role == "fable_reviewer":
                continue  # The extension owns the external Claude Code adapter.
            with self.subTest(role=role):
                fields = self.agent_fields(role)
                self.assertEqual(fields["defaultContext"], "fresh")
                self.assertEqual(fields["inheritProjectContext"], "true")
                self.assertEqual(fields["inheritGlobalContext"], "true")
                self.assertNotIn("runner", fields)
                self.assertNotIn("machine", fields)
                self.assertNotEqual(fields.get("allowNestedSubagents"), "true")

    def test_review_qa_and_audit_effective_tools_are_read_bash_only(self):
        for role in ("reviewer", "qa", "auditor", "astra_reviewer"):
            with self.subTest(role=role):
                fields = self.agent_fields(role)
                tools = {tool.strip() for tool in fields["tools"].split(",")}
                excluded = {tool.strip() for tool in
                            fields.get("excludeTools", "").split(",")}
                self.assertEqual(tools, {"read", "bash"})
                self.assertEqual(tools - excluded, {"read", "bash"})
                self.assertEqual(fields["acceptanceRole"], "read-only")

    def test_only_developer_profile_has_product_edit_tools(self):
        fields = self.agent_fields("developer")
        tools = {tool.strip() for tool in fields["tools"].split(",")}
        self.assertEqual(tools, {"read", "bash", "edit", "write"})
        self.assertNotIn("excludeTools", fields)


if __name__ == "__main__":
    unittest.main()
