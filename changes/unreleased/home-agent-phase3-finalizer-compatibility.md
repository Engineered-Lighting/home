---
title: Bind identity finalization to erasure state offline
target: internal
type: security
---

Adds a dormant compatibility compiler that re-verifies signed identity
finalization inputs, derives every affected person from same-run lineage, and
rejects overlap with the exact supplied short-lived, ledger-attached E2
tombstone rows. Its result is permanently non-deployable, write-disabled, and
coverage-unproven; no database, API, BFF, UI, or live-system path is enabled.

Also redacts the commitment secret and canonical private identity documents
from Python debug representations and hardens dormant parent-flow import
containment checks. Removes the unused whole-operator-directory mount from the
live Core API container so offline tooling is not physically shipped into that
runtime.
