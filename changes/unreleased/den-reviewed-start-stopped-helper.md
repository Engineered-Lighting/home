---
title: Preserve an operator Stop during reviewed Den trials
target: backend
type: added
---

Add an explicitly selected startup helper that holds the Den activity loop at operator Stop before its thread starts, including during baseline recovery. It does not change the default service entrypoint or deploy itself; resuming still uses the existing authenticated control.
