# Apartment Targets and Fixture Calibration

The Apartment model uses the shared `z_up_metric_floor0` frame. Named targets
and ceiling-light tape records live beside `zones` and `devices` in
`apartment_model` schema version 1.

## Named targets

`targets[]` stores semantic 3D destinations for light aiming and spatial
reasoning. A target is either an exact point or a bounded surface:

```json
{
  "id": "target-dining-table",
  "name": "dining table",
  "category": "table",
  "shape": "surface",
  "pos": [5.0, 1.5, 0.75],
  "normal": [0, 0, 1],
  "up": [0, 1, 0],
  "size_m": [1.8479, 0.9],
  "room_id": "dining_room"
}
```

- `pos` is the surface center or exact point in meters.
- `normal` points away from the aim surface.
- `up` establishes the surface's in-plane orientation.
- `size_m` is `[width, height_or_depth]` and is omitted for point targets.
- `category` is `table`, `island`, `art`, or `custom`; `shape` remains the
  geometry contract, so custom targets can be either points or surfaces.

Table/island presets stay horizontal. Art placement requires a vertical mesh
face. Custom surfaces retain the picked mesh face normal. Seed dimensions that
come from a tape plus the scan are marked `source: "tape_and_scan"`; proposed
placements remain explicitly lower-confidence.

## Ceiling fixture position measurements

Every ceiling light receives `aiming_origin: "fixture_bottom"` and a
`fixture_calibration` worksheet. The fixture bottom is the practical origin
for later aiming calculations; the ceiling mount is only a construction
reference.

The UI calls this workflow **Fixture position**. Two perpendicular wall tapes
set the floor-plane location, floor-to-ceiling height plus fixture drop set the
fixture-bottom aiming height, and an optional floor-to-bottom tape verifies the
derived result. It changes Apartment geometry only; it does not control a Home
Assistant light or move a gimbal. Co-located current and Engineered profiles
reuse these measurements, with a profile-specific vertical offset required only
when their physical fixture bottoms differ.

```json
{
  "aiming_origin": "fixture_bottom",
  "fixture_calibration": {
    "status": "proposed",
    "wall_distances": [
      { "wall": "west", "distance_m": null },
      { "wall": "south", "distance_m": null }
    ],
    "floor_to_ceiling_m": null,
    "ceiling_to_fixture_bottom_m": null,
    "floor_to_bottom_verification_m": null
  }
}
```

The two wall references must be perpendicular: one east/west wall and one
north/south wall. Their tape distances solve `pos[0]` and `pos[1]` against the
owning zone bounds. Floor-to-ceiling minus ceiling-to-bottom solves `pos[2]`.
The optional floor-to-bottom value verifies that derived height and records a
signed residual; it does not replace the primary measurement pair.

Statuses are:

- `proposed`: the fixture-bottom position comes from the seed/model and no tape
  value has been entered.
- `measured`: at least one tape value exists, but the perpendicular wall pair
  or one of the primary vertical measurements is incomplete.
- `calibrated`: both wall tapes plus the two primary vertical tapes exist.
- `verified`: the calibrated record also has a floor-to-bottom check.

Apartment edit mode accepts bare inches, feet/inches, centimeters, millimeters,
or meters. Values are persisted in meters. Survey-orange lines and labels show
only entered measurements, so proposed seed coordinates are never presented as
tape observations.

## Editing workflow

Targets can be selected directly in the 3D view. Dragging moves them across an
allowed surface; the inspector also provides exact XYZ fields, five-centimeter
nudge controls, in-plane rotation, and surface dimensions. Tables and islands
remain horizontal, art remains on a vertical face, and a custom surface keeps
the plane selected from the collision mesh.

`Add target` is an explicit two-step task: choose table, island, art, custom
point, or custom surface; then click the real surface. While placing, a left
mouse drag orbits the room and a click selects the surface, so revealing a wall
does not accidentally place the art. The bottom-left and bottom-right orbit
buttons rotate by one camera detent. The editor enters directly at the
35-degree isometric camera detent and synchronizes its fit on mount, rather than
waiting for the browser to resize. `Finish placement` accepts the placement and
`Cancel` removes a newly created draft target.

The `Zones` tool is a direct floor-boundary editor. Select a room from the left
rail or by clicking its shaded polygon, drag a round corner handle to reshape
the boundary, or drag inside the shade to translate the whole polygon. The
inspector provides exact X/Y values for every corner and can add a midpoint on
the longest edge or remove a selected corner while preserving the three-corner
minimum. `+ new zone` starts a separate click-corners workflow with a visible
3D draft and explicit `Finish zone` and `Cancel` actions. Any boundary edit
recomputes room membership for devices and named targets before persistence.

## Model sources and persistence

The Apartment view labels every loaded document as one of:

- `Simulation`
- `Local draft`
- `Seed model`
- `Live Home Assistant model`
- `Tracker / live spatial data`
- `Cached live model`

Simulation uses isolated deterministic fixtures and cannot write either the
local recovery draft or the authoritative model. A normal save posts the whole
schema-v1 document—including targets, devices, Home Assistant entity links, and
fixture calibration—to
`/api/extended_openai_conversation/apartment_model`. Home Assistant applies a
revision compare-and-swap and stores the document at
`/config/apartment_model.json`. Browser local storage is only an offline
recovery draft/cache, never an alternative authority.

An explicit offline or failed save leaves a recovery draft. That draft loads
before tracker, cached, or live data until an authoritative save succeeds, so a
reload or later Home Assistant connection cannot make unsaved work disappear.
The UI continues to identify it as `Local draft`; saving it while connected
first performs a read-only fetch of the authoritative document and shows
per-collection draft/live counts plus added, changed, and removed records. The
user must then choose `Publish reviewed draft`. Publishing is blocked if the
draft and live revisions differ. A server-side 409 leaves the displayed local
draft intact and loads the returned server document only as a comparison copy.
The recovery draft is cleared only after an authoritative save succeeds.

The spatial tracker exposes `/model` and `/ws/tracks`. It caches the same model
for spatial consumers but does not become a second writer. Scan assets continue
to come from `app/data/apartment` through Tauri, the Home web gateway, or the
local asset server.

For a local live-browser session, run the existing Home web gateway and provide
reachable `HOME_WEB_HA_TARGET` and `HOME_WEB_TRACKER_TARGET` values. Open the
gateway origin without `?simulation=1`, then authenticate in Home with a Home
Assistant long-lived access token. The token authenticates both the WebSocket
registry/state calls and the authoritative Apartment model endpoint; the
browser should not connect cross-origin to Home Assistant directly.

## Fixture/entity reconciliation

The fixture survey separates mapped ceiling fixtures, unplaced Home Assistant
`light.*` entities, and mapped lamps/other lights. An unplaced light must be
placed deliberately as either a ceiling fixture or another light. Existing
fixtures can be relinked without changing their position or tape worksheet;
unlinking requires confirmation, and duplicate entity links are blocked.

Use **Fixture links** for live reconciliation. This workflow locks fixture,
target, zone, and tape geometry; selecting a marker cannot drag it, unmatched
Home Assistant lights stay visibly unplaced, and the link-only model helper
compares spatial geometry before accepting the identity update. Use the
separate placement and tape tools only when geometry is intentionally being
changed.

The ten seeded ceiling fixtures reconcile first by stable fixture ID and exact
Home Assistant entity ID. A missing fixture remains an unresolved seed record
for manual review, with its calibrated/proposed position preserved. Display
name similarity is only a suggestion and never silently creates a duplicate.
No physical light or gimbal command is part of this workflow.
