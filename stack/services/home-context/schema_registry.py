"""Pinned local schema registry with a fail-closed stdlib keyword subset."""
import hashlib
import json
import re
from pathlib import Path
from types import MappingProxyType

from journal import JournalError, canonical, finite


ROOT = Path(__file__).resolve().parents[3]
SERVICE_SCHEMAS = ROOT / "contracts" / "home-context-service" / "schemas"
DYNAMIC_SCHEMAS = ROOT / "contracts" / "home-dynamic-context" / "schemas"
MAP_REHEARSAL_PROFILE = "home-map-rehearsal-synthetic-1"
MAP_REHEARSAL_SCHEMAS = ROOT / "contracts" / "home-map-rehearsal" / "schemas"
ADAPTIVE_SCHEMAS = ROOT / "contracts" / "home-adaptive-experience" / "schemas"
FEEDBACK_SCHEMAS = ROOT / "contracts" / "home-adaptive-feedback" / "schemas"
FEEDBACK_PROFILE = "adaptive-book-feedback-synthetic-1"
REFERENCE_SCHEMAS = ROOT / "contracts" / "home-adaptive-feedback-reference" / "schemas"
REFERENCE_PROFILE = "adaptive-book-feedback-reference-synthetic-1"
G2_SCHEMAS = ROOT / "contracts" / "home-adaptive-feedback-geometry-hold-2" / "schemas"
G2_PROFILE = "adaptive-book-feedback-reference-geometry-hold-synthetic-2"
G2_INTERFACE_DIGEST = "70b48eed3407511c88ca6b36e57f815d49bd1811cf97d84a62303d9df7abd0fb"
LIVE_PROFILE = "home-live-shadow-1"
LIVE_SCHEMAS = ROOT / "contracts" / "home-live-context" / "schemas"
LIVE_VERSIONS = {"home-live-fact-1": 1, "home-live-geometry-1": 1}
LIVE_WORKER_SCHEMAS = ROOT / "contracts" / "home-live-context" / "worker-schemas"
LIVE_SCENE_SCHEMAS = ROOT / "contracts" / "home-live-context" / "scene-schemas"
LIVE_DELIVERY_SCHEMAS = ROOT / "contracts" / "home-live-context" / "delivery-schemas"
LIVE_STABLE_DELIVERY_SCHEMAS = ROOT / "contracts" / "home-live-context" / "stable-delivery-schemas"
LIVE_STABLE_DELIVERY_VERSIONS = {"home-live-shadow-decision-4": 4, "home-live-worker-checkpoint-4": 4,
                                 "home-live-worker-commit-4": 4, "home-live-input-cut-4": 4}
LIVE_STABLE_DELIVERY_PUBLICATIONS = frozenset({"home-live-shadow-decision-4"})
LIVE_LIFECYCLE_SCHEMAS = ROOT / "contracts" / "home-live-context" / "lifecycle-schemas"
LIVE_LIFECYCLE_VERSIONS = {"home-live-shadow-decision-5": 5, "home-live-worker-checkpoint-5": 5,
                           "home-live-worker-commit-5": 5, "home-live-input-cut-5": 5,
                           "home-live-person-lifecycle-1": 1, "home-live-intent-event-2": 2}
LIVE_LIFECYCLE_PUBLICATIONS = frozenset({"home-live-shadow-decision-5", "home-live-person-lifecycle-1", "home-live-intent-event-2"})
LIVE_CONSERVATIVE_DELIVERY_SCHEMAS = ROOT / "contracts" / "home-live-context" / "conservative-delivery-schemas"
LIVE_CONSERVATIVE_DELIVERY_VERSIONS = {"home-live-shadow-decision-6": 6, "home-live-worker-checkpoint-6": 6,
    "home-live-worker-commit-6": 6, "home-live-input-cut-6": 6, "home-live-person-lifecycle-1": 1, "home-live-intent-event-2": 2}
LIVE_CONSERVATIVE_DELIVERY_PUBLICATIONS = frozenset({"home-live-shadow-decision-6", "home-live-person-lifecycle-1", "home-live-intent-event-2"})
LIVE_ROOM_SCHEMAS = ROOT / "contracts" / "home-live-context" / "room-schemas"
LIVE_ROOM_VERSIONS = {"home-live-shadow-decision-8": 8, "home-live-worker-checkpoint-8": 8,
    "home-live-worker-commit-8": 8, "home-live-input-cut-8": 8,
    "home-live-room-output-1": 1, "home-live-room-snapshot-1": 1,
    "home-live-person-lifecycle-1": 1, "home-live-intent-event-2": 2}
LIVE_ROOM_PUBLICATIONS = frozenset({"home-live-shadow-decision-8"})
LIVE_ROOM_CLARIFICATION_SCHEMAS = ROOT / "contracts" / "home-live-context" / "room-clarification-schemas"
LIVE_ROOM_CLARIFICATION_VERSIONS = {"home-live-shadow-decision-9": 9, "home-live-worker-checkpoint-9": 9,
    "home-live-worker-commit-9": 9, "home-live-input-cut-9": 9,
    "home-live-room-output-1": 1, "home-live-room-snapshot-1": 1,
    "home-live-person-lifecycle-1": 1, "home-live-intent-event-2": 2, "home-live-intent-event-3": 3}
LIVE_ROOM_CLARIFICATION_PUBLICATIONS = frozenset({"home-live-shadow-decision-9", "home-live-intent-event-3"})
LIVE_ROOM_MANUAL_SCHEMAS = ROOT / "contracts" / "home-live-context" / "room-manual-schemas"
LIVE_ROOM_MANUAL_VERSIONS = {'home-live-shadow-decision-10': 10, 'home-live-worker-checkpoint-10': 10, 'home-live-worker-commit-10': 10, 'home-live-input-cut-10': 10, 'home-live-room-output-2': 2, 'home-live-room-snapshot-2': 2, 'home-live-manual-policy-1': 1, 'home-live-manual-control-state-1': 1, 'home-live-room-control-status-1': 1, 'home-live-intent-event-4': 4, 'home-live-intent-event-2': 2, 'home-live-intent-event-3': 3, 'home-live-person-lifecycle-1': 1}
LIVE_ROOM_MANUAL_PUBLICATIONS = frozenset({'home-live-shadow-decision-10', 'home-live-intent-event-4'})

LIVE_PC_SUPPORT_SCHEMAS = ROOT / "contracts" / "home-live-context" / "pc-support-schemas"
LIVE_PC_SUPPORT_VERSIONS = {"home-live-shadow-decision-11": 11, "home-live-worker-checkpoint-11": 11,
    "home-live-worker-commit-11": 11, "home-live-input-cut-11": 11, "home-live-pc-support-policy-1": 1,
    "home-live-room-output-1": 1, "home-live-room-snapshot-1": 1,
    "home-live-person-lifecycle-1": 1, "home-live-intent-event-2": 2}
LIVE_PC_SUPPORT_PUBLICATIONS = frozenset({"home-live-shadow-decision-11"})

LIVE_ROOM_SUBSET_SCHEMAS = ROOT / "contracts" / "home-live-context" / "room-subset-schemas"
LIVE_ROOM_SUBSET_VERSIONS = {"home-live-shadow-decision-12": 12, "home-live-worker-checkpoint-12": 12,
    "home-live-worker-commit-12": 12, "home-live-input-cut-12": 12, "home-live-room-policy-2": 2,
    "home-live-pc-support-policy-1": 1, "home-live-room-output-1": 1, "home-live-room-snapshot-1": 1,
    "home-live-person-lifecycle-1": 1, "home-live-intent-event-2": 2}
LIVE_ROOM_SUBSET_PUBLICATIONS = frozenset({"home-live-shadow-decision-12"})

LIVE_DELIVERY_VERSIONS = {"home-live-shadow-decision-3": 3, "home-live-worker-checkpoint-3": 3,
                          "home-live-worker-commit-3": 3, "home-live-input-cut-3": 3}
LIVE_DELIVERY_PUBLICATIONS = frozenset({"home-live-shadow-decision-3"})
LIVE_SCENE_VERSIONS = {"home-live-intent-event-1": 1, "home-live-shadow-decision-2": 2,
                       "home-live-worker-checkpoint-2": 2, "home-live-worker-commit-2": 2,
                       "home-live-input-cut-2": 2}
LIVE_SCENE_PUBLICATIONS = frozenset({"home-live-intent-event-1", "home-live-shadow-decision-2"})
LIVE_WORKER_VERSIONS = {"home-live-engagement-state-1": 1, "home-live-shadow-decision-1": 1,
                        "home-live-worker-checkpoint-1": 1, "home-live-worker-commit-1": 1,
                        "home-live-input-cut-1": 1}
LIVE_WORKER_PUBLICATIONS = frozenset({"home-live-engagement-state-1", "home-live-shadow-decision-1"})
LIVE_HANDOFF_DIGEST = "8345e855397d86c65a2197daa7592dcf4f73dc48fa00659bd588093b297d7d0e"
DYNAMIC_VERSIONS = {
    "home-fact-2": 2, "home-fact-batch-2": 2, "home-geometry-3": 3,
    "home-intent-event-2": 2, "home-engagement-state-3": 3, "home-follow-lease-1": 1,
    "home-lighting-request-3": 3, "home-lighting-policy-decision-2": 2,
    "home-dynamic-geometry-snapshot-1": 1, "home-control-input-cut-1": 1,
    "home-resolved-target-1": 1, "home-lighting-allocation-3": 3,
    "home-lighting-execution-1": 1, "home-lighting-control-decision-1": 1,
    "home-evidence-request-2": 2, "home-evidence-reply-2": 2,
    "home-context-worker-checkpoint-3": 3, "home-context-worker-commit-3": 3,
    "home-context-replay-4": 4,
    "home-acquisition-status-1": 1, "home-intent-application-result-2": 2,
}
ADAPTIVE_NEW_VERSIONS = {
    "home-preference-snapshot-1": 1, "home-experience-goal-1": 1, "home-goal-declaration-1": 1,
    "home-registered-surface-1": 1, "home-lighting-environment-1": 1, "home-lighting-capability-1": 1,
    "home-lighting-outcome-feedback-1": 1, "home-intent-event-3": 3, "home-intent-application-result-3": 3,
    "home-follow-lease-2": 2, "home-lighting-request-4": 4, "home-lighting-policy-decision-3": 3,
    "home-control-input-cut-2": 2, "home-dynamic-geometry-snapshot-2": 2, "home-resolved-target-2": 2,
    "home-lighting-allocation-4": 4, "home-lighting-execution-2": 2, "home-lighting-control-decision-2": 2,
    "home-context-worker-checkpoint-4": 4, "home-context-worker-commit-4": 4, "home-context-replay-5": 5,
}
ADAPTIVE_VERSIONS = {**DYNAMIC_VERSIONS, **ADAPTIVE_NEW_VERSIONS}
ADAPTIVE_TRANSPORT_VERSIONS = {"home-context-snapshot-3": 3}
ADAPTIVE_PUBLICATIONS = frozenset({
    "home-fact-batch-2", "home-geometry-3", "home-engagement-state-3", "home-evidence-request-2",
    "home-evidence-reply-2", "home-acquisition-status-1", "home-preference-snapshot-1",
    "home-experience-goal-1", "home-goal-declaration-1", "home-registered-surface-1",
    "home-lighting-environment-1", "home-lighting-capability-1", "home-intent-event-3",
    "home-intent-application-result-3", "home-lighting-policy-decision-3", "home-lighting-control-decision-2",
})
REGISTERED = {name: SERVICE_SCHEMAS / (name + ".schema.json") for name in (
    "fact-1", "home-intent-event-1", "home-evidence-request-1", "home-evidence-reply-1",
    "home-context-consumer-feedback-1", "home-context-revocation-1", "home-lighting-shadow-decision-1",
    "home-source-health-1", "home-coverage-1", "home-geometry-1", "home-engagement-state-1", "home-relation-hypothesis-1")}
REGISTERED["home-context-1"] = ROOT / "contracts" / "home-context" / "schemas" / "home-context-1.schema.json"
REGISTERED.update({name: DYNAMIC_SCHEMAS / (name + ".schema.json") for name in DYNAMIC_VERSIONS})
MAP_REHEARSAL_REPLACEMENTS = {
    "home-lighting-allocation-3": "home-map-rehearsal-allocation-1",
    "home-lighting-control-decision-1": "home-map-rehearsal-control-decision-1",
    "home-context-worker-checkpoint-3": "home-map-rehearsal-worker-checkpoint-1",
    "home-context-worker-commit-3": "home-map-rehearsal-worker-commit-1",
    "home-context-replay-4": "home-map-rehearsal-replay-1",
}
MAP_REHEARSAL_VERSIONS = {MAP_REHEARSAL_REPLACEMENTS.get(name, name): 1 if name in MAP_REHEARSAL_REPLACEMENTS else version for name, version in DYNAMIC_VERSIONS.items()}
MAP_REHEARSAL_VERSIONS["home-map-rehearsal-binding-1"] = 1
REGISTERED.update({name: MAP_REHEARSAL_SCHEMAS / (name + ".schema.json") for name in MAP_REHEARSAL_VERSIONS if name not in DYNAMIC_VERSIONS})
REGISTERED.update({name: ADAPTIVE_SCHEMAS / (name + ".schema.json") for name in ADAPTIVE_NEW_VERSIONS})
REGISTERED.update({name: ADAPTIVE_SCHEMAS / (name + ".schema.json") for name in ADAPTIVE_TRANSPORT_VERSIONS})
DEPENDENCIES = {"home-context-1": [REGISTERED["home-context-1"].parent / "common.schema.json"]}
KEYWORDS = {"$schema", "$id", "$defs", "$ref", "$comment", "title", "description", "default", "examples",
            "type", "properties", "required", "additionalProperties", "items", "prefixItems", "enum", "const", "minimum", "maximum",
            "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength", "pattern", "minItems", "maxItems",
            "uniqueItems", "propertyNames", "minProperties", "maxProperties", "anyOf", "oneOf", "allOf", "not", "if", "then", "else"}


def schema_digest(payload_type, *, profile=None):
    if profile == LIVE_PROFILE and payload_type in LIVE_PC_SUPPORT_VERSIONS and LIVE_PC_SUPPORT_SCHEMAS.is_dir():
        raw = (LIVE_PC_SUPPORT_SCHEMAS / (payload_type + ".schema.json")).read_bytes().replace(b"\r\n", b"\n")
        if len(raw) > 524288:
            raise ValueError("PC support schema exceeds configured bound")
        return hashlib.sha256(raw).hexdigest()
    if profile == LIVE_PROFILE and payload_type in LIVE_ROOM_SUBSET_VERSIONS and LIVE_ROOM_SUBSET_SCHEMAS.is_dir():
        raw = (LIVE_ROOM_SUBSET_SCHEMAS / (payload_type + ".schema.json")).read_bytes().replace(b"\r\n", b"\n")
        if len(raw) > 524288:
            raise ValueError("room subset schema exceeds configured bound")
        return hashlib.sha256(raw).hexdigest()
    if profile == LIVE_PROFILE and payload_type in LIVE_ROOM_MANUAL_VERSIONS and LIVE_ROOM_MANUAL_SCHEMAS.is_dir():
        raw = (LIVE_ROOM_MANUAL_SCHEMAS / (payload_type + ".schema.json")).read_bytes().replace(b"\r\n", b"\n")
        if len(raw) > 524288:
            raise ValueError("room manual schema exceeds configured bound")
        return hashlib.sha256(raw).hexdigest()
    if profile == LIVE_PROFILE and payload_type in LIVE_ROOM_CLARIFICATION_VERSIONS and LIVE_ROOM_CLARIFICATION_SCHEMAS.is_dir():
        raw = (LIVE_ROOM_CLARIFICATION_SCHEMAS / (payload_type + ".schema.json")).read_bytes().replace(b"\r\n", b"\n")
        if len(raw) > 524288:
            raise ValueError("room clarification schema exceeds configured bound")
        return hashlib.sha256(raw).hexdigest()
    if profile == LIVE_PROFILE and payload_type in LIVE_ROOM_VERSIONS:
        raw = (LIVE_ROOM_SCHEMAS / (payload_type + ".schema.json")).read_bytes().replace(b"\r\n", b"\n")
        if len(raw) > 524288:
            raise ValueError("live room schema exceeds configured bound")
        return hashlib.sha256(raw).hexdigest()
    if profile == LIVE_PROFILE and payload_type in LIVE_CONSERVATIVE_DELIVERY_VERSIONS:
        raw = (LIVE_CONSERVATIVE_DELIVERY_SCHEMAS / (payload_type + ".schema.json")).read_bytes().replace(b"\r\n", b"\n")
        if len(raw) > 524288:
            raise ValueError("live conservative delivery schema exceeds configured bound")
        return hashlib.sha256(raw).hexdigest()
    if profile == LIVE_PROFILE and payload_type in LIVE_LIFECYCLE_VERSIONS:
        raw = (LIVE_LIFECYCLE_SCHEMAS / (payload_type + ".schema.json")).read_bytes().replace(b"\r\n", b"\n")
        if len(raw) > 524288:
            raise ValueError("live lifecycle schema exceeds configured bound")
        return hashlib.sha256(raw).hexdigest()
    if profile == LIVE_PROFILE and payload_type in LIVE_STABLE_DELIVERY_VERSIONS:
        raw = (LIVE_STABLE_DELIVERY_SCHEMAS / (payload_type + ".schema.json")).read_bytes().replace(b"\r\n", b"\n")
        if len(raw) > 524288:
            raise ValueError("stable live delivery schema exceeds configured bound")
        return hashlib.sha256(raw).hexdigest()
    if profile == LIVE_PROFILE and payload_type in LIVE_DELIVERY_VERSIONS:
        raw = (LIVE_DELIVERY_SCHEMAS / (payload_type + ".schema.json")).read_bytes().replace(b"\r\n", b"\n")
        if len(raw) > 524288:
            raise ValueError("live delivery schema exceeds configured bound")
        return hashlib.sha256(raw).hexdigest()
    if profile == LIVE_PROFILE and payload_type in LIVE_SCENE_VERSIONS:
        raw = (LIVE_SCENE_SCHEMAS / (payload_type + ".schema.json")).read_bytes().replace(b"\r\n", b"\n")
        if len(raw) > 524288:
            raise ValueError("live scene schema exceeds configured bound")
        return hashlib.sha256(raw).hexdigest()
    if profile == LIVE_PROFILE and payload_type in LIVE_WORKER_VERSIONS:
        raw = (LIVE_WORKER_SCHEMAS / (payload_type + ".schema.json")).read_bytes().replace(b"\r\n", b"\n")
        if len(raw) > 262144:
            raise ValueError("live worker schema exceeds configured bound")
        return hashlib.sha256(raw).hexdigest()
    if profile == LIVE_PROFILE and payload_type in LIVE_VERSIONS:
        raw = (LIVE_SCHEMAS / (payload_type + ".schema.json")).read_bytes().replace(b"\r\n", b"\n")
        if len(raw) > 262144:
            raise ValueError("live schema exceeds configured bound")
        return hashlib.sha256(raw).hexdigest()
    if profile == G2_PROFILE:
        path = G2_SCHEMAS / (str(payload_type) + ".schema.json")
        if not path.is_file():
            raise ValueError("payload type has no registered G2 schema")
        raw = path.read_bytes()
        if len(raw) > 524288:
            raise ValueError("G2 schema exceeds configured size limit")
        return hashlib.sha256(raw).hexdigest()
    path = REGISTERED.get(payload_type)
    if path is None and (FEEDBACK_SCHEMAS / (str(payload_type) + ".schema.json")).is_file():
        path = FEEDBACK_SCHEMAS / (payload_type + ".schema.json")
    if path is None and (REFERENCE_SCHEMAS / (str(payload_type) + ".schema.json")).is_file():
        path = REFERENCE_SCHEMAS / (payload_type + ".schema.json")
    if path is None or not path.is_file():
        raise ValueError("payload type has no registered local schema: " + payload_type)
    def file_digest(document):
        raw = document.read_bytes().replace(b"\r\n", b"\n")
        if len(raw) > 262144:
            raise ValueError("schema exceeds configured size limit")
        return hashlib.sha256(raw).hexdigest()
    dependencies = DEPENDENCIES.get(payload_type, [])
    if dependencies:
        graph = {p.relative_to(ROOT).as_posix(): file_digest(p) for p in [path, *dependencies]}
        return hashlib.sha256(canonical(graph).encode("utf-8")).hexdigest()
    return file_digest(path)


class SchemaRegistry:
    def __init__(self, grants, *, profile="legacy"):
        self.profile = profile
        self.schemas = {}
        self.documents = {}
        self.payload_versions = {}
        self.interface_digest = None
        self.publication_families = frozenset()
        if profile == LIVE_PROFILE:
            raw = (LIVE_SCHEMAS / "DIGESTS.json").read_bytes()
            if len(raw) > 65536:
                raise ValueError("live manifest exceeds configured bound")
            manifest = json.loads(raw)
            if (set(manifest) != {"profile", "stage", "handoff_digest", "interface_digest", "payload_versions", "publication_families", "schemas"}
                    or manifest["profile"] != LIVE_PROFILE or manifest["stage"] != "producer-ingress-1"
                    or manifest["handoff_digest"] != LIVE_HANDOFF_DIGEST
                    or manifest["payload_versions"] != LIVE_VERSIONS
                    or manifest["publication_families"] != list(LIVE_VERSIONS)
                    or set(manifest["schemas"]) != {name + ".schema.json" for name in LIVE_VERSIONS}):
                raise ValueError("exact initial live schema manifest required")
            graph = {}
            for name in LIVE_VERSIONS:
                path = LIVE_SCHEMAS / (name + ".schema.json")
                if schema_digest(name, profile=LIVE_PROFILE) != manifest["schemas"][path.name]:
                    raise ValueError("live schema pin mismatch")
                document = json.loads(path.read_text(encoding="utf-8"))
                graph[name] = document
                self.documents[path] = document
                self.schemas[name] = (document, path)
            self.interface_digest = hashlib.sha256(canonical(graph, -32).encode("utf-8")).hexdigest()
            if self.interface_digest != manifest["interface_digest"]:
                raise ValueError("live interface digest mismatch")
            self.payload_versions = dict(LIVE_VERSIONS)
            self.publication_families = frozenset(LIVE_VERSIONS)
            self.worker_interface_digest = None
            worker_path = LIVE_WORKER_SCHEMAS / "DIGESTS.json"
            if worker_path.is_file():
                worker_raw = worker_path.read_bytes()
                if len(worker_raw) > 65536:
                    raise ValueError("live worker schema manifest exceeds configured bound")
                worker = json.loads(worker_raw)
                if (set(worker) != {"profile", "stage", "base_interface_digest", "handoff_digest", "interface_digest",
                                    "payload_versions", "publication_families", "schemas"}
                        or worker["profile"] != LIVE_PROFILE or worker["stage"] != "engagement-worker-1"
                        or worker["base_interface_digest"] != self.interface_digest
                        or worker["handoff_digest"] != LIVE_HANDOFF_DIGEST
                        or worker["payload_versions"] != LIVE_WORKER_VERSIONS
                        or set(worker["publication_families"]) != LIVE_WORKER_PUBLICATIONS
                        or set(worker["schemas"]) != {name + ".schema.json" for name in LIVE_WORKER_VERSIONS}):
                    raise ValueError("exact additive live worker schema manifest required")
                worker_graph = {}
                for name in LIVE_WORKER_VERSIONS:
                    path = LIVE_WORKER_SCHEMAS / (name + ".schema.json")
                    if schema_digest(name, profile=LIVE_PROFILE) != worker["schemas"][path.name]:
                        raise ValueError("live worker schema pin mismatch")
                    document = json.loads(path.read_text(encoding="utf-8"))
                    if document.get("$id") != path.name or document["properties"]["schema"].get("const") != name or document["properties"]["version"].get("const") != 1:
                        raise ValueError("live worker schema identity/version differs")
                    worker_graph[name] = document
                    self.documents[path] = document
                    self.schemas[name] = (document, path)
                self.worker_interface_digest = hashlib.sha256(canonical(worker_graph, -32).encode("utf-8")).hexdigest()
                if self.worker_interface_digest != worker["interface_digest"]:
                    raise ValueError("live worker interface digest mismatch")
                self.worker_schema_pins = dict(worker["schemas"])
                self.payload_versions.update(LIVE_WORKER_VERSIONS)
                self.publication_families |= LIVE_WORKER_PUBLICATIONS
            self.scene_interface_digest = None
            scene_path = LIVE_SCENE_SCHEMAS / "DIGESTS.json"
            if scene_path.is_file():
                scene_raw = scene_path.read_bytes()
                if len(scene_raw) > 65536:
                    raise ValueError("live scene manifest exceeds configured bound")
                scene = json.loads(scene_raw)
                if (set(scene) != {"profile", "stage", "base_interface_digest", "worker_interface_digest", "handoff_digest",
                                  "interface_digest", "payload_versions", "publication_families", "schemas"}
                        or scene["profile"] != LIVE_PROFILE or scene["stage"] != "profile-scene-worker-1"
                        or scene["base_interface_digest"] != self.interface_digest
                        or scene["worker_interface_digest"] != self.worker_interface_digest
                        or scene["handoff_digest"] != LIVE_HANDOFF_DIGEST
                        or scene["payload_versions"] != LIVE_SCENE_VERSIONS
                        or set(scene["publication_families"]) != LIVE_SCENE_PUBLICATIONS
                        or set(scene["schemas"]) != {name + ".schema.json" for name in LIVE_SCENE_VERSIONS}):
                    raise ValueError("exact additive live scene schema manifest required")
                scene_graph = {}
                for name, version in LIVE_SCENE_VERSIONS.items():
                    path = LIVE_SCENE_SCHEMAS / (name + ".schema.json")
                    if schema_digest(name, profile=LIVE_PROFILE) != scene["schemas"][path.name]:
                        raise ValueError("live scene schema pin mismatch")
                    document = json.loads(path.read_text(encoding="utf-8"))
                    if (document.get("$id") != path.name or document["properties"]["schema"].get("const") != name
                            or document["properties"]["version"].get("const") != version):
                        raise ValueError("live scene schema identity/version differs")
                    scene_graph[name] = document
                    self.documents[path] = document
                    self.schemas[name] = (document, path)
                self.scene_interface_digest = hashlib.sha256(canonical(scene_graph, -32).encode("utf-8")).hexdigest()
                if self.scene_interface_digest != scene["interface_digest"]:
                    raise ValueError("live scene interface digest mismatch")
                self.scene_schema_pins = dict(scene["schemas"])
                self.payload_versions.update(LIVE_SCENE_VERSIONS)
                self.publication_families |= LIVE_SCENE_PUBLICATIONS
            self.delivery_interface_digest = None
            delivery_path = LIVE_DELIVERY_SCHEMAS / "DIGESTS.json"
            if delivery_path.is_file():
                delivery_raw = delivery_path.read_bytes()
                if len(delivery_raw) > 65536:
                    raise ValueError("live delivery manifest exceeds configured bound")
                delivery = json.loads(delivery_raw)
                if (set(delivery) != {"profile", "stage", "base_interface_digest", "worker_interface_digest", "scene_interface_digest", "handoff_digest",
                                     "interface_digest", "payload_versions", "publication_families", "schemas"}
                        or delivery["profile"] != LIVE_PROFILE or delivery["stage"] != "stationary-task-delivery-1"
                        or delivery["base_interface_digest"] != self.interface_digest
                        or delivery["worker_interface_digest"] != self.worker_interface_digest
                        or delivery["scene_interface_digest"] != self.scene_interface_digest
                        or delivery["handoff_digest"] != LIVE_HANDOFF_DIGEST
                        or delivery["payload_versions"] != LIVE_DELIVERY_VERSIONS
                        or set(delivery["publication_families"]) != LIVE_DELIVERY_PUBLICATIONS
                        or set(delivery["schemas"]) != {name + ".schema.json" for name in LIVE_DELIVERY_VERSIONS}):
                    raise ValueError("exact additive live delivery schema manifest required")
                delivery_graph = {}
                for name, version in LIVE_DELIVERY_VERSIONS.items():
                    path = LIVE_DELIVERY_SCHEMAS / (name + ".schema.json")
                    if schema_digest(name, profile=LIVE_PROFILE) != delivery["schemas"][path.name]:
                        raise ValueError("live delivery schema pin mismatch")
                    document = json.loads(path.read_text(encoding="utf-8"))
                    if (document.get("$id") != path.name or document["properties"]["schema"].get("const") != name
                            or document["properties"]["version"].get("const") != version):
                        raise ValueError("live delivery schema identity/version differs")
                    delivery_graph[name] = document
                    self.documents[path] = document
                    self.schemas[name] = (document, path)
                self.delivery_interface_digest = hashlib.sha256(canonical(delivery_graph, -32).encode("utf-8")).hexdigest()
                if self.delivery_interface_digest != delivery["interface_digest"]:
                    raise ValueError("live delivery interface digest mismatch")
                self.delivery_schema_pins = dict(delivery["schemas"])
                self.payload_versions.update(LIVE_DELIVERY_VERSIONS)
                self.publication_families |= LIVE_DELIVERY_PUBLICATIONS
            self.stable_delivery_interface_digest = None
            stable_path = LIVE_STABLE_DELIVERY_SCHEMAS / "DIGESTS.json"
            if stable_path.is_file():
                raw = stable_path.read_bytes()
                if len(raw) > 65536:
                    raise ValueError("stable delivery manifest exceeds configured bound")
                stable = json.loads(raw)
                if (set(stable) != {"profile", "stage", "base_interface_digest", "worker_interface_digest", "scene_interface_digest", "handoff_digest", "interface_digest", "payload_versions", "publication_families", "schemas"}
                        or stable["profile"] != LIVE_PROFILE or stable["stage"] != "stable-stationary-task-delivery-1"
                        or stable["base_interface_digest"] != self.interface_digest
                        or stable["worker_interface_digest"] != self.worker_interface_digest
                        or stable["scene_interface_digest"] != self.scene_interface_digest
                        or stable["handoff_digest"] != LIVE_HANDOFF_DIGEST
                        or stable["payload_versions"] != LIVE_STABLE_DELIVERY_VERSIONS
                        or set(stable["publication_families"]) != LIVE_STABLE_DELIVERY_PUBLICATIONS
                        or set(stable["schemas"]) != {name + ".schema.json" for name in LIVE_STABLE_DELIVERY_VERSIONS}):
                    raise ValueError("exact additive stable delivery manifest required")
                graph = {}
                for name, version in LIVE_STABLE_DELIVERY_VERSIONS.items():
                    path = LIVE_STABLE_DELIVERY_SCHEMAS / (name + ".schema.json")
                    if schema_digest(name, profile=LIVE_PROFILE) != stable["schemas"][path.name]:
                        raise ValueError("stable delivery schema pin mismatch")
                    document = json.loads(path.read_text(encoding="utf-8"))
                    if (document.get("$id") != path.name or document["properties"]["schema"].get("const") != name
                            or document["properties"]["version"].get("const") != version):
                        raise ValueError("stable delivery schema identity/version differs")
                    graph[name] = document
                    self.documents[path] = document
                    self.schemas[name] = (document, path)
                self.stable_delivery_interface_digest = hashlib.sha256(canonical(graph, -32).encode("utf-8")).hexdigest()
                if self.stable_delivery_interface_digest != stable["interface_digest"]:
                    raise ValueError("stable delivery interface digest mismatch")
                self.stable_delivery_schema_pins = dict(stable["schemas"])
                self.payload_versions.update(LIVE_STABLE_DELIVERY_VERSIONS)
                self.publication_families |= LIVE_STABLE_DELIVERY_PUBLICATIONS
            self.lifecycle_interface_digest = None
            lifecycle_path = LIVE_LIFECYCLE_SCHEMAS / "DIGESTS.json"
            if lifecycle_path.is_file():
                raw = lifecycle_path.read_bytes()
                if len(raw) > 65536:
                    raise ValueError("lifecycle manifest exceeds configured bound")
                lifecycle = json.loads(raw)
                if (set(lifecycle) != {"profile", "stage", "base_interface_digest", "worker_interface_digest", "scene_interface_digest", "handoff_digest", "interface_digest", "payload_versions", "publication_families", "schemas"}
                        or lifecycle["profile"] != LIVE_PROFILE or lifecycle["stage"] != "scene-correction-person-lifecycle-1"
                        or lifecycle["base_interface_digest"] != self.interface_digest
                        or lifecycle["worker_interface_digest"] != self.worker_interface_digest
                        or lifecycle["scene_interface_digest"] != self.scene_interface_digest
                        or lifecycle["handoff_digest"] != LIVE_HANDOFF_DIGEST
                        or lifecycle["payload_versions"] != LIVE_LIFECYCLE_VERSIONS
                        or set(lifecycle["publication_families"]) != LIVE_LIFECYCLE_PUBLICATIONS
                        or set(lifecycle["schemas"]) != {name + ".schema.json" for name in LIVE_LIFECYCLE_VERSIONS}):
                    raise ValueError("exact additive lifecycle manifest required")
                graph = {}
                for name, version in LIVE_LIFECYCLE_VERSIONS.items():
                    path = LIVE_LIFECYCLE_SCHEMAS / (name + ".schema.json")
                    if schema_digest(name, profile=LIVE_PROFILE) != lifecycle["schemas"][path.name]:
                        raise ValueError("lifecycle schema pin mismatch")
                    document = json.loads(path.read_text(encoding="utf-8"))
                    if (document.get("$id") != path.name or document["properties"]["schema"].get("const") != name
                            or document["properties"]["version"].get("const") != version):
                        raise ValueError("lifecycle schema identity/version differs")
                    graph[name] = document
                    self.documents[path] = document
                    self.schemas[name] = (document, path)
                self.lifecycle_interface_digest = hashlib.sha256(canonical(graph, -32).encode("utf-8")).hexdigest()
                if self.lifecycle_interface_digest != lifecycle["interface_digest"]:
                    raise ValueError("lifecycle interface digest mismatch")
                self.lifecycle_schema_pins = dict(lifecycle["schemas"])
                self.payload_versions.update(LIVE_LIFECYCLE_VERSIONS)
                self.publication_families |= LIVE_LIFECYCLE_PUBLICATIONS
            self.conservative_delivery_interface_digest = None
            conservative_delivery_path = LIVE_CONSERVATIVE_DELIVERY_SCHEMAS / "DIGESTS.json"
            if conservative_delivery_path.is_file():
                raw = conservative_delivery_path.read_bytes()
                if len(raw) > 65536:
                    raise ValueError("conservative_delivery manifest exceeds configured bound")
                conservative_delivery = json.loads(raw)
                if (set(conservative_delivery) != {"profile", "stage", "base_interface_digest", "worker_interface_digest", "scene_interface_digest", "handoff_digest", "interface_digest", "payload_versions", "publication_families", "schemas"}
                        or conservative_delivery["profile"] != LIVE_PROFILE or conservative_delivery["stage"] != "conservative-delivery-causal-repair-1"
                        or conservative_delivery["base_interface_digest"] != self.interface_digest
                        or conservative_delivery["worker_interface_digest"] != self.worker_interface_digest
                        or conservative_delivery["scene_interface_digest"] != self.scene_interface_digest
                        or conservative_delivery["handoff_digest"] != LIVE_HANDOFF_DIGEST
                        or conservative_delivery["payload_versions"] != LIVE_CONSERVATIVE_DELIVERY_VERSIONS
                        or set(conservative_delivery["publication_families"]) != LIVE_CONSERVATIVE_DELIVERY_PUBLICATIONS
                        or set(conservative_delivery["schemas"]) != {name + ".schema.json" for name in LIVE_CONSERVATIVE_DELIVERY_VERSIONS}):
                    raise ValueError("exact additive conservative_delivery manifest required")
                graph = {}
                for name, version in LIVE_CONSERVATIVE_DELIVERY_VERSIONS.items():
                    path = LIVE_CONSERVATIVE_DELIVERY_SCHEMAS / (name + ".schema.json")
                    if schema_digest(name, profile=LIVE_PROFILE) != conservative_delivery["schemas"][path.name]:
                        raise ValueError("conservative_delivery schema pin mismatch")
                    document = json.loads(path.read_text(encoding="utf-8"))
                    if (document.get("$id") != path.name or document["properties"]["schema"].get("const") != name
                            or document["properties"]["version"].get("const") != version):
                        raise ValueError("conservative_delivery schema identity/version differs")
                    graph[name] = document
                    self.documents[path] = document
                    self.schemas[name] = (document, path)
                self.conservative_delivery_interface_digest = hashlib.sha256(canonical(graph, -32).encode("utf-8")).hexdigest()
                if self.conservative_delivery_interface_digest != conservative_delivery["interface_digest"]:
                    raise ValueError("conservative_delivery interface digest mismatch")
                self.conservative_delivery_schema_pins = dict(conservative_delivery["schemas"])
                self.payload_versions.update(LIVE_CONSERVATIVE_DELIVERY_VERSIONS)
                self.publication_families |= LIVE_CONSERVATIVE_DELIVERY_PUBLICATIONS
            self.room_interface_digest = None
            self.room_schema_pins = {}
            room_path = LIVE_ROOM_SCHEMAS / "DIGESTS.json"
            if room_path.is_file():
                raw = room_path.read_bytes()
                if len(raw) > 65536:
                    raise ValueError("room manifest exceeds configured bound")
                room = json.loads(raw)
                if (set(room) != {"profile", "stage", "base_interface_digest", "worker_interface_digest", "scene_interface_digest", "handoff_digest", "interface_digest", "payload_versions", "publication_families", "schemas", "conservative_delivery_interface_digest"}
                        or room["profile"] != LIVE_PROFILE or room["stage"] != "controlled-room-delivery-1"
                        or room["base_interface_digest"] != self.interface_digest
                        or room["worker_interface_digest"] != self.worker_interface_digest
                        or room["scene_interface_digest"] != self.scene_interface_digest
                        or room["conservative_delivery_interface_digest"] != self.conservative_delivery_interface_digest
                        or room["handoff_digest"] != LIVE_HANDOFF_DIGEST
                        or room["payload_versions"] != LIVE_ROOM_VERSIONS
                        or set(room["publication_families"]) != LIVE_ROOM_PUBLICATIONS
                        or set(room["schemas"]) != {name + ".schema.json" for name in LIVE_ROOM_VERSIONS}):
                    raise ValueError("exact additive room manifest required")
                graph = {}
                for name, version in LIVE_ROOM_VERSIONS.items():
                    path = LIVE_ROOM_SCHEMAS / (name + ".schema.json")
                    if schema_digest(name, profile=LIVE_PROFILE) != room["schemas"][path.name]:
                        raise ValueError("room schema pin mismatch")
                    document = json.loads(path.read_text(encoding="utf-8"))
                    if (document.get("$id") != path.name or document["properties"]["schema"].get("const") != name
                            or document["properties"]["version"].get("const") != version):
                        raise ValueError("room schema identity/version differs")
                    graph[name] = document
                    self.documents[path] = document
                    self.schemas[name] = (document, path)
                self.room_interface_digest = hashlib.sha256(canonical(graph, -32).encode("utf-8")).hexdigest()
                if self.room_interface_digest != room["interface_digest"]:
                    raise ValueError("room interface digest mismatch")
                self.room_schema_pins = dict(room["schemas"])
                self.payload_versions.update(LIVE_ROOM_VERSIONS)
                self.publication_families |= LIVE_ROOM_PUBLICATIONS
            self.room_clarification_interface_digest = None
            self.room_clarification_schema_pins = {}
            room_clarification_path = LIVE_ROOM_CLARIFICATION_SCHEMAS / "DIGESTS.json"
            if room_clarification_path.is_file():
                raw = room_clarification_path.read_bytes()
                if len(raw) > 65536:
                    raise ValueError("room clarification manifest exceeds configured bound")
                combined = json.loads(raw)
                expected_keys = {"profile", "stage", "base_interface_digest", "worker_interface_digest", "scene_interface_digest",
                    "handoff_digest", "interface_digest", "payload_versions", "publication_families", "schemas",
                    "conservative_delivery_interface_digest", "room_interface_digest"}
                if (set(combined) != expected_keys or combined["profile"] != LIVE_PROFILE
                        or combined["stage"] != "controlled-room-clarification-1"
                        or combined["base_interface_digest"] != self.interface_digest
                        or combined["worker_interface_digest"] != self.worker_interface_digest
                        or combined["scene_interface_digest"] != self.scene_interface_digest
                        or combined["conservative_delivery_interface_digest"] != self.conservative_delivery_interface_digest
                        or combined["room_interface_digest"] != self.room_interface_digest
                        or combined["handoff_digest"] != LIVE_HANDOFF_DIGEST
                        or combined["payload_versions"] != LIVE_ROOM_CLARIFICATION_VERSIONS
                        or set(combined["publication_families"]) != LIVE_ROOM_CLARIFICATION_PUBLICATIONS
                        or set(combined["schemas"]) != {name + ".schema.json" for name in LIVE_ROOM_CLARIFICATION_VERSIONS}):
                    raise ValueError("exact additive room clarification manifest required")
                graph = {}
                for name, version in LIVE_ROOM_CLARIFICATION_VERSIONS.items():
                    path = LIVE_ROOM_CLARIFICATION_SCHEMAS / (name + ".schema.json")
                    if schema_digest(name, profile=LIVE_PROFILE) != combined["schemas"][path.name]:
                        raise ValueError("room clarification schema pin mismatch")
                    document = json.loads(path.read_text(encoding="utf-8"))
                    if (document.get("$id") != path.name or document["properties"]["schema"].get("const") != name
                            or document["properties"]["version"].get("const") != version):
                        raise ValueError("room clarification schema identity/version differs")
                    graph[name] = document
                    self.documents[path] = document
                    self.schemas[name] = (document, path)
                self.room_clarification_interface_digest = hashlib.sha256(canonical(graph, -32).encode("utf-8")).hexdigest()
                if self.room_clarification_interface_digest != combined["interface_digest"]:
                    raise ValueError("room clarification interface digest mismatch")
                self.room_clarification_schema_pins = dict(combined["schemas"])
                self.payload_versions.update(LIVE_ROOM_CLARIFICATION_VERSIONS)
                self.publication_families |= LIVE_ROOM_CLARIFICATION_PUBLICATIONS
            self.room_manual_interface_digest = None
            self.room_manual_schema_pins = {}
            room_manual_path = LIVE_ROOM_MANUAL_SCHEMAS / "DIGESTS.json"
            if room_manual_path.is_file():
                raw = room_manual_path.read_bytes()
                if len(raw) > 65536:
                    raise ValueError("room manual manifest exceeds configured bound")
                combined = json.loads(raw)
                expected_keys = {"profile", "stage", "base_interface_digest", "worker_interface_digest", "scene_interface_digest",
                    "handoff_digest", "interface_digest", "payload_versions", "publication_families", "schemas",
                    "conservative_delivery_interface_digest", "room_interface_digest", "room_clarification_interface_digest"}
                if (set(combined) != expected_keys or combined["profile"] != LIVE_PROFILE
                        or combined["stage"] != "controlled-room-manual-1"
                        or combined["base_interface_digest"] != self.interface_digest
                        or combined["worker_interface_digest"] != self.worker_interface_digest
                        or combined["scene_interface_digest"] != self.scene_interface_digest
                        or combined["conservative_delivery_interface_digest"] != self.conservative_delivery_interface_digest
                        or combined["room_interface_digest"] != self.room_interface_digest
                        or combined["room_clarification_interface_digest"] != self.room_clarification_interface_digest
                        or combined["handoff_digest"] != LIVE_HANDOFF_DIGEST
                        or combined["payload_versions"] != LIVE_ROOM_MANUAL_VERSIONS
                        or set(combined["publication_families"]) != LIVE_ROOM_MANUAL_PUBLICATIONS
                        or set(combined["schemas"]) != {name + ".schema.json" for name in LIVE_ROOM_MANUAL_VERSIONS}):
                    raise ValueError("exact additive room manual manifest required")
                graph = {}
                for name, version in LIVE_ROOM_MANUAL_VERSIONS.items():
                    path = LIVE_ROOM_MANUAL_SCHEMAS / (name + ".schema.json")
                    if schema_digest(name, profile=LIVE_PROFILE) != combined["schemas"][path.name]:
                        raise ValueError("room manual schema pin mismatch")
                    document = json.loads(path.read_text(encoding="utf-8"))
                    if (document.get("$id") != path.name or document["properties"]["schema"].get("const") != name
                            or document["properties"]["version"].get("const") != version):
                        raise ValueError("room manual schema identity/version differs")
                    graph[name] = document
                    self.documents[path] = document
                    self.schemas[name] = (document, path)
                self.room_manual_interface_digest = hashlib.sha256(canonical(graph, -32).encode("utf-8")).hexdigest()
                if self.room_manual_interface_digest != combined["interface_digest"]:
                    raise ValueError("room manual interface digest mismatch")
                self.room_manual_schema_pins = dict(combined["schemas"])
                self.payload_versions.update(LIVE_ROOM_MANUAL_VERSIONS)
                self.publication_families |= LIVE_ROOM_MANUAL_PUBLICATIONS
            self.pc_support_interface_digest = None
            self.pc_support_schema_pins = {}
            pc_support_path = LIVE_PC_SUPPORT_SCHEMAS / "DIGESTS.json"
            if pc_support_path.is_file():
                raw = pc_support_path.read_bytes()
                if len(raw) > 65536:
                    raise ValueError("room manifest exceeds configured bound")
                pc_support = json.loads(raw)
                if (set(pc_support) != {"profile", "stage", "base_interface_digest", "worker_interface_digest", "scene_interface_digest", "handoff_digest", "interface_digest", "payload_versions", "publication_families", "schemas", "conservative_delivery_interface_digest", "room_interface_digest"}
                        or pc_support["profile"] != LIVE_PROFILE or pc_support["stage"] != "qualified-pc-room-support-1"
                        or pc_support["base_interface_digest"] != self.interface_digest
                        or pc_support["worker_interface_digest"] != self.worker_interface_digest
                        or pc_support["scene_interface_digest"] != self.scene_interface_digest
                        or pc_support["conservative_delivery_interface_digest"] != self.conservative_delivery_interface_digest
                        or pc_support["room_interface_digest"] != self.room_interface_digest
                        or pc_support["handoff_digest"] != LIVE_HANDOFF_DIGEST
                        or pc_support["payload_versions"] != LIVE_PC_SUPPORT_VERSIONS
                        or set(pc_support["publication_families"]) != LIVE_PC_SUPPORT_PUBLICATIONS
                        or set(pc_support["schemas"]) != {name + ".schema.json" for name in LIVE_PC_SUPPORT_VERSIONS}):
                    raise ValueError("exact additive room manifest required")
                graph = {}
                for name, version in LIVE_PC_SUPPORT_VERSIONS.items():
                    path = LIVE_PC_SUPPORT_SCHEMAS / (name + ".schema.json")
                    if schema_digest(name, profile=LIVE_PROFILE) != pc_support["schemas"][path.name]:
                        raise ValueError("room schema pin mismatch")
                    document = json.loads(path.read_text(encoding="utf-8"))
                    if (document.get("$id") != path.name or document["properties"]["schema"].get("const") != name
                            or document["properties"]["version"].get("const") != version):
                        raise ValueError("room schema identity/version differs")
                    graph[name] = document
                    self.documents[path] = document
                    self.schemas[name] = (document, path)
                self.pc_support_interface_digest = hashlib.sha256(canonical(graph, -32).encode("utf-8")).hexdigest()
                if self.pc_support_interface_digest != pc_support["interface_digest"]:
                    raise ValueError("room interface digest mismatch")
                self.pc_support_schema_pins = dict(pc_support["schemas"])
                self.payload_versions.update(LIVE_PC_SUPPORT_VERSIONS)
                self.publication_families |= LIVE_PC_SUPPORT_PUBLICATIONS
            self.room_subset_interface_digest = None
            self.room_subset_schema_pins = {}
            room_subset_path = LIVE_ROOM_SUBSET_SCHEMAS / "DIGESTS.json"
            if room_subset_path.is_file():
                raw = room_subset_path.read_bytes()
                if len(raw) > 65536:
                    raise ValueError("room subset manifest exceeds configured bound")
                subset = json.loads(raw)
                if (set(subset) != {"profile", "stage", "base_interface_digest", "worker_interface_digest", "scene_interface_digest", "handoff_digest", "interface_digest", "payload_versions", "publication_families", "schemas", "conservative_delivery_interface_digest", "room_interface_digest", "pc_support_interface_digest"}
                        or subset["profile"] != LIVE_PROFILE or subset["stage"] != "assessed-room-subset-1"
                        or subset["base_interface_digest"] != self.interface_digest
                        or subset["worker_interface_digest"] != self.worker_interface_digest
                        or subset["scene_interface_digest"] != self.scene_interface_digest
                        or subset["conservative_delivery_interface_digest"] != self.conservative_delivery_interface_digest
                        or subset["room_interface_digest"] != self.room_interface_digest
                        or subset["pc_support_interface_digest"] != self.pc_support_interface_digest
                        or subset["handoff_digest"] != LIVE_HANDOFF_DIGEST
                        or subset["payload_versions"] != LIVE_ROOM_SUBSET_VERSIONS
                        or set(subset["publication_families"]) != LIVE_ROOM_SUBSET_PUBLICATIONS
                        or set(subset["schemas"]) != {name + ".schema.json" for name in LIVE_ROOM_SUBSET_VERSIONS}):
                    raise ValueError("exact additive assessed room subset manifest required")
                graph = {}
                for name, version in LIVE_ROOM_SUBSET_VERSIONS.items():
                    path = LIVE_ROOM_SUBSET_SCHEMAS / (name + ".schema.json")
                    schema_raw = path.read_bytes().replace(b"\r\n", b"\n")
                    if (len(schema_raw) > 524288 or hashlib.sha256(schema_raw).hexdigest() != subset["schemas"][path.name]
                            or schema_digest(name, profile=LIVE_PROFILE) != subset["schemas"][path.name]):
                        raise ValueError("room subset schema pin mismatch")
                    document = json.loads(schema_raw)
                    if (document.get("$id") != path.name or document["properties"]["schema"].get("const") != name
                            or document["properties"]["version"].get("const") != version):
                        raise ValueError("room subset schema identity/version differs")
                    graph[name] = document
                    if name in self.schemas:
                        # The five shared leaves must be exactly the existing
                        # pinned source bytes, not a reinterpretation.
                        old_document, old_path = self.schemas[name]
                        if schema_raw != old_path.read_bytes().replace(b"\r\n", b"\n"):
                            raise ValueError("room subset shared schema changed")
                    else:
                        self.schemas[name] = (document, path)
                    self.documents[path] = document
                self.room_subset_interface_digest = hashlib.sha256(canonical(graph, -32).encode("utf-8")).hexdigest()
                if self.room_subset_interface_digest != subset["interface_digest"]:
                    raise ValueError("room subset interface digest mismatch")
                self.room_subset_schema_pins = dict(subset["schemas"])
                self.payload_versions.update(LIVE_ROOM_SUBSET_VERSIONS)
                self.publication_families |= LIVE_ROOM_SUBSET_PUBLICATIONS
        elif profile == MAP_REHEARSAL_PROFILE:
            manifest = json.loads((MAP_REHEARSAL_SCHEMAS / "DIGESTS.json").read_bytes())
            if (set(manifest) != {"profile", "interface_digest", "payload_versions", "schemas"}
                    or manifest["profile"] != profile or manifest["payload_versions"] != MAP_REHEARSAL_VERSIONS
                    or set(manifest["schemas"]) != {n + ".schema.json" for n in MAP_REHEARSAL_VERSIONS}):
                raise ValueError("exact map rehearsal manifest required")
            graph = {}
            for name in MAP_REHEARSAL_VERSIONS:
                path = MAP_REHEARSAL_SCHEMAS / (name + ".schema.json")
                raw_schema = path.read_bytes().replace(b"\r\n", b"\n")
                if len(raw_schema) > 262144 or hashlib.sha256(raw_schema).hexdigest() != manifest["schemas"][path.name]:
                    raise ValueError("map rehearsal schema pin mismatch")
                if name in DYNAMIC_VERSIONS and raw_schema != (DYNAMIC_SCHEMAS / path.name).read_bytes().replace(b"\r\n", b"\n"):
                    raise ValueError("reused dynamic leaf changed")
                document = json.loads(raw_schema)
                self.documents[path] = document
                self.schemas[name] = (document, path)
                graph[name] = document
            self.interface_digest = hashlib.sha256(canonical(graph).encode("utf-8")).hexdigest()
            if self.interface_digest != manifest["interface_digest"]:
                raise ValueError("map rehearsal interface digest mismatch")
            self.payload_versions = dict(MAP_REHEARSAL_VERSIONS)
        elif profile == "dynamic-book-synthetic-1":
            raw = (DYNAMIC_SCHEMAS / "DIGESTS.json").read_bytes()
            if len(raw) > 65536:
                raise ValueError("dynamic manifest exceeds configured bound")
            manifest = json.loads(raw)
            if (set(manifest) != {"interface_digest", "payload_versions", "schemas"}
                    or manifest["payload_versions"] != DYNAMIC_VERSIONS
                    or set(manifest["schemas"]) != {n + ".schema.json" for n in DYNAMIC_VERSIONS}):
                raise ValueError("exact dynamic schema manifest required")
            graph = {}
            for name in DYNAMIC_VERSIONS:
                if schema_digest(name) != manifest["schemas"][name + ".schema.json"]:
                    raise ValueError("dynamic manifest does not match registered schema bytes")
                path = REGISTERED[name]
                document = json.loads(path.read_text(encoding="utf-8"))
                self.documents[path] = document
                self.schemas[name] = (document, path)
                graph[name] = document
            self.interface_digest = hashlib.sha256(canonical(graph).encode("utf-8")).hexdigest()
            if self.interface_digest != manifest["interface_digest"]:
                raise ValueError("dynamic interface digest mismatch")
            self.payload_versions = dict(DYNAMIC_VERSIONS)
        elif profile == "adaptive-book-synthetic-1":
            raw = (ADAPTIVE_SCHEMAS / "DIGESTS.json").read_bytes()
            if len(raw) > 65536:
                raise ValueError("adaptive manifest exceeds configured bound")
            manifest = json.loads(raw)
            required = {"profile", "interface_digest", "base_interface_digest", "payload_versions",
                        "new_payload_versions", "transport_versions", "publication_families", "schemas"}
            if (set(manifest) != required or manifest["profile"] != profile
                    or manifest["payload_versions"] != ADAPTIVE_VERSIONS
                    or manifest["new_payload_versions"] != ADAPTIVE_NEW_VERSIONS
                    or manifest["transport_versions"] != ADAPTIVE_TRANSPORT_VERSIONS
                    or len(manifest["publication_families"]) != len(ADAPTIVE_PUBLICATIONS)
                    or set(manifest["publication_families"]) != ADAPTIVE_PUBLICATIONS
                    or set(manifest["schemas"]) != {n + ".schema.json" for n in [*ADAPTIVE_VERSIONS, *ADAPTIVE_TRANSPORT_VERSIONS]}):
                raise ValueError("exact adaptive schema manifest required")
            base = json.loads((DYNAMIC_SCHEMAS / "DIGESTS.json").read_text(encoding="utf-8"))
            if manifest["base_interface_digest"] != base["interface_digest"]:
                raise ValueError("adaptive base interface mismatch")
            graph = {}
            for name in [*ADAPTIVE_VERSIONS, *ADAPTIVE_TRANSPORT_VERSIONS]:
                path = ADAPTIVE_SCHEMAS / (name + ".schema.json")
                raw_schema = path.read_bytes().replace(b"\r\n", b"\n")
                if (len(raw_schema) > 262144
                        or hashlib.sha256(raw_schema).hexdigest() != manifest["schemas"][path.name]
                        or name in DYNAMIC_VERSIONS and raw_schema != REGISTERED[name].read_bytes().replace(b"\r\n", b"\n")):
                    raise ValueError("adaptive manifest does not match pinned standalone schema bytes")
                document = json.loads(raw_schema)
                self.documents[path] = document
                self.schemas[name] = (document, path)
                graph[name] = document
            self.interface_digest = hashlib.sha256(canonical(graph).encode("utf-8")).hexdigest()
            if self.interface_digest != manifest["interface_digest"]:
                raise ValueError("adaptive interface digest mismatch")
            self.payload_versions = dict(ADAPTIVE_VERSIONS)
            self.publication_families = ADAPTIVE_PUBLICATIONS
        elif profile == FEEDBACK_PROFILE:
            raw = (FEEDBACK_SCHEMAS / "DIGESTS.json").read_bytes()
            if len(raw) > 65536:
                raise ValueError("feedback manifest exceeds configured bound")
            manifest = json.loads(raw)
            required = {"profile", "interface_digest", "base_interface_digest", "payload_versions",
                        "new_payload_versions", "transport_versions", "publication_families", "schemas"}
            if (set(manifest) != required or manifest["profile"] != profile
                    or set(manifest["schemas"]) != {name + ".schema.json" for name in [*manifest["payload_versions"], *manifest["transport_versions"]]}
                    or len(manifest["publication_families"]) != len(set(manifest["publication_families"]))
                    or not set(manifest["publication_families"]) <= set(manifest["payload_versions"])):
                raise ValueError("exact feedback schema manifest required")
            base = json.loads((ADAPTIVE_SCHEMAS / "DIGESTS.json").read_text(encoding="utf-8"))
            if manifest["base_interface_digest"] != base["interface_digest"]:
                raise ValueError("feedback base interface mismatch")
            graph = {}
            for name, version in {**manifest["payload_versions"], **manifest["transport_versions"]}.items():
                path = FEEDBACK_SCHEMAS / (name + ".schema.json")
                raw_schema = path.read_bytes().replace(b"\r\n", b"\n")
                if len(raw_schema) > 524288 or hashlib.sha256(raw_schema).hexdigest() != manifest["schemas"][path.name]:
                    raise ValueError("feedback manifest does not match pinned schema bytes")
                document = json.loads(raw_schema)
                if (name != "home-context-publication-2" and document["properties"]["version"].get("const") != version
                        or name in ADAPTIVE_VERSIONS and raw_schema != (ADAPTIVE_SCHEMAS / path.name).read_bytes().replace(b"\r\n", b"\n")):
                    raise ValueError("feedback dialect or reused schema bytes changed")
                self.documents[path] = document
                self.schemas[name] = (document, path)
                graph[name] = document
            self.interface_digest = hashlib.sha256(canonical(graph, -32).encode("utf-8")).hexdigest()
            if self.interface_digest != manifest["interface_digest"]:
                raise ValueError("feedback interface digest mismatch")
            self.payload_versions = dict(manifest["payload_versions"])
            self.publication_families = frozenset(manifest["publication_families"])
            # E consults the unchanged D graph only for pure shared-source
            # invariants. Pin it once per E registry, never per payload.
            self._feedback_shared_adaptive_registry = _PinnedAdaptiveRegistry(manifest["base_interface_digest"])
        elif profile == REFERENCE_PROFILE:
            raw = (REFERENCE_SCHEMAS / "DIGESTS.json").read_bytes()
            if len(raw) > 65536:
                raise ValueError("reference feedback manifest exceeds configured bound")
            manifest = json.loads(raw)
            required = {"profile", "profile_mode", "interface_digest", "base_interface_digest", "legacy_base_interface_digest",
                        "payload_versions", "new_payload_versions", "transport_versions", "publication_families", "schemas"}
            historical = json.loads((FEEDBACK_SCHEMAS / "DIGESTS.json").read_text(encoding="utf-8"))
            successors = {"home-context-worker-checkpoint-8": 8, "home-context-worker-commit-8": 8,
                          "home-context-worker-output-proof-4": 4, "home-context-replay-9": 9,
                          "home-context-worker-checkpoint-response-6": 6, "home-context-worker-original-lookup-1": 1}
            old_names = {"home-context-worker-checkpoint-7", "home-context-worker-commit-7", "home-context-worker-output-proof-3", "home-context-replay-8"}
            expected = (set(historical["payload_versions"]) | set(historical["transport_versions"])) - old_names | set(successors)
            if (set(manifest) != required or manifest["profile"] != profile or manifest["profile_mode"] != "feedback_reference"
                    or manifest["base_interface_digest"] != historical["interface_digest"]
                    or manifest["legacy_base_interface_digest"] != historical["base_interface_digest"]
                    or set(manifest["schemas"]) != {name + ".schema.json" for name in expected}
                    or set(manifest["payload_versions"]) | set(manifest["transport_versions"]) != expected
                    or len(expected) != 71 or manifest["publication_families"] != historical["publication_families"]):
                raise ValueError("exact isolated reference feedback graph required")
            graph = {}
            for name, version in {**manifest["payload_versions"], **manifest["transport_versions"]}.items():
                path = REFERENCE_SCHEMAS / (name + ".schema.json")
                raw_schema = path.read_bytes().replace(b"\r\n", b"\n")
                if len(raw_schema) > 524288 or hashlib.sha256(raw_schema).hexdigest() != manifest["schemas"][path.name]:
                    raise ValueError("reference feedback schema pin differs")
                if name not in successors and raw_schema != (FEEDBACK_SCHEMAS / path.name).read_bytes().replace(b"\r\n", b"\n"):
                    raise ValueError("reference feedback reused schema bytes changed")
                document = json.loads(raw_schema)
                if (name in successors and version != successors[name]
                        or name != "home-context-publication-2" and document["properties"]["version"].get("const") != version):
                    raise ValueError("reference feedback dialect version differs")
                self.documents[path] = document
                self.schemas[name] = (document, path)
                graph[name] = document
            self.interface_digest = hashlib.sha256(canonical(graph, -32).encode("utf-8")).hexdigest()
            if self.interface_digest != manifest["interface_digest"]:
                raise ValueError("reference feedback interface digest differs")
            self.payload_versions = dict(manifest["payload_versions"])
            self.publication_families = frozenset(manifest["publication_families"])
            self._feedback_shared_adaptive_registry = _PinnedAdaptiveRegistry(manifest["legacy_base_interface_digest"])
        elif profile == G2_PROFILE:
            raw = (G2_SCHEMAS / "DIGESTS.json").read_bytes()
            if len(raw) > 262144:
                raise ValueError("G2 manifest exceeds its fixed bound")
            manifest = json.loads(raw)
            names = set(manifest.get("schemas", {}))
            versions = {**manifest.get("payload_versions", {}), **manifest.get("transport_versions", {})}
            families = manifest.get("publication_families", [])
            if (manifest.get("profile") != G2_PROFILE or manifest.get("profile_mode") != "feedback_reference_geometry_hold_2"
                    or manifest.get("interface_digest") != G2_INTERFACE_DIGEST or len(names) != 75
                    or names != {name + ".schema.json" for name in versions}
                    or len(families) != 36 or len(set(families)) != 36
                    or not set(families) <= set(versions) or len(manifest.get("reused_names", [])) != 49
                    or manifest.get("worker_output_only") != ["home-lighting-control-carrier-1"]):
                raise ValueError("exact separate G2 75-root/36-family graph required")
            graph = {}
            for name, version in versions.items():
                path = G2_SCHEMAS / (name + ".schema.json")
                schema_raw = path.read_bytes()
                if len(schema_raw) > 524288 or hashlib.sha256(schema_raw).hexdigest() != manifest["schemas"][path.name]:
                    raise ValueError("G2 raw schema pin differs")
                document = json.loads(schema_raw)
                if (document.get("$id") != path.name or document["properties"]["schema"].get("const") != name
                        or name != "home-context-publication-4" and document["properties"]["version"].get("const") != version):
                    raise ValueError("G2 schema family/version differs")
                if name in manifest["reused_names"]:
                    predecessor = ROOT / "contracts/home-adaptive-feedback-geometry-hold/schemas" / path.name
                    if schema_raw != predecessor.read_bytes():
                        raise ValueError("G2 retained G1 raw schema bytes changed")
                graph[name] = document
                self.documents[path] = document
                self.schemas[name] = (document, path)
            if hashlib.sha256(canonical(graph, -32).encode("utf-8")).hexdigest() != G2_INTERFACE_DIGEST:
                raise ValueError("G2 canonical interface digest differs")
            self.interface_digest = G2_INTERFACE_DIGEST
            self.payload_versions = versions
            self.publication_families = frozenset(families)
            self.generic_event_families = frozenset(manifest["generic_event_families"])
            self.worker_output_families = frozenset(manifest["worker_output_families"])
            self.raw_schema_pins = MappingProxyType(dict(manifest["schemas"]))
        for grant in grants:
            pins = dict(grant.schema_pins)
            if set(pins) != set(grant.payload_types):
                raise ValueError("each granted payload type requires its explicit schema pin")
            for name, expected in pins.items():
                if profile == LIVE_PROFILE:
                    if name not in {*LIVE_VERSIONS, *LIVE_WORKER_VERSIONS, *LIVE_SCENE_VERSIONS, *LIVE_DELIVERY_VERSIONS, *LIVE_STABLE_DELIVERY_VERSIONS, *LIVE_LIFECYCLE_VERSIONS, *LIVE_CONSERVATIVE_DELIVERY_VERSIONS, *LIVE_ROOM_VERSIONS, *LIVE_ROOM_CLARIFICATION_VERSIONS, *LIVE_ROOM_MANUAL_VERSIONS, *LIVE_PC_SUPPORT_VERSIONS, *LIVE_ROOM_SUBSET_VERSIONS, "home-context-revocation-1"}:
                        raise ValueError("type is not admitted by the initial live profile")
                    if schema_digest(name, profile=LIVE_PROFILE) != expected:
                        raise ValueError("configured live schema pin differs")
                    if name == "home-context-revocation-1":
                        path = REGISTERED[name]
                        document = json.loads(path.read_text(encoding="utf-8"))
                        self.documents[path] = document
                        self.schemas[name] = (document, path)
                    continue
                if profile != MAP_REHEARSAL_PROFILE and name in {*MAP_REHEARSAL_REPLACEMENTS.values(), "home-map-rehearsal-binding-1"}:
                    raise ValueError("map rehearsal contracts require their isolated profile")
                if profile == G2_PROFILE:
                    if name not in self.schemas or schema_digest(name, profile=G2_PROFILE) != expected:
                        raise ValueError("configured G2 raw schema pin differs: " + name)
                    continue
                feedback_path = FEEDBACK_SCHEMAS / (name + ".schema.json")
                if profile == REFERENCE_PROFILE:
                    reference_path = REFERENCE_SCHEMAS / (name + ".schema.json")
                    if name not in self.schemas or hashlib.sha256(reference_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() != expected:
                        raise ValueError("configured reference feedback schema pin differs")
                    continue
                if profile == FEEDBACK_PROFILE:
                    if name not in self.schemas or hashlib.sha256(feedback_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() != expected:
                        raise ValueError("configured feedback schema pin does not match registered bytes: " + name)
                    continue
                if (name not in REGISTERED and (feedback_path.is_file() or (REFERENCE_SCHEMAS / (name + ".schema.json")).is_file())
                        or profile == "legacy" and name in {**ADAPTIVE_VERSIONS, **ADAPTIVE_TRANSPORT_VERSIONS}
                        or profile == "dynamic-book-synthetic-1" and name in {**ADAPTIVE_NEW_VERSIONS, **ADAPTIVE_TRANSPORT_VERSIONS}):
                    raise ValueError("synthetic contracts require their isolated profile")
                if schema_digest(name) != expected:
                    raise ValueError("configured schema pin does not match registered bytes: " + name)
                path = (MAP_REHEARSAL_SCHEMAS / (name + ".schema.json") if profile == MAP_REHEARSAL_PROFILE and name in MAP_REHEARSAL_VERSIONS
                        else ADAPTIVE_SCHEMAS / (name + ".schema.json") if profile == "adaptive-book-synthetic-1" and name in {**ADAPTIVE_VERSIONS, **ADAPTIVE_TRANSPORT_VERSIONS} else REGISTERED[name])
                for document in [path, *DEPENDENCIES.get(name, [])]:
                    self.documents[document] = json.loads(document.read_text(encoding="utf-8"))
                self.schemas[name] = (self.documents[path], path)

    def validate(self, value):
        name = value.get("schema") if isinstance(value, dict) else None
        if name not in self.schemas:
            raise JournalError("unsupported_schema", "payload has no explicitly pinned registered validator")
        schema, path = self.schemas[name]
        if not self._valid(value, schema, schema, path):
            raise JournalError("invalid_payload", "payload fails its pinned local schema")

    def _valid(self, value, node, root, path, depth=0):
        if depth > 64:
            raise JournalError("unsupported_schema", "schema recursion limit exceeded")
        if type(node) is bool:
            return node
        if not isinstance(node, dict) or set(node) - KEYWORDS:
            raise JournalError("unsupported_schema", "schema keyword is outside the registered subset")
        if "$ref" in node:
            ref = node["$ref"]
            file_name, _, pointer = ref.partition("#")
            if file_name:
                if not re.fullmatch(r"[a-z0-9.-]+\.schema\.json", file_name):
                    raise JournalError("unsupported_schema", "only pinned local sibling schema references are allowed")
                sibling = path.parent / file_name
                if sibling not in self.documents:
                    raise JournalError("unsupported_schema", "schema reference is not registered")
                root = self.documents[sibling]; path = sibling
            target = root
            if pointer:
                if not pointer.startswith("/"):
                    raise JournalError("unsupported_schema", "local JSON pointer required")
                try:
                    for key in pointer[1:].split("/"):
                        target = target[key.replace("~1", "/").replace("~0", "~")]
                except (KeyError, TypeError):
                    raise JournalError("unsupported_schema", "schema reference cannot be resolved") from None
            if not self._valid(value, target, root, path, depth + 1):
                return False
        recurse = lambda v, n: self._valid(v, n, root, path, depth + 1)
        if "const" in node and canonical(value) != canonical(node["const"]):
            return False
        if "enum" in node and canonical(value) not in [canonical(item) for item in node["enum"]]:
            return False
        types = node.get("type")
        if types is not None:
            types = [types] if isinstance(types, str) else types
            checks = {"object": isinstance(value, dict), "array": isinstance(value, list), "string": isinstance(value, str),
                      "number": finite(value), "integer": finite(value) and float(value).is_integer(), "boolean": type(value) is bool, "null": value is None}
            if not isinstance(types, list) or any(t not in checks for t in types):
                raise JournalError("unsupported_schema", "schema type is not supported")
            if not any(checks[t] for t in types):
                return False
        if "allOf" in node and not all(recurse(value, n) for n in node["allOf"]):
            return False
        if "anyOf" in node and not any(recurse(value, n) for n in node["anyOf"]):
            return False
        if "oneOf" in node and sum(recurse(value, n) for n in node["oneOf"]) != 1:
            return False
        if "not" in node and recurse(value, node["not"]):
            return False
        if "if" in node:
            branch = "then" if recurse(value, node["if"]) else "else"
            if branch in node and not recurse(value, node[branch]):
                return False
        if isinstance(value, dict):
            if any(k not in value for k in node.get("required", [])) or len(value) < node.get("minProperties", 0) or len(value) > node.get("maxProperties", float("inf")):
                return False
            properties = node.get("properties", {})
            for key, item in value.items():
                if "propertyNames" in node and not recurse(key, node["propertyNames"]):
                    return False
                if key in properties:
                    if not recurse(item, properties[key]):
                        return False
                elif "additionalProperties" in node and not recurse(item, node["additionalProperties"]):
                    return False
        if isinstance(value, list):
            if len(value) < node.get("minItems", 0) or len(value) > node.get("maxItems", float("inf")):
                return False
            if node.get("uniqueItems") and len({canonical(v) for v in value}) != len(value):
                return False
            prefix = node.get("prefixItems", [])
            if not isinstance(prefix, list):
                raise JournalError("unsupported_schema", "array prefix schemas must be a bounded list")
            if not all(recurse(item, prefix[index]) for index, item in enumerate(value[:len(prefix)])):
                return False
            if "items" in node and not all(recurse(v, node["items"]) for v in value[len(prefix):]):
                return False
        if isinstance(value, str):
            length = len(value.encode("utf-16-le", errors="surrogatepass")) // 2 if self.profile in {"dynamic-book-synthetic-1", MAP_REHEARSAL_PROFILE, "adaptive-book-synthetic-1", FEEDBACK_PROFILE, REFERENCE_PROFILE, G2_PROFILE, LIVE_PROFILE} else len(value)
            if length < node.get("minLength", 0) or length > node.get("maxLength", float("inf")):
                return False
            if "pattern" in node and (re.fullmatch(node["pattern"],value) if self.profile == LIVE_PROFILE else re.search(node["pattern"],value)) is None:
                return False
        if finite(value):
            if value < node.get("minimum", -float("inf")) or value > node.get("maximum", float("inf")):
                return False
            if "exclusiveMinimum" in node and value <= node["exclusiveMinimum"] or "exclusiveMaximum" in node and value >= node["exclusiveMaximum"]:
                return False
        return True


class _FrozenSchemaDict(dict):
    def _refuse(self, *args, **kwargs):
        raise TypeError("pinned schema is immutable")
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _refuse
    def __deepcopy__(self, memo):
        return self


class _FrozenSchemaList(list):
    def _refuse(self, *args, **kwargs):
        raise TypeError("pinned schema is immutable")
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = __iadd__ = __imul__ = _refuse
    def __deepcopy__(self, memo):
        return self


def _freeze_schema(value):
    if isinstance(value, dict):
        return _FrozenSchemaDict({key: _freeze_schema(child) for key, child in value.items()})
    if isinstance(value, list):
        return _FrozenSchemaList(_freeze_schema(child) for child in value)
    if isinstance(value, tuple):
        return tuple(_freeze_schema(child) for child in value)
    return value


class _PinnedAdaptiveRegistry(SchemaRegistry):
    """Pure immutable D validator scoped to one manifest-checked E registry."""
    def __init__(self, expected_interface_digest):
        super().__init__((), profile="adaptive-book-synthetic-1")
        if self.interface_digest != expected_interface_digest:
            raise ValueError("feedback pure adaptive validator base pin mismatch")
        self.documents = MappingProxyType({path: _freeze_schema(document) for path, document in self.documents.items()})
        self.schemas = MappingProxyType({name: (self.documents[path], path) for name, (_, path) in self.schemas.items()})
        self.payload_versions = MappingProxyType(dict(self.payload_versions))
        self._sealed = True

    def __setattr__(self, name, value):
        if getattr(self, "_sealed", False):
            raise TypeError("pinned schema registry is immutable")
        super().__setattr__(name, value)

    def __delattr__(self, name):
        if getattr(self, "_sealed", False):
            raise TypeError("pinned schema registry is immutable")
        super().__delattr__(name)
