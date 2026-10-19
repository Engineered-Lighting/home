---
title: Perception Lab (deploy)
target: backend
type: changed
---

Perception Lab (deploy): live JEV no longer asks about an arm's camera in the instant between its old frame
expiring and its own newer frame landing, a 1 Hz arm's frames stay current until its next sample is due,
and the 2 Hz preview is captured on a steady grid.

- **Paired-scheduler staleness.** The scheduler wakes on any arm's delivery. Arm B's result lands about
  140 ms after A's, so at A's delivery B's previous frame had often just expired while B's newer frame was
  already dispatched. B's facts key flipped to `expired_historical`, B was offered, JEV abstained as
  instructed, and B was offered again when its frame landed about 0.1 s later. In session f1739301 all 18
  of B's `insufficient_evidence` answers came from this race. Now, while an arm's camera reads expired and a
  dispatched request for a newer frame has not passed its deadline, the offer is skipped as
  `awaiting_inflight_frame` (a new `reasoning.scheduler` skip reason) and the scheduler wakes at that
  deadline, at most `debounce_s` (2 s) after the expiry. A stalled worker therefore still surfaces within
  2 s, and a stalled camera (nothing in flight) surfaces at once.
- **Arm validity (rule `arm-sampling-1`).** A frame's result now stays current for the source's own
  validity (3 s minus clock uncertainty, about 2.1 s) plus the extra wait the arm's sampling adds (sampling
  period minus the 0.5 s source interval). For a 1 Hz arm that is about 2.6 s, capped at observed + 3 s.
  Unsampled and 2 Hz arms are unchanged, and a source's shorter claim is never lengthened past that
  rule. The paired runner uses it for expiry before dispatch, `current` and the output's `valid_until`;
  `perception.delivery` records `valid_until` and `validity_rule` when the rule extended the source.
  `now.age` is now consistent with it: `fresh_under_3s` names current evidence only, and an expired frame
  reads `about_5_s` or older. Replaying f1739301's recorded deliveries (A/B), expiry gaps fall from 35/61
  to 1/6 and the expired-camera episodes a scheduler tick could offer from 6/36 to 0/0; none of the 1/18
  expired offers actually sent would go out.
- **Preview cadence (rule `pump-grid-1`).** About 10% of the gaps between stored preview frames exceeded
  0.75 s. The camera answers `/frame.jpg` in about 70 ms and ingest takes about 30 ms. The gaps came from the
  causal capture's variable preparation before the challenge: camera health, clock publisher open and
  Chrony publication, including 4 `systemctl` and 1 `chronyc` subprocesses and 2 fsyncs. That preparation
  takes about 0.1 s at the median, 0.3-0.4 s at p90 and up to about 0.9 s. It was compounded by a
  start-to-start pump that restarted at once after every overrun, so each overrun shifted all later
  polls. Sessions lost 7-24 slots, and about 2/3 of the long gaps were followed by a 0.2-0.3 s gap.
  `PreviewPump` now polls on a fixed monotonic grid: no drift, at most one regular fetch per slot, a
  late start only in the slot's first half, and otherwise the slot is skipped and counted
  (`skipped_slots`). The live preview makes one quick in-slot retry after a failed capture (`retries`).
  `ChronyPreviewClient` is started 0.25 s ahead of its slot and holds only the JPEG request until the
  slot: at most 0.25 s, and only while the published clock receipt stays valid for 0.5 s more. The
  preparation runs ahead, and the exposure lands on the grid. Acceptance and validation are unchanged,
  and steady-state camera load stays at 2 Hz (`last_phases_ms.capture_hold` shows the hold). Simulated
  on the three sessions' own receipts, gaps over 0.75 s go from 9.7/4.4/12.4% to 4.9/1.2/5.8%. Frames
  for which a V-JEPA clip exists go from 44/73/30% to 67/90/58%. Live numbers follow the deploy.
