# pi-subagents setup and launch contract

Read before Gate Phase 1. Use the installed pi-subagents skill and its
`guide` sections `tool-reference/retained-children`,
`tool-reference/resume`, and `agents/tool-and-extension-selection` when needed.
Do not use Herdr launch scripts, workspace IDs, terminal banners, or CLI flags.

## One-time setup

Install pi-subagents and keep this **whole Gate directory**, including `agents/`
and `references/`. Skill discovery alone does not register its agent files.
With Kyle's approval, add the installed Gate agents directory to the existing
`subagents.agentScanDirs` array in `~/.pi/agent/settings.json`, preserving all
other settings. For example (replace the absolute path with the real install):

```json
{
  "subagents": {
    "agentScanDirs": ["/absolute/path/to/skills/orchestration/gate/agents"]
  }
}
```

Reload Pi after setup. Never edit settings or copy agents automatically during
a Gate run. Missing discovery is a prerequisite blocker. Moving an existing
Gate symlink requires explicitly repointing it; do not replace it silently.

`subagent({ action: "list", options: { capabilities: true } })` must expose:

- `gate-reader`: native Pi, `read,bash`; no edit/write or child delegation.
- `gate-planner`: native Pi, `read,bash,edit,write`; charter-only authority.
- `gate-developer`: native Pi, same tools; single repair writer.

Inspect `get` for definitions or overrides when source/tool/acceptance details
are unclear. Reader and planner acceptance roles are read-only (this controls
acceptance inference, not filesystem permissions). Developer explicitly uses
checked evidence with `review.required: false` to override the writer's inferred
review gate; Gate owns the mandatory independent delta-review.
All three inherit global/repo instructions and have no nested delegation tools.
A shadowing agent, machine placement, incompatible tools, or automatic child
review policy must be reconciled before launch, not silently accepted.

## Calls and artifacts

Load `default-models.json`; confirm native models against `action: "models"`
using exact provider/model IDs. Do not substitute another model on failure.
For the selected profile, construct the following ordinary model tool call
(placeholders are data from the profile and run log):

```javascript
subagent({
  agent: "<profile.agent>",
  model: "<profile.model>:<profile.thinking>",
  task: "<complete phase brief, absolute skill/charter paths, safety rules>",
  cwd: "<absolute product checkout>",
  worktree: false,
  async: true,
  output: "<absolute evidence directory>/<unique-phase-attempt>-response.md",
  options: { context: "fresh" }
})
```

`output` binds the response artifact; merely naming a path in the task does not.
The planner separately saves the required charter with edit/write. QA shell
artifacts stay in the evidence directory. Never request session sharing.

Persist returned worker ID and artifact paths, yield for completion, then open
the saved output. Partial/failed output is not a pass. For completed native
planner/developer/QA follow-ups, inspect exact-ID status and use:

```javascript
subagent({ action: "resume", id: "<latest worker ID>",
  message: "<scoped follow-up with current baseline and artifact paths>",
  output: "<absolute evidence directory>/<unique-follow-up>-response.md" })
```

Record the new ID immediately. Resume retains model/tools; a fresh reviewer or
auditor instead uses a new launch. Missing retained state blocks replacement.
Ticket-owned developers are resumed by their existing owner/protocol, not this
API; reconcile custody before and after each repair. No parallel writer.

## Explicit Astra / Fable selection

Do not load their Herdr-backed skills as launch instructions. Use the named
JSON profiles only on Kyle's request, preserving fresh reviews and severity
labels from Gate Phase 1. Supply full comparison scope, acceptance context, and
instructions to inspect serious issues and give a merge verdict with fixes.

Astra is native Pi with the exact pinned model and high reasoning. For both
named reviewers, supply the review contract above and Gate's severity rubric,
not the default code-critique scoring instructions.

Fable uses pi-subagents' built-in **read-only `claude-code` adapter**, not an
Anthropic/OpenRouter API model. Before launch, verify `claude auth status`
indicates subscription authentication, not API-key or third-party billing;
missing/ambiguous authentication stops for Kyle. Do not print credentials.
Inspect the adapter capabilities; launch preflight validates CLI compatibility.
Only the model/thinking override is supported: omit `options.context` and do
not pass native tools, skills, acceptance, or structured-output options.

The adapter has **no checkout tools**. Before each Fable review, the coordinator
must collect a complete, sanitized handoff for the exact baseline: full scoped
diff, relevant source/tests, requirements, and validation evidence. Give it in
the task (or read-only handoff excerpts), not just filenames it cannot open.
Hash and save that handoff under the evidence directory. Missing/truncated
context or a review requiring further repository inspection blocks completion;
collect the missing evidence for a **fresh** review or ask Kyle, never claim a
full review from an insufficient summary. Do not change billing/harness or
fall back to Herdr to retrieve more context. Record this handoff-only limitation
in the Gate report. The same pre/post product identity checks still apply.
