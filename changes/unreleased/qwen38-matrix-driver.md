---
title: Add the Phase 3 latency matrix driver
target: internal
type: added
---

Adds `tools/qwen38-matrix-driver.py`, which runs the migration's latency
cells against whatever engine configuration is currently up and records
what that configuration was. It changes nothing about the engine.

The session protocol is enforced rather than remembered: a reasoning-leak
canary aborts the session before any cell runs, since numbers from a
configuration that cannot ship are worthless; an anchor cell runs first and
last, and more than ten percent drift between them invalidates the session;
and every latency carries its measurement point and cache state. Cells
needing an engine configuration only a maintenance window can create refuse
to run rather than quietly recording a cell they did not exercise.

The first session against the current model settles the migration's central
question for the incumbent. Prefix caching is working: the engine reports a
99.9 percent hit rate on repeated prompts and zero percent when the prefix
is deliberately broken, worth 5.2x. A realistic voice turn, which shares the
long system prompt and varies only the question, costs 0.16 seconds against
0.35 with no cache — and that gap is exactly what the research warns may
disappear on the new model's attention design.

Ambient captioning uses a tenth of its budget, but voice is already at 86
percent of its four-second budget on the current model, which is what makes
the cache result decisive rather than merely interesting.

The full baseline at the sizes the plan requires found that the latency gate
already fails on the current model. Measured at the gate's own named
instrument over thirty samples, a routine multi-tool voice query sits at 6.1
seconds against a four-second budget, reproducibly, while its median is a
healthy 1.8. Every cell that measures the model is comfortably inside
budget — ambient uses a tenth of its allowance, clips an eighth — so the
breach is in the pipeline around the model rather than the model itself.
Recorded as a decision the owner needs to make, because a gate the current
system cannot pass cannot judge a replacement.
