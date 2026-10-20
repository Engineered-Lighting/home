---
title: Facts-bound JEV validity, a smoothed activity timeline and change-driven cadence
target: backend
type: changed
---

Live JEV (v6 facts wire): answers now stay valid while the arm's facts are unchanged (up to 12 s), a
code-owned smoothed activity timeline runs live, and JEV is asked only when the facts change or a 10 s
keepalive is due while the room is occupied; an empty room is answered locally with no request.

- **Facts-bound validity.** Each delivered answer records its `facts_key` and `prediction.valid_through`
  (arrival + 12 s). A local `reasoning.validity` record ends it earlier when the key changes, a newer answer
  arrives or occupancy reads vacant. The strict frame-TTL `valid_until` and its metric are unchanged;
  findings adds `metrics_valid_facts` per JEV run and `jev_metrics` (`coverage` / `coverage_valid_facts` /
  `coverage_smoothed`, and the same for exact match and false-confident seconds).
- **Live smoothing.** `jev_smoothing.Smoother` (the offline filter, now incremental) steps per arm on every
  delivered answer and local abstention into local-only `reasoning.smoothed` records (run `jevA-smooth`),
  scored separately. The graph timeline (contract revision 3) carries `valid_through_ms` and a
  `jev_smoothed` strip, and the Lab timeline shows both.
- **Facts-key cadence.** Live config `reasoning_cadence` (default `mode: facts_key`, `keepalive_s` 10,
  `debounce_s` 2, `hold_max_s` 12, `gate_keepalive_s` 10; `mode: new_evidence` restores the old rule).
  The per-arm minimum offer interval still applies. When occupancy is vacant or unknown for longer than the
  gate hold, no request is made. The arm writes a local `reasoning.gate` `insufficient_evidence` abstention
  instead. New skip reasons `facts_unchanged`, `keepalive_not_due` and `no_person` appear in
  `reasoning.scheduler`, with `offer_reasons` and `local_abstains`.
- **Offline check** (`jev_cadence_replay.py`, read-only, no calls). On session 0dc34636 (reviewed labels,
  arms A/B), the recorded answers held on unchanged facts raise coverage from 0.19/0.16 to 0.46/0.43, with
  exact match at coverage 0.51 to 0.50 on A and 0.51 to 0.43 on B. The simulated cadence needs 50/53
  requests instead of 128/128, with valid-facts coverage 0.49/0.41 and exact match 0.57/0.46. Smoothing
  with default parameters reaches 0.79 coverage but drops exact match to about 0.35 and adds false-confident
  seconds, so it is displayed and scored but not yet tuned.
