---
title: Precache the web app shell in the service worker
target: web
type: changed
---

The web service worker now seeds the app shell and top-of-boot-chain modules
into its per-deploy cache during `install` (best-effort, never blocking the
worker takeover), so a mid-navigation box reboot is more likely to fall back to
a warm shell instead of a blank load.
