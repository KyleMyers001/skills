# Closed-Loop Plan Requirements

Use when the requested plan or known execution handoff is closed-loop. Apply
these requirements to the [shared layout](implementation-plan.md), not a second
full template. Planning alone does not start development or require loading the
closed-loop execution skill.

## Approval Portion

Keep handoff context, observable acceptance, approach/boundaries, expected files,
decisions, and a brief verification summary visible. Follow planning's visibility
guidance; surface missing access/infrastructure and required permissions.

Include explicit exceptions for documentation-only or genuinely inapplicable
verification layers. Missing tooling is a limitation, not proof E2E is unnecessary.
For non-Git targets, record the exception and planned file-level before/after
evidence instead of branch/PR delivery.

## Execution Details

- **Test Inventory:** map acceptance to evidence covering boundaries, failures,
  regressions, and **all unit/integration/end-to-end tests to write before
  implementation**. Use compact cases and expected results, referencing acceptance
  rather than repeating it. Do not scatter test lists through Implementation or
  Files. Record applicable manual-only scenarios and expected outcomes too.
- **Commands & Environment:** give repository-grounded commands, environment,
  and test data. For browser-visible changes include routes, actions, expected
  states, viewports, and evidence.
- **Checkpoints & Operational Details:** give finite implementation increments/
  checkpoints and applicable app startup, bounded readiness, resource ownership,
  and exact process/session cleanup. Request explicit cleanup authorization in
  the approval portion.
- Record **delivery mode and repair limits** in Checkpoints & Operational Details:
  - For Git targets, default to feature-branch PR delivery. Use
    `commit-and-handoff` only explicitly, naming the external delivery owner;
    the developer stops after the verified commit without pushing or opening a PR.
  - Default to **10 total repair cycles**, escalating after **3 consecutive cycles
    on one blocker without meaningful progress**.
  Surface non-default delivery or proposed repair-limit changes in Decisions &
  Risks for approval.

## Handoff Check

Before presenting the plan, check that it contains the applicable requirements
above or explicit exceptions. Check by substance, not heading names or preferred
phrasing; do not reformat an otherwise complete approved plan. If closed-loop is
chosen later, reconcile actual gaps rather than routinely replanning.

Follow planning's [Approval & Execution](../SKILL.md#approval--execution):
unqualified approval covers the entire revision, including execution details,
and authorizes in-scope implementation by its execution owner without a separate
“proceed.” Preserve holds, handoff restrictions, and closed-loop's opt-in gate.
Material scope, acceptance, approach, or verification changes, including filling
missing requirements, need fresh approval. Routine execution details within the
approved scope are not new product decisions.
