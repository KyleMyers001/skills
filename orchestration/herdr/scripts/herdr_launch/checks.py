"""Input validation, live-evidence checks, and the pre-submit startup gate."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import uuid

from .errors import LaunchUnavailable


AGENT_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
NAME_PREFIX_MAX = 19
NAME_ATTEMPTS = 10
PROMPT_CHARACTERS = 200_000
WAIT_TIMEOUT_MAX = 3_600_000
STARTUP_LINES = 80
STARTUP_CHARACTERS = 8000
# Worst-case UTF-8 for the character cap. The read stops here and kills the
# CLI; Herdr's --lines flag does not bound a single unwrapped line.
STARTUP_BYTES = STARTUP_CHARACTERS * 4
# Mechanical blockers only. A normal banner that omits the model is not a
# contradiction, and this is not a semantic review of the startup text.
STARTUP_BLOCKERS = (
    "authentication required",
    "not authenticated",
    "login required",
    "not logged in",
    "permission required",
    "approval required",
    "human draft",
    "unsent draft",
    "wrong model",
)


def identifier(value: object) -> bool:
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def directory(value: object) -> Path:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise LaunchUnavailable("Missing or invalid live directory evidence.")
    try:
        return Path(value).resolve()
    except (OSError, ValueError, RuntimeError):
        raise LaunchUnavailable(
            "Directory evidence could not be resolved.") from None


def records(result: dict, field: str) -> list[dict]:
    values = result.get(field)
    if not isinstance(values, list) or any(
            not isinstance(value, dict) for value in values):
        raise LaunchUnavailable("Unsupported discovery response.")
    return values


def validate_args(args: argparse.Namespace) -> None:
    requested = args.name if args.name is not None else args.name_prefix
    if not AGENT_NAME.fullmatch(requested):
        raise LaunchUnavailable(
            "Name/prefix must match [a-z][a-z0-9_-]{0,31}.")
    if not identifier(args.kind):
        raise LaunchUnavailable("Kind must be a short identifier.")
    if args.name_prefix is not None and len(args.name_prefix) > NAME_PREFIX_MAX:
        raise LaunchUnavailable("Name prefix must be at most 19 characters.")
    if args.workspace is not None and not identifier(args.workspace):
        raise LaunchUnavailable("Workspace must be a discovered identifier.")
    if not 3000 < args.timeout <= 300000:
        raise LaunchUnavailable("Startup timeout must be 3001–300000 ms.")


def choose_name(name: str | None, prefix: str | None,
                existing: set[str]) -> str:
    if prefix is None:
        if name in existing:
            raise LaunchUnavailable("Agent name already exists.", "name_exists")
        return name
    # Random suffixes avoid routinely colliding with concurrent launchers.
    # Check the current registry too; never retry an actual start on collision.
    for _ in range(NAME_ATTEMPTS):
        candidate = f"{prefix}-{uuid.uuid4().hex[:12]}"
        if candidate not in existing:
            return candidate
    raise LaunchUnavailable("Could not generate an unused name.",
                            "name_generation_failed")


def load_prompt(args: argparse.Namespace) -> str | None:
    if (args.wait_timeout is not None and args.prompt is None
            and args.prompt_file is None):
        raise LaunchUnavailable("Wait timeout requires a prompt.")
    if (args.wait_timeout is not None
            and not 0 < args.wait_timeout <= WAIT_TIMEOUT_MAX):
        raise LaunchUnavailable("Wait timeout must be 1–3600000 ms.")
    text = args.prompt
    if args.prompt_file is not None:
        path = Path(args.prompt_file)
        if not path.is_file():
            raise LaunchUnavailable("Prompt file is missing.")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise LaunchUnavailable("Prompt file could not be read.") from None
    if text is None:
        return None
    if not text.strip():
        raise LaunchUnavailable("Prompt must not be empty.")
    if len(text) > PROMPT_CHARACTERS:
        raise LaunchUnavailable("Prompt exceeds 200000 characters.")
    return text


def trim_startup(text: str, hit_cap: bool) -> tuple[str, bool]:
    """Bound startup text and report whether anything may be missing."""
    lines = text.splitlines(keepends=True)
    truncated = (hit_cap or len(lines) >= STARTUP_LINES
                 or len(text) > STARTUP_CHARACTERS)
    return "".join(lines[:STARTUP_LINES])[:STARTUP_CHARACTERS], truncated


def submission_block(startup: str, truncated: bool) -> str | None:
    if not startup.strip():
        return "startup_empty"
    if truncated:
        return "startup_truncated"
    lowered = startup.casefold()
    if any(phrase in lowered for phrase in STARTUP_BLOCKERS):
        return "startup_unsafe"
    return None
