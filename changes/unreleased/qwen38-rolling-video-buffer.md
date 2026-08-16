---
title: Plan a 48-hour rolling video buffer distilled nightly into observations
target: docs
type: added
---

Records owner decision D8: Frigate recording is enabled with a 48-hour
rolling window, and the VLM reads what accumulated each night between 03:00
and 08:00, writing durable observations before the footage ages out. This
reverses the earlier same-day recommendation to leave recording off, and it
unblocks the past-event describe, native-video, and retroactive-search
stories that had no video to work with.

The plan (capability roadmap, group G) is sized against measurements rather
than estimates where measurements exist:

- Parsing all stored video is not feasible. 48 hours across five cameras is
  roughly 13 million frames, while the five-hour window at half duty buys
  about 11,000. The lane is therefore event-anchored, with a low-rate
  ambient sweep for gaps and a hierarchical roll-up, which fits in about
  1.5 to 2 hours.
- Storage stays on the Home Assistant box, which already has 802 GB free.
  The AI box's spare NVMe is better used for the durable observation store
  than for video, and mounting it over the network would put continuous
  recording on a path known to corrupt Frigate segments.
- Retention must be confirmed by recording one camera for one hour and
  measuring, before the 48-hour number is committed.
- The nightly lane writes to a non-authoritative observation store, not to
  Home Agent memory, so the standing rule that the model never writes
  memory is preserved and promotion stays governed.
- Wiping footage at 48 hours no longer means the house forgets: the
  observations outlive it by design, so the retention question moves to the
  observation store, and the identity policy binds captions as well as
  tools.

Measured rather than estimated, after the owner brought two offline cameras
back online: all five streams run about 1.2 Mbps, so a 48-hour buffer is
121 GB (roughly 243 GB allowing for daylight and motion), against 802 GB
free. Storage is not the constraint; retention is a privacy question.

The nightly lane was re-scoped by the same measurement. Frigate emits more
than 10,000 events a day, but half are stationary furniture — 2,319 cup
detections in six hours — and merging the rest into distinct moments leaves
about 24. Parsing every event would need 233 minutes on the new model;
anchoring on people instead needs about 80, and the lane is defined that
way. Vehicles enter only as arrivals and departures, never as parked-car
persistence.

Also corrects two Phase-0 findings that were read from a stale, inactive
Frigate addon config: face recognition is enabled on the four indoor
cameras and correctly disabled on the driveway, and the camera roster has
no back_door. Adds tools/qwen38-frigate-summary.py, which reads the running
service's config rather than a file on disk, and points the Phase-0
collector at it.
