---
title: Add bounded admission for the Victoria camera pilot
target: backend
type: added
---

Add a dedicated authenticated camera-pilot endpoint with durable single-request
accounting, a persistent cooldown, fixed image/token limits and no automatic retry
after uncertain model delivery. It uses the existing LA model and does not claim
global GPU isolation. Deployment and live Home acceptance are tracked separately.

Anchor browser freshness to authenticated acceptance time plus monotonic elapsed
time so desktop clock skew does not reject a fresh result. Serialize local camera
clock publication and permit up to three fully revalidated read-only snapshot
attempts before any model submission; inference itself is never retried.
