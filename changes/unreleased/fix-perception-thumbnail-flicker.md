---
title: Fix perception thumbnail flicker
target: web
type: fixed
---

Preload camera perception thumbnails before rendering them, so slow or failed snapshots stay text-only instead of flashing blank or broken image boxes.
