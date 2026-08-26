---
title: Run step 17's registrar and executor modules against real kernels
target: backend
type: fixed
---

Step 17 runs three production modules in sequence: the registrar writes the
reviewed run, the admission writer admits the finalizer document, and the
authority executor finalizes it. Only one of the three had ever been driven
against a database.

The other two were covered the way every defect in this workstream was covered
— by a test that stopped just short of the seam. `identity_migration_registrar`
was exercised with a fake backend, and the finalizer kernel was exercised with
hand-built SQL, so the module's own URL pin, request contract, manifest
pre-check and retry loop met the kernel that actually refuses things nowhere.
`identity_authority_executor` was worse off: every finalization in the suite
called the kernel function over a hand-built engine, so the module the ceremony
invokes for the last leg — the one that pins its database URL and sets the
session GUCs the kernel inspects — ran against no database at all.

Both now do. The executor case runs writer and executor back to back, which is
the pair and the order the ceremony uses.

The registrar needs a cluster of its own, and the reason is worth recording.
It refuses any database not literally named `home_agent`, which rules out the
renamed disposable database the kernel contracts run in; and a database admits
exactly one `record_only -> shadow` authorization, which the E3 fixture already
holds. A gate phase is a fresh cluster, so its `home_agent` carries an
authorization nothing else has spent. The same phase also asserts the property
the whole ceremony is arranged around — that a second registration is refused,
because the grant is one-shot with no `DELETE` anywhere to reclaim it. That had
never been observed through the module that spends it.
