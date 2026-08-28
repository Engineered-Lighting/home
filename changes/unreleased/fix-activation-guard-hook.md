---
title: Land the activation-source guard on main and stop its hook blocking commits
target: internal
type: fixed
---

The pre-commit hook that enforces the Home Agent activation-source pin invoked
`tools/check-activation-paths.py`, but that tool had never reached `main` — it
existed only on the branch that introduced it. Every commit from every checkout
of `main` therefore died with `python3: can't open file ...`, and the way around
it was `--no-verify`, which switches the guard off for the whole commit rather
than just the missing check. A guard that cannot run was making the repository
un-committable and training people to disable it.

Adds the tool to `main` so the hook has something to run, and makes the hook
skip with an explicit warning — instead of failing — when the guard or `python3`
is absent, so a checkout that predates the tool stays committable and no one
needs `--no-verify` to get past it. Installing the hook from a linked worktree
also works now; it previously tried to write inside `.git`, which is a file
rather than a directory there.
