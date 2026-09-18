---
title: Offline simulation of the Living Lights packages
target: internal
type: added
---

`tools/lighting-sim/` runs the real Home Assistant packages inside a Home
Assistant core test instance with a fake clock and fake lights, driven by
scripted nights or nights reconstructed from the intelligence ledger. It
compares old and new packages on identical inputs and reports the asleep
latch timing, false latches, overnight turn-ons and what was lit at 03:00,
so lighting changes are validated before deploy instead of by waiting nights.
