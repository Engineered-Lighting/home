---
title: Compile atomic parent confirmations offline
target: internal
type: added
---

Adds a non-deployable, digest-bound compiler and verifier for reviewing exactly
two private parent relationships as one atomic intent. The capability remains
disabled and non-authoritative; no API, database writer, BFF, or UI route is
enabled, and the compiler authenticates no receipt or confirmation gesture.
