---
title: Perception Lab reads Home Assistant through a scoped read-only source
target: backend
type: added
---

A new `perception_lab_source` Home Assistant custom component exposes only
allowlisted entity states through a read-only view guarded by its own secret,
so the Perception Lab no longer needs an HA user token that could call services.
The Lab HA source accepts an optional `"api": "perception_lab_source"` key to use
it; the default `"core"` keeps the existing `/api/states` behavior.
