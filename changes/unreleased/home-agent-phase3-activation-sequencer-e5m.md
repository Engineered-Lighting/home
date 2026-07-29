---
title: Add fail-closed Phase 3 source admission and grant arming
target: backend
type: added
---

Add a root-only activation sequencer that can admit the exact hosted-tested
source and arm a single-link grant permit only after every E5j preflight
blocker clears. Grant replay at revisions 0017 through 0021 now stops before
its first ACL mutation unless the isolated operator service mounts that exact
permit. E5m does not execute migrations or change rollout state.

The complete hosted E1-E5m PostgreSQL 17 authority gate passed in workflow run
`30468702387` at source commit
`f918c091405e481c69c820f96181d08754999787`.
