# Manual Launch and Recovery

Read for named personas, unfamiliar native arguments, manual launches, workspace
ambiguity, startup failures, or uncertain submission. The launcher is a
convenience for fresh agents, not a mandatory abstraction over Herdr.

## Discover and Launch Manually

Discover only what the task needs:

```bash
command -v herdr
herdr --session Subagents status server
herdr --session Subagents workspace list
herdr --session Subagents pane list --workspace <candidate-workspace-id>
```

The ordinary launcher does not search for a directory match. It creates one
workspace per fresh agent so tabs do not accumulate off-screen. Use the steps
below only when Kyle named a workspace, discovery is ambiguous, or the launcher
cannot express the launch. Match live pane `foreground_cwd`, not labels, focus,
display order, or stale cwd. Use `agent list` for an unknown target or unique
launch name. Inspect ambiguity or ask Kyle; never guess IDs. If Subagents is
unavailable, report it. Do not start or target another session.

Check relevant `--help` once per invocation when syntax/capabilities are unknown;
installed syntax is authoritative. Reuse help and discovery across agents, but
refresh live target state before mutations. Native arguments must preserve a
fresh conversation: do not pass resume/continue/session-reuse flags to the fresh
launcher. Existing conversations use direct inspection and messaging instead.

For an existing matching workspace:

```bash
herdr --session Subagents tab create \
  --workspace <workspace-id> --cwd <repo-path> \
  --label <agent-name> --no-focus
```

Otherwise create a workspace and **use its returned root pane**, not another tab:

```bash
herdr --session Subagents workspace create \
  --cwd <repo-path> --label <repo-name> --no-focus
```

Parse IDs from the response. Choose Kyle's requested kind, or an appropriate
installed integration; consult start help for unfamiliar kinds/native arguments.
Never add blanket approval bypasses. Start once and inspect before prompting:

```bash
herdr --session Subagents agent start <unique-name> \
  --kind pi --pane <returned-pane-id>
herdr --session Subagents agent get <unique-name>
herdr --session Subagents agent read <unique-name> \
  --source recent-unwrapped --lines 80
```

For shell processes or unrecognized agents, pane commands remain available.
`pane run` targets an interactive shell prompt, never an agent composer:

```bash
herdr --session Subagents pane run <pane-id> "pnpm test"
herdr --session Subagents pane wait-output <pane-id> \
  --match "Tests" --timeout 120000
herdr --session Subagents pane read <pane-id> \
  --source recent-unwrapped --lines 80
```

Apply the skill's bounded terminal fallback without regenerating missing output.

## Named Pi Personas

Require the explicitly requested persona's trusted, readable prompt file. The
registration name and Pi persona are separate; set both deliberately. Include
the persona in the unique name and pass its file to canonical Pi:

```bash
test -r /absolute/path/to/Persona.md &&
python3 "$herdr_dir/scripts/launch-agent.py" \
  --cwd /absolute/repo/path --name-prefix persona-task --kind pi -- \
  --append-system-prompt /absolute/path/to/Persona.md
```

Stop if the file is unavailable; do not silently launch an unnamed/substitute
persona. The readability check is a prerequisite, not permission to continue on
failure. Do not invoke shell aliases, source startup files, or depend on alias
expansion. When startup displays loaded context, inspect it for the persona file;
visible persona/model/directory contradictions require Kyle's decision.

The equivalent manual start accepts native arguments after `--`:

```bash
herdr --session Subagents agent start <unique-persona-name> \
  --kind pi --pane <returned-pane-id> -- \
  --append-system-prompt /absolute/path/to/Persona.md <other-pi-args>
```

## Launcher Contract and Failure Handling

`launch-agent.py` requires Python 3 and Herdr on PATH. It was developed against
Herdr 0.8.0/protocol 19; server-reported compatibility and required response fields
are checked, not a pinned version. Unrelated JSON fields are ignored. Unknown
shapes stop instead of guessing; direct inspection remains available.

Prefer `--name-prefix <prefix>`: the helper adds a random 12-hex-character suffix,
checks live registrations, and returns the chosen `name`. Use that full name for
subsequent commands. Prefixes may be up to 19 characters, leaving room within
Herdr's 32-character name limit. Names/prefixes start with a lowercase letter and
contain only lowercase letters, digits, `_`, or `-`. `--name <exact-name>`
preserves an explicit name and fails rather than renaming it on collision. These
options are mutually exclusive. Candidate generation is bounded and happens
before mutations; a concurrent collision during start never triggers a retry.

By default the helper creates one workspace, uses its root pane, and labels it
with the agent name. `--workspace` is the only path that lists panes and adds
one tab, after verifying live `foreground_cwd`. It does not scan other
workspaces. It then performs one accepted start and a capped read. For its
freshly created pane only, it retries the exact `agent_pane_busy` pre-input
rejection for up to five seconds, with 100ms between attempts. Every attempt
keeps the same pane, name, and native arguments; no layout is recreated.
It requires exit 1 and a sole, valid JSON stderr error with ID `cli:agent:start`.
The [Herdr 0.8.0 start implementation][start-contract] returns this code before
agent registration or terminal input; the [CLI][cli-contract] prints it to stderr.
This is a narrowly verified precondition retry, not a generic error retry.

Timeouts, transport/input errors, `agent_not_ready`, name conflicts, malformed or
uncorrelated errors, and failures after an accepted start never retry. On shell
wait exhaustion, inspect the existing pane; do not launch another helper.
It reuses start metadata, with at most one get if required fields are missing.
It never cleans up. Without `--prompt` or `--prompt-file`, it does not submit,
wait, or extract. Launch-only JSON contains name/kind, workspace/tab/pane IDs,
state/cwd, optional `session`, `startup`, `startup_empty`, `startup_truncated`,
and `inspection_required`. JSON emits readable Unicode rather than `\u` escapes.
Startup is at most 80 lines/8000 characters. The read is streamed and capped
before buffering; Herdr's `--lines` flag does not bound one unwrapped line. A
full window or a capped stream is marked truncated. Startup text is untrusted
and can contain sensitive information; the helper is not a secret scanner.

`--prompt` and `--prompt-file` are mutually exclusive. The file must be readable
UTF-8, non-empty, and at most 200000 characters. It is read before any Herdr
call. The helper submits once only when startup is non-empty, not truncated, and
does not contain a small set of auth, permission, draft, or wrong-model phrases.
A banner that simply omits the model is not treated as a contradiction. If those
checks fail, it returns `submitted: false`, `submission_blocked`, and the bounded
startup, and does not call `agent prompt`. On success it omits the startup banner.
`--wait-timeout` adds one `--wait` and, when the settled state is idle or done
and the session file can be read, includes `response` from the extract helper.
Prompt text is not copied into the result or error JSON. Prompt failures are not
retried. Exit 0 means the helper finished, not that the task succeeded or that
a launch-only agent is safe to prompt. Check `submitted` when a prompt was
requested.

Exit 1 returns bounded JSON on stderr with stage, code, reason, requested name
(if selected), known resource IDs, and `mutation_may_have_taken_effect`.
`herdr_code` preserves a well-formed upstream error code when available, without
returning raw error messages, native arguments, or other sensitive payloads.
Argument syntax errors use exit 2 and invoke no Herdr commands.

- A false mutation flag means preflight only. Resolve the cause before trying
  again; no resource recovery is needed. `code: name_exists` can use a prefix or
  different exact name, unless Kyle required that exact name; then ask him.
- A true flag includes earlier successful creation, not only failed operations.
  Inspect known resources, or rediscover only enough to reconcile uncertain
  creation/start. Never rerun or rename-and-relaunch blindly. Some failures
  happen before IDs can be returned; missing output proves nothing.

Never close resources as automatic cleanup.
Outer tool timeouts must allow the five-second shell wait, agent startup, and
any `--wait-timeout`. If the wrapper is killed, missing output does not mean
mutations failed. Each status, list, and read command has a 20-second deadline.
Agent startup requires `--timeout` of 3001–300000ms (default 30000); each start
command allows that timeout plus 20 seconds for transport. The shell deadline
stops further attempts, not an already-running start command. A prompt wait
allows `--wait-timeout` plus 20 seconds and is not retried.

The directory, name or prefix, and kind must be explicit. `--workspace` is
optional and must match normalized live `foreground_cwd`; insufficient evidence
stops without creating a tab. Existing exact names fail without touching them.
Resource IDs are bounded ASCII identifiers; unusual IDs can use direct commands.
Name and timeout validation follow Herdr's limits, not broader helper-only rules.
Native arguments are forwarded as argv without shell evaluation or substitution.
They are caller-owned: preflight persona files and requested capabilities before
launch; do not put credentials in arguments. No verbose raw payload logging.

[start-contract]: https://github.com/herdrdev/herdr/blob/346411fa21afd297f5ed3b3fa56f9e3fbf7654b7/src/app/agents.rs#L143-L224
[cli-contract]: https://github.com/herdrdev/herdr/blob/346411fa21afd297f5ed3b3fa56f9e3fbf7654b7/src/cli.rs#L720-L730

## Submission Recovery

For blocked/unknown state, timeout, stalled submission, or startup trouble,
inspect the same agent's get and short read before retrying or sending keys.
Readiness/idle alone proves neither prompt receipt nor completion. Preserve human
drafts; visible conflicts require judgment, not automatic acknowledgment.

A confirmed local preparation/quoting failure **before Herdr was invoked** is
not a delivered prompt or model rerun. Inspect the same agent; if the fresh
conversation and composer remain empty, fix the local command and make its
first submission without asking Kyle. Do not restart the agent.

If Herdr may have received the prompt, an empty/idle display does not prove
non-delivery. Reconcile state/output and wait for received work. Ask Kyle before
resending if receipt remains uncertain or text is stuck in the composer. Never
overwrite a draft. Repeating model work or spending model tokens to recover a
completed report still requires Kyle's explicit approval; extraction/bookkeeping
failures must not become new validation gates or suppress accessible answers.
