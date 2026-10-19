---
title: Use commissioned clock evidence for live detector frames
target: backend
type: fixed
---

Refresh and validate short-lived Chrony evidence for each live detector JPEG
through the existing camera acceptance path. Preserve the original causal
schema-3 metadata and image bytes; expired or invalid timing yields unavailable
input rather than a fallback timestamp.
