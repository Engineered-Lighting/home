---
title: Fix an unregistrable reviewed identity packet for people with a legacy subrole
target: backend
type: fixed
---

The legacy identity store splits one relationship across `relationship_type` and `relationship_subrole`, and the private People review emitted a `legacy_role_candidate` for each column. That put two decisions of one kind on a single source item, which the identity migration kernel refuses, so any household where a person had a subrole produced a reviewed packet that could never be registered. The review now emits one candidate per person, preferring the specific subrole and falling back to the type.
