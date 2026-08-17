---
title: Route grounded reasoning to the Qwen3.8 sidecar, restoring boxes
target: internal
type: fixed
---

Grounded `/reason` returned `primitives: []` in production. It now returns real
labelled boxes — 3 to 9 per call across five cameras — because the vision
sidecar's `/reason` call site is routed to the Qwen3.8 grounding instance while
`/describe` and `/describe_clip` stay on the incumbent.

The routing is a two-line change to the LIVE `app.py`, taken from the running
container and bind-mounted back, never a repo copy: it adds `REASON_VLLM_URL`
and `REASON_MODEL`, both defaulting to the existing `VLLM_URL` / `VISION_MODEL`,
so unset env reverts to single-model behaviour exactly. `services/vision` in
this repo is not the deployed source and must not be built over the mount
without re-deriving the patch. A later edit points the response's `model` field
at the model that actually served the request — reporting `VISION_MODEL` there
made a routed response look unrouted and nearly caused a misread during
bring-up.

Why route rather than reprompt. Under the sidecar's own `REASON_SYSTEM` prompt,
scored with the production parsers on two frozen frames at n=6 each, the
incumbent extracts 0/6 boxes both times and Qwen3.8 extracts 6/6 with 0/6
runaway and an ANSWER line 6/6. The incumbent cannot be prompted out of it:
three repair candidates were built and measured, and every one that elicited
boxing lost the ANSWER line to runaway instead — up to 6/6 runaway, and raising
the token budget to 2500 made it worse, not better. Prompt v1 looked like a free
fix on one frame (4/6) and collapsed on the second (2/6, 4/6 runaway). So the
deployed prompt's brevity clause is load-bearing, and this is a model
difference rather than a prompt bug.

Routing also surfaced a rendering hazard that the gate could not see. Qwen3.8
occasionally emits `<bbox x1="234" ...>`, an attribute form Qwen3-VL never used,
which passed through the app's strippers untouched AND through
`app_residual_markup` as clean — the gate reported no residual markup while the
user would have seen raw XML. Both are fixed: detection is now deliberately
broader than stripping, because a false positive costs a look and a false
negative costs the user's screen.

Measured after routing, n=6 across three cameras: p50 4.02 s, p95 7.79 s,
median 5 primitives, zero residual markup. Grounding is slower than the
incumbent's empty answers were, which is the trade being made — a useful answer
in 4 s beats an empty one in 2 s. `/describe` is unchanged at ~200 ms.
