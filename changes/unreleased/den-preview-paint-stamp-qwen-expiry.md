---
title: Time the den spotlight's paint and end Qwen answers at their expiry
target: web
type: changed
---

The live den view now notes when each den spotlight snapshot reaches the screen,
so the den's cue-to-light timing can be measured. Its Qwen line stops showing an
answer 10 seconds after it came, or sooner when den-live sends the answer's own
expiry, instead of after 30 seconds.
