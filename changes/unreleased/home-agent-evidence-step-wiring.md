---
title: Record the signed cutover evidence in the steps that sign it
target: backend
type: fixed
---

Steps 21 to 23 signed the writer-freeze, privacy, and semantic-cutover packets
to private files, and nothing carried them any further. The semantic cutover
kernel reads four tables built from exactly those documents, so the ceremony
produced the evidence and the database never received it.

The evidence writer supplies the carrier; this connects it. Each step now
records what it just signed, through the same root stdin bridge the admissions
use, against the fixed image entrypoints the writer exposes.

Recording happens inside the signing step rather than as a step of its own, for
two reasons. A signed packet and its rows cannot drift apart if they are
produced together. And the activation journal requires `next_step` to equal
`STEPS[len(completed_steps)]`, so inserting steps into a live ceremony makes its
journal unloadable — the same constraint that put registration inside
`commit_finalizer`.

The order is forced by the schema and is asserted: the authority candidate
carries the writer evidence id and all six privacy check ids, and the cutover
admission carries foreign keys to the candidate. So the freeze evidence and
freeze row come first, then the six receipts, then the candidate, then the
admission.

The bridge's result contract is now per-command. The evidence writer records
rows rather than admitting one document, so it reports `recorded` and returns
the identifiers it wrote, where the admission writers report `admitted` and
return one id.
