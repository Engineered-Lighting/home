# C12 preview integration

The preview workstream provides the offline sender, trace, replay, and Home ray projection. The parent Den integration supplies live track-bound PersonState, registration and the sofa mark, and connects the Home page and controls. It owns installation and the owner-run sofa test.

## Sender and inputs

Use `install_la.py --preview-geometry` with the private registered Home geometry export. The installer records `coordinator.preview: true`, the active camera transform and `hue_overlay: false`, and supplies the geometry through the systemd `virtual-fixtures.json` credential. The producer uses the exported pivots and fixture references. The sofa service constructs no HA write client and exposes no desk route. Its switch, Stop, and Resume act on local preview state.

The live observer feeds `Preview` a current `TrackingSnapshot` before its bound `InferenceReply`. The coordinator checks the attached stills against the sitting, camera epoch and registered scan. An unclear answer breaks the pending reading streak. Ambiguous tracking or a current answer reporting several people or nobody revokes spotlight support.

## Home and replay

The coordinator GET includes `snapshot`, `trace_event_index`, and `trace_complete`. The people SSE sends `spot_clock` and `spot_snapshot`, with Home-frame angles and a monotonic deadline. Each fixture carries `last_move_trace_id` for its last applied MOVE. A superseded encoded command can remain in the diagnostic trace. Home draws the ray from the registered pivot and supplied aim through its normal surface intersection. The Full Home gateway maps browser controls to the local `/coordinator/virtual` routes. Resume names the current Stop counter.

MOVE records carry the requested travel style, dim level when applicable, and fade. FC13 leaves dim level and fade unset and maps dim travel to dark. Preview release darkens immediately while the offline trace retains the requested fade.

The trace is `days/<UTC date>/coordinator-fixture_calls.jsonl` in coordinator state, schema `c12-preview-trace-2`.
- **The run header.** Each day file a run writes to starts with the run's header row (`kind: run_header`). It holds the run's mono-and-wall anchor, the codec's and the host simulator's SHA-256, the firmware commit, the room-model binding and the profile hashes.
- **The binding.** Every row names its room model (`transform_id`, `equipment_revision`, `equipment_digest`, with the scan as `registration_revision`). Every MOVE also names its subject (`track_id`, `camera_epoch`, `association_revision`) and the reply it answers (`request_id`). A lit MOVE must have a reply.

For a run spanning UTC days, pass both day files in order. For sofa acceptance, compare the last saved event index with the final snapshot and require `trace_complete: true`. Verify the actual sofa trace locally:

```powershell
python -B tools/den-live/c12_replay.py <day-1.jsonl> [<day-2.jsonl>] --firmware-zip <private-zip>
```

Add `--sim <native-c12v2_sim>` and `--sim-sha256 <build-digest>` to replay through the compiled P2 application. The build the trace names is `preview_trace.SIMULATOR_SHA256`, and a header naming another build is refused. `test_trace_v2.py` is the committed check: rows a real Preview wrote, through the pinned codec and, when `C12_TEST_SIM` is set, the simulator. The pinned firmware ZIP remains private. The shared fixture is `fixtures/preview-f3.json`. Run `python -B -m unittest discover -s tools/den-live -p 'test_preview*.py'` and `node --test tools/test-den-spot-playback.mjs` for synthetic verification.

## Physical handoff

Desk and F3 each require their own owner go-ahead. The desk team qualifies Light 1, the measured motion profile, HA-owned sequencing and board receipts, HA-local Stop with LA down, and dark-and-hold with RF down during both dark and lit travel. Preserve ordinary manual lamp behavior. F3 follows the existing firmware recovery checks and the registered mount, aim, moving-beam, and real reading checks. The desk r4 rollback and the F3 Hue mapping rollback remain separate.
