---
title: Give the den people stream seated, posture, a who slot and Frigate's occupancy for Home's caption
target: internal
type: added
---

Each marker in den-live's people stream (`den-live-people-1`) carries what Home's caption needs from perception.
- `seated`: true while the person is seated, from the same published seated the Qwen ask and the lighting preview read.
- `posture`: one word in Qwen's own words for posture, less `lying`, which perception doesn't measure: `sitting` while
  seated; else `walking` at 0.3 m/s or more over the last second; else `standing` with the hips above the seated band
  (or, without both hips, the head at 1.30 m or more); else `unclear`. It's on the person state too, and in its log.
- `who`: always null for now. The face-recognition work will fill it.
- The event's `capture_ts` was already there: the frame's capture time on the wall clock.
- Each event's `health.occupancy {effective, why}` is Frigate's own word for the den, from the occupancy sensor the Qwen
  ask reads (`on`, `off` or `unknown`), never Qwen's gate. Home's Frigate card reads it. Without the Qwen ask set
  up, den-live reads no Frigate and says so.
