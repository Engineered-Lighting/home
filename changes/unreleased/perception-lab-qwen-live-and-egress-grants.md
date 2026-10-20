---
title: Perception Lab wires the continuous Qwen scene into JEV and tightens cloud egress
target: backend
type: added
---

The Lab controller can now run the continuous Qwen perceiver for each
recording (optional `qwen_live` block, managed controller only). Both arms get
the same closed-vocabulary scene record with a short code-built history. The
record is marked as an unvalidated, same-camera interpretation, and a new
corroboration block separates camera signals from independent device signals.
It never triggers a JEV call on its own and leaves only while the household
policy lists `lab-live-scene-1` in `structured_sources`. The recipe baseline
now grants the `qwen_scene` family to both arms. Its coverage stays
`not_provisioned` until the perceiver is configured.

The cloud packet no longer carries operator-chosen text in provenance.
Evidence roots, frame ids and capture epochs leave only as opaque digests.
`owner_home` and the new den device facts (`den_light`, `den_audio_playback`,
`computer_input_recency`) now need their own household grants:
`owner_presence: owner-presence-1` and `device_context: den-devices-1`.
Without a grant, a fact is dropped from that tick's packet and marked
`not_granted`, and dispatch continues instead of every packet being refused.
Coverage now reports `timezone_unavailable` when the site zone cannot load,
readiness reports `zone_loaded`, and HA coverage is `supplied` only when a bound
fact is actually sent. The Lab graph compares the arms' shared-context digests
only within one tick, and it shows the Qwen scene source.

The `perception_lab_source` HA component moves to 0.2.1. In area mode,
`sensor`, `select`, `input_select`, `input_number` and `number` entities are
admitted only by explicit `include`, never by area membership. This keeps
HASS.Agent's free-text sensors, such as the active window and logged-in user,
out. A listed Lab HA source (`entity_list: true`) now requires an explicit
`entities` list.
