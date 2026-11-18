"""Pure walking consumer regressions, using the real pinned joints and planner."""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

import coordinator_live
import direction
import motion
import sofa_check
import walking


def state(**changes):
    values = dict(service_run_id="walk-run", camera_epoch=2, frame_id=10, track_id="walker",
        association_revision=3, transform_id="walk-transform", scan_revision="walk-scan",
        depth_calibration_sha256="c" * 64, observed_mono=100.0, available_mono=100.05,
        valid_until_mono=101.5, xy_m=(0.0, 0.0), velocity_mps=(0.5, 0.0),
        covariance=((0.0001, 0, 0, 0), (0, 0.0001, 0, 0),
                    (0, 0, 0.0001, 0), (0, 0, 0, 0.0001)),
        sample_count=4, history_span_s=0.6, white_accel_sigma_mps32=0.0,
        registration_error_m=0.0, timing_error_s=0.0, source_obs_ids=("walk-obs-10",))
    return motion.MotionState(**(values | changes))


def snapshot(value=None, **changes):
    value = value or state()
    frame = {key: getattr(value, key) for key in walking.KEYS if key not in ("track_id", "association_revision")}
    frame["capture_mono"] = value.observed_mono
    track = dict(track_id=value.track_id, association_revision=value.association_revision,
        state="confirmed", association_status="confident", position_basis="stereo",
        current_obs_ids=value.source_obs_ids, seated={"state": False}, motion=value,
        head_point_m={"xyz": [value.xy_m[0], value.xy_m[1], 1.7]}, posture="walking")
    return dict(service_run_id=value.service_run_id, camera_epoch=value.camera_epoch, seq=value.frame_id,
                frame=frame, tracks=[track], status="fresh", presence="present") | changes


def configuration(mode="virtual"):
    return {"mode": mode, "cadence_s": 1.0, "floor": {
        "schema": "den-walkable-floor-1", "profile_id": "synthetic-floor", "qualification": "virtual",
        "transform_id": "walk-transform", "scan_revision": "walk-scan", "depth_calibration_sha256": "c" * 64,
        "floor_z_m": 0.0, "walkable_polygon_m": [[-5, -5], [5, -5], [5, 5], [-5, 5]],
        "obstacles_m": [], "beam_radius_m": 1.2, "max_sigma_m": 0.08},
        "timing": {"F3": {"basis": "virtual_profile", "queue_bound_s": 0.02,
                           "command_bound_s": 0.02, "transport_bound_s": 0.03}}}


def fixtures(core):
    origin, reference = [0, -1, 2.5], [0, 1, 0]
    current = tuple(map(core.joints.to_cdeg, core.joints.room_to_joints([0.3, 0, 0], origin, reference)))
    return {"F3": {"aim_origin_m": origin, "horizontal_world_reference": reference,
        "current": current, "ranges": ((-17000, 17000), (-8500, 8500)), "pending": None, "glide": None,
        "profile": {"label": "synthetic-fast-profile", "fictional": True,
            "axes": [{"v_max": 180, "a_max": 720, "j_max": 2880}] * 2,
            "settle_s": 0.04, "beam_fwhm_deg": 8,
            "lit_motion": False, "retarget_mid_move": False}}}


class WalkingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tree.cleanup)
        cls.core, _learn = coordinator_live.load_core(sofa_check.archive(Path(cls.tree.name), "coordinator"))

    def setUp(self):
        self.config = configuration()
        self.rows = fixtures(self.core)
        self.planner = walking.Planner(self.core, self.config)

    def evaluate(self, value=None, now=100.2, **keywords):
        return self.planner.evaluate(value or snapshot(), self.rows, now,
                                     **({"on": True, "stopped": False} | keywords))

    def eligible(self, result):
        self.assertEqual(result["status"], "eligible", result)
        self.assertEqual(len(result["proposals"]), 1)
        return result["proposals"][0]

    def test_off_default_and_invalid_optional_configuration_are_bounded(self):
        result = walking.Planner(self.core).evaluate(snapshot(), self.rows, 100.2, on=True, stopped=False)
        self.assertEqual((result["mode"], result["status"], result["proposals"]), ("off", "off", []))
        for config in ([], {"mode": "physical"}, {"mode": "virtual"}, {"mode": "virtual", "floor": {}},
                       {"mode": "virtual", "cadence_s": math.nan}, {"unknown": 1}):
            with self.subTest(config=config):
                result = walking.Planner(self.core, config).evaluate(snapshot(), self.rows, 100.2, on=True, stopped=False)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["virtual_beams"], [])
                json.dumps(result, allow_nan=False)

    def test_target_is_forecast_at_achievable_illumination_time_with_age_once(self):
        proposal = self.eligible(self.evaluate())
        timing = proposal["timing"]
        self.assertAlmostEqual(timing["observation_age_s"], 0.2)
        value = state()
        expected_x = value.xy_m[0] + value.velocity_mps[0] * (proposal["arrival_mono"] - value.observed_mono)
        expected_x += proposal["footprint"]["heading_lead_m"]
        self.assertAlmostEqual(proposal["target_m"][0], expected_x, delta=0.002)
        self.assertGreater(timing["motor_s"], 0)
        total = sum(timing[key] for key in ("cadence_wait_s", "current_motion_wait_s", "queue_bound_s",
            "command_bound_s", "transport_bound_s", "motor_s", "settle_s", "fade_to_useful_s"))
        self.assertAlmostEqual(proposal["arrival_mono"] - 100.2, total, delta=0.002)
        self.assertEqual(proposal["source_motion"]["frame_id"], 10)
        self.assertEqual(proposal["valid_until_mono"], 101.5)
        self.assertLess(proposal["arrival_mono"], 101.5)
        self.assertFalse(proposal["uncertainty"]["calibrated_probability"])

    def test_motor_and_settle_use_actual_profile_not_distance_alone(self):
        fast = self.eligible(self.evaluate())
        self.rows["F3"]["profile"]["settle_s"] = 1.2
        self.planner = walking.Planner(self.core, self.config)
        slow = self.evaluate()
        self.assertEqual(slow["proposals"], [])
        self.assertEqual(slow["fixture_reasons"]["F3"], "arrival_after_evidence_expiry")
        self.assertLess(fast["arrival_mono"], 101.5)

    def test_slower_near_fixture_does_not_beat_achievable_fast_fixture(self):
        slow = copy.deepcopy(self.rows["F3"])
        slow["profile"]["settle_s"] = 1.4
        self.rows["near-slow"] = slow
        self.config["timing"]["near-slow"] = dict(self.config["timing"]["F3"])
        self.planner = walking.Planner(self.core, self.config)
        self.assertEqual(self.eligible(self.evaluate())["fixture_id"], "F3")

    def test_queue_transport_and_original_expiry_cannot_be_hidden(self):
        for key in ("queue_bound_s", "command_bound_s", "transport_bound_s"):
            with self.subTest(key=key):
                config = configuration()
                config["timing"]["F3"][key] = 1.4
                planner = walking.Planner(self.core, config)
                result = planner.evaluate(snapshot(), self.rows, 100.2, on=True, stopped=False)
                self.assertEqual(result["proposals"], [])
        old = state(valid_until_mono=100.4)
        self.assertEqual(self.evaluate(snapshot(old))["proposals"], [])

    def test_current_glide_waits_until_rest_and_includes_settle(self):
        row = self.rows["F3"]
        row["glide"] = direction.Glide(row["current"], row["current"], row["profile"]["axes"], 100.1, settle_s=0.2)
        proposal = self.eligible(self.evaluate())
        self.assertAlmostEqual(proposal["timing"]["current_motion_wait_s"], 0.1)
        self.assertGreaterEqual(proposal["start_mono"], 100.3)

    def test_unrelated_dark_preaim_motion_invalidates_a_cached_arrival_during_cadence(self):
        def new_glide(row):
            destination = (row["current"][0] + 200, row["current"][1])
            row["glide"] = direction.Glide(row["current"], destination, row["profile"]["axes"], 100.25)
            row["current"] = row["glide"].at(100.3)

        for change in (lambda row: row.update(current=(row["current"][0] + 100, row["current"][1])),
                       lambda row: row.update(pending={"reason": "pre_aim"}), new_glide):
            with self.subTest(change=change):
                self.rows = fixtures(self.core)
                self.planner = walking.Planner(self.core, self.config)
                self.eligible(self.evaluate())
                change(self.rows["F3"])
                result = self.evaluate(now=100.3)
                self.assertEqual((result["status"], result["proposals"], result["virtual_beams"]),
                                 ("abstained", [], []))
                self.assertEqual(result["reason"], "fixture_motion_changed")
                self.assertEqual({key: result["evidence"]["motion"][key] for key in ("disposition", "selected", "reason")},
                    {"disposition": "ignored", "selected": False, "reason": "fixture_motion_changed"})
                self.assertEqual(self.planner.last_planned_mono, 100.2)
                self.assertIsNone(self.planner.last_plan)

    def test_original_known_glide_can_advance_and_complete_without_changing_cached_arrival(self):
        row = self.rows["F3"]
        target = row["current"]
        start = (target[0] - 100, target[1])
        glide = direction.Glide(start, target, row["profile"]["axes"], 100.0, settle_s=.04)
        self.assertGreater(glide.arrival, 100.2)
        self.assertLess(glide.arrival, 100.6)
        row.update(glide=glide, current=glide.at(100.2))
        original = self.eligible(self.evaluate())
        for now in (100.25, glide.arrival + .01):
            row["current"] = glide.at(now)
            if now >= glide.arrival:
                row.update(glide=None, current=glide.target)
            result = self.evaluate(now=now)
            self.assertEqual(result["status"], "cadence", result)
            self.assertEqual(result["proposals"], [original])
            self.assertEqual(result["valid_until_mono"], original["valid_until_mono"])

    def test_replaced_or_mutated_known_glide_is_not_the_original_observed_flight(self):
        for replace in (True, False):
            with self.subTest(replace=replace):
                self.rows = fixtures(self.core)
                self.planner = walking.Planner(self.core, self.config)
                row = self.rows["F3"]
                target = row["current"]
                start = (target[0] - 100, target[1])
                old = direction.Glide(start, target, row["profile"]["axes"], 100.0, settle_s=.04)
                row.update(glide=old, current=old.at(100.2))
                self.eligible(self.evaluate())
                if replace:
                    row["glide"] = direction.Glide(start, target, row["profile"]["axes"], 100.0, settle_s=.04)
                else:
                    old.settle += .01
                row["current"] = row["glide"].at(100.25)
                result = self.evaluate(now=100.25)
                self.assertEqual(result["virtual_beams"], [])
                self.assertEqual(result["reason"], "fixture_motion_changed")

    def test_pending_move_and_unknown_current_motion_abstain(self):
        for key, value in (("pending", {}), ("glide", {})):
            with self.subTest(key=key):
                self.rows = fixtures(self.core)
                self.rows["F3"][key] = value
                self.assertEqual(self.evaluate()["proposals"], [])

    def test_cadence_keeps_original_plan_without_extending_expiry(self):
        first = self.eligible(self.evaluate())
        fresh = state(observed_mono=100.1, available_mono=100.15, valid_until_mono=101.6,
                      frame_id=11, xy_m=(0.05, 0), source_obs_ids=("walk-obs-11",))
        second = self.evaluate(snapshot(fresh), now=100.3)
        self.assertEqual(second["status"], "cadence", second)
        self.assertEqual(second["proposals"], [first])
        self.assertEqual(second["proposals"][0]["valid_until_mono"], 101.5)
        self.assertEqual(second["valid_until_mono"], 101.5)
        self.assertEqual(second["original_valid_until_mono"], 101.5)
        self.assertEqual(second["evidence"]["motion"]["frame_id"], 10)
        self.assertEqual(second["validation_source"]["frame_id"], 11)
        self.assertEqual(self.planner.last_planned_mono, 100.2)

    def test_selected_and_validation_dispositions_preserve_the_raw_cached_receipt(self):
        first = self.eligible(self.evaluate())
        original_source = copy.deepcopy(first["source_motion"])
        fresh = state(observed_mono=100.1, available_mono=100.15, valid_until_mono=101.6,
                      frame_id=11, xy_m=(0.05, 0), source_obs_ids=("walk-obs-11",))
        result = self.evaluate(snapshot(fresh), now=100.3)
        self.assertEqual(result["evidence"]["motion"], dict(original_source,
            disposition="consumed", selected=True, reason="geometric_velocity"))
        self.assertEqual({key: result["validation_source"][key] for key in
            ("frame_id", "disposition", "selected", "reason")},
            {"frame_id": 11, "disposition": "consumed", "selected": False, "reason": "cadence_safety_validation"})
        self.assertEqual(first["source_motion"], original_source)
        self.assertEqual(self.planner.last_plan["proposal"]["source_motion"], original_source)
        self.assertEqual(result["valid_until_mono"], original_source["valid_until_mono"])
        self.assertNotIn("disposition", original_source)

    def test_ignored_motion_reports_the_actual_rejection_without_mutating_the_state(self):
        value = state()
        result = self.evaluate(snapshot(value), manual_holds={"F3"})
        self.assertEqual(result["proposals"], [])
        evidence = result["evidence"]["motion"]
        self.assertEqual({key: evidence[key] for key in ("frame_id", "disposition", "selected", "reason")},
            {"frame_id": 10, "disposition": "ignored", "selected": False, "reason": "no_achievable_fixture"})
        self.assertEqual(result["fixture_reasons"]["F3"], "manual_hold")
        self.assertEqual(value, state())
        self.assertFalse(hasattr(value, "disposition"))

    def test_fresh_stop_and_uturn_clear_during_cadence(self):
        for velocity, reason in (((0, 0), "stationary"), ((-0.5, 0), "motion_changed")):
            with self.subTest(velocity=velocity):
                self.planner = walking.Planner(self.core, self.config)
                self.eligible(self.evaluate())
                fresh = state(observed_mono=100.1, available_mono=100.15, valid_until_mono=101.6,
                    frame_id=11, velocity_mps=velocity, source_obs_ids=("walk-obs-11",))
                result = self.evaluate(snapshot(fresh), now=100.3)
                self.assertEqual(result["reason"], reason)
                self.assertEqual(result["virtual_beams"], [])

    def test_stale_stop_off_manual_and_reading_clear_without_waiting_for_cadence(self):
        for keywords in ({"stopped": True}, {"on": False}, {"manual_holds": (("F3", "spot"),)},
                         {"reading_active": True}, {"value": snapshot(status="stale")}):
            with self.subTest(keywords=keywords):
                self.planner = walking.Planner(self.core, self.config)
                self.eligible(self.evaluate())
                result = self.evaluate(now=100.3, **keywords)
                self.assertEqual(result["virtual_beams"], [])
                self.assertIsNone(self.planner.last_plan)

    def test_each_manual_part_preserves_spot_ownership(self):
        self.assertEqual(self.evaluate(manual_holds={"F3"})["proposals"], [])
        self.planner = walking.Planner(self.core, self.config)
        self.eligible(self.evaluate(manual_holds=(("F3", "radial:1"),)))

    def test_shadow_has_proposals_and_no_virtual_or_actuator_output(self):
        self.planner = walking.Planner(self.core, configuration("shadow"))
        result = self.evaluate()
        self.eligible(result)
        self.assertEqual(result["virtual_beams"], [])
        self.assertFalse(hasattr(self.planner, "send"))
        self.assertEqual(set(result) & {"commands", "moves", "calls"}, set())

    def test_uncertainty_and_unbounded_footprint_abstain(self):
        covariance = tuple(tuple(0.04 if i == j else 0 for j in range(4)) for i in range(4))
        self.assertEqual(self.evaluate(snapshot(state(covariance=covariance)))["proposals"], [])
        self.rows["F3"]["profile"]["beam_fwhm_deg"] = 120
        self.assertEqual(self.evaluate()["proposals"], [])

    def test_walkable_boundary_and_furniture_block_entire_uncertain_patch_and_route(self):
        for polygon, obstacles in (([[-.1, -1], [1, -1], [1, 1], [-.1, 1]], []),
            ([[-5, -5], [5, -5], [5, 5], [-5, 5]], [[[.2, -.5], [.3, -.5], [.3, .5], [.2, .5]]])):
            with self.subTest(obstacles=obstacles):
                config = configuration()
                config["floor"].update(walkable_polygon_m=polygon, obstacles_m=obstacles)
                self.planner = walking.Planner(self.core, config)
                result = self.evaluate()
                self.assertEqual(result["virtual_beams"], [])
                self.assertEqual(result["fixture_reasons"]["F3"], "route_or_patch_obstructed")

    def test_nonconvex_walkable_polygon_cannot_be_shortcut_through_its_notch(self):
        floor = {"walkable_polygon_m": ((-2, -2), (2, -2), (2, 2), (.2, 2), (.2, -.2),
                    (-.2, -.2), (-.2, 2), (-2, 2)), "obstacles_m": ()}
        self.assertFalse(walking._route_clear((-1, 1), (1, 1), .01, floor))

    def test_geometry_is_bound_to_current_transform_scan_and_calibration(self):
        for key in ("transform_id", "scan_revision", "depth_calibration_sha256"):
            with self.subTest(key=key):
                self.planner = walking.Planner(self.core, self.config)
                result = self.evaluate(snapshot(state(**{key: "changed"})))
                self.assertEqual(result["reason"], "floor_lineage_mismatch")
                self.assertEqual(result["virtual_beams"], [])

    def test_current_frame_and_observation_lineage_are_required(self):
        for key, value in (("frame_id", 99), ("capture_mono", 99.9), ("camera_epoch", 9)):
            with self.subTest(key=key):
                source = snapshot()
                source["frame"][key] = value
                self.planner = walking.Planner(self.core, self.config)
                self.assertEqual(self.evaluate(source)["proposals"], [])
        source = snapshot()
        source["tracks"][0]["current_obs_ids"] = ("different",)
        self.assertEqual(self.evaluate(source)["proposals"], [])

    def test_expired_future_or_unqualified_motion_never_lights(self):
        for value, now in ((state(), 101.5), (state(), 99.9), (state(sample_count=2), 100.2)):
            with self.subTest(now=now, samples=value.sample_count):
                self.planner = walking.Planner(self.core, self.config)
                self.assertEqual(self.evaluate(snapshot(value), now=now)["virtual_beams"], [])

    def test_replayed_producer_epoch_and_frame_cannot_recover_old_plan(self):
        self.eligible(self.evaluate())
        newer = state(service_run_id="next-run")
        self.assertEqual(self.evaluate(snapshot(newer), now=100.3)["virtual_beams"], [])
        result = self.evaluate(now=100.4)
        self.assertEqual(result["reason"], "retired_producer")
        self.planner = walking.Planner(self.core, self.config)
        self.eligible(self.evaluate())
        result = self.evaluate(snapshot(state(frame_id=9)), now=100.3)
        self.assertEqual(result["reason"], "source_replayed")
        self.planner = walking.Planner(self.core, self.config)
        self.eligible(self.evaluate())
        self.assertEqual(self.evaluate(snapshot(state(camera_epoch=1)), now=100.3)["reason"], "source_replayed")

    def test_same_frame_receipt_cannot_change_velocity_or_renew_evidence(self):
        for changes in ({"velocity_mps": (.8, 0)}, {"valid_until_mono": 101.4}):
            self.planner = walking.Planner(self.core, self.config)
            self.eligible(self.evaluate())
            result = self.evaluate(snapshot(state(**changes), seq=11), now=100.3)
            self.assertEqual(result["reason"], "source_receipt_changed")
            self.assertEqual(result["virtual_beams"], [])

    def test_seated_or_unknown_posture_and_association_abstain(self):
        for changes in ({"seated": {"state": True}}, {"seated": None}, {"association_status": "ambiguous"},
                        {"head_point_m": None}, {"motion": None}):
            with self.subTest(changes=changes):
                source = snapshot()
                source["tracks"][0].update(changes)
                self.assertEqual(self.evaluate(source)["virtual_beams"], [])

    def test_crossing_person_needs_exact_subject_and_known_clear_heads(self):
        source = snapshot()
        guest = copy.deepcopy(source["tracks"][0])
        guest.update(track_id="guest", association_revision=1, head_point_m={"xyz": [4, 4, 1.7]})
        guest["motion"] = state(track_id="guest", association_revision=1, xy_m=(4, 4), velocity_mps=(0, 0))
        source["tracks"].append(guest)
        self.assertEqual(self.evaluate(source)["reason"], "ambiguous_subject")
        binding = {key: getattr(state(), key) for key in walking.KEYS if key != "frame_id"}
        self.planner = walking.Planner(self.core, self.config)
        self.eligible(self.evaluate(source, subject_binding=binding))
        self.planner = walking.Planner(self.core, self.config)
        guest["head_point_m"] = None
        self.assertEqual(self.evaluate(source, subject_binding=binding)["reason"], "unknown_head")

    def test_future_bystander_crossing_is_refused_even_when_both_endpoint_heads_clear(self):
        target = self.eligible(self.evaluate())["target_m"]
        self.planner = walking.Planner(self.core, self.config)
        source = snapshot()
        x = target[0] * .32
        guest = copy.deepcopy(source["tracks"][0])
        guest.update(track_id="guest", association_revision=1, head_point_m={"xyz": [x, -1.5, 1.7]},
            motion=state(track_id="guest", association_revision=1, xy_m=(x, -1.5), velocity_mps=(0, 2)))
        source["tracks"].append(guest)
        binding = {key: getattr(state(), key) for key in walking.KEYS if key != "frame_id"}
        result = self.evaluate(source, subject_binding=binding)
        self.assertEqual(result["fixture_reasons"]["F3"], "beam_near_head")
        origin = self.rows["F3"]["aim_origin_m"]
        for y in (-1.1, .062):
            self.assertGreater(self.core.eye.centre_clear(origin, target, [x, y, 1.7]), .32)
        self.assertEqual(result["virtual_beams"], [])

    def test_known_bystander_head_without_bound_motion_is_not_future_clearance(self):
        source = snapshot()
        guest = copy.deepcopy(source["tracks"][0])
        guest.update(track_id="guest", association_revision=1, head_point_m={"xyz": [4, 4, 1.7]}, motion=None)
        source["tracks"].append(guest)
        binding = {key: getattr(state(), key) for key in walking.KEYS if key != "frame_id"}
        self.assertEqual(self.evaluate(source, subject_binding=binding)["reason"], "missing_or_stale_motion")

    def test_supported_eye_can_refuse_a_beam_whose_head_clears(self):
        target = self.eligible(self.evaluate())["target_m"]
        source = snapshot()
        x = target[0] * .32 - .1
        source["tracks"][0]["head_point_m"] = {"xyz": [x, -.4, 1.7]}
        source["tracks"][0]["supported_pose"] = {
            "left_eye": [[x, -.4, 1.7], "supported"], "right_eye": [[x + .01, -.4, 1.7], "supported"]}
        self.planner = walking.Planner(self.core, self.config)
        self.eligible(self.evaluate(source))
        source["tracks"][0]["supported_pose"]["left_eye"][0][1] = -.5
        self.planner = walking.Planner(self.core, self.config)
        result = self.evaluate(source)
        self.assertEqual(result["fixture_reasons"]["F3"], "beam_near_head")

    def test_beam_cannot_cross_unknown_height_furniture_above_a_clear_walking_route(self):
        obstacle = [[.1, -.8], [.25, -.8], [.25, -.65], [.1, -.65]]
        self.config["floor"]["obstacles_m"] = [obstacle]
        self.planner = walking.Planner(self.core, self.config)
        self.assertTrue(walking._route_clear((0, 0), (.49, 0), .26, self.planner.config["floor"]))
        result = self.evaluate()
        self.assertEqual(result["fixture_reasons"]["F3"], "beam_obstacle_prism")
        self.assertEqual(result["virtual_beams"], [])

    def test_head_crossing_clears_previously_accepted_plan_immediately(self):
        proposal = self.eligible(self.evaluate())
        source = snapshot()
        origin, target = self.rows["F3"]["aim_origin_m"], proposal["target_m"]
        source["tracks"][0]["head_point_m"]["xyz"] = [(a + b) / 2 for a, b in zip(origin, target)]
        self.assertEqual(self.evaluate(source, now=100.3)["virtual_beams"], [])

    def test_missing_stale_contradictory_and_multipath_intent_cannot_move_geometric_target(self):
        baseline = self.eligible(self.evaluate())
        for hint in ({}, {"valid_until_mono": 99}, {"hypotheses": [{"route": "left"}, {"route": "right"}]},
                     {"velocity_mps": [-100, 100], "score": 1}, "malformed"):
            with self.subTest(hint=hint):
                self.planner = walking.Planner(self.core, self.config)
                result = self.evaluate(intent=hint)
                self.assertEqual(self.eligible(result), baseline)
                self.assertEqual(result["evidence"]["intent"]["status"], "advisory_not_admitted")
        self.config["floor"]["obstacles_m"] = [[[.2, -.5], [.3, -.5], [.3, .5], [.2, .5]]]
        self.planner = walking.Planner(self.core, self.config)
        result = self.evaluate(intent={"hypotheses": [{"route": "left"}, {"route": "right"}]})
        self.assertEqual(result["proposals"], [])

    def test_config_work_is_bounded_and_invalid_polygons_never_activate(self):
        for edits in ({"obstacles_m": [[]] * 17}, {"walkable_polygon_m": [[0, 0]] * 65},
                      {"walkable_polygon_m": [[0, 0], [2, 2], [0, 2], [2, 0]]},
                      {"qualification": "commissioned"}, {"beam_radius_m": math.inf}):
            with self.subTest(edits=edits):
                config = configuration()
                config["floor"].update(edits)
                planner = walking.Planner(self.core, config)
                result = planner.evaluate(snapshot(), self.rows, 100.2, on=True, stopped=False)
                self.assertEqual(result["status"], "blocked")

    def test_cached_profile_geometry_and_controls_are_rechecked(self):
        for edit in (lambda row: row["profile"].update(settle_s=1.2),
                     lambda row: row.update(aim_origin_m=[0, -2, 2.5]),
                     lambda row: row["profile"]["axes"][0].update(v_max=1)):
            with self.subTest(edit=edit):
                self.planner = walking.Planner(self.core, self.config)
                self.rows = fixtures(self.core)
                self.eligible(self.evaluate())
                edit(self.rows["F3"])
                self.assertEqual(self.evaluate(now=100.3)["virtual_beams"], [])
        for keyword in ({"on": "false"}, {"stopped": 0}, {"reading_active": "no"}):
            self.assertEqual(self.evaluate(**keyword)["status"], "blocked")

    def test_oversized_motion_observations_and_incomplete_binding_are_rejected(self):
        for ids in (("x" * 257,), tuple(str(i) for i in range(65))):
            self.assertEqual(self.evaluate(snapshot(state(source_obs_ids=ids)))["proposals"], [])
        self.assertEqual(self.evaluate(subject_binding={"track_id": "walker"})["reason"], "invalid_subject_binding")

    def test_one_second_cadence_never_catches_up_with_a_burst(self):
        first = self.eligible(self.evaluate())
        for tenth in range(1, 10):
            at = 100.2 + tenth / 10
            observed = at - .2
            value = state(observed_mono=observed, available_mono=observed + .05,
                valid_until_mono=observed + 1.5, frame_id=10 + tenth,
                xy_m=((observed - 100) * .5, 0), source_obs_ids=(f"obs{tenth}",))
            result = self.evaluate(snapshot(value), now=at)
            self.assertNotEqual(result["status"], "eligible")
            self.assertEqual(self.planner.last_planned_mono, 100.2)
            if result["proposals"]:
                self.assertEqual(result["proposals"][0], first)

    def test_output_is_finite_bounded_json_and_inputs_remain_unchanged(self):
        source, rows, config = snapshot(), copy.deepcopy(self.rows), copy.deepcopy(self.config)
        result = self.evaluate(source)
        self.eligible(result)
        encoded = json.dumps(result, allow_nan=False)
        self.assertLess(len(encoded), 12000)
        self.assertEqual(self.rows, rows)
        self.assertEqual(self.config, config)
        self.assertEqual(source["tracks"][0]["motion"], state())


if __name__ == "__main__":
    unittest.main()
