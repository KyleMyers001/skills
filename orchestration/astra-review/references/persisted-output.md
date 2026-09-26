# Astra Output Retrieval (Optional)

Use only when original text or missing terminal history is needed. This is a
retrieval aid, not a validator or prerequisite for returning a completed report.

1. Inspect the same agent with `herdr --session Subagents agent get <name>` and
   `herdr --session Subagents agent read <name> --source recent-unwrapped --lines 200`.
   Increase `--lines` until the report's beginning is visible, ideally with the
   submitted prompt above it. If history is unavailable, disclose the gap.
2. If `result.agent.agent_session.value` identifies a Pi session file, extract
   only assistant text rather than loading raw JSONL into context. If the mapping
   is missing or ambiguous, use the same agent's terminal output rather than
   guessing.
3. For Pi JSONL, assistant records normally have `type: "message"` and
   `message.role: "assistant"`. Locate the final report for this task and
   preserve its `message.content` text blocks in order; exclude tool output and
   thinking. Do not require prompt equality, model/reasoning events, or a
   particular stop marker. Transcript metadata is not a handoff gate.
4. If parsing or retrieval fails, return the visible report unchanged and
   disclose that it is terminal-rendered. If only part is recoverable, return
   that part with an explicit limitation; do not invent missing findings or
   call partial output complete. If no report is accessible, say so.

## Best-Effort Extraction

After observing the report, set `session_path` to this fresh review's transcript
and run this extract-only example (requires `jq`). It selects the last assistant
message containing text:

```bash
jq -jrs '
  [.[] | select(.type == "message" and .message.role == "assistant") |
    select(any(.message.content[]?;
      .type == "text" and (.text | length) > 0))] | last |
  .message.content[]? | select(.type == "text") | .text
' "$session_path"
```

Text extraction does not prove completion: the last text may be interim
commentary or partial output. Inspect it alongside the same agent's terminal
state; a normal stop marker may support completion but is not required. If the
session was reused/branched, do not assume the last text belongs to this task.
On errors, empty output, or ambiguity, use the terminal fallback above.

Without Kyle's explicit approval, never launch another review or ask the agent
to copy/regenerate its report to work around retrieval problems. Preserve
findings, minimally redact secrets, and disclose redactions. Bookkeeping
warnings belong outside the quoted report.
