"""Walking annotations through the real Preview, without a camera, model or sender.

The qualified floor and fast fixture are explicitly synthetic virtual profiles;
they establish no physical commissioning or hardware timing facts.
"""
import copy
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import coordinator_live
from contracts import FrameRef, HeadPoint, InferenceReply, PlaneRef, SeatedState, TrackEvidence, TrackingSnapshot
import intent
import preview
import seated as seated_rule
import sofa_check
import test_intent
import test_preview as reading
import test_walking as walking_fixtures


WALL = 1_800_000_000.0
BINDING = {"transform_id": "walk-transform", "scan_revision": "walk-scan",
           "equipment_revision": 1, "equipment_digest": "synthetic-equipment"}


class PreviewWalkingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tree.cleanup)
        cls.core, _learn = coordinator_live.load_core(sofa_check.archive(Path(tree.name), "coordinator"))

    def setUp(self):
        self.now, self.seq = 100.2, 9
        self.encoded = []
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.preview = self.make_preview()

    def make_preview(self, *, walking="virtual", state=None, fixtures=None, binding=None, encoded=None):
        if fixtures is None:
            fixtures = copy.deepcopy(walking_fixtures.fixtures(self.core))
            for row in fixtures.values():
                row["initial_cdeg"] = row.pop("current")
                row["profile"]["range"] = {"pan": [-175, 175], "tilt": [-90, 90]}
        config = walking_fixtures.configuration(walking) if isinstance(walking, str) else walking
        made = preview.Preview(self.core, binding=binding or BINDING, fixtures=fixtures,
                               state=state or self.directory.name, clock=lambda: self.now,
                               utc_offset_h=0, threaded=False,
                               append=self.encoded.append if encoded is None else encoded.append,
                               walking=config)
        made.wall = lambda: WALL
        self.assertEqual(made.configure(reading.INSTANT)[0], 200)
        return made

    def capture(self, *, value=None, seq=None, seated=False, **changes):
        """A full accepted tracking receipt with the actual typed MotionState."""
        self.seq = self.seq + 1 if seq is None else seq
        at = round(self.now - .2, 6)
        value = value or walking_fixtures.state(
            frame_id=self.seq, observed_mono=at, available_mono=at + .05,
            valid_until_mono=at + 1.5, xy_m=((at - 100) * .5, 0),
            source_obs_ids=("walk-obs-%s" % self.seq,))
        plane = PlaneRef(value.frame_id, at, at, 640, 360)
        frame = FrameRef(value.service_run_id, value.camera_epoch, value.frame_id,
                         "synthetic-camera", "rgb", plane, None, plane,
                         value.depth_calibration_sha256, value.transform_id, value.scan_revision,
                         value.observed_mono, value.available_mono, WALL + value.observed_mono)
        pose = {name: [(xyz[0] - 1.2 + value.xy_m[0], xyz[1] - 1 + value.xy_m[1], xyz[2]), support]
                for name, (xyz, support) in reading.joints(seated).items()}
        track = TrackEvidence(value.track_id, "confirmed", value.xy_m, .01, "stereo", value.source_obs_ids,
                              "confident", value.association_revision, at - .6, pose,
                              seated=SeatedState(seated, at - .6 if seated else None, "hips" if seated else None),
                              head_point_m=HeadPoint((*value.xy_m, 1.2 if seated else 1.7), 3),
                              posture="sitting" if seated else "walking", motion=value)
        result = TrackingSnapshot(value.service_run_id, value.camera_epoch, self.seq, frame, "fresh", None,
                                  "present", (track,), value.track_id if seated else None, 1)
        return dataclasses.replace(result, **changes)

    def offer(self, **keywords):
        snapshot = self.capture(**keywords)
        self.preview.offer_snapshot(snapshot)
        return snapshot

    def report(self):
        return self.preview.latest["path_lighting"]

    def eligible(self):
        report = self.report()
        self.assertEqual(report["status"], "eligible", report)
        self.assertEqual(len(report["proposals"]), 1)
        return report["proposals"][0]

    def empty(self):
        self.assertEqual(self.report()["proposals"], [], self.report())
        self.assertEqual(self.report()["virtual_beams"], [], self.report())

    def event(self):
        row = self.preview.latest["fixtures"]["F3"]
        return {"schema": "den-manual-1", "event_id": "walk-event-0001", "fixture_id": "F3",
                "before": {"spotlight": {"brightness": row["level"]}},
                "after": {"spotlight": {"brightness": .4}}, "at_ms": 1,
                "snapshot": {"epoch": self.preview.epoch, "seq": self.preview.latest["seq"]},
                "client_id": "walk-client-0001", "client_seq": 1, "hold_id": "walk-hold-0001",
                "expected_revision": row["manual_revision"]}

    def intent_wire(self, *, request_id="walk-intent-request", cutoff=100.0, produced=None, dependencies=None):
        """Models' real 32-frame receipt, rebound to this synthetic Preview source."""
        value = test_intent.wire()
        deps = value["dependencies"]
        deps.update(camera_id="synthetic-camera", service_run_id="walk-run", camera_epoch="2",
                    track_id="walker", association_revision="3", transform_id="walk-transform",
                    scan_revision="walk-scan", calibration_revision="c" * 64)
        deps.update(dependencies or {})
        deps["source_generation"] = intent.generation(deps["service_run_id"], int(deps["camera_epoch"]))
        delta = cutoff - value["evidence"]["cutoff_at"]
        for entry in value["evidence"]["frames"]:
            entry["captured_at"] += delta
            entry["sample_at"] += delta
            source = entry["source_frame"]
            source.update(service_run_id=deps["service_run_id"], camera_epoch=int(deps["camera_epoch"]),
                          camera_session="synthetic-camera", transform_id=deps["transform_id"],
                          scan_revision=deps["scan_revision"], depth_calibration_sha256=deps["calibration_revision"])
            for key in ("capture_mono", "arrival_mono", "capture_ts"):
                source[key] += delta
            for key in ("device_s", "host_s"):
                source["rgb"][key] += delta
        value["request_id"] = request_id
        value["evidence"]["cutoff_at"] = cutoff
        value["produced_at"] = cutoff + .1 if produced is None else produced
        value["expires_at"] = cutoff + 2
        return value

    def intent_hint(self, **keywords):
        return intent.from_wire(self.intent_wire(**keywords), now_mono=self.now)

    def test_standing_motion_is_a_separate_virtual_lease_and_never_a_fixture_command(self):
        with patch.object(self.preview, "_aim", side_effect=AssertionError("walking aimed a fixture")), \
                patch.object(self.preview, "_submit", side_effect=AssertionError("walking submitted a command")):
            snapshot = self.offer()
            proposal = self.eligible()
            self.now += .05
            self.preview.tick()
        report = self.report()
        self.assertIs(self.preview.walking_snapshot, snapshot)
        self.assertEqual(report["schema"], "den-path-lighting-1")
        self.assertEqual(report["mode"], "virtual")
        self.assertEqual(len(report["virtual_beams"]), 1)
        self.assertEqual(proposal["target_m"][2], 0)
        self.assertGreater(proposal["target_m"][0], snapshot.tracks[0].xy_m[0])
        expected = {key: getattr(snapshot.tracks[0].motion, key) for key in
                    ("service_run_id", "camera_epoch", "frame_id", "track_id", "association_revision",
                     "transform_id", "scan_revision", "depth_calibration_sha256")}
        self.assertEqual(report["subject"], expected)
        self.assertEqual({key: proposal["source_motion"][key] for key in expected}, expected)
        self.assertEqual(proposal["source_motion"]["valid_until_mono"], snapshot.tracks[0].motion.valid_until_mono)
        self.assertEqual(proposal["authority"], "virtual_only")
        self.assertFalse(proposal["precision_qualified"])
        self.assertFalse(proposal["source_motion"]["time_qualified"])
        self.assertEqual(proposal["source_motion"]["time_status"], "estimated_source_elapsed")
        self.assertEqual(report["evidence"]["motion"]["basis"], snapshot.tracks[0].motion.basis)
        self.assertEqual(report["evidence"]["intent"]["disposition"], "absent")
        self.assertIsNone(self.preview.episode)
        self.assertFalse(self.preview.track["seated"])
        self.assertEqual(self.encoded, [])
        self.assertTrue(self.preview.jobs.empty())
        fixture = self.preview.latest["fixtures"]["F3"]
        self.assertFalse(fixture["on"])
        self.assertIsNone(fixture["target_m"])
        self.assertIsNone(fixture["move_sequence"])
        self.assertFalse(self.preview.bulbs_on)
        self.assertIsNone(self.preview.hue)
        self.assertIsNone(self.preview.trace.header["desk"])

    def test_shadow_exposes_proposal_without_drawing_or_encoding_a_beam(self):
        self.preview = self.make_preview(walking="shadow")
        self.offer()
        self.eligible()
        self.assertEqual(self.report()["mode"], "shadow")
        self.assertEqual(self.report()["virtual_beams"], [])
        self.assertEqual(self.encoded, [])

    def test_ticks_and_duplicate_frames_cannot_renew_the_original_motion_expiry(self):
        snapshot = self.offer()
        proposal = copy.deepcopy(self.eligible())
        expiry = snapshot.tracks[0].motion.valid_until_mono
        for now in (100.25, 100.3, 100.35):
            self.now = now
            self.preview.tick()
            self.assertEqual(self.report()["original_valid_until_mono"], expiry)
            for row in self.report()["proposals"] + self.report()["virtual_beams"]:
                self.assertEqual(row["valid_until_mono"], proposal["valid_until_mono"])
        # A newer publisher seq for the same camera frame cannot mint fresh motion.
        self.preview.offer_snapshot(dataclasses.replace(snapshot, seq=snapshot.seq + 1))
        self.assertEqual(self.report()["original_valid_until_mono"], expiry)
        self.now = expiry
        self.preview.tick()
        self.empty()
        self.assertEqual(self.encoded, [])

    def test_new_frame_validation_preserves_the_selected_plans_source_and_expiry(self):
        original = self.offer()
        proposal = copy.deepcopy(self.eligible())
        self.now += .1
        current = self.offer()
        report = self.report()
        self.assertEqual(report["status"], "cadence", report)
        self.assertEqual(report["proposals"], [proposal])
        self.assertEqual(report["original_valid_until_mono"], original.tracks[0].motion.valid_until_mono)
        self.assertEqual(report["evidence"]["motion"]["frame_id"], original.frame.frame_id)
        self.assertEqual(report["validation_source"]["frame_id"], current.frame.frame_id)
        self.assertEqual(report["validation_source"]["valid_until_mono"], current.tracks[0].motion.valid_until_mono)
        self.assertEqual(self.encoded, [])

    def test_stop_turn_stale_and_replayed_frame_revoke_before_the_cadence(self):
        for kind, reason in (("stop", "stationary"), ("turn", "motion_changed"),
                             ("stale", "stale_or_unknown_presence"), ("replay", "source_replayed")):
            with self.subTest(kind=kind):
                self.now, self.seq = 100.2, 9
                self.preview = self.make_preview()
                self.offer()
                self.eligible()
                self.now = 100.3
                receipt = self.capture()
                if kind in ("stop", "turn"):
                    value = dataclasses.replace(receipt.tracks[0].motion,
                                                velocity_mps=(0, 0) if kind == "stop" else (-.5, 0))
                    receipt = dataclasses.replace(receipt, tracks=(dataclasses.replace(receipt.tracks[0], motion=value),))
                elif kind == "stale":
                    receipt = dataclasses.replace(receipt, status="stale", frame=None, tracks=(), presence="unknown")
                else:
                    value = dataclasses.replace(receipt.tracks[0].motion, frame_id=1)
                    receipt = dataclasses.replace(receipt, frame=dataclasses.replace(receipt.frame, frame_id=1),
                                                  tracks=(dataclasses.replace(receipt.tracks[0], motion=value),))
                self.preview.offer_snapshot(receipt)
                self.empty()
                self.assertEqual(self.report()["reason"], reason)
                self.assertLess(self.now, 101.2, "the ordinary planner cadence has not elapsed")
        self.assertEqual(self.encoded, [])

    def test_delayed_snapshot_cannot_replace_a_fresh_stop_with_older_motion(self):
        older = self.offer()
        self.eligible()
        self.now += .1
        stopped = self.capture()
        stopped = dataclasses.replace(stopped, tracks=(dataclasses.replace(stopped.tracks[0],
            motion=dataclasses.replace(stopped.tracks[0].motion, velocity_mps=(0, 0))),))
        self.preview.offer_snapshot(stopped)
        self.empty()
        self.now += .1
        self.preview.offer_snapshot(older)
        self.assertIs(self.preview.walking_snapshot, stopped)
        self.assertEqual(self.preview.track_seq, stopped.seq)
        self.empty()
        self.assertEqual(self.report()["reason"], "stationary")
        self.assertEqual(self.encoded, [])

    def test_motion_shaped_json_cannot_substitute_for_the_tracker_motion_receipt(self):
        snapshot = self.capture()
        track = dataclasses.replace(snapshot.tracks[0], motion=dataclasses.asdict(snapshot.tracks[0].motion))
        self.preview.offer_snapshot(dataclasses.replace(snapshot, tracks=(track,)))
        self.empty()
        self.assertEqual(self.report()["reason"], "missing_or_stale_motion")
        self.assertEqual(self.encoded, [])

    def test_stop_and_off_require_a_new_accepted_frame_after_handback(self):
        for action in ("stop", "off"):
            with self.subTest(action=action):
                directory = tempfile.TemporaryDirectory()
                self.addCleanup(directory.cleanup)
                self.now, self.seq = 100.2, 9
                self.preview = self.make_preview(state=directory.name)
                self.offer()
                self.eligible()
                status, _ = self.preview.stop({}) if action == "stop" else self.preview.switch({"on": False})
                self.assertEqual(status, 200)
                self.empty()
                status, _ = (self.preview.resume({"stop_counter": self.preview.stop_counter}) if action == "stop"
                             else self.preview.switch({"on": True}))
                self.assertEqual(status, 200)
                self.empty()
                self.now += .1
                self.preview.tick()
                self.empty()
                # A delayed frame captured before the control is still too old,
                # even though its publisher sequence and arrival are newer.
                self.offer()
                self.empty()
                self.assertEqual(self.report()["reason"], "awaiting_post_control_frame")
                self.now = 101.3
                self.offer()
                self.eligible()

    def test_restart_has_no_motion_lease_and_needs_a_fresh_receipt(self):
        self.offer()
        self.eligible()
        old_epoch = self.preview.epoch
        self.preview = self.make_preview()
        self.assertNotEqual(self.preview.epoch, old_epoch)
        self.empty()
        self.assertIsNone(self.preview.walking_snapshot)
        self.now += .1
        self.preview.tick()
        self.empty()
        self.offer()
        self.eligible()
        self.assertEqual(self.encoded, [])

    def test_manual_hold_wins_during_cadence_and_survives_restart_without_learning(self):
        self.offer()
        self.eligible()
        self.now += .05
        status, reply = self.preview.manual_change(self.event())
        self.assertEqual(status, 200, reply)
        self.assertFalse(reply["learned"])
        self.empty()
        hand = copy.deepcopy(self.preview.latest["fixtures"]["F3"]["hand"])
        self.assertEqual(hand["hold_id"], "walk-hold-0001")
        self.preview = self.make_preview()
        self.empty()
        restored = self.preview.latest["fixtures"]["F3"]["hand"]
        self.assertEqual(restored["hold_id"], hand["hold_id"])
        self.assertEqual(restored["state"], hand["state"])
        self.now = 101.4
        self.offer()
        self.empty()
        self.assertEqual(self.report()["fixture_reasons"]["F3"], "manual_hold")
        status, reply = self.preview.manual_resume({"fixture_id": "F3", "hold_id": hand["hold_id"],
            "expected_revision": restored["revision"], "request_id": "walk-resume-0001"})
        self.assertEqual(status, 200, reply)
        self.assertIsNone(self.preview.latest["fixtures"]["F3"]["hand"])
        self.eligible()
        self.assertEqual(self.preview.state()[1]["learned_from_you"], {})
        self.assertEqual(self.encoded, [])

    def test_path_metadata_is_json_bounded_and_get_copies_cannot_mutate_the_projection(self):
        self.offer()
        self.eligible()
        status, state = self.preview.state()
        self.assertEqual(status, 200)
        self.assertEqual(state["path_lighting"], state["snapshot"]["path_lighting"])
        self.assertIsNot(state["path_lighting"], self.report())
        serialised = json.dumps(state["path_lighting"], allow_nan=False)
        self.assertLess(len(serialised.encode("utf-8")), 16_384)
        self.assertEqual(len(state["path_lighting"]["proposals"]), 1)
        state["path_lighting"]["proposals"][0]["target_m"][0] = 999
        self.assertNotEqual(self.report()["proposals"][0]["target_m"][0], 999)

    def test_typed_intent_callback_publishes_only_advisory_metadata_with_original_leases(self):
        self.seq = 39
        self.offer()
        proposal = copy.deepcopy(self.eligible())
        fixtures = copy.deepcopy(self.preview.latest["fixtures"])
        decision = copy.deepcopy(self.preview.latest_decision)
        episode = self.preview.episode
        before_seq = self.preview.seq
        hint = self.intent_hint()
        with patch.object(self.preview, "_evaluate", side_effect=AssertionError("intent evaluated lighting")), \
                patch.object(self.preview, "_aim", side_effect=AssertionError("intent aimed a fixture")), \
                patch.object(self.preview, "_submit", side_effect=AssertionError("intent submitted a command")):
            self.assertTrue(self.preview.offer_intent(hint))
        self.assertGreater(self.preview.seq, before_seq)
        self.assertEqual(self.preview.latest["fixtures"], fixtures)
        self.assertEqual(self.preview.latest_decision, decision)
        self.assertIs(self.preview.episode, episode)
        self.assertEqual(self.report()["proposals"], [proposal])
        self.assertEqual(self.report()["original_valid_until_mono"], proposal["valid_until_mono"])
        projection = self.report()["evidence"]["intent"]
        self.assertEqual((projection["disposition"], projection["reason"]),
                         ("ignored", "awaiting_held_out_admission"))
        self.assertFalse(projection["selected"])
        self.assertFalse(projection["consumed"])
        self.assertEqual(projection["authority"], "semantic_prior_only")
        self.assertEqual(projection["model_revision"], hint.model_revision)
        self.assertEqual(projection["dependencies"], dict(hint.dependencies))
        self.assertEqual(projection["snapshot_manifest_digest"], hint.snapshot_manifest_digest)
        self.assertEqual((projection["observed_at"], projection["produced_at"], projection["expires_at"]),
                         (100, 100.1, 102))
        self.assertEqual(len(projection["hypotheses"]), len(intent.CHOICES))
        self.assertTrue(all(row["calibrated_probability"] is False for row in projection["hypotheses"]))
        public = json.dumps(self.report(), allow_nan=False)
        self.assertLess(len(public.encode("utf-8")), 16_384)
        for private in ("private", "blob_ref", "source_frame", "track_bbox", "roi_xyxy", "raw text"):
            self.assertNotIn(private, public)
        self.assertEqual(self.encoded, [])

    def test_intent_retries_fence_the_entire_receipt_without_republishing_or_renewal(self):
        self.seq = 39
        self.offer()
        self.assertTrue(self.preview.offer_intent(self.intent_hint()))
        accepted = self.preview.walking_intent
        before = copy.deepcopy(self.preview.latest)
        before_seq = self.preview.seq
        self.now += .05
        with patch.object(self.preview, "_publish", side_effect=AssertionError("intent retry republished")):
            self.assertTrue(self.preview.offer_intent(self.intent_hint()))
            for part in ("text", "evidence"):
                conflicting = self.intent_wire()
                if part == "text":
                    conflicting["text"] = "different private text"
                else:
                    conflicting["evidence"]["frames"][0]["blob_ref"] = "different-private.ppm"
                self.assertFalse(self.preview.offer_intent(intent.from_wire(conflicting, now_mono=self.now)))
            for cutoff, produced in ((99.9, 100.05), (100, 100.0), (100, 100.1)):
                older = self.intent_hint(request_id="older-distinct-request", cutoff=cutoff, produced=produced)
                self.assertFalse(self.preview.offer_intent(older))
        self.assertIs(self.preview.walking_intent, accepted)
        self.assertEqual(self.preview.latest, before)
        self.assertEqual(self.preview.seq, before_seq)
        self.assertEqual(self.report()["evidence"]["intent"]["expires_at"], 102)
        self.assertEqual(self.encoded, [])

    def test_intent_callback_declines_stale_missing_untyped_and_wrongly_bound_receipts(self):
        self.assertFalse(self.preview.offer_intent(self.intent_hint()))
        self.seq = 39
        self.offer()
        before = copy.deepcopy(self.preview.latest)
        self.assertFalse(self.preview.offer_intent(self.intent_wire()))
        for dependency, changed in (("track_id", "other-person"), ("association_revision", "4"),
                                    ("camera_epoch", "3"), ("service_run_id", "other-run"),
                                    ("transform_id", "other-transform"), ("scan_revision", "other-scan"),
                                    ("calibration_revision", "a" * 64)):
            with self.subTest(dependency=dependency):
                self.assertFalse(self.preview.offer_intent(self.intent_hint(dependencies={dependency: changed})))
        self.assertEqual(self.preview.latest, before)
        self.assertIsNone(self.preview.walking_intent)
        expired = self.intent_hint()
        self.now += .1
        self.offer(status="stale", frame=None, tracks=(), presence="unknown")
        before = copy.deepcopy(self.preview.latest)
        self.assertFalse(self.preview.offer_intent(self.intent_hint()))
        self.assertEqual(self.preview.latest, before)
        self.now = expired.expires_at
        self.assertFalse(self.preview.offer_intent(expired))
        self.assertEqual(self.preview.latest, before)
        self.assertEqual(self.encoded, [])

    def test_a_distinct_newer_intent_receipt_updates_only_the_advisory_source(self):
        self.seq = 39
        self.offer()
        old = self.intent_hint()
        self.assertTrue(self.preview.offer_intent(old))
        self.now = 100.4
        self.offer()
        plan = copy.deepcopy(self.report()["proposals"])
        fixtures = copy.deepcopy(self.preview.latest["fixtures"])
        newer = self.intent_hint(request_id="newer-intent-request", cutoff=100.1, produced=100.3)
        self.assertTrue(self.preview.offer_intent(newer))
        self.assertEqual(self.report()["proposals"], plan)
        self.assertEqual(self.preview.latest["fixtures"], fixtures)
        self.assertEqual(self.report()["evidence"]["intent"]["request_id"], newer.request_id)
        self.assertEqual(self.report()["evidence"]["intent"]["expires_at"], newer.expires_at)
        before_seq = self.preview.seq
        before = copy.deepcopy(self.preview.latest)
        with patch.object(self.preview, "_publish", side_effect=AssertionError("old receipt republished")):
            self.assertTrue(self.preview.offer_intent(old), "an exact valid A retry retains A's accepted outcome")
            mutated_old = self.intent_hint(request_id=old.request_id, cutoff=100.15, produced=100.35)
            self.assertFalse(self.preview.offer_intent(mutated_old), "A's ID cannot be reused after B intervenes")
        self.assertEqual(self.preview.walking_intent_rejected["reason"], "request_id_conflict")
        self.assertIs(self.preview.walking_intent, newer)
        self.assertEqual(self.preview.seq, before_seq)
        self.assertEqual(self.preview.latest, before)
        self.assertEqual(self.report()["evidence"]["intent"]["request_id"], newer.request_id)
        self.assertEqual(self.report()["evidence"]["intent"]["expires_at"], newer.expires_at)
        self.assertEqual(self.report()["proposals"], plan)
        self.assertEqual(self.preview.latest["fixtures"], fixtures)
        self.now = old.expires_at
        self.assertFalse(self.preview.offer_intent(old), "freshness still precedes remembered acceptance")
        self.assertIs(self.preview.walking_intent, newer)
        self.assertEqual(self.preview.seq, before_seq)
        self.assertEqual(self.encoded, [])

    def test_rejected_intent_is_reported_on_the_next_tick_without_replacing_or_renewing_active_evidence(self):
        self.seq = 39
        self.offer()
        proposal = copy.deepcopy(self.eligible())
        active = self.intent_hint()
        self.assertTrue(self.preview.offer_intent(active))
        before = copy.deepcopy(self.preview.latest)
        before_seq = self.preview.seq
        self.now = 100.25
        rejected = self.intent_hint(request_id="older-rejected-request", cutoff=99.9, produced=100.05)
        with patch.object(self.preview, "_publish", side_effect=AssertionError("rejected intent republished")), \
                patch.object(self.preview, "_aim", side_effect=AssertionError("rejected intent aimed")), \
                patch.object(self.preview, "_submit", side_effect=AssertionError("rejected intent submitted")):
            self.assertFalse(self.preview.offer_intent(rejected))
        self.assertEqual(self.preview.seq, before_seq)
        self.assertEqual(self.preview.latest, before)
        self.assertIs(self.preview.walking_intent, active)
        self.now = 100.3
        self.preview.tick()
        evidence = self.report()["evidence"]
        diagnostic = evidence["intent_rejected"]
        self.assertEqual((diagnostic["request_id"], diagnostic["reason"], diagnostic["offered_mono"]),
                         (rejected.request_id, "older_intent_receipt", 100.25))
        self.assertEqual(diagnostic["disposition"], "ignored")
        self.assertFalse(diagnostic["consumed"])
        self.assertFalse(diagnostic["selected"])
        self.assertEqual(evidence["intent"]["request_id"], active.request_id)
        self.assertEqual(evidence["intent"]["expires_at"], active.expires_at)
        self.assertEqual(self.report()["proposals"], [proposal])
        self.assertEqual(self.report()["original_valid_until_mono"], proposal["valid_until_mono"])
        public = json.dumps(diagnostic, allow_nan=False)
        self.assertLess(len(public.encode("utf-8")), 4096)
        for private in ("private", "blob_ref", "source_frame", "track_bbox", "roi_xyxy", "text"):
            self.assertNotIn(private, public)
        self.assertEqual(self.encoded, [])
        self.assertEqual(self.preview.stop({})[0], 200)
        self.assertIsNone(self.preview.walking_intent_rejected)
        self.assertNotIn("intent_rejected", self.report()["evidence"])

    def test_intent_is_ignored_after_a_newer_measured_stop_or_turn_before_cadence(self):
        for velocity, reason in (((0, 0), "newer_measured_stop"), ((-.5, 0), "newer_measured_turn")):
            with self.subTest(velocity=velocity):
                self.now, self.seq = 100.2, 39
                self.preview = self.make_preview()
                self.offer()
                self.eligible()
                self.assertTrue(self.preview.offer_intent(self.intent_hint()))
                self.now = 100.3
                newer = self.capture()
                track = dataclasses.replace(newer.tracks[0],
                    motion=dataclasses.replace(newer.tracks[0].motion, velocity_mps=velocity))
                self.preview.offer_snapshot(dataclasses.replace(newer, tracks=(track,)))
                self.empty()
                projection = self.report()["evidence"]["intent"]
                self.assertEqual(projection["reason"], reason)
                self.assertEqual(projection["disposition"], "ignored")
                self.assertFalse(projection["selected"])
                self.assertFalse(projection["consumed"])
                self.assertEqual(projection["expires_at"], 102)
                self.assertLess(self.now, 101.2)
        self.assertEqual(self.encoded, [])

    def test_post_control_current_frame_cannot_admit_an_old_clip(self):
        self.seq = 39
        self.offer()
        hint = self.intent_hint()
        self.assertTrue(self.preview.offer_intent(hint))
        self.assertEqual(self.preview.stop({})[0], 200)
        self.assertEqual(self.preview.resume({"stop_counter": self.preview.stop_counter})[0], 200)
        self.assertIsNone(self.preview.walking_intent)
        self.now = 100.5
        self.offer()
        before_seq = self.preview.seq
        self.assertFalse(self.preview.offer_intent(hint))
        self.assertEqual(self.preview.seq, before_seq)
        self.assertIsNone(self.preview.walking_intent)
        self.assertTrue(self.preview.offer_intent(self.intent_hint(
            request_id="post-control-request", cutoff=100.25, produced=100.35)))
        self.assertEqual(self.report()["evidence"]["intent"]["observed_at"], 100.25)

    def test_default_preview_does_not_accept_optional_intent_callbacks(self):
        self.preview = self.make_preview(walking=None)
        self.seq = 39
        self.offer()
        before_seq = self.preview.seq
        self.assertFalse(self.preview.offer_intent(self.intent_hint()))
        self.assertEqual(self.preview.seq, before_seq)
        self.assertNotIn("path_lighting", self.preview.latest)
        self.assertEqual(self.encoded, [])

    def reading_replay(self, walking):
        """The existing seated reading policy, including actual encoded MOVE/KEEP_LIT rows."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.now = 100.0
        encoded = []
        with patch("preview.uuid.uuid4", return_value=SimpleNamespace(hex="same-preview-run")):
            made = self.make_preview(walking=walking, state=directory.name, fixtures=reading.FIXTURES,
                                     binding=reading.BINDING, encoded=encoded)
        rule, seq = seated_rule.Published(), 0

        def track():
            nonlocal seq
            seq += 1
            pose = reading.joints(True)
            made.offer_snapshot({"service_run_id": "run", "camera_epoch": 1, "seq": seq,
                "frame": {**reading.BINDING, "capture_mono": self.now}, "status": "fresh", "presence": "present",
                "eligibility_revision": 1, "eligible_track_id": "t1", "tracks": [{"track_id": "t1",
                "state": "confirmed", "position_basis": "stereo", "association_status": "confident",
                "association_revision": 2, "current_obs_ids": ("pose",), "supported_pose": pose,
                "seated": rule.update("t1", 2, self.now, pose)}]})

        def answer():
            at = self.now
            plane = PlaneRef(seq, at, at, 640, 360)
            frame = FrameRef("run", 1, seq, "synthetic", "rgb", plane, None, None, None,
                             reading.BINDING["transform_id"], reading.BINDING["scan_revision"], at, at, WALL + at)
            made.offer_inference(InferenceReply(str(at), "synthetic", "run", 1, "t1", 2, 1, (frame,),
                {"status": "valid", "facts": {"people_visible": "one", "activity_hypothesis": "reading"}},
                {"safe": True, "status": "confident", "window_start_mono": at, "window_end_mono": at},
                {}, {}, at, at, at + 10, {}))

        for _ in range(5):
            self.now += .5
            track()
        answer()
        self.now += .5
        track()
        answer()
        for _ in range(12):
            self.now += .25
            track()
        self.assertTrue(made.latest["fixtures"]["F3"]["on"])
        self.assertTrue(any(row["action"] == "aim_move" for row in encoded))
        self.assertTrue(any(row["action"] == "keep_lit" for row in encoded))
        return made, encoded

    def test_default_and_explicit_off_preserve_existing_reading_fixture_and_trace_payloads(self):
        baseline, baseline_trace = self.reading_replay(None)
        explicit, explicit_trace = self.reading_replay({"mode": "off"})
        self.assertNotIn("path_lighting", baseline.latest)
        self.assertNotIn("path_lighting", baseline.state()[1])
        self.assertEqual(explicit.latest["path_lighting"]["mode"], "off")
        additive = copy.deepcopy(explicit.latest)
        additive.pop("path_lighting")
        self.assertEqual(additive, baseline.latest)
        self.assertEqual(explicit.latest_decision, baseline.latest_decision)
        self.assertEqual(explicit_trace, baseline_trace)

    def test_reading_keeps_its_existing_priority_and_manual_state(self):
        baseline, baseline_trace = self.reading_replay(None)
        made, encoded = self.reading_replay(walking_fixtures.configuration())
        self.assertEqual(made.latest["path_lighting"]["reason"], "reading_active")
        self.assertEqual(made.latest["path_lighting"]["virtual_beams"], [])
        self.assertEqual(made.latest["fixtures"], baseline.latest["fixtures"])
        self.assertEqual(made.latest_decision, baseline.latest_decision)
        self.assertEqual(encoded, baseline_trace)
        before = copy.deepcopy(made.latest["fixtures"])
        episode = made.episode
        original = len(encoded)
        made.state()
        self.assertEqual(made.latest["fixtures"], before)
        self.assertIs(made.episode, episode)
        self.assertEqual(len(encoded), original)


if __name__ == "__main__":
    unittest.main()
