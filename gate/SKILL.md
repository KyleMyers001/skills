---
name: gate
description: >-
  Run the standalone post-implementation verification pipeline (Herdr code
  review → E2E plan → QA evidence → always evidence-audit → Kyle report). Use
  **only** when Kyle says "gate" / "run the gate," or manually invokes this
  skill. Do **not** use for ordinary implement/fix, closed-loop, or
  orchestrate-ticket runs.
metadata:
  argument-hint: <repo, branch/PR, or completed change to verify>
---

# Gate

**Opt-in only** — "gate" / "run the gate" or manual invoke. Finishing
implementation, closed-loop, or orchestrate-ticket does **not** select this.

Standalone verification **after** developer changes are done. Not part of
[orchestrate-ticket](../orchestration/orchestrate-ticket/SKILL.md); do not
edit that skill. May integrate later.

**EL-65:** [no-mistakes](../no-mistakes/SKILL.md) stays until a later change
removes it — this skill does not delete or modify it. Prefer **gate** when
Kyle asks for this pipeline.

## Role: orchestrator

You coordinate; you do **not** implement.

- Never write or edit target source, tests, or shipping config yourself.
  Implementation fixes → [developer](../developer/SKILL.md) via Herdr.
  E2E planning/execution and evidence collection → dedicated QA workers.
  Review and evidence-audit → separate review/audit workers.
- Your jobs: establish scope, invoke/sequence agents through
  [herdr](../orchestration/herdr/SKILL.md) (`--session Subagents`), relay
  review findings **verbatim**, enforce phase gates and budgets, route by
  cause, and report to Kyle.
- You may touch durable gate state under `~/Workbench/Reports/` (run log,
  evidence dirs, final report) — nothing that ships in the target repo.
- Subagents cannot talk to Kyle; all communication routes through you.
- Follow global safety and PR rules. Do not merge, deploy, push, or invent
  approvals. Wait for Kyle on commit/PR unless he already authorized it.

## Prerequisites

- Absolute target repo/worktree, feature branch or PR, intended base/commit
  range, and acceptance context for the **completed** change.
- Record `git status --short --branch` and a content digest before phase 1
  (same pattern as astra-review / code-critique). Disclose drift if the
  target moves mid-gate.
- Herdr Subagents available. Missing tools/models → stop and ask Kyle; never
  silently substitute.

Create/resume `~/Workbench/Reports/<work-id>-gate-run.md` and sibling
`<work-id>-gate-evidence/` before phase 1. Do not overwrite existing runs.

## Roles (skills-repo mapping)

| Gate role | Skills / workers |
| --- | --- |
| Reviewer (code-review / delta-review) | [astra-review](../orchestration/astra-review/SKILL.md) or [fable-review](../orchestration/fable-review/SKILL.md) when Kyle names them; else [code-critique](../code-critique/SKILL.md) via Herdr. Review-only. |
| Developer (fixes) | [developer](../developer/SKILL.md) via Herdr. Scoped remediation only; no E2E authorship. |
| E2E planner / QA author | Planning subagent: writes the E2E charter + evidence requirements (phase 2). |
| E2E executor | QA / browser worker: runs charter, collects evidence ([agent-browser](../agent-browser/SKILL.md) when browser). Judges nothing for final pass — record is audited. |
| Evidence auditor | **Separate** agent from the executor. Audits the record against the charter/plan. **Always** invoked, including on a clean pass. |
| Report | [report](../report/SKILL.md) for Kyle-facing Markdown under `~/Workbench/Reports/`. |

Handoffs: concise prose with absolute paths, work ID, HEAD/digest, scope
limits, and required return fields. Never guess a malformed reply — re-ask
once, then escalate to Kyle.

## Default models

Load Herdr launch defaults from
[references/default-models.json](references/default-models.json) (same
directory tree as this skill). Edit that file to change models — do not
hard-code IDs in this SKILL.md.

| Role | JSON key | Used for |
| --- | --- | --- |
| Reviewer | `reviewer` | Phase 1 code-review / delta-review |
| QA | `qa` | Phase 2 E2E planner, phase 3 executor, phase 4 evidence auditor |
| Developer | `developer` | Remediation / fix loops |

When launching via [herdr](../orchestration/herdr/SKILL.md), pass each role's
`kind`, `--model`, and `thinking` (when present) after `--`. Repeat
`--expect-startup` so the banner matches the chosen model (and thinking
level when set) before submit. Kyle may override per run; if a requested
model is unavailable, stop and ask — never silently substitute.

## Budgets and routing

- **Phase 1 review→fix loop:** max **3** remediation cycles (EL-65). Initial
  review does not count. Each cycle = developer fix + delta-review. Before a
  fourth fix attempt, **STOP and escalate to Kyle** with findings, attempts,
  and HEAD. Do not weaken severity to pass.
- **Phases 3–4 verify/audit:** route by the auditor's stated cause — never
  assume a code defect:
  - **Code/implementation defect** → developer fixes → delta-review →
    re-run affected E2E → **re-audit**.
  - **QA/evidence defect** (incomplete evidence, bad capture, table/evidence
    mismatch, executor fault) → E2E tester re-collects or corrects evidence →
    **re-audit**. Do **not** route to the developer.
  - **Runtime capability blocker** (environment, auth, external dependency,
    tooling) → **STOP and escalate to Kyle**. Not a code or QA defect.
- Evidence-only re-runs and their re-audits do not consume the phase-1 review
  budget. After **2 consecutive** verify/audit remediation cycles without
  progress, escalate to Kyle.
- **ALWAYS evidence-audit** after producing an execution record — including
  clean pass. Never skip; a fabricated all-pass looks identical to a real one
  without an independent audit.

## Phase 1 — Code review (Herdr)

1. Launch the reviewer on the completed diff/range. Pass plan/acceptance
   context if available; **never** pass prior plan-approval as a substitute
   for code review. Findings use code-critique severities (Critical/High/
   Medium/Low).
2. Blockers or should-fixes (Critical/High, blocking Medium) → relay
   **verbatim** to the developer subagent. Developer returns item-by-item
   `fixed` / `disputed`. Disputes go back to the reviewer, not silently
   dropped.
3. After fixes, invoke **delta-review** on the change set only.
4. Clean review (no blockers/should-fixes) → phase 2. Exhausted 3-cycle
   budget with major issues remaining → escalate to Kyle.

## Phase 2 — E2E plan (evidence requirements)

Delegate a planning subagent. Do **not** run tests here. The charter must:

- Cover acceptance-critical journeys and failure paths at a real system
  boundary (browser/UI, REST/API, CLI, worker/job). Prefer existing project
  E2E tooling; smallest viable path if none exists.
- State a concrete **verification contract**: surface, exact commands/tools,
  assertions, ordered steps, and **evidence requirements** for phase 4
  (commands, expected outcomes, artifact paths, and what each artifact
  **proves**). Screenshots prove visible state only — not interactions,
  persistence, or API success. Nonvisual work needs request/status/response
  or command/exit/output evidence.
- Name teardown when setup mutates runtime/external state.
- Exclude out-of-scope areas. If meaningful boundary verification is
  impossible, record a **blocking gap** and ask Kyle — never silently
  downgrade or skip.

Material ambiguity → ask Kyle before phase 3.

## Phase 3 — QA execution + evidence

Delegate the E2E executor against the verified checkout/build identity
(record URL, HEAD/digest, process ownership). Execute the charter in order;
collect artifacts under `<work-id>-gate-evidence/`. Return an itemized
record: item → expected → observed → pass/fail/unverified/blocked →
evidence path. Sanitize secrets.

- Product failures pause for phase-4 routing — do not skip items to green.
- Environment/auth/tooling blockers → escalate to Kyle (do not call them
  code defects).
- Executor does not author the final verdict; phase 4 does.

## Phase 4 — Evidence audit (always)

Invoke a **separate** auditor with: execution record, evidence paths,
phase-2 charter/contract, and acceptance context — **always**, including
`EXECUTED_PASS` / all-green. Auditor diffs the record against the charter.

Then route by cause (see Budgets and routing). After any correction,
**re-audit** before advancing. Final pass requires a clean audit; the
executor's raw pass is never sufficient alone.

## Phase 5 — Report for Kyle

Use [report](../report/SKILL.md). Save under `~/Workbench/Reports/` (e.g.
`<work-id>-gate.md`); link the run log and evidence — do not paste raw
logs. Adapt Atlas-style wrap-up into report layout:

- Outcome: distinguish **reviewed**, **E2E-verified**, and **delivered**
  (gate does not merge or deploy).
- Verification table: review result/rounds, E2E commands/results, evidence
  audit result; keep failed/unrun checks visible.
- Evidence: embed or link durable in-vault artifacts; visual UX needs
  screenshots; API/CLI needs machine-readable proof. Mark missing required
  evidence **UNVERIFIED**.
- Remaining work: nits, blockers, owners; wait for Kyle on commit/PR.

Optional: [lavish](../lavish/SKILL.md) only if Kyle asks for an HTML view —
default is the Markdown report skill, not Atlas `render-plan` / Lavish-axi.

## Stop conditions

Stop and ask Kyle when: phase-1 budget exhausted with blockers; two
no-progress verify/audit cycles; runtime/capability blockers; safety-
sensitive actions needing separate approval; material scope/acceptance
changes; audit cannot decide; or confidence cannot improve safely.
Preserve useful work; never auto-reset or discard it.

## Related skills

- [herdr](../orchestration/herdr/SKILL.md) — launch/wait on workers
- [developer](../developer/SKILL.md) — scoped fixes
- [code-critique](../code-critique/SKILL.md) — review criteria
- [astra-review](../orchestration/astra-review/SKILL.md) /
  [fable-review](../orchestration/fable-review/SKILL.md) — named reviewers
- [agent-browser](../agent-browser/SKILL.md) — browser E2E when applicable
- [report](../report/SKILL.md) — Kyle-facing report
- [closed-loop](../closed-loop/SKILL.md) — upstream implementation; not part
  of gate unless Kyle separately requests it
