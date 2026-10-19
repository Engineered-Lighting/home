---
title: Preserve original camera timestamps through Lab preview ingestion
target: backend
type: fixed
---

Convert camera UTC nanoseconds to seconds with one final rounding so valid
Perception Lab outputs agree with the context journal's original-clock check.
Original timing receipts, uncertainty, expiry bounds and exact retry behavior
are preserved. This local source fix has not been deployed.
