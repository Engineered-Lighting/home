# C12 preview integration

The preview workstream provides the offline sender, trace, replay, and Home ray projection. The parent Den integration supplies live track-bound PersonState, registration and the sofa mark, and connects the Home page and controls. It owns installation and the owner-run sofa test.

## Sender and inputs

Set `coordinator.preview` to true and point `coordinator.fixtures_file` at a private JSON file keyed by fixture ID. Optional `coordinator.seat_mark` is an XYZ room point. Preview constructs no HA client. Its switch, Stop, and Resume act on local preview state. Use a preview state directory or select just the target run from the local day logs.

Each fixture row supplies:

- `registration_revision` as a string, `aim_origin_m` as the optical pivot, `horizontal_world_reference` as the fixture's forward basis, and `start_joints_cdeg` as a modeled pan and tilt pair.
- `profile` with `frame: "c12-fixture-joints-1"`, `range_cdeg: {pan: [min,max], tilt: [min,max]}` before inset, `host_inset_cdeg`, `motion_mode`, and `lit_motion`. Supply two `axes` entries with positive `v_max`, `a_max`, and `j_max` in degrees and seconds, pan first. Copy the exact pivot, basis, and revision Home uses.

The coordinator observer uses the camera frame's `session` and `capture_ts`. Feed `CoordinatorService.person_state()` the bound `track_id`, matching `capture_epoch`, `observed_at`, `activity_valid_until`, and the existing `focused_engagement` and `field_support` fields. The latter supplies `support_status` and `last_support_capture_ts`. Pass newer support as it arrives, including when the activity label stays the same. The parent associates the inference result with the actual track before passing it here.

## Home and replay

The existing coordinator GET includes `c12_preview` with current fixture snapshots, `run_id`, `event_index`, and `as_of` in Unix seconds. Preview adds local POST `/coordinator/stop` and `/coordinator/resume`. The parent maps these through the Home gateway. The `projectC12Preview` helper returns a room-space ray from the reported joints and registered basis. Feed that ray to the beam renderer and use its real surface intersection. Check expiry using monotonic elapsed time since fetching the accepted snapshot, and discard superseded responses. Snapshot rows carry `move_event_index` to join directly to the MOVE in the run log.

The trace is `days/<UTC date>/coordinator-fixture_calls.jsonl` in coordinator state. For a run spanning UTC days, replay its complete ordered rows from both files. For sofa acceptance, compare the last saved event index with the final snapshot and require `trace_complete: true`. Verify the actual sofa trace locally:

```powershell
python -B tools/den-live/c12_replay.py <trace.jsonl> --firmware-zip <private-zip>
```

Add `--sim <native-c12v2_sim>` and `--sim-sha256 <build-digest>` to replay through the compiled P2 application. The pinned firmware ZIP remains private. Use the shared synthetic fixture at `fixtures/c12-preview-golden.json` and `node --test tools/test-c12-preview.mjs` for Home projection. Encoder and replay tests run with `python -B -m unittest discover -s tools/den-live -p 'test_c12*.py'`.

## Physical handoff

Desk and F3 each require their own owner go-ahead. The desk team qualifies Light 1, the measured motion profile, HA-owned sequencing and board receipts, HA-local Stop with LA down, and dark-and-hold with RF down during both dark and lit travel. Preserve ordinary manual lamp behavior. F3 follows the existing firmware recovery checks and the registered mount, aim, moving-beam, and real reading checks. The desk r4 rollback and the F3 Hue mapping rollback remain separate.
