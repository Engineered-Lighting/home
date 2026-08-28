---
title: Record an owner-asserted partnership through the API
target: backend
type: added
---

Adds `POST /v1/partner-attestation`, the call that reaches the owner-attested
partner kernel through the separate binding-committer credential.

Unlike the parent-relationship flow there is no second party to stage a proposal
for and no review code to read aloud, so this is a single attested call rather
than stage-then-confirm. What does not change is where authority comes from: the
request body carries no authority field and no identifiers. The kernel writes
`authorized_administrator` as a literal, and the adapter derives every primary
key by domain-separated SHA-256 from the ceremony seed, so a caller can neither
claim a confirmation that did not happen nor choose an id that collides with
another row.

The route is pinned to the revision that provisions the kernel's caller. Before
that migration the grant does not exist, so the call fails with a clear
capability message instead of a permission error.
