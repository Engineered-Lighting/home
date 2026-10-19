---
title: Preserve shared pose crops across local and remote execution
target: backend
type: added
---

Support the pinned RTMPose worker through remote execution using frame-bound,
digest-checked person proposals. Local and remote arms can share the same crop
resolver without transmitting human labels or changing source pixels.
