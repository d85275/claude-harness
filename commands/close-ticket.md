---
description: Integrate an accepted ticket - cherry-pick its single commit onto the integration branch, run the integrated check, and clean up the worktree, branch, caches, and test devices. Never pushes.
argument-hint: <ticket-id>
---

Close ticket **$ARGUMENTS**.

Conventions come from the project's Harness section (default: worktree `.worktrees/<id-lowercase>`, branch `feature/<id-lowercase>`, integration branch `main`).

## 1. Preconditions - stop and report if any fails

- Worktree and branch exist; `git -C <worktree> status --short` is empty; the branch has exactly one commit over its base.
- Evidence is recorded in the project's evidence/handoff docs with real commands and results.
- If the diff is high-risk, a `high-risk-reviewer` verdict of `ACCEPT` / `ACCEPT WITH MINOR NOTES` exists for the final commit with Critical/Important findings resolved; otherwise a `reviewer` verdict, or a coordinator self-review when the project allows it for that ticket type.
- `git -C <worktree> diff --check <base>..HEAD` is clean.
- The main checkout is clean (`git status --short`, ignoring `.worktrees/`).

## 2. Integrate

`git cherry-pick <sha>` on the integration branch. On conflict in handoff/evidence docs, merge both sides' entries; on any other conflict, stop and report.

## 3. Integrated check

Run the smallest check that proves the ticket on the integration branch (the project's focused tests plus static checks). Fresh result path. If it fails, stop; keep the worktree.

## 4. Clean up

- `git worktree remove .worktrees/<id-lowercase>` (no `--force`; report anything left).
- `git branch -D feature/<id-lowercase>` - `-D` is required because a cherry-picked branch is never "merged"; first confirm `git cherry -v <integration> feature/<id>` shows the commit as already applied (`-`) or that its patch landed.
- Delete build caches and temporary devices this ticket created, per the Harness section's cleanup list. Machine-wide resets (e.g. `xcrun simctl --set testing delete all`) only when no other build/test is running (`pgrep -fl xcodebuild` is empty).
- Keep cited result bundles.

## 5. Report

Commit SHA on the integration branch, the integrated check and its result, cleanup status, tickets done/remaining, and the next ticket. Do not push.
