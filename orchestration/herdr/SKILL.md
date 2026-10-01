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
Prefer one Python 3 launcher call. It creates one unfocused workspace, labels it
with the agent name, uses that root pane, and can submit the task. It does not
add a tab to an existing workspace unless `--workspace` is passed. It never
closes resources.

```bash
python3 "$herdr_dir/scripts/launch-agent.py" \
  --cwd /absolute/repo/path --name-prefix task-name --kind pi \
  --prompt-file /absolute/prompt.txt --wait-timeout 120000 -- \
  --model <provider/model> --thinking <level>
```

Omit `--prompt` and `--prompt-file` to launch without submitting. `--prompt` is
a short literal; longer text belongs in a file. `--wait-timeout` is milliseconds,
1–3600000, and requires a prompt. The Bash tool timeout must exceed it plus
startup. Claude uses `--model` and `--effort` after `--`.

Those flags are the normal path. They are not a reason to read the manual or
call Herdr directly. The helper forwards them unchanged. If a requested setting
is unavailable, stop and ask Kyle; never substitute it.

Use the returned `name`, including its suffix. Prefixes are lowercase and at
most 19 characters; `--name` is exact and at most 32. It waits up to five
seconds only for Herdr's proven pre-input shell-busy rejection.

If `submitted` is false, do not relaunch or send another prompt. Inspect the
returned `startup` and ask Kyle when it shows an auth failure, permission
prompt, draft, or model contradiction. Empty or truncated startup is already
blocked. If `submitted` is true, do not read startup again. Use `response` when
present; otherwise extract after the agent settles. A blocked or missing
response is not permission to resubmit.

For `code: name_exists` with `mutation_may_have_taken_effect: false`, choose a
prefix or another name without reading recovery instructions. If Kyle required
that exact name, ask instead. Other nonmutating preflight errors need their cause
resolved, not resource recovery. Missing output is never proof of no mutation.

Read [manual launch and recovery](references/manual-launch.md) only for named
personas, an explicit `--workspace`, existing agents, or a launch that may have
mutated and failed. Do not use it to replace an ordinary fresh launch.

## Prompt and Wait

Fresh agents should already have been prompted by the launcher. For existing
agents, inspect `agent get <target>` and
`agent read <target> --source recent-unwrapped --lines 80` first. Refresh
changed or stale state. Report task, directory, state, and visible conflicts;
`unknown` is inconclusive.

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
