# V · Lit view and body

Simulated light with shadows and the body as an occluder.

Every ticket builds on the existing Apartment renderer. The prototype room is a placeholder.

### APT-V01 · Lit mode on the scan mesh
**Depends on:** APT-S03
**Prototype:** scene.js `_litApply`, `_syncLit`
**Spec:** SPEC.md › Lit view; LIGHT-AND-BODY-RESEARCH.md

**Build**
- Relight the existing mesh: lumen ratings to candela by beam angle, fixed exposure, spot crisp and down soft shadows. Cloud and splat stay unlit.
- Later: IES files and lumen curves per channel (schema gap), WebGPU path per LIGHT-AND-BODY-RESEARCH.md.

**Done when**
- [ ] Labeled not photometric.
- [ ] Frame rate acceptable on the target laptop.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-V02 · One-bounce light
**Depends on:** APT-V01
**Prototype:** scene.js VPL block
**Spec:** SPEC.md › Lit view

**Build**
- Eight strongest lit patches become bounce lights.

**Done when**
- [ ] Toward walls visibly lights the room.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-V03 · Body volume
**Depends on:** APT-P05
**Prototype:** scene.js `_bodyCaps`, `_syncBodyVolume`
**Spec:** SPEC.md › Body volume

**Build**
- Capsules and head sphere from pose joints; faint in Cloud/Mesh, shadow-casting in Lit.
- Later: a fitted body-shape record per person (schema gap).

**Done when**
- [ ] Inferred limbs are marked.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-V04 · Body occlusion in aiming
**Depends on:** APT-V03, APT-A04
**Prototype:** scene.js `_bodyBlock`, ui-inspector.jsx `ShadowNote`
**Spec:** SPEC.md › Body volume; docs/plans/first-live-den-replan-2026-10-15/FURNITURE-BODY-LIGHTING-OCCLUSION.md

**Build**
- Solver tests beams against the body; chip and bar name the part; orange hit mark; offer a light that clears it.

**Done when**
- [ ] Uses the same body as Lit shadows.
- [ ] Tests added in tools/, existing suites green, change note added
