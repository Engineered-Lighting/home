---
title: Add signed semantic cutover packet
target: backend
type: added
---

Add the separately keyed, offline Phase 3 semantic-cutover packet compiler and
ceremony. The packet binds the stopped legacy-writer evidence, all six privacy
receipts, the current erasure-ledger head, release/schema/capability digests,
and an explicitly non-authoritative candidate before database admission.
