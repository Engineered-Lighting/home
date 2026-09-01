---
title: Mobile touch targets and small-screen apartment editing
target: web
type: fixed
---

Controls that were 11-26px tall on phones now meet the 44px target: action
card undo/confirm/cancel and the details expander, all control-card chips
and transport buttons, People and world-state toolbars, the voice error
retry, proactive prompts, and the diagnostics close. Atlas chart points and
mini-map dots gained invisible hit halos (24px desktop / 44px mobile)
without growing visually, and the atlas close button is no longer hidden on
phones — it was undismissable under 760px. The metrics-lab history chart
gained a legend and full keyboard/touch access (focus or tap pins the
tooltip). The apartment editor is now genuinely usable on phones: its two
fixed side panels stack into a scrollable bottom sheet, the toolbar scrolls
horizontally, and edit buttons meet the touch minimum.
