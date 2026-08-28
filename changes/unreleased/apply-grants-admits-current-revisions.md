---
title: Fix the Deploy Grant Stage at the Current Database Revision
target: web
type: fixed
---

The grant stage of a deploy would have failed. `apply-grants.sh` gates its ACL contracts on allowlists of database revisions, and every one of them stopped at `0021` while the deployed database had moved on to `0027` — so the script raised `partial identity finalizer E3 object set` instead of applying grants. The allowlists now reach the current revision, and a check fails any future migration that is not admitted by all of them.

Two kernels also lost privileges they were granted. The script revokes every function privilege from the write credential and then restores it per kernel, and neither owner-attested person creation nor the owner-attested partner commit had a restore block — so each would have stopped working on the next deploy, with no error at deploy time and a permission failure the next time the feature was used. Both are restored now, and a check requires a restore block for any future kernel the write credential is granted.

Nothing had caught this because the script had never run against a database past `0021`: the hosted gate stopped migrating there, and the Phase 3 activation ran its grant stage before the later migrations applied.
