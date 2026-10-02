---
name: orchestrate-ticket
description: >-
  Opt-in ticket pipeline Kyle starts with /skill:orchestrate-ticket. Load only
  for that command or when Kyle explicitly says "orchestrate a ticket". Never
  self-select for Herdr, spinning up agents, parallel work, closed-loop,
  planning, Jira, or a ticket key.
---

# Orchestrate Ticket

Use the current agent session as coordinator, not implementer. Follow global
safety and PR rules. Kyle invokes this with `/skill:orchestrate-ticket` or by
explicitly saying to orchestrate a ticket. If this file was loaded for any other
reason, stop; do not plan, delegate, or run gate.

Before delegation, read [Herdr](../herdr/SKILL.md) and the
[runbook](references/runbook.md) completely. Use only Herdr's `Subagents` session.

## Roles

- Orchestrator: use the current session's selected model and reasoning level;
  owns decisions, approval records, handoffs, pipeline gates, and reporting.
  Do not require a particular profile or prompt Kyle to switch. No ticket-code
  edits.
- Planner: one Pi agent, `openai-codex/gpt-6-astra`, high reasoning; uses
  [planning](../../planning/SKILL.md), not `astra-review`. Investigates and writes
  the plan only; no target edits or child agents.
- Developer: one Pi agent, `openai-codex/gpt-5.6-sol`, high reasoning; uses
  [closed-loop](../../closed-loop/SKILL.md) and
  [developer](../../developer/SKILL.md). Sole implementation writer before the
  pipeline; no child agents or independent delivery.
- Verifier/delivery: [gate](../../gate/SKILL.md). Orchestrator drives it;
  developer relinquishes write ownership.

Resolve planner/developer models and verify their live identity using the
runbook. Missing/mismatched worker profiles require Kyle's decision, never silent
substitution. Changing the orchestrator's model or reasoning does not require
approval or restart.

## Workflow

1. **Intake:** resolve the original request, absolute repository, and acceptance
   context. Ask about ambiguity. Check Herdr, checkout/branch ownership, and
   gate prerequisites read-only (Herdr, models, checkout). Do not initialize or
   reconfigure a repo.
2. **Plan:** delegate once using the runbook's planner contract and planning's
   [closed-loop requirements](../../planning/references/closed-loop-plan.md).
   Require delivery mode `commit-and-handoff` with the orchestrator as external
   delivery owner. Inspect the actual plan and completion evidence; idle is not
   success.
3. **Human gate:** present the Markdown filename in inline code, revision,
   choices, and blockers. Stop for approval under planning's
   [Approval & Execution](../../planning/SKILL.md#approval--execution) contract.
   Unqualified approval authorizes dispatching the developer without another
   “proceed”; preserve explicit holds and handoff instructions. Material revisions
   require fresh approval; do not approve on Kyle's behalf.
4. **Implement:** after authorization, create/resume durable state and establish
   a feature branch/worktree without disturbing unrelated work. Delegate the
   exact approved plan and permission evidence to the developer. It validates
   baseline drift, writes all planned tests first, implements, verifies, and
   commits using closed-loop's bounded repair budget. No push or PR creation.
5. **Handoff:** inspect the developer report, committed HEAD, acceptance
   evidence, and branch identity. Missing evidence or relevant drift blocks
   submission. Confirm the developer has stopped writing.
6. **Verify/deliver:** load [gate](../../gate/SKILL.md) and follow the
   runbook's safety preflight before starting verification. Preserve complete
   user intent. Drive the gate pipeline rather than editing code; escalate
   human-only decisions.
7. **Report:** stop when gate has published/updated the PR and the gate report
   is ready for Kyle's review; never merge. Report PR URL, full final HEAD,
   charter checks (including standard unit/lint when in scope), gate
   remediations, exceptions, evidence, and blockers. Final-head acceptance
   evidence is required; skipped/unrunnable checks are not green.

## Stop and Resume

Persist approval, ownership, identities, evidence, and counters as specified in
the runbook. Reconcile live Herdr agents and the persisted gate run state
(`run-id`, HEAD/digest, ownership, budgets, pending submissions) before
resuming; never duplicate work or reset budgets. Cap fresh gate invocations at
two per approved execution; reattachment is not a new invocation. Escalate
external blockers immediately.

Unapproved destructive operations, uncertain custody/approval, material scope
changes, exhausted limits, and missing required evidence mean stop and ask.
Never automatically clean up agents, panes, processes, branches, or worktrees.
