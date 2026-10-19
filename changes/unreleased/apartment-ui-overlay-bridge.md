---
title: Add scoped Apartment presentation overlays
target: desktop
type: added
---

Adds a renderer presentation layer for room labels and transient dots using the existing registered scene and surface picks. Moving labels use transforms to avoid repeated page layout, projection reuses private math storage, and layers withdraw when their viewport or scene lifetime ends.
