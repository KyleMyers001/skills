# Agent skills

A collection of reusable `SKILL.md` instructions for coding agents. Each
skill defines when to use a workflow and how to carry it out. Some are
general-purpose; others assume Kyle's local tools, accounts, or directory
layout. Read a skill's requirements before using it elsewhere.

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

## Development and installation

Keep two independent checkouts: `~/Development/skills` is the development
checkout, while `~/.agents/skills` is the installed checkout. The installed
path is a real Git clone, not a symlink to the development checkout. On a new
device where `~/.agents/skills` does not already exist, install it with:

```sh
git clone git@github.com:KyleMyers001/skills.git "$HOME/.agents/skills"
```

Inspect an existing installation before cloning; do not overwrite it. Make
changes in the development checkout on a feature branch, open a PR, and wait
for approval before merging. Once changes have landed on the remote default
branch, update a clean installed checkout with:

```sh
git -C "$HOME/.agents/skills" pull --ff-only
```

Review remote skill changes before pulling: skills are instructions that can
change agent behavior. Restart Pi or use `/reload` in an active session to pick
up updates. Do not edit the installed checkout or pull unpublished development
branches into it.

Each skill lives in its own directory with a `SKILL.md` containing a name,
description, and instructions. Some also include `references/` files used by
the workflow. Agents that discover skills recursively can find nested skills
such as `orchestration/herdr`; consult your agent's documentation for its
specific discovery rules.

These files are instructions, not bundled executables. Tools such as
`agent-browser`, `acli`, Herdr, and `no-mistakes axi` must be installed and
configured separately where required. The descriptions in each `SKILL.md`
specify when a skill should activate; in particular, opt-in workflows should
not be triggered merely because their topic comes up.
