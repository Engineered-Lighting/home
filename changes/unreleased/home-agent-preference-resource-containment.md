---
title: Keep location opt-ins inside the storage budget
target: backend
type: fixed
---

Keep location preference disable requests available during degraded storage,
but fail closed before enabling private location retention when optional work
is suspended or storage state is read-only, unavailable, or unrecognized.
