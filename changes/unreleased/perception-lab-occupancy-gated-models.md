---
title: Run the Lab's Qwen and V-JEPA pumps only while Frigate sees a person
target: backend
type: changed
---

The Perception Lab's continuous Qwen perceiver and V-JEPA clip pumps now send
work only while the camera-derived Frigate person-occupancy sensor
(`binary_sensor.den_oak_person_occupancy` in Victoria) reads occupied. The gate
reads the recording's own `ha.observation` rows with one bounded, indexed query
and makes no new Home Assistant calls. It ignores rows that are not yet
available and treats unknown or stale readings as closed. A 10 s hold keeps
readings flowing when occupancy flickers off while someone sits still.

A skipped tick sends no frames and no LA request. It reports `no_person` or
`occupancy_unknown`, and each pump's live status shows the gate state and skip
counts. A pump refuses to run, with `occupancy_gate_not_provisioned`, when the
recording has no HA source or no bound camera-derived occupancy entity.

The optional `qwen_live` keys are `occupancy_entity`, `occupancy_hold_seconds`
(0–30, default 10) and `occupancy_grace_seconds` (0–10, default 5).
