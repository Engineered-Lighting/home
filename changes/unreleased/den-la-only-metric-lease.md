---
title: Release skipped Den capture slots in LA-only sessions
target: backend
type: fixed
---

Automatic LA-only Den sessions now release an exact capture lease when every configured worker declines that frame for sampling cadence. They no longer wait for an unused Jetson worker; original evidence expiry, identity checks and paired-session safeguards remain intact.
