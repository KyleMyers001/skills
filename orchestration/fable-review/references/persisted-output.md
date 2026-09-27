# Fable Output Retrieval (Optional)

Use only for mapping/helper details. Follow [Herdr's Return Output](../../herdr/SKILL.md#return-output);
this is a retrieval aid, never a validator or a prerequisite to delivering a
visible report. Do not read the full terminal report before extracting it.

Use `result.agent.agent_session` from the same agent's `get`: a `path` value
identifies the Claude transcript; an `id` can locate the exact `<id>.jsonl`
under `~/.claude/projects/`. Search filenames for that ID only. If the mapping
is unavailable or ambiguous, use the shared terminal fallback, not a guessed
file or raw transcript read.

Resolve `../../herdr/scripts/extract-response.py` relative to this reference
directory and invoke its absolute path:

```bash
python3 "<absolute-herdr-skill-dir>/scripts/extract-response.py" \
  --kind claude "$session_path"
```

The helper collects the last assistant text message, including records sharing
its message ID. Success is a candidate, not proof of completion or task identity.
Changed handoff prose, `<pasted_content>` wrappers, missing model/effort metadata,
and unfamiliar stop markers are not validation failures. Do not add checks for
prompt equality, a fixed user-message count, or a particular report schema.

If extraction is unavailable, return the same agent's available terminal report
and disclose rendering, partial output, or uncertainty. Preserve findings,
minimally redact secrets, and keep bookkeeping warnings outside the report.
Without Kyle's explicit approval, never restart, re-prompt, launch another
reviewer, or ask it to copy/regenerate output to repair retrieval.
