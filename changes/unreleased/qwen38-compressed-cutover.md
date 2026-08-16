---
title: Add a compressed cutover path evaluated in daily use
target: internal
type: added
---

The owner elected to adopt the new model in daily use rather than run a
multi-evening comparison matrix first. Adds
`docs/QWEN38-CUTOVER-COMPRESSED.md` and `tools/qwen38-cutover-smoke.py`,
which make that trade safe rather than merely faster.

The reasoning is that some failures degrade quality — those an owner living
with the system will notice — while others break the house outright and
would not read as "worse" at all: a voice turn that never returns, or the
assistant speaking its own reasoning aloud through the speakers. The smoke
check rules out only the second kind, in about three minutes, and prints one
of three verdicts ending in either "keep it" or the exact rollback command.

Two of the house's live tools take zero arguments, which is the shape of a
known parser hang, and the parser change is exactly what would expose it. So
that case is exercised deliberately with a timeout rather than left to be
discovered during a voice command.

What is deferred is comparison evidence: blinded caption judging, the
labeler replay, and paired hallucination scoring. All three harnesses exist
with their current-model arms already captured, so they can be run at any
time, including after the fact. The cost of skipping them is stated plainly
in the runbook — without them there is no defensible answer to whether the
new model is actually better or merely different.

Validated against the current model, where it passes.
