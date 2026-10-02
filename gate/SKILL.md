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

**Opt-in only** ("gate" / "run the gate" / manual invoke). Not part of
[orchestrate-ticket](../orchestration/orchestrate-ticket/SKILL.md) — do not
edit it. [no-mistakes](../no-mistakes/SKILL.md) stays until a later change
(EL-65); prefer **gate** for this pipeline.

## Orchestrator

Coordinate only — never edit target source/tests/shipping config.

- Fixes → [developer](../developer/SKILL.md) via Herdr. Plan/execute E2E →
  planner/executor. Review/audit → separate workers.
- Sequence via [herdr](../orchestration/herdr/SKILL.md) (`--session
  Subagents`), relay findings **verbatim**, enforce budgets, route by the
  **auditor's** cause, report to Kyle.
- Durable state only under `~/Workbench/Reports/`. Subagents never talk to
  Kyle. No merge/deploy/push or invented approvals.

## Scope and run identity

Need absolute repo/worktree, branch/PR, base/range, and acceptance context for
the **completed** change. Herdr + models available or stop (no silent
substitutes).

Before phase 1 and after every developer return, record
`git status --short --branch` plus a content digest (astra-review /
code-critique pattern) as the **current gate baseline**. Out-of-band drift →
stop.

- `work-id`: ticket key if known, else `gate-<YYYY-MM-DD>-<short-slug>`.
- Each invocation: unique `run-id` = `<work-id>-<YYYYMMDD-HHMMSS>` (local).
  Never reuse a prior evidence dir against a new HEAD.
- Create `~/Workbench/Reports/<run-id>-gate-run.md` and
  `<run-id>-gate-evidence/` before phase 1; do not overwrite existing runs.

## Roles and profiles

| Role | Profile | Notes |
| --- | --- | --- |
| Reviewer | `reviewer` | Default [code-critique](../code-critique/SKILL.md). [astra-review](../orchestration/astra-review/SKILL.md) / [fable-review](../orchestration/fable-review/SKILL.md) only when Kyle names them (their launch profile). Naming authorizes a **fresh** launch each review, delta-review, and dispute. |
| Developer | `developer` | Scoped remediation; no E2E authorship. |
| E2E planner | `e2e_planner` | Charter + evidence requirements. |
| E2E executor | `qa` | Runs charter; [agent-browser](../agent-browser/SKILL.md) when browser. No final verdict. |
| Auditor | `auditor` | Separate launch from planner/executor. After every execution record. |
| Report | — | [report](../report/SKILL.md). Gate invoke = explicit Phase 5 report request. |

Handoffs: absolute paths, `run-id`, baseline HEAD/digest, scope, return
fields. Malformed reply → re-ask once, then escalate.

### Launch (`default-models.json`)

Ordinary launches **must** use
[references/default-models.json](references/default-models.json) (`provider/model`
IDs). Missing file/key → stop; never invent fields. Edit the JSON, not this
file, to change models.

1. Confirm the exact `model` is listed (`pi --list-models` or Claude
   equivalent).
2. `--kind` from `kind`; after `--`, `--model <model>`.
3. Reasoning: Pi → `--thinking` when `thinking` set; Claude → `--effort`
   when set; other kinds → stop.
4. `--expect-startup`: model **basename** (after last `/`); if Pi +
   `thinking`, also `"<basename> • <thinking>"`. Never a bare reasoning word.
5. Per-run overrides OK; unavailable model → stop.

## Budgets and routing

- **Developer remediations (shared):** max **3** per run (phase 1 +
  post-audit code fixes). Cycle = fix + delta-review. Initial review free.
  Fourth fix → escalate. Do not weaken severity.
- **Evidence-only:** no developer budget. **2 consecutive** with no progress
  → escalate. Progress = artifacts that change auditor outcome, or full
  charter re-run on a new baseline.
- Route **only** by auditor cause:
  - **Code** → developer (counts) → delta-review → invalidate prior execution
    evidence → **full charter** on new baseline → re-audit.
  - **QA/evidence** → executor re-collects → re-audit (not developer).
  - **Runtime capability** → escalate **only** if auditor confirms.
- Always run phase 4 after any execution record (pass/fail/blocked). Never
  escalate env/auth/tooling from phase 3.

## Phase 1 — Review

1. Launch reviewer (`reviewer` or named Astra/Fable). Pass acceptance context;
   never use prior plan-approval as code review.
2. Require **Critical / High / Medium / Low** labels (instruct Astra/Fable to
   add them).
3. **Must-fix** = Critical/High only (Medium/Low reported, not auto-blocking).
   Relay verbatim; developer returns `fixed`/`disputed`. One dispute in the
   **same** cycle; standing disagreement → escalate.
4. Re-baseline → fresh **delta-review** on the change set.
5. No Critical/High → phase 2; budget exhausted with must-fix left → escalate.

## Phase 2 — E2E plan

Planner (`e2e_planner`); do not run tests. Charter: acceptance-critical
journeys + failure paths at a real boundary; prefer existing E2E tooling;
smallest viable path. Include verification contract (surface, commands,
assertions, ordered steps, evidence requirements and what each artifact
**proves**). Screenshots = visible state only; nonvisual needs
request/status/response or command/exit/output. Name teardown if setup
mutates state. Blocking gap or material ambiguity → ask Kyle (no audit yet).

## Phase 3 — Execute

Executor (`qa`) on **current** baseline (URL, HEAD/digest, ownership). Full
charter in order → `<run-id>-gate-evidence/`. Each item: expected, observed,
pass/fail/unverified/blocked, path, **baseline digest**. Sanitize secrets.
Don't skip failures to green. Suspected env/auth/tooling → blocked record →
phase 4 (no escalate here).

## Phase 4 — Audit (always)

New `auditor` launch with record, paths, charter, acceptance, baseline —
including all-green and blocked. Auditor: open artifacts; spot-check cheap
read-only charter checks when feasible; reject digest ≠ baseline (default);
state cause (`code` / `QA-evidence` / `runtime-capability` / `clean`). Route
per Budgets; re-audit after corrections. Executor alone never decides pass.

## Phase 5 — Report

[report](../report/SKILL.md) → `~/Workbench/Reports/<run-id>-gate.md`; link
run log/evidence. Cover reviewed vs E2E-verified vs delivered (no
merge/deploy); verification table; evidence links (**UNVERIFIED** if missing);
remaining work. [lavish](../lavish/SKILL.md) only if Kyle asks.

## Stop

Ask Kyle on: shared developer budget exhausted with Critical/High left; two
no-progress evidence cycles; **auditor-confirmed** runtime blockers;
pre-phase-1 tool/model/Herdr gaps; phase-2 blocking gaps/ambiguity;
safety-sensitive needs; out-of-band baseline/scope drift; audit undecidable.
Preserve useful work.

Also: [closed-loop](../closed-loop/SKILL.md) is upstream implementation — not
part of gate unless Kyle asks separately.
