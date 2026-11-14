# Apartment unified design: implementation handoff

## The room view in the prototype is a placeholder
The prototype draws its own stand-in room: authored boxes for walls and furniture, a sampled point cloud and simple fixture orbs. Don't port it. Production keeps the current Apartment renderer in `app/src/home-3d/` as it is: the streamed point cloud and its shader, the splat (posed by `frame.json`), the mesh, wall-fade, orbit and camera behavior, and asset loading. Build the new UI, features and interactions on top of it.
- Map the toolbar's Cloud, Photo and Mesh to the existing point cloud, splat and mesh modes. Lit is new: it relights the existing scan mesh, and the cloud and splat stay the unlit "how it looks" layers.
- In-room overlays render over the existing scene: fixture labels, reticle, Zones | Spot pill, Set aim bar, radial composer, named-aim handles, camera and sensor frustums, body volume.
- Every geometric query the prototype makes against its boxes runs against the real scan mesh through the existing mesh picking (used today for target dragging). That covers aim hits, occlusion, named-aim surfaces, zone facing, coverage and body shadowing.
- Where the prototype's look differs from the current renderer, the current renderer wins.

Design only. Production source is unchanged. Renderer, data and command boundaries stay where they are; nothing here adds a backend contract.

## Shared component map

| Component | Responsibility | Replaces or absorbs |
|---|---|---|
| `ApartmentShell` | Grid, size container, Escape ladder, per-home state store | Layout code in `home-apartment.jsx` |
| `TopBar` | `HomeSwitcher`, `StatusSummary` + `StatusPopover`, Setup toggle, Close | `source`/`ha`/`save`/`tracker` chips, `aim light`, `edit`, `close · esc` |
| `RoomToolbar` | Scene source, Cloud/Photo/Mesh, detents, Fit, `LayersMenu`, Help | Bottom HUD, view dots, hint line |
| `CanvasLabels` | Fixture, named-aim, body and camera labels (anchored DOM) | `AptDeviceLabel`, `AptPersonLabel`, `AptRoomChip` |
| `AimReticle` + `DragChip` | The only aim drag affordance, with live surface feedback | Destination list as the primary path (kept as Named aims) |
| `Inspector` | `RoomInspector` · `LightInspector` (Light · Aim · Setup) · `TargetInspector` · `SetupInspector` | `AptControlCard`, `HomeApartmentAim` panel, Victoria fixture controls, execution panels |
| `ChannelCard`, `RadialRing` | Spot, Down, Radial (six zones, all-zones and per-zone level) | Radials tab |
| `CommandLadder` | Intent, four steps, exact output, provenance, copy | Product Aim preview block, `home-fixture-execution-panel` |
| `PerceptionDock` | Header and states, `FrameView` + `FrameOverlay`, `Trace`, summaries, details, Lab handoff | Victoria right-side perception panel, live-feed overlay |
| `SetupRail` | Devices and sockets, Named aims, Zones, Fixture position, Fixture links, Cameras | Edit toolbar, top-bar `calibrate` |
| `Badge` | The status vocabulary (DIRECTION.md) | Ad hoc coloured text |

## Capability-driven variants
A per-site `capabilities` object drives every variant. Components never branch on the site id.

| Capability | Values | Variant |
|---|---|---|
| `engineered` | `none` · `simulated` · `modeled` · `commissioned` | Aim tab explains / "Set simulated aim" + blocked Product Aim request / "Set modeled aim" + no command path / Apply with live Accepted and Reported steps |
| `perception` | `none` · `detection` · `exact-frame` | No dock content / snapshot + boxes, trace muted after Seen / frame + pose + JEV |
| `body` | `false` · `true` | Body layers disabled with reason / enabled when a session exists |
| `lights` | `ha` · `none` | HA controls with offline handling / modeled channel preview |
| `assets` | per mode: `ok` · `loading` · `unavailable` | Toolbar segment states and notices |
| `bench` | `loopback` · `none` | Status row and command blockers |

## Source files likely affected
Verified on `main`:
- `app/src/home-apartment.jsx`: shell, top bar, bottom HUD, live overlay, cards layer
- `app/src/home-apartment-cards.jsx`: retire `AptControlCard`; labels move to `CanvasLabels`
- `app/src/home-apartment-aim.jsx`: split into Light, Aim (proposal, named aims, command) and Setup sections
- `app/src/home-apartment-edit.jsx`: toolbar becomes `SetupRail`; inspector moves into the column
- `app/src/home-apartment-calibrate.jsx`: entry from Setup › Cameras
- `app/src/home-tokens.css`: add the `--apt-*` tokens (dark and light)
- `app/src/home-3d/markers.js`: fixture glyphs, pools, proposal rings, reticle anchor
- `app/src/home-3d/picking.js`: visible-surface hits, opening-aware occlusion, tilt check
- `app/src/home-overlay.js`: register drag, tap-to-aim, camera view and sheet as layers

Named in the brief, to verify in the local checkout:
- `home-site-registry.js` and `home-apartment-site-session.js`: the source of `capabilities` and per-home state
- `home-apartment-data.js`: `targets[]` state and geometry hash
- `home-equipment-registration.js`
- `home-3d/equipment-virtual-fixtures.mjs`, `equipment-spotlight-visual.mjs`, `committed-room-fixtures.mjs`, `live-fixture-visual.mjs`, `live-spotlight-visual.mjs`: cylinder glyph, strips and pools
- `home-apartment-den-perception.jsx`: retire in favour of `PerceptionDock`
- `home-apartment-perception-source.js`, `-player.js`, `-view.js`: dock data
- `home-3d/apartment-perception-layer.mjs`: body layers
- `home-lighting-target-panel.jsx`, `home-lighting-live-panel.jsx`, `home-lighting-dynamic-panel.jsx`, `home-live-room-controls.jsx`, `home-fixture-execution-panel.jsx`: merge into Inspector and CommandLadder
- `home-perception-lab.jsx`: deep-link entry

## Data
**Already available (source-verified)**
- `apartment_model` schema v1: `zones`, `devices`, `targets[]` (id, name, category, shape, pos, normal, up, size_m, room_id, source), `fixture_calibration` statuses, `fixture_kind: "engineered_gimbal_v1"`, `spotlight.optic_profile.configured_fwhm_deg`, `radial_zones.zones[]` + `orientation_calibration`, `gimbal.limits`, `visualization_calibration`, `revision`. Model source labels: Simulation, Local draft, Seed model, Live Home Assistant model, Tracker / live spatial data, Cached live model
- HA states (`state`, `brightness`, `color_temp_kelvin`) and `call_service`
- Bench telemetry: `usb.board_vid_pid`, `usb.board_serial`, `product_aim.profile_sha256`, `session_epoch`, `state_seq`, per-axis `source`. Product Aim contract `{target_x_m, target_y_m, post_dwell_ms}` with its blockers
- `manifest.json` sha8/bytes, `frame.json` registration, camera intrinsics and extrinsics (`K`, `dist`, `image_size`, `rms_px`)

**Check in the local checkout:** Victoria capability flags, socket ids and channel-to-entity mapping (including Down), the perception view contract (frame id, captured/analyzed times, keypoint format, inferred flags, uncertainty, registration), the JEV payload (distribution, abstention, window, reasons, label set) and the direct-aim command output.

**Genuinely missing, needed by this design**
- A per-site `capabilities` object (above)
- Per-observation `analyzed_at` and held/stale thresholds, so ages can be stated
- A proposal record linking perception to lighting: `{fixture_id, target_id | point, origin: "perception", observation_ids[], reason}`
- An execution receipt per command: `{accepted_at, reported_state, source}`
- Target `state` (derived: saving/saved/local/conflict) and the geometry hash at save time, to flag `changed`
- Quick presets for modeled fixtures. Echo Park can list its Home Assistant `scene.*` and `script.*` entities; Victoria has none for the Den sockets yet
- Per-fixture `radial_zone1_heading_deg`, measured at commissioning (blink zone 1, then click where it lands)
- IES files per channel (spot per zoom step, down, one radial zone) and lumen output against brightness
- A body-shape record per person (parametric shape only, no imagery) from the perception service
- Per-fixture `display_name` and `label` overrides stored with the Apartment model (the prototype keeps them in localStorage). Optional: push names to Home Assistant's entity registry
- Chat in the column reuses today's pieces: feed renderer (`home-events.jsx`), action cards (`home-control.jsx`), the slash dispatcher (`home-app.jsx`), voice (`home-s2s.jsx`), /look (`home-look.jsx`), /ask (`home-external.jsx`), /explain (`home-explain.jsx`), /world-state (`home-worldstate.jsx`), preference review and Travel Mode locks. New: entity chips bound to the scene, the selection sent as context, and aim proposals with origin `chat`
- Removing Close: Apartment becomes the default route; anything reached through the old menu (Lab, metrics, people) needs a home in the top bar or the slash menu
- Composer commands: send zone levels at most every 100 ms during a drag, with the driver's transition time covering the gap so the real light fades the way the view does
- A device record for mmWave presence sensors: `type: "presence_sensor"`, mount, `yaw`/`pitch`, `hfov_deg`, `vfov_deg`, `range_m`, and its Home Assistant entities once paired
- A hand-placed camera pose kept separate from calibrated `extrinsics`, so placing never overwrites calibration
- An agreed automation reference for a named aim (prototype shows `aim {fixture_id} → {target_id}` as a proposal)

## Removal and consolidation
- Retire the anchored control card. One inspector per selection.
- Retire the `aim light` mode and its button. Aim is a tab plus the reticle.
- Move debug statistics behind Details.
- Remove the live-feed full-viewport overlay. The camera view is a room pose with Return to room, and the media lives in the dock.
- Remove Victoria's bottom-left controls and its separately styled perception panel.
- Fold execution and lighting panels into the Command ladder.
- Move `AIM_*` colour literals into tokens. Remove the forced `data-theme="dark"`: the canvas stays dark, and the chrome follows the theme.

## Migration stages
1. **Tokens and shell.** Add `--apt-*`; new top bar, status summary and room toolbar; contrast fixes. Behaviour is unchanged.
2. **One inspector.** Light, Aim and Setup from the card plus the Lighting inspector; selection precedence; Escape ladder.
3. **Victoria in the shell.** Channels (Spot/Down/Radial) and the dock fed by the site session; remove the duplicate panels.
4. **Direct aim.** Reticle drag, tap and keys, snapping, validity reasons, undo; named-aim save states (DIRECT-AIM-AUTHORING-PLAN).
5. **Perception linkage.** Trace, body layers bound to the frame, Lab deep link.
6. **Setup rail.** Same workflows, new placement; calibrate moves.
7. **Responsive sheet** with container queries.
8. **Occlusion and shadows** once FURNITURE-BODY-LIGHTING-OCCLUSION lands (layers already reserved).

## Acceptance examples
- **Home isolation:** with an Echo local draft, switch to Victoria and back. Echo's draft, selection and proposals remain. Victoria shows no Echo entity, HA status, camera, saved placement or connection.
- **Direct selection:** click S4's label, its cylinder, or its list row; each opens the same Light inspector. N/P cycles lights; Escape clears the selection and focus returns.
- **Drag aim:** drag S4's reticle onto the painting. The chip reads "Snaps to “Painting” · 1.85 m · 59° tilt". Release shows the Proposed callout while the warm beam stays on the desk. "Set aim" (the Aim header reads Modeled) sweeps the beam and toasts "no hardware command sent".
- **Unreachable:** Echo Front left → Living room art reads "Out of reach · 80° from straight down, limit 75°", and Set is unavailable.
- **Escape order:** during a drag, Escape cancels the drag only. Next, it discards the proposal (⌘Z restores it), then clears the selection, then does nothing.
- **Frame honesty:** in the stale scenario the frame is greyed, the overlay is dashed at reduced opacity, and the dock says "Not used for lighting". Choosing Live never shows the analyzed skeleton.
- **JEV abstains:** Interpreted reads "Abstained", Proposed reads "Nothing", and the reason is shown. Detector, pose and JEV values are labeled separately in Details.
- **No session:** an empty state, no body layer, and no start button.
- **HA offline (Echo):** controls are disabled, "Last reported 20:58" is shown, and nothing is queued.
- **Photo missing:** loading, then "Photo isn't available here" with Retry and Back to Cloud; still Echo Park.
- **Map a named aim:** Setup › Named aims › New, then click the Den desk top. The draft fits the desk (1.70 × 0.60 m), suggests the name “Desk” and type Table, and Reach lists which sockets can light it. Save adds it to every light's named aims.
- **Phone:** the sheet has a light/camera switch, the toolbar sits above the sheet, and every control is at least 44 px.

## Prototype notes
`prototype/index.html` runs from a static server at the repository root. It tries `app/data/apartment/points.sim.ply` for Echo; when that is reachable, the scene chip reads "Scan".

To use Victoria's real material, add its scan path to `VICTORIA.scene.ply` in `data-sites.js`, replace `cameras[0].media` with a recorded Den frame, and paste the pose keypoints into `perception.body`.

Review states via URL: `?home=victoria&sel=vic-s4&tab=aim`, `&vic=current|held|stale|abstain|replay|none`, `&echo=connected|draft|loading|failed`, `&theme=light`, `&dock=collapsed|expanded|hidden`, `&detail=detailed`, `&vp=390x844` to simulate a viewport.
