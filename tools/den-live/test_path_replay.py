"""Deterministic synthetic replay contracts, not held-out motion/model accuracy."""
import copy
import dataclasses
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import motion
import path_replay as replay


class PersonLogImportTest(unittest.TestCase):
    def test_availability_is_exact_and_anchor_is_not_an_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "person-state.jsonl"
            record = dict(observation()["snapshot"], available_mono=100.050000007)
            path.write_text(json.dumps(dict(schema="den-person-state-1", anchor=dict(mono=100, ts=1800000000)))
                            + "\n" + json.dumps(record) + "\n", encoding="utf-8")
            self.assertEqual(replay.read_observations(path),
                             [dict(available_mono=100.050000007, snapshot=json.loads(json.dumps(record)))])
            del record["available_mono"]
            path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "person_log_availability_missing"):
                replay.read_observations(path)

    def test_jsonl_rejects_duplicate_and_nonfinite_source_receipts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "person-state.jsonl"
            for value in ('{"schema":"den-person-state-1","available_mono":NaN}',
                          '{"schema":"den-person-state-1","available_mono":100,"available_mono":101}'):
                path.write_text(value, encoding="utf-8")
                with self.assertRaises(ValueError):
                    replay.read_observations(path)


def binding(revision=1):
    return dict(service_run_id="synthetic-run", camera_epoch=1, track_id="synthetic-person",
                association_revision=revision, transform_id="synthetic-transform", scan_revision="synthetic-scan",
                depth_calibration_sha256="c" * 64)


def observation(at=100.0, xy=(0, 0), velocity=(1, 0), frame_id=1, revision=1):
    deps = binding(revision)
    state = motion.MotionState(
        service_run_id=deps["service_run_id"], camera_epoch=1, frame_id=frame_id, track_id=deps["track_id"],
        association_revision=revision, transform_id=deps["transform_id"], scan_revision=deps["scan_revision"],
        depth_calibration_sha256=deps["depth_calibration_sha256"], observed_mono=at, available_mono=at + .05,
        valid_until_mono=at + 1.4, xy_m=xy, velocity_mps=velocity,
        covariance=((.01, 0, 0, 0), (0, .01, 0, 0), (0, 0, .01, 0), (0, 0, 0, .01)),
        sample_count=3, history_span_s=.4, white_accel_sigma_mps32=0,
        registration_error_m=0, timing_error_s=.01, source_obs_ids=(f"synthetic-obs-{frame_id}",),
        timing_evidence_id=f"synthetic-age-{frame_id}")
    frame = dict(service_run_id=deps["service_run_id"], camera_epoch=1, frame_id=frame_id,
                 capture_mono=at, arrival_mono=at + .02, transform_id=deps["transform_id"],
                 scan_revision=deps["scan_revision"], depth_calibration_sha256=deps["depth_calibration_sha256"])
    track = dict(track_id=deps["track_id"], state="confirmed", xy_m=list(xy), position_basis="stereo",
                 current_obs_ids=list(state.source_obs_ids), association=dict(status="confident", revision=revision),
                 motion=dataclasses.asdict(state))
    return dict(available_mono=at + .05, snapshot=dict(schema="den-person-state-1", service_run_id=deps["service_run_id"],
                camera_epoch=1, seq=frame_id, frame=frame, status="fresh", presence="present", tracks=[track]))


def case():
    cutoff = 100.1
    return dict(schema=replay.SCHEMA, evidence_kind="synthetic_contract", observations=[observation()],
                decisions=[dict(decision_id="d1", cutoff_mono=cutoff, dependencies=binding())], recorded_candidates=[],
                truth=[dict(at_mono=cutoff + h, dependencies=binding(), xy_m=[cutoff + h - 100, 0], tangent_mps=[1, 0])
                       for h in motion.HORIZONS_S], actuation=[], events=[])


def projection(selected="left"):
    return dict(projection_id="synthetic-projection", revision="a" * 40, source_hint_id="recorded-hint",
                basis_frame_id=1, observed_mono=100.0, available_mono=100.08, valid_until_mono=101.0,
                dependencies=binding(), selected_candidate_id=selected,
                candidates=[dict(candidate_id=name, route_id=name,
                                 samples=[dict(at_mono=100.1 + h, xy_m=[h, sign],
                                               covariance_m2=[[.01, 0], [0, .01]]) for h in motion.HORIZONS_S])
                            for name, sign in (("left", 1), ("right", -1))])


def row(report, arm, horizon=.2):
    return next(r for r in report["rows"] if r["arm"] == arm and r["horizon_s"] == horizon)


class CausalityTests(unittest.TestCase):
    def test_current_position_and_real_cpu_forecast_are_paired_at_all_horizons(self):
        report = replay.replay(case())
        self.assertEqual(len(report["rows"]), 12)
        for h in motion.HORIZONS_S:
            self.assertAlmostEqual(row(report, "cpu_cv", h)["metrics"]["spatial_error_m"], 0)
            self.assertAlmostEqual(row(report, "current_position", h)["metrics"]["spatial_error_m"], .1 + h)
            self.assertIs(row(report, "cpu_cv", h)["time_qualified"], False)
        self.assertIs(report["live_model_accuracy_established"], False)
        self.assertIs(report["physical_admission"], False)

    def test_truth_changes_never_change_decisions(self):
        first, second = case(), case()
        for point in second["truth"]:
            point["xy_m"] = [999, -100]
            point["route_id"] = "different"
        def decisions(value):
            return replay.make_predictions(value["observations"], value["decisions"], value["recorded_candidates"])
        self.assertEqual(decisions(first), decisions(second))
        self.assertNotEqual(replay.replay(first)["summary"], replay.replay(second)["summary"])

    def test_future_available_observation_is_not_used(self):
        value = case()
        future = observation(100.08, xy=(999, 0), frame_id=2)
        future["available_mono"] = 100.2
        value["observations"].append(future)
        self.assertEqual(replay.replay(value)["rows"], replay.replay(case())["rows"])

    def test_latest_crossing_epoch_or_future_capture_revokes_old_support(self):
        for problem in ("crossing", "epoch", "future"):
            value = case()
            newer = observation(100.02, frame_id=2)
            if problem == "crossing": newer["snapshot"]["tracks"].append(copy.deepcopy(newer["snapshot"]["tracks"][0]))
            if problem == "epoch": newer["snapshot"]["camera_epoch"] = newer["snapshot"]["frame"]["camera_epoch"] = 2
            if problem == "future": newer["snapshot"]["frame"]["capture_mono"] = 101
            value["observations"].append(newer)
            self.assertTrue(all(r["xy_m"] is None for r in replay.replay(value)["rows"]))

    def test_motion_expiry_does_not_fall_back_to_older_forecast(self):
        value = case()
        value["decisions"][0]["cutoff_mono"] = 101.45
        report = replay.replay(value)
        self.assertIsNotNone(row(report, "current_position")["xy_m"])
        self.assertIsNone(row(report, "cpu_cv")["xy_m"])


class RecordedAdviserTests(unittest.TestCase):
    def test_semantic_influence_is_disabled_by_default(self):
        value = case()
        value["recorded_candidates"] = [projection()]
        report = replay.replay(value)
        self.assertEqual(row(report, "cpu_cv_recorded_adviser")["xy_m"], row(report, "cpu_cv")["xy_m"])
        self.assertEqual(row(report, "cpu_cv_recorded_adviser")["reason"], "semantic_influence_disabled")

    def test_experimental_modes_are_separate_and_never_averaged(self):
        value = case()
        value["recorded_candidates"] = [projection(None)]
        report = replay.replay(value, semantic_influence=True)
        adviser = row(report, "cpu_cv_recorded_adviser")
        self.assertIsNone(adviser["xy_m"])
        self.assertEqual([p["xy_m"][1] for p in adviser["candidate_targets"]], [1, -1])
        self.assertEqual(len(adviser["candidate_metrics"]), 2)
        self.assertEqual(next(s for s in report["summary"] if s["arm"] == adviser["arm"])["paired_truth_count"], 0)

    def test_recorded_selection_requires_current_geometry_binding_and_does_not_narrow_covariance(self):
        value = case()
        value["recorded_candidates"] = [projection()]
        report = replay.replay(value, semantic_influence=True)
        adviser, cv = row(report, "cpu_cv_recorded_adviser"), row(report, "cpu_cv")
        self.assertEqual(adviser["xy_m"], [.2, 1])
        self.assertEqual(adviser["candidate_id"], "left")
        self.assertGreaterEqual(adviser["covariance_m2"][0][0], cv["covariance_m2"][0][0])
        value["recorded_candidates"][0]["observed_mono"] = 99.9
        stale = row(replay.replay(value, semantic_influence=True), "cpu_cv_recorded_adviser")
        self.assertEqual(stale["xy_m"], cv["xy_m"])
        self.assertEqual(stale["reason"], "recorded_projection_stale_or_rebound")

    def test_future_or_expired_recorded_candidates_are_not_consumed(self):
        for changes in (dict(available_mono=100.2), dict(valid_until_mono=100.1), dict(basis_frame_id=2)):
            value = case()
            value["recorded_candidates"] = [projection() | changes]
            report = replay.replay(value, semantic_influence=True)
            self.assertEqual(row(report, "cpu_cv_recorded_adviser")["xy_m"], row(report, "cpu_cv")["xy_m"])


class MetricTests(unittest.TestCase):
    def test_missing_metadata_is_unavailable_not_zero(self):
        value = case()
        value["truth"] = []
        report = replay.replay(value)
        self.assertIsNone(row(report, "cpu_cv")["metrics"]["spatial_error_m"])
        for summary in report["summary"]:
            self.assertEqual(summary["metrics"]["arrival_beam_coverage"]["n"], 0)
            self.assertIsNone(summary["metrics"]["arrival_beam_coverage"]["mean"])
            self.assertIsNotNone(summary["metrics"]["arrival_beam_coverage"]["unavailable"])
        self.assertEqual(report["turn_recovery_unavailable"], "turn_event_annotations_missing")

    def test_arrival_beam_coverage_requires_actual_receipt_and_truth_at_arrival(self):
        value = case()
        value["actuation"] = [dict(decision_id="d1", arm="cpu_cv", horizon_s=.2, candidate_id=None,
            sent_mono=100.1, arrival_mono=100.3, beam_center_xy_m=[.3, 0], beam_radius_m=.1, receipt_basis="synthetic")]
        result = row(replay.replay(value), "cpu_cv")
        self.assertIs(result["metrics"]["arrival_beam_coverage"], True)
        self.assertAlmostEqual(result["metrics"]["command_to_arrival_s"], .2)
        self.assertEqual(result["arrival_receipt_basis"], "synthetic")
        value["actuation"][0]["arrival_mono"] = 100.31
        self.assertIsNone(row(replay.replay(value), "cpu_cv")["metrics"]["arrival_beam_coverage"])

    def test_stop_overshoot_wrong_route_and_conditional_uncertainty_are_labelled(self):
        value = case()
        value["recorded_candidates"] = [projection("right")]
        for truth in value["truth"]:
            truth.update(xy_m=[.2, 0], tangent_mps=[0, 0], stopped=True, stop_axis_xy=[1, 0], route_id="left")
        report = replay.replay(value, semantic_influence=True)
        self.assertAlmostEqual(row(report, "cpu_cv", 2)["metrics"]["target_stop_overshoot_m"], 1.9)
        self.assertIs(row(report, "cpu_cv", 2)["metrics"]["model_envelope_coverage"], False)
        self.assertIs(row(report, "cpu_cv_recorded_adviser")["metrics"]["wrong_path"], True)
        self.assertIsNone(row(report, "cpu_cv")["metrics"]["wrong_path"])
        self.assertIsNone(row(report, "cpu_cv")["metrics"]["along_path_lag_proxy_s"])

    def test_uturn_recovery_requires_annotated_event_and_sustained_postturn_matches(self):
        value = case()
        value["observations"] += [observation(101, (1, 0), (-1, 0), 2), observation(102, (0, 0), (-1, 0), 3)]
        value["decisions"] += [dict(decision_id=f"d{i}", cutoff_mono=t + .1, dependencies=binding()) for i, t in ((2, 101), (3, 102))]
        truth = {}
        for decision in value["decisions"]:
            for h in motion.HORIZONS_S:
                at = decision["cutoff_mono"] + h
                x = at - 100 if at <= 101 else 102 - at
                truth[at] = dict(at_mono=at, dependencies=binding(), xy_m=[x, 0], tangent_mps=[1 if at <= 101 else -1, 0])
        value["truth"] = list(truth.values())
        value["events"] = [dict(event_id="uturn", kind="turn", at_mono=101, dependencies=binding(),
                                recovery_tolerance_m=.001, required_consecutive=2)]
        report = replay.replay(value)
        recovery = next(r for r in report["turn_recovery"] if r["arm"] == "cpu_cv" and r["horizon_s"] == 1)
        self.assertAlmostEqual(recovery["turn_recovery_s"], .1)


class CommandTests(unittest.TestCase):
    def test_bundled_stop_uturn_and_crossing_fixture_is_contract_only(self):
        value = json.loads((Path(__file__).resolve().parent / "fixtures" / "path-replay-contract.json").read_text())
        report = replay.replay(value, semantic_influence=True)
        self.assertEqual(report["evidence_kind"], "synthetic_contract")
        crossing = [r for r in report["rows"] if r["decision_id"] == "d4"]
        self.assertEqual(len(crossing), 12)
        self.assertTrue(all(r["xy_m"] is None for r in crossing))
        self.assertTrue(all(r["physical_authority"] is False for r in report["rows"]))
        self.assertIsNone(row(report, "cpu_cv_recorded_adviser")["xy_m"])

    def test_cli_is_deterministic_and_writes_a_real_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "case.json", Path(temporary) / "report.json"
            source.write_text(replay.canonical(case()), encoding="utf-8")
            replay.main([str(source), "--output", str(output)])
            first = output.read_bytes()
            replay.main([str(source), "--output", str(output)])
            self.assertEqual(first, output.read_bytes())
            self.assertEqual(json.loads(first)["schema"], "den.path-replay-report/v1")

    def test_future_truth_cannot_be_hidden_in_decision_or_candidate_probability_fields(self):
        value = case()
        value["decisions"][0]["future_truth"] = [999, 0]
        with self.assertRaises(ValueError): replay.replay(value)
        value = case()
        value["recorded_candidates"] = [projection()]
        value["recorded_candidates"][0]["candidates"][0]["probability"] = .99
        with self.assertRaises(ValueError): replay.replay(value)


if __name__ == "__main__":
    unittest.main()
