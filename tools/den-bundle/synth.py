"""Synthetic fixtures, written from schemas.json's Observation and Track fields.

Nothing here is real: the people are A and B, the numbers are made up, and no
frame exists. Times are Unix seconds from T0 on one capture clock. Each scene
returns {'observations': [...], 'tracks': [...], 'crossings': [...], 'about': str}.

Scenes:
- crossing: A and B walk past each other; the crossing is annotated over
  [5.0, 7.0] s and the tracker is ambiguous from 4.5 s to 7.0 s.
- missing_depth: A at the counter; depth arrives 0.2 s late from 4.0 s (the
  plane fallback) and stops from 6.0 s.
- stale_observation: A at the counter; the detector stalls after 5.0 s, and a
  result captured at 3.2 s arrives late, listed after the 5.0 s line.
- two_tracks: A at the counter and B in the armchair, both seen for 70 s.
- track_leaves: as two_tracks for 40 s, but B walks out at 20.0 s.
"""
T0 = 1793066400.0
CAMERA = 'den-oak'
TRANSFORM = 'den-oak@2026-10-18'
FRAME_W, FRAME_H = 1920, 1080
COV3 = [[0.003, 0, 0], [0, 0.009, 0], [0, 0, 0.003]]
COV2 = [[0.03, 0], [0, 0.06]]
JOINT_Z = [1.6, 1.62, 1.62, 1.6, 1.6, 1.4, 1.4, 1.15, 1.15, 0.95, 0.95, 0.95, 0.95, 0.5, 0.5, 0.08, 0.08]
UNSUPPORTED = (1, 2)
UNCERTAIN = (3, 4, 15, 16)


def ts(t):
    return round(T0 + t, 3)


def box_at(x, y, standing=True):
    """A plausible person box in den-oak pixels for a floor point (made up, not a camera model)."""
    cx = 200 + 420 * x
    w, h = (190, 520) if standing else (230, 380)
    bottom = 980 - 120 * y
    return [round(cx - w / 2), round(bottom - h), round(cx + w / 2), round(bottom)]


def keypoints(box, k):
    x0, y0, x1, y1 = box
    out = []
    for j in range(17):
        fx = 0.2 + 0.6 * ((j * 7) % 17) / 16.0
        fy = 0.04 + 0.92 * j / 16.0
        out.append([round(x0 + fx * (x1 - x0), 1), round(y0 + fy * (y1 - y0), 1),
                    round(0.55 + 0.4 * ((j + k) % 5) / 4.0, 2)])
    return out


def joints(x, y, lift):
    out = []
    for j in range(17):
        if j in UNSUPPORTED:
            out.append({'j': j, 'xyz': None, 'cov': None, 'status': 'unsupported', 'lift': 'none'})
            continue
        dx = 0.12 * (1 if j % 2 else -1) if j >= 5 else 0.0
        out.append({'j': j, 'xyz': [round(x + dx, 3), round(y, 3), JOINT_Z[j]], 'cov': COV3,
                    'status': 'uncertain' if j in UNCERTAIN else 'supported',
                    'lift': lift if lift == 'stereo' else 'plane:%.2f' % JOINT_Z[j]})
    return out


def observation(oid, t, x, y, depth='stereo', standing=True, k=0):
    """A keypoints Observation. depth: 'stereo' (33 ms skew), 'edge' (67 ms), 'late' (0.2 s), 'none'."""
    capture = ts(t)
    skew = {'stereo': 0.033, 'edge': 0.067, 'late': 0.2, 'none': None}[depth]
    lift = 'stereo' if depth in ('stereo', 'edge') else 'plane'
    box = box_at(x, y, standing)
    return {
        'version': 'observation/v1',
        'obs_id': oid,
        'source': {'sensor_id': CAMERA, 'model': 'RTMPose', 'model_version': 'rtmpose-m@synthetic'},
        'capture_ts': capture,
        'kind': 'keypoints',
        'image_box': box,
        'keypoints': keypoints(box, k),
        'joint_set': 'coco17',
        'joints_room': joints(x, y, lift),
        'depth_ts': None if skew is None else round(capture - skew, 3),
        'room_position': {'x': x, 'y': y, 'z': 1.0 if standing else 0.7, 'cov': COV3},
        'transform_id': TRANSFORM,
        'score': 0.86,
        'visibility': 'visible',
        'validity': 'valid',
    }


def object_box(oid, t, label, x, y, z):
    return {
        'version': 'observation/v1',
        'obs_id': oid,
        'source': {'sensor_id': CAMERA, 'model': 'YOLO26s', 'model_version': 'yolo26s@synthetic'},
        'capture_ts': ts(t),
        'kind': 'object_box',
        'image_box': [800, 600, 900, 640],
        'label': label,
        'depth_ts': round(ts(t) - 0.033, 3),
        'room_position': {'x': x, 'y': y, 'z': z, 'cov': COV3},
        'transform_id': TRANSFORM,
        'score': 0.8,
        'visibility': 'visible',
        'validity': 'valid',
    }


def track_line(tid, x, y, obs_ids, status='confident', since=0.0, state='confirmed',
               linked=None, posture='standing'):
    hyps = [] if status == 'confident' else [{'hypothesis': 'kept', 'p': 0.55}, {'hypothesis': 'swapped', 'p': 0.45}]
    return {
        'version': 'track/v1',
        'track_id': tid,
        'state': state,
        'position': {'x': x, 'y': y, 'cov': COV2},
        'velocity': {'vx': 0.0, 'vy': 0.0},
        'heading': {'deg': 180, 'sd_deg': 12},
        'heading_as_of': None,
        'head_pose': {'yaw': 180, 'pitch': -40, 'sd': 15},
        'posture': {'value': posture, 'score': 0.9},
        'linked_objects': list(linked or []),
        'association': {'status': status, 'hypotheses': hyps, 'since_ts': ts(since)},
        'identity': {'binding': 'unbound', 'person_id': None, 'method': None, 'confidence': None},
        'last_obs_ids': list(obs_ids),
    }


def steps(t_from, t_to, dt):
    n = int(round((t_to - t_from) / dt))
    return [round(t_from + i * dt, 3) for i in range(n + 1)]


def oid(tid, t):
    return 'o-%s-%06d' % (tid, int(round(t * 1000)))


def seen(obs, tracks, tid, t, x, y, standing=True, depth='stereo', **line_kw):
    o = observation(oid(tid, t), t, x, y, depth=depth, standing=standing, k=int(t * 2))
    obs.append(o)
    tracks.append(track_line(tid, x, y, [o['obs_id']], posture='standing' if standing else 'sitting', **line_kw))


def crossing():
    obs, tracks = [], []
    for t in steps(0.0, 12.0, 0.5):
        ambiguous = 4.5 <= t <= 7.0
        status = 'ambiguous' if ambiguous else 'confident'
        since = 4.5 if ambiguous else (7.5 if t >= 7.5 else 0.0)
        xa, xb = 1.0 + t / 6.0, 3.0 - t / 6.0
        seen(obs, tracks, 'A', t, round(xa, 3), 1.0, status=status, since=since)
        seen(obs, tracks, 'B', t, round(xb, 3), 1.2, status=status, since=since)
    return {'observations': obs, 'tracks': tracks,
            'crossings': [{'crossing_id': 'x1', 'track_ids': ['A', 'B'], 'interval': [ts(5.0), ts(7.0)]}],
            'about': 'A and B cross; annotated [5.0, 7.0] s; tracker ambiguous 4.5-7.0 s'}


def missing_depth():
    obs, tracks = [], []
    board = [{'object': 'cutting board', 'relation': 'using', 'score': 0.81}]
    for t in steps(0.0, 8.0, 0.5):
        depth = 'none' if t >= 6.0 else 'late' if t >= 4.0 else 'edge' if t == 2.0 else 'stereo'
        seen(obs, tracks, 'A', t, 1.95, 0.95, depth=depth, linked=board)
        if t == int(t):
            obs.append(object_box(oid('board', t), t, 'cutting board', 1.9, 0.6, 0.91))
    return {'observations': obs, 'tracks': tracks, 'crossings': [],
            'about': 'A at the counter; depth late (plane) from 4.0 s, absent from 6.0 s'}


def stale_observation():
    obs, tracks = [], []
    for t in steps(0.0, 5.0, 0.5):
        seen(obs, tracks, 'A', t, 1.95, 0.95)
    late = observation('o-A-late-003200', 3.2, 1.95, 0.95, k=6)
    obs.append(late)
    tracks.append(track_line('A', 1.95, 0.95, [late['obs_id']]))
    tracks.append(track_line('A', 1.95, 0.95, []))       # a predicted step: no observation
    return {'observations': obs, 'tracks': tracks, 'crossings': [],
            'about': 'A at the counter; the detector stalls after 5.0 s; a 3.2 s result arrives late'}


def two_people(duration, b_leaves_at=None):
    obs, tracks = [], []
    for t in steps(0.0, duration, 1.0):
        seen(obs, tracks, 'A', t, 1.95, 0.95)
        if b_leaves_at is None or t <= b_leaves_at:
            seen(obs, tracks, 'B', t, 3.2, 2.6, standing=False)
    return {'observations': obs, 'tracks': tracks, 'crossings': []}


def two_tracks():
    scene = two_people(70.0)
    scene['about'] = 'A at the counter and B in the armchair for 70 s'
    return scene


def track_leaves():
    scene = two_people(40.0, b_leaves_at=20.0)
    scene['about'] = 'A and B for 40 s; B walks out after 20.0 s'
    return scene


SCENES = {
    'crossing': crossing,
    'missing_depth': missing_depth,
    'stale_observation': stale_observation,
    'two_tracks': two_tracks,
    'track_leaves': track_leaves,
}
