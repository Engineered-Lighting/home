---
title: Run every Home Agent contract suite in CI
target: internal
type: fixed
---

Nineteen `tests/home_agent` contract suites never ran in CI; two of them were
named only in trigger lists. The tailnet origin contract now runs in the hosted
web boundary gate, and the other eighteen run in a new hosted `home agent
repository contracts` workflow. That workflow installs pytest from a
hash-pinned lock and triggers on every file the suites read. It also fails if
any `tests/home_agent` suite is not run by a workflow step or the E1 gate. The
E1 gate now reruns when the web boundary workflow changes, and its stale web
trigger assertion now checks glob coverage instead of an exact path.
