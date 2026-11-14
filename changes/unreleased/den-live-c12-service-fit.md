---
title: The den spotlight's sender fits the real c12 Home Assistant integration
target: internal
type: fixed
---

den-live's FC13 sender, its fake Home Assistant and the replay now match the `c12` integration as the firmware lane built it (c12-service-1), before anything is installed.
- **Velocity:** a move without velocity no longer sends `velocity_cdeg_s: [0, 0]`. The integration sets `has_velocity` whenever the field is given, which would have made the bytes on the air differ from the trace.
- **The integration's own answers:** `send_failed` is recorded as not sent, and `seq_unknown`, `dropped_expired` and `dropped_stopped` as refusals that release the row.
- **Stop with seq 0** now means "no seq", as the firmware's contract says from v2-b, in the replay and the desk report. The fake integration's count skips 0, and its Stop and Resume take `all: true`.
- **No stale Stop copies:** nothing reports the fixtures' attributes yet, so a latch that changed before den-live's own Resume is never copied into Home's Stop.
