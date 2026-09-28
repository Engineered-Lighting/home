---
title: Provision fixed shared preference sources through owner maintenance
target: backend
type: added
---

Add a bounded, transactional maintenance command for the two issuer registrations
and four preference-source capabilities. Exact repeats are read-only; changed or
revoked registrations require review. The command creates no person links, source
grants, service logins, or listeners and leaves existing memory untouched.
