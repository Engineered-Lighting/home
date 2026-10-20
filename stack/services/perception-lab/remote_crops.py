"""Bounded pose proposals for matched remote execution; never human labels."""
from copy import deepcopy
from reasoning import canonical,digest


def pose_bundle(value,frame,max_people):
    identity=('site_id','session_id','camera_id','capture_epoch','frame_id')
    if not isinstance(value,dict) or any(not frame.get(k) or value.get(k)!=frame[k] for k in identity):
        raise ValueError('remote crops belong to another observation')
    crops=value.get('crops')
    keys={'id','frame_id','detector_version','xyxy','detector_artifact_sha256','source_event_id'}
    if (not isinstance(crops,list) or len(crops)>max_people
            or any(not isinstance(c,dict) or set(c)-keys for c in crops)
            or value.get('crop_set_digest')!=digest(crops)):
        raise ValueError('bounded exact crop set required')
    result={k:deepcopy(value[k]) for k in (*identity,'crops','crop_set_digest')}
    # Keep only the structured source reference, never arbitrary enclosing data.
    for name,allowed in (('reference',{'arm','generation','profile_id','model_version','engine_epoch','artifact_sha256'}),
                         ('policy',{'minimum_confidence','max_people','revision'})):
        if name in value:
            if not isinstance(value[name],dict) or set(value[name])-allowed:
                raise ValueError('unexpected crop provenance fields')
            result[name]=deepcopy(value[name])
    for name in ('source_event_id','available_at'):
        if name in value: result[name]=deepcopy(value[name])
    if len(canonical(result))>32768: raise ValueError('crop metadata exceeds bound')
    return result
