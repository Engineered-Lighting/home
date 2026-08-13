---
title: Add private People review packet
target: backend
type: added
---

The first E5x private People review boundary reads a stopped, root-owned legacy
Identity Store snapshot through the existing query-only allowlist, suppresses
ignored identities, and identifies the unique `me` plus two legacy parent
candidates without creating authoritative relationship facts.
