---
title: Fix Apartment camera feed loading on mobile
target: web
type: fixed
---

Apartment camera snap mode now loads mobile Frigate snapshots sequentially
instead of replacing the image on a fixed timer, so slow travel connections do
not leave calibrated camera views stuck at connecting.
