---
title: Stabilize Apartment mobile camera overlays
target: web
type: fixed
---

Apartment camera snap views on mobile now use a cache-busted Frigate snapshot
refresh path for calibrated overlays, avoiding iOS Safari MJPEG stalls while
keeping the feed mapped into the same projection frame as the mesh.
