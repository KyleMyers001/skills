"""Launch contracts against a fake CLI; never launch real agents."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/launch-agent.py"
FAKE = r'''import json, os, re, sys
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
sys.stderr.write(step.get("stderr", ""))
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

    def workspaces(self, ids=()):
        return step(["workspace", "list"], response(
            "workspace_list", workspaces=[
                {"workspace_id": value, "label": "duplicate"} for value in ids]))

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
        args += ["--cwd", str(self.cwd), "--label",
                 "worker" if existing else self.cwd.name, "--no-focus"]
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

    def happy_steps(self, existing=False, native=()):
        steps = self.preflight() + [self.workspaces(["w2"] if existing else [])]
        if existing:
            steps += [self.panes()]
        return steps + [self.creation(existing), self.start(native), self.read()]

    def prefix_steps(self, existing=False):
        steps = self.happy_steps(existing=existing)
        for item in steps:
            item["args"] = ["$GENERATED_NAME" if arg == "worker" else arg
                            for arg in item["args"]]
            item["output"] = item["output"].replace(
                '"worker"', '"$GENERATED_NAME"')
        steps[1] = step(["agent", "list"], response(
            "agent_list", agents=[{"name": "worker"}]))
        return steps

    def run_launcher(self, steps, extra=(), cwd=None, env=None, naming=None):
        self.steps_file.write_text(json.dumps(steps))
        if self.log.exists():
            self.log.write_text("")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--cwd", str(cwd or self.cwd),
             *(naming if naming is not None else ["--name", "worker"]),
             "--kind", "pi", *extra],
            env=env or self.env, capture_output=True, text=True,
            cwd=self.directory, timeout=15,
        )
        calls = ([json.loads(line) for line in self.log.read_text().splitlines()]
                 if self.log.exists() else [])
        self.assertNotIn("PRIVATE_PAYLOAD", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        for call in calls:
            self.assertEqual(call[:2], ["--session", "Subagents"])
            self.assertNotIn(call[2:4], [
                ["agent", "prompt"], ["agent", "stop"], ["workspace", "delete"],
                ["tab", "close"], ["pane", "run"], ["agent", "wait"]])
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

    def test_existing_workspace_creates_one_unfocused_tab(self):
        steps = self.happy_steps(existing=True)
        result, calls = self.run_launcher(steps)
        self.assert_success(result)
        self.assertEqual(calls, [item["args"] for item in steps])

    def test_explicit_workspace_skips_global_workspace_discovery(self):
        steps = self.preflight() + [self.panes(), self.creation(True),
                                    self.start(), self.read()]
        result, calls = self.run_launcher(steps, ["--workspace", "w2"])
        self.assert_success(result)
        self.assertEqual(calls, [item["args"] for item in steps])

    def test_symlink_directory_matches_live_foreground_cwd(self):
        alias = self.directory / "alias"
        alias.symlink_to(self.cwd, target_is_directory=True)
        steps = self.happy_steps(existing=True)
        steps[3] = self.panes(directories=[str(alias)])
        result, _ = self.run_launcher(steps, cwd=alias)
        self.assert_success(result)

    def test_native_arguments_are_forwarded_without_shell_evaluation(self):
        native = ["--append-system-prompt", "/tmp/Persona 'é'.md",
                  "--model", "$(touch SHOULD_NOT_EXIST); PRIVATE_PAYLOAD"]
        result, calls = self.run_launcher(
            self.happy_steps(native=native), ["--", *native])
        self.assert_success(result)
        self.assertEqual(calls[-2][-len(native):], native)
        self.assertFalse((self.directory / "SHOULD_NOT_EXIST").exists())

    def test_ambiguous_or_missing_directory_evidence_never_mutates(self):
        variants = [
            [self.workspaces(["w2", "w3"]), self.panes(), self.panes("w3")],
            [self.workspaces(["w2"]), self.panes(directories=[None])],
            [self.workspaces(["w2"]), self.panes(directories=[])],
            [self.workspaces(["w2"]), self.panes(directories=["relative"])],
            [self.workspaces(["w2", "w3"]), self.panes(),
             self.panes("w3", [None])],
        ]
        for discovery in variants:
            with self.subTest(discovery=discovery):
                steps = self.preflight() + discovery
                result, calls = self.run_launcher(steps)
                self.assert_failure(result, "workspace")
                self.assertEqual(calls, [item["args"] for item in steps])

    def test_explicit_workspace_must_match_directory(self):
        steps = self.preflight() + [self.panes(directories=["/elsewhere"])]
        result, calls = self.run_launcher(steps, ["--workspace", "w2"])
        self.assert_failure(result, "workspace")
        self.assertEqual(len(calls), 3)

    def test_labels_and_stale_cwd_do_not_select_workspace(self):
        steps = self.preflight() + [self.workspaces(["w1", "w2"]),
                                   self.panes("w1", ["/elsewhere"]),
                                   self.panes(), self.creation(True),
                                   self.start(), self.read()]
        result, _ = self.run_launcher(steps)
        self.assert_success(result)

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
        for existing in (False, True):
            with self.subTest(existing=existing):
                steps = self.prefix_steps(existing=existing)
                result, calls = self.run_launcher(
                    steps, naming=["--name-prefix", "worker"])
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
        self.assertEqual(len(calls), 5)
        self.assertEqual(data["resources"]["pane_id"], "w2:p1")

    def test_name_generation_checks_registry_and_is_bounded(self):
        spec = importlib.util.spec_from_file_location("launcher", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        occupied = "worker-" + "a" * 12
        fresh = "worker-" + "b" * 12
        with patch.object(module.uuid, "uuid4", side_effect=[
                module.uuid.UUID("a" * 32), module.uuid.UUID("b" * 32)]) as generate:
            self.assertEqual(module.choose_name(None, "worker", {occupied}), fresh)
            self.assertEqual(generate.call_count, 2)
        with patch.object(module.uuid, "uuid4",
                          return_value=module.uuid.UUID("a" * 32)) as generate:
            with self.assertRaises(module.LaunchUnavailable) as raised:
                module.choose_name(None, "worker", {occupied})
            self.assertEqual(raised.exception.code, "name_generation_failed")
            self.assertEqual(generate.call_count, 10)
        with patch.object(module.uuid, "uuid4") as generate:
            self.assertEqual(module.choose_name("worker", None, set()), "worker")
            with self.assertRaises(module.LaunchUnavailable) as raised:
                module.choose_name("worker", None, {"worker"})
            self.assertEqual(raised.exception.code, "name_exists")
            generate.assert_not_called()
        self.assertEqual(len(module.choose_name(None, "x" * 115, set())), 128)

    def test_prefix_and_exact_name_are_exclusive_and_prefix_is_bounded(self):
        for naming in ([], ["--name", "worker", "--name-prefix", "worker"]):
            with self.subTest(naming=naming):
                result, calls = self.run_launcher([], naming=naming)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(calls, [])
        for prefix in ("", "bad name", "x" * 116):
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
        steps = self.happy_steps(existing=True)
        steps[3] = self.panes(directories=[str(self.cwd), None, str(self.cwd)])
        result, _ = self.run_launcher(steps)
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
        stages = ["server", "name", "workspace", "create", "start", "inspect"]
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
                                     index >= 3)
                    if index >= 4:
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
                steps = self.happy_steps()[:4]
                steps[-1]["output"] = json.dumps(response(
                    "workspace_created", workspace={"workspace_id": "w2"},
                    tab={"tab_id": "w2:t1"}, root_pane=root))
                result, calls = self.run_launcher(steps)
                data = self.assert_failure(result, "create")
                self.assertEqual(data["resources"]["workspace_id"], "w2")
                self.assertEqual(data["resources"]["tab_id"], "w2:t1")
                if root and "pane_id" in root:
                    self.assertEqual(data["resources"]["pane_id"], "w2:p1")
                self.assertEqual(len(calls), 4)

    def test_conflicting_creation_workspace_never_starts_agent(self):
        steps = self.happy_steps(existing=True)[:5]
        steps[-1]["output"] = json.dumps(response("tab_created", root_pane={
            "workspace_id": "w9", "tab_id": "w9:t1", "pane_id": "w9:p1"}))
        result, calls = self.run_launcher(steps)
        self.assert_failure(result, "create")
        self.assertEqual(len(calls), 5)

    def test_incomplete_get_is_not_retried(self):
        for metadata in ({}, {**self.agent, "interactive_ready": False}, None):
            with self.subTest(metadata=metadata):
                steps = self.happy_steps()[:-1]
                steps[-1] = self.start(agent={})
                steps += [step(["agent", "get", "worker"], response(
                    "agent_info", agent=metadata))]
                result, calls = self.run_launcher(steps)
                self.assert_failure(result, "start")
                self.assertEqual(len(calls), 6)

    def test_invalid_session_mapping_falls_back_without_echoing_metadata(self):
        for mapping in ({"kind": "future", "value": "PRIVATE_PAYLOAD"},
                        {"agent": "other", "kind": "path", "value": "PRIVATE_PAYLOAD"},
                        {"agent": "pi", "kind": "path", "value": ""}):
            with self.subTest(mapping=mapping):
                self.agent["agent_session"] = mapping
                result, calls = self.run_launcher(self.happy_steps())
                self.assertIsNone(self.assert_success(result)["session"])
                self.assertEqual(len(calls), 6)

    def test_unsupported_discovery_shapes_stop_without_creating_resources(self):
        for index, payload in (
            (1, response("agent_list", agents=[{}])),
            (1, response("agent_list", agents="PRIVATE_PAYLOAD")),
            (2, response("workspace_list", workspaces=[{"workspace_id": "--bad"}])),
            (2, response("workspace_list", workspaces=[{"workspace_id": "w2"}] * 2)),
            (3, response("pane_list", panes=[{"workspace_id": "w9"}])),
            (3, response("pane_list", panes=[None])),
        ):
            with self.subTest(index=index, payload=payload):
                steps = self.happy_steps(existing=True)[:index + 1]
                steps[-1]["output"] = json.dumps(payload)
                result, calls = self.run_launcher(steps)
                data = self.assert_failure(result, "name" if index == 1 else "workspace")
                self.assertFalse(data["mutation_may_have_taken_effect"])
                self.assertEqual(len(calls), len(steps))

    def test_new_workspace_after_known_nonmatching_directories(self):
        steps = self.preflight() + [self.workspaces(["w1"]),
                                   self.panes("w1", ["/elsewhere"]),
                                   self.creation(), self.start(), self.read()]
        result, _ = self.run_launcher(steps)
        self.assert_success(result)

    def test_transport_failures_have_bounded_diagnostics(self):
        spec = importlib.util.spec_from_file_location("launcher", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for error in (OSError("PRIVATE_PAYLOAD"), ValueError("PRIVATE_PAYLOAD"),
                      UnicodeError("PRIVATE_PAYLOAD"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                with patch.object(module.subprocess, "run", side_effect=error), \
                     patch.object(module.shutil, "which", return_value="herdr"), \
                     patch.object(module.sys, "stderr") as stderr:
                    code = module.main(["--cwd", str(self.cwd), "--name",
                                        "worker", "--kind", "pi"])
                self.assertEqual(code, 1)
                output = "".join(call.args[0] for call in stderr.write.call_args_list)
                self.assertNotIn("PRIVATE_PAYLOAD", output)
                self.assertFalse(json.loads(output)["mutation_may_have_taken_effect"])

    def test_timeout_at_mutation_stages_is_uncertain_without_retry(self):
        # Inject subprocess timeouts in-process to avoid slow wall-clock tests.
        spec = importlib.util.spec_from_file_location("launcher", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for index in range(6):
            with self.subTest(index=index):
                responses = [subprocess.CompletedProcess(
                    item["args"], 0, item["output"], "")
                    for item in self.happy_steps()[:index]]
                responses.append(subprocess.TimeoutExpired(
                    "herdr", 1, output="PRIVATE_PAYLOAD"))
                with patch.object(module.subprocess, "run", side_effect=responses) as run, \
                     patch.object(module.shutil, "which", return_value="herdr"), \
                     patch.object(module.sys, "stdout") as stdout, \
                     patch.object(module.sys, "stderr") as stderr:
                    code = module.main(["--cwd", str(self.cwd), "--name",
                                        "worker", "--kind", "pi"])
                self.assertEqual(code, 1)
                self.assertFalse(stdout.write.called)
                output = "".join(call.args[0] for call in stderr.write.call_args_list)
                self.assertNotIn("PRIVATE_PAYLOAD", output)
                self.assertEqual(json.loads(output)["mutation_may_have_taken_effect"],
                                 index >= 3)
                self.assertEqual(run.call_count, index + 1)
                self.assertTrue(all(call.kwargs["timeout"] > 0
                                    for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
