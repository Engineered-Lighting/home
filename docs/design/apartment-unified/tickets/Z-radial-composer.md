# Z · Radial composer

Set the six radial zones with one continuous gesture in the room.

Every ticket builds on the existing Apartment renderer. The prototype room is a placeholder.

### APT-Z01 · Composer drawing and abstract fixtures
**Depends on:** APT-F01
**Prototype:** `prototype/index.html?vp=1366x768&home=victoria&sel=vic-s4&tab=light`, scene.js `_drawZones`
**Spec:** SPEC.md › Radial zones in the room (composer)

**Build**
- Soft polar glow at fixture height, edge line, node per lit zone, ticks on the outer ring. Fixtures drawn as small orbs tinted by the channels that are on.

**Done when**
- [ ] Readable from any orbit angle in Cloud, Photo and Mesh.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-Z02 · Composer gesture
**Depends on:** APT-Z01
**Prototype:** scene.js `_composeStartable`, `_composeMove`, `_endCompose`

**Build**
- Press on hub or lit lobe, drag outward to brighten and inward to dim, sweep interpolation, pickup without jumps, zone readout by the cursor.

**Done when**
- [ ] Orbiting works outside hub and lobes.
- [ ] Pinch and second pointer cancel cleanly.
- [ ] The ring panel (APT-L03) is the non-drag alternative for every composer change.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-Z03 · Fades and command rate
**Depends on:** APT-Z02
**Prototype:** scene.js `_fadeTick`

**Build**
- Display eases with a 160 ms time constant. Commands go out at most every 100 ms with the driver's transition covering the gap.
- Reduced motion: no easing, values snap.

**Done when**
- [ ] No visible jump entering a brighter or dimmer zone.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-Z04 · Zone facing
**Depends on:** APT-F01
**Prototype:** scene.js `zoneFacing`
**Spec:** HANDOFF.md › Data (radial_zone1_heading_deg)

**Build**
- Ray cast each zone direction against the scan mesh to name what it faces. Uses the per-fixture zone-1 heading (schema gap).

**Done when**
- [ ] Toward walls lights the zones with a wall within 2.2 m.
- [ ] Tests added in tools/, existing suites green, change note added
