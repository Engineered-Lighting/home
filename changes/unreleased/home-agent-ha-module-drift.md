---
title: Deploy and verify every module step 20 runs on the Home Assistant host
target: backend
type: fixed
---

Step 19 stops Home Assistant and step 20 then runs the legacy-identity freeze
and its observation on the Home Assistant host. Three modules have to be there
and byte-identical to the pinned source: the source-projection loader, the
freeze, and the observer.

Only the loader was ever installed or verified. The other two were checked for
presence and nothing else — and presence is exactly what a stale copy also
satisfies. The freeze observer on the Home Assistant host had gone three
revisions behind its pinned source: it still shelled out to `ha core info` for
a run-state key this deployment does not return, so it raised unconditionally,
including when Home Assistant genuinely was stopped. The repaired version
landed in the repository but nothing carried it onward, and a readiness audit
recorded the file as "present".

Both halves of step 20 execute after Home Assistant is already down, so this
would have failed at the worst possible point.

The installer now deploys all three through one digest-verified helper, and the
runner verifies all three before authorization rather than hashing a single
hard-coded module. A contract test cross-checks the two lists, so a module the
runner verifies but the installer never copies fails at review time instead of
mid-ceremony. Verified non-vacuous: restoring the original single-module
installer makes it fail, naming the observer.
