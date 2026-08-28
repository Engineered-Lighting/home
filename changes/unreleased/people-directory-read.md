---
title: Read the household and its relationships from the app
target: user
type: added
---

The app can now show who the system believes lives here, and the relationships
it holds as fact. Two read-only endpoints — `GET /v1/people-directory` and
`GET /v1/relationships` — expose the roster and the committed relationship
facts, resolved to display names.

Both apply the same visibility rule the parent-relationship ceremony applies
when it chooses candidates, kept as a single shared SQL fragment so the two
reads cannot drift apart. Row-level security already suppresses erased people
for every role, but it does not apply privacy directives or edge blocks; those
are enforced here. A relationship requires *both* of its people to be visible —
hiding only the blocked end would still disclose that the person exists and is
related to someone.

Nothing is written, no grant is added, and the predicate vocabulary stays
closed: the view's `predicate` field is the same `ContextPredicate` the context
layer admits, imported rather than restated, so a relationship can never name a
predicate the model is not already allowed to see.
