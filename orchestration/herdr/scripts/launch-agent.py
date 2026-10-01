#!/usr/bin/env python3
"""Start one fresh Herdr agent and optionally submit one prompt.

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
import threading
import time
import uuid


COMMAND_TIMEOUT = 20
SHELL_READY_TIMEOUT = 5
SHELL_POLL_INTERVAL = 0.1
AGENT_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
STARTUP_LINES = 80
STARTUP_CHARACTERS = 8000
# Worst-case UTF-8 for the character cap. The read stops here and kills the
# CLI; Herdr's --lines flag does not bound a single unwrapped line.
STARTUP_BYTES = STARTUP_CHARACTERS * 4
JSON_STDOUT_LIMIT = 1_048_576
STDERR_LIMIT = 65_536
PROMPT_CHARACTERS = 200_000
WAIT_TIMEOUT_MAX = 3_600_000
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
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
AGENT_STATUSES = {"idle", "working", "blocked", "done", "unknown"}


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


class CommandResult:
    def __init__(self, returncode, stdout, stderr, stdout_truncated=False):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.stdout_truncated = stdout_truncated


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


def read_capped(stream, limit):
    """Read at most `limit` bytes, plus one byte to detect overflow."""
    chunks = []
    total = 0
    try:
        while total < limit:
            chunk = stream.read(min(65536, limit - total))
            if not chunk:
                return b"".join(chunks), False
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks), bool(stream.read(1))
    except OSError:
        return b"".join(chunks), True


def run_bounded(argv, *, timeout, max_stdout=JSON_STDOUT_LIMIT):
    """Run a command without buffering an unbounded pipe."""
    try:
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except (OSError, ValueError) as error:
        raise error
    captured = {}

    def collect(stream, key, limit):
        data, truncated = read_capped(stream, limit)
        captured[key] = (data, truncated)
        if truncated:
            process.kill()

    threads = [
        threading.Thread(target=collect, args=(process.stdout, "stdout", max_stdout)),
        threading.Thread(target=collect, args=(process.stderr, "stderr", STDERR_LIMIT)),
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


def submission_block(startup, truncated):
    if not startup.strip():
        return "startup_empty"
    if truncated:
        return "startup_truncated"
    lowered = startup.casefold()
    if any(phrase in lowered for phrase in STARTUP_BLOCKERS):
        return "startup_unsafe"
    return None


def load_prompt(args):
    if args.wait_timeout is not None and args.prompt is None and args.prompt_file is None:
        raise LaunchUnavailable("Wait timeout requires a prompt.")
    if args.wait_timeout is not None and not 0 < args.wait_timeout <= WAIT_TIMEOUT_MAX:
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


def session_mapping(agent, kind):
    session = agent.get("agent_session") if isinstance(agent, dict) else None
    if (isinstance(session, dict) and session.get("kind") in ("path", "id")
            and isinstance(session.get("value"), str)
            and 0 < len(session["value"]) <= 4096
            and session.get("agent") == kind):
        return {key: session[key] for key in ("agent", "kind", "value")}
    return None


def extract_answer(session, kind):
    """Return candidate text only after a settled wait. Never raise."""
    if (kind not in ("pi", "claude") or not session
            or session.get("kind") != "path"):
        return None
    path = Path(session["value"])
    if not path.is_file():
        return None
    script = Path(__file__).with_name("extract-response.py")
    try:
        result = run_bounded(
            [sys.executable, str(script), "--kind", kind, str(path)],
            timeout=COMMAND_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if result.returncode or result.stdout_truncated or not result.stdout:
        return None
    return result.stdout


class Launcher:
    def __init__(self, args):
        self.args = args
        self.stage = "input"
        self.resources = {}
        self.mutation_attempted = False
        self.prompt = None

    def command(self, argv, response_type=None, *, mutation=False,
                timeout=COMMAND_TIMEOUT, text=False):
        if mutation:
            # Set before invoking: even a timeout or malformed reply can leave
            # a workspace, pane, or agent behind. Only a verified pre-input
            # shell rejection may be retried by start_when_ready().
            self.mutation_attempted = True
        try:
            result = run_bounded(
                ["herdr", "--session", "Subagents", *argv],
                timeout=timeout,
                max_stdout=STARTUP_BYTES if text else JSON_STDOUT_LIMIT,
            )
        except subprocess.TimeoutExpired:
            raise LaunchUnavailable("Herdr command timed out.") from None
        except (OSError, ValueError):
            raise LaunchUnavailable("Herdr command could not be executed/read.") from None
        truncated = getattr(result, "stdout_truncated", False)
        if text:
            # A killed reader is success only when we kept a capped prefix.
            if truncated and result.stdout:
                return result.stdout, True
            if result.returncode:
                raise herdr_failure(result)
            return result.stdout, False
        if truncated:
            raise LaunchUnavailable("Herdr returned an oversized response.")
        if result.returncode:
            raise herdr_failure(result)
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
        payload = data.get("result")
        if not isinstance(payload, dict) or payload.get("type") != response_type:
            raise LaunchUnavailable("Herdr returned an unsupported response type.")
        return payload

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
        """Verify an explicitly requested workspace. Never scan for a match."""
        self.stage = "workspace"
        workspace_id = self.args.workspace
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
            return workspace_id
        if unknown:
            raise LaunchUnavailable("Workspace selection is ambiguous; inspect manually.")
        raise LaunchUnavailable("Requested workspace does not match the directory.")

    def create(self, workspace_id):
        self.stage = "create"
        label = self.args.name
        if workspace_id:
            self.resources["workspace_id"] = workspace_id
            argv = ["tab", "create", "--workspace", workspace_id]
            response_type = "tab_created"
        else:
            argv = ["workspace", "create"]
            response_type = "workspace_created"
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

    def startup_text(self):
        self.stage = "inspect"
        startup, hit_cap = self.command(
            ["agent", "read", self.args.name, "--source", "recent-unwrapped",
             "--lines", str(STARTUP_LINES), "--format", "text"], text=True)
        lines = startup.splitlines(keepends=True)
        truncated = (hit_cap or len(lines) >= STARTUP_LINES
                     or len(startup) > STARTUP_CHARACTERS)
        startup = "".join(lines[:STARTUP_LINES])[:STARTUP_CHARACTERS]
        return startup, truncated

    def submit(self, agent, startup, truncated):
        blocked = submission_block(startup, truncated)
        if blocked:
            return {
                "submitted": False, "submission_blocked": blocked,
                "startup": startup, "startup_truncated": truncated,
                "startup_empty": not startup.strip(),
                "inspection_required": True, "state": agent["agent_status"],
            }
        self.stage = "prompt"
        argv = ["agent", "prompt", self.args.name, self.prompt]
        timeout = COMMAND_TIMEOUT
        if self.args.wait_timeout is not None:
            argv += ["--wait", "--timeout", str(self.args.wait_timeout)]
            timeout = self.args.wait_timeout / 1000 + COMMAND_TIMEOUT
        result = self.command(argv, "agent_prompted", mutation=True, timeout=timeout)
        prompted = result.get("agent") if isinstance(result, dict) else None
        state = agent["agent_status"]
        if isinstance(prompted, dict) and prompted.get("agent_status") in AGENT_STATUSES:
            state = prompted["agent_status"]
            mapped = session_mapping(prompted, self.args.kind)
            if mapped:
                agent["agent_session"] = mapped
        payload = {
            "submitted": True, "submission_blocked": None,
            "startup_checked": True, "inspection_required": False,
            "state": state, "waited": self.args.wait_timeout is not None,
        }
        if payload["waited"] and state in ("idle", "done"):
            answer = extract_answer(session_mapping(agent, self.args.kind), self.args.kind)
            if answer is not None:
                payload["response"] = answer
        return payload

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
        self.prompt = load_prompt(args)
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
        self.create(self.workspace() if args.workspace else None)
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
        startup, truncated = self.startup_text()
        session = session_mapping(agent, args.kind)
        payload = {
            "status": "started", **self.resources, "kind": args.kind,
            "state": agent["agent_status"], "cwd": str(self.cwd),
            "session": session,
        }
        if self.prompt is None:
            payload.update({
                "startup": startup, "startup_truncated": truncated,
                "startup_empty": not startup.strip(), "inspection_required": True,
            })
            return payload
        submitted = self.submit(agent, startup, truncated)
        payload["state"] = submitted.pop("state")
        payload["session"] = session_mapping(agent, args.kind)
        payload.update(submitted)
        return payload


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
    parser.add_argument("--workspace", help="add one tab to this workspace; verified live")
    parser.add_argument("--timeout", type=int, default=30000,
                        help="startup timeout in ms (3001–300000; default 30000)")
    prompt = parser.add_mutually_exclusive_group()
    prompt.add_argument("--prompt", help="submit this text after a safe startup")
    prompt.add_argument("--prompt-file", help="UTF-8 file to submit after a safe startup")
    parser.add_argument("--wait-timeout", type=int,
                        help="after submit, wait this many ms (1–3600000)")
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
