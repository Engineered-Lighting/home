---
title: Keep Live JEV reasoning from saturating the Lab controller
target: backend
type: fixed
---

With Live JEV reasoning on, the Lab controller on Victoria (one CPU core) was
saturated. Source-to-controller latency doubled, the graph and live polls
slowed, and one recording stopped as `recorder_failure`. The controller now
does much less work per reasoning update. Gates, freshness, budgets and JEV
question and projection versions are unchanged.

- **Reasoning is offered only for new evidence.** An arm is offered to JEV
  only when a new camera output has been admitted for it since its last offer,
  or when its settings change (experiment generation, window, HA or caption
  grant, reasoning revision). Each arm is offered at most once per second. A
  newer output within that second is offered when the second ends. The 1 s
  timer no longer re-requests unchanged histories, and a new HA fact alone
  waits for the next camera output. Skips are counted, not requested. Live
  status shows them as `reasoning_scheduler`, and Stop writes one
  `reasoning.scheduler` summary receipt.
  - A and B are no longer offered together at one shared cutoff. Each arm is
    triggered by its own outputs, so the two arms' cutoffs can differ.
  - Unchanged evidence is never re-requested. When an arm's outputs stop, its
    last judgment is not refreshed and goes stale at its own validity.
  - A disable or Stop that lands while a request is being prepared admits
    nothing. A wall-clock step backwards cannot delay an arm's next offer or
    dispatch.
- **Less work per update.** Reasoning work no longer holds the runtime lock,
  so it cannot delay preview ingestion, Stop or live status. Each evidence
  window read now costs only the events added since the previous read, not the
  whole session. JSON evidence is copied without `deepcopy`. Each request is
  encoded once. At publication, the egress check does not recompute the
  projection for the exact packet it admitted at send; every other check still
  runs. Dispatch spacing and the published-judgment snapshot lookup now use an
  index. Live status reuses parsed payloads and reads request payloads only for
  its 30 s freshness counts.
- **Cheaper graph polling.** Projections built from recorded events are
  cached by event id, and snippets skip truncation levels that cannot fit.
  While recording, the UI reads the live graph every 3 s instead of every
  1.5 s. It pauses reads when the graph is covered by the expanded video dock
  or is scrolled off screen, and says so in the dock. A saved request whose
  preparation section is empty (for example no historical caption) now reads
  "none recorded"; "not recorded" means the request did not save it.
- **Recording stops honestly under load.** FFmpeg can list a segment just
  before renaming its `.tmp` file, or rename a file while the monitor lists
  the directory. The monitor now waits briefly for that segment instead of
  ending the recording as `recorder_failure`. If the segment is still missing
  after 5 s, or a later segment is already on disk, the recording ends as
  `segment_missing`. A listed segment that is still missing after FFmpeg
  exits still fails final collection. An unexpected failure is
  still reported as `recorder_failure`, now with its exception class. The
  storage quota scan tolerates a directory removed while it runs.

On a synthetic 90 s Victoria-shaped session on the workstation, reasoning CPU
per perception output fell from 41–48 ms to 17 ms. That run made 118 requests
instead of 171, with no replaced-pending receipts instead of 178. The
remaining cost is mostly the exact quota scan, which is kept.
