---
title: Measure private nose candidates before person association
target: backend
type: added
---

Separate the existing retained stereo nose calculation from person-association
qualification. The pure private helper still requires independently assessed
static metric, camera, registration and clock inputs; it grants no person,
anatomy, geometry or beam authority and does not replace unknown-error assessment.
Existing operational selection continues to require its complete association.
