---
title: Restore JEV requests and use fresher depth capture evidence for reading
target: backend
type: fixed
---

Keep local reading diagnostics out of the JEV provider API, and choose the closest actual camera anchor with one bounded retry when depth capture lags. Reading qualification preserves original evidence expiry and refuses originals that required development timing warnings, while virtual viewing remains available.
