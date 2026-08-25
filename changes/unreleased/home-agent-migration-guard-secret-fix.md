---
title: Verify activation migration revisions with the database secret loaded
target: backend
type: fixed
---

The Phase 3 migration executor and activation runner verified database
revisions by overriding the Core image entrypoint with `python`, which skips
the only step that materialises the database URL from its file-backed secret.
Every revision guard therefore failed closed regardless of the database's
actual state, blocking the reviewed migrations. Both call sites now share one
guard invocation that loads the secret under the entrypoint's own fail-closed
rules, passing only the secret's path and never its value.
