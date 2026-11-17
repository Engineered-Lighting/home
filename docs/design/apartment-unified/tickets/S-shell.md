# S · Shell

Apartment becomes Home's main screen with one layout for both homes.

Every ticket builds on the existing Apartment renderer. The prototype room is a placeholder.

### APT-S01 · Apartment is the default route
**Depends on:** APT-F01
**Prototype:** `prototype/index.html?vp=1366x768&home=victoria`
**Spec:** HANDOFF.md › Removal and consolidation
**Likely files:** app/src/home-app.jsx, home-apartment.jsx

**Build**
- Open on Apartment. Remove the Close button.
- Give Lab, metrics, people and anything else only reachable from the old menu a place in the top bar overflow or the slash menu.

**Done when**
- [ ] Every screen reachable before is still reachable, listed in the PR.
- [ ] Deep links to old routes still work.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-S02 · Top bar and home switcher
**Depends on:** APT-S01, APT-F05
**Prototype:** `prototype/index.html?vp=1366x768&home=echo`, ui-shell.jsx top bar
**Spec:** SPEC.md › Status semantics
**Likely files:** home-apartment.jsx

**Build**
- "Home" title, Echo Park / Victoria switcher, status summary with a popover that says what is connected.
- Switching keeps each home's unsaved work.

**Done when**
- [ ] Unavailable assets explain themselves and never silently switch homes.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-S03 · Room toolbar
**Depends on:** APT-F01
**Prototype:** `prototype/index.html?vp=1366x768`, ui-shell.jsx toolbar
**Spec:** SPEC.md › Status semantics (assets)
**Likely files:** home-apartment.jsx

**Build**
- Cloud, Photo, Mesh mapped to the existing modes (Lit arrives in APT-V01). Camera views, Return to room, help popover with shortcuts.
- Loading, progress and retry per mode.
- Camera views (room poses) with Return to room. Photo unavailable shows Retry and Back to Cloud.

**Done when**
- [ ] Toolbar sits in the same place in both homes and never covers the selected fixture.
- [ ] Acceptance example "Photo missing" passes.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-S04 · Right column: Controls and Chat tabs
**Depends on:** APT-S01
**Prototype:** `prototype/index.html?vp=1366x768&home=victoria&col=chat`
**Spec:** SPEC.md › Layout and responsive rules; SPEC.md › Home chat
**Likely files:** home-apartment.jsx

**Build**
- One right column with tabs Controls and Chat and an unread dot on Chat. Chat content arrives in APT-C01; ship with the existing chat mounted as is.

**Done when**
- [ ] Column width and room fit follow SPEC layout rules at 1920, 1366 and 1024 wide.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-S05 · Tablet and phone layouts
**Depends on:** APT-S04
**Prototype:** `?vp=768x1024`, `?vp=390x844`
**Spec:** SPEC.md › Layout and responsive rules; SPEC.md › Keyboard and touch
**Likely files:** home-apartment.jsx, CSS

**Build**
- Phone: bottom sheet (peek, half, full) with Lights, Camera, Chat tabs. Tablet per SPEC.
- Safe areas, scroll ownership, no panel collisions.

**Done when**
- [ ] Screenshots at 390x844, 768x1024, 1366x768 match the prototype's layout.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-S06 · Layers, legend, help and about
**Depends on:** APT-S03
**Prototype:** ui-shell.jsx `LayersPopover`, `Legend`, `HelpModal`, `AboutModal`
**Spec:** SPEC.md › Keyboard and touch
**Likely files:** home-apartment.jsx

**Build**
- Layers popover: pools, beams, named aims always, labels always, cameras, body observed, body inferred, body volume, uncertainty. Unavailable layers explain why.
- Legend for in-room marks, shown on demand.
- Help modal listing every shortcut (A, Z, Enter, N/P, ⌘Z, Esc, ?).
- About: what is live, modeled, authored or recorded in this home.

**Done when**
- [ ] Layer choices persist across homes.
- [ ] Help lists exactly the shortcuts that work.
- [ ] Tests added in tools/, existing suites green, change note added
