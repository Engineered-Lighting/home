---
title: Own canonical software commands within a bounded Den session
target: backend
type: added
---

Add a fixed session attachment that pairs retained worker originals with accepted
room commits before using the canonical software fixture consumers. One owned
thread manages native readiness and cleanup; uncertain recording or dispatch
remains incomplete, and worker replay does not imply combined command replay.

Honor durably recorded worker resets by abandoning only the preceding unaccepted
preparation while preserving accepted commands and exact reset retries.
