# Contributing

Thanks for helping. This page says how to run the tests and how changes are made here.

## Requirements

Python 3.9 or newer, git and `/bin/sh`. The plugin uses the Python standard library only; please keep it that way.

## Running the tests

From the repository root:

```sh
# full suite
python3 -m unittest discover -s tests

# one test module, while you work
python3 -m unittest tests.test_push_policy

# static checks
python3 -m py_compile hooks/scripts/*.py board/*.py
sh -n templates/githooks/hook.sh
```

Some tests create real git repositories in the temp folder. If your temp folder is shared or small, point `TMPDIR` somewhere else first.

## CI

[`.github/workflows/test.yml`](.github/workflows/test.yml) runs the static checks and the full suite on every push to `main` and every pull request, on Ubuntu and macOS with Python 3.9 and 3.13. A pull request should pass it.

## How changes are made

This repository uses its own loop. Each change is a ticket in [`docs/tickets/`](docs/tickets/), and [`docs/tickets/tracker.md`](docs/tickets/tracker.md) is the source of truth for its state.

1. **Plan.** `/plan` writes the ticket from [`TEMPLATE.md`](docs/tickets/TEMPLATE.md): scope, done criteria, allowed files and review level.
2. **Implement.** `/start-ticket` starts an implementer in its own worktree, `.worktrees/<id-lowercase>` on `feature/<id-lowercase>`. The work ends as exactly one commit, `<type>: <id> <summary>` (for example `feat: ch-03 push policy`), with the commands and results recorded in the ticket file.
3. **Review.** `/review` sends the commit to an independent reviewer; changes that weaken or bypass a guard, git hooks, and releases go to the high-risk reviewer. Fixes amend the same commit.
4. **Integrate.** `/close-ticket` cherry-picks the commit onto `main` and reruns the tests.

The loop and the commands are described in the [README](README.md#how-it-works). Repository rules for agents are in [`CLAUDE.md`](CLAUDE.md).

You do not need Claude Code to contribute: an ordinary pull request with tests is welcome. Small, focused changes with a test that fails without them are the easiest to review.

## Fixtures and examples are made up

This repository is public. Test fixtures, examples, ticket text and screenshots must be invented: no real project names, ticket IDs, paths, people or credentials. Use neutral names and `example.invalid` addresses.

## Regenerating the examples

The sample board and screenshots come from [`examples/sample-project`](examples/sample-project). After changing the board or the sample tickets, rebuild them:

```sh
scripts/make-examples.sh
```

It writes `docs/examples/board.html` and, when Chrome is found, the PNGs in `docs/images/`. The test suite checks that the committed board matches the sample project.

## Changelog and version

Add a line under the newest heading of [`CHANGELOG.md`](CHANGELOG.md) for anything a user would notice. The version in `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` and the newest changelog heading must agree; a test checks it.
