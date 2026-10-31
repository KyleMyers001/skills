# Gate worker briefs

Read when composing any BB phase prompt. These are **prompt-enforced authority
limits, not tool allowlists**. Append the phase-specific scope and required
return fields from Gate; do not rely on provider defaults or a parent's history.
The coordinator saves the final brief and captures the answer outside the
product tree. The old `agents/` definitions are legacy Pi material, not BB setup.

## Every worker

Supply absolute checkout, branch/base, baseline HEAD/content identity, Gate
run/role/attempt, coordinator/project/environment/host identity, applicable
global/repo safety rules, scope/exclusions, skill/artifact paths, authorized
writes and teardown, remaining budgets, return fields, and stop conditions.
For repairs include a compact handoff with prior results and current custody.
No secret-bearing prompt or artifact may be copied into a brief.

Execute only the assigned phase in the named checkout; no delegation, child
agents/threads, forks, branch changes, alternate checkout, push/PR/merge/deploy,
or independent delivery. Read the supplied applicable skills/rules before work.
Tools being available do not authorize their use outside this assignment.
Destructive operations still require Kyle's explicit approval for the exact
operation; stop on missing authority, protected/scope/behavior changes, unknown
writer custody, wrong environment, or unexpected product identity drift.

Return actual results and referenced evidence, not invented success. Include
assigned run/attempt, checkout/branch, baseline/final identity, completion or
blocker, artifact paths, and whether custody is relinquished. Report any child
threads or outstanding commands/work; do not claim completion while they run.
Give the final answer to the parent through the normal final response; with
`parent-notifications auto`, do not send duplicate completion messages.

## Review/audit

Entirely read-only: do not write files, edit source/tests/config, commit, run
mutating checks/services, or create evidence files. Read assigned artifacts and
return the answer; the coordinator saves it. Bash is for read-only inspection,
not blanket write authority. Record missing/inaccessible evidence honestly.

- **Default review:** read the absolute `code-critique/SKILL.md` path; inspect
  the full intended comparison/base, not only a clean worktree. Return
  **Risk: N/10**, findings verbatim, concrete paths/lines/evidence and fixes.
- **Named review:** use Gate's named rubric, not numeric score conversion;
  label every finding Critical/High/Medium/Low and include fixes/merge verdict.
- **Audit:** open the exact saved charter/revision and actual artifacts, including
  all-green and blocked execution records. Spot-check cheap read-only checks
  when feasible. Verify pre/post product identity and untracked non-source
  disposable exclusions. Return cause `code` / `QA-evidence` /
  `runtime-capability` / `clean`, concrete evidence gaps, and required corrections.
  Neither a green QA summary nor runtime acceptance replaces this audit.

## QA only

Read the supplied absolute planning skill and E2E test-plan reference. Own
planning plus execution in one attempt, with no extra planner or ready-charter
approval roundtrip. Do not edit product source/tests/shipping config or commit.

Write authority covers only the assigned plan file under `~/Workbench/Plans/`
(creating that directory if needed), assigned evidence directory, and explicitly
charter-approved untracked non-source disposables. Tools do not widen that scope.
Before product checks/services, save, read back, and validate the charter with
acceptance coverage, grounded standard commands or explicit gaps, stable IDs,
evidence, permissions, exclusions, disposables, and approved teardown. Missing
charter, blocking gaps, or unsafe teardown authority → blocked, no unsafe run.

For a fresh follow-up, open/reconcile the same saved charter on the current
baseline, increment Revision/Updated without narrowing coverage or changing
retained stable IDs, validate, then execute the full charter after code changes.
Record pre-run product identity before executing and recompute after teardown:
identities must match. Preserve each check/case ID, expected/observed,
pass/fail/unverified/blocked, and evidence path. Suspected environment/tool/auth
problems become blocked records, not final runtime diagnoses.

Return charter path/revision, coverage, blockers, execution record/artifacts,
identities, teardown outcome, and custody. QA makes no final Gate verdict.
Even failed/partial execution records must be preserved for independent audit.

## Developer only

Read the supplied absolute developer skill. Repair only assigned findings in
the named feature checkout; preserve unrelated work and all global safeguards.
For each finding return `fixed` or `disputed` with concrete evidence. Run affected
checks, commit only intended fixes, and return full HEAD, changed files, exact
commands/results, remaining blockers, and no staged changes. A dispute does not
waive independent adjudication or reset the shared repair budget.

Do not author the E2E charter/evidence or run your own Gate. Gate owns separate
delta-review, QA, audit, report, and publication. Relinquish writing on return;
completed Gate-owned attempts will be archived, and further repairs use a fresh
worker. The external ticket-owned developer instead follows its owner's
existing transport/custody contract. Runtime acceptance is not Gate approval.
