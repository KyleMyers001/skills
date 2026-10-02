---
name: gate
description: >-
  Run the standalone post-implementation verification pipeline (Herdr code
  review → E2E plan → QA evidence → always evidence-audit → Kyle report). Use
  **only** when Kyle says "gate" / "run the gate," or manually invokes this
  skill. Replaces the former no-mistakes ship/gate skill. Do **not** use for
  ordinary implement/fix, closed-loop, or orchestrate-ticket plan/implement
  stages (orchestrate-ticket may call gate for verify/deliver).
metadata:
  argument-hint: <repo, branch/PR, or completed change to verify>
---

# Gate

Opt-in post-implementation verification ("gate" / "run the gate" / manual
invoke). Replaces **no-mistakes**. Coordinate only — never edit target
source, tests, or shipping config yourself.

Fixes go to [developer](../developer/SKILL.md) via Herdr. Plan/execute E2E
with planner/executor. Review and evidence-audit use separate workers.
Sequence via [herdr](../orchestration/herdr/SKILL.md) (`--session Subagents`).
Relay findings verbatim. Route by the **auditor's** cause. Durable state only
under `~/Workbench/Reports/`. No merge, deploy, push, or invented approvals.

## Scope and baseline

Need absolute repo/worktree, branch/PR, base/range, and acceptance context for
the **completed** change.

**Baseline digest** = `git status --short --branch` plus the astra-review /
code-critique content digest. Record it before phase 1, after every developer
return, after every read-only worker returns (reviewer, planner, auditor), and
**again after E2E teardown**. Unexpected tracked or nonignored changes outside
the evidence directory → invalidate that phase's evidence and stop (out-of-band
drift). Expected E2E outputs must land under `<run-id>-gate-evidence/` or paths
the charter lists as disposable; those do not count as drift. If the charter
cannot avoid mutating the product tree, stop and ask Kyle before phase 3.

- `work-id`: ticket key if known, else `gate-<YYYY-MM-DD>-<short-slug>`.
- Each invocation: unique `run-id` = `<work-id>-<YYYYMMDD-HHMMSS>` (local).
  Never reuse a prior evidence dir against a new HEAD.
- Create `~/Workbench/Reports/<run-id>-gate-run.md` and
  `<run-id>-gate-evidence/` before phase 1; do not overwrite existing runs.

## Roles

| Role | Profile | Notes |
| --- | --- | --- |
| Reviewer | `reviewer` | Default [code-critique](../code-critique/SKILL.md). [astra-review](../orchestration/astra-review/SKILL.md) / [fable-review](../orchestration/fable-review/SKILL.md) only when Kyle names them. Naming authorizes a **fresh** launch each review, delta-review, and dispute. |
| Developer | `developer` | Scoped remediation; no E2E authorship. |
| E2E planner | `e2e_planner` | Charter + evidence requirements. |
| E2E executor | `qa` | Runs charter; [agent-browser](../agent-browser/SKILL.md) when browser. No final verdict. |
| Auditor | `auditor` | Separate launch. After every execution **record**. |
| Report | — | [report](../report/SKILL.md). Gate invoke = explicit Phase 5 report request. |

Handoffs: absolute paths, `run-id`, baseline HEAD/digest, scope, return fields.
Malformed or unlabeled required fields → **escalate to Kyle** (do not re-prompt;
Herdr and astra/fable-review forbid regenerate-without-approval).

## Launch profiles

Ordinary launches **must** use
[references/default-models.json](references/default-models.json). Missing
file/key → stop; never invent fields. Edit the JSON to change models.

Each profile has `kind`, `model` (`provider/model`), optional `thinking`,
optional `exclude_tools`, and required `expect_startup` (string list).

1. Confirm the model exists: Pi → `pi --list-models`; Claude → `claude --help`
   and `claude auth status` (no `pi --list-models` equivalent).
2. `--kind` from `kind`. After `--`: `--model <model>`; if `thinking` set, Pi
   gets `--thinking <thinking>`, Claude gets `--effort <thinking>`.
3. Pass every `expect_startup` entry via repeated `--expect-startup`. For Pi,
   include basename and contiguous `"basename • thinking"` when reasoning is
   set. For Claude, list display phrases from the profile (e.g. model display
   name and `with high effort`) — not a bare `high`. Never invent banner text
   beyond the JSON list.
4. If `exclude_tools` is set, pass `--exclude-tools <value>` (Pi) or the Claude
   `--disallowed-tools` equivalent from the named review skill.
5. `submitted: false` / auth / permission / `startup_unexpected` on **any**
   mid-run launch → stop and ask Kyle (no audit path without a record).

## Budgets and routing

- **Developer remediations:** max **3** per run (phase 1 + post-audit code).
  Cycle = fix + delta-review. Initial review free. Do not weaken severity.
- **Evidence-only:** max **2 consecutive** with no progress (auditor cause and
  required evidence unchanged). Progress = artifacts that change auditor
  outcome, or a full charter re-run on a new baseline.
- Route only by auditor cause:
  - **Code** → developer (counts) → delta-review → invalidate prior execution
    evidence → **full charter** on new baseline → re-audit.
  - **QA/evidence** → executor re-collects → re-audit.
  - **Runtime capability** → escalate only if auditor confirms.
- After any execution **record**, always phase 4. Target-env failures reported
  by a running executor go to phase 4 first — never escalate those from phase 3.

## Phase 1 — Review

Launch reviewer. When using Astra/Fable, **append** to the launch prompt:
label every finding Critical/High/Medium/Low (in addition to any merge
verdict). Unlabeled report → escalate (no re-prompt).

**Must-fix** = Critical/High only. Relay verbatim; developer returns
`fixed`/`disputed`. One dispute (fresh launch) in the same cycle; standing
disagreement → escalate. Re-baseline → delta-review. Clean → phase 2.

## Phase 2 — E2E plan

Planner (`e2e_planner`); do not run tests. Charter covers acceptance-critical
journeys and failure paths at a real boundary; prefer existing E2E tooling;
smallest viable path. Include verification contract (surface, commands,
assertions, steps, evidence requirements and what each artifact **proves**).
Screenshots = visible state only. Name teardown. List expected disposable
output paths. Blocking gap or ambiguity → ask Kyle.

## Phase 3 — Execute

Executor (`qa`) on current baseline. Full charter in order → evidence dir.
Each item: expected, observed, pass/fail/unverified/blocked, path, **pre-run
baseline digest**. After teardown, recompute digest: unexpected product-tree
drift → invalidate record and stop; expected evidence-dir outputs OK. Suspected
target env/auth/tooling → blocked record → phase 4. Mid-run Herdr launch
failure → stop (see Launch).

## Phase 4 — Audit

New `auditor` launch with record, paths, charter, acceptance, and **post-exec
digest** — including all-green and blocked records. Auditor opens artifacts,
spot-checks cheap read-only charter checks when feasible, rejects evidence
whose digest ≠ post-exec baseline (default), and states cause (`code` /
`QA-evidence` / `runtime-capability` / `clean`). Route per Budgets; re-audit
after corrections.

## Phase 5 — Report

[report](../report/SKILL.md) → `~/Workbench/Reports/<run-id>-gate.md`. Cover
reviewed vs E2E-verified vs delivered; verification table; evidence links
(**UNVERIFIED** if missing); remaining work. [lavish](../lavish/SKILL.md) only
if Kyle asks.

## Stop

Ask Kyle when: developer budget exhausted with must-fix findings **or** an
outstanding auditor `code` cause; two no-progress evidence cycles;
auditor-confirmed runtime blockers; tool/model/Herdr gaps (pre-phase-1 or
mid-run launch failure); phase-2 blocking gaps; safety-sensitive needs;
unexpected baseline drift; audit undecidable. Preserve useful work.

[closed-loop](../closed-loop/SKILL.md) is upstream implementation — not part of
gate unless Kyle asks separately. [orchestrate-ticket](../orchestration/orchestrate-ticket/SKILL.md)
may invoke gate for verify/deliver after implementation.
