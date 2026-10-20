---
title: Keep Lab comparison arms moving after proven non-execution, and measure delivery
target: backend
type: fixed
---

A paired arm is no longer blocked for the rest of a session by a request proven never to have run, such as a local pre-dispatch failure or an exact busy/unauthorized/closed worker rejection. Unproven requests get a delayed identity-only lookup, repeated with doubling delay for up to about 46 s only while the worker ingress answers busy, and otherwise stay indeterminate; dispatch times live in a side table so the previous controller build can still write the request ledger; and once both workers are proven stopped a slow local drain after Stop clears on its own instead of wedging new recordings. Live state and findings now report controller time, per-arm output age, freshness counts and gaps, preview acquisition counters, grouped terminal reasons, and the exact failed Jetson preflight gate, with fewer storage scans per frame and freshness windows, resource gates and limits unchanged.
