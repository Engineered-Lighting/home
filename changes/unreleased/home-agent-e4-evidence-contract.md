---
title: Pin the agreement between the signed E4 documents and the tables they fill
target: backend
type: added
---

The semantic cutover reads four tables — writer evidence, the enforced writer
freeze, six privacy check receipts, and the authority candidate — and **nothing
in the application writes any of them**. The whole of `app/` contains exactly
two `INSERT INTO operations.` statements, both admissions. The only code that
populates the four is a test fixture, which is why the gap survived: the E4
gate exercises the commit kernel against a pre-populated database, so the
missing carrier is invisible from either end.

Steps 21–23 already sign documents that carry, in exact correspondence, the
column sets those tables want: 17 keys for the writer evidence, 27 for the
enforced freeze, 10 per privacy receipt, 28 for the authority candidate. The
schema and the producers were designed together; only the carrier between them
was never built.

These tests pin that correspondence in both directions, so a change to either
side fails in CI rather than in the ceremony with Home Assistant already
stopped. They also record the absence of a production writer explicitly, so
that when one lands the marker has to be updated deliberately rather than
quietly passing.

One asymmetry worth knowing: `enforced_legacy_identity_writer_freezes` is
created by migrations `0014`/`0015` and has no model in `app/schema.py` at all
— itself a symptom of nothing in the application ever having written it.
