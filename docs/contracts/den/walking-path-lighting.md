# Walking-path lighting projection

`tools/den-live/walking.py` consumes the producer's committed `MotionState` through `motion.forecast`. It introduces no tracker, model request, file access or actuator transport. `Preview` publishes its result separately from the existing reading and fixture-command state.

The default is off. `Preview(..., walking=None)` preserves the existing snapshot shape. An explicit walking configuration adds `path_lighting` to the spotlight snapshot and coordinator GET state. These are shadow/virtual proposals; no physical walking mode exists.

## Configuration

`coordinator.walking` is an optional block carried by the producer's configuration wiring. The planner accepts `off`, `shadow` or `virtual`. `shadow` publishes proposals with no `virtual_beams`; `virtual` also publishes the separately identified virtual beam annotations. Invalid optional configuration blocks this lane while reading continues.

The following values are authored synthetic examples, not this household's geometry or commissioned timing:

```json
{
  "mode": "shadow",
  "cadence_s": 1.0,
  "floor": {
    "schema": "den-walkable-floor-1",
    "profile_id": "synthetic-floor",
    "qualification": "virtual",
    "transform_id": "synthetic-transform",
    "scan_revision": "synthetic-scan",
    "depth_calibration_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
    "floor_z_m": 0.0,
    "walkable_polygon_m": [[-5, -5], [5, -5], [5, 5], [-5, 5]],
    "obstacles_m": [],
    "beam_radius_m": 1.2,
    "max_sigma_m": 0.08
  },
  "timing": {
    "synthetic-fixture": {
      "basis": "virtual_profile",
      "queue_bound_s": 0.02,
      "command_bound_s": 0.02,
      "transport_bound_s": 0.03
    }
  }
}
```

Active modes require an explicit scan/transform/depth-calibration binding, simple walkable polygon, bounded obstacle polygons and per-fixture timing. `qualification: virtual` records only the supplied planning profile. It is not physical qualification. Unknown obstacle heights are conservatively treated as vertical prisms. Floor-path clearance and fixture-to-patch beam obstruction are checked independently.

The fixture's existing axes, current aim, ranges, pending motion, S-curve and settling are used. A pending or unbounded fixture, unsupported horizon, unbounded footprint or arrival after the original evidence expiry abstains. The FWHM floor projection is a virtual footprint estimate; it does not establish an optical spill boundary or calibrated coverage probability.

## Timing and authority

Forecast samples are at `now + horizon`. `motion.forecast` already includes observation age; the walking caller does not add it again. The fixed-point target calculation includes cadence wait, remaining current motion, queue/command/transport bounds, motor travel and settling. It accepts only a converged, reachable target within the forecast horizon and original source lifetime.

Ordinary planning cadence is at least one second. The cached proposal is revalidated against fresh geometry and the observed fixture flight without renewing its lease. A new dark pre-aim, pending command or unexpected joint change invalidates the previous arrival solve. Measured stops, turns outside the retained footprint, unsafe or stale tracking, manual ownership, reading, Off and Stop clear the old virtual proposal immediately. Resume and On require a newly accepted frame captured after the control change; a delayed pre-control frame is insufficient.

Walking never creates a seated episode, invents Qwen task evidence, calls `_aim`/`_submit`, or changes the existing fixture rows, command sequence, room-glow policy or C12 trace. A seated person or existing reading light has priority. Held fixtures remain excluded, and the existing manual hold/end/recovery semantics are unchanged.

Current motion timing is explicitly `estimated_source_elapsed` with `time_qualified: false`. Proposals carry `authority: virtual_only` and `precision_qualified: false`. Conservative uncertainty envelopes are not calibrated route probabilities. Physical acceptance still requires commissioned fixture/clock/geometry and optical evidence.

## Wire contract

The schema is `den-path-lighting-1`. The current producer-generated authored example is `tools/den-live/fixtures/path-lighting-virtual.json`.

Top-level fields are `mode`, `status`, fixed `reason`, `evaluated_mono`, `original_valid_until_mono`, `valid_until_mono`, `subject`, `evidence`, `proposals` and `virtual_beams`. Optional `validation_source` identifies newer evidence used only to check an older cached target. Optional per-fixture refusal reasons explain unavailable candidates.

`subject` binds service run, camera epoch, frame, track, association revision, transform, scan revision and depth calibration. A proposal includes:

- Fixture and floor/profile identities, floor `target_m`, joint target, start, arrival and original expiry.
- The actual immutable `source_motion` receipt, including basis, observation/availability/expiry, velocity, observation IDs and timing qualification.
- Forecast covariance/envelope and footprint assumptions, plus the timing decomposition and convergence residual.
- Explicit virtual authority and lack of precision qualification.

`evidence.motion` has the actual disposition (`consumed` or `ignored`), selection flag and reason. Consumed means used by this shadow/virtual planner, not sent to hardware. The selected source remains the cached plan's original frame. A newer `validation_source` is separately marked consumed for safety validation and unselected for target generation.

`evidence.intent` uses `den.walking-intent/v1`: `absent`, `ignored` or `expired`, with fixed reason, request/clip/profile/model revision, original producer-monotonic observation/production/expiry and exact dependencies. All five raw option scores remain separate hypotheses with `calibrated_probability: false`; they are never normalized, averaged into a route or used to aim. `selected` and `consumed` remain false pending held-out admission.

`Preview.offer_intent(hint)` accepts the immutable producer `IntentHint` for diagnostics only. Valid exact retries do not republish or renew anything. A conflicting request ID, older receipt, changed binding, expired hint or pre-control clip is declined. Identity outcomes are retained in a bounded recent cache, with fixed-size hash tombstones for the current control/source scope. If exact history is unavailable and reuse cannot be ruled out, advisory metadata is conservatively declined; geometric forecasts continue. One sanitized `evidence.intent_rejected` record appears on the next ordinary tick with its original offered time and reason; it does not replace the active hint or trigger a command.

All times are on the existing producer monotonic clock, anchored by `spot_clock`. A viewer must enforce the proposal/beam's own original expiry, even when newer people/spot packets arrive. Reconnect, Stop, manual takeover or absent valid geometry must not leave a visible old annotation. Missing or malformed optional metadata must not reject otherwise valid legacy spotlight playback.

No model text, prompts, image data, clip evidence, crop mappings, blob references, URLs or credentials enter this projection. The Models producer separately owns model-stage observations and safe people-stream projections; the Visuals consumer owns realtime presentation.

## Verification boundary

Pure policy and real-Preview synthetic regressions cover compensation, reachability, cadence, stop/U-turn revocation, covariance/clock lineage, furniture, swept head/eye crossings, manual holds/restart, control generations and independent semantic expiry. The default and explicit-off paths retain the existing seated-reading snapshot and encoded trace.

These tests establish source contracts and virtual behavior. They do not establish real fixture timing, calibrated containment, model benefit or continuous production admission. This implementation performs no live service or GPU operation.
