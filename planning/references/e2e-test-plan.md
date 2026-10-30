# E2E Test Plan

Use for an explicit E2E test-plan request or Gate's QA planning assignment.
The plan is an executable verification charter, not an implementation proposal
or test-results report. Inspect the repository; do not run product checks, start
services, author tests, or change product files while planning. Standalone plan
requests end at handoff. Gate QA saves and validates the charter first, then
executes in the same phase under Gate's explicit authority, without a separate
planner handoff or planning roundtrip.

## Artifact & Handoff

- Save one Markdown file directly under `~/Workbench/Plans/` before replying.
  For Gate, use `<run-id>-e2e-test-plan.md`; otherwise use
  `<WORK-ID>-e2e-test-plan.md` or
  `PLAN-<YYYYMMDD>-<HHMMSS>-<short-title>-e2e-test-plan.md` (local time).
  Never overwrite an implementation plan or a different run's plan.
- Read before revising the same plan. Increment **Revision** and update
  **Updated** for changes to scope, baseline, steps, assertions, or evidence.
  Gate controls execution; a plan's status is not approval or a test verdict.
- If `edit`/`write` are excluded, shell writes are allowed **only to the assigned
  plan file** (and creating `~/Workbench/Plans/` if needed), never product files.
  If the artifact cannot be saved, report the blocker; chat is not a substitute.
- Run `npx -y markdownlint-cli2@latest <plan-path>` and fix issues. This validates
  the document, not the product. If unavailable, disclose the validation gap.
- Return the filename as inline code plus at most three short bullets: coverage,
  blockers/decisions (or none), and validation gaps if any. For an agent handoff,
  also return the absolute plan path and revision. Gate QA includes these in its
  execution return; it need not return between planning and execution unless
  blocked. Do not paste the plan into chat or create HTML/YAML siblings.

## Content Rules

- Use the compact layout below. Aim for 300–600 words for ordinary changes;
  expand only for distinct risks. Prefer bullets and numbered steps, not wide
  tables, repeated requirements, or paragraphs per assertion.
- Cover acceptance-critical journeys and failure paths at a real boundary.
  Prefer existing E2E tooling and the smallest viable path. Ground exact commands,
  routes, fixtures, and expected results in the repository; never invent them.
- Include the repository's documented standard unit/lint/typecheck commands
  unless Kyle scoped them out. Cite their source (package scripts, Makefile, CI).
  Record absent tooling, reduced coverage, and missing prerequisites as gaps,
  not silent omissions. Blocking gaps prevent execution; save the blocked plan.
- Use stable IDs (`CHECK-01`, `E2E-01`) that the executor and auditor can reuse.
  Each check/case needs actions, observable assertions, and an evidence path
  with what it proves. Screenshots prove visible state only, not persistence,
  API behavior, or backend effects. Never include secrets.
- Keep scope and blockers above execution details. Put shared setup once;
  cases reference it. Include explicit exclusions and required permissions.
  Do not shrink necessary coverage just to meet the word-count guide.

## Compact Layout

Replace placeholders and omit inapplicable optional fields. For Gate, retain
run ID, baseline, run-log identity reference, and the run's evidence directory.
Use paths relative to that evidence directory for individual artifacts.

```markdown
# {Work ID} — E2E Test Plan: {Short title}

- **Repository:** {absolute checkout path}
- **Run:** {Gate run ID, if applicable}
- **Baseline:** {full HEAD; link to run-log product identity, or non-Git baseline}
- **Evidence:** {absolute evidence directory}
- **Status:** Ready for execution / Blocked — not a test result
- **Revision:** 1
- **Updated:** YYYY-MM-DD

## Scope & Blockers

- **Verify:** {acceptance references, real boundary, and critical outcomes}
- **Exclude:** {out of scope and reason, or none}
- **Blockers / permissions:** {missing inputs, gaps, decisions, or none}

## Execution Details

### Setup

- {environment, access, fixtures, exact startup command, bounded readiness check}
- {record pre-run product identity; reference Gate's run-log method}

### Standard Checks

- **CHECK-01 — {name}:** `{exact command}` (source: `{path}`) →
  {expected result}; evidence: `{artifact}` proves {claim}.

### E2E-01 — {Journey or failure path}

- **Covers:** {acceptance reference and verification surface}
- **Steps:**
  1. {specific action/command and data; reference shared setup}
  2. {next action at the real boundary}
- **Expect:** {observable assertions, including exact values where required}
- **Evidence:** `{artifact}` proves {claim}; {additional artifact only if needed}.

### Teardown & Identity

- **Disposable paths:** {explicit untracked non-source paths, or none}
- **Cleanup:** {exact commands, owned resources, required authorization}
- **Identity:** {post-teardown check; must match pre-run identity for Gate}
```

Repeat only the check line or case block as needed, in execution order. Ensure
cleanup restores the baseline, including on failure; never assume permission
for destructive cleanup. Evidence belongs in the evidence directory, not the
product checkout or the plan. Before handing off, read the saved file and check
that another agent can execute it without the originating chat.
