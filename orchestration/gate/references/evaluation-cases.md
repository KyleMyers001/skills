# Gate authoring evaluation cases

Paper scenarios, not runtime instructions or proof of a live pipeline. During
maintenance, verify observed tool calls separately from static/paper checks.

From the repository root, run profile/agent metadata regression checks with
`python3 -m unittest discover -s orchestration/gate/tests` and retained Herdr
coverage with `python3 -m unittest discover -s orchestration/herdr/tests`.
These static checks do not launch children or replace the scenarios below.

- Explicit “run the gate” selects Gate and authorizes its phase children.
  “Update the gate skill” selects skill-authoring, not pipeline execution.
  Ordinary implementation and closed-loop do not auto-trigger Gate.
- Missing pi-subagents, undiscovered Gate agents, missing profile keys, model
  unavailability, tool mismatch, machine placement, or shadowing definitions
  block before Phase 1. No settings rewrite, fallback model, CLI, or Herdr.
- Profiles map `agent` and `model:thinking` to the actual schema. No separate
  dispatch `thinking`, `exclude_tools`, startup phrases, or invented tool option.
  Planner retains edit/write; reader can use bash but has no edit/write.
- Every fresh native child gets the exact checkout, `worktree: false`, async,
  fresh context, artifact binding, safety rules, and no delegation/publication.
  Gate's run ID and returned subagent IDs are distinct and persisted.
- Completion notices wake the parent. A launch receipt, timeout, silence, or
  attention notice neither advances phases nor triggers polling/relaunch.
- Retained planner/developer/QA follow-ups resume the latest returned ID and
  record its successor. Live guidance uses steer; review/delta/dispute/audit
  instead launch fresh. Ambiguous custody or a missing session stops.
- Ticket handoff preserves the same waiting developer/model/checkout. Its
  coordinator owns transport, including legacy workers; Gate never treats a
  workspace ID as a subagent ID or creates a second remediation developer.
- Named Astra uses its exact high-thinking native model. Named Fable uses the
  read-only Claude Code adapter with subscription auth, high effort, and a
  complete sanitized exact-baseline handoff. No API billing, native-only
  adapter fields, missing-context pass, or Herdr-backed review skill launch.
- Dirty input stops before Phase 1. Read-only-worker product drift invalidates
  the run. Display-only ahead/behind changes do not invalidate content identity.
  Only charter-listed untracked non-source paths may be excluded consistently.
- Default Risk 7–10 / named Critical or High findings route to the developer.
  Findings relay verbatim; unresolved substantive classification escalates.
  Initial review is free; at most three repair+commit+delta-review cycles, one
  fresh dispute per cycle, no severity downgrade or budget reset on resume.
- Planner must save the charter to the assigned Workbench path. A chat-only or
  missing file is repaired with the same planner; it cannot authorize execution.
  Code remediation reconciles the revision without narrowing coverage.
- Every execution record, including blocked/all-green, gets a separate fresh
  auditor. A target-env failure is evidence first, not immediate escalation.
  Infrastructure failure with no record blocks; usable partial evidence is
  preserved and audited if an auditor can launch.
- Auditor cause alone routes: code → same developer → delta-review → full
  charter on the new baseline → new audit; evidence → re-collect → new audit;
  runtime-capability → Kyle. Two consecutive no-progress evidence rounds stop.
- Teardown must restore product identity; missing teardown approval or any
  drift blocks. Bash availability never grants destructive-operation approval.
- A clean audit alone permits Phase 6 feature-branch non-force push/PR. Never
  push main/master, merge, or deploy. No remote means verified/unpublished.
  Existing PR base mismatch is corrected or escalated. Missing/pending/failed
  required checks are not green; finalize exact SHA, PR/remote/base/status.
- Blocked stops still produce the Gate report with useful evidence and exact
  failure/custody. No automatic resource cleanup, cancellation, session sharing,
  replacement checkout, duplicate report, or invented approval.
