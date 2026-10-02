---
name: gate
description: >-
  Run the standalone post-implementation verification pipeline (Herdr code
  review → E2E plan → QA evidence → always evidence-audit → publish/update PR →
  Kyle report). Use **only** when Kyle says "gate" / "run the gate," or manually
  invokes this skill. Replaces the former no-mistakes ship/gate skill. Do **not**
  use for ordinary implement/fix, closed-loop, or orchestrate-ticket
  plan/implement stages (orchestrate-ticket may call gate for verify/deliver).
metadata:
  argument-hint: <repo, branch/PR, or completed change to verify>
---

# Gate

Opt-in post-implementation verification ("gate" / "run the gate" / manual
invoke). Replaces **no-mistakes**. Coordinate only — never edit target source,
tests, or shipping config yourself.

Fixes go to [developer](../developer/SKILL.md) via Herdr. Plan/execute E2E with
planner/executor. Review and evidence-audit use separate workers. Sequence via
[herdr](../orchestration/herdr/SKILL.md) (`--session Subagents`). Relay findings
verbatim. Route by the **auditor's** cause. Durable state under
`~/Workbench/Reports/`. No merge, deploy, force-push, or invented approvals.
Feature-branch push and PR create/update happen only in Phase 6 after a clean
audit.

## Scope and baseline

Need absolute repo/worktree, feature branch, intended PR base, and acceptance
context for the **completed** change.

**Product identity digest** = `git status --short --branch` plus the
astra-review / code-critique content digest, computed over the checkout **after
excluding** paths the charter lists as disposable (and normal gitignored
paths). Evidence under `~/Workbench/...` is hashed separately and never enters
this digest. Record product identity before phase 1, after every developer
return, after every read-only worker returns, **before** phase-3 execution, and
**after** teardown. Pre-run and post-teardown product identity **must match
exactly**. Teardown must delete or restore disposable outputs so that holds;
charter-listed disposables that cannot be torn down to restore identity → stop
before phase 3. Unexpected product-tree drift → invalidate that phase's
evidence and stop.

- `work-id`: ticket key if known, else `gate-<YYYY-MM-DD>-<short-slug>`.
- Each invocation: unique `run-id` = `<work-id>-<YYYYMMDD-HHMMSS>` (local).
  Never reuse a prior evidence dir against a new HEAD.
- Create `~/Workbench/Reports/<run-id>-gate-run.md` and
  `<run-id>-gate-evidence/` before phase 1; do not overwrite existing runs.

## Roles

| Role | Profile | Notes |
| --- | --- | --- |
| Reviewer | `reviewer` | Default [code-critique](../code-critique/SKILL.md). [astra-review](../orchestration/astra-review/SKILL.md) / [fable-review](../orchestration/fable-review/SKILL.md) only when Kyle names them. Naming authorizes a **fresh** launch each review, delta-review, and dispute. |
| Developer | `developer` | Scoped remediation + commits on the feature branch; no E2E authorship, push, or PR. |
| E2E planner | `e2e_planner` | Charter + evidence requirements. |
| E2E executor | `qa` | Runs charter; [agent-browser](../agent-browser/SKILL.md) when browser. No final verdict. Writes evidence only under the run's evidence dir (shell OK); `exclude_tools` blocks product-tree edit/write. Orchestrator may assemble the formal record from the reply into that dir. |
| Auditor | `auditor` | Separate launch (`gpt-5.6-terra` by default). After every execution **record**. |
| Report | — | [report](../report/SKILL.md). Gate invoke = explicit Phase 5 report request. |
| Publish | — | Phase 6: coordinator pushes feature branch and creates/updates the PR. |

Handoffs: absolute paths, `run-id`, baseline HEAD/digest, scope, return fields.
Malformed or unlabeled required fields → **escalate to Kyle** (do not re-prompt).

## Launch profiles

Ordinary launches **must** use
[references/default-models.json](references/default-models.json). Missing
file/key → stop; never invent fields. Edit the JSON to change models.

Each profile has `kind`, `model` (`provider/model`), optional `thinking`,
optional `exclude_tools` (Pi) and/or `disallowed_tools` (Claude; set explicitly
when needed — do not derive Claude names from Pi names), and required
`expect_startup` (string list).

1. Confirm the model exists: Pi → `pi --list-models`; Claude → `claude --help`
   and `claude auth status` (no list-models equivalent).
2. `--kind` from `kind`. After `--`: `--model <model>`; if `thinking` set, Pi
   gets `--thinking <thinking>`, Claude gets `--effort <thinking>`.
3. Pass every `expect_startup` entry via repeated `--expect-startup`. For Pi,
   include basename and contiguous `"basename • thinking"` when reasoning is
   set. For Claude, use only the phrases listed in JSON. Never invent banner
   text beyond that list.
4. If `exclude_tools` is set (Pi), pass `--exclude-tools <value>`. If
   `disallowed_tools` is set (Claude), pass `--disallowed-tools <value>`.
5. `submitted: false` / auth / permission / `startup_unexpected` on **any**
   mid-run launch → stop and ask Kyle (no audit path without a record).

## Budgets and routing

- **Developer remediations:** max **3** per run (phase 1 + post-audit code).
  Cycle = fix + commit + delta-review. Initial review free. Do not weaken
  severity.
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
`fixed`/`disputed` and commits fixes on the feature branch. One dispute (fresh
launch) in the same cycle; standing disagreement → escalate. Re-baseline →
delta-review. Clean → phase 2.

## Phase 2 — E2E plan

Planner (`e2e_planner`); do not run tests. Charter covers acceptance-critical
journeys and failure paths at a real boundary; prefer existing E2E tooling;
smallest viable path. **Also include** the repo's documented standard
unit/lint/typecheck commands (from package scripts, Makefile, or CI config)
unless Kyle scoped them out — gate has no separate CI phase. Include
verification contract (surface, commands, assertions, steps, evidence
requirements and what each artifact **proves**). Screenshots = visible state
only. Name teardown that restores product identity. List disposable paths
excluded from the product identity digest. Blocking gap or ambiguity → ask
Kyle.

## Phase 3 — Execute

Executor (`qa`) on current baseline. Full charter in order → evidence dir.
Each item: expected, observed, pass/fail/unverified/blocked, path, **pre-run
product identity**. After teardown, recompute: must equal pre-run identity;
mismatch → invalidate record and stop. Suspected target env/auth/tooling →
blocked record → phase 4. Mid-run Herdr launch failure → stop (see Launch).

## Phase 4 — Audit

New `auditor` launch with record, paths, charter, acceptance, and **matching
pre/post product identity** — including all-green and blocked records. Auditor
opens artifacts, spot-checks cheap read-only charter checks when feasible,
rejects evidence when pre-run ≠ post-teardown identity (default), and states
cause (`code` / `QA-evidence` / `runtime-capability` / `clean`). Route per
Budgets; re-audit after corrections.

## Phase 5 — Report

[report](../report/SKILL.md) → `~/Workbench/Reports/<run-id>-gate.md`. Cover
reviewed vs E2E-verified vs delivered; verification table; evidence links
(**UNVERIFIED** if missing); remaining work. Leave PR URL/final SHA placeholders
until Phase 6 fills them. [lavish](../lavish/SKILL.md) only if Kyle asks.

## Phase 6 — Publish

Only after auditor cause `clean` and a clean working tree at the verified HEAD
(developer commits any last remediation first). Then the **gate coordinator**:

1. Confirm branch is not `main`/`master`/default; intended PR base is set.
2. Push the feature branch with a normal (non-force) push.
3. If an open PR already targets this branch → that push updates it. Else create
   a PR against the intended base (`gh pr create` or equivalent) with title/body
   from the change scope; do not invent reviewers or auto-merge.
4. Record PR URL and full final SHA in the gate report. If the repo has required
   checks, note their status on that SHA when available; missing/failing required
   checks block "ready" wording but do not authorize merge or force-push.
5. Never merge, never push to the default branch, never force-push.

If push/PR creation fails (auth, protected branch, missing base) → stop and ask
Kyle with the gate report still linked.

## Stop

Ask Kyle when: developer budget exhausted with must-fix findings **or** an
outstanding auditor `code` cause; two no-progress evidence cycles;
auditor-confirmed runtime blockers; tool/model/Herdr gaps (pre-phase-1 or
mid-run launch failure); phase-2 blocking gaps; safety-sensitive needs;
unexpected baseline drift; audit undecidable; publish failure. Preserve useful
work.

[closed-loop](../closed-loop/SKILL.md) is upstream implementation — not part of
gate unless Kyle asks separately. [orchestrate-ticket](../orchestration/orchestrate-ticket/SKILL.md)
may invoke gate for verify/deliver after implementation; gate owns publish.
