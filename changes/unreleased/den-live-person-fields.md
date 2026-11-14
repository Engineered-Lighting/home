---
title: Publish the den person's facing, head point and wrists once, in perception
target: internal
type: added
---

den-live's person state (contract 2) has its remaining fields, worked out once per track per frame from
stereo-supported joints only.
- `body_facing {deg, sd_deg, as_of_mono, n}`: den-track's `body_heading` on both shoulders, plus both hips when their
  line is within 60° of the shoulders'. A sample counts at an sd of 30° or less. A turn of 150° or more waits for a
  second sample, so one left-right swap can't turn the body round. It's the circular median of the last second's
  samples, expires 1.5 s after the newest, and starts again with the association revision.
- `head_point_m {xyz, n}`: the median of the face joints, or null with fewer than two.
- `wrists_m`: each wrist that's there.
- The people stream's marker arrow (`heading_deg`) now comes from `body_facing`, so it goes when the pose expires.
  den-track's own heading and its 3 s hold no longer draw it.
- The lighting preview reads `head_point_m` for its keep-out-of-the-eyes check, and no longer reads
  `capture_earliest_mono`; a pose's age runs from `capture_mono` alone.
- `den-person-state-1` lines carry the three fields, as numbers; the schema id is unchanged. A line with one person is
  about 2.0 KB, up from 1.7 KB.
