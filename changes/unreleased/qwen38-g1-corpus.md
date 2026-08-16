---
title: Correct the caption-gate camera roster and capture its corpus
target: internal
type: fixed
---

The caption gate's camera list came from the stale Frigate addon config: it
named a `back_door` camera this house does not have and omitted `workshop`.
A capture against it would have failed a fifth of its frames and missed a
real room. Corrected against the running service's own configuration.

The corpus is captured with a spread rather than back to back. Fifty frames
taken in one burst would be ten near-identical shots per camera, and
near-identical captions produce ties — which carry no information in a
paired comparison and would leave the gate underpowered no matter how good
or bad the new model turned out to be.
