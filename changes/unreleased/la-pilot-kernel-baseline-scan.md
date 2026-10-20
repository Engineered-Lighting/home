---
title: LA camera pilot's one-time kernel-log scan gets enough time
target: backend
type: fixed
---

The camera pilot's first scan of the kernel log back to its 2026-09-26 baseline now has 180 s instead of 15 s. The scan had grown to about 40 s, so every health check failed and camera chat and live Qwen were refused. Later incremental scans keep the 15 s limit.
