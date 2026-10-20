"""Synthetic live-profile HTTP fixtures; never reads household data or media."""
import http.client
import copy
import hashlib
import json
import threading
from pathlib import Path

from server import LocalServer


NOW = 1800000000
PROFILE = "home-live-shadow-1"
SCOPE = {"site_id": "synthetic-site", "room_id": "synthetic-room", "session_id": "synthetic-session", "domain_epoch": "synthetic-domain"}
TOKEN = "synthetic-live-token"


def reference(name):
    return {"id": name, "digest": hashlib.sha256(name.encode()).hexdigest()}


MANIFEST = reference("synthetic-manifest")
METHOD = reference("synthetic-interaction-method")
GEOMETRY_METHOD = reference("synthetic-geometry-method")
ASSOCIATION_METHOD = reference("synthetic-association-method")
CAPTURE_CLOCK = reference("synthetic-capture-clock")
SERVICE_CLOCK = reference("synthetic-service-clock")
REGISTRATION = {
    "registration_ref": "synthetic-registration", "registration_digest": reference("registration")["digest"],
    "geometry_revision": "synthetic-geometry-r1", "geometry_digest": reference("geometry")["digest"],
    "mount_digest": reference("mount")["digest"], "calibration_digest": reference("calibration")["digest"],
    "coverage_refs": ["synthetic-coverage"], "frame": "z_up_metric_floor0", "unit": "m",
    "transform_chain_refs": [reference("synthetic-depth-to-room")],
}


def independent_digest(value):
    """Fixtures use simple ASCII keys and finite non-exponent numeric values."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def person(track="synthetic-track-1"):
    return {**SCOPE, "track_epoch": track, "pid": 1}


def subject(epoch="synthetic-subject-1"):
    return {"subject_id": "synthetic-work", "subject_epoch": epoch}


def receipt(seq=1, component="pose"):
    observed = NOW + seq - 1
    return {"store_id": "synthetic-receipts", "receipt_id": f"synthetic-capture-{seq}:{component}",
            "digest": reference(f"capture-{seq}:{component}")["digest"], "component_id": component,
            "observed_at": observed, "available_at": observed + .05,
            "clock_mapping_id": CAPTURE_CLOCK["id"]}


def capture_payload(schema, event_id, seq=1, generation=1):
    observed = NOW + seq - 1
    method = GEOMETRY_METHOD if schema == "home-live-geometry-1" else METHOD
    selected_person, selected_subject = person(), subject()
    return {
        "schema": schema, "version": 1, "id": event_id, **SCOPE,
        "manifest_ref": copy.deepcopy(MANIFEST), "person_ref": selected_person, "subject_ref": selected_subject,
        "association": {"person_status": "qualified", "subject_status": "qualified", "association_id": "synthetic-association",
                        "generation": 1, "method_ref": copy.deepcopy(ASSOCIATION_METHOD),
                        "candidate_person_refs": [], "candidate_subject_refs": []},
        "head": {"key": {"source_id": "synthetic-source", "payload_type": schema,
                         "person_ref": copy.deepcopy(selected_person), "subject_ref": copy.deepcopy(selected_subject),
                         "predicate": "geometry_bundle" if schema == "home-live-geometry-1" else "object_interaction",
                         "method_ref": copy.deepcopy(method)},
                 "position": {"capture_epoch": "synthetic-camera-epoch", "capture_seq": seq, "revision": 0}, "supersedes": None},
        "status": "supplied", "observed_at": observed, "available_at": observed + .1, "valid_until": observed + 10,
        "source_id": "synthetic-source", "sensor_roots": ["camera:synthetic-oak"],
        "observation_roots": [f"synthetic-frame-{seq}:rgb", f"synthetic-frame-{seq}:depth"],
        "observation_root_times": {f"synthetic-frame-{seq}:rgb": observed, f"synthetic-frame-{seq}:depth": observed},
        "sensor_root_bindings": {f"synthetic-frame-{seq}:rgb": ["camera:synthetic-oak"], f"synthetic-frame-{seq}:depth": ["camera:synthetic-oak"]},
        "parent_refs": [], "original_receipt_refs": [receipt(seq, "pose"), receipt(seq, "depth")],
        "clock": {"status": "commissioned", "mapping_id": CAPTURE_CLOCK["id"], "mapping_digest": CAPTURE_CLOCK["digest"],
                  "source_epoch": "synthetic-camera-epoch", "original_unit": "unix_ns", "original_value": str(observed * 1000000000),
                  "uncertainty_s": .001},
        "method_ref": copy.deepcopy(method), "source_generation": generation,
    }


def fact(event_id="synthetic-fact-1", seq=1, generation=1):
    return {**capture_payload("home-live-fact-1", event_id, seq, generation), "layer": "interpretation", "kind": "object_interaction",
            "value": True, "assertion_basis": "inferred", "reason": "qualified_per_observation_holding",
            "model_ref": None, "probability": None, "conflicting_refs": []}


def geometry(event_id="synthetic-geometry-1", seq=1, generation=1):
    payload = capture_payload("home-live-geometry-1", event_id, seq, generation)
    observed = payload["observed_at"]
    unavailable = {"status": "unobservable", "reason": "missing_measurement_bound", "support_refs": [receipt(seq, "pose")],
                   "observed_at": observed, "valid_until": observed + 10}
    parts = {name: copy.deepcopy(unavailable) for name in ("body", "head", "hand_left", "hand_right", "protection", "body_forward", "head_forward")}
    parts["work"] = {
        "status": "supplied", "basis": "inferred",
        "shape": {"kind": "aabb", "center_m": [1.25, 2.5, .9], "half_extents_m": [.3, .2, .1]},
        "uncertainty": {"model": "conservative_additive_radius-1", "bound_at_observation_m": .04,
                        "components_m": {"capture_association": .01, "depth": .01, "registration": .01, "estimator": .01},
                        "age": {"origin_at": observed, "max_age_s": 10, "max_speed_m_s": .1, "max_accel_m_s2": .2},
                        "applicability_ref": reference("synthetic-bound-applicability")},
        "support_refs": [receipt(seq, "pose"), receipt(seq, "depth")], "observed_at": observed,
        "valid_until": observed + 10, "method_ref": copy.deepcopy(GEOMETRY_METHOD),
    }
    return {**payload, "reason": "qualified_coarse_bounds", "registration": copy.deepcopy(REGISTRATION), "parts": parts}


def event(payload):
    return {"schema": "home-context-event-1", "schema_version": "1.0", "id": payload["id"],
            "producer_id": "synthetic-producer", "stream_epoch": "synthetic-producer-epoch", **SCOPE,
            "observed_at": payload["observed_at"], "available_at": payload["available_at"], "valid_until": payload["valid_until"],
            "payload_type": payload["schema"], "payload_version": "1.0", "revision": "synthetic-r1", "payload": payload}


def publication_ref(saved):
    return {key: saved[key] for key in ("stream_epoch", "cursor", "producer_id", "id", "payload_digest")} | {"component_id": None}


def unavailable(payload, until=None):
    payload = copy.deepcopy(payload)
    payload["status"] = "unobservable"
    payload["reason"] = "material_occluded"
    if until is not None:
        payload["valid_until"] = until
    if payload["schema"] == "home-live-fact-1":
        payload["value"] = None
    else:
        payload["parts"] = {name: {"status": "unobservable", "reason": "missing_measurement_bound",
                                    "support_refs": part["support_refs"], "observed_at": payload["observed_at"],
                                    "valid_until": payload["valid_until"]} for name, part in payload["parts"].items()}
    return payload


def barrier(refs, event_id="synthetic-barrier", at=NOW + 1, expected=1, authority=0, schema="home-live-geometry-1"):
    return {"schema": schema, "version": 1, "id": event_id, **SCOPE, "manifest_ref": copy.deepcopy(MANIFEST),
            "source_id": "synthetic-source", "source_generation": expected + 1, "status": "withdrawn", "reason": "source_unavailable",
            "observed_at": at, "available_at": at, "valid_until": at + 1,
            "control": {"kind": "source_barrier", "asserted_at": at, "clock": {
                "status": "commissioned", "mapping_id": SERVICE_CLOCK["id"], "mapping_digest": SERVICE_CLOCK["digest"],
                "source_epoch": "synthetic-service-epoch", "original_unit": "unix_ns", "original_value": str(at * 1000000000),
                "uncertainty_s": .001}, "expected_source_generation": expected, "next_source_generation": expected + 1,
                "expected_authority_generation": authority, "retired_refs": [publication_ref(ref) for ref in refs]}}


def configuration(**limit_overrides):
    schema_pins = json.loads((Path(__file__).resolve().parents[3] / "contracts/home-live-context/schemas/DIGESTS.json").read_text(encoding="utf-8"))
    work_policy = geometry()["parts"]["work"]["uncertainty"]
    work_policy["age"].pop("origin_at")
    clock = {"mapping_id": CAPTURE_CLOCK["id"], "mapping_digest": CAPTURE_CLOCK["digest"],
             "source_epoch": "synthetic-camera-epoch", "original_unit": "unix_ns", "epoch_origin_s": 0, "max_uncertainty_s": .01}
    control_clock = {"mapping_id": SERVICE_CLOCK["id"], "mapping_digest": SERVICE_CLOCK["digest"],
                     "source_epoch": "synthetic-service-epoch", "original_unit": "unix_ns", "epoch_origin_s": 0, "max_uncertainty_s": .01}
    source = {"producer_id": "synthetic-producer", "producer_epoch": "synthetic-producer-epoch",
              "capture_epoch": "synthetic-camera-epoch", "initial_generation": 1,
              "sensor_roots": ["camera:synthetic-oak"], "capture_clock": clock, "control_clock": control_clock,
              "method_refs": [copy.deepcopy(METHOD), copy.deepcopy(GEOMETRY_METHOD)], "model_refs": [],
              "association_method_refs": [copy.deepcopy(ASSOCIATION_METHOD)], "registration": copy.deepcopy(REGISTRATION),
              "receipt_store_ids": ["synthetic-receipts"], "max_age_s": 10,
              "receipt_component_ids": ["pose", "depth"], "primitive_ids": {"body": [], "protection": []}, "protection_policy_refs": [],
              "part_policies": {name: (work_policy if name == "work" else None) for name in geometry()["parts"]}}
    manifest = {"schema": "home-live-ingress-manifest-1", "version": 1, "identity": copy.deepcopy(SCOPE),
                "handoff_digest": schema_pins["handoff_digest"], "interface_digest": schema_pins["interface_digest"],
                "sources": {"synthetic-source": source},
                "limits": {"max_original_roots": 1024, "max_heads": 128, "max_receipt_bindings": 2048, **limit_overrides}}
    manifest_ref = {"id": "synthetic-manifest", "digest": independent_digest(manifest)}
    MANIFEST.update(manifest_ref)
    return {"schema": "home-live-ingress-config-1", "version": 1, "manifest_ref": manifest_ref, "manifest": manifest}


def grant(token=TOKEN, *, types=None, capabilities=None, operations=None):
    from schema_registry import schema_digest
    types = ["home-live-fact-1", "home-live-geometry-1", "home-context-revocation-1"] if types is None else types
    return {"principal_id": "synthetic-" + hashlib.sha256(token.encode()).hexdigest()[:12], "producer_id": "synthetic-producer",
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(), "scopes": [{"site_id": SCOPE["site_id"], "room_id": SCOPE["room_id"]}],
            "operations": ["read", "event", "revoke"] if operations is None else operations, "payload_types": list(types),
            "revisions": ["synthetic-r1"], "source_ids": ["synthetic-source"], "sensor_roots": ["camera:synthetic-oak"],
            "schema_pins": {name: schema_digest(name, profile=PROFILE) for name in types},
            "live_bindings": [{"manifest_ref": copy.deepcopy(MANIFEST), "source_id": "synthetic-source",
                               "capabilities": ["capture", "control", "read", "revoke"] if capabilities is None else capabilities}]}


def receipt_resolver(store_id, receipt_id):
    if store_id != "synthetic-receipts":
        raise KeyError(store_id)
    capture, component = receipt_id.split(":")
    seq = int(capture.removeprefix("synthetic-capture-"))
    if not 1 <= seq <= 10000 or component not in {"pose", "depth"}:
        raise KeyError(receipt_id)
    root = f"synthetic-frame-{seq}:depth" if component == "depth" else f"synthetic-frame-{seq}:rgb"
    return {"ref": receipt(seq, component), "capture_epoch": "synthetic-camera-epoch", "capture_seq": seq,
            "observation_root_times": {root: NOW + seq - 1}, "sensor_root_bindings": {root: ["camera:synthetic-oak"]}}


class LiveHttp:
    """Exercise the actual loopback handler rather than bypassing admission."""

    def __init__(self, journal):
        self.server = LocalServer(("127.0.0.1", 0), journal)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=False)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        if self.thread.is_alive():
            raise AssertionError("synthetic loopback server did not stop")

    def request(self, method, path, body=None, token="synthetic-live-token"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            headers = {"Authorization": "Bearer " + token}
            if body is not None:
                headers["Content-Type"] = "application/json"
            connection.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
