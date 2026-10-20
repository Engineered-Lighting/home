# Live Home context producer contracts

`home-live-shadow-1`, stage `producer-ingress-1`, admits only the explicitly
pinned `home-live-fact-1` and `home-live-geometry-1` publication families.
These are additive contracts. Historical profiles, payloads and schema pins
are unchanged. Future live engagement, intent, decision, checkpoint and commit
types are **not** admitted by naming this profile.

The additive [engagement worker stage](WORKER.md) now provides explicit version-two
commissioning, separate worker schema pins and private authority for engagement,
checkpoint/commit and unavailable decision baseline. Config version one remains
producer-only; the original schema files and interface digest are unchanged.

The additive [profile and scene stage](SCENES.md) requires explicit version-three
commissioning and a separately pinned scene schema bundle. It adds authenticated
bind/unbind intents, durable application results, stable approved scenes and a
separate presence-background goal. Allocation/execution remain unavailable.

The additive [stationary delivery stage](STATIONARY-DELIVERY.md) requires explicit
version-four commissioning and its own delivery schemas/pins. It realizes one
protected virtual task fixture through committed movement, settling and fade
samples; prior stage meanings remain unchanged. Background delivery without a
commissioned channel remains explicitly unmet. This is controlled software
integration, not real Den perception or physical lighting qualification.

Later explicitly selected commissioning stages preserve those historical scopes:

| Config/manifest | Added local behavior | Consumer binding |
|---|---|---|
| 5 | Retain a feasible stationary aim while revalidating every fresh geometry cut | 3/4 |
| 6 | Scene-scoped brightness settlement and qualified lifecycle/hazard recovery | 5 |
| 7 | Repaired producer/temporal admission, bounded service-clock mapping and conservative delivery policy4 | 6 |

Config7 selects worker6 and decision/checkpoint/commit/input-cut6 with a separate
`conservative-delivery-schemas` graph, exact pins and grants. It does not rename
historical fields or reinterpret older checkpoints. Its modeled-emission and
person-observation coverage requirements are described in
[stationary delivery](STATIONARY-DELIVERY.md#config7-conservative-successor).
These are local controlled software capabilities; current source/interface
receipts and independently measured owner prerequisites remain necessary before
claiming a connected real-Den experience or physical lighting qualification.

The [registered resident view](RESIDENT-VIEW.md) now projects the admitted
geometry into the actual Apartment canvas with an explicit commissioned map
binding, evidence/expiry checks and permanent write isolation. It reuses the
public transport and adds no service publication or decision authority.

The [accepted handoff](../../docs/contracts/first-live-den-handoff/README.md)
and [dictionary](../../docs/contracts/first-live-den-handoff/FIELD-DICTIONARY.md)
define the source/time/head/authority semantics. `schemas/DIGESTS.json` pins
that reviewed bundle plus the exact schema graph. Its interface digest is
SHA-256 over canonical JSON of the schema-name-to-document graph. Individual
file hashes use UTF-8 with LF line endings.

Generate with `node tools/build-home-live-context-schemas.cjs`; verify without
writing with `node tools/build-home-live-context-schemas.cjs --check`.
The generator writes only this new `schemas/` directory and needs no added
dependencies. Each schema is self-contained with local `$defs` references,
using the journal's fail-closed standard-library validator subset.

Facts contain closed semantic kinds, values and reasons, not coordinates or
free-text model descriptions. Metric geometry requires separate whole-type
authority and has eight required parts; unavailable parts are explicit.
Shape alone confers no calibration, valid clock, association, measurement or
runtime grant. Probabilities are null unless immutable independent calibration
and applicability references are supplied and admitted by policy.

Both families have a disjoint `withdrawn` source-barrier control variant.
It has service-clock assertion time and no fabricated capture, roots, person,
shape or camera clock. The durable journal enforces generation compare-and-swap,
causal head ordering and no resurrection independently of retained body expiry.

Structural limits are 32 physical/observation roots, parents, receipts,
support refs and conflict refs; eight association alternatives; 16 transform
refs; 128 retired publication refs; 12 body and four protection primitives.
Arrays of roots/refs are unique. Numeric vectors are fixed length three;
volume radii/extents are positive. IDs are ASCII opaque tokens capped at 160
characters; paths, URLs and free text do not fit this identifier grammar. Part IDs,
components, clocks, stores, roots, methods, calibration and transforms must
also resolve to their exact frozen manifest/grants. Schema caps do not establish
anatomical completeness or measured precision. The journal's 64 KiB message
and configured page/session/database limits still apply.

Cross-field identity equality, root-map unions and original-time maxima,
commissioned clock conversion, parent causality, capture progression, per-part
support, vector normalization and additive uncertainty sums require semantic
journal checks as well as schema validation. Missing commissioned bounds are
unavailable; schemas do not fill them with zero or allowed-error thresholds.

The handoff's fictional examples remain immutable design fixtures. They are
not a production manifest, source grant, Den commissioning record or accuracy
claim. No camera, model, host, household media or physical writer is involved
in generating these contracts.

## Implemented ingress runtime

The initial journal/HTTP integration is opt-in with `--profile home-live-shadow-1`.
Follow the [operator procedure](../../docs/runbooks/home-live-context-ingress.md)
for the exact frozen manifest, separate capture/control/read grants, finite CLI
receipt catalog, original-owner resolver boundary, restart and rollback.
Runtime currently admits null probabilities only. Required parents are current
dependencies; their retirement withdraws derived heads. Association loss without
a qualified replacement requires an explicit source barrier. Future live worker,
intent and lighting families remain refused in that initial configuration.
The version-two worker stage is separately opt-in. Verification is local and synthetic;
no Den acquisition or deployment is established by this contract implementation.

The explicit [conservative delivery successor](CONSERVATIVE-DELIVERY.md) records
config7 clock/cut/retention semantics and the corresponding repaired consumer
versions. It does not change the initial or historical profile meanings.
