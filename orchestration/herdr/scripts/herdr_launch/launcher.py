"""Launch stages: preflight, create, start, verify, then hand off to submit."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil

from . import checks, submission
from .client import COMMAND_TIMEOUT, SESSION, HerdrClient
from .errors import LaunchUnavailable
from .session import session_mapping
from .submission import SETTLED


class Launcher:
    def __init__(self, args: argparse.Namespace,
                 client: HerdrClient | None = None):
        self.args = args
        self.client = client or HerdrClient()
        self.stage = "input"
        self.name = args.name
        self.resources: dict[str, str] = {}
        self.cwd: Path | None = None
        self.prompt: str | None = None

    def launch(self) -> dict:
        args = self.args
        checks.validate_args(args)
        self.prompt = checks.load_prompt(args)
        self.cwd = checks.directory(args.cwd)
        if not self.cwd.is_dir():
            raise LaunchUnavailable("Launch directory does not exist.")
        if shutil.which("herdr") is None:
            raise LaunchUnavailable("Herdr is not available on PATH.")
        self.check_server()
        self.name = self.choose_name()
        self.create(self.workspace() if args.workspace else None)
        agent = self.start()
        self.resources["name"] = self.name
        self.stage = "inspect"
        startup, truncated = submission.read_startup(self.client, self.name)
        payload = {
            "status": "started", **self.resources, "kind": args.kind,
            "state": agent["agent_status"], "cwd": str(self.cwd),
            "session": session_mapping(agent, args.kind),
        }
        if self.prompt is None:
            payload.update(submission.startup_fields(startup, truncated))
            payload["inspection_required"] = True
            return payload
        blocked = checks.submission_block(startup, truncated)
        if blocked:
            payload.update(submission.blocked_result(blocked, startup, truncated))
            return payload
        self.stage = "prompt"
        payload.update(submission.submit_prompt(
            self.client, name=self.name, kind=args.kind, prompt=self.prompt,
            wait_timeout=args.wait_timeout, agent=agent))
        return payload

    def check_server(self) -> None:
        self.stage = "server"
        status = self.client.json_command(["status", "server", "--json"])
        if (status.get("running") is not True
                or status.get("compatible") is not True
                or status.get("session") != SESSION):
            raise LaunchUnavailable(
                "Subagents server is unavailable or incompatible.")

    def choose_name(self) -> str:
        self.stage = "name"
        agents = checks.records(
            self.client.json_command(["agent", "list"], "agent_list"), "agents")
        if any(not isinstance(agent.get("name"), str) or not agent["name"]
               for agent in agents):
            raise LaunchUnavailable("Agent names could not be established.")
        return checks.choose_name(self.args.name, self.args.name_prefix,
                                  {agent["name"] for agent in agents})

    def workspace(self) -> str:
        """Verify an explicitly requested workspace. Never scan for a match."""
        self.stage = "workspace"
        workspace_id = self.args.workspace
        panes = checks.records(self.client.json_command(
            ["pane", "list", "--workspace", workspace_id], "pane_list"),
            "panes")
        matched, unknown = False, not panes
        for pane in panes:
            if pane.get("workspace_id") != workspace_id:
                raise LaunchUnavailable("Pane has an unexpected workspace.")
            try:
                matched |= checks.directory(pane.get("foreground_cwd")) == self.cwd
            except LaunchUnavailable:
                unknown = True
        if matched:
            return workspace_id
        if unknown:
            raise LaunchUnavailable(
                "Workspace selection is ambiguous; inspect manually.")
        raise LaunchUnavailable(
            "Requested workspace does not match the directory.")

    def create(self, workspace_id: str | None) -> None:
        self.stage = "create"
        if workspace_id:
            self.resources["workspace_id"] = workspace_id
            argv = ["tab", "create", "--workspace", workspace_id]
            response_type = "tab_created"
        else:
            argv = ["workspace", "create"]
            response_type = "workspace_created"
        result = self.client.json_command(
            [*argv, "--cwd", str(self.cwd), "--label", self.name,
             "--no-focus"], response_type, mutation=True)
        # Preserve every known ID even if another required field is missing.
        for field, key in (("workspace", "workspace_id"), ("tab", "tab_id")):
            item = result.get(field)
            if isinstance(item, dict) and checks.identifier(item.get(key)):
                if (key == "workspace_id" and workspace_id
                        and workspace_id != item[key]):
                    raise LaunchUnavailable(
                        "Creation returned a different workspace.")
                self.resources[key] = item[key]
        pane = result.get("root_pane")
        if not isinstance(pane, dict):
            raise LaunchUnavailable("Creation did not identify its root pane.")
        keys = ("workspace_id", "tab_id", "pane_id")
        for key in keys:
            value = pane.get(key)
            if checks.identifier(value):
                if key in self.resources and self.resources[key] != value:
                    raise LaunchUnavailable("Creation returned conflicting IDs.")
                self.resources[key] = value
        if any(not checks.identifier(pane.get(key)) for key in keys):
            raise LaunchUnavailable(
                "Creation returned incomplete resource identities.")

    def start(self) -> dict:
        self.stage = "start"
        args = self.args
        argv = ["agent", "start", self.name, "--kind", args.kind, "--pane",
                self.resources["pane_id"], "--timeout", str(args.timeout)]
        if args.agent_args:
            argv += ["--", *args.agent_args]
        result = self.client.start_when_ready(
            argv, timeout=args.timeout / 1000 + COMMAND_TIMEOUT)
        agent = result.get("agent", {})
        if self.verify_agent(agent):
            return agent
        agent = self.client.json_command(
            ["agent", "get", self.name], "agent_info").get("agent")
        if not self.verify_agent(agent):
            raise LaunchUnavailable("Agent metadata is still incomplete.")
        return agent

    def verify_agent(self, agent: object) -> bool:
        """Reject contradictions; return whether all evidence is present."""
        if not isinstance(agent, dict):
            raise LaunchUnavailable("Unsupported agent metadata.")
        expected = {**self.resources, "name": self.name,
                    "agent": self.args.kind, "interactive_ready": True}
        for key, value in expected.items():
            if key in agent and (agent[key] != value or (
                    key == "interactive_ready" and agent[key] is not True)):
                raise LaunchUnavailable("Startup identity/readiness mismatch.")
        if ("foreground_cwd" in agent
                and checks.directory(agent["foreground_cwd"]) != self.cwd):
            raise LaunchUnavailable("Agent started in an unexpected directory.")
        if "agent_status" in agent and agent["agent_status"] not in SETTLED:
            raise LaunchUnavailable(
                "Fresh agent is not in an inspectable idle state.")
        return all(key in agent for key in (
            *expected, "foreground_cwd", "agent_status"))
