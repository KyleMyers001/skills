---
name: closed-loop
description: >-
  Run the closed-loop development workflow (investigate → approved plan →
  all-tests-first → bounded repair/verify). Use **only** when Kyle says
  "closed loop" / "run in a closed loop," or manually invokes this skill.
  Do **not** use for plan-only requests, ordinary implement/fix/ship after plan
  approval, or tasks that merely need tests.
metadata:
  argument-hint: <task, ticket, or implementation goal>
---

# Closed-Loop Development

**Opt-in only** — explicit closed-loop language or manual invoke; plan approval
alone is not enough.

Use the current agent; no subagents or external reviewers unless requested.
Load `planning` and `developer` as needed.
This workflow requires **all planned tests before implementation**, overriding
incremental test-first sequencing. Work does not continue after the session stops.

Follow global safety and PR rules; plan approval does not authorize destructive
actions, production mutations, overwriting user work, or merging. Preserve
unrelated work. Use safe local/test environments and synthetic data. For non-Git
targets, state the exception in the plan and use file-level before/after evidence;
confirm destructive overwrites separately. Ordinary approved targeted edits
need no additional approval. After authorization, choose routine details within
scope and report milestones without asking to continue.

## 1. Investigate and Obtain Approval

Before modifying the target:

1. Inspect instructions, Git status, code, callers, tests, and scripts; follow
   CodeGraph-first discovery where indexed. Run proportionate non-updating
   baseline checks without auto-fixes or snapshot updates. Compare status and
   file contents before/after, including untracked files. Defer checks that
   mutate target files until authorized. Report unexpected changes without
   attribution or automatic reversion.
2. Establish observable acceptance criteria, scope, and non-goals. Resolve
   material behavior, architecture, and verification questions.
3. Use `planning` to create and lint a plan satisfying its
   [closed-loop plan requirements](../planning/references/closed-loop-plan.md).
   Read the entire plan before execution, including execution details.
4. Present path, decisions, verification, and blockers. Ask for approval **and
   authorization to execute**, then stop. “Approve and proceed” grants both;
   silence or review-only approval does not. Write no target tests or code yet.

For an already approved plan with explicit execution authorization, confirm it
still fits planning's closed-loop requirements or applicable explicit exceptions.
Validate by content, not heading names; revise and seek fresh approval for actual
missing requirements, not formatting preferences. A delegated agent may validate
recorded approval and execution permission for the exact plan revision/content;
delegation alone is not permission. Material scope, acceptance, approach, or
verification changes always require renewed approval.

## Durable State

After authorization, before target changes, create/resume
`~/Workbench/Reports/<plan-id>-run.md` using the plan's work-item ID. Store
screenshots/evidence in sibling `<plan-id>-evidence/`, outside the target.
Do not overwrite an existing run or evidence. Maintain:

- Approval wording, date, source/session, plan path/revision, absolute target,
  and branch/checkout identity when applicable.
- Baselines, checkpoints, acceptance evidence, exact commands/results, and
  artifact paths, with sensitive output sanitized.
- Approved budget, total attempts used, consecutive no-progress attempts on the
  same blocker, and remaining budget. Initialize only for a new run.
- Each repair's hypothesis and charged attempt number **before execution**,
  then changes, verification, outcome, and remaining failures. Interrupted
  attempts stay counted; their outcome is unknown until verified.

Appending execution facts is not a plan revision and cannot change approval or
scope. On resume, reconcile the log, plan, current work, and pending attempts;
confirm authorization still applies.
If the log is missing or approval/counts ambiguous, stop and ask; never guess
or reset. Rerun checks as needed for current-code evidence.

## 2. Write All Tests — Red

1. Write every planned test across applicable unit, integration, and E2E layers.
   Add only necessary fixtures/harness setup, not disguised implementation.
2. Assert observable workflows, edges, failures, and regressions. Keep integration
   boundaries real where practical; do not mock away the tested behavior.
3. Run tests and record red evidence. Failures must expose missing behavior,
   not broken setup or unrelated errors. A missing public API can be expected
   red; recheck behavioral assertions once it exists.
4. Existing behavior/refactors may already pass: explain, never manufacture red.
   Record manual-only browser scenarios and expected outcomes before coding.

Do not implement with an unexplained red baseline. If meaningful tests cannot
be written/run under the approved approach, report the blocker rather than
silently abandoning test-first development.

## 3. Implement and Repair

Implement the smallest cohesive solution. Give each approved increment one
initial pass ending at its checkpoint. Tests for future increments may remain
red without consuming repairs. A failed current checkpoint or any regression
enters repair; do not relabel repairs as new increments or extend initial
implementation indefinitely. Refactor with relevant checks green and rerun
afterward.

For every failed checkpoint, including E2E/browser:

1. Check both limits. If reached, stop; otherwise log and charge the attempt
   before diagnosis/repair. Complete it even if it consumes the final cycle.
2. Test an evidence-based hypothesis. Add a regression test before fixing an
   uncovered in-scope case.
3. Make a targeted repair; rerun the failing check and affected regressions.
4. Log evidence, changes, commands, results, and remaining work.

A cycle is one bounded diagnostic/repair attempt plus verification, not one
command. Diagnosis-only attempts count. Allow at most one unchanged diagnostic
rerun per failure, inside a counted attempt; a flaky pass is not success.
Counts span phases/sessions. Progress means new relevant evidence or a resolved
failure, not another edit. Reset the consecutive count only for such progress
or a genuinely different blocker; never reset total attempts. Escalate when
either limit leaves work blocked.

Never weaken assertions, delete/skip valid tests, mask errors, or blindly accept
snapshots for green. Mechanical selector/import/fixture corrections require
logged evidence of unchanged behavior/coverage. Changing expected behavior
requires independent evidence from approved requirements; escalate ambiguous
intent or reduced coverage.

## 4. Verify the Whole Workflow

- Run planned unit/integration tests, relevant broader regressions, and
  applicable type checks, lint, and build.
- Run E2E through the actual application entry point and components. For
  non-browser work, use API/CLI/job execution asserting output, persistence,
  and failure behavior. Fully mocked components are not E2E evidence.
- For browser-visible work, use `agent-browser` and its current CLI workflow
  docs against the changed running app. Exercise approved journeys, states,
  interactions, layout, and console/network failures. Capture screenshots
  and observed outcomes; images alone do not prove behavior.
- Start servers from the working checkout in the background with bounded
  readiness waits. Log URL, checkout/build identity, process ownership, and
  named browser session. Verify reused servers too; an open port is not proof
  of checkout identity. Never hijack ports or kill their owners; use another
  port or escalate. Clean up only owned resources with explicitly authorized
  exact cleanup; otherwise ask and report what remains running.
- Route failures through the remaining repair budget. After the final edit,
  rerun affected checks; browser evidence must cover the current build.
- Inspect the final diff for unintended changes, incomplete acceptance,
  unsafe behavior, and unnecessary complexity. Repair in-scope defects and
  verify again. Pre-existing failures do not excuse new ones.

Use approved alternatives for inapplicable layers. Unrunnable required checks
are **blocked**, not passed. Separate pre-existing failures and limit assurance
to actual evidence; unit tests alone do not complete behavioral work.

## 5. Delivery or Escalation

**Done:** every acceptance criterion has current evidence, required applicable
checks pass, approved exceptions are recorded, and the diff is self-reviewed.
Implementation completion is not merging or deployment.

For Git targets, stage only intended changes, including tests, and compare
staged content with verified work. Preserve unrelated staged/unstaged changes;
do not require a globally clean worktree. Commit on the feature branch, verify
commit/branch diff including hook changes, and rerun affected checks if content
changed. By default, push only that branch, open a PR, and log commit/PR.
Missing delivery access is blocked delivery, not permission to push to the
default branch. Non-Git targets use planned file evidence.

**Opt-in `commit-and-handoff`:** only when the approved plan explicitly names
this mode and an external delivery owner, stop after the verified feature-branch
commit. All preceding approval, test-first, repair, verification, self-review,
and commit safeguards still apply. Do not push, open a PR, or invoke a delivery
pipeline. Report `implementation-complete / delivery-pending`, with absolute
checkout, branch/base, full commit SHA, changed files, acceptance-to-evidence
mapping, exact check results, exceptions, log location, remaining repair budget,
and blockers. Explicitly relinquish write ownership to the named owner and wait.
Missing required implementation evidence blocks handoff; it is not delegated
away. Handoff is not completed delivery and never changes standalone defaults.

**Stop and ask Kyle** for exhausted limits with work remaining; external
credential/environment/requirement/dependency blockers; safety-sensitive actions
that need separate permission; material scope/architecture/verification changes;
test/requirement disagreement; unexplained failures; or confidence that cannot
improve safely within the approach. Escalate clear external blockers immediately.
Bound commands and polling. Preserve useful work; never automatically reset or
discard it.

Report changes, plan/log/branch/PR paths, actual checks/results/evidence, approved
exceptions, pre-existing failures, and risks. If blocked, include the exact
blocker, attempts/evidence, remaining budget, current work state, and smallest
needed decision or action.

The run log is the audit trail, not Kyle's reading copy. When reporting to Kyle
directly, write a separate report with the `report` skill and link the run log
from it. Under `commit-and-handoff`, do not write one: return the handoff fields
in your response, and the delivery owner writes the report.
