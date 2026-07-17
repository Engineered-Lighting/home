---
title: Compile principal binding previews offline
target: internal
type: added
---

Adds a dormant, pure compiler and verifier for a private Home Assistant
account-to-person binding preview. The preview requires a fresh, server-supplied
authenticated-HA snapshot, exactly one reviewed person nominee with the
`operator_review_candidate` role, conflict-free graph cardinalities, and clear
privacy and erasure state. A nominee is only a person proposed as the user's
identity: the HMAC-bound preview explicitly keeps `me_identity_established`,
`binding_created`, external-receipt verification, fresh-state-origin
verification, and nomination-authority verification false. Protocol inputs
reject scalar subclasses and require distinct IDs for each semantic role; the
migration run and its finalization intentionally share one signed activity ID.
It remains replayable until a future single-use, transaction-time kernel, as
well as capability-disabled, non-authoritative, non-committable, and
disconnected from all production routes and writers.
