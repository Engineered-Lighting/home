---
title: Own software fixture commands within a bounded Den session
target: backend
type: added
---

Allow a fixed installed software-command capability to share the Den recorder
and lifecycle, with readiness before worker admission and command settlement
before Journal closure. Preserve explicit incomplete outcomes and keep worker
replay separate from unproven combined command and original-data replay.
