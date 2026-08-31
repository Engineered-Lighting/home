---
title: The Relationship Vocabulary Update Can Be Deployed
target: backend
type: fixed
---

The change that lets friends, siblings, roommates, neighbours and colleagues be recorded had no way to be applied to a running system. Database updates past a fixed early point each need their own named deployment step, and this one was written without one, so it could be merged but never actually take effect.

It now has that step.
