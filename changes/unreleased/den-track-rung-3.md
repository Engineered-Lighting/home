---
title: Den geometry-only tracker and crossing scorer (rung 3)
target: internal
type: added
---

`tools/den-track` adds rung 3 of the live architecture. A causal, deterministic
tracker turns observation records into track records, with a floor-plane motion
model, covariance-gated joint association, coasting through occlusion, entry and
exit. When association is in doubt or two people are in reach, it reports
`ambiguous` with named hypotheses, so a swap under doubt is not silent. E2's scorer
reports silent swaps, IDF1 and the one-sided bound per crossing, from a small
versioned ground-truth format. Synthetic scenes and tests cover crossings,
occlusions, near passes and noisy depth. Nothing has been run on real data and
nothing calls a device. A scored run refuses while twelve open slots (the freeze, a
time on track lines, the floor, the door, how long a person coasts, the crossing
radius, the return to confident, the scoring pad, the face stand-in, what a crossing
must show to count, how an unresolved switch counts, and the crossings plan) await the
owner.

A crossing counts only if it shows what its kind claims, such as both people followed
through the owner's mark, unseen for no longer than a track coasts, and near each other; the
rest are listed as excluded, and a scored run refuses when too few remain or a kind falls short
of the plan. Someone followed through is read on both sides of the mark even past the padding, so
a swap behind a detector stall that outlasts it costs IDF1 and does not go unread, though at less
weight than inside a window, as `METHOD.md` measures. A take that has
been scored is used: a retune of the numbers or the code, new labels, or dropping or adding
a take once results are known needs fresh takes, and so does the same footage come back
with a frame less. The tool refuses the old ones, from a record of hashes and capture times
kept beside each take and for the Windows user. Truth files say who
labelled, whether blind, and which observations, and may say "cannot tell". Overlapping
boxes are never merged, since rung 2 writes one observation per detector box. A new track
claims nothing until it has been seen for a while, so two people close as a take starts no
longer read as a silent swap. A duplicate box costs IDF1 and rarely reads as one, at the rates
`METHOD.md` gives.
