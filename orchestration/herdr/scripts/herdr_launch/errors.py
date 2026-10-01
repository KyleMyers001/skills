"""Launch failures whose messages are locally authored and bounded."""

from __future__ import annotations

import json
import re

from .process import CommandResult


ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class LaunchUnavailable(Exception):
    """A bounded, locally authored diagnostic safe to return to the caller."""

    def __init__(self, message: str, code: str = "launch_unavailable"):
        super().__init__(message)
        self.code = code


class HerdrFailure(LaunchUnavailable):
    def __init__(self, herdr_code: str | None, pre_start_busy: bool = False):
        super().__init__("Herdr command failed; inspect the known target.",
                         "herdr_error")
        self.herdr_code = herdr_code
        self.pre_start_busy = pre_start_busy


def parse_herdr_failure(result: CommandResult) -> HerdrFailure:
    """Keep only a bounded code, never upstream messages, argv, or raw output."""
    code, pre_start_busy = None, False
    outputs = [value.strip() for value in (result.stdout, result.stderr)
               if value.strip()]
    if len(outputs) != 1:
        return HerdrFailure(code)
    try:
        data = json.loads(outputs[0])
    except (ValueError, TypeError):
        return HerdrFailure(code)
    if not isinstance(data, dict) or "result" in data:
        return HerdrFailure(code)
    error = data.get("error")
    if not isinstance(error, dict) or not isinstance(error.get("code"), str):
        return HerdrFailure(code)
    if ERROR_CODE.fullmatch(error["code"]):
        code = error["code"]
    # v0.8.0 emits this exact stderr envelope before registering an agent or
    # writing input. Never infer it from error prose.
    pre_start_busy = (
        code == "agent_pane_busy" and result.returncode == 1
        and not result.stdout.strip() and bool(result.stderr.strip())
        and data.get("id") == "cli:agent:start"
        and isinstance(error.get("message"), str)
    )
    return HerdrFailure(code, pre_start_busy)
