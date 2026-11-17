# L · Light inspector

One inspector for every light, with tabs Light, Aim and Setup.

Every ticket builds on the existing Apartment renderer. The prototype room is a placeholder.

### APT-L01 · Inspector frame
**Depends on:** APT-R01, APT-F03
**Prototype:** `prototype/index.html?vp=1366x768&home=victoria&sel=vic-s4&tab=light`
**Spec:** HANDOFF.md › Removal and consolidation; HANDOFF.md › Shared component map
**Likely files:** home-apartment-aim.jsx (merge into it)

**Build**
- Header, status badge, Light / Aim / Setup tabs, and a line saying what a change sends.
- Consolidate home-lighting-target-panel, -live-panel, -dynamic-panel, home-live-room-controls and home-fixture-execution-panel into this inspector. List what each one became in the PR.
- Provenance badges in the header (live, modeled, authored).
- Retire the anchored control card and the `aim light` mode and its button. Remove Victoria's bottom-left fixture controls. Move debug statistics behind Details.

**Done when**
- [ ] No duplicate light controls remain on screen in either home.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-L02 · Cylinder channels: Spot, Down, Radial
**Depends on:** APT-L01
**Prototype:** `prototype/index.html?vp=1366x768&home=victoria&sel=vic-s4&tab=light`
**Spec:** HANDOFF.md › Capability-driven variants (lights)
**Likely files:** home-apartment-aim.jsx

**Build**
- Spot and Down each have brightness and color temperature. Radial has a level and color temperature. Per-fixture All on and All off.

**Done when**
- [ ] Each channel maps to its own existing entity or command.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-L03 · Radial ring panel
**Depends on:** APT-L02, APT-Z04
**Prototype:** ui-inspector.jsx `RadialRing`
**Likely files:** home-apartment-aim.jsx

**Build**
- Plan view, north up, turned to the zone-1 heading. Off/25/50/100 presets, Toward walls, Into the room, Show in room.

**Done when**
- [ ] Selecting a zone names what it faces.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-L04 · Echo lights
**Depends on:** APT-L01
**Prototype:** `prototype/index.html?vp=1366x768&home=echo`
**Likely files:** home-apartment-aim.jsx

**Build**
- Ceiling and plug lights use the same inspector with their available controls and HA entity line.

**Done when**
- [ ] Echo's existing engineered-light controls keep working.
- [ ] Tests added in tools/, existing suites green, change note added
