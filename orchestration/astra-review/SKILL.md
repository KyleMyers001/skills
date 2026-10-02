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
   follow its fresh-launch and safety rules. Do not add a discovery or manual
   launch checklist. Every direct Herdr command needs `--session Subagents`.
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
   review skill. Use one launcher call with the `astra-review` prefix and the
   verified model. Do not discover workspaces, create tabs, or call
   `herdr agent start` / `agent prompt` yourself. Model and reasoning flags after
   `--` are the normal path. Never reuse an existing conversation.

   When gate supplies its recorded `workspace_id`, read Herdr's workspace
   guidance and add `--workspace <recorded-id>` before `--` in the launcher call
   below, including delta/dispute reviews. Never omit it to recover from failure.
   Otherwise use the fresh-workspace default; do not read the manual launch
   reference just for an ordinary launch. For every gate review, including its
   first standalone launch, return the launcher's workspace/tab/pane IDs and name
   to the coordinator for its run log; preserve known IDs on launch failure too.
   Return these alongside the review without changing its text.

   Set the Bash tool timeout to 600000ms and the initial wait to 300000ms;
   reserve the rest for launch, verification, extraction, and subprocess cleanup.
   Write the prompt file and launch in that same call. Add step 2's scope.
   The quoted heredoc preserves prompt text literally.

   ```bash
   prompt_file=$(mktemp)
   trap 'rm -f "$prompt_file"' EXIT
   cat > "$prompt_file" << 'REVIEW_PROMPT'
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
   python3 "$herdr_dir/scripts/launch-agent.py" \
     --cwd "<absolute repository/worktree path>" \
     --name-prefix astra-review --kind pi \
     --prompt-file "$prompt_file" --wait-timeout 300000 \
     --expect-startup gpt-6-astra --expect-startup "gpt-6-astra • high" -- \
     --model openai-codex/gpt-6-astra --thinking high \
     --exclude-tools edit,write
   ```

   If `wait_timed_out` is true, the review is still running on the same agent.
   In separate Bash calls with a 600000ms timeout, repeat
   `herdr --session Subagents agent wait "<name>" --timeout 540000` while it
   returns `timeout`; then continue with step 4. Never resubmit the prompt.
   If `submitted` is false, stop and report the returned startup. Do not relaunch
   or submit the prompt yourself. If the launcher fails after creating resources,
   follow Herdr's recovery rules for that same agent; do not start another.
   Excluding tools is defense in depth, not a Bash security boundary. Missing
   display metadata beyond the required model and contiguous Pi footer
   `gpt-6-astra • high` is not a new gate. Missing either required signal blocks
   submission; unrelated `high` text is not reasoning evidence.
4. If the result includes `response`, return that text in full and skip
   extraction. Otherwise set `agent_name` to the returned `name`, including its
   suffix, and use Herdr's Return Output path: mapped text extraction first,
   bounded terminal fallback only if needed. Never load raw JSONL, require a
   handoff validator, or read the full report by both routes. Return it in full,
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
5. Repeat step 2's status/digest. Report changes with a bounded status/diff,
   without attributing them to an actor or reverting them. If status/digest
   collection fails, disclose it without withholding the report or rerunning.
   Do not close the workspace, tab, or agent without Kyle's explicit approval.

Use another harness only if Kyle explicitly requests it. Preserve
`openai-codex/gpt-6-astra`, high reasoning, the same scope, and global
safeguards.
