---
title: Perception Lab derives computer activity from the PC's lock state and last input
target: backend
type: added
---

A Perception Lab HA source file can now declare an optional `derived` rule for
`computer_active`. It reads two allowlisted entities, such as HASS.Agent's session
state and last-active time. Each HA snapshot then records a locally derived
`derived.computer_active` fact:

- `on` when the session is one of the configured unlocked values and the last
  keyboard or mouse input is no more than `max_idle_seconds` (30–3600) before the
  snapshot.
- `off` when the session is known and not unlocked, or when idle time is past
  that bound.
- `unknown` when either input is unknown, unavailable or not a timestamp with an
  offset, or when the last input is more than 5 s in the future.

The JEV config can bind it as `"derived.computer_active": "computer_active"`, and
startup rejects the binding unless the HA source file declares the rule. Only the
`on`/`off`/`unknown` value reaches the cloud packet. The raw session state,
timestamp, entity ids and rule details stay in the local session ledger. The fact
is independent of the camera. It never counts as camera-derived and never extends
visual or HA freshness.
