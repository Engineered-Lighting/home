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

## The FC13 sender (offline)

`c12_send.py` is the sender for the desk stage (plan 05, step 05-2), and nothing builds one in the service yet. It takes each trace row after the storage has fsynced it (`Sender.after(trace.append)`), and calls `c12.move`, `c12.keep_lit`, `c12.stop` and `c12.resume` on the den home's Home Assistant through `ha_link.HaSession`, one persistent connection per lane. Each call or drop leaves a send row (`kind: send`, schema `c12-fixture-trace-3`) with the wire `seq` and bytes the integration used, the answer and the outcome. The `c12` integration owns the wire `seq`, so den-live's trace `seq` never goes on the air.
- **Order:** Stop and Resume ride their own lane, ahead of everything, and a Stop cancels its fixture's unsent keep-lit and move. One move or keep-lit is in flight per fixture, and a move waits for its fixture's unanswered Resume.
- **Drops:** a move whose `valid_ms` ran out before its call is never sent. A keep-lit's `lit_seq` is the wire `seq` of the latest accepted lit move, and a keep-lit for any other move is dropped.
- **Hooks for the preview:** `on_release(fixture, move_trace_id, reason)` fires for a refused or expired move, and after two failed keep-lits in a row. `on_board_stop(fixture, reason)` fires for a Stop den-live didn't send: a move refused `stopped`, or a latch read more than 2 s after den-live's own Resume answer. `copy_stop_into(preview)` turns that into Home's Stop. Wiring the release into the preview is a later step.

`fake_c12_ha.py` is a fake of that Home Assistant for the tests. Its integration reads the board's `last_seq` when it starts, sends only Stop while it can't, resends a move once with the same `seq`, and drives the v2-a host simulator (when `C12_TEST_SIM` is set) or a model of the P3 board with its 3 s lease. Run `python -B -m unittest discover -s tools/den-live -p test_c12_send.py` from the repo root.

## Physical handoff

Desk and F3 each require their own owner go-ahead. The desk team qualifies Light 1, the measured motion profile, HA-owned sequencing and board receipts, HA-local Stop with LA down, and dark-and-hold with RF down during both dark and lit travel. Preserve ordinary manual lamp behavior. F3 follows the existing firmware recovery checks and the registered mount, aim, moving-beam, and real reading checks. The desk r4 rollback and the F3 Hue mapping rollback remain separate.
