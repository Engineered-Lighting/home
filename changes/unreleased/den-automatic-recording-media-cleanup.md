---
title: Bound automatic Den recording retention for continuous operation
target: backend
type: added
---

Continuous Den commissioning can opt into automatic media cleanup in an exact
dedicated Store, with an explicit creation cutoff and a maximum 24-hour media
age. It uses the existing runtime deletion guards, preserves structured
measurements, and leaves historical and manual recordings untouched. Cleanup
failures or expired recordings that still need execution reconciliation stop
the activity loop through its existing monitor backoff and expose the reason
in status. This prepares continuous commissioning; it is not a live soak pass.
