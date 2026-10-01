"""Command-line interface: parse arguments and print one JSON record."""

from __future__ import annotations

import argparse
import json
import sys

from . import __doc__ as DESCRIPTION
from .checks import identifier
from .errors import LaunchUnavailable
from .launcher import Launcher


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse launcher flags; everything after `--` goes to the agent."""
    native = []
    if "--" in argv:
        boundary = argv.index("--")
        argv, native = argv[:boundary], argv[boundary + 1:]
    parser = argparse.ArgumentParser(description=DESCRIPTION)
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
    args = parser.parse_args(argv)
    args.agent_args = native
    return args


def error_record(launcher: Launcher, error: BaseException) -> dict:
    code = error.code if isinstance(error, LaunchUnavailable) else "interrupted"
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
        "reason": (str(error) if isinstance(error, LaunchUnavailable)
                   else "Interrupted."),
        "resources": launcher.resources,
        "requested_name": launcher.name if identifier(launcher.name) else None,
        "mutation_may_have_taken_effect": mutated,
        "next": next_step,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    launcher = Launcher(args)
    try:
        result = launcher.launch()
    except (LaunchUnavailable, KeyboardInterrupt) as error:
        print(json.dumps(error_record(launcher, error), ensure_ascii=False),
              file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0
