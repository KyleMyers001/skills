---
name: astra-review
description: >-
  Launch Themis on Astra with high reasoning through Herdr's Subagents session
  and return its review in full. Use for /skill:astra-review or requests for an
  Astra or Themis review; use fable-review for Fable.
---

# Themis Review on Astra

Use the **Themis** persona, **Astra**, **high** reasoning, and Pi. This is a
trusted-agent workflow, not an enforced read-only sandbox: Bash remains
available for inspection/tests.

## Runbook

1. Read [Herdr](../herdr/SKILL.md) completely and follow its discovery, targeting,
   waiting, and safety rules. Every Herdr command needs `--session Subagents`.
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
   substitute another provider, Astra variant, or GPT 5.6 Sol Max. Require the
   persona definition before creating the review tab:

   ```bash
   test -r ~/Agents/agents/Themis.md
   ```

   If it is unavailable, stop; never fall back to an unnamed reviewer or another
   persona. Do not invoke the `themis` shell alias, source `~/.zshrc`, or depend
   on shell alias expansion.

   Discover the matching workspace and a short unique name prefixed with
   `themis-astra-review`; create an unfocused background tab and launch one fresh
   conversation with the discovered model. Never reuse an existing conversation:

   ```bash
   herdr --session Subagents agent start <unique-themis-name> \
     --kind pi --pane <discovered-pane-id> -- \
     --append-system-prompt ~/Agents/agents/Themis.md \
     --model openai-codex/gpt-6-astra --thinking high \
     --exclude-tools edit,write
   ```

   Inspect `agent get`/`agent read` for the correct directory, launch errors,
   and, when Pi shows its loaded context, `agents/Themis.md`. If the startup
   display visibly contradicts the requested persona, model, reasoning, or
   directory, stop and ask Kyle before submitting the task. Missing display
   metadata is not a new gate. Do not revalidate transcript metadata after
   submission. Excluding tools is defense in depth, not a Bash security boundary.
4. Set `agent_name` to the launched agent's unique Themis-based name. Add step
   2's scope below.
   Run this directly in Bash, not inside another `sh -c`/`eval` string. The
   quoted heredoc preserves prompt text literally. Avoid `$(...)` around the
   heredoc: macOS Bash 3.2 can misparse embedded quotes there. `read` returns
   nonzero at EOF; `|| :` handles that expected status.

   ```bash
   IFS= read -r -d '' prompt <<'REVIEW_PROMPT' || :
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
5. Read the completed report from that agent and return it in full, without
   rewriting, summarizing, or dropping findings. Minimally redact secrets and
   disclose redactions. Idle alone is inconclusive: inspect the response for a
   final report or a genuine agent failure. Surface launch, authentication,
   permission, usage/rate-limit, timeout, or agent failures honestly; label
   partial output.

   Prefer original text when readily available; use the optional
   [output retrieval notes](references/persisted-output.md) if needed. Otherwise
   return the visible report, disclosing terminal-rendered text or truncation.
   Transcript formatting, prompt wrappers, missing metadata, or bookkeeping
   failures must not block a completed report. Do not require exact prompt
   matching or persisted-output validation.

   Inspect/read/wait on the same agent. Without Kyle's explicit approval,
   never restart, re-prompt, launch another reviewer, or ask it to copy or
   regenerate output to repair bookkeeping or retrieve a report.
6. Repeat step 2's status/digest. Report changes with a bounded status/diff,
   without attributing them to an actor or reverting them. If status/digest
   collection fails, disclose it without withholding the report or rerunning.
   Do not clean up, close the tab, or stop the agent without Kyle's explicit
   approval.

Use another harness only if Kyle explicitly requests it. Preserve Themis,
`openai-codex/gpt-6-astra`, high reasoning, the same scope, and global
safeguards.
