# Registered live resident view

The historical binding-one read-only Apartment consumer uses the local commissioning contract
`home-live-resident-binding-1`. It introduces no service publication family,
grants, sensor interpretation or decision authority. Existing config3 journal
messages and their schema pins are unchanged.

The binding has exact fields:

```text
schema, version, scope, manifest_ref, live_manifest, registration,
pack, room_to_scene, qualification, digest
```

`scope` is the full site/room/session/domain. `manifest_ref.digest` must equal
canonical JSON SHA-256 of the complete version-three `live_manifest`.
`registration` is the exact registered source geometry/mount/calibration/
coverage/transform record, not a surface name guessed into a room. Source methods,
physical roots and clock/age limits resolve through that pinned manifest.

`pack` is `{site_id,revision,manifest_digest,frame_digest,registration_version}`.
Its manifest digest is canonical JSON SHA-256, while its frame digest is the
exact asset-byte hash declared by the registered content pack. The active
descriptor and actual engine scene identity must match those pins. A missing,
altered, differently registered or unapproved binding cannot receive geometry.
The controlled software-test qualification remains explicit and does not imply
independent measurement or owner approval of real Den content.

`room_to_scene` is `{matrix,from_geometry_digest,to_frame_digest,unit}`. `matrix`
is an explicit 16-value column-major rigid transform in metres; identity is
accepted only when explicitly commissioned. Scaling, reflection, shear and
guessed transforms are refused. Geometry and scene-frame digests remain distinct
and must match the transform's declared endpoints. A coordinate label alone
does not commission this relationship.

`qualification` is `controlled_software_test` or `live_shadow_unqualified`.
`digest` hashes the canonical complete binding excluding `digest` itself. The
binding is supplied by reviewed page commissioning before any Home service
mount, then copied and frozen. It is not accepted from an inference message,
ordinary browser navigation or a model-generated path.

The boot options also specify `resident_source_paths` and `resident_assets`.
Source paths admit named renderer/vendor/font modules on the same origin.
Asset paths admit only the pack index and the exact bound site/revision.
`resident_assets` values are exact SHA-256 byte pins, verified through bounded
Fetch before a caller receives the bytes. XHR cannot bypass verification. Limits
are 4,096 named source paths, 129 pinned pack/index assets and 64 MiB per asset.
No credentials, token or filesystem path is carried in this binding.

## Projection and rendering

`HomeLiveGeometryView.createView({binding})` exposes:

```text
update({transport,clock,clock_uncertainty_s}) -> view
readback() -> view
binding() -> immutable-value copy
```

Only exact public journal publications included in a fresh committed worker cut
can supply current geometry. Causal observation, producer availability, journal
commit, worker receipt, decision and output publication order remain distinct.
Source/client clock uncertainty, source max age, part expiry and modeled age
uncertainty all tighten current validity. Later worker ticks cannot refresh an
old observation. Reconnect, revocation, malformed input or clock failure removes
current authority instead of restoring a convenient old head.

The projection distinguishes measured/inferred basis, unknown, unobservable,
conflicting, withdrawn and historical records. Current parts retain original
geometry/evidence references and timestamps. A bounded history of 128 metadata
records has no shapes/coordinates and supplies no current rendering authority.
Unknown body/head direction remains unknown rather than drawing an assumed gaze.

`attachLiveGeometryLayer(engine,binding)` is an additive module on the actual
Apartment engine/root/camera/canvas. It draws only projection-admitted
`current_parts`, applies the commissioned room-to-scene transform and owns only
its own geometry/materials. `draw`, `readback` and `dispose` never choose fixtures
or modify the resident model. Return removes this layer while retaining the
resident engine, camera, root, model and unrelated data.

For binding one/config3, the worker's execution `display_lease` remains withdrawn
because that stage has no allocation/execution implementation. Observational
geometry has separate bounded display admission; it does not gain beam authority
from that observation. The binding-two delivery extension below explicitly adds
committed virtual output. Ordinary engine beam groups retain no such authority.

## Existing Home integration

The actual Home index and `HomeApp` select this host only when the immutable
shadow capability includes a validated binding. It mounts the existing
`HomeApartmentView` and uses its ordinary renderer and registered pack loaders.
No replacement renderer, camera or schematic viewer is introduced.

The page uses a scoped, read-only viewport registry and a registered `model.json`.
It does not edit the normal Echo/Victoria registry, active home selection or
runtime assets. Device, map, calibration and native authority remain absent;
the existing data/service facades also refuse writes directly. The live source
adapter never starts the normal tracker, synthetic person feed or lighting
replay. Optional renderer prewarming is disabled in this explicit shadow mode.

The same public transport supplies geometry and the embedded profile/scene
controls. The persistent Node worker remains the only decision authority.
Actual binding ingress/application receipts and scene continuity now appear
beside the resident room rather than requiring the scene-only host.


## Version two: exact pack model and committed virtual output

`home-live-resident-binding-2` keeps the same top-level fields and rigid spatial
registration rules. Its `pack` additionally requires `model_asset`, exactly
`model.json` or `seed-model.json`. The host resolves only that commissioned name;
there is no filename fallback or mutation of the registered immutable pack.
Version-one bindings retain their exact original shape and `model.json` reader.
Version two explicitly accepts a pinned version-three or version-four live
manifest; version one remains version-three only. The named asset must still
exist in the same content manifest and pass the bounded exact-byte Fetch fence.
This adapter does not independently qualify the real Den pack or lift the
64 MiB asset bound.

Only binding two with live manifest four emits `home-live-geometry-view-2`.
It retains all observational geometry admission from version one and adds a
separate `execution` projection. A current projection requires an exact public
`home-live-shadow-decision-3`, its intact input-cut digest, a current lease,
selected approved profile/example/scene, requested work goal, one allocated
commissioned fixture and its committed virtual sample. The source registration,
causal receipt times, source/client uncertainty and half-open expiry still
apply. A virtual sample and its publication commit must precede the earliest
qualified client time (`now - clock_uncertainty_s`); expiry uses the latest
qualified time. Clock uncertainty cannot expose a future sampled state.
Every currently consumed person's body, head, both hands and protection
must remain supplied; the target additionally needs current work geometry.
The lease's raw geometry/protection references must match the complete current
geometry set, not only the selected person's working region. An endpoint
proposal, uncertain anatomy, missing sample, unrelated reference or expired
lease supplies no illuminated display authority.

Current `execution` includes `execution_id`, `continuity`, `sample_at`, `interval_start`,
`interval_end`, `valid_until`, `scene_id`, `episode_id`, approved `profile_ref`
and `example_ref`, the exact publication `decision_ref`, `input_cut_digest`,
`geometry_refs`, `protection_refs`, `assessment_refs`, a single actual
`readback`, and `truth_role:"virtual_execution"`. Its resolved `fixture` is
`{fixture_id,fixture_pose_ref,origin_m,direction_unit,beam_half_angle_rad,range_m}`.
The origin, local basis, envelope half-angle and displayed range come only from
the pinned delivery policy; sampled pan/tilt is converted once in that basis.
Aim, brightness and CCT come from committed sampled readback. The drawn envelope
excludes pointing-error inflation and is neither the narrower work-coverage cone
nor the wider protection assessment. A cone's appearance establishes no lux,
receiver surface, optical measurement or physical clearance.
The allocated command remains distinct from achieved sampled aim and level.

The owned resident layer displays a fixture marker and its sampled aiming
orientation while moving or settling in darkness. It draws an explicitly
simulated cone only when that current readback has positive brightness. There
is no interpolation to a future endpoint, second execution loop or ordinary
Apartment beam authority. `no_beams` is true for dark/unavailable samples and
false only for admitted positive current output. The layer verifies that this
flag agrees with phase/brightness and the resolved fixture agrees with its
binding before drawing. Invalid samples clear all owned visuals.

`HomeLiveResidentUI.readback().rendered` now reports `beam_count`,
`fixture_count`, and the exact projected `execution` alongside the existing
geometry and resident identity readback. Local 100 ms projection ticks remove
expired virtual visuals without requiring another server publication. Return,
remount, registration mismatch and the permanent mounted writer fence preserve
the original no-actuation authority boundary. This is modeled virtual delivery,
not measured useful illumination, physical clearance or a deployed live Den
inference loop.

`continuity` is `initial_dark_start`, `continuous`, or
`discontinuous_dark_reset`. Recovery that restarts from the commissioned virtual
initial pose is labeled as a simulation reset rather than traveled continuity
from a previous sampled pose. The resident panel preserves that distinction.

## Explicit successor bindings and page clocks

Bindings three/four select config5 retained stationary delivery. Binding four
also pins the read-only equipment/model adapter and exact resident asset
selection; it does not turn surveyed positions into camera or optical calibration.
Binding five selects config6 scene-scoped brightness and qualified lifecycle
recovery. Binding six selects config7/policy4, scene3 and decision6; old bindings
and historical schema bytes keep their original meanings.

Binding six requires an independently qualified service-clock policy in the
complete pinned manifest. A bounded request/receipt interval maps its current
sample onto the browser monotonic clock, with transport delay and uncertainty.
The mapping stream and authority generation must match the transport. Exclusive
mapping expiry, anchor divergence or unavailable qualification removes current
output. The wall/monotonic refusal latch belongs to one page and survives React
remount. A refresh creates a new page and requires a new qualified mapping; an
old page's latch is not persisted as cross-refresh clock qualification.

A still-leased eligible prior sample may bridge a newer positive sample's clock
uncertainty interval only for the same scene, sampled aim/fixture, source-generation
map and authority. Withdrawal, dark motion, changed aim, source generation or
revocation prevents that handover. This is display of a prior committed sample,
not interpolation or retrospective certification by newer geometry.

Policy4 separately pins modeled virtual emission and independently qualified
person-observation coverage; both expiries bound the lease. Capture metadata
retains its exact raw input-cut reference but cannot masquerade as supplied
anatomy. Protection references cover the complete qualified occupant rows used.
See the [policy4 conditions](STATIONARY-DELIVERY.md#config7-conservative-successor)
and [visual rendering limits](../../docs/design/virtual-spotlight-rendering.md).
Real Den coverage/accuracy, useful service and physical operation remain
independently unqualified.
