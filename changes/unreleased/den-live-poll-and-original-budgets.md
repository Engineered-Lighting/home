---
title: Separate finite Lab polling from retained capture capacity
target: backend
type: added
---

An explicit Lab source and session profile now budgets polling attempts separately from distinct retained capture originals, so empty reads and retries do not exhaust capture capacity. Its package and protected mount readers preserve the existing wall, archive, lease, and Stop/drain limits and refuse canonical commands until that profile is separately supported; this does not select a continuous operating cadence.
