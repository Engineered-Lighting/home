# Reviewed identity projection verifier contract

`reviewed_identity_payload.py` is the offline cryptographic boundary between a
fresh, read-only legacy Identity Store snapshot and a future atomic PostgreSQL
semantic finalizer. It has no network or database client and cannot authorize a
cutover or create a relationship fact.

## Canonical input

The verifier accepts at most 4 MiB of UTF-8 JSON. The supplied bytes must equal
the exact compact, key-sorted, UTF-8 encoding produced by the verifier. Duplicate
keys, floats, non-finite numbers, non-NFC text, Unicode control/format/surrogate
characters, extra keys, noncanonical UUIDs, and noncanonical UTC timestamps are
rejected before signature verification.

Every UUID created for a migration run, source item, decision, projection
receipt, or operator request is a lowercase canonical UUIDv7. Stable legacy
person UUIDs are preserved and may be another RFC 4122 version.

## Keyed commitments

Each HMAC-SHA256 commitment uses this byte framing:

```text
"home-agent-identity" || 0x00
|| uint16_be(len(ascii(domain))) || ascii(domain)
|| uint64_be(len(canonical_json)) || canonical_json
```

The HMAC key is selected by the reviewed run's positive epoch and must match the
deployment-pinned SHA-256 fingerprint. Distinct fixed domains bind source row
keys, allowed source projections, decisions, semantic candidates, projection
rows, projection references, receipts, and aggregate manifest roots. A value
committed in one domain cannot be replayed as another artifact type.

The verifier receives freshly reconstructed minimized source records separately
from the signed bundle. Their keyed row and allowed-projection commitments must
match the registered manifest. For every source row, the exact ordered allowed
projection list must then equal its reviewed apply/coalescing/omission decisions.
This prevents a signed semantic record from being detached from the source view
that the narrow legacy reader allowed.

## Review signature

The Ed25519 key is deployment-pinned for purpose
`identity_migration_review`, must not be revoked, and must match the exact raw
public-key SHA-256 fingerprint in the run. The signed canonical document is:

```json
{
  "domain": "reviewed-identity-migration-review-v1",
  "run": "all run fields except review_signature",
  "source_items": "the ordered full source manifest",
  "decisions": "the ordered full decision manifest",
  "projections": "the ordered full apply projection set"
}
```

Tests pin a deterministic HMAC vector and an Ed25519 vector so a change in
canonical bytes, length framing, domain separation, or signing behavior fails
across runtimes.

## Finalization compilation and signature

Successful review verification compiles an immutable, unsigned
`reviewed-identity-migration-finalization-v1` proposal. It deterministically
uses `finalization_id=run_id` and `lineage_id=receipt_id`, derives projection
subject roles, and HMAC-binds the decision manifest, receipt set, lineage set,
privacy-closure set, auto-expiry-effect set, and complete proposal. The proposal
remains `verification_status=candidate_unverified` and `authoritative=false`.

The original review signature is retained as an attestation explicitly scoped
to `reviewed-identity-migration-review-v1`; it is never relabelled as a
finalization signature. A distinct, purpose-pinned Ed25519 finalization key must
sign the canonical payload:

```json
{
  "domain": "reviewed-identity-migration-finalization-v1",
  "finalization": "the exact compiler-owned proposal"
}
```

Only `verify_finalization_envelope` can add the separately verified finalization
attestation and yield database-bound input. The envelope cannot substitute any
proposal field. The module has no signing private key and no database or network
client.

The compiler emits one privacy closure per person in person-projection order.
Ignored people must be content-suppressed tombstones and cannot have aliases,
recognition bindings, role candidates, or relationship candidates. Ignored or
do-not-track people cannot have an active recognition binding. All projection
subjects must be people inserted by the same run; status and directive kinds are
unique per person. Auto-expiry must be strictly future and creates a deterministic
effect with `schedule_id=directive_id`, `outbox_id=receipt_id`, topic
`privacy.person.auto_expire`, due time, and its own keyed commitment.

An exact signed envelope may be cryptographically checked after review expiry
only through `verify_projection_bundle_for_exact_replay` followed by
`verify_finalization_envelope(..., allow_expired_for_exact_replay=True)`, or the
combined `verify_expired_finalization_replay` helper. The reconstructed bundle
and final document report temporal expiry, and an expired bundle cannot emit
signing bytes even when given a backdated clock. These modes are solely for a
database kernel to compare against an already committed finalization after a
lost response. First admission remains fail-closed after expiry.

The output never emits raw source rows or accepts actor, principal, authority,
cutover, parent-fact, memory, place, initiative, action, or model fields.

Allowed typed semantic targets are:

- private canonical People rows;
- executable privacy directives;
- reviewed names/nicknames;
- Frigate recognition bindings;
- reviewed archived status;
- legacy role labels with `perspective=unknown`;
- legacy relationship candidates with `perspective=unknown` and
  `authoritative=false`.

The verifier stores verified projections internally as canonical bytes and
returns a fresh parse for each access, so caller mutation after verification
cannot alter the later finalizer document.

The verification policy's HMAC key and both canonical private-document byte
fields are excluded from Python debug representations. Logging or formatting a
policy, verified bundle, or verified finalizer object therefore does not emit
the commitment key, person names, source references, aliases, or projection
content. Callers must still treat the explicit projection/document accessors as
private data.

## Dormant E2 compatibility intent

`identity_finalizer_compatibility.py` is a second offline, non-deployable
boundary. It accepts raw bundle and envelope bytes and invokes both verification
paths itself; it never accepts a caller-constructed verified document. It then
cross-checks each projection's same-run lineage tuple and includes every person
subject, including both endpoints of a legacy relationship candidate.

The comparison input is a canonical, HMAC-bound, maximum-five-minute E2
tombstone snapshot. Each row carries only pseudonymous person/block/outbox IDs,
the positive ledger epoch, and exact block/hash/digest commitments. Rows must be
strictly sorted and unique, the high-water mark must equal the maximum included
ledger epoch, and the snapshot policy and revision must match the finalization
policy. A blocked-subject intersection fails with only
`identity_erasure_blocked`, never the person identifier.

E2 also permits an active block before ledger attachment; that row cannot be
represented by this ledger-bound observation contract. The input therefore
cannot establish a complete negative view even when its row list is empty.

Even a disjoint or empty supplied snapshot produces only
`compatibility_status=coverage_unproven`. The intent permanently reports
`non_deployable`, `capability_disabled`, `authoritative=false`,
`commit_ready=false`, `enables_writes=false`, and
`atomic_commit_enforced=false`. It cannot prove snapshot completeness or
current database state, acquire a transaction lock, or substitute for the
future `SERIALIZABLE` database kernel. No API, store, BFF, UI, Compose service,
migration, or role activation imports it. The live Core service does not mount
the operator source tree.

## Dormant principal-nominee staging

`principal_binding_candidate_staging.py` is an offline, non-deployable bridge
from the signed finalizer document to a future principal-binding ceremony. It
accepts the raw canonical review bundle, raw finalization envelope, freshly
reconstructed source rows, deployment-pinned verification policy, and exactly
one UUIDv7 Person-projection receipt ID. The receipt is the only selector: the
compiler accepts no name, alias, HA user, principal, actor, legacy `me` label,
or caller-constructed verified object.

The compiler re-runs both signature checks, freezes the private source rows
exactly once as a canonical source-item-ID-sorted snapshot, and verifies the
selected Person's decision, receipt, projection, lineage, source digest, and
signed-artifact commitments. It rejects an archived nominee, every known signed
privacy directive, and display ambiguity after NFKC normalization, whitespace
collapse, and case folding. Re-verification requires the original raw
artifacts, reconstructed rows, and exact receipt selector again and returns a
fresh object; the stable keyed nomination commitment deliberately excludes the
verification clock.

Selecting a signed receipt proves only which reviewed Person row is being
nominated. This module authenticates neither the selector's caller nor an
operator's authority to make that nomination. Its output therefore reports
`nomination_role=operator_review_candidate`,
`nomination_authority_status=unverified`, `nominee_count=1`,
`operator_nomination_authority_verified=false`,
`selector_authority_verified=false`, `me_identity_established=false`, and
`binding_created=false`. Current HA identity, binding graph, person status,
privacy, retrieval, erasure, confirmation, single-use consumption, and
transaction state are also unverified. The result remains `non_deployable`,
`capability_disabled`, `coverage_unproven`, non-authoritative, write-disabled,
and non-portable. No Core API, store, BFF, UI, Compose service, migration, or
runtime role imports it.

## Dormant parent-candidate staging

`parent_confirmation_staging.py` is a separate offline bridge from the signed
finalizer document to the later parent-confirmation ceremony. It accepts only
the raw canonical review bundle, raw finalization envelope, freshly
reconstructed source records, and deployment-pinned verification policy. It
invokes both signature-verification paths itself and accepts no parent selector,
child ID, caller-constructed verified document, display label, or receipt digest.

The compiler derives every signed `legacy_role_candidate` whose exact role is
`parent`, requires exactly two distinct People projections that are not marked
archived in that signed snapshot, and rejects compatibility-normalized
ambiguous display labels or any known parent privacy directive. Each result
binds the signed People and role decision IDs, projection receipts, projection
commitments, compiler-owned lineage commitments, and source snapshot digests.
Candidate ordering is canonical by stable person UUID.

This proves source provenance only. The result permanently reports
`non_deployable`, `capability_disabled`, and `coverage_unproven`, with
`authoritative=false`, `commit_ready=false`, `enables_writes=false`, and
`atomic_commit_enforced=false`. Current person status is explicitly unverified.
The Python result and its private payload are not portable proof: any future
consumer must submit the original raw signed artifacts and reconstructed source
rows to the compiler's re-verification boundary. It cannot prove current
retrieval blocks, complete erasure state, or a current HA-user-to-child binding;
it cannot mint a review code or confirmation artifact, accept a private gesture,
choose valid-from, or create either parent fact. No Core API, store, BFF, UI,
Compose service, migration, or runtime role imports it.

## Still disabled

This verifier does not make finalization callable. Revision 0009 provides only
the expired-by-default login/NOLOGIN owner pair and normalized lineage
foundation. A future database kernel still requires complete erasure handling
and atomic `SERIALIZABLE` PostgreSQL 17 tests. Production remains pinned to 0006
and the dormant capability continues to report `finalization_enabled=false`.
