---
name: planning
description: >-
  Create grounded implementation or E2E test plans under ~/Workbench/Plans.
  Use when Kyle asks to plan, scope, design, or outline work before coding,
  requests an E2E test plan, or Gate assigns a QA Planner.
metadata:
  argument-hint: <task, ticket, or implementation goal>
---

# Planning

Apply this skill when planning is requested or the selected workflow requires it;
it does not require a plan for ordinary development edits.

## Choose the Plan Type

- **E2E test plan / Gate QA Planner:** read and follow
  [references/e2e-test-plan.md](references/e2e-test-plan.md) instead of the
  implementation workflow below. It defines the Markdown artifact, compact
  format, and handoff. Plan only; do not run tests or implement changes.
  Gate owns execution authorization and routing for its delegated planner.
- **Implementation plan (including closed-loop):** follow the workflow below.
  Initially create only the plan; after approval, follow
  [Approval & Execution](#approval--execution).

## Workflow

1. Identify the repository, goal, constraints, work item, and acceptance criteria.
   Ask about ambiguity that materially changes scope or architecture.
2. Read applicable instructions and inspect implementation, tests, configuration,
   nearby patterns, and Git status without altering project files. Ground every
   named file, symbol, and command in the repository.
3. Save directly under `~/Workbench/Plans/`:
   - With a work ID: `<WORK-ID>-<short-title>.md`, preserving the supplied ID.
   - Otherwise: `PLAN-<YYYYMMDD>-<HHMMSS>-<short-title>.md`, using local time.
   - Use a lowercase kebab-case title, normally 2–5 words. Revise an existing
     plan for the same work ID; do not rename it when its title changes.
4. Read and follow
   [references/implementation-plan.md](references/implementation-plan.md).
   Use one document; put detailed tests/operations in a final **Execution Details**
   section when needed. For a closed-loop request or known closed-loop handoff,
   also read [references/closed-loop-plan.md](references/closed-loop-plan.md).
   It adds plan requirements to the shared layout, not execution; other plans
   need only the general template.
5. Separate facts from assumptions. In **Decisions & Risks**, identify ambiguity,
   missing inputs/assets, dependencies, blockers, and choices that could
   misdirect implementation. Write `None identified.` only after checking.
   Keep material behavior, scope, approach, and file-boundary choices, verification
   gaps/reduced coverage, new dependencies, destructive actions, and required
   permissions above Execution Details. Routine implementation/test mechanics can
   stay below; moving text there is not compression.
6. Self-check that the approval portion explains the intended changes without
   reading test cases, and the whole plan works without the originating chat.
   Preserve essential background, decisions, constraints, and supporting links.
   Check the work-ID-and-name title, grounded file list, meaningful approach,
   and exact, proportionate verification commands with expected outcomes. For a
   closed-loop handoff, check the additional requirements before presenting it.
7. Run `npx -y markdownlint-cli2@latest <plan-path>` and fix every issue before
   reporting. Keep blank lines around fences and avoid inline HTML.
8. Return only the Markdown filename (no directory path) as visible inline
   code—not a Markdown link—so it is readable and copyable in the TUI. Include
   two or three summary bullets and every open decision or missing input.
   Do not create YAML/HTML siblings, derivative review artifacts, or open a
   separate review tool.

## Keep It Proportionate

- Treat the template as a default, not a form. Small plans can omit unnecessary
  scaffolding and keep brief verification in Verification Summary without Execution
  Details. Preserve essential context/files and workflow requirements.
- Keep small plans near one screen; aim for 300–500 words above Execution Details
  for substantial work. This is a guide, not permission to omit decisions.
- Give sections distinct jobs: Context & Goal explains why; Implementation
  explains changes, sequencing, and boundaries; Files lists paths with a short
  change/reason per entry, not a repeated implementation narrative.
- When verification needs detail, keep Verification Summary to a few coverage/
  outcome/limitation bullets. Put cases, commands, fixtures, and browser steps in
  Execution Details under H3 headings so Obsidian can fold them together.
- State requirements once; reference them from compact test inventories with
  expected results. Avoid paragraphs per assertion, speculative test names or
  fixture contents, and copies of workflow rules. Preserve required coverage.
- Do not duplicate full templates for implementation workflows or split their
  tests into separate files by default. The explicitly selected E2E test-plan
  mode above produces its own artifact.
- Prefer bullets to prose or tables when either works. Wrap at 80 columns where
  practical; long URLs, commands, and compact tables may exceed that width.
- Cite key ticket acceptance criteria instead of copying the whole ticket.
- Use repository-relative paths inside a repository and absolute paths outside.
- Put test-first sequencing in Implementation when meaningful tests are supported.
- Do not invent missing context. Record material unknowns or ask before
  finalizing.

## Approval & Execution

Unqualified approval of the current plan (such as “approved”) authorizes in-scope
implementation. “Approve and proceed” or “implement this plan” also suffices;
no magic wording is required. Read the entire approved revision, including
Execution Details, and continue without asking for a separate “proceed.”

The current agent implements by default. An initial request to create a plan is
not a permanent planner-only role. Explicit restrictions and established roles
still control execution:

- Approval qualified by “hold” or “review only”: preserve the restriction and
  stop. Do not reinterpret historical review-only approvals as authorization.
- “I'll hand this off”: prepare the handoff, but do not implement or launch
  anyone.
- Delegated planner-only worker: return to the coordinator, not implementation.
  An orchestrator continues by assigning its designated developer.
- Genuinely unresolved ownership (such as “maybe I'll hand this off”): ask one
  focused question.

Approval is not assignment. A receiving implementer must be assigned the work
and receive the full plan, Kyle's approval wording/source, plan path/revision,
and remaining restrictions/ownership context. Reuse existing metadata, handoff,
or workflow logs; no new artifact is required for ordinary same-session work.
A bare `Status: Approved` or finding a plan is insufficient evidence to start.
With valid inherited approval and unchanged scope, proceed without another gate.
Reconcile relevant checkout drift; ask only about missing or conflicting evidence.

Conditional approval with unresolved material changes requires resolution and
approval of the resulting revision. Approval does not activate opt-in workflows,
authorize unrequested delegation, or waive global safety and PR rules.

## Revisions

Read the current plan immediately before editing. Keep its filename and work ID;
incorporate feedback into the Markdown source under `~/Workbench/Plans/` only.
Material scope, acceptance, approach, or verification changes require incrementing
`Revision`, updating `Updated`, and fresh approval. Formatting, approval/status
notes, and ownership transfers do not change the substantive revision or require
reapproval. Keep administrative records outside a plan frozen by a workflow's
content digest; never alter approved content merely to record its approval.
