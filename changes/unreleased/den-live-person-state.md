---
title: Keep the den person's identity over a late frame and publish seated once
target: internal
type: changed
---

den-live's person state (step 02-3) is steadier and has one answer for seated.
- A late or missing frame no longer starts a new camera epoch. The epoch moves on a new camera session, the camera's
  clock going back more than 1 s, a new depth calibration, a day/night switch, or 3.0 s without a fresh frame. The
  pose still expires after 1.5 s for every consumer.
- A track's `association_revision` moves only once its safe or unsafe state has held for two consecutive frames. One
  unsafe frame still reads ambiguous, so the ask skips it.
- `seated {state, since_mono, basis}` is worked out once per track per frame with the pinned rule and published on the
  track. It starts again with the association revision. The Qwen ask and the lighting preview read it instead of each
  working it out, so the ask's stills and the preview's episode start at the same instant.
- The LA service logs snapshots as `den-person-state-1` in `days/<UTC date>/person-state.jsonl`, beside the ask's
  log: numbers only, with a mono-and-wall anchor, kept 14 days. Every snapshot with someone in view goes in; with
  nobody in view, about one a second, plus any change of status or presence at once, so an empty den's day is about
  50 MB rather than about 200 MB.
- `person-state.jsonl` and the ask's `asks.jsonl` each have their own day's cap, 300 MB and 50 MB, so a busy day's
  person state no longer stops the asks. The status line counts the lines not kept today, naming the file, and the
  count starts again each UTC day.
- Each marker in the people stream (`den-live-people-1`) adds `service_run_id`, `camera_epoch` and
  `association_revision`.
