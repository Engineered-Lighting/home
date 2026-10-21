# Apartment unified design: interaction and state specification

> The room rendering in the prototype is a stand-in. Production keeps the current Apartment renderer (point cloud, splat, mesh) and adds this spec's UI, overlays and behavior on top. See HANDOFF.md.

## Layout and responsive rules
The Apartment view is a size container (`container: apt / size`), so the same rules apply when it is embedded or full-screen.

| Width | Layout |
|---|---|
| ≥ 1440 | Top bar 52 px · canvas · column 360 px (540 px when the dock is expanded) |
| 1180–1439 (incl. 1366 × 768) | Column 340 px (500 expanded) |
| 900–1179 | Column 320 px (440 expanded); status summary drops its optional segments; legend hidden |
| 600–899 (tablet) | Column becomes a bottom sheet with two panes side by side: inspector and camera dock. Detents: peek 150 px, half 50%, full (top bar visible) |
| < 600 (phone) | Same sheet with one pane at a time: a segmented switch "{light} / Camera · JEV". Status collapses to a dot; the room toolbar rides above the sheet and hides at the full detent |

- **Fitting:** fit uses the visible canvas: the area left of the column, minus the toolbar inset on desktop, minus the sheet on narrow screens (projection centre shifted with `setViewOffset`). After the owner orbits or zooms, sheet changes no longer refit.
- **Dock height:** at most 58% of the column (48% when the column is ≤ 900 px tall); the dock body scrolls. Expanded gives the dock the remaining height, and the inspector keeps at least 160 px.
- **Scroll ownership:** the inspector and the dock body scroll independently (`overscroll-behavior: contain`). The canvas never scrolls; wheel and pinch zoom.
- **Safe areas:** the sheet pads `env(safe-area-inset-bottom)`; touch targets are at least 44 px under `pointer: coarse`.
- **Collisions:** the top-left legend, the top-centre hint bar, centre notices, bottom-centre toolbar and right-edge zoom never overlap each other at any width. On phone, zoom buttons are hidden (pinch).

## Selection precedence
1. An active sub-operation owns the pointer: reticle drag, tap-to-aim, Setup placement, measurement.
2. A fixture, its label, or its list row selects that fixture and opens the Light inspector. The tab is kept if the new light supports it, otherwise Light.
3. A named aim (label or surface) while an aimable light is selected **on the Aim tab** proposes that aim for the light. Otherwise it selects the named aim.
4. A click on empty canvas clears the selection. A drag on empty canvas orbits and never selects.
5. Hover shows a label and a soft ring only; it never changes the inspector.

## Gestures
| Gesture | Result |
|---|---|
| Drag empty space | Orbit (azimuth and elevation, elevation clamped 11°–83°) |
| Shift-drag or right-drag | Pan |
| Wheel or pinch | Zoom |
| Click or tap fixture/label | Select |
| Drag the reticle | Aim: raycast against visible surfaces (cut-away walls are skipped; openings pass through). The socket stays fixed. A chip by the cursor shows the surface, distance and tilt, or why it can't aim there |
| Release over a valid surface | Creates or updates the proposal; the current aim does not move |
| Near a named aim | Snaps: "Snaps to “Painting”" |
| Tap to aim (Aim tab) | One tap on a surface proposes. Escape or Cancel leaves |
| Arrow keys on the focused reticle | Nudge the proposal by 10 px (Shift 40 px) in screen space |

Starting a reticle drag, or nudging it with the keyboard, switches the inspector to **Aim** so the proposal and its Set aim / Discard are in view. On phone it also brings the light pane forward and raises the sheet from peek to half.

Invalid hits keep the cursor feedback and become an invalid proposal with a reason and a suggestion. "Set" is disabled.
- **No surface:** "No surface here".
- **Out of reach:** "Out of reach · 80° from straight down, limit 75°".
- **Blocked:** "Blocked by Desk chair", with a red marker at the blocking point.
- **Grazing:** allowed, with "the pool will be long and dim".

## Aim lifecycle
`current` (warm, from the model) → `proposed` (cyan; from drag, tap, keys, named aim or perception) → **Set aim** (the Aim header says Modeled or Simulated) → `current`, with a 650 ms Kelvin sweep (instant under reduced motion) and the toast "… no hardware command sent".

Undo and redo (⌘Z / ⇧⌘Z or the inspector buttons) step through snapshots of `{current, proposed}` per light, kept for 30 steps.

**Save as named aim** creates a `targets[]` record from the proposal (point, normal, surface, `source: "aimed"`) and moves through these states:
- `saving` → `saved`
- `local` when the model is a local draft (publish later)
- `conflict` when the name already exists (Replace existing / Keep both)
- `changed` when the scan or profile changed after saving (Preview it / Keep as is)

The automation reference is the target id. The fixture + target reference shape shown in the prototype is proposed.

## Quick controls
- **Room inspector › Quick:** All on, All off, and scene chips (Bright, Evening, Reading or Dinner, Glow) plus automations marked ▶ (Goodnight). Each applies at once and the toast offers Undo. In Home the chips list the home's scenes and scripts.
- **Per room (Echo Park):** a switch on each room heading turns that room on or off.
- **Per light:** All on and All off for every channel. The Radial card has Off · 25% · 50% · 100% for all six zones; turning radial on when every zone is at 0% starts it at 50%.
- The per-channel and per-zone controls stay below for nuanced changes.

## Mapping a named aim (Setup › Named aims)
1. **New** (also in the Room inspector) shows every mapped aim as a faint outline and turns the cursor into a crosshair. Hovering previews the surface under the cursor; furniture tops and the painting preview their full outline.
2. **Click a surface:** the draft drops there in cyan. Furniture tops and art fit their own size; walls and the floor start from the type's preset (table 1.2 × 0.75 m, island 1.2 × 0.65, art 0.9 × 0.65, custom 0.6 × 0.6). Dragging still orbits.
3. **Edit:** name (suggested from the surface, unique per home), type (Table, Island, Art, Custom; tables and islands must be flat, art must be vertical), shape (Area or Point), size, height (a slider, or the arrow handle above a flat surface), 5 cm nudges, 15° rotation on flat surfaces, Pick again. In the room the draft has handles: drag a corner to resize it (the opposite corner stays put), the center to move it within its plane, and on flat surfaces the arrow to raise or lower it. A dashed line drops to the floor to show its height.
4. **Reach** says which aimable lights can reach it and why the others can't. Preview from a light draws its beam.
5. **Save** adds it to `targets[]` (`saving → saved`, or `local` with a local draft). Clicking an aim in the room or the list opens it for editing. Delete keeps each light's current aim but drops the name.

Escape leaves placing (back to the editor if the aim already has a spot), then closes the editor.

## Placing cameras and sensors (Setup › Cameras and sensors)
1. **+ Camera** or **+ mmWave sensor**, then choose the mount: Wall (click the wall at its height), Ceiling (click the floor below it), or Shelf (click the top it sits on).
2. It drops facing into the room. **Point at a spot** (one click) aims it; Pan, Tilt, Lens/Field, Height and, for sensors, Range fine-tune it. The room draws its coverage: a solid frustum for cameras, a dashed one for sensors.
3. **Covers** lists the furniture inside its field and range. Line of sight is not checked yet.
4. Status stays honest: cameras read "Placed by hand · not calibrated" until calibrated on the desktop app; mmWave sensors read "Ordered · not connected" until paired with Home Assistant.

## Spot or zones (cylinders)
A selected cylinder has two in-room tools, switched by a small pill above it: **Zones** (drag around the light to set the radial zones) and **Spot** (drag the reticle to aim the spot). The pill follows the inspector: Zones goes with the Light tab and Spot with the Aim tab, so switching one switches the other. Keys: A for Spot, Z for Zones. While an aim is waiting to be set, the pill's Spot button shows a cyan dot.

After a drop, a bar beside the proposed spot offers **Set aim** and **Discard** (Enter and Esc), so an aim can be confirmed from the room with Chat open or the panel hidden. It names the target and notes when your body shadows it. When the spot is out of reach or blocked, it says so and offers only Discard.

## Radial zones in the room (composer)
With a cylinder selected on the Light tab, the radial output lies flat around it at fixture height as a soft polar light shape. How far it reaches in each zone's direction is that zone's brightness: the hub is off, the outer ring is 100%. It's drawn in the radial color temperature and fades outward, with a lit edge and a node at each lit zone. Six faint ticks on the outer ring mark the zones. There are no direction labels.
- **Press on the light** (or a lit lobe) and drag. The zone under the cursor follows its distance from the center: outward brightens, inward dims. Sweeping around paints each zone you pass; zones skipped by a fast sweep are interpolated.
- While dragging, the outer ring, hub and the active zone's edges appear faintly, and the active node turns white.
- **Pickup:** a press changes nothing until the cursor crosses that zone's current edge or enters another zone, so grabbing never makes a light jump.
- A readout by the cursor shows the zone and level ("Zone 2 · 64%"). Release ends the gesture. Orbiting works anywhere outside the hub and lit lobes.
- The inspector ring mirrors the shape in plan view, north up. **Toward walls** lights zones whose wall is within 2.2 m; **Into the room** lights the rest. The zone-1 heading is authored until commissioning measures it.

Radial light in the room fades like the fixture does. The shape, pools, orb glow and Lit lights ease toward each zone's set level (160 ms time constant, settled in about half a second), so entering a brighter or dimmer zone never jumps. The set level and the readout update at once; only the drawing eases. The inspector ring fades the same way.

Fixtures are abstract light points: a small orb with a glow tinted by the channels that are on. LED strips and housings aren't drawn.

## Lit view
Toolbar › Lit renders the mesh with simulated light. Spot and Down cast soft shadows from walls, furniture and the body; each radial zone is its own unshadowed lobe. Intensities come from assumed lumen ratings (spot 450 lm, down 350, each radial zone 120, ceiling 800, plug 400), converted to candela by beam angle, under a fixed night exposure. One bounce of indirect light is ray traced against the scan mesh, so lighting a wall lights the room, and every bounce path is shadowed, so no bounce passes through a wall. Aiming a spot re-bakes only that spot's bounce, once the Set sweep lands; moving a slider only re-weights it. Spot shadows are crisp (2048) and Down shadows soft (1024), each cached until its light moves. Lux at named aims is shown as modeled. Materials are assumed. LIGHT-AND-BODY-RESEARCH.md has the production path.

## Body volume
The pose is wrapped in capsules (torso and limbs) and a head sphere: faint in Cloud and Mesh, solid and shadow-casting in Lit. The aim solver tests each beam against it. When the body blocks a proposal, the drag chip names the part ("Your head shadows it"), an orange dot marks where the beam hits, and the Aim tab offers another light that reaches the spot clear of the body.

## Names (Setup › Names)
Every light, grouped by room, with two fields: **Name** (lists, inspector, messages) and **Label** (the short tag over the light in the room, up to 10 characters). Edits apply as you type. Focusing or hovering a row highlights that light in the room, and the target button frames it. Clearing a field restores its original value on blur. Changed rows show "Was …" with Reset; duplicate names are flagged but allowed. Renames stay in Apartment; Home Assistant keeps its own names.

## Home Assistant links (Setup › Home Assistant)
Formerly "Fixture links". Each light in the model is matched to the Home Assistant light that controls it, which is how a slider here reaches the real bulb. Rows read Linked, Shared (two model lights on one Home Assistant light) or Not linked. Pick from the list to change a match. Flash blinks the real light and frames it in the room, so you can check the match. Victoria's sockets have no Home Assistant lights until commissioning, so they read Not linked and have no picker.

## Home chat (right column › Chat)
The Apartment view becomes Home's main view. The top bar reads **Home** and loses **Close**; the right column gets two tabs, **Controls** (inspector and camera dock) and **Chat**. On phones, Chat is the third sheet tab. A dot on Chat marks unread proactive messages.
- **Feed:** Home, user, external and system turns, grouped by speaker, with time. Replies stream while a progress line shows the first tool step. **Stop** cancels before tools run, or cuts the text short after they have.
- **Bound to the room:** lights and named aims in replies are chips; hovering highlights them in the room and clicking selects them. The current selection rides along as "About Socket 4 · Desk" (removable) and resolves "brighter", "warmer", "50%".
- **Cards:** action (Show, Adjust, Undo), aim proposal (Set aim, Preview, Discard; the same proposal as a drag, origin "chat", previewed in cyan), camera look (Open camera), world state, explain (tool steps), preference review (private, kept out of saved chat), Travel Mode.
- **Composer:** "Ask your home, or type /". The slash menu lists /look, /world-state, /explain, /lights, /ask, /travel and /clear, with arrow keys and Tab to complete. Voice uses the mic button; Enter sends and Shift+Enter adds a new line. Suggestions sit under the last reply.
- **Header:** model and source, Explain the last answer, Copy transcript, New conversation.
Prototype replies are scripted; the production chat keeps today's routing, tools and history.

## Command ladder (Aim tab)
Collapsed by default to one line with its status ("Modeled · nothing sent", "Simulated · execution blocked"). Expanded, it shows the intent sentence first, then:
1. **Draft target:** proposed in this view, or the current aim.
2. **Modeled command:** the solve in the Apartment frame (pan, tilt, limit).
3. **Accepted execution:** "not available · not commissioned" for Victoria; "blocked · no movement authority and no target-plane binding" for Echo.
4. **Reported state:** none, or no telemetry because the bench is loopback-only.

Exact output shows the modeled solve JSON (copyable) and, for Echo, the documented Product Aim request with `null` values plus its blockers. Copy request is disabled whenever it is blocked. Provenance lists model and revision, frame, scene, collision hash, optic, origin and the data source.

## Inspector and dock coexistence
- The dock is part of the column and never covers the inspector or the canvas.
- Selecting a light leaves the dock as it was. A perception trace step can select a light ("Proposed" selects Socket 4 and proposes Desk reading with origin `perception`).
- **Trace:** Seen (frame) → Interpreted (JEV) → Proposed (intent) → Did (execution). Each step is a button. Seen shows camera positions and body layers. Proposed selects and proposes. Did explains that nothing executes without the owner and that these fixtures aren't commissioned.
- **Expanding the dock** widens the column; collapsing reduces it to a one-line summary ("Reading at the desk · proposes Desk reading on S4"). Hidden leaves a "Show camera" button at the bottom of the column.
- **Live view and the analyzed frame** are separate sources. The overlay is drawn only on the analyzed frame it belongs to, stamped with its frame number and capture time. Choosing Live never shows the analyzed skeleton.
- **Body in the room:** only when a session exists and body layers are on. Observed joints are solid, inferred ones dashed, nominal volume is translucent and labeled "not measured", and uncertainty is a halo. The label says "one frame, not tracked". Follow stays disabled until continuous registered tracking exists.

## Cancel, Return and Escape
Escape walks one step at a time, topmost first, and never skips a level.
1. A reticle drag in progress → cancel the drag (the proposal is unchanged).
2. Tap-to-aim → leave it.
3. Camera view → **Return to room**.
4. A proposal on the selected light → discard it ("⌘Z restores it").
5. A selection → clear it, and focus returns to the canvas or the list row.
6. Nothing active → nothing happens. Apartment is the main view and has no Close. Unsaved work in any home is kept as a local draft.

Popovers, modals and the naming field claim Escape first. Three phrases never overlap in meaning:
- **Return to room** leaves a camera view.
- **Return to Auto** hands a manually changed light back to adaptive lighting.
- **Back** leaves a secondary screen (Lab, metrics) for Apartment.

## Status semantics
- "Connected" always names the system: "Home Assistant connected". Victoria shows "No light entities for Den fixtures" and never inherits Echo's status.
- Frame ages:
  - **current** ≤ 10 s
  - **held** 10 s – 2 min: shown, not used for lighting
  - **stale** > 2 min: dimmed and dashed, not used for lighting
  - **expired:** frame hidden, summary only
  - **no session:** empty state, no start button; sessions start in Perception Lab
  - **replay:** labeled with its recording date, scrubbable
- An old analyzed frame is never called a running camera. A modeled or simulated aim is never called measured.
- When Home Assistant is offline, the last reported state is shown with its time, controls are disabled and nothing is queued.
- Assets: Photo shows loading with its percentage, then unavailable with Retry or Back to Cloud. A failed scan says so and offers Retry, never switching homes on its own.

## Keyboard and touch
| Key | Action |
|---|---|
| ← → | Orbit one detent |
| ↑ ↓ | Tilt |
| + − | Zoom |
| H / F | Fit |
| N / P | Next or previous light (selects; the inspector follows) |
| A | Aim tab |
| ⌘Z / ⇧⌘Z | Undo / redo the aim proposal |
| ? | Gestures and keys |
| Tab | Header → home switcher → status → Setup → Close → canvas labels (skipped; use the list) → reticle → toolbar → inspector → dock |

Arrow keys inside the column are left to its controls. Sliders, switches and segmented controls are native buttons or ranges with labels. The focus ring is the global 1 px `--hg-blue`.

On touch: tap selects, one-finger drag orbits, two-finger pinch zooms, the reticle has a 44 px hit area, and tap-to-aim is the alternative to dragging.

## What persists
- **Across homes:** theme, layers, dock state, detail level.
- **Per home:** selection, inspector tab, proposals with undo history, unsaved drafts and named aims, camera choice, camera view. The orbit is refit on switching.
- **Reset when the selection changes:** the open channel, zone focus and naming field. Proposals stay with their light.
