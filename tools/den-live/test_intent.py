"""Causal semantic evidence stays separate from geometric/lighting authority."""
import copy
import unittest

import intent


def wire():
    deps = dict(camera_id="camera", service_run_id="run", camera_epoch="1",
                source_generation=intent.generation("run", 1), track_id="person", association_revision="2",
                transform_id="transform", scan_revision="scan", calibration_revision="a" * 64)
    frames = []
    for i in range(32):
        at = 96 + (i + .5) * 4 / 32
        source = dict(service_run_id="run", camera_epoch=1, frame_id=i + 1, camera_session="camera-session",
                      mode="rgb", rgb=dict(seq=i + 1, device_s=at, host_s=at, width=640, height=360),
                      right=None, depth=None, depth_calibration_sha256="a" * 64, transform_id="transform",
                      scan_revision="scan", capture_mono=at, arrival_mono=at + .01, capture_ts=1800000000 + at)
        frames.append(dict(frame_id=str(i + 1), blob_ref="private.ppm", sha256="b" * 64,
                           captured_at=at, sample_at=at, repeated=False, track_bbox_xyxy=[100, 10, 200, 300],
                           source_frame=source))
    return dict(schema=intent.SCHEMA, request_id="request", clip_id="clip", profile_digest="c" * 64,
                snapshot_manifest_digest="d" * 64, model_revision="e" * 40, dependencies=deps,
                evidence=dict(frames=frames, source_size=[640, 360], roi_xyxy=[0, 0, 360, 360],
                              source_to_model=[[384/360, 0, 0], [0, 384/360, 0], [0, 0, 1]], cutoff_at=100,
                              duration_seconds=4, sampling="causal_slot_center_sample_hold/v1", additional_center_crop=False),
                produced_at=100.1, expires_at=102, intent="continue",
                raw_option_scores={key: float(i) for i, key in enumerate(intent.CHOICES)},
                text="private raw text", uncertainty="uncalibrated_semantic_hint", authority="semantic_prior_only")


REGISTERED = "den-oak@2026-11-13.1"   # the live registration's transform_id shape: <camera>@<version>


def registered(raw, transform_id=REGISTERED):
    raw["dependencies"]["transform_id"] = transform_id
    for entry in raw["evidence"]["frames"]:
        entry["source_frame"]["transform_id"] = transform_id
    return raw


def trusted(raw):
    request = {key: copy.deepcopy(raw[key]) for key in ("request_id", "clip_id", "profile_digest",
                                                        "dependencies", "expires_at")}
    request.update({key: copy.deepcopy(raw["evidence"][key]) for key in
                    ("frames", "source_size", "roi_xyxy", "cutoff_at", "duration_seconds")})
    return request


def current():
    track = dict(track_id="person", association_revision=2, state="confirmed", association_status="confident",
                 current_obs_ids=["observation"])
    frame = dict(service_run_id="run", camera_epoch=1, transform_id="transform", scan_revision="scan",
                 depth_calibration_sha256="a" * 64, capture_mono=100.2)
    return dict(service_run_id="run", camera_epoch=1, status="fresh", presence="present", frame=frame), track


class IntentTest(unittest.TestCase):
    def test_validated_hint_is_immutable_and_never_grants_authority(self):
        raw = wire()
        hint = intent.from_wire(raw, now_mono=100.2)
        raw["dependencies"]["track_id"] = "changed"
        self.assertEqual(hint.dependencies["track_id"], "person")
        with self.assertRaises(TypeError):
            hint.evidence["frames"][0]["captured_at"] = 0
        snapshot, track = current()
        self.assertTrue(hint.matches(snapshot, track))
        public = hint.public(now_mono=100.2, snapshot=snapshot, track=track)
        self.assertEqual(public["disposition"], "advisory_only")
        self.assertFalse(public["consumed"])
        self.assertNotIn("private", str(public))
        self.assertNotIn("blob_ref", str(public))

    def test_old_saved_request_does_not_replace_current_authority(self):
        hint = intent.from_wire(wire(), now_mono=100.2)
        snapshot, track = current()
        for change in (dict(association_revision=3), dict(association_status="ambiguous"), dict(state="coasting")):
            self.assertEqual(hint.disposition(100.2, snapshot, dict(track, **change))[0], "ignored")
        snapshot["camera_epoch"] = 2
        self.assertEqual(hint.disposition(100.2, snapshot, track)[0], "ignored")

    def test_expiry_is_original_and_cannot_be_reposted(self):
        hint = intent.from_wire(wire(), now_mono=100.2)
        self.assertEqual(hint.disposition(102)[0], "expired")
        self.assertEqual(hint.public(now_mono=200)["expires_at"], 102)
        for key, value in (("expires_at", 111), ("produced_at", 101), ("produced_at", 99)):
            bad = wire(); bad[key] = value
            with self.assertRaises(ValueError):
                intent.from_wire(bad, now_mono=100.2)

    def test_future_or_substituted_source_frames_refuse(self):
        for key, value in (("capture_mono", 103), ("camera_epoch", 2), ("transform_id", "other"),
                           ("arrival_mono", 101), ("frame_id", -1), ("frame_id", True),
                           ("frame_id", "1"), ("camera_session", "")):
            bad = wire(); bad["evidence"]["frames"][0]["source_frame"][key] = value
            with self.assertRaises(ValueError):
                intent.from_wire(bad, now_mono=100.2)

    def test_current_frame_and_snapshot_cannot_disagree(self):
        hint = intent.from_wire(wire(), now_mono=100.2)
        snapshot, track = current()
        for key, value in (("service_run_id", "other"), ("camera_epoch", 2)):
            changed = copy.deepcopy(snapshot); changed["frame"][key] = value
            self.assertFalse(hint.matches(changed, track))

    def test_trusted_request_checks_model_crop_and_frame_dependencies(self):
        raw = wire()
        request = {key: copy.deepcopy(raw[key]) for key in ("request_id", "clip_id", "profile_digest",
                                                            "dependencies", "expires_at")}
        request.update({key: copy.deepcopy(raw["evidence"][key]) for key in
                        ("frames", "source_size", "roi_xyxy", "cutoff_at", "duration_seconds")})
        self.assertTrue(intent.from_wire(raw, now_mono=100.2, request=request))
        request["roi_xyxy"] = [1, 0, 361, 360]
        with self.assertRaisesRegex(ValueError, "hint_request_mismatch"):
            intent.from_wire(raw, now_mono=100.2, request=request)

    def test_registered_transform_lineage_binds_by_exact_equality(self):
        raw = registered(wire())
        hint = intent.from_wire(raw, now_mono=100.2, request=trusted(raw))
        snapshot, track = current()
        snapshot["frame"]["transform_id"] = REGISTERED
        self.assertTrue(hint.matches(snapshot, track))
        self.assertEqual(hint.disposition(100.2, snapshot, track), ("advisory_only", "awaiting_held_out_admission"))
        for other in ("den-oak@2026-11-13.2", "DEN-OAK@2026-11-13.1", "den-oak", "transform"):
            changed = copy.deepcopy(snapshot); changed["frame"]["transform_id"] = other
            self.assertEqual(hint.disposition(100.2, changed, track), ("ignored", "current_binding_changed"))

    def test_mismatched_registered_transform_refuses(self):
        bad = registered(wire()); bad["evidence"]["frames"][5]["source_frame"]["transform_id"] = "den-oak@2026-11-13.2"
        with self.assertRaisesRegex(ValueError, "hint_source_mismatch"):
            intent.from_wire(bad, now_mono=100.2)
        raw = registered(wire())
        request = trusted(raw)
        request["dependencies"]["transform_id"] = "den-oak@2026-11-13.2"
        with self.assertRaisesRegex(ValueError, "hint_request_mismatch"):
            intent.from_wire(raw, now_mono=100.2, request=request)

    def test_lineage_grammar_admits_the_at_sign_only_in_the_transform(self):
        for value in (REGISTERED, "den-oak@" + "1" * 120, "registered-room@2"):
            self.assertEqual(intent.from_wire(registered(wire(), value), now_mono=100.2).dependencies["transform_id"], value)
        for value in ("den-oak@2026/11/13", "den-oak/../x", "den-oak@2026-11-13.1 ", "den oak@1", "den-oak@1\n",
                      "den-oak\t@1", "", "den-oak@" + "1" * 121, "den-oak＠1", "den-oak@1;x", None, 1):
            with self.assertRaisesRegex(ValueError, "invalid_hint_dependencies", msg=repr(value)):
                intent.from_wire(registered(wire(), value), now_mono=100.2)
        bad = registered(wire()); bad["dependencies"]["scan_revision"] = "scan/1"
        with self.assertRaisesRegex(ValueError, "invalid_hint_dependencies"):
            intent.from_wire(bad, now_mono=100.2)
        # Same field scope as the den-models worker: every other binding keeps the narrower grammar.
        for key in ("camera_id", "service_run_id", "camera_epoch", "source_generation", "track_id",
                    "association_revision", "scan_revision", "calibration_revision"):
            bad = registered(wire()); bad["dependencies"][key] = "den-oak@1"
            with self.assertRaisesRegex(ValueError, "invalid_hint_dependencies", msg=key):
                intent.from_wire(bad, now_mono=100.2)
        # Request/clip/session identities are not lineage and keep the narrower grammar.
        bad = registered(wire()); bad["request_id"] = "request@1"
        with self.assertRaisesRegex(ValueError, "invalid_hint_identity"):
            intent.from_wire(bad, now_mono=100.2)
        bad = registered(wire())
        for entry in bad["evidence"]["frames"]:
            entry["source_frame"]["camera_session"] = "camera@session"
        with self.assertRaisesRegex(ValueError, "invalid_source_frame"):
            intent.from_wire(bad, now_mono=100.2)

    def test_string_integer_bindings_are_canonical(self):
        for value in ("02", "-1", "2.0", "２"):
            bad = wire(); bad["dependencies"]["association_revision"] = value
            with self.assertRaises(ValueError):
                intent.from_wire(bad, now_mono=100.2)


if __name__ == "__main__":
    unittest.main()
