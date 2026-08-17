---
title: Find the real Qwen3.8 blocker — it invents the state of the house
target: internal
type: added
---

The Qwen3.8 migration was blocked on a reasoning leak, and the leak is solved:
with `preserve_thinking` unset and `--reasoning-parser qwen3` on, reasoning
lands in a separate field 24/24 with nothing spoken aloud. The 31.6 s
thinking-ON voice figure is also wrong for this path — measured at the engine
it is p50 3.24 s, roughly ten times better than the number that killed the
config.

Neither of those saves it. Given the house's own system prompt and its real 23
functions as `tools`, Qwen3.8 with thinking OFF calls a tool on only 8 of 24
turns that need live state, and answers 13 of them with a confident false
claim: which lights are on, who is home, how many rooms exist. Two are worse
than wrong — "Office light off." reports an action it never performed, and
"someone in the kitchen" invents occupancy. Its claim that the kitchen light
was on is falsifiable directly: with tools executing, the incumbent answers
that there are no lights in the kitchen.

Thinking ON fixes fidelity completely — 24/24 tool calls, zero fabrications —
but costs p50 3.24 s and p95 8.98 s at the engine, before HA, STT and TTS. The
incumbent reaches the same fidelity, 24/24 correct with zero fabrications, at
p50 0.18 s. So Qwen3.8's only safe configuration is about eighteen times slower
than the model already installed, against a voice budget that the incumbent is
already breaching. It does not belong on the voice path.

It may still belong beside it. Its grounding win is real, and the second
finding here pays for it: the incumbent's KV pool can be cut from 33.59 GiB to
14.6 GiB with no measurable cost — warm and shared-prefix hit rates stay at
100% and 99.8%, latency and TTFT are unchanged, ambient and voice both verified
on the live path — which frees 19.9 GiB. Both 27-30B models were then held
resident simultaneously, so two large models co-existing is demonstrated rather
than computed. Qwen3.8's measured footprint is 28.51 GiB of weights, ~3.4 GiB
of process overhead, and 73.3 KiB per token rather than the 64 KiB the
attention math predicts, the difference being GDN recurrent state.

Also settles a premise: both checkpoints carry the identical video processor
and vision config, so native video is not a reason to migrate. Video is gated
by the live compose's `video: 0` limit, which is a flag, not a model choice.

Adds `docs/AI-ARCHITECTURE-RESTORE.md`, written before the first live change,
because these experiments swap models in VRAM. The incumbent was restored
byte-identical afterwards and voice verified through the conversation API with
real tool-backed answers.

Records the owner's decisions taken on these results: Qwen3.8 becomes a
second, non-voice instance for grounding and nightly video rather than a
replacement for the incumbent; the KV pool moves to 0.50 in the same window
that brings that sidecar up rather than as a standalone change; and MTP is
shelved, since even a 2x gain leaves it about nine times off the incumbent on
voice and so cannot change the routing decision.

The look classifier's deployed copy was then located, and it was worse than the
repo version. `home-web-gateway.service` serves `app/src` from the sibling
`/home/marcelo-lima/code/home` repo, not from here, and that copy was still
pre-`7ce30b0`: the person branch keyed only on posture gerunds, with no human
nouns and `walking` but not `walks`. On the frozen 50-frame daylight corpus,
using the deployed model's own captions, it missed 6 of 19 people — one filed at
importance 10, the "nothing to report" tier — while a pink bicycle "standing
upright" raised 2 alerts at importance 90. Fixed in that repo as `abd85e7`:
missed people 6 → 1, phantom alerts 2 → 0, with the negatives corpus
unchanged at 3/25. So the live defect was the opposite of the one first
suspected — under-reporting people, not over-reporting negations.

The negation defect stays open in both copies, deliberately, as a separate
change with a separate risk profile.
