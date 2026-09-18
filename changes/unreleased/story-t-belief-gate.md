---
title: One TV predicate for Living Lights, with a belief fail-safe and a route light
target: backend
type: changed
---

The generated observability package now carries `binary_sensor.living_lights_tv_playing`,
the single "the TV is playing" predicate every classifier reads (the shared
`tools/living_lights_tv_states.py` vocabulary: `standby` counts as off), with a
90 s hold through the LG TV's `unavailable` blips, an instant release on an
explicit off, and the belief fail-safe built in: a live belief publisher may
keep the room dark through a blip or release an unattended TV, never while the
sofa is occupied, and a stale heartbeat (`binary_sensor.living_lights_publisher_fresh`,
180 s, never a classifier trigger) means the legacy rule. Every living-room
zone still goes dark while the TV plays (`living_lights_movie_dim_pct` now
defaults to 0); kitchen and dining zones get a new vacant floor
(`living_lights_tv_vacant_floor_pct`, default 0) and a route light while
occupied (`living_lights_tv_route_pct`, default 30, 8 overnight), released after
five minutes of dwell. The belief entities (`tv_watching`, `publisher_fresh`,
the 15 activity sensors) are classifier triggers. A generated
`living_lights_mqtt_mirror.yaml` republishes the TV, presence, the asleep
latch, the profile, the three toggles and the last command helpers as retained
local MQTT topics with a heartbeat, so the publisher needs no Home Assistant
token. Deploy with the story S set: regenerate, copy with `.bak.<ts>`,
`ha core check`, reload template and automation. Rollback: restore the
copies and reload; the new helpers keep their defaults.
