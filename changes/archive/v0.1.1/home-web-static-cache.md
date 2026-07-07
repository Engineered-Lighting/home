---
title: Speed up Home web static assets
target: web
type: changed
---

The private Home web gateway now uses versioned caching for static app files,
faster reuse for Apartment 3D assets, and a conservative service worker that
avoids proxy/API/live home-state routes.
