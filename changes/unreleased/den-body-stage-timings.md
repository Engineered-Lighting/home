---
title: Measure Den body processing and stage optional CUDA masks
target: backend
type: added
---

Den Live reports bounded timings for body queueing, segmentation waiting, segmentation inference and body construction from accepted frames. The isolated mask worker retains CPU as its default and supports an opt-in automatic CUDA mode with the existing GPU memory guard and CPU load fallback.
