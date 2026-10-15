---
name: skill-authoring
description: >-
  Create or revise agent skills for clear triggers, concise instructions, and
  low context cost. Use when authoring a SKILL.md or tightening an existing
  skill; not for executing skills or editing ordinary documentation.
---

# Skill Authoring

Write the smallest skill that reliably changes behavior. Optimize for human
readability and correct execution, not the fewest possible tokens.

## Skill Structure

Use the [Agent Skills format](https://agentskills.io/home); its
[specification](https://agentskills.io/specification) defines the portable
structure. A skill is a directory, not a standalone Markdown file:

```text
my-skill/
├── SKILL.md       # Required: YAML frontmatter + Markdown instructions
├── scripts/       # Optional: executable helpers
├── references/    # Optional: documentation loaded when needed
└── assets/        # Optional: templates, images, or other static resources
```

Only `SKILL.md` is required; create supporting directories only when useful.
Other files and directories are allowed. Keep the whole skill directory together
when distributing or installing it; discovery paths depend on the harness.

Start `my-skill/SKILL.md` with this minimal shape:

```markdown
---
name: my-skill
description: Summarize deployment logs. Use when reviewing a deployment result.
---

# Deployment Log Review

1. Read the supplied logs; request them if missing.
2. Report the outcome and cite relevant log lines; flag uncertainty.
```

`name` and `description` are required; see their constraints below. Optional
standard fields include `license`, `compatibility`, `metadata`, and experimental
`allowed-tools`. Check the specification before using them and the harness docs
for support or extensions; do not treat harness-specific fields as universal.
The Markdown body has no mandatory headings: organize it around the workflow,
not a fixed template. Metadata supports discovery, the body loads on activation,
and supporting resources are read or run only when needed.

## Workflow

1. **Scope:** identify the task, triggering requests, expected output, and
   boundaries with neighboring skills. Inspect relevant existing skills and
   the target harness's documentation. Ask only about consequential unknowns.
2. **Draft:** create `<name>/SKILL.md` using the structure above. Give it a short
   description and an actionable workflow. Include prerequisites, decision
   points, failure/stop conditions, and output expectations only where they
   affect the task.
3. **Trim:** remove explanations the model already knows, repeated rules,
   redundant examples, and instructions that do not change decisions or output.
   When revising, preserve behavior unless a change was requested.
4. **Verify:** check format and walk through representative requests using the
   checklist below. Fix ambiguity before adding more prose.
5. **Report:** give the file path, meaningful changes, and checks actually run.
   Distinguish static review from real execution; flag anything unverified.

## Writing Rules

- Describe **what it does and when to load it** in one or two sentences.
  Add an exclusion when it prevents likely overlap. Names and descriptions
  occupy baseline context; full instructions load on demand.
- Use direct verbs, short sections, and plain English. Avoid role-play,
  motivational filler, cryptic shorthand, and exhaustive hypothetical cases.
- Include only task-specific knowledge or constraints. Do not copy global
  instructions; reference shared guidance only when necessary.
- Preserve safeguards, prerequisites, exceptions, and precise commands.
  Brevity never justifies removing a necessary approval or verification step.
- Aim for roughly **300–600 words or fewer** in `SKILL.md`; this is a soft
  guide, not a minimum or a reason to fragment a cohesive workflow.
- Move substantial examples or rare procedures into `references/` only when
  useful. Link directly from `SKILL.md`, saying exactly when to read each file.
  Resolve relative paths from the skill directory, not the working directory.
- Add scripts only for repeated, deterministic work; document dependencies
  and test them. Do not create scaffolding or tooling by default.

## Verification Checklist

- `<name>/SKILL.md` starts with YAML frontmatter delimited by `---`, followed
  by Markdown instructions. Frontmatter contains `name` and a non-empty
  `description` (at most 1024 characters). Use a matching directory name:
  1–64 lowercase ASCII letters, digits, or hyphens; no leading, trailing, or
  consecutive hyphens.
- Referenced files exist; commands, paths, and harness-specific behavior are
  verified rather than invented. Avoid name collisions with installed skills.
- Walk through a matching request, a near-miss that must not trigger, and an
  edge case or missing prerequisite. Check both selection and expected output.
  Use safe execution where practical; label paper walkthroughs as such.
- For revisions, compare against the original for lost constraints. Report
  word counts when evaluating concision; do not present them as token counts.
