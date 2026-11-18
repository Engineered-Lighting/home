---
title: Den Live reports why the live body blanks
target: backend
type: added
---

Den Live's people events now carry `health.body`, a count-only record of the live body stamped with its run, epoch, newest body seq and capture time. It counts ready frames, clears by reason (lease expiries apart), skips split into own job, Qwen stills first and busy slot, held misses and exact-frame mask use, so operators can attribute body blanks; body frames, clears and their timing are unchanged.
