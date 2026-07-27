---
title: Speed up Apartment photo mode on mobile
target: web
type: fixed
---

Apartment photo mode now prefers a generated mobile splat asset on phone-sized
viewports, deploys create that runtime asset from the full scan when needed,
and the mobile screenshot audit fails if the UI remains stuck on loading photo.
