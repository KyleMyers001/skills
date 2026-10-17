---
name: handoff
description: >-
  Produce a copyable continuation prompt for a fresh Pi session. Use only when
  Kyle explicitly requests a session handoff, not for routine summaries,
  compaction, or agent-to-agent task delegation.
disable-model-invocation: true
---

# Handoff

Preserve the intent, decisions, and working state a fresh agent cannot cheaply
recover. Produce the handoff; do not continue the underlying task.

## Gather

1. Treat arguments after `/skill:handoff` as the next session's focus. Otherwise,
   use the current task and Kyle's latest request. A new focus is not approval
   to implement it.
2. Use the conversation and existing artifacts first. Preserve decisions and
   rationale, rejected approaches, blockers, and any corrections to earlier
   assumptions. Reference plans, issues, PRs, and reports instead of duplicating
   them. Do not repeat project instructions or the transcript.
3. Check the current working directory. In a Git repository, use read-only
   checks such as `git status --short --branch` and `git diff --stat` to confirm
   the branch and relevant uncommitted state, including untracked files. Note
   unrelated work that must be preserved. Outside Git, omit Git details.
4. Record verification already performed: commands, results, and what remains
   untested. Distinguish observed state from assumptions and stale claims;
   mark unavailable information unknown. Do not rerun a test suite just to
   prepare a handoff.
5. Include known active agents or processes only when relevant, with their
   identifiers, purpose, and last observed state. Never imply they survived
   the session transition without checking.

## Write

Return one fenced Markdown block containing a self-contained continuation
prompt, with no commentary outside it. Use these headings; omit optional
sections that add nothing:

- **Goal and latest request:** objective, next-session focus, and relevant
  constraints.
- **Workspace:** absolute repository/worktree path, branch, relevant changed
  files, and any existing artifact pointers needed to locate the work.
- **Current state and verification:** done, partial, not started, test evidence,
  and uncertainties.
- **Decisions and traps:** rationale and failed approaches worth preserving.
- **Read first:** a small, prioritized set of files or artifacts, with a reason
  for each. Include relevant skills to load, without granting permission to
  run an opt-in workflow.
- **Next step and authorization:** the immediate next step, remaining blockers,
  and whether Kyle approved implementation, requested discussion, or asked the
  next agent to wait. If authorization is unclear, require clarification before
  changes. Do not turn pending approval into permission.

Address the fresh agent directly: follow the destination's project instructions,
read the priority pointers, and verify the relevant working state before acting
within the recorded authorization. Neither blindly trust the handoff nor repeat
all prior investigation. Do not require waiting when continuation is authorized.

Redact secrets and sensitive personal data. Reference credential locations only
when necessary; never include their values.

## Delivery

Default to inline output only. In Pi, Kyle can use `/copy`, then `/new`, then
paste. Do not switch sessions, copy to the clipboard, or launch agents yourself.

Save a file only on request. Use the requested path, or a new uniquely named
file under `~/Workbench/` by default. Do not overwrite an existing file without
confirmation. Save the block's contents and include the absolute saved path
inside the handoff so the response remains a single copyable block.
