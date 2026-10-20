---
title: Den desk preview can run under an explicit development timing policy
target: backend
type: added
---

The virtual den desk preview can now be switched, in its private v2 policy, to
the owner's "experience first" development timing. Under it, a late camera
original or model result on the arm-B preview path no longer cancels the
preview. Each late stage is recorded as a named warning, from capture through
dispatch, finalize and serving, and the preview stays available inside its
own 10 s window instead of the 2 s display receipt. The development policy
also skips the per-frame mount check for the preview; camera identity, epoch
and calibration checks still apply. Each preview also reports whether the latest arm-B output
supports, contradicts or breaks the integrity of the held aim. The analyzed
image stays fetchable long enough for Home to show it next to a held aim.
Strict timing remains the default and is unchanged for arm A, ordinary arm-B
evidence, reasoning and the qualified lane. Physical output stays disabled.
No LA worker update is needed: the controller gives only the preview's arm-B
request a longer dispatch deadline and keeps the original deadline for
everything it reports. A low-confidence duplicate box over the same person no
longer counts as a second person in the preview.
