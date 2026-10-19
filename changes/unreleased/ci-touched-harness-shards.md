---
title: Split touched-surface CI into verified shards
target: internal
type: changed
---

The touched-surface job now runs as three workers, each taking a deterministic share of one harness plan (`tools/run-touched-harnesses.mjs --shard i/n`) and publishing a receipt. The required `deterministic chat and ui contracts` check recomputes the plan for the same base and fails unless the receipts cover it exactly once, all green. Deadlines are unchanged: 145 s browser, 180 s per file, 10 minutes per worker.
