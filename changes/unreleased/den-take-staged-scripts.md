---
title: Den take recorder plays N1's staged scripts
target: internal
type: added
---

`tools/den-take/take.py --script` also takes a staged script from
`tools/den-take/scripts/` (`hard-a` or `hard-b`, format `den-take-script-1`).
It speaks each cue at its time after "Begin.", logs it in `take.json` as a
`stage` cue with its case, starts the end slate at the script's `end_s`, and
records the script file's sha256. Enter still ends a take early; check.py is
unchanged.
