"""Score independently witnessed optical acquisition bounds for one RGB sofa session."""

import math
import re


REFERENCE_SCHEMA = "den-sofa-clock-reference-1"
REFERENCE_BASIS = "independent_acquisition_bounds"
PLANES = ("rgb", "left", "right", "depth")
EXPOSURE_PLANES = ("rgb", "left", "right")


def _identity(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is not None


def _seconds(value):
    return type(value) in (float, int) and 0 <= value < 1e9


def _camera_rows(samples):
    rows = []
    previous = None
    for metadata in samples:
        session = metadata["session"]
        planes = metadata["frames"]
        if not _identity(session):
            raise ValueError("Missing or invalid camera session")
        if previous is not None and session != previous[0][0]:
            raise ValueError("Camera session changed during capture")
        for plane in PLANES:
            frame = planes[plane]
            sequence = frame["sequence"]
            if type(sequence) is not int or sequence < 0 or any(
                not _seconds(frame[field])
                for field in ("host_monotonic_timestamp_seconds", "device_timestamp_seconds")
            ):
                raise ValueError(f"Invalid {plane} frame sequence or timestamp")
            duration = frame.get("exposure_duration_seconds")
            if duration is not None and (not _seconds(duration) or duration == 0):
                raise ValueError(f"Invalid {plane} exposure duration")
            if previous is not None and any(
                frame[field] <= previous[1][plane][field]
                for field in ("sequence", "host_monotonic_timestamp_seconds", "device_timestamp_seconds")
            ):
                raise ValueError(f"{plane} frame sequence or timestamp did not advance")
        key = (session, planes["rgb"]["sequence"], planes["depth"]["sequence"])
        previous = key, planes
        rows.append(previous)
    return rows


def reference_template(samples):
    """List exact capture keys for an independent witness to fill in."""
    return {
        "schema": REFERENCE_SCHEMA,
        "basis": None,
        "evidence_id": None,
        "clock_drift_ppm": None,
        "receiver_drift_ppm": None,
        "samples": [
            {
                "frame_key": list(key),
                "camera_metadata": {
                    plane: {
                        "sequence": frames[plane]["sequence"],
                        "host_monotonic_timestamp_seconds": _stamp(frames, plane),
                        "device_timestamp_seconds": frames[plane]["device_timestamp_seconds"],
                        "exposure_duration_seconds": frames[plane].get("exposure_duration_seconds"),
                    }
                    for plane in PLANES
                },
                "exposures": {
                    plane: {
                        "sequence": frames[plane]["sequence"],
                        "start_lower_s": None,
                        "end_upper_s": None,
                    }
                    for plane in EXPOSURE_PLANES
                },
            }
            for key, frames in _camera_rows(samples)
        ],
    }


def _reference_rows(reference, keys):
    if reference.get("schema") != REFERENCE_SCHEMA:
        raise ValueError("Unknown independent clock reference schema")
    if reference.get("basis") is None:
        raise LookupError("Independent acquisition bounds are missing")
    if reference["basis"] != REFERENCE_BASIS or not _identity(reference.get("evidence_id")):
        raise ValueError("Independent acquisition basis and evidence ID are required")
    for field in ("clock_drift_ppm", "receiver_drift_ppm"):
        rate = reference.get(field)
        if rate is None:
            raise LookupError(f"Independent {field} bound is missing")
        if type(rate) is not int or not 0 <= rate <= 1000:
            raise ValueError(f"Invalid {field} bound")
    evidence = {}
    for row in reference["samples"]:
        key = tuple(row["frame_key"])
        if (len(key) != 3 or not _identity(key[0])
                or any(type(sequence) is not int or sequence < 0 for sequence in key[1:])):
            raise ValueError("Invalid reference frame key")
        if key in evidence or key not in keys:
            raise ValueError("Reference includes a duplicate or unmatched frame key")
        evidence[key] = row["exposures"]
    if evidence.keys() != keys:
        raise LookupError("Independent acquisition bounds are missing for captured bundles")
    return evidence


def _exposure(frame, bounds, plane):
    if type(bounds.get("sequence")) is not int or bounds["sequence"] != frame["sequence"]:
        raise ValueError(f"Independent {plane} exposure does not match the captured plane")
    start = bounds.get("start_lower_s")
    end = bounds.get("end_upper_s")
    if start is None or end is None:
        raise LookupError(f"Independent {plane} exposure bounds are missing")
    if not _seconds(start) or not _seconds(end) or start >= end:
        raise ValueError(f"Invalid independent {plane} exposure interval")
    duration = frame.get("exposure_duration_seconds")
    if duration is None and plane == "rgb":
        raise LookupError("Camera RGB exposure duration is missing")
    if duration is not None and end - start + math.ulp(end) + math.ulp(start) < duration:
        raise ValueError(f"Independent {plane} interval is shorter than the reported exposure")
    return start, end


def _stamp(frames, plane):
    return frames[plane]["host_monotonic_timestamp_seconds"]


def _outward_seconds(value):
    """Round up to the nanoseconds consumed by the camera release."""
    return math.ceil(math.nextafter(value, math.inf) * 1e9) / 1e9 if value > 0 else 0.0


def _separation(rgb, depth):
    return _outward_seconds(max(rgb[1] - depth[0], depth[1] - rgb[0]))


def _elapsed_upper(value, ppm):
    return _outward_seconds(value * 1_000_000 / (1_000_000 - ppm))


def _score(rows, reference):
    evidence = _reference_rows(reference, {key for key, _ in rows})
    clock_ppm = reference["clock_drift_ppm"]
    receiver_ppm = reference["receiver_drift_ppm"]
    margins = {plane: {"before_stamp_s": 0.0, "after_stamp_s": 0.0} for plane in ("rgb", "depth")}
    worst_separation = 0.0
    for key, frames in rows:
        rgb, left, right = (
            _exposure(frames[plane], evidence[key][plane], plane) for plane in EXPOSURE_PLANES
        )
        depth = (min(left[0], right[0]), max(left[1], right[1]))
        worst_separation = max(worst_separation, _separation(rgb, depth))
        for plane, (start, end) in (("rgb", rgb), ("depth", depth)):
            stamp = _stamp(frames, plane)
            margin = margins[plane]
            margin["before_stamp_s"] = max(margin["before_stamp_s"], _outward_seconds(stamp - start))
            margin["after_stamp_s"] = max(margin["after_stamp_s"], _outward_seconds(end - stamp))

    for plane, margin in margins.items():
        # The camera release applies margins in Python camera monotonic time.
        for field, value in margin.items():
            margin[field] = _elapsed_upper(value, clock_ppm)
        # The existing camera profile requires a positive before-stamp margin and caps each side at 1 s.
        margin["before_stamp_s"] = max(margin["before_stamp_s"], 1e-9)
        if max(margin.values()) > 1:
            raise ValueError(f"Observed {plane} margins exceed the camera profile range")

    candidate_separations = []
    for key, frames in rows:
        delta = _stamp(frames, "rgb") - _stamp(frames, "depth")
        delta_uncertainty = _elapsed_upper(abs(delta) * clock_ppm / 1_000_000, clock_ppm)
        rgb, depth = margins["rgb"], margins["depth"]
        separation = max(
            delta + rgb["after_stamp_s"] + depth["before_stamp_s"],
            -delta + rgb["before_stamp_s"] + depth["after_stamp_s"],
        ) + delta_uncertainty
        candidate_separations.append({
            "frame_key": list(key), "separation_s": _elapsed_upper(separation, receiver_ppm),
        })
    return {
        "status": "measured",
        "scored": len(rows),
        "max_exposure_separation_s": _elapsed_upper(_elapsed_upper(worst_separation, clock_ppm), receiver_ppm),
        "max_candidate_separation_s": max(row["separation_s"] for row in candidate_separations),
        "candidate_separations": candidate_separations,
        "candidate": {
            "evidence_id": reference["evidence_id"],
            "clock_drift_ppm": reference["clock_drift_ppm"],
            "receiver_drift_ppm": reference["receiver_drift_ppm"],
            **margins,
        },
    }


def score_clock(samples, reference=None):
    """Measure the supplied witness evidence for this session, without deploying a profile.

    Bounds must enclose complete optical acquisition in SDK host-monotonic seconds,
    including readout and uncertainty in the independent clock conversion. The supplied
    drift allowances also need independent validation. Separation bounds are converted
    to receiver seconds but do not cover uncertainty of issuing a future camera receipt.
    Metadata skew is diagnostic only.
    """
    result = {"captured": len(samples), "scored": 0}
    try:
        rows = _camera_rows(samples)
        if len(rows) < 2:
            raise LookupError("At least two distinct bundles are needed to check clock continuity")
        result["max_rgb_depth_stamp_delta_s"] = max(
            abs(_stamp(frames, "rgb") - _stamp(frames, "depth")) for _, frames in rows
        )
        result["rgb_stamp_span_s"] = _stamp(rows[-1][1], "rgb") - _stamp(rows[0][1], "rgb")
        if reference is None:
            raise LookupError("No independent whole-acquisition bounds were supplied")
        if not isinstance(reference, dict):
            raise ValueError("Independent clock reference must be a JSON object")
        result.update(_score(rows, reference))
    except KeyError as exc:
        result.update(status="invalid", reason=f"Missing required field {exc.args[0]}")
    except LookupError as exc:
        result.update(status="unavailable", reason=str(exc))
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        result.update(status="invalid", reason=str(exc))
    return result
