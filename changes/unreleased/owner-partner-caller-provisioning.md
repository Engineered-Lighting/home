---
title: Provision the caller for the owner-attested partner kernel
target: backend
type: added
---

The owner-attested partner kernel was created unreachable, with no `GRANT`, in
the same way the parent kernel was left dormant until its own reviewed change.
This is that change: it grants `EXECUTE` to one role and nothing else.

It does not widen the runtime API role, which holds no identity write privilege
and must keep holding none — verified against the live deployment, where the
equivalent parent kernel is executable by the binding committer and *not* by the
API role. A compromised API can read a relationship but still cannot write one,
which is the whole reason a kernel exists rather than an `INSERT`.

`EXECUTE` for `PUBLIC` is revoked before the grant, since PostgreSQL grants it
on new functions by default. The migration also refuses to provision a caller
that is a member of the kernel role: the kernel rejects any caller able to
`SET ROLE` into it, so such a grant would make it permanently unreachable rather
than more permissive.
