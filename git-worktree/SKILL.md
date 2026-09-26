---
name: git-worktree
description: >-
  Manage Git worktrees when explicitly requested or required by the active
  workflow. Standardize locations and reuse existing task worktrees. Do not
  trigger for ordinary feature-branch creation or continuation.
---

# Git Worktree

Use at most one worktree per task per repository, when needed; reuse it across
sessions and agent handoffs. Only one agent may write there at a time.

## Scope Gate

A feature-branch requirement is not a worktree requirement. This skill governs
worktrees after they are explicitly requested or required by the active workflow;
loading it does not authorize creating one.

- Ordinary branch creation or continuation: leave checkout decisions to the
  active workflow. Do not run the creation procedure below.
- Already on this task's feature branch: verify ownership and continue there.
  Do not create a worktree solely because this skill loaded. If an explicit
  worktree requirement conflicts with the existing checkout, ask before changing it.
- Task branch checked out elsewhere: locate and reuse that checkout after
  verifying ownership; do not duplicate it.
- Another task's branch is active: leave it untouched. Its existence alone
  authorizes neither switching branches nor creating a worktree.
- Read-only review needs no new checkout. An ordinary task checkout in the
  primary repository is not a legacy worktree that needs migration.

Continue below only for an explicitly requested or workflow-required worktree.

## Location and Identity

Create new worktrees only at `~/Worktrees/<repo-key>/<task-id>`.

- `repo-key`: primary checkout's directory name, not the current worktree's name.
  Reuse a previously recorded key. If separate clones share that name, stop and
  agree on distinct, stable keys; do not mix their worktrees.
- `task-id`: existing ticket key or a descriptive lowercase hyphenated slug,
  chosen once. No slashes, spaces, `..`, timestamps, agent names, or retry suffixes.
- Preserve the task's existing branch name. For a new branch, follow the active
  workflow's naming convention; use `task/<task-id>` only if none is specified.
- A new session, reviewer, retry, or implementation phase is not a new task.
  Additional worktrees for the same task require Kyle's explicit approval.

## Find Before Creating

1. From the supplied checkout, inspect:

   ```bash
   git rev-parse --path-format=absolute --git-common-dir
   git worktree list --porcelain
   git branch --list
   git status --short --branch
   ```

   Use Git's registered primary checkout, not `pwd`, to identify the repository.
   Bare repositories, missing primary checkouts, or uncertain identity: stop.
2. Check the task's existing notes/PR, branches, and **all** registered worktrees,
   including paths outside `~/Worktrees`. Match task identity, not just folder
   names. An inherited checkout is not automatically the correct one.
3. Reuse the matching checkout after verifying its branch, absolute Git common
   directory, changes, and writer ownership. Legacy locations: report the exact
   path and ask whether to keep or migrate it; never silently replace it.
4. Multiple matches, an active/unknown writer, missing registered directory,
   locked/prunable entry, unrelated changes, or an occupied target path: stop
   and report the conflict. Never choose a new suffix or force past the error.

## Create Only If Required and Absent

The coordinator alone creates the checkout, after establishing task ownership.
Workers receive its absolute path and branch; they must not create another.

Set `primary`, `target`, `branch`, and `base` to the resolved values above; quote
paths. Verify the target does not exist, including as a dangling symlink. Check
other checkouts under the repo-key belong to the same Git common directory.
Inspect the repository's documented base; fetch its remote if available. Do not
assume `main`, `origin`, or network access. If fetching fails, report it and ask
before using a potentially stale base. Local-only repositories use a verified
local base.

For a new branch:

```bash
git -C "$primary" worktree add -b "$branch" "$target" "$base"
```

If the task branch already exists but is not checked out, verify it belongs to
this task, then attach it instead of creating or resetting a branch:

```bash
git -C "$primary" worktree add "$target" "$branch"
```

If either command fails, inspect again and stop on conflict; no fallback path,
`--force`, `-B`, detached checkout, or duplicate clone.

## Setup and Handoff

- Use the project's bootstrap instructions. Install dependencies in this
  checkout; do not symlink dependency directories. Rebuild generated output.
- Copy only required, authorized local configuration; verify secrets stay
  ignored and never print or commit them. Do not symlink mutable env files.
- Agree on service/database identity and ports before starting processes; do
  not accidentally duplicate shared services. Check hooks for primary-path
  assumptions; Git config, refs, and stashes can be shared.
- Record task ID, primary path, worktree path, branch, base commit, writer, and
  setup/test status in the task's durable notes before delegating. Include the
  absolute path and branch in every handoff and final report. The previous
  writer must stop before the next starts; reviewers remain read-only.

## Verify and Finish

Run `git -C "$target" status --short --branch`, check its absolute common
Git directory, and re-list worktrees from the primary checkout. Confirm exactly
one checkout is assigned to this task and it has the expected branch, unless
Kyle explicitly approved an exception; record each approved path and its writer.
Run the project's relevant baseline checks; report failures rather than calling
it ready.

Follow global feature-branch/PR rules. Never push to main/master, auto-merge,
auto-rebase, or automatically remove/move worktrees, prune registrations, delete
branches, or discard changes. Leave the checkout available for review. Cleanup
requires explicit approval of exact targets, stopped writers/services, and a
check for uncommitted, untracked, ignored, and unpushed work before acting.
