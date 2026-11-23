---
description: Set up the ticket system (tracker, ticket template, board, Harness section) in a project that has none. Never overwrites; never installs hooks without asking.
argument-hint: [--dry-run]
---

Set up tickets in this project. Arguments: **$ARGUMENTS**

1. Check the project has no tickets yet: no tracker file and no `docs/tickets/` folder. If either exists, stop and say adoption of an existing tracker is a separate mode that is not available yet; write nothing.
2. Ask the owner, one short question each:
   - the ticket ID prefix (e.g. `WEB`; several allowed, each optionally `PREFIX:platform:kind`);
   - the integration branch (default `main`);
   - archive closed tickets: on or off (default off);
   - twin platforms (e.g. `ios,android`; default none);
   - install the git hooks that rebuild the board (default no).
3. Run `dev-harness-board init --dry-run --prefix <P> [--branch <B>] [--archive] [--twin-platforms <a,b>]` and show the owner the list of files it would write. If the argument was `--dry-run`, stop here.
4. On the owner's yes, run the same command without `--dry-run`. It writes only files that do not exist, merges missing `tickets` keys into an existing `.claude/harness.json`, appends the Harness section to `CLAUDE.md` only when there is none (an existing one is never edited; the command prints the lines to add, which you put to the owner), builds the board once, and does not commit.
5. If the owner said yes to hooks, run `dev-harness-board install-hooks` as its own Bash command (the guard asks), or tell the owner to run it. `init` never installs them.
6. Run `dev-harness-board --check`, then report what was created, what was left alone, and that nothing is committed. Tell the owner to add the generated board file (`tickets.boardPath`, default `docs/tickets/board.html`) to `.gitignore`, because the guard asks before it is committed. Remind the owner to fill the `<...>` placeholders in the new Harness section (verify commands, caches, high-risk categories).

Do not commit anything: the owner or coordinator commits. Running it again changes nothing and says so.
