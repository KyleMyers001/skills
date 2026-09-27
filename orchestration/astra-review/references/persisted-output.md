# Astra Output Retrieval (Optional)

Use only for mapping/helper details. Follow [Herdr's Return Output](../../herdr/SKILL.md#return-output);
this is a retrieval aid, never a validator or a prerequisite to delivering a
visible report. Do not read the full terminal report before extracting it.

For the fresh Pi review, `result.agent.agent_session.value` from the same
agent's `get` normally identifies the JSONL file. Resolve
`../../herdr/scripts/extract-response.py` relative to this reference directory
and invoke its absolute path:

```bash
python3 "<absolute-herdr-skill-dir>/scripts/extract-response.py" \
  --kind pi "$session_path"
```

The helper returns the last assistant text blocks in order, excluding thinking
and tools. It does not prove completion or task identity. Reused/branched or
ambiguous mappings require the shared terminal fallback; never guess a file or
read raw JSONL into context. No exact prompt, metadata, or stop marker is required.

If extraction is unavailable, return the same agent's available terminal report
and disclose rendering, partial output, or uncertainty. Preserve findings,
minimally redact secrets, and keep bookkeeping warnings outside the report.
Without Kyle's explicit approval, never restart, re-prompt, launch another
reviewer, or ask it to copy/regenerate output to repair retrieval.
