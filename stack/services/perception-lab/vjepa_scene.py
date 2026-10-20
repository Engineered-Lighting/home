"""V-JEPA 2.1 den evidence: pins, clip request, result validation, ledger payloads,
causal history and the JEV projection.

Pure functions only: no store, network, clock or model access. The LA worker
(tools/la-vjepa-worker/vjepa_worker.py) and this module must agree on PINS,
INPUT_PROFILE, the request schema and the result schema; its tests compare the
two literal copies.

Privacy: the ledger keeps the worker's raw probabilities and scalars locally
(they are this Lab's own measurements). Only bucket words leave toward JEV:
never a probability, embedding, drift value, box, timing or identifier. Invalid
results are stored with no result and are never projected.

Calibration: every probe here was trained on other rooms and cameras (LA home
recordings and NTU), none on the Victoria den. The bucket thresholds are
provisional, versioned constants (BUCKETS_VERSION) chosen before any den
replay; every projection says calibration: unvalidated.
"""
import base64
import hashlib
import json
import math
import re

# --- Pinned LA artifacts (read-only probe audit on EngineeredLightingServer1, 2026-09-29;
# re-verified at probe commit 67853d2f: sources, heads and weights unchanged)
PINS = {
    "encoder": {"file": "vjepa2_1_vitb_dist_vitG_384.pt", "selector": "ema_encoder",
                "sha256": "848a77c33cc9e6649ed2119c9bea1e2c569bcdab9539ff3e7c02ccc2959ddf4d", "bytes": 1664223428,
                "bundle": "61ab0b0c0be0e741daba599af1cb8593e85fa3875fc36fbbc24711defb9f9659",
                "meta_source_commit": "204698b45b3712590f06245fbfba32d3be539812"},
    "probe_commit": "67853d2f85a5d4a072023b90bc48043a3994bf76",
    "posture": ["d2b405993fe22251d4417239fcd2535e9144617dd02eb9c6df00a7ac491d934a",
                "628c1e6094c129f939f9a834bf61fb181bfed080bb41c860048c8f7a95e2e039",
                "1afec41295fe0f44c45d9423a471e8c813a68452849b8fd38b333a47983f613b"],
    "activity": ["618d80c3b71efb3862b0398f19fcadbb20c0a284f9bed8c002d36c5e912dc40d",
                 "3478f4f4e9d58320b72c7f059cd8592c6af4dc9f3d2c60857f3bb5d15d58e8a2",
                 "11f6bb03c3b0a48a9847ef09d55a5d76eea6ccbb6ed33c3e42234f79c881066d"],
    "ntu": "1dcfa5ec8328c1a4e006bff575784819d56b4109d5120141030881dffa6dc3af",
}
MODEL_VERSION = "vjepa2.1-vitb-384-frozen+home-v2-20260908+ntu23-e13"
# Crop and timing the worker applies (mirrors probe0 Jepa.profile except the
# source cadence: den previews are ~2 Hz, so frames are nearest-within-0.25 s
# to the 0.5 s grid rather than causal-within-100 ms of a video stream).
INPUT_PROFILE = "den-preview-640x360-8f-3.5s-union-box-square-1.5-bilinear-384-bf16/v1"
DRIFT_VERSION = "vjepa-token-drift-1"
BUCKETS_VERSION = "vjepa-den-buckets-1"
REVISIONS = {"encoder": "vjepa2.1-vitb-384-ema-848a77c3", "posture": "home-v2-20260908-posture-household-seed012",
             "reading": "home-v2-20260908-activity-mixed_standard-seed012-reading",
             "zero_shot": "probe0-v3-ntu23-finetune-unfreeze-0-e13", "motion": DRIFT_VERSION, "buckets": BUCKETS_VERSION}

REQUEST_SCHEMA = "vjepa-clip-request-1"
RESULT_SCHEMA = "vjepa-clip-result-1"
RECEIPT_SCHEMA = "vjepa-observation-1"
PROJECTION_SCHEMA = "vjepa-den-projection-1"
EGRESS_GRANT = "vjepa-den-1"
FAMILY = "temporal_encoder"
ORIGIN = ORIGIN_FAMILY = "same_camera_visual"
METHOD = "vjepa_frozen_probe_buckets"
PROFILE_FIELDS = frozenset({"model_version", "artifact_sha256", "runtime_revision", "engine_epoch", "input_profile"})

FRAME_COUNT = 8
SPAN_SECONDS = 3.5
STEP_SECONDS = SPAN_SECONDS / (FRAME_COUNT - 1)
MIN_SPAN, MAX_SPAN = 3.0, 4.0
MIN_GAP, MAX_GAP = .25, .75
IMAGE_SIZE = (640, 360)
MAX_IMAGE_BYTES = 256 * 1024
MAX_AGE_SECONDS = 6
HORIZON_SECONDS = 60
MAX_RESULT_BYTES = 8192

POSTURES = ("sitting", "standing", "walking", "lying_down", "bending", "exercising_pose")
NTU_CLASSES = ("brushing_hair", "brushing_teeth", "check_watch", "clapping", "drinking", "eating", "fall", "fan_self",
               "hand_waving", "hopping", "jump_up", "phone_call", "phone_or_tablet", "reading", "remove_jacket",
               "remove_shoe", "sit_down", "stand_up", "typing", "wear_jacket", "wear_shoe", "wipe_face", "writing")
NTU_MAP = {"computer_work": ("typing",), "phone_use": ("phone_or_tablet", "phone_call"), "reading": ("reading", "writing")}
ZERO_SHOT = ("phone_use", "computer_work", "reading")
ZERO_SHOT_STATUSES = ("supplied", "single_person_gate_not_met")
TIMINGS = ("decode", "crop", "encode", "heads", "total")

# Provisional, uncalibrated bucket bounds (BUCKETS_VERSION). Lower bounds, high first.
POSTURE_CONFIDENCE = ((.7, "high"), (.4, "medium"))
READING_SIGNAL = ((.6, "high"), (.3, "medium"))
ZERO_SHOT_SIGNAL = ((.5, "high"), (.2, "medium"))
# Motion: mean (1 - cosine) between consecutive temporal token slices of one clip.
MOTION = ((.04, "still"), (.12, "some"))  # upper bounds; at or above the last is active
# Scene change: 1 - cosine of mean-pooled tokens against the immediately previous clip.
SCENE_CHANGE_AT = .08

AGE_BUCKETS = ((3, "fresh_under_3s"), (10, "about_5_s"), (30, "about_20_s"), (60, "about_40_s"))
COUNT_WORDS = ("none", "one", "few", "several", "many")
AGREEMENT_WORDS = ("all", "most", "some", "one", "none")
INVALID_REASONS = ("result_unbound", "not_object", "nonfinite_or_unserializable", "keys_mismatch", "identity_mismatch", "probability_invalid",
                   "posture_not_normalized", "zero_shot_invalid", "motion_invalid", "drift_invalid", "timings_invalid",
                   "result_too_large")
HEX64 = re.compile(r"[0-9a-f]{64}")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


ARTIFACT_SHA256 = digest(PINS)


def check_profile(profile, *, runtime_revision=None):
    """The exact worker profile a lease reported; engine_epoch is per session."""
    if (not isinstance(profile, dict) or set(profile) != PROFILE_FIELDS
            or any(not isinstance(v, str) or not 1 <= len(v) <= 128 for v in profile.values())
            or profile["model_version"] != MODEL_VERSION or profile["artifact_sha256"] != ARTIFACT_SHA256
            or profile["input_profile"] != INPUT_PROFILE
            or (runtime_revision is not None and profile["runtime_revision"] != runtime_revision)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", profile["engine_epoch"])):
        raise ValueError("exact pinned V-JEPA worker profile required")
    return dict(profile)


# --- Clip request ------------------------------------------------------------

def check_box(box, size=IMAGE_SIZE):
    if (not isinstance(box, (list, tuple)) or len(box) != 4 or not all(finite(v) for v in box)
            or not 0 <= box[0] < box[2] <= size[0] or not 0 <= box[1] < box[3] <= size[1]):
        raise ValueError("person box in source pixels required")
    return [float(v) for v in box]


def check_times(observed):
    if (len(observed) != FRAME_COUNT or not all(finite(t) for t in observed)
            or any(not MIN_GAP <= b - a <= MAX_GAP for a, b in zip(observed, observed[1:]))
            or not MIN_SPAN <= observed[-1] - observed[0] <= MAX_SPAN):
        raise ValueError("eight ordered frames spanning about 3.5 s required")
    return list(observed)


def request_id(*, session_id, media_ids, box, single_person, previous_request_id, profile):
    return digest({"schema": REQUEST_SCHEMA, "session_id": session_id, "media_ids": list(media_ids), "box": list(box),
                   "single_person": bool(single_person), "previous_request_id": previous_request_id,
                   "profile": dict(profile)})


def request_body(*, site_id, camera_id, session_id, profile, images, observed, media_ids, box, single_person,
                 previous_request_id=None):
    """The exact JSON the LA worker validates. Frames travel base64 and are
    never persisted there; the worker keeps only the previous pooled
    embedding in memory for drift."""
    profile = check_profile(profile)
    check_times(observed)
    box = check_box(box)
    if len(images) != FRAME_COUNT or len(media_ids) != FRAME_COUNT:
        raise ValueError("one image per frame required")
    for image in images:
        if type(image) is not bytes or not 0 < len(image) <= MAX_IMAGE_BYTES or not image.startswith(b"\xff\xd8"):
            raise ValueError("bounded JPEG required")
    if previous_request_id is not None and (not isinstance(previous_request_id, str) or not HEX64.fullmatch(previous_request_id)):
        raise ValueError("previous request identity invalid")
    identity = request_id(session_id=session_id, media_ids=media_ids, box=box, single_person=single_person,
                          previous_request_id=previous_request_id, profile=profile)
    return {"version": 1, "schema": REQUEST_SCHEMA, "request_id": identity, "site_id": site_id, "camera_id": camera_id,
            "session_id": session_id, "profile": profile, "person_box": box, "single_person": bool(single_person),
            "previous_request_id": previous_request_id,
            "frames": [{"observed_at": t, "jpeg_base64": base64.b64encode(image).decode("ascii")}
                       for t, image in zip(observed, images)]}


# --- Worker result -----------------------------------------------------------

RESULT_KEYS = frozenset({"version", "schema", "request_id", "engine_epoch", "artifact_sha256", "posture", "reading",
                         "zero_shot", "zero_shot_status", "motion", "scene_drift", "drift_reference", "timings_ms"})


def _probability(value):
    return finite(value) and 0 <= value <= 1


def validate_result(value, *, request):
    """(result, None) for an exact vjepa-clip-result-1 bound to request, else (None, reason). Never repaired."""
    if value is None:
        return None, "result_unbound"
    if not isinstance(value, dict):
        return None, "not_object"
    try:
        if len(canonical(value)) > MAX_RESULT_BYTES:
            return None, "result_too_large"
    except (TypeError, ValueError):
        return None, "nonfinite_or_unserializable"
    if set(value) != RESULT_KEYS:
        return None, "keys_mismatch"
    profile = request["profile"]
    if (value["version"] != 1 or value["schema"] != RESULT_SCHEMA or value["request_id"] != request["request_id"]
            or value["engine_epoch"] != profile["engine_epoch"] or value["artifact_sha256"] != profile["artifact_sha256"]):
        return None, "identity_mismatch"
    posture, reading = value["posture"], value["reading"]
    if (not isinstance(posture, dict) or set(posture) != set(POSTURES) or not all(_probability(p) for p in posture.values())
            or not _probability(reading)):
        return None, "probability_invalid"
    if abs(sum(posture.values()) - 1) > 1e-3:
        return None, "posture_not_normalized"
    zero, status = value["zero_shot"], value["zero_shot_status"]
    if status not in ZERO_SHOT_STATUSES or (status == "supplied") != (zero is not None) \
            or (status == "supplied" and not request["single_person"]):
        return None, "zero_shot_invalid"
    if zero is not None and (not isinstance(zero, dict) or set(zero) != set(ZERO_SHOT)
                             or not all(_probability(p) for p in zero.values()) or sum(zero.values()) > 1 + 1e-3):
        return None, "zero_shot_invalid"
    if not finite(value["motion"]) or not 0 <= value["motion"] <= 2:
        return None, "motion_invalid"
    drift, reference = value["scene_drift"], value["drift_reference"]
    if reference is None:
        if drift is not None:
            return None, "drift_invalid"
    elif reference != request.get("previous_request_id") or not finite(drift) or not 0 <= drift <= 2:
        return None, "drift_invalid"
    timings = value["timings_ms"]
    if (not isinstance(timings, dict) or set(timings) != set(TIMINGS)
            or not all(finite(v) and 0 <= v <= 60000 for v in timings.values())):
        return None, "timings_invalid"
    return json.loads(canonical(value)), None


# --- Ledger payload ----------------------------------------------------------

def build_observation(*, site_id, camera_id, session_id, request, frames, box_input, generated_at, result, body_sha256,
                      response_sha256):
    """A vjepa.observation draft; available_at is set by first_receipt().

    frames: the request's frames oldest first (media_id, frame_id,
    capture_epoch, observed_at). observed_at is the latest frame's and
    valid_until is observed_at + MAX_AGE_SECONDS. box_input records which
    detector outputs supplied the crop (event ids, frame count, gate).
    result is the worker's reply (None when none arrived bound); it is kept
    only when valid.
    """
    if len(frames) != FRAME_COUNT:
        raise ValueError("eight request frames required")
    for frame in frames:
        if not isinstance(frame.get("media_id"), str) or not isinstance(frame.get("frame_id"), str) or not finite(frame.get("observed_at")):
            raise ValueError("frame provenance required")
    observed = check_times([f["observed_at"] for f in frames])
    latest = observed[-1]
    if not finite(generated_at) or generated_at < latest:
        raise ValueError("generation cannot precede the observed frame")
    checked, reason = validate_result(result, request=request)
    profile = request["profile"]
    events = [e for e in box_input.get("event_ids", []) if isinstance(e, (int, str))][:32]
    return {"schema": RECEIPT_SCHEMA, "result_schema": RESULT_SCHEMA, "site_id": site_id, "camera_id": camera_id,
            "session_id": session_id, "request_id": request["request_id"],
            "frame_ids": [f["frame_id"] for f in frames], "media_ids": [f["media_id"] for f in frames],
            "capture_epoch": frames[-1].get("capture_epoch"), "window_start": observed[0], "observed_at": latest,
            "generated_at": generated_at, "valid_until": latest + MAX_AGE_SECONDS, "max_age_seconds": MAX_AGE_SECONDS,
            "input": {"person_box": list(request["person_box"]), "box_frames": box_input.get("frames"),
                      "single_person": bool(request["single_person"]), "box_event_ids": events,
                      "previous_request_id": request.get("previous_request_id"),
                      # How clip frames were matched to detector outputs
                      # (vjepa_live nearest-detector-output-1), when supplied.
                      **({"box_match": dict(box_input["match"])} if isinstance(box_input.get("match"), dict) else {})},
            "profile": dict(profile), "revisions": dict(REVISIONS), "body_sha256": body_sha256,
            "response_sha256": response_sha256, "engine_epoch": profile["engine_epoch"],
            "artifact_sha256": profile["artifact_sha256"], "status": "valid" if checked else "invalid",
            "invalid_reason": reason, "result": checked, "origin": ORIGIN,
            "evidence_roots": ["media:" + f["media_id"] for f in frames] + ["event:%s" % e for e in events],
            "frames_scope": "not_retained_by_worker"}


def first_receipt(draft, now):
    if not finite(now) or now < draft["generated_at"]:
        raise ValueError("receipt clock precedes generation")
    return {**draft, "available_at": now, "first_received_at": now, "fingerprint": fingerprint(draft)}


def fingerprint(payload):
    return digest({k: payload.get(k) for k in ("request_id", "frame_ids", "media_ids", "observed_at", "window_start",
                                               "input", "body_sha256", "response_sha256", "status", "invalid_reason",
                                               "result", "engine_epoch", "artifact_sha256")})


def rejection(payload, reason):
    """Content-free rejection: identity, times and digests, never the result."""
    return {**{k: v for k, v in payload.items() if k != "result"}, "result": None, "reason": reason}


def admit(candidate, *, prior=None, latest_observed_at=None, now):
    """First receipt wins: accept, duplicate, identity_conflict, out_of_order or expired_on_receipt."""
    if prior is not None:
        return "duplicate" if prior.get("fingerprint") == fingerprint(candidate) else "identity_conflict"
    if latest_observed_at is not None and candidate["observed_at"] <= latest_observed_at:
        return "out_of_order"
    if candidate["valid_until"] <= now:
        return "expired_on_receipt"
    return "accept"


# --- Buckets, causal history and projection ----------------------------------

def _lower(value, bounds, last):
    for bound, word in bounds:
        if value >= bound:
            return word
    return last


def motion_word(value):
    for bound, word in MOTION:
        if value < bound:
            return word
    return "active"


def facts(result):
    """Bucket words for one valid result; no number survives."""
    top = max(POSTURES, key=lambda k: (result["posture"][k], -POSTURES.index(k)))
    zero = ("unavailable" if result["zero_shot"] is None else
            {k: _lower(result["zero_shot"][k], ZERO_SHOT_SIGNAL, "low") for k in ZERO_SHOT})
    change = ("no_prior" if result["scene_drift"] is None else
              "changed" if result["scene_drift"] >= SCENE_CHANGE_AT else "stable")
    return {"posture": {"top": top, "confidence": _lower(result["posture"][top], POSTURE_CONFIDENCE, "low")},
            "reading_signal": _lower(result["reading"], READING_SIGNAL, "low"), "zero_shot": zero,
            "motion": motion_word(result["motion"]), "scene_change": change}


def causal(receipt, cutoff):
    return (isinstance(receipt, dict)
            and all(finite(receipt.get(k)) for k in ("observed_at", "generated_at", "available_at", "valid_until"))
            and receipt["observed_at"] <= cutoff and receipt["generated_at"] <= cutoff
            and receipt["available_at"] <= cutoff and receipt.get("first_received_at") == receipt["available_at"])


def known_receipts(receipts, cutoff):
    known = [r for r in receipts if causal(r, cutoff)]
    return sorted(known, key=lambda r: (r["observed_at"], r["available_at"], r["request_id"]))


def _valid(receipt):
    if receipt.get("status") != "valid" or not isinstance(receipt.get("result"), dict):
        return None
    try:
        checked, reason = validate_result(receipt["result"], request={
            "request_id": receipt["request_id"], "profile": receipt.get("profile") or {},
            "single_person": (receipt.get("input") or {}).get("single_person"),
            "previous_request_id": (receipt.get("input") or {}).get("previous_request_id")})
    except (KeyError, TypeError, AttributeError):
        return None
    return checked if reason is None else None


def age_word(seconds):
    seconds = max(0.0, seconds)
    for bound, word in AGE_BUCKETS:
        if seconds < bound:
            return word
    return "over_60_s"


def count_word(count):
    return "none" if count <= 0 else "one" if count == 1 else "few" if count <= 3 else "several" if count <= 9 else "many"


def agreement_word(agree, total):
    if total <= 0 or agree <= 0:
        return "none"
    if agree == total:
        return "all"
    if agree == 1:
        return "one"
    return "most" if agree * 2 > total else "some"


def _mode(values):
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    best = max(counts.values())
    winners = [v for v, c in counts.items() if c == best]
    return (winners[0] if len(winners) == 1 else "mixed"), best


def summarize(known, cutoff):
    """Code-built 60 s history at cutoff from valid receipts only."""
    window = [r for r in known if cutoff - HORIZON_SECONDS <= r["observed_at"] <= cutoff]
    valid = [(r, _valid(r)) for r in window]
    valid = [(r, facts(v)) for r, v in valid if v is not None]
    invalid = len(window) - len(valid)
    summary = {"window": "last_60_s", "valid_samples": count_word(len(valid)), "invalid_samples": count_word(invalid)}
    if not valid:
        return {**summary, "posture": None, "reading_signal_high": "none", "zero_shot_high": None,
                "motion": None, "scene_changes": "none", "last_scene_change": "none_in_window"}
    posture, agree = _mode([f["posture"]["top"] for _, f in valid])
    motion, motion_agree = _mode([f["motion"] for _, f in valid])
    supplied = [f["zero_shot"] for _, f in valid if f["zero_shot"] != "unavailable"]
    zero = None if not supplied else {k: agreement_word(sum(1 for z in supplied if z[k] == "high"), len(supplied))
                                      for k in ZERO_SHOT}
    changes = [r for r, f in valid if f["scene_change"] == "changed"]
    return {**summary, "posture": {"value": posture, "agreement": agreement_word(agree, len(valid))},
            "reading_signal_high": agreement_word(sum(1 for _, f in valid if f["reading_signal"] == "high"), len(valid)),
            "zero_shot_high": zero, "motion": {"value": motion, "agreement": agreement_word(motion_agree, len(valid))},
            "scene_changes": count_word(len(changes)),
            "last_scene_change": age_word(cutoff - changes[-1]["observed_at"]) if changes else "none_in_window"}


def projection(receipts, cutoff):
    """The exact bounded structure JEV may get for the temporal_encoder family.

    Identical for every arm (no arm input). Enum words and booleans only. The
    latest known receipt supersedes older ones even when invalid or expired.
    """
    if not finite(cutoff):
        raise ValueError("finite cutoff required")
    known = known_receipts(receipts, cutoff)
    latest = known[-1] if known else None
    if latest is None:
        status, current = "stale", None
    else:
        state = "current" if cutoff < latest["valid_until"] else "expired_historical"
        checked = _valid(latest)
        status = "stale" if state != "current" else "supplied" if checked is not None else "invalid"
        current = {"state": state, "age": age_word(cutoff - latest["observed_at"]),
                   "facts": facts(checked) if checked is not None else None}
    return {"family": FAMILY, "schema": PROJECTION_SCHEMA, "status": status, "latest": current,
            "summary_last_60s": summarize(known, cutoff), "calibration": "unvalidated", "independent_confirmation": False,
            "origin_family": ORIGIN_FAMILY, "correlated_with": ORIGIN, "method": METHOD,
            "probe_revisions": dict(REVISIONS)}


def projection_tokens():
    """Every string a projection may contain, for egress allowlist checks."""
    words = set(POSTURES) | set(ZERO_SHOT) | {w for _, w in POSTURE_CONFIDENCE} | {"low", "still", "some", "active",
                                                                                   "stable", "changed", "no_prior",
                                                                                   "unavailable", "mixed"}
    words |= {w for _, w in AGE_BUCKETS} | {"over_60_s", "none_in_window", "last_60_s"}
    words |= set(COUNT_WORDS) | set(AGREEMENT_WORDS) | set(REVISIONS.values())
    words |= {FAMILY, PROJECTION_SCHEMA, "supplied", "stale", "invalid", "current", "expired_historical",
              "unvalidated", ORIGIN_FAMILY, METHOD}
    keys = {"family", "schema", "status", "latest", "state", "age", "facts", "posture", "top", "confidence",
            "reading_signal", "zero_shot", "motion", "scene_change", "summary_last_60s", "window", "valid_samples",
            "invalid_samples", "value", "agreement", "reading_signal_high", "zero_shot_high", "scene_changes",
            "last_scene_change", "calibration", "independent_confirmation", "origin_family", "correlated_with",
            "method", "probe_revisions"} | set(REVISIONS)
    return frozenset(words | keys)


def scene_record(projected, receipts, cutoff, *, site_id, camera_id, session_id):
    """One shared (arm-less) record carrying the projection, for integration.

    Never visual: it cannot make a packet current or dispatchable. Its id is
    stable while the summarized receipt set is unchanged.
    """
    known = known_receipts(receipts, cutoff)
    if not known:
        return None
    latest = known[-1]
    roots = list(dict.fromkeys(root for r in known[-4:] for root in r.get("evidence_roots", [])))[:64]
    members = [r["request_id"] for r in known if r["observed_at"] >= cutoff - HORIZON_SECONDS]
    return {"id": "vjepa-scene:" + digest([session_id, latest["request_id"], members])[:24], "kind": "observation",
            "source_kind": "vjepa.observation", "site_id": site_id, "camera_id": camera_id, "session_id": session_id,
            "observed_at": latest["observed_at"], "available_at": max(r["available_at"] for r in known),
            "valid_until": max(latest["valid_until"], latest["observed_at"] + HORIZON_SECONDS),
            "evidence_roots": roots, "vjepa_scene": projected,
            "quality": {"correlated_with": {"relation": ORIGIN, "camera_id": camera_id}}}


def shared_records(record, *, arms, generation):
    """The same record for every granted arm; only the arm tag differs."""
    if record is None:
        return {}
    return {arm: [{**json.loads(canonical(record)), "arm": arm, "generation": generation}] for arm in ("a", "b") if arm in arms}
