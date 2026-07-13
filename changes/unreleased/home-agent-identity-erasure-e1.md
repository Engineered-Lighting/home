---
category: security
title: Add dormant identity-erasure schema foundation
---

Add a fail-closed Phase 3 schema foundation for confirmed person-erasure
scope, a dormant mandatory subject retrieval-block marker, exact-source erasure
receipts, and a quarantined NOLOGIN identity-erasure kernel role. This slice
adds no deletion function, worker, or online-role authority and leaves the
existing descriptor-erasure workflow unchanged. Whole-person scope commitments
are relationally bound to the reviewed confirmation proposal digest. Existing
auto-expiry schedules are deliberately not accepted as E1 erasure authority;
their source and ACL hardening remains a separate fail-closed gate.

Add a one-command, local-endpoint-only PostgreSQL 17 admission harness that
uses separate behavioral, lifecycle, and tamper clusters; recreates the sole
kernel-bearing case database from a locked revision-0007 template; rejects
tampered constraints, policies, functions, triggers, roles, and ACLs; and
verifies atomic failure and downgrade guards. The harness sends only a filtered
source context to Docker and fails if bounded label-based cleanup leaves any
managed container, network, or image residue. Admission also seals the
dedicated cluster's role inventory, membership graph, reviewed
security-definer bodies/ACLs, views, rewrite rules, event triggers, and
inheritance boundary before E1 creates durable authority records. The pinned
PostgreSQL 17 system-catalog object/ACL baseline is exact, and logical
subscriptions, publications, slots, replication origins, non-default replica
identity, and parameter-level `SET`/`ALTER SYSTEM` grants are refused.
Current-database role/GUC defaults and offline-owner role defaults are also
required to be empty.
The Docker build context now enumerates reviewed Core trees from the Git index,
keeps pre-commit E1 tests on an exact explicit manifest, excludes stray
untracked files by aborting on unexpected source, and rejects symlink,
sensitive-path, binary, oversized, or non-UTF-8 inputs before contacting
Docker.
