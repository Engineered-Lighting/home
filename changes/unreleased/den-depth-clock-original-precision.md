---
title: Preserve precise original RGB time during depth mapping
target: backend
type: fixed
---

Make retained depth-clock mapping use the same single-round nanosecond conversion
as the existing camera preview. Valid real-resolution originals no longer fail
an inconsistent float comparison; freshness, uncertainty and identity checks
remain unchanged.
