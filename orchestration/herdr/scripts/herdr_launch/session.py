"""Map an agent to its transcript and extract a candidate final answer."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

from .client import COMMAND_TIMEOUT
from .process import run_bounded


SESSION_VALUE_MAX = 4096
EXTRACTOR = Path(__file__).resolve().parents[1] / "extract-response.py"
# Claude reports a session id; its transcript is <projects>/<project>/<id>.jsonl.
CLAUDE_SESSION_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


def session_mapping(agent: object, kind: str) -> dict | None:
    session = agent.get("agent_session") if isinstance(agent, dict) else None
    if (isinstance(session, dict) and session.get("kind") in ("path", "id")
            and isinstance(session.get("value"), str)
            and 0 < len(session["value"]) <= SESSION_VALUE_MAX
            and session.get("agent") == kind):
        return {key: session[key] for key in ("agent", "kind", "value")}
    return None


def transcript_path(session: dict) -> Path | None:
    if session["kind"] == "path":
        return Path(session["value"])
    if (session["agent"] != "claude"
            or not CLAUDE_SESSION_ID.fullmatch(session["value"])):
        return None
    projects = Path.home() / ".claude" / "projects"
    matches = list(projects.glob(f"*/{session['value']}.jsonl"))
    return matches[0] if len(matches) == 1 else None


def extract_answer(session: dict | None, kind: str) -> str | None:
    """Return candidate text only after a settled wait. Never raise."""
    if kind not in ("pi", "claude") or not session:
        return None
    path = transcript_path(session)
    if path is None or not path.is_file():
        return None
    try:
        result = run_bounded(
            [sys.executable, str(EXTRACTOR), "--kind", kind, str(path)],
            timeout=COMMAND_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if result.returncode or result.stdout_truncated or not result.stdout:
        return None
    return result.stdout
