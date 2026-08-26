---
title: Carry the signed identity cutover evidence into PostgreSQL
target: backend
type: added
---

The semantic cutover kernel reads four tables — the legacy writer evidence, the
enforced writer freeze, six privacy check receipts, and the authority candidate
— and nothing in the application wrote any of them. The whole of `app` held
exactly two `INSERT INTO operations.` statements, both admissions. The only code
that filled these four was a test fixture, so the gap was invisible from either
end: the hosted gate exercised the commit kernel against a database the fixture
had already populated, while the signing ceremony produced documents that went
nowhere.

Steps 21 to 23 already sign packets carrying, key for key, the columns those
tables want. `app/identity_evidence_writer.py` is the carrier that was missing.
It reads one signed packet on stdin, converts every value to its column's own
type — identifiers to UUIDs, timestamps to aware datetimes, counts to integers,
flags to booleans — and inserts it. It invents nothing and maps nothing.

Three arms, reachable only through fixed image entrypoints
(`identity-evidence-freeze`, `identity-evidence-privacy`,
`identity-evidence-cutover`), each running through the existing `migrate`
service and its owner credential, so no new service or secret is introduced.

Replay is verified rather than assumed. Each insert is `ON CONFLICT DO NOTHING`
followed by reading the stored row back and comparing it in full: a repeated
step succeeds, and a different document reusing an identity is refused instead
of being mistaken for one.

The contract tests are the point of the change as much as the writer is. They
pin the writer's column sets against the operator producers' frozen key sets,
the producers against the real table definitions, and each arm's packet shape
against the producer that signs it — in both directions, so drift on either
side fails in CI rather than in the ceremony with Home Assistant stopped. They
already earned it: they caught a hand-assumed packet shape for the cutover arm
before it could reach a gate.
