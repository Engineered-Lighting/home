---
title: Perception Lab gets a regression set, a ship gate and automatic per-activity results
target: backend
type: changed
---

Perception Lab gets a regression set, a ship gate and automatic per-activity results. A JEV change is
now checked across every labelled interaction in the room (reading, phone, computer work, resting,
transition, other and the empty room), not only the one it targets.

- **Controller (deploy).** New release module `lab_scoring`. About 30 s after a recording stops (by any
  route), the controller's background maintenance stores one local `results.auto_score` event per session.
  The event scores what went out live against the session's saved labels. If there are no saved labels, it
  scores against draft labels made from the bookmarks: each bookmark's quick tag or text sets the activity
  until the next bookmark. For each arm and activity it records coverage, exact@coverage, abstention,
  false-confident seconds, false positives and time to first correct. Draft labels are unreviewed and
  stay in that local event. They are never saved as annotations and never reach a model input.
  `GET /sessions/{id}/findings` now carries the stored result as `auto_results`, and the Lab Score tab
  shows it as a Results card.
- **Operator tool (not in the release).** `lab_regression.py` gathers the regression set from the
  annotations table plus a private manifest (`~/perception-lab/private/regression/manifest.json`, never
  in the repo) and reports per-activity coverage, flagging activities that have only one session. It
  scores live answers and `jev_replay_calls.py` replay jobs. `gate --candidate JOBS` compares the
  candidate with a baseline on their common time, with explicit tolerances. By default the baseline is a
  replay of the baseline wire under the candidate's conditions (`--baseline-wire W` picks the replay jobs
  of W that cover the candidate's recorded requests with its question set, or the manifest names a
  `baseline`), never what went out live; `--baseline live` must be named and is flagged. The gate fails when exact@coverage
  drops, false-confident seconds rise, false positives are added to another activity's intervals (the
  empty room included) or coverage collapses. It reports INCOMPLETE when an activity in the set was not
  tested, and exits non-zero in every one of those cases. `replay` runs a dry-run cost estimate over the
  whole set first. It makes paid calls only with `--yes` and only within a hard cap (default 400).
  `jev_replay_calls.py` now accepts a caller-named job.
- **Gate noise.** `replay --repeat N --job NAME` re-asks every cached packet once into its own job, and
  `gate --noise JOBS` (repeatable, one run per job) measures JEV's run-to-run spread per activity, arm and
  metric. A difference that breaks a tolerance but stays within the measured spread is reported as
  `within_noise` (the gate passes and names those activities); a larger difference still fails, and
  without `--noise` nothing is excused. `require_all_activities` stays on by default. This covers the
  den-facts-7 vs den-facts-6 failure on phone_use false-confident seconds (0.3 s to 2.4 s in one session),
  which a repeat replay of that window showed was JEV variance.
