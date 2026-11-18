---
title: Connect live model and forecast evidence to Home
target: web
type: added
---

Home's Den view uses the producer's connection clock for model freshness and registered motion annotations. Virtual beam previews respect Stop, manual fixture holds, source changes and the original evidence expiry.

Forecast updates are coalesced at the render boundary so ordinary receive-time clock uncertainty does not briefly clear valid annotations between packets. Model details use current detector model and round-trip timing fields.
