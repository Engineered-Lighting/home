"""Authored independent joint vectors with fixed original-decoder doubles.

These pure tests do not execute the pinned provider, simulate motor physics or
qualify hardware. The coordinator runs the actual canonical provider join.
"""
import copy
import dataclasses
import hashlib
import math
import unittest
from dataclasses import dataclass, replace

from fixture_execution import FixtureExecutionRefusal
from fixture_joint_execution import (ABI, ASSESSMENT_METHOD, COORDINATES, CURRENT_FIELDS,
    PROFILE_SCHEMA, JointFixtureCompiler, quantize_joint_target_centideg, validate_joint_profile)
from fixture_profile import CAP_FIELDS, FixtureProfileRefusal, digest
from test_fixture_profile import fictional_profile


@dataclass(frozen=True)
class AuthoredBinding:
    binding_sha256: str = "71" * 32
    boot_generation: int = 1
    session_id: int = 2
    ownership_generation: int = 3
    context_digest: bytes = b"c" * 32


@dataclass(frozen=True)
class AuthoredCurrentReference:
    binding_sha256: str
    snapshot_sha256: str
    capabilities_sha256: str
    boot_generation: int
    session_id: int
    ownership_generation: int
    context_digest: str
    state_generation: int
    revision_id: int
    domain_generations: tuple
    captured_device_us: int
    device_now_lower_us: int
    device_now_upper_us: int
    maximum_status_age_us: int
    evidence_source: str


def joint_fixture():
    """Exported coordinator fixture; fixed doubles are explicitly non-wire data."""
    p = fictional_profile()
    motors = [dict(axis=0, name="PAN", request_id=0x141, response_id=0x241, direction=-1,
                   joint_per_motor_num=1, joint_per_motor_den=2, motor_zero_centideg=1000),
              dict(axis=1, name="TILT", request_id=0x142, response_id=0x242, direction=1,
                   joint_per_motor_num=2, joint_per_motor_den=1, motor_zero_centideg=-500)]
    for a, m, ratio, limit in zip(p["axes"], motors, (200, 50), (100, 50)):
        a.update(zero_centideg=m["motor_zero_centideg"], direction=m["direction"],
                 units_per_fixture_degree={"numerator": ratio, "denominator": 1},
                 minimum_centideg=-40000, maximum_centideg=40000, max_speed_deg_s=limit)
    p["bounds"]["max_quantization_error_rad"] = 0.0004
    spotlight = dict(domain=7, channels=[21, 22, 23],
                     test_emitters=["TEST_WARM_WHITE", "TEST_NEUTRAL_WHITE", "TEST_PC_AMBER"],
                     input="raw_logical_duty_only", semantic_CCT_mapping=None, real_emitter_qualification=False)
    config = dict(schema="c12-canonical-capture-config-1", version=1, coordinate_contract=COORDINATES,
                  evidence_source="simulated", physical_permission=False, physical_truth=False,
                  motors=motors, spotlight=spotlight, stationary_speed_centideg_s=200)
    pins = dict(manifest_sha256="81" * 32, source_sha256="82" * 32, executable_sha256="83" * 32,
                configuration_sha256=digest(config))
    profile = dict(schema=PROFILE_SCHEMA, version=1, motor_profile=p,
                   canonical={"abi": ABI, "coordinate_contract": COORDINATES, **pins}, spotlight=spotlight)
    binding = AuthoredBinding()
    wire_pins = {k: p["pins"][k] for k in ("fixture", "firmware", "protocol", "profile", "calibration", "contract")}
    status = dict(schema="c12-runtime-decoded-snapshot-1", document_version=3, phase="idle", result="ok",
                  boot_generation="1", session_id="2", ownership_generation="3", state_generation="8",
                  revision_id="3", motion_id="0", motion_started_us="0", motion_deadline_us="0",
                  support_expiry_home_us="0", lighting_generation="4", can_generation="17",
                  context_digest=binding.context_digest.hex(), pins=wire_pins,
                  domains=[dict(generation=4) for _ in range(8)], session_open=True, thermal_latched=False,
                  simulated=True, physical_permission=False, mechanical_settled=False, stop_confirmed=False,
                  requested=[0] * 24,
                  current_semantic_target=dict(target_centideg=[0, 0], speed_deg_s=[0, 0], motion_timeout_us="0",
                                               start_deadline_device_us="0"),
                  axis_observations=[dict(axis=i, opcode=0x92, angle_centideg=0, speed_centideg_s=0,
                      sampled_device_us="90000", received_device_us="95000", motion_generation="0",
                      matched=True, stationary=True) for i in range(2)])
    cap = dict(schema="c12-runtime-capabilities-1", document_version=2, configuration_permission=False,
               qualified_mask=255, pins=wire_pins, protocol=1, max_driver_commit_us="100000",
               max_temperature_age_us="250000", max_motion_us="2000000", maximum_duty=[4096] * 24,
               full_channel_milliwatt=[100] * 24, max_average_lighting_milliwatt=2400,
               max_instant_lighting_milliwatt=2400,
               axes=[dict(request_id=0x141 + i, response_id=0x241 + i, max_speed_deg_s=50,
                          minimum_centideg=-17000, maximum_centideg=17000) for i in range(2)], physical_permission=False)
    f = {"profile": profile, "canonical_config": config, "provider_pins": pins, "binding": binding,
         "status": status, "cap": cap}
    return refresh_joint_fixture(f)


def refresh_joint_fixture(f, *, captured_device_us=100000):
    """Freeze a new authored original after deliberately changing decoded fields."""
    status, cap, binding = f["status"], f["cap"], f["binding"]
    status_body = {k: v for k, v in status.items() if k not in
                   ("original_document_hex", "original_document_sha256", "captured_device_us")}
    raw_status = (b"AUTHORED_STATUS3:" + bytes.fromhex(digest(status_body))).ljust(998, b"\0")
    raw_cap = (b"AUTHORED_CAP2:" + bytes.fromhex(digest(cap))).ljust(404, b"\0")
    status.update(original_document_hex=raw_status.hex(), original_document_sha256=hashlib.sha256(raw_status).hexdigest(),
                  captured_device_us=str(captured_device_us))
    frozen_status, frozen_cap = copy.deepcopy(status), copy.deepcopy(cap)

    def decode_status(raw, *, binding, captured_device_us):
        if raw != raw_status or binding != f["binding"] or str(captured_device_us) != frozen_status["captured_device_us"]:
            raise ValueError("authored original mismatch")
        return copy.deepcopy(frozen_status)

    def decode_capabilities(raw, expected_pins):
        if raw != raw_cap or expected_pins != frozen_cap["pins"]:
            raise ValueError("authored capability mismatch")
        return copy.deepcopy(frozen_cap)

    ref = AuthoredCurrentReference(binding.binding_sha256, hashlib.sha256(raw_status).hexdigest(),
        hashlib.sha256(raw_cap).hexdigest(), binding.boot_generation, binding.session_id,
        binding.ownership_generation, binding.context_digest.hex(), int(status["state_generation"]),
        int(status["revision_id"]), tuple(d["generation"] for d in status["domains"]),
        captured_device_us, captured_device_us, captured_device_us, 250000, "simulated")
    f.update(raw_status=raw_status, raw_capabilities=raw_cap, ref=ref,
             compiler=JointFixtureCompiler(f["profile"], canonical_config=f["canonical_config"],
                 provider_pins=f["provider_pins"], binding=binding, decode_status=decode_status,
                 decode_capabilities=decode_capabilities))
    return f


def joint_request(f, *, kind="MOVE", previous=None, now_home_us=1000000):
    pan, tilt = math.radians(10), math.radians(20)
    target = [math.cos(pan) * math.sin(tilt), math.sin(pan) * math.sin(tilt), 2 - math.cos(tilt)]
    r = dict(kind=kind, motion_id=7, revision_id=f["ref"].revision_id + 1, predecessor_id=f["ref"].revision_id,
             scope=dict(home_id="Victoria", room_id="den", fixture_id=f["profile"]["motor_profile"]["fixture_id"]),
             pins=copy.deepcopy(f["profile"]["motor_profile"]["pins"]),
             lineage=dict(source_cut_sha256="91" * 32, protection_sha256="92" * 32, authority_sha256="93" * 32,
                          scene_id="scene-1", episode_id="episode-1", example_id="example-1", correction_ref=None),
             context=dict(boot_generation=1, session_id=2, ownership_generation=3), target_room_m=target,
             speed_fixture_deg_s=[20, 20], issued_home_us=now_home_us, support_expiry_home_us=3900000,
             original_caps={key: 4000000 for key in CAP_FIELDS}, motion_timeout_us=2000000)
    compiled = f["compiler"].compile_target_joint(target, [20, 20], now_home_us=now_home_us)
    start = [0, 0] if previous is None else previous["start_centideg"]
    r["assessment"] = dict(status="fictional", assessment_id="assessed-authored-cut", method=ASSESSMENT_METHOD,
        profile_sha256=digest(f["profile"]), pins=copy.deepcopy(r["pins"]), lineage=copy.deepcopy(r["lineage"]),
        target_room_m=target, target_centideg=compiled["target_centideg"], start_centideg=copy.deepcopy(start),
        current_reference_sha256=digest({k: getattr(f["ref"], k) for k in CURRENT_FIELDS}), motion_id=7,
        checks={key: True for key in ("work_coverage", "all_people_protection", "room_collision", "dark_motion_path")},
        valid_from_home_us=0, valid_until_home_us=4000000, original_caps=copy.deepcopy(r["original_caps"]),
        target_uncertainty_m=0.001, total_aim_error_bound_rad=0.01)
    return r


def compile_fixture(f, r=None, *, now_home_us=1000000, previous=None, **overrides):
    kw = dict(current_ref=f["ref"], raw_status_v3=f["raw_status"], raw_capabilities_v2=f["raw_capabilities"])
    kw.update(overrides)
    return f["compiler"].compile_stationary_joint(r or joint_request(f), now_home_us=now_home_us, previous=previous, **kw)


def retained_fixture(*, phase="lit", captured_device_us=2100000):
    f = joint_fixture()
    prior = compile_fixture(f)
    f["status"].update(phase=phase, revision_id="5", state_generation="10", motion_id="7",
                        motion_started_us="200000", motion_deadline_us="2000000",
                        support_expiry_home_us="3900000", mechanical_settled=(phase == "lit"),
                        current_semantic_target=dict(target_centideg=copy.deepcopy(prior["target_centideg"]),
                            speed_deg_s=copy.deepcopy(prior["speed_deg_s"]), motion_timeout_us="2000000",
                            start_deadline_device_us="300000"))
    refresh_joint_fixture(f, captured_device_us=captured_device_us)
    return f, prior


class JointExecutionTests(unittest.TestCase):
    def assert_refused(self, code, callable_, *args, **kwargs):
        with self.assertRaises(FixtureExecutionRefusal) as raised:
            callable_(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def test_nonidentity_known_vectors_stay_joint_and_legacy_mapping_is_only_validation(self):
        f = joint_fixture()
        c = f["compiler"]
        target = c.compile_target_joint(joint_request(f)["target_room_m"], [20, 20], now_home_us=1000000)
        self.assertEqual(target["target_centideg"], [1000, 2000])
        self.assertEqual(target["speed_deg_s"], [20, 20])
        # Independent canonical mapping vectors; no CAN encoder is implemented.
        self.assertEqual(c._raw_validation([1000, -2000], [20, 20]), ([-1000, -1500], [40, 10]))
        self.assertNotIn("raw_target_centideg", target)

    def test_signed_ties_and_exact_representability_grid(self):
        motors = joint_fixture()["canonical_config"]["motors"]
        self.assertEqual(quantize_joint_target_centideg(motors[0], "0.005"), 1)
        self.assertEqual(quantize_joint_target_centideg(motors[0], "-0.005"), -1)
        self.assertEqual(quantize_joint_target_centideg(motors[1], "0.01"), 2)
        self.assertEqual(quantize_joint_target_centideg(motors[1], "-0.01"), -2)
        self.assert_refused("joint_not_exactly_representable", joint_fixture()["compiler"]._raw_validation, [1, 1], [20, 20])

    def test_first_move_uses_actual_ambient_predecessor_and_actual_joint_start(self):
        f = joint_fixture()
        draft = compile_fixture(f)
        self.assertEqual((draft["revision_id"], draft["predecessor_id"]), (4, 3))
        self.assertEqual(draft["start_centideg"], [0, 0])
        self.assertFalse(draft["physical_eligible"])
        self.assertFalse(draft["authority_created"])
        r = joint_request(f)
        r["predecessor_id"] = 0
        self.assert_refused("actual_predecessor_required", compile_fixture, f, r)

    def test_move_refuses_travel_and_stationary_active_task_phases(self):
        for phase in ("travel", "lit", "illuminating", "darkening", "decaying", "launch_pan", "launch_tilt",
                      "moving", "settling", "protecting", "unconfirmed", "fault", "inhibited"):
            f = joint_fixture()
            f["status"].update(phase=phase, result="travel" if phase == "travel" else "ok", motion_id="7")
            for axis in f["status"]["axis_observations"]:
                axis["motion_generation"] = "7"
            refresh_joint_fixture(f)
            r = joint_request(f)
            r["motion_id"] = r["assessment"]["motion_id"] = 8
            with self.subTest(phase=phase):
                self.assert_refused("current_move_phase_unavailable", compile_fixture, f, r)

    def test_terminal_retarget_requires_confirmed_stop_and_keeps_actual_predecessor(self):
        f = joint_fixture()
        f["status"].update(motion_id="7", motion_started_us="20000", motion_deadline_us="50000",
                            phase="idle", result="revoked", stop_confirmed=False)
        for axis in f["status"]["axis_observations"]:
            axis["motion_generation"] = "7"
        refresh_joint_fixture(f)
        r = joint_request(f)
        r["motion_id"] = r["assessment"]["motion_id"] = 8
        self.assert_refused("current_move_stop_unconfirmed", compile_fixture, f, r)
        f["status"]["stop_confirmed"] = True
        refresh_joint_fixture(f)
        r = joint_request(f)
        r["motion_id"] = r["assessment"]["motion_id"] = 8
        out = compile_fixture(f, r)
        self.assertEqual((out["motion_id"], out["predecessor_id"]), (8, 3))
        self.assertEqual(out["phase_policy"], "dark_retarget")

    def test_cancellation_before_first_motor_launch_does_not_require_reset_or_synthetic_stop(self):
        f = joint_fixture()
        f["status"].update(motion_id="7", phase="idle", result="revoked", stop_confirmed=False)
        # Original fresh initial feedback still has generation zero; no motor
        # launch occurred, so native motor_ever_sent remains false.
        refresh_joint_fixture(f)
        r = joint_request(f)
        r["motion_id"] = r["assessment"]["motion_id"] = 8
        out = compile_fixture(f, r)
        self.assertEqual(out["predecessor_id"], 3)
        self.assertEqual(out["context"], r["context"])

    def test_source_profile_mapping_and_config_digest_must_match(self):
        f = joint_fixture()
        changed = copy.deepcopy(f["profile"])
        changed["motor_profile"]["axes"][0]["direction"] = 1
        self.assert_refused("motor_mapping_disagrees_with_canonical", validate_joint_profile, changed,
                            canonical_config=f["canonical_config"], provider_pins=f["provider_pins"])
        changed = copy.deepcopy(f["canonical_config"])
        changed["motors"][0]["motor_zero_centideg"] += 1
        self.assert_refused("canonical_configuration_mismatch", validate_joint_profile, f["profile"],
                            canonical_config=changed, provider_pins=f["provider_pins"])

    def test_original_status_and_cap_hashes_and_generation_fail_closed(self):
        f = joint_fixture()
        self.assert_refused("current_original_digest_mismatch", compile_fixture, f, raw_status_v3=b"x" * 998)
        self.assert_refused("current_original_digest_mismatch", compile_fixture, f, raw_capabilities_v2=b"x" * 404)
        self.assert_refused("current_binding_generation_mismatch", compile_fixture, f,
                            current_ref=replace(f["ref"], boot_generation=9))
        self.assert_refused("trusted_immutable_reference_required", compile_fixture, f,
                            current_ref=dataclasses.asdict(f["ref"]))

    def test_stale_uncertain_and_future_clock_references_refuse(self):
        f = joint_fixture()
        for change in (dict(device_now_lower_us=350000, device_now_upper_us=350000),
                       dict(device_now_lower_us=100000, device_now_upper_us=120001),
                       dict(device_now_lower_us=99998, device_now_upper_us=99999)):
            with self.subTest(change=change):
                self.assert_refused("current_clock_or_status_stale", compile_fixture, f,
                                    current_ref=replace(f["ref"], **change))

    def test_axis_original_generation_and_staleness_cannot_supply_new_start(self):
        for mutation, code in ((dict(motion_generation="17"), "axis_motion_generation_mismatch"),
                               (dict(stationary=False), "current_stationary_axes_unavailable"),
                               (dict(speed_centideg_s=201), "current_stationary_speed_mismatch"),
                               (dict(sampled_device_us="0"), "stale_axis_original")):
            f = joint_fixture()
            f["status"]["axis_observations"][0].update(mutation)
            refresh_joint_fixture(f, captured_device_us=250000 if "sampled_device_us" in mutation else 100000)
            with self.subTest(mutation=mutation):
                self.assert_refused(code, compile_fixture, f)

    def test_capability_mismatch_or_missing_task_bits_refuse(self):
        f = joint_fixture()
        f["cap"]["axes"][0]["request_id"] = 0x143
        refresh_joint_fixture(f)
        self.assert_refused("capability_motor_binding_mismatch", compile_fixture, f)
        f = joint_fixture()
        f["cap"]["qualified_mask"] &= ~128
        refresh_joint_fixture(f)
        self.assert_refused("canonical_task_profile_unavailable", compile_fixture, f)

    def test_levels_after_ambient_revision_and_settled_after_arrival_retains_original_motion(self):
        f, prior = retained_fixture()
        r = joint_request(f, kind="LEVELS", previous=prior, now_home_us=3200000)
        out = compile_fixture(f, r, previous=prior, now_home_us=3200000)
        self.assertEqual((out["revision_id"], out["predecessor_id"]), (6, 5))
        self.assertEqual(out["motion_origin_revision_id"], 4)
        self.assertEqual(out["motion_origin_support_expiry_home_us"], 3900000)
        self.assertEqual(out["motion_deadline_device_us"], 2000000)
        self.assertEqual(out["target_centideg"], prior["target_centideg"])

    def test_retained_semantic_change_or_expired_support_cannot_resume(self):
        f, prior = retained_fixture()
        f["status"]["current_semantic_target"]["target_centideg"][0] += 1
        refresh_joint_fixture(f, captured_device_us=2100000)
        r = joint_request(f, kind="CONTINUE", previous=prior, now_home_us=3200000)
        self.assert_refused("actual_retained_motion_changed", compile_fixture, f, r, previous=prior, now_home_us=3200000)
        f, prior = retained_fixture()
        prior["support_expiry_home_us"] = 3200000
        prior["draft_sha256"] = digest({k: v for k, v in prior.items() if k != "draft_sha256"})
        r = joint_request(f, kind="CONTINUE", previous=prior, now_home_us=3200000)
        self.assert_refused("expired_motion_cannot_resume", compile_fixture, f, r, previous=prior, now_home_us=3200000)
        f, prior = retained_fixture()
        f["status"]["support_expiry_home_us"] = "3200000"
        refresh_joint_fixture(f, captured_device_us=2100000)
        r = joint_request(f, kind="CONTINUE", previous=prior, now_home_us=3200000)
        self.assert_refused("actual_motion_support_expired", compile_fixture, f, r, previous=prior, now_home_us=3200000)

    def test_timely_fresh_cuts_extend_support_past_first_move_without_changing_arrival(self):
        f, prior = retained_fixture()
        r = joint_request(f, kind="CONTINUE", previous=prior, now_home_us=3200000)
        r["support_expiry_home_us"] = 6000000
        r["original_caps"] = {key: 6100000 for key in CAP_FIELDS}
        r["assessment"]["original_caps"] = copy.deepcopy(r["original_caps"])
        r["assessment"]["valid_until_home_us"] = 6100000
        accepted = compile_fixture(f, r, previous=prior, now_home_us=3200000)
        f["status"].update(revision_id="6", state_generation="11", support_expiry_home_us="6000000")
        refresh_joint_fixture(f, captured_device_us=3000000)
        r = joint_request(f, kind="LEVELS", previous=accepted, now_home_us=4100000)
        r["support_expiry_home_us"] = 6500000
        r["original_caps"] = {key: 6600000 for key in CAP_FIELDS}
        r["assessment"]["original_caps"] = copy.deepcopy(r["original_caps"])
        r["assessment"]["valid_until_home_us"] = 6600000
        out = compile_fixture(f, r, previous=accepted, now_home_us=4100000)
        self.assertGreater(out["support_expiry_home_us"], out["motion_origin_support_expiry_home_us"])
        self.assertEqual(out["motion_started_device_us"], 200000)
        self.assertEqual(out["motion_deadline_device_us"], 2000000)
        self.assertEqual(out["motion_start_deadline_device_us"], 300000)

    def test_moving_arrival_deadline_expires_and_retained_deadline_cannot_reset(self):
        f, prior = retained_fixture(phase="moving")
        r = joint_request(f, kind="CONTINUE", previous=prior, now_home_us=3200000)
        self.assert_refused("expired_device_motion_cannot_resume", compile_fixture, f, r, previous=prior, now_home_us=3200000)
        f, prior = retained_fixture()
        r = joint_request(f, kind="LEVELS", previous=prior, now_home_us=3200000)
        out = compile_fixture(f, r, previous=prior, now_home_us=3200000)
        f["status"].update(revision_id="6", state_generation="11", motion_deadline_us="2200000")
        refresh_joint_fixture(f, captured_device_us=2150000)
        r = joint_request(f, kind="CONTINUE", previous=out, now_home_us=3250000)
        self.assert_refused("retained_device_motion_deadline_changed", compile_fixture, f, r, previous=out, now_home_us=3250000)

    def test_assessment_binds_exact_reference_and_envelope_and_no_lease_refresh(self):
        f = joint_fixture()
        r = joint_request(f)
        r["assessment"]["current_reference_sha256"] = "00" * 32
        self.assert_refused("joint_assessment_binding_mismatch", compile_fixture, f, r)
        r = joint_request(f)
        r["assessment"]["total_aim_error_bound_rad"] = 0
        self.assert_refused("assessment_understates_uncertainty", compile_fixture, f, r)
        r = joint_request(f)
        r["support_expiry_home_us"] = 4000001
        self.assert_refused("support_exceeds_original_bounds", compile_fixture, f, r)

    def test_raw_duties_are_explicit_bounded_and_unknown_CCT_always_refuses(self):
        f = joint_fixture()
        c = f["compiler"]
        kwargs = dict(current_ref=f["ref"], raw_status_v3=f["raw_status"], raw_capabilities_v2=f["raw_capabilities"])
        out = c.validate_raw_spotlight([64, 128, 0], **kwargs)
        self.assertEqual(out["duties"], [64, 128, 0])
        self.assertIsNone(out["semantic_CCT_mapping"])
        self.assertFalse(out["real_emitter_qualification"])
        with self.assertRaises(FixtureProfileRefusal):
            c.validate_raw_spotlight([4097, 0, 0], **kwargs)
        self.assert_refused("semantic_spotlight_cct_mapping_unavailable", c.compile_semantic_spotlight, cct=3000, brightness=0.25)


if __name__ == "__main__":
    unittest.main()
