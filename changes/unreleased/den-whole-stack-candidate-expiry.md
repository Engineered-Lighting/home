---
title: Continue bounded Den checks past expired representative frames
target: internal
type: fixed
---

Treat a correctly bound but expired RGB descriptor as unavailable and try a
later frame without extending its lease. Recheck both original leases against a
same-session server clock sampled after descriptor receipt. Collect terminal
cleanup evidence even after a genuine metadata-check failure, preserving the
original failure.
