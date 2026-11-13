---
title: Calm the live body's look and take its facing from the skeleton
target: internal
type: changed
---

Follows the owner's review of the live body in the apartment view.
- The per-dot glow is off, and the trail length is configurable.
- Guessed points no longer flicker, in both the WebGL layer and its WebGPU twin.
- Facing comes from the shoulder and hip lines instead of the nose and ears, with the same debounce.
- The sight wedge under the facing ring is smaller.
- Tests: `tools/test-apartment-body-annotation-model.mjs` covers facing from the skeleton.
