"""Strict causal clip contracts; all timestamps are caller monotonic seconds.

Frames are immutable P6 RGB PPM blobs below the operator's frame_root. A
request carries exactly 32 sample slots, which may explicitly repeat an older
source frame. No path, prompt, model name, or URL is supplied to the model by
an HTTP caller. The request names its sampling rule; only SAMPLING is admitted.
"""
from dataclasses import dataclass, asdict
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re

HEX64 = re.compile(r"[0-9a-f]{64}\Z")
IDENT = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
# Den lineage values such as the transform "den-oak@2026-11-13.1" carry "@".
# They are only compared for equality and echoed; never a path, URL or unit
# name. The Den side (den-live intent.py) uses this identical grammar.
LINEAGE = re.compile(r"[A-Za-z0-9_.:@-]{1,128}\Z")
LINEAGE_FIELDS = frozenset(("transform_id",))
CHOICES = ("continue", "stop", "turn_left", "turn_right", "unclear")
QUESTION = ("Based only on this person's observed motion, which near-term movement "
            "intent is best supported? Choose unclear when the evidence is insufficient. "
            "Left and right refer to the person's own direction of travel.")
DEPENDENCIES = ("camera_id", "service_run_id", "camera_epoch", "source_generation", "track_id", "association_revision",
                "transform_id", "scan_revision", "calibration_revision")
FRAME_REF_KEYS = ("service_run_id", "camera_epoch", "frame_id", "camera_session", "mode", "rgb", "right", "depth",
                  "depth_calibration_sha256", "transform_id", "scan_revision", "capture_mono", "arrival_mono", "capture_ts")
# The one admitted sampling rule: 32 uniform causal slot centres, each holding
# the newest frame captured at or before it for at most MAX_HOLD_SECONDS, with
# repeats marked and true capture times kept. Den sends this identifier in every
# request, the profile names it (so the admission receipt, which pins the
# profile digest, names it too) and every hint echoes it. v1 (0.4 s) is refused.
SAMPLING = "causal_slot_center_sample_hold/v2;max_hold_s=0.5"
MAX_HOLD_SECONDS = .5


class ContractError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def profile_digest(profile):
    return hashlib.sha256(canonical(profile).encode()).hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContractError("duplicate_json_key")
            result[key] = value
        return result
    def invalid(_):
        raise ContractError("nonfinite_json")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def ident(value):
    return isinstance(value, str) and bool(IDENT.fullmatch(value))


def binding_values_valid(dependencies):
    """Lineage grammar for LINEAGE_FIELDS only; every other binding stays IDENT."""
    return all(isinstance(value, str) and bool((LINEAGE if name in LINEAGE_FIELDS else IDENT).fullmatch(value))
               for name, value in dependencies.items())


def keys(value, expected, reason):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ContractError(reason)


def below(root, reference):
    """Require a regular, non-symlink blob with a relative POSIX reference."""
    if not isinstance(reference, str) or len(reference) > 240 or "\\" in reference:
        raise ContractError("invalid_blob_ref")
    rel = PurePosixPath(reference)
    if rel.is_absolute() or not rel.parts or any(p in (".", "..") for p in rel.parts) or ":" in reference:
        raise ContractError("invalid_blob_ref")
    root = Path(root).resolve(strict=True)
    path = root
    for part in rel.parts:
        path = path / part
        if path.is_symlink():
            raise ContractError("symlink_blob_ref")
    resolved = path.resolve(strict=True)
    if root not in resolved.parents or not resolved.is_file():
        raise ContractError("blob_outside_root")
    return resolved


def read_ppm(path, width, height):
    """Canonical PPM only; bounded reads and no third-party image decoder."""
    expected = f"P6\n{width} {height}\n255\n".encode("ascii")
    count = width * height * 3
    if path.stat().st_size != len(expected) + count:
        raise ContractError("frame_size_mismatch")
    with path.open("rb") as source:
        header = source.read(len(expected))
        rgb = source.read(count + 1)
    if header != expected or len(rgb) != count:
        raise ContractError("invalid_rgb_ppm")
    return rgb


def store_rgb_blob(frame_root, rgb, width, height):
    """CPU-only bridge helper; immutable content-addressed canonical RGB PPM."""
    if (type(width) is not int or type(height) is not int or not 16 <= width <= 4096
            or not 16 <= height <= 4096 or width * height > 4096 * 2160
            or not isinstance(rgb, bytes) or len(rgb) != width * height * 3):
        raise ContractError("invalid_rgb_bytes")
    root = Path(frame_root).resolve(strict=True)
    data = f"P6\n{width} {height}\n255\n".encode() + rgb
    digest = hashlib.sha256(data).hexdigest()
    reference = digest + ".ppm"
    path = root / reference
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ContractError("existing_blob_mismatch")
    else:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    return {"blob_ref": reference, "sha256": digest}


def validate_lineage(frame, dependencies, source_size, cutoff):
    source = frame["source_frame"]
    keys(source, FRAME_REF_KEYS, "incomplete_source_frame")
    if (source["service_run_id"] != dependencies["service_run_id"]
            or type(source["camera_epoch"]) is not int or source["camera_epoch"] < 0
            or str(source["camera_epoch"]) != dependencies["camera_epoch"]
            or type(source["frame_id"]) is not int or source["frame_id"] < 0
            or str(source["frame_id"]) != frame["frame_id"]
            or source["transform_id"] != dependencies["transform_id"]
            or source["scan_revision"] != dependencies["scan_revision"]
            or source["depth_calibration_sha256"] != dependencies["calibration_revision"]
            or not ident(source["camera_session"]) or source["mode"] != "rgb"):
        raise ContractError("source_frame_binding_mismatch")
    for name in ("capture_mono", "arrival_mono", "capture_ts"):
        if not finite(source[name]):
            raise ContractError("unknown_source_timing")
    if (source["capture_mono"] != frame["captured_at"]
            or not source["capture_mono"] <= source["arrival_mono"] <= cutoff):
        raise ContractError("noncausal_source_arrival")
    for name in ("rgb", "right", "depth"):
        plane = source[name]
        if plane is None and name != "rgb":
            continue
        keys(plane, ("seq", "device_s", "host_s", "width", "height"), "incomplete_source_plane")
        if (type(plane["seq"]) is not int or plane["seq"] < 0
                or not finite(plane["host_s"]) or not finite(plane["device_s"])
                or type(plane["width"]) is not int or type(plane["height"]) is not int
                or not 1 <= plane["width"] <= 8192 or not 1 <= plane["height"] <= 8192):
            raise ContractError("unknown_source_plane_timing_or_size")
    if [source["rgb"]["width"], source["rgb"]["height"]] != source_size:
        raise ContractError("source_rgb_size_mismatch")
    return source


def validate_request(value, *, profile, now):
    keys(value, ("schema", "request_id", "clip_id", "profile_digest", "dependencies", "frames",
                 "roi_xyxy", "source_size", "cutoff_at", "expires_at", "duration_seconds",
                 "mode", "sampling"), "invalid_request_keys")
    if value["schema"] != "den.intent-request/v1" or value["profile_digest"] != profile_digest(profile):
        raise ContractError("profile_or_schema_mismatch")
    if value["sampling"] != SAMPLING or (profile.get("inputs") or {}).get("sampling") != SAMPLING:
        raise ContractError("unsupported_sampling")
    if not ident(value["request_id"]) or not ident(value["clip_id"]):
        raise ContractError("invalid_request_identity")
    keys(value["dependencies"], DEPENDENCIES, "invalid_dependencies")
    if not binding_values_valid(value["dependencies"]):
        raise ContractError("invalid_dependencies")
    dep = value["dependencies"]
    if (not re.fullmatch(r"0|[1-9][0-9]*", dep["camera_epoch"])
            or not re.fullmatch(r"0|[1-9][0-9]*", dep["association_revision"])
            or not HEX64.fullmatch(dep["calibration_revision"])
            or dep["source_generation"] != hashlib.sha256(canonical(
                [dep["service_run_id"], int(dep["camera_epoch"])]).encode()).hexdigest()):
        raise ContractError("invalid_generation_binding")
    if value["mode"] not in ("choose", "chat"):
        raise ContractError("invalid_mode")
    cutoff, expiry, duration = value["cutoff_at"], value["expires_at"], value["duration_seconds"]
    if (not all(finite(v) for v in (cutoff, expiry, duration)) or duration not in (4, 8)
            or cutoff > now or not now < expiry <= cutoff + 10 or cutoff < now - 10):
        raise ContractError("invalid_clip_time")
    size = value["source_size"]
    if (not isinstance(size, list) or len(size) != 2 or not all(type(v) is int for v in size)
            or not all(16 <= v <= 4096 for v in size) or size[0] * size[1] > 4096 * 2160):
        raise ContractError("invalid_source_size")
    roi = value["roi_xyxy"]
    if (not isinstance(roi, list) or len(roi) != 4 or not all(type(v) is int for v in roi)
            or not 0 <= roi[0] < roi[2] <= size[0] or not 0 <= roi[1] < roi[3] <= size[1]
            or roi[2] - roi[0] != roi[3] - roi[1]):
        raise ContractError("fixed_square_roi_required")
    frames = value["frames"]
    if not isinstance(frames, list) or len(frames) != 32:
        raise ContractError("exactly_32_sample_slots_required")
    seen, previous_time, previous_slot, camera_session = {}, float("-inf"), float("-inf"), None
    for i, frame in enumerate(frames):
        keys(frame, ("frame_id", "blob_ref", "sha256", "captured_at", "sample_at", "repeated",
                     "track_bbox_xyxy", "source_frame"), "invalid_frame_ref")
        if (not ident(frame["frame_id"]) or not isinstance(frame["sha256"], str)
                or not HEX64.fullmatch(frame["sha256"]) or type(frame["repeated"]) is not bool):
            raise ContractError("invalid_frame_identity")
        stamp, slot = frame["captured_at"], frame["sample_at"]
        expected_slot = cutoff - duration + (i + .5) * duration / 32
        # Slot centers are causal: sample-and-hold chooses the newest frame
        # available at each slot, never a future nearest-neighbor frame, and
        # holds it at most MAX_HOLD_SECONDS (so no stamp precedes the window by more).
        if (not finite(stamp) or not finite(slot) or abs(slot - expected_slot) > .00001
                or not cutoff - duration - MAX_HOLD_SECONDS <= stamp <= slot
                or stamp < previous_time or slot <= previous_slot or slot - stamp > MAX_HOLD_SECONDS):
            raise ContractError("noncausal_or_stale_frame")
        source = validate_lineage(frame, dep, size, cutoff)
        if camera_session is not None and camera_session != source["camera_session"]:
            raise ContractError("camera_session_changed")
        camera_session = source["camera_session"]
        identity = (frame["blob_ref"], frame["sha256"], stamp, canonical(source))
        duplicate = frame["frame_id"] in seen
        if duplicate != frame["repeated"] or (duplicate and seen[frame["frame_id"]] != identity):
            raise ContractError("repeat_identity_mismatch")
        seen[frame["frame_id"]] = identity
        # Every track box must fit the SAME crop. Recentered crops would alter
        # apparent motion; an incomplete track requires a different clip.
        box = frame["track_bbox_xyxy"]
        if (not isinstance(box, list) or len(box) != 4 or not all(finite(v) for v in box)
                or not roi[0] <= box[0] < box[2] <= roi[2]
                or not roi[1] <= box[1] < box[3] <= roi[3]):
            raise ContractError("track_exits_fixed_roi")
        reference = frame["blob_ref"]
        if (not isinstance(reference, str) or not re.fullmatch(r"[A-Za-z0-9_/-]+\.ppm", reference)
                or ".." in reference or reference.startswith("/")):
            raise ContractError("invalid_blob_ref")
        previous_time, previous_slot = stamp, slot
    # Defensive deep copy: the caller cannot mutate queued dependencies.
    return json.loads(canonical(value))


def load_rgb_frames(request, frame_root):
    frames, cache = [], {}
    w, h = request["source_size"]
    for frame in request["frames"]:
        key = frame["blob_ref"], frame["sha256"]
        if key not in cache:
            path = below(frame_root, frame["blob_ref"])
            raw = path.read_bytes() if path.stat().st_size <= w * h * 3 + 64 else b""
            if hashlib.sha256(raw).hexdigest() != frame["sha256"]:
                raise ContractError("frame_digest_mismatch")
            # Decode exactly the bytes hashed, avoiding a second mutable read.
            header = f"P6\n{w} {h}\n255\n".encode()
            if len(raw) != len(header) + w * h * 3 or not raw.startswith(header):
                raise ContractError("invalid_rgb_ppm")
            cache[key] = raw[len(header):]
        frames.append(cache[key])
    return frames


@dataclass(frozen=True)
class IntentHint:
    schema: str
    request_id: str
    clip_id: str
    profile_digest: str
    snapshot_manifest_digest: str
    model_revision: str
    dependencies: dict
    evidence: dict
    produced_at: float
    expires_at: float
    intent: str
    raw_option_scores: dict | None
    text: str | None
    uncertainty: str = "uncalibrated_semantic_hint"
    authority: str = "semantic_prior_only"

    def as_dict(self):
        return asdict(self)


def make_hint(request, profile, receipt_digest, output, now):
    intent = output.get("intent", "unclear")
    if not isinstance(receipt_digest, str) or not HEX64.fullmatch(receipt_digest):
        raise ContractError("invalid_snapshot_manifest_digest")
    if intent not in CHOICES:
        raise ContractError("invalid_backend_intent")
    scores = output.get("raw_option_scores")
    if scores is not None and (not isinstance(scores, dict) or set(scores) != set(CHOICES)
                               or not all(finite(v) for v in scores.values())):
        raise ContractError("invalid_backend_scores")
    text = output.get("text")
    if text is not None and (not isinstance(text, str) or len(text) > 4096):
        raise ContractError("invalid_backend_text")
    roi = request["roi_xyxy"]
    scale = 384 / (roi[2] - roi[0])
    return IntentHint(
        "den.intent-hint/v1", request["request_id"], request["clip_id"], request["profile_digest"],
        receipt_digest, profile["revision"], request["dependencies"],
        {"frames": request["frames"], "source_size": request["source_size"], "roi_xyxy": roi,
         "source_to_model": [[scale, 0, -roi[0] * scale], [0, scale, -roi[1] * scale], [0, 0, 1]],
         "cutoff_at": request["cutoff_at"], "duration_seconds": request["duration_seconds"],
         "sampling": request["sampling"], "additional_center_crop": False},
        now, request["expires_at"], intent, scores, text)
