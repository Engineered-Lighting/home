"""Minimal cloud projection. Local provenance remains in the session ledger."""

import hashlib
import re
from reasoning import finite, plain_copy

# Initial den vocabulary. Unknown class text is never forwarded as arbitrary prose.
OBJECTS=frozenset(('person','book','cell phone','phone','laptop','keyboard','mouse','tv',
    'chair','couch','dining table','bed','cup','bottle','bowl','cat','dog','backpack',
    'handbag','remote','potted plant','clock','vase','scissors'))
PROVENANCE=('id','site_id','camera_id','session_id','arm','generation','frame_id',
    'observed_at','available_at','valid_until','capture_epoch','evidence_roots','source_kind')
SCORES=('SimCC_response_not_probability','heatmap_response_not_probability','unknown')
JOINTS=('nose','left_eye','right_eye','left_ear','right_ear','left_shoulder','right_shoulder',
    'left_elbow','right_elbow','left_wrist','right_wrist','left_hip','right_hip',
    'left_knee','right_knee','left_ankle','right_ankle')
from ambient_context import WEATHER
HA_FACTS={'computer_active':('on','off'), 'screen_playback':('playing','paused','idle','off'),
          'person_present':('on','off'), 'den_audio_playback':('playing','paused','idle','off'),
          # v2 (computer_active_v2) and v3 (computer_active_v3) buckets; one
          # source file configures one rule, so a packet carries one scheme.
          'computer_input_recency':('under_15s','15_to_60s','under_60s','1_to_5_min','over_5_min','locked'),
          # Independent of the camera: a den lamp's on/off state (never attributes
          # or control) and the owner's home/away (never a zone name or place).
          'den_light':('on','off'), 'owner_home':('home','away'),
          # Ambient-only facts: they feed the shared context block, never device state.
          'daylight':('day','dark'), 'weather_condition':WEATHER}
AMBIENT_FACTS=frozenset(('daylight','weather_condition'))
# Presence binds only from a person.* entity; egress needs its own household grant.
PRESENCE_EGRESS='owner-presence-1'
# Den device context (media, lamp, input recency); its own household grant.
DEVICE_EGRESS='den-devices-1'
# HA device facts whose egress needs an explicit household grant, as
# {fact: (household policy key, exact schema value)}. Without it the fact is
# dropped from the packet per tick and marked not_granted in the shared context.
# person_present, computer_active and screen_playback predate these grants and
# need none. daylight/weather_condition ride the ambient_context grant: they
# feed only the ambient block, never device state.
FACT_GRANTS={'owner_home':('owner_presence',PRESENCE_EGRESS),
             'den_audio_playback':('device_context',DEVICE_EGRESS),
             'den_light':('device_context',DEVICE_EGRESS),
             'computer_input_recency':('device_context',DEVICE_EGRESS)}
# Continuous Qwen scene projection; granted by the household policy's
# structured_sources list naming exactly this schema.
QWEN_EGRESS='lab-live-scene-1'
# V-JEPA 2.1 den projection (temporal_encoder family); granted by the household
# policy's structured_sources list naming exactly this grant.
VJEPA_EGRESS='vjepa-den-1'
# Provenance identifiers that could carry operator- or source-chosen text leave
# only as opaque, stable digests; the local ledger keeps the originals.
OPAQUE={'evidence_roots':'root','frame_id':'frame','capture_epoch':'epoch'}
OPAQUE_TOKEN=re.compile(r'(root|frame|epoch)-[0-9a-f]{24}')


def opaque(prefix, value):
    """'<prefix>-' + 24 hex of sha256(value); an existing token is kept (idempotent)."""
    if isinstance(value,str) and OPAQUE_TOKEN.fullmatch(value) and value.startswith(prefix+'-'):
        return value
    return prefix+'-'+hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:24]


def fact_granted(fact, policy):
    """True when the fact needs no grant or the household policy names its schema."""
    grant=FACT_GRANTS.get(fact)
    return grant is None or (isinstance(policy,dict) and policy.get(grant[0])==grant[1])


def granted_bindings(bindings, not_granted):
    """Operator bindings minus facts whose household grant is absent this tick."""
    return {entity:fact for entity,fact in (bindings or {}).items() if fact not in set(not_granted or ())}


# Structural keys a qwen-scene-projection-1 object may use (values are checked
# against qwen_scene.projection_tokens()).
QWEN_KEYS=frozenset({'family','schema','result_schema','status','latest','state','age','facts','calibration',
    'independent_confirmation','origin_family','correlated_with','method','window','valid_samples','invalid_samples',
    'segments','earlier_segments_omitted','latest_activity_agreement','last_activity_change','activity','span',
    'samples','cues','ongoing','value','agreement','summary_last_60s','summary_last_120s'})


def qwen_projection(value):
    """A copy of an exact qwen-scene-projection-1 object, else None.

    Every string must be a projection token, every key structural or a field
    name; numbers (and anything else) are refused, so no time, count,
    identifier, digest or free text can ride along."""
    from qwen_scene import KEYS, PROJECTION_SCHEMA, projection_tokens
    tokens,keys=projection_tokens(),QWEN_KEYS|set(KEYS)

    def allowed(item, depth):
        if depth>8:return False
        if item is None or type(item) is bool:return True
        if type(item) is str:return item in tokens
        if type(item) is list:return len(item)<=16 and all(allowed(x,depth+1) for x in item)
        if type(item) is dict:
            return len(item)<=32 and all(type(k) is str and k in keys and allowed(x,depth+1) for k,x in item.items())
        return False
    if not isinstance(value,dict) or value.get('schema')!=PROJECTION_SCHEMA or not allowed(value,0):
        return None
    return plain_copy(value)


def vjepa_projection(value):
    """A copy of an exact vjepa-den-projection-1 object, else None.

    Buckets only: every key and every string must be a projection token
    (vjepa_scene.projection_tokens()); numbers, and so any probability, drift,
    box, time, count or identifier, are refused."""
    from vjepa_scene import FAMILY, PROJECTION_SCHEMA, projection_tokens
    tokens=projection_tokens()

    def allowed(item, depth):
        if depth>6:return False
        if item is None or type(item) is bool:return True
        if type(item) is str:return item in tokens
        if type(item) is dict:
            return len(item)<=24 and all(type(k) is str and k in tokens and allowed(x,depth+1) for k,x in item.items())
        return False
    if (not isinstance(value,dict) or value.get('schema')!=PROJECTION_SCHEMA or value.get('family')!=FAMILY
            or value.get('calibration')!='unvalidated' or value.get('independent_confirmation') is not False
            or not allowed(value,0)):
        return None
    return plain_copy(value)


def ha_state(fact, state):
    """A raw HA state as its registered fact value, else 'unknown'."""
    if fact=='owner_home':
        # HA person state: 'home', 'not_home' or another zone's name. Any known
        # non-home state is 'away'; the zone name itself never leaves.
        return 'unknown' if not isinstance(state,str) or not state else 'home' if state=='home' else 'away'
    return state if state in HA_FACTS[fact] else 'unknown'


# Lab-derived HA ids (reserved 'derived.' domain) and the only fact each may bind.
# The derived fact is independent of the camera; its inputs stay local.
DERIVED_HA={'derived.computer_active':'computer_active','derived.computer_input_recency':'computer_input_recency'}
ZONE_TYPES=('desk','sofa','reading_chair','table','doorway')
# Code-computed head-direction words (jev_semantics.head_direction, rule
# head-direction-1): the only values a head block may carry. The pixel
# measures behind them never leave. monitor_side is operator site config (the
# JEV provisioning file), never sent; it only maps yaw to head_toward.
from jev_semantics import HEAD_PITCH, HEAD_SUPPORT, HEAD_TOWARD, HEAD_YAW, MONITOR_SIDES
HEAD_WORDS={'yaw':HEAD_YAW,'pitch':HEAD_PITCH,'support':HEAD_SUPPORT,'head_toward':HEAD_TOWARD}
# Code-computed desk-seat words (jev_semantics.frame_position, rule
# desk-seat-1), the only values den-facts-7's now.position may carry. The box
# and the site's desk_seat region (operator config in the JEV provisioning
# file, checked by desk_seat_config) never leave.
from jev_semantics import POSITIONS, desk_seat_config


def head_words(head, monitor_side=None):
    """True when a head block holds exactly the registered words: yaw, pitch
    and support, plus head_toward only when a site monitor_side is set."""
    keys={'yaw','pitch','support'}|({'head_toward'} if isinstance(monitor_side,str) and monitor_side in MONITOR_SIDES else set())
    return (isinstance(head,dict) and set(head)==keys
            and all(type(head[k]) is str and head[k] in HEAD_WORDS[k] for k in keys))
# Historical caption egress schema; the household policy must grant exactly this.
CAPTION_EGRESS='caption-activity-1'
CAPTION_ACTIVITIES=('reading','phone_use','computer_work')
# Ambient context egress schema; the household policy must grant exactly this.
AMBIENT_EGRESS='ambient-context-1'
# Spatial track methods and the depth claim each supports. RGB floor contact is
# a homography estimate, never measured depth.
TRACK_DEPTH={'stereo_surface':'validated_surface','rgb_floor_contact':'rgb_floor_estimate'}


def track_reference(record, item):
    from reasoning import digest
    import re
    identity=item.get('track_id')
    reference=(digest([record.get('session_id'),record.get('arm'),record.get('generation'),record.get('capture_epoch'),identity])[:24]
        if isinstance(identity,str) and identity else item.get('track_ref'))
    return reference if isinstance(reference,str) and re.fullmatch('[a-f0-9]{24}',reference) else None


def pixel_box(box, width, height):
    """Source-pixel XYWH only; absent dimensions never imply valid geometry."""
    if not isinstance(box,dict) or width is None or height is None:return None
    coords=[box.get(k) for k in ('x','y','w','h')]
    if not all(finite(v) for v in coords):return None
    x,y,w,h=coords
    if not (0<=x<x+w<=width and 0<=y<y+h<=height):return None
    return dict(zip(('x','y','w','h'),coords))


def project(records, *, ha_bindings=None, zone_bindings=None):
    """Allowlisted cloud records. ha_bindings are the bindings in force this
    tick (JevProvider removes facts whose household grant is absent). An HA
    record left without a bound fact, and a Qwen or V-JEPA record without an
    exact projection, are dropped rather than sent as empty shells."""
    result=[]
    for record in records:
        value={k:plain_copy(record[k]) for k in PROVENANCE if k in record}
        # Opaque provenance: HA roots carry operator source ids and frame ids
        # the camera owner's session name; only digests leave.
        roots=value.get('evidence_roots')
        if roots is not None:
            roots=[opaque('root',r) for r in roots if isinstance(r,str) and r] if isinstance(roots,list) else []
            value['evidence_roots']=list(dict.fromkeys(roots))[:64]
            if not value['evidence_roots']:value.pop('evidence_roots')
        for key in ('frame_id','capture_epoch'):
            if key in value:
                if isinstance(value[key],str) and value[key]:value[key]=opaque(OPAQUE[key],value[key])
                else:value.pop(key)
        quality=record.get('quality',{})
        dimensions={k:quality[k] for k in ('image_width','image_height')
            if finite(quality.get(k)) and 0<quality[k]<=4096}
        width,height=dimensions.get('image_width'),dimensions.get('image_height')
        boxes=record.get('boxes',[])
        refs={}
        value['objects']=[]
        for obj in record.get('objects',[])[:128]:
            if not isinstance(obj,dict) or obj.get('label') not in OBJECTS:continue
            item={'label':obj['label']}
            if finite(obj.get('confidence')) and 0<=obj['confidence']<=1:item['confidence']=obj['confidence']
            # Never forward worker IDs. This index is scoped to this observation,
            # not a person identity or continuity claim across frames.
            reference='object-'+str(len(value['objects']))
            identity=obj.get('instance_id',obj.get('instance_ref'))
            if isinstance(identity,str) and identity:
                refs[identity]=reference if identity not in refs else None
                item['instance_ref']=reference
            matches=[b for b in boxes[:128] if isinstance(b,dict) and identity is not None
                     and b.get('instance_id')==identity and b.get('label')==obj['label']]
            candidate=obj.get('pixel_box') if not matches else matches[0] if len(matches)==1 else None
            geometry=pixel_box(candidate,width,height)
            if geometry is not None:item['pixel_box']=geometry
            value['objects'].append(item)
        value['pose_instances']=[]
        for pose in record.get('pose_instances',[])[:2]:
            if not isinstance(pose,dict) or pose.get('layout') not in ('coco17','coco_wholebody133'):continue
            joints=[]
            for joint in pose.get('joints',[])[:17]:
                if not isinstance(joint,dict) or type(joint.get('joint_id')) is not int or not 0<=joint['joint_id']<17:continue
                pixel=joint.get('pixel');score=joint.get('score')
                valid=(isinstance(pixel,list) and len(pixel)==2 and width is not None and height is not None
                    and all(finite(p) for p in pixel) and 0<=pixel[0]<=width and 0<=pixel[1]<=height)
                joints.append({'joint_id':joint['joint_id'],'name':JOINTS[joint['joint_id']],'pixel':pixel if valid else None,
                    'score':score if finite(score) else None,
                    'status':'supported' if valid and joint.get('status')=='supported' else 'uncertain'})
            semantics=pose.get('score_semantics','unknown')
            item={'layout':'coco17','joints':joints,'score_semantics':semantics if semantics in SCORES else 'unknown',
                  'source_layout':pose.get('source_layout',pose['layout'])}
            if item['source_layout'] not in ('coco17','coco_wholebody133'):item['source_layout']='coco17'
            identity=pose.get('instance_id',pose.get('instance_ref'))
            reference=refs.get(identity) if isinstance(identity,str) else None
            if reference is not None:
                person=next(o for o in value['objects'] if o.get('instance_ref')==reference)
                if person['label']=='person':item['instance_ref']=reference
            value['pose_instances'].append(item)
        value['quality']=dimensions
        value['quality']['metric_depth']='not_supplied'
        ha={}
        correlated=[]
        for entity,fact in (ha_bindings or {}).items():
            if fact not in HA_FACTS:raise ValueError('Unregistered HA semantic fact')
            if isinstance(entity,str) and entity.split('.',1)[0]=='derived' and DERIVED_HA.get(entity)!=fact:
                raise ValueError('Derived HA binding must name its own fact')
            if fact=='owner_home' and (not isinstance(entity,str) or entity.split('.',1)[0]!='person'):
                raise ValueError('Owner presence binds only a person entity')
            if fact in AMBIENT_FACTS:continue
            raw=record.get('ha',{}).get(entity,{})
            state=raw.get('state') if isinstance(raw,dict) else None
            if entity in record.get('ha',{}):ha[fact]=ha_state(fact,state)
            if isinstance(raw,dict) and raw.get('origin')=='same_camera_visual' and entity not in DERIVED_HA:correlated.append(fact)
        # Reprojection accepts only names enabled by the operator binding.
        for fact in set((ha_bindings or {}).values())-AMBIENT_FACTS:
            if fact in record.get('ha',{}):
                state=record['ha'][fact]
                ha[fact]=state if state in (*HA_FACTS[fact],'unknown') else 'unknown'
        if record.get('source_kind')=='ha.observation' and not ha:
            continue  # no bound (or granted) fact: nothing to send
        if ha:
            value['ha']=ha
            correlated.extend(f for f in quality.get('camera_derived_ha_facts',[]) if f in ha)
            if correlated:
                value['quality']['camera_derived_ha_facts']=sorted(set(correlated))
                # Structural link to the visual source, not only a label: these
                # facts come from this camera's images, like its visual records.
                value['quality']['correlated_with']={'relation':'same_camera_visual','camera_id':record.get('camera_id')}
        relations=[]
        permitted=set((zone_bindings or {}).values())
        for relation in record.get('relations',[])[:32]:
            if not isinstance(relation,dict):continue
            zone=(zone_bindings or {}).get(relation.get('zone_id'),relation.get('zone_type'))
            state=relation.get('relation')
            if zone in permitted and zone in ZONE_TYPES and state in ('inside','outside','uncertain','boundary','unknown') and record.get('source_kind')=='room.observation':
                item={'zone_type':zone,'relation':state}
                reference=track_reference(record,relation)
                if reference:item['track_ref']=reference
                relations.append(item)
        if relations:value['relations']=relations
        if record.get('source_kind')=='room.observation':
            tracks=[]
            for track in record.get('tracks',[])[:16]:
                if not isinstance(track,dict) or track.get('method') not in TRACK_DEPTH:continue
                pos=track.get('pos');uncertainty=track.get('uncertainty_m')
                if not isinstance(pos,list) or len(pos)!=3 or not all(finite(v) for v in pos):continue
                if not finite(uncertainty) or uncertainty<0:continue
                reference=track_reference(record,track)
                if reference is None:continue
                tracks.append({'track_ref':reference,'pos':pos,'units':'metres','uncertainty_m':uncertainty,'method':track['method']})
            if tracks:
                value['tracks']=tracks
                # A record with any RGB estimate claims only the weaker estimate.
                methods={t['method'] for t in tracks}
                value['quality']['metric_depth']=TRACK_DEPTH['rgb_floor_contact' if 'rgb_floor_contact' in methods else 'stereo_surface']
                value['quality']['tracking']='session_scoped_spatial_tracks_not_identity'
        if record.get('source_kind')=='caption.receipt':
            # Bounded enum only. Caption text, rule and digests never reach this
            # path; the caption shares the visual source, so it stays correlated.
            caption=record.get('historical_caption') if isinstance(record.get('historical_caption'),dict) else {}
            version=caption.get('version')
            value['historical_caption']={'schema':CAPTION_EGRESS,
                'activity':caption['activity'] if caption.get('activity') in CAPTION_ACTIVITIES else 'unavailable',
                'interpretation':'historical_model_interpretation',
                'version':version if isinstance(version,str) and re.fullmatch(r'caption-projection-\d{1,4}:[0-9a-f]{12}',version) else 'unknown'}
            value['quality']['correlated_with']={'relation':'same_camera_visual','camera_id':record.get('camera_id')}
        if record.get('source_kind')=='qwen.observation':
            # Closed-vocabulary projection only (no receipt facts, digests,
            # times or reply text); an inexact one drops the record.
            scene=qwen_projection(record.get('qwen_scene'))
            if scene is None:
                continue
            value['qwen_scene']=scene
            value['quality']['correlated_with']={'relation':'same_camera_visual','camera_id':record.get('camera_id')}
        if record.get('source_kind')=='vjepa.observation':
            # Uncalibrated bucket words only (no probability, drift, box, time
            # or identifier); an inexact projection drops the record. It shares
            # the camera frames, so it stays correlated with the visual source.
            projected=vjepa_projection(record.get('vjepa_scene'))
            if projected is None:
                continue
            value['vjepa_scene']=projected
            value['quality']['correlated_with']={'relation':'same_camera_visual','camera_id':record.get('camera_id')}
        if record.get('source_kind')=='room.facts':
            # Room facts (room-facts-1): category words only (no coordinate,
            # distance, zone or target name); an inexact record is dropped.
            # Derived from this camera's frames and depth: same camera.
            from room_words import projection as room_projection
            words=room_projection(record.get('room_facts'))
            if words is None:
                continue
            value['room_facts']=words
            value['quality']['correlated_with']={'relation':'same_camera_visual','camera_id':record.get('camera_id')}
        # HA and room facts require their own registered allowlists. Do not send
        # free-form entity attributes, zone names or identity through this path.
        result.append(value)
    return result


LOCAL_HOURS=frozenset(f'{h:02d}:00-{h+1:02d}:00' for h in range(24))


def project_context(block):
    """Allowlisted shared context block: exact shape, closed enums and a
    recomputed digest. Clock facts are re-derived by the policy from the
    cutoff and its own site zone; nothing here reads a clock or network."""
    from ambient_context import (CONTEXT_SCHEMA, COVERAGE, FACT_COVERAGE, FAMILIES, INTERPRETATION, ORIGINS, PARTS,
        SCHEMA, DAYLIGHT, context_digest)
    if block is None:return None
    facts=block.get('ha_facts') if isinstance(block,dict) else None
    if (not isinstance(block,dict) or set(block)-{'ambient'}!={'schema','coverage','digest_scope','ha_facts','shared_context_digest'}
            or block['schema']!=CONTEXT_SCHEMA or not isinstance(block['coverage'],dict)
            or set(block['coverage'])!=set(FAMILIES) or any(v not in COVERAGE for v in block['coverage'].values())
            or not isinstance(block['digest_scope'],list) or block['digest_scope']!=sorted(set(block['digest_scope']))
            or not set(block['digest_scope'])<=set(FAMILIES)
            or not isinstance(facts,dict) or list(facts)!=sorted(facts) or len(facts)>8
            or any(f not in HA_FACTS or f in AMBIENT_FACTS or v not in FACT_COVERAGE for f,v in facts.items())
            or ('ambient' in block)!=(block['coverage']['ambient'] in ('supplied','timezone_unavailable'))):
        raise ValueError('invalid shared context')
    value={'schema':CONTEXT_SCHEMA,'coverage':{f:block['coverage'][f] for f in FAMILIES},'digest_scope':list(block['digest_scope']),
           'ha_facts':dict(facts)}
    if 'ambient' in block:
        a=block['ambient']
        if (not isinstance(a,dict) or set(a)!={'schema','part_of_day','day_type','local_hour','daylight','weather','origin_family','interpretation'}
                or a['schema']!=SCHEMA or a['part_of_day'] not in {p[2] for p in PARTS}|{'unknown'}
                or a['day_type'] not in ('weekday','weekend','unknown') or a['local_hour'] not in LOCAL_HOURS|{'unknown'}
                or a['daylight'] not in (*DAYLIGHT.values(),'unknown') or a['weather'] not in (*WEATHER,'unknown')
                or a['origin_family']!=ORIGINS or a['interpretation']!=INTERPRETATION
                # An unloadable zone leaves every clock fact unknown, never a guess.
                or (block['coverage']['ambient']=='timezone_unavailable'
                    and (a['part_of_day'],a['day_type'],a['local_hour'])!=('unknown',)*3)):
            raise ValueError('invalid ambient context')
        value['ambient']={k:plain_copy(a[k]) for k in a}
    value['shared_context_digest']=context_digest(value)
    if block['shared_context_digest']!=value['shared_context_digest']:
        raise ValueError('shared context digest mismatch')
    return value
