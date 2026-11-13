---
title: Den pose worker loads beside the V-JEPA live worker
target: backend
type: fixed
---

The AI host's den pose worker now needs 16 GiB of free GPU memory before it loads, down from 32 GiB. That matches the supervisor's preflight gate, which has been 16 GiB since 2026-09-29. With the V-JEPA live worker resident, about 32.1 GiB is free, so the old gate refused every den lease even though the worker's allocator is capped at 512 MiB. Den pose and V-JEPA can now run together.
