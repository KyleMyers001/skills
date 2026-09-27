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

Verify access and discover only what this task needs:

```bash
command -v herdr
herdr --session Subagents status server
herdr --session Subagents workspace list
herdr --session Subagents pane list --workspace <candidate-workspace-id>
```

Match workspaces by live pane `foreground_cwd`, not labels or display order.
Use session-wide `pane list` only if scoped discovery cannot resolve the target;
use `agent list` to locate an unknown agent or choose a unique launch name.
Inspect ambiguity or ask Kyle. Read runtime IDs/state from JSON; target unique
stable agent names or discovered pane IDs.

If unavailable, report it; do not start or target another session. Check relevant
`--help` once per invocation when syntax/capabilities are not already known;
installed syntax is authoritative. Reuse loaded skills, help, and discovery
results across agents, but refresh live target state before mutations. Never
run bare `herdr` (it opens the TUI) or probe mutations by omitting arguments.

## Inspect, Prompt, Wait

Before messaging, inspect the target's state and a short recent view. Reuse an
inspection already performed for this submission; do not repeat it by checklist:

```bash
herdr --session Subagents agent get <target>
herdr --session Subagents agent read <target> \
  --source recent-unwrapped --lines 80
```

Report task, directory, and state (`idle`, `working`, `blocked`, `done`,
`unknown`); disclose conflicts with visible output. `unknown` is inconclusive.
Use agent-aware prompting rather than raw pane input:

```bash
herdr --session Subagents agent prompt <target> "<task>" \
  --wait --timeout 120000
herdr --session Subagents agent wait <target> --timeout 120000
```

Use `wait` for an already-running agent, not repeated reads of unchanged history.
Prompt/wait normally settle on `idle`, `done`, or `blocked`, not proof of successful
completion. After blocking, unknown state, timeout, or stalled submission,
inspect `get` and a short `read` before retrying or sending keys. Do not assume
receipt or completion. After a normal wait, go directly to Return Output; a
second full terminal read is not required.

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

Parse the returned pane ID and choose a short unique name. Start, perform the
short pre-prompt inspection above for directory/startup errors, then prompt:

```bash
herdr --session Subagents agent start <agent-name> \
  --kind pi --pane <returned-pane-id>
herdr --session Subagents agent get <agent-name>
herdr --session Subagents agent read <agent-name> \
  --source recent-unwrapped --lines 80
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

Use Kyle's requested kind, or an appropriate installed integration; consult
`agent start --help` before choosing unfamiliar kinds/native arguments. Do not
add blanket approval bypasses. Include repository/global safety and
branch/worktree/PR constraints in every delegated prompt.

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

Return the response from the agent that received the task. Use one retrieval
route, not terminal output followed by a second full transcript copy. If the
completed report is already visible, return it; original-byte fidelity is not
an extra gate.

**Never read raw child JSONL into model context**, including `read`, `cat`,
`tail`, or paging after truncation. Parse locally and emit only answer text,
not thinking, tools, prompts, or metadata.

### Extract First When Mapped

Use the same agent's `get` for state and `result.agent.agent_session`. For Pi,
its `value` normally identifies the JSONL path. For Claude, a `path` value is
the file; an `id` can locate the exact `<id>.jsonl` under `~/.claude/projects/`.
Search filenames for that ID only, not transcript contents. Missing/ambiguous
mapping is a reason to use terminal fallback, not guess another session.

Set `herdr_dir` to the absolute directory containing **this loaded SKILL.md**,
`session_path` to the mapped file, and `kind` to `pi` or `claude`:

```bash
python3 "$herdr_dir/scripts/extract-response.py" \
  --kind "$kind" "$session_path"
```

The bundled [Python 3 helper](scripts/extract-response.py) selects the last
assistant text, preserving text blocks and Claude records sharing its message
ID. It is **not a validator**:
no required report format, exact prompt, model metadata, or stop marker.
Success returns a candidate, not proof of completion or task ownership. Judge
the response and genuine errors alongside the known state; idle alone is not
proof. For reused/branched sessions with unclear task association, use terminal
fallback instead. For work still running without a completed response, wait.
Do not read the full terminal report again after successful extraction.

### Terminal Fallback, Not Model Retry

If Python/helper access, parsing, or format changes prevent extraction, return
the same agent's available terminal report. Start with the same target's
`agent read <target> --source recent-unwrapped --lines 80` (include
`--session Subagents`); normally expand once to cover missing history.
Further diagnostic reads need new evidence or an explicit investigation, not
unchanged polling. This bounds diagnostics, not the report: preserve the full
answer when available, paginate **extracted text only** if tool limits truncate
it, and label any remaining partial output or uncertainty. Disclose terminal
rendering rather than claiming byte-for-byte original text. Never expose secrets.

Extraction/bookkeeping failures must not suppress an accessible report or become
handoff gates. Do not add a validator to compensate for parser uncertainty.
Without Kyle's explicit approval, never restart, re-prompt, launch another
agent, or ask it to copy/regenerate output to repair retrieval. If no response
is accessible, say so; do not spend more model tokens recovering it.

## Pane Fallback

Use pane commands only for shell processes or unrecognized agents; `pane run`
targets an interactive shell prompt, not an agent composer:

```bash
herdr --session Subagents pane run <pane-id> "pnpm test"
herdr --session Subagents pane wait-output <pane-id> \
  --match "Tests" --timeout 120000
herdr --session Subagents pane read <pane-id> \
  --source recent-unwrapped --lines 80
```

Apply the same bounded fallback above for missing history; disclose remaining
gaps without regenerating the task.
