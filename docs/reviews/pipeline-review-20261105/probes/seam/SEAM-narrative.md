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
