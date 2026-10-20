---
title: Perception Lab places the person at the desk seat for JEV and feeds V-JEPA its person boxes
target: backend
type: fixed
---

In today's at-the-PC sessions (`8824f874`, `30b6f5e5`), JEV answered `insufficient_evidence` in 60/64 and 59/59
arm-A requests. The inputs were good, but the only camera line for computer work was `qwen_only`. At the PC the owner
sits at the far right edge of the image, and the monitor, keyboard and hands are out of view.

- **Desk-seat position (wire `den-facts-7`).** Rule `desk-seat-1` turns the detector's person box into
  `now.position`: `desk_seat`, `elsewhere_in_room` or `unknown`. It uses a private per-site region, `desk_seat` in
  the JEV provisioning file. Only the word leaves.
  - `desk_seat` counts as a detector line for `computer_work` (`detector_and_qwen` when Qwen agrees) and as a
    camera cue at the PC for `pc_input_role`. A book or phone near the hand turns this off.
  - Frame-level checks against the labels (`0dc34636` intervals, plus the two at-PC sessions): precision 1.00,
    recall 0.95. It never fired in the armchair during reading or phone use.
  - `den-facts-7` is a new wire id with its own household grant in `structured_sources`. Each wire schema's digest
    is pinned in the tests. `den-facts-6` is byte-for-byte unchanged and stays selectable with `"wire"`.
  - The new projection is `structured-facts-10` (`den-facts-6` stays `structured-facts-9`). An optional question
    set, `v8` (`den-activity-10-facts`, `den-facts-7` only), changes only the computer-work wording.
- **V-JEPA clip boxes.** Clips take 8 preview frames at about 2 Hz, but the detectors run at 1 Hz with about 0.6 s
  of lag. So only about 3 of the 8 frames had a box of their own, and 101 of 105 complete clips in `30b6f5e5` were
  skipped as `person_box_sparse`.
  - Each clip frame is now matched, per arm, to the nearest available detector output within `box_match_seconds`.
    The default is 0.75 s, the bound is 0.75 s, and 0 is the old exact match. It is settable in the managed live
    `vjepa_live` block.
  - Replayed over `30b6f5e5`, 105 of those 105 clips get a box.
  - The rule, the tolerance and the largest offset are kept in the observation's input.
  - The pump writes its typed skip counts to the ledger as `vjepa.pump_status` every 30 s and at stop.
- **V-JEPA clip frames on an uneven cadence.** The stored previews arrive about twice a second, but unevenly. In
  `006752dc`, 37 of 60 pump ticks were skipped as `frames_insufficient` or `frames_irregular`.
  - Each 0.5 s grid slot now takes a stored frame within `clip_slot_tolerance_seconds` of its grid time. The rule is
    `grid-slot-nearest-1`, the default and bound are 0.4 s, and 0.25 s is the old strict grid.
  - The chosen clip is the one nearest the grid that still meets the wire contract: gaps of 0.25-0.75 s and a span
    of 3.0-4.0 s. No frame is duplicated or invented.
  - A stored gap over 0.75 s in the last 3 s can't be bridged, so it now skips with its own reason, `frames_gap`.
  - The observation's `input.clip` keeps each frame's time relative to the latest frame, its offset from the grid,
    the span and the largest and smallest gaps.
  - Replayed over the stored frames, `006752dc` goes from 23 to 26 of 60 ticks with a sendable clip, and `30b6f5e5`
    from 102 to 107 of 145. That is the most the current wire contract allows. 28 of the remaining 34 ticks in
    `006752dc` are `frames_gap`.
  - The median span stays at 3.51 s, with a range of 3.23-3.76 s.
- **Replay tool.** `jev_replay_calls.py` gains `--wire`, `--desk-seat` and a `--from-s`/`--to-s` window.

Nothing is deployed. Enabling `den-facts-7` needs the household grant plus `"wire": "den-facts-7"` and `desk_seat`
in the JEV provisioning file.
