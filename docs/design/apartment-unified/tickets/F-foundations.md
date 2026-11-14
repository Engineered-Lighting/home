# F · Foundations

Shared pieces every later ticket uses. Nothing user-visible changes yet.

Every ticket builds on the existing Apartment renderer. The prototype room is a placeholder.

### APT-F01 · Renderer overlay bridge
**Depends on:** none
**Prototype:** scene.js: `_screen`, `_placeLabels`, `gDyn`, `surfaceHit`, `checkAim`
**Spec:** HANDOFF.md › The room view in the prototype is a placeholder
**Likely files:** app/src/home-3d/*, app/src/home-apartment.jsx

**Build**
- A small API on the existing renderer for: projecting world points to screen, a DOM layer for labels and handles, a scene group for transient overlay geometry, and ray picks against the real scan mesh (reuse the target-drag picking).
- No change to point cloud, splat, mesh, wall-fade, orbit or asset loading.

**Done when**
- [ ] A test overlay (a dot plus a label at a fixture) tracks correctly while orbiting in Cloud, Photo and Mesh.
- [ ] A pick returns point, normal and surface name on the scan mesh.
- [ ] Renderer frame time unchanged within noise.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-F02 · Status vocabulary and tokens
**Depends on:** none
**Prototype:** app.css (`--apt-*` tokens, `.apt-badge`), ui-kit.jsx `Badge`
**Spec:** SPEC.md status semantics; DIRECTION.md
**Likely files:** app/src/home-tokens.css

**Build**
- Map the prototype's state colors (ok, attention, blocked, authored, proposed, none) onto existing `--hg-*` tokens, dark and light theme.
- One Badge component with the agreed labels (Live, Held, Stale, Recorded, Authored, Proposed, Saved, Blocked, Reported).
- Move `AIM_*` color literals into tokens. Remove the forced `data-theme="dark"`: the canvas stays dark and the chrome follows the theme.

**Done when**
- [ ] Badges meet 4.5:1 text contrast in both themes.
- [ ] `--hg-fg-5` is never used for informative text.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-F03 · UI primitives
**Depends on:** APT-F02
**Prototype:** ui-kit.jsx (`Btn`, `Seg`, `Slider`, `Switch`, `Icon`, `Popover`), toast in app.jsx
**Likely files:** app/src/home-apartment-cards.jsx or a new home-apartment-ui.jsx

**Build**
- Reuse existing Home controls where they exist; add only what is missing.
- Toast supports an action button (Undo) and a timeout.

**Done when**
- [ ] Each primitive is keyboard operable with visible focus.
- [ ] Touch targets are at least 44 px on coarse pointers.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-F04 · Escape and focus stack
**Depends on:** none
**Prototype:** app.jsx keydown handler
**Spec:** SPEC.md Escape rules; SPEC.md › Cancel, Return and Escape
**Likely files:** app/src/home-overlay.js

**Build**
- Escape cancels the innermost operation first: drag, tap-aim, draft, popover, setup tool, then selection. It never closes Apartment.
- Focus returns to the control that started the operation.
- With Close gone, step 6 of the ladder does nothing. Popovers, modals and text fields claim Escape first.

**Done when**
- [ ] A table test covers each level of the stack.
- [ ] The ladder matches SPEC.md › Cancel, Return and Escape, step by step.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-F05 · Per-home capability model
**Depends on:** none
**Prototype:** data-sites.js (`capabilities`)
**Spec:** HANDOFF.md data gaps; SPEC.md › What persists; HANDOFF.md › Capability-driven variants
**Likely files:** app/src/home-site-registry.js, home-apartment-site-session.js

**Build**
- Propose a capabilities object per home (HA connection, engineered fixtures, perception, JEV, cameras) from existing registry data. Schema change goes in the PR description first.
- Unsaved work and selection are kept per home.
- Variants are driven by the capability values in HANDOFF.md (engineered, perception, body, lights, assets, bench). Components never branch on the site id.
- Persistence per SPEC › What persists: theme, layers, dock and detail across homes; selection, tab, proposals with undo, drafts, camera choice per home; orbit refits on switch.

**Done when**
- [ ] Switching homes never shows another home's devices, feed or saved aims.
- [ ] Acceptance example "Home isolation" in HANDOFF.md passes.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-F06 · Data states and ages
**Depends on:** APT-F02, APT-F05
**Prototype:** `?echo=` and `?vic=` scenarios: draft, stale, held, replay, loading, failed; Tweaks panel
**Spec:** SPEC.md › Status semantics; HANDOFF.md › Data (analyzed_at, thresholds)
**Likely files:** home-apartment-site-session.js

**Build**
- One place that turns raw data into states: HA connected or offline (with last reported time), local draft, loading, failed, and frame ages current (≤10 s), held (≤2 min), stale, expired, no session, replay.
- Every surface reads these states; none computes its own.

**Done when**
- [ ] "Connected" always names the system.
- [ ] Acceptance examples "HA offline (Echo)" and "No session" pass.
- [ ] Tests added in tools/, existing suites green, change note added
