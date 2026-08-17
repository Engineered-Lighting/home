---
title: Measure the VRAM envelope and kill the small-VLM ambient plan
target: internal
type: added
---

The house runs one model for everything, and the proposed fix was a fast
small vision model for ambient captions plus a stronger model for reasoning.
The first half of that plan does not survive measurement.

Adds `docs/AI-ARCHITECTURE-EXPERIMENTS.md` with three experiments run against
the live host, incumbent untouched throughout.

`Qwen3-VL-4B-Instruct-FP8` was brought up on a second port beside the running
incumbent and scored on the frozen G1 and G4 corpora, so the comparison cost
nothing to set up. It is not faster — ambient p95 0.17 s against the
incumbent's 0.176 s, both far inside the 1.5 s budget the incumbent already
meets at 12% — and it hallucinates far more, 13/35 false presence against
5/35, failing G4's paired test at p=0.96. There is no ambient latency problem
for a fast model to solve, so the fast-model leg is dropped.

The VRAM envelope is now measured rather than assumed. 84 GiB is genuinely
available to models, confirming the owner's figure. A second vLLM process
costs about 1.0 GiB beyond its weights and KV, and vLLM 0.20.2 cannot serve
two models in one process — confirmed from the pinned image's own argument
parser, not from recollection. KV geometry was read from each checkpoint's
config and inverts the intuition: the 4B needs 144 KiB per token against the
30B MoE's 96 and the Qwen3.8 candidate's 64, so the smallest model has the
largest KV footprint.

The useful opening is that the incumbent reserves 33.59 GiB of KV for a
scheduler capped at four sequences, which can only ever occupy 12 GiB. Freeing
that would fund two 27–30B models in 84 GiB with about 2 GiB to spare. Whether
it can be freed depends on whether the incumbent's 5.23× prefix-cache win
survives a smaller pool, which is the next experiment and is stated as such.

Also records two defects in the look classifier found while verifying G4's
scoring: negated motion ("no one moving", "no motion detected") classifies as
a person at importance 90, and a plain "The room is quiet." falls through to
activity at 55. Both are in the repo source and in the gate port; a deployed
copy was not located, so this is reported as a source defect and a
question, not a live outage. Neither changes the 4B verdict.

The pool question is now mostly answered ahead of that experiment. The 5.23×
prefix-cache win rests on one shared voice prefix — the EOC prompt plus its
function schema — which tokenises to 13,552 tokens, or 1.24 GiB of KV: 3.7%
of the pool it sits in. The retained set is bounded by how many distinct
system prompts exist, not by traffic, so a pool sized at 16 GiB would hold
the full four-sequence ceiling and eleven times the entire reused prefix.
What remains to test is vLLM's eviction behaviour, not the arithmetic.
