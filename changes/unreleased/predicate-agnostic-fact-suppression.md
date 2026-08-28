---
title: Suppress erased people on the object side of every relationship
target: backend
type: fixed
---

`privacy.identity_fact_is_blocked` is the `USING` and `WITH CHECK` expression of
the restrictive policy that hides erased people's facts. It returned false for
any predicate other than `parent_of` before it looked at the object at all, so
the object side of a fact was checked for exactly one predicate.

That was sound while `parent_of` was the only person-to-person predicate. It
stops being sound the moment a second one exists: an erased person would remain
visible as the object of a relationship, which is exactly the disclosure the
erasure kernel exists to prevent. The hole is closed before any new predicate
lands, not after.

The replacement is strictly stronger — true everywhere the old body was true,
plus the cases the predicate guard skipped — so no fact that is currently
suppressed becomes visible. The signature is unchanged, so the policies binding
to it need no edit.
