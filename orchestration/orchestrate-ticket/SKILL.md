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

- Orchestrator: the current session's selected model and reasoning. Owns
  decisions, approval records, handoffs, and pipeline gates. No ticket-code
  edits. Do not ask Kyle to switch models. A model change does not need
  approval or a restart.
- Planner (`planner`) and developer (`developer`): Pi agents. Read model,
  thinking, startup checks, and any tool exclusions from
  [default-models.json](references/default-models.json). Do not copy those
  values into prose or substitute a missing profile.
  - Planner uses [planning](../../planning/SKILL.md), not `astra-review`.
    It writes only the plan markdown. No target edits or child agents.
  - Developer uses [closed-loop](../../closed-loop/SKILL.md) and
    [developer](../../developer/SKILL.md). Sole implementation writer before
    the pipeline. No child agents or independent delivery.
- Verifier/delivery: [gate](../../gate/SKILL.md). The orchestrator drives it.
  Remediations reuse the waiting ticket developer. Do not launch gate's
  developer profile.

## Workflow

1. **Intake:** resolve the original request, absolute repository, and acceptance
   context. Ask about ambiguity. Check Herdr, checkout, and gate prerequisites
   read-only. If `git status --short` is non-empty, follow the runbook and stop
   before planning. Do not initialize or reconfigure a repo.
2. **Plan:** delegate once using the runbook's planner contract and planning's
   [closed-loop requirements](../../planning/references/closed-loop-plan.md).
   Require `commit-and-handoff` with the orchestrator as external delivery
   owner. Inspect the saved plan. Idle is not success.
3. **Human gate:** present the Markdown filename in inline code, revision,
   choices, and blockers. Stop for approval under planning's
   [Approval & Execution](../../planning/SKILL.md#approval--execution) contract.
   Unqualified approval dispatches the developer without another “proceed”.
   Preserve holds and handoff instructions. Do not approve on Kyle's behalf.
4. **Implement:** after authorization, follow the runbook for durable state and
   checkout. The orchestrator creates or checks out the feature branch before
   delegation. Delegate the exact approved plan and permission evidence. The
   developer writes planned tests first, implements, verifies, and commits on
   that branch. No push or PR.
5. **Handoff:** inspect the developer report, committed HEAD, acceptance
   evidence, and branch identity. Missing evidence or relevant drift blocks
   submission. Confirm the developer has stopped writing.
6. **Verify and report:** follow the runbook's Gate section, including its
   report rule. Do not edit product code. Never merge.

## Stop and Resume

Follow the runbook's Durable State and Recovery. Do not duplicate work, reset
budgets, or clean up automatically. Uncertain custody, missing approval,
material scope changes, exhausted limits, and missing required evidence mean
stop and ask.

## Authoring

[Evaluation cases](references/evaluation-cases.md) are an authoring fixture.
Do not load that file during a run.
