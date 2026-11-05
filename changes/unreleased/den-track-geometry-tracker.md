---
title: Add the den's geometry-only person tracker and E2 crossing scorer
target: internal
type: added
---

Adds `tools/den-track`, rung 3 of the live den architecture: a causal, deterministic tracker that links room-frame `observation` records into anonymous per-person `track` records and reports `ambiguous`, naming the other person, whenever two people may have swapped. It also adds E2's scorer (silent swaps, IDF1 and the one-sided 95% bound per staged crossing), a versioned ground-truth format and a synthetic scene generator for tests. Nothing runs it yet: no crossing has been recorded or scored, it never contacts a camera, Home Assistant or a light, and a scored run refuses while METHOD.md still has open slots.
