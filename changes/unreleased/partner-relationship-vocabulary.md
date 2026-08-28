---
title: Admit partner_of into the relationship vocabulary
target: backend
type: added
---

`partner_of` joins `parent_of` as a recognised relationship predicate, with the
invariants a predicate needs rather than just a new string.

`knowledge.fact_versions.predicate` is `varchar(128)` with no CHECK, so the
storage layer would accept anything; everything that makes a predicate safe
lives elsewhere and has to be added deliberately. This adds a uniqueness index
scoped to currently-believed accepted facts, so the same pair cannot be asserted
twice, and a constraint preventing anyone being recorded as their own partner.

`partner_of` is symmetric where `parent_of` is asymmetric. The legacy store
models that as two directed rows, and the fact model follows it — exactly as
`parent_of` uses two facts for two parents — so the uniqueness index makes each
*directed* edge unique and the symmetric pair stays a transactional invariant
for the commit kernel.

This grants no ability to write such a fact; that needs a `SECURITY DEFINER`
kernel under its own role. It lands after the object-side erasure fix so a
partnership can never exist while erased people remain visible through it.
