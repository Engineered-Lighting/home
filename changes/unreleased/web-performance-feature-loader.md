---
title: Faster web app feature loading
target: web
type: changed
---

The web app now defers heavier optional surfaces until first use or idle prefetch, adds `/perf` diagnostics, and keeps a `lazyFeatures` escape hatch for quick rollback.
