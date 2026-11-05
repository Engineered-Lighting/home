#!/usr/bin/env python3
"""Writes findings/seam.json from the seam harness results (run_seam.py). Standard library only."""
import json
import sys
from pathlib import Path

SP = "$SCRATCH"
WT = SP + "/seamwt2"
OUT = SP + "/seamout/round2b"   # round 2: every branch at its newest head (seamwt2)
OUT1 = SP + "/seamout/final"   # round 1: the heads at 05:27 UTC (seamwt)
WT1 = WT
PROBE = SP + "/probes/seam/run_seam.py"
SHAS = {l.split()[0]: l.split()[1] for l in open(SP + "/seamwt2/SHAS_ROUND2.txt") if len(l.split()) >= 2}
F = []


def add(**k):
    k.setdefault("probe_path", PROBE)
    k["commit"] = SHAS.get(k["branch"].replace("claude/", ""), k.get("commit", "absent"))
    F.append(k)


add(id="SEAM-01", severity="high", category="seam", branch="claude/rung-2-observe",
    title="den-observe refuses den-register's RoomModel camera entry: intrinsics/pose inner forms differ",
    file="tools/den-observe/roommodel.py", line=90,
    failure_scenario=("The owner registers the camera with den-register (REGISTERED den-oak@<date>.1) and passes "
                      "its room-model.jsonl to den-observe replay.py. den-observe needs its own 'den-observe-camera-1' "
                      "entry form (intrinsics.rectified_left/rgb/rgb_from_rectified_left/stereo_baseline_m/depth_size/"
                      "rgb_size; pose.camera_frame == 'rectified_left'), while den-register writes intrinsics "
                      "{source, frame, width, height, fx, fy, cx, cy} and pose.camera_frame "
                      "'rectified_left: +x right, +y down, +z forward, metres' (register.py 339-341, records.py 20). "
                      "No Observation can be made from any real registration. Both sides follow different readings "
                      "of an open spec: schemas.json gives RoomModel.cameras[].intrinsics/pose no inner form (its "
                      "example uses the strings 'EEPROM (nominal)' and 'approximate visual alignment'); den-observe "
                      "marks camera_entry_form as an open slot. Even with the intrinsics keys fixed, the pose "
                      "check at line 94 would refuse the camera_frame string next."),
    reproduction=(f"python3 -B {WT}/rung-2-observe/tools/den-observe/replay.py --take {OUT}/takes/evening "
                  f"--room-model {OUT}/register/room-model.jsonl --detections {OUT}/detections.jsonl "
                  f"--out {OUT}/observe-from-register  ->  exit 2: refused: den-oak@2026-11-05.1 camera entry is "
                  "not usable: 'rgb_from_rectified_left'"),
    claimed_status="confirmed",
    suggested_fix=("Fix one camera-entry form in schemas.json (or a shared rung-1/rung-2 note) and make den-register "
                   "write it (rgb intrinsics, rgb_from_rectified_left, baseline, sizes, camera_frame 'rectified_left'); "
                   "den-observe should name every missing key, not only the first."))

add(id="SEAM-02", severity="high", category="seam", branch="claude/rung-2-observe",
    title="den-observe --room-model reads one JSON document, but den-register writes room-model.jsonl (one revision per line)",
    file="tools/den-observe/roommodel.py", line=26,
    failure_scenario=("After any second registration (re-fit on another frame, or a re-registration after the mount "
                      "moves) den-register's room-model.jsonl holds two RoomModel revisions, one per line, as the "
                      "README's JSON Lines rule says. den-observe's roommodel.load() calls json.load() on the whole "
                      "file, so the file is 'no readable RoomModel' and every replay is refused. With exactly one "
                      "line it parses only by accident. The consumer departs from the JSON Lines rule."),
    reproduction=(f"(second fit on frame 40 -> 'REGISTERED den-oak@2026-11-05.2', room-model.jsonl now 2 lines) "
                  f"python3 -B {WT}/rung-2-observe/tools/den-observe/replay.py --take {OUT}/takes/evening "
                  f"--room-model {OUT}/register-2/room-model.jsonl --detections {OUT}/detections.jsonl "
                  f"--out {OUT}/observe-from-register-2rev  ->  exit 2: refused: no readable RoomModel at "
                  f"{OUT}/register-2/room-model.jsonl"),
    claimed_status="confirmed",
    suggested_fix="Read --room-model as JSON Lines and take the newest revision for the room (or accept a .json and a .jsonl explicitly).")

add(id="SEAM-04", severity="critical", category="seam", branch="claude/den-core-live",
    title="need v0 merges per-person bundles into one room-level PersonState: B reading in the armchair never gets the reading look while A works at the counter",
    file="tools/den-core/need.py", line=174,
    probe_path=SP + "/probes/seam/probe_collapse.py",
    failure_scenario=("Rung 5 emits person_bundle records per track. The only interpreter output need reads is "
                      "la-fact-log-1, which has no track_id, and need writes one PersonState with the constant "
                      "track_id 'room'. With A (counter, computer_work) and B (armchair, reading) both routed, the "
                      "facts alternate idle/playing, so focused_engagement oscillates none/starting and never reaches "
                      "engaged: decide never chooses reading_look, with no flag, refusal or warning. With B's bundles "
                      "alone the same tools reach engaged and decide chooses reading_look (all four bulbs on, 255, "
                      "2700 K). schemas.json PersonState is per track (track_id A/B); the consumer departs from it "
                      "(need v0 calls itself room-level, but nothing on any branch consumes per-person interpreter "
                      "output, and whole-frame facts would be idle anyway: 'two people reading is idle'). Same code on "
                      "claude/den-core."),
    reproduction=(f"python3 -B {SP}/probes/seam/probe_collapse.py {WT} {SP}/seamout  ->  den-bundle route on its "
                  "two_tracks fixture: A 9 requests, B 8; A+B (17 bundles): engagement ['none','starting','none',"
                  "'starting', ... ,'none','none'], decisions: den_lighting time step off; decide@v0 'vacant_off' "
                  "only. B only (8 bundles): engagement ['starting','engaged','engaged'], decision decide@v0 "
                  "'person_state change' 'reading_look: chose look:reading@0' (on, brightness 255, 2700 K). On the "
                  f"real chain (python3 run_seam.py --wt-root {WT} --out {OUT}): den-track T1/T2 -> 53 den-bundle "
                  "bundles -> person_state track_ids ['room']; engagement none/starting alternating every 4 s for the "
                  "first 100 s while B's bundles all read 'reading'; reading_look only at +104 s, after A (T1) walked "
                  "to the armchair side at +102.8 s and its bundle read as reading too; never from B's own reading "
                  f"(python3 -B {SP}/probes/seam/timeline.py {OUT})"),
    claimed_status="confirmed",
    suggested_fix="Carry track_id from the bundle through a per-track interpreter output and key need's state per track_id; until then refuse (or flag) input from more than one track instead of merging it.")

add(id="SEAM-05", severity="medium", category="schema", branch="claude/den-core-live",
    title="PersonState.focused_engagement carries non-canon sub-keys kind and onset_ts",
    file="tools/den-core/need.py", line=177,
    failure_scenario=("schemas.json PersonState.focused_engagement is {state, since_s, confidence}; need writes "
                      "{state, onset_ts, confidence, kind, since_s}. A reader built from schemas.json ignores onset_ts "
                      "(the owner's contact-starting onset, choices item 3) and kind, so onset timing is lost at the "
                      "first schema-built consumer. Same on claude/den-core."),
    reproduction=(f"python3 -B {SP}/probes/seam/schema_validator.py {WT}/live-records/docs/architecture/live-v1/schemas.json "
                  f"person_state {OUT}/core-den-core-live/person_state.jsonl  ->  FAIL ... nested: focused_engagement: "
                  "unknown sub-keys ['kind', 'onset_ts'] (canon ['state', 'since_s', 'confidence'])"),
    claimed_status="confirmed",
    suggested_fix="Propose onset_ts/kind into schemas.json (via the corrections pass) or move them under a documented extension key.")

add(id="SEAM-06", severity="medium", category="schema", branch="claude/den-core-live",
    title="Decision.trigger uses 'time step' and 'mode change', outside the canon enum; extra keys transitions and why",
    file="tools/den-core/incumbent.py", line=18,
    failure_scenario=("schemas.json Decision.trigger is person_state change | hold change | correction | time of day | "
                      "light residual. Both incumbent (TRIGGERS tick -> 'time step', else 'mode change') and decide "
                      "(decide.py 603) write 'time step', which a canon reader (e.g. den-verify, rung 12 attribution) "
                      "would reject or bucket as unknown. transitions and why are not canon fields; predicted, verify "
                      "and targets are absent. Same on claude/den-core."),
    reproduction=(f"python3 -B {SP}/probes/seam/schema_validator.py {WT}/live-records/docs/architecture/live-v1/schemas.json "
                  f"decision {OUT}/core-den-core-live/decision.jsonl  ->  FAIL ... unknown: transitions, why; enum: "
                  "trigger='time step' not in ['correction', 'hold change', 'light residual', 'person_state change', "
                  "'time of day']"),
    claimed_status="confirmed",
    suggested_fix="Map tick-driven decisions to 'time of day' (or add 'time step'/'mode change' to the canon enum) and nest transitions/why under documented keys.")

add(id="SEAM-07", severity="medium", category="seam", branch="claude/rung-5-bundle",
    title="den-bundle route exits 0 with no requests and no bundles when the track file is empty",
    file="tools/den-bundle/den_bundle.py", line=72,
    failure_scenario=("With den-track absent, an owner who runs route on real observations and an empty or missing-content "
                      "tracks file gets exit 0 ('practice replay with a fake sender') and two empty files, so the "
                      "absence of every track is never flagged and the router looks healthy."),
    reproduction=(f"(round 1, before den-track had a writer) python3 -B {SP}/seamwt/rung-5-bundle/tools/den-bundle/den_bundle.py route --observations "
                  f"{OUT1}/observe-fallback/observations.jsonl --tracks {OUT1}/tracks-none.jsonl --from 2.5 --to 60 "
                  f"--requests {OUT1}/requests-real.jsonl --bundles {OUT1}/bundles-real.jsonl  ->  exit 0: practice "
                  "replay with a fake sender ...; requests-real.jsonl 0 lines, bundles-real.jsonl 0 lines"),
    claimed_status="confirmed",
    suggested_fix="Refuse (exit 2) when no track line falls inside --from/--to, naming the tracks file.")

add(id="SEAM-08", severity="medium", category="seam", branch="claude/rung-1-register",
    title="Each RoomModel revision keeps only the newest entry per camera, so an older transform_id no longer resolves in the newest RoomModel",
    file="tools/den-register/records.py", line=103,
    failure_scenario=("with_camera() replaces a camera's entry in every new revision. After a second registration the "
                      "newest revision (den-v2) holds only den-oak@2026-11-05.2; observations recorded under "
                      "den-oak@2026-11-05.1 resolve only in the older line. Observations carry no RoomModel version, so "
                      "a consumer given the newest RoomModel (as den-observe/den-bundle would be) cannot resolve "
                      "transform_ids of earlier evenings: schemas.json requires transform_id to resolve to exactly one "
                      "RoomModel.cameras entry."),
    reproduction=(f"python3 -c \"import json; [print(json.loads(l)['version'], [c['camera_id']+'@'+c['version'] for c in "
                  f"json.loads(l)['cameras']]) for l in open('{OUT}/register-2/room-model.jsonl')]\"  ->  "
                  "den-v1 ['den-oak@2026-11-05.1'] / den-v2 ['den-oak@2026-11-05.2']"),
    claimed_status="confirmed",
    suggested_fix="Keep superseded camera entries (marked superseded/invalidated) in each revision, or add the RoomModel version to Observation so a reader can pick the right revision.")

add(id="SEAM-09", severity="low", category="schema", branch="claude/rung-2-observe",
    title="Observation carries a non-canon top-level 'freshness' object",
    file="tools/den-observe/observe.py", line=14,
    failure_scenario=("Every Observation has freshness {available_ts, age_s, max_age_s, status, valid_until, depth, "
                      "depth_gap_s}; schemas.json has no such field (validity is the canon place for rejection). "
                      "A schema-built consumer drops it, so expiry and depth use are lost downstream; den-observe marks "
                      "freshness_field as an open slot."),
    reproduction=(f"python3 -B {SP}/probes/seam/schema_validator.py {WT}/live-records/docs/architecture/live-v1/schemas.json "
                  f"observation {OUT}/observe-fallback/observations.jsonl  ->  FAIL ...: unknown: freshness (x all records)"),
    claimed_status="confirmed",
    suggested_fix="Propose the field into schemas.json Observation or fold expiry into validity with a reason.")

add(id="SEAM-10", severity="low", category="schema", branch="claude/rung-1-register",
    title="den-register's pose CalibrationRecord has non-canon keys n_pairs, loo_m_max, error_bound_m and fit inputs under 'inputs'",
    file="tools/den-register/register.py", line=375,
    failure_scenario=("schemas.json CalibrationRecord names residual/confidence (with the unit in the key), inputs for "
                      "method meter only, checks, ts/version/supersedes, vs_manual. den-register adds n_pairs, loo_m_max "
                      "and error_bound_m at top level and puts the fit's sha/take/pairs under inputs; a canon reader "
                      "ignores the leave-one-out bound and error bound. Ambiguous: the canon example itself uses n_zones."),
    reproduction=(f"python3 -B {SP}/probes/seam/schema_validator.py {WT}/live-records/docs/architecture/live-v1/schemas.json "
                  f"calibration_record {OUT}/register/calibration-records.jsonl  ->  FAIL: unknown: n_pairs, loo_m_max, "
                  "error_bound_m; nested: inputs: unknown sub-keys [bundle_sha256, ..., worksheet_sha256]"),
    claimed_status="confirmed",
    suggested_fix="Name them as canon residual/confidence variants (e.g. residual_m_loo_max, confidence_m_bound) or propose them into schemas.json.")

add(id="SEAM-11", severity="low", category="seam", branch="claude/den-take-script",
    title="take.py's default --camera is the real Jetson LAN address",
    file="tools/den-take/take.py", line=44,
    failure_scenario=("Any run of take.py without --camera (a test harness, an agent, a mistyped command) polls "
                      "http://<camera-lan-address>:8090, a real host, and the README warns that polling can restart the camera "
                      "and drop the feed the den lights follow. The harness always passes a 127.0.0.1 fake."),
    reproduction=(f"grep -n '^DEFAULT_CAMERA' {WT}/den-take-script/tools/den-take/take.py  ->  "
                  "44:DEFAULT_CAMERA = \"http://<camera-lan-address>:8090\""),
    claimed_status="confirmed",
    suggested_fix="Make --camera required (or default to 127.0.0.1:18090, the documented tunnel).")

add(id="SEAM-12", severity="low", category="schema", branch="claude/rung-2-observe",
    title="Observation records carry no record version",
    file="tools/den-observe/observe.py", line=183,
    failure_scenario=("The README rule is that records travel 'with the record's field names and its version', and "
                      "schemas.json gives Observation a proposed minor version (transform_id narrowing, joints_room). "
                      "den-observe's lines carry no version key, so a reader cannot tell a pre-narrowing "
                      "'den-cam-align@...' transform_id record from a current one (den-bundle, by contrast, writes "
                      "person_bundle/v1 and reads track/v1)."),
    reproduction=(f"head -1 {OUT}/observe-fallback/observations.jsonl | python3 -c 'import json,sys; print(sorted(json.load(sys.stdin)))'"
                  "  ->  ['capture_ts', 'depth_ts', 'freshness', 'image_box', 'joint_set', 'joints_room', 'keypoints', "
                  "'kind', 'obs_id', 'room_position', 'score', 'source', 'transform_id', 'validity', 'visibility'] "
                  "(no version)"),
    claimed_status="confirmed",
    suggested_fix="Add version: 'observation/v1' (or the proposed minor) to every line, as den-bundle does.")

add(id="SEAM-13", severity="high", category="seam", branch="claude/den-core-live",
    title="den-verify crashes on den-core's decisions: den-core writes them only as den-event-1 envelopes inside log.jsonl",
    file="tools/den-core/run.py", line=418,
    failure_scenario=("Rung 10 reads Decision records, one per line (verify.py run --decisions). den-core writes "
                      "decisions only inside log.jsonl as {schema: den-event-1, t, seq, kind: decision, data: {...}}, "
                      "mixed with ticks, facts and modes, with no record version. Given log.jsonl, den-verify reads "
                      "every envelope as a decision with ts None and dies sorting them: a TypeError traceback and "
                      "exit 1, not a refusal. Given the same decisions unwrapped one per line (done here by the "
                      "harness), den-verify reads them (0 of 3 verified, for lack of light states). The producer "
                      "departs from the one-record-per-line rule for decisions; the consumer should refuse, not crash."),
    reproduction=(f"python3 -B {WT}/rung-10-verify/tools/den-verify/verify.py run --decisions "
                  f"{OUT}/core-den-core-live/log.jsonl --light-states {OUT}/verify-probe/light_states-none.jsonl "
                  f"--model {OUT}/verify-probe/light-model.json --out {OUT}/verify-probe/verify-log --preview  ->  "
                  "exit 1: Traceback ... score.py, line 210, in __init__ ... TypeError: '<' not supported between "
                  "instances of 'NoneType' and 'NoneType'. With --decisions .../decision.jsonl (unwrapped): exit 0, "
                  "'decisions verified 0 of 3; hit rate None (0 of 0), gate 0.85: insufficient (preview)'"),
    claimed_status="confirmed",
    probe_path=SP + "/probes/seam/probe_verify.py",
    suggested_fix="den-core: also write decisions.jsonl (one Decision per line, with version); den-verify: refuse a line without ts/decision_id by name instead of crashing.")

add(id="SEAM-15", severity="medium", category="seam", branch="claude/rung-2-observe",
    title="UTF-16 detector rows (PowerShell 5.1 '>') crash replay.py with a traceback and exit 1, the code that means 'scored and the gate failed'",
    file="tools/den-observe/detections.py", line=110,
    failure_scenario=("If the owner captures detector rows with PowerShell 5.1's '>' (UTF-16LE with a BOM, CRLF), "
                      "read_rows opens the file as utf-8-sig and the UnicodeDecodeError escapes the (OSError, RowError) "
                      "handler in replay.py: a raw traceback and exit 1. replay.py documents exit 1 as 'scored and the "
                      "gate failed', so a script or the owner reads an encoding mistake as a gate failure. The room "
                      "model in UTF-16 is refused more gracefully (exit 2, 'no readable RoomModel', but without saying "
                      "why). den-core's own sources._text() already decodes a UTF-16 BOM for other inputs."),
    reproduction=(f"(rows re-encoded as PowerShell 5.1 '>' writes them) python3 -B {WT}/rung-2-observe/tools/den-observe/"
                  f"replay.py --take {OUT}/takes/evening --room-model {OUT}/room-model-den-observe-synth.json "
                  f"--detections {OUT}/enc/detections-utf16.jsonl --out {OUT}/enc/obs-d16  ->  exit 1: Traceback ... "
                  "detections.py line 111, in read_rows ... UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff "
                  "in position 0: invalid start byte"),
    claimed_status="confirmed",
    suggested_fix="Decode a UTF-16 BOM (as den-core's sources._text does) or catch UnicodeDecodeError and refuse with exit 2 naming the encoding.")

add(id="SEAM-16", severity="low", category="seam", branch="claude/den-core-live",
    title="den-core's fact-log adapter refuses UTF-16 la-fact-log-1 files although its other adapters decode them",
    file="tools/den-core/sources.py", line=59,
    failure_scenario=("fact_log() splits raw bytes on b'\\n' and decodes each line as ASCII, so a fact log saved through "
                      "PowerShell 5.1's '>' or Out-File (UTF-16LE + BOM, CRLF) fails on line 1 as 'not a la-fact-log-1 "
                      "line', while poll and journal inputs go through _text(), which handles the BOM. den-bundle "
                      "(read_jsonl: utf-8) refuses UTF-16 too, with exit 2 and the codec message."),
    reproduction=(f"python3 -B {WT}/den-core-live/tools/den-core/run.py --out {OUT}/enc/core16 --facts "
                  f"{OUT}/enc/la-fact-log-1-utf16.jsonl --assume-occupied --policies need incumbent decide  ->  exit 1: "
                  f"run.py: {OUT}/enc/la-fact-log-1-utf16.jsonl line 1 is not a la-fact-log-1 line"),
    claimed_status="confirmed",
    suggested_fix="Route fact_log through _text() (or say 'UTF-16: re-save as UTF-8' in the refusal).")

add(id="SEAM-17", severity="high", category="seam", branch="claude/den-core-live",
    title="den-core-live's coordinator refuses rung 8 Stage 1's real light-model.json (KeyError 'light.den_front_left')",
    file="tools/den-core/light_model.py", line=80,
    probe_path=SP + "/probes/seam/probe_verify.py",
    failure_scenario=("decide's light model adapter was written to rung 8's plan before Stage 1 existed: basis keyed by "
                      "HA entity id ('light.den_front_left') and kelvin. Stage 1's light-model.json keys its basis by "
                      "channel and temperature ('den_front_left.white@2202K'), its dimming by channel "
                      "('den_front_left.white'), and publishes predict(scene, model) as 'what rungs 9 and 10 call'. "
                      "So need -> decision through predict() cannot run: run.py exits 1. The consumer departs from the "
                      "producer's published contract; schemas.json LightModel names basis but not its key form."),
    reproduction=(f"python3 -B {WT}/den-core-live/tools/den-core/run.py --out {OUT}/core-with-stage1-model --facts "
                  f"{OUT}/interp/la-fact-log-1-seam.jsonl --assume-occupied --policies need incumbent decide "
                  f"--light-model {OUT}/verify-probe/light-model.json  ->  exit 1: run.py: not a light model: "
                  "KeyError('light.den_front_left')"),
    claimed_status="confirmed",
    suggested_fix="Have den-core's LightModel call Stage 1's predict.prepare/predict (scene_from_ha for HA states) instead of its own reading of the plan.")

add(id="SEAM-18", severity="high", category="seam", branch="claude/rung-10-verify",
    title="den-verify's light model adapter cannot read Stage 1's light-model.json, and its call shape differs from Stage 1's predict()",
    file="tools/den-verify/light_model.py", line=65,
    probe_path=SP + "/probes/seam/probe_verify_model.py",
    failure_scenario=("den-verify predicts only through LightModel, whose backend is fake_model.predict(model, "
                      "{bulb: {u 0-254, k}}) with basis[bulb][kelvin]. Stage 1's file has basis keys "
                      "'den_front_left.white@2202K' and its predict(scene, model) takes {channel: {level 0-1, cct_k}}. "
                      "With Stage 1's channel names LightModel raises 'has no basis for den_front_left.white'; with the "
                      "flattened basis key it fails in fake_model (KeyError). Every verify of a real decision would "
                      "crash. The light_model.py note says to swap in predict() 'and keep its argument shape: "
                      "{bulb: {u, k}}', which is not Stage 1's shape."),
    reproduction=(f"python3 -B {SP}/probes/seam/probe_verify_model.py {WT} {OUT}/verify-probe/light-model.json  ->  "
                  "Stage 1 predict({'den_front_left.white': {level 1.0, cct_k 2700}}, model): surfaces dict "
                  "[('r00c00', 0.78...), ...]; den-verify LightModel.predict(same) -> KeyError: 'light model "
                  "lm-den-seam-1 has no basis for den_front_left.white' [light_model.py:65]; with "
                  "'den_front_left.white@2202K' -> KeyError [fake_model.py:32]"),
    claimed_status="confirmed",
    suggested_fix="Replace _backend_predict with Stage 1's predict(scene, model) and convert states to {channel: {level, cct_k}}; refuse a model whose basis keys it cannot map.")

add(id="SEAM-19", severity="medium", category="seam", branch="claude/rung-3-track",
    title="Track's time goes in a non-canon capture_ts that den-bundle never reads; den-bundle misdates coasting and ended lines",
    file="tools/den-track/records.py", line=284,
    failure_scenario=("schemas.json gives Track no time field. den-track writes the frame time as capture_ts (its open "
                      "slot record.time_field) and writes last_obs_ids [] on coasting and ended lines. den-bundle "
                      "ignores capture_ts and dates a track line only from its last_obs_ids' observations, so each "
                      "such line takes the previous line's time (or is dropped as undated): a coasting or ended state "
                      "is placed at the time of the last association, earlier than it happened. In the evening, 136 "
                      "of 1692 track lines have last_obs_ids []. Both sides read an open spec differently; den-track "
                      "says 'Rung 5 and K7 must read the same name'."),
    reproduction=(f"python3 -B {WT}/rung-3-track/tools/den-track/den_track.py track --session {OUT}/track-session  "
                  "->  tracks.jsonl: 1692 lines, ids T1/T2, states tentative 4, confirmed 1686, coasting 2, 22 "
                  "ambiguous; 136 lines with last_obs_ids []; time key 'capture_ts' (validator: unknown: capture_ts "
                  "x1692); "
                  f"den-bundle records.py Store dates lines by max(capture_ts of last_obs_ids) else the previous line"),
    claimed_status="confirmed",
    suggested_fix="Close record.time_field in schemas.json (capture_ts) and have den-bundle date track lines from it, falling back to last_obs_ids only when absent.")

add(id="SEAM-20", severity="low", category="schema", branch="claude/rung-8-stage1",
    title="light-model.json carries many top-level keys schemas.json's LightModel does not name",
    file="tools/den-light/model.py", line=633,
    failure_scenario=("Besides the canon fields, Stage 1's light-model.json has contract, room_view, meter_norm, "
                      "channels, shared_curve, cct, anchor, sessions, method_sha256, method_stage1_block_sha256 and "
                      "record. Consumers built from schemas.json (den-core, den-verify) cannot know channels is the key "
                      "map they need (see SEAM-17/18)."),
    reproduction=(f"python3 -B {SP}/probes/seam/schema_validator.py {SP}/seamwt/live-records/docs/architecture/live-v1/"
                  f"schemas.json light_model {OUT}/verify-probe/light-model.json  ->  FAIL: unknown: contract, "
                  "room_view, meter_norm, channels, shared_curve, cct, anchor, sessions, method..."),
    claimed_status="confirmed",
    suggested_fix="Propose channels/anchor/sessions into schemas.json LightModel, or document them as Stage 1 extensions in the predict() contract.")

add(id="SEAM-21", severity="low", category="seam", branch="claude/den-n2",
    title="den-n2's default --den-core points at tools/den-core, which its own checkout does not contain",
    file="tools/den-n2/score.py", line=44,
    failure_scenario=("score.py runs den-core's check.py on every need log from DEFAULT_DEN_CORE = tools/den-core beside "
                      "it, but claude/den-n2 carries tools/den-label and tools/den-take, not tools/den-core. Once N1 "
                      "freezes, a run from that checkout without --den-core would fail to find check.py. Not reached "
                      "here: it refuses first because N1 froze nothing (no staged take)."),
    reproduction=(f"ls {WT}/den-n2/tools  ->  den-label den-n2 den-take ... (no den-core); python3 -B "
                  f"{WT}/den-n2/tools/den-n2/score.py --split development --takes {OUT}/takes --n1 {OUT}/n1 --n2 "
                  f"{OUT}/n2  ->  exit 2: Refused, nothing written: no labels freeze line in FREEZES.md"),
    claimed_status="plausible",
    suggested_fix="Merge den-core into the den-n2 branch or make --den-core required with a named refusal.")


if __name__ == "__main__":
    dest = Path(SP).parent / "findings" / "seam.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    for f in F:
        assert set(f) >= {"id", "title", "severity", "category", "branch", "commit", "file", "line", "failure_scenario",
                          "reproduction", "probe_path", "claimed_status", "suggested_fix"}, f["id"]
        assert f["severity"] in ("critical", "high", "medium", "low") and f["category"] in ("seam", "schema")
    order = ["id", "title", "severity", "category", "branch", "commit", "file", "line", "failure_scenario",
             "reproduction", "probe_path", "claimed_status", "suggested_fix"]
    dest.write_text(json.dumps([{k: f[k] for k in order} for f in F], indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    counts = {}
    for f in F:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    print("wrote", dest, len(F), counts)
