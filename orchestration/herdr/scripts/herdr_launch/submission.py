"""Inspect the fresh agent's startup, then submit the prompt exactly once."""

from __future__ import annotations

from . import checks
from .client import COMMAND_TIMEOUT, HerdrClient
from .session import extract_answer, session_mapping


AGENT_STATUSES = {"idle", "working", "blocked", "done", "unknown"}
SETTLED = ("idle", "done")


def read_startup(client: HerdrClient, name: str) -> tuple[str, bool]:
    text, hit_cap = client.text_command(
        ["agent", "read", name, "--source", "recent-unwrapped",
         "--lines", str(checks.STARTUP_LINES), "--format", "text"],
        max_bytes=checks.STARTUP_BYTES)
    return checks.trim_startup(text, hit_cap)


def startup_fields(startup: str, truncated: bool) -> dict:
    return {"startup": startup, "startup_truncated": truncated,
            "startup_empty": not startup.strip()}


def blocked_result(reason: str, startup: str, truncated: bool) -> dict:
    return {"submitted": False, "submission_blocked": reason,
            **startup_fields(startup, truncated), "inspection_required": True}


def submit_prompt(client: HerdrClient, *, name: str, kind: str, prompt: str,
                  wait_timeout: int | None, agent: dict) -> dict:
    """Submit once; return state, session, and any extracted response."""
    waited = wait_timeout is not None
    argv = ["agent", "prompt", name, prompt]
    timeout = COMMAND_TIMEOUT
    if waited:
        argv += ["--wait", "--timeout", str(wait_timeout)]
        timeout = wait_timeout / 1000 + COMMAND_TIMEOUT
    result = client.json_command(
        argv, "agent_prompted", mutation=True, timeout=timeout)
    prompted = result.get("agent")
    state = agent["agent_status"]
    session = session_mapping(agent, kind)
    if (isinstance(prompted, dict)
            and prompted.get("agent_status") in AGENT_STATUSES):
        state = prompted["agent_status"]
        session = session_mapping(prompted, kind) or session
    payload = {
        "submitted": True, "submission_blocked": None,
        "startup_checked": True, "inspection_required": False,
        "state": state, "session": session, "waited": waited,
    }
    if waited and state in SETTLED:
        answer = extract_answer(session, kind)
        if answer is not None:
            payload["response"] = answer
    return payload
