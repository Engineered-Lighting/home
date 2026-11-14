---
title: Add a people layer to the apartment view for the live den view's markers
target: internal
type: added
---

Adds `app/src/home-3d/apartment-people-layer.mjs`, the layer that draws live people markers from den-live's people stream (`den-live-people-1`). The live den view (`home-den-live-view.mjs`) mounts it on the den's scan.
- One floor marker per track: a disc with a ring showing its spread, Home's own person spring, a facing arrow when a heading is known, and a 10 s trail.
- Coasting tracks are dimmed. A track that leaves the stream fades within about 2.5 s.
- It ignores any event that isn't `den-live-people-1`, and has no network, identity or lighting authority.
- Its tests are `tools/test-apartment-people-layer.mjs`, mapped in `run-touched-harnesses.mjs`.
