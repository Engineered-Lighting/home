---
title: Deduplicate legacy aliases that the semantic store treats as one
target: backend
type: fixed
---

The legacy identity store lets one person carry the same alias text under several alias kinds, but `identity.aliases` enforces `UNIQUE(normalized_alias)` globally and `UNIQUE(person_id, normalized_alias)`. The duplicate surfaced only inside the finalizer kernel, as an opaque `identity_finalizer_projection_conflict`, after the packet had been reviewed, signed, and registered. The migration plan now keeps the first alias per normalized form and refuses outright when two different people claim one alias, since choosing a winner is not the importer's decision.
