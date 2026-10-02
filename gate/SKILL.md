---
name: gate
description: >-
  Run the standalone post-implementation verification pipeline (Herdr code
  review → E2E plan → QA evidence → always evidence-audit → Kyle report →
  publish/update PR). Use **only** when Kyle says "gate" / "run the gate," or
  manually invokes this skill. Replaces the former no-mistakes ship/gate skill.
  Do **not** use for ordinary implement/fix, closed-loop, or orchestrate-ticket
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
audit, and only when a remote exists.

## Scope and baseline

Need absolute repo/worktree and a **clean, committed** feature-branch HEAD for
the completed change. Dirty staged/unstaged trees → stop before Phase 1 (do not
commit the original input as coordinator). When publishing, record an **explicit
push remote** (e.g. `origin`) and intended PR base before Phase 6 — do not infer
the destination when multiple remotes exist. If there is no usable push remote,
treat as local-only.

**Product identity** = HEAD plus the astra-review / code-critique **content
digest** (tracked diffs and nonignored untracked files). Record the branch line
for display only — do **not** fail identity on ahead/behind shifts from a
background fetch. Exclude from the digest only charter-listed **untracked,
non-source** disposable paths (never tracked or source paths), via a fixed
pathspec such as `:(exclude)<path>` / `-- . ':(exclude)<path>'` applied
consistently to status, diff, and untracked hashing. Evidence under
`~/Workbench/...` is hashed separately. Record identity before phase 1, after
every developer return, after every read-only worker returns, before phase-3
execution, and after teardown. Pre-run and post-teardown product identity
**must match**. Teardown must restore it; if disposables cannot be torn down to
match, stop before phase 3. Unexpected product-tree drift → invalidate and stop.

- `work-id`: ticket key if known, else `gate-<YYYY-MM-DD>-<short-slug>`.
- Each invocation: unique `run-id` = `<work-id>-<YYYYMMDD-HHMMSS>` (local).
  Never reuse a prior evidence dir against a new HEAD.
- Create `~/Workbench/Reports/<run-id>-gate-run.md` and
  `<run-id>-gate-evidence/` before phase 1; do not overwrite existing runs.
  Write a report on blocked stops too (state = blocked + reason).

## Run workspace

Use one Herdr workspace per run, with separate worker conversations/tabs.
**Standalone:** launch the first reviewer without `--workspace` and capture its
returned ID. **Inside orchestrate-ticket:** adopt the waiting developer's live,
verified workspace/checkout before Phase 1, including for the first reviewer.
Persist `workspace_id` with `run-id` in `<run-id>-gate-run.md` before further
launches. Pass `--workspace <recorded-id>` before `--` on every subsequent launch,
including Astra/Fable and all review, repair, QA, and audit cycles; retain worker
names and returned workspace/tab/pane IDs. Follow Herdr's workspace/recovery
rules. On resume, reconcile the saved ID and pending work; missing, stale, or
conflicting workspace evidence blocks launches, never permits a replacement.
A new standalone run creates a workspace; resuming one does not.

## Roles

| Role | Profile | Notes |
| --- | --- | --- |
| Reviewer | `reviewer` | Default [code-critique](../code-critique/SKILL.md). [astra-review](../orchestration/astra-review/SKILL.md) / [fable-review](../orchestration/fable-review/SKILL.md) only when Kyle names them. Naming authorizes a **fresh** launch each review, delta-review, and dispute. |
| Developer | `developer` | Scoped remediation; see constraints below. |
| E2E planner | `e2e_planner` | Charter + evidence requirements. |
| E2E executor | `qa` | Runs charter; [agent-browser](../agent-browser/SKILL.md) when browser. No final verdict. Evidence only under the run's evidence dir (shell OK); `exclude_tools` blocks product-tree edit/write. Orchestrator may assemble the formal record from the reply into that dir. |
| Auditor | `auditor` | Separate launch after every execution **record**. |
| Report | — | [report](../report/SKILL.md). Gate invoke = explicit Phase 5 report request (including blocked runs). |
| Publish | — | Phase 6 when a remote exists. |

Developer commits fixes on the feature branch; no E2E authorship, push, or PR.
**Standalone gate:** launch from `default-models.json`. **Inside
orchestrate-ticket:** reuse the waiting ticket developer (keep its model); do
not launch a second developer. Auditor confirms the disposable-path list is
untracked non-source only.

Handoffs: absolute paths, `run-id`, selected `workspace_id` (once established),
baseline HEAD/identity, scope, return fields.
Consume accessible answers even if labels are messy — record missing fields as
uncertainty in the run log/report. Escalate only when **severity labels** (must-fix
set) or **auditor cause** are substantively unresolved so routing cannot proceed;
do not invent classifications or re-prompt for formatting alone.

## Launch profiles

Ordinary launches **must** use
[references/default-models.json](references/default-models.json), except the
orchestrate-ticket developer reuse rule above. Missing file/key → stop; never
invent fields. Edit the JSON to change defaults.

Each profile has `kind`, `model` (`provider/model`), optional `thinking`,
optional `exclude_tools` (Pi) and/or `disallowed_tools` (Claude; set explicitly),
and required `expect_startup` (string list).

1. Confirm the model exists: Pi → `pi --list-models`; Claude → `claude --help`
   and `claude auth status`.
2. `--kind` from `kind`. After `--`: `--model <model>`; if `thinking` set, Pi
   gets `--thinking <thinking>`, Claude gets `--effort <thinking>`.
3. Pass every `expect_startup` entry via repeated `--expect-startup`. For Pi,
   include basename and contiguous `"basename • thinking"` when reasoning is
   set. For Claude, use only JSON phrases. Never invent banner text beyond that
   list.
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
verdict). If severities are substantively missing so must-fix is undecidable →
escalate (no re-prompt).

**Must-fix** = Critical/High only. Relay verbatim; developer returns
`fixed`/`disputed` and commits fixes on the feature branch. One dispute (fresh
launch) in the same cycle; standing disagreement → escalate. Re-baseline →
delta-review. Clean → phase 2.

## Phase 2 — E2E plan

Planner (`e2e_planner`); do not run tests. Charter covers acceptance-critical
journeys and failure paths at a real boundary; prefer existing E2E tooling;
smallest viable path. **Also include** the repo's documented standard
unit/lint/typecheck commands (package scripts, Makefile, or CI config) unless
Kyle scoped them out. Include verification contract (surface, commands,
assertions, steps, evidence requirements and what each artifact **proves**).
Screenshots = visible state only. Name teardown that restores product identity.
List disposable paths (untracked non-source only). Blocking gap → ask Kyle.

## Phase 3 — Execute

Executor (`qa`) on current baseline. Full charter in order → evidence dir.
Each item: expected, observed, pass/fail/unverified/blocked, path, **pre-run
product identity**. After teardown, recompute: must equal pre-run; mismatch →
invalidate and stop. Suspected target env/auth/tooling → blocked record →
phase 4. Mid-run Herdr launch failure → stop (see Launch).

## Phase 4 — Audit

New `auditor` launch with record, paths, charter, acceptance, matching pre/post
identity, and the disposable-path list — including all-green and blocked
records. Auditor opens artifacts, spot-checks cheap read-only charter checks
when feasible, rejects bad disposable lists or identity mismatch, and states
cause (`code` / `QA-evidence` / `runtime-capability` / `clean`). Route per
Budgets; re-audit after corrections.

## Phase 5 — Report

[report](../report/SKILL.md) → `~/Workbench/Reports/<run-id>-gate.md` (also on
blocked stops). Cover reviewed vs E2E-verified vs delivered; verification table;
evidence links (**UNVERIFIED** if missing); remaining work; formatting
uncertainties. Draft PR URL / final SHA / check-status fields here; **Phase 6
must finalize them** (no placeholders left when the report is declared ready).
[lavish](../lavish/SKILL.md) only if Kyle asks.

## Phase 6 — Publish

Only after auditor cause `clean` at the already-committed verified HEAD.

- **No usable push remote:** skip publish; report
  `verified, unpublished (local-only)`.
- **Publish path:** record the explicit push remote and intended PR base in the
  run log. Confirm the branch is not the default. Non-force push to
  `<push-remote>/<feature-branch>` only (`git push <remote> HEAD`). Never merge,
  never push the default branch, never force-push.
- **Existing open PR for this branch:** read its base. If it matches the
  intended base, the push updates the head. If it differs, correct it with
  `gh pr edit --base <intended>` (or equivalent) before treating the run as
  published; if correction is unauthorized or fails → stop and ask. Do not
  report publication against the wrong base.
- **No open PR:** create one against the intended base
  (`gh pr create --base ...` or equivalent), targeting the repo that owns that
  base (usually after pushing to the recorded remote).
- **Required checks only:** bounded wait with
  `gh pr checks --watch --required` (finite timeout). Ignore optional checks
  when assigning status. Record one of: `published, checks passed`;
  `published, checks failed` (block ready wording); or `published, CI pending`
  if required checks are still running at timeout. Missing required checks
  after wait ≠ green.
- Push/PR auth or protected-branch failure → stop and ask.
- **Finalize the report before declaring ready:** for every Phase 6 outcome
  (published with checks passed/failed/pending, local-only unpublished, or
  publish failure), update `<run-id>-gate.md` with the PR URL or unpublished
  note, full final SHA, push remote, intended base, and required-check status.
  Revalidate that no placeholders remain ([report](../report/SKILL.md)). Only
  then treat the gate report as ready for Kyle / orchestrate-ticket.

## Stop

Ask Kyle when: dirty/uncommitted input before Phase 1; developer budget
exhausted with must-fix **or** outstanding auditor `code` cause; two
no-progress evidence cycles; auditor-confirmed runtime blockers; tool/model/
Herdr gaps (pre-phase-1 or mid-run launch failure); phase-2 blocking gaps;
safety-sensitive needs; unexpected identity drift; audit undecidable (missing
cause); publish failure. Preserve useful work; still write the blocked report.

[closed-loop](../closed-loop/SKILL.md) is upstream implementation — not part of
gate unless Kyle asks separately.
[orchestrate-ticket](../orchestration/orchestrate-ticket/SKILL.md) may invoke gate
for verify/deliver; gate owns publish when an explicit push remote is recorded.
