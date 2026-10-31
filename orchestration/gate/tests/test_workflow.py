"""Static instruction regressions, not proof of live BB orchestration."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[3]


class GateWorkflowTests(unittest.TestCase):
    def text(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def setUp(self):
        self.skill = self.text("orchestration/gate/SKILL.md")

    def setup_text(self):
        # Prose contracts must survive Markdown wrapping/emphasis changes.
        text = self.text("orchestration/gate/references/bb-threads.md")
        return " ".join(text.replace("**", "").split())

    def test_phases_and_qa_charter_contract_are_preserved(self):
        self.assertEqual(re.findall(r"^## Phase (\d+) — (.+)$", self.skill, re.M), [
            ("1", "Review"), ("2", "QA — plan and execute"),
            ("3", "Audit"), ("4", "Report"), ("5", "Publish"),
        ])
        qa = " ".join(self.skill.split("## Phase 2 —", 1)[1]
                      .split("## Phase 3 —", 1)[0].split())
        for requirement in (
            "before executing", "read back", "standard commands",
            "stable check/case IDs", "Revision", "blocking gaps",
            "without narrowing coverage", "no planning roundtrip",
            "same saved charter", "pre-run product identity",
            "fresh QA thread", "full re-run",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, qa)
        self.assertNotIn("e2e_planner", self.skill)
        self.assertIn("including all-green and blocked records", self.skill)

    def test_spawn_is_fresh_visible_and_explicit(self):
        setup = self.setup_text()
        call = re.search(r"bb thread spawn .*?--json", setup, re.S).group()
        for flag in ("--project", "--environment", "--parent-self",
                     "--visibility visible", "--parent-notifications auto",
                     "--provider", "--model", "--reasoning-level",
                     "--prompt-file"):
            with self.subTest(flag=flag):
                self.assertIn(flag, call)
        for forbidden in ("--new-environment", "--source-thread",
                          "--origin-kind", "--permission-mode",
                          "--lifecycle-owner-thread"):
            self.assertNotIn(forbidden, call)
        for requirement in ("never fork", "launch intent", "missing receipt",
                            "no fallback", "inherit permissions"):
            self.assertIn(requirement, setup)

    def test_completion_and_recovery_do_not_duplicate_workers(self):
        setup = self.setup_text()
        for requirement in (
            "bb thread wait", "bb thread show", "bb thread output",
            "bb thread log", "--mode steer", "queued", "do not resend",
            "no sleep", "stale", "not phase completion",
            "never automatically stop", "user-stopped", "reconcile",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, setup)

    def test_capture_precedes_guarded_archive(self):
        setup = self.setup_text()
        self.assertLess(setup.index("## Capture"), setup.index("## Archive"))
        for requirement in (
            "verbatim", "unique", "turn", "capture fails", "settled",
            "pending", "descendants", "unarchived coordinator",
            "bb thread archive", "archivedAt", "environment",
            "never delete", "cleanup failure", "reconciliation",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, setup)

    def test_archive_checks_complete_non_parent_cascade(self):
        archive = self.setup_text().split("## Archive", 1)[1].split(
            "## Repairs", 1)[0]
        for requirement in (
            "bb thread list --include-hidden --json",
            "parentThreadId", "sourceThreadId", "lifecycleOwnerThreadId",
            "recursively", "archived", "cross-project",
            "visible-only", "unarchivedDescendantCount",
            "incomplete inventory", "retain the worker",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, archive)
        self.assertNotIn("any known lifecycle dependents", archive)
        self.assertNotIn("--parent-thread <worker-id>", archive)

    def test_paper_cases_cover_non_parent_hidden_forks(self):
        cases = " ".join(self.text(
            "orchestration/gate/references/evaluation-cases.md").split())
        for requirement in (
            "non-parent hidden fork", "lifecycle-owned", "archived intermediary",
            "incomplete inventory", "retain the worker",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, cases)

    def test_fresh_repairs_keep_custody_and_budgets(self):
        setup = self.setup_text()
        for requirement in (
            "fresh developer", "fresh QA", "compact handoff",
            "budgets", "one writer", "legacy Pi", "ticket-owned developer",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, setup)
        self.assertNotIn("resume the latest QA ID", self.skill)
        self.assertNotIn("resume its latest returned ID", self.skill)
        self.assertIn("max **3**", self.skill)
        self.assertIn("max **2 consecutive**", self.skill)

    def test_role_limits_are_honest_and_scoped(self):
        briefs = self.text("orchestration/gate/references/worker-briefs.md")
        for requirement in (
            "prompt-enforced", "not tool allowlists", "no delegation",
            "Review/audit", "do not write files", "QA only",
            "assigned plan file", "assigned evidence directory",
            "charter-approved", "product identity", "Destructive",
            "Relinquish writing", "fixed", "disputed",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, briefs)
        self.assertIn("no per-thread tool allowlist", self.setup_text())

    def test_safety_routing_and_publication_are_preserved(self):
        for requirement in (
            "clean, committed", "Unexpected product-tree drift",
            "**7–10**", "**Critical/High**", "One dispute",
            "Route only by auditor cause", "**full charter**",
            "No-record planning blockers", "always phase 3",
            "No usable push remote", "Never merge",
            "never push the default branch", "never force-push",
            "Required checks only", "no placeholders remain",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, self.skill)

    def test_publication_pins_source_and_destination_refs(self):
        publish = self.skill.split("## Phase 5 — Publish", 1)[1]
        publish = " ".join(publish.replace("\\\n", "").split())
        command = re.search(r"git push <push-remote> ([^\s`]+)", publish)
        self.assertIsNotNone(
            command, "Publish must provide an explicit push command")
        self.assertEqual(
            command.group(1),
            "refs/heads/<feature-branch>:refs/heads/<feature-branch>",
        )

    def test_direct_consumers_use_bb_but_keep_ticket_transport(self):
        ticket = self.text("orchestration/orchestrate-ticket/SKILL.md")
        readme = self.text("README.md")
        cases = self.text(
            "orchestration/orchestrate-ticket/references/evaluation-cases.md")
        for text in (ticket, readme, cases):
            self.assertNotIn("Gate's own children use pi-subagents", text)
            self.assertNotIn("undiscovered Gate agents", text)
        self.assertIn("../gate/references/bb-threads.md", ticket)
        self.assertIn("Herdr's existing-agent procedure", ticket)
        self.assertIn("review → QA (plan and execute) → audit", ticket)
        self.assertIn("BB child threads", readme)
        self.assertIn("maintenance", self.setup_text())


if __name__ == "__main__":
    unittest.main()
