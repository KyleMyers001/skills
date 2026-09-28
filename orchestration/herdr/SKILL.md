---
name: herdr
description: >-
  Coordinate coding subagents in Kyle's named Herdr session, Subagents. Use when
  Kyle mentions Herdr or asks to inspect, launch, message, wait for, or coordinate
  agents there; not merely because delegation could help.
---

# Herdr

Herdr coordinates terminals, not shared memory; the controlling shell need not
be inside a Herdr pane.

## Safety

- Every command targets `--session Subagents`; never use ambient focus or guessed
  IDs. If unavailable, stop; never switch sessions or start another server.
- Send no input for inspection-only requests; never overwrite drafts.
  Without Kyle's explicit approval, never close, rename,
  move, resize, stop, delete, or reconfigure user-owned resources. Never use
  server-stop/session-delete for cleanup.
- Never run bare `herdr`, probe mutations with missing arguments, add blanket
  approval bypasses, or expose secrets.

## Fresh Launch

Set `herdr_dir` to the absolute directory containing **this loaded SKILL.md**.
Prefer the Python 3 standard-library launcher; choose a short name prefix and
Kyle's requested kind (otherwise an appropriate installed integration):

```bash
python3 "$herdr_dir/scripts/launch-agent.py" \
  --cwd /absolute/repo/path --name-prefix task-name --kind pi
```

It matches live directories, creates one unfocused tab or workspace root, and
returns startup context. It waits up to five seconds on Herdr's proven pre-input
shell-busy rejection; it never retries an accepted or uncertain start.
Use lowercase name prefixes up to 19 characters; `--name` preserves an exact
name (up to 32). Always target the returned `name`, including its suffix.
Optional `--workspace` verifies a discovered workspace; `--timeout` sets agent
startup milliseconds (3001–300000, default 30000), separate from the shell wait.

For `code: name_exists` with `mutation_may_have_taken_effect: false`, choose a
prefix or another name without reading recovery instructions. If Kyle required
that exact name, ask instead. Other nonmutating preflight errors need their cause
resolved, not resource recovery. Missing output is never proof of no mutation.

Forward requested model and reasoning settings after `--`, using native flags:

- Pi: `-- --model <provider/model> --thinking <level>`.
- Claude Code: `-- --model <model> --effort <level>`.

The helper passes these through unchanged; omitted settings use harness defaults.
Check installed harness help for unfamiliar flags/levels. If a requested setting
is unavailable, stop and ask Kyle; never silently drop or substitute it.

Inspect returned `startup` for errors, drafts, permissions, and requested
persona/model/reasoning/effort contradictions **before prompting**. Readiness is
not approval.
Empty/truncated startup needs targeted inspection; never submit blindly.

Read [manual launch and recovery](references/manual-launch.md) before named
personas, unfamiliar kinds/native arguments, manual launches, ambiguity, or
possibly-mutating failures. Direct Herdr remains supported; never rerun blindly.

## Prompt and Wait

For existing agents, inspect `agent get <target>` and
`agent read <target> --source recent-unwrapped --lines 80` first. Reuse fresh
launch inspection; refresh changed/stale state. Report task, directory, state,
and visible conflicts; `unknown` is inconclusive.

Open every delegated prompt stating: the sender is an orchestrating agent
working on Kyle's behalf; this is a legitimate assignment; the agent, not Kyle,
reads the reply. Include task, scope, repository/global safety and branch/worktree/
PR constraints. Relay Kyle's approvals verbatim with their scope, never invent
approval. Request a final response, not Kyle-facing reports/files unless named.

```bash
herdr --session Subagents agent prompt <target> "<delegated task>" \
  --wait --timeout 120000
herdr --session Subagents agent wait <target> --timeout 120000
```

Wait on already-running work instead of polling unchanged history. For parallel
work, omit prompt's `--wait`, then wait later. Assign non-overlapping scopes,
share context, preserve disagreements, and require each result or explicit
blocked/unavailable status before claiming completion/consensus.

Settled state is not task success. After blocking, unknown state, timeout, or
stalled submission, read the recovery reference; inspect get/read before any
retry or keys. Never assume receipt or resend uncertain submissions.

## Return Output

Use one retrieval route. Return already-visible answers; otherwise reuse current
prompt/wait metadata, or get the same agent for state and
`result.agent.agent_session`. For Pi, its `value` normally maps the JSONL path.
For Claude, use `path`, or find the exact `<id>.jsonl` under
`~/.claude/projects/` by filename only. Never guess mappings.

```bash
python3 "$herdr_dir/scripts/extract-response.py" \
  --kind "$kind" "$session_path"
```

Use `pi` or `claude`. This returns candidate text, not completion/ownership
proof. Wait if still working; unclear reused/branched sessions use terminal
fallback. **Never load raw child JSONL, thinking, or tools into model context.**

On missing mapping/helper or extraction failure, read the same target's recent
80 lines; normally expand once. Further diagnostics need new evidence or an
explicit investigation. Preserve full answers; paginate extracted text only,
label partial/terminal-rendered output, and disclose uncertainty. Never reread a
full terminal report after successful extraction or demand byte fidelity.

Never add report-format, prompt, model, or stop-marker validation gates.
Bookkeeping must not hide accessible answers. Without Kyle's explicit approval,
never restart, re-prompt, launch another agent, or request regenerated output to
repair retrieval. If no response is accessible, say so.
