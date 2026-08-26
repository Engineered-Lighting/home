---
title: Collapse a legacy identity row to one role candidate at the source
target: backend
type: fixed
---

A legacy identity row expresses one relationship across `relationship_type` and `relationship_subrole`, and the migration plan emitted a role candidate for each column. The identity migration kernel accepts at most one decision per kind on a source item, so any person with a subrole made the reviewed packet unregistrable. The plan now yields a single candidate per row, preferring the specific subrole and falling back to the type. This supersedes the narrower fix in the private People review, which shaped only the review document and not the packet the signing ceremony compiles.
