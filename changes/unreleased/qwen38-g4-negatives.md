---
title: Build the hallucination-on-negatives gate from live camera events
target: internal
type: added
---

Adds `tools/gate-g4-negatives.py`, which assembles the gate's corpus from
real Frigate event snapshots rather than authored examples: 25 negatives
and 10 quiet-literal sentinels across three cameras. The kitchen camera
alone re-detects a cup on the counter roughly 2,300 times in six hours, so
frames where a detector fired and nothing happened are abundant.

Captions are scored with the app's own finding classifier, now ported into
the shared gate core and cross-asserted against the JavaScript on seventeen
shared cases, so the gate answers the operational question — would the app
have raised a notable finding on a frame where nothing happened — rather
than a matter of taste.

Building it surfaced two design errors worth recording. A parked car is not
a hallucination, so the corpus now requires living things to be absent while
allowing tolerated objects to be present and excluded from scoring per
frame; discarding those frames instead would have dropped the driveway,
the camera most likely to invent a person. And the sentinels were pointed
at the ambient caption prompt, which asks the model to describe directly and
so never produces the quiet phrase; against the deep-look prompt that does
ask for it, the incumbent files 8 of 10.

Incumbent baseline on the new corpus: 5 of 35 false presences, 8 of 10
sentinels quiet, no reasoning leaks. The corpus is proposed rather than
confirmed — its ground truth is the detector's opinion, and three of the
five false presences may be a real person it missed in the dark — so the
runner refuses unverified frames unless explicitly asked for a dry run.
