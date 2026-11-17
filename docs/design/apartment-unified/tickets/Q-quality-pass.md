# Q · Quality pass

Run at the end of each epic, not only once.

Every ticket builds on the existing Apartment renderer. The prototype room is a placeholder.

### APT-Q01 · Viewport matrix
**Depends on:** none
**Spec:** SPEC.md › Layout and responsive rules

**Build**
- Screenshots at 1920x1080, 1366x768, 768x1024, 390x844 for both homes.
- Run every acceptance example in HANDOFF.md at the end of the epic that owns it.

**Done when**
- [ ] No collisions between column, dock, toolbar and in-room controls.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-Q02 · Keyboard, touch and motion
**Depends on:** none
**Spec:** SPEC.md › Keyboard and touch

**Build**
- Focus order, 44 px targets, reduced motion disables easing and camera tweens.

**Done when**
- [ ] Every gesture has a non-drag alternative.
- [ ] Tests added in tools/, existing suites green, change note added

### APT-Q03 · Light theme
**Depends on:** none

**Build**
- All new UI through tokens.

**Done when**
- [ ] Light theme screenshots for the main states.
- [ ] Tests added in tools/, existing suites green, change note added
