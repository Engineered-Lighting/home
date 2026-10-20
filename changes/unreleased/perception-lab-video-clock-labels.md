---
title: Label recordings before sensor clock alignment
target: backend
type: added
---

Support annotations bound to a recording's encoded video timeline and initialization
object. Preserve them across review/export while excluding them from session-clock
scores. Expose the recorded duration and enable matching Home controls only when
the backend advertises this capability.
