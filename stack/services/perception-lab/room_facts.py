"""Placement-agnostic room facts (rule room-facts-1): words about the one
person in view, computed locally from the detector's person box and COCO17
keypoints, the camera's stereo depth and the saved den site map.

Facts describe the ROOM, never an activity or a camera placement:

- position: the room zone the person is in (its category word: desk_chair,
  armchair, sofa, bed, floor, doorway, standing_area), from the 3D torso
  (depth at the shoulder and hip keypoints) inside the map's seat volumes and
  floor zones; elsewhere_in_room outside every zone; unknown without depth.
- looking_at: the map target category the head points at (head3d's cone
  against the map's anchors), lap_or_hands, elsewhere or unknown, with its
  stability. Head pose, never eye gaze.
- hands_near: target and object categories within HAND_NEAR_M of either wrist
  in 3D: the map's static targets (keyboard, desk surface, ...) and the
  detector's objects lifted to 3D (book, phone, cup, ...).
- posture: sitting, standing or lying from 3D shoulder, hip and knee heights
  above the floor (the map frame is z up, floor at z = 0).
- motion: still, small or moving from the 3D torso displacement over ~3 s.

Only category WORDS from the fixed vocabularies below ever leave the device:
no coordinate, distance, zone or target name, label or identifier. A camera
move only needs a re-registered site map (room_mapper register + save); the
facts, their words and every question stay the same.

The controller stage (RoomFactsStage) samples the camera owner's /frame.npz at
2 Hz for the recording, identical for both arms (a shared baseline, never an
experiment lever), pairs each arm's perception.output with the bundle of the
nearest RGB device timestamp and appends one 'room.facts' ledger event per
paired output. JEV's den-facts-8 wire reads the latest one per arm as shared
context (facts_context); the egress policy re-derives it from the ledger.
Without a provisioned site map nothing here runs.
"""
import io
import json
import math
import threading
import time

SITE_MAP_SCHEMA = 'den-site-map-1'
MAP_FRAME = 'z_up_metric_floor0'
REGISTRATION_METHODS = ('mesh_icp', 'floor_only')

# Vocabularies and the word projection live in room_words (no heavy imports),
# re-exported here for the stage and its callers.
from room_words import (PEOPLE, POSITIONS, POSITION_METHODS, LOOK_WORDS, STABILITY, HAND_WORDS,  # noqa: F401
                        POSTURES, MOTIONS, ORDERS, SPANS, HISTORY_WINDOW, _WORD_FIELDS, _SEGMENT_FIELDS,
                        projection, RECORD_SCHEMA, MAX_SEGMENTS, RULE, EVENT_KIND)

# --- Category tables: normalized map category/label -> word (data, not code) --
# Map words are normalized (lower case, '_' for spaces, a numbered suffix such
# as window_2 dropped) and then looked up; anything unlisted never leaves.
# Seats and places a person can be IN (seat targets and floor zones).
ZONE_WORDS = {'desk_chair': 'desk_chair', 'office_chair': 'desk_chair', 'reading_chair': 'armchair',
              'armchair': 'armchair', 'sofa': 'sofa', 'couch': 'sofa', 'bed': 'bed', 'floor': 'floor', 'rug': 'floor',
              'door': 'doorway', 'doorway': 'doorway', 'standing_area': 'standing_area'}
# Floor zones only (never a target volume): open areas.
AREA_ONLY = frozenset(('floor', 'standing_area'))
# Zone words that are seats (never look targets).
SEAT_WORDS = frozenset(('desk_chair', 'armchair', 'sofa', 'bed'))
# What the head can point at. Seats are not look targets: a seat's box is
# large and near the person, so it would swallow every downward cone.
LOOK_TARGETS = {'monitor': 'monitor', 'screen': 'monitor', 'tv': 'tv', 'television': 'tv', 'window': 'window',
                'bookshelf': 'bookshelf', 'painting': 'painting', 'picture': 'painting', 'door': 'door',
                'fireplace': 'fireplace', 'desk': 'desk_surface', 'keyboard_area': 'keyboard', 'keyboard': 'keyboard',
                'table': 'table'}
# Static map targets a hand can be near (seats are left out: an armrest is
# always near a seated hand and says nothing).
HAND_TARGETS = {'keyboard_area': 'keyboard', 'keyboard': 'keyboard', 'mouse': 'mouse', 'desk': 'desk_surface',
                'table': 'table_surface', 'bookshelf': 'bookshelf'}
# Detector object labels (jev_evidence.OBJECTS) lifted to 3D near a wrist.
HAND_OBJECTS = {'book': 'book', 'cell phone': 'phone', 'phone': 'phone', 'cup': 'cup', 'bottle': 'bottle',
                'remote': 'remote', 'keyboard': 'keyboard', 'mouse': 'mouse', 'laptop': 'laptop', 'bowl': 'bowl'}

# --- Thresholds (metres, seconds) ---------------------------------------------
HAND_NEAR_M = 0.15            # a wrist this close to a target volume or object surface
SEAT_MARGIN_M = 0.15          # torso footprint slack around a seat volume
SEAT_ABOVE_TOP_M = 0.45       # torso may sit this far above a seat volume's top
BODY_SPREAD_M = 0.8           # a body joint further from the torso is background or an occluder
WRIST_REACH_M = 1.0           # a wrist further from the shoulders is a depth leak
JOINT_PATCH_PX = 5.0
MOTION_WINDOW_S = 3.5         # ~3 s of torso samples (the detector runs at ~1 Hz)
MOTION_MIN_SPAN_S = 1.5
STILL_M, SMALL_M = 0.10, 0.30  # stereo noise at 1-3 m is several cm: 'still' allows it
HISTORY_SECONDS = 60
HISTORY_LIMIT = 128
MAX_TARGETS = 128
MAX_ZONES = 32
# Live stage.
SAMPLE_INTERVAL_S = 0.5       # 2 Hz NPZ sampling (the camera owner compresses at up to 5 Hz)
PAIR_TOLERANCE_S = 0.30       # RGB device-time distance between a detection and its bundle
PENDING_S = 2.0               # a detection waits at most this long for its bundle
RING = 8                      # decoded bundles kept (~4 s at 2 Hz)
OUTPUTS_PER_TICK = 64
MOUNT_CHANGED_CHECKS = 5      # consecutive 'changed' mount checks before the stage stops
COCO = {'nose': 0, 'left_shoulder': 5, 'right_shoulder': 6, 'left_wrist': 9, 'right_wrist': 10, 'left_hip': 11,
        'right_hip': 12, 'left_knee': 13, 'right_knee': 14}


def _np():
    import numpy as np
    return np


def calibration_content_id(calibration):
    """The camera owner's calibration content digest without its per-capture
    session (the same digest as bundle_ingest.calibration_content_id and
    room_mapper's map registration)."""
    from reasoning import digest
    return digest(['oak-calibration-content-v1', {k: v for k, v in calibration.items() if k != 'session'}])


def _finite(values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


def normalize(text):
    """A map category or label as a lookup key (never sent)."""
    if not isinstance(text, str):
        return None
    key = text.strip().lower().replace(' ', '_').replace('-', '_')
    head, _, tail = key.rpartition('_')
    return head if head and tail.isdigit() else key


def category_word(values, table):
    """The table word of the first value that normalizes to a key, else None."""
    for value in values:
        key = normalize(value)
        if key in table:
            return table[key]
    return None


# --- Site map ----------------------------------------------------------------------

def _rigid(matrix):
    np = _np()
    m = np.asarray(matrix, dtype=np.float64)
    if m.shape != (4, 4) or not np.all(np.isfinite(m)) or np.abs(m[3]-[0, 0, 0, 1]).max() > 1e-9:
        raise ValueError('rigid 4x4 camera registration required')
    rotation = m[:3, :3]
    if np.abs(rotation.T @ rotation-np.eye(3)).max() > 1e-3 or abs(np.linalg.det(rotation)-1) > 1e-3:
        raise ValueError('rigid 4x4 camera registration required')
    return m


def _volume(target):
    """(centre, half extents or None, radius or None) of one map target."""
    centre = [float(v) for v in target['pos']]
    size = target.get('size_m')
    if target.get('shape') == 'box' and isinstance(size, list) and len(size) == 3:
        return centre, [float(v)/2 for v in size], None
    from head3d import anchor_radius
    return centre, None, anchor_radius({k: target[k] for k in ('radius_m', 'size_m') if k in target})


def load_site_map(document, *, site_id=None, camera_id=None):
    """The validated room model of a saved den site map (room_mapper save,
    schema den-site-map-1), else ValueError.

    The binding digest is checked by room_context.apartment_map; the camera
    registration must be rigid, into z_up_metric_floor0, from rectified-left
    depth, and carry the camera calibration content id and mount signature it
    was registered with. Labels and names are read only to look up category
    words; they are never kept."""
    from room_context import apartment_map
    if not isinstance(document, dict) or document.get('schema') != SITE_MAP_SCHEMA:
        raise ValueError('den site map required')
    model, binding = document.get('model'), document.get('binding')
    if not isinstance(model, dict) or not isinstance(binding, dict):
        raise ValueError('den site map required')
    room = apartment_map(model, binding)
    meta = model.get('meta') or {}
    camera = meta.get('camera_registration')
    if ((site_id is not None and (binding.get('site_id') != site_id or meta.get('site_id') != site_id))
            or (camera_id is not None and meta.get('camera_id') != camera_id)):
        raise ValueError('site map belongs to another camera')
    if (not isinstance(camera, dict) or camera.get('source_frame') != 'rectified_left'
            or camera.get('target_frame') != MAP_FRAME or camera.get('method') not in REGISTRATION_METHODS
            or camera.get('status') != 'fitted' or not isinstance(camera.get('calibration_content_id'), str)
            or not 1 <= len(camera['calibration_content_id']) <= 128 or not isinstance(camera.get('mount_signature'), dict)):
        raise ValueError('registered camera pose required')
    matrix = _rigid(camera['matrix'])
    signature = camera['mount_signature']
    if (not isinstance(signature.get('grid'), list) or len(signature['grid']) != 2
            or not isinstance(signature.get('cells'), list) or signature.get('shape') != [400, 640]):
        raise ValueError('registered camera mount signature required')
    if len(room['targets']) > MAX_TARGETS or len(room['zones']) > MAX_ZONES:
        raise ValueError('site map exceeds bounds')
    seats, areas, anchors, surfaces = [], [], [], []
    for target in room['targets']:
        names = (target.get('category'), target.get('label'), target.get('id'))
        centre, half, radius = _volume(target)
        zone = category_word(names, ZONE_WORDS)
        if zone is not None and normalize(target.get('category') or target.get('label')) not in AREA_ONLY:
            seats.append({'word': zone, 'centre': centre, 'half': half, 'radius': radius})
        hand = category_word(names, HAND_TARGETS)
        if hand is not None:
            surfaces.append({'word': hand, 'centre': centre, 'half': half, 'radius': radius})
        # The cone test sees every target but the seats (an unlisted wall
        # picture can still hide behind the monitor: other_object); its map
        # category is kept only as a lookup key.
        if zone in SEAT_WORDS:
            continue
        key = next((normalize(n) for n in names if normalize(n) in LOOK_TARGETS), 'other_object')
        anchor = {'category': key, 'pos': centre}
        anchor.update({k: target[k] for k in ('radius_m', 'size_m') if k in target})
        anchors.append(anchor)
    for zone in room['zones']:
        word = category_word((zone.get('label'), zone.get('id')), ZONE_WORDS)
        if word is not None:
            areas.append({'word': word, 'polygon': [[float(x), float(y)] for x, y in zone['polygon']]})
    from head3d import room_up_in_camera
    return {'site_id': binding['site_id'], 'map_id': binding['map_id'], 'revision': binding['revision'],
            'model_sha256': binding['model_sha256'], 'matrix': [[float(v) for v in row] for row in matrix],
            'up': room_up_in_camera(matrix), 'registration_method': camera['method'],
            'calibration_content_id': camera['calibration_content_id'], 'mount_signature': signature,
            'seats': seats, 'areas': areas, 'anchors': anchors, 'surfaces': surfaces}


# --- Geometry (room frame, metres) ---------------------------------------------------

def to_room(matrix, point):
    np = _np()
    m = np.asarray(matrix, dtype=np.float64)
    return m[:3, :3] @ np.asarray(point, dtype=np.float64)+m[:3, 3]


def volume_distance(point, volume):
    """Distance from a point to a box (half extents) or sphere volume; 0 inside."""
    np = _np()
    p, c = np.asarray(point, dtype=np.float64), np.asarray(volume['centre'], dtype=np.float64)
    if volume.get('half') is not None:
        return float(np.linalg.norm(np.maximum(np.abs(p-c)-np.asarray(volume['half']), 0.0)))
    return max(0.0, float(np.linalg.norm(p-c))-volume['radius'])


def _in_polygon(x, y, polygon):
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:]+polygon[:1]):
        if (y1 > y) != (y2 > y) and x < (x2-x1)*(y-y1)/(y2-y1)+x1:
            inside = not inside
    return inside


def _polygon_area(polygon):
    return abs(sum(x1*y2-x2*y1 for (x1, y1), (x2, y2) in zip(polygon, polygon[1:]+polygon[:1])))/2


def zone_word(torso, room):
    """position for a 3D torso point: the nearest seat volume it sits in
    (footprint within SEAT_MARGIN_M, not far above the seat's top), else the
    smallest floor zone containing it, else elsewhere_in_room."""
    np = _np()
    if torso is None:
        return 'unknown'
    p = np.asarray(torso, dtype=np.float64)
    best = None
    for seat in room['seats']:
        c = np.asarray(seat['centre'])
        if seat['half'] is not None:
            half = np.asarray(seat['half'])
            horizontal = float(np.linalg.norm(np.maximum(np.abs(p[:2]-c[:2])-half[:2], 0.0)))
            top = c[2]+half[2]
        else:
            horizontal = max(0.0, float(np.linalg.norm(p[:2]-c[:2]))-seat['radius'])
            top = c[2]+seat['radius']
        if horizontal <= SEAT_MARGIN_M and p[2] <= top+SEAT_ABOVE_TOP_M:
            key = (horizontal, float(np.linalg.norm(p[:2]-c[:2])))
            if best is None or key < best[0]:
                best = (key, seat['word'])
    if best is not None:
        return best[1]
    inside = [a for a in room['areas'] if _in_polygon(p[0], p[1], a['polygon'])]
    if inside:
        return min(inside, key=lambda a: _polygon_area(a['polygon']))['word']
    return 'elsewhere_in_room'


def posture_word(body):
    """sitting / standing / lying / unknown from room-frame joints
    {'shoulder', 'hip', 'knee', 'head'} (z up, floor at 0; any may be absent).
    Adult seated heights: hip 0.3-0.7 m, shoulder 0.85-1.25 m, head 1.1-1.4 m;
    standing: hip >= 0.75 m, shoulder >= 1.3 m, head >= 1.5 m."""
    np = _np()
    shoulder, hip, knee, head = (body.get(k) for k in ('shoulder', 'hip', 'knee', 'head'))
    if shoulder is not None and hip is not None:
        torso = np.asarray(shoulder)-np.asarray(hip)
        length = float(np.linalg.norm(torso))
        if length >= 0.2:
            upright = torso[2]/length
            if upright < 0.5 and shoulder[2] < 1.0:
                return 'lying'
            if upright >= 0.5:
                if knee is not None:
                    thigh = hip[2]-knee[2]
                    if thigh >= 0.25 and hip[2] >= 0.6:
                        return 'standing'
                    if abs(thigh) < 0.2 and hip[2] < 0.8:
                        return 'sitting'
                if hip[2] >= 0.75 and shoulder[2] >= 1.25:
                    return 'standing'
                if 0.2 <= hip[2] < 0.7 and 0.7 <= shoulder[2] < 1.3:
                    return 'sitting'
    if shoulder is not None and hip is None:
        if shoulder[2] >= 1.3:
            return 'standing'
        if 0.85 <= shoulder[2] <= 1.2:
            return 'sitting'
    if head is not None and shoulder is None and hip is None:
        if head[2] >= 1.5:
            return 'standing'
        if 1.05 <= head[2] <= 1.4:
            return 'sitting'
    return 'unknown'


def motion_word(samples, t):
    """still / small / moving from (time, 3D torso) samples: the largest
    displacement of the current torso from any sample in the last
    MOTION_WINDOW_S; unknown until the samples span MOTION_MIN_SPAN_S."""
    np = _np()
    window = [(s, p) for s, p in samples if t-MOTION_WINDOW_S <= s <= t]
    if len(window) < 2 or window[-1][0] != t or t-window[0][0] < MOTION_MIN_SPAN_S:
        return 'unknown'
    current = np.asarray(window[-1][1])
    moved = max(float(np.linalg.norm(current-np.asarray(p))) for _, p in window[:-1])
    return 'still' if moved < STILL_M else 'small' if moved < SMALL_M else 'moving'


def look_word(target):
    """The wire word of head3d.look_target's result (its anchor category key)."""
    word = target.get('looking_at')
    if word in ('lap_or_hands', 'elsewhere', 'unknown'):
        return word
    return LOOK_TARGETS.get(word, 'other_object')


# --- One frame -------------------------------------------------------------------------

UNKNOWN = {'position': 'unknown', 'looking_at': 'unknown', 'looking_at_stability': 'unknown', 'hands_near': [],
           'posture': 'unknown', 'motion': 'unknown'}


class ArmTrack:
    """Per-arm temporal state: head stability, torso samples, the storyline."""

    def __init__(self):
        from head3d import Stability
        self.stability = Stability()
        self.torso = []
        self.history = []
        self.last_t = None

    def accept(self, t):
        """False for an out-of-order frame (it is skipped, never reorders state)."""
        if self.last_t is not None and t < self.last_t:
            return False
        self.last_t = t
        return True


def _box(obj):
    box = obj.get('pixel_box')
    if not isinstance(box, dict) or not _finite([box.get(k) for k in ('x', 'y', 'w', 'h')]):
        return None
    return (box['x'], box['y'], box['x']+box['w'], box['y']+box['h'])


def _person_pose(record):
    """(person box, pose) of a record's one person, or (None, None)."""
    people = [o for o in record.get('objects') or [] if o.get('label') == 'person']
    poses = record.get('pose_instances') or []
    if len(people) != 1:
        return None, None
    pose = next((p for p in poses if p.get('instance_ref') is not None
                 and p.get('instance_ref') == people[0].get('instance_ref')), None)
    if pose is None and len(poses) == 1:
        pose = poses[0]
    return _box(people[0]), pose


def _people_word(record):
    count = sum(1 for o in record.get('objects') or [] if o.get('label') == 'person')
    return 'none' if count == 0 else 'one' if count == 1 else 'multiple'


def measure(record, depth_mm, model, room):
    """Local 3D measures of one projected perception.output record (never
    sent): torso and body joints in the room frame, the head direction, and
    the hands_near words. None without one person, a pose or a person box."""
    np = _np()
    import head3d
    box, pose = _person_pose(record)
    if box is None or pose is None:
        return None
    joints = head3d.joints_of(pose)
    candidates = [o for o in record.get('objects') or [] if o.get('label') in HAND_OBJECTS and _box(o) is not None]
    x0, y0, x1, y1 = box
    grow_x, grow_y = 0.3*(x1-x0), 0.3*(y1-y0)
    near = [o for o in candidates if not (_box(o)[2] < x0-grow_x or _box(o)[0] > x1+grow_x
                                          or _box(o)[3] < y0-grow_y or _box(o)[1] > y1+grow_y)]
    roi = [min([x0]+[_box(o)[0] for o in near]), min([y0]+[_box(o)[1] for o in near]),
           max([x1]+[_box(o)[2] for o in near]), max([y1]+[_box(o)[3] for o in near])]
    lifter = head3d.DepthLifter(depth_mm, model, rgb_roi=roi)
    lifted = {}
    for name in ('left_shoulder', 'right_shoulder', 'left_hip', 'right_hip', 'left_knee', 'right_knee', 'nose',
                 'left_wrist', 'right_wrist'):
        joint = COCO[name]
        if joint in joints:
            sample = lifter.lift(*joints[joint], radius_px=JOINT_PATCH_PX)
            if sample['status'] == 'measured':
                lifted[name] = np.asarray(sample['xyz'])
    torso_points = [lifted[n] for n in ('left_shoulder', 'right_shoulder', 'left_hip', 'right_hip') if n in lifted]
    if not torso_points:
        surface = lifter.lift_box(box, min_support=20, layer='foreground')
        torso_points = [np.asarray(surface['xyz'])] if surface['status'] == 'measured' else []
        reference = torso_points[0] if torso_points else None
    else:
        reference = np.median(np.array(torso_points), axis=0)
        # A joint far from the torso is background leaking into its patch or
        # an occluder in front of it (the desk edge over a hip): not the body.
        for name in list(lifted):
            limit = WRIST_REACH_M if name.endswith('wrist') else BODY_SPREAD_M
            if float(np.linalg.norm(lifted[name]-reference)) > limit:
                del lifted[name]
        kept = [lifted[n] for n in ('left_shoulder', 'right_shoulder', 'left_hip', 'right_hip') if n in lifted]
        reference = np.median(np.array(kept), axis=0) if kept else reference
    matrix = room['matrix']
    torso = None if reference is None else to_room(matrix, reference)

    def mean(*names):
        points = [lifted[n] for n in names if n in lifted]
        return to_room(matrix, np.mean(points, axis=0)) if points else None
    body = {'shoulder': mean('left_shoulder', 'right_shoulder'), 'hip': mean('left_hip', 'right_hip'),
            'knee': mean('left_knee', 'right_knee'), 'head': mean('nose')}
    head = head3d.estimate_head(pose, lifter, up=room['up'])
    look = head3d.to_room(head, matrix) if head['status'] == 'estimated' else None
    target = head3d.look_target(look, room['anchors']) if look is not None else {'looking_at': 'unknown'}
    if body['head'] is None and look is not None:
        body['head'] = np.asarray(look['head_m'])
    hands = set()
    wrists = [lifted[n] for n in ('left_wrist', 'right_wrist') if n in lifted]
    for wrist in wrists:
        place = to_room(matrix, wrist)
        for surface in room['surfaces']:
            if volume_distance(place, surface) <= HAND_NEAR_M:
                hands.add(surface['word'])
    if wrists:
        for obj in near:
            points = lifter.select(box=_box(obj))
            if len(points) < 10:
                continue
            points = head3d.foreground_layer(points)
            for wrist in wrists:
                if float(np.min(np.linalg.norm(points-wrist, axis=1))) <= HAND_NEAR_M:
                    hands.add(HAND_OBJECTS[obj['label']])
    return {'torso': torso, 'body': body, 'look': look, 'target': target, 'hands': sorted(hands),
            'head_method': head.get('method')}


def segments(history, t):
    """The code-built position/looking_at storyline of one arm, oldest first:
    a new (position, looking_at) opens a segment only when two consecutive
    frames carry it (a one-frame blip folds into its segment)."""
    from qwen_scene import span_word
    entries = [e for e in history if t-HISTORY_SECONDS <= e[0] <= t]
    runs = []
    for index, (at, position, looking) in enumerate(entries):
        state = (position, looking)
        if not runs:
            runs.append({'state': state, 'start': at})
            continue
        if state == runs[-1]['state']:
            continue
        following = entries[index+1] if index+1 < len(entries) else None
        if following is not None and (following[1], following[2]) == state:
            runs.append({'state': state, 'start': at})
    words = []
    for index, run in enumerate(runs):
        end = runs[index+1]['start'] if index+1 < len(runs) else t
        words.append({'position': run['state'][0], 'looking_at': run['state'][1], 'span': span_word(end-run['start'])})
    kept = words[-MAX_SEGMENTS:]
    for word, order in zip(kept, ORDERS[-len(kept):]):
        word['order'] = order
    return {'window': HISTORY_WINDOW, 'segments': kept, 'earlier_segments_omitted': len(words) > len(kept)}


def frame_facts(record, depth_mm, model, room, track, t):
    """The room.facts words of one projected perception.output record at its
    observation time t, updating the arm's track. Words only."""
    people = _people_word(record)
    words = dict(UNKNOWN, hands_near=[])
    measured = measure(record, depth_mm, model, room) if people == 'one' else None
    in_order = track.accept(t)
    if measured is not None:
        words.update(position=zone_word(measured['torso'], room), posture=posture_word(measured['body']),
                     looking_at=look_word(measured['target']), hands_near=measured['hands'])
        if in_order and measured['torso'] is not None:
            track.torso = [s for s in track.torso if t-s[0] <= MOTION_WINDOW_S]+[(t, [float(v) for v in measured['torso']])]
            words['motion'] = motion_word(track.torso, t)
    if in_order:
        forward = measured['look']['forward'] if measured and measured['look'] else None
        words['looking_at_stability'] = track.stability.update(t, words['looking_at'], forward)
        track.history = [e for e in track.history if t-e[0] <= HISTORY_SECONDS][-(HISTORY_LIMIT-1):]
        track.history.append((t, words['position'], words['looking_at']))
    history = segments(track.history, t)
    return {'schema': RECORD_SCHEMA, 'people': people, **words, 'history': history}


# --- The record projection (allowlist walker) ------------------------------------------

# --- Ledger reads ------------------------------------------------------------------------

class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def facts_context(store, session, cutoff, *, generation, arms=('a', 'b')):
    """{arm: [record]}: the newest room.facts record per arm that is observed
    and available by the cutoff and still valid after it, as the projected
    observation record (persisted_evidence.observation_record) a shared
    context list carries. Strictly causal; at most one per arm."""
    from persisted_evidence import observation_record
    result = {}
    lock = getattr(store, 'lock', None) or _NoLock()
    for arm in arms:
        with lock:
            row = store.db.execute(
                """SELECT id,payload FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind=?
                AND json_extract(payload,'$.arm')=? AND json_extract(payload,'$.generation')=?
                AND json_extract(payload,'$.observed_at')<=? AND json_extract(payload,'$.available_at')<=?
                AND json_extract(payload,'$.valid_until')>?
                ORDER BY json_extract(payload,'$.observed_at') DESC,id DESC LIMIT 1""",
                (session['id'], EVENT_KIND, arm, generation, cutoff, cutoff, cutoff)).fetchone()
        if row is None:
            continue
        record = observation_record(row[0], EVENT_KIND, json.loads(row[1]), session)
        if record is not None:
            result[arm] = [record]
    return result


def decode_bundle(data):
    """(depth uint16 400x640, metadata) of one camera NPZ. Only the depth and
    metadata members are decompressed; left, right and RGB are never read."""
    np = _np()
    with np.load(io.BytesIO(data), allow_pickle=False) as archive:
        if set(archive.files) != {'depth_mm', 'left', 'right', 'rgb_nv12', 'metadata_json'}:
            raise ValueError('unexpected camera bundle members')
        depth = archive['depth_mm']
        meta = json.loads(archive['metadata_json'].tobytes())
    if (depth.shape != (400, 640) or depth.dtype != np.uint16 or not isinstance(meta, dict)
            or meta.get('depth_alignment') != 'rectified_left' or meta.get('depth_units') != 'millimetres'
            or meta.get('invalid_depth') != 0):
        raise ValueError('unexpected camera bundle shape or semantics')
    return depth, meta


def check_config(value):
    """The live config's optional 'room_facts' block: {'enabled': bool,
    'site_map_file': absolute path}."""
    from pathlib import PurePosixPath
    if (not isinstance(value, dict) or set(value) != {'enabled', 'site_map_file'} or type(value['enabled']) is not bool
            or not isinstance(value['site_map_file'], str) or not PurePosixPath(value['site_map_file']).is_absolute()):
        raise ValueError('Exact room facts provisioning required')
    return dict(value)


# --- Controller stage ----------------------------------------------------------------------

class _Session:
    def __init__(self, session, cursor, generation):
        self.session, self.cursor, self.generation = session, cursor, generation
        self.epoch = self.calibration = None
        self.ring, self.pending, self.arms = [], [], {}
        self.mount, self.mount_changed = 'unverified', 0
        self.reason = None
        self.counts = {'bundles': 0, 'written': 0, 'no_depth_in_window': 0, 'rgb_frame_unavailable': 0,
                       'expired_before_write': 0, 'failed': 0}
        self.timing = {'decode_ms': None, 'facts_ms': None}


class RoomFactsStage:
    """Recording-owned room facts for one camera and one validated site map.

    One NPZ fetch every SAMPLE_INTERVAL_S per recording (a PreviewPump, which
    the runtime keeps with its other numerical-bundle pumps and stops with
    them), shared by both arms. Each tick decodes the bundle's depth, keeps a
    short ring, and turns the recording's new perception.output events into
    room.facts events. A calibration that differs from the one the map was
    registered with, or a camera that moved, stops the facts for that
    recording (a recorded reason; never a guess)."""

    def __init__(self, store, room, *, camera_id, client, clock=time.monotonic):
        if not isinstance(room, dict) or 'matrix' not in room:
            raise ValueError('validated site map required')
        self.store, self.room, self.camera_id, self.client, self.clock = store, room, camera_id, client, clock
        self.sessions = {}
        self.models = {}
        self.lock = threading.Lock()

    def pump(self, session, *, active, generation):
        """A PreviewPump for this recording; generation() is the active pair's
        generation (None before activation)."""
        from preview_client import PreviewPump
        sid = session['id']
        with self.store.lock:
            cursor = self.store.db.execute('SELECT coalesce(max(id),0) FROM events WHERE session_id=?', (sid,)).fetchone()[0]
        with self.lock:
            self.sessions[sid] = _Session(session, cursor, generation)
        return PreviewPump(lambda: (self.client._read('/frame.npz')[0], None), lambda data, _: self.ingest(sid, data),
                           active, interval=SAMPLE_INTERVAL_S)

    def status(self, sid):
        state = self.sessions.get(sid)
        if state is None:
            return {'state': 'not_started'}
        return {'state': state.reason or ('active' if state.mount != 'unverified' else 'mount_unverified'),
                'map_revision': self.room['revision'], **state.counts, **state.timing}

    def _model(self, meta, calibration):
        from depth_association import camera_model, eeprom_rgb_from_rectified_left
        frames = meta['frames']
        key = json.dumps([frames['depth']['intrinsics'], frames['rgb']['intrinsics'],
                          frames['rgb'].get('source_distortion_coefficients'), calibration_content_id(calibration)])
        if key not in self.models:
            reference = {'id': calibration_content_id(calibration), 'mount_revision': 'room-map-' + self.room['revision'],
                         'rgb_from_rectified_left': eeprom_rgb_from_rectified_left(calibration['eeprom']),
                         'reference_intrinsics': {'rectified_left': frames['depth']['intrinsics'],
                                                  'rgb': frames['rgb']['intrinsics']}}
            self.models.clear()
            self.models[key] = camera_model(meta, reference, measuring_reprojection=True)
        return self.models[key]

    def ingest(self, sid, data):
        """One sampled bundle: decode, bind, ring, then process new outputs."""
        state = self.sessions.get(sid)
        if state is None or state.reason is not None:
            return {'state': 'inactive'}
        started = time.perf_counter()
        depth, meta = decode_bundle(data)
        state.timing['decode_ms'] = round(1000*(time.perf_counter()-started), 2)
        epoch = meta.get('session')
        if epoch != state.epoch:
            raw, _ = self.client._read('/calibration')
            calibration = json.loads(raw)
            if not isinstance(calibration, dict) or calibration.get('session') != epoch:
                raise ValueError('camera restarted between reads')
            if calibration_content_id(calibration) != self.room['calibration_content_id']:
                state.reason = 'calibration_differs_from_map'
                return {'state': state.reason}
            state.epoch, state.calibration, state.ring = epoch, calibration, []
        if state.mount != 'verified':
            from spatial_validation import check_mount
            outcome = check_mount(self.room['mount_signature'], depth)['status']
            state.mount_changed = state.mount_changed+1 if outcome == 'changed' else 0
            if outcome == 'unchanged':
                state.mount = 'verified'
            elif state.mount_changed >= MOUNT_CHANGED_CHECKS:
                state.reason = 'camera_moved_since_map'
                return {'state': state.reason}
        stamp = ((meta.get('frames') or {}).get('rgb') or {}).get('device_timestamp_seconds')
        if not _finite([stamp]):
            raise ValueError('bundle RGB device timestamp required')
        state.ring = (state.ring+[{'stamp': float(stamp), 'epoch': epoch, 'depth': depth,
                                   'model': self._model(meta, state.calibration)}])[-RING:]
        state.counts['bundles'] += 1
        return self._process(sid, state)

    def _frame(self, sid, payload):
        """(RGB device seconds, capture epoch) of an output's own RGB frame."""
        ids = [m for m in payload.get('media_ids') or [] if isinstance(m, str)][:8]
        if not ids:
            return None
        with self.store.lock:
            rows = self.store.db.execute('SELECT metadata FROM media WHERE session_id=? AND id IN (%s)' % ','.join('?'*len(ids)),
                                         (sid, *ids)).fetchall()
        for (raw,) in rows:
            meta = json.loads(raw)
            receipt = meta.get('source_timing_receipt')
            # The device exposure time the camera owner also stamps its bundles with.
            stamp = (receipt['frame_device_ns']/1e9 if isinstance(receipt, dict) and type(receipt.get('frame_device_ns')) is int
                     else meta.get('source_timestamp_seconds'))
            if (meta.get('stream') == 'rgb' and meta.get('frame_id') == payload.get('frame_id') and _finite([stamp])
                    and meta.get('capture_epoch') == payload.get('capture_epoch')):
                return float(stamp), meta['capture_epoch']
        return None

    def _process(self, sid, state):
        generation = state.generation()
        with self.store.lock:
            rows = self.store.db.execute(
                """SELECT id,payload FROM events INDEXED BY events_session_kind_id WHERE session_id=?
                AND kind='perception.output' AND id>? ORDER BY id LIMIT ?""", (sid, state.cursor, OUTPUTS_PER_TICK)).fetchall()
        now = self.clock()
        for event_id, raw in rows:
            state.cursor = event_id
            payload = json.loads(raw)
            if (generation is None or payload.get('generation') != generation or payload.get('status') != 'completed'
                    or payload.get('arm') not in ('a', 'b')):
                continue
            frame = self._frame(sid, payload)
            if frame is None:
                state.counts['rgb_frame_unavailable'] += 1
                continue
            state.pending.append((event_id, payload, frame, now))
        state.pending = state.pending[-OUTPUTS_PER_TICK:]
        newest = max((b['stamp'] for b in state.ring), default=None)
        waiting, written = [], 0
        for item in state.pending:
            event_id, payload, (stamp, epoch), seen = item
            same = [b for b in state.ring if b['epoch'] == epoch]
            bundle = min(same, key=lambda b: abs(b['stamp']-stamp), default=None)
            if bundle is not None and abs(bundle['stamp']-stamp) <= PAIR_TOLERANCE_S:
                try:
                    written += self._write(sid, state, event_id, payload, bundle, abs(bundle['stamp']-stamp))
                except Exception:
                    state.counts['failed'] += 1
            elif newest is not None and newest >= stamp+PAIR_TOLERANCE_S or now-seen > PENDING_S:
                state.counts['no_depth_in_window'] += 1
            else:
                waiting.append(item)
        state.pending = waiting
        return {'state': 'active', 'written': written}

    def _write(self, sid, state, event_id, payload, bundle, skew):
        from jev_evidence import project
        from persisted_evidence import observation_record
        started = time.perf_counter()
        base = observation_record(event_id, 'perception.output', payload, state.session)
        if base is None:
            return 0
        record = project([base])[0]
        quality = record.get('quality') or {}
        track = state.arms.setdefault(payload['arm'], ArmTrack())
        if (quality.get('image_width'), quality.get('image_height')) != tuple(bundle['model']['rgb_size']):
            words = frame_facts({**record, 'pose_instances': []}, bundle['depth'], bundle['model'], self.room, track,
                                payload['observed_at'])
        else:
            words = frame_facts(record, bundle['depth'], bundle['model'], self.room, track, payload['observed_at'])
        state.timing['facts_ms'] = round(1000*(time.perf_counter()-started), 2)
        now = self.store.clock()
        if now >= payload['valid_until']:
            state.counts['expired_before_write'] += 1
            return 0
        roots = payload.get('evidence_roots')
        event = {'site_id': payload['site_id'], 'camera_id': payload['camera_id'], 'arm': payload['arm'],
                 'generation': payload['generation'], 'status': 'completed', 'rule': RULE,
                 'observed_at': payload['observed_at'], 'available_at': max(now, payload['observed_at']),
                 'valid_until': payload['valid_until'], 'source_event_id': event_id,
                 'evidence_roots': list(roots) if isinstance(roots, list) and roots else [str(event_id)],
                 'map': {'map_id': self.room['map_id'], 'revision': self.room['revision'],
                         'model_sha256': self.room['model_sha256']},
                 'pairing_skew_s': round(skew, 4), 'result': {'room_facts': words}}
        for key in ('frame_id', 'capture_epoch'):
            if isinstance(payload.get(key), str):
                event[key] = payload[key]
        self.store.append_event(sid, EVENT_KIND, event)
        state.counts['written'] += 1
        return 1
