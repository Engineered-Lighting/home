---
title: Keep predictive lighting checks on pinned Python
target: internal
type: fixed
---

Predictive lighting CI uses a verified, preinstalled Python 3.12 interpreter so runner image updates cannot change its hash-pinned synthetic test dependencies. Missing or incompatible interpreters fail without downloading a replacement or changing the dependency hashes.
