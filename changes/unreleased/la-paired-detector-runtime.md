---
title: Prepare isolated LA paired detector dependencies
target: backend
type: added
---

Add a Linux-specific hash-locked dependency candidate matching the Jetson
YOLO26s trial's application versions. Keep LA's production Qwen environment
unchanged and record the CUDA, Python and architecture differences. No LA
installation or detector workload is enabled. Prepare a separate LA-only
three-frame runner with cgroup checks, exact input hashes and an exclusive
attempt marker; failed attempts cannot silently retry.
