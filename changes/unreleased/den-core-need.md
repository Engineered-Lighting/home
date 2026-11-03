---
title: Add den-core need v0, turning Qwen reading facts into a room PersonState
target: internal
type: added
---

Adds an offline den-core policy that turns the LA perceiver's Qwen scene facts into a room-level PersonState for one anonymous person. It says whether that person is starting, engaged in or ending reading, using the same reading rule and the same on/off timing as den_lighting's dwell, which is what the reading-shadow replay scores. A test checks it against den_lighting's own code. It runs only on replayed or synthetic events: it sends no light commands and touches no host, Home Assistant or camera.
