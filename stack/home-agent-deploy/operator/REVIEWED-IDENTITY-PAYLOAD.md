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

## Allowed output

The verifier can emit only a `reviewed-identity-semantic-finalizer-input-v1`
document containing the run ID, review/projection root commitments, and the
verified typed projection set. It never emits raw source rows or accepts actor,
principal, authority, cutover, parent-fact, memory, place, initiative, action,
or model fields.

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

## Still disabled

This verifier does not make 0008 finalization callable. The future database
kernel requires its own expired-by-default login/NOLOGIN owner pair, normalized
receipt-to-target and affected-person lineage, complete erasure handling, and
atomic `SERIALIZABLE` PostgreSQL 17 tests. Until then, the production revision
remains 0006 and 0008 continues to report `finalization_enabled=false`.
