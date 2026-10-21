---
title: Explain missing joints in Den body tracking diagnostics
target: backend
type: added
---

An opt-in fast-tracking diagnostic packet preserves each image joint's response,
omission reason and paired-depth result so body reconstruction failures can be
traced to the exact frame. It keeps original evidence expiry and distinguishes
visible depth-surface points from fitted anatomical joints or persistent tracks.
