"""Offline RGB provider integration using native generated bundles and the measured-body worker."""
import json
import sys
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from body import BodyChannel, head_exclusion
from body_clock import AGE_HEADER, AGE_SCHEMA
from body_head import HeadProfile
from body_link import BodyLink
from body_rgb import RgbBodyInput, RgbBodyProfile
import install_la
from perceive import Perceiver, Tools, track_method_copy
import run_service
from sofa_check import archive
from source import Bundle, camera_client

CALIBRATION = "a" * 64
TIMING_EVIDENCE = "fixture-capture-clock"
CORRECTION = {"applied": True, "factor": 0.889, "calibration_sha256": CALIBRATION}
REVISION = "synthetic-scan"


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Fixture:
    def __init__(self, scratch):
        self.tools = Tools(archive(scratch, "observe"), archive(scratch, "track"))
        import fake_detector
        import synth

        self.synth = synth
        self.rows = fake_detector
        self.scene = synth.Scene()
        self.depth = self.scene.depth_mm()
        self.empty_depth = (4200).to_bytes(2, "little") * (640 * 400)
        # A distinct foreground silhouette and shoulder/hip seeds outside
        # the conservative neck exclusion, using the pinned projection.
        joints = {**synth.JOINTS, "left_hip": (0.16, 0.95), "right_hip": (-0.16, 0.95),
                  "left_shoulder": (0.23, 1.41), "right_shoulder": (-0.23, 1.41)}
        self.pixels = {}
        for name, (x, z) in joints.items():
            left = synth.g.apply(*self.scene.left_from_room, (x, synth.PANEL_Y, z))
            self.pixels[name] = synth.g.project(
                synth.g.apply(*synth.RGB_FROM_LEFT, left), self.scene.k_rgb, self.scene.d_rgb)
        self.model = scratch / "room-model.jsonl"
        self.model.write_text(json.dumps(synth.room_model()), encoding="utf-8")
        self.method = track_method_copy(self.tools, [-2.0, -1.0, 5.0, 6.0], scratch)
        self.clock = Clock()
        self.perceiver = Perceiver(self.tools, room_model_path=self.model, camera_id="den-oak",
                                   max_error_bound_m=0.10, track_method_path=self.method, home="den",
                                   content_revision=REVISION, now=lambda: 1_800_000_000.0 + self.clock())
        self.channel = BodyChannel()
        self.detector = SimpleNamespace(next_id=0, on_body=None, detect=self.detect, send_alone=self.send)
        self.perceiver.detector = self.detector
        self.rendered = []
        self.link = BodyLink(self.detector, stream=SimpleNamespace(publish_body=self.publish), clock=self.clock,
                             min_interval_s=0)
        self.perceiver.body = self.link
        self.block = {"qualification_id": "fixture-profile", "clock_evidence_id": TIMING_EVIDENCE,
                      "transform_id": self.perceiver.camera.transform_id,
                      "scan_revision": REVISION, "min_valid_depth_fraction": 0.2,
                      "head": asdict(HeadProfile(0.13, 0.055, 0.35, 1.0, 0.006, 0.5, 0.085))}
        self.receipt = {"source_correction": {"calibrations": [CALIBRATION], "factors": [0.889],
                                             "frames": 3, "applied": 3},
                        "fit": {"result": "registered", "transform_id": self.block["transform_id"]},
                        "scan_revision": REVISION}
        profile = RgbBodyProfile.from_config(self.block, self.receipt)
        self.provider = RgbBodyInput(profile, self.perceiver.camera, track_epoch=lambda: self.perceiver.sessions,
                                     clock=self.clock)
        self.perceiver.body_input = self.observe
        self.last = None
        self.reply = None
        self.luma = 110

    def publish(self, scope, **fields):
        self.rendered.append((scope, fields))
        return True

    def observe(self, *args, **kwargs):
        self.last = self.provider(*args, **kwargs)
        return self.last

    def detect(self, bundle, _, *, frame_key=None):
        self.detector.next_id += 1
        self.kept = (self.detector.next_id, bundle.data, frame_key)
        return self.reply

    def send(self, header):
        self.detector.on_body(self.channel.handle(header, self.kept))

    def frame(self, seq, *, empty=False, rival=False, receipt=True, age=0.05,
              correction=CORRECTION, depth=None, skew=0.015, session="fixture-camera", missing_ear=False,
              timing_evidence=TIMING_EVIDENCE):
        capture = 5100.0 + seq * 0.2
        self.clock.now = 100.0 + seq * 0.2 + 0.07
        meta = self.synth.metadata(session, seq, capture, capture - skew,
                                   {"rgb": self.synth.RGB_K, "depth": self.synth.LEFT_K})
        meta["depth_correction"] = correction
        pixels = {name: uv for name, uv in self.pixels.items() if not missing_ear or name != "right_ear"}
        row = self.rows.row(capture, rgb_seq=seq, pixels=pixels)
        if empty:
            row["detections"].clear()
        if rival:
            row["detections"].append({"id": "rival", "class": "person", "score": 0.55,
                                      "box_xyxy": [8.0, 12.0, 90.0, 65.0], "keypoints": None})
        self.reply = {"luma": self.luma, "row": row}
        depth = (self.empty_depth if empty else self.depth) if depth is None else depth
        proof = {"schema": AGE_SCHEMA, "frame_key": [session, seq, seq], "evidence_id": timing_evidence,
                 "rgb_age_upper_s": age, "depth_age_upper_s": age + skew, "separation_upper_s": skew + 0.01}
        item = Bundle(self.synth.bundle(meta, depth), session, seq, capture, 1_800_000_000 + self.clock(),
                      self.clock() - 0.01, self.clock() - 0.03, proof if receipt else None,
                      receipt_arrival_mono=self.clock() - 0.01)
        self.last = None
        return self.perceiver.process(item, {"status": "ok"}, self.clock())


class RgbBodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory(prefix="den-rgb-body-")
        cls.addClassCleanup(tmp.cleanup)
        cls.scratch = Path(tmp.name)

    def setUp(self):
        self.f = Fixture(self.scratch)
        self.addCleanup(self.f.link.close)

    def test_generated_bundles_produce_a_current_measured_head_free_figure(self):
        f = self.f
        for n in range(55):
            f.frame(n, empty=True)
        self.assertIsNotNone(f.channel.maker.background)
        for n in range(55, 70):
            f.frame(n)
        self.assertEqual(f.last.observation, "person")
        self.assertEqual(f.last.frame_key, ["fixture-camera", 69, 69])
        self.assertEqual(f.last.track["association"], "confident")
        self.assertLess(f.last.capture_earliest_mono, f.clock() - 0.08)
        _, output = next(((scope, fields) for scope, fields in reversed(f.rendered) if fields.get("frame")), (None, None))
        self.assertIsNotNone(output, f.link.why)
        cloud = output["frame"]
        self.assertGreater(cloud["points"]["count"], 0)
        self.assertNotIn("head_evidence", cloud)
        from test_body import decode
        bounds = head_exclusion(f.last.head_evidence, frame_key=f.last.frame_key, binding=f.last.binding())
        left, top, right, bottom = bounds
        import numpy as np
        keys = decode(cloud)["key"].reshape(-1)
        ys, xs = np.divmod(keys, 640)
        self.assertFalse(((left <= xs) & (xs < right) & (top <= ys) & (ys < bottom)).any())

    def test_stale_unqualified_and_dark_empty_frames_do_not_feed_learning(self):
        f = self.f
        for seq, changes in enumerate(({"receipt": False}, {"age": 1}, {"skew": 0.068},
                                       {"correction": dict(CORRECTION, calibration_sha256="b" * 64)},
                                       {"depth": bytes(640 * 400 * 2)}, {"timing_evidence": "other-profile"})):
            f.frame(seq, empty=True, **changes)
            self.assertIsNone(f.last)
        f.luma = 10
        f.frame(6, empty=True)
        self.assertIsNone(f.last)
        self.assertIsNone(f.channel.maker)

    def test_ambiguous_or_unbound_pose_clears(self):
        f = self.f
        for n in range(16):
            f.frame(n)
        self.assertEqual(f.last.observation, "person")
        f.frame(16, rival=True)
        self.assertEqual(f.last.observation, "ambiguous")
        f.frame(17, missing_ear=True)
        self.assertEqual(f.last.observation, "unknown")
        f.frame(18, session="fixture-camera-restarted")
        self.assertNotEqual(f.last.observation, "person")

    def test_normal_service_passes_registration_bound_profile_without_an_injected_callback(self):
        f = self.f
        creds = self.scratch / "credentials"
        creds.mkdir(exist_ok=True)
        token, receipt = creds / "camera-token.txt", creds / "register-receipt.json"
        token.write_text("fixture", encoding="ascii")
        receipt.write_text(json.dumps(f.receipt), encoding="utf-8")
        for name in run_service.MODEL_FILES:
            (self.scratch / name).touch()
        config = {"camera_url": "http://127.0.0.1:8090", "venv_python": sys.executable, "models": str(self.scratch),
                  "site": "fixture", "content_packs": str(self.scratch), "stream_port": 8091, "body_rgb": f.block}
        take = SimpleNamespace(read_token=lambda _: "fixture", Camera=lambda *args: SimpleNamespace(host="127.0.0.1", port=8090),
                               MAX_BODY=4194304)
        with (patch.object(run_service, "credentials", return_value=({"token": token, "receipt": receipt,
                                                                        "room_model": f.model}, self.scratch)),
              patch.object(run_service, "read_config", return_value=config),
              patch.object(run_service.rl, "den_take", return_value=take),
              patch.object(run_service.rl, "serve") as serve):
            run_service.run(config_path=Path("fixture-config.json"), env={}, trees={"observe": self.scratch})
        arguments = serve.call_args.kwargs
        self.assertIsNone(arguments["body_input"])
        self.assertEqual(arguments["body_profile"], f.provider.profile)
        self.assertEqual(arguments["picked_on"](f.block["transform_id"]), REVISION)

    def test_profile_refuses_a_different_registration(self):
        f = self.f
        with self.assertRaises(ValueError):
            RgbBodyProfile.from_config(dict(f.block, scan_revision="other"), f.receipt)

    def test_installer_reads_only_a_registration_bound_profile(self):
        f = self.f
        profile = self.scratch / install_la.BODY_PROFILE_FILE
        receipt = self.scratch / "fixture-registration.json"
        receipt.write_text(json.dumps(f.receipt), encoding="utf-8")
        self.assertIsNone(install_la.body_profile(profile, receipt))
        profile.write_text(json.dumps(f.block), encoding="utf-8-sig")
        self.assertEqual(install_la.body_profile(profile, receipt), f.block)
        profile.write_text(json.dumps(dict(f.block, transform_id="other")), encoding="utf-8")
        with self.assertRaisesRegex(install_la.Stop, "does not match"):
            install_la.body_profile(profile, receipt)


class TransportTests(unittest.TestCase):
    def test_age_receipt_comes_from_the_same_get(self):
        take = SimpleNamespace(Camera=lambda *args: SimpleNamespace(host="fixture", port=1234), MAX_BODY=100)
        camera = camera_client(take, "http://fixture:1234", "fixture-token", body_timing=True)
        header = (AGE_HEADER, json.dumps({"schema": AGE_SCHEMA, "frame_key": ["fixture", 2, 3]}))
        with patch("source.http.client.HTTPConnection") as connect:
            socket = connect.return_value
            response = socket.getresponse.return_value
            response.status, response.length = 200, 0
            response.read.return_value = b"bundle"
            response.getheaders.return_value = [header]
            self.assertEqual(camera.get_bundle(), (200, b"bundle", json.loads(header[1])))
            socket.request.assert_called_once_with("GET", "/frame.npz", headers={"Authorization": "Bearer fixture-token"})
            socket.close.assert_called_once()
            response.getheaders.return_value = [header, header]
            self.assertEqual(camera.get_bundle(), (200, b"bundle", None))


if __name__ == "__main__":
    unittest.main()
