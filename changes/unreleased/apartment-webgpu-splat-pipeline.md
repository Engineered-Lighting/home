---
title: Photo on the WebGPU renderer preview (off by default)
target: desktop
type: added
---

With `?renderer=webgpu`, the Apartment view's Photo mode now renders through Home's own Gaussian splat renderer instead of reporting unavailable. Splats are re-sorted on the GPU every frame, so their order no longer lags while orbiting, and the scan is parsed in a background worker. Photo matches the default Spark renderer at fixed poses, including the dollhouse cutaway, the exterior cleanup and the photo floor. A device check page (`webgpu-splat-check.html`) measures splat frame rate on a phone or Mac. Without the flag, Photo still uses Spark and nothing changes.
