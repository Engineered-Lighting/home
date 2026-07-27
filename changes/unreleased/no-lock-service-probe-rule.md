---
title: Avoid probing unavailable lock entities
target: backend
type: fixed
---

Made the live assistant instructions explicit that this home has no exposed
lock entities, so front-door lock requests should be refused without probing a
guessed `lock.front_door` service call.
