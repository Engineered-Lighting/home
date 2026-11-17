# P · Perception and camera dock

See what Home sees and thinks, tied to what it proposes.

Every ticket builds on the existing Apartment renderer. The prototype room is a placeholder.

### APT-P01 · Camera dock states
**Depends on:** APT-S04
**Prototype:** `prototype/index.html?vp=1366x768&home=victoria&dock=default`, ui-dock.jsx
**Spec:** SPEC.md › Inspector and dock coexistence
**Likely files:** home-apartment-den-perception.jsx, home-apartment-perception-*.js

**Build**
- Collapsed, default, expanded and hidden, sharing the column with the inspector.
- Echo and Victoria docks differ by `perception` capability, not by site.
- Remove the live-feed full-viewport overlay and Victoria's separately styled perception panel. Collapsed shows a one-line summary; hidden leaves Show camera.

**Done when**
- [ ] Dock never covers the inspector or shrinks the room to a corner.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-P02 · Exact-frame viewer
**Depends on:** APT-P01
**Prototype:** `prototype/index.html?vp=1366x768&home=victoria&dock=expanded`
**Spec:** handoffs/victoria-apartment-perception-view-contract-20261017.md; SPEC.md › Status semantics (frame ages); SPEC.md › Inspector and dock coexistence

**Build**
- Analyzed frame with its own 2D skeleton and boxes; separate ages for live video and analyzed frame.
- Replay is labeled with its recording date and scrubbable. Stale frames are dimmed and dashed and say "Not used for lighting".

**Done when**
- [ ] An overlay never draws on a frame other than the one that produced it.
- [ ] Acceptance example "Frame honesty" passes.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-P03 · Inference and JEV
**Depends on:** APT-P02
**Prototype:** ui-dock.jsx
**Spec:** SPEC.md › Inspector and dock coexistence

**Build**
- Model identity, age, results; JEV activity distribution, abstention, evidence window and reasons when supplied.
- Simple and Detailed levels (`?detail=`), following Perception Lab. Details shows exact payloads, timestamps, frame association and registration.

**Done when**
- [ ] Detector confidence, pose scores and JEV probabilities are never merged into one gauge.
- [ ] Acceptance example "JEV abstains" passes.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-P04 · Evidence to proposal link
**Depends on:** APT-P03, APT-A05
**Spec:** SPEC.md › Inspector and dock coexistence (Trace); HANDOFF.md › Data (proposal record)

**Build**
- Perception-origin proposals show their evidence; selecting one highlights the fixture and target.
- Trace: Seen → Interpreted → Proposed → Did, each a button. Proposed selects the light and proposes with origin `perception`.

**Done when**
- [ ] "Why is it aiming there" is answerable in two clicks.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-P05 · Room-space body layer
**Depends on:** APT-P02
**Spec:** SPEC.md › Inspector and dock coexistence (Body in the room)

**Build**
- Observed versus inferred joints, Follow where registration supports it.
- Uncertainty halo; nominal volume labeled "not measured"; Follow disabled until continuous tracking exists.

**Done when**
- [ ] Labels say "one analyzed frame" unless tracking is real.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-P06 · Perception Lab handoff
**Depends on:** APT-P03
**Spec:** docs/design/perception-lab-v8/README.md
**Likely files:** home-perception-lab.jsx

**Build**
- Open in Lab, carrying session, time and selection where supported.

**Done when**
- [ ] Lab opens on the same frame.
- [ ] Tests added in tools/, existing suites green, change note added
