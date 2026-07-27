---
title: Add a hosted Home Agent web boundary gate
target: backend
type: added
---

Adds a GitHub-hosted-only security gate for the private Agent BFF and origin.
It runs deterministic tests in a networkless pinned Node image, builds both
Linux AMD64 images from deny-by-default contexts, and smoke-tests them on a
disposable internal-only Docker network. Only trusted `main` builds may emit
short-lived image archives, immutable image identities, checksums, and signed
GitHub provenance; the Ubuntu deployment continues to forbid local builds and
pulls.
