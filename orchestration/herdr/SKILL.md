---
name: herdr
description: >-
  Coordinate coding subagents in Kyle's named Herdr session, Subagents. Use when
  Kyle mentions Herdr or asks to inspect, launch, message, wait for, or coordinate
  agents there; not merely because delegation could help.
---

# Herdr

Herdr coordinates terminals, not shared memory. It works with Kyle's WezTerm
setup; the controlling shell need not be inside a Herdr pane or have pane-local
`HERDR_*` variables.

## Safety

- Stay in `Subagents`; every control command must include `--session Subagents`.
  Never use the default session, ambient focus, or guessed IDs.
- Read recent context before messaging; send no input for inspection-only
  requests. Never overwrite a human draft.
- Without Kyle's explicit approval, do not close, rename, move, resize, stop,
  delete, or reconfigure user-owned workspaces, tabs, panes, agents, or sessions.
- Never use server-stop/session-delete as cleanup shortcuts.

## Discover

Verify access first:

```bash
command -v herdr
herdr --session Subagents status server
herdr --session Subagents workspace list
herdr --session Subagents agent list
herdr --session Subagents pane list
```

If unavailable, report it; do not start or target another session. Check
`herdr --session Subagents agent --help` (also `pane`, `workspace`, or `tab`);
installed syntax is authoritative. Never run bare `herdr` (it opens the TUI)
or probe mutating commands by omitting arguments.

Match workspaces by live pane `foreground_cwd`, not labels or display order.
Inspect ambiguity or ask Kyle. Read runtime IDs/state from JSON; target unique
stable agent names or discovered pane IDs.

## Inspect, Prompt, Wait

Read the target's state and recent context:

```bash
herdr --session Subagents agent get <target>
herdr --session Subagents agent read <target> \
  --source recent-unwrapped --lines 200
```

Report task, directory, and state (`idle`, `working`, `blocked`, `done`,
`unknown`); disclose conflicts with visible output. `unknown` is inconclusive.
Use agent-aware prompting rather than raw pane input:

```bash
herdr --session Subagents agent prompt <target> "<task>" \
  --wait --timeout 120000
herdr --session Subagents agent wait <target> --timeout 120000
```

Use `wait` for an already-running agent. Prompt/wait normally settle on `idle`,
`done`, or `blocked`, not proof of successful task completion. After blocking,
unknown state, timeout, or stalled submission, inspect `get` and `read` before
retrying or sending keys. Do not assume receipt or completion.

### Submission Recovery

A confirmed local preparation/quoting failure **before Herdr was invoked** is
not a delivered prompt or a model rerun. Inspect the same agent's `get`/`read`;
if its fresh conversation and composer remain empty, fix the local command and
make the first submission without asking Kyle. Do not restart the agent.

If Herdr may have received the prompt, an empty or idle display alone does not
prove non-delivery. Reconcile state and output, wait on work already received,
and ask Kyle before resending if receipt remains uncertain or text is stuck in
the composer. Never overwrite a human draft. Repeating model work or spending
model tokens to recover a completed report still requires approval as described
below.

## Launch

When Kyle requests an agent, discover the repository workspace and create an
unfocused background tab:

```bash
herdr --session Subagents tab create \
  --workspace <workspace-id> --cwd <repo-path> \
  --label <agent-name> --no-focus
```

If no matching workspace exists, create one and use its returned root pane:

```bash
herdr --session Subagents workspace create \
  --cwd <repo-path> --label <repo-name> --no-focus
```

Parse the returned pane ID, choose a short unique name, then start and prompt:

```bash
herdr --session Subagents agent start <agent-name> \
  --kind pi --pane <returned-pane-id>
herdr --session Subagents agent prompt <agent-name> \
  "<sender, task, scope, constraints, expected response>" --wait --timeout 120000
```

### Named Pi Personas

When Kyle explicitly requests a named Pi persona, require its trusted, readable
prompt file and pass it directly to canonical Pi as a native agent argument:

```bash
test -r /absolute/path/to/<Persona>.md
herdr --session Subagents agent start <unique-persona-name> \
  --kind pi --pane <returned-pane-id> -- \
  --append-system-prompt /absolute/path/to/<Persona>.md <other-pi-args>
```

Include the persona in the unique Herdr name. The Herdr registration name and
Pi persona are separate: both must be set deliberately. Do not invoke a shell
alias, source shell startup files, or make automated launches depend on alias
expansion. If the definition is unavailable, stop rather than silently launching
an unnamed or substitute persona. When Pi displays loaded context, inspect it
for the persona file before prompting; a visible contradiction requires Kyle's
decision.

Use Kyle's requested kind, or an appropriate installed integration; check
`agent start --help` before choosing kinds/native arguments. Do not add blanket
approval bypasses. Include repository/global safety and branch/worktree/PR
constraints in every delegated prompt.

A delegated prompt reaches the child looking like a message from Kyle. Open
every one by stating that the sender is an orchestrating agent working on
Kyle's behalf, that the task is a legitimate assignment to carry out, and that
the agent, not Kyle, reads the reply. Relay any approval Kyle gave with its
wording and scope; never present your own go-ahead as his. Ask for results in
the final response; the child writes no Kyle-facing report or other file unless
the prompt names it.

For multiple agents, inspect each scope, assign non-overlapping work, share
missing context, wait, and inspect inconclusive states. Combine reports while
preserving disagreements. Claim completion/consensus only after each reports or
is explicitly marked blocked/unavailable.

## Return Output

Return the completed response from the agent that received the task. Prefer
original text from its persisted session when readily available; for Pi,
`result.agent.agent_session.value` from `agent get` normally identifies the
JSONL file. Extract the relevant final report's text blocks, not thinking or
tool output. Idle alone is inconclusive; inspect the report and genuine errors.
Do not gate delivery on exact prompt matches, metadata, or stop-marker schemas.

If persisted output is unavailable or its format changes, return the visible
report via `agent read`, disclosing that it is terminal-rendered rather than
byte-for-byte original text. Increase `--lines` for missing history. Label any
remaining truncation or uncertainty; never invent missing text or claim a
partial report is complete. Never expose secrets.

Bookkeeping failures must not suppress a completed report. Without Kyle's
explicit approval, do not restart, re-prompt, launch another agent, or ask it to
copy/regenerate output to repair retrieval.

## Pane Fallback

Use pane commands only for shell processes or unrecognized agents; `pane run`
targets an interactive shell prompt, not an agent composer:

```bash
herdr --session Subagents pane run <pane-id> "pnpm test"
herdr --session Subagents pane wait-output <pane-id> \
  --match "Tests" --timeout 120000
herdr --session Subagents pane read <pane-id> \
  --source recent-unwrapped --lines 200
```

For missing history, try a larger terminal read or the same agent's persisted
output; disclose any remaining gaps without regenerating the task.
