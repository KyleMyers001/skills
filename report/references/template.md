---
title: "{{Work ID}} · {{Short outcome-focused title}}"
tags:
  - report
  - "{{report-type}}"
  - "{{Work ID}}"
created: "{{YYYY-MM-DD}}"
---

# {{Work ID}} · {{Short outcome-focused title}}

**{{Implementation status}} · {{Verification status}} · {{Delivery status}}**

{{One or two sentences: what changed and why it matters.}}

## Verification

| Check | Result | Log |
| --- | --- | --- |
| Tests · `{{test command}}` | {{Result; tests/files passed}} | {{Test log link}} |
| Lint · `{{lint command}}` | {{Result}} | {{Lint log link}} |
| Types · `{{typecheck command}}` | {{Result}} | {{Typecheck log link}} |
| Build · `{{build command}}` | {{Result}} | {{Build log link}} |

<!-- Omit inapplicable rows, but retain relevant checks that failed or were not
     run. Link real logs when available; otherwise state evidence is unavailable.
     For investigations, substitute Findings and supporting evidence for checks
     that do not apply. Adapt status to the task; do not imply implementation.
     Omit the work ID from title/tags when there is none. Tags are kebab-case
     without spaces. Write `created` as an unquoted date: created: 2026-01-31 -->

## Desktop

![{{Visible desktop state}}]({{report-stem}}-assets/{{desktop}}.png)

*{{State, viewport, and one short observation.}}*

## Mobile

| {{View A}} | {{View B}} |
| --- | --- |
| ![{{Visible state A}}]({{report-stem}}-assets/{{mobile-a}}.png) | ![{{Visible state B}}]({{report-stem}}-assets/{{mobile-b}}.png) |

*{{Viewport and short comparison.}}*

<!-- Use real note-relative image paths inside the ~/Workbench/ vault, with no
     spaces (or %20). Existing in-vault evidence such as <plan-id>-evidence/ is
     referenced in place, not copied. Keep desktop images full-width and
     mobile images side by side; use one column for a single mobile capture.
     Plain embeds only: do not wrap images in clickable links.
     Omit image sections if no relevant screenshots were collected. Never
     duplicate or capture extra images just to fill a table. -->

## Remaining work & decisions

- **{{Owner}}:** {{Outstanding action or decision.}}
- **{{Owner}}:** {{Another action, decision, or blocker, if any.}}
- **Coverage:** {{Material limitation, if any.}}
- **Detail:** [{{Run log}}]({{plan-id}}-run.md)

<!-- Omit this section when nothing remains. Add operational cleanup details
     only when needed for safe continuation. Remove comments, unused sections,
     and all placeholders before delivery. -->
