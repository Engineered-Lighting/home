---
title: Require retained PC originals during Den causal replay
target: backend
type: fixed
---

Match private PC proofs in worker cuts to their exact committed original bodies
during ordinary replay. Missing or changed originals now make replay incomplete
even when the surrounding archive has a valid recomputed hash chain.
