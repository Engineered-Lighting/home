---
title: Add physical legacy writer-freeze evidence
target: backend
type: added
---

Home Agent Phase 3 now has a production writer-freeze evidence boundary. A
fixed HAOS collector runs only while Home Assistant is stopped, holds the
legacy process lock, verifies the exact immutable SQLite trigger fence and
external witness, probes blocked writes, and confirms clean checkpoint,
journal, integrity, and source-projection state. A purpose-separated offline
signer binds that physical observation to the exact reviewed identity run and
produces the non-authoritative E3 evidence and stronger E4 enforced-freeze
records with resumable, content-free receipts.
