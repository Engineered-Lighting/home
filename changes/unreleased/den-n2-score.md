---
title: N2 need-test scorer with an empty gate
target: internal
type: added
---

`tools/den-n2` adds `score.py`, which scores need logs for one person against
N1's frozen blind labels and gives `feasible`, `not_feasible` or
`insufficient`. It ships with `GATE-V1.md`, committed with every owner and
development slot empty. The held-out run refuses while any slot is empty, and
development mode never opens held-out labels. Labels, logs and reports stay on
the PC. Only one line per held-out run, with hashes and counts, reaches
`RESULTS.md`.
