---
title: A night excursion no longer energizes the house at dawn
target: backend
type: fixed
---

The wake-up latch (`living_lights_woke_up_on_wake` in
`living_lights_override_lifecycle.yaml`) used to fire when the asleep latch
cleared during the morning window. A 10-minute kitchen visit at 05:00 clears
that latch through the generated 10-minute occupancy rule, so the wake-up
latch set `woke_up_today` and the good-morning automation energized five
zones at 90 % while the person walked back to bed (simulation S12, made
visible by the 255-character override fix). The automation now triggers on
`binary_sensor.living_lights_any_occupied` being on for 15 minutes, with the
asleep latch off, `woke_up_today` off, `user_at_home` on and the 04:30-11:00
window unchanged; a 10-minute excursion never reaches the hold, and a real
wake keeps the house occupied past it. The failsafe and the override
lifecycle in the same package are untouched. Deploy: copy the package with a
`.bak.<ts>`, `ha core check`, reload automations. Rollback: restore the
`.bak.<ts>` copy and reload.
