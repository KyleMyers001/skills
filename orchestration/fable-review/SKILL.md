---
name: fable-review
description: >-
  Launch Fable with high effort through Claude Code in Herdr's Subagents session
  and return its review in full. Use for /skill:fable-review or requests for a
  Fable review; use astra-review for Astra.
---

# Fable Review

Use **Fable**, **high** effort, and **Claude Code** with Kyle's subscription.
Do not route reviews through OpenRouter or API billing. This is a trusted-agent
workflow, not an enforced read-only sandbox: Bash remains available for
inspection/tests.

## Runbook

1. Load [Herdr](../herdr/SKILL.md) unless already read for this invocation;
   follow its discovery, targeting, waiting, and safety rules. Reuse its checks,
   not a second checklist. Every Herdr command needs `--session Subagents`.
2. Establish absolute target path, branch, base/commit range, and included
   staged/unstaged/untracked files. Record `git status --short --branch` and
   this digest before review:

   ```bash
   {
     git rev-parse HEAD
     git status --porcelain=v2 -z --untracked-files=all
     git diff --binary HEAD
     while IFS= read -r -d '' path; do
       printf '%s\0' "$path"
       git hash-object -- "$path"
     done < <(git ls-files --others --exclude-standard -z)
   } | shasum -a 256
   ```

   This covers tracked/nonignored untracked content, not ignored/generated
   files. For non-Git targets, hash reviewed files with `shasum -a 256`.
3. Check `claude --help` for `--model`, `--effort`, and `--disallowed-tools`
   unless already checked for this invocation.
   Check `claude auth status` in the launch environment; require subscription
   authentication, not API-key or third-party billing. If unavailable or
   ambiguous, stop and ask Kyle; never silently substitute a harness/provider.
   Do not print credentials.

   Discover the matching workspace and a unique name; create an unfocused
   background tab. Start one **fresh** Claude conversation; never reuse an
   existing conversation:

   ```bash
   herdr --session Subagents agent start <unique-name> \
     --kind claude --pane <discovered-pane-id> -- \
     --model fable --effort high --disallowed-tools Edit,Write,NotebookEdit
   ```

   Use Herdr's short startup `get`/`read` once for directory and launch errors
   before prompting. If the startup display visibly contradicts the requested
   model, effort, or directory, stop and ask Kyle before submitting the task.
   Missing display metadata is not a new gate. Do not revalidate transcript
   metadata after submission. Never bypass Claude permissions.
   Tool exclusions are defense in depth, not a Bash security boundary.
4. Set `agent_name` to the launched agent's name. Add step 2's scope below.
   Run this directly in Bash, not inside another `sh -c`/`eval` string. The
   quoted heredoc preserves prompt text literally. Avoid `$(...)` around the
   heredoc: macOS Bash 3.2 can misparse embedded quotes there. `read` returns
   nonzero at EOF; `|| :` handles that expected status.

   ```bash
   IFS= read -r -d '' prompt <<'REVIEW_PROMPT' || :
   I am an orchestrating agent working on Kyle's behalf. This is an assigned
   review to carry out; reply to me, not Kyle.
   Review the current work in this repository fully and carefully as a neutral
   senior developer reviewing a junior developer's changes.

   Target: <absolute repository/worktree path>
   Comparison: <base, commit range, or working-tree scope>
   Include: <staged, unstaged, untracked, or named files>

   Inspect the relevant code and tests; work extremely hard and go deep enough
   to surface critical or serious issues. Do not modify repository files or
   create review artifacts. Produce a detailed but concise final report in
   plain English. State whether the code is good and safe to merge into
   production, and explain how to fix serious or critical issues. Follow all
   repository and global instructions.
   REVIEW_PROMPT
   herdr --session Subagents agent prompt "$agent_name" "$prompt" \
     --wait --timeout 600000
   ```

   Follow Herdr's submission recovery rules: a confirmed local failure before
   Herdr was invoked can be fixed without approval; it is still the first
   submission. For blocked, unknown, stalled, or timed-out results, inspect
   `agent get` and `agent read`. Never blindly resend or overwrite composer text;
   uncertain receipt is not permission to duplicate work. For permission
   prompts, ask Kyle; after resolution, use `agent wait`.
5. Use Herdr's Return Output path directly after waiting: mapped text extraction
   first, bounded terminal fallback only if needed. Never load raw JSONL, require
   a handoff validator, or read the full report by both routes. Return it in full,
   without rewriting, summarizing, or dropping findings. Minimally redact secrets
   and disclose redactions. Idle alone is inconclusive: inspect for a final report
   or a genuine agent failure. Surface launch, authentication,
   permission, usage/rate-limit, timeout, or agent failures honestly; label
   partial output.

   Use the optional [retrieval notes](references/persisted-output.md) only for
   mapping/helper details. If a report is already visible, return it rather than
   extracting another copy. Parser errors, changed response format, wrappers,
   missing metadata, and bookkeeping failures must not block an accessible
   report. Return the available terminal response with any rendering/truncation
   limitation; do not require exact prompt matching or persisted-output validation.

   Inspect/read/wait on the same agent. Without Kyle's explicit approval,
   never restart, re-prompt, launch another reviewer, or ask it to copy or
   regenerate output to repair bookkeeping or retrieve a report.
6. Repeat step 2's status/digest. Report changes with a bounded status/diff,
   without attributing them to an actor or reverting them. If status/digest
   collection fails, disclose it without withholding the report or rerunning.
   Do not clean up, close the tab, or stop the agent without Kyle's explicit
   approval.

Use another harness or provider only if Kyle explicitly requests it. Preserve
Fable/high effort, the same scope, and global safeguards.
