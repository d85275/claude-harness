---
description: Integrate an accepted ticket - cherry-pick its single commit onto the integration branch, run the integrated check, and clean up the worktree, branch, caches, and test devices. Never pushes.
argument-hint: <ticket-id>
---

Close ticket **$ARGUMENTS**.

Conventions come from the project's Harness section (default: worktree `.worktrees/<id-lowercase>`, branch `feature/<id-lowercase>`, integration branch `main`).

Run the state and board steps below only when `dev-harness-board status` exits 0 (the project has opted in with a `tickets` section in its `harness.json`); otherwise skip them silently and follow the project's own Harness section. (This covers every `dev-harness-board` call here; the cherry-pick, checks and cleanup always run.)

## 1. Preconditions - stop and report if any fails

- Worktree and branch exist; `git -C <worktree> status --short` is empty, or its only changes are `set-state` edits (the Fixing from a review round): then run `dev-harness-board set-state <ID> "In review" --root <worktree>`, which restores the committed bytes of the tracker row and Status line, and check the status is empty; the branch has exactly one commit over its base.
- Evidence is recorded in the project's evidence/handoff docs with real commands and results.
- If the diff is high-risk, a `high-risk-reviewer` verdict of `ACCEPT` / `ACCEPT WITH MINOR NOTES` exists for the final commit with Critical/Important findings resolved; otherwise a `reviewer` verdict, or a coordinator self-review when the project allows it for that ticket type.
- `git -C <worktree> diff --check <base>..HEAD` is clean.
- The main checkout is clean (`git status --short`, ignoring `.worktrees/`).

## 2. Integrate

`git cherry-pick <sha>` on the integration branch. If the only conflicts are this ticket's own tracker row and Status line (parallel tickets touch adjacent rows), (opted-in projects only) take the integration-branch side (`git checkout --ours <tracker> <ticket file>`, `git add`, `git cherry-pick --continue`); step 4 sets Done anyway. On conflict in handoff/evidence docs, merge both sides' entries; on any other conflict, stop and report.

## 3. Integrated check

Run the smallest check that proves the ticket on the integration branch (the project's focused tests plus static checks). Fresh result path. If it fails, stop; keep the worktree.

## 4. Ticket state - docs close commit on the integration branch

1. `dev-harness-board set-state <ID> Done --commit <sha on the integration branch>` (the cherry-picked SHA, `git rev-parse --short HEAD`). It edits the tracker row and the ticket's Status line. Do not edit Findings, Evidence or Follow-ups for the owner; if the ticket file lacks them, report it.
2. If `tickets.twinPlatforms` is set: `dev-harness-board twin <ID>` and report whether the twin is open. Never close it.
3. If `tickets.archive` is on: `dev-harness-board archive <ID>` (only for a Done ticket; moves the row to the archive tracker and the file into `archiveDirs[0]`, fixes links to it).
4. `dev-harness-board`, then `dev-harness-board --check`. Any output from `--check` stops the close: report it, fix nothing silently.
5. Commit the changed tracker, ticket and (if tracked) board files in one docs-only commit, `docs: close <id-lowercase>` (the ticket's own commit stays the only commit with its code). Never push.

## 5. Clean up

- `git worktree remove .worktrees/<id-lowercase>` (no `--force`; report anything left).
- `git branch -D feature/<id-lowercase>` - `-D` is required because a cherry-picked branch is never "merged"; first confirm `git cherry -v <integration> feature/<id>` shows the commit as already applied (`-`) or that its patch landed.
- Delete build caches and temporary devices this ticket created, per the Harness section's cleanup list. Machine-wide resets (e.g. `xcrun simctl --set testing delete all`) only when no other build/test is running (`pgrep -fl xcodebuild` is empty).
- Keep cited result bundles.

## 6. Report

Commit SHA on the integration branch, the docs close commit, the twin result, the integrated check and its result, cleanup status, tickets done/remaining, and the next ticket. Do not push.
