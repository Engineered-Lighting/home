---
title: Camera pilot kernel-fault baseline moved past its own scan OOM
target: backend
type: fixed
---

The camera pilot's kernel-fault baseline now starts at 2026-11-11 21:37:42 UTC. That's just after an owner-inspected out-of-memory kill inside the pilot's own memory cgroup, where its slow baseline scans had piled up. Before this, the pilot refused every camera chat and live-facts request. The source now matches the host release that runs it.
