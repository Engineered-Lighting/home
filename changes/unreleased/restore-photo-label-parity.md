---
title: Restore consistent Photo rendering while fixture labels are repaired
target: web
type: fixed
---

Reverts the fixture canvas-label suppression that made the two Photo renderer paths diverge. Existing labels return while a corrected one-label change is qualified separately; renderer comparisons now also run for Apartment and marker changes.
