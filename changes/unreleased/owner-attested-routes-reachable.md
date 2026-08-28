---
title: Make the owner-attested routes reachable
target: backend
type: fixed
---

Both owner-attested routes gated on `readiness_migration` equalling a revision
that was not a member of the `ReadinessMigration` literal. Since that literal is
closed, the setting could never hold those values and neither route could ever
be reached — each would refuse with a capability message that looked
deliberate.

They were also pinned to two *different* revisions. `readiness_migration` holds
one value, so even with both revisions admitted, any deployment could satisfy at
most one of them.

Adds `0027_owner_person_e5n` to the literal, pins both routes to it, and adds
tests asserting that every pinned route revision is a settable value, that
routes meant to be live together share one, and that every literal member has a
migration behind it. The six new migrations also now declare `revision: str`,
matching every other migration in the tree.
