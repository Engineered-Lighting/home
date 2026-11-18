---
title: den-live's Qwen ask uses the newest second of pictures, and rides out one bad frame
target: backend
type: changed
---

den-live's Qwen ask now picks its stills newest first, from at most the last second before the newest frame, so the first question after someone opens a book shows the book, not the seconds before. A single stale or unsafe camera frame no longer resets the ask. It skips asking on that frame but keeps its stills, its earlier answer and any question in flight, until the person, the camera epoch or the gate really changes. The pace is unchanged: at most one question every 2 s and 30 a minute. A new cue-to-light instrument, `tools/den-live/cue_light.py`, and the ask check line up a cue, the first lit spot snapshot and Home's paint on the workstation's clock, in numbers only.
