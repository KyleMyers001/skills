#!/usr/bin/env python3
"""Extract candidate assistant text, not a validated handoff or completion signal.

Only use a transcript mapped to the requested task. This does not resolve
branches or task ownership. On failure, return the same agent's terminal report;
never rerun model work to repair retrieval.
"""

import argparse
import json
import sys


class ExtractionUnavailable(Exception):
    """Text could not be read reliably; use the terminal response instead."""


def text_parts(content):
    if isinstance(content, str):
        return [content] if content else []
    if not isinstance(content, list):
        raise ExtractionUnavailable("unrecognized assistant content")
    parts = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        value = block.get("text")
        if not isinstance(value, str):
            raise ExtractionUnavailable("unreadable text block")
        if value:
            parts.append(value)
    return parts


def extract_response(path, kind):
    candidate = []
    claude_messages = {}
    with open(path, encoding="utf-8") as transcript:
        for line_number, line in enumerate(transcript, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                raise ExtractionUnavailable(
                    f"unreadable JSON at line {line_number}"
                ) from None
            if not isinstance(record, dict):
                raise ExtractionUnavailable(
                    f"unrecognized record at line {line_number}"
                )
            if record.get("type") != ("message" if kind == "pi" else "assistant"):
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                raise ExtractionUnavailable("unrecognized message")
            if kind == "pi" and message.get("role") != "assistant":
                continue
            parts = text_parts(message.get("content"))
            if not parts:
                continue
            message_id = message.get("id")
            if kind == "claude" and isinstance(message_id, str) and message_id:
                candidate = claude_messages.setdefault(message_id, [])
                candidate.extend(parts)
            else:
                candidate = parts
    if not candidate:
        raise ExtractionUnavailable("no assistant text found")
    return "".join(candidate)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("pi", "claude"), required=True)
    parser.add_argument("session_path", help="exact mapped JSONL file")
    args = parser.parse_args()
    try:
        answer = extract_response(args.session_path, args.kind)
    except (ExtractionUnavailable, OSError, UnicodeError) as error:
        # Only our own bounded messages are safe to expose, not raw records,
        # filenames, decoder details, or exception tracebacks.
        reason = (str(error) if isinstance(error, ExtractionUnavailable)
                  else "session file could not be read")
        print(f"Extraction unavailable ({reason}). Return the same agent's "
              "terminal response; do not rerun the agent.", file=sys.stderr)
        return 1
    sys.stdout.write(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
