"""Freeze exact canonical local P1 originals; no identity/qualification grant."""
from copy import deepcopy
from reasoning import canonical, digest, finite
import qwen_person_scene as qwen
from jev_person_wire import SLOTS, valid_facts

SCHEMA='home-lab-person-jev-input-1'
KEYS={'schema','version','semantics_event_id','semantics_payload_digest','visual_event_id','visual_payload_digest','packet','slots'}
KINDS=('perception.output','qwen.person_observation')


def _token(value):
    return type(value) is str and 0<len(value)<=256


def freeze_person_context(records, *, site_id, session_id, arm, generation, cutoff):
    """Raw Store event wrappers only. Latest invalid/new evidence never falls back."""
    try:
        if (type(records) not in (list,tuple) or len(records)>512 or arm!='b' or not finite(cutoff)
                or type(generation) is not int or not 0<=generation<=9007199254740991):
            return None
        visuals=[];semantics=[]
        for event in records:
            if type(event) is not dict or type(event.get('id')) is not int or event['id']<1 or type(event.get('payload')) is not dict:
                return None
            if event.get('session_id',session_id)!=session_id:
                continue
            payload=event['payload']
            if event.get('kind')=='perception.output':
                if (payload.get('site_id')==site_id and payload.get('arm')==arm and payload.get('generation')==generation
                        and finite(payload.get('observed_at')) and finite(payload.get('available_at'))
                        and payload['observed_at']<=cutoff and payload['available_at']<=cutoff):
                    visuals.append(event)
            elif event.get('kind')=='qwen.person_observation':
                packet=payload.get('packet',{})
                scope=packet.get('basis',{}).get('scope',{})
                if (scope.get('site_id')==site_id and scope.get('session_id')==session_id and scope.get('arm')==arm
                        and scope.get('generation')==generation and finite(packet.get('available_at'))
                        and packet['available_at']<=cutoff):
                    semantics.append(event)
        if not visuals or not semantics:
            return None
        visual=max(visuals,key=lambda e:(e['payload']['observed_at'],e['id']))
        sem=max(semantics,key=lambda e:(e['payload']['packet'].get('observed_at',-1),e['id']))
        return _freeze(visual,sem,site_id=site_id,session_id=session_id,arm=arm,generation=generation,cutoff=cutoff)
    except (ValueError,TypeError,KeyError,AttributeError,RecursionError):
        return None


def _freeze(visual,sem,*,site_id,session_id,arm,generation,cutoff):
    value=visual['payload'];receipt=sem['payload'];packet=receipt['packet']
    association=value.get('arm_snapshot',{}).get('persons2d',{}).get('association',{})
    basis={k:association.get(k) for k in ('scope','source','track_epoch')}
    scope,source=basis['scope'],basis['source']
    if (value.get('status')!='completed' or value.get('current') is not True
            or association.get('schema')!='home-lab-view-person-association-1' or association.get('version')!=1
            or association.get('status')!='available' or association.get('reason') is not None
            or not _token(basis['track_epoch']) or not isinstance(scope,dict) or not isinstance(source,dict)
            or any(scope.get(k)!=v for k,v in dict(site_id=site_id,session_id=session_id,arm=arm,generation=generation,
                camera_id=value.get('camera_id'),capture_epoch=value.get('capture_epoch'),engine_epoch=value.get('engine_epoch'),
                artifact_sha256=value.get('artifact_sha256'),model_version=value.get('model_version')).items())
            or source.get('frame_id')!=value.get('frame_id') or source.get('request_id')!=value.get('request_id')
            or source.get('observed_at')!=value.get('observed_at') or source.get('available_at')!=value.get('available_at')
            or not all(finite(source.get(k)) for k in ('observed_at','available_at','valid_until'))
            or not source['observed_at']<=source['available_at']<=cutoff<source['valid_until']
            or source['valid_until']>value.get('valid_until',-1)
            or packet.get('schema')!='home-lab-view-person-semantics-1' or packet.get('version')!=1
            or canonical(packet.get('basis'))!=canonical(basis)
            or receipt.get('source_event_id')!=visual['id'] or receipt.get('source_payload_digest')!=digest(value)
            or packet.get('observed_at')!=source['observed_at'] or packet.get('valid_until')!=source['valid_until']
            or not finite(packet.get('available_at')) or not source['available_at']<=packet['available_at']<=cutoff
            or packet.get('viewing_only') is not True
            or any(packet.get(k) is not False for k in ('identity_authority','geometry_authority','engagement_authority','physical_permission'))
            or association.get('viewing_only') is not True
            or any(association.get(k) is not False for k in ('identity_authority','geometry_authority','physical_permission'))):
        return None
    if (type(packet.get('scene')) is not dict or set(packet['scene'])!={'people_visible'}
            or packet['scene']['people_visible'] not in qwen.PEOPLE_VISIBLE):
        return None
    persons=value['arm_snapshot']['persons2d'].get('persons');assignments=association.get('assignments');subjects=packet.get('subjects')
    if any(type(rows) is not list for rows in (persons,assignments,subjects)) or not 1<=len(persons)<=2 or len(assignments)!=len(persons) or len(subjects)!=len(persons):
        return None
    ids=[p.get('instance_id') for p in persons]
    if any(not _token(i) for i in ids) or len(set(ids))!=len(ids):return None
    amap={p.get('instance_id'):p for p in assignments};smap={p.get('instance_id'):p for p in subjects}
    if set(amap)!=set(ids) or set(smap)!=set(ids) or len(amap)!=len(assignments) or len(smap)!=len(subjects):return None
    refs=[];slots=[]
    for slot,instance in zip(SLOTS,sorted(ids)):
        assignment,subject=amap[instance],smap[instance]
        ref=assignment.get('track_ref');refs.append(ref)
        if (not _token(ref) or assignment.get('status') not in ('new','continued') or assignment.get('reason') is not None
                or subject.get('track_ref')!=ref or subject.get('status') not in ('available','unknown')):return None
        if subject['status']=='available':
            if subject.get('reason') is not None or not valid_facts(subject.get('facts')):return None
        elif subject.get('facts') is not None or not _token(subject.get('reason')):return None
        slots.append(dict(subject_slot=slot,instance_id=instance,track_ref=ref))
    if len(set(refs))!=len(refs):return None
    evidence=packet.get('evidence',{});frame=value.get('arm_snapshot',{}).get('frame',{})
    if (evidence.get('media_id') not in value.get('media_ids',[]) or evidence.get('media_id')!=frame.get('media_id')
            or evidence.get('frame_id')!=source['frame_id'] or evidence.get('capture_epoch')!=scope['capture_epoch']
            or (evidence.get('width'),evidence.get('height'))!=qwen.IMAGE_SIZE
            or not isinstance(evidence.get('sha256'),str) or len(evidence['sha256'])!=64):return None
    return dict(schema=SCHEMA,version=1,semantics_event_id=sem['id'],semantics_payload_digest=digest(receipt),
                visual_event_id=visual['id'],visual_payload_digest=digest(value),packet=deepcopy(packet),slots=slots)


def validate_person_context(context, *, site_id, session_id, arm, generation, cutoff):
    if (type(context) is not dict or set(context)!=KEYS or context.get('schema')!=SCHEMA or context.get('version')!=1
            or arm!='b' or type(generation) is not int or not 0<=generation<=9007199254740991):
        raise ValueError('person_context_invalid')
    packet=context.get('packet',{});scope=packet.get('basis',{}).get('scope',{})
    if (any(scope.get(k)!=v for k,v in dict(site_id=site_id,session_id=session_id,arm=arm,generation=generation).items())
            or not finite(cutoff) or not finite(packet.get('available_at')) or not finite(packet.get('valid_until'))
            or not packet['available_at']<=cutoff<packet['valid_until']
            or type(context.get('slots')) is not list or not 1<=len(context['slots'])<=2
            or [p.get('subject_slot') for p in context['slots']]!=list(SLOTS[:len(context['slots'])])):
        raise ValueError('person_context_invalid')
    return deepcopy(context)
