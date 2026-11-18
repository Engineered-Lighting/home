"""Offline CPU specialist candidates and real lazy backends; no provisioning.

Outputs are local evaluation observations in image coordinates. They are not
identity decisions, metric room geometry, calibrated probabilities or lighting
authority. Shipped candidates admit no native work without a separately reviewed
artifact/runtime/reference/resource receipt. No URL is passed to a loader.
"""
import hashlib
import importlib.metadata
import math
from pathlib import Path, PurePosixPath
import re
import sys
import time
import zipfile

from .artifacts import cgroup_limits, digest_file, installed_runtime
from .contracts import ContractError, HEX64, canonical, profile_digest, read_ppm, strict_json


PROFILE_ROOT = Path(__file__).resolve().parents[1] / "profiles" / "specialists"
PROFILE_IDS = ("rtmw-l-384", "rtmpose-hand5", "yunet-sface-cpu", "mediapipe-face-landmarker-cpu", "yoloe-26m-seg")
MAX_RESULT_BYTES = 4 * 1024**2


def load_profile(profile_id):
    if profile_id not in PROFILE_IDS:
        raise ContractError("unsupported_specialist")
    return strict_json((PROFILE_ROOT / (profile_id + ".json")).read_text(encoding="utf-8"))


def bundle_digest():
    root = Path(__file__).resolve().parent
    files = ("__init__.py", "specialists.py", "specialist_cli.py", "contracts.py", "resources.py",
             "artifacts.py", "control_producer.py")
    return hashlib.sha256(canonical({name: digest_file(root / name) for name in files}).encode()).hexdigest()


def _hex(value):
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _local_file(root, name):
    if not isinstance(name, str):
        raise ContractError("invalid_specialist_local_path")
    pure = PurePosixPath(name)
    if (pure.is_absolute() or ".." in pure.parts or "\\" in name
            or not pure.parts or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in pure.parts)):
        raise ContractError("invalid_specialist_local_path")
    root = Path(root).absolute()
    path = root.joinpath(*pure.parts)
    if root.is_symlink() or any(p.is_symlink() for p in (path, *path.parents) if p == root or root in p.parents):
        raise ContractError("specialist_symlink_refused")
    if not path.is_file():
        raise ContractError("specialist_artifact_missing")
    return path


def _read_json(path, maximum=128 * 1024):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise ContractError("specialist_manifest_missing_or_large")
    return strict_json(path.read_text(encoding="utf-8"))


def _check_archive(root, profile, receipt):
    if not profile.get("archive"):
        return
    member = receipt.get("archive_member")
    if not isinstance(member, str) or PurePosixPath(member).suffix != ".onnx":
        raise ContractError("specialist_archive_graph_unreviewed")
    archive = _local_file(root, profile["archive"])
    try:
        with zipfile.ZipFile(archive) as source:
            files = source.infolist()
            if len(files) > 32 or sum(f.file_size for f in files) > 1024**3:
                raise ContractError("specialist_archive_unbounded")
            for item in files:
                name = PurePosixPath(item.filename)
                if (name.is_absolute() or ".." in name.parts or "\\" in item.filename
                        or (item.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ContractError("specialist_archive_unsafe_member")
            if len([item for item in files if item.filename == member]) != 1:
                raise ContractError("specialist_archive_graph_missing")
            digest = hashlib.sha256()
            with source.open(member) as model:
                for block in iter(lambda: model.read(8 << 20), b""):
                    digest.update(block)
            if digest.hexdigest() != receipt["files"][profile["model_file"]]:
                raise ContractError("specialist_archive_graph_mismatch")
    except (OSError, zipfile.BadZipFile, RuntimeError):
        raise ContractError("specialist_archive_unreadable") from None


def check_admission(profile, root, receipt, *, now=None, runtime_check=True):
    """Permit offline evaluation only, never production admission.

    Unknown publisher file hashes may be supplied only by this trusted local
    reviewed receipt. An archive hash, graph extraction and reference-parity
    report are required for the uninspected MMPose SDK archives.
    """
    if profile.get("backend") == "profile_only":
        raise ContractError("specialist_backend_not_qualified")
    expected = {"schema", "scope", "profile_digest", "adapter_sha256", "expires_at_utc", "files", "runtime",
                "reference", "resources", "archive_member"}
    if (not isinstance(receipt, dict) or set(receipt) != expected
            or receipt["schema"] != "den.specialist-evaluation-receipt/v1" or receipt["scope"] != "offline_evaluation"
            or receipt["profile_digest"] != profile_digest(profile) or receipt["adapter_sha256"] != bundle_digest()
            or not _finite(receipt["expires_at_utc"])
            or receipt["expires_at_utc"] <= (time.time() if now is None else now)):
        raise ContractError("specialist_not_admitted")
    reference, resource = receipt["reference"], receipt["resources"]
    if (not isinstance(reference, dict) or set(reference) != {"passed", "report_sha256", "io_contract_sha256"}
            or reference["passed"] is not True or not _hex(reference["report_sha256"])
            or reference["io_contract_sha256"] != profile_digest(profile["io"])):
        raise ContractError("specialist_preprocessing_reference_unchecked")
    if (not isinstance(resource, dict)
            or set(resource) != {"qualified", "report_sha256", "peak_host_bytes", "peak_gpu_bytes", "maximum_seconds"}
            or resource["qualified"] is not True or not _hex(resource["report_sha256"])
            or type(resource["peak_host_bytes"]) is not int or resource["peak_host_bytes"] <= 0
            or resource["peak_host_bytes"] > profile["execution"]["memory_max_bytes"]
            or resource["peak_gpu_bytes"] != 0 or type(resource["peak_gpu_bytes"]) is not int
            or not _finite(resource["maximum_seconds"]) or not 0 < resource["maximum_seconds"] <= 60):
        raise ContractError("specialist_resource_peaks_unqualified")
    files = receipt["files"]
    if not isinstance(files, dict) or set(files) != set(profile["files"]) or not all(map(_hex, files.values())):
        raise ContractError("specialist_file_closure_unpinned")
    for name, digest in files.items():
        if profile["files"][name] is not None and profile["files"][name] != digest:
            raise ContractError("specialist_publisher_pin_mismatch")
        if digest_file(_local_file(root, name)) != digest:
            raise ContractError("specialist_file_digest_mismatch")
    _check_archive(root, profile, receipt)
    runtime = receipt["runtime"]
    expected_runtime = {"distributions", "wheel_sha256", "record_sha256"}
    if (not isinstance(runtime, dict) or set(runtime) != expected_runtime
            or not all(isinstance(runtime[k], dict) for k in expected_runtime)
            or not runtime["distributions"] or len(runtime["distributions"]) > 256
            or set(runtime["distributions"]) != set(runtime["wheel_sha256"])
            or set(runtime["distributions"]) != set(runtime["record_sha256"])
            or not all(_hex(v) for k in ("wheel_sha256", "record_sha256") for v in runtime[k].values())):
        raise ContractError("specialist_runtime_closure_unpinned")
    cv_distributions = {name for name in runtime["distributions"] if name.startswith("opencv-")}
    if len(cv_distributions) > 1:
        raise ContractError("duplicate_cv2_distributions")
    if any(not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
           or not isinstance(version, str) or not 0 < len(version) <= 128
           for name, version in runtime["distributions"].items()):
        raise ContractError("invalid_specialist_runtime_pin")
    for name, digest in profile.get("known_linux_wheel_sha256", {}).items():
        if runtime["wheel_sha256"].get(name) != digest:
            raise ContractError("specialist_known_wheel_pin_mismatch")
    for name, version in profile["runtime"].items():
        if name != "python" and runtime["distributions"].get(name) != version:
            raise ContractError("specialist_runtime_direct_pin_mismatch")
    if runtime_check:
        if sys.version_info[:2] != (3, 12) or sys.prefix == sys.base_prefix:
            raise ContractError("isolated_python312_environment_required")
        versions, records = installed_runtime()
        if versions != runtime["distributions"] or records != runtime["record_sha256"]:
            raise ContractError("specialist_installed_runtime_mismatch")
    return hashlib.sha256(canonical(receipt).encode()).hexdigest()


def load_manifest(path):
    return validate_manifest(_read_json(path))


def _sample_rgb(root, sample):
    path = _local_file(root, sample["blob_ref"])
    rgb = read_ppm(path, sample["width"], sample["height"])
    header = f"P6\n{sample['width']} {sample['height']}\n255\n".encode("ascii")
    # Bind the actual bytes consumed, not a separate earlier read of the path.
    if hashlib.sha256(header + rgb).hexdigest() != sample["sha256"]:
        raise ContractError("specialist_frame_digest_mismatch")
    return rgb


def validate_manifest(manifest):
    if (not isinstance(manifest, dict) or set(manifest) != {"schema", "frame_root", "samples"}
            or manifest["schema"] != "den.specialist-evaluation/v1"
            or not isinstance(manifest["frame_root"], str) or not Path(manifest["frame_root"]).is_absolute()
            or not isinstance(manifest["samples"], list) or not 1 <= len(manifest["samples"]) <= 32):
        raise ContractError("invalid_specialist_evaluation_manifest")
    seen = set()
    for sample in manifest["samples"]:
        if (not isinstance(sample, dict) or set(sample) != {"id", "blob_ref", "sha256", "width", "height", "crops"}
                or not isinstance(sample["id"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", sample["id"])
                or sample["id"] in seen or not _hex(sample["sha256"])
                or sample["blob_ref"] != sample["sha256"] + ".ppm"
                or any(type(sample[k]) is not int or not 1 <= sample[k] <= 4096 for k in ("width", "height"))
                or sample["width"] * sample["height"] > 4096 * 2160
                or not isinstance(sample["crops"], list) or not 1 <= len(sample["crops"]) <= 4):
            raise ContractError("invalid_specialist_sample")
        seen.add(sample["id"])
        for box in sample["crops"]:
            if (not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box)
                    or not 0 <= box[0] < box[2] <= sample["width"]
                    or not 0 <= box[1] < box[3] <= sample["height"]):
                raise ContractError("invalid_specialist_crop")
        item = _local_file(manifest["frame_root"], sample["blob_ref"])
        if item.stat().st_size > sample["width"] * sample["height"] * 3 + 64:
            raise ContractError("specialist_frame_digest_mismatch")
        _sample_rgb(manifest["frame_root"], sample)
    return manifest


class RtmPoseBackend:
    """One CPU ORT session, pinned RTMLib affine/normalization/SimCC decode.

    Avoid BaseTool.__init__: it has a downloader and uncontrolled session
    construction. Only its pinned pure preprocessing/decoding methods are used.
    Passing model bytes to ORT refuses implicit external-data filesystem loads.
    """
    def __init__(self, profile, root, *, session=None, tool=None):
        import numpy as np
        self.np, self.profile = np, profile
        if session is None:
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.intra_op_num_threads = profile["execution"]["cpu_threads"]
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            session = ort.InferenceSession(_local_file(root, profile["model_file"]).read_bytes(), sess_options=options,
                                           providers=["CPUExecutionProvider"])
        self.session = session
        width, height = profile["io"]["input_size_wh"]
        joints = profile["io"]["keypoints"]
        inputs, outputs = session.get_inputs(), session.get_outputs()
        if (session.get_providers() != ["CPUExecutionProvider"] or len(inputs) != 1 or len(outputs) != 2
                or inputs[0].type != "tensor(float)" or list(inputs[0].shape) != [1, 3, height, width]
                or [list(o.shape) for o in outputs] != [[1, joints, width * 2], [1, joints, height * 2]]
                or any(o.type != "tensor(float)" for o in outputs)):
            raise ContractError("specialist_onnx_graph_contract_mismatch")
        if tool is None:
            from rtmlib import RTMPose
            tool = RTMPose.__new__(RTMPose)
        self.tool = tool
        tool.model_input_size = tuple(profile["io"]["input_size_wh"])
        tool.mean, tool.std = tuple(profile["io"]["mean"]), tuple(profile["io"]["std"])
        self.input_name, self.output_names = inputs[0].name, [o.name for o in outputs]

    def predict(self, rgb, crops):
        np, joints = self.np, self.profile["io"]["keypoints"]
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        results = []
        for box in crops:
            image, center, scale = self.tool.preprocess(bgr, box)
            tensor = np.ascontiguousarray(image.transpose(2, 0, 1)[None], dtype=np.float32)
            outputs = self.session.run(self.output_names, {self.input_name: tensor})
            expected = self.profile["io"]["input_size_wh"]
            if (len(outputs) != 2 or any(not np.isfinite(out).all() for out in outputs)
                    or [list(out.shape) for out in outputs] != [[1, joints, expected[0] * 2], [1, joints, expected[1] * 2]]):
                raise ContractError("specialist_onnx_output_contract_mismatch")
            points, scores = self.tool.postprocess(outputs, center, scale)
            if list(points.shape) != [1, joints, 2] or list(scores.shape) != [1, joints]:
                raise ContractError("specialist_pose_decode_mismatch")
            if not np.isfinite(points).all() or not np.isfinite(scores).all():
                raise ContractError("specialist_nonfinite_pose")
            results.append({"crop_xyxy": list(box), "keypoints_xy": points[0].tolist(), "scores": scores[0].tolist(),
                            "coordinate_space": "original_image_pixels", "score_semantics": "raw_simcc_peak_uncalibrated"})
        return results

    def close(self):
        self.session = self.tool = None


class FaceIdentityBackend:
    """YuNet detection plus SFace alignment/features on CPU, without a gallery."""
    def __init__(self, profile, root, *, detector=None, recognizer=None):
        import numpy as np
        self.np = np
        if detector is None or recognizer is None:
            import cv2
            cv2.setNumThreads(profile["execution"]["cpu_threads"])
            detector = cv2.FaceDetectorYN.create(str(_local_file(root, "yunet.onnx")), "", (320, 320), 0.6, 0.3, 32,
                                                 cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU)
            recognizer = cv2.FaceRecognizerSF.create(str(_local_file(root, "sface.onnx")), "",
                                                     cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU)
        self.detector, self.recognizer = detector, recognizer

    def predict(self, rgb, crops):
        np, results = self.np, []
        for box in crops:
            x0, y0, x1, y1 = box
            image = np.ascontiguousarray(rgb[y0:y1, x0:x1, ::-1])
            self.detector.setInputSize((x1 - x0, y1 - y0))
            _, faces = self.detector.detect(image)
            detected = []
            if faces is not None:
                if len(faces.shape) != 2 or faces.shape[1] != 15 or faces.shape[0] > 32 or not np.isfinite(faces).all():
                    raise ContractError("specialist_face_detection_contract_mismatch")
                for face in sorted(faces, key=lambda row: -float(row[14]))[:8]:
                    aligned = self.recognizer.alignCrop(image, face)
                    feature = self.recognizer.feature(aligned)
                    if feature.size != 128 or not np.isfinite(feature).all():
                        raise ContractError("specialist_face_feature_contract_mismatch")
                    detected.append({"box_xyxy": [float(face[0]) + x0, float(face[1]) + y0,
                                                   float(face[0] + face[2]) + x0, float(face[1] + face[3]) + y0],
                                     "landmarks_xy": [[float(face[i]) + x0, float(face[i + 1]) + y0] for i in range(4, 14, 2)],
                                     "score": float(face[14]), "embedding": feature.reshape(-1).tolist(),
                                     "identity": None})
            results.append({"crop_xyxy": list(box), "faces": detected, "coordinate_space": "original_image_pixels",
                            "score_semantics": "detector_score_uncalibrated", "identity_decision": "not_performed"})
        return results

    def close(self):
        self.detector = self.recognizer = None


class FaceLandmarkerBackend:
    """MediaPipe Tasks IMAGE mode, CPU delegate; no legacy solutions API."""
    def __init__(self, profile, root, *, task=None, image_factory=None):
        import numpy as np
        self.np = np
        if task is None:
            import mediapipe as mp
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision
            options = vision.FaceLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=str(_local_file(root, "face_landmarker.task")),
                                                delegate=python.BaseOptions.Delegate.CPU),
                running_mode=vision.RunningMode.IMAGE, num_faces=1, output_face_blendshapes=True,
                output_facial_transformation_matrixes=False)
            task = vision.FaceLandmarker.create_from_options(options)
            image_factory = lambda array: mp.Image(image_format=mp.ImageFormat.SRGB, data=array)
        self.task, self.image_factory = task, image_factory

    def predict(self, rgb, crops):
        results = []
        for box in crops:
            x0, y0, x1, y1 = box
            result = self.task.detect(self.image_factory(self.np.ascontiguousarray(rgb[y0:y1, x0:x1])))
            if len(result.face_landmarks) > 1 or len(result.face_blendshapes) > 1:
                raise ContractError("specialist_landmarker_face_count_mismatch")
            faces = []
            for index, landmarks in enumerate(result.face_landmarks):
                if len(landmarks) != 478 or any(not _finite(v) for p in landmarks for v in (p.x, p.y, p.z)):
                    raise ContractError("specialist_landmarker_contract_mismatch")
                blendshapes = result.face_blendshapes[index] if index < len(result.face_blendshapes) else []
                if len(blendshapes) > 52 or any(not _finite(c.score) or not isinstance(c.category_name, str)
                                              or len(c.category_name) > 64 for c in blendshapes):
                    raise ContractError("specialist_blendshape_contract_mismatch")
                faces.append({"landmarks_xy": [[x0 + p.x * (x1 - x0), y0 + p.y * (y1 - y0)] for p in landmarks],
                              "z_relative": [p.z for p in landmarks],
                              "blendshapes": [{"name": c.category_name, "score": c.score} for c in blendshapes]})
            results.append({"crop_xyxy": list(box), "faces": faces, "coordinate_space": "original_image_pixels",
                            "z_semantics": "mediapipe_relative_not_room_metres", "eye_gaze": "not_measured"})
        return results

    def close(self):
        self.task.close()


def create_backend(profile, root):
    backends = {"rtmlib_ort_cpu": RtmPoseBackend, "opencv_yunet_sface_cpu": FaceIdentityBackend,
                "mediapipe_tasks_face_cpu": FaceLandmarkerBackend}
    if profile["backend"] not in backends:
        raise ContractError("specialist_backend_not_implemented")
    return backends[profile["backend"]](profile, root)


def evaluate(profile, root, receipt, manifest, *, io_device, backend_factory=None):
    """The isolated child's synchronous path; caller owns process deadline/reap."""
    digest = check_admission(profile, root, receipt)
    validate_manifest(manifest)
    cgroup_limits(profile, io_device)
    import numpy as np
    backend = (backend_factory or create_backend)(profile, root)
    started, results = time.monotonic(), []
    try:
        for sample in manifest["samples"]:
            if time.monotonic() - started >= receipt["resources"]["maximum_seconds"]:
                raise ContractError("specialist_evaluation_deadline")
            data = _sample_rgb(manifest["frame_root"], sample)
            image = np.frombuffer(data, dtype=np.uint8).reshape(sample["height"], sample["width"], 3)
            results.append({"sample_id": sample["id"], "frame_sha256": sample["sha256"],
                            "observations": backend.predict(image, sample["crops"])})
        response = {"schema": "den.specialist-evaluation-result/v1", "profile_id": profile["id"],
                    "profile_digest": profile_digest(profile), "receipt_digest": digest,
                    "scope": "offline_evaluation", "production_admitted": False, "metric_geometry": False,
                    "provider": "cpu", "elapsed_seconds": time.monotonic() - started, "results": results}
        if len(canonical(response).encode()) > MAX_RESULT_BYTES:
            raise ContractError("specialist_result_too_large")
        return response
    finally:
        backend.close()
