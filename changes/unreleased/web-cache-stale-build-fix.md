---
title: Web App Stops Reusing Stale Builds
target: web
type: fixed
---

The browser service worker no longer caches or falls back to executable app shell files, so a slow mobile/Tailscale load cannot reopen an older deployed build after tabs or the browser app are closed.
