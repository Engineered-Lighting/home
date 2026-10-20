---
title: Recover desk preview after a metric frame is cadence-dropped
target: backend
type: fixed
---

The optional desk-preview source now releases an exact image wait when both analysis arms have provably dropped that image for sampling cadence, allowing a newer depth-backed frame to be considered. Existing analysis rates, original image expiry, ordinary image fallback and Stop behavior are preserved; this fix is validated offline and has not been activated.
