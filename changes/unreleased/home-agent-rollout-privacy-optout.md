---
title: Preserve privacy opt-out during Home Agent rollback
target: backend
type: changed
---

Makes rollout mode independent authority for precise-location retention, visit
projection, and direct visit creation. Record-only and shadow now suppress
those capabilities even if an enabled preference survived rollback, while an
authenticated subject can still inspect the two stored preference booleans and
disable them from browser or native Agent surfaces. Contained clients discard
all other snapshot content, expose no enable controls, and use direction-fixed
opt-out requests.
