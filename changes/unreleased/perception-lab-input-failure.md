---
title: Distinguish missing analysis inputs from uncertain execution
target: backend
type: fixed
---

Mark read-only input-loading failures as failed before analysis dispatch,
without exposing private error details or leaving a false uncertain reservation
that blocks recording deletion. Preserve post-dispatch uncertainty handling.
