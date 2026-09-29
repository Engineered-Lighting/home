---
title: Core lighting request, resolution and signed-client kernel
target: internal
type: added
---

Core gains the inactive building blocks for explicit cross-home lighting: a
typed lighting request, deterministic resolution of light names against each
home's allowlisted inventory (with clarifications instead of guesses), and a
signed client for the Home Assistant light-only endpoint that never retries an
uncertain execute. Nothing calls them yet.
