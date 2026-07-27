---
title: Fix direct visual question routing
target: backend
type: fixed
---

Direct camera questions such as "what's going on in my driveway" now invoke the
grounded visual look path deterministically instead of relying on the model to
choose the tool.
