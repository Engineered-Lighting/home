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
owner on grant replay. The NOLOGIN kernel receives only person identifiers and
an exact six-column, forced-RLS projection of `knowledge.fact_versions`, with
no table-level fact access or fact DML. Exact semantic-policy roles may execute
only the three base suppression predicates; the additional principal-scoped
fact-version visibility helper is callable only by the API, ingest, and erasure
roles, not by `PUBLIC` or the kernel. This lets initiative RLS evaluate its
source fact without granting ingest direct fact-table access. Grant replay pins
that helper's language, execution settings, volatility, and exact body digest
before restoring any caller, and commits a caller-side function quarantine
first so a rejected replacement cannot retain stale execution grants. Trigger
functions remain non-callable, and the restore login receives only the
owner-owned v2 replay entry point. No online role gains direct tombstone or
residual DML, and future objects remain inaccessible until they receive a
separately reviewed conditional ACL.

Restore the NOLOGIN erasure kernel's ordinary `EXECUTE` edge to its three
read-only suppression helpers after grant replay revokes owner privileges, so
anti-resurrection triggers can evaluate normal writes without widening their
external surface. Also remove the initial lineage trigger function's legacy
default `PUBLIC EXECUTE` grant.
