"""Launch contracts against a fake CLI; never launch real agents."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS / "launch-agent.py"
sys.path.insert(0, str(SCRIPTS))
from herdr_launch import checks, cli, client, errors, launcher  # noqa: E402
from herdr_launch.process import CommandResult  # noqa: E402
FAKE = r'''import json, os, re, sys, time
from pathlib import Path
args = sys.argv[1:]
log = Path(os.environ["FAKE_LOG"])
with log.open("a") as stream:
    stream.write(json.dumps(args) + "\n")
steps = json.loads(Path(os.environ["FAKE_STEPS"]).read_text())
index = len(log.read_text().splitlines()) - 1
if index >= len(steps):
    sys.exit(90)
step = steps[index]
expected = step["args"]
generated = None
for actual, wanted in zip(args, expected):
    if wanted == "$GENERATED_NAME" and re.fullmatch(r"[A-Za-z0-9_.:-]+-[0-9a-f]{12}", actual):
        generated = actual
if generated:
    expected = [generated if arg == "$GENERATED_NAME" else arg for arg in expected]
if args != expected:
    print("unexpected command", file=sys.stderr)
    sys.exit(90)
sys.stdout.write(step.get("output", "").replace("$GENERATED_NAME", generated or ""))
sys.stdout.flush()
sys.stderr.write(step.get("stderr", ""))
sys.stderr.flush()
if step.get("hold"):
    time.sleep(step["hold"])
sys.exit(step.get("code", 0))
'''


def step(args, result=None, raw=None, **extra):
    return {"args": ["--session", "Subagents", *args],
            "output": raw if raw is not None else json.dumps(result), **extra}


def response(kind, **fields):
    return {"result": {"type": kind, **fields}}


class LaunchAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.cwd = self.directory / "repo with spaces é"
        self.cwd.mkdir()
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        executable = self.bin / "herdr"
        executable.write_text(f"#!{sys.executable}\n{FAKE}")
        executable.chmod(0o755)
        self.log = self.directory / "calls.jsonl"
        self.steps_file = self.directory / "steps.json"
        self.env = {**os.environ, "PATH": str(self.bin),
                    "FAKE_LOG": str(self.log),
                    "FAKE_STEPS": str(self.steps_file)}
        self.agent = {
            "name": "worker", "agent": "pi", "workspace_id": "w2",
            "tab_id": "w2:t1", "pane_id": "w2:p1",
            "foreground_cwd": str(self.cwd), "interactive_ready": True,
            "agent_status": "idle", "agent_session": {
                "agent": "pi", "kind": "path", "value": "/tmp/session.jsonl",
                "source": "herdr:pi"},
        }

    def preflight(self):
        return [
            step(["status", "server", "--json"], {
                "running": True, "compatible": True, "session": "Subagents",
                "protocol": 19, "version": "0.8.0"}),
            step(["agent", "list"], response("agent_list", agents=[])),
        ]

    def panes(self, workspace="w2", directories=None):
        if directories is None:
            directories = [str(self.cwd)]
        return step(["pane", "list", "--workspace", workspace], response(
            "pane_list", panes=[
                {"workspace_id": workspace, "foreground_cwd": directory,
                 "cwd": str(self.cwd), "pane_id": f"{workspace}:p{i}"}
                for i, directory in enumerate(directories)]))

    def creation(self, existing=False):
        args = (["tab", "create", "--workspace", "w2"] if existing else
                ["workspace", "create"])
        args += ["--cwd", str(self.cwd), "--label", "worker", "--no-focus"]
        return step(args, response(
            "tab_created" if existing else "workspace_created",
            root_pane={key: self.agent[key] for key in (
                "workspace_id", "tab_id", "pane_id")},
        ))

    def start(self, native=(), agent=None):
        args = ["agent", "start", "worker", "--kind", "pi", "--pane",
                "w2:p1", "--timeout", "30000"]
        if native:
            args += ["--", *native]
        return step(args, response(
            "agent_started", agent=self.agent if agent is None else agent,
            argv=["pi", "PRIVATE_PAYLOAD"], ignored="PRIVATE_PAYLOAD" * 1000))

    def read(self, text="Ready\n"):
        return step(["agent", "read", "worker", "--source", "recent-unwrapped",
                     "--lines", "80", "--format", "text"], raw=text)

    def happy_steps(self, native=(), workspace=None):
        steps = self.preflight()
        if workspace:
            steps += [self.panes(workspace), self.creation(existing=True)]
        else:
            steps += [self.creation(existing=False)]
        return steps + [self.start(native), self.read()]

    def prefix_steps(self, workspace=None):
        steps = self.happy_steps(workspace=workspace)
        for item in steps:
            item["args"] = ["$GENERATED_NAME" if arg == "worker" else arg
                            for arg in item["args"]]
            item["output"] = item["output"].replace(
                '"worker"', '"$GENERATED_NAME"')
        steps[1] = step(["agent", "list"], response(
            "agent_list", agents=[{"name": "worker"}]))
        return steps

    def run_launcher(self, steps, extra=(), cwd=None, env=None, naming=None,
                     allow_prompt=False, timeout=15):
        self.steps_file.write_text(json.dumps(steps))
        if self.log.exists():
            self.log.write_text("")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--cwd", str(cwd or self.cwd),
             *(naming if naming is not None else ["--name", "worker"]),
             "--kind", "pi", *extra],
            env=env or self.env, capture_output=True, text=True,
            cwd=self.directory, timeout=timeout,
        )
        calls = ([json.loads(line) for line in self.log.read_text().splitlines()]
                 if self.log.exists() else [])
        self.assertNotIn("PRIVATE_PAYLOAD", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        forbidden = [["agent", "stop"], ["workspace", "delete"],
                     ["tab", "close"], ["pane", "run"], ["agent", "wait"]]
        if not allow_prompt:
            forbidden.append(["agent", "prompt"])
        for call in calls:
            self.assertEqual(call[:2], ["--session", "Subagents"])
            self.assertNotIn(call[2:4], forbidden)
        return result, calls

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        data = json.loads(result.stdout)
        self.assertEqual(data["status"], "started")
        self.assertEqual(data["pane_id"], "w2:p1")
        self.assertEqual(data["state"], "idle")
        return data

    def assert_failure(self, result, stage):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        data = json.loads(result.stderr)
        self.assertEqual(data["stage"], stage)
        self.assertLess(len(result.stderr), 1500)
        if data["mutation_may_have_taken_effect"]:
            self.assertIn("Do not relaunch", data["next"])
        else:
            self.assertIn("No mutations", data["next"])
            self.assertNotIn("Do not relaunch", data["next"])
        return data

    def test_new_workspace_uses_root_without_creating_tab(self):
        steps = self.happy_steps()
        result, calls = self.run_launcher(steps)
        data = self.assert_success(result)
        self.assertEqual(data["startup"], "Ready\n")
        self.assertFalse(data["startup_truncated"])
        self.assertEqual(data["session"]["value"], "/tmp/session.jsonl")
        self.assertEqual(calls, [item["args"] for item in steps])
        self.assertLess(len(result.stdout), 1000)

    def test_default_launch_does_not_reuse_a_directory_workspace(self):
        steps = self.happy_steps()
        result, calls = self.run_launcher(steps)
        self.assert_success(result)
        self.assertEqual(calls[2][2:4], ["workspace", "create"])
        self.assertFalse(any(call[2:4] == ["workspace", "list"] for call in calls))
        self.assertFalse(any(call[2:4] == ["pane", "list"] for call in calls))
        self.assertFalse(any(call[2:4] == ["tab", "create"] for call in calls))
        self.assertEqual(calls[2][calls[2].index("--label") + 1], "worker")

    def test_explicit_workspace_skips_global_workspace_discovery(self):
        steps = self.preflight() + [self.panes(), self.creation(True),
                                    self.start(), self.read()]
        result, calls = self.run_launcher(steps, ["--workspace", "w2"])
        self.assert_success(result)
        self.assertEqual(calls, [item["args"] for item in steps])

    def test_symlink_directory_matches_live_foreground_cwd(self):
        alias = self.directory / "alias"
        alias.symlink_to(self.cwd, target_is_directory=True)
        steps = self.happy_steps(workspace="w2")
        steps[2] = self.panes(directories=[str(alias)])
        result, _ = self.run_launcher(steps, ["--workspace", "w2"], cwd=alias)
        self.assert_success(result)

    def test_native_arguments_are_forwarded_without_shell_evaluation(self):
        native = ["--append-system-prompt", "/tmp/Persona 'é'.md",
                  "--model", "$(touch SHOULD_NOT_EXIST); PRIVATE_PAYLOAD"]
        result, calls = self.run_launcher(
            self.happy_steps(native=native), ["--", *native])
        self.assert_success(result)
        self.assertEqual(calls[-2][-len(native):], native)
        self.assertFalse((self.directory / "SHOULD_NOT_EXIST").exists())

    def test_explicit_workspace_without_directory_evidence_never_mutates(self):
        for panes in (self.panes(directories=[None]), self.panes(directories=[]),
                      self.panes(directories=["relative"])):
            with self.subTest(panes=panes):
                steps = self.preflight() + [panes]
                result, calls = self.run_launcher(steps, ["--workspace", "w2"])
                data = self.assert_failure(result, "workspace")
                self.assertFalse(data["mutation_may_have_taken_effect"])
                self.assertEqual(calls, [item["args"] for item in steps])

    def test_explicit_workspace_must_match_directory(self):
        steps = self.preflight() + [self.panes(directories=["/elsewhere"])]
        result, calls = self.run_launcher(steps, ["--workspace", "w2"])
        self.assert_failure(result, "workspace")
        self.assertEqual(len(calls), 3)

    def test_duplicate_agent_name_stops_before_discovery(self):
        steps = self.preflight()
        steps[-1] = step(["agent", "list"], response(
            "agent_list", agents=[{"name": "worker"}]))
        result, calls = self.run_launcher(steps)
        data = self.assert_failure(result, "name")
        self.assertEqual(data["code"], "name_exists")
        self.assertFalse(data["mutation_may_have_taken_effect"])
        self.assertIn("--name-prefix", data["next"])
        self.assertEqual(len(calls), 2)

    def test_generated_names_are_fresh_and_used_for_all_launch_commands(self):
        names = []
        for workspace in (None, "w2"):
            with self.subTest(workspace=workspace):
                extra = ["--workspace", workspace] if workspace else []
                steps = self.prefix_steps(workspace=workspace)
                result, calls = self.run_launcher(
                    steps, extra, naming=["--name-prefix", "worker"])
                name = self.assert_success(result)["name"]
                self.assertRegex(name, r"^worker-[0-9a-f]{12}$")
                names.append(name)
                expected = json.loads(json.dumps(steps).replace(
                    "$GENERATED_NAME", name))
                self.assertEqual(calls, [item["args"] for item in expected])
        self.assertEqual(len(names), 2)
        self.assertNotEqual(names[0], names[1])

    def test_generated_name_start_failure_is_not_renamed_or_retried(self):
        steps = self.prefix_steps()[:-1]
        steps[-1].update(code=1, output="PRIVATE_PAYLOAD")
        result, calls = self.run_launcher(
            steps, naming=["--name-prefix", "worker"])
        data = self.assert_failure(result, "start")
        self.assertTrue(data["mutation_may_have_taken_effect"])
        self.assertRegex(data["requested_name"], r"^worker-[0-9a-f]{12}$")
        self.assertEqual(data["requested_name"], calls[-1][4])
        self.assertEqual(len(calls), 4)
        self.assertEqual(data["resources"]["pane_id"], "w2:p1")

    def test_name_generation_checks_registry_and_is_bounded(self):
        occupied = "worker-" + "a" * 12
        fresh = "worker-" + "b" * 12
        with patch.object(checks.uuid, "uuid4", side_effect=[
                checks.uuid.UUID("a" * 32), checks.uuid.UUID("b" * 32)]) as generate:
            self.assertEqual(checks.choose_name(None, "worker", {occupied}), fresh)
            self.assertEqual(generate.call_count, 2)
        with patch.object(checks.uuid, "uuid4",
                          return_value=checks.uuid.UUID("a" * 32)) as generate:
            with self.assertRaises(errors.LaunchUnavailable) as raised:
                checks.choose_name(None, "worker", {occupied})
            self.assertEqual(raised.exception.code, "name_generation_failed")
            self.assertEqual(generate.call_count, 10)
        with patch.object(checks.uuid, "uuid4") as generate:
            self.assertEqual(checks.choose_name("worker", None, set()), "worker")
            with self.assertRaises(errors.LaunchUnavailable) as raised:
                checks.choose_name("worker", None, {"worker"})
            self.assertEqual(raised.exception.code, "name_exists")
            generate.assert_not_called()
        self.assertEqual(len(checks.choose_name(None, "x" * 19, set())), 32)

    def test_prefix_and_exact_name_are_exclusive_and_prefix_is_bounded(self):
        for naming in ([], ["--name", "worker", "--name-prefix", "worker"]):
            with self.subTest(naming=naming):
                result, calls = self.run_launcher([], naming=naming)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(calls, [])
        for prefix in ("", "bad name", "x" * 20, "Uppercase", "name.dot", "1name"):
            with self.subTest(prefix=prefix):
                result, calls = self.run_launcher(
                    [], naming=["--name-prefix", prefix])
                self.assert_failure(result, "input")
                self.assertEqual(calls, [])

    def test_unicode_startup_is_readable_without_changing_its_content(self):
        text = "─" * 100 + "\nπ · Café — vérifier ✅\n"
        steps = self.happy_steps()
        steps[-1] = self.read(text)
        result, _ = self.run_launcher(steps)
        data = self.assert_success(result)
        self.assertEqual(data["startup"], text)
        self.assertIn("─" * 100, result.stdout)
        self.assertIn("π · Café", result.stdout)
        self.assertNotIn("\\u2500", result.stdout)
        self.assertLess(len(result.stdout), len(json.dumps(data)))

    def test_unusual_existing_names_do_not_block_a_distinct_fresh_name(self):
        steps = self.happy_steps()
        steps[1] = step(["agent", "list"], response(
            "agent_list", agents=[{"name": "Kyle’s other agent"}]))
        result, _ = self.run_launcher(steps)
        self.assert_success(result)

    def test_multiple_matching_panes_are_one_workspace_not_ambiguity(self):
        steps = self.happy_steps(workspace="w2")
        steps[2] = self.panes(directories=[str(self.cwd), None, str(self.cwd)])
        result, _ = self.run_launcher(steps, ["--workspace", "w2"])
        self.assert_success(result)

    def test_server_or_protocol_failure_stops_before_mutations(self):
        for status in ({"running": False, "compatible": True},
                       {"running": True, "compatible": False},
                       {"running": True},
                       {"running": True, "compatible": True,
                        "session": "default"}):
            with self.subTest(status=status):
                result, calls = self.run_launcher([
                    step(["status", "server", "--json"], status)])
                self.assert_failure(result, "server")
                self.assertEqual(len(calls), 1)

    def test_missing_binary_and_invalid_inputs_never_call_herdr(self):
        result, calls = self.run_launcher([], env={**self.env, "PATH": ""})
        self.assert_failure(result, "input")
        self.assertEqual(calls, [])
        for extra in (["--name", "--bad"], ["--timeout", "0"],
                      ["--timeout", "300001"], ["--name", "bad\nname"],
                      ["--workspace", "-bad"]):
            with self.subTest(extra=extra):
                result, calls = self.run_launcher([], extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])
        result, calls = self.run_launcher([], cwd=self.directory / "absent")
        self.assert_failure(result, "input")
        self.assertEqual(calls, [])

    def test_incomplete_start_uses_one_get_but_missing_mapping_does_not(self):
        steps = self.happy_steps()
        incomplete = dict(self.agent)
        del incomplete["interactive_ready"]
        steps[-2] = self.start(agent=incomplete)
        steps.insert(-1, step(["agent", "get", "worker"], response(
            "agent_info", agent=self.agent)))
        result, calls = self.run_launcher(steps)
        self.assert_success(result)
        self.assertEqual(sum(call[2:4] == ["agent", "get"] for call in calls), 1)
        del self.agent["agent_session"]
        result, calls = self.run_launcher(self.happy_steps())
        self.assertIsNone(self.assert_success(result)["session"])
        self.assertFalse(any(call[2:4] == ["agent", "get"] for call in calls))

    def test_contradictory_start_metadata_never_reads_or_prompts(self):
        for key, value in (("agent", "claude"), ("pane_id", "w2:p9"),
                           ("foreground_cwd", "/wrong"),
                           ("interactive_ready", False), ("name", "other"),
                           ("workspace_id", "w9"), ("tab_id", "w2:t9"),
                           ("agent_status", "working")):
            with self.subTest(key=key):
                steps = self.happy_steps()[:-1]
                steps[-1] = self.start(agent={**self.agent, key: value})
                result, calls = self.run_launcher(steps)
                data = self.assert_failure(result, "start")
                self.assertEqual(data["resources"]["pane_id"], "w2:p1")
                self.assertEqual(len(calls), len(steps))

    def test_output_is_bounded_and_does_not_hide_empty_startup(self):
        for text in ("", "x" * 20000, "line\n" * 100):
            with self.subTest(length=len(text)):
                steps = self.happy_steps()
                steps[-1] = self.read(text)
                result, _ = self.run_launcher(steps)
                data = self.assert_success(result)
                self.assertLessEqual(len(data["startup"]), 8000)
                self.assertLessEqual(len(data["startup"].splitlines()), 80)
                self.assertEqual(data["startup_truncated"], len(text) > 8000
                                 or len(text.splitlines()) >= 80)
                self.assertEqual(data["inspection_required"], True)
                self.assertEqual(data["startup_empty"], not bool(text.strip()))

    def test_failures_at_every_stage_never_retry_or_echo_raw_errors(self):
        stages = ["server", "name", "create", "start", "inspect"]
        happy = self.happy_steps()
        for index, stage in enumerate(stages):
            for failure in ({"output": "PRIVATE_PAYLOAD", "code": 1},
                            {"output": '{"PRIVATE_PAYLOAD":'},
                            {"output": json.dumps({"error": "PRIVATE_PAYLOAD"})}):
                # agent read is intentionally plain text, not a JSON response.
                if stage == "inspect" and not failure.get("code"):
                    continue
                with self.subTest(stage=stage, failure=failure):
                    steps = happy[:index + 1]
                    steps[-1] = {**steps[-1], **failure,
                                 "stderr": "PRIVATE_PAYLOAD"}
                    result, calls = self.run_launcher(steps)
                    data = self.assert_failure(result, stage)
                    self.assertEqual(calls, [item["args"] for item in steps])
                    self.assertEqual(data["mutation_may_have_taken_effect"],
                                     index >= 2)
                    if index >= 3:
                        self.assertEqual(data["resources"]["pane_id"], "w2:p1")

    def test_claude_and_custom_timeout_keep_the_same_launch_contract(self):
        steps = self.happy_steps()
        agent = {**self.agent, "agent": "claude", "agent_session": {
            "agent": "claude", "kind": "id", "value": "exact-session-id"}}
        steps[-2] = step([
            "agent", "start", "worker", "--kind", "claude", "--pane", "w2:p1",
            "--timeout", "90000"], response("agent_started", agent=agent))
        result, _ = self.run_launcher(steps, ["--kind", "claude", "--timeout", "90000"])
        data = self.assert_success(result)
        self.assertEqual(data["kind"], "claude")
        self.assertEqual(data["session"]["value"], "exact-session-id")

    def test_reviewer_model_reasoning_and_tool_flags_are_forwarded_exactly(self):
        profiles = [
            ("pi", ["--model", "openai-codex/gpt-6-astra", "--thinking", "high",
                    "--exclude-tools", "edit,write"]),
            ("claude", ["--model", "fable", "--effort", "high",
                        "--disallowed-tools", "Edit,Write,NotebookEdit"]),
        ]
        for kind, native in profiles:
            with self.subTest(kind=kind):
                prefix = "astra-review" if kind == "pi" else "fable-review"
                steps = self.prefix_steps()
                agent = {**self.agent, "name": "$GENERATED_NAME", "agent": kind,
                         "agent_session": {**self.agent["agent_session"], "agent": kind}}
                steps[-2] = step([
                    "agent", "start", "$GENERATED_NAME", "--kind", kind,
                    "--pane", "w2:p1", "--timeout", "30000", "--", *native,
                ], response("agent_started", agent=agent))
                result, calls = self.run_launcher(
                    steps, ["--kind", kind, "--", *native],
                    naming=["--name-prefix", prefix])
                data = self.assert_success(result)
                self.assertEqual(data["kind"], kind)
                self.assertRegex(data["name"], f"^{prefix}-[0-9a-f]{{12}}$")
                expected = json.loads(json.dumps(steps).replace(
                    "$GENERATED_NAME", data["name"]))
                self.assertEqual(calls, [item["args"] for item in expected])
                self.assertEqual(calls[-2][-len(native):], native)

    def test_busy_shell_then_ready_reuses_the_same_fresh_pane_and_name(self):
        for workspace in (None, "w2"):
            with self.subTest(workspace=workspace):
                steps = self.prefix_steps(workspace=workspace)
                start = steps[-2]
                start["args"] += ["--", "--thinking", "high"]
                busy = {**start, "output": "", "code": 1, "stderr": json.dumps({
                    "id": "cli:agent:start", "error": {
                        "code": "agent_pane_busy", "message": "PRIVATE_PAYLOAD"}})}
                steps[-2:-1] = [busy, busy, start]
                extra = ["--", "--thinking", "high"]
                if workspace:
                    extra = ["--workspace", workspace, *extra]
                result, calls = self.run_launcher(
                    steps, extra, naming=["--name-prefix", "worker"])
                data = self.assert_success(result)
                starts = [call for call in calls if call[2:4] == ["agent", "start"]]
                self.assertEqual(len(starts), 3)
                self.assertTrue(all(call == starts[0] for call in starts))
                self.assertEqual(starts[0][4], data["name"])
                creates = [call for call in calls if call[3] == "create"]
                self.assertEqual(len(creates), 1)

    def test_structured_herdr_errors_are_preserved_without_general_retries(self):
        for code in ("agent_not_ready", "timeout", "agent_start_input_failed",
                     "agent_start_transport_failed", "agent_name_taken",
                     "agent_pane_unavailable", "unsupported_agent_kind"):
            with self.subTest(code=code):
                steps = self.happy_steps()[:-1]
                steps[-1].update(output="", code=1, stderr=json.dumps({
                    "id": "cli:agent:start", "error": {
                        "code": code, "message": "PRIVATE_PAYLOAD"}}))
                result, calls = self.run_launcher(steps)
                data = self.assert_failure(result, "start")
                self.assertEqual(data["herdr_code"], code)
                self.assertEqual(calls, [item["args"] for item in steps])

    def test_uncorrelated_or_malformed_busy_errors_are_never_retried(self):
        busy = {"id": "cli:agent:start", "error": {
            "code": "agent_pane_busy", "message": "PRIVATE_PAYLOAD"}}
        cases = [
            {"output": json.dumps(busy), "stderr": "", "code": 1},
            {"output": "", "stderr": json.dumps(busy), "code": 2},
            {"output": "", "stderr": json.dumps({**busy, "id": "cli:agent:wait"}), "code": 1},
            {"output": "", "stderr": json.dumps({**busy, "result": {}}), "code": 1},
            {"output": "", "stderr": json.dumps({"id": "cli:agent:start", "error": {
                "code": "agent_pane_busy"}}), "code": 1},
            {"output": "unexpected", "stderr": json.dumps(busy), "code": 1},
            {"output": "", "stderr": "warning\n" + json.dumps(busy), "code": 1},
            {"output": "", "stderr": json.dumps({"id": "cli:agent:start", "error": {
                "code": "PRIVATE_PAYLOAD", "message": "PRIVATE_PAYLOAD"}}), "code": 1},
        ]
        for failure in cases:
            with self.subTest(failure=failure):
                steps = self.happy_steps()[:-1]
                steps[-1].update(failure)
                result, calls = self.run_launcher(steps)
                self.assert_failure(result, "start")
                self.assertEqual(calls, [item["args"] for item in steps])

    def test_busy_then_uncertain_failure_stops_without_retry(self):
        busy = json.dumps({"id": "cli:agent:start", "error": {
            "code": "agent_pane_busy", "message": "PRIVATE_PAYLOAD"}})
        for failure in (
            {"code": 1, "output": "", "stderr": json.dumps({
                "id": "cli:agent:start", "error": {
                    "code": "timeout", "message": "PRIVATE_PAYLOAD"}})},
            {"code": 1, "output": "", "stderr": "PRIVATE_PAYLOAD"},
            {"code": 0, "output": '{"PRIVATE_PAYLOAD":', "stderr": ""},
        ):
            with self.subTest(failure=failure):
                steps = self.happy_steps()[:-1]
                start = steps[-1]
                steps[-1:] = [
                    {**start, "code": 1, "output": "", "stderr": busy},
                    {**start, **failure},
                ]
                result, calls = self.run_launcher(steps)
                data = self.assert_failure(result, "start")
                self.assertTrue(data["mutation_may_have_taken_effect"])
                self.assertEqual(len(calls), 5)
                self.assertEqual(calls[-1], calls[-2])

    def test_busy_wait_is_bounded_and_retains_created_resources(self):
        clock = [0.0]
        initial = iter(self.happy_steps()[:3])
        busy = json.dumps({"id": "cli:agent:start", "error": {
            "code": "agent_pane_busy", "message": "PRIVATE_PAYLOAD"}})

        def run(argv, **kwargs):
            if argv[3:5] != ["agent", "start"]:
                item = next(initial)
                self.assertEqual(argv[1:], item["args"])
                return CommandResult(0, item["output"], "")
            return CommandResult(1, "", busy)

        def sleep(seconds):
            clock[0] += seconds

        with patch.object(client, "SHELL_READY_TIMEOUT", 0.25), \
             patch.object(client.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(client.time, "sleep", side_effect=sleep), \
             patch.object(client, "run_bounded", side_effect=run) as process, \
             patch.object(launcher.shutil, "which", return_value="herdr"), \
             patch.object(cli.sys, "stdout") as stdout, \
             patch.object(cli.sys, "stderr") as stderr:
            code = cli.main(["--cwd", str(self.cwd), "--name", "worker", "--kind", "pi"])
        self.assertEqual(code, 1)
        self.assertFalse(stdout.write.called)
        self.assertAlmostEqual(clock[0], 0.25)
        output = "".join(call.args[0] for call in stderr.write.call_args_list)
        self.assertNotIn("PRIVATE_PAYLOAD", output)
        data = json.loads(output)
        self.assertEqual(data["herdr_code"], "agent_pane_busy")
        self.assertTrue(data["mutation_may_have_taken_effect"])
        self.assertEqual(data["resources"]["pane_id"], "w2:p1")
        self.assertEqual(data["requested_name"], "worker")
        self.assertLessEqual(process.call_count, 8)
        starts = [call.args[0] for call in process.call_args_list
                  if call.args[0][3:5] == ["agent", "start"]]
        self.assertGreaterEqual(len(starts), 2)
        self.assertTrue(all(call == starts[0] for call in starts))

    def test_busy_error_outside_start_never_retries(self):
        for index, stage in ((2, "create"), (4, "inspect")):
            with self.subTest(stage=stage):
                steps = self.happy_steps()[:index + 1]
                steps[-1].update(output="", code=1, stderr=json.dumps({
                    "id": "cli:agent:start", "error": {
                        "code": "agent_pane_busy", "message": "PRIVATE_PAYLOAD"}}))
                result, calls = self.run_launcher(steps)
                data = self.assert_failure(result, stage)
                self.assertEqual(data["herdr_code"], "agent_pane_busy")
                self.assertEqual(len(calls), len(steps))

    def test_names_and_timeout_follow_installed_herdr_limits(self):
        for name in ("Uppercase", "1agent", "agent.dot", "agent:name", "x" * 33):
            with self.subTest(name=name):
                result, calls = self.run_launcher([], naming=["--name", name])
                self.assert_failure(result, "input")
                self.assertEqual(calls, [])
        for name in ("a", "a" * 32, "a_b-c1"):
            with self.subTest(valid_name=name):
                steps = self.happy_steps()
                for item in steps:
                    item["args"] = [name if arg == "worker" else arg
                                    for arg in item["args"]]
                    item["output"] = item["output"].replace('"worker"', json.dumps(name))
                result, _ = self.run_launcher(steps, naming=["--name", name])
                self.assertEqual(self.assert_success(result)["name"], name)
        for timeout in (1, 3000, 300001):
            with self.subTest(timeout=timeout):
                result, calls = self.run_launcher([], ["--timeout", str(timeout)])
                self.assert_failure(result, "input")
                self.assertEqual(calls, [])
        for timeout in (3001, 300000):
            with self.subTest(timeout=timeout):
                steps = self.happy_steps()
                steps[-2]["args"][-1] = str(timeout)
                result, _ = self.run_launcher(steps, ["--timeout", str(timeout)])
                self.assert_success(result)

    def test_startup_warnings_and_drafts_are_returned_for_judgment(self):
        text = "Authentication required\nWrong model loaded\n> Human draft"
        steps = self.happy_steps()
        steps[-1] = self.read(text)
        result, _ = self.run_launcher(steps)
        data = self.assert_success(result)
        self.assertEqual(data["startup"], text)
        self.assertTrue(data["inspection_required"])

    def test_partial_creation_retains_known_ids_without_starting(self):
        for root in ({"workspace_id": "w2", "tab_id": "w2:t1"},
                     {"pane_id": "w2:p1"}, None):
            with self.subTest(root=root):
                steps = self.happy_steps()[:3]
                steps[-1]["output"] = json.dumps(response(
                    "workspace_created", workspace={"workspace_id": "w2"},
                    tab={"tab_id": "w2:t1"}, root_pane=root))
                result, calls = self.run_launcher(steps)
                data = self.assert_failure(result, "create")
                self.assertEqual(data["resources"]["workspace_id"], "w2")
                self.assertEqual(data["resources"]["tab_id"], "w2:t1")
                if root and "pane_id" in root:
                    self.assertEqual(data["resources"]["pane_id"], "w2:p1")
                self.assertEqual(len(calls), 3)

    def test_conflicting_creation_workspace_never_starts_agent(self):
        steps = self.happy_steps(workspace="w2")[:4]
        steps[-1]["output"] = json.dumps(response("tab_created", root_pane={
            "workspace_id": "w9", "tab_id": "w9:t1", "pane_id": "w9:p1"}))
        result, calls = self.run_launcher(steps, ["--workspace", "w2"])
        self.assert_failure(result, "create")
        self.assertEqual(len(calls), 4)

    def test_incomplete_get_is_not_retried(self):
        for metadata in ({}, {**self.agent, "interactive_ready": False}, None):
            with self.subTest(metadata=metadata):
                steps = self.happy_steps()[:-1]
                steps[-1] = self.start(agent={})
                steps += [step(["agent", "get", "worker"], response(
                    "agent_info", agent=metadata))]
                result, calls = self.run_launcher(steps)
                self.assert_failure(result, "start")
                self.assertEqual(len(calls), 5)

    def test_invalid_session_mapping_falls_back_without_echoing_metadata(self):
        for mapping in ({"kind": "future", "value": "PRIVATE_PAYLOAD"},
                        {"agent": "other", "kind": "path", "value": "PRIVATE_PAYLOAD"},
                        {"agent": "pi", "kind": "path", "value": ""}):
            with self.subTest(mapping=mapping):
                self.agent["agent_session"] = mapping
                result, calls = self.run_launcher(self.happy_steps())
                self.assertIsNone(self.assert_success(result)["session"])
                self.assertEqual(len(calls), 5)

    def test_unsupported_discovery_shapes_stop_without_creating_resources(self):
        for payload in (response("agent_list", agents=[{}]),
                        response("agent_list", agents="PRIVATE_PAYLOAD")):
            with self.subTest(payload=payload):
                steps = self.happy_steps()[:2]
                steps[-1]["output"] = json.dumps(payload)
                result, calls = self.run_launcher(steps)
                data = self.assert_failure(result, "name")
                self.assertFalse(data["mutation_may_have_taken_effect"])
                self.assertEqual(len(calls), len(steps))
        for payload in (response("pane_list", panes=[{"workspace_id": "w9"}]),
                        response("pane_list", panes=[None])):
            with self.subTest(payload=payload):
                steps = self.preflight() + [self.panes()]
                steps[-1]["output"] = json.dumps(payload)
                result, calls = self.run_launcher(steps, ["--workspace", "w2"])
                data = self.assert_failure(result, "workspace")
                self.assertFalse(data["mutation_may_have_taken_effect"])
                self.assertEqual(len(calls), len(steps))

    def test_transport_failures_have_bounded_diagnostics(self):
        for error in (OSError("PRIVATE_PAYLOAD"), ValueError("PRIVATE_PAYLOAD"),
                      UnicodeError("PRIVATE_PAYLOAD"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                with patch.object(client, "run_bounded", side_effect=error), \
                     patch.object(launcher.shutil, "which", return_value="herdr"), \
                     patch.object(cli.sys, "stderr") as stderr:
                    code = cli.main(["--cwd", str(self.cwd), "--name",
                                        "worker", "--kind", "pi"])
                self.assertEqual(code, 1)
                output = "".join(call.args[0] for call in stderr.write.call_args_list)
                self.assertNotIn("PRIVATE_PAYLOAD", output)
                self.assertFalse(json.loads(output)["mutation_may_have_taken_effect"])

    def test_timeout_at_mutation_stages_is_uncertain_without_retry(self):
        # Inject subprocess timeouts in-process to avoid slow wall-clock tests.
        for index in range(5):
            with self.subTest(index=index):
                responses = [CommandResult(
                    0, item["output"], "")
                    for item in self.happy_steps()[:index]]
                responses.append(subprocess.TimeoutExpired(
                    "herdr", 1, output="PRIVATE_PAYLOAD"))
                with patch.object(client, "run_bounded", side_effect=responses) as run, \
                     patch.object(launcher.shutil, "which", return_value="herdr"), \
                     patch.object(cli.sys, "stdout") as stdout, \
                     patch.object(cli.sys, "stderr") as stderr:
                    code = cli.main(["--cwd", str(self.cwd), "--name",
                                        "worker", "--kind", "pi"])
                self.assertEqual(code, 1)
                self.assertFalse(stdout.write.called)
                output = "".join(call.args[0] for call in stderr.write.call_args_list)
                self.assertNotIn("PRIVATE_PAYLOAD", output)
                self.assertEqual(json.loads(output)["mutation_may_have_taken_effect"],
                                 index >= 2)
                self.assertEqual(run.call_count, index + 1)
                self.assertTrue(all(call.kwargs["timeout"] > 0
                                    for call in run.call_args_list))


    def prompt_steps(self, text="Ready\n", prompt="Review the diff"):
        steps = self.happy_steps()
        steps[-1] = self.read(text)
        steps.append(step(
            ["agent", "prompt", "worker", prompt],
            response("agent_prompted", agent={**self.agent, "agent_status": "done"})))
        return steps

    def test_startup_read_stops_without_buffering_a_held_stream(self):
        steps = self.happy_steps()
        steps[-1] = {**self.read("x" * 100000), "hold": 60}
        started = time.monotonic()
        result, _ = self.run_launcher(steps, timeout=8)
        data = self.assert_success(result)
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(data["startup_truncated"])
        self.assertLessEqual(len(data["startup"]), 8000)
        self.assertNotIn("x" * 9000, result.stdout)

    def test_prompt_is_submitted_once_and_not_echoed(self):
        prompt = "Review the diff\nDO_NOT_ECHO"
        steps = self.prompt_steps(prompt=prompt)
        result, calls = self.run_launcher(
            steps, ["--prompt", prompt], allow_prompt=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["state"], "done")
        self.assertTrue(data["submitted"])
        self.assertIsNone(data["submission_blocked"])
        self.assertFalse(data["inspection_required"])
        self.assertNotIn("startup", data)
        self.assertNotIn("DO_NOT_ECHO", result.stdout)
        prompts = [call for call in calls if call[2:4] == ["agent", "prompt"]]
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0][5], prompt)
        self.assertNotIn("--wait", prompts[0])

    def test_unsafe_or_incomplete_startup_never_submits(self):
        cases = {
            "startup_empty": "",
            "startup_truncated": "x" * 20000,
            "startup_unsafe": "Authentication required\nWrong model loaded\n> Human draft",
        }
        for code, text in cases.items():
            with self.subTest(code=code):
                steps = self.happy_steps()
                steps[-1] = self.read(text)
                result, calls = self.run_launcher(steps, ["--prompt", "go"])
                data = self.assert_success(result)
                self.assertFalse(data["submitted"])
                self.assertEqual(data["submission_blocked"], code)
                self.assertTrue(data["inspection_required"])
                self.assertIn("startup", data)
                self.assertFalse(any(call[2:4] == ["agent", "prompt"] for call in calls))

    def test_prompt_failure_is_not_retried(self):
        steps = self.prompt_steps()[:-1]
        steps.append({**self.prompt_steps()[-1], "code": 1, "output": "",
                      "stderr": json.dumps({"id": "cli:agent:prompt", "error": {
                          "code": "agent_prompt_failed", "message": "PRIVATE_PAYLOAD"}})})
        result, calls = self.run_launcher(
            steps, ["--prompt", "Review the diff"], allow_prompt=True)
        data = self.assert_failure(result, "prompt")
        self.assertEqual(data["herdr_code"], "agent_prompt_failed")
        self.assertTrue(data["mutation_may_have_taken_effect"])
        self.assertEqual(data["resources"]["name"], "worker")
        self.assertEqual(sum(call[2:4] == ["agent", "prompt"] for call in calls), 1)

    def test_prompt_file_and_wait_are_forwarded_once(self):
        prompt_path = self.directory / "prompt.txt"
        prompt_path.write_text("Café review\n", encoding="utf-8")
        steps = self.happy_steps()
        steps[-1] = self.read("Ready\n")
        steps.append(step(
            ["agent", "prompt", "worker", "Café review\n", "--wait", "--timeout", "1500"],
            response("agent_prompted", agent={**self.agent, "agent_status": "idle"})))
        result, calls = self.run_launcher(
            steps, ["--prompt-file", str(prompt_path), "--wait-timeout", "1500"],
            allow_prompt=True)
        data = self.assert_success(result)
        self.assertTrue(data["submitted"])
        self.assertTrue(data["waited"])
        self.assertNotIn("response", data)
        self.assertEqual(calls[-1][-4:], ["Café review\n", "--wait", "--timeout", "1500"])

    def test_settled_wait_extracts_the_mapped_answer(self):
        session = self.directory / "session.jsonl"
        session.write_text(json.dumps({"type": "message", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "Hello World"}]
        }}) + "\n", encoding="utf-8")
        self.agent["agent_session"]["value"] = str(session)
        steps = self.prompt_steps()
        steps[-1]["args"] += ["--wait", "--timeout", "1500"]
        result, _ = self.run_launcher(
            steps, ["--prompt", "Review the diff", "--wait-timeout", "1500"],
            allow_prompt=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["response"], "Hello World")
        self.assertNotIn("DO_NOT_ECHO", result.stdout)

    def test_missing_prompt_file_never_calls_herdr(self):
        result, calls = self.run_launcher(
            [], ["--prompt-file", str(self.directory / "missing.txt")])
        self.assert_failure(result, "input")
        self.assertEqual(calls, [])

    def test_wait_timeout_without_prompt_never_calls_herdr(self):
        result, calls = self.run_launcher([], ["--wait-timeout", "1000"])
        self.assert_failure(result, "input")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
