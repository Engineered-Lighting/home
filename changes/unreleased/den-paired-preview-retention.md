---
title: Retain exact Den stereo pairs before model admission
target: backend
type: added
---

Add an explicit bounded controller configuration that retains each accepted
preview's matching numerical bundle and calibration in the existing Lab Store
before offering its original RGB frame to inference. Preserve commissioned
capture acceptance, original clocks, expiry and Stop fences; missing depth clock
commissioning leaves the private pair unqualified. Existing configurations keep
their current capture path, and no camera owner or physical writer is added.
