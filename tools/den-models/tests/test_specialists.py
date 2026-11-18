"""Synthetic CPU contracts, fake native sessions; no weights, GPU or downloads."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from den_models import specialist_cli as cli
from den_models import specialists as s
from den_models.contracts import ContractError, canonical, profile_digest, store_rgb_blob


class AdmissionTests(unittest.TestCase):
    def test_adapter_receipt_covers_the_trusted_control_producer_too(self):
        with patch.object(s, "digest_file", return_value="a" * 64) as digest:
            s.bundle_digest()
        self.assertIn("control_producer.py", {call.args[0].name for call in digest.call_args_list})
        self.assertIn("__init__.py", {call.args[0].name for call in digest.call_args_list})

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.profile = copy.deepcopy(s.load_profile("rtmpose-hand5"))
        self.profile.pop("archive")
        model = self.root / "pose.onnx"
        model.write_bytes(b"synthetic-not-a-model")
        self.profile["files"] = {"pose.onnx": hashlib.sha256(model.read_bytes()).hexdigest()}
        distributions = {k: v for k, v in self.profile["runtime"].items() if k != "python"}
        self.receipt = {"schema": "den.specialist-evaluation-receipt/v1", "scope": "offline_evaluation",
                        "profile_digest": profile_digest(self.profile), "adapter_sha256": s.bundle_digest(),
                        "expires_at_utc": 200.0, "files": dict(self.profile["files"]), "archive_member": None,
                        "runtime": {"distributions": distributions,
                                    "wheel_sha256": {k: "a" * 64 for k in distributions},
                                    "record_sha256": {k: "b" * 64 for k in distributions}},
                        "reference": {"passed": True, "report_sha256": "c" * 64,
                                      "io_contract_sha256": profile_digest(self.profile["io"])},
                        "resources": {"qualified": True, "report_sha256": "d" * 64,
                                      "peak_host_bytes": 1024**3, "peak_gpu_bytes": 0, "maximum_seconds": 10}}

    def check(self, receipt=None):
        return s.check_admission(self.profile, self.root, self.receipt if receipt is None else receipt,
                                 now=100.0, runtime_check=False)

    def test_candidate_catalog_truthfully_admits_nothing(self):
        for identifier in s.PROFILE_IDS:
            profile = s.load_profile(identifier)
            self.assertEqual(profile["admission_status"], "not_admitted")
            self.assertIs(profile["production_admitted"], False)
            self.assertIsNone(profile["observed_peak_host_bytes"])
            with self.assertRaises(ContractError):
                s.check_admission(profile, self.root, {}, now=100, runtime_check=False)
        self.assertEqual(s.load_profile("rtmpose-hand5")["io"]["keypoints"], 21)
        self.assertEqual(s.load_profile("rtmw-l-384")["io"]["parts"]["right_hand"], [112, 133])
        self.assertEqual(s.load_profile("yoloe-26m-seg")["backend"], "profile_only")

    def test_complete_synthetic_reviewed_receipt_has_only_evaluation_scope(self):
        self.assertEqual(len(self.check()), 64)
        self.assertEqual(self.receipt["scope"], "offline_evaluation")
        forged = copy.deepcopy(self.receipt)
        forged["scope"] = "production"
        with self.assertRaisesRegex(ContractError, "specialist_not_admitted"):
            self.check(forged)

    def test_missing_reference_peaks_or_runtime_closure_refuses_before_native_load(self):
        for category, name, invalid in (("reference", "passed", False), ("resources", "qualified", False),
                                         ("resources", "peak_host_bytes", None), ("resources", "peak_gpu_bytes", 1),
                                         ("runtime", "wheel_sha256", {})):
            receipt = copy.deepcopy(self.receipt)
            receipt[category][name] = invalid
            with self.subTest(category=category, name=name), self.assertRaises(ContractError):
                self.check(receipt)
        with patch.object(s, "create_backend") as load, self.assertRaises(ContractError):
            s.evaluate(self.profile, self.root, {}, {"samples": []}, io_device="8:0")
        load.assert_not_called()

    def test_artifact_and_adapter_changes_invalidate_reviewed_receipt(self):
        receipt = copy.deepcopy(self.receipt)
        receipt["adapter_sha256"] = "e" * 64
        with self.assertRaises(ContractError):
            self.check(receipt)
        (self.root / "pose.onnx").write_bytes(b"changed")
        with self.assertRaisesRegex(ContractError, "specialist_file_digest_mismatch"):
            self.check()

    def test_duplicate_cv2_runtime_is_refused(self):
        receipt = copy.deepcopy(self.receipt)
        for key, value in (("distributions", "4.11.0.86"), ("wheel_sha256", "a" * 64), ("record_sha256", "b" * 64)):
            receipt["runtime"][key]["opencv-python-headless"] = value
        with self.assertRaisesRegex(ContractError, "duplicate_cv2_distributions"):
            self.check(receipt)

    def test_archive_graph_must_match_materialized_onnx_without_extracting(self):
        self.profile["archive"] = "publisher.zip"
        archive = self.root / "publisher.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("sdk/end2end.onnx", (self.root / "pose.onnx").read_bytes())
        self.profile["files"]["publisher.zip"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.receipt["files"] = dict(self.profile["files"])
        self.receipt["profile_digest"] = profile_digest(self.profile)
        self.receipt["archive_member"] = "sdk/end2end.onnx"
        self.assertEqual(len(self.check()), 64)
        self.receipt["archive_member"] = "other.onnx"
        with self.assertRaisesRegex(ContractError, "specialist_archive_graph_missing"):
            self.check()


class ManifestTests(unittest.TestCase):
    def test_only_hashed_local_ppm_with_explicit_bounded_crops(self):
        with tempfile.TemporaryDirectory() as root:
            blob = store_rgb_blob(root, bytes([10, 20, 30]) * 256, 16, 16)
            manifest = {"schema": "den.specialist-evaluation/v1", "frame_root": root,
                        "samples": [{"id": "synthetic", **blob, "width": 16, "height": 16, "crops": [[0, 0, 16, 16]]}]}
            path = Path(root) / "manifest.json"
            path.write_text(canonical(manifest), encoding="utf-8")
            self.assertEqual(s.load_manifest(path), manifest)
            for box in ([-1, 0, 16, 16], [0, 0, 17, 16], [0, 0, 0, 16], [0, True, 16, 16]):
                manifest["samples"][0]["crops"] = [box]
                path.write_text(canonical(manifest), encoding="utf-8")
                with self.assertRaises(ContractError):
                    s.load_manifest(path)


class FakeSession:
    def __init__(self, joints=21, width=256, height=256):
        self.inputs = [SimpleNamespace(name="input", shape=[1, 3, height, width], type="tensor(float)")]
        self.outputs = [SimpleNamespace(name="simcc_x", shape=[1, joints, width * 2], type="tensor(float)"),
                        SimpleNamespace(name="simcc_y", shape=[1, joints, height * 2], type="tensor(float)")]
        self.arrays = [np.zeros(item.shape, dtype=np.float32) for item in self.outputs]
        self.providers = ["CPUExecutionProvider"]
        self.arrays[0][:, :, 4], self.arrays[1][:, :, 6] = 0.8, 0.7
        self.calls = []

    def get_inputs(self): return self.inputs
    def get_outputs(self): return self.outputs
    def get_providers(self): return self.providers
    def run(self, names, inputs):
        self.calls.append((names, inputs))
        return self.arrays


class FakePoseTool:
    def preprocess(self, bgr, box):
        self.bgr, self.box = bgr.copy(), list(box)
        return np.ones((self.model_input_size[1], self.model_input_size[0], 3)), np.array([20, 30]), np.array([40, 50])

    def postprocess(self, outputs, center, scale):
        # A synthetic SimCC decode in the reference tool's original-image convention.
        points = np.stack([out.argmax(axis=-1) for out in outputs], axis=-1).astype(float) / 2
        points = points / np.array(self.model_input_size) * scale + center - scale / 2
        return points, np.minimum(outputs[0].max(axis=-1), outputs[1].max(axis=-1))


class BackendMappingTests(unittest.TestCase):
    def test_rtmpose_session_tensor_channels_decode_and_original_pixel_coordinates(self):
        session, tool = FakeSession(), FakePoseTool()
        backend = s.RtmPoseBackend(s.load_profile("rtmpose-hand5"), ".", session=session, tool=tool)
        image = np.full((40, 50, 3), [10, 20, 30], dtype=np.uint8)
        result = backend.predict(image, [[3, 4, 23, 34]])[0]
        self.assertEqual(tool.bgr[0, 0].tolist(), [30, 20, 10])
        self.assertEqual(tool.box, [3, 4, 23, 34])
        self.assertEqual(session.calls[0][0], ["simcc_x", "simcc_y"])
        tensor = session.calls[0][1]["input"]
        self.assertEqual((tensor.shape, tensor.dtype), ((1, 3, 256, 256), np.dtype("float32")))
        self.assertEqual(len(result["keypoints_xy"]), 21)
        self.assertAlmostEqual(result["keypoints_xy"][0][0], 0.3125)
        self.assertAlmostEqual(result["keypoints_xy"][0][1], 5.5859375)
        self.assertAlmostEqual(result["scores"][0], 0.7, places=6)
        self.assertEqual(result["coordinate_space"], "original_image_pixels")

    def test_rtmw133_graph_and_wrong_provider_or_shape_fail_closed(self):
        profile = s.load_profile("rtmw-l-384")
        session = FakeSession(133, 288, 384)
        backend = s.RtmPoseBackend(profile, ".", session=session, tool=FakePoseTool())
        self.assertEqual(len(backend.predict(np.zeros((20, 20, 3), np.uint8), [[0, 0, 20, 20]])[0]["scores"]), 133)
        for mutation in ("provider", "graph", "output"):
            session = FakeSession()
            if mutation == "provider": session.providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if mutation == "graph": session.inputs[0].shape[-1] = 192
            if mutation == "output": session.outputs[0].shape[1] = 7
            with self.assertRaisesRegex(ContractError, "graph_contract_mismatch"):
                s.RtmPoseBackend(s.load_profile("rtmpose-hand5"), ".", session=session, tool=FakePoseTool())

    def test_nonfinite_native_pose_is_not_fabricated_into_a_valid_observation(self):
        session = FakeSession()
        session.arrays[0][0, 0, 0] = np.nan
        backend = s.RtmPoseBackend(s.load_profile("rtmpose-hand5"), ".", session=session, tool=FakePoseTool())
        with self.assertRaises(ContractError):
            backend.predict(np.zeros((20, 20, 3), np.uint8), [[0, 0, 20, 20]])

    def test_yunet_and_sface_alignment_preserve_crop_offsets_without_identity_claim(self):
        class Detector:
            def setInputSize(self, size): self.size = size
            def detect(self, image):
                self.image = image.copy()
                return None, np.array([[1, 2, 2, 2, 1, 1, 2, 1, 1.5, 2, 1, 3, 2, 3, 0.9]], np.float32)
        class Recognizer:
            def alignCrop(self, image, face): self.face = face; return image
            def feature(self, image): return np.ones((1, 128), np.float32)
        detector, recognizer = Detector(), Recognizer()
        backend = s.FaceIdentityBackend(s.load_profile("yunet-sface-cpu"), ".", detector=detector, recognizer=recognizer)
        face = backend.predict(np.full((6, 7, 3), [10, 20, 30], np.uint8), [[2, 1, 6, 5]])[0]["faces"][0]
        self.assertEqual(detector.size, (4, 4))
        self.assertEqual(detector.image[0, 0].tolist(), [30, 20, 10])
        self.assertEqual(face["box_xyxy"], [3, 3, 5, 5])
        self.assertEqual(face["landmarks_xy"][0], [3, 2])
        self.assertEqual(len(face["embedding"]), 128)
        self.assertIsNone(face["identity"])

    def test_face_tasks_maps_xy_but_preserves_nonmetric_z_and_has_no_gaze_claim(self):
        class Task:
            def detect(self, image):
                self.image = image.copy()
                return SimpleNamespace(face_landmarks=[[SimpleNamespace(x=.25, y=.5, z=-.1)] * 478],
                                       face_blendshapes=[[SimpleNamespace(category_name="jawOpen", score=.2)]])
            def close(self): self.closed = True
        task = Task()
        backend = s.FaceLandmarkerBackend(s.load_profile("mediapipe-face-landmarker-cpu"), ".", task=task,
                                          image_factory=lambda array: array)
        result = backend.predict(np.full((6, 7, 3), [10, 20, 30], np.uint8), [[2, 1, 6, 5]])[0]
        self.assertEqual(task.image[0, 0].tolist(), [10, 20, 30])
        self.assertEqual(result["faces"][0]["landmarks_xy"][0], [3, 3])
        self.assertEqual(result["faces"][0]["z_relative"][0], -.1)
        self.assertEqual(result["eye_gaze"], "not_measured")
        backend.close()
        self.assertTrue(task.closed)


class CompletionProofTests(unittest.TestCase):
    def test_failed_start_without_pid_is_not_native_completion_proof(self):
        calls = []
        class Pipe:
            def close(self): pass
        class Process:
            pid, exitcode = None, None
            def start(self): raise OSError("synthetic start failure")
        class Context:
            def Pipe(self, duplex): return Pipe(), Pipe()
            def Process(self, **kwargs): return Process()
        class Lease:
            def try_acquire(self, request): return "opaque"
            def release(self, token, proof): calls.append("released"); return True
        with patch.object(cli, "check_admission"), patch.object(cli, "cgroup_limits"), self.assertRaises(OSError):
            cli.run_bounded(s.load_profile("rtmpose-hand5"), ".",
                            {"resources": {"peak_host_bytes": 1024**3, "maximum_seconds": 10}}, {},
                            lease=Lease(), io_device="8:0",
                            control=SimpleNamespace(read=lambda now: {"qwen_busy": False}),
                            host_probe=SimpleNamespace(available_bytes=lambda: 8 * 1024**3), context=Context())
        self.assertNotIn("released", calls)

    def test_shared_execution_lease_releases_only_after_child_completion(self):
        events = []
        class Pipe:
            def poll(self, seconds): return True
            def recv_bytes(self, maximum): return canonical({"ok": True, "result": {"synthetic": True}}).encode()
            def close(self): pass
        class Process:
            pid, exitcode, alive = None, None, False
            def start(self): self.pid, self.alive = 123, True; events.append("native_started")
            def is_alive(self): return self.alive
            def join(self, seconds=None): self.alive, self.exitcode = False, 0; events.append("reaped")
        class Context:
            def Pipe(self, duplex): return Pipe(), Pipe()
            def Process(self, **kwargs): return Process()
        class Lease:
            def try_acquire(self, request): events.append("acquired"); return "opaque"
            def release(self, token, proof): events.append(proof); return True
        profile = s.load_profile("rtmpose-hand5")
        receipt = {"resources": {"peak_host_bytes": 1024**3, "maximum_seconds": 10}}
        with patch.object(cli, "check_admission"), patch.object(cli, "cgroup_limits"):
            result = cli.run_bounded(profile, ".", receipt, {"synthetic": True}, lease=Lease(), io_device="8:0",
                                     control=SimpleNamespace(read=lambda now: {"qwen_busy": False}),
                                     host_probe=SimpleNamespace(available_bytes=lambda: 8 * 1024**3), context=Context())
        self.assertEqual(result, {"synthetic": True})
        self.assertEqual(events[0], "acquired")
        self.assertEqual(events[-1], "process_reaped")
        self.assertLess(events.index("reaped"), events.index("process_reaped"))

    def test_unreapable_cancel_keeps_execution_lease(self):
        calls = []
        class Pipe:
            def close(self): pass
        class Process:
            pid, exitcode = None, None
            def start(self): self.pid = 123
            def is_alive(self): return True
            def terminate(self): calls.append("terminate")
            def kill(self): calls.append("kill")
            def join(self, seconds): pass
        class Context:
            def Pipe(self, duplex): return Pipe(), Pipe()
            def Process(self, **kwargs): return Process()
        class Lease:
            def try_acquire(self, request): return "opaque"
            def release(self, token, proof): calls.append("released"); return True
        def control(now):
            calls.append("guard")
            return {"qwen_busy": calls.count("guard") > 1}
        with patch.object(cli, "check_admission"), patch.object(cli, "cgroup_limits"), self.assertRaises(ContractError):
            cli.run_bounded(s.load_profile("rtmpose-hand5"), ".",
                            {"resources": {"peak_host_bytes": 1024**3, "maximum_seconds": 10}}, {},
                            lease=Lease(), io_device="8:0", control=SimpleNamespace(read=control),
                            host_probe=SimpleNamespace(available_bytes=lambda: 8 * 1024**3), context=Context())
        self.assertIn("kill", calls)
        self.assertNotIn("released", calls)

    def test_a_timeout_is_not_completion_until_native_process_is_reaped(self):
        class Process:
            exitcode = None
            alive = True
            def is_alive(self): return self.alive
            def terminate(self): pass
            def kill(self): self.alive, self.exitcode = False, -9
            def join(self, seconds): pass
        self.assertTrue(cli.reap(Process()))
        class Unreapable(Process):
            def kill(self): pass
        self.assertFalse(cli.reap(Unreapable()))


if __name__ == "__main__":
    unittest.main()
