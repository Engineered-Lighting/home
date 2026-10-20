"""Deterministic semantic facts; no inference, identity or physical-contact claims."""
import math
from reasoning import PC_NOTE, finite

# 4: shared context block (coverage, ambient priors) and its digest.
# 5: continuous Qwen scene block (unvalidated, never independent) and a
# code-computed corroboration block; shared-context-2 per-fact HA coverage.
# 6: corroboration-2 (PC input counts for computer_work only under 15 s with a
# camera cue; held book/phone is a conflict), static-scene-1 background
# objects, and phone relations (phone-length margin, phone near face).
# 7: head-direction-1 per-observation head words (yaw, pitch, support, and
# head_toward with a site monitor_side) and corroboration-3 (head toward the
# monitor side is a camera cue at the PC).
# Rule desk-seat-1 (frame_position, the PC_POSITION_CUE) feeds only the
# den-facts-7 wire; this v5 semantic block does not carry it, so it stays 7.
VERSION='den-semantics-7'
# Ambient time, daylight and weather are priors about the setting, never
# observations of activity; every question says so.
CONTEXT_NOTE=' Time of day, daylight and weather in the shared context are priors about the setting, not observations of activity.'
QUESTIONS={
 'reading_supported':{'type':'noul','instructions':'Does semantic evidence support the observed person actively attending to reading material? A detected or held book alone, including a closed book, does not establish reading. Use only observed evidence; do not infer attention from proximity.'+CONTEXT_NOTE},
 'phone_use_supported':{'type':'noul','instructions':'Does semantic evidence support active phone use by the observed person? A nearby phone alone does not establish use.'+CONTEXT_NOTE},
 'computer_work_supported':{'type':'noul','instructions':'Does semantic evidence support active computer work by the observed person? Sitting near a computer or passive screen viewing alone does not establish work.'+PC_NOTE+' corroboration.activities.computer_work.pc_input says whether PC input counts.'+CONTEXT_NOTE}
}


# Code-computed corroboration (rule corroboration-3). Same-camera signals are
# one line of evidence seen by several models; independent signals come from
# devices that never see the camera. Consistent-with, never proof.
# corroboration-3: corroboration-2 plus head_toward monitor_side as a camera cue.
CORROBORATION_RULE='corroboration-3'
RELATION_OBJECTS={'reading':('book',),'phone_use':('cell_phone','phone'),'computer_work':('keyboard','mouse'),'resting':()}
# Relation facts that are not wrist-object pairs, per activity.
RELATION_FACTS={'phone_use':('phone_near_face_in_image',)}
QWEN_SUPPORT={'reading':{'activity_hypothesis':'reading','held_object':'book','book_state':'open','gaze_target':'book'},
              'phone_use':{'activity_hypothesis':'phone_use','held_object':'phone','gaze_target':'phone'},
              'computer_work':{'activity_hypothesis':'computer_work','typing_visible':'yes','gaze_target':'screen'},
              'resting':{'activity_hypothesis':'resting'}}
# PC input (computer_input_recency, computer_active) is judged by pc_input(),
# never listed here: by itself it is not evidence about the person in view.
INDEPENDENT_SUPPORT={'reading':{'den_light':('on',)},
                     'phone_use':{},
                     'computer_work':{},
                     'resting':{'computer_input_recency':('over_5_min','locked'),'den_audio_playback':('playing',)}}
# PC input buckets: v3 (computer_active_v3) and v2 (computer_active_v2).
PC_CURRENT=('under_15s',)
PC_RECENT=('under_15s','15_to_60s','under_60s')
PC_IDLE=('1_to_5_min','over_5_min','locked')
# Camera cues that the observed person is at the PC: Qwen facts, wrist
# relations and (corroboration-3) the head turned toward the site's monitor
# side. A detected tv/monitor box is never one.
PC_QWEN_CUES={'gaze_target':'screen','typing_visible':'yes','screen_facing_subject':'yes'}
PC_RELATION_OBJECTS=('keyboard','mouse')
PC_HEAD_CUE='monitor_side'
# den-facts-7: the person's box in the site's desk-seat region (desk-seat-1).
PC_POSITION_CUE='desk_seat'
# An object in hand that is reading/phone evidence conflicts with computer work.
HELD_CONFLICTS=('book','phone')


def _support_word(count):
    return 'none' if count<=0 else 'one' if count==1 else 'multiple'


def pc_input(devices, camera_devices, relations, facts, head_toward=None, position=None, room_cue=False):
    """(status, conflicts, counts) for PC input as computer_work evidence.

    PC input is independent of the camera but says nothing about who used the
    PC: it counts only when under 15 s (v3 buckets) and a camera cue places
    the observed person at the PC, and never while Qwen sees a book or phone
    in hand (also when the head points at the monitor side or the lap). v2
    'under_60s' and v1 computer_active 'on' never count. head_toward is the
    latest frame's head_toward word, None without a site monitor_side;
    position is the latest frame's desk-seat-1 word (den-facts-7 only), None
    otherwise, and desk_seat is a camera cue at the PC. room_cue (den-facts-8
    only) is True when a current room fact the wire's table marks as a camera
    cue at the PC holds (jev_wire.ROOM_SUPPORT)."""
    def state(fact):
        return devices.get(fact) if fact not in camera_devices else None
    recency,active=state('computer_input_recency'),state('computer_active')
    known=recency in PC_RECENT+PC_IDLE
    recent=recency in PC_RECENT or (not known and active=='on')
    idle=recency in PC_IDLE or (not known and active=='off')
    cue=(any(facts.get(k)==v for k,v in PC_QWEN_CUES.items())
         or any(f.endswith('_near_'+name+'_in_image') for f in relations for name in PC_RELATION_OBJECTS)
         or head_toward==PC_HEAD_CUE or position==PC_POSITION_CUE or room_cue is True)
    conflicts=['qwen_held_object_'+facts['held_object']] if facts.get('held_object') in HELD_CONFLICTS else []
    if recency in PC_CURRENT and cue and not conflicts:
        return 'pc_input_under_15s_with_camera_cue',conflicts,True
    if recent:
        return ('pc_input_recent_but_conflicts_with_held_object' if conflicts
                else 'pc_input_recent_but_not_observed_at_pc'),conflicts,False
    return ('pc_input_not_recent' if idle else 'pc_input_unknown'),conflicts,False


def corroboration(observations, qwen, latest_id):
    """Per activity: which current same-camera and independent signals are
    consistent with it. Only fresh observations count; a camera-derived HA
    fact (occupancy) stays on the same-camera side."""
    fresh=[o for o in observations if o['freshness']=='fresh']
    latest=next((o for o in fresh if o['evidence_id']==latest_id),None)
    relations={f['fact'] for f in (latest or {}).get('facts',[])}
    head=((latest or {}).get('head') or {}).get('head_toward')
    devices,camera_devices={},set()
    for o in sorted(fresh,key=lambda o:-o['age_seconds']):
        devices.update(o['device_state'])
        camera_devices.update(o['camera_derived_device_facts'])
    occupancy=devices.get('person_present','not_supplied') if 'person_present' in devices else 'not_supplied'
    facts=(qwen or {}).get('latest_facts') or {}
    result={}
    for activity in ('reading','phone_use','computer_work','resting'):
        same=[]
        if (any(fact.endswith('_near_'+name+'_in_image') for fact in relations for name in RELATION_OBJECTS[activity])
                or relations & set(RELATION_FACTS.get(activity,()))):
            same.append('detector_pose_relation')
        if facts and any(facts.get(k)==v for k,v in QWEN_SUPPORT[activity].items()):
            same.append('qwen_scene')
        if occupancy=='on':
            same.append('camera_occupancy')
        independent=sorted(fact for fact,states in INDEPENDENT_SUPPORT[activity].items()
                           if fact not in camera_devices and devices.get(fact) in states)
        extra={}
        if activity=='computer_work':
            status,conflicts,counts=pc_input(devices,camera_devices,relations,facts,head)
            if counts:independent=sorted(independent+['computer_input_recency'])
            extra={'pc_input':status,'conflicts':conflicts}
        result[activity]={'same_camera':same,'independent':independent,'independent_support':_support_word(len(independent)),**extra}
    return {'rule':CORROBORATION_RULE,'basis':'fresh_observations_at_cutoff','camera_occupancy':occupancy,'activities':result,
            'note':'same_camera signals share one camera and are not independent confirmation of each other; independent signals are consistent with an activity, never proof of it. PC input counts for computer_work only when under 15 s with a camera cue that the observed person is at the PC (a Qwen screen cue, a wrist near the keyboard or mouse, or the latest head_toward monitor_side); pc_input says why it does or does not count, and conflicts lists what in hand contradicts computer work.'}


# Static scene objects (rule static-scene-1), from this arm's packet only: an
# object whose box stays put across the packet's recent visual frames, or that
# is seen while no person is present, is background_static and never an
# activity cue. An object a wrist or the face is near is never tagged (a
# keyboard does not move; a held book is in use).
STATIC_RULE='static-scene-1'
STATIC_IOU=.8
STATIC_MIN_FRAMES=3
STATIC_WINDOW=8
STATIC_BOXES_PER_LABEL=8


def _iou(a, b):
    w=min(a['x']+a['w'],b['x']+b['w'])-max(a['x'],b['x'])
    h=min(a['y']+a['h'],b['y']+b['h'])-max(a['y'],b['y'])
    if w<=0 or h<=0:return 0.0
    inter=w*h
    return inter/(a['w']*a['h']+b['w']*b['h']-inter)


def static_scene(visual, occupancy_off):
    """{id(record): set of object indexes tagged background_static}.

    visual: this packet's visual records. Bounded and cheap: each box is
    compared only with the newest STATIC_WINDOW distinct frames, at most
    STATIC_BOXES_PER_LABEL boxes per label per frame; nothing outside the
    packet is read. Needs STATIC_MIN_FRAMES distinct frames holding the label
    (a latest-only packet has one, so only the no-person rule applies)."""
    frames={}
    for r in sorted(visual,key=lambda r:(r['observed_at'],str(r['id']))):
        frames.setdefault(r['observed_at'],r)
    window=[]
    for t in sorted(frames)[-STATIC_WINDOW:]:
        labels={}
        for o in frames[t].get('objects',[]):
            if o.get('pixel_box') and len(labels.setdefault(o.get('label'),[]))<STATIC_BOXES_PER_LABEL:
                labels[o['label']].append(o['pixel_box'])
        window.append((t,labels))
    tagged={}
    for r in visual:
        objects=r.get('objects',[])
        no_person=not any(o.get('label')=='person' for o in objects) or occupancy_off.get(id(r),False)
        found=set()
        for index,obj in enumerate(objects):
            if obj.get('label')=='person':continue
            if no_person:
                found.add(index);continue
            box=obj.get('pixel_box')
            if not box:continue
            others=[labels[obj['label']] for t,labels in window if t!=r['observed_at'] and obj['label'] in labels]
            if len(others)+1>=STATIC_MIN_FRAMES and all(any(_iou(box,b)>=STATIC_IOU for b in frame) for frame in others):
                found.add(index)
        tagged[id(r)]=found
    return tagged


# Phone relations: a phone is small and held with the wrist below it, often
# near the face, so besides the person-scale bound it is also judged against
# the phone box expanded by one phone length (its own longer side).
PHONE_LABELS=('cell phone','phone')
RELATION_LABELS=('book','cell phone','phone','keyboard','mouse')
WRISTS=(9,10)
FACE=(0,1,2,3,4)
NEAR=.08
# A held phone's box covers the screen; the gripping wrist sits below it.
# Live 2026-09-29: wrist 62 px under a 29x52 px phone box (one length missed).
PHONE_MARGIN=1.5


def _gap(point, box, margin=0):
    x,y=point
    dx=max(box['x']-margin-x,0,x-box['x']-box['w']-margin)
    dy=max(box['y']-margin-y,0,y-box['y']-box['h']-margin)
    return math.hypot(dx,dy)


def relation_facts(objects, poses):
    """Heuristic image-plane relations for one observation, and the indexes
    of the objects they name. Never contact, attention or activity."""
    facts,related=[],set()
    for pose in poses:
        person=next((o for o in objects if o.get('instance_ref')==pose.get('instance_ref') and o.get('label')=='person'),None)
        box=person.get('pixel_box') if person else None
        if not box:continue
        scale=math.hypot(box['w'],box['h'])
        joints=[j for j in pose.get('joints',[]) if j['status']=='supported' and j['pixel'] is not None]
        for joint in joints:
            if joint['joint_id'] not in WRISTS:continue
            for index,obj in enumerate(objects):
                target=obj.get('pixel_box')
                if obj['label'] not in RELATION_LABELS or not target:continue
                distance=_gap(joint['pixel'],target)/scale
                basis='within_person_scale' if distance<=NEAR else None
                if (basis is None and obj['label'] in PHONE_LABELS
                        and _gap(joint['pixel'],target,PHONE_MARGIN*max(target['w'],target['h']))==0):
                    basis='within_phone_length'
                if basis:
                    related.add(index)
                    facts.append({'fact':joint['name']+'_near_'+obj['label'].replace(' ','_')+'_in_image',
                        'person_ref':pose['instance_ref'],'object_ref':obj.get('instance_ref'),
                        'quality':'heuristic_image_plane','physical_contact':'unknown','basis':basis,
                        'normalized_distance':round(distance,4)})
        face=[j for j in joints if j['joint_id'] in FACE]
        for index,obj in enumerate(objects):
            target=obj.get('pixel_box')
            if obj['label'] not in PHONE_LABELS or not target or not face:continue
            if any(_gap(j['pixel'],target,max(target['w'],target['h']))==0 for j in face):
                related.add(index)
                facts.append({'fact':'phone_near_face_in_image','person_ref':pose['instance_ref'],
                    'object_ref':obj.get('instance_ref'),'quality':'heuristic_image_plane',
                    'physical_contact':'unknown','basis':'within_phone_length'})
    return facts,related


# Head direction (rule head-direction-1): yaw and pitch buckets of one pose's
# head from its supported COCO17 face joints and shoulders. Head pose in the
# image plane, never eye gaze, attention or identity; only the words leave.
HEAD_RULE='head-direction-1'
HEAD_YAW=('toward_image_left','toward_image_right','forward','unknown')
HEAD_PITCH=('down','level','unknown')
HEAD_SUPPORT=('both_ears','one_ear','eyes_only','insufficient')
HEAD_TOWARD=('monitor_side','lap_or_hands','elsewhere','unknown')
# Site config monitor_side -> the yaw bucket that points at the monitor.
MONITOR_SIDES={'image_left':'toward_image_left','image_right':'toward_image_right'}
NOSE,EYES,EARS,SHOULDERS=0,(1,2),(3,4),(5,6)
# Calibrated on owner session 0dc34636 (both arms, 2026-09-29), in units of
# the inter-shoulder distance:
# - A turned head hides its far ear; RTMPose then places that ear on the near
#   one (0.00-0.06 apart at the PC), while two visible ears sit 0.45-0.55
#   apart. Ears under EAR_MERGED apart are one ear.
# - Nose minus ear reference (ear midpoint, or the one ear): reading 0.12
#   median (max 0.15), phone 0.16 (max 0.20), computer one-ear 0.54 (p10 0.53)
#   and computer both-ears up to 0.27. YAW_TURNED sits in the 0.27-0.53 gap.
# - Nose height above the shoulder line: phone 0.34 median (p90 0.36, head
#   bowed), reading 0.47 (p10 0.45), computer 0.42 (p10 0.40). Nose offsets
#   from the ear or eye line did not separate the windows (0.25-0.31 median).
EAR_MERGED=.1
YAW_TURNED=.4
PITCH_DOWN=.4


def _point(joint):
    if not isinstance(joint,dict):return None
    pixel=joint.get('pixel')
    if (joint.get('status')!='supported' or not isinstance(pixel,(list,tuple)) or len(pixel)!=2
            or not all(finite(v) for v in pixel)):
        return None
    return float(pixel[0]),float(pixel[1])


def head_measures(pose):
    """(support, yaw, pitch) raw measures, local only (never sent): yaw is
    (nose x - ear reference x) / shoulder width, positive toward image right;
    pitch is (shoulder-line y - nose y) / shoulder width. None when missing."""
    joints={}
    for joint in (pose or {}).get('joints') or []:
        point=_point(joint)
        if point is not None and type(joint.get('joint_id')) is int:joints[joint['joint_id']]=point
    if NOSE not in joints or not all(s in joints for s in SHOULDERS):
        return 'insufficient',None,None
    (lx,ly),(rx,ry)=(joints[s] for s in SHOULDERS)
    scale=math.hypot(lx-rx,ly-ry)
    if not scale>0:
        return 'insufficient',None,None
    nx,ny=joints[NOSE]
    pitch=((ly+ry)/2-ny)/scale
    ears=[joints[e] for e in EARS if e in joints]
    if len(ears)==2:
        support='one_ear' if abs(ears[0][0]-ears[1][0])<EAR_MERGED*scale else 'both_ears'
        return support,(nx-(ears[0][0]+ears[1][0])/2)/scale,pitch
    if len(ears)==1:
        return 'one_ear',(nx-ears[0][0])/scale,pitch
    if all(e in joints for e in EYES):
        # Eyes sit near the nose in depth: their offset does not separate a
        # turned head (0.07-0.09) from a frontal one (0.03-0.06), so yaw is unknown.
        return 'eyes_only',None,pitch
    return 'insufficient',None,None


def head_direction(pose):
    """{'yaw', 'pitch', 'support'} words for one pose (head-direction-1)."""
    support,yaw,pitch=head_measures(pose)
    return {'yaw':'unknown' if yaw is None else 'toward_image_right' if yaw>=YAW_TURNED
                  else 'toward_image_left' if yaw<=-YAW_TURNED else 'forward',
            'pitch':'unknown' if pitch is None else 'down' if pitch<PITCH_DOWN else 'level',
            'support':support}


def head_toward(head, monitor_side):
    """The semantic word for a head block, or None without a site monitor_side.
    Toward the monitor side wins (monitor and keyboard sit there); else a bowed
    head is lap_or_hands; else a measured yaw is elsewhere."""
    if not isinstance(monitor_side,str) or monitor_side not in MONITOR_SIDES:
        return None
    if head['yaw']==MONITOR_SIDES[monitor_side]:return 'monitor_side'
    if head['pitch']=='down':return 'lap_or_hands'
    return 'elsewhere' if head['yaw']!='unknown' else 'unknown'


# Desk-seat position (rule desk-seat-1): a coarse word for where the one person
# in view sits, from the detector's person box in image coordinates and a
# private per-site region (the JEV provisioning file's desk_seat). Measured on
# the den (640x360; owner sessions 0dc34636, 8824f874, 30b6f5e5, 2026-09-30):
# seated at the PC the owner is ~0.5-0.7 m from the camera at the right image
# edge (box right edge 639-640 px, width 264-339 px, top 41-90 px); standing
# or walking at that edge the box top is 0-24 px (head at the frame top) and a
# passing person's box is 85-172 px wide; the armchair (reading, phone) box
# ends at 366-524 px. Only the word leaves; the box and the region stay local.
POSITION_RULE='desk-seat-1'
POSITIONS=('desk_seat','elsewhere_in_room','unknown')
DESK_SEAT_EDGES=('image_left','image_right')
# key -> (low, high) for the region's fractions of the image size.
DESK_SEAT_BOUNDS={'edge_margin_frac':(0.0,0.25),'min_width_frac':(0.05,1.0),'min_top_frac':(0.0,0.9)}
DESK_SEAT_KEYS=frozenset(('edge',*DESK_SEAT_BOUNDS))


def desk_seat_config(value):
    """A validated copy of a site desk_seat region, else ValueError:
    {'edge': 'image_left'|'image_right', 'edge_margin_frac',
    'min_width_frac', 'min_top_frac'} with each fraction inside its bound."""
    if (not isinstance(value,dict) or set(value)!=DESK_SEAT_KEYS or value['edge'] not in DESK_SEAT_EDGES
            or any(type(value[k]) not in (int,float) or not finite(value[k]) or not lo<=value[k]<=hi
                   for k,(lo,hi) in DESK_SEAT_BOUNDS.items())):
        raise ValueError('registered desk_seat region required')
    return {'edge':value['edge'],**{k:float(value[k]) for k in DESK_SEAT_BOUNDS}}


def frame_position(record, desk_seat):
    """desk-seat-1 for one projected visual record: desk_seat when its one
    person's box touches the region's image edge (within edge_margin_frac of
    the width), is at least min_width_frac of the width wide (near the camera)
    and its top is at least min_top_frac of the height below the image top
    (seated, not standing); elsewhere_in_room for any other one-person box;
    unknown with no person, several people, no box or no image size. Inputs
    are only the RGB detector box and the verified RGB preview frame size
    (640x360 on the den; persisted_evidence binds it): never depth,
    calibration or a capture release's content id, so a capture-side
    calibration change leaves the word unchanged."""
    region=desk_seat_config(desk_seat)
    quality=record.get('quality') or {}
    width,height=quality.get('image_width'),quality.get('image_height')
    people=[o for o in record.get('objects') or [] if o.get('label')=='person']
    box=people[0].get('pixel_box') if len(people)==1 else None
    if (not isinstance(box,dict) or not all(finite(box.get(k)) for k in 'xywh')
            or not finite(width) or not finite(height) or width<=0 or height<=0):
        return 'unknown'
    margin=region['edge_margin_frac']*width
    touches=(box['x']+box['w']>=width-margin if region['edge']=='image_right' else box['x']<=margin)
    if touches and box['w']>=region['min_width_frac']*width and box['y']>=region['min_top_frac']*height:
        return 'desk_seat'
    return 'elsewhere_in_room'


def frame_head(record, monitor_side=None):
    """The head block of one visual record: head_direction of its one pose
    (insufficient with no pose, several poses or several people), plus
    head_toward when the site configures monitor_side."""
    poses=record.get('pose_instances') or []
    people=sum(1 for o in record.get('objects') or [] if o.get('label')=='person')
    head=(head_direction(poses[0]) if len(poses)==1 and people<=1
          else {'yaw':'unknown','pitch':'unknown','support':'insufficient'})
    toward=head_toward(head,monitor_side)
    if toward is not None:
        head['head_toward']=toward
    return head



def semantic_state(records, cutoff, context=None, monitor_side=None):
    """monitor_side: the site's optional monitor side ('image_left' or
    'image_right'); with it each posed observation's head block carries
    head_toward, and head_toward monitor_side is a camera cue at the PC."""
    if monitor_side is not None and (not isinstance(monitor_side,str) or monitor_side not in MONITOR_SIDES):
        raise ValueError('registered monitor_side required')
    observations=[]
    captions=[]
    scenes=[]
    temporal=[]
    # Occupancy off at each visual record: the newest person_present at or
    # before it in this packet (HA records carry it, never the frames).
    occupancy_off,present={},None
    for r in sorted((r for r in records if finite(r.get('observed_at'))),
                    key=lambda r:(r['observed_at'],r.get('source_kind')!='ha.observation')):
        if isinstance(r.get('ha'),dict) and 'person_present' in r['ha']:
            present=r['ha']['person_present']
        if r.get('objects'):
            occupancy_off[id(r)]=present=='off'
    # Keyed by record object: ids are not assumed unique across sources.
    visual=[r for r in records if r.get('source_kind')!='ha.observation' and (r.get('objects') or r.get('pose_instances'))]
    framed=[r for r in visual if finite(r.get('observed_at')) and finite(r.get('valid_until'))]
    relations={id(r):relation_facts(r.get('objects',[]),r.get('pose_instances',[])) for r in framed}
    static={key:tags-relations[key][1] for key,tags in static_scene(framed,occupancy_off).items()}
    for r in records:
        observed=r.get('observed_at');expiry=r.get('valid_until')
        if not finite(observed) or not finite(expiry):continue
        if r.get('source_kind')=='caption.receipt':
            if isinstance(r.get('historical_caption'),dict):captions.append(r)
            continue
        if r.get('source_kind')=='qwen.observation':
            if isinstance(r.get('qwen_scene'),dict):scenes.append(r)
            continue
        if r.get('source_kind')=='vjepa.observation':
            if isinstance(r.get('vjepa_scene'),dict):temporal.append(r)
            continue
        objects=r.get('objects',[])
        facts=relations[id(r)][0] if id(r) in relations else []
        background=static.get(id(r),set())
        cues={o['label'] for i,o in enumerate(objects) if i not in background}
        observations.append({'evidence_id':r['id'],'age_seconds':round(max(0,cutoff-observed),3),
            'freshness':'fresh' if expiry>cutoff else 'historical',
            'person_count':sum(o['label']=='person' for o in objects),
            'detected_objects':sorted(cues),
            'background_objects':sorted({objects[i]['label'] for i in background}-cues),
            'facts':facts[:64],'device_state':r.get('ha',{}),'room_relations':r.get('relations',[]),
            'camera_derived_device_facts':r.get('quality',{}).get('camera_derived_ha_facts',[]),
            'correlated_with':r.get('quality',{}).get('correlated_with'),
            'depth':r.get('quality',{}).get('metric_depth','not_supplied'),
            'spatial_tracks':[{'track_ref':t['track_ref'],'position_uncertainty_m':t['uncertainty_m'],'method':t.get('method')} for t in r.get('tracks',[])],
            'tracking':r.get('quality',{}).get('tracking','not_supplied'),
            'attention':'not_measured','book_open_or_closed':'not_measured'})
        if r.get('pose_instances'):
            observations[-1]['head']=frame_head(r,monitor_side)
    latest=max(visual,key=lambda r:r['observed_at']) if visual else None
    caption=max(captions,key=lambda r:(r['observed_at'],r['id'])) if captions else None
    scene=max(scenes,key=lambda r:(r['observed_at'],r['id'])) if scenes else None
    projected=scene['qwen_scene'] if scene else None
    current=projected.get('latest') if projected and isinstance(projected.get('latest'),dict) else None
    qwen=None if scene is None else {'evidence_id':scene['id'],'interpretation':'model_interpretation_of_camera',
        'projection':projected,'calibration':'unvalidated','independent_confirmation':False,
        'correlated_with':scene.get('quality',{}).get('correlated_with'),
        'latest_facts':current.get('facts') if current and current.get('state')=='current' else None}
    methods={t.get('method') for r in records for t in r.get('tracks',[])}
    result={'version':VERSION,'observations':observations,
        'latest_visual_evidence_id':latest['id'] if latest else None,
        'historical_caption':None if caption is None else {'evidence_id':caption['id'],
            'activity':caption['historical_caption'].get('activity','unavailable'),
            'interpretation':'historical_model_interpretation','age_seconds':round(max(0,cutoff-caption['observed_at']),3),
            'freshness':'within_validity' if caption['valid_until']>cutoff else 'expired',
            'correlated_with':caption.get('quality',{}).get('correlated_with'),'independent_confirmation':False},
        'qwen_scene':qwen,
        'corroboration':corroboration(observations,qwen,latest['id'] if latest else None),
        'static_scene_objects':{'rule':STATIC_RULE,'basis':'this_packet_visual_observations',
            'stationarity':'compared' if len({r['observed_at'] for r in framed})>=STATIC_MIN_FRAMES else 'too_few_frames',
            'background_static':next((o['background_objects'] for o in observations
                                      if latest and o['evidence_id']==latest['id']),[])},
        'source_coverage':{'ha':'supplied' if any(r.get('ha') for r in records) else 'not_supplied',
            'room':'supplied' if any(r.get('relations') for r in records) else 'not_supplied',
            'pose':'supplied' if any(r.get('pose_instances') for r in records) else 'not_supplied',
            'depth':'supplied' if 'stereo_surface' in methods else 'not_supplied',
            'rgb_floor_position':'supplied' if 'rgb_floor_contact' in methods else 'not_supplied',
            'tracking':'supplied' if any(r.get('tracks') for r in records) else 'not_supplied',
            'qwen_caption':'supplied' if caption else 'not_supplied',
            'qwen_scene':'supplied' if qwen else 'not_supplied'},
        'limitations':'Image-plane nearness is a heuristic, not depth, contact, attention or activity. RGB floor-contact positions are estimates, not measured depth. Camera-derived device facts share the visual source named by correlated_with and are not independent confirmation. A historical caption is an earlier model interpretation of the same camera, not an observation or independent confirmation, and never makes evidence current. The qwen_scene block is an unvalidated model interpretation of the same camera frames the specialists saw: it is correlated with them, never independent confirmation, never makes evidence current, and its history summary describes earlier interpretations, not observed durations. corroboration counts signals consistent with an activity; only its independent signals come from outside the camera. PC input shows the PC was used, not who used it or whether the person in view is at it. background_objects are static scene objects (stationary across this packet or seen with no person present): never activity cues, and a detected tv or monitor is never a screen the person is using. within_phone_length and phone_near_face relations are heuristics against the phone box expanded by one and a half of its own length (wrist) or its own length (face). An observation head block (rule head-direction-1) buckets one pose\'s head direction from face keypoints: head pose in the image, not eye gaze or attention; head_toward (present only with a site monitor side) maps it to monitor_side, lap_or_hands or elsewhere, and monitor_side lets recent PC input count for computer_work. Frame-local references do not establish track continuity. Missing evidence is unknown, not a negative observation.'}
    if temporal:
        # Only when a V-JEPA record is present, so every other packet is unchanged.
        record=max(temporal,key=lambda r:(r['observed_at'],r['id']))
        projected=record['vjepa_scene']
        latest=projected.get('latest') if isinstance(projected.get('latest'),dict) else None
        result['vjepa_scene']={'evidence_id':record['id'],'interpretation':'frozen_probe_buckets_of_camera_clips',
            'projection':projected,'calibration':'unvalidated','independent_confirmation':False,
            'correlated_with':record.get('quality',{}).get('correlated_with'),
            'latest_facts':latest.get('facts') if latest and latest.get('state')=='current' else None}
        result['source_coverage']['vjepa_scene']='supplied'
        result['limitations']+=(' The vjepa_scene block holds uncalibrated bucket words from frozen V-JEPA 2.1 probes'
            ' (posture, a reading signal, zero-shot phone/computer/reading signals, motion and scene change) trained on'
            ' other rooms and cameras over the same camera clips the specialists saw: correlated with them, never'
            ' independent confirmation, never makes evidence current, and its history summarizes earlier clips.')
    if context is not None:
        # The shared block, identical for both arms except a withheld lever family.
        result['context']=context
        result['limitations']+=' context.coverage values other than supplied are unknown, not negative; context.ambient facts are priors, not activity observations.'
    return result
