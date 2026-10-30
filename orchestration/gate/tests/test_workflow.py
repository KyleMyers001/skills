"""Static Gate instruction regressions, not proof of live orchestration."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[3]
GATE = ROOT / "orchestration/gate"


class GateWorkflowTests(unittest.TestCase):
    def text(self, path):
        return path.read_text(encoding="utf-8")

    def test_active_phases_combine_qa_planning_and_execution(self):
        skill = self.text(GATE / "SKILL.md")
        self.assertEqual(re.findall(r"^## Phase (\d+) — (.+)$", skill, re.M), [
            ("1", "Review"), ("2", "QA — plan and execute"),
            ("3", "Audit"), ("4", "Report"), ("5", "Publish"),
        ])
        self.assertNotIn("e2e_planner", skill)
        setup = self.text(GATE / "references/pi-subagents.md")
        self.assertNotRegex(setup, r"(?m)^- `gate-planner`:")
        self.assertNotIn("Phase 6", skill + setup)
        ticket = self.text(ROOT / "orchestration/orchestrate-ticket/SKILL.md")
        self.assertIn("review → QA (plan and execute) → audit", ticket)
        self.assertNotIn("Phase 6", ticket)

    def test_live_steer_explicitly_disables_recovery(self):
        for path in (GATE / "SKILL.md", GATE / "references/pi-subagents.md"):
            with self.subTest(path=path):
                text = self.text(path)
                self.assertIn("options: { steeringRecovery: false }", text)
                self.assertIn("missed acknowledgment", text)
                # Any executable steer example must carry the non-recovery option.
                for call in re.findall(r"subagent\(\{(.*?)\}\)", text, re.S):
                    if re.search(r'action:\s*"steer"', call):
                        self.assertIn("options: { steeringRecovery: false }", call)

    def test_qa_saves_and_validates_charter_before_execution(self):
        skill = self.text(GATE / "SKILL.md")
        qa = " ".join(skill.split("## Phase 2 —", 1)[1]
                      .split("## Phase 3 —", 1)[0].split())
        for requirement in (
            "Launch `qa` once", "before executing", "read back",
            "standard commands", "stable check/case IDs", "Revision",
            "blocking gaps", "without narrowing coverage", "no planning roundtrip",
            "same saved charter", "pre-run product identity",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, qa)
        self.assertIn("including all-green and blocked records", skill)

    def test_reader_permissions_are_role_scoped_not_blanket_bash_writes(self):
        reader = " ".join(self.text(GATE / "agents/gate-reader.md").split())
        for requirement in (
            "For review/audit, do not write files", "For QA only",
            "assigned plan file", "assigned evidence directory",
            "charter-approved", "not unrestricted edits",
            "destructive", "product identity",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, reader)
        self.assertIn("Inactive compatibility artifact",
                      self.text(GATE / "agents/gate-planner.md"))


if __name__ == "__main__":
    unittest.main()
