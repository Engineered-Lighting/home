---
title: Recover the web app from stale service worker after a box reboot
target: web
type: fixed
---

The browser/Tailscale web app no longer strands on a red "boot chain stopped"
overlay when the AI box is rebooting. The service worker now derives its cache
name from the per-deploy asset version (so a new deploy always supersedes a warm
cache instead of requiring a manual `v2 → v3` bump), takes over immediately via
`skipWaiting()` + `clients.claim()`, applies a generous network timeout with
cache fallback for the shell, and prefers the network for JSX modules once it has
been alive a while. The boot loader now treats a failed boot-file fetch as a
recoverable "Reconnecting to home…" state: it retries with exponential backoff so
the app self-heals when the box returns, and offers a "Reload now" button that
unregisters the service worker and reloads — a full escape hatch that no longer
requires clearing browser site data. Service-worker version and boot outcome are
logged to the console for future debugging.
