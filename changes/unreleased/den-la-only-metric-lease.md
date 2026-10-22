---
title: Release skipped Den capture slots in LA-only sessions
target: backend
type: fixed
---

Automatic LA-only Den sessions now release an exact capture lease when every configured worker declines that frame for sampling cadence. They no longer wait for an unused Jetson worker; original evidence expiry, identity checks and paired-session safeguards remain intact.

Existing source diagnostics also report bounded timings for the latest capture attempt, separating clock checks, camera reads, frame preparation and registration. These measurements do not alter evidence timestamps or validity.
