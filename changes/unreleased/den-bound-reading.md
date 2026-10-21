---
title: Bind virtual reading lighting to fresh source evidence
target: backend
type: fixed
---

Virtual Den reading spotlights require a fresh, matching anonymous reader and
original inference expiry; stale replies and legacy unbound inputs retain
ambient-only behavior. Recording worker failures retry with bounded backoff,
intentional stops remain stopped, and genuinely unmarked optional zones are
skipped while malformed marks are refused.
