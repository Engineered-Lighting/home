"""Optional model wiring preserves the current camera, reading and private-credential boundaries."""
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
import copy

import enrichment_link
import perceive
import run_local
import run_service
from test_enrichment_link import Clock, PROFILE, FakeClient, frame, snapshot


class ServiceWiringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def block(self):
        return dict(enabled=True, port=18199, profile_file=str(self.root / "profile.json"),
                    frame_root=str(self.root / "frames"), dependencies_file=str(self.root / "source.json"))

    def test_only_explicit_optional_settings_and_own_credential_enable_lane(self):
        self.assertEqual(run_service.enrichment_settings({}, self.root), (None, "disabled"))
        self.assertEqual(run_service.enrichment_settings({"enrichment": {"enabled": False}}, self.root), (None, "disabled"))
        value = self.block()
        self.assertIsNone(run_service.enrichment_settings({"enrichment": value}, self.root)[0])
        (self.root / run_service.MODEL_TOKEN).write_text("a" * 64)
        settings, reason = run_service.enrichment_settings({"enrichment": value}, self.root)
        self.assertIsNone(reason)
        self.assertEqual(settings["token_file"], str(self.root / run_service.MODEL_TOKEN))
        for change in ({"port": True}, {"frame_root": "relative"}, {"token_file": "HA-secret"},
                       {"duration_s": 2}, {"frame_access": "world"}, {"frame_access": "private_group", "frame_group": True}):
            with self.subTest(change=change):
                self.assertIsNone(run_service.enrichment_settings({"enrichment": dict(value, **change)}, self.root)[0])

    def test_optional_unit_opens_only_scoped_inputs_and_its_own_token(self):
        block = dict(enabled=True, port=9461, frame_root="/run/home-den-model-input/frames",
                     dependencies_file="/run/home-den-model-input/source/source.json",
                     profile_file="/opt/den-models/release/profiles/vjepa-lm-qwen3-8b.json",
                     frame_access="private_group", frame_group=993)
        plain = run_service.dropin_text("192.0.2.3", Path("/srv/packs"))
        self.assertNotIn("model-worker-token", plain)
        self.assertNotIn("SupplementaryGroups", plain)
        text = run_service.dropin_text("192.0.2.3", Path("/srv/packs"), enrichment=block)
        self.assertIn("LoadCredential=model-worker-token.txt:/etc/den-live/private/model-worker-token.txt", text)
        self.assertIn("SupplementaryGroups=993", text)
        self.assertIn("ReadWritePaths=/run/home-den-model-input/frames /run/home-den-model-input/source", text)
        self.assertNotIn("ha-token", text)
        for change in ({"frame_root": "/"}, {"frame_root": "/etc/den-live/private"},
                       {"frame_root": "/run/frames\nReadWritePaths=/"}, {"frame_group": True},
                       {"profile_file": "/run/home-den-model-input/frames/profile.json"}):
            with self.subTest(change=change), self.assertRaises(run_service.Refusal):
                run_service.enrichment_dropin(dict(block, **change))

    def test_source_receipt_has_real_observation_expiry_not_publish_time(self):
        clock = Clock()
        settings = dict(enabled=True, frame_root=str(self.root / "frames"), dependencies_file=str(self.root / "source.json"))
        bridge = enrichment_link.Bridge(settings, camera_id="camera", clock=clock,
            profile=PROFILE, client=FakeClient(clock), threaded=False)
        bridge.offer_snapshot(snapshot())
        bridge.step()
        import json
        value = json.loads((self.root / "source.json").read_text())
        self.assertEqual(value["observed_at"], 100.1)
        self.assertEqual(value["valid_until"], 101.5)
        self.assertEqual(len(value["dependencies"]), 1)
        clock.value = 105
        bridge.step()
        self.assertEqual(json.loads((self.root / "source.json").read_text()), value)

    def test_observer_passes_same_current_snapshot_to_lighting_log_and_bridge(self):
        original, decorated = snapshot(), snapshot(100.1)
        original.seq = decorated.seq = 3
        calls = []
        class Asker:
            snapshot = decorated
            def observe(self, event, source):
                return dict(event, ask=True)
            def pop_reply(self):
                return "reply"
        lighting = NS(offer_snapshot=lambda current: calls.append(("lighting", current)),
                      offer_inference=lambda reply: calls.append(("reply", reply)))
        log = NS(write=lambda current: calls.append(("log", current)))
        bridge = NS(decorate=lambda event, current: calls.append(("bridge", current)) or dict(event, projected=True))
        event = run_local.evidence_observer(Asker(), lighting, log, enrichment=bridge)({}, original)
        self.assertTrue(event["projected"])
        self.assertEqual(calls, [("lighting", decorated), ("reply", "reply"), ("log", decorated), ("bridge", decorated)])

    def test_rival_without_pose_cannot_enter_single_track_intent_clip(self):
        clock = Clock()
        decoded = []
        bridge = enrichment_link.Bridge(dict(enabled=True, frame_root=str(self.root / "frames")),
            camera_id="camera", clock=clock, profile=PROFILE, client=FakeClient(clock), threaded=False,
            decoder=lambda value: decoded.append(value) or bytes(16 * 16 * 3))
        current = snapshot()
        deps, _ = enrichment_link.binding(current, "camera")
        reply = dict(row=dict(frame=dict(width=16, height=16), detections=[
            dict(**{"class": "person"}, score=.9, keypoints=[[2, 2, .9]] * 17, box_xyxy=[1, 1, 15, 15]),
            dict(**{"class": "person"}, score=.9, keypoints=None, box_xyxy=[2, 2, 14, 14])]))
        bridge._frame(current, NS(ref=current.frame), reply, deps=deps)
        self.assertEqual(decoded, [])
        self.assertFalse((self.root / "frames").exists())

    def test_local_handshake_does_not_assert_external_qwen_loaded(self):
        process = NS(poll=lambda: None)
        detector = NS(proc=process, devices={"boxes": "cuda:0", "pose": "cpu"})
        stages = run_local.model_stages(detector, object(), 100)
        self.assertTrue(stages[0]["loaded"])
        self.assertEqual(stages[1]["device"], "cpu")
        self.assertIsNone(stages[2]["loaded"])
        self.assertIsNone(stages[2]["model_id"])
        process.poll = lambda: 1
        self.assertFalse(run_local.model_stages(detector, None, 101)[0]["loaded"])

    def test_async_loop_offers_exact_completed_prepared_frame(self):
        ref = frame()
        prepared = NS(ref=ref)
        reply = {"row": {}}
        detector = NS(submit=lambda *args: True, poll=lambda: reply)
        state = NS(frame=None, camera_epoch=1)
        p = NS(snapshot=state, sessions=1, detector=detector, body_input=None,
               _timing=lambda *args, **kwargs: None, prepare=lambda *args: prepared)
        def finish(value, result, camera, at):
            self.assertIs(value, prepared)
            self.assertIs(result, reply)
            p.snapshot = NS(frame=ref, camera_epoch=1)
            return {"type": "people"}
        p.finish = finish
        mailbox = NS(take=lambda timeout: object())
        seen = []
        def sink(current, value, result):
            seen.append((current.frame, value, result))
            loop.halt.set()
        loop = perceive.Loop(NS(state=lambda: {"status": "ok"}), mailbox, p, lambda event: None,
                             clock=lambda: 100.1, frame_sink=sink)
        loop.start()
        loop.join(2)
        self.assertFalse(loop.is_alive())
        self.assertIsNone(loop.stop_reason)
        self.assertEqual(seen, [(ref, prepared, reply)])

    def test_worker_reply_reaches_real_preview_as_advisory_without_changing_target(self):
        from test_preview_walking import PreviewWalkingTest
        from test_enrichment_link import packet
        from den_models.contracts import profile_digest
        PreviewWalkingTest.setUpClass()
        self.addCleanup(PreviewWalkingTest.doClassCleanups)
        case = PreviewWalkingTest("runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.seq = 39
        current = case.offer()
        before = copy.deepcopy(case.eligible())
        raw = case.intent_wire()
        raw.update(profile_digest=profile_digest(PROFILE), model_revision=PROFILE["revision"])
        request = {key: copy.deepcopy(raw[key]) for key in
                   ("request_id", "clip_id", "profile_digest", "dependencies", "expires_at")}
        request.update({key: copy.deepcopy(raw["evidence"][key]) for key in
                        ("frames", "source_size", "roi_xyxy", "cutoff_at", "duration_seconds")})
        bridge = enrichment_link.Bridge(camera_id="synthetic-camera", clock=lambda: case.now,
                                         on_hint=case.preview.offer_intent)
        bridge.profile, bridge.latest = PROFILE, current
        bridge.requests[request["request_id"]] = request
        bridge.client = NS(poll=lambda _: packet(request, "succeeded", raw))
        bridge._results(current, case.now)
        self.assertEqual(bridge.counts["completed"], 1)
        self.assertEqual(case.report()["proposals"], [before])
        evidence = case.report()["evidence"]["intent"]
        self.assertEqual(evidence["request_id"], request["request_id"])
        self.assertFalse(evidence["consumed"])
        self.assertFalse(evidence["selected"])
        self.assertEqual(case.encoded, [])


if __name__ == "__main__":
    unittest.main()
