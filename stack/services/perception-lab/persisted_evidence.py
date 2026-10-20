"""One provenance-preserving projection for live and saved reasoning inputs.

HA observations and historical caption receipts are shared context: they carry
no arm or generation until a policy-granted consumer attaches them to an arm.
"""
from reasoning import EVIDENCE_FIELDS, PROVENANCE_FIELDS, finite, plain_copy


def observation_record(event_id, kind, value, session):
    if (not isinstance(value, dict)
            or value.get("site_id") != session["site_id"]
            or value.get("camera_id") != session["camera_id"]
            or not all(finite(value.get(k)) for k in ("observed_at", "available_at", "valid_until"))
            or value["valid_until"] < value["observed_at"]):
        return None
    if kind in {"perception.output","room.observation","room.facts"}:
        if (value.get("status") != "completed" or value.get("arm") not in {"a", "b"}
                or type(value.get("generation")) is not int or value["generation"] < 1):
            return None
        fields = value.get("result")
        if not isinstance(fields, dict):
            return None
        # A worker result cannot smuggle in shared context fields. The
        # controller's room.facts record (room_facts.RoomFactsStage) carries
        # only its words; the policy re-derives it from this same row.
        allowed = ({"room_facts"} if kind == "room.facts"
                   else EVIDENCE_FIELDS - {"ha", "historical_caption", "qwen_scene", "vjepa_scene", "room_facts"})
    elif kind == "ha.observation":
        if not isinstance(value.get("ha"), dict):
            return None
        fields, allowed = value, {"ha", "quality"}
    elif kind == "caption.receipt":
        # Projection only: the local caption text, digests and rule never leave.
        projection = value.get("projection")
        if (not isinstance(projection, dict) or value.get("session_id") != session["id"]
                or value.get("first_received_at") != value["available_at"]):
            return None
        fields = {"historical_caption": {k: projection[k] for k in ("activity", "interpretation", "version") if k in projection},
                  "quality": {"correlated_with": {"relation": "same_camera_visual", "camera_id": value["camera_id"]}}
                  if value.get("origin") == "same_camera_visual" else {}}
        allowed = {"historical_caption", "quality"}
    else:
        return None
    record = {k: v for k, v in value.items() if k in PROVENANCE_FIELDS}
    record.update({k: plain_copy(v) for k, v in fields.items() if k in allowed})
    # The incumbent detector result omits image dimensions. Reuse only the
    # controller's verified media/frame binding, never infer size from boxes.
    snapshot=value.get('arm_snapshot',{})
    frame=snapshot.get('frame',{}) if isinstance(snapshot,dict) else {}
    if (kind=='perception.output' and fields.get('schema_version')==1
            and fields.get('coordinate_space')=='source_pixels'
            and isinstance(value.get('frame_id'),str)
            and fields.get('frame_id')==frame.get('id')==value['frame_id']
            and 'overlay_source_frame_not_verified' not in snapshot.get('validation',[])
            and all(type(frame.get(k)) is int and 0<frame[k]<=4096 for k in ('width','height'))):
        quality=record.setdefault('quality',{})
        if isinstance(quality,dict):
            for key,source_key in (('image_width','width'),('image_height','height')):
                if key not in quality:quality[key]=frame[source_key]
    # Envelope identity/provenance cannot be replaced by fields inside a model
    # response. Preserve common source roots so fusion can detect correlation.
    roots = value.get("evidence_roots")
    if roots is not None and (not isinstance(roots, list) or not roots or len(roots) > 64
            or any(not isinstance(root, str) or not root or len(root) > 256 for root in roots)):
        return None
    record.update(id=str(event_id), kind="observation", session_id=session["id"],
                  source_kind=kind,
                  evidence_roots=list(dict.fromkeys(roots)) if roots else [str(event_id)])
    if kind in {"ha.observation", "caption.receipt"}:
        record.update(arm=None, generation=None)
    if kind == "caption.receipt":
        record.pop("frame_id", None)  # The pilot's frame, not a Lab frame.
    return record
