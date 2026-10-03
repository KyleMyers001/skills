# Evaluation cases

Authoring fixture for orchestrate-ticket. Do not load this file during a run.
It is not part of the execution path.

Use these as supervised scenarios, not source-string assertions. Record observed
actions separately from paper walkthroughs; no live trial is implied by lint.

## Scenarios

- Explicit orchestration selects this flow; bare Jira, planning, skill design,
  and single-agent closed-loop requests do not launch agents.
- Any selected orchestrator model/reasoning, including a mid-run change,
  continues without a model-switch prompt, fresh approval, or restart. Planner
  and developer profiles remain pinned and verified.
- No approval means no target tests/code. Unqualified “approved” dispatches the
  designated developer without a second permission prompt; the planner remains
  planner-only. “Approve and proceed” remains valid authorization too.
- “Approved, but hold,” review-only approval, or “I'll hand this off” prevents
  automatic dispatch. Receiving agents preserve restrictions, including those
  in historical approvals. An approved artifact alone is not assignment.
- An assigned developer with valid inherited approval starts without reapproval.
  Approval/ownership log updates preserve the plan's revision and digest.
  Material revisions or unresolved conditional approval return to Kyle; missing
  approval evidence or conflicting ownership blocks only that unresolved gate.
- Missing worker models/prerequisites block, not substitute.
- Fresh planner/developer launch and initial submission follow Herdr's Fresh
  Launch procedure with the selected worker profile, not a ticket-owned command
  recipe. A Herdr script rename alone requires no ticket instruction edits.
- Planner and developer receive separate fresh workspaces. Gate receives the
  developer's recorded workspace and reuses that developer for remediation;
  missing workspace evidence blocks handoff, not permission for a replacement.
- Planner revisions and developer repairs follow Herdr's existing-agent
  procedure after reconciling state; no fresh launch or prompt to a busy worker.
- Blocked startup follows Herdr recovery without a second submission. Successful
  submission does not trigger another startup/model inspection.
- A delivered prompt whose wait times out continues waiting on the returned
  full agent name within the ticket wait budgets. It never causes resubmission
  or relaunch; accessible output uses Herdr's retrieval procedure.
- Developer writes all planned tests first and hands off a verified commit
  without publication. Standalone closed-loop still pushes a feature-branch PR.
- Ask-user/unsafe-operation gates reach Kyle; only gate's developer path writes
  product code while gate owns custody. Unapproved destructive behavior blocks
  launch.
- Timeout, interrupted submission, stale HEAD, and missing logs do not duplicate
  work, invent approval, or reset counters. Exhausted budgets stop further work.
- Skips, empty CI, and stale final-head evidence never become an unqualified pass.
- Dirty intake stops before planning. A requested worktree needs Kyle's task
  id/branch and a clean checkout; never auto-stash or commit unrelated work.
- Approval freezes the exact plan revision/content. A changed digest is
  investigated, not replaced; material changes require renewed approval.
- Wrong checkout/base or another task's branch blocks checkout preparation.
  Do not create a second branch or move the developer to a replacement checkout.
- Before gate, stale HEAD, missing acceptance evidence, competing writers, or
  missing developer workspace evidence blocks submission, not verification duty.
- Interrupted gate attempts remain charged until reconciled; reattachment is
  not a new invocation. Two fresh attempts exhaust the ticket's gate budget.
- A pre-gate stop uses chat and, after authorization, both run logs; it does not
  produce another Kyle-facing report. Gate's report remains the delivery report.
- Local-only delivery ends verified but unpublished. Required CI pending or
  failed, missing checks, and UNVERIFIED evidence never become readiness claims.
- With a chosen pilot and resolved safety prerequisites, run one authorized
  non-production ticket to a review-ready PR without merging it.

## Consolidation constraint map

Use this before/after checklist when reviewing the single-file workflow.
Former section names identify source responsibilities, not runtime dependencies.
Each entry must remain covered by the destination and its explicitly named owner.

- **Roles / trigger** → [Purpose and roles](../SKILL.md#purpose-and-roles):
  opt-in, current coordinator model, planner/developer scope, no child agents.
- **Preflight** → [Intake](../SKILL.md#1-intake): absolute repo, untrusted Jira,
  clean checkout, model validation, writable planner, gate prerequisites,
  branch-specific competing work, no automatic setup/cleanup.
- **Launch and Wait** → [Workers](../SKILL.md#working-with-agents) and
  [Herdr](../../herdr/SKILL.md): profile forwarding, separate fresh workspaces,
  exact identities, startup/retrieval/recovery, ticket wait limits, no duplicates.
- **Planner** → [Plan](../SKILL.md#2-plan-and-approval) and
  [planning](../../../planning/SKILL.md): investigate, save/lint, repair defaults,
  checkout/base, final-HEAD checks, permissions, external delivery owner.
- **Approval and Checkout** → [Plan](../SKILL.md#2-plan-and-approval) and
  [Implement](../SKILL.md#3-implement-and-handoff): exact authorization,
  frozen digest, holds/assignment, safe branch preparation, baseline drift.
- **Developer** → [Implement](../SKILL.md#3-implement-and-handoff) and
  [closed-loop](../../../closed-loop/SKILL.md): tests first, bounded repairs,
  verified commit including hook changes, handoff fields, no publication,
  relinquished writing, scope/HEAD/evidence inspection.
- **Gate verify/deliver** → [Gate](../SKILL.md#4-verify-and-deliver) and
  [gate](../../../gate/SKILL.md): two fresh attempts, adopted developer workspace,
  retained scope, existing developer, findings/ask-user handling, routing/audit,
  evidence rerun on final HEAD, publication status, one report.
- **Durable State and Recovery** → [Resume](../SKILL.md#state-stop-and-resume):
  log timing/ownership, phase/blocker, approval/digest, checkout and worker IDs,
  evidence/outcomes, all budgets, live-state reconciliation, no counter resets,
  invented approval, automatic cleanup, or duplicate runs.
