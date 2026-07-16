---
category: security
title: Contract identity-erasure restore authority
---

Replace the restore credential's schema-wide identity, knowledge, engagement,
privacy, and ingest DML with a replay-safe reset and an exact table/function
allowlist. The credential no longer receives direct binding-table mutation or
any table `DELETE` capability; existing descriptor and person-ledger restore
paths retain only their required reads, inserts, and bounded updates.

Keep E2 person tombstone and normalized residual writes limited to the offline
owner on grant replay; the NOLOGIN kernel can read only person identifiers from
the tombstone and principal tables. Exact semantic-policy roles may execute
only the three suppression predicates; trigger functions remain non-callable,
and the restore login receives only the owner-owned v2 replay entry point. No
online role gains direct tombstone or residual DML, and future objects remain
inaccessible until they receive a separately reviewed conditional ACL.
