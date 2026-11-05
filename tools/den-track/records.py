"""`observation` records in, `track` records out.

Field names are schemas.json's. Records travel as JSON Lines: one per line,
UTF-8, LF; reading takes CRLF and a BOM too. Other keys on an Observation
(`version`, or rung 2's `freshness`) are ignored.

What the tracker uses from an Observation: `obs_id`, `capture_ts`, `source.sensor_id`,
`kind` (person_box and keypoints are evidence of a person), `validity`,
`image_box`, `room_position` (projected to the floor) and `joints_room`.
"""
import json
import math

import tracker_core

TRACK_VERSION = 'track/v1'
PERSON_KINDS = ('person_box', 'keypoints')
LEFT_RIGHT_PAIRS = ((5, 6), (11, 12))       # coco17: shoulders, hips
UTF16_BOMS = (b'\xff\xfe', b'\xfe\xff')


class RecordError(Exception):
    pass


def read_jsonl(path):
    with open(path, 'rb') as f:
        head = f.read(2)
    if head in UTF16_BOMS:
        raise RecordError("%s is UTF-16 (PowerShell 5.1's > writes that): save it as UTF-8" % path)
    out = []
    with open(path, encoding='utf-8-sig', newline=None) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError as e:
                raise RecordError('%s line %d: not JSON (%s)' % (path, n, e))
            if not isinstance(rec, dict):
                raise RecordError('%s line %d: not a JSON object' % (path, n))
            out.append(rec)
    return out


def write_jsonl(path, records):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def is_valid(obs):
    """`validity` is `valid`, or rejected with why (schemas.json: enum + reason)."""
    v = obs.get('validity')
    if v is None:
        raise RecordError('observation %s has no validity' % obs.get('obs_id'))
    if isinstance(v, dict):
        return 'valid' in (v.get('status'), v.get('value'))
    return v == 'valid'


def floor_measurement(obs, cov_scale=1.0):
    """(xy, R) from `room_position`, or None when it is absent, null or unusable."""
    rp = obs.get('room_position')
    if not isinstance(rp, dict) or not is_number(rp.get('x')) or not is_number(rp.get('y')):
        return None
    cov = rp.get('cov')
    try:
        c = [[float(cov[0][0]) * cov_scale, float(cov[0][1]) * cov_scale],
             [float(cov[1][0]) * cov_scale, float(cov[1][1]) * cov_scale]]
    except (TypeError, IndexError, ValueError):
        return None
    if not all(math.isfinite(v) for row in c for v in row):
        return None
    if c[0][0] <= 0 or c[0][0] * c[1][1] - c[0][1] * c[1][0] <= 0:
        return None
    return (float(rp['x']), float(rp['y'])), c


def body_heading(obs):
    """(bearing deg, sd deg) of body facing from supported shoulder and hip pairs, or None.

    Bearings run clockwise from +y, seen from above (schemas.json frames). A
    person facing +y has their left at -x and their right at +x, so facing is
    the left-to-right line turned 90 degrees counterclockwise.
    """
    if obs.get('kind') != 'keypoints' or obs.get('joint_set') != 'coco17':
        return None
    joints = {j.get('j'): j for j in obs.get('joints_room') or [] if isinstance(j, dict)}
    spans = []
    for left, right in LEFT_RIGHT_PAIRS:
        a, b = joints.get(left), joints.get(right)
        if not a or not b or a.get('status') != 'supported' or b.get('status') != 'supported':
            continue
        if not a.get('xyz') or not b.get('xyz') or not a.get('cov') or not b.get('cov'):
            continue
        spans.append((b['xyz'][0] - a['xyz'][0], b['xyz'][1] - a['xyz'][1],
                      a['cov'][0][0] + b['cov'][0][0], a['cov'][0][1] + b['cov'][0][1],
                      a['cov'][1][1] + b['cov'][1][1]))
    if not spans:
        return None
    n = len(spans)
    ax, ay = sum(s[0] for s in spans) / n, sum(s[1] for s in spans) / n
    length = math.hypot(ax, ay)
    if length < 1e-6:
        return None
    cxx, cxy, cyy = (sum(s[k] for s in spans) / (n * n) for k in (2, 3, 4))
    fx, fy = -ay / length, ax / length      # facing, a unit vector
    # Doubt in the line's direction is its variance across its own length.
    across = fx * fx * cxx + 2 * fx * fy * cxy + fy * fy * cyy
    return math.degrees(math.atan2(fx, fy)) % 360.0, math.degrees(math.sqrt(max(across, 0.0)) / length)


def iou(a, b):
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    if ix <= 0 or iy <= 0:
        return 0.0
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _pair_box_with_pose(group, min_iou):
    """One person's person_box and keypoints in a frame share a box: pair them, best overlap first."""
    boxes = [o for o in group if o['kind'] == 'person_box' and o.get('image_box')]
    poses = [o for o in group if o['kind'] == 'keypoints' and o.get('image_box')]
    pairs = sorted(((-iou(b['image_box'], k['image_box']), b['obs_id'], k['obs_id']) for b in boxes for k in poses))
    used, partner = set(), {}
    for neg, b, k in pairs:
        if -neg >= min_iou and b not in used and k not in used:
            used.update((b, k))
            partner[b], partner[k] = k, b
    return partner


def frames_from_observations(observations, params):
    """Valid person evidence as tracker frames, oldest first, with what was left out.

    Returns (frames, counts). A frame is one capture time on one sensor, and any
    Observation there makes a frame, even one with nobody in it. Each detection
    is one person: a keypoints Observation with its person_box if both exist,
    measured at the keypoints' torso point. A valid Observation with no usable
    position still joins its partner's update, and is otherwise left out.
    """
    counts = {'observations': 0, 'not_person': 0, 'rejected': 0, 'no_position': 0}
    by_frame = {}
    seen = set()
    for obs in observations:
        oid, ts = obs.get('obs_id'), obs.get('capture_ts')
        if not isinstance(oid, str) or not is_number(ts):
            raise RecordError('an observation needs a string obs_id and a numeric capture_ts: %r' % (oid,))
        if oid in seen:
            raise RecordError('obs_id %s appears twice' % oid)
        seen.add(oid)
        counts['observations'] += 1
        sensor = (obs.get('source') or {}).get('sensor_id') or ''
        by_frame.setdefault((ts, sensor), [])
        if obs.get('kind') not in PERSON_KINDS:
            counts['not_person'] += 1
        elif not is_valid(obs):
            counts['rejected'] += 1
        else:
            by_frame[(ts, sensor)].append(obs)
    frames = []
    for (ts, sensor) in sorted(by_frame):
        group = sorted(by_frame[(ts, sensor)], key=lambda o: o['obs_id'])
        partner = _pair_box_with_pose(group, params['merge_iou'])
        detections = []
        for obs in group:
            mate = partner.get(obs['obs_id'])
            members = [obs] + [o for o in group if o['obs_id'] == mate]
            if mate is not None and obs['kind'] != 'keypoints':
                continue        # the keypoints half of the pair speaks for both
            where = None
            for member in members:      # the keypoints' torso point first
                where = floor_measurement(member, params['obs_cov_scale'])
                if where is not None:
                    break
            if where is None:
                counts['no_position'] += len(members)
                continue
            detections.append({'id': obs['obs_id'], 'xy': where[0], 'R': where[1],
                               'obs_ids': sorted(m['obs_id'] for m in members),
                               'heading': body_heading(obs)})
        frames.append({'t': ts, 'sensor': sensor, 'detections': detections})
    return frames, counts


def _r(v, digits=6):
    return round(v, digits) + 0.0


def track_record(track, state):
    """One `track` line. Rung 3 fills what geometry gives; the rest stays empty."""
    x, y, vx, vy = track.x
    hypotheses = []
    if track.ambiguous:
        hypotheses.append({'hypothesis': 'kept', 'p': _r(track.confidence, 4)})
        named = dict(track.rivals)
        for other in track.near:
            named.setdefault(other, 0.0)
        for who in sorted((k for k in named if k is not None)):
            hypotheses.append({'hypothesis': 'swapped:%s' % track_name(who), 'p': _r(named[who], 4)})
        if None in named:
            hypotheses.append({'hypothesis': 'newcomer', 'p': _r(named[None], 4)})
    heading = None
    if track.heading is not None:
        heading = {'deg': _r(track.heading[0], 2), 'sd_deg': _r(track.heading[1], 2)}
    return {
        'version': TRACK_VERSION,
        'track_id': track_name(track.id),
        'state': state,
        'position': {'x': _r(x), 'y': _r(y),
                     'cov': [[_r(track.P[0][0]), _r(track.P[0][1])], [_r(track.P[1][0]), _r(track.P[1][1])]]},
        'velocity': {'vx': _r(vx), 'vy': _r(vy)},
        'heading': heading,
        'heading_as_of': track.heading_as_of,
        'head_pose': None,
        'posture': {'value': 'unknown', 'score': None},
        'linked_objects': [],
        'association': {'status': 'ambiguous' if track.ambiguous else 'confident',
                        'hypotheses': hypotheses, 'since_ts': track.since_ts},
        'identity': {'binding': 'unbound', 'person_id': None, 'method': None, 'confidence': None, 'as_of': None},
        'last_obs_ids': [] if state == 'ended' else list(track.obs_ids),
    }


def track_name(number):
    return 'trk-%d' % number


def run_tracker(observations, params, floor=None, exits=()):
    """All `track` lines for these observations, plus the left-out counts."""
    frames, counts = frames_from_observations(observations, params)
    core = tracker_core.TrackerCore(params, exits=exits, floor=floor)
    lines = []
    for frame in frames:
        for track, state in core.step(frame['t'], frame['detections']):
            lines.append(track_record(track, state))
    counts['frames'] = len(frames)
    counts['tracks'] = core.next_id - 1
    counts['track_lines'] = len(lines)
    return lines, counts
