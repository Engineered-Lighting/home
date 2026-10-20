---
title: Perception Lab turns head direction into a camera cue for computer work
target: backend
type: added
---

In owner session `0dc34636`, JEV scored computer work correctly only 0–7% of the time. The monitor and hands were out of view, so PC input under 15 s never counted: there was no camera cue. The owner's head, though, was clearly turned toward the monitor.

The Lab now computes head direction in code (rule `head-direction-1`) from the pose's face keypoints and shoulders:

- `yaw`: `toward_image_left`, `toward_image_right`, `forward` or `unknown`.
- `pitch`: `down`, `level` or `unknown`.
- `support`: `both_ears`, `one_ear`, `eyes_only` or `insufficient`.

A new optional JEV provisioning key, `"monitor_side": "image_right"` (or `"image_left"`), maps these to `head_toward`: `monitor_side`, `lap_or_hands`, `elsewhere` or `unknown`.

Corroboration rule `corroboration-3` adds one camera cue: `head_toward` `monitor_side` with PC input `under_15s` counts the PC input for computer work. A book or phone that Qwen sees in hand is still a conflict.

The v6 packet carries these facts as words only:

- `now.head`, plus `now.head_toward` when `monitor_side` is set.
- A per-segment `head_toward` in the storyline (`mixed` when no word holds 80% of the segment's frames).

The policy re-derives all of these with its own `monitor_side`. The versions are now `den-semantics-7`, `structured-semantic-8` and `structured-facts-9`; the wire grant stays `den-facts-6`.

Replayed offline over `0dc34636`, PC input would count in 24 of the 59 computer-window requests, up from 10. That is 24 of the 30 requests where PC input was under 15 s. The other 29 had stale or older PC input. No request in the reading or phone windows read `monitor_side`.

To enable this at Victoria, add `"monitor_side": "image_right"` to the Lab JEV provisioning file. Nothing is deployed.
