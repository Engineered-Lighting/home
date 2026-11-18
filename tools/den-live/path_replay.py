"""Deterministic paired shadow replay. Ground truth is scoring input only.

Uses the existing MotionState/forecast; it neither tracks again nor runs a
model. Recorded route projections are experimental inputs, never a geographic
interpretation manufactured from V-JEPA option scores. No actuator is called.
"""
import argparse
from bisect import bisect_left
import hashlib
import json
import math
from pathlib import Path
import statistics

import motion


SCHEMA = "den.path-replay/v1"
ARMS = ("current_position", "cpu_cv", "cpu_cv_recorded_adviser")
DEPENDENCIES = ("service_run_id", "camera_epoch", "track_id", "association_revision", "transform_id",
                "scan_revision", "depth_calibration_sha256")
METRICS = ("spatial_error_m", "along_path_error_m", "cross_path_error_m", "along_path_lag_proxy_s",
           "wrong_path", "target_stop_overshoot_m", "model_envelope_coverage", "arrival_beam_coverage",
           "arrival_spatial_error_m", "command_to_arrival_s")


def canonical(value):
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))


def parse_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_replay_key")
            result[key] = value
        return result
    def reject(_):
        raise ValueError("nonfinite_replay_json")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=reject)


def read_observations(path):
    """Read producer JSONL; older lines without actual availability remain unusable."""
    path = Path(path)
    if path.stat().st_size > 32 * 1024**2:
        raise ValueError("replay_input_too_large")
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = parse_json(line)
        if not isinstance(value, dict) or value.get("schema") != "den-person-state-1":
            raise ValueError("invalid_person_log")
        if "anchor" in value:
            continue
        available = value.get("available_mono")
        if not _finite(available):
            raise ValueError("person_log_availability_missing")
        result.append(dict(available_mono=available, snapshot=value))
        if len(result) > 10000:
            raise ValueError("replay_observations_over_bound")
    return result


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _xy(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 2 or not all(map(_finite, value))
            or any(abs(v) > 1000000 for v in value)):
        raise ValueError("invalid_replay_xy")
    return tuple(float(v) for v in value)


def _binding(value):
    if not isinstance(value, dict) or set(value) != set(DEPENDENCIES):
        raise ValueError("invalid_replay_dependencies")
    for key in DEPENDENCIES:
        item = value[key]
        if key in ("camera_epoch", "association_revision"):
            if type(item) is not int or item < 0:
                raise ValueError("invalid_replay_dependency_counter")
        elif not isinstance(item, str) or not 0 < len(item) <= 128:
            raise ValueError("invalid_replay_dependency_id")
    return value


def _snapshot_binding(snapshot, track):
    frame, association = snapshot.get("frame") or {}, track.get("association") or {}
    return {"service_run_id": snapshot.get("service_run_id"), "camera_epoch": snapshot.get("camera_epoch"),
            "track_id": track.get("track_id"), "association_revision": association.get("revision"),
            **{key: frame.get(key) for key in ("transform_id", "scan_revision", "depth_calibration_sha256")}}


def _observation(observations, decision):
    cutoff = decision["cutoff_mono"]
    available = [o for o in observations if o["available_mono"] <= cutoff]
    if not available:
        return None, None, "observation_unavailable"
    # An unsafe newest receipt revokes old support. Never search backwards for
    # the last convenient confident person after a crossing or camera restart.
    selected = max(enumerate(available), key=lambda item: (item[1]["available_mono"], item[0]))[1]
    snapshot = selected["snapshot"]
    frame, tracks = snapshot.get("frame") or {}, snapshot.get("tracks") or []
    if (snapshot.get("status") != "fresh" or snapshot.get("presence") != "present" or len(tracks) != 1
            or frame.get("service_run_id") != snapshot.get("service_run_id")
            or type(frame.get("camera_epoch")) is not int or frame.get("camera_epoch") != snapshot.get("camera_epoch")
            or type(frame.get("frame_id")) is not int or frame["frame_id"] <= 0
            or not _finite(frame.get("capture_mono")) or not _finite(frame.get("arrival_mono"))
            or not frame["capture_mono"] <= frame["arrival_mono"] <= selected["available_mono"] <= cutoff
            or not cutoff < frame["capture_mono"] + motion.MAX_AGE_S):
        return None, None, "latest_observation_unsafe_or_expired"
    track = tracks[0]
    try:
        current_binding = _binding(_snapshot_binding(snapshot, track))
    except ValueError:
        return None, None, "current_person_binding_changed"
    if (track.get("state") != "confirmed" or (track.get("association") or {}).get("status") != "confident"
            or track.get("position_basis") != "stereo" or not track.get("current_obs_ids")
            or current_binding != decision["dependencies"]):
        return None, None, "current_person_binding_changed"
    return snapshot, track, None


def _recorded(projections, decision, state):
    cutoff = decision["cutoff_mono"]
    known = [p for p in projections if p["available_mono"] <= cutoff]
    if not known:
        return None, "recorded_projection_unavailable"
    projection = max(known, key=lambda p: (p["available_mono"], p["projection_id"]))
    if (projection["dependencies"] != decision["dependencies"]
            or not projection["observed_mono"] <= projection["available_mono"] <= cutoff < projection["valid_until_mono"]
            or projection["basis_frame_id"] != state.frame_id
            or projection["observed_mono"] != state.observed_mono):
        return None, "recorded_projection_stale_or_rebound"
    return projection, None


def _combined_covariance(geometric, candidate):
    candidate = motion._covariance(candidate, 2)
    # Conditional modes remain separate. A recorded prior cannot narrow the
    # geometric uncertainty; unknown correlation uses an SD sum.
    radius = math.sqrt(motion._largest_eigenvalue(candidate))
    base_sd = math.sqrt(motion._largest_eigenvalue(geometric))
    added = 2 * base_sd * radius + radius ** 2
    return tuple(tuple(v + (added if i == j else 0) for j, v in enumerate(row)) for i, row in enumerate(geometric))


def make_predictions(observations, decisions, recorded_candidates, *, semantic_influence=False):
    """Pure causal decision phase. Its API deliberately has no truth argument."""
    result = []
    for decision in decisions:
        snapshot, track, refused = _observation(observations, decision)
        state, predicted = None, None
        if track is not None and track.get("motion") is not None:
            try:
                state = motion.MotionState(**track["motion"])
                predicted = motion.forecast(state, decision["cutoff_mono"], frame=snapshot["frame"],
                                            track_id=track["track_id"],
                                            association_revision=track["association"]["revision"])
            except (TypeError, ValueError):
                pass
        projection, projection_reason = (_recorded(recorded_candidates, decision, state)
                                         if predicted is not None else (None, "motion_unavailable"))
        for index, horizon in enumerate(motion.HORIZONS_S):
            common = {"decision_id": decision["decision_id"], "cutoff_mono": decision["cutoff_mono"],
                      "horizon_s": horizon, "at_mono": decision["cutoff_mono"] + horizon,
                      "dependencies": decision["dependencies"], "time_qualified": False,
                      "physical_authority": False}
            baseline = {**common, "arm": ARMS[0], "xy_m": None if track is None else list(_xy(track["xy_m"])),
                        "covariance_m2": None, "route_id": None, "reason": refused,
                        "source_available_mono": None if track is None else max(
                            o["available_mono"] for o in observations if o["snapshot"] is snapshot)}
            result.append(baseline)
            sample = predicted.samples[index] if predicted is not None else None
            geometric = {**common, "arm": ARMS[1], "xy_m": None if sample is None else list(sample.xy_m),
                         "covariance_m2": None if sample is None else sample.covariance_m2, "route_id": None,
                         "reason": refused or ("motion_unavailable" if sample is None else None),
                         "valid_until_mono": None if state is None else state.valid_until_mono,
                         "time_status": None if state is None else state.time_status}
            result.append(geometric)
            adviser = {**geometric, "arm": ARMS[2], "semantic_influence_enabled": semantic_influence,
                       "projection_id": None, "candidate_id": None, "candidate_targets": []}
            if not semantic_influence:
                adviser["reason"] = geometric["reason"] or "semantic_influence_disabled"
            elif projection is None:
                adviser["reason"] = geometric["reason"] or projection_reason
            else:
                adviser["projection_id"] = projection["projection_id"]
                adviser["projection_revision"] = projection["revision"]
                for candidate in projection["candidates"]:
                    point = next((p for p in candidate["samples"] if abs(p["at_mono"] - common["at_mono"]) <= 1e-9), None)
                    if point is not None:
                        adviser["candidate_targets"].append({"candidate_id": candidate["candidate_id"],
                            "route_id": candidate["route_id"], "xy_m": list(_xy(point["xy_m"])),
                            "covariance_m2": _combined_covariance(sample.covariance_m2, point["covariance_m2"])})
                selected_id = projection["selected_candidate_id"]
                chosen = next((c for c in adviser["candidate_targets"] if c["candidate_id"] == selected_id), None)
                if chosen is None:
                    adviser.update(xy_m=None, covariance_m2=None, route_id=None,
                                   reason="recorded_modes_unselected_or_horizon_missing")
                else:
                    adviser.update(chosen, reason=None, valid_until_mono=min(state.valid_until_mono,
                                                                            projection["valid_until_mono"]))
            result.append(adviser)
    return result


def _truth_at(truth, dependencies, at):
    times, rows = truth.get(canonical(dependencies), ((), ()))
    index = bisect_left(times, at - 1e-9)
    return rows[index] if index < len(rows) and abs(times[index] - at) <= 1e-9 else None


def _score_target(row, truth, multiplier):
    values = dict.fromkeys(METRICS)
    if row["xy_m"] is None or truth is None:
        return values
    delta = tuple(a - b for a, b in zip(row["xy_m"], truth["xy_m"]))
    values["spatial_error_m"] = math.hypot(*delta)
    velocity = truth.get("tangent_mps")
    speed = math.hypot(*velocity) if velocity is not None else 0
    if speed > 1e-9:
        direction = tuple(v / speed for v in velocity)
        along = sum(a * b for a, b in zip(delta, direction))
        values.update(along_path_error_m=along,
                      cross_path_error_m=abs(delta[0] * direction[1] - delta[1] * direction[0]),
                      along_path_lag_proxy_s=-along / speed)
    if row.get("route_id") is not None and truth.get("route_id") is not None:
        values["wrong_path"] = row["route_id"] != truth["route_id"]
    if truth.get("stopped") is True and truth.get("stop_axis_xy") is not None:
        axis = truth["stop_axis_xy"]
        length = math.hypot(*axis)
        if length > 1e-9:
            values["target_stop_overshoot_m"] = max(0.0, sum(d * a / length for d, a in zip(delta, axis)))
    if row.get("covariance_m2") is not None:
        radius = multiplier * math.sqrt(motion._largest_eigenvalue(row["covariance_m2"]))
        values["model_envelope_coverage"] = values["spatial_error_m"] <= radius
    return values


def score_predictions(predictions, truth, actuation, events, *, uncertainty_multiplier=3.0):
    """Evaluation-only phase. No result from here can feed a replay decision."""
    truth_index = {}
    for row in truth:
        truth_index.setdefault(canonical(row["dependencies"]), []).append(row)
    truth_index = {key: (tuple(r["at_mono"] for r in sorted(rows, key=lambda r: r["at_mono"])),
                         tuple(sorted(rows, key=lambda r: r["at_mono"]))) for key, rows in truth_index.items()}
    receipts = {(r["decision_id"], r["arm"], r["horizon_s"], r["candidate_id"]): r for r in actuation}
    scored, groups, eligible = [], {}, {}
    for row in predictions:
        reference = _truth_at(truth_index, row["dependencies"], row["at_mono"])
        values = _score_target(row, reference, uncertainty_multiplier)
        receipt = receipts.get((row["decision_id"], row["arm"], row["horizon_s"], row.get("candidate_id")))
        if receipt is not None and row["xy_m"] is not None:
            arrived = _truth_at(truth_index, row["dependencies"], receipt["arrival_mono"])
            values["command_to_arrival_s"] = receipt["arrival_mono"] - receipt["sent_mono"]
            if arrived is not None:
                error = math.dist(receipt["beam_center_xy_m"], arrived["xy_m"])
                values["arrival_spatial_error_m"] = error
                values["arrival_beam_coverage"] = error <= receipt["beam_radius_m"]
        candidate_metrics = []
        for candidate in row.get("candidate_targets", []):
            candidate_metrics.append({"candidate_id": candidate["candidate_id"], "route_id": candidate["route_id"],
                                      "metrics": _score_target(candidate, reference, uncertainty_multiplier)})
        scored.append({**row, "metrics": values, "candidate_metrics": candidate_metrics,
                       "arrival_receipt_basis": receipt["receipt_basis"] if receipt is not None else None,
                       "truth_available": reference is not None})
        groups.setdefault((row["arm"], row["horizon_s"]), []).append(scored[-1])
        if values["spatial_error_m"] is not None:
            eligible.setdefault((row["decision_id"], row["horizon_s"]), set()).add(row["arm"])
    summary = []
    for horizon in motion.HORIZONS_S:
        paired = {identity for (identity, h), arms in eligible.items() if h == horizon and len(arms) == len(ARMS)}
        for arm in ARMS:
            rows = groups.get((arm, horizon), [])
            summary.append({"arm": arm, "horizon_s": horizon, "prediction_count": sum(r["xy_m"] is not None for r in rows),
                "paired_truth_count": len(paired), "metrics": {name: _aggregate([r["metrics"][name] for r in rows]) for name in METRICS},
                "arrival_receipt_basis_counts": {basis: sum(r["arrival_receipt_basis"] == basis for r in rows)
                                                  for basis in ("measured", "simulation", "synthetic")},
                "paired_spatial_error_m": _aggregate([r["metrics"]["spatial_error_m"] for r in rows if r["decision_id"] in paired])})
    recovery = []
    for event in events:
        for arm in ARMS:
            for horizon in motion.HORIZONS_S:
                rows = sorted((r for r in groups.get((arm, horizon), [])
                               if r["dependencies"] == event["dependencies"] and r["cutoff_mono"] >= event["at_mono"]),
                              key=lambda r: r["cutoff_mono"])
                streak, first, recovered = 0, None, None
                for row in rows:
                    error = row["metrics"]["spatial_error_m"]
                    if error is not None and error <= event["recovery_tolerance_m"]:
                        first = row["cutoff_mono"] if streak == 0 else first
                        streak += 1
                        if streak >= event["required_consecutive"]:
                            recovered = first - event["at_mono"]
                            break
                    else:
                        streak, first = 0, None
                recovery.append({"event_id": event["event_id"], "arm": arm, "horizon_s": horizon,
                                 "turn_recovery_s": recovered,
                                 "reason": None if recovered is not None else "no_sustained_recovery_observed"})
    return scored, summary, recovery


def _aggregate(values):
    values = [float(v) for v in values if v is not None]
    return {"n": len(values), "mean": statistics.mean(values) if values else None,
            "median": statistics.median(values) if values else None,
            "unavailable": None if values else "required_prediction_or_measurement_metadata_missing"}


def validate_case(case):
    keys = {"schema", "evidence_kind", "observations", "decisions", "recorded_candidates", "truth", "actuation", "events"}
    if (not isinstance(case, dict) or set(case) != keys or case["schema"] != SCHEMA
            or case["evidence_kind"] not in ("synthetic_contract", "annotated_replay")):
        raise ValueError("invalid_replay_schema")
    for key, maximum in (("observations", 10000), ("decisions", 1000), ("recorded_candidates", 1000),
                         ("truth", 50000), ("actuation", 12000), ("events", 128)):
        if not isinstance(case[key], list) or len(case[key]) > maximum:
            raise ValueError("replay_collection_unbounded")
    seen = set()
    for decision in case["decisions"]:
        if (not isinstance(decision, dict) or set(decision) != {"decision_id", "cutoff_mono", "dependencies"} or not _finite(decision["cutoff_mono"])
                or not isinstance(decision["decision_id"], str) or not 0 < len(decision["decision_id"]) <= 128
                or decision["decision_id"] in seen):
            raise ValueError("invalid_replay_decision")
        seen.add(decision["decision_id"])
        _binding(decision["dependencies"])
    for receipt in case["observations"]:
        if (not isinstance(receipt, dict) or set(receipt) != {"available_mono", "snapshot"}
                or not _finite(receipt["available_mono"]) or not isinstance(receipt["snapshot"], dict)):
            raise ValueError("invalid_replay_observation")
    seen = set()
    for projection in case["recorded_candidates"]:
        required = {"projection_id", "revision", "source_hint_id", "basis_frame_id", "observed_mono", "available_mono",
                    "valid_until_mono", "dependencies", "selected_candidate_id", "candidates"}
        if (not isinstance(projection, dict) or set(projection) != required
                or any(not _finite(projection[k]) for k in ("observed_mono", "available_mono", "valid_until_mono"))
                or not isinstance(projection["revision"], str) or len(projection["revision"]) not in (40, 64)
                or any(c not in "0123456789abcdef" for c in projection["revision"])
                or not isinstance(projection["projection_id"], str) or not 0 < len(projection["projection_id"]) <= 128
                or projection["projection_id"] in seen
                or not isinstance(projection["source_hint_id"], str) or not 0 < len(projection["source_hint_id"]) <= 128
                or type(projection["basis_frame_id"]) is not int
                or not isinstance(projection["candidates"], list) or not 1 <= len(projection["candidates"]) <= 8):
            raise ValueError("invalid_recorded_projection")
        seen.add(projection["projection_id"])
        _binding(projection["dependencies"])
        ids = set()
        for candidate in projection["candidates"]:
            if (not isinstance(candidate, dict) or set(candidate) != {"candidate_id", "route_id", "samples"}
                    or not isinstance(candidate["candidate_id"], str) or not 0 < len(candidate["candidate_id"]) <= 128
                    or candidate["candidate_id"] in ids
                    or not isinstance(candidate["route_id"], str) or not 0 < len(candidate["route_id"]) <= 128
                    or not isinstance(candidate["samples"], list) or not 1 <= len(candidate["samples"]) <= 4):
                raise ValueError("invalid_recorded_candidate")
            ids.add(candidate["candidate_id"])
            times = set()
            for point in candidate["samples"]:
                if (not isinstance(point, dict) or set(point) != {"at_mono", "xy_m", "covariance_m2"}
                        or not _finite(point["at_mono"]) or any(abs(point["at_mono"] - t) <= 1e-9 for t in times)):
                    raise ValueError("invalid_recorded_candidate_sample")
                times.add(point["at_mono"])
                _xy(point["xy_m"])
                motion._covariance(point["covariance_m2"], 2)
        if projection["selected_candidate_id"] is not None and projection["selected_candidate_id"] not in ids:
            raise ValueError("recorded_selected_mode_missing")
    seen = set()
    for truth in case["truth"]:
        if (not isinstance(truth, dict) or set(truth) - {"at_mono", "dependencies", "xy_m", "tangent_mps", "route_id", "stopped", "stop_axis_xy"}
                or not _finite(truth.get("at_mono"))):
            raise ValueError("invalid_replay_truth")
        _binding(truth["dependencies"])
        _xy(truth["xy_m"])
        identity = (canonical(truth["dependencies"]), truth["at_mono"])
        if identity in seen:
            raise ValueError("duplicate_replay_truth")
        seen.add(identity)
        for key in ("tangent_mps", "stop_axis_xy"):
            if key in truth:
                _xy(truth[key])
        if "stopped" in truth and type(truth["stopped"]) is not bool:
            raise ValueError("invalid_truth_stop_label")
        if "route_id" in truth and (not isinstance(truth["route_id"], str) or not 0 < len(truth["route_id"]) <= 128):
            raise ValueError("invalid_truth_route_label")
    times_by_binding = {}
    for row in case["truth"]:
        times_by_binding.setdefault(canonical(row["dependencies"]), []).append(row["at_mono"])
    for times in times_by_binding.values():
        ordered = sorted(times)
        if any(b - a <= 1e-9 for a, b in zip(ordered, ordered[1:])):
            raise ValueError("ambiguous_near_duplicate_replay_truth")
    seen = set()
    decisions = {row["decision_id"]: row for row in case["decisions"]}
    for row in case["actuation"]:
        required = {"decision_id", "arm", "horizon_s", "candidate_id", "sent_mono", "arrival_mono",
                    "beam_center_xy_m", "beam_radius_m", "receipt_basis"}
        if (not isinstance(row, dict) or set(row) != required or row["arm"] not in ARMS
                or not _finite(row["horizon_s"]) or row["horizon_s"] not in motion.HORIZONS_S
                or row["decision_id"] not in decisions
                or (row["candidate_id"] is not None
                    and (not isinstance(row["candidate_id"], str) or not 0 < len(row["candidate_id"]) <= 128))
                or not all(_finite(row[k]) for k in ("sent_mono", "arrival_mono", "beam_radius_m"))
                or row["arrival_mono"] < row["sent_mono"] or row["sent_mono"] < decisions[row["decision_id"]]["cutoff_mono"]
                or not 0 < row["beam_radius_m"] <= 10
                or row["receipt_basis"] not in ("measured", "simulation", "synthetic")):
            raise ValueError("invalid_replay_actuation_receipt")
        identity = tuple(row[k] for k in ("decision_id", "arm", "horizon_s", "candidate_id"))
        if identity in seen:
            raise ValueError("duplicate_replay_actuation_receipt")
        seen.add(identity)
        _xy(row["beam_center_xy_m"])
    for event in case["events"]:
        if (not isinstance(event, dict) or set(event) != {"event_id", "kind", "at_mono", "dependencies", "recovery_tolerance_m", "required_consecutive"}
                or not isinstance(event["event_id"], str) or not 0 < len(event["event_id"]) <= 128
                or event["kind"] != "turn" or not _finite(event["at_mono"])
                or not _finite(event["recovery_tolerance_m"]) or not 0 < event["recovery_tolerance_m"] <= 10
                or type(event["required_consecutive"]) is not int or not 1 <= event["required_consecutive"] <= 4):
            raise ValueError("invalid_replay_turn_event")
        _binding(event["dependencies"])
    return case


def replay(case, *, semantic_influence=False):
    validate_case(case)
    predictions = make_predictions(case["observations"], case["decisions"], case["recorded_candidates"],
                                   semantic_influence=semantic_influence)
    rows, summary, recovery = score_predictions(predictions, case["truth"], case["actuation"], case["events"])
    return {"schema": "den.path-replay-report/v1", "evidence_kind": case["evidence_kind"],
            "input_sha256": hashlib.sha256(canonical(case).encode()).hexdigest(),
            "semantic_influence_enabled": semantic_influence, "live_model_accuracy_established": False,
            "physical_admission": False, "source_time_qualified": False,
            "uncertainty_multiplier": 3.0, "uncertainty_semantics": "conditional_model_radius_not_calibrated_probability",
            "rows": rows, "summary": summary, "turn_recovery": recovery,
            "turn_recovery_unavailable": None if case["events"] else "turn_event_annotations_missing"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--output")
    parser.add_argument("--observations-jsonl", help="Use producer receipts in a case whose observations array is empty")
    parser.add_argument("--semantic-influence", action="store_true", help="Enable experimental recorded route projection only")
    args = parser.parse_args(argv)
    path = Path(args.input)
    if path.stat().st_size > 32 * 1024**2:
        raise ValueError("replay_input_too_large")
    case = parse_json(path.read_text(encoding="utf-8"))
    if args.observations_jsonl:
        if not isinstance(case, dict) or case.get("observations") != []:
            raise ValueError("explicit_empty_observations_required")
        case["observations"] = read_observations(args.observations_jsonl)
    report = replay(case, semantic_influence=args.semantic_influence)
    output = canonical(report) + "\n"
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
