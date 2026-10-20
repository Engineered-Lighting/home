"""Server-only publication and causal replay of validated spatial context.

room.observation keeps one record shape for both localization methods:
result.tracks[] (pos, uncertainty_m, method, frame, validity times) plus
result.relations[] and result.quality. quality.method is 'stereo_surface'
(surface_only true, measured depth) or 'rgb_floor_contact' (surface_only
false, floor_contact true, homography_version; no depth measurement).
"""
import json
from room_context import build_room_context
from reasoning import finite,canonical

# (arm localization policy, point method) pairs an arm may receive.
ADMITTED={('stereo','stereo_surface'),('floor_projection','rgb_floor_contact')}


def _check_sources(store, sid, session, point, registration):
    depth_sources,rgb_sources=[],[]
    for mid in point['media_ids']:
        media=store.get_media(sid,mid)
        meta=media['metadata']
        if (meta.get('site_id')!=session['site_id'] or meta.get('camera_id')!=session['camera_id']
                or meta.get('capture_epoch')!=point['capture_epoch']):
            raise ValueError('spatial media source mismatch')
        if meta.get('format')=='depth_u16_le':depth_sources.append((media,meta))
        elif meta.get('stream')=='rgb' and meta.get('format')=='jpeg':rgb_sources.append(meta)
    if point.get('method')=='rgb_floor_contact':
        # RGB fallback needs no depth media, but the same verified observation.
        homography=registration.get('floor_homography') or {}
        if len(rgb_sources)!=1 or depth_sources:
            raise ValueError('one registered RGB source required')
        rgb=rgb_sources[0]
        if (rgb.get('clock_mapping_status')!='verified' or not rgb.get('clock_mapping_id')
                or rgb.get('frame_id')!=point['frame_id'] or rgb.get('observed_at')!=point.get('observed_at')
                or rgb.get('width')!=homography.get('width') or rgb.get('height')!=homography.get('height')
                or point.get('calibration_version')!=homography.get('id')):
            raise ValueError('verified RGB floor-contact observation required')
        return
    if len(depth_sources)!=1:
        raise ValueError('one registered measured depth source required')
    media,depth=depth_sources[0]
    if (depth.get('alignment_frame')!=registration.get('source_frame')
            or depth.get('units')!='millimetres' or depth.get('invalid_value')!=0
            or depth.get('calibration_content_id')!=point.get('calibration_version')
            or depth.get('derivation')!='depth-clock-v1' or not isinstance(depth.get('source_media_id'),str)
            or media.get('source_key')!='depth-mapped-v1:'+depth['source_media_id']
            or depth.get('geometric_validity')!='validated' or not depth.get('depth_validation_receipt')
            or depth.get('clock_mapping_status')!='verified' or not depth.get('clock_mapping_id')
            or depth.get('frame_id')!=point['frame_id']
            or depth.get('observed_at')!=point.get('observed_at')):
        raise ValueError('validated depth geometry and observation mapping required')


def publish_room(store, sid, points, registration, room_map, *, generation, as_of, sampling_receipt=None, arms=None):
    """Publish one room snapshot. arms=None covers both arms (full snapshot);
    otherwise only those arms are replaced and the other arm's still-valid
    positions from the previous snapshot are kept with their own expiry."""
    if arms is not None and (not isinstance(arms,(list,tuple)) or not set(arms)<={'a','b'}
            or any(p.get('arm') not in arms for p in points)):
        raise ValueError('published points must belong to the covered arms')
    session=store.get_session(sid)
    if (registration.get('site_id')!=session['site_id'] or registration.get('camera_id')!=session['camera_id']
            or not finite(as_of) or not store.clock()-3<=as_of<=store.clock()):
        raise ValueError('fresh site-bound room evidence required')
    for point in points:
        if (point.get('session_id')!=sid or point.get('generation')!=generation
                or not point.get('frame_id') or not point.get('capture_epoch')
                or not isinstance(point.get('media_ids'),list) or not 1<=len(point['media_ids'])<=8):
            raise ValueError('source frame and generation provenance required')
        _check_sources(store,sid,session,point,registration)
    context=build_room_context(points,registration,room_map,as_of=as_of)
    observations=[]
    for person in context['people']:
        source=next(p for p in points if p['arm'].upper()==person['arm'] and p['track_id']==person['track_id'])
        roots=source.get('evidence_roots')
        if (not isinstance(roots,list) or not 1<=len(roots)<=64
                or any(not isinstance(root,str) or not 1<=len(root)<=256 for root in roots)):
            raise ValueError('spatial evidence needs original correlation roots')
        relations=[r for r in context['memberships'] if r['arm']==source['arm'] and r['track_id']==source['track_id']]
        if person['method']=='stereo_surface':
            quality={'method':'stereo_surface','surface_only':True,'independent_confirmation':False,
                'transform_version':registration['transform_version'],'validation_receipt':registration['validation_receipt']}
        else:
            homography=registration['floor_homography']
            quality={'method':'rgb_floor_contact','surface_only':False,'floor_contact':True,
                'independent_confirmation':False,'transform_version':registration['transform_version'],
                'homography_version':homography['id'],'validation_receipt':homography['validation_receipt']}
        observations.append({'site_id':session['site_id'],'camera_id':session['camera_id'],
            'generation':generation,'arm':source['arm'],'track_id':source['track_id'],
            'frame_id':source['frame_id'],'capture_epoch':source['capture_epoch'],
            'observed_at':person['observed_at'],'available_at':store.clock(),'valid_until':person['valid_until'],
            'coordinate_frame':context['coordinate_frame'],'calibration_version':person['calibration_version'],
            'map_version':context['map_version'],'evidence_roots':list(dict.fromkeys(roots)),
            'status':'completed','result':{'tracks':[person],'relations':relations,'quality':quality}})
    with store.transaction() as db:
        active=db.execute('SELECT generation FROM lab_experiment_state WHERE session_id=?',(sid,)).fetchone()
        if store.get_session(sid)['state']!='recording' or not active or active[0]!=generation:
            raise ValueError('inactive spatial experiment')
        activation=db.execute("SELECT payload FROM events WHERE session_id=? AND kind='experiment.activated' ORDER BY id DESC LIMIT 1",(sid,)).fetchone()
        settings=json.loads(activation[0]) if activation else {}
        if settings.get('generation')!=generation:raise ValueError('recorded spatial policy required')
        policy=settings.get('spatial_policy',{})
        allowed=lambda arm,method:(policy.get(arm,{}).get('localization'),method) in ADMITTED
        admitted=[]
        for observation in observations:
            arm_policy=policy.get(observation['arm'],{})
            if not allowed(observation['arm'],observation['result']['quality']['method']):continue
            if arm_policy.get('room_relations') is not True:
                observation['result']['relations']=[]
            admitted.append(observation)
        observations=admitted
        # The displayed context follows the same arm policy: a floor-projection
        # arm never shows stereo positions, and a 'none' arm shows no position.
        withheld={(p['arm'].lower(),p['track_id']) for p in context['people'] if not allowed(p['arm'].lower(),p['method'])}
        context['people']=[p for p in context['people'] if (p['arm'].lower(),p['track_id']) not in withheld]
        context['memberships']=[m for m in context['memberships'] if (m['arm'],m['track_id']) not in withheld]
        context['unavailable'].extend({'track_id':t,'arm':a,'reason':'arm_policy_excludes_method'} for a,t in sorted(withheld))
        previous=db.execute("SELECT payload FROM events WHERE session_id=? AND kind='room.output' ORDER BY id DESC LIMIT 1",(sid,)).fetchone() if arms is not None else None
        prior=json.loads(previous[0]) if previous else {}
        room=prior.get('room') or {}
        if (prior.get('generation')==generation and room.get('map_version')==context['map_version']
                and room.get('registration_version')==context['registration_version']):
            # Carried positions keep their own expiry; nothing is refreshed.
            kept=[p for p in room.get('people',[]) if p['arm'].lower() not in arms and p['available_at']<=as_of<=p['valid_until']]
            keys={(p['arm'].lower(),p['track_id']) for p in kept}
            context['people'].extend(kept)
            context['memberships'].extend(m for m in room.get('memberships',[]) if (m['arm'],m['track_id']) in keys)
        payload={'room':context,'generation':generation,'available_at':store.clock(),
            'reasoning_spatial_policy':policy,
            'source_frames':[{k:p[k] for k in ('arm','track_id','frame_id','capture_epoch','media_ids')} for p in points]}
        size=len(canonical(payload).encode())+sum(len(canonical(value).encode()) for value in observations)
        if sampling_receipt is not None:size+=len(canonical(sampling_receipt).encode())
        if size>256*1024:raise ValueError('room evidence exceeds event bound')
        store.check_capacity(size+65536)
        store._event(db,sid,'room.output',payload)
        for observation in observations:store._event(db,sid,'room.observation',observation)
        if sampling_receipt is not None:store._event(db,sid,'room.sampling',sampling_receipt)
    return context


def room_at(store, session, cutoff):
    if not finite(cutoff):raise ValueError('finite room replay cutoff required')
    with store.lock:
        row=store.db.execute("""SELECT payload FROM events WHERE session_id=? AND kind='room.output'
            AND json_extract(payload,'$.available_at')<=?
            AND json_extract(payload,'$.room.as_of')<=?
            ORDER BY json_extract(payload,'$.room.as_of') DESC,id DESC LIMIT 1""",(session['id'],cutoff,cutoff)).fetchone()
        activation=store.db.execute("""SELECT payload FROM events WHERE session_id=? AND kind='experiment.activated'
            AND json_extract(payload,'$.effective_at')<=? ORDER BY id DESC LIMIT 1""",(session['id'],cutoff)).fetchone()
    if not row or not activation:return None
    payload=json.loads(row[0]);active=json.loads(activation[0])
    if payload['generation']!=active['generation']:return None
    room=payload['room']
    if room.get('site_id')!=session['site_id'] or room.get('camera_id')!=session['camera_id']:return None
    room['people']=[p for p in room['people'] if p['available_at']<=cutoff<=p['valid_until']]
    room['memberships']=[p for p in room['memberships'] if p['available_at']<=cutoff<=p['valid_until']]
    room['replay_cutoff']=cutoff
    return room
