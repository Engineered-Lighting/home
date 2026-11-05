"""Track and Observation records -> one PersonBundle (rung 5).

`assemble(store, method, track_id, t1)` reads only what is known by `t1`, never
waits, and never reads a clock: a source that is absent or stale is listed in
`missing`. Field names are those of `schemas.json` PersonBundle, plus the
record's `version`. METHOD-V1.md states every rule used here.
"""
import copy
import math

from records import BUNDLE_VERSION, EPS, camera_of, is_valid

PERSON_KINDS = ('person_box', 'keypoints')
SOURCES = ('track', 'person_box', 'keypoints', 'depth', 'object_box')


def inside(ts, t0, t1):
    return t0 - EPS <= ts <= t1 + EPS


def covered(times, t0, t1, max_gap):
    """True when the times leave no hole longer than max_gap in [t0, t1], edges included."""
    if not times:
        return False
    edges = [t0] + sorted(times) + [t1]
    return all(b - a <= max_gap + EPS for a, b in zip(edges, edges[1:]))


def depth_usable(obs, skew):
    d = obs.get('depth_ts')
    if not isinstance(d, (int, float)) or abs(d - obs['capture_ts']) > skew + EPS:
        return False
    for joint in obs.get('joints_room') or []:
        if str(joint.get('lift') or '').startswith('plane'):
            return False
    return True


def crop_box(box, frame_px, pad_fraction, pad_min):
    """N2's crop: pad, clip to the frame, widen outward to even pixels."""
    x0, y0, x1, y1 = box
    w, h = frame_px
    dx = max(pad_fraction * (x1 - x0), pad_min)
    dy = max(pad_fraction * (y1 - y0), pad_min)
    x0, y0 = max(0.0, x0 - dx), max(0.0, y0 - dy)
    x1, y1 = min(float(w), x1 + dx), min(float(h), y1 + dy)
    even_down = lambda v: int(math.floor(v / 2.0)) * 2
    even_up = lambda v: int(math.ceil(v / 2.0)) * 2
    return [even_down(x0), even_down(y0), min(w, even_up(x1)), min(h, even_up(y1))]


def association_safe(store, track_id, t0, t1):
    lines = store.lines_of(track_id, t1)
    at_start = [l for l in lines if l.ts <= t0 + EPS]       # the status at t0 is the latest line by then
    in_effect = at_start[-1:] + [l for l in lines if l.ts > t0 + EPS]
    if not in_effect:
        return False
    first_ts = lines[0].ts
    for line in in_effect:
        if line.status != 'confident':
            return False
        since = line.since
        if isinstance(since, (int, float)) and since > t0 + EPS and first_ts < since - EPS:
            return False      # the status changed inside the window, though no ambiguous line was logged
    return True


def person_frames(store, track_id, t0, t1):
    """(camera_id, ts) -> the person's image_box, from the track's own image observations."""
    frames = {}
    for oid in store.listed(track_id, t1):
        o = store.obs[oid]
        if o.get('kind') not in PERSON_KINDS or not is_valid(o) or not inside(o['capture_ts'], t0, t1):
            continue
        key, box = (camera_of(o), o['capture_ts']), o.get('image_box')
        if key not in frames or frames[key] is None or (o.get('kind') == 'person_box' and box is not None):
            frames[key] = box     # a person_box wins a tie with a keypoints box
    return frames


def gated_keypoints(store, track_id, t0, t1):
    out = []
    for oid, statuses in store.listed(track_id, t1).items():
        o = store.obs[oid]
        if (o.get('kind') == 'keypoints' and is_valid(o) and statuses == {'confident'}
                and inside(o['capture_ts'], t0, t1)):
            out.append(o)
    out.sort(key=lambda o: (o['capture_ts'], o['obs_id']))
    return out


def assemble(store, method, track_id, t1, bundle_id=None):
    t0 = t1 - method['window_s']
    max_gap = method['max_gap_s']

    frames_by_key = person_frames(store, track_id, t0, t1)
    keys = sorted(frames_by_key, key=lambda k: (k[1], str(k[0])))
    latest = {}
    for cam, ts in keys:
        if frames_by_key[(cam, ts)] is not None:
            latest[cam] = ts
    crops = {}
    for cam, ts in latest.items():
        if cam not in method['frame_px']:
            raise ValueError('camera %s has no frame_px in %s' % (cam, method.source))
        crops[cam] = {'box': crop_box(frames_by_key[(cam, ts)], method['frame_px'][cam],
                                      method['pad_fraction'], method['pad_min_px']),
                      'from_ts': ts}
    frames = [{'camera_id': cam, 'ts': ts, 'image_box': copy.deepcopy(frames_by_key[(cam, ts)]),
               'crop_ref': copy.deepcopy(crops.get(cam))} for cam, ts in keys]

    kp_obs = gated_keypoints(store, track_id, t0, t1)
    keypoint_series = [{'obs_id': o['obs_id'], 'ts': o['capture_ts'], 'camera_id': camera_of(o),
                        'transform_id': o.get('transform_id'),
                        'keypoints': copy.deepcopy(o.get('keypoints')),
                        'joints_room': copy.deepcopy(o.get('joints_room'))} for o in kp_obs]

    lines = store.lines_of(track_id, t1)
    positions = []
    for line in lines:
        pos = line.rec.get('position')
        if inside(line.ts, t0, t1) and isinstance(pos, dict):
            positions.append({'ts': line.ts, 'x': pos.get('x'), 'y': pos.get('y'),
                              'cov': copy.deepcopy(pos.get('cov'))})

    objects, objects_missing = [], False
    newest = lines[-1].rec if lines else {}
    for link in newest.get('linked_objects') or []:
        boxes = [o for o in store.obs.values()
                 if o.get('kind') == 'object_box' and o.get('label') == link.get('object') and is_valid(o)
                 and inside(o['capture_ts'], t0, t1) and isinstance(o.get('room_position'), dict)]
        where = None
        if boxes:
            rp = max(boxes, key=lambda o: (o['capture_ts'], o['obs_id']))['room_position']
            where = {'x': rp.get('x'), 'y': rp.get('y'), 'z': rp.get('z')}
        else:
            objects_missing = True
        objects.append({'label': link.get('object'), 'relation': link.get('relation'), 'room_position': where})

    others = []
    for other in store.order:
        if other == track_id:
            continue
        theirs = person_frames(store, other, t0, t1)
        boxes = [theirs.get(k) for k in keys]
        if any(k in theirs for k in keys):
            others.append({'track_id': other, 'image_box': copy.deepcopy(boxes)})

    flags = {
        'track': not covered([p['ts'] for p in positions], t0, t1, max_gap),
        'person_box': not covered([f['ts'] for f in frames if f['image_box'] is not None], t0, t1, max_gap),
        'keypoints': not covered([k['ts'] for k in keypoint_series], t0, t1, max_gap),
        'depth': not kp_obs or not all(depth_usable(o, method['depth_skew_s']) for o in kp_obs),
        'object_box': objects_missing,
    }
    return {
        'version': BUNDLE_VERSION,
        'bundle_id': bundle_id or 'pb-%s-%d' % (track_id, int(round(t1 * 1000))),
        'track_id': track_id,
        'interval': [t0, t1],
        'frames': frames,
        'keypoint_series': keypoint_series,
        'positions': positions,
        'objects': objects,
        'others': others,
        'missing': [s for s in SOURCES if flags[s]],
        'association_safe': association_safe(store, track_id, t0, t1),
    }


def has_box(bundle):
    return any(f['image_box'] is not None for f in bundle['frames'])
