---
title: Select desk corners on the detailed registered scan
target: web
type: fixed
---

Show why a desk corner was not added while keeping the selected points and
corner count visible, so a missed surface or drag does not silently fail.
Distinguish unavailable scan geometry, a missing triangle and an unselected raw
hit without admitting an unfiltered point as a desk corner.

Use the detailed registered scan mesh for desktop selection and record its
source in version2 target exports. Aim calculation validates that source against
the independent content manifest; earlier version1 exports remain supported.
