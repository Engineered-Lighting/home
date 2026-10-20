---
title: Represent unassessed Den fixtures without inventing lighting support
target: backend
type: added
---

Add an explicitly selected room-policy successor that keeps all four fixture
identities while allocating only independently assessed domains. Unassessed
domains remain unavailable, and qualified vacancy fades continue across eligible
domains when an earlier domain assessment expires. The existing worker profile
and command owner do not acquire this capability automatically.
