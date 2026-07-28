---
title: Separate Phase 3 restore and erasure evidence
target: backend
type: changed
---

Replace the conflated Phase 3 restore placeholder with independent,
root-owned receipts for a successful isolated database restore and a current
external erasure-ledger gate. Off-host backup and activation-source admission
remain fail-closed until their own verifiable ceremonies exist.

GitHub-hosted PostgreSQL 17 acceptance passed in workflow run `30394033276`.
A live read-only preflight from the accepted checkout kept production at
revision `0006a` in `record_only`, observed all required containers ready and
the encrypted local repository healthy, and reported 220 of 500 qualifying
events without creating a receipt or changing rollout state.
