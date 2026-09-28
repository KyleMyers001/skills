#!/usr/bin/env python3
"""Start one fresh Herdr agent; return startup context, never submit a prompt.

Python standard library only. All commands target Subagents. Failures leave
resources intact: inspect them instead of blindly rerunning this helper.
"""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid


COMMAND_TIMEOUT = 20
SHELL_READY_TIMEOUT = 5
SHELL_POLL_INTERVAL = 0.1
AGENT_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
STARTUP_LINES = 80
STARTUP_CHARACTERS = 8000
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


class LaunchUnavailable(Exception):
    """A bounded, locally authored diagnostic safe to return to the caller."""

    def __init__(self, message, code="launch_unavailable"):
        super().__init__(message)
        self.code = code


class HerdrFailure(LaunchUnavailable):
    def __init__(self, code, pre_start_busy=False):
        super().__init__("Herdr command failed; inspect the known target.", "herdr_error")
        self.herdr_code = code
        self.pre_start_busy = pre_start_busy


def herdr_failure(result):
    """Keep only a bounded code, never upstream messages, argv, or raw output."""
    code, pre_start_busy = None, False
    outputs = [value.strip() for value in (result.stdout, result.stderr)
               if value.strip()]
    if len(outputs) == 1:
        try:
            data = json.loads(outputs[0])
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict) and "result" not in data:
            error = data.get("error")
            if isinstance(error, dict) and isinstance(error.get("code"), str):
                if ERROR_CODE.fullmatch(error["code"]):
                    code = error["code"]
                # v0.8.0 emits this exact stderr envelope before registering an
                # agent or writing input. Never infer it from error prose.
                pre_start_busy = (
                    code == "agent_pane_busy" and result.returncode == 1
                    and not result.stdout.strip() and bool(result.stderr.strip())
                    and data.get("id") == "cli:agent:start"
                    and isinstance(error.get("message"), str)
                )
    return HerdrFailure(code, pre_start_busy)


def choose_name(name, prefix, existing):
    if prefix is None:
        if name in existing:
            raise LaunchUnavailable("Agent name already exists.", "name_exists")
        return name
    # Random suffixes avoid routinely colliding with concurrent launchers.
    # Check the current registry too; never retry an actual start on collision.
    for _ in range(10):
        candidate = f"{prefix}-{uuid.uuid4().hex[:12]}"
        if candidate not in existing:
            return candidate
    raise LaunchUnavailable("Could not generate an unused name.",
                            "name_generation_failed")


def identifier(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def directory(value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise LaunchUnavailable("Missing or invalid live directory evidence.")
    try:
        return Path(value).resolve()
    except (OSError, ValueError, RuntimeError):
        raise LaunchUnavailable("Directory evidence could not be resolved.") from None


def records(result, field):
    values = result.get(field)
    if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
        raise LaunchUnavailable("Unsupported discovery response.")
    return values


class Launcher:
    def __init__(self, args):
        self.args = args
        self.stage = "input"
        self.resources = {}
        self.mutation_attempted = False

    def command(self, argv, response_type=None, *, mutation=False,
                timeout=COMMAND_TIMEOUT, text=False):
        if mutation:
            # Set before invoking: even a timeout or malformed reply can leave
            # a workspace, pane, or agent behind. Only a verified pre-input
            # shell rejection may be retried by start_when_ready().
            self.mutation_attempted = True
        try:
            result = subprocess.run(
                ["herdr", "--session", "Subagents", *argv],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise LaunchUnavailable("Herdr command timed out.") from None
        except (OSError, ValueError):
            raise LaunchUnavailable("Herdr command could not be executed/read.") from None
        if result.returncode:
            raise herdr_failure(result)
        if text:
            return result.stdout
        try:
            data = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise LaunchUnavailable("Herdr returned unreadable JSON.") from None
        if not isinstance(data, dict):
            raise LaunchUnavailable("Herdr returned an unsupported response.")
        if "error" in data:
            raise herdr_failure(result)
        if response_type is None:
            return data
        result = data.get("result")
        if not isinstance(result, dict) or result.get("type") != response_type:
            raise LaunchUnavailable("Herdr returned an unsupported response type.")
        return result

    def start_when_ready(self, argv):
        # Only used for the pane just created by this launch. The same name,
        # pane and arguments survive every rejected attempt; never recreate.
        deadline = time.monotonic() + SHELL_READY_TIMEOUT
        while True:
            try:
                return self.command(
                    argv, "agent_started", mutation=True,
                    timeout=self.args.timeout / 1000 + COMMAND_TIMEOUT)
            except HerdrFailure as error:
                remaining = deadline - time.monotonic()
                if not error.pre_start_busy or remaining <= 0:
                    raise
                time.sleep(min(SHELL_POLL_INTERVAL, remaining))
                if time.monotonic() >= deadline:
                    raise

    def workspace(self):
        self.stage = "workspace"
        if self.args.workspace:
            workspace_ids = [self.args.workspace]
        else:
            result = self.command(["workspace", "list"], "workspace_list")
            workspace_ids = [item.get("workspace_id")
                             for item in records(result, "workspaces")]
            if (any(not identifier(value) for value in workspace_ids)
                    or len(set(workspace_ids)) != len(workspace_ids)):
                raise LaunchUnavailable("Unsupported workspace identities.")
        matches = []
        inconclusive = False
        for workspace_id in workspace_ids:
            result = self.command(
                ["pane", "list", "--workspace", workspace_id], "pane_list")
            panes = records(result, "panes")
            matched, unknown = False, not bool(panes)
            for pane in panes:
                if pane.get("workspace_id") != workspace_id:
                    raise LaunchUnavailable("Pane has an unexpected workspace.")
                try:
                    matched |= directory(pane.get("foreground_cwd")) == self.cwd
                except LaunchUnavailable:
                    unknown = True
            if matched:
                matches.append(workspace_id)
            elif unknown:
                inconclusive = True
        if inconclusive or len(matches) > 1:
            raise LaunchUnavailable("Workspace selection is ambiguous; inspect manually.")
        if self.args.workspace and not matches:
            raise LaunchUnavailable("Requested workspace does not match the directory.")
        return matches[0] if matches else None

    def create(self, workspace_id):
        self.stage = "create"
        if workspace_id:
            self.resources["workspace_id"] = workspace_id
            argv = ["tab", "create", "--workspace", workspace_id]
            response_type, label = "tab_created", self.args.name
        else:
            argv = ["workspace", "create"]
            response_type, label = "workspace_created", self.cwd.name or "root"
        result = self.command(
            [*argv, "--cwd", str(self.cwd), "--label", label, "--no-focus"],
            response_type, mutation=True,
        )
        # Preserve every known ID even if another required field is missing.
        for field, key in (("workspace", "workspace_id"), ("tab", "tab_id")):
            item = result.get(field)
            if isinstance(item, dict) and identifier(item.get(key)):
                if key == "workspace_id" and workspace_id != item[key] and workspace_id:
                    raise LaunchUnavailable("Creation returned a different workspace.")
                self.resources[key] = item[key]
        pane = result.get("root_pane")
        if not isinstance(pane, dict):
            raise LaunchUnavailable("Creation did not identify its root pane.")
        for key in ("workspace_id", "tab_id", "pane_id"):
            value = pane.get(key)
            if identifier(value):
                if key in self.resources and self.resources[key] != value:
                    raise LaunchUnavailable("Creation returned conflicting IDs.")
                self.resources[key] = value
        if any(not identifier(pane.get(key)) for key in (
                "workspace_id", "tab_id", "pane_id")):
            raise LaunchUnavailable("Creation returned incomplete resource identities.")

    def verify_agent(self, agent):
        if not isinstance(agent, dict):
            raise LaunchUnavailable("Unsupported agent metadata.")
        expected = {**self.resources, "name": self.args.name,
                    "agent": self.args.kind, "interactive_ready": True}
        for key, value in expected.items():
            if key in agent and (agent[key] != value or
                                 (key == "interactive_ready" and agent[key] is not True)):
                raise LaunchUnavailable("Startup identity/readiness mismatch.")
        if ("foreground_cwd" in agent
                and directory(agent["foreground_cwd"]) != self.cwd):
            raise LaunchUnavailable("Agent started in an unexpected directory.")
        if "agent_status" in agent and agent["agent_status"] not in ("idle", "done"):
            raise LaunchUnavailable("Fresh agent is not in an inspectable idle state.")
        return all(key in agent for key in (
            *expected, "foreground_cwd", "agent_status"))

    def launch(self):
        args = self.args
        requested_name = args.name if args.name is not None else args.name_prefix
        if not AGENT_NAME.fullmatch(requested_name):
            raise LaunchUnavailable(
                "Name/prefix must match [a-z][a-z0-9_-]{0,31}.")
        if not identifier(args.kind):
            raise LaunchUnavailable("Kind must be a short identifier.")
        if args.name_prefix is not None and len(args.name_prefix) > 19:
            raise LaunchUnavailable("Name prefix must be at most 19 characters.")
        if args.workspace is not None and not identifier(args.workspace):
            raise LaunchUnavailable("Workspace must be a discovered identifier.")
        if not 3000 < args.timeout <= 300000:
            raise LaunchUnavailable("Startup timeout must be 3001–300000 ms.")
        self.cwd = directory(args.cwd)
        if not self.cwd.is_dir():
            raise LaunchUnavailable("Launch directory does not exist.")
        if shutil.which("herdr") is None:
            raise LaunchUnavailable("Herdr is not available on PATH.")
        self.stage = "server"
        status = self.command(["status", "server", "--json"])
        if (status.get("running") is not True or status.get("compatible") is not True
                or status.get("session") != "Subagents"):
            raise LaunchUnavailable("Subagents server is unavailable or incompatible.")
        self.stage = "name"
        agents = records(self.command(["agent", "list"], "agent_list"), "agents")
        if any(not isinstance(agent.get("name"), str) or not agent["name"]
               for agent in agents):
            raise LaunchUnavailable("Agent names could not be established.")
        args.name = choose_name(args.name, args.name_prefix,
                                {agent["name"] for agent in agents})
        self.create(self.workspace())
        self.stage = "start"
        argv = ["agent", "start", args.name, "--kind", args.kind, "--pane",
                self.resources["pane_id"], "--timeout", str(args.timeout)]
        if args.agent_args:
            argv += ["--", *args.agent_args]
        result = self.start_when_ready(argv)
        agent = result.get("agent", {})
        if not self.verify_agent(agent):
            agent = self.command(["agent", "get", args.name], "agent_info").get("agent")
            if not self.verify_agent(agent):
                raise LaunchUnavailable("Agent metadata is still incomplete.")
        self.resources["name"] = args.name
        self.stage = "inspect"
        startup = self.command(
            ["agent", "read", args.name, "--source", "recent-unwrapped",
             "--lines", str(STARTUP_LINES), "--format", "text"], text=True)
        lines = startup.splitlines(keepends=True)
        # A full line window might have omitted earlier startup context.
        truncated = len(lines) >= STARTUP_LINES or len(startup) > STARTUP_CHARACTERS
        startup = "".join(lines[:STARTUP_LINES])[:STARTUP_CHARACTERS]
        session = agent.get("agent_session")
        if (isinstance(session, dict) and session.get("kind") in ("path", "id")
                and isinstance(session.get("value"), str)
                and 0 < len(session["value"]) <= 4096
                and session.get("agent") == args.kind):
            session = {key: session[key] for key in ("agent", "kind", "value")}
        else:
            session = None
        return {
            "status": "started", **self.resources, "kind": args.kind,
            "state": agent["agent_status"], "cwd": str(self.cwd),
            "session": session, "startup": startup,
            "startup_truncated": truncated, "startup_empty": not startup.strip(),
            "inspection_required": True,
        }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    native = []
    if "--" in argv:
        boundary = argv.index("--")
        argv, native = argv[:boundary], argv[boundary + 1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", required=True, help="absolute existing directory")
    naming = parser.add_mutually_exclusive_group(required=True)
    naming.add_argument("--name", help="exact agent name; collisions fail unchanged")
    naming.add_argument("--name-prefix", help="generate an unused name from this prefix")
    parser.add_argument("--kind", required=True, help="Herdr-supported canonical kind")
    parser.add_argument("--workspace", help="discovered workspace ID; verified live")
    parser.add_argument("--timeout", type=int, default=30000,
                        help="startup timeout in ms (3001–300000; default 30000)")
    args = parser.parse_args(argv)
    args.agent_args = native
    launcher = Launcher(args)
    try:
        result = launcher.launch()
    except (LaunchUnavailable, KeyboardInterrupt) as error:
        code = error.code if isinstance(error, LaunchUnavailable) else "interrupted"
        if launcher.mutation_attempted:
            next_step = ("Do not relaunch blindly. Inspect known resources; "
                         "nothing was cleaned up.")
        elif code == "name_exists":
            next_step = ("No mutations were attempted. Choose --name-prefix or a "
                         "different --name; no recovery inspection is needed.")
        else:
            next_step = ("No mutations were attempted. Resolve this preflight "
                         "issue before trying again.")
        print(json.dumps({
            "status": "error", "stage": launcher.stage, "code": code,
            "herdr_code": getattr(error, "herdr_code", None),
            "reason": (str(error) if isinstance(error, LaunchUnavailable)
                       else "Interrupted."),
            "resources": launcher.resources,
            "requested_name": args.name if identifier(args.name) else None,
            "mutation_may_have_taken_effect": launcher.mutation_attempted,
            "next": next_step,
        }, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
