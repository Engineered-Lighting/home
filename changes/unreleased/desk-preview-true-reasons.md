---
title: Den desk preview reports the real refusal and announces new frames early
target: backend
type: changed
---

When the approximate den placement refuses a frame, the virtual desk preview
now reports that refusal's own reason, such as a changed camera epoch, a
changed mount or no person in view, instead of a generic "awaiting
observation". The live status also announces each new metric image as soon as
it is registered, so Home can fetch it before the model result arrives. No
expiry, timing or identity limit changed.
