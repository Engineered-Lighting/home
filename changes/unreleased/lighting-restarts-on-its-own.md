---
title: Cross-home lighting restarts on its own
target: backend
type: changed
---

The private lighting service now uses `restart: unless-stopped`, like the other
Home Agent listeners, after an 8-hour sustained check. A crash or a host reboot
no longer leaves lighting between homes down until someone starts it. The
lighting deployment is recorded in `docs/CROSS-HOME-LIGHTING-RELEASE-2026-09-29.md`.
