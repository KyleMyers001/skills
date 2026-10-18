# Ralph execution prompt: {{TITLE}}

## Authorization

Reference only until Kyle explicitly authorizes this goal and repository for a
run. Reading, reviewing, or editing these files does not authorize execution.
A saved status or historical approval is not permission to launch another run.
If no run authorization was supplied by Kyle or an authorized runner, stop and
ask before changing code or starting work.

Once explicitly launched, that authorization covers the same runner's fresh
agent iterations; do not ask for approval on every iteration. A user pause,
exhausted budget, changed scope, or independent new run requires approval to
resume. This prompt cannot grant permissions beyond repository/global rules.

## Run Contract

- Objective: {{OBJECTIVE}}
- Repository/worktree: {{REPOSITORY_PATH}}
- Branch: {{BRANCH}}
- Read first: {{READ_FIRST_PATHS}}
- Scope and constraints: {{CONSTRAINTS}}
- Task file: {{TASKS_PATH}}
- Progress file: {{PROGRESS_PATH}}
- Final acceptance criteria: {{FINAL_ACCEPTANCE_CRITERIA}}
- Final validation commands: {{FINAL_VALIDATION_COMMANDS}}
- Runner and completion protocol: {{RUNNER_AND_COMPLETION_PROTOCOL}}
- User-selected or verified runtime limits: {{RUNTIME_LIMITS_OR_NOT_CONFIGURED}}

## One Iteration

1. Confirm the working directory, branch, project instructions, and existing
   diff. Preserve unrelated and uncommitted work. Do not create a concurrent
   worker or duplicate run. Read the task file and progress, including reusable
   learnings; do not rely on conversation memory from a previous iteration.
2. Pick the first pending task whose prerequisites pass. Inspect existing code
   before implementing: the feature may already exist. If it exceeds one
   context window, split it into dependency-ordered tasks within the approved
   scope, recording why. Do not expand the goal.
3. Implement that task only, including tests and relevant documentation. No
   unrelated refactors or unapproved dependencies/ADRs. Do not delete, skip,
   weaken, or narrow tests or acceptance criteria to manufacture success.
4. Run its exact checks. For UI work, use the `agent-browser` skill to inspect
   and exercise the changed behavior; passing code checks alone is insufficient.
   Review the diff. Mark `passes: true` only when every task criterion is met
   with evidence; otherwise keep it false and record the failure and next step.
5. Persist changes to the task file and append an iteration record to progress:
   task ID, changes, commands/results, reusable learnings, remaining issues, and
   next action. Save before ending the iteration so a fresh agent can continue.
   Follow the approved Git workflow; this prompt does not authorize commits to
   main/master, direct pushes there, merges, or destructive cleanup.
6. End the iteration using the runner's verified protocol. The runner supplies
   fresh iterations while work remains; do not emit its overall completion
   signal just because this task passed or this response is finished. Do not
   implement your own shell loop, scheduler, or replacement runner.

## Completion and Blockers

A failed test is feedback for the next iteration, not automatically a reason to
abandon the goal. Keep the task pending and preserve useful failure evidence.
There is no arbitrary repair-attempt cutoff. Stop for required human decisions,
missing permissions/prerequisites, a user pause, or actual runtime limits; record
`blocked`, `paused`, or `limit-reached` and use the runner's verified stop control.
If no suitable runner/control is configured, report that rather than claiming
persistent execution. Prompt instructions are not enforced runtime controls.

When all tasks pass, run the final integration/acceptance checks and review the
complete goal diff. If checks expose more in-scope work, reopen the affected task
or add a focused follow-up; do not declare success while it remains unresolved.
Only when all criteria and final checks pass, record `complete` with evidence
and emit the runner's verified overall completion signal, if one is configured.
