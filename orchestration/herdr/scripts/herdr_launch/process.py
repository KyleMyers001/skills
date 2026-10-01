"""Run a child process without buffering unbounded output."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
import threading
from typing import BinaryIO


JSON_STDOUT_LIMIT = 1_048_576
STDERR_LIMIT = 65_536
READ_CHUNK = 65_536


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    stdout_truncated: bool = False


def read_capped(stream: BinaryIO, limit: int) -> tuple[bytes, bool]:
    """Read at most `limit` bytes, plus one byte to detect overflow."""
    chunks = []
    total = 0
    try:
        while total < limit:
            chunk = stream.read(min(READ_CHUNK, limit - total))
            if not chunk:
                return b"".join(chunks), False
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks), bool(stream.read(1))
    except OSError:
        return b"".join(chunks), True


def run_bounded(argv: list[str], *, timeout: float,
                max_stdout: int = JSON_STDOUT_LIMIT) -> CommandResult:
    process = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    captured = {}

    def collect(stream, key, limit):
        data, truncated = read_capped(stream, limit)
        captured[key] = (data, truncated)
        if truncated:
            process.kill()

    threads = [
        threading.Thread(target=collect,
                         args=(process.stdout, "stdout", max_stdout)),
        threading.Thread(target=collect,
                         args=(process.stderr, "stderr", STDERR_LIMIT)),
    ]
    for thread in threads:
        thread.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)
        raise
    for thread in threads:
        thread.join(timeout=5)
    stdout, stdout_truncated = captured.get("stdout", (b"", False))
    stderr, _stderr_truncated = captured.get("stderr", (b"", False))
    return CommandResult(
        process.returncode,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
        stdout_truncated,
    )
