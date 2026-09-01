---
title: Restore keyboard focus visibility everywhere
target: web
type: fixed
---

Keyboard focus was invisible on the header controls, action menu, first-run
form, People panel fields, and every video-labeler control: inline style
resets (outline suppression and all-property unsets) were silently defeating
the focus ring at inline priority. The ring is now global for keyboard focus
(buttons, inputs, selects, links, sliders — including a Firefox slider ring
that never existed), all inline suppressions are removed, and a new test
harness (run-focus-visibility-tests) keeps them out. Ctrl+K now targets the
command input by its accessible name instead of a class selector that could
silently re-target.
