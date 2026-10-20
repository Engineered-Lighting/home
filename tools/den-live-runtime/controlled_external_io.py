"""Fixed authored camera/SDK ports for the local controlled owner session.

Only external observations are authored here. Capture, model normalization,
spatial estimation, lifecycle, journal admission and the worker remain ordinary
production implementations. No camera, HA, model artifact or hardware is opened.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT / "stack/services/home-context", ROOT / "stack/services/perception-lab",
                  ROOT / "tools/shared-home", ROOT / "tools/la-detector-trial"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

SCENARIOS = ("stationary_work", "presence", "gap", "missing_protection", "empty", "source_loss")
CLARIFICATION_SCENARIOS = SCENARIOS + ("material_uncertain",)
MANUAL_SCENARIOS = CLARIFICATION_SCENARIOS + ("perception_unavailable",)
SOURCE, CAMERA, REVISION = "controlled-paired-source", "controlled-oak", "controlled-r1"
BOOT_ID = "00000000-0000-0000-0000-000000000031"
CAMERA_EPOCH = "controlled-authored-camera-epoch"
KD = [[450, 0, 320], [0, 450, 200], [0, 0, 1]]
KRGB = [[500, 0, 320], [0, 500, 180], [0, 0, 1]]
ROOM_FROM_LEFT = [[1, 0, 0, 1], [0, 0, 1, 2], [0, -1, 0, 1.5], [0, 0, 0, 1]]
JOINTS = [[320, 70], [310, 65], [330, 65], [300, 70], [340, 70], [280, 110], [360, 110],
          [308, 200], [332, 200], [300, 200], [340, 200], [290, 220], [350, 220],
          [290, 270], [350, 270], [290, 320], [350, 320]]
BODY_IDS = ["torso", "upper_arm_left", "forearm_left", "upper_arm_right", "forearm_right",
            "thigh_left", "shin_left", "thigh_right", "shin_right"]


def process_rss_bytes():
    """Read actual resident memory on the workstation; never invent a budget."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                *[(name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel, psapi = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return None
        return counters.WorkingSetSize
    from controller_load import _rss_bytes
    return _rss_bytes()


def pin(name):
    return {"id": name, "digest": hashlib.sha256(name.encode()).hexdigest()}


class _Tensor:
    def __init__(self, value): self.value = value
    def cpu(self): return self
    def tolist(self): return deepcopy(self.value)


class ControlledExternalIO:
    def __init__(self, *, wall_clock=time.time, monotonic=time.monotonic, clarification=False, manual=False):
        if type(clarification) is not bool or type(manual) is not bool or manual and not clarification:
            raise ValueError("explicit_controlled_profile_required")
        self.scenarios = MANUAL_SCENARIOS if manual else CLARIFICATION_SCENARIOS if clarification else SCENARIOS
        self.wall_clock, self.monotonic = wall_clock, monotonic
        self.anchor_wall = wall_clock()
        self.lock = threading.RLock()
        self.scenario = self.active_scenario = "stationary_work"
        self.sequence = 0

    def set_scenario(self, scenario):
        if not isinstance(scenario, str) or scenario not in self.scenarios:
            raise ValueError("scenario_refused")
        with self.lock:
            self.scenario = scenario
        return self.status()

    def status(self):
        with self.lock:
            return {"source_mode": "controlled_external_io", "scenario": self.scenario,
                    "scenarios": list(self.scenarios), "captures": self.sequence}

    @staticmethod
    def calibration():
        return {"session": CAMERA_EPOCH, "depth_alignment": "rectified_left", "depth_units": "millimetres",
                "stereo_size": [640, 400], "eeprom": {"controlled_device": "authored-only"}}

    @staticmethod
    def depth_plane():
        plane = np.full((400, 640), 1234, np.uint16)
        for index, (u, v) in enumerate(JOINTS):
            depth = 1234 if index in (9, 10) else 2000 if index in (7, 8) else 2500
            x, y = round(320 + .9 * (u - 320)), round(200 + .9 * (v - 180))
            plane[y-5:y+6, x-5:x+6] = depth
        return plane

    def camera_pair(self, _token, *, timeout):
        with self.lock:
            if self.scenario == "source_loss":
                raise OSError("controlled_source_unavailable")
            self.sequence += 1
            self.active_scenario = self.scenario
            sequence = self.sequence
        # Both clocks advance with the actual process. The fixed authored
        # exposure delay is part of each immutable external observation.
        now, mono = self.wall_clock(), self.monotonic()
        now_ns, receive = round(now * 1e9), round(mono * 1e9)
        device = 50_200_000_000 + round((now - self.anchor_wall) * 1e9) - 198_000_000
        encoded = io.BytesIO()
        Image.new("RGB", (640, 360), (sequence * 7 % 250, 100, 60)).save(encoded, format="JPEG", quality=85)
        jpeg = encoded.getvalue()
        nonce = format(sequence, "032x")
        header = {"schema": 3, "session": CAMERA_EPOCH, "sequence": sequence, "timing_generation": 0,
            "width": 640, "height": 360, "quality": 85,
            "observed_monotonic_seconds": (receive - 10_000_000) / 1e9,
            "observed_at_unix_ns": now_ns - 198_000_000, "uncertainty_seconds": .022001,
            "validation_id": "causal:" + nonce, "exposure_duration_seconds": .01,
            "source_clock": "depthai_device_monotonic", "monotonic_clock_scope": "python_monotonic_on_camera_host",
            "timestamp_semantics": "causal_exact_frame_exposure_end", "challenge_nonce": nonce,
            "challenge_device_ns": device - 200_000_000, "frame_device_ns": device,
            "host_send_monotonic_ns": receive - 20_000_000, "host_receive_monotonic_ns": receive,
            "timestamp_quantization_ns": 1000, "preview_sha256": pin("controlled-preview")['digest'],
            "jpeg_sha256": hashlib.sha256(jpeg).hexdigest(), "host_boot_id": BOOT_ID,
            "host_clock_validation_id": "controlled-host-clock-validation", "host_clock_uncertainty_seconds": .002}
        common = {"host_monotonic_timestamp_seconds": (receive - 20_000_000) / 1e9, "transformation_valid": True}
        frames = {name: {**common, "sequence": sequence + 40, "device_timestamp_seconds": (device - 10_000_000) / 1e9,
                         "width": 640, "height": 400, "intrinsics": deepcopy(KD)} for name in ("depth", "left", "right")}
        frames["rgb"] = {**common, "host_monotonic_timestamp_seconds": (receive - 10_000_000) / 1e9,
            "sequence": sequence, "device_timestamp_seconds": device / 1e9,
            "width": 640, "height": 360, "intrinsics": deepcopy(KRGB), "source_distortion_coefficients": [0] * 14}
        metadata = {"schema": 1, "session": CAMERA_EPOCH, "depth_alignment": "rectified_left", "depth_units": "millimetres",
            "invalid_depth": 0, "captured_unix_ns": now_ns,
            "captured_unix_ns_semantics": "bundle_packaging_time_not_sensor_exposure", "frames": frames}
        bundle = io.BytesIO()
        np.savez_compressed(bundle, depth_mm=self.depth_plane(), left=np.zeros((400, 640), np.uint8),
            right=np.zeros((400, 640), np.uint8), rgb_nv12=np.zeros((540, 640), np.uint8),
            metadata_json=np.frombuffer(json.dumps(metadata).encode(), np.uint8))
        return json.dumps({"schema": "home-camera-pair-1", "version": 1,
            "jpeg_b64": base64.b64encode(jpeg).decode(),
            "header": base64.urlsafe_b64encode(json.dumps(header).encode()).decode(),
            "bundle_b64": base64.b64encode(bundle.getvalue()).decode(), "calibration": self.calibration()}).encode()

    def detector_result(self):
        with self.lock:
            mode = self.active_scenario
        boxes = [] if mode == "empty" else [[220, 40, 420, 350]]
        materials = [[342, 190, 382, 210] if mode == "material_uncertain" else [300, 190, 340, 210]] if mode not in {"presence","perception_unavailable"} and boxes else []
        return [SimpleNamespace(orig_shape=(360, 640), names={0: "person", 1: "book"},
            boxes=SimpleNamespace(xyxy=_Tensor(boxes + materials), conf=_Tensor([.9] * (len(boxes) + len(materials))),
                                  cls=_Tensor([0.] * len(boxes) + [1.] * len(materials))), speed={})]

    def pose_result(self, _image, bboxes):
        with self.lock:
            mode = self.active_scenario
        scores = ([0.] if mode == "perception_unavailable" else [.9]) * 17
        for index in ([0, 1, 2, 3, 4] if mode == "missing_protection" else [5, 6, 11, 12] if mode == "gap" else []):
            scores[index] = 0
        return [deepcopy(JOINTS) for _ in bboxes], [list(scores) for _ in bboxes]

    def model_workers(self):
        from rtmpose_worker import RTMPoseWorker, CHECKPOINT, SOURCE_FILES
        from ultralytics_worker import UltralyticsWorker
        detector_manifest = {"runtime_version": "8.4.0", "task": "detect", "checkpoint": "yolo26s", "precision": "fp32",
            "sha256": "a" * 64, "license": "AGPL-3.0", "device_id": "controlled-cpu", "device": "cpu",
            "engine_epoch": "controlled-detector-epoch", "profile_id": "controlled-detector", "model_version": "controlled-authored-detector",
            "format": "pt", "preprocessing": {"imgsz": [640, 640], "confidence": .25, "iou": .7, "max_det": 8, "retina_masks": False}}
        pose_manifest = {"checkpoint": CHECKPOINT, "runtime_version": "0.0.16", "precision": "fp32", "input_size": [192, 256],
            "layout": "coco17", "format": "onnx", "sha256": "b" * 64, "source_sha256": {name: "c" * 64 for name in SOURCE_FILES},
            "license": "Apache-2.0", "model_version": "controlled-authored-pose", "profile_id": "controlled-pose", "device_id": "controlled-cpu",
            "engine_epoch": "controlled-pose-epoch", "onnxruntime_version": "controlled-sdk", "device": "cpu", "score_threshold": .4, "max_people": 2}
        detector, pose = UltralyticsWorker(detector_manifest, lambda *_: True), RTMPoseWorker(pose_manifest, lambda *_: True)
        detector.model = SimpleNamespace(predict=lambda **_: self.detector_result())
        pose.model = self.pose_result
        return detector, pose


def build_base_configuration(*, session_ref, node_runtime, acquisition_binding=None, wall_clock=time.time, monotonic=time.monotonic, clarification=False, manual=False):
    """Build explicit authored config7. Nothing starts or opens a socket here.

    The connected owner composition upgrades these immutable dictionaries using
    the independently prepared room policy before assembling grants or starting.
    The returned SDK and camera capabilities never select code from input JSON.
    """
    from bundle_ingest import calibration_content_id
    from frame_ring import CaptureTurns
    from journal import digest
    from live_assessment import AssessmentRecorder
    from occupancy_reader import OccupancyReader
    from reasoning import digest as model_digest
    from schema_registry import SchemaRegistry
    from spatial_validation import mount_signature
    from supervisor import describe_worker
    identity = deepcopy(session_ref)
    now = wall_clock()
    external = ControlledExternalIO(wall_clock=wall_clock, monotonic=monotonic, clarification=clarification, manual=manual)
    detector, pose = external.model_workers()
    refs = {name: {"id": worker.manifest["profile_id"], "digest": model_digest(worker.manifest)}
            for name, worker in (("detector", detector), ("pose", pose))}
    calibration = external.calibration()
    mount = mount_signature(external.depth_plane())
    projection = {"id": calibration_content_id(calibration), "mount_revision": "controlled-mount-r1", "status": "validated",
        "site_id": identity["site_id"], "camera_id": CAMERA, "validation_receipt": "controlled-depth-validation",
        "error_bound_m": .001, "frame": "rectified_left", "width": 640, "height": 400,
        "intrinsics": {"fx": 450, "fy": 450, "cx": 320, "cy": 200},
        "reference_intrinsics": {"rectified_left": deepcopy(KD), "rgb": deepcopy(KRGB)},
        "rgb_distortion_coefficients": [0] * 14,
        "rgb_from_rectified_left": {"rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "translation_m": [0, 0, 0]},
        "reprojection": {"receipt": "controlled-authored-reprojection", "p95_px": 1.2, "max_px": 2.}}
    registration = {"schema": "home-capture-registration-1", "version": 1, "id": "controlled-registration",
        "site_id": identity["site_id"], "room_id": identity["room_id"], "camera_id": CAMERA,
        "frame": "z_up_metric_floor0", "unit": "m", "geometry_revision": "controlled-geometry-r1",
        "geometry_digest": pin("controlled-geometry")["digest"], "mount_revision": "controlled-mount-r1",
        "source_frame": "rectified_left", "room_from_rectified_left": deepcopy(ROOM_FROM_LEFT), "coverage_refs": ["controlled-coverage"],
        "transform_ref": {"id": "controlled-left-to-room", "digest": digest(ROOM_FROM_LEFT)},
        "validation_ref": pin("controlled-registration-validation"), "error_bound_m": .001}
    if acquisition_binding is not None:
        if (not isinstance(acquisition_binding, dict) or not {"registration"} <= set(acquisition_binding)
                or set(acquisition_binding) - {"registration", "projection", "mount_signature"}):
            raise ValueError("controlled_acquisition_binding_refused")
        registration = deepcopy(acquisition_binding["registration"])
        projection = deepcopy(acquisition_binding.get("projection", projection))
        mount = deepcopy(acquisition_binding.get("mount_signature", mount))
        if (registration.get("site_id") != identity["site_id"] or registration.get("room_id") != identity["room_id"]
                or registration.get("camera_id") != CAMERA or registration.get("frame") != "z_up_metric_floor0"
                or registration.get("unit") != "m"):
            raise ValueError("controlled_acquisition_binding_refused")
        projection.update(site_id=identity["site_id"], camera_id=CAMERA, mount_revision=registration["mount_revision"])
    capture_policy = {"schema": "home-live-capture-policy-2", "version": 2, "camera_id": CAMERA,
        "clock_binding": {"timing_generation": 0, "host_boot_id": BOOT_ID,
            "validation_mode": "owner_accepted_per_observation", "authority_ref": pin("controlled-clock-acceptor")},
        "depth_clock": {"id": "controlled-depth-clock", "drift_ppm": 50, "max_delta_s": .03,
            "semantics_bound_s": .002, "semantics_receipt": "controlled-timestamp-semantics"},
        "projection": projection, "mount_signature": mount, "registration": registration,
        "max_skew_s": .03, "intrinsics_tolerance_px": 1, "receipt_store_id": "controlled-capture-receipts"}
    capture_method = {"id": "controlled-paired-capture-method", "digest": digest(capture_policy)}
    wire_registration = {"registration_ref": registration["id"], "registration_digest": digest(registration),
        "geometry_revision": registration["geometry_revision"], "geometry_digest": registration["geometry_digest"],
        "mount_digest": digest(mount), "calibration_digest": calibration_content_id(calibration),
        "coverage_refs": deepcopy(registration["coverage_refs"]), "frame": "z_up_metric_floor0", "unit": "m",
        "transform_chain_refs": [deepcopy(registration["transform_ref"])]}
    spatial_policy = {"schema": "home-live-spatial-policy-1", "version": 1,
        "capture_method_ref": deepcopy(capture_method), "model_refs": deepcopy(refs),
        "receipt_store_id": "controlled-spatial-receipts", "receipt_component_id": "spatial",
        "qualification_ref": pin("controlled-independent-spatial-applicability"),
        "sampling": {"radius_px": 1.5, "min_support": 3, "max_spread_m": .03},
        "continuity": {"max_gap_s": 2, "max_speed_m_s": 2, "max_step_m": .5}, "work_margin_m": [.1, .1, .1],
        "anatomy": {"torso_radius_m": .22, "limb_radius_m": .02, "hand_radius_m": .02,
            "head_half_extents_m": [.12, .12, .15], "protection_margin_m": [.08, .08, .08], "min_shoulder_baseline_m": .1}}
    spatial_method = {"id": "controlled-authored-stationary-spatial", "digest": digest(spatial_policy)}
    interaction_policy = {"schema": "home-live-interaction-policy-1", "version": 1,
        "capture_method_ref": deepcopy(capture_method), "spatial_method_ref": deepcopy(spatial_method), "model_refs": deepcopy(refs),
        "receipt_store_id": "controlled-interaction-receipts", "receipt_component_id": "interaction",
        "qualification_ref": pin("controlled-authored-holding"), "max_age_s": 3, "semantic_model_refs": [],
        "material_geometry": {"labels": {"book": "book", "cell phone": "phone", "phone": "phone"},
            "min_detector_score": .6, "max_materials": 4, "min_object_support": 20, "max_object_spread_m": .5,
            "max_wrist_spread_m": .03, "max_hand_object_distance_m": .08, "max_near_distance_m": .35,
            "min_hand_span_m": .08, "edge_band_fraction": .25, "rgb_margin_px": 8., "min_between_fraction": .7}}
    interaction_method = {"id": "controlled-authored-holding-method", "digest": digest(interaction_policy)}
    part_policies = {}
    for name in ("work", "body", "head", "hand_left", "hand_right", "protection", "body_forward", "head_forward"):
        if name.endswith("_forward"):
            bound = {"model": "bounded_angle-1", "angular_error_rad": .2,
                "age": {"max_age_s": 1, "max_angular_speed_rad_s": .5}, "applicability_ref": deepcopy(spatial_policy["qualification_ref"])}
        else:
            bound = {"model": "conservative_additive_radius-1", "bound_at_observation_m": .018,
                "components_m": {"capture_association": .001, "depth": .015, "registration": .001, "estimator": .001},
                "age": {"max_age_s": 3, "max_speed_m_s": 0, "max_accel_m_s2": 0},
                "applicability_ref": deepcopy(spatial_policy["qualification_ref"])}
        part_policies[name] = bound
    continuity = {"method_ref": pin("controlled-person-continuity"), "qualification_ref": pin("controlled-person-continuity-qualification"),
                  "applicability_ref": pin("controlled-person-continuity-applicability"), "max_gap_s": 4}
    lifecycle_policy = {"schema": "home-local-person-lifecycle-policy-1", "version": 1, "id": "controlled-person-lifecycle",
        "capture_method_ref": deepcopy(capture_method), "spatial_method_ref": deepcopy(spatial_method), "model_refs": deepcopy(refs),
        "registration": deepcopy(wire_registration), "receipt_store_id": "controlled-lifecycle-receipts", "receipt_component_id": "lifecycle",
        "continuity": {**deepcopy(continuity), "anchor_joint_ids": [5, 6, 11, 12], "min_anchor_count": 2,
            "max_anchor_error_m": .1, "max_step_m": .5, "max_speed_m_s": 2}, "negative_methods": [], "absence_methods": []}
    source = {"producer_id": "controlled-capture-producer", "producer_epoch": "controlled-producer-epoch", "capture_epoch": CAMERA_EPOCH,
        "initial_generation": 1, "sensor_roots": ["camera:" + CAMERA],
        "capture_clock": {"mapping_id": "controlled-rgb-clock", "mapping_digest": digest(capture_policy["clock_binding"]),
            "source_epoch": CAMERA_EPOCH, "original_unit": "unix_ns", "epoch_origin_s": 0, "max_uncertainty_s": .1},
        "control_clock": {"mapping_id": "controlled-service-clock", "mapping_digest": pin("controlled-service-clock")["digest"],
            "source_epoch": "controlled-service-epoch", "original_unit": "unix_ns", "epoch_origin_s": 0, "max_uncertainty_s": .001},
        "method_refs": [deepcopy(capture_method), deepcopy(spatial_method), deepcopy(interaction_method), deepcopy(continuity["method_ref"])],
        "association_method_refs": [deepcopy(capture_method), deepcopy(spatial_method), deepcopy(continuity["method_ref"])], "model_refs": list(deepcopy(refs).values()),
        "registration": wire_registration, "receipt_store_ids": ["controlled-capture-receipts", "controlled-spatial-receipts",
            "controlled-interaction-receipts", "controlled-lifecycle-receipts"], "receipt_component_ids": ["rgb", "depth", "spatial", "interaction", "lifecycle"],
        "max_age_s": 3, "part_policies": part_policies, "primitive_ids": {"body": list(BODY_IDS), "protection": ["head_exclusion"]},
        "protection_policy_refs": [pin("controlled-head-protection")]}
    scene_policy = _scene_policy(interaction_method)
    delivery_policy = _delivery_policy(wire_registration, source, now)
    worker_lifecycle = {"schema": "home-live-person-lifecycle-policy-1", "version": 1, "continuity_methods": [deepcopy(continuity)],
        "negative_methods": [], "absence_methods": [], "operator_clearance": {"policy_ref": pin("controlled-clearance-policy"),
            "applicability_ref": pin("controlled-clearance-applicability"), "procedure_ref": pin("controlled-clearance-procedure"),
            "room_assessment_ref": deepcopy(delivery_policy["room"]["assessment_ref"]), "max_age_s": 5}}
    worker_config = {"profile": "home-live-shadow-1", "identity": identity, "worker_id": "controlled-worker", "producer_id": "controlled-worker",
        "producer_epoch": "controlled-worker-epoch", "revision": REVISION, "max_output_age_s": 3, "semantic_interval_s": 1,
        "source_pins": {SOURCE: digest(source)}, "engagement_policy": {"support_methods": [deepcopy(interaction_method)],
            "refinement_methods": [], "negative_methods": [], "allow_experimental": True},
        "scene_policy": scene_policy, "delivery_policy": delivery_policy, "lifecycle_policy": worker_lifecycle}
    description = describe_worker(node_runtime, worker_config)
    worker_keys = ("worker_id", "producer_id", "producer_epoch", "revision", "max_output_age_s", "semantic_interval_s",
                   "source_pins", "engagement_policy", "scene_policy", "delivery_policy", "lifecycle_policy")
    worker = {key: deepcopy(worker_config[key]) for key in worker_keys}
    worker.update({key: deepcopy(description[key]) for key in ("config_digest", "schema_pins", "scene_schema_pins", "delivery_schema_pins",
                    "lifecycle_schema_pins", "conservative_delivery_schema_pins")})
    manifest = {"schema": "home-live-ingress-manifest-7", "version": 7, "identity": deepcopy(identity),
        "handoff_digest": "8345e855397d86c65a2197daa7592dcf4f73dc48fa00659bd588093b297d7d0e",
        "interface_digest": SchemaRegistry((), profile="home-live-shadow-1").interface_digest,
        **{key: deepcopy(description[key]) for key in ("worker_interface_digest", "scene_interface_digest", "delivery_interface_digest",
            "lifecycle_interface_digest", "conservative_delivery_interface_digest")}, "sources": {SOURCE: source}, "worker": worker,
        "service_clock_policy": {"schema": "home-live-service-clock-policy-1", "version": 1,
            "qualification_ref": pin("controlled-service-clock-qualification"), "valid_until": now + 3600,
            "uncertainty_s": .001, "max_anchor_divergence_s": .05, "max_round_trip_s": .5, "max_mapping_age_s": 3},
        "limits": {"max_original_roots": 1024, "max_heads": 128, "max_receipt_bindings": 2048}}
    configuration = {"schema": "home-live-ingress-config-7", "version": 7,
        "manifest_ref": {"id": "controlled-live-manifest", "digest": digest(manifest)}, "manifest": manifest}
    worker_config.update(manifest_ref=deepcopy(configuration["manifest_ref"]), live_manifest=deepcopy(manifest))
    assessment = AssessmentRecorder({"schema": "home-live-assessment-policy-1", "version": 1,
        "id": "controlled-assessment", "max_records": 24, "max_bytes": 8 * 1024**2})
    entity = "binary_sensor.controlled_person_occupancy"
    acquisition = {"schema": "home-production-acquisition-5", "version": 5, "session_ref": deepcopy(identity),
        "source_id": SOURCE, "revision": REVISION, "local_grant_id": "controlled-local-acquisition-grant", "model_refs": refs,
        "limits": {"interval_s": 1, "max_cycles": 24, "max_runtime_s": 120, "stage_timeout_s": 2, "memory_limit_mb": 512},
        "person_threshold": .5, "max_people": 2, "occupancy_entity": entity, "camera_derived_entities": [entity],
        "spatial_method_ref": spatial_method, "interaction_method_ref": interaction_method,
        "assessment_ref": assessment.policy_ref, "lifecycle_ref": {"id": lifecycle_policy["id"], "digest": digest(lifecycle_policy)}}
    local_authority = {"schema": "home-local-acquisition-grant-1", "version": 1, "id": acquisition["local_grant_id"],
        "session_ref": deepcopy(identity), "source_id": SOURCE, "camera_id": CAMERA, "configuration_digest": digest(acquisition),
        "model_refs": deepcopy(refs), "operations": ["capture", "detector", "pose", "spatial", "interaction", "assessment", "lifecycle"],
        "not_before": now - 1, "valid_until": now + 180, "generation": 1}
    occupancy = OccupancyReader(lambda: ({"observed_at": wall_clock(), "states": {entity: "on"}}, None), entity,
                                clock=wall_clock, monotonic=monotonic)
    pilot_config = SimpleNamespace(ntp=SimpleNamespace(server_ip="192.0.2.1", local_clock_quantization_ns=1000,
        drift_slew_ppm=1000, timeout_seconds=.5, valid_for_seconds=1, max_uncertainty_seconds=1))
    def accept_once(_config, token, *, transport, publisher_factory):
        transport("/frame.jpg", token)
        return {"status": "accepted"}
    pilot = SimpleNamespace(CONFIG="controlled-fixed-config", load_config=lambda _: pilot_config,
        fetch=lambda *_: ({"content-type": "application/json"}, b'{"healthy":true}'),
        accept_once=accept_once, read_token=lambda: "controlled-authored-camera-token")
    camera = {"turns": CaptureTurns(threading.Lock(), monotonic=monotonic),
        "runtime_factory": lambda: (pilot, lambda *_: None, lambda *_: None), "pair_transport": external.camera_pair}
    def control_clock():
        policy = source["control_clock"]
        current = wall_clock()
        return {"observed_at": current, "clock": {"status": "commissioned", "mapping_id": policy["mapping_id"],
            "mapping_digest": policy["mapping_digest"], "source_epoch": policy["source_epoch"], "original_unit": "unix_ns",
            "original_value": str(round(current * 1e9)), "uncertainty_s": .001}}
    return {"configuration": configuration, "worker_config": worker_config, "acquisition_config": acquisition,
        "capture_policy": capture_policy, "capture_method": capture_method, "spatial_policy": spatial_policy,
        "interaction_policy": interaction_policy, "lifecycle_policy": lifecycle_policy, "assessment_recorder": assessment,
        "detector": detector, "pose": pose, "local_authority": lambda: deepcopy(local_authority), "household_enabled": lambda: True,
        "occupancy": occupancy, "camera_dependencies": camera, "control_clock": control_clock, "rss": process_rss_bytes, "external_io": external}


def _scene_policy(interaction_method):
    from journal import digest
    examples, profiles = [], []
    for name, level, kelvin in (("anonymous", .4, 3000), ("owner", .7, 3500)):
        example = {"schema": "home-approved-lighting-example-1", "version": 1, "id": "controlled-" + name + "-example",
            "approved": True, "profile_id": "controlled-" + name + "-profile",
            "nominal": {"brightness_fraction": level, "cct_kelvin": kelvin, "fixture_family_ref": pin("controlled-fixture-family")},
            "requirements_ref": pin("controlled-task-requirements")}
        profile = {"schema": "home-approved-profile-1", "version": 1, "id": example["profile_id"], "approved": True,
                   "example_refs": [{"id": example["id"], "digest": digest(example)}]}
        examples.append(example); profiles.append(profile)
    return {"source_gap_hold_s": 10, "catalog": {"profiles": profiles, "examples": examples,
        "anonymous_baseline": {"profile_ref": {"id": profiles[0]["id"], "digest": digest(profiles[0])},
            "example_ref": {"id": examples[0]["id"], "digest": digest(examples[0])}},
        "background": {"approval_ref": pin("controlled-background-approval"), "requirements_ref": pin("controlled-background-requirements"),
            "nominal": {"brightness_fraction": .2, "cct_kelvin": 3000, "fixture_family_ref": pin("controlled-fixture-family")},
            "fade_in_s": 1, "methods": [deepcopy(interaction_method)]}}}


def _delivery_policy(registration, source, now):
    bounds = {"kind": "aabb", "center_m": [1, 3, 1.75], "half_extents_m": [4, 4, 1.75]}
    return {"schema": "home-live-task-delivery-policy-4", "version": 4, "policy_ref": pin("controlled-delivery-policy"),
        "registration": deepcopy(registration), "fixture": {"id": "controlled-task-fixture", "fixture_pose_ref": pin("controlled-task-fixture-pose"),
            "family_ref": pin("controlled-fixture-family"), "assessment_ref": pin("controlled-optical-assessment"),
            "origin_m": [1, 3.234, 1.7], "local_to_room": [1, 0, 0, 0, 1, 0, 0, 0, 1],
            "optics": {"envelope_half_angle_deg": 70, "max_range_m": 8, "pointing_error_deg": .1,
                "full_dark_upper_fraction": 0, "outside_envelope_upper_fraction": 0},
            "mechanics": {"pan_min_deg": -180, "pan_max_deg": 180, "tilt_min_deg": 0, "tilt_max_deg": 90,
                "max_velocity_deg_s": 120, "max_acceleration_deg_s2": 240, "max_jerk_deg_s3": 1000, "settle_s": .2, "fade_s": 1},
            "initial": {"pan_deg": 0, "tilt_deg": 0}},
        "room": {"assessment_ref": pin("controlled-room-assessment"), "collision_ref": pin("controlled-collision-asset"),
            "geometry_digest": registration["geometry_digest"], "complete": True, "usable_bounds": bounds, "occluders": [], "valid_until": now + 3600},
        "protection": {"assessment_ref": pin("controlled-protection-assessment"), "policy_ref": deepcopy(source["protection_policy_refs"][0]),
            "max_exposure_fraction": 0}, "requirements_ref": pin("controlled-task-requirements"), "required_body_parts": list(BODY_IDS),
        "coverage": {"grid_size": 9, "min_fraction": .7, "assessment_ref": pin("controlled-coverage-assessment")}, "brightness_transition_s": 2,
        "modeled_emission": {"assessment_ref": pin("controlled-emission-boundary"), "range_m": 12, "valid_until": now + 3600},
        "observation_coverage": {"assessment_ref": pin("controlled-person-coverage-assessment"), "method_ref": pin("controlled-person-coverage-method"),
            "qualification_ref": pin("controlled-person-coverage-qualification"), "applicability_ref": pin("controlled-person-coverage-applicability"),
            "bounds": deepcopy(bounds), "valid_until": now + 3600}}


def assemble_journal_grants(configuration, worker_config, *, publication_token, worker_token, human_token):
    """Issue narrow grants after the final manifest/version has been frozen."""
    import schema_registry as schemas
    scope = worker_config["identity"]
    manifest, manifest_ref = configuration["manifest"], configuration["manifest_ref"]
    sources = manifest["sources"]
    stage = (getattr(schemas, "LIVE_ROOM_SUBSET_VERSIONS", None) if configuration["version"] == 13 else
             getattr(schemas, "LIVE_PC_SUPPORT_VERSIONS", None) if configuration["version"] == 12 else
             getattr(schemas, "LIVE_ROOM_MANUAL_VERSIONS", None) if configuration["version"] == 11 else
             getattr(schemas, "LIVE_ROOM_CLARIFICATION_VERSIONS", None) if configuration["version"] == 10 else
             getattr(schemas, "LIVE_ROOM_VERSIONS", None) if configuration["version"] == 9 else schemas.LIVE_CONSERVATIVE_DELIVERY_VERSIONS)
    if not stage:
        raise ValueError("controlled_room_schema_closure_unavailable")
    worker_types = ["home-live-fact-1", "home-live-geometry-1", *schemas.LIVE_WORKER_VERSIONS, *schemas.LIVE_SCENE_VERSIONS,
                    "home-live-person-lifecycle-1", "home-live-intent-event-2", *stage]
    producer_types = ["home-live-fact-1", "home-live-geometry-1", "home-live-person-lifecycle-1"]
    decision = next(name for name in stage if name.startswith("home-live-shadow-decision-"))
    human_types = ["home-live-fact-1", "home-live-geometry-1", "home-live-engagement-state-1", decision,
                   "home-live-intent-event-1", "home-live-intent-event-2"]
    if configuration["version"] in {10,11}:
        human_types.append("home-live-intent-event-3")
    if configuration["version"] == 11:
        human_types.append("home-live-intent-event-4")
    def grant(principal, token, operations, types, capabilities):
        types = list(dict.fromkeys(types))
        return {"principal_id": principal, "producer_id": principal, "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "scopes": [{"site_id": scope["site_id"], "room_id": scope["room_id"]}], "operations": operations,
            "payload_types": types, "revisions": [REVISION, "1.0", "2.0"] + (["3.0", "9.0"] if configuration["version"] in {10,11} else []) + (["4.0", "10.0"] if configuration["version"] == 11 else []) + (["11.0"] if configuration["version"] == 12 else []) + (["12.0"] if configuration["version"] == 13 else []), "source_ids": list(sources),
            "sensor_roots": sorted({root for source in sources.values() for root in source["sensor_roots"]}),
            "schema_pins": {name: schemas.schema_digest(name, profile="home-live-shadow-1") for name in types},
            "live_bindings": [{"manifest_ref": deepcopy(manifest_ref), "source_id": source, "capabilities": capabilities} for source in sources]}
    producer_id = next(iter(sources.values()))["producer_id"]
    producer = grant(producer_id, publication_token, ["read", "event"], producer_types, ["capture", "control", "read"])
    worker = grant(worker_config["worker_id"], worker_token, ["read", "worker_commit"], worker_types, ["read", "worker_commit"])
    worker.update(producer_id=worker_config["producer_id"], producer_epoch=worker_config["producer_epoch"],
        expected_consumer_id=worker_config["worker_id"], expected_worker_id=worker_config["worker_id"])
    human = grant("controlled-owner", human_token, ["read", "intent"], human_types, ["read"])
    human.update(expected_actor_ref="controlled-owner", sensor_roots=[])
    return [producer, worker, human]
