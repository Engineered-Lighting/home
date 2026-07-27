---
title: Add the dormant current identity-authority verifier
target: backend
type: added
---

Add a database-only, content-free categorical verifier for determining whether
a reviewed E4 identity semantic-authority promotion remains current against
the database erasure overlay. Production remains pinned to revision 0006a in
record-only mode; its post-quarantine PostgreSQL catalog is hash-pinned, and
this adds no binding, API, UI, or live activation path.
