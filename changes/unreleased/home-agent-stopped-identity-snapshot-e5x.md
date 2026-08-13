---
title: Add stopped Home Assistant identity snapshot
target: backend
type: added
---

Security: add a root-only, fixed-path E5x capture ceremony that briefly stops
Home Assistant, copies the consistent legacy Identity Store directly onto the
encrypted Ubuntu private volume, verifies its digest and SQLite integrity, and
restarts Home Assistant from a failure-safe boundary without sending private
People bytes through OneDrive, argv, or logs.
