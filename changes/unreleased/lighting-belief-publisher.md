---
title: Living Lights belief publisher runs the two lighting stories in shadow
target: backend
type: added
---

The AI stack gains `lighting-publisher` (`hav-lighting-publisher`, port 8105,
loopback only). It runs the television story and the asleep story as state
machines and publishes them to Home Assistant over MQTT discovery:
`binary_sensor.living_lights_tv_watching`,
`sensor.living_lights_asleep_estimator`, the per-zone
`sensor.<camera>_<zone>_activity` sensors and a
`sensor.lighting_publisher_heartbeat` every 60 seconds.

It holds no Home Assistant token, and there is nowhere to put one: Home
Assistant state reaches it only through the retained MQTT mirror, and
everything else comes from Frigate's person topics and the observer on the
LAN. Nothing leaves the house in this release.

It starts in shadow mode, where every entity carries a `_shadow` suffix that
no generated Home Assistant template reads, so the house keeps its existing
lighting behaviour while the nights are scored with `tools/shadow-report.py`.
When the mirror or the observer goes stale, or the broker drops, the heartbeat
stops and every belief is published as unknown, which is how Home Assistant
knows to fall back rather than to trust a guess. Deploy, verify and rollback
steps are in `docs/RUNBOOK.md`.
