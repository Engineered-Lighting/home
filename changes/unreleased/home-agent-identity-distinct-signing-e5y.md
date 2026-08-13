---
title: Add distinct-purpose identity signing ceremony
target: backend
type: added
---

Home Agent Phase 3 identity activation now includes a resumable, offline
distinct-purpose signing ceremony. It compiles a complete reviewed legacy
People packet, requires separate exact-digest private approvals, exposes only
the review key to the review phase and only the finalization key to the
finalization phase, and independently verifies both signatures before writing
the non-authoritative finalizer document. Fixed systemd credential launches
deny network access, accept no content or key paths, and persist root-only,
fsynced private state plus a content-free receipt.
