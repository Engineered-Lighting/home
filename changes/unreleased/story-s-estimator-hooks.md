---
title: Overnight asleep latch gets credibility checks, a hard backstop and estimator hooks
target: backend
type: changed
---

The legacy asleep ON and OFF automations are gated on the estimator not being
live (both toggles on, a real estimator state, a fresh publisher heartbeat),
so a future belief publisher can own the latch through the new
`living_lights_asleep_mirror` automation without a package change. The OFF
triggers carry ids: the midday clear now needs the house occupied for two
minutes and a presence reconnect only clears the latch when a person was seen
at the front door or in the living room in the last minute (a 03:00 phone
reconnect no longer lights the house), and a fourth trigger `arrival` clears
it when the front-door camera sees a person within a minute of the phone
reconnecting, so a real arrival counts whichever of the two reports first.
An ungated hard backstop clears the
latch after thirty minutes of occupancy from 06:00. Every writer records its
name in `input_text.living_lights_asleep_writer`. New helpers
`living_lights_asleep_from_estimator` and `living_lights_typesafe_egress_enabled`
default off and reset to off on every restart. Same deploy and rollback as
the story T change; nothing changes until the toggles are turned on.
