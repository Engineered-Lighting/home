# R · Selection and room inspector

Clicking anything in the room selects it, with list and keyboard equivalents.

Every ticket builds on the existing Apartment renderer. The prototype room is a placeholder.

### APT-R01 · Selection model and labels
**Depends on:** APT-F01
**Prototype:** `prototype/index.html?vp=1366x768&home=victoria&sel=vic-s4`
**Spec:** SPEC.md › Selection precedence; SPEC.md › Gestures
**Likely files:** home-apartment.jsx, home-3d/markers.js

**Build**
- Click fixture or label selects; empty-canvas drag orbits; hover labels; selected emphasis; Escape deselects last.
- Keyboard: Tab through fixtures, Enter selects.
- N and P cycle through lights.

**Done when**
- [ ] Selection precedence matches SPEC.
- [ ] Orbit, select and aim gestures never conflict.
- [ ] Acceptance example "Direct selection" passes (without "offers Close").
- [ ] Tests added in tools/, existing suites green, change note added

### APT-R02 · Room inspector
**Depends on:** APT-R01, APT-F03
**Prototype:** `prototype/index.html?vp=1366x768&home=echo`
**Spec:** SPEC.md › Selection precedence
**Likely files:** home-apartment-cards.jsx

**Build**
- Lights grouped by room with a switch per room heading, named aims list with Edit.

**Done when**
- [ ] Clicking a row selects the same object as clicking it in the room.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-R03 · Quick controls
**Depends on:** APT-R02
**Prototype:** `prototype/index.html?vp=1366x768&home=victoria`, ui-inspector.jsx `QuickControls`
**Spec:** SPEC.md › Quick controls
**Likely files:** home-apartment-cards.jsx

**Build**
- All on, All off, scene and script chips from the home's HA scenes and scripts (automations marked ▶), each with Undo in the toast.

**Done when**
- [ ] Undo restores the exact previous state.
- [ ] Offline HA disables the chips with a reason.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-R04 · Named aim inspector
**Depends on:** APT-R01
**Prototype:** ui-inspector.jsx `TargetInspector`
**Spec:** SPEC.md › Selection precedence

**Build**
- Selecting a named aim in the room or list opens its inspector: which lights use it, which can reach it, Aim a light here, Edit in Setup.

**Done when**
- [ ] Same inspector from the room, the list and a chat chip.
- [ ] Tests added in tools/, existing suites green, change note added
