---
title: Prove Home Assistant is stopped from the database, not a status string
target: backend
type: fixed
---

The writer-freeze observation refused to run unless `ha core info --raw-json`
reported `state == "stopped"`. On this deployment (Core 2026.8.1) that command
returns the Supervisor envelope `{"result", "data"}`, and **neither level
carries a run-state key at all** — verified against the live host. The
comparison was therefore `None != "stopped"` for every possible input, so the
step raised unconditionally, including when Home Assistant genuinely was
stopped, which is precisely when it runs.

That failure would have landed at step 20, immediately after step 19 stopped
Home Assistant, with the activation past its forward-only containment boundary.

The only test covering it fed a hand-written `{"state": "running"}` payload
that the CLI never emits, so the shape was never checked against reality.

The property this step needs is not that a supervisor reports a string but that
nothing is writing the legacy identity store — and the observation already
proved exactly that, a few lines later, with a process lock, a checkpoint and
integrity check, the absence of the SQLite sidecars, and a digest that is
stable across the observation.

The guard now uses that same evidence up front: SQLite creates `-wal`,
`-journal`, and `-shm` beside a database while a connection is open and removes
them on the last clean close, so their absence is direct proof the store is
quiescent. Checking them before anything else keeps the fail-fast ordering that
mattered — the collector never opens the database read-write while Home
Assistant is live — while removing the dependency on a status field that does
not exist. The collector now runs no subprocess at all.
