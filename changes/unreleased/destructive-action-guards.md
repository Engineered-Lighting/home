---
title: Destructive actions now confirm, and most can be undone
target: web
type: changed
---

Ctrl+L (and /clear) no longer wipe the conversation instantly — they raise a
confirm card, and /undo-clear restores the conversation within the session;
on the web the browser keeps its own Ctrl+L. "Reset all to defaults" in the
Lights drawer, frame deletion in the Intelligence atlas, and person deletion
all use a two-click arm-and-confirm (the metrics-lab confirm helper was also
rebuilt — its old DOM-based arming silently disarmed on re-render). Labeler
review keys are undo-first, with arm-to-confirm only where the server has no
compensating call. Unsaved work is guarded on exit: spatial polygon drafts,
People edits, and in-progress apartment calibration all warn before
discarding. The external-key dialog no longer saves when Enter is pressed on
Cancel, zone naming uses a real inline input instead of a blockable native
prompt, geometry undo no longer hijacks Ctrl+Z inside text fields, keyboard
shortcuts no longer fire from inside text fields, and opening the apartment
view no longer maximizes your window.
