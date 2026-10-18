# Apartment unified design: inventory

Grounded in the Home repository at GitHub `Engineered-Lighting/home@main` (41a6ce7, 2026-09-30). The owner attached the local checkout (`home`) for this session, but its file tools were not reachable. Everything the brief names that lives only in the local checkout is listed under "Not inspected", and the prototype labels those parts as authored.

## What was inspected

**Read on `main`**
- `AGENTS.md`, `CLAUDE.md`, `changes/README.md` (change-note format)
- `app/src/home-tokens.css`: dark and light `--hg-*` tokens, Geist/Geist Mono, focus ring, slider, reduced motion, Escape-hint dimming
- `app/src/home-apartment.jsx`: the render tree (top bar, loading/error, zoom HUD, bottom HUD, live camera overlay, labels/cards layer, Aim and Edit mounts, toast)
- `app/src/home-apartment-cards.jsx`: hover label, person label, room chip, anchored control card
- `app/src/home-apartment-aim.jsx`: the Lighting inspector (profile view, Light/Radials/Setup tabs, destinations, Set simulated aim, Product Aim request preview, colour constants)
- `app/src/home-apartment-edit.jsx`: edit toolbar and tools (select, + smart device, + target, fixture links, fixture position, zones)
- `app/src/home-apartment-sim.js`: the simulated engineered fixture and optic profile
- `app/src/home-overlay.js`: the single Escape/focus layer stack
- `app/src/home-3d/assets.js`, `home-3d/pointcloud.js`: asset resolution and the `homeapt-points v1` PLY format
- `app/src/assets/apartment/seed-model.json`, `frame.json`, `manifest.json`
- `docs/APARTMENT-LIGHT-AIMING.md`, `docs/APARTMENT-TARGETS-AND-FIXTURES.md`, `docs/APARTMENT-CAMERA-MESH-CALIBRATION.md`
- `changes/unreleased/apartment-light-aiming.md`, `apartment-target-labels-and-fixture-calibration.md`

**Copied for reference but not reviewed in depth:** `home-apartment-aiming.js`, `home-apartment-calibrate.jsx`, `home-3d/markers.js`, `home-3d/modes.js`.

**Runtime views:** none. No running Home app was reachable from this session, so no owner-local screenshots were captured. The screenshots already in this design project show Home's chat surface (May–June 2026), not Apartment.

## Not inspected (local checkout only)
Victoria and perception sources: `home-site-registry.js`, `home-apartment-site-session.js`, `home-equipment-registration.js`, `home-3d/equipment-virtual-fixtures.mjs`, `equipment-spotlight-visual.mjs`, `committed-room-fixtures.mjs`, `live-fixture-visual.mjs`, `live-spotlight-visual.mjs`, `home-apartment-den-perception.jsx`, `home-apartment-perception-source.js`, `-player.js`, `-view.js`, `home-3d/apartment-perception-layer.mjs`.
Lighting surfaces: `home-lighting-target-panel.jsx`, `home-lighting-live-panel.jsx`, `home-lighting-dynamic-panel.jsx`, `home-live-room-controls.jsx`, `home-fixture-execution-panel.jsx`.
Perception Lab: `home-perception-lab.jsx`, `.css`, `docs/design/perception-lab-v8/`.
Plans and contracts: `docs/design/virtual-spotlight-rendering.md`, `docs/PERCEPTION-LAB-CONTEXT-PIPELINE-DESIGN-BRIEF.md`, `docs/PERCEPTION-LAB-PIPELINE-DATA-FOR-DESIGN.md`, the `first-live-den-replan-2026-10-15` set (PLAN, STATUS, COMPLETE-BACKLOG, FULL-FIXTURE-PLAN, DIRECT-AIM-AUTHORING-PLAN, HUMAN-BODY-VOLUME-AMENDMENT, FURNITURE-BODY-LIGHTING-OCCLUSION), `handoffs/victoria-apartment-perception-view-contract-20261017.md`, and `artifacts/apartment-design-handoff-20261017/`.

What I know about Victoria comes from the brief: a registered Den scan, four mapped sockets, virtual fixtures (white cylinders with tubular diffuser film, six outward radial strip zones, one downward zone, one hidden robotic spotlight), exact-frame perception viewing, and the panel divergence it describes.

## Echo Park today (source-verified)

| Area | Current behaviour |
|---|---|
| Scene | Cloud (points), Photo (splat), Mesh. Collision mesh is always loaded as a hidden pick/beam proxy. Assets are in `app/data/apartment` (mesh 57 MB, collision 1.1 MB, points 15 MB / 1.15 M, sim points 2.3 MB / 180 k), outside git |
| Frame | `z_up_metric_floor0`, extents 14.2 × 8.2 × 2.8 m, floor RMS 9.5 mm |
| Navigation | Drag orbit, wheel zoom, eight view-angle dots, +/− buttons, arrows, `h` home, `n`/`p` select device, double-click toggles lights |
| Top bar | "apartment / spatial command center", then chips: `sim`, `source · {label} · rev N`, `ha · {state}`, `save · {state}`, `tracker · {state}`; buttons `aim light`, `edit`, `close · esc` |
| Ordinary view | Debug line (fps, points, dpr, draw calls, GPU) bottom-left; hint line 8.5 px in `--hg-fg-5` |
| Selecting a light | Opens a 260 px card anchored to the device: name, state, entity id, on/off, brightness |
| Engineered light | `aim light` mode opens the right-side Lighting inspector (338 px, radius 16): Auto/Current/Engineered/Both, Light/Radials/Setup, destinations (Mirror wash, named targets, exact mesh hit), Set simulated aim, Product Aim request preview with blockers |
| Camera | A camera card offers "fly to view"; the live feed then covers the whole viewport with a `live · {cam} · mjpeg` badge; `← back` and `calibrate` replace the top-bar actions |
| Edit | Separate toolbar and inspector; tape-measured Fixture position with proposed/measured/calibrated/verified; geometry-locked Fixture links; zones; targets with a two-step add |
| Presence | Person label ("someone · activity" when activity confidence > 0.6) or a room chip |
| Contracts | HA through `/proxy/ha`; revisioned `apartment_model` with local-draft recovery and Publish reviewed draft; bench loopback-only (`POST /api/group/aim {target_x_m, target_y_m, post_dwell_ms}`), telemetry usable only when < 300 ms and motor-sourced; no Apartment→Product-plane transform, so no gimbal write |

## Common ground and divergence

**Both homes need:** home selection; the same scene modes; orbit and camera views; fixture selection; brightness, warmth and channels; aiming at named or picked surfaces; named targets; command honesty; perception results; setup and measurement.

**Divergence on Echo (from source):**
- Two control surfaces for one light: the anchored card and the Lighting inspector, with different widths, radii, type and copy.
- Aiming is a mode (`aim light`) rather than a property of the selected light.
- Status chips are 8.5 px mono, several informative ones use `--hg-fg-5` (decorative/disabled only), and "connected" never says what is connected.
- Debug statistics are in the ordinary view.
- The live camera replaces the room instead of sitting beside it.
- Colour constants (`#45dfff`, `#ffddb0`, `#ffb45f`, `#91e6bd`, `#ff625f`) live outside the token file, so the light theme can't adapt them. The Apartment view also forces `data-theme="dark"`.

**Divergence on Victoria (from the brief, to verify):** separate bottom-left fixture controls, an independently styled right-side perception panel, and lighting/execution overlays that compete with Echo's inspector.

## Current and planned capability

| Capability | Echo Park | Victoria |
|---|---|---|
| Light control | HA entities (brightness, warmth) | No entities mapped to Den sockets (assumed; verify) |
| Engineered spotlight | Simulated preview at an existing mount; no gimbal write | Four modeled fixtures at mapped sockets (in source per brief); not commissioned |
| Direct drag aim, named presets | Not in source (destinations are picked from a list or the mesh) | Planned (DIRECT-AIM-AUTHORING-PLAN) |
| Perception | Snapshots, Frigate detection, tracker room/position | Exact-frame video/pose viewing (in source per brief) |
| Body in room | Person label only | Planned: body volume, supplied vs inferred pose |
| Furniture/body shadowing | None | Planned |
| Continuous registered live 3D, native motion, commissioning | Not delivered | Check current receipts; continuous room-space body tracking is not delivered (owner) |

## Retain, merge, replace

| Today | In the unified design |
|---|---|
| Cloud / Photo / Mesh | **Retain** in one place: the room toolbar, bottom centre |
| Eight view dots, +/−, arrows, `h`, `n`/`p` | **Retain**, compacted to ‹ n/8 › and Fit; zoom stays on the right edge |
| Double-click toggles lights | **Replace** with the explicit switch in the inspector; double-click is easy to trigger while orbiting |
| Top-bar chips (`source`, `ha`, `save`, `tracker`) | **Merge** into one status summary that names what is connected, with a popover |
| Debug statistics | **Move** behind Details/diagnostics |
| Anchored control card + Lighting inspector | **Merge** into one Light inspector in the right column (Light · Aim · Setup), varied by capability |
| `aim light` mode | **Replace**: selecting an aimable light shows its reticle; the Aim tab holds named aims and the command |
| Auto/Current/Engineered/Both | **Retain** for co-located mounts (Echo), under the Light inspector |
| Set simulated aim, Product Aim preview | **Retain**: "Set simulated/modeled aim" and Command › Exact output |
| Live feed overlay over the room | **Replace** with the camera dock and "From camera" view with Return to room |
| `calibrate` in the top bar | **Move** to Setup › Cameras |
| Edit toolbar | **Merge** into the Setup rail with the same workflows |
| Person label / room chip | **Replace**: Echo presence lives in the dock; Victoria's body is a layer tied to the analyzed frame |
| Victoria fixture controls (bottom left) | **Merge** into Light inspector channels (Spot, Down, Radial) |
| Victoria perception panel (right) | **Replace** with the camera dock under the inspector |
| Execution panels | **Merge** into Command's four-step ladder |
| Toasts | **Retain**, bottom centre |

## Source notes used in the design
- Tokens: dark `--hg-bg-0..3` #000/#0A0A0A/#111/#161616, `--hg-fg-0..5` #FFF/#EDEDED/#A1A1A1/#8F8F8F/#767676/#404040, ice #B8D8FF, blue #4D9CFF, warn #FFB224, crit #E15553; light "paper terminal" #EFE9DC… with ink #1A1812 and accent #1F4FA8.
- Echo seed model: living room 7.4–13.6 × 0.6–6.2 m (h 2.5), kitchen 3.0–7.4 × 2.6–6.5, dining 3.0–7.4 × 0.6–2.6; ten ceiling lights, three plugs, three cameras, four targets (art is `proposed`), all positions `proposed` with confidence 0.5.
- Optic in simulation: Carclo 10511 frosted narrow spot, 20° FWHM.
- Camera calibration record: video size, fx/fy/cx/cy, distortion, extrinsics, crop mode, `rms_px`, `updated_at`.
