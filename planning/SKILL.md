---
name: planning
description: >-
  Create grounded general or closed-loop implementation plans under
  ~/Workbench/Plans. Use when Kyle asks to plan, scope, design, or outline work
  before coding.
metadata:
  argument-hint: <task, ticket, or implementation goal>
---

# Planning

Apply this skill when planning is requested or the selected workflow requires it;
it does not require a plan for ordinary development edits. During a planning task,
create the plan only; wait for Kyle to authorize implementation.

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
- Do not duplicate full templates for different workflows or create separate
  test-plan files by default. Add detail only for non-obvious work or risk.
- Prefer bullets to prose or tables when either works. Wrap at 80 columns where
  practical; long URLs, commands, and compact tables may exceed that width.
- Cite key ticket acceptance criteria instead of copying the whole ticket.
- Use repository-relative paths inside a repository and absolute paths outside.
- Put test-first sequencing in Implementation when meaningful tests are supported.
- Do not invent missing context. Record material unknowns or ask before
  finalizing.

## Revisions

Read the current plan immediately before editing. Keep its filename and work ID,
increment `Revision`, and update `Updated`. Incorporate feedback into the
Markdown source under `~/Workbench/Plans/` only.

Approval covers the entire revision, including Execution Details; executing
agents must read the whole plan. Revisions require fresh approval. Plan approval
alone does not authorize implementation; Kyle must also say to proceed.
