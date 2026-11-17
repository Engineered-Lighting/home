"""Producer-side semantic hints. These are never positions, probabilities or commands.

All times are seconds on this LA host's time.monotonic clock, shared by the
loopback worker and producer. Wall-clock/browser timestamps are not accepted.
The producer checks the trusted request again when the asynchronous reply lands.
"""
import hashlib
import json
import math
import re
from dataclasses import dataclass
from types import MappingProxyType

SCHEMA = "den.intent-hint/v1"
CHOICES = ("continue", "stop", "turn_left", "turn_right", "unclear")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
DEPENDENCIES = frozenset(("camera_id", "service_run_id", "camera_epoch", "source_generation", "track_id",
                          "association_revision", "transform_id", "scan_revision", "calibration_revision"))


def field(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def generation(run, epoch):
    return hashlib.sha256(json.dumps([run, epoch], separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _plain(value):
    if hasattr(value, "items"):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value


@dataclass(frozen=True)
class IntentHint:
    request_id: str
    clip_id: str
    profile_digest: str
    snapshot_manifest_digest: str
    model_revision: str
    dependencies: object
    evidence: object
    produced_at: float
    expires_at: float
    intent: str
    raw_option_scores: object
    text: str | None = None
    schema: str = SCHEMA
    uncertainty: str = "uncalibrated_semantic_hint"
    authority: str = "semantic_prior_only"

    def valid_at(self, now_mono):
        return finite(now_mono) and self.produced_at <= now_mono < self.expires_at

    def matches(self, snapshot, track):
        """Current authority, not a saved request, decides whether a clip still belongs to this person."""
        frame = field(snapshot, "frame")
        deps = self.dependencies
        epoch = field(snapshot, "camera_epoch")
        run = field(snapshot, "service_run_id")
        return (frame is not None and field(snapshot, "status") == "fresh"
                and field(snapshot, "presence") == "present"
                and field(track, "state") == "confirmed"
                and field(track, "association_status") == "confident"
                and bool(field(track, "current_obs_ids"))
                and deps["service_run_id"] == run and type(epoch) is int
                and deps["camera_epoch"] == str(epoch)
                and deps["source_generation"] == generation(run, epoch)
                and deps["track_id"] == field(track, "track_id")
                and deps["association_revision"] == str(field(track, "association_revision"))
                and deps["transform_id"] == field(frame, "transform_id")
                and deps["scan_revision"] == field(frame, "scan_revision")
                and deps["calibration_revision"] == field(frame, "depth_calibration_sha256")
                and finite(field(frame, "capture_mono"))
                and self.evidence["cutoff_at"] <= field(frame, "capture_mono"))

    def disposition(self, now_mono, snapshot=None, track=None):
        if not self.valid_at(now_mono):
            return "expired", "capture_lease_expired"
        if snapshot is not None and not self.matches(snapshot, track):
            return "ignored", "current_binding_changed"
        return "advisory_only", "awaiting_held_out_admission"

    def public(self, *, now_mono=None, snapshot=None, track=None):
        """Bounded Home projection: no blobs, paths, raw model text, prompts or URLs."""
        disposition, reason = (self.disposition(now_mono, snapshot, track) if now_mono is not None
                               else ("advisory_only", "awaiting_held_out_admission"))
        return {"schema": SCHEMA, "request_id": self.request_id, "clip_id": self.clip_id,
                "profile_digest": self.profile_digest, "snapshot_manifest_digest": self.snapshot_manifest_digest,
                "model_revision": self.model_revision, "dependencies": _plain(self.dependencies),
                "produced_at": self.produced_at, "expires_at": self.expires_at,
                "observed_at": self.evidence["cutoff_at"], "clock": "producer_monotonic",
                "intent": self.intent, "raw_option_scores": _plain(self.raw_option_scores),
                "uncertainty": self.uncertainty, "authority": self.authority,
                "disposition": disposition, "reason": reason, "consumed": False}


def from_wire(value, *, now_mono, request=None):
    """Validate worker output against the exact trusted request before constructing an immutable hint.

    Raise only fixed reason words. Neither exception messages nor public output
    include input blobs or model-generated text.
    """
    expected = {"schema", "request_id", "clip_id", "profile_digest", "snapshot_manifest_digest", "model_revision",
                "dependencies", "evidence", "produced_at", "expires_at", "intent", "raw_option_scores", "text",
                "uncertainty", "authority"}
    if not isinstance(value, dict) or set(value) != expected or value["schema"] != SCHEMA:
        raise ValueError("invalid_hint_schema")
    if value["uncertainty"] != "uncalibrated_semantic_hint" or value["authority"] != "semantic_prior_only":
        raise ValueError("invalid_hint_authority")
    for key in ("profile_digest", "snapshot_manifest_digest"):
        if not isinstance(value[key], str) or not HEX64.fullmatch(value[key]):
            raise ValueError("invalid_hint_digest")
    if not isinstance(value["model_revision"], str) or not REVISION.fullmatch(value["model_revision"]):
        raise ValueError("invalid_hint_revision")
    for key in ("request_id", "clip_id"):
        if not isinstance(value[key], str) or not 0 < len(value[key]) <= 128:
            raise ValueError("invalid_hint_identity")
    deps, evidence = value["dependencies"], value["evidence"]
    if (not isinstance(deps, dict) or set(deps) != DEPENDENCIES
            or not all(isinstance(v, str) and 0 < len(v) <= 128 for v in deps.values())):
        raise ValueError("invalid_hint_dependencies")
    for key in ("camera_epoch", "association_revision"):
        if not deps[key].isascii() or not deps[key].isdigit() or str(int(deps[key])) != deps[key]:
            raise ValueError("invalid_hint_counter")
    if deps["source_generation"] != generation(deps["service_run_id"], int(deps["camera_epoch"])):
        raise ValueError("invalid_hint_generation")
    if not isinstance(evidence, dict) or evidence.get("additional_center_crop") is not False:
        raise ValueError("invalid_hint_preprocessing")
    frames = evidence.get("frames")
    cutoff, produced, expires = evidence.get("cutoff_at"), value["produced_at"], value["expires_at"]
    if (not isinstance(frames, list) or len(frames) != 32
            or not all(finite(v) for v in (cutoff, produced, expires, now_mono))
            or not cutoff <= produced <= now_mono < expires <= cutoff + 10):
        raise ValueError("invalid_hint_time")
    previous = -math.inf
    for entry in frames:
        source = entry.get("source_frame") if isinstance(entry, dict) else None
        if not isinstance(source, dict):
            raise ValueError("missing_source_frame")
        stamp = entry.get("captured_at")
        if (not finite(stamp) or not previous <= stamp <= cutoff
                or source.get("capture_mono") != stamp or source.get("service_run_id") != deps["service_run_id"]
                or type(source.get("camera_epoch")) is not int or str(source["camera_epoch"]) != deps["camera_epoch"]
                or source.get("transform_id") != deps["transform_id"] or source.get("scan_revision") != deps["scan_revision"]
                or source.get("depth_calibration_sha256") != deps["calibration_revision"]
                or not finite(source.get("arrival_mono")) or not stamp <= source["arrival_mono"] <= produced):
            raise ValueError("hint_source_mismatch")
        previous = stamp
    if value["intent"] not in CHOICES:
        raise ValueError("invalid_hint_intent")
    scores = value["raw_option_scores"]
    if scores is not None and (not isinstance(scores, dict) or set(scores) != set(CHOICES)
                               or not all(finite(v) for v in scores.values())):
        raise ValueError("invalid_hint_scores")
    text = value["text"]
    if text is not None and (not isinstance(text, str) or len(text) > 4096):
        raise ValueError("invalid_hint_text")
    if request is not None:
        for key in ("request_id", "clip_id", "profile_digest", "dependencies", "expires_at"):
            if value[key] != request.get(key):
                raise ValueError("hint_request_mismatch")
        for key in ("frames", "source_size", "roi_xyxy", "cutoff_at", "duration_seconds"):
            if evidence.get(key) != request.get(key):
                raise ValueError("hint_request_mismatch")
    return IntentHint(value["request_id"], value["clip_id"], value["profile_digest"],
                      value["snapshot_manifest_digest"], value["model_revision"], _freeze(deps), _freeze(evidence),
                      float(produced), float(expires), value["intent"], _freeze(scores), text)
