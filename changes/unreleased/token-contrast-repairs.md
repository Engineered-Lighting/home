---
title: Repair color-token contrast and define the missing accent token
target: web
type: fixed
---

The --hg-accent token every accent control referenced was never defined — the
feature-load retry button rendered literally invisible on the error path, and
the Lights drawer was stuck on a hardcoded dark-theme blue in the paper theme.
It is now defined in both themes (ice-blue dark, ink-blue light). Accent-filled
controls switch to dark-on-accent text (white-on-accent was ~2.5:1). The light
"paper" theme's secondary text tokens were re-derived to actually pass AA
(fg-3 ~4.9:1, warn 5.5:1 — previously 2.96:1 and 3.38:1), dark fg-4 was raised
to 4.6:1, fg-5 is documented decorative-only, and the informative micro-text
that used it (entity IDs, legends, hints at 7-9px) moved to readable tokens
and a 10.5px floor.
