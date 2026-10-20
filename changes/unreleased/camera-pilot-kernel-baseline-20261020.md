---
title: Camera pilot kernel-fault baseline moved past the Qwen3.6 download OOM
target: backend
type: fixed
---

The LA camera pilot's kernel-fault baseline now starts at 2026-10-20 01:36 UTC. That's just after an inspected out-of-memory kill of the one-off Qwen3.6 download container, so the pilot no longer refuses to start because of it. The source now matches the host release that is already running.
