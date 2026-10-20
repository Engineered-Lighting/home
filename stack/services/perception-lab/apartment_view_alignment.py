"""Trusted approximate visual placement, separate from registration admission.

This pure module never fits, captures, writes, commands or qualifies geometry.
Its private document explicitly preserves a refused fit and a separate actual
image review. Only same-NPZ supported joints can enter the presentation lane.
An optional reviewed successor lets the unchanged alignment serve one verified
later camera session; it never rewrites the reviewed epoch or originals.
Browser/model dictionaries are not trusted configuration or authentication.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import re

SCHEMA='home-apartment-view-alignment-1'
PACKET_SCHEMA='home-apartment-approximate-room-pose-1'
LABEL='Approximate room placement'
SCOPE=('siteId','contentRevision','frameDigest','registrationVersion','cameraId','sessionId','generation')
HEX=re.compile(r'^[a-f0-9]{64}$')
NO_AUTHORITY=dict(operational_geometry=False,geometry_authority=False,identity_authority=False,
    engagement_authority=False,absence_authority=False,protection_authority=False,beam_authority=False,
    physical_permission=False,follow_allowed=False,lighting_authority=False)
EDGES=((0,1),(0,2),(1,3),(2,4),(5,6),(5,7),(7,9),(6,8),(8,10),
       (5,11),(6,12),(11,12),(11,13),(13,15),(12,14),(14,16))
EVIDENCE=('original_rgb_sha256','original_npz_sha256','original_calibration_sha256',
    'original_metadata_sha256','input_manifest_sha256','projection_review_sha256',
    'render_comparison_sha256','landmark_overlay_sha256')
SUCCESSOR_SCHEMA='home-apartment-view-alignment-successor-1'
SUCCESSOR_REVIEW_SCHEMA='home-apartment-view-alignment-successor-review-1'
COMPATIBILITY_SCHEMA='home-apartment-camera-session-compatibility-1'
SUCCESSOR_KEYS=('schema','version','status','label','scope','predecessor','matrix_sha256','mount','camera',
    'verification','metric_error_bound_m','owner_or_physical_calibration',*NO_AUTHORITY)
VERIFICATION_KEYS=('verified_epoch','calibration_sha256','calibration_content_id','npz_sha256','check_mount',
    'rgb_sha256','landmark_overlay_sha256','verified_at')
VERIFIED_AT=re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$')

class ViewAlignmentRefusal(ValueError):pass
def need(ok,reason):
    if not ok:raise ViewAlignmentRefusal(reason)
def finite(value):return type(value) in (int,float) and math.isfinite(value)
def text(value):return isinstance(value,str) and 0<len(value)<=256
def pin(value):return isinstance(value,str) and HEX.fullmatch(value) is not None
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def digest(value):return sha(encoded(value))
def _json(raw,maximum):
    need(type(raw) is bytes and 0<len(raw)<=maximum,'approximate_original_document_bytes_required')
    def unique(items):
        out={}
        for key,value in items:need(key not in out,'approximate_duplicate_document_key');out[key]=value
        return out
    try:return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:need(False,'approximate_nonfinite_document'))
    except (UnicodeDecodeError,json.JSONDecodeError,RecursionError) as error:raise ViewAlignmentRefusal('approximate_document_invalid') from error

def _matrix(value):
    need(isinstance(value,list) and len(value)==4 and all(isinstance(r,list) and len(r)==4
        and all(finite(n) for n in r) for r in value) and value[3]==[0,0,0,1],'approximate_rigid_matrix_required')
    for i in range(3):
        for j in range(3):need(abs(sum(value[k][i]*value[k][j] for k in range(3))-(1 if i==j else 0))<1e-6,'approximate_rigid_matrix_required')
    r=value;det=(r[0][0]*(r[1][1]*r[2][2]-r[1][2]*r[2][1])-r[0][1]*(r[1][0]*r[2][2]-r[1][2]*r[2][0])+r[0][2]*(r[1][0]*r[2][1]-r[1][1]*r[2][0]))
    need(abs(det-1)<1e-6,'approximate_right_handed_matrix_required');return value

@dataclass(frozen=True)
class ViewAlignment:
    """Immutable reviewed private configuration. Never supplied by the browser."""
    _document:bytes
    _review:bytes
    _candidate:bytes
    @property
    def document(self):return _json(self._document,131072)
    @property
    def alignment_sha256(self):return sha(self._document)
    @property
    def review_sha256(self):return sha(self._review)
    @property
    def retained_byte_allocations(self):
        """Actual immutable aliases for the incumbent owner's union budget."""
        return (self._document,self._review,self._candidate)

def bind_view_alignment(*,alignment_original_bytes,review_original_bytes,source_candidate_original_bytes):
    """Validate exact private bytes, retaining the original refused status.

    This binds presentation configuration only. It does not modify or pass the
    incumbent registered/diagnostic projector's independent acceptance gates.
    """
    try:return _bind(alignment_original_bytes,review_original_bytes,source_candidate_original_bytes)
    except ViewAlignmentRefusal:raise
    except (KeyError,TypeError,ValueError,OverflowError,AttributeError,IndexError,RecursionError) as error:
        raise ViewAlignmentRefusal('approximate_private_configuration_invalid') from error

def _bind(alignment_original_bytes,review_original_bytes,source_candidate_original_bytes):
    a=_json(alignment_original_bytes,131072);review=_json(review_original_bytes,16384);source=_json(source_candidate_original_bytes,512*1024)
    need(isinstance(a,dict) and a.get('schema')==SCHEMA and a.get('version')==1
        and a.get('status')=='approximate_visual_alignment' and a.get('label')==LABEL
        and a.get('metric_error_bound_m') is None and a.get('owner_or_physical_calibration') is False
        and all(a.get(k) is False for k in NO_AUTHORITY),'explicit_approximate_only_document_required')
    need(a['scope']==dict(site_id='victoria',map_id='victoria-den',room_id='den'),'approximate_scope_required')
    matrix=_matrix(a['matrix']);need(a['matrix_sha256']==digest(matrix),'approximate_matrix_digest_changed')
    pack=a['pack'];camera=a['camera'];mount=a['mount'];evidence=a['evidence'];s=a['source_candidate']
    need(text(pack['content_revision']) and type(pack['registration_version']) is int and pack['registration_version']>=0
        and pin(pack['frame_sha256']) and pin(pack['collision_sha256']) and camera['camera_id']=='den'
        and camera['source_frame']=='rectified_left' and text(camera['capture_epoch']) and pin(camera['calibration_content_id']),
        'approximate_exact_pack_camera_required')
    need(text(mount['revision']) and mount['signature_sha256']==digest(mount['signature']),'approximate_mount_identity_required')
    signature=mount['signature'];grid=signature['grid'];cells=signature['cells']
    need(grid==[8,8] and signature['shape']==[400,640] and isinstance(cells,list) and len(cells)==8
        and all(isinstance(r,list) and len(r)==8 and all(v is None or finite(v) and 0<v<=65535 for v in r) for r in cells),
        'approximate_mount_signature_required')
    need(set(evidence)==set(EVIDENCE) and all(pin(v) for v in evidence.values()),'approximate_actual_evidence_pins_required')
    need(s['status']=='refused' and s['sha256']==sha(source_candidate_original_bytes)
        and source['schema']=='home-apartment-camera-alignment-candidate-1' and source['version']==1
        and source['status']=='refused' and source['qualification']=='diagnostic_alignment'
        and source['scope']==a['scope'] and source['pack']==pack and source['camera']==camera
        and source['matrix']==matrix and source['matrix_sha256']==a['matrix_sha256']
        and source['publication_or_state_write'] is False and isinstance(s['refusal_reasons'],list)
        and len(s['refusal_reasons'])>0 and s['refusal_reasons']==source['fit']['best']['reasons'],
        'approximate_original_refusal_must_remain_refused')
    for key,role in (('original_rgb_sha256','rgb'),('original_npz_sha256','original_bundle'),
                     ('original_calibration_sha256','calibration'),('original_metadata_sha256','original')):
        need(evidence[key]==source['inputs'][role]['sha256'],'approximate_original_source_pin_changed')
    need(isinstance(review,dict) and review.get('schema')=='home-apartment-view-alignment-review-1'
        and review.get('version')==1 and review.get('status')=='reviewed_for_approximate_visualization'
        and review.get('alignment_sha256')==sha(alignment_original_bytes) and review.get('source_candidate_sha256')==s['sha256']
        and review.get('matrix_sha256')==a['matrix_sha256'] and review.get('actual_retained_inputs_verified') is True
        and review.get('metric_error_unknown') is True and review.get('owner_or_physical_calibration') is False
        and text(review.get('reviewed_by')) and review.get('evidence_sha256s')==[evidence[k] for k in EVIDENCE]
        and all(review.get(k) is False for k in NO_AUTHORITY),'approximate_distinct_actual_image_review_required')
    return ViewAlignment(alignment_original_bytes,review_original_bytes,source_candidate_original_bytes)

@dataclass(frozen=True)
class VerifiedSessionAlignment:
    """The unchanged reviewed alignment plus one verified camera-session successor.

    The successor names the single capture epoch it was verified in. It never
    edits the reviewed epoch, matrix, mount or refused fit, so any later
    reconnect still refuses until a new verification is reviewed.
    """
    _document:bytes
    _review:bytes
    _candidate:bytes
    _successor:bytes
    _successor_review:bytes
    @property
    def document(self):return _json(self._document,131072)
    @property
    def successor(self):return _json(self._successor,16384)
    @property
    def alignment_sha256(self):return sha(self._document)
    @property
    def review_sha256(self):return sha(self._review)
    @property
    def successor_sha256(self):return sha(self._successor)
    @property
    def successor_review_sha256(self):return sha(self._successor_review)
    @property
    def verified_epoch(self):return self.successor['verification']['verified_epoch']
    @property
    def retained_byte_allocations(self):
        return (self._document,self._review,self._candidate,self._successor,self._successor_review)

def bind_view_alignment_successor(*,alignment_original_bytes,review_original_bytes,source_candidate_original_bytes,
        successor_original_bytes,successor_review_original_bytes):
    """Bind the unchanged v1 originals first, then one verified-session successor."""
    try:
        bound=_bind(alignment_original_bytes,review_original_bytes,source_candidate_original_bytes)
        return _bind_successor(bound,successor_original_bytes,successor_review_original_bytes)
    except ViewAlignmentRefusal:raise
    except (KeyError,TypeError,ValueError,OverflowError,AttributeError,IndexError,RecursionError) as error:
        raise ViewAlignmentRefusal('approximate_private_configuration_invalid') from error

def _bind_successor(bound,successor_original_bytes,successor_review_original_bytes):
    a=bound.document;s=_json(successor_original_bytes,16384);review=_json(successor_review_original_bytes,16384)
    need(isinstance(s,dict) and set(s)==set(SUCCESSOR_KEYS) and s['schema']==SUCCESSOR_SCHEMA and type(s['version']) is int and s['version']==1
        and s['status']=='verified_camera_session_successor' and s['label']==LABEL,'approximate_successor_document_required')
    need(s['metric_error_bound_m'] is None and s['owner_or_physical_calibration'] is False
        and all(s[k] is False for k in NO_AUTHORITY),'approximate_successor_approximate_only_required')
    need(s['scope']==a['scope'],'approximate_successor_scope_changed')
    need(s['predecessor']==dict(alignment_sha256=bound.alignment_sha256,review_sha256=bound.review_sha256,
        source_candidate_sha256=a['source_candidate']['sha256'],source_fit_status='refused',
        reviewed_capture_epoch=a['camera']['capture_epoch']),'approximate_successor_predecessor_changed')
    need(s['matrix_sha256']==a['matrix_sha256']==digest(a['matrix']),'approximate_successor_matrix_changed')
    need(s['mount']==dict(revision=a['mount']['revision'],signature_sha256=a['mount']['signature_sha256']),
        'approximate_successor_mount_identity_changed')
    v=s['verification']
    need(isinstance(v,dict) and set(v)==set(VERIFICATION_KEYS) and text(v['verified_epoch'])
        and all(pin(v[k]) for k in ('calibration_sha256','calibration_content_id','npz_sha256','rgb_sha256','landmark_overlay_sha256'))
        and isinstance(v['verified_at'],str) and VERIFIED_AT.fullmatch(v['verified_at']) is not None,
        'approximate_successor_verification_required')
    # Same calibration content as the reviewed fit, recomputed from the read.
    need(s['camera']==dict(camera_id='den',source_frame='rectified_left',calibration_content_id=a['camera']['calibration_content_id'])
        and v['calibration_content_id']==a['camera']['calibration_content_id'],'approximate_successor_calibration_changed')
    mount=v['check_mount']
    need(isinstance(mount,dict) and mount.get('status') in ('unchanged','changed','unknown'),'approximate_successor_mount_check_required')
    need(mount['status']=='unchanged','approximate_successor_mount_'+mount['status'])
    need(set(mount)=={'status','compared_cells','changed_cells'} and type(mount['compared_cells']) is int
        and type(mount['changed_cells']) is int and 16<=mount['compared_cells']<=64
        and 0<=2*mount['changed_cells']<=mount['compared_cells'],'approximate_successor_mount_check_required')
    need(isinstance(review,dict) and set(review)=={'schema','version','status','successor_sha256','predecessor_alignment_sha256',
            'verified_epoch','verification_sha256s','landmark_overlay_matches','metric_error_unknown',
            'owner_or_physical_calibration','reviewed_by',*NO_AUTHORITY}
        and review['schema']==SUCCESSOR_REVIEW_SCHEMA and type(review['version']) is int and review['version']==1
        and review['status']=='reviewed_for_verified_camera_session','approximate_successor_review_required')
    need(review['owner_or_physical_calibration'] is False and all(review[k] is False for k in NO_AUTHORITY),
        'approximate_successor_approximate_only_required')
    need(review['successor_sha256']==sha(successor_original_bytes)
        and review['predecessor_alignment_sha256']==bound.alignment_sha256
        and review['verified_epoch']==v['verified_epoch']
        and review['verification_sha256s']==[v[k] for k in ('calibration_sha256','npz_sha256','rgb_sha256','landmark_overlay_sha256')]
        and review['landmark_overlay_matches'] is True and review['metric_error_unknown'] is True
        and text(review['reviewed_by']),'approximate_successor_review_required')
    return VerifiedSessionAlignment(*bound.retained_byte_allocations,successor_original_bytes,successor_review_original_bytes)

def camera_session_compatibility(*,health_session,calibration_original_bytes,successor):
    """Pure preflight check that the live camera session is the verified one.

    The caller makes the authenticated /health and /calibration reads; this
    opens nothing. Any mismatch is camera_session_incompatible: do not arm.
    """
    need(type(successor) is VerifiedSessionAlignment,'trusted_view_alignment_successor_required')
    s=successor.successor;epoch=s['verification']['verified_epoch'];expected=s['camera']['calibration_content_id']
    reasons=[];session=content=None
    if not (text(health_session) and health_session==epoch):reasons.append('camera_health_session_changed')
    try:
        calibration=_json(calibration_original_bytes,24576);need(isinstance(calibration,dict),'approximate_document_invalid')
        from bundle_ingest import calibration_content_id
        session=calibration.get('session');content=calibration_content_id(calibration)
    except (ViewAlignmentRefusal,TypeError,ValueError,OverflowError,RecursionError):reasons.append('camera_calibration_invalid')
    else:
        if session!=epoch:reasons.append('camera_calibration_session_changed')
        if content!=expected:reasons.append('camera_calibration_content_changed')
    return dict(schema=COMPATIBILITY_SCHEMA,status='camera_session_incompatible' if reasons else 'compatible',reasons=reasons,
        verified_epoch=epoch,health_session=health_session if text(health_session) else None,
        calibration_session=session if text(session) else None,calibration_content_id=content,
        expected_calibration_content_id=expected,view_alignment_successor_sha256=successor.successor_sha256)

def blank(reason):return dict(schema=PACKET_SCHEMA,status='unavailable',renderable=False,reason=reason,
    label=LABEL,approximate_pose=None,room_pose=None,**NO_AUTHORITY)

def project_approximate_ram_room_pose(lease,*,event,image_record,now_s,alignment,scope,
        active_collision_sha256,current_mount_revision,session_state,received_monotonic_s,
        now_monotonic_s,max_hold_s=2.,instance_index=0,timing_warnings=None,development=False):
    """Return supported actual camera joints with explicitly approximate placement.

    One RAM lease belongs to the existing source owner. No lease/media is held
    here. Receipt retirement is NOT source exposure age or clock qualification.
    """
    try:
        return _project(lease,event,image_record,now_s,alignment,scope,active_collision_sha256,
            current_mount_revision,session_state,received_monotonic_s,now_monotonic_s,max_hold_s,instance_index,timing_warnings,development)
    except ViewAlignmentRefusal as error:return blank(str(error))
    except (KeyError,TypeError,ValueError,OverflowError,AttributeError,IndexError,RecursionError):return blank('approximate_original_input_refused')

def _project(lease,event,image_record,now_s,alignment,scope,collision,mount_revision,state,received,now,hold,instance_index,timing_warnings=None,development=False):
    verified=type(alignment) is VerifiedSessionAlignment
    need(verified or type(alignment) is ViewAlignment,'trusted_approximate_alignment_required')
    need(isinstance(scope,dict) and set(scope)==set(SCOPE) and scope['siteId']=='victoria' and scope['cameraId']=='den'
        and text(scope['sessionId']) and type(scope['generation']) is int and scope['generation']>=0
        and type(scope['registrationVersion']) is int and scope['registrationVersion']>=0,'approximate_scene_scope_required')
    need(state=='recording' and all(finite(t) for t in (received,now,hold)) and 0<=received<=now
        and 0<hold<=2 and now-received<hold,'approximate_source_or_receipt_expired')
    a=alignment.document;pack=a['pack'];camera=a['camera']
    need(pack['content_revision']==scope['contentRevision'] and pack['registration_version']==scope['registrationVersion']
        and pack['frame_sha256']==scope['frameDigest'] and pack['collision_sha256']==collision
        and a['mount']['revision']==mount_revision,'approximate_scene_or_mount_identity_changed')
    from apartment_viewing_metric import lift_supported_joints,PreparedMetricFrame
    need(type(lease) is PreparedMetricFrame,'approximate_same_npz_lease_required')
    binding=lease.binding;descriptor=lease.descriptor
    need(binding['session']=={'id':scope['sessionId'],'site_id':'victoria','camera_id':'den'}
        and binding['media_generation']==scope['generation'],'approximate_camera_identity_changed')
    # A successor stands in for the reviewed epoch only in the one capture
    # epoch it was verified in; any other reconnect still refuses here.
    successor=alignment.successor if verified else None
    if verified:need(descriptor['capture_epoch']==successor['verification']['verified_epoch'],'approximate_camera_epoch_unverified')
    else:need(descriptor['capture_epoch']==camera['capture_epoch'],'approximate_camera_epoch_changed')
    need(descriptor['calibration_content_id']==camera['calibration_content_id'],'approximate_camera_calibration_changed')
    # Existing verifier checks original bytes, actual clock proof, exact model
    # image and original model output before lifting supported pixels only.
    # A caller-owned list (development preview timing only) records late
    # originals as named warnings; without it every deadline still refuses.
    candidate=lift_supported_joints(lease,event=event,image_record=image_record,now_s=now_s,
        projection='diagnostic_nominal_eeprom',warnings=timing_warnings)
    if development:
        # Owner decision (2026-10-19): the development preview does not run the
        # per-frame mount check; it is named once in the registration block.
        # Camera identity, epoch and calibration above still refuse.
        mount_check=dict(status='mount_check_skipped_development',compared_cells=None,changed_cells=None)
    else:
        from bundle_rgb_input import decode_bundle
        from spatial_validation import check_mount
        depth,_,_=decode_bundle(dict(lease._originals)['npz']);mount_check=check_mount(a['mount']['signature'],depth)
        need(mount_check['status']=='unchanged','approximate_mount_signature_'+mount_check['status'])
    need(type(instance_index) is int and 0<=instance_index<2 and instance_index<len(candidate['instances']),
        'approximate_observed_instance_unavailable')
    instance=candidate['instances'][instance_index];observed=instance['joints'];ids=set();joints=[];omitted=[];m=a['matrix']
    need(isinstance(observed,list) and len(observed)<=17,'approximate_joint_limit')
    for joint in observed:
        index=joint['joint_id'];need(type(index) is int and 0<=index<17 and index not in ids,'approximate_joint_identity_invalid');ids.add(index)
        if joint['status']!='candidate':omitted.append(index);continue
        xyz=joint['camera_xyz_m'];pixel=joint['pixel']
        need(isinstance(xyz,list) and len(xyz)==3 and all(finite(v) and abs(v)<=1000 for v in xyz) and xyz[2]>0
            and isinstance(pixel,list) and len(pixel)==2 and all(finite(v) for v in pixel)
            and 0<=pixel[0]<640 and 0<=pixel[1]<360,'approximate_supported_joint_invalid')
        point=[sum(m[row][k]*xyz[k] for k in range(3))+m[row][3] for row in range(3)]
        need(all(finite(v) and abs(v)<=1000 for v in point),'approximate_transformed_joint_invalid')
        joints.append(dict(id=index,position_m=point,camera_xyz_m=deepcopy(xyz),pixel=deepcopy(pixel),visible=True,
            error_bound_m=None,depth_support=joint['depth_support'],depth_spread_m=joint['depth_spread_m']))
    need(joints,'approximate_no_supported_metric_joints');kept={j['id'] for j in joints};omitted=sorted(set(omitted)|set(range(17))-kept)
    frame=candidate['original_frame_record'];p=candidate['original_event']['payload'];meta=frame['metadata']
    pose=dict(scope=deepcopy(scope),qualification='approximate_visual_alignment',label=LABEL,frame_id=p['frame_id'],
        source_epoch=p['capture_epoch'],instance_index=instance_index,instance_ref=instance.get('instance_ref'),person_ref=None,subject_ref=None,
        joints=joints,edges=[list(e) for e in EDGES if set(e)<=kept],omitted_joint_ids=omitted,anchor_joint_id=None,error_bound_m=None,
        source_times=deepcopy(candidate['source_times']),registration=dict(status='approximate_visual_alignment',camera_id='den',
            camera_to_room_digest=a['matrix_sha256'],alignment_digest=alignment.alignment_sha256,review_digest=alignment.review_sha256,
            source_fit_digest=a['source_candidate']['sha256'],source_fit_status='refused',canonical_collision_digest=collision,
            mount_revision=mount_revision,metric_error_bound_m=None,
            mount_movement_detection=dict(status='mount_check_skipped_development' if development else 'unvalidated_coarse_scene_check',
                result=None if development else mount_check['status'],
                compared_cells=mount_check['compared_cells'],changed_cells=mount_check['changed_cells'],physical_movement_error_bound_m=None)),
        provenance=dict(input_family=candidate['schema'],source_event_id=event['id'],original_event_sha256=candidate['original_event_sha256'],
            frame_media_id=frame['id'],frame_media_sha256=frame['sha256'],model_generation=p['generation'],media_generation=frame['generation'],
            calibration_content_id=camera['calibration_content_id'],ram_originals=deepcopy(descriptor['originals'])))
    if verified:pose['registration']['camera_session']=dict(status='verified_session_successor',
        verified_epoch=successor['verification']['verified_epoch'],reviewed_capture_epoch=camera['capture_epoch'],
        successor_digest=alignment.successor_sha256,successor_review_digest=alignment.successor_review_sha256)
    return dict(schema=PACKET_SCHEMA,status='approximate',renderable=True,reason=None,label=LABEL,session_state=state,
        approximate_pose=pose,room_pose=None,frame=dict(frame_id=p['frame_id'],media_id=frame['id'],media_sha256=frame['sha256'],
            generation=frame['generation'],site_id='victoria',camera_id='den',width=640,height=360,source_epoch=p['capture_epoch'],
            original_times={k:meta.get(k) for k in ('observed_at','available_at','valid_until')}),
        timing=dict(status='unverified',clock_qualification='unavailable',exposure_error_bound_s=None,
            original_depth_clock=deepcopy(candidate['depth_clock']),receipt_age_s=now-received,
            remaining_receipt_hold_s=hold-(now-received),receipt_semantics='display_retirement_only_not_exposure_freshness',
            projection_qualification=candidate['projection']['status']),**NO_AUTHORITY)

class ApproximatePoseReceiptTracker:
    """Retain one small projected packet, never RAM originals or a second owner."""
    def __init__(self,*,alignment,scope,active_collision_sha256,current_mount_revision,monotonic,max_hold_s=2.):
        need(callable(monotonic) and finite(max_hold_s) and 0<max_hold_s<=2,'approximate_tracker_clock_required')
        self.fixed=dict(alignment=alignment,scope=deepcopy(scope),active_collision_sha256=active_collision_sha256,
            current_mount_revision=current_mount_revision,max_hold_s=max_hold_s)
        self.monotonic=monotonic;self.latest=None;self.received=None;self.event_id=None;self.fingerprint=None;self.closed=False
        self.reason='approximate_no_observation'
    @property
    def retained_byte_allocations(self):
        # The packet is a small dictionary, not a hidden encoded byte buffer.
        # Its encoded charge is counted separately by the existing owner.
        return self.fixed['alignment'].retained_byte_allocations
    def ingest(self,lease,*,event,image_record,now_s,session_state,instance_index=0,timing_warnings=None,development=False):
        if self.closed:return blank(self.reason)
        if session_state!='recording':return self.stop('approximate_source_stopped')
        try:
            need(isinstance(event,dict) and len(encoded(event))<=2*1024**2 and type(event.get('id')) is int,'approximate_event_required')
            fingerprint=digest([event,image_record,instance_index]);eid=event['id']
            need(self.event_id is None or eid>=self.event_id,'approximate_out_of_order_event')
            if self.event_id==eid:
                need(fingerprint==self.fingerprint,'approximate_original_event_changed');return self.snapshot()
            received=self.monotonic();result=project_approximate_ram_room_pose(lease,event=event,image_record=image_record,now_s=now_s,
                session_state=session_state,received_monotonic_s=received,now_monotonic_s=self.monotonic(),instance_index=instance_index,
                timing_warnings=timing_warnings,development=development,**self.fixed)
            if not result['renderable']:self.latest=None;self.reason=result['reason'];return result
            self.latest=deepcopy(result);self.received=received;self.event_id=eid;self.fingerprint=fingerprint
            # Decode/lift may be slow; retire against the actual completion
            # clock before any caller can attach this result.
            return self.snapshot()
        except (KeyError,TypeError,ValueError,OverflowError,RecursionError) as error:
            self.latest=None;self.reason=str(error) if isinstance(error,ViewAlignmentRefusal) else 'approximate_original_input_refused';return blank(self.reason)
    def snapshot(self):
        if self.closed or self.latest is None:return blank(self.reason)
        age=self.monotonic()-self.received
        if not finite(age) or not 0<=age<self.fixed['max_hold_s']:return blank('approximate_receipt_expired')
        result=deepcopy(self.latest);result['timing'].update(receipt_age_s=age,remaining_receipt_hold_s=self.fixed['max_hold_s']-age);return result
    def stop(self,reason='approximate_view_closed'):
        self.closed=True;self.latest=None;self.fingerprint=None;self.received=None;self.reason=reason
        return blank(reason)
