---
title: Keep Qwen supplied during metric camera recording
target: backend
type: fixed
---

Share each accepted original metric-camera frame with the existing always-on Qwen buffer, as the non-metric capture path already does. This prevents active recording from starving Qwen while preserving original evidence timing, virtual-only permissions and independent failure handling.
