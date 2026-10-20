"""Narrow wire adapters connecting the independently tested contracts.

No HTTP/MQTT connection opens here. Installed transports must authenticate the
peer, enforce exact topic/endpoint ACLs, and never expose these constructors to
untrusted callers. Camera metadata is accepted only from the registered camera
owner on the same Jetson's loopback interface.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re

from .adapter import Frame, VictoriaAdapter, Work, finite


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate camera metadata field")
        result[key] = value
    return result


def _causal_frame(jpeg, metadata):
    """Validate exact-frame evidence supplied by the authenticated camera owner.

    This checks the receipt's arithmetic and content binding, not the physical
    device semantics or commissioned UTC authority behind its issuance.
    """
    fields = {"schema", "session", "sequence", "timing_generation", "width", "height", "quality",
              "observed_monotonic_seconds", "observed_at_unix_ns", "uncertainty_seconds", "validation_id",
              "exposure_duration_seconds", "source_clock", "monotonic_clock_scope", "timestamp_semantics",
              "challenge_nonce", "challenge_device_ns", "frame_device_ns", "host_send_monotonic_ns",
              "host_receive_monotonic_ns", "timestamp_quantization_ns", "preview_sha256", "jpeg_sha256",
              "host_boot_id", "host_clock_validation_id", "host_clock_uncertainty_seconds"}
    if set(metadata) != fields:
        raise ValueError("causal frame fields mismatch")
    expected = {"width": 640, "height": 360, "quality": 85,
                "source_clock": "depthai_device_monotonic",
                "monotonic_clock_scope": "python_monotonic_on_camera_host",
                "timestamp_semantics": "causal_exact_frame_exposure_end"}
    if any(metadata.get(k) != v for k, v in expected.items()):
        raise ValueError("causal frame semantics mismatch")
    for key in ("session", "validation_id", "host_clock_validation_id"):
        if not isinstance(metadata[key], str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", metadata[key]) is None:
            raise ValueError("causal frame identity invalid")
    for key, pattern in (("challenge_nonce", r"[a-f0-9]{32}"),
                         ("host_boot_id", r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"),
                         ("preview_sha256", r"[a-f0-9]{64}"), ("jpeg_sha256", r"[a-f0-9]{64}")):
        if not isinstance(metadata[key], str) or re.fullmatch(pattern, metadata[key]) is None:
            raise ValueError("causal frame binding invalid")
    integers = ("sequence", "timing_generation", "challenge_device_ns", "frame_device_ns",
                "host_send_monotonic_ns", "host_receive_monotonic_ns", "timestamp_quantization_ns",
                "observed_at_unix_ns")
    if any(type(metadata[key]) is not int or not 0 <= metadata[key] <= 2**63 - 1 for key in integers):
        raise ValueError("causal frame bounds invalid")
    start, end = metadata["host_send_monotonic_ns"], metadata["host_receive_monotonic_ns"]
    quantization = metadata["timestamp_quantization_ns"]
    if (end < start or metadata["observed_at_unix_ns"] <= 0 or not 1000 <= quantization <= 1000000
            or metadata["frame_device_ns"] <= metadata["challenge_device_ns"] + quantization):
        raise ValueError("causal frame ordering invalid")
    exposure, host_error = metadata["exposure_duration_seconds"], metadata["host_clock_uncertainty_seconds"]
    midpoint, uncertainty = metadata["observed_monotonic_seconds"], metadata["uncertainty_seconds"]
    if (not all(finite(v) for v in (exposure, host_error, midpoint, uncertainty))
            or not 0 < exposure <= 1 or not 0 <= host_error <= 1):
        raise ValueError("causal frame uncertainty invalid")
    expected_midpoint = (start + end) / 2e9
    expected_error = (end - start) / 2e9 + exposure + quantization / 1e9 + host_error
    if (not 0 < uncertainty <= 1 or not math.isclose(midpoint, expected_midpoint, rel_tol=0, abs_tol=1e-9)
            or uncertainty < expected_error or not math.isclose(uncertainty, expected_error, rel_tol=0, abs_tol=1e-9)):
        raise ValueError("causal frame derived timing mismatch")
    if (not isinstance(jpeg, bytes) or not 1 <= len(jpeg) <= 256 * 1024
            or hashlib.sha256(jpeg).hexdigest() != metadata["jpeg_sha256"]):
        raise ValueError("causal JPEG binding mismatch")
    return Frame(metadata["session"], f"c{metadata['timing_generation']}-s{metadata['sequence']}",
                 metadata["observed_at_unix_ns"] / 1e9, midpoint, uncertainty,
                 metadata["validation_id"], jpeg, timing_generation=metadata["timing_generation"],
                 timing_evidence_version=2, camera_boot_id=metadata["host_boot_id"],
                 host_clock_validation_id=metadata["host_clock_validation_id"],
                 camera_host_uncertainty_seconds=host_error)


def camera_frame(jpeg: bytes, metadata_header: str) -> Frame:
    if not isinstance(metadata_header, str) or len(metadata_header) > 8192:
        raise ValueError("camera metadata exceeds limit")
    try:
        metadata = json.loads(base64.b64decode(metadata_header, altchars=b"-_", validate=True),
                              object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise ValueError("invalid camera metadata") from exc
    if isinstance(metadata, dict) and type(metadata.get("schema")) is int and metadata["schema"] == 3:
        return _causal_frame(jpeg, metadata)
    expected = {"width": 640, "height": 360, "quality": 85,
                "source_clock": "depthai_host_monotonic",
                "monotonic_clock_scope": "python_monotonic_on_camera_host",
                "timestamp_semantics": "commissioned_source_frame_timestamp_mapping"}
    if not isinstance(metadata, dict) or any(metadata.get(k) != v for k, v in expected.items()):
        raise ValueError("camera timestamp or preview contract mismatch")
    schema = metadata.get("schema")
    if type(schema) is not int or schema not in (1, 2):
        raise ValueError("unsupported camera metadata schema")
    for key in ("session", "validation_id"):
        if not isinstance(metadata.get(key), str) or not 1 <= len(metadata[key]) <= 128:
            raise ValueError("camera source identity missing")
    if (type(metadata.get("sequence")) is not int or metadata["sequence"] < 0
            or type(metadata.get("observed_at_unix_ns")) is not int or metadata["observed_at_unix_ns"] <= 0
            or not finite(metadata.get("observed_monotonic_seconds"))
            or not finite(metadata.get("uncertainty_seconds"))):
        raise ValueError("camera observation timing invalid")
    frame_id = str(metadata["sequence"])
    generation = 0
    if schema == 2:
        generation = metadata.get("timing_generation")
        exposure = metadata.get("exposure_duration_seconds")
        if (type(generation) is not int or not 0 <= generation <= 2**63 - 1
                or not finite(exposure) or not 0 < exposure <= metadata["uncertainty_seconds"] <= 1):
            raise ValueError("camera generation or exposure invalid")
        frame_id = f"g{generation}-s{metadata['sequence']}"
    elif metadata.get("timing_generation", 0) != 0:
        raise ValueError("legacy metadata cannot represent timing generations")
    return Frame(metadata["session"], frame_id, metadata["observed_at_unix_ns"] / 1e9,
                 metadata["observed_monotonic_seconds"], metadata["uncertainty_seconds"],
                 metadata["validation_id"], jpeg, timing_generation=generation)


def inference_payload(adapter: VictoriaAdapter, frame: Frame, *, device_id: str,
                      configuration_revision: str, max_output_tokens: int,
                      now_mono: float, now_unix: float, cross_host_uncertainty: float) -> dict:
    """Called only after take_work; image selection cannot change schedule/expiry."""
    adapter.bind_frame(frame, now_mono=now_mono, now_unix=now_unix,
                       cross_host_uncertainty=cross_host_uncertainty)
    frame = adapter.active_frame
    work = adapter.active
    return {"version": 1, "request_id": work.request_id, "site_id": work.site_id,
            "device_id": device_id, "camera_id": work.camera_id, "room_id": work.room_id,
            "source_event_id": f"{work.adapter_session}:{work.occupancy_generation}",
            "producer_session": work.adapter_session, "producer_sequence": work.sequence,
            "occupancy_generation": work.occupancy_generation,
            "capture_session": frame.capture_session, "frame_id": frame.frame_id,
            "scheduled_at": work.scheduled_at, "captured_at": frame.observed_at,
            "received_at": now_unix, "clock_uncertainty_seconds": frame.uncertainty_seconds,
            "timestamp_quality": "validated_observation", "request_deadline": work.request_deadline,
            "task_class": "interactive" if work.kind == "interactive" else "background", "configuration_revision": configuration_revision,
            "max_output_tokens": max_output_tokens, "image_base64": base64.b64encode(frame.jpeg).decode("ascii")}


def finish_observation(adapter: VictoriaAdapter, work: Work, response: dict, *, now_mono: float,
                       now_unix: float) -> dict | None:
    """Translate a terminal gateway result; retrieved/model text never gains action authority."""
    if (not isinstance(response, dict) or response.get("request_id") != work.request_id
            or response.get("site_id") != work.site_id):
        return None
    if response.get("status") != "terminal":
        return None  # queued/running/indeterminate remain accounted, never auto-retried
    if response.get("outcome") != "completed":
        adapter.complete(work.request_id, None, now_mono=now_mono, now_unix=now_unix)
        return None
    observation = response.get("observation")
    if not isinstance(observation, dict):
        adapter.complete(work.request_id, None, now_mono=now_mono, now_unix=now_unix)
        return None
    result = dict(observation)
    result.update(request_id=work.request_id, status="ok", adapter_session=result.pop("producer_session", None),
                  sequence=result.pop("producer_sequence", None), authority="untrusted_observation")
    return adapter.complete(work.request_id, result, now_mono=now_mono, now_unix=now_unix)


class FrigateWire:
    """Exact MQTT message mapping, independent of the installed client library.

    connect→SUBACK(all)→fresh health→onConnect→camera_activity. MQTT client must
    use a clean session and serialize callbacks. Counts only request reconcile.
    The retained flag is never silently removed by this boundary.
    """

    def __init__(self, adapter: VictoriaAdapter):
        self.adapter = adapter

    def receive(self, topic: str, payload: bytes, *, retained: bool, duplicate: bool,
                now_mono: float) -> bool:
        if topic not in self.adapter.subscriptions or len(payload) > 64 * 1024:
            return False
        if topic == "frigate/available":
            if payload != b"online":
                self.adapter.unavailable()
            return True
        if topic in ("frigate/den_oak/person", "frigate/den_oak/all"):
            self.adapter.count_hint(retained=retained)
            return True
        try:
            activity = json.loads(payload)
        except (ValueError, UnicodeError):
            self.adapter.invalidate()
            return False
        return self.adapter.snapshot(activity, now_mono, retained=retained, duplicate=duplicate)

    def availability(self, *, now_mono: float) -> tuple[str, str, bool]:
        a = self.adapter
        online = (a.connected and a.subscribed and a.state == "occupied"
                  and 0 <= now_mono - a.health_at <= 10 and 0 <= now_mono - a.snapshot_at <= 10)
        value = {"site_id": a.site_id, "camera_id": a.camera_id, "adapter_session": a.session,
                 "occupancy_generation": a.generation, "online": online}
        return "home/shared/v1/victoria/den_oak/availability", json.dumps(value), False

    def last_will(self) -> tuple[str, str, bool]:
        topic, payload, retain = self.availability(now_mono=float("inf"))
        return topic, payload, retain

    @staticmethod
    def publication(observation: dict) -> tuple[str, str, bool]:
        if observation.get("site_id") != "victoria" or observation.get("camera_id") != "den_oak":
            raise ValueError("output topic is fixed to the registered pilot camera")
        return "home/shared/v1/victoria/den_oak/observation", json.dumps(observation, allow_nan=False), False
