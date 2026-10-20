"""Pure vjepa_scene tests: pins, request, result validation, receipts, causal history and the JEV projection."""
import copy
import json
import random
import unittest

import vjepa_scene as scene

PROFILE = {"model_version": scene.MODEL_VERSION, "artifact_sha256": scene.ARTIFACT_SHA256,
           "runtime_revision": "fixture-runtime", "engine_epoch": "la-live-" + "0" * 32, "input_profile": scene.INPUT_PROFILE}
OBSERVED = [96.5 + .5 * i for i in range(8)]
FRAMES = [{"media_id": "m%d" % i, "frame_id": "f%d" % i, "capture_epoch": "e", "observed_at": t} for i, t in enumerate(OBSERVED)]
JPEG = b"\xff\xd8" + b"\x00" * 64
BOX = [200.0, 40.0, 420.0, 350.0]
POSTURE = {"sitting": .82, "standing": .08, "walking": .04, "lying_down": .02, "bending": .02, "exercising_pose": .02}


def request(previous=None, single=True):
    return {"request_id": "r" * 64, "profile": dict(PROFILE), "person_box": BOX, "single_person": single,
            "previous_request_id": previous}


def result(req=None, **changes):
    req = req or request()
    value = {"version": 1, "schema": scene.RESULT_SCHEMA, "request_id": req["request_id"],
             "engine_epoch": PROFILE["engine_epoch"], "artifact_sha256": PROFILE["artifact_sha256"],
             "posture": dict(POSTURE), "reading": .71,
             "zero_shot": {"phone_use": .05, "computer_work": .1, "reading": .6} if req["single_person"] else None,
             "zero_shot_status": "supplied" if req["single_person"] else "single_person_gate_not_met",
             "motion": .02, "scene_drift": .01 if req["previous_request_id"] else None,
             "drift_reference": req["previous_request_id"],
             "timings_ms": {"decode": 12.0, "crop": 4.0, "encode": 38.0, "heads": 6.0, "total": 61.0}}
    value.update(changes)
    return value


def draft(value="default", *, req=None, generated=101.0, frames=FRAMES):
    req = req or request()
    return scene.build_observation(site_id="victoria", camera_id="den", session_id="s" * 32, request=req, frames=frames,
                                   box_input={"frames": 6, "event_ids": [11, 12]}, generated_at=generated,
                                   result=result(req) if value == "default" else value,
                                   body_sha256="b" * 64, response_sha256="c" * 64)


def receipt(t, *, posture="sitting", reading=.71, motion=.02, drift=None, status="valid", available=None, request_id=None,
            single=True):
    """A stored receipt observed at t, first received shortly after."""
    req = request(previous=None if drift is None else "p" * 64, single=single)
    req["request_id"] = request_id or ("%064x" % int(t * 1000))
    probs = {k: .02 for k in scene.POSTURES}
    probs[posture] = 1 - .02 * 5
    value = result(req, posture=probs, reading=reading, motion=motion)
    if drift is not None:
        value["scene_drift"] = drift
    available = t + 1 if available is None else available
    return {"request_id": req["request_id"], "observed_at": t, "generated_at": available, "available_at": available,
            "first_received_at": available, "valid_until": t + scene.MAX_AGE_SECONDS, "status": status,
            "result": value if status == "valid" else None, "profile": dict(PROFILE),
            "input": {"single_person": single, "previous_request_id": req["previous_request_id"]},
            "evidence_roots": ["media:%d" % int(t * 10)]}


def strings_and_numbers(value, strings, numbers):
    if isinstance(value, dict):
        for key, item in value.items():
            strings.append(key)
            strings_and_numbers(item, strings, numbers)
    elif isinstance(value, list):
        for item in value:
            strings_and_numbers(item, strings, numbers)
    elif isinstance(value, str):
        strings.append(value)
    elif isinstance(value, bool) or value is None:
        pass
    else:
        numbers.append(value)


class PinTests(unittest.TestCase):
    def test_pins_are_exact_hex_and_the_artifact_digest_covers_them(self):
        self.assertEqual(scene.PINS["encoder"]["sha256"][:8], "848a77c3")
        for value in [scene.PINS["encoder"]["sha256"], scene.PINS["ntu"], *scene.PINS["posture"], *scene.PINS["activity"]]:
            self.assertRegex(value, "^[0-9a-f]{64}$")
        self.assertEqual(scene.ARTIFACT_SHA256, scene.digest(scene.PINS))
        self.assertEqual(len(scene.NTU_CLASSES), 23)
        self.assertTrue(all(n in scene.NTU_CLASSES for names in scene.NTU_MAP.values() for n in names))

    def test_profile_must_match_the_pins(self):
        self.assertEqual(scene.check_profile(PROFILE), PROFILE)
        for change in ({"artifact_sha256": "0" * 64}, {"model_version": "other"}, {"input_profile": "x"},
                       {"engine_epoch": "bad epoch"}, {"extra": "x"}):
            with self.assertRaises(ValueError):
                scene.check_profile({**PROFILE, **change})
        with self.assertRaises(ValueError):
            scene.check_profile(PROFILE, runtime_revision="another-release")


class RequestTests(unittest.TestCase):
    def body(self, **changes):
        values = dict(site_id="victoria", camera_id="den", session_id="s" * 32, profile=PROFILE, images=[JPEG] * 8,
                      observed=OBSERVED, media_ids=["m%d" % i for i in range(8)], box=BOX, single_person=True)
        values.update(changes)
        return scene.request_body(**values)

    def test_body_is_bounded_ordered_and_bound_to_its_inputs(self):
        body = self.body()
        self.assertEqual(len(body["frames"]), 8)
        self.assertEqual([f["observed_at"] for f in body["frames"]], OBSERVED)
        self.assertEqual(body["request_id"], scene.request_id(session_id="s" * 32, media_ids=["m%d" % i for i in range(8)],
                                                              box=BOX, single_person=True, previous_request_id=None,
                                                              profile=PROFILE))
        self.assertNotEqual(self.body(box=[0, 0, 10, 10])["request_id"], body["request_id"])
        self.assertNotEqual(self.body(previous_request_id="a" * 64)["request_id"], body["request_id"])

    def test_bad_inputs_are_refused(self):
        for changes in ({"observed": OBSERVED[:7]}, {"observed": [t * 2 for t in OBSERVED]},
                        {"observed": list(reversed(OBSERVED))}, {"images": [b"png"] * 8}, {"images": [JPEG] * 7},
                        {"box": [0, 0, 700, 10]}, {"box": [10, 10, 5, 20]}, {"previous_request_id": "short"},
                        {"profile": {**PROFILE, "artifact_sha256": "0" * 64}}):
            with self.assertRaises(ValueError):
                self.body(**changes)


class ResultTests(unittest.TestCase):
    def test_exact_bound_result_is_accepted(self):
        checked, reason = scene.validate_result(result(), request=request())
        self.assertIsNone(reason)
        self.assertEqual(checked["posture"], POSTURE)

    def test_anything_outside_the_schema_is_invalid_and_never_repaired(self):
        req = request()
        cases = [(None, "result_unbound"), ([], "not_object"), ({**result(), "extra": 1}, "keys_mismatch"),
                 (result(request_id="x" * 64), "identity_mismatch"), (result(engine_epoch="other"), "identity_mismatch"),
                 (result(artifact_sha256="0" * 64), "identity_mismatch"),
                 (result(reading=1.2), "probability_invalid"), (result(posture={**POSTURE, "sitting": .5}), "posture_not_normalized"),
                 (result(posture={"sitting": 1.0}), "probability_invalid"),
                 (result(zero_shot={"phone_use": .7, "computer_work": .7, "reading": 0}), "zero_shot_invalid"),
                 (result(zero_shot=None), "zero_shot_invalid"), (result(motion=float("nan")), "nonfinite_or_unserializable"),
                 (result(motion=3), "motion_invalid"), (result(scene_drift=.1), "drift_invalid"),
                 (result(drift_reference="q" * 64, scene_drift=.1), "drift_invalid"),
                 (result(timings_ms={"total": 1}), "timings_invalid"),
                 (result(posture={**POSTURE, "sitting": True}), "probability_invalid")]
        for value, expected in cases:
            self.assertEqual(scene.validate_result(value, request=req), (None, expected), expected)

    def test_zero_shot_is_only_valid_when_the_single_person_gate_passed(self):
        gated = request(single=False)
        self.assertIsNone(scene.validate_result(result(gated), request=gated)[1])
        smuggled = result(gated, zero_shot={"phone_use": .9, "computer_work": 0, "reading": 0}, zero_shot_status="supplied")
        self.assertEqual(scene.validate_result(smuggled, request=gated)[1], "zero_shot_invalid")

    def test_drift_is_bound_to_the_requested_previous_clip(self):
        chained = request(previous="p" * 64)
        self.assertIsNone(scene.validate_result(result(chained), request=chained)[1])
        # The worker may decline drift (it no longer holds that clip): null, no reference.
        self.assertIsNone(scene.validate_result(result(chained, scene_drift=None, drift_reference=None), request=chained)[1])


class ReceiptTests(unittest.TestCase):
    def test_payload_binds_frames_times_and_digests(self):
        value = draft()
        self.assertEqual((value["observed_at"], value["window_start"], value["valid_until"]), (100.0, 96.5, 106.0))
        self.assertEqual(value["evidence_roots"][:2], ["media:m0", "media:m1"])
        self.assertIn("event:11", value["evidence_roots"])
        self.assertEqual((value["status"], value["invalid_reason"]), ("valid", None))
        self.assertEqual(value["frames_scope"], "not_retained_by_worker")
        self.assertNotIn("jpeg_base64", json.dumps(value))

    def test_invalid_results_are_stored_without_a_result(self):
        value = draft(result(reading=2))
        self.assertEqual((value["status"], value["invalid_reason"], value["result"]), ("invalid", "probability_invalid", None))
        self.assertEqual(draft(None)["invalid_reason"], "result_unbound")

    def test_bad_provenance_is_refused(self):
        with self.assertRaises(ValueError):
            draft(frames=FRAMES[:7])
        with self.assertRaises(ValueError):
            draft(generated=99.0)
        with self.assertRaises(ValueError):
            draft(frames=[dict(f, media_id=None) for f in FRAMES])

    def test_first_receipt_wins_and_conflicts_are_detected(self):
        first = scene.first_receipt(draft(), 102.0)
        self.assertEqual((first["available_at"], first["first_received_at"]), (102.0, 102.0))
        self.assertEqual(scene.admit(draft(), prior=first, now=103), "duplicate")
        self.assertEqual(scene.admit(draft(result(reading=.1)), prior=first, now=103), "identity_conflict")
        self.assertEqual(scene.admit(draft(), latest_observed_at=100.0, now=103), "out_of_order")
        self.assertEqual(scene.admit(draft(), now=106.0), "expired_on_receipt")
        self.assertEqual(scene.admit(draft(), latest_observed_at=99.5, now=103), "accept")
        with self.assertRaises(ValueError):
            scene.first_receipt(draft(), 100.5)
        self.assertIsNone(scene.rejection(first, "out_of_order")["result"])


class ProjectionTests(unittest.TestCase):
    def test_future_receipts_never_enter_the_projection(self):
        values = [receipt(90), receipt(94, posture="standing", available=99)]
        self.assertEqual(scene.projection(values, 98)["latest"]["facts"]["posture"]["top"], "sitting")
        self.assertEqual(scene.projection(values, 99)["latest"]["facts"]["posture"]["top"], "standing")
        late = [receipt(90), receipt(94, posture="standing", available=99)]
        late[1]["first_received_at"] = 95  # receipt time moved: not causal evidence
        self.assertEqual(scene.projection(late, 100)["latest"]["facts"]["posture"]["top"], "sitting")

    def test_projection_is_enum_words_and_booleans_only(self):
        rng = random.Random(4)
        tokens = scene.projection_tokens()
        for trial in range(60):
            values = []
            for t in range(0, 120, rng.choice((2, 3))):
                values.append(receipt(t, posture=rng.choice(scene.POSTURES), reading=rng.random(), motion=rng.random() * .3,
                                      drift=rng.choice((None, rng.random() * .2)), single=rng.random() < .7,
                                      status="invalid" if rng.random() < .1 else "valid", available=t + rng.uniform(.2, 3)))
            projected = scene.projection(values, rng.uniform(0, 130))
            strings, numbers = [], []
            strings_and_numbers(projected, strings, numbers)
            self.assertEqual(numbers, [])
            self.assertFalse(set(strings) - tokens, set(strings) - tokens)
            self.assertLessEqual(len(scene.canonical(projected)), 4096)
            self.assertEqual((projected["calibration"], projected["independent_confirmation"], projected["origin_family"]),
                             ("unvalidated", False, "same_camera_visual"))
            self.assertEqual(projected["probe_revisions"], scene.REVISIONS)

    def test_no_raw_numbers_or_identifiers_even_when_the_ledger_holds_them(self):
        projected = scene.projection([receipt(90, drift=.2), receipt(92, reading=.55)], 93)
        text = json.dumps(projected)
        for secret in ("0.55", "0.2", "0.71", "media:", "event:", "%064x" % 90000, PROFILE["engine_epoch"]):
            self.assertNotIn(secret, text)

    def test_invalid_output_is_never_projected(self):
        tampered = {**receipt(99, posture="lying_down"), "status": "invalid"}  # still carries a result
        broken = receipt(98, posture="walking")
        broken["result"]["reading"] = 5  # a stored result that fails validation is not facts either
        values = [receipt(90), receipt(94), broken, tampered]
        projected = scene.projection(values, 100)
        self.assertEqual((projected["status"], projected["latest"]["facts"]), ("invalid", None))
        summary = projected["summary_last_60s"]
        self.assertEqual((summary["valid_samples"], summary["invalid_samples"]), ("few", "few"))
        self.assertNotIn("lying_down", json.dumps(projected["latest"]))
        self.assertNotIn("walking", json.dumps(projected))
        later = scene.projection(values, 120)
        self.assertEqual((later["status"], later["latest"]["state"], later["latest"]["facts"]),
                         ("stale", "expired_historical", None))

    def test_buckets(self):
        facts = scene.facts(result(request(previous="p" * 64)))
        self.assertEqual(facts, {"posture": {"top": "sitting", "confidence": "high"}, "reading_signal": "high",
                                 "zero_shot": {"phone_use": "low", "computer_work": "low", "reading": "high"},
                                 "motion": "still", "scene_change": "stable"})
        gated = scene.facts(result(request(single=False), motion=.2, reading=.35))
        self.assertEqual((gated["zero_shot"], gated["motion"], gated["scene_change"], gated["reading_signal"]),
                         ("unavailable", "active", "no_prior", "medium"))
        self.assertEqual([scene.motion_word(v) for v in (0, .039, .04, .119, .12)], ["still", "still", "some", "some", "active"])
        self.assertEqual(scene.facts(result(request(previous="p" * 64), scene_drift=.08))["scene_change"], "changed")

    def test_summary_is_causal_and_bounded_to_sixty_seconds(self):
        values = [receipt(t, posture="standing") for t in range(0, 30, 2)]
        values += [receipt(t, drift=.3 if t == 80 else .01) for t in range(70, 100, 2)]
        summary = scene.projection(values, 100)["summary_last_60s"]
        self.assertEqual(summary["posture"], {"value": "sitting", "agreement": "all"})
        self.assertEqual((summary["scene_changes"], summary["last_scene_change"]), ("one", "about_20_s"))
        self.assertEqual(summary["reading_signal_high"], "all")
        self.assertEqual(summary["zero_shot_high"], {"phone_use": "none", "computer_work": "none", "reading": "all"})
        empty = scene.projection([], 100)
        self.assertEqual((empty["status"], empty["latest"], empty["summary_last_60s"]["valid_samples"]), ("stale", None, "none"))


class SharedRecordTests(unittest.TestCase):
    def test_every_arm_gets_the_identical_record(self):
        values = [receipt(t) for t in range(80, 100, 2)]
        projected = scene.projection(values, 100)
        record = scene.scene_record(projected, values, 100, site_id="victoria", camera_id="den", session_id="s" * 32)
        arms = scene.shared_records(record, arms={"a", "b"}, generation=3)
        strip = lambda r: {k: v for k, v in r.items() if k != "arm"}
        self.assertEqual(strip(arms["a"][0]), strip(arms["b"][0]))
        self.assertEqual(arms["a"][0]["vjepa_scene"], projected)
        self.assertEqual(list(scene.shared_records(record, arms={"b"}, generation=3)), ["b"])
        self.assertEqual(scene.shared_records(None, arms={"a", "b"}, generation=3), {})
        self.assertEqual((record["source_kind"], record["quality"]["correlated_with"]["relation"]),
                         ("vjepa.observation", "same_camera_visual"))

    def test_record_identity_is_stable_until_new_receipts(self):
        values = [receipt(t) for t in range(80, 100, 2)]
        first = scene.scene_record(scene.projection(values, 100), values, 100, site_id="victoria", camera_id="den", session_id="s" * 32)
        again = scene.scene_record(scene.projection(values, 100.5), values, 100.5, site_id="victoria", camera_id="den",
                                   session_id="s" * 32)
        self.assertEqual(first["id"], again["id"])
        newer = values + [receipt(100)]
        third = scene.scene_record(scene.projection(newer, 102), newer, 102, site_id="victoria", camera_id="den", session_id="s" * 32)
        self.assertNotEqual(first["id"], third["id"])

    def test_inputs_are_not_mutated(self):
        values = [receipt(t) for t in range(80, 100, 2)]
        before = copy.deepcopy(values)
        projected = scene.projection(values, 100)
        projected["latest"]["facts"]["posture"]["top"] = "x"
        self.assertEqual(values, before)


if __name__ == "__main__":
    unittest.main()
