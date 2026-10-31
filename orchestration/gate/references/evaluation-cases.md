# Gate authoring evaluation cases

Paper scenarios, not runtime instructions or proof of a live pipeline. During
maintenance, distinguish static/paper checks from observed BB worker execution.

From the repository root, run static profile/instruction regressions with
`python3 -m unittest discover -s orchestration/gate/tests` and retained Herdr
coverage with `python3 -m unittest discover -s orchestration/herdr/tests`.
Neither suite launches workers or replaces a separately authorized live trial.

- Explicit “run the gate” selects Gate and authorizes its phase workers.
  “Update the gate skill” selects skill-authoring, not pipeline execution.
  Ordinary implementation and closed-loop do not auto-trigger Gate.
- Missing BB/provider/model/reasoning/auth, wrong project/environment/host,
  retiring environment, absent unarchived coordinator in the product environment,
  or incompatible required tools block before Phase 1. No permission elevation,
  settings rewrite, replacement checkout, fallback model, Pi, or Herdr launch.
- Profiles use BB `provider/model/reasoning`, not Pi agent names or model suffixes.
  Reviewer/QA pin Sol xhigh; auditor/developer pin Sol high. Named Astra/Fable
  retain high reasoning and require Kyle's selection. Catalogs are checked on
  the actual target environment; observed identity mismatch stops. Missing
  observations are uncertainty, not invented runtime confirmation.
- Every Gate-owned launch is a fresh visible BB thread, explicitly parented with
  automatic completion notifications in the exact project/environment. No fork,
  new worktree, implicit model, or lifecycle-owner cascade. Run and BB IDs differ.
- Role authority is prompt-enforced, not per-thread tool allowlists. Read/audit
  cannot write; QA's only writes are assigned charter/evidence and approved
  untracked non-source disposables; developer is the sole product writer.
  Available tools never imply broader authority; every brief forbids delegation.
- Save launch intent before dispatch and returned IDs/state before another launch.
  A missing receipt, timeout, silence, attention notice, idle turn, or completed
  wait does not prove phase completion or authorize another worker. Reconcile
  lost receipts/duplicate notices through saved identity and parent children.
- Yield for automatic parent notices and link visible workers. Blocking waits
  are bounded; no sleep/status/log polling or promised native Codex waiting UI.
  Worker questions/partial answers and stale prior turns do not advance phases.
- Capture exact-turn answers verbatim to unique Workbench evidence paths, inspect
  them and referenced artifacts, record custody/identity, then archive only the
  exact settled Gate-owned child. Missing/empty/truncated capture retains the
  child; never overwrite the QA charter. Secrets are redacted and disclosed as
  redactions rather than relayed verbatim.
- Before archive, confirm no queued work/interactions, unknown live attempt,
  unexpected descendants/dependents, or lost shared-environment custodian.
  Confirm archived state and unchanged active environment after cleanup. Archive
  failure remains a reconciliation item, never a product verdict. No delete,
  coordinator/external-developer archive, environment cleanup, or forced stop.
- A non-parent hidden fork references the worker through `sourceThreadId` but
  has no parent (or a different one). A lifecycle-owned thread references it
  through `lifecycleOwnerThreadId` in another project/environment/host. An
  archived intermediary links onward to live work. Even with an empty parent
  query or visible-only cascade preview, recursively discover these targets
  and retain the worker; never stop/archive the linked work automatically.
- An incomplete inventory (missing relationship keys, failed/truncated/bounded
  reads, unexhausted pagination, or missing worker) must retain the worker.
  Explicit null relationship fields differ from omitted keys. A complete leaf
  inventory permits existing settlement/capture/environment checks, not a claim
  of atomic protection against later concurrent relationship creation.
- Completed standalone developer and QA attempts are captured/archived; repairs
  use fresh threads and compact handoffs. Preserve findings/disputes, baseline,
  prior evidence, charter path/revision/IDs, permissions, and run-wide budgets.
  Unknown/active/interrupted workers instead require exact-state reconciliation;
  no automatic restart/retry/unarchive/cancel, especially after a user stop.
- Necessary live guidance uses exact-ID BB steer. A queued receipt is not failure
  or proof of compliance; never resend or replace because acknowledgment missed.
  Coordinator resume reconciles all intents/IDs/custody/queues/outputs/budgets
  before dispatch. Legacy Pi runs retain recorded transport, not ID conversion.
- Ticket handoff preserves the same waiting developer/model/checkout. Its
  coordinator owns Herdr transport; Gate routes repairs through that owner,
  never spawns another writer, archives it, or treats a workspace ID as a BB ID.
  Missing owner/liveness evidence blocks; no budget reset across workers.
- Named Astra uses its exact native Codex high-reasoning profile. Named Fable
  uses native Claude Code, subscription auth, high reasoning, and direct checkout
  inspection. No API billing, handoff-only adapter claims, missing-context pass,
  or Herdr-backed review-skill launch. Preserve severity labels/merge verdict.
- Dirty input stops before Phase 1. Read-only-worker product drift invalidates
  the run. Display-only ahead/behind changes do not invalidate content identity.
  Only charter-listed untracked non-source paths may be excluded consistently.
- Default Risk 7–10 / named Critical or High findings route to developer.
  Findings relay verbatim except secret redaction; unresolved substantive
  classification escalates. Initial review is free; at most three
  repair+commit+delta-review cycles, one fresh dispute per cycle, no severity
  downgrade, budget reset, or concurrent writer. Standing disagreement escalates.
- Flow remains review → QA (plan and execute) → audit → report → publish.
  QA saves/reads back/validates its assigned Workbench charter before checks or
  services, then executes in the same attempt. No extra planner or ready-plan
  roundtrip; chat-only/missing charter or blocking gaps prevent unsafe execution.
- Charter includes grounded standard unit/lint/typecheck commands or explicit
  gaps, acceptance/failure paths, stable IDs, scope/permissions, evidence, approved
  disposables, and teardown. Fresh QA reads the same charter and increments its
  revision on changed baseline without narrowing coverage or changing retained
  IDs. Code remediation requires the full charter rerun on the new baseline.
- Every execution record, including blocked/all-green/partial, gets a separate
  fresh auditor who opens that charter and artifacts. No-record planning blockers
  go to Kyle. Target-env failure is evidence first; infrastructure failure with
  usable records still attempts audit. If audit cannot launch, remain blocked.
- Auditor cause alone routes: code → developer → delta-review → full charter →
  fresh audit; QA/evidence → fresh QA re-collection → fresh audit;
  runtime-capability → Kyle. Two consecutive no-progress evidence rounds stop.
  Cheap read-only audit spot-checks never mutate the product.
- Teardown must restore product identity; missing authority or drift blocks.
  Bash availability never grants destructive-operation approval. Keep evidence
  and blocked reports; never clean up unknown active resources automatically.
- Clean audit alone permits Phase 5 feature-branch non-force push/PR. Never push
  main/master, merge, or deploy. No remote means verified/unpublished. Correct
  wrong PR base or escalate. Missing/pending/failed required checks are not green;
  finalize exact SHA, PR/remote/base/status with no report placeholders.
- Blocked stops still produce the Gate report with useful evidence, missing
  fields/uncertainty, exact custody, and unarchived/cleanup reconciliation work.
  Native completion/runtime acceptance is not approval or independent audit.
