# {WORK-ID} — {Plan title}

> {One sentence describing the intended outcome and why it matters.}

- **Work item:** `{Jira/work ID or generated plan ID}`
- **Repository:** `{repository or workspace}`
- **Status:** Draft
- **Revision:** 1
- **Updated:** YYYY-MM-DD

For handoffs, preserve approval wording/source, plan path/revision, and any
execution-owner or hold instructions in existing metadata, the handoff, or the
workflow log. Omit unused fields; do not ask for an owner when the default is
clear. Follow [Approval & Execution](../SKILL.md#approval--execution); recording
approval is not a substantive revision. Keep records outside a digest-bound plan.

## Context & Goal

- **Problem:** {Current behavior and why it needs to change.}
- **Outcome:** {Observable acceptance criteria. Use labels only when helpful
  for test references.}
- **Background:** {When needed: essential decisions/constraints from chat and
  supporting links so another agent can work without the conversation.}

## Decisions & Risks

- **Decision needed:** {Unresolved choice and its consequence.}
- **Missing input:** {Required asset, access, requirement, or dependency.}
- **Risk / permission:** {Meaningful tradeoff, mitigation, or authorization.}

If none apply, replace the example list with `None identified.` Follow the
planning skill's visibility guidance; consequential choices belong above the fold.

## Implementation

1. {Cohesive change, key approach, and why.}
2. {Important sequencing/integration; test-first strategy when applicable.}

- **Boundaries:** {Non-goals or behavior that must remain unchanged, when needed.}

## Files

- `{verified/path}` — {Short description of expected change and why it belongs
  here; do not repeat implementation steps.}

## Verification Summary

- {Coverage and observable outcomes, not individual test cases.}
- {Verification gaps, exceptions, and their impact; keep blockers visible.}

## Execution Details

Use this foldable section when verification or operations need detail; otherwise
keep the check in Verification Summary. Omit unused scaffolding, not requirements.
Elaborate the choices above rather than introducing material new ones. Approval
covers this section too, and executing agents must read it.

### Test Inventory

- {Requirement reference → layer, compact cases, expected results. Include all
  tests required by the workflow without repeating the requirements narrative.}

### Commands & Environment

- `{exact repository-grounded command}` — {Expected result.}
- {Required environment/data; browser routes, actions, states, viewports, and
  evidence when applicable. Surface missing prerequisites in the summary.}

### Checkpoints & Operational Details

- {Finite checkpoints, startup/readiness, resource ownership, exact cleanup,
  delivery mode, or repair limits as required by the workflow. Required
  permissions and departures from defaults must also be visible above.}
