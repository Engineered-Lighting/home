---
title: Match RTMPose input channels to the pinned model export
target: backend
type: fixed
---

Convert registered BGR frames to RGB before RTMPose normalization, matching the export's preprocessing contract, and bound its runtime thread pools. Add remote detector-plus-pose execution under one durable reservation, retaining frame provenance and separate stage timings without duplicating the household session database.
