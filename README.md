# Agent skills

A collection of reusable `SKILL.md` instructions for coding agents. Each skill
defines when to use a workflow and how to carry it out. Some are general-purpose;
others assume Kyle's local tools, accounts, or directory layout. Read a skill's
requirements before using it elsewhere.

## Skills

| Skill | Purpose |
| --- | --- |
| [agent-browser](agent-browser/SKILL.md) | Automate browsers and Electron apps with the `agent-browser` CLI. |
| [closed-loop](closed-loop/SKILL.md) | Run an opt-in investigate → approved plan → tests-first → repair/verify workflow. |
| [code-critique](code-critique/SKILL.md) | Review code, diffs, plans, and technical documents without editing them. |
| [developer](developer/SKILL.md) | Guide straightforward implementation, bug fixes, and refactoring. |
| [frontend-design](frontend-design/SKILL.md) | Design and build distinctive web interfaces. |
| [git-worktree](git-worktree/SKILL.md) | Manage task worktrees when the workflow calls for them. |
| [jira-acli](jira-acli/SKILL.md) | Look up Jira issues using Atlassian's `acli`. |
| [lavish](lavish/SKILL.md) | Create reviewable HTML artifacts for visual or complex responses. |
| [no-mistakes](no-mistakes/SKILL.md) | Gate changes with review, tests, lint, docs, PR, and CI checks. |
| [astra-review](orchestration/astra-review/SKILL.md) | Request an Astra review through Herdr. |
| [fable-review](orchestration/fable-review/SKILL.md) | Request a Fable review through Claude Code and Herdr. |
| [herdr](orchestration/herdr/SKILL.md) | Coordinate coding agents in a Herdr session. |
| [orchestrate-ticket](orchestration/orchestrate-ticket/SKILL.md) | Run an explicitly invoked ticket pipeline with delegated agents. |
| [planning](planning/SKILL.md) | Create grounded implementation plans. |
| [report](report/SKILL.md) | Write an Obsidian report for substantial work or on request. |
| [skill-authoring](skill-authoring/SKILL.md) | Create and refine agent skills. |

## Using a skill

Each skill lives in its own directory with a `SKILL.md` containing a name,
description, and instructions. Some also include `references/` files used by the
workflow. Place the **whole directory** in a location your agent scans for skills;
consult your agent's documentation for its discovery and invocation rules. For
example, if your agent scans `~/.agents/skills`, you can link a skill from this
checkout:

```sh
mkdir -p ~/.agents/skills
ln -s "$PWD/developer" ~/.agents/skills/developer
```

Run the example from the repository root, and use the individual skill path
(for example, `orchestration/herdr`) for nested skills. Don't replace an existing
installed skill without checking it first.

Skills are primarily instructions. Herdr also bundles
`scripts/extract-response.py`, an optional Python 3 standard-library helper that
extracts candidate answer text without loading raw transcripts into the
orchestrator. It does not validate handoffs or launch agents; extraction failures
use the same agent's terminal response, never an automatic model rerun. Keep the
whole skill directory when installing it.

Tools such as `agent-browser`, `acli`, Herdr, and `no-mistakes axi` must be installed
and configured separately where required. The descriptions in each `SKILL.md`
specify when a skill should activate; in particular, opt-in workflows should not
be triggered merely because their topic comes up.
