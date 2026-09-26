---
name: frontend-design
description: >-
  Design and implement distinctive, production-quality web interfaces. Use when
  creating or substantially restyling a frontend; not for backend-only work or
  minor changes governed by an established design system.
---

# Frontend Design

Give each interface a deliberate visual identity grounded in its subject and
brief. Explicit requirements, existing product conventions, and user direction
always take precedence. Distinctive does not mean decorative: take an aesthetic
risk only when it serves the product.

## Establish the Direction

Before designing, identify the subject, audience, primary task, technical
constraints, and available content. Ask when missing information would
materially change the result. For an explicitly open-ended prototype, make a
reasonable choice and state it. Use preferences evidenced in the conversation
or repository; do not invent user preferences.

Derive visual choices from the subject's materials, instruments, artifacts,
and vernacular. Build with real content where available. The same treatment
should not fit an unrelated product equally well.

When the project has a design system or established visual language, take
color, type, and components from it and direct only what it leaves open.

Create a compact direction before coding:

- **Color:** four to six named values with clear roles and sufficient contrast.
- **Type:** deliberate display, body, and utility roles; one family is enough
  when it provides the needed range.
- **Layout:** the organizing idea, major alignments, density, and responsive
  behavior. Use a small ASCII wireframe when it clarifies alternatives.
- **Signature:** one memorable element that expresses the subject and supports
  the primary task. Keep it subtle or omit it on utilitarian surfaces such as
  dashboards, forms, and settings.

Critique the direction against the brief. Replace choices that are generic
habits rather than subject-specific decisions. Common defaults include cream
and terracotta editorial palettes, near-black pages with an acid accent,
broadsheet layouts, identical rounded cards, decorative gradients, and
all-caps eyebrow labels. These treatments are valid when the brief supports
them; they are not substitutes for a direction. Preserve familiar interaction
patterns when they help users understand and complete the task.

Do this planning internally unless the user asks to compare directions or a
consequential choice needs approval.

## Design Intentionally

When a page has a hero, treat it as the page's thesis. Lead with the most
characteristic and useful expression of the subject: a statement, image,
demonstration, data point, or interaction. Do not default to oversized metrics.
On task-focused views, lead with the primary task instead of adding a hero.

Use typography as part of the identity. Choose faces and roles deliberately,
set a coherent scale, and control measure, weight, spacing, and line height.
Use structural devices such as numbering, labels, rules, and dividers only when
they communicate real hierarchy or sequence.

Match execution to the direction. Maximalism needs coherent detail; minimalism
needs precise spacing and typography. Spend boldness on the signature element
and keep supporting elements disciplined.

Use motion to explain change, guide attention, or create one meaningful moment.
Prefer interaction-driven feedback over scattered entrance effects. Respect
reduced-motion preferences and omit animation when it adds no value.

## Implement Carefully

Follow the project's framework, components, and conventions. Preserve existing
behavior unless the task changes it. Derive colors, typography, spacing, and
motion from the chosen direction rather than adding one-off values.

Keep CSS predictable. Prefer low-specificity component classes, avoid broad
selectors that unintentionally affect descendants, and inspect the cascade,
source order, and overlapping spacing rules when styles conflict.

Build responsive behavior from the start. Use semantic HTML, visible keyboard
focus, readable contrast, sensible touch targets, and content that remains
usable under zoom, wrapping, loading, empty, and error states. Prefer native
controls; ensure accessible names for controls and correct keyboard and focus
behavior for custom interactions.

## Write for the Interface

Treat words as design material. Write from the user's perspective with plain,
specific language. Name things by what people recognize, not by implementation
details. Prefer active, consistent action labels: a button labeled “Publish”
should lead to confirmation that says “Published.”

Explain errors with the cause and a useful next step. Make empty states invite a
relevant action. Keep labels, examples, and supporting text to one job each;
avoid promotional filler and cleverness that obscures meaning.

## Verify and Refine

Run the project's relevant tests, lint, type checks, and build. Inspect the
rendered result at representative desktop and mobile sizes, preferably with
screenshots. Check overflow, wrapping, and hierarchy, and confirm the
accessibility, responsive, and reduced-motion requirements above. Exercise the
primary user flow, including keyboard navigation, validation, and relevant
loading, empty, and error states. If rendering or interaction checks cannot be
performed, say so and report those checks as unverified.

Compare the implementation with the brief and the chosen direction. Remove
unjustified decoration, fix generic or inconsistent choices, and verify that the
signature element supports rather than competes with the page's primary task.
