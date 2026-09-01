# Apartment light aiming contracts

This document defines the control and data boundaries for Apartment light
aiming. Apartment may visualize an engineered fixture and may control
explicitly mapped Home Assistant light entities. It must not infer that an
ordinary ceiling light is an engineered gimbal, translate Apartment
coordinates into a bench command without an authoritative frame binding, or
acquire bench movement authority on the user's behalf.

## Home Assistant

Browser traffic uses the Home web gateway on port `5181`. The existing Home
connection flow stores the Home Assistant URL and long-lived access token in
the `hg-prefs` browser preference record. Browser API and WebSocket traffic is
routed through `/proxy/ha`; Apartment does not add another credential store.

The supported interfaces are the existing authenticated Home Assistant
WebSocket commands for entity, device, and area registries, `get_states`,
`state_changed`, and typed `call_service`. Spotlight and radial-zone roles are
mapped explicitly to entity IDs. The primary fixture `ha_entity_id` never
implies one of those roles. Entity names are not used for automatic mapping.

## Apartment model

The authoritative model remains the authenticated, revisioned
`/api/extended_openai_conversation/apartment_model` endpoint. It uses
`schema_version: 1` and optimistic revision checks. Engineered fixture fields
are optional additions to existing device records; existing device IDs,
positions, target IDs, room zones, fixture links, and tape measurements are
preserved. Browser local storage is recovery-only and simulation state is
memory-only. A simulation save must never reach Home Assistant or the
Apartment endpoint.

Only a device explicitly assigned
`fixture_kind: "engineered_gimbal_v1"` receives spotlight, gimbal, and six-zone
configuration. Transient gimbal telemetry is never persisted in this model.
Visualization calibration is explicitly non-authoritative for movement and is
stored separately from tape and radial orientation calibration.

An Engineered profile is added to an existing physical ceiling-mount record;
it is not placed as a second device. The existing primary `ha_entity_id`, exact
position, room, named-target relationships, and fixture-position measurements
remain unchanged. The UI can show `Auto`, `Current`, `Engineered`, or `Both`:

- `Auto` emphasizes an available Engineered profile, otherwise the available
  current light, and finally a clearly simulated Engineered preview if both are
  offline.
- `Current` shows the existing Home Assistant light at the shared mount.
- `Engineered` shows the spotlight/gimbal/radial profile. When hardware is
  unavailable its beam is dashed and labeled simulated; no command is sent.
- `Both` compares both identities at the same coordinates without duplicating
  geometry.

Availability controls presentation, not physical-installation truth. Both
profile statuses remain visible, and simulation is never labeled live.

The backend accepts these optional schema-v1 fields only after its Home
Assistant custom-component update is separately deployed. Updating this
repository does not make that backend validation active on a running Home
Assistant installation.

## Tracker and apartment geometry

Tracker `/model` and `/ws/tracks` remain read-only inputs. Apartment scan,
mesh, splat, point-cloud, and `collision.glb` assets remain display and spatial
reference data. The collision mesh is loaded as a hidden picking and beam
proxy independently of the selected display mode. Its full SHA-256 is recorded
with visualization calibration so a geometry change invalidates that
calibration.

## Gimbal bench

The bench remains loopback-only at `http://127.0.0.1:8765` and is never
proxied through the Home tailnet gateway.

- `GET /healthz` is a liveness check.
- `GET /api/state` is read-only telemetry.
- `POST /api/group/aim` accepts exactly
  `{target_x_m,target_y_m,post_dwell_ms}`.
- Movement requires foreground, same-origin `X-Bench-Owner` and `X-Bench-UI`
  capabilities from the bench console.

Apartment may poll read-only telemetry only from the Tauri desktop runtime on
the same machine. A regular browser must make no loopback request and must show
the loopback-only blocker. A telemetry observation is usable for live world
rendering only when the fixture/device identity and Product profile match, both
axes report `source: "motor"`, identity and session data are complete, and the
individual samples are younger than 300 ms. Re-reading an old snapshot never
makes it fresh.

The current bench state exposes the stable board binding as
`usb.board_vid_pid/usb.board_serial`, the Product digest as
`product_aim.profile_sha256`, the capture boundary as `session_epoch`, and the
monotonic snapshot counter as `state_seq`.

The bench Product endpoint uses a commissioned two-dimensional target plane.
Apartment currently has no authoritative Apartment-world-to-Product-plane
transform, no foreground owner-authority delegation, and Product adoption is
false. Consequently Apartment exposes no gimbal write transport and no SAFE,
STOP, jog, slew, or movement call. It may display the endpoint contract and a
disabled command preview, but must not fabricate a live request body.

## Calibration and command boundary

Fixture tape calibration establishes the practical fixture-bottom aiming
origin. Visualization calibration converts raw encoder observations into an
ideal two-axis mechanical visualization with positive tilt down. It cannot
correct mounting roll, a nonvertical pan axis, or mechanical nonorthogonality.
One known mesh corner can establish a `calibrated` draft; `verified` requires
multiple known destinations. Live capture remains disabled until the authority
handoff exists.

Apartment visualization output is a three-dimensional destination plus an
estimated mechanical pan/tilt solve. An authoritative Product Aim request is a
separate output and is available only when a qualified target-plane descriptor
binds the current Apartment frame and Product profile digest. Simulation may
use its synthetic descriptor, clearly labeled as simulation. Selection and
preview never move hardware.

## Optical honesty

Configured full FWHM defines the rendered half-maximum contour. It is not a
claim about the fixture's full photometric intensity distribution. The soft
falloff and on-screen color temperature are qualitative. Current measured
beams, previews, stale observations, off fixtures, obstructions, and inferred
target intersections must remain visually and textually distinct.

## Evaluation layout and interaction hierarchy

Simulation may clone the last successfully saved Apartment snapshot into
memory so fixture, target, and zone locations match the room being evaluated.
If the browser has no authoritative last-good cache, the local runtime may
provide an explicitly exported recovery snapshot. The source label includes
the snapshot revision, and simulation still cannot call Home Assistant or the
Apartment persistence endpoint. Recovery exports remain local runtime data and
are not committed as application fixtures or tests.

The Lighting inspector prioritizes everyday operations in three sections:
Light, Radials, and Setup. Light exposes power, brightness, color temperature,
and destinations before diagnostic detail. Radials presents all six zones as
a single compact control surface, with one selected-zone editor. Mapping,
identity, and calibration remain available under Setup rather than competing
with daily controls.

Every simulated engineered fixture keeps a quiet beam visible while Lighting
is open. The selected fixture gains emphasis and an optical-cyan destination
preview; other beams remain deliberately subdued. On fixtures use a
Kelvin-tinted qualitative volume and off fixtures retain their last aim as a
dashed cone. The Mirror wash preview shows the narrow upward path to a convex
mirror and a broad approximate downward wash. None of these visualizations is
a photometric claim or a physical movement command.

For a named surface or exact mesh face, the preview cone is terminated by a
boundary constructed in that destination plane. Its half-maximum contour
therefore follows the table, island, art, or selected mesh face instead of
showing a circular cap perpendicular to the beam axis. In simulation, **Set
simulated aim** deliberately promotes the cyan preview to the Kelvin-colored
current aim and retains it in memory. Confirmation animates one short
Kelvin-colored sweep from the observed direction to the destination; reduced
motion changes it to an instant state transition. Clicking an engineered
fixture opens this Lighting workflow directly rather than the generic device
card. Live mode keeps the same action disabled until movement authority and an
authoritative target-plane binding exist.

The simulation initializes each fixture's encoder pose from the same named
destination used for its current beam. Merely choosing a different named or
mesh destination changes only the cyan preview; the Kelvin-colored current aim
remains fixed until **Set simulated aim** is confirmed and its transition
completes.

Named target geometry remains available for picking, but labels and outlines
stay quiet until hover or selection so the light field remains the primary
visual layer when a saved model contains many targets.
