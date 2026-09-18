---
title: Arrivals and the ambient strips respect a film in progress
target: backend
type: changed
---

The ambient strips and the return-home scene now read the shared
`binary_sensor.living_lights_tv_playing` (one predicate for every package,
with the unavailable-blip delay and the belief fail-safe inside it) instead
of testing `media_player.lg_tv`'s raw state, which reads unavailable when the
TV is off and blips unavailable while it is on. The return-home scene adds a
template condition on that sensor so a 90-second presence backstop during a
film no longer relights the living room; the asleep and travel-mode gates
stay. The morning wake-up latch that fires when the asleep latch clears now
also requires the house to have been occupied for two minutes, so a backstop
or presence-jitter clear with nobody up does not trigger the greeting and
energize. Deploy: copy `living_lights_ambient.yaml`,
`living_lights_override_lifecycle.yaml` and `homeai_proactive.yaml` with a
`.bak.<ts>` of each after the observability package that defines the sensor,
`ha core check`, reload automations and scripts. Rollback: restore the
`.bak.<ts>` copies and reload.
