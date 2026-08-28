---
title: Record relationships between two other household members
target: backend
type: added
---

The owner can now assert a relationship they are not part of — Holly is a parent
of Ben, Felipe and Ashley are partners — where before only relationships with
one end at the account holder could be recorded.

That is a weaker claim than asserting about your own life: two people are
involved, neither has an account, and neither consented. The record does not
flatten the difference. Receipts gain `assertion_scope`, derived rather than
supplied: `self` when the attester is one end, `third_party` when they are not.
Both stay `authorized_administrator` on the authority axis — that says who had
the standing, not how close they stood — so a later review that decides
third-party assertions need something stronger can find every one of them with a
`WHERE` clause instead of re-deriving the graph.

`parent_of` is now recordable this way too, and is deliberately *not* written
symmetrically: writing the inverse would assert that a child is a parent of
their parent. The receipt's edge count is constrained to match the predicate.
