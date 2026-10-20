---
title: Cut Lab controller load from storage scans, pace Live JEV and stop recordings honestly
target: backend
type: fixed
---

With Live JEV on, the Victoria Lab controller (one CPU core) still spent most
of its reasoning cost walking the whole storage tree (~3,650 files) for every
quota check, three times per JEV request, several times per perception frame
and every 0.2 s from the recorder monitor. Quota semantics are unchanged: no
write is admitted that would exceed the limit.

- **Exact storage accounting without rescans.** Quota checks keep a running
  total of the files only the Lab writes (`media/`, finished `staging/`
  directories and the lock file). Every Lab write and delete updates it.
  Every check also measures, at that moment, the database files, each active
  recording's staging directory, and everything else in the Lab storage.
  Operator output such as `derived/` from `extract-lab-detector-inputs.py`
  therefore counts immediately. That last walk is capped at 4,096 entries;
  beyond the cap, writes are refused as `storage_unbounded`.
  - Only a file changed inside `media/` or `staging/` by something other than
    the Lab waits to be counted. It is found by a full scan of those trees at
    controller start, session start and stop, and every 60 s. Any such drift
    is corrected, counted and logged without content.
  - If usage is over quota after a correction, or a scan fails, new writes are
    refused until it is resolved. A link refuses as `unsafe_storage`; a
    permission or I/O error refuses as `storage_scan_failed`.
  - The recorder monitor no longer scans storage every 0.2 s.
- **Live JEV pacing is a controller setting.** Each arm is offered to JEV at
  most once per `reasoning_min_offer_interval_s` (live config, 1–10 s,
  default 2 s, previously a fixed 1 s). A newer output inside the interval is
  still offered, with the newest evidence, when the interval ends. JEV
  admission budgets are unchanged.
- **A user Stop finalizes as `user_stop`.** With the managed pair, Stop could
  move the session to `stopping` before the recorder's own stop signal. The
  recording then ended as `recording_state_changed` with
  `partial_or_unverified` capture (or `conflict` if a segment import raced it),
  even though every segment was committed. Stop now records
  `recording.stop_requested`, and the recorder finalizes with that reason and
  complete capture when all segments are committed. A stop signalled
  in-process now reports its recorded reason too (`controller_shutdown`,
  `quota_exceeded`, `duration_limit`) instead of always `user_stop`.
- **Load evidence for live tests.** While recording, the controller writes a
  content-free `controller.load` receipt every 5 s. It records process CPU as a
  percentage of one core, RSS, server-side timings for `/live`, the session
  graph and JEV build/send, and storage counters. `/live` shows the latest
  sample as `controller_load`. The once-per-second expiry check now reads
  only recording sessions. Each of its steps is guarded and failures are
  counted by step, so one error cannot stop duration limits, reconciliation
  or load receipts.

On a synthetic 90 s Victoria-shaped session on the workstation, with the
recorder's 0.2 s capacity poll, controller CPU per recorded second fell from
about 63 ms (1,456 full scans) to about 19 ms at a 1 s interval and about
13 ms at the 2 s default (2 full scans).
