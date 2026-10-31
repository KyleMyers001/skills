# BB thread setup and launch contract

Read before Gate Phase 1. Use `bb guide threads`, `thread-inspection`,
`thread-messaging`, `thread-lifecycle`, `environments`, and `providers` for the
installed CLI contract. Gate invocation authorizes these phase workers;
maintenance of Gate does not invoke it. No Pi-subagents or Herdr launch fallback.

## Preflight

- Resolve `bb status --json`, `bb thread show <coordinator-id> --json`, and
  `bb environment show <environment-id> --json`. Require the coordinator's
  project/environment/host and absolute product checkout to agree, with an
  unarchived coordinator in that same environment throughout the run. A ready
  managed environment must not be retiring. Wrong/missing environment or
  custodian → stop and ask; never create or substitute a checkout.
- Read [profiles](default-models.json). Require every selected role's `provider`,
  `model`, and `reasoning`; change pins there, not in prompts. Check
  `bb provider list --environment <id> --json` and
  `bb provider models <provider> --environment <id> --json` on the actual host.
  Missing models/reasoning, tools, auth, permissions, or BB access → stop;
  no fallback or automatic settings changes. Catalog presence is not auth.
- BB spawn has **no per-thread tool allowlist**. Role limits in
  [worker briefs](worker-briefs.md) are prompt-enforced, not Pi tool-denial
  guarantees or a Bash sandbox. Verify the provider supports required work and
  inherit permissions without elevation. Do not add `--permission-mode full`,
  change service tiers/global provider settings, or enable native delegation.
  Every brief prohibits nested delegation even if a provider exposes it.
- The coordinator must be able to capture worker answers and maintain Gate's
  assigned run/evidence files outside the product tree. Review/audit write no
  files; QA writes only its assigned charter/evidence/disposables. These are
  authority limits, not claims that edit tools are unavailable.

## Launch and custody

Every Gate-owned attempt is new: never fork or reuse a completed worker's
conversation. A fresh developer and fresh QA get a compact handoff on each
repair/recollection, not the parent's transcript. Review/delta/dispute/audit
always get independent fresh contexts. One QA attempt plans and executes;
never add a separate planner or planning approval roundtrip for a ready charter.

Before dispatch, save a unique launch intent (run/role/attempt, baseline, profile,
expected output path, and pending state) and complete brief under the assigned
Workbench evidence directory. Do not overwrite existing attempts. Use real
values from preflight, not remembered project defaults:

```sh
bb thread spawn --project <project-id> --environment <environment-id> \
  --parent-self --visibility visible --parent-notifications auto \
  --provider <profile.provider> --model <profile.model> \
  --reasoning-level <profile.reasoning> \
  --title '<run-id> · <role> · <attempt>' --prompt-file <brief-path> --json
```

Do not pass fork/source, new-environment, or lifecycle-owner flags. The same
explicit environment reuses the exact checkout; the coordinator remains its
live custodian. Capture stdout and exit status separately from stderr. Persist
returned BB thread ID, parent/project/environment/host IDs, role/attempt,
requested and observed execution identity, brief/output paths, and pending task
before another launch. Compare returned identity with the requested profile when
available; a mismatch stops. Disclose unavailable observations, never invent them.
Gate `run-id`, BB thread ID, and any external developer ID are distinct.

A missing receipt or interrupted dispatch is ambiguous, not a failed launch.
Reconcile `bb thread list --project <id> --parent-thread <coordinator-id> --json`
(also `--include-hidden` and `--archived` as needed), saved launch intent, and
exact-ID show/log data before recovery. Do not blindly repeat spawn. If state remains
ambiguous, stop and ask. Maintain **one writer**; all read-only workers must
return before a developer gets custody.

After launch, name the phase and link `@thread:<id>` in progress commentary.
Yield for BB's automatic parent notices. If a blocking wait is needed, use
`bb thread wait <id> --timeout <finite-bound>`; no sleep or repeated status/log
polling. A wait timeout, idle state, receipt, or completion notice is **not phase
completion**. Inspect the actual result before advancing. BB's visible worker
threads are the visibility mechanism; do not promise Codex's native waiting
indicator or a custom panel.

## Capture

For each returned attempt, inspect exact-ID `bb thread show <id> --json`,
`bb thread output <id> --json` (`{output}`), and the relevant
`bb thread log <id> --format json --after-seq <saved-seq> --limit <bound>` pages.
The show response nests identity under `.thread` and environment under
`.environment`. Record the actual completed request/turn and answer sequence,
not only the latest text. An idle turn can be a question or partial handoff;
a stale prior answer never satisfies a pending attempt. Inspect queues and
interactions with `bb thread queue list <id> --json` and
`bb thread interactions list <id> --json` when reconciling settlement.

Save the **verbatim** answer to a unique absolute response file in the evidence
directory using coordinator file tools; do not save JSON wrappers as findings,
overwrite the charter, or ask review/audit to write their own answers. Reject
missing, empty, truncated, or mismatched-turn capture as incomplete. Open/read
back the captured answer and referenced artifacts. Save source thread/turn,
completion state, classification uncertainties, identities, and custody in the
run log. A partial/failed output is not a pass. Preserve usable blocked/failed
records too; every execution record still goes to audit.

Recompute product identity after each return and QA teardown per Gate. Unexpected
drift blocks advancement; a developer's committed handoff establishes the new
baseline. If capture fails, retain the worker and stop cleanup. Coordinator
writes only its assigned Workbench records/evidence, not product source/tests
or shipping config. Do not expose secrets; if a returned answer contains them,
retain only a sanitized response, record redactions, and do not relay secrets.

## Archive

Archive only a Gate-owned worker whose completed attempt has been captured,
inspected, and recorded. Before each archive confirm:

1. Exact child ID and ownership; returned custody; settled state; no pending
   queued work/interactions or unresolved active attempt. Failed/partial workers
   with uncertain liveness are retained for reconciliation, not discarded.
2. No unexpected cascade targets: use the complete inventory below, not just
   parented descendants or previously known dependents. Gate workers should be
   leaves; any additional target is unexpected. Retain the worker for ownership
   reconciliation instead of automatically archiving the cascade.
3. An **unarchived coordinator** still occupies the exact shared product
   environment, which remains active/ready; capture and identity checks passed.

### Complete cascade inventory

Immediately before archive, capture the complete nondeleted thread inventory:

```sh
bb thread list --include-hidden --json
```

Do not add project/environment/host/parent filters or `--archived` (which selects
only archived rows). The inspected CLI lists all nondeleted rows, including
archived threads, when these filters are omitted. Verify completeness against
the installed contract; if a version bounds/paginates the result, exhaust its
supported pagination or retain the worker. Save/parse the full JSON outside chat
output limits; record only IDs and relationship fields, not unrelated titles.

Starting at the worker ID, recursively compute the visited-set closure over
all three relationships for each reached ID:

- `parentThreadId` equals the reached ID: parented descendants.
- `visibility` is `hidden` and `sourceThreadId` equals it: hidden source forks,
  even when they have no parent or have a different parent.
- `lifecycleOwnerThreadId` equals it: lifecycle-owned threads, including
  cross-project/environment/host links and threads parented elsewhere.

Include archived intermediaries; do not prune them or hidden rows before
traversal. Check every newly reached node, not just direct links. Relationship
alone is not proof of Gate ownership. Missing worker/relationship fields,
truncation, failed reads, or unproven completeness mean an incomplete inventory:
retain the worker and log pending reconciliation; no archive. An empty parent
query is never sufficient. BB's visible-only `cascadeThreadIds` preview and
`unarchivedDescendantCount` omit hidden targets and are not substitutes.

Any additional cascade target blocks automatic cleanup, even if already settled
or archived. If concurrent relationship changes are observed or unresolved,
retain the worker. This read-before-archive check is not an atomic lock against
another caller creating a relationship afterward; do not claim that guarantee.

### Apply and confirm

Run `bb thread archive <id> --json`, then confirm `.thread.archivedAt` via show
and the unchanged active environment via environment show. Record archival
state or cleanup failure. A cleanup failure is not a product failure, but it
must remain a pending reconciliation item; do not claim every child was archived.
No need to unarchive just to read retained findings. Gate's saved evidence,
not archive retention/pruning behavior, is the durable record.

Never archive the coordinator or ticket-owned developer; never delete threads,
archive an environment, or call environment cleanup. Archival is not proof an
active process stopped: BB has an archive grace window. This contract archives
only already-settled workers and never automatically stops them to force cleanup.
A shared environment with live threads does not retire; checkout environments
never retire. Losing the custodian blocks cleanup rather than risking managed
worktree teardown. Verify installed behavior if the CLI contract changes.

## Repairs and recovery

For a completed Gate-owned attempt, capture/archive, then launch a fresh
replacement with current baseline, verbatim findings/disputes, prior outputs,
charter path/revision/stable IDs, scope/permissions, pending work, and remaining
budgets. Run budgets survive fresh threads and coordinator restarts. A new QA
reads the same charter, reconciles/revises without narrowing coverage, and runs
the full charter after code remediation. This is not recovery of a live writer.

On coordinator resume reconcile saved IDs, launch intents, checkout/identity,
queues/interactions, writer custody, outputs, archive state, and budgets before
any dispatch. Unknown/active/interrupted workers are not completed attempts:
never automatically stop, retry, unarchive, or replace them. A user-stopped
worker remains stopped until Kyle explicitly authorizes continuation. Launch,
auth, provider, or permission failures stop and ask; preserve useful state.

For necessary live guidance only, after exact-ID inspection:

```sh
bb thread tell <worker-id> --mode steer --message-file <guidance-path> --json
```

A `queued` receipt is not failure: do not resend or assume delivery/compliance.
Reconcile before any further action. A missed acknowledgment or elapsed time
does not authorize replacement, cancellation, or a second writer.

**Ticket-owned developer:** the ticket coordinator retains its existing Herdr
transport/model/checkout. Send repairs through that owner and await its committed
handoff; never spawn a replacement, archive it, or reinterpret its workspace ID
as a BB thread ID. Missing owner or liveness evidence blocks. This exception does
not change Gate-owned fresh-worker rules. An in-flight **legacy Pi** Gate run
keeps its recorded contract; do not reinterpret IDs or silently migrate it.

## Named reviewers

Select Astra/Fable only when Kyle names them. Use JSON profiles and a fresh
thread for every review/delta/dispute, not the Herdr-backed Astra/Fable review
skills. Supply full comparison scope/base, acceptance, requirements, baseline,
paths, and the named review contract: serious issues, fixes, merge verdict,
and **Critical/High/Medium/Low** on every finding. Do not invent conversions to
code-critique's numeric rubric.

Before Fable, verify `claude auth status` indicates subscription authentication,
not API-key or third-party billing. Never print credentials. Ambiguous auth
stops for Kyle; do not switch harness/billing. Native Claude Code can inspect
the checkout directly (unlike the legacy handoff-only Pi adapter); require it to
inspect the checkout and report any inaccessible scope. Missing context blocks
completion, not a full-review claim from a summary. Product identity and all
read-only review constraints still apply.
