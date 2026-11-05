"""Binding to the live-v1 records: `observation` in, `track` out.

Field names are exactly those in docs/architecture/live-v1/schemas.json.
Records travel as JSON Lines (one record per line, UTF-8, LF). A reader
accepts CRLF and a UTF-8 byte-order mark too, because a file that passed
through Windows may carry either.

Observations: only `person_box` and `keypoints` observations that are valid
and carry a `room_position` with its covariance are used. Everything else is
counted and skipped: object boxes, depth and radar points, and the face and
appearance kinds, which this geometry-only arm never reads. A person_box and
a keypoints observation of the same person in the same frame (same sensor,
same capture time, overlapping image boxes) become one measurement, and the
track lists both in `last_obs_ids`. The floor point is the keypoints torso
reference point when there is one.

Heading: body facing from the left and right shoulders and hips in
`joints_room` (coco17 joints 5, 6, 11, 12), as a bearing from +y, clockwise
seen from above. It is accepted only when the pair is wide enough on the
floor and its spread is small enough, and `heading_as_of` is that
observation's capture time.
"""
import hashlib
import json
import math

OBSERVATION_VERSION = 'observation/v1'
TRACK_VERSION = 'track/v1'
PERSON_KINDS = ('person_box', 'keypoints')
NEVER_READ = ('face_match', 'appearance_vec')

# The Track record's fields, in schemas.json order. `appearance` exists only
# if the owner allows session appearance ({{dec:learning-permissions}}), so
# this arm never writes it.
TRACK_FIELDS = ('track_id', 'state', 'position', 'velocity', 'heading', 'heading_as_of', 'head_pose',
                'posture', 'linked_objects', 'association', 'identity', 'last_obs_ids')
TRACK_FIELDS_NOT_WRITTEN = ('appearance',)


class RecordError(Exception):
    pass


# -- JSON Lines -----------------------------------------------------------------

def read_jsonl(path):
    """[(line_number, record)] from a JSON Lines file; CRLF and a BOM read the same."""
    out = []
    with open(path, encoding='utf-8-sig', newline=None) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError as e:
                raise RecordError('%s line %d is not JSON: %s' % (path, n, e))
            if not isinstance(rec, dict):
                raise RecordError('%s line %d is not a JSON object' % (path, n))
            out.append((n, rec))
    return out


def write_jsonl(path, records):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(', ', ': ')) + '\n')


def file_sha256(path):
    """Of the file's lines with LF endings, so CRLF and LF copies hash the same."""
    h = hashlib.sha256()
    with open(path, encoding='utf-8-sig', newline=None) as f:
        for line in f:
            h.update(line.rstrip('\n').encode('utf-8') + b'\n')
    return 'sha256:' + h.hexdigest()


# -- observations ---------------------------------------------------------------

def _valid(validity):
    if isinstance(validity, str):
        return validity == 'valid'
    if isinstance(validity, dict):
        return validity.get('status', validity.get('value')) == 'valid'
    return False


def _cov2(cov, floor_sd):
    """The floor (x, y) block of a 3x3 room covariance, plus the body term."""
    try:
        c = [[float(cov[0][0]), float(cov[0][1])], [float(cov[1][0]), float(cov[1][1])]]
    except (TypeError, IndexError, ValueError):
        return None
    c[0][1] = c[1][0] = (c[0][1] + c[1][0]) / 2
    if not (c[0][0] > 0 and c[1][1] > 0 and c[0][0] * c[1][1] - c[0][1] ** 2 > 0):
        return None          # no measurement is exact: a zero or broken covariance is refused
    c[0][0] += floor_sd ** 2
    c[1][1] += floor_sd ** 2
    return c


def _iou(a, b):
    try:
        ax0, ay0, ax1, ay1 = (float(v) for v in a)
        bx0, by0, bx1, by1 = (float(v) for v in b)
    except (TypeError, ValueError):
        return 0.0
    iw, ih = min(ax1, bx1) - max(ax0, bx0), min(ay1, by1) - max(ay0, by0)
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / union if union > 0 else 0.0


def heading_from_joints(joints, cfg):
    """(deg, sd_deg) of body facing from coco17 shoulders and hips, or None.

    Facing is the left-to-right vector turned a quarter turn to the left: a
    person facing +y has their right shoulder toward +x."""
    if not isinstance(joints, list):
        return None
    by_j = {}
    for e in joints:
        if isinstance(e, dict) and isinstance(e.get('j'), int):
            by_j[e['j']] = e
    estimates = []
    for left, right in cfg['pairs']:
        a, b = by_j.get(left), by_j.get(right)
        if not a or not b or a.get('status') != 'supported' or b.get('status') != 'supported':
            continue
        if a.get('xyz') is None or b.get('xyz') is None or a.get('cov') is None or b.get('cov') is None:
            continue
        try:
            dx, dy = float(b['xyz'][0]) - float(a['xyz'][0]), float(b['xyz'][1]) - float(a['xyz'][1])
            cxx = float(a['cov'][0][0]) + float(b['cov'][0][0])
            cxy = float(a['cov'][0][1]) + float(b['cov'][0][1])
            cyy = float(a['cov'][1][1]) + float(b['cov'][1][1])
        except (TypeError, IndexError, ValueError):
            continue
        sep = math.hypot(dx, dy)
        if sep < cfg['min_pair_m']:
            continue
        nx, ny = -dy / sep, dx / sep            # unit normal to the pair = facing
        var_across = nx * nx * cxx + 2 * nx * ny * cxy + ny * ny * cyy
        sd = math.degrees(math.sqrt(max(var_across, 0.0)) / sep)
        estimates.append((nx, ny, sd))
    if not estimates:
        return None
    wsum = sx = sy = 0.0
    for nx, ny, sd in estimates:
        w = 1.0 / max(sd, 0.1) ** 2
        sx, sy, wsum = sx + w * nx, sy + w * ny, wsum + w
    sd = math.sqrt(1.0 / wsum)
    if sd > cfg['max_sd_deg']:
        return None
    return math.degrees(math.atan2(sx, sy)) % 360.0, sd


def read_observations(path, cfg):
    """Group usable person observations into frames of measurements.

    Returns (frames, counts): frames is [(capture_ts, sensor_id,
    [measurement])] in time order, each measurement {'ids', 'xy', 'R',
    'heading', 'members'}; counts says what was used and why the rest was
    skipped. `members` are the merged observations other than the one that
    gives the floor point."""
    counts = {'lines': 0, 'used': 0, 'measurements': 0, 'merged': 0}

    def skip(why):
        counts['skipped_' + why] = counts.get('skipped_' + why, 0) + 1

    by_frame = {}
    seen_ids = set()
    for n, rec in read_jsonl(path):
        counts['lines'] += 1
        version = rec.get('schema_version')
        if version is not None and not str(version).startswith(OBSERVATION_VERSION):
            raise RecordError('%s line %d: schema_version %r is not %s' % (path, n, version, OBSERVATION_VERSION))
        if version is None:
            counts['unversioned'] = counts.get('unversioned', 0) + 1
        for key in ('obs_id', 'source', 'capture_ts', 'kind'):
            if key not in rec:
                raise RecordError('%s line %d: observation without %s' % (path, n, key))
        if rec['obs_id'] in seen_ids:
            raise RecordError('%s line %d: obs_id %r appears twice' % (path, n, rec['obs_id']))
        seen_ids.add(rec['obs_id'])
        kind = rec['kind']
        if kind in NEVER_READ:
            skip('identity_kind')
            continue
        if kind not in PERSON_KINDS:
            skip('not_a_person_kind')
            continue
        if not _valid(rec.get('validity')):
            skip('not_valid')
            continue
        rp = rec.get('room_position')
        if not isinstance(rp, dict) or rp.get('x') is None or rp.get('y') is None:
            skip('no_room_position')
            continue
        R = _cov2(rp.get('cov'), cfg['floor_sd_m'])
        if R is None:
            skip('bad_covariance')
            continue
        source = rec['source'] if isinstance(rec['source'], dict) else {}
        sensor = str(source.get('sensor_id'))
        ts = float(rec['capture_ts'])
        heading = None
        if kind == 'keypoints':
            heading = heading_from_joints(rec.get('joints_room'), cfg['heading'])
        by_frame.setdefault((ts, sensor), []).append({
            'id': rec['obs_id'], 'kind': kind, 'model': str(source.get('model')),
            'box': rec.get('image_box'), 'xy': (float(rp['x']), float(rp['y'])), 'R': R, 'heading': heading,
        })
        counts['used'] += 1
    frames = []
    for (ts, sensor) in sorted(by_frame, key=lambda k: (k[0], k[1])):
        ms = _merge(by_frame[(ts, sensor)], cfg)
        counts['measurements'] += len(ms)
        counts['merged'] += sum(len(m['ids']) - 1 for m in ms)
        frames.append((ts, sensor, ms))
    return frames, counts


def _merge(obs, cfg):
    """One measurement per person: pair observations from different models
    whose image boxes overlap (or, without boxes, whose floor points are
    close), best pairs first. An observation left over stays a measurement
    of its own. Where two people overlap in the image a pairing can be wrong;
    the tracker weighs each merged observation on its own for that."""
    obs = sorted(obs, key=lambda o: o['id'])
    pairs = []
    for i, a in enumerate(obs):
        for j in range(i + 1, len(obs)):
            b = obs[j]
            if a['model'] == b['model']:
                continue
            if a['box'] and b['box']:
                s = _iou(a['box'], b['box'])
                ok = s >= cfg['merge_iou']
            else:
                d = math.dist(a['xy'], b['xy'])
                s = 1.0 - d / (cfg['merge_m'] + 1e-9)
                ok = d <= cfg['merge_m']
            if ok:
                pairs.append((-s, i, j))
    group = list(range(len(obs)))
    models = [{o['model']} for o in obs]

    def find(k):
        while group[k] != k:
            k = group[k]
        return k

    for _, i, j in sorted(pairs):
        ri, rj = find(i), find(j)
        if ri != rj and not (models[ri] & models[rj]):
            group[max(ri, rj)] = min(ri, rj)
            models[min(ri, rj)] |= models[max(ri, rj)]
    members = {}
    for k in range(len(obs)):
        members.setdefault(find(k), []).append(obs[k])
    out = []
    for root in sorted(members):
        ms = members[root]
        main = min(ms, key=lambda o: (0 if o['kind'] == 'keypoints' else 1, o['R'][0][0] + o['R'][1][1], o['id']))
        heading = next((o['heading'] for o in ms if o['heading'] is not None), None)
        out.append({'ids': sorted(o['id'] for o in ms), 'xy': main['xy'], 'R': main['R'], 'heading': heading,
                    'members': [{'id': o['id'], 'xy': o['xy'], 'R': o['R']} for o in ms if o is not main]})
    return out


# -- tracks ---------------------------------------------------------------------

def _r(v, nd=4):
    return None if v is None else round(float(v), nd)


def track_record(tracker, tr, t, time_field):
    """One Track record, field names exactly as schemas.json."""
    x, P = tr.x, tr.P
    rec = {'schema_version': TRACK_VERSION, time_field: _r(t, 4)}
    rec.update({
        'track_id': tr.id,
        'state': tr.state,
        'position': {'x': _r(x[0]), 'y': _r(x[1]),
                     'cov': [[_r(P[0][0], 6), _r(P[0][1], 6)], [_r(P[1][0], 6), _r(P[1][1], 6)]]},
        'velocity': {'vx': _r(x[2]), 'vy': _r(x[3])},
        'heading': None if tr.heading is None else {'deg': _r(tr.heading[0], 1), 'sd_deg': _r(tr.heading[1], 1)},
        'heading_as_of': _r(tr.heading_as_of, 4),
        'head_pose': None,        # a summary from face keypoints; not this rung
        'posture': None,          # a summary; K7's PersonState is authoritative
        'linked_objects': None,   # hands on objects; not this rung
        'association': {
            'status': 'ambiguous' if tr.ambiguous else 'confident',
            'hypotheses': [dict(h, p=_r(h['p'], 4)) for h in tracker.hypotheses(tr)],
            'since_ts': _r(tr.since_ts, 4),
        },
        'identity': {'binding': 'unbound', 'person_id': None, 'method': None, 'confidence': None,
                     'as_of': None},
        'last_obs_ids': list(tr.last_obs_ids),
    })
    return rec


def read_tracks(path):
    """Track records from a JSON Lines file, checked for their version and fields."""
    out = []
    for n, rec in read_jsonl(path):
        if rec.get('schema_version') is None or not str(rec['schema_version']).startswith(TRACK_VERSION):
            raise RecordError('%s line %d: not a %s record' % (path, n, TRACK_VERSION))
        missing = [k for k in TRACK_FIELDS if k not in rec]
        if missing:
            raise RecordError('%s line %d: track record without %s' % (path, n, ', '.join(missing)))
        out.append(rec)
    return out
