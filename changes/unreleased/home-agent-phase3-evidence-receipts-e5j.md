---
title: Separate Phase 3 restore and erasure evidence
target: backend
type: changed
---

Replace the conflated Phase 3 restore placeholder with independent,
root-owned receipts for a successful isolated database restore and a current
external erasure-ledger gate. Off-host backup and activation-source admission
remain fail-closed until their own verifiable ceremonies exist.
