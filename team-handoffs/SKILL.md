---
name: team-handoffs
description: >-
  Shared conventions for Kyle's Grok bot team: who reports to whom, who
  escalates to Kyle, and how to send bot-to-bot messages so requests and
  handoffs are not lost. Use whenever one bot messages another (SendToAgent),
  hands off work, requests a review or QA pass, or reports results.
---

# Team Handoffs

## Org structure

- **Kyle** approves. Only Sophie escalates to Kyle.
- **Sophie (Chief of Staff)**: every specialist reports to Sophie. There is no
  "Head of" management layer between specialists and Sophie.
- **Evelyn (Head of Product)**: owns product specs and priorities.
- **Alice (Architect)**: owns the skills (this repo), each repo's `AGENTS.md`,
  deploy/infra setup, and is the escalation point for design calls.
- **Emma (Quality Auditor)**, **Clara (Growth Strategist)**, and the other
  specialists: report to Sophie.
- **Review bots (Code Reviewer, QA Tester, Release Gate)**: report results to
  the bot that requested the work, copying Sophie.

Escalations go to Sophie, not directly to Kyle, unless Kyle is already the
person you are talking with.

## Message priority

When one bot asks another to act, send the message with `priority: true`.
This covers review or QA requests, re-reviews, sign-offs, handoffs,
merge-ready reports, and anything someone is waiting on.

Non-priority messages are queued and do not wake the recipient. Sending an
actionable request as non-priority has caused lost handoffs.

Use `priority: false` only for pure FYIs that need no action and that nobody
is waiting on.

## Handoff contents

A handoff or request should stand on its own: what to do, where (repo, branch,
PR or ticket link), what "done" looks like, and who to report back to.
