---
title: Recommend staying on the current model after measuring the candidate
target: internal
type: changed
---

Four short windows established that the candidate has no configuration that
is both leak-free and usable for voice, so the migration's recorded failure
default — stay on the current model — is the right outcome.

The bind is structural rather than a tuning problem. The model must reason
in order to stop emitting closing think tags into spoken output, and
reasoning on the tool-bearing voice path costs about thirty seconds per
turn against a budget of two and a half, seventeen times the current model,
and beyond the app's own processing guard. Lowering the reasoning effort
moves that by single digits, not by the order of magnitude required.

The sidecar routing patch is kept and remains correct: it rescued
background captioning from over four seconds to under one by suppressing
reasoning on the fourteen-hundred daily requests that never needed it. It is
a no-op on the current model and would be the right design if this
checkpoint is revisited.

The candidate is genuinely better where reasoning is not required —
grounded boxes on three of three frames where the current model manages
none, more cache headroom, and no tool hang. What would have to change is
upstream: a checkpoint or engine build where suppressing thinking does not
leak with tools attached.

Every window ended with the current model restored and verified. All
harnesses, corpora and baselines remain valid for a future attempt.
