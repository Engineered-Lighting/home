---
title: Restate the labeler validity gate as a keyframe replay
target: internal
type: added
---

Adds `tools/gate-g2-labeler-replay.py` and its parity tests. The gate as
written could not run: the labeler has no request log, its newest row is two
months old, and the prelabels it does hold were produced by a different
model than the incumbent, so none of the three things the gate asked for
exist.

What the labeler does retain is the exact model input — 683 analysis
windows of nine keyframes each — so the gate becomes a true paired replay
over identical inputs, which clears the n>=500 bar without depending on
live traffic and, unlike live traffic, repeats.

The harness imports the live ontology enums and the live prompt builders
rather than copying them, and a test asserts the live validation model's
field set is unchanged, so a labeler edit fails a test instead of quietly
making the gate measure the wrong contract.

Closes an open verification item along the way: the labeler requests plain
JSON rather than a grammar-constrained schema, so xgrammar's unsupported
feature list does not apply to it. That check belongs with the grammar-based
labeler planned for later.
