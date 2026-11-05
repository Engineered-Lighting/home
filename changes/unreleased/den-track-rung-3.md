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
nothing calls a device. A scored run refuses while eight open slots (the freeze, the
floor, the door, how long a person coasts, the crossing radius, the return to
confident, the scoring pad and the face stand-in) await the owner.
