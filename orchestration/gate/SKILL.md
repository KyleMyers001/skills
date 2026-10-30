---
name: gate
description: >-
  Run the standalone post-implementation verification pipeline (pi-subagents code
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

Load the installed **pi-subagents** skill for launch/resume rules. Gate invocation
authorizes these phase workers, not running Gate while maintaining this skill.
Fixes go to [developer](../../developer/SKILL.md); plan/execute E2E with separate
planner/executor children. Review and audit always get fresh contexts. Relay
findings verbatim; route by the **auditor's** cause. Durable state under
`~/Workbench/Reports/`. No merge, deploy, force-push, or invented approvals.
Feature-branch push and PR create/update happen only in Phase 6 after a clean
audit, and only when a remote exists.

## Scope and baseline

Need absolute repo/worktree and a **clean, committed** feature-branch HEAD for
the completed change. Staged/unstaged/nonignored-untracked changes → stop before
Phase 1 (do not commit the original input as coordinator). When publishing, record an **explicit
push remote** (e.g. `origin`) and intended PR base before Phase 6 — do not infer
the destination when multiple remotes exist. If there is no usable push remote,
treat as local-only.

**Product identity** = HEAD plus the
[code-critique](../../code-critique/SKILL.md) **content digest** (tracked diffs and nonignored untracked files). Record the branch line
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

## Run custody

Use the **same absolute checkout** for every phase: explicit `cwd`,
`worktree: false`. Gate never allocates child worktrees or changes branches.
Only one developer may write; other children must finish before remediation.
Persist Gate's `run-id` separately from each returned **subagent run ID**, role,
model/reasoning, session/artifact paths, completion state, and pending task in
`<run-id>-gate-run.md` before further launches. Save each resume's new ID and
lineage; never resume an older ID after its successor exists.

Launch asynchronously (`async: true`) and yield for native completion notices;
no sleep, polling, or `bg_wait` for ordinary children. A launch receipt, timeout,
or silence is not completion. Inspect output/artifacts before advancing. For
recovery, use exact-ID `status` (transcript view only as needed), then `resume`
for a completed/paused child or `steer` for a live one. Resume keeps the stored
model/tools; never pass a replacement agent/model. Review, delta-review,
dispute, and audit always launch **new**, with `options.context: "fresh"`
for native children (the Fable adapter is inherently fresh).

On coordinator resume, reconcile saved worker IDs, checkout/identity, custody,
pending work, and budgets before any launch. Missing/conflicting worker state
blocks replacement; never duplicate a writer, restart on elapsed time, or use
a CLI/pane fallback. Launch/auth/permission/model/tool-setup failures stop and
ask Kyle; preserve partial output and checkout state. No automatic stop,
cleanup, or session sharing.

## Roles

| Role | Profile | Notes |
| --- | --- | --- |
| Reviewer | `reviewer` | Default [code-critique](../../code-critique/SKILL.md). Select `astra_reviewer` / `fable_reviewer` only when Kyle names that reviewer; naming authorizes a **fresh** launch each review, delta-review, and dispute. |
| Developer | `developer` | Scoped remediation; see constraints below. |
| E2E planner | `e2e_planner` | [planning](../../planning/SKILL.md), E2E mode. Saves charter under `~/Workbench/Plans/` with edit/write. Product checkout read-only. |
| E2E executor | `qa` | Runs charter; [agent-browser](../../agent-browser/SKILL.md) when browser. No final verdict. Evidence only under the run's evidence dir (shell OK); reader agent has no edit/write tools. Coordinator may assemble the formal record from its reply. |
| Auditor | `auditor` | Separate fresh launch after every execution **record**. |
| Report | — | [report](../../report/SKILL.md). Gate invoke = explicit Phase 5 report request (including blocked runs). |
| Publish | — | Phase 6 when a remote exists. |

Developer commits fixes on the feature branch; no E2E authorship, push, or PR.
**Standalone:** launch the developer once, then resume its latest returned ID
for repairs. **Inside orchestrate-ticket:** keep the waiting ticket developer
and its model/checkout. The ticket coordinator owns that worker's existing
transport; send repairs through that owner and await its committed handoff,
never relaunch or pass a ticket workspace ID to `subagent`. Record owner,
worker identity/protocol, and custody before Phase 1; missing owner or uncertain
liveness blocks. All Gate-owned children use pi-subagents. Auditor confirms
the disposable-path list is untracked non-source only.

Every handoff: absolute checkout/branch, Gate `run-id`, worker ID/owner,
baseline HEAD/identity, scope/exclusions, applicable global/repo safety rules,
artifact paths, expected return fields, and stop conditions. No child agents,
branch changes, publication, or unapproved destructive operations.
Consume accessible answers even if labels are messy — record missing fields as
uncertainty in the run log/report. Escalate only when **must-fix classification**
(risk score or severity label) or **auditor cause** is substantively unresolved so
routing cannot proceed;
do not invent classifications or re-prompt for formatting alone. Phase 2's saved
plan is a required artifact, not a formatting preference.

## Launch profiles

Read [setup and launch contract](references/pi-subagents.md) before Phase 1.
Profiles in [default-models.json](references/default-models.json) provide required
`agent`, `model`, and `thinking`. Use them for every fresh launch except the
ticket-owned developer. Change pins in JSON, not prose. Missing file/key/agent,
unavailable model, or incompatible tools → stop; never substitute.

If only `subagents_enable` is visible, call it; `subagent` appears on the next
model request. Inspect `list` with `options.capabilities: true` and `models`.
Verify bundled Gate agents are discovered and effective tools match their role:
review/QA/audit have read + bash but no edit/write or delegation; planner has
edit/write to save its plan. Tool allowlists are not a Bash sandbox: prompts
must forbid product writes except developer repairs and charter-approved
disposables. Verify any shadowing definitions/overrides, not just agent names.

Launch with the profile's `agent`, `model: "<model>:<thinking>"`, explicit `cwd`,
`worktree: false`, `async: true`, `options: { context: "fresh" }`, and the full
task. Do **not** pass `thinking` separately: dispatch ignores it. Use `output`
for a unique absolute response path under the evidence dir; save the planner's
short handoff there, not over its charter. Inspect returned model/reasoning when
available and stop on a mismatch. Runtime acceptance is evidence, never a
replacement for Gate's independent review, charter, and audit.

Named reviewers still use pi-subagents, **not** the Astra/Fable Herdr skills.
Astra uses the pinned native profile. Fable uses the `claude-code` adapter with
subscription auth and high effort; it cannot inspect the checkout itself.
Follow the reference's evidence-handoff and billing checks, or stop.

## Budgets and routing

- **Developer remediations:** max **3** per run (phase 1 + post-audit code).
  Cycle = fix + commit + delta-review. Initial review free. Do not lower risk
  scores or severity labels to bypass must-fix routing.
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

Launch the default reviewer with explicit instructions to read the absolute
[code-critique](../../code-critique/SKILL.md) path and inspect the full comparison
against the intended base (not just the clean working tree). Default review
returns **Risk: N/10**. When using
Astra/Fable, **append** to the launch prompt: label every finding
Critical/High/Medium/Low (in addition to any merge verdict). If risk scores or
severity labels are substantively unresolved so must-fix is undecidable →
escalate (no re-prompt).

**Must-fix** = code-critique scores **7–10**, or Astra/Fable labels
**Critical/High**. Scores 1–6 and Medium/Low are nonblocking. Use the selected
reviewer's rubric; do not invent score-to-label conversions.
Relay verbatim; developer returns
`fixed`/`disputed` and commits fixes on the feature branch. One dispute (fresh
launch) in the same cycle; standing disagreement → escalate. Re-baseline →
delta-review. Clean → phase 2.

## Phase 2 — E2E plan

Launch `e2e_planner` with explicit instructions to read
[planning](../../planning/SKILL.md) and its
[E2E test-plan reference](../../planning/references/e2e-test-plan.md) (resolve both
to absolute paths in the prompt). Do not run product tests or edit product files.

Assign `~/Workbench/Plans/<run-id>-e2e-test-plan.md` as the required deliverable.
Pass its absolute path, the run log, evidence directory, acceptance/scope, and
current baseline identity. edit/write stay available so it can save that
markdown file. Do not edit product files. Require the reference's compact
format and a short reply with the absolute path, revision, coverage, and
blockers — no inline plan.

Open the saved file before Phase 3; check the reference's coverage, standard
commands, evidence contract, and teardown requirements. Record its absolute path
and revision in the run log; pass the same artifact to executor and auditor.
Chat-only output or an unreadable/missing plan → have the planner save it before
continuing; do not treat the chat as the charter. Blocking gap → ask Kyle.
After code remediation, have the planner reconcile the saved plan with the new
baseline and update its revision before the full re-run; do not narrow coverage.

## Phase 3 — Execute

Executor (`qa`) reads the full saved plan on the current baseline. Execute the
charter in order → evidence dir; retain its check/case IDs in the record.
Each item: expected, observed, pass/fail/unverified/blocked, path, **pre-run
product identity**. After teardown, recompute: must equal pre-run; mismatch →
invalidate and stop. Suspected target env/auth/tooling → blocked record →
phase 4. Subagent infrastructure failure without an execution record → stop;
with a usable execution record, preserve it and attempt Phase 4 if available.
If audit cannot launch, remain blocked; no repair, verdict, or publish without it.

## Phase 4 — Audit

New `auditor` launch with record, paths, saved plan path/revision, acceptance,
matching pre/post identity, and the disposable-path list — including all-green
and blocked records. Auditor reads the plan and opens artifacts, spot-checks
cheap read-only charter checks
when feasible, rejects bad disposable lists or identity mismatch, and states
cause (`code` / `QA-evidence` / `runtime-capability` / `clean`). Route per
Budgets; re-audit after corrections.

## Phase 5 — Report

[report](../../report/SKILL.md) → `~/Workbench/Reports/<run-id>-gate.md` (also on
blocked stops). Cover reviewed vs E2E-verified vs delivered; verification table;
evidence links (**UNVERIFIED** if missing); remaining work; formatting
uncertainties. Draft PR URL / final SHA / check-status fields here; **Phase 6
must finalize them** (no placeholders left when the report is declared ready).
[lavish](../../lavish/SKILL.md) only if Kyle asks.

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
  Revalidate that no placeholders remain ([report](../../report/SKILL.md)). Only
  then treat the gate report as ready for Kyle / orchestrate-ticket.

## Stop

Ask Kyle when: dirty/uncommitted input before Phase 1; developer budget
exhausted with must-fix **or** outstanding auditor `code` cause; two
no-progress evidence cycles; auditor-confirmed runtime blockers; tool/model/
subagent gaps (pre-phase-1 or mid-run launch failure); phase-2 blocking gaps;
safety-sensitive needs; unexpected identity drift; audit undecidable (missing
cause); publish failure. Preserve useful work; still write the blocked report.

[closed-loop](../../closed-loop/SKILL.md) is upstream implementation — not part
of gate unless Kyle asks separately.
[orchestrate-ticket](../orchestrate-ticket/SKILL.md) may invoke gate for
verify/deliver; gate owns publish when an explicit push remote is recorded.

[Evaluation cases](references/evaluation-cases.md): authoring only, never runtime.
