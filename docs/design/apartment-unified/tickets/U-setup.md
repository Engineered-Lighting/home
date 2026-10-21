# U · Setup

Everything about placing and naming things, kept apart from everyday controls.

Every ticket builds on the existing Apartment renderer. The prototype room is a placeholder.

### APT-U01 · Setup mode and rail
**Depends on:** APT-L01, APT-F04
**Prototype:** ui-shell.jsx `SetupInspector`
**Spec:** HANDOFF.md › Migration stages (Setup rail)
**Likely files:** home-apartment-edit.jsx, home-apartment-calibrate.jsx

**Build**
- Setup rail: Names, Named aims, Cameras and sensors, Home Assistant, plus the existing socket and equipment placement, wall references, tape inputs and measure.
- Rail items: Devices and sockets, Names, Named aims, Zones, Fixture position, Home Assistant, Cameras and sensors. Calibration moves here.

**Done when**
- [ ] Existing edit and measure features keep working and saving.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-U02 · Names
**Depends on:** APT-U01
**Prototype:** ui-shell.jsx `NamesTool`
**Spec:** SPEC.md › Names (Setup › Names)

**Build**
- Name and Label per light, live edit, Reset, duplicate warning. Stored with the Apartment model (schema gap).

**Done when**
- [ ] Names never change HA entity names.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-U03 · Home Assistant links
**Depends on:** APT-U01
**Prototype:** ui-shell.jsx `HaLinks`
**Spec:** SPEC.md › Home Assistant links

**Build**
- Match each light to its HA light, Linked/Shared/Not linked badge, Flash.

**Done when**
- [ ] Flash is the only output here and respects Travel Mode.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-U04 · Named aim mapper
**Depends on:** APT-U01, APT-F01
**Prototype:** ui-shell.jsx `TargetMapper`
**Spec:** SPEC.md › Mapping a named aim

**Build**
- New, click a surface, auto-fit, name, type checks, shape, reach list, preview from a light, save, edit, delete.

**Done when**
- [ ] Saved aims appear in every light's Aim tab.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-U05 · Named aim handles
**Depends on:** APT-U04
**Prototype:** scene.js `_placeRHandles`, `_rectDrag`
**Spec:** SPEC.md › Mapping a named aim

**Build**
- Corner resize, center move, height arrow on flat surfaces, floor drop line.

**Done when**
- [ ] Handles stay on the real surface plane.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-U06 · Camera placement
**Depends on:** APT-U01
**Prototype:** ui-shell.jsx `DevicePlacer`
**Spec:** SPEC.md › Placing cameras and sensors

**Build**
- Mount, click to place, point at a spot, pan/tilt/lens/height, coverage frustum, Covers list. Hand-placed pose kept apart from calibration (schema gap).

**Done when**
- [ ] Placing never overwrites calibrated extrinsics.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-U07 · mmWave sensors
**Depends on:** APT-U06
**Spec:** SPEC.md › Placing cameras and sensors; HANDOFF.md › Data (presence sensor)

**Build**
- Same placer with field and range; "Ordered, not connected" until paired (schema gap).

**Done when**
- [ ] No sensor appears connected without HA entities.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-U08 · Zone heading commissioning
**Depends on:** APT-U01, APT-Z04
**Prototype:** Setup › Zones
**Spec:** HANDOFF.md › Data (radial_zone1_heading_deg)

**Build**
- Blink zone 1 on the real fixture, then click where its light lands; store the heading. Until then the heading reads Authored.

**Done when**
- [ ] Composer and ring turn to the stored heading.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-U09 · Fixture position and measurement
**Depends on:** APT-U01
**Prototype:** Setup › Fixture position, Devices and sockets
**Likely files:** home-apartment-edit.jsx, home-equipment-registration.js

**Build**
- Keep socket and equipment placement, named wall references, tape inputs and saved-state feedback, restyled into the rail.

**Done when**
- [ ] Every existing edit workflow still saves the same data.
- [ ] Tests added in tools/, existing suites green, change note added
