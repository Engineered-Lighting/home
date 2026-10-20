---
title: Add stored Qwen captions to Lab JEV as causal historical context
target: backend
type: added
---

During an opted-in recording, the Lab now reads the caption already stored by the explicit camera refresh once a second. It never requests a refresh or runs a model. Each caption becomes a durable first receipt: its expiry is capped at 30 seconds and never extends past the caption's own expiry. The receipt records the source request, frame, time and content digest, marks the caption as correlated with the same camera, and records the model revision only when the running refresh configuration pins it. The caption text itself is never stored; the receipt keeps only its digest and projection. Out-of-order captions, captions already expired when first seen and changed-content identity conflicts are recorded explicitly and are never used.

JEV receives only a bounded activity projection (reading, phone use, computer work or unavailable). An activity counts only when a whole sentence states it plainly, and the projection returns unavailable when any sentence of the caption carries a hedge or negation, an identity reference, instruction-like text, a second person (including relatives, guests or someone else) or another activity. Live reasoning and replay select the same single latest caption that was received and still valid at the cutoff, and every arm the recipe grants gets it identically, whatever that arm's history window. Recipes withhold captions by default, and preparing a recipe that grants captions fails unless caption history is provisioned and the household policy grants caption egress. Caption-only evidence never triggers a JEV call, and the household JEV policy must grant `caption-activity-1` before any caption leaves Victoria.

RGB floor-contact room positions are projected as `rgb_floor_estimate`, separate from stereo depth. Projection version is now `structured-semantic-4` and semantics version `den-semantics-3`; the question version is unchanged.
