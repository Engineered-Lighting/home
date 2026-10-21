# Apartment UI fidelity acceptance ledger

Overall result: **blocked**. This is a clause inventory and evidence map, not a blanket acceptance or a replacement for the original issue owners. All original issues retain their existing credit and open/closed state.

Reference: `HomeUIticketBreakdown.zip`, SHA-256 `4f7fdb568481517e0e34e58459fcdd48f6111aac6b57c8c145c55d57091466c3`. The unchanged ZIP contains 61 tickets, 101 build clauses and 83 top-level acceptance clauses. Every clause appears below with a stable identifier.

Baseline: `d6701024ce6ebc45a94b4d5ac7562574de0efd2d`; predecessor #411: `f22912312fed3d15d85f7ab2254d1fb22c0fc38f`. Current runtime/qualification pins are in `design-qa.md` and private receipts. Main, #411 and #414 remain unmerged by this work; deployment is held.

The owner removed Light appearance from this release and deferred body mesh/fitting/occlusion. These are scope decisions, not failed UI tests. Proposed persisted fields remain approval dependent. No tracking chat or producer is contacted or changed.

The ZIP room and its scripted data are illustrative. The existing scan renderer wins under HANDOFF; authored comparisons do not qualify installed Echo Park or Victoria scan bytes, perception accuracy, physical aiming or photometry. Screenshots and diagnostic receipts stay private.

## Design-gap tickets

| Ticket | Local correction | Acceptance still required |
|---|---|---|
| #398 | Orb/halo, selected/hover glyph and channel cosmetics in predecessor #411 | Complete declared fixture-state/orbit proof and installed scan qualification |
| #399 | Selected/all/off beams, independent pools, optional channel details, restrained beam gain | Final combined proof and current visual review |
| #400 | Light/Aim rows and presets; compact 118 px radial ring with native touch equivalent | Final combined proof and reviewed phone radial states |
| #401 | Header/Lit/direct Setup, normal Setup toolbar, direct Echo Aim with shared tabs | Final combined proof and advanced-Aim visual review |
| #402 | Radial Off reads zero while levels/CCT and protected geometry stay intact | Canonical radial proof retained in final run |
| #403 | Short source-derived tags, selected phone tab, compact tool pill and one room toolbar | Final viewport/target proof and reviewed pairs |
| #404 | Compact dock header, single no-session/closed state, idle diagnostics in Details; cancel stays available during discovery | Populated/empty/loading/failure visual states plus lifecycle proof |
| #405 | Compact Setup header/source disclosure and shared room toolbar; original save/read-only guards | Stored-field approval remains for proposed tools; final fixture-save proof |
| #406 | Compact ZIP header icons and action menu; single pipeline error per failed turn | Populated card paired visual matrix, final stream/cancel/recovery and authority proof |
| #407 | Owner decision: dark only; normalize saved Light preferences and remove switches | Dark-only mounted Home/Previous Home proof |
| #408 | Truthful simulated scan-shadow and deferred-body copy; capability gates retained | Final room guide/connected-view proof; live producer qualification separate |
| #409 | This complete clause inventory, reference component/spec map and explicit dispositions | Fill current exact-head software and visual receipts; no missing evidence counted as pass |

## Clause-level inventory

Build clause IDs use `B01…`; acceptance IDs use `A01…` within each original requirement. “Mapped” means an evidence owner exists; it does not assert that every clause has passed. Gates above apply to every row.

### APT-F01 — Renderer overlay bridge

Original: [#220](https://github.com/Engineered-Lighting/home/issues/220) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Overlay bridge, overlay stack, UI primitives, home capability and state harnesses; mounted shell audits.

Source seam: `app/src/home-3d/*, app/src/home-apartment.jsx`. Prototype: scene.js: `_screen`, `_placeLabels`, `gDyn`, `surfaceHit`, `checkAim`. Spec: HANDOFF.md › The room view in the prototype is a placeholder.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-F01-B01 | A small API on the existing renderer for: projecting world points to screen, a DOM layer for labels and handles, a scene group for transient overlay geometry, and ray picks against the real scan mesh (reuse the target-drag picking). | Mapped; evidence gate above |
| APT-F01-B02 | No change to point cloud, splat, mesh, wall-fade, orbit or asset loading. | Mapped; evidence gate above |
| APT-F01-A01 | A test overlay (a dot plus a label at a fixture) tracks correctly while orbiting in Cloud, Photo and Mesh. | Mapped; evidence gate above |
| APT-F01-A02 | A pick returns point, normal and surface name on the scan mesh. | Mapped; evidence gate above |
| APT-F01-A03 | Renderer frame time unchanged within noise. | Mapped; evidence gate above |

### APT-F02 — Status vocabulary and tokens

Original: [#221](https://github.com/Engineered-Lighting/home/issues/221) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Overlay bridge, overlay stack, UI primitives, home capability and state harnesses; mounted shell audits.

Source seam: `app/src/home-tokens.css`. Prototype: app.css (`--apt-*` tokens, `.apt-badge`), ui-kit.jsx `Badge`. Spec: SPEC.md status semantics; DIRECTION.md.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-F02-B01 | Map the prototype's state colors (ok, attention, blocked, authored, proposed, none) onto existing `--hg-*` tokens, dark and light theme. | Mapped; evidence gate above |
| APT-F02-B02 | One Badge component with the agreed labels (Live, Held, Stale, Recorded, Authored, Proposed, Saved, Blocked, Reported). | Mapped; evidence gate above |
| APT-F02-B03 | Move `AIM_*` color literals into tokens. Remove the forced `data-theme="dark"`: the canvas stays dark and the chrome follows the theme. | Mapped; evidence gate above |
| APT-F02-A01 | Badges meet 4.5:1 text contrast in both themes. | Mapped; evidence gate above |
| APT-F02-A02 | `--hg-fg-5` is never used for informative text. | Mapped; evidence gate above |

### APT-F03 — UI primitives

Original: [#222](https://github.com/Engineered-Lighting/home/issues/222) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Overlay bridge, overlay stack, UI primitives, home capability and state harnesses; mounted shell audits.

Source seam: `app/src/home-apartment-cards.jsx or a new home-apartment-ui.jsx`. Prototype: ui-kit.jsx (`Btn`, `Seg`, `Slider`, `Switch`, `Icon`, `Popover`), toast in app.jsx. Spec: See the reference coverage map below.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-F03-B01 | Reuse existing Home controls where they exist; add only what is missing. | Mapped; evidence gate above |
| APT-F03-B02 | Toast supports an action button (Undo) and a timeout. | Mapped; evidence gate above |
| APT-F03-A01 | Each primitive is keyboard operable with visible focus. | Mapped; evidence gate above |
| APT-F03-A02 | Touch targets are at least 44 px on coarse pointers. | Mapped; evidence gate above |

### APT-F04 — Escape and focus stack

Original: [#223](https://github.com/Engineered-Lighting/home/issues/223) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Overlay bridge, overlay stack, UI primitives, home capability and state harnesses; mounted shell audits.

Source seam: `app/src/home-overlay.js`. Prototype: app.jsx keydown handler. Spec: SPEC.md Escape rules; SPEC.md › Cancel, Return and Escape.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-F04-B01 | Escape cancels the innermost operation first: drag, tap-aim, draft, popover, setup tool, then selection. It never closes Apartment. | Mapped; evidence gate above |
| APT-F04-B02 | Focus returns to the control that started the operation. | Mapped; evidence gate above |
| APT-F04-B03 | With Close gone, step 6 of the ladder does nothing. Popovers, modals and text fields claim Escape first. | Mapped; evidence gate above |
| APT-F04-A01 | A table test covers each level of the stack. | Mapped; evidence gate above |
| APT-F04-A02 | The ladder matches SPEC.md › Cancel, Return and Escape, step by step. | Mapped; evidence gate above |

### APT-F05 — Per-home capability model

Original: [#224](https://github.com/Engineered-Lighting/home/issues/224) (open at inspection).

Disposition: Reuse existing home/controller/capability contracts. No new persisted capability schema or registration is authorized by this batch. Home isolation requires the mapped software proofs and separate upstream qualification.

Evidence family: Overlay bridge, overlay stack, UI primitives, home capability and state harnesses; mounted shell audits.

Source seam: `app/src/home-site-registry.js, home-apartment-site-session.js`. Prototype: data-sites.js (`capabilities`). Spec: HANDOFF.md data gaps; SPEC.md › What persists; HANDOFF.md › Capability-driven variants.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-F05-B01 | Propose a capabilities object per home (HA connection, engineered fixtures, perception, JEV, cameras) from existing registry data. Schema change goes in the PR description first. | Scope/gate above |
| APT-F05-B02 | Unsaved work and selection are kept per home. | Scope/gate above |
| APT-F05-B03 | Variants are driven by the capability values in HANDOFF.md (engineered, perception, body, lights, assets, bench). Components never branch on the site id. | Scope/gate above |
| APT-F05-B04 | Persistence per SPEC › What persists: theme, layers, dock and detail across homes; selection, tab, proposals with undo, drafts, camera choice per home; orbit refits on switch. | Scope/gate above |
| APT-F05-A01 | Switching homes never shows another home's devices, feed or saved aims. | Scope/gate above |
| APT-F05-A02 | Acceptance example "Home isolation" in HANDOFF.md passes. | Scope/gate above |

### APT-F06 — Data states and ages

Original: [#225](https://github.com/Engineered-Lighting/home/issues/225) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Overlay bridge, overlay stack, UI primitives, home capability and state harnesses; mounted shell audits.

Source seam: `home-apartment-site-session.js`. Prototype: `?echo=` and `?vic=` scenarios: draft, stale, held, replay, loading, failed; Tweaks panel. Spec: SPEC.md › Status semantics; HANDOFF.md › Data (analyzed_at, thresholds).

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-F06-B01 | One place that turns raw data into states: HA connected or offline (with last reported time), local draft, loading, failed, and frame ages current (≤10 s), held (≤2 min), stale, expired, no session, replay. | Mapped; evidence gate above |
| APT-F06-B02 | Every surface reads these states; none computes its own. | Mapped; evidence gate above |
| APT-F06-A01 | "Connected" always names the system. | Mapped; evidence gate above |
| APT-F06-A02 | Acceptance examples "HA offline (Echo)" and "No session" pass. | Mapped; evidence gate above |

### APT-S01 — Apartment is the default route

Original: [#226](https://github.com/Engineered-Lighting/home/issues/226) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Home shell, M2 shell, room guide, dark-only browser, unread integration and responsive shell audits.

Source seam: `app/src/home-app.jsx, home-apartment.jsx`. Prototype: `prototype/index.html?vp=1366x768&home=victoria`. Spec: HANDOFF.md › Removal and consolidation.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-S01-B01 | Open on Apartment. Remove the Close button. | Mapped; evidence gate above |
| APT-S01-B02 | Give Lab, metrics, people and anything else only reachable from the old menu a place in the top bar overflow or the slash menu. | Mapped; evidence gate above |
| APT-S01-A01 | Every screen reachable before is still reachable, listed in the PR. | Mapped; evidence gate above |
| APT-S01-A02 | Deep links to old routes still work. | Mapped; evidence gate above |

### APT-S02 — Top bar and home switcher

Original: [#227](https://github.com/Engineered-Lighting/home/issues/227) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Home shell, M2 shell, room guide, dark-only browser, unread integration and responsive shell audits.

Source seam: `home-apartment.jsx`. Prototype: `prototype/index.html?vp=1366x768&home=echo`, ui-shell.jsx top bar. Spec: SPEC.md › Status semantics.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-S02-B01 | "Home" title, Echo Park / Victoria switcher, status summary with a popover that says what is connected. | Mapped; evidence gate above |
| APT-S02-B02 | Switching keeps each home's unsaved work. | Mapped; evidence gate above |
| APT-S02-A01 | Unavailable assets explain themselves and never silently switch homes. | Mapped; evidence gate above |

### APT-S03 — Room toolbar

Original: [#228](https://github.com/Engineered-Lighting/home/issues/228) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Home shell, M2 shell, room guide, dark-only browser, unread integration and responsive shell audits.

Source seam: `home-apartment.jsx`. Prototype: `prototype/index.html?vp=1366x768`, ui-shell.jsx toolbar. Spec: SPEC.md › Status semantics (assets).

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-S03-B01 | Cloud, Photo, Mesh mapped to the existing modes (Lit arrives in APT-V01). Camera views, Return to room, help popover with shortcuts. | Mapped; evidence gate above |
| APT-S03-B02 | Loading, progress and retry per mode. | Mapped; evidence gate above |
| APT-S03-B03 | Camera views (room poses) with Return to room. Photo unavailable shows Retry and Back to Cloud. | Mapped; evidence gate above |
| APT-S03-A01 | Toolbar sits in the same place in both homes and never covers the selected fixture. | Mapped; evidence gate above |
| APT-S03-A02 | Acceptance example "Photo missing" passes. | Mapped; evidence gate above |

### APT-S04 — Right column: Controls and Chat tabs

Original: [#229](https://github.com/Engineered-Lighting/home/issues/229) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Home shell, M2 shell, room guide, dark-only browser, unread integration and responsive shell audits.

Source seam: `home-apartment.jsx`. Prototype: `prototype/index.html?vp=1366x768&home=victoria&col=chat`. Spec: SPEC.md › Layout and responsive rules; SPEC.md › Home chat.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-S04-B01 | One right column with tabs Controls and Chat and an unread dot on Chat. Chat content arrives in APT-C01; ship with the existing chat mounted as is. | Mapped; evidence gate above |
| APT-S04-A01 | Column width and room fit follow SPEC layout rules at 1920, 1366 and 1024 wide. | Mapped; evidence gate above |

### APT-S05 — Tablet and phone layouts

Original: [#230](https://github.com/Engineered-Lighting/home/issues/230) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Home shell, M2 shell, room guide, dark-only browser, unread integration and responsive shell audits.

Source seam: `home-apartment.jsx, CSS`. Prototype: `?vp=768x1024`, `?vp=390x844`. Spec: SPEC.md › Layout and responsive rules; SPEC.md › Keyboard and touch.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-S05-B01 | Phone: bottom sheet (peek, half, full) with Lights, Camera, Chat tabs. Tablet per SPEC. | Mapped; evidence gate above |
| APT-S05-B02 | Safe areas, scroll ownership, no panel collisions. | Mapped; evidence gate above |
| APT-S05-A01 | Screenshots at 390x844, 768x1024, 1366x768 match the prototype's layout. | Mapped; evidence gate above |

### APT-S06 — Layers, legend, help and about

Original: [#231](https://github.com/Engineered-Lighting/home/issues/231) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Home shell, M2 shell, room guide, dark-only browser, unread integration and responsive shell audits.

Source seam: `home-apartment.jsx`. Prototype: ui-shell.jsx `LayersPopover`, `Legend`, `HelpModal`, `AboutModal`. Spec: SPEC.md › Keyboard and touch.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-S06-B01 | Layers popover: pools, beams, named aims always, labels always, cameras, body observed, body inferred, body volume, uncertainty. Unavailable layers explain why. | Mapped; evidence gate above |
| APT-S06-B02 | Legend for in-room marks, shown on demand. | Mapped; evidence gate above |
| APT-S06-B03 | Help modal listing every shortcut (A, Z, Enter, N/P, ⌘Z, Esc, ?). | Mapped; evidence gate above |
| APT-S06-B04 | About: what is live, modeled, authored or recorded in this home. | Mapped; evidence gate above |
| APT-S06-A01 | Layer choices persist across homes. | Mapped; evidence gate above |
| APT-S06-A02 | Help lists exactly the shortcuts that work. | Mapped; evidence gate above |

### APT-R01 — Selection model and labels

Original: [#232](https://github.com/Engineered-Lighting/home/issues/232) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Room selection, room inspector, named-aim mapper and Quick harnesses; fixture-only write refusal tests.

Source seam: `home-apartment.jsx, home-3d/markers.js`. Prototype: `prototype/index.html?vp=1366x768&home=victoria&sel=vic-s4`. Spec: SPEC.md › Selection precedence; SPEC.md › Gestures.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-R01-B01 | Click fixture or label selects; empty-canvas drag orbits; hover labels; selected emphasis; Escape deselects last. | Mapped; evidence gate above |
| APT-R01-B02 | Keyboard: Tab through fixtures, Enter selects. | Mapped; evidence gate above |
| APT-R01-B03 | N and P cycle through lights. | Mapped; evidence gate above |
| APT-R01-A01 | Selection precedence matches SPEC. | Mapped; evidence gate above |
| APT-R01-A02 | Orbit, select and aim gestures never conflict. | Mapped; evidence gate above |
| APT-R01-A03 | Acceptance example "Direct selection" passes (without "offers Close"). | Mapped; evidence gate above |

### APT-R02 — Room inspector

Original: [#233](https://github.com/Engineered-Lighting/home/issues/233) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Room selection, room inspector, named-aim mapper and Quick harnesses; fixture-only write refusal tests.

Source seam: `home-apartment-cards.jsx`. Prototype: `prototype/index.html?vp=1366x768&home=echo`. Spec: SPEC.md › Selection precedence.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-R02-B01 | Lights grouped by room with a switch per room heading, named aims list with Edit. | Mapped; evidence gate above |
| APT-R02-A01 | Clicking a row selects the same object as clicking it in the room. | Mapped; evidence gate above |

### APT-R03 — Quick controls

Original: [#234](https://github.com/Engineered-Lighting/home/issues/234) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Room selection, room inspector, named-aim mapper and Quick harnesses; fixture-only write refusal tests.

Source seam: `home-apartment-cards.jsx`. Prototype: `prototype/index.html?vp=1366x768&home=victoria`, ui-inspector.jsx `QuickControls`. Spec: SPEC.md › Quick controls.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-R03-B01 | All on, All off, scene and script chips from the home's HA scenes and scripts (automations marked ▶), each with Undo in the toast. | Mapped; evidence gate above |
| APT-R03-A01 | Undo restores the exact previous state. | Mapped; evidence gate above |
| APT-R03-A02 | Offline HA disables the chips with a reason. | Mapped; evidence gate above |

### APT-R04 — Named aim inspector

Original: [#235](https://github.com/Engineered-Lighting/home/issues/235) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Room selection, room inspector, named-aim mapper and Quick harnesses; fixture-only write refusal tests.

Source seam: `Cross-cutting quality gate`. Prototype: ui-inspector.jsx `TargetInspector`. Spec: SPEC.md › Selection precedence.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-R04-B01 | Selecting a named aim in the room or list opens its inspector: which lights use it, which can reach it, Aim a light here, Edit in Setup. | Mapped; evidence gate above |
| APT-R04-A01 | Same inspector from the room, the list and a chat chip. | Mapped; evidence gate above |

### APT-L01 — Inspector frame

Original: [#236](https://github.com/Engineered-Lighting/home/issues/236) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Radial visual contracts, Echo inspector, Echo Aim tabs, phone controls, light display and room inspector browser proofs.

Source seam: `home-apartment-aim.jsx (merge into it)`. Prototype: `prototype/index.html?vp=1366x768&home=victoria&sel=vic-s4&tab=light`. Spec: HANDOFF.md › Removal and consolidation; HANDOFF.md › Shared component map.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-L01-B01 | Header, status badge, Light / Aim / Setup tabs, and a line saying what a change sends. | Mapped; evidence gate above |
| APT-L01-B02 | Consolidate home-lighting-target-panel, -live-panel, -dynamic-panel, home-live-room-controls and home-fixture-execution-panel into this inspector. List what each one became in the PR. | Mapped; evidence gate above |
| APT-L01-B03 | Provenance badges in the header (live, modeled, authored). | Mapped; evidence gate above |
| APT-L01-B04 | Retire the anchored control card and the `aim light` mode and its button. Remove Victoria's bottom-left fixture controls. Move debug statistics behind Details. | Mapped; evidence gate above |
| APT-L01-A01 | No duplicate light controls remain on screen in either home. | Mapped; evidence gate above |

### APT-L02 — Cylinder channels: Spot, Down, Radial

Original: [#237](https://github.com/Engineered-Lighting/home/issues/237) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Radial visual contracts, Echo inspector, Echo Aim tabs, phone controls, light display and room inspector browser proofs.

Source seam: `home-apartment-aim.jsx`. Prototype: `prototype/index.html?vp=1366x768&home=victoria&sel=vic-s4&tab=light`. Spec: HANDOFF.md › Capability-driven variants (lights).

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-L02-B01 | Spot and Down each have brightness and color temperature. Radial has a level and color temperature. Per-fixture All on and All off. | Mapped; evidence gate above |
| APT-L02-A01 | Each channel maps to its own existing entity or command. | Mapped; evidence gate above |

### APT-L03 — Radial ring panel

Original: [#252](https://github.com/Engineered-Lighting/home/issues/252) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Radial visual contracts, Echo inspector, Echo Aim tabs, phone controls, light display and room inspector browser proofs.

Source seam: `home-apartment-aim.jsx`. Prototype: ui-inspector.jsx `RadialRing`. Spec: See the reference coverage map below.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-L03-B01 | Plan view, north up, turned to the zone-1 heading. Off/25/50/100 presets, Toward walls, Into the room, Show in room. | Mapped; evidence gate above |
| APT-L03-A01 | Selecting a zone names what it faces. | Mapped; evidence gate above |

### APT-L04 — Echo lights

Original: [#238](https://github.com/Engineered-Lighting/home/issues/238) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Radial visual contracts, Echo inspector, Echo Aim tabs, phone controls, light display and room inspector browser proofs.

Source seam: `home-apartment-aim.jsx`. Prototype: `prototype/index.html?vp=1366x768&home=echo`. Spec: See the reference coverage map below.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-L04-B01 | Ceiling and plug lights use the same inspector with their available controls and HA entity line. | Mapped; evidence gate above |
| APT-L04-A01 | Echo's existing engineered-light controls keep working. | Mapped; evidence gate above |

### APT-A01 — Reticle drag on the scan mesh

Original: [#239](https://github.com/Engineered-Lighting/home/issues/239) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Aim presentation, lifecycle, reticle drag, wiring, phone, keys, library, mapper, chat and history harnesses.

Source seam: `home-apartment-aiming.js, home-3d/*`. Prototype: `prototype/index.html?vp=1366x768&home=victoria&sel=vic-s4&tab=aim`. Spec: docs/APARTMENT-LIGHT-AIMING.md; SPEC.md › Gestures; SPEC.md › Aim lifecycle.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-A01-B01 | Drag the reticle: socket stays fixed, pick on the mesh, drag chip shows surface, distance and tilt. Invalid, blocked and out-of-reach states. | Mapped; evidence gate above |
| APT-A01-B02 | Snap to named aims nearby. | Mapped; evidence gate above |
| APT-A01-B03 | Validity reasons as in SPEC (out of reach with angle and limit, blocked, grazing). | Mapped; evidence gate above |
| APT-A01-A01 | Empty-canvas drag still orbits. | Mapped; evidence gate above |
| APT-A01-A02 | Cancel restores the previous aim. | Mapped; evidence gate above |
| APT-A01-A03 | Acceptance examples "Drag aim" and "Unreachable" pass. | Mapped; evidence gate above |

### APT-A02 — Aim tab opens on drag

Original: [#240](https://github.com/Engineered-Lighting/home/issues/240) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Aim presentation, lifecycle, reticle drag, wiring, phone, keys, library, mapper, chat and history harnesses.

Source seam: `Cross-cutting quality gate`. Prototype: app.jsx `onAim`. Spec: SPEC.md › Aim lifecycle.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-A02-B01 | Starting a drag or keyboard nudge switches to Aim; on phones it raises the sheet to half. Chat stays open if it was open. | Mapped; evidence gate above |
| APT-A02-A01 | Dropping always leaves Set aim reachable. | Mapped; evidence gate above |

### APT-A03 — Zones | Spot pill

Original: [#249](https://github.com/Engineered-Lighting/home/issues/249) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Aim presentation, lifecycle, reticle drag, wiring, phone, keys, library, mapper, chat and history harnesses.

Source seam: `Cross-cutting quality gate`. Prototype: scene.js `_placeAimUI`, `.apt-toolpill`. Spec: SPEC.md › Spot or zones (cylinders).

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-A03-B01 | Pill above a selected cylinder switches the in-room tool; tied to Light/Aim tabs; keys Z and A; cyan dot when an aim is pending. | Mapped; evidence gate above |
| APT-A03-A01 | Pill hides during drags and never covers the reticle. | Mapped; evidence gate above |

### APT-A04 — Set aim bar in the room

Original: [#241](https://github.com/Engineered-Lighting/home/issues/241) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Aim presentation, lifecycle, reticle drag, wiring, phone, keys, library, mapper, chat and history harnesses.

Source seam: `Cross-cutting quality gate`. Prototype: scene.js `_placeAimUI`, `.apt-aimbar`. Spec: SPEC.md › Spot or zones (cylinders).

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-A04-B01 | After a drop, a bar by the point: target name, shadow note, Set aim, Discard. Enter and Escape. | Mapped; evidence gate above |
| APT-A04-A01 | Works with Chat open or the column hidden. | Mapped; evidence gate above |
| APT-A04-A02 | Blocked shows only Discard. | Mapped; evidence gate above |

### APT-A05 — Aim tab content

Original: [#242](https://github.com/Engineered-Lighting/home/issues/242) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Aim presentation, lifecycle, reticle drag, wiring, phone, keys, library, mapper, chat and history harnesses.

Source seam: `Cross-cutting quality gate`. Prototype: ui-inspector.jsx `AimTab`. Spec: SPEC.md › Aim lifecycle.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-A05-B01 | Proposal, recall chips for named aims, Save as named aim, undo and redo. | Mapped; evidence gate above |
| APT-A05-B02 | ⌘Z and ⇧⌘Z for undo and redo; Escape discards a proposal and ⌘Z restores it. | Mapped; evidence gate above |
| APT-A05-A01 | Matches the simplified prototype; no extra sections. | Mapped; evidence gate above |

### APT-A06 — Command ladder

Original: [#243](https://github.com/Engineered-Lighting/home/issues/243) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Aim presentation, lifecycle, reticle drag, wiring, phone, keys, library, mapper, chat and history harnesses.

Source seam: `home-apartment-aim.jsx`. Prototype: ui-inspector.jsx command section. Spec: SPEC.md › Command ladder (Aim tab).

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-A06-B01 | Intent, then modeled command, accepted execution and reported state. The exact generated payload on demand with copy. Use the canonical command builder; never a mock payload. | Mapped; evidence gate above |
| APT-A06-B02 | Variants per `engineered` capability: simulated, modeled, commissioned. Execution receipts `{accepted_at, reported_state, source}` (schema gap). Fold execution and lighting panels into this ladder. | Mapped; evidence gate above |
| APT-A06-A01 | Victoria reads modeled-only until commissioning. | Mapped; evidence gate above |

### APT-A07 — Named aim save states

Original: [#244](https://github.com/Engineered-Lighting/home/issues/244) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Aim presentation, lifecycle, reticle drag, wiring, phone, keys, library, mapper, chat and history harnesses.

Source seam: `Cross-cutting quality gate`. Prototype: Complete prototype. Spec: SPEC.md › Aim lifecycle; HANDOFF.md › Data (target state, geometry hash).

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-A07-B01 | Unsaved, saving, saved, conflict, changed scan or profile. | Mapped; evidence gate above |
| APT-A07-B02 | Resolve a conflict (keep mine, keep theirs) and confirm a target after the scan or profile changed. | Mapped; evidence gate above |
| APT-A07-A01 | Each state appears with its copy from SPEC. | Mapped; evidence gate above |

### APT-A08 — Tap and keyboard aiming

Original: [#245](https://github.com/Engineered-Lighting/home/issues/245) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Aim presentation, lifecycle, reticle drag, wiring, phone, keys, library, mapper, chat and history harnesses.

Source seam: `Cross-cutting quality gate`. Prototype: Complete prototype. Spec: SPEC.md › Keyboard and touch.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-A08-B01 | Tap-to-aim mode and arrow-key nudges as alternatives to dragging. | Mapped; evidence gate above |
| APT-A08-A01 | Aiming is possible without a pointer drag. | Mapped; evidence gate above |

### APT-A09 — Return to Auto

Original: [#246](https://github.com/Engineered-Lighting/home/issues/246) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Aim presentation, lifecycle, reticle drag, wiring, phone, keys, library, mapper, chat and history harnesses.

Source seam: `Cross-cutting quality gate`. Prototype: app.jsx `returnToAuto`. Spec: SPEC.md › Cancel, Return and Escape.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-A09-B01 | A light changed by hand shows Manual with Return to Auto, which hands it back to adaptive lighting. | Mapped; evidence gate above |
| APT-A09-A01 | "Return to Auto", "Return to room" and "Close" never share wording. | Mapped; evidence gate above |

### APT-A10 — Named aim as an automation reference

Original: [#262](https://github.com/Engineered-Lighting/home/issues/262) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Aim presentation, lifecycle, reticle drag, wiring, phone, keys, library, mapper, chat and history harnesses.

Source seam: `Cross-cutting quality gate`. Prototype: Complete prototype. Spec: HANDOFF.md › Data (automation reference).

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-A10-B01 | Show how a named aim is used by automations: its stable id, a copyable reference `aim {fixture_id} → {target_id}`, and which automations use it. Propose the reference format first (schema gap). | Mapped; evidence gate above |
| APT-A10-A01 | Renaming an aim does not break the reference. | Mapped; evidence gate above |

### APT-Z01 — Composer drawing and abstract fixtures

Original: [#247](https://github.com/Engineered-Lighting/home/issues/247) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Directional unit, integration and both browser phases; canonical radial geometry/state proof.

Source seam: `Cross-cutting quality gate`. Prototype: `prototype/index.html?vp=1366x768&home=victoria&sel=vic-s4&tab=light`, scene.js `_drawZones`. Spec: SPEC.md › Radial zones in the room (composer).

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-Z01-B01 | Soft polar glow at fixture height, edge line, node per lit zone, ticks on the outer ring. Fixtures drawn as small orbs tinted by the channels that are on. | Mapped; evidence gate above |
| APT-Z01-A01 | Readable from any orbit angle in Cloud, Photo and Mesh. | Mapped; evidence gate above |

### APT-Z02 — Composer gesture

Original: [#248](https://github.com/Engineered-Lighting/home/issues/248) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Directional unit, integration and both browser phases; canonical radial geometry/state proof.

Source seam: `Cross-cutting quality gate`. Prototype: scene.js `_composeStartable`, `_composeMove`, `_endCompose`. Spec: See the reference coverage map below.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-Z02-B01 | Press on hub or lit lobe, drag outward to brighten and inward to dim, sweep interpolation, pickup without jumps, zone readout by the cursor. | Mapped; evidence gate above |
| APT-Z02-A01 | Orbiting works outside hub and lobes. | Mapped; evidence gate above |
| APT-Z02-A02 | Pinch and second pointer cancel cleanly. | Mapped; evidence gate above |
| APT-Z02-A03 | The ring panel (APT-L03) is the non-drag alternative for every composer change. | Mapped; evidence gate above |

### APT-Z03 — Fades and command rate

Original: [#250](https://github.com/Engineered-Lighting/home/issues/250) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Directional unit, integration and both browser phases; canonical radial geometry/state proof.

Source seam: `Cross-cutting quality gate`. Prototype: scene.js `_fadeTick`. Spec: See the reference coverage map below.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-Z03-B01 | Display eases with a 160 ms time constant. Commands go out at most every 100 ms with the driver's transition covering the gap. | Mapped; evidence gate above |
| APT-Z03-B02 | Reduced motion: no easing, values snap. | Mapped; evidence gate above |
| APT-Z03-A01 | No visible jump entering a brighter or dimmer zone. | Mapped; evidence gate above |

### APT-Z04 — Zone facing

Original: [#251](https://github.com/Engineered-Lighting/home/issues/251) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Directional unit, integration and both browser phases; canonical radial geometry/state proof.

Source seam: `Cross-cutting quality gate`. Prototype: scene.js `zoneFacing`. Spec: HANDOFF.md › Data (radial_zone1_heading_deg).

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-Z04-B01 | Ray cast each zone direction against the scan mesh to name what it faces. Uses the per-fixture zone-1 heading (schema gap). | Mapped; evidence gate above |
| APT-Z04-A01 | Toward walls lights the zones with a wall within 2.2 m. | Mapped; evidence gate above |

### APT-C01 — Chat in the column

Original: [#253](https://github.com/Engineered-Lighting/home/issues/253) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Chat presenter, context, header/action, unread and mounted Chat stream/cancel/error/recovery harnesses. Populated-card paired visual evidence remains separate.

Source seam: `home-events.jsx, home-app.jsx`. Prototype: `prototype/index.html?vp=1366x768&home=victoria&col=chat`, ui-chat.jsx. Spec: SPEC.md › Home chat.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-C01-B01 | Mount the existing chat feed, cards, slash dispatcher, voice, stop, preference review and Travel Mode in the Chat tab. Take only layout from the prototype. | Mapped; evidence gate above |
| APT-C01-B02 | Proactive messages set the unread dot on Chat. | Mapped; evidence gate above |
| APT-C01-A01 | Every chat feature from before works in the tab. | Mapped; evidence gate above |
| APT-C01-A02 | Travel Mode still blocks all light output. | Mapped; evidence gate above |

### APT-C02 — Entity chips

Original: [#254](https://github.com/Engineered-Lighting/home/issues/254) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Chat presenter, context, header/action, unread and mounted Chat stream/cancel/error/recovery harnesses. Populated-card paired visual evidence remains separate.

Source seam: `Cross-cutting quality gate`. Prototype: Complete prototype. Spec: SPEC.md › Home chat.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-C02-B01 | Lights and named aims in replies render as chips; hover highlights in the room, click selects. | Mapped; evidence gate above |
| APT-C02-A01 | Chips resolve to the current home only. | Mapped; evidence gate above |

### APT-C03 — Selection as context

Original: [#255](https://github.com/Engineered-Lighting/home/issues/255) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Chat presenter, context, header/action, unread and mounted Chat stream/cancel/error/recovery harnesses. Populated-card paired visual evidence remains separate.

Source seam: `Cross-cutting quality gate`. Prototype: Complete prototype. Spec: SPEC.md › Home chat.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-C03-B01 | Removable "About <selection>" chip sent with the message. | Mapped; evidence gate above |
| APT-C03-A01 | "Brighter" with a light selected changes that light. | Mapped; evidence gate above |

### APT-C04 — Chat aim proposals

Original: [#256](https://github.com/Engineered-Lighting/home/issues/256) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Chat presenter, context, header/action, unread and mounted Chat stream/cancel/error/recovery harnesses. Populated-card paired visual evidence remains separate.

Source seam: `Cross-cutting quality gate`. Prototype: Complete prototype. Spec: SPEC.md › Home chat; SPEC.md › Aim lifecycle.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-C04-B01 | Aim proposals from chat use origin "chat", preview in the room, and use the same Set aim and Discard path. | Mapped; evidence gate above |
| APT-C04-A01 | Setting from the card or the room bar gives the same result. | Mapped; evidence gate above |

### APT-C05 — Composer and header

Original: [#257](https://github.com/Engineered-Lighting/home/issues/257) (open at inspection).

Disposition: Compact header/actions corrected; Connection/Bench remain reachable in Chat actions. Copy retains its local text allowlist. Final software qualification and populated-card visual matrix still required.

Evidence family: Chat presenter, context, header/action, unread and mounted Chat stream/cancel/error/recovery harnesses. Populated-card paired visual evidence remains separate.

Source seam: `Cross-cutting quality gate`. Prototype: Complete prototype. Spec: SPEC.md › Home chat.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-C05-B01 | Slash menu with keyboard completion, mic, Stop; header with explain last answer, copy transcript, new conversation. | Mapped; evidence gate above |
| APT-C05-A01 | Existing slash commands unchanged. | Mapped; evidence gate above |

### APT-U01 — Setup mode and rail

Original: [#258](https://github.com/Engineered-Lighting/home/issues/258) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: All four Setup rail phases, writable fixture saves, read-only/conflict guards, calibration and named-aim mapper harnesses.

Source seam: `home-apartment-edit.jsx, home-apartment-calibrate.jsx`. Prototype: ui-shell.jsx `SetupInspector`. Spec: HANDOFF.md › Migration stages (Setup rail).

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-U01-B01 | Setup rail: Names, Named aims, Cameras and sensors, Home Assistant, plus the existing socket and equipment placement, wall references, tape inputs and measure. | Mapped; evidence gate above |
| APT-U01-B02 | Rail items: Devices and sockets, Names, Named aims, Zones, Fixture position, Home Assistant, Cameras and sensors. Calibration moves here. | Mapped; evidence gate above |
| APT-U01-A01 | Existing edit and measure features keep working and saving. | Mapped; evidence gate above |

### APT-U02 — Names

Original: [#259](https://github.com/Engineered-Lighting/home/issues/259) (open at inspection).

Disposition: Existing names and source-derived short tags are implemented; a new saved short-label override remains approval dependent.

Evidence family: All four Setup rail phases, writable fixture saves, read-only/conflict guards, calibration and named-aim mapper harnesses.

Source seam: `Cross-cutting quality gate`. Prototype: ui-shell.jsx `NamesTool`. Spec: SPEC.md › Names (Setup › Names).

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-U02-B01 | Name and Label per light, live edit, Reset, duplicate warning. Stored with the Apartment model (schema gap). | Scope/gate above |
| APT-U02-A01 | Names never change HA entity names. | Scope/gate above |

### APT-U03 — Home Assistant links

Original: [#260](https://github.com/Engineered-Lighting/home/issues/260) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: All four Setup rail phases, writable fixture saves, read-only/conflict guards, calibration and named-aim mapper harnesses.

Source seam: `Cross-cutting quality gate`. Prototype: ui-shell.jsx `HaLinks`. Spec: SPEC.md › Home Assistant links.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-U03-B01 | Match each light to its HA light, Linked/Shared/Not linked badge, Flash. | Mapped; evidence gate above |
| APT-U03-A01 | Flash is the only output here and respects Travel Mode. | Mapped; evidence gate above |

### APT-U04 — Named aim mapper

Original: [#261](https://github.com/Engineered-Lighting/home/issues/261) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: All four Setup rail phases, writable fixture saves, read-only/conflict guards, calibration and named-aim mapper harnesses.

Source seam: `Cross-cutting quality gate`. Prototype: ui-shell.jsx `TargetMapper`. Spec: SPEC.md › Mapping a named aim.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-U04-B01 | New, click a surface, auto-fit, name, type checks, shape, reach list, preview from a light, save, edit, delete. | Mapped; evidence gate above |
| APT-U04-A01 | Saved aims appear in every light's Aim tab. | Mapped; evidence gate above |

### APT-U05 — Named aim handles

Original: [#263](https://github.com/Engineered-Lighting/home/issues/263) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: All four Setup rail phases, writable fixture saves, read-only/conflict guards, calibration and named-aim mapper harnesses.

Source seam: `Cross-cutting quality gate`. Prototype: scene.js `_placeRHandles`, `_rectDrag`. Spec: SPEC.md › Mapping a named aim.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-U05-B01 | Corner resize, center move, height arrow on flat surfaces, floor drop line. | Mapped; evidence gate above |
| APT-U05-A01 | Handles stay on the real surface plane. | Mapped; evidence gate above |

### APT-U06 — Camera placement

Original: [#264](https://github.com/Engineered-Lighting/home/issues/264) (open at inspection).

Disposition: Approval dependent: existing camera calibration is retained; proposed camera placement/sensor persisted fields remain gated by setupProposal. Do not count proposed storage or coverage as accepted.

Evidence family: All four Setup rail phases, writable fixture saves, read-only/conflict guards, calibration and named-aim mapper harnesses.

Source seam: `Cross-cutting quality gate`. Prototype: ui-shell.jsx `DevicePlacer`. Spec: SPEC.md › Placing cameras and sensors.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-U06-B01 | Mount, click to place, point at a spot, pan/tilt/lens/height, coverage frustum, Covers list. Hand-placed pose kept apart from calibration (schema gap). | Scope/gate above |
| APT-U06-A01 | Placing never overwrites calibrated extrinsics. | Scope/gate above |

### APT-U07 — mmWave sensors

Original: [#265](https://github.com/Engineered-Lighting/home/issues/265) (open at inspection).

Disposition: Approval dependent: existing camera calibration is retained; proposed camera placement/sensor persisted fields remain gated by setupProposal. Do not count proposed storage or coverage as accepted.

Evidence family: All four Setup rail phases, writable fixture saves, read-only/conflict guards, calibration and named-aim mapper harnesses.

Source seam: `Cross-cutting quality gate`. Prototype: Complete prototype. Spec: SPEC.md › Placing cameras and sensors; HANDOFF.md › Data (presence sensor).

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-U07-B01 | Same placer with field and range; "Ordered, not connected" until paired (schema gap). | Scope/gate above |
| APT-U07-A01 | No sensor appears connected without HA entities. | Scope/gate above |

### APT-U08 — Zone heading commissioning

Original: [#266](https://github.com/Engineered-Lighting/home/issues/266) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: All four Setup rail phases, writable fixture saves, read-only/conflict guards, calibration and named-aim mapper harnesses.

Source seam: `Cross-cutting quality gate`. Prototype: Setup › Zones. Spec: HANDOFF.md › Data (radial_zone1_heading_deg).

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-U08-B01 | Blink zone 1 on the real fixture, then click where its light lands; store the heading. Until then the heading reads Authored. | Mapped; evidence gate above |
| APT-U08-A01 | Composer and ring turn to the stored heading. | Mapped; evidence gate above |

### APT-U09 — Fixture position and measurement

Original: [#267](https://github.com/Engineered-Lighting/home/issues/267) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: All four Setup rail phases, writable fixture saves, read-only/conflict guards, calibration and named-aim mapper harnesses.

Source seam: `home-apartment-edit.jsx, home-equipment-registration.js`. Prototype: Setup › Fixture position, Devices and sockets. Spec: See the reference coverage map below.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-U09-B01 | Keep socket and equipment placement, named wall references, tape inputs and saved-state feedback, restyled into the rail. | Mapped; evidence gate above |
| APT-U09-A01 | Every existing edit workflow still saves the same data. | Mapped; evidence gate above |

### APT-P01 — Camera dock states

Original: [#268](https://github.com/Engineered-Lighting/home/issues/268) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Perception source/player/view/dock contracts, mounted connected-view and full Lab harnesses. Producer inference and room qualification remain outside this UI batch.

Source seam: `home-apartment-den-perception.jsx, home-apartment-perception-*.js`. Prototype: `prototype/index.html?vp=1366x768&home=victoria&dock=default`, ui-dock.jsx. Spec: SPEC.md › Inspector and dock coexistence.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-P01-B01 | Collapsed, default, expanded and hidden, sharing the column with the inspector. | Mapped; evidence gate above |
| APT-P01-B02 | Echo and Victoria docks differ by `perception` capability, not by site. | Mapped; evidence gate above |
| APT-P01-B03 | Remove the live-feed full-viewport overlay and Victoria's separately styled perception panel. Collapsed shows a one-line summary; hidden leaves Show camera. | Mapped; evidence gate above |
| APT-P01-A01 | Dock never covers the inspector or shrinks the room to a corner. | Mapped; evidence gate above |

### APT-P02 — Exact-frame viewer

Original: [#269](https://github.com/Engineered-Lighting/home/issues/269) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Perception source/player/view/dock contracts, mounted connected-view and full Lab harnesses. Producer inference and room qualification remain outside this UI batch.

Source seam: `Cross-cutting quality gate`. Prototype: `prototype/index.html?vp=1366x768&home=victoria&dock=expanded`. Spec: handoffs/victoria-apartment-perception-view-contract-20261017.md; SPEC.md › Status semantics (frame ages); SPEC.md › Inspector and dock coexistence.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-P02-B01 | Analyzed frame with its own 2D skeleton and boxes; separate ages for live video and analyzed frame. | Mapped; evidence gate above |
| APT-P02-B02 | Replay is labeled with its recording date and scrubbable. Stale frames are dimmed and dashed and say "Not used for lighting". | Mapped; evidence gate above |
| APT-P02-A01 | An overlay never draws on a frame other than the one that produced it. | Mapped; evidence gate above |
| APT-P02-A02 | Acceptance example "Frame honesty" passes. | Mapped; evidence gate above |

### APT-P03 — Inference and JEV

Original: [#270](https://github.com/Engineered-Lighting/home/issues/270) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Perception source/player/view/dock contracts, mounted connected-view and full Lab harnesses. Producer inference and room qualification remain outside this UI batch.

Source seam: `Cross-cutting quality gate`. Prototype: ui-dock.jsx. Spec: SPEC.md › Inspector and dock coexistence.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-P03-B01 | Model identity, age, results; JEV activity distribution, abstention, evidence window and reasons when supplied. | Mapped; evidence gate above |
| APT-P03-B02 | Simple and Detailed levels (`?detail=`), following Perception Lab. Details shows exact payloads, timestamps, frame association and registration. | Mapped; evidence gate above |
| APT-P03-A01 | Detector confidence, pose scores and JEV probabilities are never merged into one gauge. | Mapped; evidence gate above |
| APT-P03-A02 | Acceptance example "JEV abstains" passes. | Mapped; evidence gate above |

### APT-P04 — Evidence to proposal link

Original: [#271](https://github.com/Engineered-Lighting/home/issues/271) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Perception source/player/view/dock contracts, mounted connected-view and full Lab harnesses. Producer inference and room qualification remain outside this UI batch.

Source seam: `Cross-cutting quality gate`. Prototype: Complete prototype. Spec: SPEC.md › Inspector and dock coexistence (Trace); HANDOFF.md › Data (proposal record).

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-P04-B01 | Perception-origin proposals show their evidence; selecting one highlights the fixture and target. | Mapped; evidence gate above |
| APT-P04-B02 | Trace: Seen → Interpreted → Proposed → Did, each a button. Proposed selects the light and proposes with origin `perception`. | Mapped; evidence gate above |
| APT-P04-A01 | "Why is it aiming there" is answerable in two clicks. | Mapped; evidence gate above |

### APT-P05 — Room-space body layer

Original: [#272](https://github.com/Engineered-Lighting/home/issues/272) (open at inspection).

Disposition: Partial/deferred: existing skeletal observations stay in scope; nominal body volume and body-shadow portions are owner deferred. Producer latency and actual-room registration are separately owned.

Evidence family: Perception source/player/view/dock contracts, mounted connected-view and full Lab harnesses. Producer inference and room qualification remain outside this UI batch.

Source seam: `Cross-cutting quality gate`. Prototype: Complete prototype. Spec: SPEC.md › Inspector and dock coexistence (Body in the room).

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-P05-B01 | Observed versus inferred joints, Follow where registration supports it. | Scope/gate above |
| APT-P05-B02 | Uncertainty halo; nominal volume labeled "not measured"; Follow disabled until continuous tracking exists. | Scope/gate above |
| APT-P05-A01 | Labels say "one analyzed frame" unless tracking is real. | Scope/gate above |

### APT-P06 — Perception Lab handoff

Original: [#273](https://github.com/Engineered-Lighting/home/issues/273) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: Perception source/player/view/dock contracts, mounted connected-view and full Lab harnesses. Producer inference and room qualification remain outside this UI batch.

Source seam: `home-perception-lab.jsx`. Prototype: Complete prototype. Spec: docs/design/perception-lab-v8/README.md.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-P06-B01 | Open in Lab, carrying session, time and selection where supported. | Mapped; evidence gate above |
| APT-P06-A01 | Lab opens on the same frame. | Mapped; evidence gate above |

### APT-V01 — Lit mode on the scan mesh

Original: [#274](https://github.com/Engineered-Lighting/home/issues/274) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: WebGL/WebGPU mesh, cloud, Photo, HDR and receiver proofs. Native capture calibration is a separate unresolved diagnostic; the owner said it must not hold up UI work.

Source seam: `Cross-cutting quality gate`. Prototype: scene.js `_litApply`, `_syncLit`. Spec: SPEC.md › Lit view; LIGHT-AND-BODY-RESEARCH.md.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-V01-B01 | Relight the existing mesh: lumen ratings to candela by beam angle, fixed exposure, spot crisp and down soft shadows. Cloud and splat stay unlit. | Mapped; evidence gate above |
| APT-V01-B02 | Later: IES files and lumen curves per channel (schema gap), WebGPU path per LIGHT-AND-BODY-RESEARCH.md. | Mapped; evidence gate above |
| APT-V01-A01 | Labeled not photometric. | Mapped; evidence gate above |
| APT-V01-A02 | Frame rate acceptable on the target laptop. | Mapped; evidence gate above |

### APT-V02 — One-bounce light

Original: [#275](https://github.com/Engineered-Lighting/home/issues/275) (open at inspection).

Disposition: Existing implementation credit retained; this ledger maps the clause to current local proof families. No individual clause is marked passed solely because a family or screenshot exists.

Evidence family: WebGL/WebGPU mesh, cloud, Photo, HDR and receiver proofs. Native capture calibration is a separate unresolved diagnostic; the owner said it must not hold up UI work.

Source seam: `Cross-cutting quality gate`. Prototype: scene.js VPL block. Spec: SPEC.md › Lit view.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-V02-B01 | Eight strongest lit patches become bounce lights. | Mapped; evidence gate above |
| APT-V02-A01 | Toward walls visibly lights the room. | Mapped; evidence gate above |

### APT-V03 — Body volume

Original: [#276](https://github.com/Engineered-Lighting/home/issues/276) (open at inspection).

Disposition: Owner deferred: body mesh, fitting and body occlusion are shelved. Skeletal tracking remains the accepted presentation. No implementation/acceptance claimed for these clauses.

Evidence family: WebGL/WebGPU mesh, cloud, Photo, HDR and receiver proofs. Native capture calibration is a separate unresolved diagnostic; the owner said it must not hold up UI work.

Source seam: `Cross-cutting quality gate`. Prototype: scene.js `_bodyCaps`, `_syncBodyVolume`. Spec: SPEC.md › Body volume.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-V03-B01 | Capsules and head sphere from pose joints; faint in Cloud/Mesh, shadow-casting in Lit. | Scope/gate above |
| APT-V03-B02 | Later: a fitted body-shape record per person (schema gap). | Scope/gate above |
| APT-V03-A01 | Inferred limbs are marked. | Scope/gate above |

### APT-V04 — Body occlusion in aiming

Original: [#277](https://github.com/Engineered-Lighting/home/issues/277) (open at inspection).

Disposition: Owner deferred: body mesh, fitting and body occlusion are shelved. Skeletal tracking remains the accepted presentation. No implementation/acceptance claimed for these clauses.

Evidence family: WebGL/WebGPU mesh, cloud, Photo, HDR and receiver proofs. Native capture calibration is a separate unresolved diagnostic; the owner said it must not hold up UI work.

Source seam: `Cross-cutting quality gate`. Prototype: scene.js `_bodyBlock`, ui-inspector.jsx `ShadowNote`. Spec: SPEC.md › Body volume; docs/plans/first-live-den-replan-2026-10-15/FURNITURE-BODY-LIGHTING-OCCLUSION.md.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-V04-B01 | Solver tests beams against the body; chip and bar name the part; orange hit mark; offer a light that clears it. | Scope/gate above |
| APT-V04-A01 | Uses the same body as Lit shadows. | Scope/gate above |

### APT-Q01 — Viewport matrix

Original: [#278](https://github.com/Engineered-Lighting/home/issues/278) (open at inspection).

Disposition: Evidence gate: current local matrix and full mapped receipts required. Actual devices, installed assets and live/physical behavior are not inferred from authored fixtures.

Evidence family: Dark-only preference/Previous Home proof, focus/keyboard/target audits, full mapped suite and reviewed viewport pairs. No actual Mac/iPhone acceptance.

Source seam: `Cross-cutting quality gate`. Prototype: Complete prototype. Spec: SPEC.md › Layout and responsive rules.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-Q01-B01 | Screenshots at 1920x1080, 1366x768, 768x1024, 390x844 for both homes. | Mapped; evidence gate above |
| APT-Q01-B02 | Run every acceptance example in HANDOFF.md at the end of the epic that owns it. | Mapped; evidence gate above |
| APT-Q01-A01 | No collisions between column, dock, toolbar and in-room controls. | Mapped; evidence gate above |

### APT-Q02 — Keyboard, touch and motion

Original: [#279](https://github.com/Engineered-Lighting/home/issues/279) (open at inspection).

Disposition: Evidence gate: current local matrix and full mapped receipts required. Actual devices, installed assets and live/physical behavior are not inferred from authored fixtures.

Evidence family: Dark-only preference/Previous Home proof, focus/keyboard/target audits, full mapped suite and reviewed viewport pairs. No actual Mac/iPhone acceptance.

Source seam: `Cross-cutting quality gate`. Prototype: Complete prototype. Spec: SPEC.md › Keyboard and touch.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-Q02-B01 | Focus order, 44 px targets, reduced motion disables easing and camera tweens. | Mapped; evidence gate above |
| APT-Q02-A01 | Every gesture has a non-drag alternative. | Mapped; evidence gate above |

### APT-Q03 — Light theme

Original: [#280](https://github.com/Engineered-Lighting/home/issues/280) (open at inspection).

Disposition: Superseded by owner decision: this release is dark only. Retained Light CSS is inaccessible from app settings; saved Light preferences normalize to Dark.

Evidence family: Dark-only preference/Previous Home proof, focus/keyboard/target audits, full mapped suite and reviewed viewport pairs. No actual Mac/iPhone acceptance.

Source seam: `Cross-cutting quality gate`. Prototype: Complete prototype. Spec: See the reference coverage map below.

| Clause | Original requirement | Disposition |
|---|---|---|
| APT-Q03-B01 | All new UI through tokens. | Scope/gate above |
| APT-Q03-A01 | Light theme screenshots for the main states. | Scope/gate above |

## Complete reference coverage map

The following unchanged map from the ZIP connects every SPEC section, prototype component, data gap, consolidation and HANDOFF example to the clause owners above. It is a reference map; its historical “known limits” are not current software acceptance.


Adversarial review of the tickets against SPEC.md, HANDOFF.md, the prototype and the original brief. Every row names the ticket that owns it. If something in the design has no row here, it's missing; add a ticket.

## SPEC.md sections
| Section | Tickets |
|---|---|
| Layout and responsive rules | APT-S04, APT-S05, APT-Q01 |
| Selection precedence | APT-R01, APT-R02, APT-R04 |
| Gestures | APT-R01, APT-A01, APT-Z02 |
| Aim lifecycle | APT-A01, APT-A02, APT-A04, APT-A05, APT-A07 |
| Quick controls | APT-R03, APT-L02, APT-L03 |
| Mapping a named aim | APT-U04, APT-U05 |
| Placing cameras and sensors | APT-U06, APT-U07 |
| Spot or zones (cylinders) | APT-A03, APT-A04 |
| Radial zones in the room (composer) | APT-Z01, APT-Z02, APT-Z03, APT-Z04 |
| Lit view | APT-V01, APT-V02 |
| Body volume | APT-V03, APT-V04, APT-P05 |
| Names | APT-U02 |
| Home Assistant links | APT-U03 |
| Home chat | APT-S04, APT-C01 to APT-C05 |
| Command ladder | APT-A06 |
| Inspector and dock coexistence | APT-P01 to APT-P05 |
| Cancel, Return and Escape | APT-F04, APT-A09, APT-S03 |
| Status semantics | APT-F02, APT-F06, APT-S02, APT-P02 |
| Keyboard and touch | APT-F03, APT-A08, APT-S06, APT-Q02 |
| What persists | APT-F05 |

## Prototype components
| Component | Tickets |
|---|---|
| TopBar, status rows | APT-S02 |
| SceneToolbar | APT-S03 |
| LayersPopover, Legend, HelpModal, AboutModal | APT-S06 |
| RoomInspector, QuickControls | APT-R02, APT-R03 |
| TargetInspector | APT-R04 |
| LightInspector, ProvenanceBadges, LightTab, ChannelCard, ToneSliders | APT-L01, APT-L02, APT-L04 |
| RadialRing | APT-L03 |
| AimTab, AimRow, TargetState | APT-A05, APT-A07 |
| CommandInspector | APT-A06 |
| ShadowNote | APT-V04 |
| SetupTab, SetupRail, SetupInspector | APT-U01 |
| NamesTool, HaLinks, TargetMapper, DevicePlacer | APT-U02, APT-U03, APT-U04, APT-U06, APT-U07 |
| PerceptionDock, VictoriaDock, EchoDock, DockHeader | APT-P01 |
| FrameOverlay | APT-P02 |
| DetailSeg | APT-P03 |
| Trace | APT-P04 |
| ChatPanel, ChatCard, ChatEnt | APT-C01 to APT-C05 |
| scene.js reticle, drag chip, snapping | APT-A01 |
| scene.js `_placeAimUI` (pill, aim bar) | APT-A03, APT-A04 |
| scene.js composer, `_fadeTick`, `zoneFacing` | APT-Z01 to APT-Z04 |
| scene.js `_placeRHandles`, `_rectDrag` | APT-U05 |
| scene.js `_drawDevices`, `coverage` | APT-U06, APT-U07 |
| scene.js `_syncLit`, VPL | APT-V01, APT-V02 |
| scene.js `_bodyCaps`, `_syncBodyVolume`, `_bodyBlock` | APT-V03, APT-V04 |
| Toast with Undo | APT-F03 |
| Scenario tweaks (draft, stale, held, replay, loading, failed) | APT-F06 |

## HANDOFF.md data gaps (schema changes need approval)
| Gap | Ticket |
|---|---|
| Per-site `capabilities` | APT-F05 |
| `analyzed_at` and held/stale thresholds | APT-F06 |
| Proposal record (perception → lighting) | APT-P04 |
| Execution receipt | APT-A06 |
| Target state and geometry hash | APT-A07 |
| Quick presets for modeled fixtures | APT-R03 |
| `radial_zone1_heading_deg` | APT-Z04, APT-U08 |
| IES files and lumen curves | APT-V01 (later) |
| Body-shape record | APT-V03 (later) |
| Name and label overrides | APT-U02 |
| Presence sensor record | APT-U07 |
| Hand-placed camera pose apart from extrinsics | APT-U06 |
| Automation reference for a named aim | APT-A10 |
| Composer command rate | APT-Z03 |

## Removals and consolidation
| Removal | Ticket |
|---|---|
| Close button; old menu items need a new home | APT-S01 |
| Anchored control card, `aim light` mode, Victoria bottom-left controls, debug stats behind Details | APT-L01 |
| Lighting target, live and dynamic panels, live room controls, fixture execution panel | APT-L01, APT-A06 |
| Live-feed full-viewport overlay, Victoria's separate perception panel | APT-P01 |
| `AIM_*` color literals, forced dark theme | APT-F02 |

## Acceptance examples (HANDOFF.md)
| Example | Ticket |
|---|---|
| Home isolation | APT-F05 |
| Direct selection | APT-R01 |
| Drag aim | APT-A01 |
| Unreachable | APT-A01 |
| Escape order | APT-F04 |
| Frame honesty | APT-P02 |
| JEV abstains | APT-P03 |
| No session | APT-F06 |
| HA offline (Echo) | APT-F06 |
| Photo missing | APT-S03 |
| Map a named aim | APT-U04 |
| Phone | APT-S05 |

## Corrections made in this review
- SPEC.md › Cancel, Return and Escape and the "Escape order" example still ended in "Close Apartment". Close is gone, so step 6 now does nothing.
- Added tickets for gaps with no owner: APT-F06 (data states and ages), APT-S06 (layers, legend, help, about), APT-R04 (named aim inspector), APT-A09 (Return to Auto), APT-A10 (automation reference), APT-U08 (zone heading commissioning), APT-U09 (fixture position and measurement).
- Folded removals, capability variants, persistence rules, validity reasons, undo keys, N/P cycling, Trace, Simple/Detailed, replay, uncertainty and Follow into the tickets that own them.
- Added spec references to every ticket and wired each acceptance example to a ticket.

## Known limits
- Victoria's ticket details come from the brief, not its source. APT-F05, APT-L02, APT-P02 and APT-P03 must check the real socket map, perception contract and JEV payload first.
- The composer has no keyboard path in the room; the ring panel is the alternative (APT-Z02).
