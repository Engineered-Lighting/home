---
title: Apartment mobile camera feed stops gray flicker
target: web
type: fixed
---

Mobile Apartment camera snaps now avoid the iOS WebGL undistortion canvas and prefer the signed Home Assistant stream before falling back to Frigate snapshots, preventing gray bands during live camera playback.
