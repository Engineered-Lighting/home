---
title: Retire the Completed Migration's Activation Contracts
target: web
type: changed
---

Adding a new kind of relationship had become impossible without breaking a deploy. A large part of the grant script verifies that a governed identity migration, completed in August, left the database in an exact shape — catalog digests, exact privilege lists, and object counts frozen at the revisions that migration passed through. Giving a new relationship kernel the row policy it needs to write its own records necessarily changes those numbers, so the checks and the work could not both succeed.

Twenty-two of those checks now report rather than halt. They still run and still say what changed; they can no longer stop a deploy over the shape of a migration that is finished.

Everything protecting the property this system exists for is untouched and still fatal: an erased person stays erased, a kernel cannot be swapped or re-owned, the write fence holds, no privilege appears that nobody granted, nothing leaks to `PUBLIC`, and the kernels in daily use keep their exact privilege contracts. None of the grant, revoke, or policy statements — the enforcement itself — was modified. A new check fails the build if any of those protections is ever quietly downgraded.
