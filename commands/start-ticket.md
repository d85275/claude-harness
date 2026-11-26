---
description: Launch an implementer agent for one approved ticket in its own worktree, with a short brief.
argument-hint: <ticket-id> [notes for this run]
---

Start ticket **$ARGUMENTS**.

Run the state and board steps below only when `dev-harness-board status` exits 0 (the project has opted in with a `tickets` section in its `harness.json`); otherwise skip them silently and follow the project's own Harness section.

**Files and shell.** Create and edit files with the Edit and Write tools only, never through the shell (`cat > file <<EOF`, `python3 - <<EOF`, `printf >> file`). Run one plain command per Bash call, with absolute paths, `git -C <absolute path>` and `--root <absolute worktree path>` instead of `cd X && ...` chains. A command that reads the current folder (the Harness Verify commands, test runners, builds) takes the tool's folder option (`python3 -m unittest discover -s <abs>/tests`, `make -C <abs>`, `npm --prefix <abs>`, `go -C <abs>`, `./gradlew -p <abs>`, `git -C <abs>`); if it has none, stop and report rather than run it from the wrong tree. Clean with the build tool's clean task, never `rm` with wildcards or variables. If a command still asks or is denied, stop and say which rule it hit; don't rewrite it to get around the check. Pass these rules into every implementer brief.

1. Read the project's Harness section (root `CLAUDE.md`) and its handoff/progress doc. Confirm the ticket exists in the spec or plan, its dependencies are integrated, and no owner decision blocks it. If something blocks it, stop and report.
2. Check the machine: `git status --short` on the main checkout is clean; no stale worktree/branch with this ticket's name; enough free disk (`df -h /`). Note any other ticket currently running.
   Read the ticket's state first (`dev-harness-board --json`, or its tracker row). If it is `Done`, `Blocked` or `Deferred`, stop and ask the owner; continue only if they say so.
3. Read the ticket's **Review level**: `high-risk` -> agent `implementer-advanced`; anything else (or no level) -> `implementer`.
4. Create the worktree (`git worktree add <repo>/.worktrees/<id-lowercase> -b feature/<id-lowercase> <base>`, `<repo>` being the main checkout's absolute path), then in it run `dev-harness-board set-state <ID> "In progress" --root <repo>/.worktrees/<id-lowercase>`. That edits the worktree's tracker row and the ticket's Status line only; main is untouched until close. Leave the edit uncommitted: the implementer's single commit carries it. The board shows the ticket as In progress.
5. Launch that agent in the background with a brief containing only:
   - the ticket ID and where its spec/acceptance criteria live;
   - the worktree path and the base commit (`git rev-parse HEAD` on the integration branch);
   - owner decisions that apply to this ticket and are not yet in the docs;
   - if another ticket is running: its worktree, and "use your own build cache and devices; no machine-wide simulator/emulator resets";
   - anything unusual about this run;
   - the working rules from **Files and shell** above, in a few lines: Edit and Write for files, one plain command per Bash call, absolute paths for `git -C` and `--root`, the tool's folder option for test runners and builds (they read the current folder, which is the main checkout after every call), no `rm` with wildcards or variables (scratch files stay in the scratchpad), and a command that asks or is denied stops the ticket and is reported, not worked around.
   Everything else (conventions, safety rules, evidence, commit, report format) is already in the agent definition and the project's CLAUDE.md; do not repeat it.
6. Tell the owner in one or two lines which agent started and why, and what happens next.
