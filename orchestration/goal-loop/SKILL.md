---
name: goal-loop
description: >-
  Create Ralph loop prompts from a goal, requirements, or an existing PRD when
  Kyle asks. Produces small verifiable tasks and persistent progress artifacts;
  not for launching loops, ordinary implementation, or closed-loop workflows.
disable-model-invocation: true
---

# Ralph Prompt Authoring

Produce a prompt package another AI can execute through a Ralph runner. Do not
implement the goal, launch agents, or install a runner while drafting.

Ralph repeatedly invokes an agent, typically with fresh context. The prompt,
task list, code, and progress carry state between iterations. An agent ending
its turn is not the goal finishing. A prompt alone cannot provide persistence.

## Ground the Goal

1. Establish the objective, repository/worktree, intended branch, scope,
   constraints, and final acceptance criteria. Read project instructions,
   relevant code, and existing plans/PRDs. Ask about consequential unknowns.
2. Discover exact validation commands from the project. Use its typecheck,
   tests, lint, and build where applicable; do not invent commands or require
   TypeScript checks in a Python project. Include browser verification with
   the `agent-browser` skill for UI changes.
3. Identify the intended agent/runner if supplied. Verify its input format,
   completion signal, persistence, and pause/stop controls before providing
   launch instructions. Otherwise mark the runner unselected; drafting can
   proceed, but do not claim the package is runtime-integrated. Pi reading a
   prompt does not itself create a persistent loop or a `/goal` command.

## Shape the Tasks

- Split work into independently verifiable tasks small enough for one fresh
  context window. A task too large to explain in two or three sentences usually
  needs splitting. One iteration tackles one task, not the entire backlog.
- Order dependencies first; use stable IDs and explicit prerequisite IDs. No
  task may depend on a later task. A ready task has all prerequisites passing.
- Give each task observable acceptance criteria and exact checks. Avoid vague
  criteria such as "works correctly." Define final integration checks as well.
- Initialize every task with `passes: false` and empty `notes`. Existing work
  must be checked rather than assumed absent or accepted without evidence.

## Write the Package

Create `~/Workbench/Goals/YYYY-MM-DD-HHMMSS-topic/` with these files. Check for
collisions and choose a new directory; never overwrite prior runs. Honor a
requested destination without overwriting existing files unless approved.

- **`prompt.md`:** read and customize [the template](assets/prompt.md). Replace
  placeholders with absolute artifact/repository paths, constraints, read-first
  files, and exact final checks. Keep its authorization warning intact.
- **`tasks.json`:** use the shape below unless a selected runner requires its
  own schema. This generic format is not a drop-in snarktank integration.
- **`progress.md`:** initialize with `Status: draft`, the objective, and headings
  for reusable project learnings and append-only iteration records. Records
  capture task ID, changes, commands/results, blockers, and the next step.

```json
{
  "tasks": [
    {
      "id": "US-001",
      "title": "One focused change",
      "description": "Concrete outcome and purpose",
      "dependsOn": [],
      "acceptanceCriteria": ["Observable behavior"],
      "checks": ["EXACT_PROJECT_COMMAND"],
      "passes": false,
      "notes": ""
    }
  ]
}
```

Record only user-selected budgets or verified runtime limits, not an invented
iteration/time cap. Keep briefs outside auto-loaded skill/prompt/instruction
locations. Never name them `AGENTS.md`, `CLAUDE.md`, or `SKILL.md`.

## Verify and Deliver

Parse the JSON; check unique IDs, dependency order, task size, criteria, commands,
artifact links, and template substitutions. Walk through a failed check, an
ordinary fresh iteration, final completion, and an unauthorized reader.

Return the absolute prompt path, a short package summary, and unresolved runtime
requirements. Distinguish static checks from execution. Explicit run approval
covers runner-managed fresh iterations; reading files or recorded approval alone
cannot authorize an independent run. Do not launch anything as part of delivery.

Adapted from [snarktank's task-shaping guidance][snarktank] and
[Geoffrey Huntley's Ralph technique][ralph].

[snarktank]: https://github.com/snarktank/ralph/blob/main/skills/ralph/SKILL.md
[ralph]: https://ghuntley.com/ralph/
