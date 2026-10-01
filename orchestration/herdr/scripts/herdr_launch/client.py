"""Bounded Herdr CLI calls against the Subagents session."""

from __future__ import annotations

import json
import subprocess
import time

from .errors import HerdrFailure, LaunchUnavailable, parse_herdr_failure
from .process import JSON_STDOUT_LIMIT, CommandResult, run_bounded


SESSION = "Subagents"
COMMAND_TIMEOUT = 20
SHELL_READY_TIMEOUT = 5
SHELL_POLL_INTERVAL = 0.1


class HerdrClient:
    def __init__(self):
        # Set before invoking a mutation: even a timeout or malformed reply can
        # leave a workspace, pane, or agent behind.
        self.mutation_attempted = False

    def _run(self, argv: list[str], *, mutation: bool, timeout: float,
             max_stdout: int) -> CommandResult:
        if mutation:
            self.mutation_attempted = True
        try:
            return run_bounded(["herdr", "--session", SESSION, *argv],
                               timeout=timeout, max_stdout=max_stdout)
        except subprocess.TimeoutExpired:
            raise LaunchUnavailable("Herdr command timed out.") from None
        except (OSError, ValueError):
            raise LaunchUnavailable(
                "Herdr command could not be executed/read.") from None

    def json_command(self, argv: list[str], response_type: str | None = None,
                     *, mutation: bool = False,
                     timeout: float = COMMAND_TIMEOUT) -> dict:
        """Return the top-level object, or its `result` of `response_type`."""
        result = self._run(argv, mutation=mutation, timeout=timeout,
                           max_stdout=JSON_STDOUT_LIMIT)
        if result.stdout_truncated:
            raise LaunchUnavailable("Herdr returned an oversized response.")
        if result.returncode:
            raise parse_herdr_failure(result)
        try:
            data = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise LaunchUnavailable("Herdr returned unreadable JSON.") from None
        if not isinstance(data, dict):
            raise LaunchUnavailable("Herdr returned an unsupported response.")
        if "error" in data:
            raise parse_herdr_failure(result)
        if response_type is None:
            return data
        payload = data.get("result")
        if not isinstance(payload, dict) or payload.get("type") != response_type:
            raise LaunchUnavailable(
                "Herdr returned an unsupported response type.")
        return payload

    def text_command(self, argv: list[str], *, max_bytes: int,
                     timeout: float = COMMAND_TIMEOUT) -> tuple[str, bool]:
        """Return text and whether the read hit `max_bytes`."""
        result = self._run(argv, mutation=False, timeout=timeout,
                           max_stdout=max_bytes)
        # A killed reader is success only when we kept a capped prefix.
        if result.stdout_truncated and result.stdout:
            return result.stdout, True
        if result.returncode:
            raise parse_herdr_failure(result)
        return result.stdout, False

    def start_when_ready(self, argv: list[str], timeout: float) -> dict:
        """Start an agent, retrying only verified pre-start shell rejections.

        Only used for the pane just created by this launch. The same name, pane
        and arguments survive every rejected attempt; never recreate.
        """
        deadline = time.monotonic() + SHELL_READY_TIMEOUT
        while True:
            try:
                return self.json_command(argv, "agent_started", mutation=True,
                                         timeout=timeout)
            except HerdrFailure as error:
                remaining = deadline - time.monotonic()
                if not error.pre_start_busy or remaining <= 0:
                    raise
                time.sleep(min(SHELL_POLL_INTERVAL, remaining))
                if time.monotonic() >= deadline:
                    raise
