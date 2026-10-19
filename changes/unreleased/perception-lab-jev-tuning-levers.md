---
title: JEV tuning levers for the v6 facts wire, all off by default (no live behaviour changes…
target: backend
type: changed
---

JEV tuning levers for the v6 facts wire, all off by default (no live behaviour changes until the private
JEV config names them), plus a bounded paid replay tool.

- **v7 questions** (`questions: v7` in the private JEV config): question set `den-activity-9-fanout`. It has
  a structured Choice and seven single-condition Nouls: book open and attended, holding a closed book, phone
  attended, hands on keyboard or mouse, facing the monitor side, moving or changing place, and continues the
  current segment. `jev_wire.fuse` applies plan §4.3 veto gates in code (τ 0.55 reading/phone, 0.65
  computer work, ν 0.5); a vetoed class abstains, never relabels. `diagnostics.fusion` keeps the gate
  outcome and the Nouls, and the smoother receives `continues_current_segment`.
- **Coarse facts key** (`facts_key: facts-key-2`): it ignores storyline span drift and earlier segments,
  the derived support lists, Qwen `visible_basis`, posture and history, and detector/pose coverage flicker.
  A hand cue lost for under 10 s still counts. On 0dc34636 the key changes 48/51 times instead of 75/79,
  and the simulated cadence needs 48/51 calls instead of 65/68. The median held answer goes from 1.2 s to
  2.7 s, with no more false-confident seconds.
- **Smoothing options** (`smoothing: <set>`): `jev_smoothing` gains optional `insufficient`
  (`flat`/`spread`), `min_choice`, `release` and `confidence` (`posterior`/`evidence`) parameters, and
  registered sets `jev-smoothing-1` (unchanged default) and `jev-smoothing-2`. The offline sweep found no
  set that meets the plan §7.4 smoothing bar on 0dc34636, so neither is recommended as the displayed
  timeline.
- **`jev_replay_calls.py`** (operator tool, not in the release): re-asks JEV at a recorded session's
  cutoffs through the unchanged `JevProvider` and `LabJevPolicy`. It needs the `jev-replay-1` household
  grant, reads the ledger read-only, writes only a separate replay database, caches responses by packet
  digest, and has a hard cap of 600 calls.
