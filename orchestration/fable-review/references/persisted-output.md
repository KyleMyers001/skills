# Fable Output Retrieval (Optional)

Use only when original text or missing terminal history is needed. This is a
retrieval aid, not a validator or prerequisite for returning a completed report.

1. Inspect the same agent with `herdr --session Subagents agent get <name>` and
   `herdr --session Subagents agent read <name> --source recent-unwrapped --lines 200`.
   Increase `--lines` until the report's beginning is visible, ideally with the
   submitted prompt above it. If history is unavailable, disclose the gap.
2. If `result.agent.agent_session` identifies a Claude transcript, extract only
   assistant text rather than loading raw JSONL into context. A `path` value
   identifies the file; an `id` may identify a
   matching `<id>.jsonl` under `~/.claude/projects/`. If the mapping is missing
   or ambiguous, use the same agent's terminal output rather than guessing.
3. For Claude JSONL, assistant text is normally in `message.content` blocks
   whose `type` is `text`. Locate the final report for this task, preserve all
   its text blocks in order (including records sharing its message ID), and
   exclude tool output and thinking. Do not require prompt equality, a fixed
   number of user messages, model/effort metadata, or a particular stop marker.
   Claude's `<pasted_content>` wrappers do not invalidate a report.
4. If parsing or retrieval fails, return the visible report unchanged and
   disclose that it is terminal-rendered. If only part is recoverable, return
   that part with an explicit limitation; do not invent missing findings or
   call partial output complete. If no report is accessible, say so.

## Best-Effort Extraction

After observing the report, set `session_path` to this fresh review's transcript
and run this extract-only example (requires `jq`). It collects the last
assistant text message, including records sharing its ID:

```bash
jq -jrs '
  [.[] | select(.type == "assistant")] as $messages |
  ($messages | map(select(any(.message.content[]?;
    .type == "text" and (.text | length) > 0))) | last) as $final |
  (if $final.message.id then
    $messages[] | select(.message.id == $final.message.id)
  else $final end) |
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
