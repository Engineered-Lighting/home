"""Local inference boundary with no built-in production model connector.

The executable HTTP service is intentionally readiness-only/disabled until a
trusted deployment supplies both an evidence verifier and a bounded backend.
There is no credential, policy or health bypass flag. Fake backends can exercise
the whole dispatcher without a network call, GPU, model or live HA instance.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from dataclasses import dataclass, replace
import hashlib
import hmac
import io
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
from pathlib import Path
import queue
import re
import threading
import time
import uuid
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from .admission import (AdmissionClosed, AdmissionController, AdmissionError, AdmissionLedger,
                        CapacityExceeded, RequestConflict, RequestRecord, REJECTION_OUTCOMES, canonical_digest)


MAX_IMAGE_BYTES = 256 * 1024
MAX_HTTP_BODY_BYTES = 384 * 1024
SITES = frozenset({"echo", "victoria"})
_WIRE_FIELDS = frozenset({
    "version", "request_id", "site_id", "device_id", "camera_id", "room_id", "source_event_id",
    "producer_session", "producer_sequence", "occupancy_generation", "capture_session", "frame_id",
    "scheduled_at", "captured_at", "received_at", "clock_uncertainty_seconds", "timestamp_quality",
    "request_deadline", "task_class", "configuration_revision", "max_output_tokens", "image_base64",
})


def _id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise AdmissionError(f"invalid {name}")
    return value


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AdmissionError(f"{name} must be finite")
    return float(value)


def _counter(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 2**63 - 1:
        raise AdmissionError(f"invalid {name}")
    return value


def jpeg_dimensions(image: bytes) -> tuple[int, int]:
    """Bounded JPEG marker inspection before a backend's full JPEG decoder.

    This does not claim to decode entropy data. Require one baseline/progressive
    frame with the pilot dimensions and reject alternative SOF image formats.
    A backend must still reject an invalid compressed image without execution.
    """
    if not 4 <= len(image) <= MAX_IMAGE_BYTES or image[:2] != b"\xff\xd8" or image[-2:] != b"\xff\xd9":
        raise AdmissionError("invalid or oversized JPEG")
    position = 2
    dimensions = None
    while position < len(image) - 2:
        if image[position] != 0xFF:
            raise AdmissionError("invalid JPEG marker")
        while position < len(image) and image[position] == 0xFF:
            position += 1
        if position >= len(image):
            raise AdmissionError("truncated JPEG marker")
        marker = image[position]
        position += 1
        if marker in {0, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            raise AdmissionError("unexpected JPEG marker")
        if position + 2 > len(image):
            raise AdmissionError("truncated JPEG segment")
        size = int.from_bytes(image[position:position + 2], "big")
        if size < 2 or position + size > len(image):
            raise AdmissionError("truncated JPEG segment")
        segment = image[position + 2:position + size]
        if marker in {0xC0, 0xC2}:
            if dimensions is not None or len(segment) < 6:
                raise AdmissionError("invalid JPEG frame header")
            components = segment[5]
            if segment[0] != 8 or components not in {1, 3} or len(segment) != 6 + 3 * components:
                raise AdmissionError("unsupported JPEG frame")
            dimensions = (int.from_bytes(segment[3:5], "big"), int.from_bytes(segment[1:3], "big"))
        elif marker in {0xC1, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            raise AdmissionError("unsupported JPEG coding")
        if marker == 0xDA:
            if dimensions != (640, 360):
                raise AdmissionError("snapshot JPEG must be exactly 640x360 pixels")
            return dimensions
        position += size
    raise AdmissionError("JPEG frame/scan missing")


def verify_snapshot_image(image: bytes) -> None:
    """Fully decode a strictly bounded snapshot before upstream dispatch.

    Pillow is needed only for the admitted image path. A missing decoder closes
    that path; marker inspection alone is never a production validation fallback.
    The real model adapter should independently validate its input as well.
    """
    jpeg_dimensions(image)
    try:
        from PIL import Image
    except ImportError as exc:
        raise AdmissionClosed("bounded JPEG decoder unavailable") from exc
    try:
        with Image.open(io.BytesIO(image)) as picture:
            if picture.format != "JPEG" or picture.size != (640, 360) or picture.mode not in {"RGB", "L"}:
                raise AdmissionError("snapshot must decode to 640x360 RGB/grayscale JPEG")
            picture.verify()
        with Image.open(io.BytesIO(image)) as picture:
            picture.load()
    except AdmissionError:
        raise
    except Exception as exc:
        raise AdmissionError("snapshot JPEG decoding failed") from exc


@dataclass(frozen=True)
class CredentialBinding:
    """Provisioned identity; never constructed from a request body/header claim."""

    site_id: str
    device_id: str
    consumer_id: str
    cameras: Mapping[str, str]
    configuration_revision: str
    allow_interactive: bool = False

    def __post_init__(self) -> None:
        if self.site_id not in SITES:
            raise AdmissionError("unknown registered site")
        for name in ("device_id", "consumer_id", "configuration_revision"):
            _id(getattr(self, name), name)
        if not self.cameras:
            raise AdmissionError("credential requires an explicit camera registry")
        for camera, room in self.cameras.items():
            _id(camera, "camera_id")
            _id(room, "room_id")
        object.__setattr__(self, "cameras", MappingProxyType(dict(self.cameras)))


@dataclass(frozen=True)
class InferenceRequest:
    request_id: str
    ledger_id: str
    site_id: str
    device_id: str
    consumer_id: str
    camera_id: str
    room_id: str
    source_event_id: str
    producer_session: str
    producer_sequence: int
    occupancy_generation: int
    capture_session: str
    frame_id: str
    scheduled_at: float
    captured_at: float
    received_at: float
    request_deadline: float
    valid_until: float
    clock_uncertainty_seconds: float
    task_class: str
    configuration_revision: str
    max_output_tokens: int
    image: bytes
    digest: str


def validate_inference_request(payload: Mapping[str, Any], binding: CredentialBinding,
                               now: float, *, max_output_tokens: int = 512,
                               enforce_freshness: bool = True) -> InferenceRequest:
    now = _finite(now, "gateway clock")
    if (not isinstance(payload, dict) or set(payload) != _WIRE_FIELDS or
            type(payload["version"]) is not int or payload["version"] != 1):
        raise AdmissionError("inference request must exactly match version 1")
    if (payload["site_id"], payload["device_id"], payload["configuration_revision"]) != (
            binding.site_id, binding.device_id, binding.configuration_revision):
        raise AdmissionError("request disagrees with authenticated device binding")
    for name in ("request_id", "camera_id", "room_id", "source_event_id", "producer_session",
                 "capture_session", "frame_id"):
        _id(payload[name], name)
    if binding.cameras.get(payload["camera_id"]) != payload["room_id"]:
        raise AdmissionError("camera/room pair is not registered to this credential")
    if payload["task_class"] not in {"interactive", "background"}:
        raise AdmissionError("unknown task class")
    if payload["task_class"] == "interactive" and not binding.allow_interactive:
        raise AdmissionError("device has no interactive capability")
    for name in ("producer_sequence", "occupancy_generation"):
        _counter(payload[name], name)
    output_tokens = _counter(payload["max_output_tokens"], "max_output_tokens")
    if not 1 <= output_tokens <= max_output_tokens:
        raise AdmissionError("output token limit outside admitted bounds")
    scheduled, captured, received, deadline, uncertainty = (
        _finite(payload[name], name) for name in
        ("scheduled_at", "captured_at", "received_at", "request_deadline", "clock_uncertainty_seconds"))
    if payload["timestamp_quality"] != "validated_observation" or not 0 <= uncertainty <= 1:
        raise AdmissionError("capture clock mapping is not validated within one second")
    if abs(deadline - scheduled - 15) > 0.001:
        raise AdmissionError("request deadline must be scheduled_at plus 15 seconds")
    # Age includes the uncertainty budget rather than subtracting it to make
    # stale images seem newer. Negative age beyond uncertainty is impossible.
    if received < captured - uncertainty or received - captured + uncertainty > 3:
        raise AdmissionError("frame was not fresh at selection")
    if received < scheduled - uncertainty or received > deadline:
        raise AdmissionError("selection is outside the request scheduling interval")
    if enforce_freshness and (scheduled > now + uncertainty or received > now + uncertainty or deadline <= now):
        raise AdmissionError("request is expired or from the future")
    encoded = payload["image_base64"]
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        raise AdmissionError("image exceeds byte limit")
    try:
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise AdmissionError("image_base64 is invalid") from exc
    jpeg_dimensions(image)
    # Identity namespacing prevents equal device/request IDs in different homes
    # from accessing or deduplicating each other's operations.
    ledger_id = canonical_digest([binding.site_id, binding.device_id, payload["request_id"]])
    return InferenceRequest(
        payload["request_id"], ledger_id, binding.site_id, binding.device_id, binding.consumer_id,
        payload["camera_id"], payload["room_id"], payload["source_event_id"], payload["producer_session"],
        payload["producer_sequence"], payload["occupancy_generation"], payload["capture_session"], payload["frame_id"],
        scheduled, captured, received, deadline, captured + 180, uncertainty, payload["task_class"],
        binding.configuration_revision, output_tokens, image, canonical_digest(payload))


@dataclass(frozen=True)
class BackendResult:
    observation_text: str
    observations: tuple[Mapping[str, Any], ...]
    model_revision: str
    terminal_evidence: Any


class InferenceBackend(Protocol):
    """The provisioned backend has a fixed upstream and no HA/tools authority.

    Its implementation must fully decode/reject malformed JPEG, enforce token
    bounds and streaming/cancellation semantics, and supply affirmative terminal
    evidence for the exact pinned engine. No production adapter is bundled.
    """

    def execute(self, request: InferenceRequest, deadline: float) -> BackendResult: ...


class Gateway:
    def __init__(self, controller: AdmissionController, backend: InferenceBackend | None = None,
                 *, clock: Callable[[], float] = time.time,
                 image_validator: Callable[[bytes], None] = verify_snapshot_image,
                 legacy_backend: Any = None):
        self.controller = controller
        self.backend = backend
        self.clock = clock
        self.image_validator = image_validator
        self.legacy_backend = legacy_backend
        self._legacy_pending: dict[str, tuple[Any, Callable[[bytes], None] | None]] = {}
        self._legacy_results: dict[str, tuple[float, Any]] = {}
        self._pending: dict[str, InferenceRequest] = {}
        self._results: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._dispatch_lock = threading.Lock()
        self._late: dict[str, queue.Queue[Any]] = {}
        self._latest: dict[tuple[str, str, str], tuple[str, int, int, float]] = {}

    def readiness(self) -> dict[str, Any]:
        if self.backend is None and self.legacy_backend is None:
            return {"ready": False, "reason": "no approved production backend installed"}
        return self.controller.readiness()

    def submit_legacy(self, payload: Mapping[str, Any], binding: CredentialBinding,
                      request_id: str, on_chunk: Callable[[bytes], None] | None = None) -> Any:
        """Keep content volatile; the common ledger stores identity/digest only."""
        from .legacy_chat import prepare_chat_request
        if self.legacy_backend is None or self.controller.manifest is None:
            raise AdmissionClosed("approved legacy compatibility adapter not installed")
        if binding.site_id != "echo":
            raise AdmissionError("legacy ingress requires a provisioned echo identity")
        request = prepare_chat_request(payload, consumer_id=binding.consumer_id, request_id=request_id,
                                       deadline=self.clock() + self.controller.deadline_limit(binding.consumer_id),
                                       manifest=self.controller.manifest, policy=self.legacy_backend.policy,
                                       task_class="interactive" if binding.allow_interactive else "background")
        with self._lock:
            self._prune_memory()
            existing = self.controller.ledger.get(request.ledger_id)
            if existing is not None:
                request = replace(request, request_deadline=existing.deadline)
            record = self.controller.submit(request.ledger_id, request.site_id, request.consumer_id,
                                            request.digest, request.task_class, request.request_deadline)
            if existing is None and record.state == "queued":
                self._legacy_pending[request.ledger_id] = (request, on_chunk)
            return request

    def legacy_result(self, request: Any) -> tuple[RequestRecord, Any]:
        with self._lock:
            self._prune_memory()
            record = self.controller.ledger.get(request.ledger_id)
            if record is None or record.digest != request.digest:
                raise AdmissionError("unknown legacy request")
            cached = self._legacy_results.get(request.ledger_id)
            return record, cached[1] if cached else None

    def submit(self, payload: Mapping[str, Any], binding: CredentialBinding) -> dict[str, Any]:
        limit = self.controller.manifest.max_output_tokens if self.controller.manifest else 512
        # Immutable contract validation precedes lookup. Mutable resource
        # policy/freshness applies only to new work, never an existing digest.
        request = validate_inference_request(payload, binding, self.clock(),
                                             max_output_tokens=2**63 - 1, enforce_freshness=False)
        with self._lock:
            existing = self.controller.ledger.get(request.ledger_id)
            source_key = (request.site_id, request.device_id, request.camera_id)
            if existing is None:
                now = _finite(self.clock(), "gateway clock")
                if (request.request_deadline <= now or request.scheduled_at > now + request.clock_uncertainty_seconds
                        or request.received_at > now + request.clock_uncertainty_seconds):
                    return self._reject(request, "rejected_expired")
                if self.backend is None:
                    return self._reject(request, "rejected_closed")
                if (request.max_output_tokens > limit or (self.controller.manifest
                        and len(request.image) > self.controller.manifest.max_image_bytes)):
                    return self._reject(request, "rejected_closed")
                try:
                    self.image_validator(request.image)
                except AdmissionClosed:
                    return self._reject(request, "rejected_closed")
                except AdmissionError:
                    return self._reject(request, "rejected_invalid_image")
                previous = self._latest.get(source_key)
                if previous and ((request.producer_session == previous[0] and
                                  (request.producer_sequence <= previous[1] or
                                   request.occupancy_generation < previous[2])) or
                                 request.captured_at < previous[3]):
                    return self._reject(request, "rejected_superseded")
            try:
                record = self.controller.submit(request.ledger_id, request.site_id, request.consumer_id,
                                                request.digest, request.task_class, request.request_deadline)
            except RequestConflict:
                raise
            except CapacityExceeded:
                return self._reject(request, "rejected_capacity")
            except AdmissionClosed:
                # A broken ledger cannot commit a rejection, so uncertain
                # acceptance/commit failures remain transport ambiguity.
                return self._reject(request, "rejected_closed")
            except AdmissionError:
                # Identity, task class and immutable bounds already validated;
                # the remaining controller input gate is its deadline window.
                return self._reject(request, "rejected_expired")
            if record.state == "queued" and existing is None:
                self._pending[request.ledger_id] = request
                self._latest[source_key] = (request.producer_session, request.producer_sequence,
                                           request.occupancy_generation, request.captured_at)
            self._prune_memory()
            return self._status(request.request_id, record, request.device_id)

    def _reject(self, request: InferenceRequest, outcome: str) -> dict[str, Any]:
        record = self.controller.reject_if_absent(
            request.ledger_id, request.site_id, request.consumer_id, request.digest,
            request.task_class, request.request_deadline, outcome)
        return self._status(request.request_id, record, request.device_id)

    def _prune_memory(self) -> None:
        now = _finite(self.clock(), "gateway clock")
        for key, request in list(self._pending.items()):
            if request.request_deadline <= now:
                self._pending.pop(key, None)
        for key, result in list(self._results.items()):
            if result["valid_until"] <= now:
                self._results.pop(key, None)
        for key, (request, _) in list(self._legacy_pending.items()):
            if request.request_deadline <= now:
                self._legacy_pending.pop(key, None)
        for key, (expiry, _) in list(self._legacy_results.items()):
            if expiry <= now:
                self._legacy_results.pop(key, None)

    def status(self, request_id: str, binding: CredentialBinding) -> dict[str, Any]:
        _id(request_id, "request_id")
        ledger_id = canonical_digest([binding.site_id, binding.device_id, request_id])
        with self._lock:
            self._prune_memory()
            record = self.controller.ledger.get(ledger_id)
            if record is None or record.consumer_id != binding.consumer_id:
                raise AdmissionError("unknown request")
            return self._status(request_id, record, binding.device_id)

    def _status(self, external_id: str, record: RequestRecord, device_id: str) -> dict[str, Any]:
        result: dict[str, Any] = {"version": 1, "request_id": external_id, "site_id": record.site_id,
                                  "status": record.state, "outcome": record.outcome}
        if record.request_id in self._results:
            result["observation"] = self._results[record.request_id]
        if record.state == "terminal" and record.outcome in REJECTION_OUTCOMES:
            result["rejection"] = {"version": 1, "device_id": device_id,
                                   "request_digest": record.digest,
                                   "reason": REJECTION_OUTCOMES[record.outcome]}
        return result

    def dispatch_once(self) -> dict[str, Any] | None:
        """Execute at most one reservation. Never retry an uncertain execution."""
        if not self._dispatch_lock.acquire(blocking=False):
            return None
        try:
            if self.backend is None and self.legacy_backend is None:
                raise AdmissionClosed("no approved production backend installed")
            record = self.controller.claim_next()
            if record is None:
                return None
            with self._lock:
                legacy = self._legacy_pending.pop(record.request_id, None)
            if legacy is not None:
                return self._dispatch_legacy(record, *legacy)
            with self._lock:
                request = self._pending.pop(record.request_id, None)
            if request is None:
                # Fail closed even though no network call was made. Reconciliation
                # is preferable to inventing terminal evidence at this layer.
                self.controller.mark_indeterminate(record.request_id, "payload_unavailable")
                return None
            self.controller.mark_running(record.request_id)
            completed: queue.Queue[Any] = queue.Queue(maxsize=1)

            def run() -> None:
                try:
                    completed.put((True, self.backend.execute(request, request.request_deadline)))
                except Exception:
                    # Do not expose backend exceptions: they may contain secrets
                    # or prompt content. Exceptions never prove engine termination.
                    completed.put((False, None))

            worker = threading.Thread(target=run, daemon=True, name="shared-home-upstream")
            worker.start()
            try:
                okay, output = completed.get(timeout=max(0, request.request_deadline - self.clock()))
            except queue.Empty:
                self._late[record.request_id] = completed
                self.controller.mark_indeterminate(record.request_id, "upstream_timeout")
                return self.status(request.request_id, self._binding_for(request))
            if not okay or not isinstance(output, BackendResult):
                self.controller.mark_indeterminate(record.request_id)
                return self.status(request.request_id, self._binding_for(request))
            self.controller.finish(record.request_id, output.terminal_evidence)
            manifest = self.controller.manifest
            if (manifest is None or output.model_revision != manifest.model_revision or
                    not isinstance(output.observation_text, str) or len(output.observation_text) > 4096):
                self.controller.close_admission("backend contract violation")
                raise AdmissionClosed("backend contract violation")
            if not isinstance(output.observations, tuple) or len(output.observations) > 32:
                self.controller.close_admission("backend contract violation")
                raise AdmissionClosed("backend structured output exceeds bound")
            for observation in output.observations:
                if (not isinstance(observation, dict) or len(observation) > 16 or
                        any(not isinstance(key, str) or len(key) > 128 or
                            not isinstance(value, (str, int, float, bool, type(None))) or
                            (isinstance(value, str) and len(value) > 2048)
                            for key, value in observation.items())):
                    self.controller.close_admission("backend contract violation")
                    raise AdmissionClosed("backend observations must be bounded flat data")
            try:
                structured = json.dumps(output.observations, allow_nan=False)
            except (TypeError, ValueError) as exc:
                self.controller.close_admission("backend contract violation")
                raise AdmissionClosed("backend structured output invalid") from exc
            if len(structured.encode()) > 64 * 1024:
                self.controller.close_admission("backend contract violation")
                raise AdmissionClosed("backend structured output exceeds limit")
            current = self._latest.get((request.site_id, request.device_id, request.camera_id))
            if (self.clock() < request.request_deadline and self.clock() < request.valid_until and
                    current == (request.producer_session, request.producer_sequence,
                                request.occupancy_generation, request.captured_at)):
                with self._lock:
                    self._results[record.request_id] = {
                        "site_id": request.site_id, "device_id": request.device_id,
                        "camera_id": request.camera_id, "room_id": request.room_id,
                        "producer_session": request.producer_session,
                        "producer_sequence": request.producer_sequence,
                        "occupancy_generation": request.occupancy_generation,
                        "capture_session": request.capture_session, "frame_id": request.frame_id,
                        "source_event_id": request.source_event_id,
                        "observed_at": request.captured_at, "valid_until": request.valid_until,
                        "request_deadline": request.request_deadline,
                        "configuration_revision": request.configuration_revision,
                        "model_revision": output.model_revision,
                        "description": output.observation_text,
                        "uncertainty_seconds": request.clock_uncertainty_seconds,
                        "observations": json.loads(structured),
                    }
                    while len(self._results) > max(16, manifest.max_queue * 4):
                        self._results.pop(next(iter(self._results)))
            return self.status(request.request_id, self._binding_for(request))
        finally:
            self._dispatch_lock.release()

    def _dispatch_legacy(self, record: RequestRecord, request: Any,
                         on_chunk: Callable[[bytes], None] | None) -> dict[str, Any]:
        from .legacy_chat import LegacyChatResult
        self.controller.mark_running(record.request_id)
        completed: queue.Queue[Any] = queue.Queue(maxsize=1)
        def run() -> None:
            try:
                completed.put((True, self.legacy_backend.execute(request, request.request_deadline, on_chunk)))
            except Exception:
                completed.put((False, None))
        threading.Thread(target=run, daemon=True, name="shared-home-legacy-upstream").start()
        try:
            okay, output = completed.get(timeout=max(0, request.request_deadline - self.clock()))
        except queue.Empty:
            self._late[record.request_id] = completed
            self.controller.mark_indeterminate(record.request_id, "upstream_timeout")
            return {"status": "indeterminate"}
        if not okay or not isinstance(output, LegacyChatResult):
            self.controller.mark_indeterminate(record.request_id)
            return {"status": "indeterminate"}
        self.controller.finish(record.request_id, output.terminal_evidence)
        if output.content_type == "application/json" and self.clock() < request.request_deadline:
            # The backend validates the OpenAI schema. Apply a second aggregate
            # bound before retaining any content, and never persist it.
            encoded = output.body
            if (not isinstance(encoded, bytes) or len(encoded) > self.legacy_backend.policy.max_response_bytes
                    or type(output.status) is not int or not 200 <= output.status <= 599):
                self.controller.close_admission("legacy response contract violation")
                raise AdmissionClosed("legacy response contract violation")
            with self._lock:
                self._legacy_results[record.request_id] = (request.request_deadline, output)
                bound = max(1, min(16, self.controller.manifest.max_queue))
                while len(self._legacy_results) > bound:
                    self._legacy_results.pop(next(iter(self._legacy_results)))
        return {"status": "terminal"}

    def reconcile_late_completions(self) -> int:
        """Inspect affirmative late completions; never publish their old output.

        Admission remains closed until explicit re-enablement verifies all gates.
        A real engine lookup or terminated-epoch maintenance proof may instead be
        supplied directly to controller.finish by its trusted deployment adapter.
        """
        finished = 0
        from .legacy_chat import LegacyChatResult
        with self._dispatch_lock:
            for ledger_id, completed in list(self._late.items()):
                try:
                    okay, output = completed.get_nowait()
                except queue.Empty:
                    continue
                if okay and isinstance(output, (BackendResult, LegacyChatResult)):
                    self.controller.finish(ledger_id, output.terminal_evidence, "completed_after_deadline")
                    finished += 1
                self._late.pop(ledger_id, None)
        return finished

    @staticmethod
    def _binding_for(request: InferenceRequest) -> CredentialBinding:
        return CredentialBinding(request.site_id, request.device_id, request.consumer_id,
                                 {request.camera_id: request.room_id}, request.configuration_revision)


class GatewayHTTPServer(HTTPServer):
    """Bounded single-request local harness, not a TLS ingress or deployment."""

    def __init__(self, address: tuple[str, int], gateway: Gateway,
                 credentials: Mapping[str, CredentialBinding] | None = None):
        if address[0] not in {"127.0.0.1", "localhost"}:
            raise AdmissionError("local harness only binds IPv4 loopback")
        self.gateway = gateway
        # Configuration contains bearer hashes, not raw tokens. mTLS/trusted
        # identity ingress is a deployment integration, not caller HTTP headers.
        self.credentials = dict(credentials or {})
        if any(not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in self.credentials):
            raise AdmissionError("credential keys must be SHA256 bearer hashes")
        super().__init__(address, _Handler)

    def get_request(self) -> tuple[Any, Any]:
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address


class _Handler(BaseHTTPRequestHandler):
    server: GatewayHTTPServer
    protocol_version = "HTTP/1.0"

    def log_message(self, *args: Any) -> None:
        pass  # No paths, request bodies, bearer tokens or prompts in logs.

    def _body_received(self) -> None:
        """Transport hook after the complete body, before parsing or admission.

        Private TLS ingress uses this to end its absolute upload deadline.
        The loopback harness keeps its existing socket timeout behavior.
        """

    def _write(self, status: int, payload: Mapping[str, Any]) -> None:
        encoded = json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _binding(self) -> CredentialBinding | None:
        auth = self.headers.get_all("Authorization", [])
        if len(auth) != 1 or not auth[0].startswith("Bearer ") or len(auth[0]) > 4096:
            return None
        digest = hashlib.sha256(auth[0][7:].encode()).hexdigest()
        for expected, binding in self.server.credentials.items():
            if hmac.compare_digest(digest, expected):
                return binding
        return None

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._write(200, {"alive": True, "mode": "local-boundary-harness"})
        elif self.path == "/readyz":
            status = self.server.gateway.readiness()
            self._write(200 if status["ready"] else 503, status)
        elif self.path == "/v1/models":
            binding = self._binding()
            if binding is None or binding.site_id != "echo":
                self._write(401, {"error": "provisioned echo authentication required"})
            elif self.server.gateway.legacy_backend is None or self.server.gateway.controller.manifest is None:
                self._write(503, {"error": "approved legacy compatibility adapter not installed"})
            else:
                self._write(200, {"object": "list", "data": [{"id": self.server.gateway.controller.manifest.model_alias,
                             "object": "model", "created": 0, "owned_by": "shared-home"}]})
        elif self.path.startswith("/v1/inference/"):
            binding = self._binding()
            if binding is None:
                self._write(401, {"error": "device authentication required"})
                return
            try:
                self._write(200, self.server.gateway.status(self.path[len("/v1/inference/"):], binding))
            except AdmissionError:
                self._write(404, {"error": "unknown request"})
        else:
            self._write(404, {"error": "unknown route"})

    def do_POST(self) -> None:
        if self.path == "/v1/chat/completions":
            self._chat()
            return
        if self.path == "/v1/completions":
            self._write(503, {"error": "raw completion compatibility is not installed"})
            return
        if self.path != "/v1/inference":
            self._write(404, {"error": "unknown route"})
            return
        binding = self._binding()
        if binding is None:
            self._write(401, {"error": "device authentication required"})
            return
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get_all("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isdigit():
            self._write(400, {"error": "one bounded Content-Length required"})
            return
        length = int(lengths[0])
        if not 0 < length <= MAX_HTTP_BODY_BYTES:
            self._write(413, {"error": "body exceeds limit"})
            return
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                raise AdmissionError("truncated body")
            self._body_received()
            def reject_constant(value: str) -> Any:
                raise AdmissionError("non-finite JSON number")
            def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                value: dict[str, Any] = {}
                for key, item in pairs:
                    if key in value:
                        raise AdmissionError("duplicate JSON member")
                    value[key] = item
                return value
            payload = json.loads(body, parse_constant=reject_constant, object_pairs_hook=unique_object)
            response = self.server.gateway.submit(payload, binding)
            self._write(202, response)
        except RequestConflict as exc:
            self._write(409, {"error": str(exc)})
        except CapacityExceeded as exc:
            self._write(429, {"error": str(exc)})
        except AdmissionClosed as exc:
            self._write(503, {"error": str(exc)})
        except (AdmissionError, UnicodeError, json.JSONDecodeError, RecursionError, OSError) as exc:
            self._write(400, {"error": str(exc) if isinstance(exc, AdmissionError) else "invalid request"})

    def _chat(self) -> None:
        gateway = self.server.gateway
        if gateway.legacy_backend is None:
            self._write(503, {"error": "approved legacy compatibility adapter not installed"})
            return
        binding = self._binding()
        if binding is None or binding.site_id != "echo":
            self._write(401, {"error": "provisioned echo authentication required"})
            return
        started = False
        stopped = threading.Event()
        output_lock = threading.Lock()
        def emit(event: bytes) -> None:
            nonlocal started
            with output_lock:
                if stopped.is_set():
                    raise ConnectionError("client delivery ended")
                if not isinstance(event, bytes) or len(event) > gateway.legacy_backend.policy.max_event_bytes:
                    raise AdmissionError("invalid stream event")
                if not started:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    started = True
                self.wfile.write(event)
                self.wfile.flush()
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get_all("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isdigit():
                raise AdmissionError("one bounded Content-Length required")
            length = int(lengths[0])
            if not 0 < length <= gateway.legacy_backend.policy.max_body_bytes:
                self._write(413, {"error": "body exceeds limit"})
                return
            keys = self.headers.get_all("Idempotency-Key", [])
            if len(keys) > 1:
                raise AdmissionError("one idempotency key required")
            request_id = _id(keys[0] if keys else uuid.uuid4().hex, "request_id")
            body = self.rfile.read(length)
            if len(body) != length:
                raise AdmissionError("truncated body")
            self._body_received()
            from .openai_backend import _object, _constant
            payload = json.loads(body, object_pairs_hook=_object, parse_constant=_constant)
            request = gateway.submit_legacy(payload, binding, request_id, emit)
            # Drive the SAME dispatcher/priority queue. A production server may
            # drive it independently; this loop never introduces another queue.
            monotonic_deadline = time.monotonic() + max(0, request.request_deadline - gateway.clock())
            while True:
                record, result = gateway.legacy_result(request)
                if result is not None:
                    self.send_response(result.status)
                    self.send_header("Content-Type", result.content_type)
                    self.send_header("Content-Length", str(len(result.body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(result.body)
                    return
                if record.state in {"terminal", "indeterminate"}:
                    if not started:
                        self._write(409 if record.state == "terminal" else 504,
                                    {"error": "recorded request has no replayable response", "status": record.state})
                    return
                if time.monotonic() >= monotonic_deadline:
                    if not started:
                        self._write(504, {"error": "request deadline elapsed"})
                    return
                dispatched = gateway.dispatch_once()
                if dispatched is None:
                    stopped.wait(0.01)
        except RequestConflict:
            if not started:
                self._write(409, {"error": "idempotency key conflicts with recorded request"})
        except CapacityExceeded:
            if not started:
                self._write(429, {"error": "admission capacity unavailable"})
        except AdmissionClosed:
            if not started:
                self._write(503, {"error": "admission closed"})
        except (AdmissionError, ValueError, UnicodeError, RecursionError):
            if not started:
                self._write(400, {"error": "invalid chat request"})
        except OSError:
            pass  # Delivery failure never releases a running reservation.
        finally:
            with output_lock:
                stopped.set()
            self.close_connection = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the disabled shared-home admission readiness harness")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8781)
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("port must be 0..65535")
    with AdmissionLedger(args.ledger) as ledger:
        gateway = Gateway(AdmissionController(ledger))
        with GatewayHTTPServer(("127.0.0.1", args.port), gateway) as server:
            print(json.dumps({"mode": "disabled", "host": "127.0.0.1", "port": server.server_port}))
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
