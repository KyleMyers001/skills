---
name: report
description: >-
  Write or revise an Obsidian report when Kyle explicitly requests one, or at
  the final handoff to Kyle of substantial development work or an implementation
  pipeline. Not for agent-to-agent handoffs or intermediate pipeline stages.
  Standalone code reviews, investigations, verification-only runs, and small
  fixes stay in chat unless Kyle explicitly requests a report.
---

# Report

## When to Use

- Automatic use: final handoff to Kyle of a feature, significant refactor,
  coordinated implementation, or implementation pipeline, completed or blocked.
  Honor chat-only/no-report requests; when scope is uncertain, stay in chat.
- Explicit request only: standalone reviews (code, PR, design, visual),
  investigations, verification-only runs, and small fixes. Asking to summarize
  or share findings is not a report request; neither is the agent deciding
  to save Markdown.
- Agent handoffs and intermediate pipeline stages stay in the response.
  Run logs, orchestration logs, and plans are not reports.

## Format

Use one adaptable format, not separate templates for every scenario.

The reader is Kyle, in Obsidian. The vault is `~/Workbench/`; files outside it
do not render. Run logs and orchestration logs are audit trails, not reports:
link them, never copy them in.

## Workflow

1. Read any existing report and relevant evidence. Establish what changed,
   what was actually verified, and what remains. Report writing does not
   authorize new tests, browser runs, delivery, or cleanup.
2. Read [references/template.md](references/template.md) before drafting.
   For a completed visual example, read
   [references/example.md](references/example.md). Treat its results as
   historical, never as evidence for new work.
3. Save reports under `~/Workbench/Reports/`. Use `<WORK-ID>-<short-title>.md`
   when an ID exists; otherwise use `<YYYY-MM-DD>-<short-title>.md`. Revise the
   existing report for the same purpose; avoid overwriting unrelated notes.
4. Apply the layout below, omitting inapplicable sections rather than filling
   them with boilerplate. Keep prose under about 250 words, excluding tables
   and captions. Detail beyond that belongs in the linked run log.
5. Verify references and claims, then return the report path and a brief outcome.
   State any verification you could not perform.

## Layout

- **Properties:** YAML `title`, `tags` (`report`, kebab-case report type, work
  ID if any; no spaces), and `created` as an unquoted `YYYY-MM-DD`. Match the
  visible H1 to the title. Preserve the creation date on revisions; use
  `updated` separately if needed.
- **Outcome:** explicit status and one or two sentences. Distinguish implemented,
  locally verified, and delivered. Never imply approval, CI, or deployment.
- **Verification:** compact table before images. Include relevant test, lint,
  typecheck, and build commands, results/counts, warnings, and descriptive log
  links. Keep failed or not-run relevant checks visible. Do not dump routine
  browser commands or their log links. For investigations, use concise findings
  and supporting evidence instead of irrelevant implementation checks.
- **Evidence:** desktop images full-width; mobile images in two-column Markdown
  tables, or one column for a single capture. Add brief captions identifying
  the state, viewport when known, and what is visible. Use plain image embeds,
  never outer clickable-link wrappers, which can trigger Obsidian hover previews.
- **Remaining work & decisions:** one bullet per action, decision, blocker, or
  material limitation; identify owners when known. Include operational handoff
  details only when necessary. Link the run log here or in Verification when
  one exists. Omit this section when nothing remains.

## Evidence Rules

- Embed screenshots used as report evidence; never substitute filename lists.
  Select meaningful captures, not every diagnostic image. Do not invent images
  or capture extra screenshots merely to fill the template.
- Use note-relative paths to durable files inside the vault. Reference existing
  in-vault evidence in place, such as a run's `<plan-id>-evidence/`; do not
  duplicate it. Copy temporary or out-of-vault assets into a sibling
  `<report-stem>-assets/` directory without overwriting files or moving
  originals.
- Name new assets in kebab-case. Spaces break Markdown embeds; for an existing
  file with spaces, URL-encode them (`%20`) in the path.
- Inspect images for relevance and sensitive content before embedding. Do not
  expose secrets; use a separately sanitized copy when necessary.
- Screenshots establish visible states, not successful interactions, persistence,
  or accessibility. Ground behavioral claims in actual checks. Distinguish old
  results from fresh verification and disclose missing evidence.
- Check every local link/image resolves, table structure is valid, and no
  placeholders remain. Do not claim Obsidian rendering or click behavior was
  tested unless it was. Keep the report native Markdown: no HTML preview,
  custom CSS, or plugin dependency.
