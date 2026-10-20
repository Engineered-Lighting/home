---
title: Prepare an isolated Jetson detector worker for the live comparison
target: backend
type: added
---

Add an explicitly launched Jetson-only worker factory using the existing authenticated specialist protocol. Require pinned FP32 model settings, bounded process resources and lifetime, an expiring trial grant, and a separate durable execution ledger. Opening Home cannot launch it; no live workload is enabled by this change.
