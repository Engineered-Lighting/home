# SEAM: one synthetic evening through the real live-v1 tools

Run folders: round 2 `$SCRATCH/seamout/round2b`, round 1 `$SCRATCH/seamout/final` (synthetic data only, temp dirs only, no network beyond 127.0.0.1).
Harness: `$SCRATCH/probes/seam/run_seam.py` (`python3 run_seam.py --wt-root <checkouts> [--out <dir>]`), validator `$SCRATCH/probes/seam/schema_validator.py` (improved: keys a field's meaning names in backticks, such as `n_zones`, and sub-keys the spec's own example uses are accepted; all 17 records[].example now pass, two failed before), findings `$SCRATCH/../findings/seam.json` (19 findings: critical 1, high 5, medium 6, low 7).

## Summary

Round 2 statuses: MISREAD 3, MISSING-TOOL 1, OK 24, REFUSED 10, SCHEMA 8.
The real chain now runs from the fake camera through take, check, registration, detector rows, observe (only with den-observe's own camera entry: den-register's is refused), den-track, den-bundle, the fake interpreter and need/decide. It breaks at: register -> observe (SEAM-01, SEAM-02); bundle -> need, where two people collapse into one room-level PersonState and B's reading goes unserved while A works (SEAM-04, critical); Stage 1's light-model.json -> den-core decide and -> den-verify (SEAM-17, SEAM-18); den-core's log.jsonl -> den-verify (SEAM-13). Labels -> N2 refuses correctly (no staged take, so N1 froze nothing).

## Seam table, round 2 (every branch at its newest head; seamwt2)

Status: OK = consumer read the producer's real output; REFUSED = consumer refused it; MISREAD = read but meaning lost or changed; SCHEMA = record differs from schemas.json; MISSING-TOOL = no tool to run. FALLBACK / FIXTURE-FED rows are where an upstream refused or was missing and the harness kept going from the closest real output, as labelled.

| link | producer@sha -> consumer@sha | status | detail |
|---|---|---|---|
| schemas.json examples -> schema_validator.py | live-records@6772ed39 -> validator | OK | 17 of 17 examples pass; failing: [] |
| fake camera -> take | den-take fake camera (test_den_take.FakeCamera + den-register scene) -> den-take take.py@23cdde50 | OK | take.run exit 0, complete=True, 778 frames, script=commission |
| take -> check | take.py@23cdde50 -> check.py@23cdde50 | OK | check exit 0, verdict PASS, failures [] |
| take -> register | take.py@23cdde50 -> den-register@ce55b0a9 | OK | CLI fit with committed REGISTER-V1: slots_open (expected: open slots); fit with proposed values: exit 0, REGISTERED den-oak@2026-11-05.1 |
| register output room-model.jsonl | den-register@ce55b0a9 -> schemas.json | OK | PASS $SCRATCH/seamout/round2b/register/room-model.jsonl as room_model: 1 records /   missing (informational): ['version'] |
| register output calibration-records.jsonl | den-register@ce55b0a9 -> schemas.json | SCHEMA | FAIL $SCRATCH/seamout/round2b/register/calibration-records.jsonl as calibration_record: 1 records /   unknown: n_pairs (x1) /   unknown: loo_m_max (x1) /   unknown: error_bound_m (x1) /   nested: inputs: unknown sub-keys ['bundle_sha256', 'depth_window_px', 'frame', 'frame_file', 'intrinsics', 'measured_by', 'method_file', 'metho |
| take -> detector rows | take.py@23cdde50 -> den-observe fake_detector@ab491a88 | OK | 778 den-detections-1 rows (2 persons each) over 211.8 s of camera time |
| register -> observe (room_model camera entry) | den-register@ce55b0a9 -> den-observe@ab491a88 | REFUSED | refused: den-oak@2026-11-05.1 camera entry is not usable: 'rgb_from_rectified_left' |
| register (2 revisions in room-model.jsonl) -> observe | den-register@ce55b0a9 -> den-observe@ab491a88 | REFUSED | refused: no readable RoomModel at $SCRATCH/seamout/round2b/register-2/room-model.jsonl |
| take + detector rows -> observe (FALLBACK room model: den-observe synth) | take.py@23cdde50 + fake_detector -> den-observe@ab491a88 | OK | wrote 1556 observations from 778 rows to $SCRATCH/seamout/round2b/observe-fallback / transform_id den-oak@2026-11-12; clock from the slate / kind: keypoints 1556 |
| observe output observations.jsonl | den-observe@ab491a88 -> schemas.json | SCHEMA | FAIL $SCRATCH/seamout/round2b/observe-fallback/observations.jsonl as observation: 1556 records /   unknown: freshness (x1556) /   missing (informational): ['label'] |
| observe capture_ts clock domain | take.py@23cdde50 -> den-observe@ab491a88 | OK | LF only: True; UTF-8: True; 1556 records; capture_ts range 1793858837.670..1793859049.420; take t_host range 7000.012..7211.762 |
| observation.transform_id -> den-register RoomModel.cameras | den-observe@ab491a88 -> den-register@ce55b0a9 | MISREAD | transform_ids ['den-oak@2026-11-12'] resolve in den-register's latest RoomModel: {'den-oak@2026-11-12': 0}; register entries: ['den-oak@2026-11-05.1'] |
| observe -> track | den-observe@ab491a88 -> den-track@49ff481d | OK | note: 5 open slot(s) in METHOD.md; tracks use the proposed values and cannot be scored / evening                                  frames   778  used  1556  skipped    0  tracks   2  observations sha256:dd226e4ce2c1 |
| track output tracks.jsonl | den-track@49ff481d -> schemas.json | SCHEMA | FAIL $SCRATCH/seamout/round2b/track-session/evening/tracks.jsonl as track: 1692 records /   unknown: capture_ts (x1692) /   missing (informational): ['appearance'] |
| track.last_obs_ids -> observation.obs_id | den-track@49ff481d -> den-observe@ab491a88 | OK | 1692 track lines, ids ['T1', 'T2'], states {'tentative': 4, 'confirmed': 1686, 'coasting': 2}, ambiguous lines 22; 1556 last_obs_ids, 0 unresolved; 136 lines with last_obs_ids [] (undatable by den-bundle, which dates a line only from its observations); time keys on track lines: ['capture_ts'] |
| track -> bundle (real observations + real tracks) | den-track@49ff481d -> den-bundle@cf659ba8 | OK | route exit 0, 53 bundles: T1: 27 requests (27 ok) / T2: 26 requests (26 ok) / practice replay with a fake sender: rule router-v0 73047b0ff292, method den-bundle-method-1 2d5a4d9f387b |
| bundle gate on the real chain | den-bundle@cf659ba8 -> den-bundle@cf659ba8 | OK | gate exit 0: backoff      ok   checked 53 / round_robin  ok   checked 53 / stamped      ok   checked 53 / verdict: pass (practice: open slots METHOD-V1.md:window_s, METHOD-V1.md:max_gap_s, METHOD-V1.md:pad_fraction, METHOD-V1.md:pad_min_px, ROUTER-V0.md:timeout_s) |
| bundle output bundles-real.jsonl | den-bundle@cf659ba8 -> schemas.json | OK | PASS $SCRATCH/seamout/round2b/bundles-real.jsonl as person_bundle: 53 records |
| bundles-real keypoint_series -> observation.obs_id | den-bundle@cf659ba8 -> den-observe@ab491a88 | OK | 623 keypoint entries, 0 unresolved; tracks bundled ['T1', 'T2']; association_safe false in 2 of 53 |
| bundle fixture -> route (FIXTURE-FED, upstream missing) | den-bundle@cf659ba8 fixtures -> den-bundle@cf659ba8 | OK | practice replay with a fake sender: rule router-v0 73047b0ff292, method den-bundle-method-1 2d5a4d9f387b |
| bundle output bundles.jsonl | den-bundle@cf659ba8 -> schemas.json | OK | PASS $SCRATCH/seamout/round2b/bundles.jsonl as person_bundle: 3 records |
| bundle.keypoint_series / track.last_obs_ids -> observation.obs_id | den-bundle@cf659ba8 -> den-bundle@cf659ba8 | OK | 15 keypoint obs_ids, 0 unresolved; 50 last_obs_ids, 0 unresolved |
| bundle -> FAKE INTERPRETER -> facts | den-bundle@cf659ba8 -> FAKE INTERPRETER (run_seam.py) | OK | 53 bundles -> 53 la-fact-log-1 lines; per-track facts collapse into one room-level line format (no track_id field exists in la-fact-log-1) |
| FAKE INTERPRETER facts -> need (den-core-live) | FAKE INTERPRETER -> den-core-live need/decide@64bd23ca | OK |   rear_right    +104.0 s ..   +334.0 s /   front_right   +104.0 s ..   +334.0 s / log: $SCRATCH/seamout/round2b/core-den-core-live/log.jsonl |
| den-core-live person_state records (from log.jsonl) | den-core-live need/decide@64bd23ca -> schemas.json | SCHEMA | FAIL $SCRATCH/seamout/round2b/core-den-core-live/person_state.jsonl as person_state: 53 records /   nested: focused_engagement: unknown sub-keys ['kind', 'onset_ts'] (canon ['state', 'since_s', 'confidence']) (x53) /   missing (informational): ['affected', 'attention', 'embedding', 'episodes', 'field_as_of', 'heading', 'motion',  |
| den-core-live decision records (from log.jsonl) | den-core-live need/decide@64bd23ca -> schemas.json | SCHEMA | FAIL $SCRATCH/seamout/round2b/core-den-core-live/decision.jsonl as decision: 3 records /   unknown: transitions (x3) /   enum: trigger='time step' not in ['correction', 'hold change', 'light residual', 'person_state change', 'time of day'] (x2) /   missing (informational): ['hold_limited', 'safety_override', 'targets', 'verify'] |
| person_bundle tracks -> person_state.track_id (den-core-live) | den-bundle + FAKE INTERPRETER -> den-core-live need/decide@64bd23ca | MISREAD | bundles for tracks ['T1', 'T2'] -> person_state track_ids ['room']; engagement states [(1793858841.369, 'none'), (1793858845.369, 'starting'), (1793858849.369, 'none'), (1793858853.369, 'starting'), (1793858857.369, 'none'), (1793858861.369, 'starting')] |
| FAKE INTERPRETER facts -> need (den-core) | FAKE INTERPRETER -> den-core need/decide@022aa9d5 | OK |   rear_right    +104.0 s ..   +334.0 s /   front_right   +104.0 s ..   +334.0 s / log: $SCRATCH/seamout/round2b/core-den-core/log.jsonl |
| den-core person_state records (from log.jsonl) | den-core need/decide@022aa9d5 -> schemas.json | SCHEMA | FAIL $SCRATCH/seamout/round2b/core-den-core/person_state.jsonl as person_state: 53 records /   nested: focused_engagement: unknown sub-keys ['kind', 'onset_ts'] (canon ['state', 'since_s', 'confidence']) (x53) /   missing (informational): ['affected', 'attention', 'embedding', 'episodes', 'field_as_of', 'heading', 'motion', 'posi |
| den-core decision records (from log.jsonl) | den-core need/decide@022aa9d5 -> schemas.json | SCHEMA | FAIL $SCRATCH/seamout/round2b/core-den-core/decision.jsonl as decision: 3 records /   unknown: transitions (x3) /   enum: trigger='time step' not in ['correction', 'hold change', 'light residual', 'person_state change', 'time of day'] (x2) /   missing (informational): ['applied_scene', 'hold_limited', 'predicted', 'recipients', ' |
| person_bundle tracks -> person_state.track_id (den-core) | den-bundle + FAKE INTERPRETER -> den-core need/decide@022aa9d5 | MISREAD | bundles for tracks ['T1', 'T2'] -> person_state track_ids ['room']; engagement states [(1793858841.369, 'none'), (1793858845.369, 'starting'), (1793858849.369, 'none'), (1793858853.369, 'starting'), (1793858857.369, 'none'), (1793858861.369, 'starting')] |
| PowerShell 5.1 '>' (UTF-16LE+BOM, CRLF) detector rows | owner redirection -> den-observe@ab491a88 | REFUSED | exit 1: UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte |
| PowerShell 5.1 '>' (UTF-16LE+BOM, CRLF) room model | owner redirection -> den-observe@ab491a88 | REFUSED | exit 2: refused: no readable RoomModel at $SCRATCH/seamout/round2b/enc/room-model-utf16.json |
| PowerShell 5.1 '>' (UTF-16LE+BOM, CRLF) observations + tracks | owner redirection -> den-bundle@cf659ba8 | REFUSED | exit 2: refused: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte |
| PowerShell 5.1 '>' (UTF-16LE+BOM, CRLF) la-fact-log-1 facts | owner redirection -> den-core-live@64bd23ca | REFUSED | exit 1: run.py: $SCRATCH/seamout/round2b/enc/la-fact-log-1-utf16.jsonl line 1 is not a la-fact-log-1 line |
| Stage 1 fit -> light-model.json + predict() | den-light stage1 model.py@028981bc -> den-light predict.py@028981bc | OK | stage1 predict.predict(scene, model) -> dict ['daylight', 'surfaces', 'version'] |
| light-model.json | den-light@028981bc -> schemas.json | SCHEMA | FAIL $SCRATCH/seamout/round2b/verify-probe/light-model.json as light_model: 1 records /   unknown: contract (x1) /   unknown: room_view (x1) /   unknown: meter_norm (x1) /   unknown: channels (x1) /   unknown: shared_curve (x1) /   unknown: cct (x1) /   unknown: anchor (x1) /   unknown: sessions (x1) /   unknown: method_sha256 (x |
| light-model.json -> den-core-live decide (--light-model) | den-light@028981bc -> den-core-live@64bd23ca | REFUSED | exit 1: run.py: not a light model: KeyError('light.den_front_left') |
| light-model.json -> den-verify LightModel adapter | den-light@028981bc -> den-verify@b8ac320e | REFUSED | den-verify LightModel.predict({'den_front_left.white': {'level': 1.0, 'cct_k': 2700}}) -> KeyError: 'light model lm-den-seam-1 has no basis for den_front_left.white'  [light_model.py:65] / den-verify LightModel.predict({'den_front_left.white@2202K': {'level': 1.0, 'cct_k': 2700}}) -> KeyError: 'den_front_left.white@2202K'  [fake_model.py:32] |
| den-core decisions -> den-verify (log.jsonl) | den-core-live@64bd23ca -> den-verify@b8ac320e | REFUSED | verify.py run --decisions log.jsonl (den-event-1 envelopes): exit 1:                 ~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ / TypeError: '<' not supported between instances of 'NoneType' and 'NoneType' |
| den-core decisions -> den-verify (decision.jsonl unwrapped by the harness) | den-core-live@64bd23ca -> den-verify@b8ac320e | OK | verify.py run --decisions decision.jsonl (unwrapped by the harness): exit 0: decisions verified 0 of 3; hit rate None (0 of 0), gate 0.85: insufficient (preview) / wrote report-preview.json in $SCRATCH/seamout/round2b/verify-probe/ |
| photometry -> light_state/k_L | den-photometry@9a5023e3 -> den-verify | MISSING-TOOL | photometry.py present but not exercised here: --capture needs a camera day folder and a Stage 0 sweep; no light_state.jsonl, so verify ran with an empty light-states file |
| take -> den-label sessions | take.py@23cdde50 -> den-label@a28130a4 | OK | 0 sessions registered. |
| den-label status | den-label@a28130a4 -> den-label@a28130a4 | OK | Split held_out: 0 sessions over 0 days; intervals fine 0, casual 0; cases none; quiet none; uncertain 0.0 s. / Re-mark: needs a second development session. / Frozen: fixture none; labels none. |
| labels -> N2 scorer | den-label@a28130a4 -> den-n2@a39f02f5 | REFUSED | exit 2: Refused, nothing written: no labels freeze line in FREEZES.md: N2 opens nothing before N1's freeze (no staged take, so N1 froze nothing) |

## Seam table, round 1 (the heads at 05:27 UTC; seamwt)

| link | producer@sha -> consumer@sha | status | detail |
|---|---|---|---|
| schemas.json examples -> schema_validator.py | live-records@6772ed39 -> validator | OK | 17 of 17 examples pass; failing: [] |
| fake camera -> take | den-take fake camera (test_den_take.FakeCamera + den-register scene) -> den-take take.py@23cdde50 | OK | take.run exit 0, complete=True, 791 frames, script=commission |
| take -> check | take.py@23cdde50 -> check.py@23cdde50 | OK | check exit 0, verdict PASS, failures [] |
| take -> register | take.py@23cdde50 -> den-register@ce55b0a9 | OK | CLI fit with committed REGISTER-V1: slots_open (expected: open slots); fit with proposed values: exit 0, REGISTERED den-oak@2026-11-05.1 |
| register output room-model.jsonl | den-register@ce55b0a9 -> schemas.json | OK | PASS $SCRATCH/seamout/final/register/room-model.jsonl as room_model: 1 records /   missing (informational): ['version'] |
| register output calibration-records.jsonl | den-register@ce55b0a9 -> schemas.json | SCHEMA | FAIL $SCRATCH/seamout/final/register/calibration-records.jsonl as calibration_record: 1 records /   unknown: n_pairs (x1) /   unknown: loo_m_max (x1) /   unknown: error_bound_m (x1) /   nested: inputs: unknown sub-keys ['bundle_sha256', 'depth_window_px', 'frame', 'frame_file', 'intrinsics', 'measured_by', 'method_file', 'method_ |
| take -> detector rows | take.py@23cdde50 -> den-observe fake_detector@c34e53a1 | OK | 791 den-detections-1 rows (2 persons each) over 198.8 s of camera time |
| register -> observe (room_model camera entry) | den-register@ce55b0a9 -> den-observe@c34e53a1 | REFUSED | refused: den-oak@2026-11-05.1 camera entry is not usable: 'rgb_from_rectified_left' |
| register (2 revisions in room-model.jsonl) -> observe | den-register@ce55b0a9 -> den-observe@c34e53a1 | REFUSED | refused: no readable RoomModel at $SCRATCH/seamout/final/register-2/room-model.jsonl |
| take + detector rows -> observe (FALLBACK room model: den-observe synth) | take.py@23cdde50 + fake_detector -> den-observe@c34e53a1 | OK | wrote 1582 observations from 791 rows to $SCRATCH/seamout/final/observe-fallback / transform_id den-oak@2026-11-12; clock from the slate / kind: keypoints 1582 |
| observe output observations.jsonl | den-observe@c34e53a1 -> schemas.json | SCHEMA | FAIL $SCRATCH/seamout/final/observe-fallback/observations.jsonl as observation: 1582 records /   unknown: freshness (x1582) /   missing (informational): ['label'] |
| observe capture_ts clock domain | take.py@23cdde50 -> den-observe@c34e53a1 | OK | LF only: True; UTF-8: True; 1582 records; capture_ts range 1793857617.670..1793857816.420; take t_host range 7000.012..7198.762 |
| observation.transform_id -> den-register RoomModel.cameras | den-observe@c34e53a1 -> den-register@ce55b0a9 | MISREAD | transform_ids ['den-oak@2026-11-12'] resolve in den-register's latest RoomModel: {'den-oak@2026-11-12': 0}; register entries: ['den-oak@2026-11-05.1'] |
| observe -> track | den-observe@c34e53a1 -> den-track@44f3964d | MISSING-TOOL | no CLI/reader/writer (files: associate.py, kalman.py, method.py, scorer_core.py, scorer_math.py, synth.py, test_scorer.py, tracker_core.py); TrackerCore.step takes plain (id, xy, cov) inputs and returns Python objects, no Track records; method.read_block(): exit 1: FileNotFoundError: [Errno 2] No such file or directory: '$SCRATCH |
| track -> bundle (real observations, no Track records) | den-track@44f3964d -> den-bundle@cf659ba8 | MISSING-TOOL | route exit 0: practice replay with a fake sender: rule router-v0 73047b0ff292, method den-bundle-method-1 2d5a4d9f387b |
| bundle fixture -> route (FIXTURE-FED, upstream missing) | den-bundle@cf659ba8 fixtures -> den-bundle@cf659ba8 | OK | practice replay with a fake sender: rule router-v0 73047b0ff292, method den-bundle-method-1 2d5a4d9f387b |
| bundle output bundles.jsonl | den-bundle@cf659ba8 -> schemas.json | OK | PASS $SCRATCH/seamout/final/bundles.jsonl as person_bundle: 3 records |
| bundle.keypoint_series / track.last_obs_ids -> observation.obs_id | den-bundle@cf659ba8 -> den-bundle@cf659ba8 | OK | 15 keypoint obs_ids, 0 unresolved; 50 last_obs_ids, 0 unresolved |
| bundle -> FAKE INTERPRETER -> facts | den-bundle@cf659ba8 -> FAKE INTERPRETER (run_seam.py) | OK | 3 bundles -> 3 la-fact-log-1 lines; per-track facts collapse into one room-level line format (no track_id field exists in la-fact-log-1) |
| FAKE INTERPRETER facts -> need (den-core-live) | FAKE INTERPRETER -> den-core-live need/decide@78a6b6a2 | OK |   2026-10-27T02:02:17Z   +134.0 s  time step   front_left=off/0s front_right=off/0s rear_left=off/0s rear_right=off/0s / diff incumbent vs decide (gaming left out): 0 interval(s) / log: $SCRATCH/seamout/final/core-den-core-live/log |
| den-core-live person_state records (from log.jsonl) | den-core-live need/decide@78a6b6a2 -> schemas.json | SCHEMA | FAIL $SCRATCH/seamout/final/core-den-core-live/person_state.jsonl as person_state: 4 records /   nested: focused_engagement: unknown sub-keys ['kind', 'onset_ts'] (canon ['state', 'since_s', 'confidence']) (x4) /   missing (informational): ['affected', 'attention', 'embedding', 'episodes', 'field_as_of', 'heading', 'motion', 'pos |
| den-core-live decision records (from log.jsonl) | den-core-live need/decide@78a6b6a2 -> schemas.json | SCHEMA | FAIL $SCRATCH/seamout/final/core-den-core-live/decision.jsonl as decision: 2 records /   unknown: transitions (x2) /   enum: trigger='time step' not in ['correction', 'hold change', 'light residual', 'person_state change', 'time of day'] (x2) /   missing (informational): ['applied_scene', 'hold_limited', 'predicted', 'recipients' |
| person_bundle tracks -> person_state.track_id (den-core-live) | den-bundle + FAKE INTERPRETER -> den-core-live need/decide@78a6b6a2 | MISREAD | bundles for tracks ['A', 'B'] -> person_state track_ids ['room']; engagement states [(1793066403.7, 'none'), (1793066407.7, 'starting'), (1793066411.7, 'none'), (1793066455.7, 'none')] |
| FAKE INTERPRETER facts -> need (den-core) | FAKE INTERPRETER -> den-core need/decide@022aa9d5 | OK |   2026-10-27T02:02:17Z   +134.0 s  time step   front_left=off/0s front_right=off/0s rear_left=off/0s rear_right=off/0s / diff incumbent vs decide (gaming left out): 0 interval(s) / log: $SCRATCH/seamout/final/core-den-core/log.json |
| den-core person_state records (from log.jsonl) | den-core need/decide@022aa9d5 -> schemas.json | SCHEMA | FAIL $SCRATCH/seamout/final/core-den-core/person_state.jsonl as person_state: 4 records /   nested: focused_engagement: unknown sub-keys ['kind', 'onset_ts'] (canon ['state', 'since_s', 'confidence']) (x4) /   missing (informational): ['affected', 'attention', 'embedding', 'episodes', 'field_as_of', 'heading', 'motion', 'position |
| den-core decision records (from log.jsonl) | den-core need/decide@022aa9d5 -> schemas.json | SCHEMA | FAIL $SCRATCH/seamout/final/core-den-core/decision.jsonl as decision: 2 records /   unknown: transitions (x2) /   enum: trigger='time step' not in ['correction', 'hold change', 'light residual', 'person_state change', 'time of day'] (x2) /   missing (informational): ['applied_scene', 'hold_limited', 'predicted', 'recipients', 'sa |
| person_bundle tracks -> person_state.track_id (den-core) | den-bundle + FAKE INTERPRETER -> den-core need/decide@022aa9d5 | MISREAD | bundles for tracks ['A', 'B'] -> person_state track_ids ['room']; engagement states [(1793066403.7, 'none'), (1793066407.7, 'starting'), (1793066411.7, 'none'), (1793066455.7, 'none')] |
| PowerShell 5.1 '>' (UTF-16LE+BOM, CRLF) detector rows | owner redirection -> den-observe@c34e53a1 | REFUSED | exit 1: UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte |
| PowerShell 5.1 '>' (UTF-16LE+BOM, CRLF) room model | owner redirection -> den-observe@c34e53a1 | REFUSED | exit 2: refused: no readable RoomModel at $SCRATCH/seamout/final/enc/room-model-utf16.json |
| PowerShell 5.1 '>' (UTF-16LE+BOM, CRLF) observations + tracks | owner redirection -> den-bundle@cf659ba8 | REFUSED | exit 2: refused: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte |
| PowerShell 5.1 '>' (UTF-16LE+BOM, CRLF) la-fact-log-1 facts | owner redirection -> den-core-live@78a6b6a2 | REFUSED | exit 1: run.py: $SCRATCH/seamout/final/enc/la-fact-log-1-utf16.jsonl line 1 is not a la-fact-log-1 line |
| need -> decision through light model predict() | den-light@58aa1742 -> den-core-live coordinator@78a6b6a2 | MISSING-TOOL | rung-8-stage1 checkout: absent (branch not pushed); any predict() in den-light: False; den-core-live decide.py reads no light_model ('no lux or light model') |
| decision -> verify | den-core-live@78a6b6a2 -> den-verify@3832f76c | MISSING-TOOL | tools/den-verify holds only ['VERIFY-V1.md'] |
| photometry -> light_state/k_L | den-photometry@91dbb798 -> den-light / den-verify | MISSING-TOOL | tools/den-photometry holds only ['METHOD-V1.md'] |
| take -> den-label sessions | take.py@23cdde50 -> den-label@a28130a4 | OK | 0 sessions registered. |
| den-label status | den-label@a28130a4 -> den-label@a28130a4 | OK | Split held_out: 0 sessions over 0 days; intervals fine 0, casual 0; cases none; quiet none; uncertain 0.0 s. / Re-mark: needs a second development session. / Frozen: fixture none; labels none. |
| labels -> N2 scorer | den-label@a28130a4 -> den-n2@absent | MISSING-TOOL | claude/den-n2 does not exist on origin |

## Commits used

Round 1 (detached worktrees under scratchpad/seamwt, made at 05:27 UTC):

```
rung-r 8b1c945ce28b8b8237d5def3de737867ea6d4cac
den-take-script 23cdde50fdac55e6f5ea775944783bd494867e4a
rung-1-register ce55b0a9d94e68c1189d20203ac1954aa39bd0f7
rung-2-observe c34e53a1b72e7c26c1e83e62ffffec41ee594da3
rung-3-track 44f3964d6861aa4529f3be2a5aadf561a3e5436b
rung-5-bundle cf659ba8b1347cbe971b02ac9aa6796d47edb6af
den-core 022aa9d5049399328525e92c838145600e6d2375
den-core-live 78a6b6a24a2a643c5c3c017782fa3afbc0d1ed1c
rung-8-stage0 58aa1742c221dfd039cbad5cce2bde15dc895068
rung-8-stage1 MISSING
rung-7-photometry 91dbb798869c2b03f31a0a484f4c9ed340d4e41f
rung-10-verify 3832f76cf000171de5c587a4a8bed96a54d89d73
den-label a28130a45a84eb268ed87431fb88b713317da2c8
den-n2 MISSING
live-records 6772ed39d0880f88a600989274b8f48ce851be60
```

Round 2 (scratchpad/seamwt2; NEW = a new worktree at the moved head, same = round 1's checkout):

```
rung-r 8b1c945ce28b8b8237d5def3de737867ea6d4cac same
den-take-script 23cdde50fdac55e6f5ea775944783bd494867e4a same
rung-1-register ce55b0a9d94e68c1189d20203ac1954aa39bd0f7 same
rung-2-observe ab491a88cf7b60ec9d62034a353dcecbf7059d53 NEW
rung-3-track 49ff481dc31b3de4ec0d12505aab227c79b3baa0 NEW
rung-5-bundle cf659ba8b1347cbe971b02ac9aa6796d47edb6af same
den-core 022aa9d5049399328525e92c838145600e6d2375 same
den-core-live 64bd23caf4dbf3bb15b0fc08c4f91991f3350741 NEW
rung-8-stage0 58aa1742c221dfd039cbad5cce2bde15dc895068 same
rung-8-stage1 028981bc5298153e510d8408cb5e1a4f24b52959 NEW
rung-7-photometry 9a5023e3095abc7c3f90534a5995d98d60041cdf NEW
rung-10-verify b8ac320e3c6b5e5f1b755aa073f0de321b46e9ea NEW
den-label a28130a45a84eb268ed87431fb88b713317da2c8 same
den-n2 a39f02f59ed4c5e8b390c69ee497221df04964f2 NEW
live-records 6772ed39d0880f88a600989274b8f48ce851be60 same
```

## Branches that moved

Since round 1's worktrees (fetched again at the end):

```
rung-r: unchanged 8b1c945c
den-take-script: unchanged 23cdde50
rung-1-register: unchanged ce55b0a9
rung-2-observe: MOVED c34e53a1 -> ab491a88 (3 commits: den-observe: drop an unused import from replay.py)
rung-3-track: MOVED 44f3964d -> 49ff481d (9 commits: den-track: README for the staged crossings session, and a release note)
rung-5-bundle: unchanged cf659ba8
den-core: unchanged 022aa9d5
den-core-live: MOVED 78a6b6a2 -> 64bd23ca (2 commits: den-core: score coordinator v0 on replay, and run it in shadow)
rung-8-stage0: unchanged 58aa1742
rung-8-stage1: NEW on origin since then: 028981bc (den-light: README Stage 1 steps, predict() contract, open slots; change fragment)
rung-7-photometry: MOVED 91dbb798 -> 9a5023e3 (6 commits: den-photometry: report.json records the pinned Stage 0 responses' hashes)
rung-10-verify: MOVED 3832f76c -> b8ac320e (7 commits: den-verify: release note for rung 10)
den-label: unchanged a28130a4
den-n2: NEW on origin since then: a39f02f5 (den-n2: release note)
live-records: unchanged 6772ed39
```

Since round 2's worktrees (fetched again at the end):

```
rung-r: unchanged 8b1c945c
den-take-script: unchanged 23cdde50
rung-1-register: unchanged ce55b0a9
rung-2-observe: unchanged ab491a88
rung-3-track: unchanged 49ff481d
rung-5-bundle: unchanged cf659ba8
den-core: unchanged 022aa9d5
den-core-live: unchanged 64bd23ca
rung-8-stage0: unchanged 58aa1742
rung-8-stage1: unchanged 028981bc
rung-7-photometry: unchanged 9a5023e3
rung-10-verify: unchanged b8ac320e
den-label: unchanged a28130a4
den-n2: unchanged a39f02f5
live-records: unchanged 6772ed39
```

## How the evening was made and pushed through

Two rounds. **Round 1** used every branch as it stood at 05:27 UTC (worktrees `scratchpad/seamwt`). By the
end of round 1, seven branches had moved, among them den-track (now with a reader, writer and CLI),
den-verify (code), Stage 1 (`predict()`), den-photometry (code) and den-n2 (new), so **round 2** re-ran the
whole chain with every branch at its newest head (`scratchpad/seamwt2`; unchanged branches are symlinks to
round 1's checkouts). The findings cite round 2 unless they say otherwise.

Raw inputs only were synthetic, made with the tools' own fakes:

1. **Camera -> take.** den-take's own loopback `FakeCamera` (from `test_den_take.py`, bound to 127.0.0.1,
   every HTTP connection checked to be loopback) serving bundles whose depth/left planes are den-register's
   synthetic room (`synthetic.render()`: six taped control points) and whose RGB carries the CHECK-V1 slate
   while "Hold the phone" is spoken. The real `take.run()` recorded it (script `commission`, 30 s real =
   about 150 s of camera time at 4 fps, about 790 frames), then the real `check.run_check()` judged it (PASS;
   the interactive span prompt answered `none`, as den-take's tests do). Patched exactly as den-take's own
   tests patch them: `take.free_bytes` (this container has 27 GB free, under take.py's 35 GB floor) and speech.
2. **Take -> registration.** den-register's synthetic worksheet and one click per control point (the owner's
   raw inputs) on that same take. `register.py fit` (CLI) refuses `slots_open` with the committed
   REGISTER-V1, so the fit also ran through `register.run_fit()` with the proposed values closed in memory, as
   den-register's tests do -> `REGISTERED den-oak@2026-11-05.1`. A second fit on frame 40 gives a second
   revision (`den-oak@2026-11-05.2`), to see what a consumer does with a two-line `room-model.jsonl`.
3. **Detector rows.** den-observe's `fake_detector.row()` twice per frame (A at the counter; B in the
   armchair, right of A), merged into one den-detections-1 row per frame; A walks across to B's side and
   back once, about 20 s around mid-evening (the crossing). Round 2 places both figures right of image
   centre so that, under the fallback camera pose, both stay inside the room (x > 0).
4. **Observe.** `replay.py` on the real take, the real registration and the rows: REFUSED (SEAM-01, SEAM-02).
   To keep going it was re-run with den-observe's own `synth.room_model()` (FALLBACK, labelled); those
   observations feed everything below. Consequence: their `transform_id` (`den-oak@2026-11-12`) resolves to
   no entry of den-register's RoomModel.
5. **Track.** Round 1: MISSING-TOOL (no reader/writer/CLI; `method.read_block()` -> FileNotFoundError
   METHOD.md). Round 2: `den_track.py track --session` on a byte copy of the real observations -> 1630 track
   lines, T1/T2, 20 ambiguous lines around the crossing, every `last_obs_ids` entry resolves.
6. **Bundle.** Round 2: den-bundle `route` and `gate` on the real observations and real tracks -> 51
   person_bundle records (T1 26 requests, T2 25), gate verdict pass (practice), 643 keypoint entries all
   resolving to real observations, `association_safe` false in 1 of 51. Round 1 (no tracks): `route` with an
   empty track file exits 0 with nothing (SEAM-07); kept going from den-bundle's own `crossing` fixture
   (FIXTURE-FED). Both rounds also run the fixture path.
7. **FAKE INTERPRETER** (the one stand-in written here, in `run_seam.py`): one la-fact-log-1 line per bundle,
   built with den-core's own `synthetic.fact_line()` (the perceiver's qwen_scene/la_fact_log builders).
   Rule: track B, or with den-track ids the bundle whose mean x is past 1.2 m (the armchair figure), is
   `reading`; the other is `computer_work`; `people_visible` one.
8. **Need -> decision.** den-core-live's and den-core's real `run.py --facts ... --assume-occupied --policies
   need incumbent decide`; person_state and decision events extracted from `log.jsonl` and validated.
   `probe_collapse.py` reruns need on den-bundle's `two_tracks` fixture with A+B and with B alone (SEAM-04).
9. **Light model and verify (round 2).** A real `light-model.json` from den-light Stage 1's own
   `model.fit/score/light_model` on readings from its own `SyntheticDen` (via its test helpers); Stage 1's
   `predict()` reads it. den-core-live `run.py --light-model` and den-verify's `LightModel` both refuse it
   (SEAM-17, SEAM-18). den-verify on den-core's `log.jsonl` crashes; on the harness-unwrapped decisions it runs
   (SEAM-13). den-photometry was not exercised (it needs a camera day folder and a Stage 0 sweep), so verify
   ran with an empty light-states file and verified 0 of 3 decisions.
10. **Labels -> N2.** den-label's real `label.py sessions/status` on the takes folder registers 0 sessions,
    correctly: N1 takes must be staged (`hard-a`/`hard-b`, 25 minutes each), and a commission take is not.
    den-n2 (round 2) then refuses: no N1 freeze line. Not a seam break; the link needs a staged take and
    the owner's blind labels.
11. **Encoding.** Each consumer's real input re-encoded as PowerShell 5.1's `>` writes it (UTF-16LE with BOM,
    CRLF) and the consumer re-run (SEAM-15, SEAM-16).

Things the validator cannot see, checked by the harness:
- **Clock domain.** Observation `capture_ts` is Unix seconds on the slate-synchronised clock
  (`clock from the slate`): take t_host 7000.0 s maps to about 1.7938e9 s through check-1.json's offset.
  Track `capture_ts`, bundle intervals and fact times stay in that domain. den-core prints decision times as
  2026-10-27 dates when fed the fixture path (the fixture's own epoch), and 2026-11-05 on the real chain.
- **Units.** room_position/joints_room in metres, cov in m²; den-register error_bound in metres; bundle
  intervals in s; Stage 1 levels 0-1 against den-verify's u 0-254 (SEAM-18). No s/ms or m/cm mix found.
- **transform_id resolution.** See step 4 and SEAM-08 (only the newest camera entry survives per revision).
- **obs_id references.** All `last_obs_ids` and `keypoint_series[].obs_id` resolve (round 2, real chain).
- **Version strings.** Observation: none (SEAM-12; den-track counts them as unversioned); track:
  `schema_version: track/v1`; person_bundle: `version: person_bundle/v1`; person_state:
  `schema_version: person_state/v1`; decision: none (inside den-event-1 envelopes, SEAM-13).
- **Encoding.** Every file the tools wrote is UTF-8 with LF; UTF-16 input handling is in the table.

## Problems, with the exact command and the observed output

### SEAM-01 (high, seam): den-observe refuses den-register's RoomModel camera entry: intrinsics/pose inner forms differ

- Where: `claude/rung-2-observe` @ `ab491a88`, `tools/den-observe/roommodel.py:90`
- What breaks: The owner registers the camera with den-register (REGISTERED den-oak@<date>.1) and passes its room-model.jsonl to den-observe replay.py. den-observe needs its own 'den-observe-camera-1' entry form (intrinsics.rectified_left/rgb/rgb_from_rectified_left/stereo_baseline_m/depth_size/rgb_size; pose.camera_frame == 'rectified_left'), while den-register writes intrinsics {source, frame, width, height, fx, fy, cx, cy} and pose.camera_frame 'rectified_left: +x right, +y down, +z forward, metres' (register.py 339-341, records.py 20). No Observation can be made from any real registration. Both sides follow different readings of an open spec: schemas.json gives RoomModel.cameras[].intrinsics/pose no inner form (its example uses the strings 'EEPROM (nominal)' and 'approximate visual alignment'); den-observe marks camera_entry_form as an open slot. Even with the intrinsics keys fixed, the pose check at line 94 would refuse the camera_frame string next.
- Reproduce: `python3 -B $SCRATCH/seamwt2/rung-2-observe/tools/den-observe/replay.py --take $SCRATCH/seamout/round2b/takes/evening --room-model $SCRATCH/seamout/round2b/register/room-model.jsonl --detections $SCRATCH/seamout/round2b/detections.jsonl --out $SCRATCH/seamout/round2b/observe-from-register  ->  exit 2: refused: den-oak@2026-11-05.1 camera entry is not usable: 'rgb_from_rectified_left'`
- Status: confirmed. Fix: Fix one camera-entry form in schemas.json (or a shared rung-1/rung-2 note) and make den-register write it (rgb intrinsics, rgb_from_rectified_left, baseline, sizes, camera_frame 'rectified_left'); den-observe should name every missing key, not only the first.

### SEAM-02 (high, seam): den-observe --room-model reads one JSON document, but den-register writes room-model.jsonl (one revision per line)

- Where: `claude/rung-2-observe` @ `ab491a88`, `tools/den-observe/roommodel.py:26`
- What breaks: After any second registration (re-fit on another frame, or a re-registration after the mount moves) den-register's room-model.jsonl holds two RoomModel revisions, one per line, as the README's JSON Lines rule says. den-observe's roommodel.load() calls json.load() on the whole file, so the file is 'no readable RoomModel' and every replay is refused. With exactly one line it parses only by accident. The consumer departs from the JSON Lines rule.
- Reproduce: `(second fit on frame 40 -> 'REGISTERED den-oak@2026-11-05.2', room-model.jsonl now 2 lines) python3 -B $SCRATCH/seamwt2/rung-2-observe/tools/den-observe/replay.py --take $SCRATCH/seamout/round2b/takes/evening --room-model $SCRATCH/seamout/round2b/register-2/room-model.jsonl --detections $SCRATCH/seamout/round2b/detections.jsonl --out $SCRATCH/seamout/round2b/observe-from-register-2rev  ->  exit 2: refused: no readable RoomModel at $SCRATCH/seamout/round2b/register-2/room-model.jsonl`
- Status: confirmed. Fix: Read --room-model as JSON Lines and take the newest revision for the room (or accept a .json and a .jsonl explicitly).

### SEAM-04 (critical, seam): need v0 merges per-person bundles into one room-level PersonState: B reading in the armchair never gets the reading look while A works at the counter

- Where: `claude/den-core-live` @ `64bd23ca`, `tools/den-core/need.py:174`
- What breaks: Rung 5 emits person_bundle records per track. The only interpreter output need reads is la-fact-log-1, which has no track_id, and need writes one PersonState with the constant track_id 'room'. With A (counter, computer_work) and B (armchair, reading) both routed, the facts alternate idle/playing, so focused_engagement oscillates none/starting and never reaches engaged: decide never chooses reading_look, with no flag, refusal or warning. With B's bundles alone the same tools reach engaged and decide chooses reading_look (all four bulbs on, 255, 2700 K). schemas.json PersonState is per track (track_id A/B); the consumer departs from it (need v0 calls itself room-level, but nothing on any branch consumes per-person interpreter output, and whole-frame facts would be idle anyway: 'two people reading is idle'). Same code on claude/den-core.
- Reproduce: `python3 -B $SCRATCH/probes/seam/probe_collapse.py $SCRATCH/seamwt2 $SCRATCH/seamout  ->  den-bundle route on its two_tracks fixture: A 9 requests, B 8; A+B (17 bundles): engagement ['none','starting','none','starting', ... ,'none','none'], decisions: den_lighting time step off; decide@v0 'vacant_off' only. B only (8 bundles): engagement ['starting','engaged','engaged'], decision decide@v0 'person_state change' 'reading_look: chose look:reading@0' (on, brightness 255, 2700 K). On the real chain (python3 run_seam.py --wt-root $SCRATCH/seamwt2 --out $SCRATCH/seamout/round2b): den-track T1/T2 -> 53 den-bundle bundles -> person_state track_ids ['room']; engagement none/starting alternating every 4 s for the first 100 s while B's bundles all read 'reading'; reading_look only at +104 s, after A (T1) walked to the armchair side at +102.8 s and its bundle read as reading too; never from B's own reading (python3 -B $SCRATCH/probes/seam/timeline.py $SCRATCH/seamout/round2b)`
- Status: confirmed. Fix: Carry track_id from the bundle through a per-track interpreter output and key need's state per track_id; until then refuse (or flag) input from more than one track instead of merging it.

### SEAM-05 (medium, schema): PersonState.focused_engagement carries non-canon sub-keys kind and onset_ts

- Where: `claude/den-core-live` @ `64bd23ca`, `tools/den-core/need.py:177`
- What breaks: schemas.json PersonState.focused_engagement is {state, since_s, confidence}; need writes {state, onset_ts, confidence, kind, since_s}. A reader built from schemas.json ignores onset_ts (the owner's contact-starting onset, choices item 3) and kind, so onset timing is lost at the first schema-built consumer. Same on claude/den-core.
- Reproduce: `python3 -B $SCRATCH/probes/seam/schema_validator.py $SCRATCH/seamwt2/live-records/docs/architecture/live-v1/schemas.json person_state $SCRATCH/seamout/round2b/core-den-core-live/person_state.jsonl  ->  FAIL ... nested: focused_engagement: unknown sub-keys ['kind', 'onset_ts'] (canon ['state', 'since_s', 'confidence'])`
- Status: confirmed. Fix: Propose onset_ts/kind into schemas.json (via the corrections pass) or move them under a documented extension key.

### SEAM-06 (medium, schema): Decision.trigger uses 'time step' and 'mode change', outside the canon enum; extra keys transitions and why

- Where: `claude/den-core-live` @ `64bd23ca`, `tools/den-core/incumbent.py:18`
- What breaks: schemas.json Decision.trigger is person_state change | hold change | correction | time of day | light residual. Both incumbent (TRIGGERS tick -> 'time step', else 'mode change') and decide (decide.py 603) write 'time step', which a canon reader (e.g. den-verify, rung 12 attribution) would reject or bucket as unknown. transitions and why are not canon fields; predicted, verify and targets are absent. Same on claude/den-core.
- Reproduce: `python3 -B $SCRATCH/probes/seam/schema_validator.py $SCRATCH/seamwt2/live-records/docs/architecture/live-v1/schemas.json decision $SCRATCH/seamout/round2b/core-den-core-live/decision.jsonl  ->  FAIL ... unknown: transitions, why; enum: trigger='time step' not in ['correction', 'hold change', 'light residual', 'person_state change', 'time of day']`
- Status: confirmed. Fix: Map tick-driven decisions to 'time of day' (or add 'time step'/'mode change' to the canon enum) and nest transitions/why under documented keys.

### SEAM-07 (medium, seam): den-bundle route exits 0 with no requests and no bundles when the track file is empty

- Where: `claude/rung-5-bundle` @ `cf659ba8`, `tools/den-bundle/den_bundle.py:72`
- What breaks: With den-track absent, an owner who runs route on real observations and an empty or missing-content tracks file gets exit 0 ('practice replay with a fake sender') and two empty files, so the absence of every track is never flagged and the router looks healthy.
- Reproduce: `(round 1, before den-track had a writer) python3 -B $SCRATCH/seamwt/rung-5-bundle/tools/den-bundle/den_bundle.py route --observations $SCRATCH/seamout/final/observe-fallback/observations.jsonl --tracks $SCRATCH/seamout/final/tracks-none.jsonl --from 2.5 --to 60 --requests $SCRATCH/seamout/final/requests-real.jsonl --bundles $SCRATCH/seamout/final/bundles-real.jsonl  ->  exit 0: practice replay with a fake sender ...; requests-real.jsonl 0 lines, bundles-real.jsonl 0 lines`
- Status: confirmed. Fix: Refuse (exit 2) when no track line falls inside --from/--to, naming the tracks file.

### SEAM-08 (medium, seam): Each RoomModel revision keeps only the newest entry per camera, so an older transform_id no longer resolves in the newest RoomModel

- Where: `claude/rung-1-register` @ `ce55b0a9`, `tools/den-register/records.py:103`
- What breaks: with_camera() replaces a camera's entry in every new revision. After a second registration the newest revision (den-v2) holds only den-oak@2026-11-05.2; observations recorded under den-oak@2026-11-05.1 resolve only in the older line. Observations carry no RoomModel version, so a consumer given the newest RoomModel (as den-observe/den-bundle would be) cannot resolve transform_ids of earlier evenings: schemas.json requires transform_id to resolve to exactly one RoomModel.cameras entry.
- Reproduce: `python3 -c "import json; [print(json.loads(l)['version'], [c['camera_id']+'@'+c['version'] for c in json.loads(l)['cameras']]) for l in open('$SCRATCH/seamout/round2b/register-2/room-model.jsonl')]"  ->  den-v1 ['den-oak@2026-11-05.1'] / den-v2 ['den-oak@2026-11-05.2']`
- Status: confirmed. Fix: Keep superseded camera entries (marked superseded/invalidated) in each revision, or add the RoomModel version to Observation so a reader can pick the right revision.

### SEAM-09 (low, schema): Observation carries a non-canon top-level 'freshness' object

- Where: `claude/rung-2-observe` @ `ab491a88`, `tools/den-observe/observe.py:14`
- What breaks: Every Observation has freshness {available_ts, age_s, max_age_s, status, valid_until, depth, depth_gap_s}; schemas.json has no such field (validity is the canon place for rejection). A schema-built consumer drops it, so expiry and depth use are lost downstream; den-observe marks freshness_field as an open slot.
- Reproduce: `python3 -B $SCRATCH/probes/seam/schema_validator.py $SCRATCH/seamwt2/live-records/docs/architecture/live-v1/schemas.json observation $SCRATCH/seamout/round2b/observe-fallback/observations.jsonl  ->  FAIL ...: unknown: freshness (x all records)`
- Status: confirmed. Fix: Propose the field into schemas.json Observation or fold expiry into validity with a reason.

### SEAM-10 (low, schema): den-register's pose CalibrationRecord has non-canon keys n_pairs, loo_m_max, error_bound_m and fit inputs under 'inputs'

- Where: `claude/rung-1-register` @ `ce55b0a9`, `tools/den-register/register.py:375`
- What breaks: schemas.json CalibrationRecord names residual/confidence (with the unit in the key), inputs for method meter only, checks, ts/version/supersedes, vs_manual. den-register adds n_pairs, loo_m_max and error_bound_m at top level and puts the fit's sha/take/pairs under inputs; a canon reader ignores the leave-one-out bound and error bound. Ambiguous: the canon example itself uses n_zones.
- Reproduce: `python3 -B $SCRATCH/probes/seam/schema_validator.py $SCRATCH/seamwt2/live-records/docs/architecture/live-v1/schemas.json calibration_record $SCRATCH/seamout/round2b/register/calibration-records.jsonl  ->  FAIL: unknown: n_pairs, loo_m_max, error_bound_m; nested: inputs: unknown sub-keys [bundle_sha256, ..., worksheet_sha256]`
- Status: confirmed. Fix: Name them as canon residual/confidence variants (e.g. residual_m_loo_max, confidence_m_bound) or propose them into schemas.json.

### SEAM-11 (low, seam): take.py's default --camera is the real Jetson LAN address

- Where: `claude/den-take-script` @ `23cdde50`, `tools/den-take/take.py:44`
- What breaks: Any run of take.py without --camera (a test harness, an agent, a mistyped command) polls http://<camera-lan-address>:8090, a real host, and the README warns that polling can restart the camera and drop the feed the den lights follow. The harness always passes a 127.0.0.1 fake.
- Reproduce: `grep -n '^DEFAULT_CAMERA' $SCRATCH/seamwt2/den-take-script/tools/den-take/take.py  ->  44:DEFAULT_CAMERA = "http://<camera-lan-address>:8090"`
- Status: confirmed. Fix: Make --camera required (or default to 127.0.0.1:18090, the documented tunnel).

### SEAM-12 (low, schema): Observation records carry no record version

- Where: `claude/rung-2-observe` @ `ab491a88`, `tools/den-observe/observe.py:183`
- What breaks: The README rule is that records travel 'with the record's field names and its version', and schemas.json gives Observation a proposed minor version (transform_id narrowing, joints_room). den-observe's lines carry no version key, so a reader cannot tell a pre-narrowing 'den-cam-align@...' transform_id record from a current one (den-bundle, by contrast, writes person_bundle/v1 and reads track/v1).
- Reproduce: `head -1 $SCRATCH/seamout/round2b/observe-fallback/observations.jsonl | python3 -c 'import json,sys; print(sorted(json.load(sys.stdin)))'  ->  ['capture_ts', 'depth_ts', 'freshness', 'image_box', 'joint_set', 'joints_room', 'keypoints', 'kind', 'obs_id', 'room_position', 'score', 'source', 'transform_id', 'validity', 'visibility'] (no version)`
- Status: confirmed. Fix: Add version: 'observation/v1' (or the proposed minor) to every line, as den-bundle does.

### SEAM-13 (high, seam): den-verify crashes on den-core's decisions: den-core writes them only as den-event-1 envelopes inside log.jsonl

- Where: `claude/den-core-live` @ `64bd23ca`, `tools/den-core/run.py:418`
- What breaks: Rung 10 reads Decision records, one per line (verify.py run --decisions). den-core writes decisions only inside log.jsonl as {schema: den-event-1, t, seq, kind: decision, data: {...}}, mixed with ticks, facts and modes, with no record version. Given log.jsonl, den-verify reads every envelope as a decision with ts None and dies sorting them: a TypeError traceback and exit 1, not a refusal. Given the same decisions unwrapped one per line (done here by the harness), den-verify reads them (0 of 3 verified, for lack of light states). The producer departs from the one-record-per-line rule for decisions; the consumer should refuse, not crash.
- Reproduce: `python3 -B $SCRATCH/seamwt2/rung-10-verify/tools/den-verify/verify.py run --decisions $SCRATCH/seamout/round2b/core-den-core-live/log.jsonl --light-states $SCRATCH/seamout/round2b/verify-probe/light_states-none.jsonl --model $SCRATCH/seamout/round2b/verify-probe/light-model.json --out $SCRATCH/seamout/round2b/verify-probe/verify-log --preview  ->  exit 1: Traceback ... score.py, line 210, in __init__ ... TypeError: '<' not supported between instances of 'NoneType' and 'NoneType'. With --decisions .../decision.jsonl (unwrapped): exit 0, 'decisions verified 0 of 3; hit rate None (0 of 0), gate 0.85: insufficient (preview)'`
- Status: confirmed. Fix: den-core: also write decisions.jsonl (one Decision per line, with version); den-verify: refuse a line without ts/decision_id by name instead of crashing.

### SEAM-15 (medium, seam): UTF-16 detector rows (PowerShell 5.1 '>') crash replay.py with a traceback and exit 1, the code that means 'scored and the gate failed'

- Where: `claude/rung-2-observe` @ `ab491a88`, `tools/den-observe/detections.py:110`
- What breaks: If the owner captures detector rows with PowerShell 5.1's '>' (UTF-16LE with a BOM, CRLF), read_rows opens the file as utf-8-sig and the UnicodeDecodeError escapes the (OSError, RowError) handler in replay.py: a raw traceback and exit 1. replay.py documents exit 1 as 'scored and the gate failed', so a script or the owner reads an encoding mistake as a gate failure. The room model in UTF-16 is refused more gracefully (exit 2, 'no readable RoomModel', but without saying why). den-core's own sources._text() already decodes a UTF-16 BOM for other inputs.
- Reproduce: `(rows re-encoded as PowerShell 5.1 '>' writes them) python3 -B $SCRATCH/seamwt2/rung-2-observe/tools/den-observe/replay.py --take $SCRATCH/seamout/round2b/takes/evening --room-model $SCRATCH/seamout/round2b/room-model-den-observe-synth.json --detections $SCRATCH/seamout/round2b/enc/detections-utf16.jsonl --out $SCRATCH/seamout/round2b/enc/obs-d16  ->  exit 1: Traceback ... detections.py line 111, in read_rows ... UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte`
- Status: confirmed. Fix: Decode a UTF-16 BOM (as den-core's sources._text does) or catch UnicodeDecodeError and refuse with exit 2 naming the encoding.

### SEAM-16 (low, seam): den-core's fact-log adapter refuses UTF-16 la-fact-log-1 files although its other adapters decode them

- Where: `claude/den-core-live` @ `64bd23ca`, `tools/den-core/sources.py:59`
- What breaks: fact_log() splits raw bytes on b'\n' and decodes each line as ASCII, so a fact log saved through PowerShell 5.1's '>' or Out-File (UTF-16LE + BOM, CRLF) fails on line 1 as 'not a la-fact-log-1 line', while poll and journal inputs go through _text(), which handles the BOM. den-bundle (read_jsonl: utf-8) refuses UTF-16 too, with exit 2 and the codec message.
- Reproduce: `python3 -B $SCRATCH/seamwt2/den-core-live/tools/den-core/run.py --out $SCRATCH/seamout/round2b/enc/core16 --facts $SCRATCH/seamout/round2b/enc/la-fact-log-1-utf16.jsonl --assume-occupied --policies need incumbent decide  ->  exit 1: run.py: $SCRATCH/seamout/round2b/enc/la-fact-log-1-utf16.jsonl line 1 is not a la-fact-log-1 line`
- Status: confirmed. Fix: Route fact_log through _text() (or say 'UTF-16: re-save as UTF-8' in the refusal).

### SEAM-17 (high, seam): den-core-live's coordinator refuses rung 8 Stage 1's real light-model.json (KeyError 'light.den_front_left')

- Where: `claude/den-core-live` @ `64bd23ca`, `tools/den-core/light_model.py:80`
- What breaks: decide's light model adapter was written to rung 8's plan before Stage 1 existed: basis keyed by HA entity id ('light.den_front_left') and kelvin. Stage 1's light-model.json keys its basis by channel and temperature ('den_front_left.white@2202K'), its dimming by channel ('den_front_left.white'), and publishes predict(scene, model) as 'what rungs 9 and 10 call'. So need -> decision through predict() cannot run: run.py exits 1. The consumer departs from the producer's published contract; schemas.json LightModel names basis but not its key form.
- Reproduce: `python3 -B $SCRATCH/seamwt2/den-core-live/tools/den-core/run.py --out $SCRATCH/seamout/round2b/core-with-stage1-model --facts $SCRATCH/seamout/round2b/interp/la-fact-log-1-seam.jsonl --assume-occupied --policies need incumbent decide --light-model $SCRATCH/seamout/round2b/verify-probe/light-model.json  ->  exit 1: run.py: not a light model: KeyError('light.den_front_left')`
- Status: confirmed. Fix: Have den-core's LightModel call Stage 1's predict.prepare/predict (scene_from_ha for HA states) instead of its own reading of the plan.

### SEAM-18 (high, seam): den-verify's light model adapter cannot read Stage 1's light-model.json, and its call shape differs from Stage 1's predict()

- Where: `claude/rung-10-verify` @ `b8ac320e`, `tools/den-verify/light_model.py:65`
- What breaks: den-verify predicts only through LightModel, whose backend is fake_model.predict(model, {bulb: {u 0-254, k}}) with basis[bulb][kelvin]. Stage 1's file has basis keys 'den_front_left.white@2202K' and its predict(scene, model) takes {channel: {level 0-1, cct_k}}. With Stage 1's channel names LightModel raises 'has no basis for den_front_left.white'; with the flattened basis key it fails in fake_model (KeyError). Every verify of a real decision would crash. The light_model.py note says to swap in predict() 'and keep its argument shape: {bulb: {u, k}}', which is not Stage 1's shape.
- Reproduce: `python3 -B $SCRATCH/probes/seam/probe_verify_model.py $SCRATCH/seamwt2 $SCRATCH/seamout/round2b/verify-probe/light-model.json  ->  Stage 1 predict({'den_front_left.white': {level 1.0, cct_k 2700}}, model): surfaces dict [('r00c00', 0.78...), ...]; den-verify LightModel.predict(same) -> KeyError: 'light model lm-den-seam-1 has no basis for den_front_left.white' [light_model.py:65]; with 'den_front_left.white@2202K' -> KeyError [fake_model.py:32]`
- Status: confirmed. Fix: Replace _backend_predict with Stage 1's predict(scene, model) and convert states to {channel: {level, cct_k}}; refuse a model whose basis keys it cannot map.

### SEAM-19 (medium, seam): Track's time goes in a non-canon capture_ts that den-bundle never reads; den-bundle misdates coasting and ended lines

- Where: `claude/rung-3-track` @ `49ff481d`, `tools/den-track/records.py:284`
- What breaks: schemas.json gives Track no time field. den-track writes the frame time as capture_ts (its open slot record.time_field) and writes last_obs_ids [] on coasting and ended lines. den-bundle ignores capture_ts and dates a track line only from its last_obs_ids' observations, so each such line takes the previous line's time (or is dropped as undated): a coasting or ended state is placed at the time of the last association, earlier than it happened. In the evening, 136 of 1692 track lines have last_obs_ids []. Both sides read an open spec differently; den-track says 'Rung 5 and K7 must read the same name'.
- Reproduce: `python3 -B $SCRATCH/seamwt2/rung-3-track/tools/den-track/den_track.py track --session $SCRATCH/seamout/round2b/track-session  ->  tracks.jsonl: 1692 lines, ids T1/T2, states tentative 4, confirmed 1686, coasting 2, 22 ambiguous; 136 lines with last_obs_ids []; time key 'capture_ts' (validator: unknown: capture_ts x1692); den-bundle records.py Store dates lines by max(capture_ts of last_obs_ids) else the previous line`
- Status: confirmed. Fix: Close record.time_field in schemas.json (capture_ts) and have den-bundle date track lines from it, falling back to last_obs_ids only when absent.

### SEAM-20 (low, schema): light-model.json carries many top-level keys schemas.json's LightModel does not name

- Where: `claude/rung-8-stage1` @ `028981bc`, `tools/den-light/model.py:633`
- What breaks: Besides the canon fields, Stage 1's light-model.json has contract, room_view, meter_norm, channels, shared_curve, cct, anchor, sessions, method_sha256, method_stage1_block_sha256 and record. Consumers built from schemas.json (den-core, den-verify) cannot know channels is the key map they need (see SEAM-17/18).
- Reproduce: `python3 -B $SCRATCH/probes/seam/schema_validator.py $SCRATCH/seamwt/live-records/docs/architecture/live-v1/schemas.json light_model $SCRATCH/seamout/round2b/verify-probe/light-model.json  ->  FAIL: unknown: contract, room_view, meter_norm, channels, shared_curve, cct, anchor, sessions, method...`
- Status: confirmed. Fix: Propose channels/anchor/sessions into schemas.json LightModel, or document them as Stage 1 extensions in the predict() contract.

### SEAM-21 (low, seam): den-n2's default --den-core points at tools/den-core, which its own checkout does not contain

- Where: `claude/den-n2` @ `a39f02f5`, `tools/den-n2/score.py:44`
- What breaks: score.py runs den-core's check.py on every need log from DEFAULT_DEN_CORE = tools/den-core beside it, but claude/den-n2 carries tools/den-label and tools/den-take, not tools/den-core. Once N1 freezes, a run from that checkout without --den-core would fail to find check.py. Not reached here: it refuses first because N1 froze nothing (no staged take).
- Reproduce: `ls $SCRATCH/seamwt2/den-n2/tools  ->  den-label den-n2 den-take ... (no den-core); python3 -B $SCRATCH/seamwt2/den-n2/tools/den-n2/score.py --split development --takes $SCRATCH/seamout/round2b/takes --n1 $SCRATCH/seamout/round2b/n1 --n2 $SCRATCH/seamout/round2b/n2  ->  exit 2: Refused, nothing written: no labels freeze line in FREEZES.md`
- Status: plausible. Fix: Merge den-core into the den-n2 branch or make --den-core required with a named refusal.

