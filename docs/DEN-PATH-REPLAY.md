# Paired predictive-path replay

`tools/den-live/path_replay.py` runs a deterministic, CPU-only comparison using
recorded numbers. It loads no models, camera frames or native inference library,
and emits no lighting commands. The bundled fixture validates contracts; it is
not held-out evidence, measured lighting performance or live model accuracy.

Run the bundled walk, stop, U-turn and crossing example:

```powershell
python -B tools/den-live/path_replay.py tools/den-live/fixtures/path-replay-contract.json --output replay-report.json
python -B tools/den-live/path_replay.py tools/den-live/fixtures/path-replay-contract.json --semantic-influence --output experimental-report.json
python -B -m unittest discover -s tools/den-live -p test_path_replay.py
```

Use a workstation Python interpreter. No host operation, model install or GPU
trial is involved. Output is canonical JSON without wall-clock or random fields.

## Comparison and causal boundary

Each decision supplies a monotonic cutoff and exact current person binding.
The three arms share the same available observations and horizons of 0.2, 0.5,
1 and 2 seconds after that cutoff:

- `current_position`: holds the newest measured tracker floor position. This
  is a positional baseline proxy, not an emulation of every existing fixture rule.
- `cpu_cv`: calls the real `motion.MotionState` and `motion.forecast`, preserving
  observation age, covariance, source timing status and evidence expiry. It does
  not estimate a second velocity or import a language model.
- `cpu_cv_recorded_adviser`: equals CPU CV by default. `--semantic-influence`
  enables only separately recorded experimental route projections.

`make_predictions(observations, decisions, recorded_candidates, ...)` has no
ground-truth argument. Scoring happens afterward in `score_predictions`.
Changing truth cannot change the replay's decisions. This code can enforce
recorded timestamps and bindings; it cannot prove an external author did not
create a supposedly recorded candidate using hindsight.

The newest available unsafe snapshot revokes older support. A crossing, missing
current association, camera epoch change, future capture/arrival or expired
observation never causes a search back to a convenient older confident person.
Motion receipts still require the producer's history gate, full covariance and
current frame/calibration binding. Source elapsed time remains
`estimated_source_elapsed`, `time_qualified: false`; the report does not establish
physical timing or calibrated error containment. A future sample may describe a
time beyond the current evidence lease; it does not renew the lease or authorize
an actuator to keep operating until that sample.

## Input record

The top-level schema is `den.path-replay/v1`, with exactly these fields:

| Field | Meaning |
| --- | --- |
| `evidence_kind` | `synthetic_contract` or `annotated_replay`; neither asserts held-out qualification |
| `observations` | `{available_mono, snapshot}` receipts; snapshot uses the `person_state.py` JSON projection |
| `decisions` | `{decision_id, cutoff_mono, dependencies}` |
| `recorded_candidates` | Experimental projections, described below; may be empty |
| `truth` | Evaluation-only labelled positions and optional path/event metadata |
| `actuation` | Optional measured/simulated/synthetic arrival receipts |
| `events` | Optional turn annotations used only to score recovery |

All arrays may be empty; absent evidence produces unavailable metrics. Limits
are 32 MiB input, 10,000 observation receipts, 1,000 decisions, 1,000 projections,
50,000 truth points, 12,000 arrival receipts and 128 turn events.

Every `dependencies` object contains exactly `service_run_id`, integer
`camera_epoch`, `track_id`, integer `association_revision`, `transform_id`,
`scan_revision`, and `depth_calibration_sha256`. Annotations must explicitly bind
future truth to the forecast's subject claim. This is evaluator bookkeeping,
not a new runtime identity binding across an ambiguous crossing.

The person-state log preserves full motion receipts when available. Wrap each
snapshot with its actual availability receipt; do not substitute camera arrival
for unknown processing completion. Anchor-only JSONL records are not snapshots.
Older logs without motion receipts can support a current-position baseline but
cannot reconstruct the missing velocity covariance or timing qualification.
New person-state log rows include full-precision `available_mono`, sampled after
serialization, as a conservative receipt of availability. Use
`--observations-jsonl <person-state.jsonl>` with an explicitly empty
`observations` array to import those rows. Anchor rows are skipped; missing
availability is refused rather than substituted with camera arrival. Decisions,
truth, fixture arrivals and any recorded adviser projections still require
separate annotations on the same monotonic run.

Truth rows require `at_mono`, `dependencies`, and `xy_m`. Optional fields are
`tangent_mps`, `route_id`, `stopped`, and `stop_axis_xy`. Truth is matched at the
exact target/arrival time within 1 ns numerical tolerance. There is no implicit
interpolation across a stop or turn, nearest-frame identity match, or invented
truth velocity. Missing exact truth remains unavailable.

## Experimental recorded projections

Native V-JEPA-LM returns semantic choices, not geographic routes. This tool does
not turn a raw `turn_left` score into a point, probability or motor angle. A
separately recorded projection supplies:

```json
{
  "projection_id": "recorded-projection-id",
  "revision": "0000000000000000000000000000000000000000",
  "source_hint_id": "recorded-hint-id",
  "basis_frame_id": 1,
  "observed_mono": 100.0,
  "available_mono": 100.08,
  "valid_until_mono": 101.0,
  "dependencies": {"COMPLETE_BINDING": "replace with the seven fields above"},
  "selected_candidate_id": null,
  "candidates": [{"candidate_id":"left", "route_id":"left-route",
    "samples":[{"at_mono":100.3, "xy_m":[1.0,2.0],
      "covariance_m2":[[0.01,0.0],[0.0,0.01]]}]}]
}
```

The example is explanatory, not an admissible real receipt. A real revision
must name the reviewed projection implementation. The replay checks its
40/64-character hexadecimal form but performs no network revision lookup.
Geometry must reference the exact current motion frame and observation time;
an older projection cannot override newer measured stopping/turning evidence.
Availability, expiry and person/calibration dependencies are checked again at
every decision. Targets must exist at the requested absolute horizon time.

Up to eight incompatible modes remain separate. Without an explicit valid
`selected_candidate_id`, the experimental arm abstains and reports each mode's
diagnostic scores. It never averages left/right routes through an obstacle.
Candidate uncertainty cannot narrow the CPU geometric envelope: combination uses
an SD sum under unknown correlation. This remains a conditional model envelope,
with no calibrated probability or physical admission implied.

## Metrics and missing evidence

Summaries include per-arm support counts and a spatial-error comparison limited
to decisions where all three arms have predictions and matching truth. An
abstaining experimental arm is never silently removed from only its own mean.
Non-paired descriptive means are separately labelled by their support counts.

| Metric | Required input and interpretation |
| --- | --- |
| `spatial_error_m` | Euclidean target error against exact-time truth |
| `along_path_error_m`, `cross_path_error_m` | Truth `tangent_mps`; signed along and absolute perpendicular error |
| `along_path_lag_proxy_s` | Negative along error divided by nonzero truth speed; a positional lag proxy, not measured network latency |
| `wrong_path` | Both predicted and truth route IDs; CPU/current-position arms do not invent a route label |
| `target_stop_overshoot_m` | Truth `stopped:true` and a nonzero pre-stop `stop_axis_xy`; positive error along that axis |
| `model_envelope_coverage` | Truth inside a radius of three times the largest positional covariance SD; no confidence percentage |
| `arrival_beam_coverage`, `arrival_spatial_error_m` | A beam-centre/radius arrival receipt plus truth at its actual arrival time |
| `command_to_arrival_s` | Actual receipt `arrival_mono - sent_mono` |
| `turn_recovery_s` | Explicit turn event and one to four consecutive post-turn predictions within its annotated tolerance |

Arrival receipts have `decision_id`, `arm`, `horizon_s`, `candidate_id` (null for
ordinary CV/current-position), `sent_mono`, `arrival_mono`, `beam_center_xy_m`,
`beam_radius_m`, and `receipt_basis` (`measured`, `simulation`, or `synthetic`).
The report preserves that basis and its counts. Simulated/synthetic coverage
does not establish actual photon arrival or fixture performance.

Turn events have `event_id`, `kind:"turn"`, `at_mono`, `dependencies`,
`recovery_tolerance_m`, and `required_consecutive`. Recovery is the time from the
event to the first decision in the first successful sustained streak. No such
streak is reported as `no_sustained_recovery_observed`; it is not an invented
recovery time at the end of the recording.

Unsupported values are null with zero support and an unavailable reason. The
tool makes no claim about obstacle clearance, real beam footprint, source-clock
drift, identity through crossings, held-out accuracy, or production admission.
