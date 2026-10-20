---
title: WebGPU renderer preview for the Apartment view (off by default)
target: desktop
type: added
---

Opening Home with `?renderer=webgpu` now renders the Apartment view with three.js r186's WebGPU renderer (falling back to its WebGL2 backend where WebGPU is unavailable; `?renderer=webgpu-webgl` forces that backend). Cloud, Mesh and the virtual fixture visuals match the existing renderer; Photo reports unavailable on this preview until the WebGPU splat pipeline lands. Without the flag nothing changes.
