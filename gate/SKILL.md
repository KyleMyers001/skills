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
  E2E planning/execution → QA workers. Review and evidence-audit → separate
  review/audit workers.
- Your jobs: establish scope, invoke/sequence agents through
  [herdr](../orchestration/herdr/SKILL.md) (`--session Subagents`), relay
  review findings **verbatim**, enforce phase gates and budgets, route by
  the auditor's cause, and report to Kyle.
- You may touch durable gate state under `~/Workbench/Reports/` (run log,
  evidence dirs, final report) — nothing that ships in the target repo.
- Subagents cannot talk to Kyle; all communication routes through you.
- Follow global safety and PR rules. Do not merge, deploy, push, or invent
  approvals. Wait for Kyle on commit/PR unless he already authorized it.

## Prerequisites

- Absolute target repo/worktree, feature branch or PR, intended base/commit
  range, and acceptance context for the **completed** change.
- Before phase 1, and again after every developer return, record
  `git status --short --branch` and a content digest (same pattern as
  astra-review / code-critique). That digest is the **current gate
  baseline**. Out-of-band changes (not from a gate developer cycle) → stop
  and ask Kyle.
- Herdr Subagents available. Missing tools/models → stop and ask Kyle; never
  silently substitute.

### Run identity

- `work-id`: ticket key when known (e.g. `EL-65`); otherwise
  `gate-<YYYY-MM-DD>-<short-slug>`.
- Each gate **invocation** gets a unique `run-id` =
  `<work-id>-<YYYYMMDD-HHMMSS>` (local). Never resume a prior run's evidence
  dir against a new HEAD.
- Create `~/Workbench/Reports/<run-id>-gate-run.md` and sibling
  `<run-id>-gate-evidence/` before phase 1. Do not overwrite existing runs.

## Roles

| Gate role | Profile key | Skills / workers |
| --- | --- | --- |
| Reviewer | `reviewer` | Default: [code-critique](../code-critique/SKILL.md) via Herdr. [astra-review](../orchestration/astra-review/SKILL.md) / [fable-review](../orchestration/fable-review/SKILL.md) **only** when Kyle names them (their skill launch profile). Naming them for a gate run authorizes a **fresh** launch each review, delta-review, and dispute round. |
| Developer | `developer` | [developer](../developer/SKILL.md) via Herdr. Scoped remediation only; no E2E authorship. |
| E2E planner | `e2e_planner` | Herdr planner: charter + evidence requirements (phase 2). |
| E2E executor | `qa` | Herdr QA / browser worker ([agent-browser](../agent-browser/SKILL.md) when browser). Collects evidence; does not author the final verdict. |
| Evidence auditor | `auditor` | **Separate** Herdr launch from planner and executor. Always runs after an execution record (pass, fail, or blocked). |
| Report | — | [report](../report/SKILL.md). Invoking gate is an explicit Phase 5 report request (overrides report's usual verification-only chat-default). |

Handoffs: absolute paths, `run-id`, current baseline HEAD/digest, scope
limits, required return fields. Never guess a malformed reply — re-ask once,
then escalate to Kyle.

## Default models (required launch profiles)

Ordinary gate launches **must** use
[references/default-models.json](references/default-models.json). Edit that
file to change models — do not hard-code IDs here. Missing file/key → stop
and ask Kyle; never invent `kind` / `model` / reasoning fields.

`model` values are full `provider/model` IDs (e.g. `openai-codex/gpt-6.1-sol`).
Defaults today: `reviewer`, `e2e_planner`, `auditor`, and `developer` →
`openai-codex/gpt-6.1-sol` with high reasoning; `qa` (executor) →
`openai-codex/gpt-5.6-terra`.

### Launch mapping

1. Read the role's JSON object. Confirm `pi --list-models` (or Claude
   equivalent) contains the exact `model` string.
2. Pass `--kind` from `kind`. After `--`, pass `--model <model>`.
3. Reasoning flag by kind: Pi → `--thinking <thinking>` when `thinking` is
   set; Claude → `--effort <thinking>` when `thinking` is set. If `kind` is
   neither `pi` nor `claude`, stop and ask Kyle.
4. `--expect-startup` (repeat): always the model **basename** (segment after
   the last `/`). When `thinking` is set on Pi, also pass the contiguous
   footer `"<basename> • <thinking>"` (e.g. `gpt-6.1-sol` and
   `gpt-6.1-sol • high`). Do not pass a bare reasoning word alone.
5. Kyle may override per run; unavailable model → stop — never substitute.

## Budgets and routing

- **Developer remediation budget (shared):** max **3** cycles per gate run
  (EL-65). A cycle = developer fix + delta-review. Counts **all** developer
  remediations from phase 1 **and** from post-audit code defects. Initial
  review does not count. Before a fourth fix, **STOP and escalate** with
  findings, attempts, and HEAD. Do not weaken severity to pass.
- **Evidence-only cycles:** do not consume the developer budget. After **2
  consecutive** evidence remediations with no progress (auditor cause class
  and required evidence set unchanged), escalate. Progress = new artifacts
  that change the auditor outcome, or a full charter re-run on a new
  baseline.
- **Phases 3–4:** route only by the **auditor's** stated cause:
  - **Code/implementation defect** → developer fix (counts toward the shared
    3) → delta-review → **invalidate** prior execution evidence → **full
    charter** re-run on the new baseline → **re-audit**.
  - **QA/evidence defect** → E2E **executor** re-collects/corrects →
    **re-audit**. Do not route to the developer.
  - **Runtime capability blocker** → escalate to Kyle **only** after the
    auditor confirms it.
- **Always evidence-audit** after any execution record (pass, fail, or
  blocked). Never skip phase 4 to escalate early from phase 3.

## Phase 1 — Code review

1. Launch the reviewer (`reviewer` profile, or named Astra/Fable skill). Pass
   plan/acceptance context if available; never substitute prior plan-approval
   for code review.
2. Require findings labelled **Critical / High / Medium / Low**
   (code-critique). For named Astra/Fable, instruct them to label each
   finding with those severities in the report (in addition to any merge
   verdict).
3. **Must-fix** (blocks advance): Critical and High only. Medium and Low are
   reported but do not auto-block unless Kyle says otherwise. Relay must-fix
   items **verbatim** to the developer. Developer returns item-by-item
   `fixed` / `disputed`. One dispute round with the reviewer is part of the
   **same** remediation cycle; standing disagreement after that → escalate.
4. After fixes, re-baseline, then **delta-review** (fresh launch) on the
   change set only.
5. Clean (no Critical/High) → phase 2. Exhausted 3-cycle budget with
   Critical/High remaining → escalate.

## Phase 2 — E2E plan

Delegate the planner (`e2e_planner`). Do **not** run tests here. Charter must:

- Cover acceptance-critical journeys and failure paths at a real system
  boundary (browser/UI, REST/API, CLI, worker/job). Prefer existing project
  E2E tooling; smallest viable path if none exists.
- State a **verification contract**: surface, exact commands/tools,
  assertions, ordered steps, and **evidence requirements** (commands,
  expected outcomes, artifact paths, and what each artifact **proves**).
  Screenshots prove visible state only. Nonvisual work needs
  request/status/response or command/exit/output evidence.
- Name teardown when setup mutates runtime/external state.
- Exclude out-of-scope areas. If meaningful boundary verification is
  impossible, record a **blocking gap** and ask Kyle (pre-phase-3 stop; no
  audit required) — never silently downgrade or skip.

Material ambiguity → ask Kyle before phase 3.

## Phase 3 — QA execution + evidence

Delegate the executor (`qa`) against the **current** baseline (record URL,
HEAD/digest, process ownership). Execute the full charter in order; collect
under `<run-id>-gate-evidence/`. Each item: expected → observed →
pass/fail/unverified/blocked → evidence path → **baseline digest**. Sanitize
secrets.

- Product failures pause for phase-4 routing — do not skip items to green.
- Suspected env/auth/tooling blockers still produce a complete blocked record
  and **always go to phase 4**. Do not escalate from phase 3.
- Executor does not author the final verdict.

## Phase 4 — Evidence audit (always)

New Herdr launch with the `auditor` profile. Give it the execution record,
evidence paths, phase-2 charter, acceptance context, and current baseline —
**always**, including all-green and blocked records.

Auditor must:

1. Open cited artifacts (not filenames alone).
2. Re-execute cheap read-only checks from the charter when feasible (e.g.
   file exists, command/exit replay, response status); note what was
   spot-checked vs inspection-only.
3. Reject evidence whose recorded digest ≠ current gate baseline (or justify
   why a specific item is unaffected — default is reject).
4. Diff the record against the charter and state the cause class
   (code / QA-evidence / runtime-capability / clean).

Then route (see Budgets). After any correction, **re-audit**. Final pass
requires a clean audit; the executor's raw pass or self-reported blocker is
never enough alone.

## Phase 5 — Report for Kyle

Use [report](../report/SKILL.md). Save
`~/Workbench/Reports/<run-id>-gate.md`; link the run log and evidence — do
not paste raw logs.

- Outcome: **reviewed**, **E2E-verified**, and **delivered** (gate does not
  merge or deploy).
- Verification table: review result/rounds, E2E commands/results, audit
  result; keep failed/unrun checks visible.
- Evidence: embed or link durable artifacts; mark missing required evidence
  **UNVERIFIED**.
- Remaining work: nits, blockers, owners; wait for Kyle on commit/PR.

Optional: [lavish](../lavish/SKILL.md) only if Kyle asks for HTML.

## Stop conditions

Stop and ask Kyle when:

- Phase-1 / shared developer budget exhausted with Critical/High remaining
- Two consecutive no-progress evidence remediations
- **Auditor-confirmed** runtime/capability blockers
- Pre-phase-1 prerequisite failures (tools/models/Herdr missing)
- Phase-2 blocking gaps or material ambiguity
- Safety-sensitive actions needing separate approval
- Material out-of-band scope/acceptance or baseline drift
- Audit cannot decide, or confidence cannot improve safely

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
