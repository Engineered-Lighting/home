---
title: Make the Phase 3 catalog contract compare privileges, not grant order
target: backend
type: fixed
---

Revision `0011` verified the system catalog by hashing raw ACL text. PostgreSQL
ACL arrays preserve the order privileges were granted in, so a deployment whose
grants accumulated over time hashed differently from a freshly provisioned
database holding exactly the same privileges. No single pinned digest could
satisfy both, and the live database could never pass the check. ACL entries are
now sorted before hashing, so the contract describes the privileges a database
holds rather than the history of how they were applied, and the pinned digest is
updated to the canonicalised value. The check is no less strict: any change to
the privileges themselves still fails it.
