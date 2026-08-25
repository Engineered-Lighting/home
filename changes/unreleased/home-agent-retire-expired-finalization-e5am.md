---
title: Recover from a finalized identity packet whose run expired
target: backend
type: added
---

The signing ceremony can supersede a reviewed packet only while it is unsigned
and staged at the four-step await boundary. A packet that was review-signed and
finalized, and whose reviewed run then expired before anything was registered,
had no recovery at all — the runbook recorded that such a state "is out of scope
and fails closed for separate owner review". The live activation is in exactly
that state, so the ceremony could neither finish nor start again.

The activation runner gains `retire-expired-finalization`. It applies only where
the ceremony actually strands: the journal parked at `commit_finalizer`, a
reviewed run whose window has lapsed, and a packet in any phase it can be
stranded in — `staged`, `review_signed`, or `finalized`. The ten-minute window
holds two interactive signatures and two container round-trips, so lapsing
before `finalize` is at least as likely as lapsing after it, and those earlier
phases previously had no recovery verb in either tool. It
refuses unless a new read-only `migration` probe proves that no run was
registered, no admission written, and nothing finalized — registration is
one-shot for the life of the database, and no role can delete a run row, so
retiring a packet whose run was already registered would leave a successor that
could never be registered.

It archives the three private artifacts under `*.retired-<run_id>.json` beside a
content-free `identity-finalization-retirement-<run_id>-e5am.json` receipt. It
writes nothing to the database and never overwrites an archive.

The journal is deliberately not rewound. Rewinding is unnecessary, because the
unchanged ceremony's `stage` refuses only while a state file exists and step 17
signs the finalizer itself when the document and receipt are absent. It is also
unsafe: the migration executor guards its source revision before migrating and
is not idempotent, and the binding-kernel provisioner refuses a second run, so
re-banking steps 6–16 would fail partway.

The archive name is deliberately `.retired-` rather than the ceremony's
`.superseded-`. The latter means an unsigned packet whose staged review window
lapsed; conflating the two would misdescribe the private record.
