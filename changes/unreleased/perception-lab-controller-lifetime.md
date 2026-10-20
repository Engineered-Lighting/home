---
title: Protect Perception Lab controller ownership and shutdown
target: backend
type: fixed
---

Acquire exclusive OS ownership before opening the Lab ledger or recovering work,
preventing a second controller from treating active sessions as crashed. Drain
local workers before closing SQLite; uncertain remote requests remain reserved,
and incomplete local shutdown retains the database and ownership lock.
