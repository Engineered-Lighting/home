---
title: Preserve rendered tracking updates with broad clock uncertainty
target: web
type: fixed
---

Actual GPU-completed tracking updates retain timing evidence when clock
uncertainty spans zero latency. Receipts preserve the raw interval and mark the
nonnegative causal bound explicitly; impossible or malformed intervals remain
rejected. Clock admission, evidence expiry and GPU deadlines are unchanged.
