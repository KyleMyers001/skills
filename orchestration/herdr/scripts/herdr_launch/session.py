"""Map an agent to its transcript and extract a candidate final answer."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from .client import COMMAND_TIMEOUT
from .process import run_bounded


SESSION_VALUE_MAX = 4096
EXTRACTOR = Path(__file__).resolve().parents[1] / "extract-response.py"


def session_mapping(agent: object, kind: str) -> dict | None:
    session = agent.get("agent_session") if isinstance(agent, dict) else None
    if (isinstance(session, dict) and session.get("kind") in ("path", "id")
            and isinstance(session.get("value"), str)
            and 0 < len(session["value"]) <= SESSION_VALUE_MAX
            and session.get("agent") == kind):
        return {key: session[key] for key in ("agent", "kind", "value")}
    return None


def extract_answer(session: dict | None, kind: str) -> str | None:
    """Return candidate text only after a settled wait. Never raise."""
    if (kind not in ("pi", "claude") or not session
            or session.get("kind") != "path"):
        return None
    path = Path(session["value"])
    if not path.is_file():
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
