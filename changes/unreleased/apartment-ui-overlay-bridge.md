---
title: Add scoped Apartment presentation overlays
target: desktop
type: added
---

Adds a renderer presentation layer for room labels and transient dots using the existing registered scene and surface picks. Projection reuses private math storage, and layers withdraw when their viewport or scene lifetime ends, without changing room assets, fixture data or lighting controls.
