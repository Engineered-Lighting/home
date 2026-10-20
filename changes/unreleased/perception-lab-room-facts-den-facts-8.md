---
title: Perception Lab turns the den room map into room facts for JEV (den-facts-8, off until switched on)
target: backend
type: added
---

The Perception Lab can now compute placement-agnostic room facts from the saved den site map. They describe the room,
never an activity, and a new JEV wire, `den-facts-8`, carries them. Nothing changes until the operator enables the map
and the owner grants the wire.

- **Room facts (rule `room-facts-1`).** Each arm's person box and COCO17 keypoints are combined with the camera's
  stereo depth and the map:
  - `position`: the room zone the person is in (`desk_chair`, `armchair`, `sofa`, `doorway` and so on);
  - `looking_at` with its stability: where the head points in the room (head pose, not eye gaze);
  - `hands_near`: map targets and detected objects within 15 cm of a wrist;
  - `posture`: sitting, standing or lying, from 3D joint heights;
  - `motion`: still, small or moving, from torso displacement over about 3 s.

  Only fixed category words leave the Jetson. No coordinates, distances, anchor or zone names, or labels do.
- **Shared baseline.** With `room_facts` in the live config, placement recipes sample the camera's depth bundle at 2 Hz.
  The sample is identical for both arms and is never an experiment lever. Each paired detection writes one
  `room.facts` ledger event.
  - The controller fails closed at startup on a map that does not bind or is not registered.
  - It stops the facts for a recording when the camera calibration differs from the map's or the camera has moved.
  - Measured on the workstation: loopback fetch about 7 ms and depth decode about 3 ms per 787 KB bundle, and
    20–45 ms of facts per frame.
- **`den-facts-8`.** It is `den-facts-6` plus:
  - the room words under `now`;
  - a `room_storyline` of position and looking_at changes;
  - `room_line` and `room_conflicts` in `support`, from a table that maps each fact to the activities it supports or
    conflicts with. For example, looking at the monitor or hands near the keyboard supports computer work, and looking
    at the TV supports watching.

  Without a current 3D fact, the desk-seat image rule is the position fallback, and `position_method` records which
  rule placed the person.
  - It has its own household grant, registered schema digest and projection `structured-facts-11`.
  - The egress policy re-derives the `room.facts` record from the ledger.
  - `den-facts-6` and `den-facts-7` are unchanged and stay selectable.
  - Without a map, everything behaves exactly as before.

Switch-on steps: `docs/DEN-ROOM-MAPPING-RUNBOOK.md` section 7. Nothing is deployed.
