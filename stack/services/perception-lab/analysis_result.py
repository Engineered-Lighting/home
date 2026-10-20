"""Versioned saved-analysis judgments; no probabilities inferred from prose."""
from reasoning import ACTIVITIES

SCHEMA_VERSION = "lab-activity-1"


def render_result(request, result):
    """A judgment describes the cutoff, not all frames used to reach it.

    Invalid model output is distinct from uncertain execution: a matching terminal
    receipt can prove resource release even when its answer is unusable.
    """
    invalid = {"status": "invalid_output", "reason": "activity_schema_invalid"}
    if (not isinstance(result, dict)
            or set(result) != {"schema_version", "activity", "evidence_refs"}
            or result.get("schema_version") != SCHEMA_VERSION
            or not isinstance(result.get("activity"), str)
            or result["activity"] not in ACTIVITIES):
        return invalid
    refs = result["evidence_refs"]
    permitted = {f["media_id"] for f in request["frames"]}
    permitted.update(o["id"] for o in request["evidence"] if isinstance(o.get("id"), str))
    if (not isinstance(refs, list) or len(refs) > 64
            or any(not isinstance(ref, str) or ref not in permitted for ref in refs)
            or len(refs) != len(set(refs))):
        return invalid
    activity = result["activity"]
    abstain = activity == "insufficient_evidence"
    if not abstain and not refs:
        return invalid
    cutoff = request["window"]["cutoff"]
    end = request["judgment_end_s"]
    output = {"status": "abstain" if abstain else "completed",
        "judgment": {"activity": activity, "confidence": None,
            "schema_version": SCHEMA_VERSION, "evidence_refs": list(refs)},
        "judgment_start_s": cutoff, "judgment_end_s": end,
        "confidence_kind": "not_supplied", "execution": "offline"}
    if end > cutoff:
        output["prediction"] = {"time_basis": "session_relative_seconds",
            "start": cutoff, "end": end, "available_at": cutoff,
            "valid_until": end, "activities": [] if abstain else [activity],
            "mode": request["mode"], "availability_kind": "offline_cutoff_not_delivery"}
    return output
