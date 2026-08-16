---
title: Add the Phase 3 candidate session runbook and preflight
target: internal
type: added
---

Adds `docs/QWEN38-PHASE3-SESSION-1-RUNBOOK.md` and
`tools/qwen38-phase3-preflight.py`. The session is bounded and reversible:
it loads the candidate model, runs the matrix cells, and restores the
current model before the operator walks away. Every expected value in the
runbook was measured on this host rather than assumed, including the
startup cache-pool figure that decides whether the long-context maths holds.

The session is ordered around the one question that decides the migration:
whether prefix caching survives on the new model's attention design. It is
worth 5.2x on the current model and 9x on time-to-first-token, so if it is
inert the answer arrives in ten minutes and the operator can restore
without running anything else.

The compose edit is four lines, with an explicit list of what must not
change alongside it, including the memory-utilisation figure the plan
wanted raised — one variable at a time, or the cache result cannot be
interpreted.

The preflight checks every precondition mechanically and refuses to start
on a failure, matching the host stability review's abort-without-retry
rule. Two corrections came out of writing it: ambient camera traffic is
about 1,400 requests a day across two drivers rather than the 350 from one
that the plan assumed, and the kernel error scan initially blocked on a
healthy host because the full journal contains an audit record of an
earlier search whose own pattern matched the search terms.
