# C12 preview integration

The preview workstream provides the offline sender, trace, replay, and Home ray projection. The parent Den integration supplies live track-bound PersonState, registration and the sofa mark, and connects the Home page and controls. It owns installation and the owner-run sofa test.

## Sender and inputs

Use `install_la.py --preview-geometry` with the private registered Home geometry export. The installer records `coordinator.preview: true`, the active camera transform and `hue_overlay: false`, and supplies the geometry through the systemd `virtual-fixtures.json` credential. The producer uses the exported pivots and fixture references. The sofa service constructs no HA write client and exposes no desk route. Its switch, Stop, and Resume act on local preview state.

The live observer feeds `Preview` a current `TrackingSnapshot` before its bound `InferenceReply`. The coordinator checks the attached stills against the sitting, camera epoch and registered scan. An unclear answer breaks the pending reading streak. Ambiguous tracking or a current answer reporting several people or nobody revokes spotlight support.

## Home and replay

The coordinator GET includes `snapshot`, `trace_event_index`, and `trace_complete`. The people SSE sends `spot_clock` and `spot_snapshot`, with Home-frame angles and a monotonic deadline. Each fixture carries `last_move_trace_id` for its last applied MOVE. A superseded encoded command can remain in the diagnostic trace. Home draws the ray from the registered pivot and supplied aim through its normal surface intersection. The Full Home gateway maps browser controls to the local `/coordinator/virtual` routes. Resume names the current Stop counter.

MOVE records carry the requested travel style, dim level when applicable, and fade. FC13 leaves dim level and fade unset and maps dim travel to dark. Preview release darkens immediately while the offline trace retains the requested fade.

The trace is `days/<UTC date>/coordinator-fixture_calls.jsonl` in coordinator state, schema `c12-fixture-trace-3` (it was `c12-preview-trace-2`; the replay still reads both, and trace-1).
- **The run header.** Each day file a run writes to starts with the run's header row (`kind: run_header`). It holds the run's mono-and-wall anchor, the codec's and the host simulator's SHA-256, the firmware commit, the room-model binding and the profile hashes. In trace-3 it also holds `desk`, the one fixture the run may send to (`{fixture_id, device, light}`), or `null` for a virtual-only run.
- **Encoded rows are written before any send.** `sent: false` on one means "this row is the encoding". In a desk run the sender's send rows follow (see the sender, below).
- **The binding.** Every row names its room model (`transform_id`, `equipment_revision`, `equipment_digest`, with the scan as `registration_revision`). Every MOVE also names its subject (`track_id`, `camera_epoch`, `association_revision`) and the reply it answers (`request_id`). A lit MOVE must have a reply.

For a run spanning UTC days, pass both day files in order. For sofa acceptance, compare the last saved event index with the final snapshot and require `trace_complete: true`. Verify the actual sofa trace locally:

```powershell
python -B tools/den-live/c12_replay.py <day-1.jsonl> [<day-2.jsonl>] --firmware-zip <private-zip>
```

Add `--sim <native-c12v2_sim>` and `--sim-sha256 <build-digest>` to replay through the compiled P2 application. The build the trace names is `preview_trace.SIMULATOR_SHA256`, and a header naming another build is refused.

For trace-3's send rows, pass every run a board took part in, in time order. The replay's five send checks:
1. Every send row names an earlier encoded row of its run and fixture, and only the run's desk fixture is sent to. A run with `desk: null` sends nothing.
2. The wire payload equals the encoded payload, except the `seq` and a keep-lit's `lit_seq`.
3. A keep-lit's `lit_seq` is the wire `seq` of its run's latest accepted lit move, from that move's send row.
4. Per board, across runs in time order, every wire `seq` is new to the board. STOP is taken at any `seq`, and a repeat is allowed only as a retry of the same row. Calls that overlapped are ordered by the integration's own count.
5. With `--sim`, each board's frames go through one simulator run across all its runs. Each MOVE's answer, and the latch after each STOP and RESUME, must be the board's own.

`test_trace_v3.py` is trace-3's committed check: two den-live runs on one board with a restart between them, the rows a real Preview wrote plus the sender's send rows from the fake Home Assistant. Each check is shown refusing its own fault. `test_trace_v2.py` is the committed check for the encoded rows: rows a real Preview wrote, through the pinned codec and, when `C12_TEST_SIM` is set, the simulator. The pinned firmware ZIP remains private. The shared fixture is `fixtures/preview-f3.json`. Run `python -B -m unittest discover -s tools/den-live -p 'test_preview*.py'` and `node --test tools/test-den-spot-playback.mjs` for synthetic verification.

## The FC13 sender (offline)

`c12_send.py` is the sender for the desk stage (plan 05, step 05-2), and nothing builds one in the service yet. It takes each trace row after the storage has fsynced it (`Sender.after(trace.append)`), and calls `c12.move`, `c12.keep_lit`, `c12.stop` and `c12.resume` on the den home's Home Assistant through `ha_link.HaSession`, one persistent connection per lane. Each call or drop leaves a send row (`kind: send`, schema `c12-fixture-trace-3`) with the wire `seq` and bytes the integration used, the answer and the outcome. The `c12` integration owns the wire `seq`, so den-live's trace `seq` never goes on the air.
- **Order:** Stop and Resume ride their own lane, ahead of everything, and a Stop cancels its fixture's unsent keep-lit and move. One move or keep-lit is in flight per fixture, and a move waits for its fixture's unanswered Resume.
- **Drops:** a move whose `valid_ms` ran out before its call is never sent. A keep-lit's `lit_seq` is the wire `seq` of the latest accepted lit move, and a keep-lit for any other move is dropped.
- **Hooks for the preview:** `on_release(fixture, move_trace_id, reason)` fires for a refused or expired move, and after two failed keep-lits in a row. `on_board_stop(fixture, reason)` fires for a Stop den-live didn't send: a move refused `stopped`, or a latch read more than 2 s after den-live's own Resume answer. `copy_stop_into(preview)` turns that into Home's Stop. Wiring the release into the preview is a later step.

`fake_c12_ha.py` is a fake of that Home Assistant for the tests. Its integration reads the board's `last_seq` when it starts, sends only Stop while it can't, resends a move once with the same `seq`, and drives the v2-a host simulator (when `C12_TEST_SIM` is set) or a model of the P3 board with its 3 s lease. Run `python -B -m unittest discover -s tools/den-live -p test_c12_send.py` from the repo root.

## The desk switch

`desk.py` turns the sender on for one fixture (plan 05, step 05-4). The service reads `desk.json` (schema `den-c12-desk-1`) from its credentials folder. **Without the file, den-live is virtual only, as before:** no sender, no Home Assistant write client, the preview's own storage unwrapped, and `desk: null` in the run header. The file holds:
- `fixture_id`, one registered fixture, and `device`, its board's Home Assistant device reference;
- `light`: `false` is the dark rehearsal. Every move goes with its spot at level 0 and no lit travel, no keep-lit goes (`dropped_dark`), and Home still shows the virtual spot;
- `profile`, the board's measured profile in den-core's shape (`fictional: false`). It replaces the fictional one for that fixture, so the run header's `profile_sha256` names it;
- `readback` (optional): the board's Home Assistant entities (`state`, `controller`, `stop_latched`, `last_seq`, `pan_cdeg`, `tilt_cdeg`, `fault_code`), read every second. Their values become the snapshot's `devices` key, and a `kind: device` row in the trace when they change. A latch read feeds the Stop copy.

A desk file that's there but doesn't check, or a desk with no Home Assistant link in the asks block, keeps the coordinator off with the reason on the status line, rather than letting it run virtual by mistake. The room geometry stays `physical_commands: false`. `test_desk.py` covers all of it with the fake Home Assistant.

**Installing it (the desk stage, D-1 on).** `install_la.py --preview-geometry <export> --desk <desk.json>` brings the file, and only when `--desk` is given, each time. It's checked on this PC before anything is built or copied: the fixture must be one of that geometry export's, and the Qwen ask's two Home Assistant files must be there, since the desk's commands go through them. The device reference is never printed. install.sh installs it root-only as `/etc/den-live/private/desk.json`, and the drop-in loads it as the `desk.json` credential. **To turn the desk off, install again without `--desk`:** install.sh then removes LA's old file, so den-live is virtual only again. `test_install_desk.py` covers the installer, the drop-in and install.sh's own desk blocks.

**Checking it.** Before the install, on this PC: `python -B tools/den-live/desk.py --check <desk.json> --geometry <export>` says in words whether the file checks against the geometry. During a desk run, on LA: `sudo /usr/bin/python3.12 -B /opt/den-live/current/tools/den-live/desk_report.py` sums up the day's trace (what was sent, how the board answered) and prints PASS or FAIL for one fixture, the dark rule, the bytes, no stale seq, and Stop. It prints no device reference, joint angle or coordinate. The full replay with the pinned firmware codec and the simulator still runs on the PC, once the trace is copied off LA. `test_desk_report.py` covers both.

## Physical handoff

Desk and F3 each require their own owner go-ahead. The desk team qualifies Light 1, the measured motion profile, HA-owned sequencing and board receipts, HA-local Stop with LA down, and dark-and-hold with RF down during both dark and lit travel. Preserve ordinary manual lamp behavior. F3 follows the existing firmware recovery checks and the registered mount, aim, moving-beam, and real reading checks. The desk r4 rollback and the F3 Hue mapping rollback remain separate.
