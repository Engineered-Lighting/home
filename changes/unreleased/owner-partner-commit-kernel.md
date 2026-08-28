---
title: Add the dormant owner-attested partner commit kernel
target: backend
type: added
---

Adds one `SECURITY DEFINER` kernel that can atomically record a partnership the
owner asserts, plus its own receipt ledger. It creates no fact, mounts no API,
grants nothing to a runtime role, and stays unreachable until a later reviewed
change provisions its caller.

`parent_of` was committed with authority `explicit_related_party` because a
second party confirmed it in a browser. Most people in a household have no
account and cannot confirm anything, so a partnership the owner asserts alone is
a weaker claim and is recorded as one: `authorized_administrator`, written as a
literal so a caller can never choose its own provenance. Owner-attested receipts
live in their own table so they can never be mistaken for confirmed ones. The
attestation is registered as a real artifact, so the support rows have a root to
point at rather than being unprovenanced.

A partnership is symmetric and is stored as two directed facts, both written in
the same transaction or neither — a half-recorded partnership would satisfy the
uniqueness index while being false one way round.

The subject must be the bound account holder. Asserting a partnership between
two other people needs its own review of who may speak for whom.
