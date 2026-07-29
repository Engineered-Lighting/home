---
title: Add one-time identity authority admission writers
target: backend
type: added
---

Identity finalization and semantic cutover now have private, content-minimized
one-time admission writers behind a root-only bridge. Requests are canonical,
bounded, database-evidence-bound, serializable, exact-replay-safe, and never
accepted through arguments, environment variables, logs, or an online API.
