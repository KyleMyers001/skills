---
name: astra-review
description: >-
  Launch Astra with high reasoning through Herdr's Subagents session and return
  its review in full. Use for /skill:astra-review or requests for an Astra review;
  use fable-review for Fable.
---

# Astra Review

Use **Astra**, **high** reasoning, and Pi. This is a
trusted-agent workflow, not an enforced read-only sandbox: Bash remains
available for inspection/tests.

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
3. Confirm that `pi --list-models astra` contains the exact model
   `openai-codex/gpt-6-astra`. If it is unavailable, stop; never guess or
   substitute another provider, Astra variant, or GPT 5.6 Sol Max.

   Set `herdr_dir` to the absolute directory of the loaded Herdr skill, not this
   review skill. Use the `astra-review` name prefix and launch one fresh
   conversation with the verified model. The helper discovers the workspace and
   creates an unfocused tab or workspace root; do not repeat discovery/creation
   manually. Never reuse an existing conversation:

   ```bash
   python3 "$herdr_dir/scripts/launch-agent.py" \
     --cwd "<absolute repository/worktree path>" \
     --name-prefix astra-review --kind pi -- \
     --model openai-codex/gpt-6-astra --thinking high \
     --exclude-tools edit,write
   ```

   Inspect returned `startup` once for directory and launch errors; it replaces
   the routine separate startup `get`/`read`. Empty/truncated output, changed
   state, or failures follow Herdr's inspection/recovery rules, not blind
   relaunch. If startup visibly contradicts the requested model, reasoning, or
   directory, stop and ask Kyle before submitting the task. Missing display
   metadata is not a new gate. Do not revalidate transcript metadata after
   submission. Excluding tools is defense in depth, not a Bash security boundary.
4. Set `agent_name` to the returned `name`, including its generated suffix.
   Add step 2's scope below.
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
   uncertain receipt is not permission to duplicate work.
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

Use another harness only if Kyle explicitly requests it. Preserve
`openai-codex/gpt-6-astra`, high reasoning, the same scope, and global
safeguards.
