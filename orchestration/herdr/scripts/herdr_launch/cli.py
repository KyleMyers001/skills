"""Command-line interface: parse arguments and print one JSON record."""

from __future__ import annotations

import argparse
import json
import signal
import sys

from . import __doc__ as DESCRIPTION
from .checks import identifier
from .errors import LaunchUnavailable
from .launcher import Launcher


TERMINATING_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


class Terminated(BaseException):
    """Raised from a signal handler so the error record is still printed."""


def terminate(signum, frame):
    raise Terminated


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse launcher flags; everything after `--` goes to the agent."""
    native = []
    if "--" in argv:
        boundary = argv.index("--")
        argv, native = argv[:boundary], argv[boundary + 1:]
    parser = argparse.ArgumentParser(description=DESCRIPTION,
                                     allow_abbrev=False)
    parser.add_argument("--cwd", required=True,
                        help="absolute existing directory")
    naming = parser.add_mutually_exclusive_group(required=True)
    naming.add_argument("--name",
                        help="exact agent name; collisions fail unchanged")
    naming.add_argument("--name-prefix",
                        help="generate an unused name from this prefix")
    parser.add_argument("--kind", required=True,
                        help="Herdr-supported canonical kind")
    parser.add_argument("--workspace",
                        help="add one tab to this workspace; verified live")
    parser.add_argument("--timeout", type=int, default=30000,
                        help="startup timeout in ms (3001–300000; default 30000)")
    prompt = parser.add_mutually_exclusive_group()
    prompt.add_argument("--prompt",
                        help="submit this text after a safe startup")
    prompt.add_argument("--prompt-file",
                        help="UTF-8 file to submit after a safe startup")
    parser.add_argument("--wait-timeout", type=int,
                        help="after submit, wait this many ms (1–3600000)")
    parser.add_argument("--expect-startup", action="append", metavar="TEXT",
                        help="refuse to submit unless startup contains TEXT "
                             "(case-insensitive; repeatable)")
    args = parser.parse_args(argv)
    args.expect_startup = args.expect_startup or []
    args.agent_args = native
    return args


def error_record(launcher: Launcher, error: BaseException) -> dict:
    if isinstance(error, LaunchUnavailable):
        code, reason = error.code, str(error)
    elif isinstance(error, Terminated):
        code, reason = "terminated", "Terminated by signal."
    else:
        code, reason = "interrupted", "Interrupted."
    mutated = launcher.client.mutation_attempted
    if mutated:
        next_step = ("Do not relaunch blindly. Inspect known resources; "
                     "nothing was cleaned up.")
    elif code == "name_exists":
        next_step = ("No mutations were attempted. Choose --name-prefix or a "
                     "different --name; no recovery inspection is needed.")
    else:
        next_step = ("No mutations were attempted. Resolve this preflight "
                     "issue before trying again.")
    return {
        "status": "error", "stage": launcher.stage, "code": code,
        "herdr_code": getattr(error, "herdr_code", None),
        "reason": reason,
        "resources": launcher.resources,
        "requested_name": launcher.name if identifier(launcher.name) else None,
        "mutation_may_have_taken_effect": mutated,
        "next": next_step,
    }


def main(argv: list[str] | None = None) -> int:
    # Output is non-ASCII JSON; a Latin-1 locale must not crash after launch.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    launcher = Launcher(args)
    previous = {signum: signal.signal(signum, terminate)
                for signum in TERMINATING_SIGNALS}
    try:
        result = launcher.launch()
    except (LaunchUnavailable, KeyboardInterrupt, Terminated) as error:
        print(json.dumps(error_record(launcher, error), ensure_ascii=False),
              file=sys.stderr)
        return 1
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    print(json.dumps(result, ensure_ascii=False))
    return 0
