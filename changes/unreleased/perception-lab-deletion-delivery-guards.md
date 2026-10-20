---
title: Prevent Lab media delivery during deletion
target: backend
type: fixed
---

Reject new exports while media deletion is pending and revalidate direct media
reads before returning bytes. An open file handle cannot bypass generation
invalidation after an explicit deletion request.
