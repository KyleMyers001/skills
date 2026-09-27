---
name: developer
description: >-
  Write simple, maintainable, tested code. Use for implementation, bug fixes,
  refactoring, or code review; avoid speculative abstractions and unrelated
  cleanup.
---

# Developer

Optimize for code another developer can understand and safely change. Explicit
requirements and project conventions take precedence over these preferences.

When editing or reviewing code, read only the matching local guide:

- TypeScript (`.ts`, `.tsx`, `.mts`, `.cts`):
  [TypeScript](references/typescript.md), not the JavaScript guide.
- JavaScript (`.js`, `.jsx`, `.mjs`, `.cjs`):
  [JavaScript](references/javascript.md).
- Python (`.py`, `.pyi`): [Python](references/python.md).

For mixed-language work, read one guide per language involved; for other
languages, read none. External source links are citations, not reading
instructions: do not fetch them for normal coding work. Consult one only if
the user explicitly requests source verification or strict compliance.

## Implementation

- Read relevant instructions, code, callers, and tests before editing. Confirm
  behavior instead of guessing. Define expected behavior and the smallest useful
  change; reuse existing conventions.
- Choose straightforward solutions. Build only what the task needs, without
  speculative configuration, extension points, dependencies, or fallbacks.
- Centralize knowledge that must stay consistent, not merely similar syntax.
  Prefer duplication over coupling unrelated behavior.
- Give functions and modules cohesive responsibilities. Prefer targeted edits;
  rewrite a file only when required or safer and clearer than precise editing.
  Avoid unrelated refactors and formatting churn.
- Use descriptive domain names and explicit inputs, outputs, and dependencies.
  Avoid hidden mutable state and surprising effects. Use guard clauses where
  helpful, not unnecessary one-line helpers.
- Model valid states with types.
- Validate untrusted input at boundaries; handle errors and async failures
  explicitly. Do not swallow failures or hide defects behind defaults. Release
  resources and keep sensitive data out of code, logs, fixtures, and reports.
- Preserve public contracts unless changing them is part of the task. Check
  callers when changing interfaces, data shapes, or error behavior.
- Comment on intent and constraints, not syntax. Remove stale comments and keep
  documentation current.
- Prefer existing dependencies and platform capabilities. Justify new
  dependencies by their benefit versus maintenance, compatibility, and security
  costs. Optimize established bottlenecks, not imagined ones.

## Size: Guidelines, Not Gates

Prefer lines under 80 columns, following the formatter. Long URLs, strings,
commands, generated content, and clearer expressions may exceed that width.

Prefer source files under 100 lines; treat 200 as a strong soft upper limit
prompting a responsibility check. Do not compress code, remove helpful spacing,
create tiny-file mazes, or restructure existing large files just to hit counts.
Extract only when useful to the current scope and adequately verified.
Generated files, fixtures, declarative data, and cohesive tests may be larger.
Do not add tooling solely to enforce these style or size preferences.

## Verification and Handoff

1. Default to **red, green, refactor**, one behavior at a time:
   - Write and run a small test before implementation. Confirm failure reflects
     missing behavior, not broken setup.
   - Implement the simplest passing solution; refactor with tests green and
     rerun afterward.
   - Start bug fixes with a reproducing regression test. Establish passing
     coverage before behavior-preserving refactors.
   - Test observable behavior, boundaries, and failures. Never weaken assertions
     just to get green.
   - If test-first is impractical, explain why and choose alternative evidence.
     Do not invent tests for documentation-only edits.
2. Use the project's package manager and scripts. Run focused tests, then
   proportionate type checks, linting, and broader tests.
3. Review the diff for accidental changes, duplication, complexity, contract
   regressions, unsafe inputs, and missing tests. Explain significant trade-offs,
   not every long line.
4. Report changes, checks actually run, failures, skipped checks, and remaining
   risks. Do not imply unperformed verification, commits, review, or merging.
