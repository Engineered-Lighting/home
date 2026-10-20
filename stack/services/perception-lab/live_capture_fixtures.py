"""Fictional paired-capture commissioning and bytes; no household inputs."""
import base64
import copy
import hashlib
import io
import json
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
for directory in (ROOT / "tools/shared-home", ROOT / "stack/services/home-context"):
    if str(directory) not in sys.path:
        sys.path.append(str(directory))

from journal import digest
from schema_registry import SchemaRegistry, schema_digest
from bundle_ingest import calibration_content_id
from spatial_validation import mount_signature

NOW = 1800000000
PROFILE = "home-live-shadow-1"
SOURCE = "synthetic-paired-source"
TOKEN = "synthetic-paired-token"
REVISION = "synthetic-paired-r1"
CAMERA = "synthetic-oak"
SCOPE = {"site_id": "synthetic-site", "room_id": "synthetic-room", "session_id": "synthetic-session", "domain_epoch": "synthetic-domain"}
KD = [[450, 0, 320], [0, 450, 200], [0, 0, 1]]
KRGB = [[500, 0, 320], [0, 500, 180], [0, 0, 1]]
ROOM_FROM_LEFT = [[1, 0, 0, 1], [0, 0, 1, 2], [0, -1, 0, 1.5], [0, 0, 0, 1]]


def pin(name):
    return {"id": name, "digest": hashlib.sha256(name.encode()).hexdigest()}


def jpeg():
    encoded = io.BytesIO()
    Image.new("RGB", (640, 360), (61, 79, 101)).save(encoded, format="JPEG", quality=85)
    return encoded.getvalue()


def header(data, *, observed_ns=1800000000000000000, sequence=7, epoch="synthetic-camera-epoch", **changes):
    """The complete real shared contract's causal challenge/exposure receipt."""
    send, receive, exposure, quantization, host_error = 10_000_000_000, 10_020_000_000, .01, 1000, .002
    value = {
        "schema": 3, "session": epoch, "sequence": sequence, "timing_generation": 0,
        "width": 640, "height": 360, "quality": 85,
        "observed_monotonic_seconds": (send + receive) / 2e9,
        "observed_at_unix_ns": observed_ns,
        "uncertainty_seconds": (receive - send) / 2e9 + exposure + quantization / 1e9 + host_error,
        "validation_id": "synthetic-camera-clock-validation", "exposure_duration_seconds": exposure,
        "source_clock": "depthai_device_monotonic", "monotonic_clock_scope": "python_monotonic_on_camera_host",
        "timestamp_semantics": "causal_exact_frame_exposure_end", "challenge_nonce": "0" * 32,
        "challenge_device_ns": 50_000_000_000, "frame_device_ns": 50_200_000_000,
        "host_send_monotonic_ns": send, "host_receive_monotonic_ns": receive,
        "timestamp_quantization_ns": quantization, "preview_sha256": "1" * 64,
        "jpeg_sha256": hashlib.sha256(data).hexdigest(),
        "host_boot_id": "00000000-0000-0000-0000-000000000001",
        "host_clock_validation_id": "synthetic-host-clock-validation", "host_clock_uncertainty_seconds": host_error,
        **changes,
    }
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()


def decoded_header(encoded):
    return json.loads(base64.urlsafe_b64decode(encoded))


def owner_calibration():
    return {"session": "synthetic-camera-epoch", "depth_alignment": "rectified_left", "depth_units": "millimetres",
            "stereo_size": [640, 400], "eeprom": {"synthetic_device": "fictional-only"}}


def frame_metadata(*, sequence=7, depth_sequence=41, depth_device_s=50.19, rgb_device_s=50.2, epoch="synthetic-camera-epoch"):
    common = {"host_monotonic_timestamp_seconds": 10, "transformation_valid": True}
    frames = {name: {**common, "sequence": depth_sequence, "device_timestamp_seconds": depth_device_s,
                     "width": 640, "height": 400, "intrinsics": copy.deepcopy(KD)} for name in ("depth", "left", "right")}
    frames["rgb"] = {**common, "sequence": sequence, "device_timestamp_seconds": rgb_device_s,
                     "width": 640, "height": 360, "intrinsics": copy.deepcopy(KRGB), "source_distortion_coefficients": [0] * 14}
    return {"schema": 1, "session": epoch, "depth_alignment": "rectified_left", "depth_units": "millimetres", "invalid_depth": 0,
            "captured_unix_ns": 1800000000900000000,
            "captured_unix_ns_semantics": "bundle_packaging_time_not_sensor_exposure", "frames": frames}


def bundle(metadata=None, *, value=1234):
    """A tiny compressed archive of fixed-sized numeric planes, generated in RAM."""
    output = io.BytesIO()
    np.savez_compressed(output, depth_mm=np.full((400, 640), value, np.uint16), left=np.zeros((400, 640), np.uint8),
                        right=np.zeros((400, 640), np.uint8), rgb_nv12=np.zeros((540, 640), np.uint8),
                        metadata_json=np.frombuffer(json.dumps(metadata or frame_metadata()).encode(), np.uint8))
    return output.getvalue()


def inputs(*, sequence=7, observed_ns=1800000000000000000, depth_delta_s=-.01, value=1234, **header_changes):
    """One original pair; callers can retain these exact bytes for ACK retries."""
    data = jpeg()
    device_ns = 50_200_000_000 + (sequence - 7) * 1_000_000_000
    receipt = header(data, observed_ns=observed_ns, sequence=sequence, frame_device_ns=device_ns, **header_changes)
    depth_stamp = float(Decimal(device_ns) / Decimal(1000000000) + Decimal(str(depth_delta_s)))
    meta = frame_metadata(sequence=sequence, depth_sequence=41 + sequence - 7,
                          rgb_device_s=device_ns / 1e9, depth_device_s=depth_stamp)
    return data, receipt, bundle(meta), owner_calibration()


def setup():
    """Complete explicit synthetic policy, manifest and least-scope test grant."""
    calibration = owner_calibration()
    plane = np.full((400, 640), 1234, np.uint16)
    mount = mount_signature(plane)
    projection = {"id": calibration_content_id(calibration), "mount_revision": "synthetic-mount-r1", "status": "validated",
                  "site_id": SCOPE["site_id"], "camera_id": CAMERA, "validation_receipt": "synthetic-depth-validation",
                  "error_bound_m": .03, "frame": "rectified_left", "width": 640, "height": 400,
                  "intrinsics": {"fx": 450, "fy": 450, "cx": 320, "cy": 200},
                  "reference_intrinsics": {"rectified_left": copy.deepcopy(KD), "rgb": copy.deepcopy(KRGB)},
                  "rgb_distortion_coefficients": [0] * 14,
                  "rgb_from_rectified_left": {"rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "translation_m": [-.0375, 0, 0]},
                  "reprojection": {"receipt": "synthetic-assessed-reprojection", "p95_px": 1.2, "max_px": 2.0}}
    registration = {"schema": "home-capture-registration-1", "version": 1, "id": "synthetic-registration",
                    "site_id": SCOPE["site_id"], "room_id": SCOPE["room_id"], "camera_id": CAMERA,
                    "frame": "z_up_metric_floor0", "unit": "m", "geometry_revision": "synthetic-geometry-r1",
                    "geometry_digest": pin("synthetic-geometry")["digest"], "mount_revision": "synthetic-mount-r1",
                    "source_frame": "rectified_left", "room_from_rectified_left": copy.deepcopy(ROOM_FROM_LEFT),
                    "coverage_refs": ["synthetic-coverage"],
                    "transform_ref": {"id": "synthetic-left-to-room", "digest": digest(ROOM_FROM_LEFT)},
                    "validation_ref": pin("synthetic-registration-validation"), "error_bound_m": .02}
    policy = {"schema": "home-live-capture-policy-1", "version": 1, "camera_id": CAMERA,
              "clock_binding": {"timing_generation": 0, "validation_id": "synthetic-camera-clock-validation",
                                "host_boot_id": "00000000-0000-0000-0000-000000000001",
                                "host_clock_validation_id": "synthetic-host-clock-validation"},
              "depth_clock": {"id": "synthetic-depth-clock", "drift_ppm": 50, "max_delta_s": .03,
                              "semantics_bound_s": .002, "semantics_receipt": "synthetic-timestamp-semantics"},
              "projection": projection, "mount_signature": mount, "registration": registration,
              "max_skew_s": .03, "intrinsics_tolerance_px": 1, "receipt_store_id": "synthetic-capture-receipts"}
    method = {"id": "synthetic-paired-capture-method", "digest": digest(policy)}
    wire_registration = {"registration_ref": registration["id"], "registration_digest": digest(registration),
                         "geometry_revision": registration["geometry_revision"], "geometry_digest": registration["geometry_digest"],
                         "mount_digest": digest(mount), "calibration_digest": calibration_content_id(calibration),
                         "coverage_refs": registration["coverage_refs"], "frame": "z_up_metric_floor0", "unit": "m",
                         "transform_chain_refs": [copy.deepcopy(registration["transform_ref"])]}
    source = {"producer_id": "synthetic-capture-producer", "producer_epoch": "synthetic-producer-epoch",
              "capture_epoch": "synthetic-camera-epoch", "initial_generation": 1, "sensor_roots": ["camera:synthetic-oak"],
              "capture_clock": {"mapping_id": "synthetic-rgb-clock", "mapping_digest": digest(policy["clock_binding"]),
                                "source_epoch": "synthetic-camera-epoch", "original_unit": "unix_ns", "epoch_origin_s": 0, "max_uncertainty_s": .1},
              "control_clock": {"mapping_id": "synthetic-service-clock", "mapping_digest": pin("synthetic-service-clock")["digest"],
                                "source_epoch": "synthetic-service-epoch", "original_unit": "unix_ns", "epoch_origin_s": 0, "max_uncertainty_s": .001},
              "method_refs": [copy.deepcopy(method)], "association_method_refs": [copy.deepcopy(method)], "model_refs": [],
              "registration": wire_registration, "receipt_store_ids": [policy["receipt_store_id"]], "receipt_component_ids": ["rgb", "depth"],
              "max_age_s": 3, "part_policies": {name: None for name in ("work", "body", "head", "hand_left", "hand_right", "protection", "body_forward", "head_forward")},
              "primitive_ids": {"body": [], "protection": []}, "protection_policy_refs": []}
    registry = SchemaRegistry((), profile=PROFILE)
    manifest = {"schema": "home-live-ingress-manifest-1", "version": 1, "identity": copy.deepcopy(SCOPE),
                "handoff_digest": "8345e855397d86c65a2197daa7592dcf4f73dc48fa00659bd588093b297d7d0e",
                "interface_digest": registry.interface_digest, "sources": {SOURCE: source},
                "limits": {"max_original_roots": 1024, "max_heads": 128, "max_receipt_bindings": 2048}}
    manifest_ref = {"id": "synthetic-capture-manifest", "digest": digest(manifest)}
    config = {"schema": "home-live-ingress-config-1", "version": 1, "manifest_ref": manifest_ref, "manifest": manifest}
    types = ["home-live-fact-1", "home-live-geometry-1"]
    grant = {"principal_id": "synthetic-capture-principal", "producer_id": source["producer_id"],
             "token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest(),
             "scopes": [{"site_id": SCOPE["site_id"], "room_id": SCOPE["room_id"]}],
             "operations": ["event", "read"], "payload_types": types, "revisions": [REVISION],
             "schema_pins": {kind: schema_digest(kind, profile=PROFILE) for kind in types},
             "source_ids": [SOURCE], "sensor_roots": source["sensor_roots"],
             "live_bindings": [{"manifest_ref": copy.deepcopy(manifest_ref), "source_id": SOURCE, "capabilities": ["capture", "control", "read"]}]}
    return config, policy, method, calibration, grant
