# A · Aiming

Point a spot at a real surface by dragging, then confirm.

Every ticket builds on the existing Apartment renderer. The prototype room is a placeholder.

### APT-A01 · Reticle drag on the scan mesh
**Depends on:** APT-F01, APT-L01
**Prototype:** `prototype/index.html?vp=1366x768&home=victoria&sel=vic-s4&tab=aim`
**Spec:** docs/APARTMENT-LIGHT-AIMING.md; SPEC.md › Gestures; SPEC.md › Aim lifecycle
**Likely files:** home-apartment-aiming.js, home-3d/*

**Build**
- Drag the reticle: socket stays fixed, pick on the mesh, drag chip shows surface, distance and tilt. Invalid, blocked and out-of-reach states.
- Snap to named aims nearby.
- Validity reasons as in SPEC (out of reach with angle and limit, blocked, grazing).

**Done when**
- [ ] Empty-canvas drag still orbits.
- [ ] Cancel restores the previous aim.
- [ ] Acceptance examples "Drag aim" and "Unreachable" pass.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-A02 · Aim tab opens on drag
**Depends on:** APT-A01
**Prototype:** app.jsx `onAim`
**Spec:** SPEC.md › Aim lifecycle

**Build**
- Starting a drag or keyboard nudge switches to Aim; on phones it raises the sheet to half. Chat stays open if it was open.

**Done when**
- [ ] Dropping always leaves Set aim reachable.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-A03 · Zones | Spot pill
**Depends on:** APT-A01, APT-Z02
**Prototype:** scene.js `_placeAimUI`, `.apt-toolpill`
**Spec:** SPEC.md › Spot or zones (cylinders)

**Build**
- Pill above a selected cylinder switches the in-room tool; tied to Light/Aim tabs; keys Z and A; cyan dot when an aim is pending.

**Done when**
- [ ] Pill hides during drags and never covers the reticle.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-A04 · Set aim bar in the room
**Depends on:** APT-A01
**Prototype:** scene.js `_placeAimUI`, `.apt-aimbar`
**Spec:** SPEC.md › Spot or zones (cylinders)

**Build**
- After a drop, a bar by the point: target name, shadow note, Set aim, Discard. Enter and Escape.

**Done when**
- [ ] Works with Chat open or the column hidden.
- [ ] Blocked shows only Discard.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-A05 · Aim tab content
**Depends on:** APT-A01
**Prototype:** ui-inspector.jsx `AimTab`
**Spec:** SPEC.md › Aim lifecycle

**Build**
- Proposal, recall chips for named aims, Save as named aim, undo and redo.
- ⌘Z and ⇧⌘Z for undo and redo; Escape discards a proposal and ⌘Z restores it.

**Done when**
- [ ] Matches the simplified prototype; no extra sections.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-A06 · Command ladder
**Depends on:** APT-A05
**Prototype:** ui-inspector.jsx command section
**Spec:** SPEC.md › Command ladder (Aim tab)
**Likely files:** home-apartment-aim.jsx

**Build**
- Intent, then modeled command, accepted execution and reported state. The exact generated payload on demand with copy. Use the canonical command builder; never a mock payload.
- Variants per `engineered` capability: simulated, modeled, commissioned. Execution receipts `{accepted_at, reported_state, source}` (schema gap). Fold execution and lighting panels into this ladder.

**Done when**
- [ ] Victoria reads modeled-only until commissioning.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-A07 · Named aim save states
**Depends on:** APT-A05
**Spec:** SPEC.md › Aim lifecycle; HANDOFF.md › Data (target state, geometry hash)

**Build**
- Unsaved, saving, saved, conflict, changed scan or profile.
- Resolve a conflict (keep mine, keep theirs) and confirm a target after the scan or profile changed.

**Done when**
- [ ] Each state appears with its copy from SPEC.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-A08 · Tap and keyboard aiming
**Depends on:** APT-A01
**Spec:** SPEC.md › Keyboard and touch

**Build**
- Tap-to-aim mode and arrow-key nudges as alternatives to dragging.

**Done when**
- [ ] Aiming is possible without a pointer drag.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-A09 · Return to Auto
**Depends on:** APT-L01
**Prototype:** app.jsx `returnToAuto`
**Spec:** SPEC.md › Cancel, Return and Escape

**Build**
- A light changed by hand shows Manual with Return to Auto, which hands it back to adaptive lighting.

**Done when**
- [ ] "Return to Auto", "Return to room" and "Close" never share wording.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-A10 · Named aim as an automation reference
**Depends on:** APT-A05, APT-U04
**Spec:** HANDOFF.md › Data (automation reference)

**Build**
- Show how a named aim is used by automations: its stable id, a copyable reference `aim {fixture_id} → {target_id}`, and which automations use it. Propose the reference format first (schema gap).

**Done when**
- [ ] Renaming an aim does not break the reference.
- [ ] Tests added in tools/, existing suites green, change note added
