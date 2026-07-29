---
title: Add fixed dormant Phase 3 migration entrypoints
target: backend
type: added
---

Add five exact, operator-only migration roles for the reviewed Phase 3 schema
checkpoints. The normal deployment migration remains pinned to revision
`0006a`, automatic startup migration is rejected for every Phase 3 role, and
no Compose activation surface or production rollout is added.

The complete hosted E1-E5l PostgreSQL 17 authority gate passed in workflow run
`30395870684` at source commit `74c751628f0559452215897d1fd251e7277f0f51`.

The subsequent Ubuntu verification at source-plan commit `be02ad3` routed all
five roles through stubbed Alembic/Python commands to their exact revisions.
The accepted 86-entry source pack matched digest
`9862ecd4b6d0368d6324106b376a619224d12bab1855149bbb7223c944d58f73`;
no database, service, or rollout state was changed.
