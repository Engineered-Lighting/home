---
title: Frigate images reach the People tab only through exact, authenticated routes
target: backend
type: fixed
---

Home Assistant's Frigate proxy no longer forwards any path that merely starts
with `api/faces` or `api/events`, which let `api/events/../config` read
Frigate's configuration and its camera credentials. It now serves four exact
routes for a person's sightings, event thumbnails, the face library and
enrolled face images. It checks every name against the face library, returns
only JSON or raster images, never follows redirects, and never tells the
browser Frigate's address. `agent_profiles` no longer returns `frigate_url`.
The old `frigate_proxy?path=` form now answers 404.
