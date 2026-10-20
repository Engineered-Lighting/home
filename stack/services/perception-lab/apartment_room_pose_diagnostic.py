"""Pure, owner-local projection of verified metric samples for diagnostic viewing.

Caller must obtain ``collection`` from the incumbent read_verified_attempts()
original-byte verifier and ``review`` from the trusted backend's separately
reviewed retained-data alignment record. These dictionaries are not signatures
or an authentication boundary and must never be supplied by a browser/model.

No IO, inference, clock qualification, identity inference or operational gate is
performed here. Unknown exposure timing does not prohibit a labeled diagnostic
picture. It always prohibits any claim that this module admitted live geometry,
Follow, protected-body volume, physical aiming or lighting commands.
"""
from copy import deepcopy
import hashlib
import json
import math
import re

SCHEMA='home-apartment-diagnostic-room-pose-1'
SCOPE=('siteId','contentRevision','frameDigest','registrationVersion','cameraId','sessionId','generation')
HEX=re.compile(r'^[a-f0-9]{64}$')
# Incumbent rtmpose_worker.EDGES, filtered to observed endpoints only.
COCO17_EDGES=((0,1),(0,2),(1,3),(2,4),(5,6),(5,7),(7,9),(6,8),(8,10),
              (5,11),(6,12),(11,12),(11,13),(13,15),(12,14),(14,16))
NO_AUTHORITY=dict(operational_geometry=False,geometry_authority=False,identity_authority=False,
    engagement_authority=False,absence_authority=False,protection_authority=False,beam_authority=False,
    physical_permission=False,follow_allowed=False)

class DiagnosticRefusal(ValueError):pass
def need(value,reason):
    if not value:raise DiagnosticRefusal(reason)
def finite(value):return type(value) in (int,float) and math.isfinite(value)
def text(value):return isinstance(value,str) and 0<len(value)<=256
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def sha(value):return isinstance(value,str) and HEX.fullmatch(value) is not None
def blank(reason):return dict(schema=SCHEMA,status='unavailable',renderable=False,reason=reason,
    diagnostic_pose=None,room_pose=None,**NO_AUTHORITY)

def _matrix(value):
    need(isinstance(value,list) and len(value)==4 and all(isinstance(row,list) and len(row)==4
        and all(finite(n) for n in row) for row in value),'diagnostic_matrix_invalid')
    need(value[3]==[0,0,0,1],'diagnostic_matrix_affine_required')
    for i in range(3):
        for j in range(3):
            need(abs(sum(value[k][i]*value[k][j] for k in range(3))-(1 if i==j else 0))<1e-6,
                'diagnostic_matrix_must_be_rigid')
    r=value
    determinant=(r[0][0]*(r[1][1]*r[2][2]-r[1][2]*r[2][1])
        -r[0][1]*(r[1][0]*r[2][2]-r[1][2]*r[2][0])+r[0][2]*(r[1][0]*r[2][1]-r[1][1]*r[2][0]))
    need(abs(determinant-1)<1e-6,'diagnostic_matrix_must_be_right_handed')
    return value

def project_diagnostic_room_pose(collection,*,attempt_id,instance_index,alignment,review,scope,
        session_state,received_monotonic_s,now_monotonic_s,max_hold_s=2.,replay=False,
        active_collision_sha256=None,anchor_joint_id=None,alignment_original_bytes=None):
    """Return separate diagnostic presentation; incumbent room_pose stays null.

    ``replay=True`` is explicit recorded inspection. Otherwise the actual source
    must still be recording and the first producer receipt retires after at most
    two seconds. Receipt retirement is not exposure age or source freshness;
    repeated reads must retain that SAME first receipt, not renew this lease.
    """
    try:
        return _project(collection,attempt_id,instance_index,alignment,review,scope,session_state,
            received_monotonic_s,now_monotonic_s,max_hold_s,replay,active_collision_sha256,anchor_joint_id,alignment_original_bytes)
    except DiagnosticRefusal as error:return blank(str(error))
    except (KeyError,TypeError,ValueError,OverflowError):return blank('diagnostic_input_invalid')

class DiagnosticPoseReceiptTracker:
    """One trusted producer's latest verified event; no polling or acquisition.

    Repeated reads cannot renew its first-receipt retirement. Session stop is
    terminal for this scope; reopening a producer needs a new owner instance.
    This is display retirement only, never a source/exposure clock assertion.
    """
    def __init__(self,*,scope,alignment,review,active_collision_sha256,monotonic,max_hold_s=2.,alignment_original_bytes=None):
        need(callable(monotonic),'diagnostic_monotonic_clock_required')
        self.fixed=deepcopy(dict(scope=scope,alignment=alignment,review=review,
            active_collision_sha256=active_collision_sha256,max_hold_s=max_hold_s,alignment_original_bytes=alignment_original_bytes))
        self.monotonic=monotonic;self.latest=None;self.closed=False;self.last_event_id=None;self.fingerprint=None;self.received=None
        self.reason='diagnostic_no_observation'
    def _project(self,collection,attempt_id,instance_index,received):
        return project_diagnostic_room_pose(collection,attempt_id=attempt_id,instance_index=instance_index,
            session_state='recording',received_monotonic_s=received,now_monotonic_s=self.monotonic(),**self.fixed)
    def ingest(self,collection,*,attempt_id,instance_index=0,session_state):
        if self.closed:return blank(self.reason)
        if session_state!='recording':return self.stop('diagnostic_source_not_recording')
        if type(attempt_id) is not int or self.last_event_id is not None and attempt_id<self.last_event_id:
            return blank('diagnostic_out_of_order_observation')
        try:
            found=[c for c in collection['attempts'] if c.get('source_event_id')==attempt_id]
            need(len(found)==1,'diagnostic_exact_attempt_required')
            candidate=found[0];fingerprint=digest([candidate,instance_index])
            if self.last_event_id==attempt_id:
                need(fingerprint==self.fingerprint,'diagnostic_original_event_changed')
                return self.snapshot()
            received=self.monotonic()
            # Retain one exact observation, never an accumulating collection.
            selected={**collection,'attempts':[candidate]}
            result=self._project(selected,attempt_id,instance_index,received)
            if not result['renderable']:
                self.latest=None;self.reason=result['reason'];return result
            self.latest=deepcopy(dict(collection=selected,attempt_id=attempt_id,instance_index=instance_index))
            self.received=received;self.last_event_id=attempt_id;self.fingerprint=fingerprint
            return result
        except DiagnosticRefusal as error:
            self.latest=None;self.reason=str(error);return blank(self.reason)
        except (KeyError,TypeError,ValueError,OverflowError):
            self.latest=None;self.reason='diagnostic_input_invalid';return blank(self.reason)
    def snapshot(self):
        if self.closed or self.latest is None:return blank(self.reason)
        return self._project(self.latest['collection'],self.latest['attempt_id'],self.latest['instance_index'],self.received)
    def stop(self,reason='diagnostic_source_stopped'):
        self.closed=True;self.latest=None;self.fingerprint=None;self.received=None
        self.reason=reason if reason in ('diagnostic_source_stopped','diagnostic_source_not_recording','diagnostic_view_closed') else 'diagnostic_source_stopped'
        return blank(self.reason)

def _scope_receipt(scope,session_state,received,now,hold,replay,collision):
    need(isinstance(scope,dict) and set(scope)==set(SCOPE) and scope['siteId']=='victoria' and scope['cameraId']=='den'
        and all(text(scope[k]) for k in ('contentRevision','sessionId')) and sha(scope['frameDigest'])
        and type(scope['registrationVersion']) is int and scope['registrationVersion']>=0
        and type(scope['generation']) is int and scope['generation']>=0,'diagnostic_scope_required')
    need(sha(collision),'diagnostic_active_collision_required')
    need(type(replay) is bool and finite(received) and finite(now) and 0<=received<=now
        and finite(hold) and 0<hold<=2,'diagnostic_receipt_retirement_required')
    if not replay:
        need(session_state=='recording','diagnostic_source_not_recording')
        need(now-received<hold,'diagnostic_receipt_expired')
    need(session_state in ('requested','starting','recording','stopping','stopped','failed','blocked','interrupted','indeterminate'),
        'diagnostic_session_state_invalid')


def project_ram_diagnostic_room_pose(lease,*,event,image_record,now_s,instance_index,alignment,review,scope,
        session_state,received_monotonic_s,now_monotonic_s,max_hold_s=2.,replay=False,
        active_collision_sha256=None,anchor_joint_id=None,alignment_original_bytes=None,
        projection='diagnostic_nominal_eeprom'):
    """Distinct, actual-byte-verified bounded-RAM input; no assessment receipt.

    The same-NPZ PNG must already have been offered through the incumbent RAM
    owner. This entrypoint neither resolves media nor acquires a source. Its
    reviewed room transform and display retirement are identical to the retained
    path, while RAM originals and nominal projection are explicitly labeled.
    """
    try:
        _scope_receipt(scope,session_state,received_monotonic_s,now_monotonic_s,max_hold_s,replay,active_collision_sha256)
        from apartment_viewing_metric import lift_supported_joints,SCHEMA as RAM_SCHEMA,digest as ram_digest
        candidate=lift_supported_joints(lease,event=event,image_record=image_record,now_s=now_s,projection=projection)
        binding=lease.binding
        need(binding['session']=={'id':scope['sessionId'],'site_id':'victoria','camera_id':'den'}
            and binding['media_generation']==scope['generation'],'diagnostic_ram_binding_mismatch')
        need(type(instance_index) is int and 0<=instance_index<2,'diagnostic_selection_required')
        descriptor=lease.descriptor
        result=_project_sample(candidate,binding,ram_digest(descriptor),event['id'],instance_index,alignment,review,
            scope,session_state,received_monotonic_s,now_monotonic_s,max_hold_s,replay,
            active_collision_sha256,anchor_joint_id,alignment_original_bytes)
        result['diagnostic_pose']['provenance'].update(input_family=RAM_SCHEMA,
            collection_sha256_semantics='ram_original_binding_not_retained_collection',
            ram_originals=deepcopy(descriptor['originals']),ram_lease_digest=ram_digest(descriptor))
        result['timing']['projection_qualification']=candidate['projection']['status']
        return result
    except DiagnosticRefusal as error:return blank(str(error))
    except (KeyError,TypeError,ValueError,OverflowError,AttributeError,IndexError,RecursionError):return blank('diagnostic_ram_input_refused')


class RamDiagnosticPoseReceiptTracker:
    """One small verified RAM packet, with nonrenewable first-event retirement.

    Raw bundles remain exclusively in the incumbent owner's accounted lease;
    this tracker retains neither a bundle nor a model input. No worker, timer,
    media or acquisition is created. The owner calls stop during its cleanup.
    """
    def __init__(self,*,scope,alignment,review,active_collision_sha256,monotonic,max_hold_s=2.,alignment_original_bytes=None):
        need(callable(monotonic),'diagnostic_monotonic_clock_required')
        self.fixed=deepcopy(dict(scope=scope,alignment=alignment,review=review,active_collision_sha256=active_collision_sha256,
            max_hold_s=max_hold_s,alignment_original_bytes=alignment_original_bytes))
        self.monotonic=monotonic;self.latest=None;self.closed=False;self.last_event_id=None
        self.fingerprint=None;self.received=None;self.reason='diagnostic_no_observation'
    def ingest(self,lease,*,event,image_record,now_s,session_state,instance_index=0):
        if self.closed:return blank(self.reason)
        if session_state!='recording':return self.stop('diagnostic_source_not_recording')
        try:
            index=event['id']
            need(type(index) is int and index>0 and (self.last_event_id is None or index>=self.last_event_id),
                'diagnostic_out_of_order_observation')
            fingerprint=digest([lease.descriptor,event,image_record,instance_index])
            if index==self.last_event_id:
                need(fingerprint==self.fingerprint,'diagnostic_original_event_changed')
                return self.snapshot()
            received=self.monotonic()
            result=project_ram_diagnostic_room_pose(lease,event=event,image_record=image_record,now_s=now_s,
                instance_index=instance_index,session_state=session_state,received_monotonic_s=received,
                now_monotonic_s=received,**self.fixed)
            if not result['renderable']:
                self.latest=None;self.reason=result['reason'];return result
            need(len(json.dumps(result,allow_nan=False))<=256*1024,'diagnostic_packet_limit')
            self.latest=deepcopy(result);self.received=received;self.last_event_id=index;self.fingerprint=fingerprint
            return result
        except DiagnosticRefusal as error:
            self.latest=None;self.reason=str(error);return blank(self.reason)
        except (KeyError,TypeError,ValueError,OverflowError,AttributeError):
            self.latest=None;self.reason='diagnostic_ram_input_refused';return blank(self.reason)
    def snapshot(self):
        if self.closed or self.latest is None:return blank(self.reason)
        now=self.monotonic();hold=self.fixed['max_hold_s']
        try:_scope_receipt(self.fixed['scope'],'recording',self.received,now,hold,False,self.fixed['active_collision_sha256'])
        except DiagnosticRefusal as error:
            self.latest=None;self.reason=str(error);return blank(self.reason)
        result=deepcopy(self.latest);result['timing'].update(receipt_age_s=now-self.received,
            remaining_receipt_hold_s=hold-(now-self.received))
        return result
    def stop(self,reason='diagnostic_source_stopped'):
        self.closed=True;self.latest=None;self.fingerprint=None;self.received=None
        self.reason=reason if reason in ('diagnostic_source_stopped','diagnostic_source_not_recording','diagnostic_view_closed') else 'diagnostic_source_stopped'
        return blank(self.reason)


def _project(collection,attempt_id,instance_index,alignment,review,scope,session_state,received,now,hold,replay,collision,anchor,alignment_bytes):
    _scope_receipt(scope,session_state,received,now,hold,replay,collision)
    need(isinstance(collection,dict) and sha(collection['collection_sha256'])
        and isinstance(collection['attempts'],list) and len(collection['attempts'])<=24
        and collection['summary']['ordinary_lighting_replay'] is False,'diagnostic_verified_collection_required')
    need(type(attempt_id) is int and attempt_id>0 and type(instance_index) is int and 0<=instance_index<2,'diagnostic_selection_required')
    found=[c for c in collection['attempts'] if c.get('source_event_id')==attempt_id]
    need(len(found)==1,'diagnostic_exact_attempt_required');candidate=found[0]
    need(len(json.dumps(candidate,allow_nan=False))<=2*1024**2,'diagnostic_candidate_limit')
    wrapped=collection['binding'];binding=wrapped['binding']
    need(wrapped['binding_digest']==digest(binding)==candidate['binding_digest']
        and binding['session']=={'id':scope['sessionId'],'site_id':'victoria','camera_id':'den'}
        and binding['media_generation']==scope['generation'],'diagnostic_collection_binding_mismatch')
    need(candidate['schema'] in ('home-lab-assessment-attempt-2','home-lab-assessment-attempt-3')
        and candidate['version']==int(candidate['schema'][-1]) and candidate['status']=='candidate'
        and candidate['coordinate_frame']=='rectified_left' and candidate['unit']=='m'
        and candidate['candidate_only'] is True and candidate['owner_local_only'] is True
        and candidate['operational_geometry'] is None and candidate['person_ref'] is None and candidate['subject_ref'] is None
        and all(candidate.get(k) is False for k in NO_AUTHORITY if k not in ('operational_geometry','physical_permission','follow_allowed')),
        'diagnostic_nominal_candidate_required')
    return _project_sample(candidate,binding,collection['collection_sha256'],attempt_id,instance_index,alignment,review,
        scope,session_state,received,now,hold,replay,collision,anchor,alignment_bytes)


def _project_sample(candidate,binding,originals_digest,attempt_id,instance_index,alignment,review,scope,
                    session_state,received,now,hold,replay,collision,anchor,alignment_bytes):
    event=candidate['original_event'];p=event['payload'];frame=candidate['original_frame_record'];meta=frame['metadata']
    need(candidate['original_event_sha256']==digest(event) and event['id']==attempt_id and event['kind']=='perception.output'
        and event['session_id']==scope['sessionId'] and p['site_id']=='victoria' and p['camera_id']=='den'
        and p['generation']==binding['model_generation'] and text(p['frame_id']) and text(p['capture_epoch']),
        'diagnostic_original_event_mismatch')
    need(frame['session_id']==scope['sessionId'] and frame['generation']==scope['generation'] and text(frame['id']) and sha(frame['sha256'])
        and frame['mime_type'] in ('image/jpeg','image/png') and meta['site_id']=='victoria' and meta['camera_id']=='den'
        and meta['frame_id']==p['frame_id'] and meta['capture_epoch']==p['capture_epoch']
        and all(type(meta[k]) is int and 0<meta[k]<=8192 for k in ('width','height')),'diagnostic_exact_image_required')
    times=candidate['source_times']
    need(set(times)=={'observed_at','available_at','valid_until'} and all(finite(times[k]) and times[k]>=0 and times[k]==p[k] for k in times)
        and times['observed_at']<=times['available_at'] and times['observed_at']<times['valid_until'],'diagnostic_original_times_required')
    need(isinstance(alignment_bytes,bytes) and 0<len(alignment_bytes)<=512*1024,'diagnostic_alignment_original_bytes_required')
    def unique(items):
        out={}
        for key,value in items:need(key not in out,'diagnostic_alignment_duplicate_key');out[key]=value
        return out
    original=json.loads(alignment_bytes,object_pairs_hook=unique,
        parse_constant=lambda _:need(False,'diagnostic_alignment_nonfinite'))
    need(original==alignment,'diagnostic_alignment_original_changed')
    alignment_sha=hashlib.sha256(alignment_bytes).hexdigest()
    a=alignment;camera=a['camera'];pack=a['pack'];matrix=_matrix(a['matrix'])
    need(a['schema']=='home-apartment-camera-alignment-candidate-1' and a['version']==1 and a['status']=='review_required'
        and a['qualification']=='diagnostic_alignment' and a['viewing_only'] is True and a['operational_geometry'] is False
        and a['physical_permission'] is False and a['scope']=={'site_id':'victoria','map_id':'victoria-den','room_id':'den'}
        and pack['content_revision']==scope['contentRevision'] and pack['registration_version']==scope['registrationVersion']
        and pack['frame_sha256']==scope['frameDigest'] and pack['collision_sha256']==collision
        and camera['camera_id']=='den' and camera['source_frame']=='rectified_left' and camera['capture_epoch']==p['capture_epoch']
        and camera['calibration_content_id']==candidate['media_records']['bundle']['metadata']['calibration_content_id']
        and sha(camera['calibration_content_id']) and a['matrix_sha256']==digest(matrix),'diagnostic_alignment_binding_mismatch')
    need(isinstance(review,dict) and review.get('schema')=='home-apartment-diagnostic-alignment-review-1'
        and review.get('status')=='reviewed_for_diagnostic_viewing' and review.get('alignment_sha256')==alignment_sha
        and review.get('matrix_sha256')==a['matrix_sha256'] and review.get('actual_retained_inputs_verified') is True
        and isinstance(review.get('input_evidence_sha256'),list) and 0<len(review['input_evidence_sha256'])<=16
        and all(sha(x) for x in review['input_evidence_sha256']) and text(review.get('reviewed_by')),
        'diagnostic_real_alignment_review_required')
    instances=candidate['instances'];need(isinstance(instances,list) and len(instances)<=2 and instance_index<len(instances),'diagnostic_instance_unavailable')
    instance=instances[instance_index];raw=instance['joints'];need(isinstance(raw,list) and len(raw)<=17
        and (instance.get('instance_ref') is None or text(instance['instance_ref'])),'diagnostic_joint_limit')
    ids=set();joints=[];omitted=[]
    for joint in raw:
        index=joint['joint_id'];need(type(index) is int and 0<=index<17 and index not in ids,'diagnostic_joint_identity_invalid');ids.add(index)
        if joint['status']!='candidate':omitted.append(index);continue
        xyz=joint['camera_xyz_m'];pixel=joint['pixel']
        need(isinstance(xyz,list) and len(xyz)==3 and all(finite(n) and abs(n)<=1000 for n in xyz) and xyz[2]>0
            and isinstance(pixel,list) and len(pixel)==2 and all(finite(n) for n in pixel)
            and 0<=pixel[0]<=meta['width'] and 0<=pixel[1]<=meta['height'],'diagnostic_joint_geometry_invalid')
        point=[sum(matrix[row][k]*xyz[k] for k in range(3))+matrix[row][3] for row in range(3)]
        need(all(finite(n) and abs(n)<=1000 for n in point),'diagnostic_transformed_joint_invalid')
        joints.append(dict(id=index,position_m=point,camera_xyz_m=deepcopy(xyz),pixel=deepcopy(pixel),visible=True,
            error_bound_m=joint.get('error_bound_m'),depth_support=joint.get('depth_support'),depth_spread_m=joint.get('depth_spread_m')))
    need(joints,'diagnostic_no_metric_joints')
    kept={j['id'] for j in joints};need(anchor is None or type(anchor) is int and anchor in kept,'diagnostic_anchor_not_observed')
    capture=candidate.get('capture_started',{}).get('payload',{}).get('capture',{})
    capture_id=capture.get('capture_id')
    depth_clock=deepcopy(candidate.get('depth_clock'))
    pose=dict(scope=deepcopy(scope),qualification='diagnostic_alignment',frame_id=p['frame_id'],capture_id=capture_id,
        source_epoch=p['capture_epoch'],instance_index=instance_index,instance_ref=instance.get('instance_ref'),
        person_ref=None,subject_ref=None,joints=joints,edges=[list(e) for e in COCO17_EDGES if set(e)<=kept],omitted_joint_ids=omitted,
        anchor_joint_id=anchor,source_times=deepcopy(times),error_bound_m=candidate.get('error_bound_m'),
        registration=dict(status='reviewed_diagnostic',camera_id='den',camera_to_room_digest=a['matrix_sha256'],
            alignment_digest=alignment_sha,canonical_collision_digest=collision),
        provenance=dict(collection_sha256=originals_digest,source_event_id=attempt_id,
            original_event_sha256=candidate['original_event_sha256'],candidate_sha256=digest(candidate),
            frame_media_id=frame['id'],frame_media_sha256=frame['sha256'],calibration_content_id=camera['calibration_content_id'],
            model_generation=p['generation'],media_generation=frame['generation']))
    return dict(schema=SCHEMA,status='recorded' if replay else 'timing_unverified',renderable=True,reason=None,
        session_state=session_state,diagnostic_pose=pose,room_pose=None,
        frame=dict(frame_id=p['frame_id'],media_id=frame['id'],media_sha256=frame['sha256'],generation=frame['generation'],
            site_id='victoria',camera_id='den',width=meta['width'],height=meta['height'],capture_id=capture_id,
            source_epoch=p['capture_epoch'],original_times={k:meta.get(k) for k in ('observed_at','available_at','valid_until')}),
        timing=dict(status='recorded' if replay else 'unverified',clock_qualification='unavailable',exposure_error_bound_s=None,
            original_depth_clock=depth_clock,receipt_age_s=now-received,remaining_receipt_hold_s=None if replay else hold-(now-received),
            receipt_semantics='display_retirement_only_not_exposure_freshness',
            metric_candidate_produced_at=candidate.get('candidate_produced_at')),**NO_AUTHORITY)
