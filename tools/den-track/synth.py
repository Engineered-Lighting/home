"""Synthetic two-person den scenes for tests and tuning.

A made-up den, not the real one: the camera sits at the room-frame origin
looking along +y, the floor runs x -2.2..2.2 m and y 0.8..5.2 m, the door is
at the left wall near (-2.2, 4.4) and an armchair stands at x 0.2..1.0,
y 3.0..3.7. People A and B walk waypoint paths at their own speeds and pauses.

What the camera reports is made noisy the way stereo depth is: the error
along the line of sight grows with the square of the range, the error across
it stays small, a share of frames is missed, a share of observations is an
outlier along the ray, and a few false people appear. A person is hidden
while the armchair, or the nearer person's body, stands between them and the
camera.

Everything is deterministic for a given scenario name and seed.
"""
import math
import random

ROOM = {'x': (-2.2, 2.2), 'y': (0.8, 5.2)}
CAMERA = (0.0, 0.0)
CAMERA_HEIGHT_M = 2.1
DOOR = (-2.4, 4.4)          # just outside the left wall
ARMCHAIR = (0.2, 3.0, 1.0, 3.7)  # x0, y0, x1, y1
BODY_HALF_WIDTH_M = 0.22
CHEST_HEIGHT_M = 1.25

NOISE = {
    'clean': {'depth_base': 0.03, 'depth_per_m2': 0.008, 'lateral': 0.03, 'miss': 0.0,
              'outlier': 0.0, 'false_per_frame': 0.0},
    'typical': {'depth_base': 0.04, 'depth_per_m2': 0.015, 'lateral': 0.04, 'miss': 0.05,
                'outlier': 0.02, 'false_per_frame': 0.02},
    'noisy': {'depth_base': 0.05, 'depth_per_m2': 0.025, 'lateral': 0.05, 'miss': 0.10,
              'outlier': 0.05, 'false_per_frame': 0.05},
}


class Walker:
    """A person following waypoints [(x, y, pause_s), ...] at a speed (m/s),
    starting at t0. Before t0 and after the last waypoint (if `leaves`) the
    person is out of the room."""

    def __init__(self, pid, waypoints, speed, t0=0.0, leaves=False):
        self.pid, self.speed, self.t0, self.leaves = pid, speed, t0, leaves
        self.legs = []  # (t_start, t_end, (x0, y0), (x1, y1))
        t = t0
        x0, y0, pause = waypoints[0]
        if pause:
            self.legs.append((t, t + pause, (x0, y0), (x0, y0)))
            t += pause
        for x1, y1, pause in waypoints[1:]:
            dur = math.hypot(x1 - x0, y1 - y0) / speed
            self.legs.append((t, t + dur, (x0, y0), (x1, y1)))
            t += dur
            if pause:
                self.legs.append((t, t + pause, (x1, y1), (x1, y1)))
                t += pause
            x0, y0 = x1, y1
        self.t_end = t

    def at(self, t):
        if t < self.t0 or (self.leaves and t > self.t_end):
            return None
        for a, b, p, q in self.legs:
            if t <= b:
                f = 0.0 if b <= a else max(0.0, min(1.0, (t - a) / (b - a)))
                return (p[0] + f * (q[0] - p[0]), p[1] + f * (q[1] - p[1]))
        return self.legs[-1][3] if self.legs else None

    def inside(self, t):
        p = self.at(t)
        return p is not None and ROOM['x'][0] <= p[0] <= ROOM['x'][1] and ROOM['y'][0] <= p[1] <= ROOM['y'][1]

    def facing(self, t):
        """The bearing walked (clockwise from +y): the current leg, else the last one with any length."""
        bearing = 0.0
        for a, _, p, q in self.legs:
            if a > t:
                break
            if p != q:
                bearing = math.degrees(math.atan2(q[0] - p[0], q[1] - p[1])) % 360.0
        return bearing


def _segment_hits_box(p, q, box):
    """Does the segment p->q pass through the axis-aligned box (Liang-Barsky)?"""
    x0, y0, x1, y1 = box
    t0, t1 = 0.0, 1.0
    dx, dy = q[0] - p[0], q[1] - p[1]
    for d, lo, hi, s in ((dx, x0, x1, p[0]), (dy, y0, y1, p[1])):
        if abs(d) < 1e-12:
            if s < lo or s > hi:
                return False
            continue
        a, b = (lo - s) / d, (hi - s) / d
        if a > b:
            a, b = b, a
        t0, t1 = max(t0, a), min(t1, b)
        if t0 > t1:
            return False
    return True


def hidden(point, others):
    """Is a person at `point` hidden from the camera?"""
    r = math.hypot(point[0] - CAMERA[0], point[1] - CAMERA[1])
    if _segment_hits_box(CAMERA, point, ARMCHAIR) and r > ARMCHAIR[1]:
        return True
    ux, uy = (point[0] - CAMERA[0]) / r, (point[1] - CAMERA[1]) / r
    for o in others:
        ro = math.hypot(o[0] - CAMERA[0], o[1] - CAMERA[1])
        if ro + 0.25 >= r:
            continue
        # distance of the other body from this person's line of sight
        across = abs((o[0] - CAMERA[0]) * uy - (o[1] - CAMERA[1]) * ux)
        if across < 2 * BODY_HALF_WIDTH_M * 0.8:
            return True
    return False


def ray_noise(point, noise, rng):
    """Measured floor point for a true one: along-ray and across-ray errors."""
    dx, dy = point[0] - CAMERA[0], point[1] - CAMERA[1]
    r = math.hypot(dx, dy)
    ux, uy = dx / r, dy / r
    along_sigma = noise['depth_base'] + noise['depth_per_m2'] * r * r
    along = rng.gauss(0.0, along_sigma)
    if noise['outlier'] and rng.random() < noise['outlier']:
        along = r * rng.choice((-1, 1)) * rng.uniform(0.15, 0.35)
    across = rng.gauss(0.0, noise['lateral'])
    return (point[0] + along * ux - across * uy, point[1] + along * uy + across * ux), along_sigma


def clock_start(name, seed, noise):
    """When a scene is captured. Each has its own place on the clock, as real footage does, since the tool takes
    overlapping capture times for one recording. Whole seconds, so a frame's fraction of a second never depends on this."""
    noises = sorted(NOISE)
    at_noise = noises.index(noise) if isinstance(noise, str) else len(noises)
    return 1000.0 + 1000.0 * (seed + 10000 * (sorted(SCENES).index(name) + 10 * at_noise))


def scenario(name, seed=0, noise='typical', fps=4.0):
    """Build one scene. Returns a dict with 'frames' (list of {t, obs: [{
    'id', 'pid', 'xy', 'z', 'depth_sigma', 'lateral', 'facing'}]}), 'events' and
    'name'. An event's start and end mark the contact, as the owner would."""
    rng = random.Random('%s/%s/%s' % (name, seed, noise))
    walkers, events = SCENES[name](rng)
    noise_cfg = NOISE[noise] if isinstance(noise, str) else noise
    duration = max(w.t_end for w in walkers) + 1.5
    frames = []
    n = int(duration * fps)
    t0 = clock_start(name, seed, noise) + rng.random()
    for k in range(n):
        t = round(t0 + k / fps + rng.uniform(-0.01, 0.01), 4)
        local = t - t0
        truth = [(w.pid, w.at(local)) for w in walkers if w.inside(local)]
        facing = {w.pid: w.facing(local) for w in walkers}
        obs = []
        for pid, p in truth:
            others = [q for opid, q in truth if opid != pid]
            if hidden(p, others) or rng.random() < noise_cfg['miss']:
                continue
            xy, sigma = ray_noise(p, noise_cfg, rng)
            obs.append({'pid': pid, 'xy': xy, 'z': CHEST_HEIGHT_M + rng.gauss(0, 0.05), 'depth_sigma': sigma,
                        'lateral': noise_cfg['lateral'], 'facing': facing[pid]})
        if noise_cfg['false_per_frame'] and rng.random() < noise_cfg['false_per_frame']:
            fx, fy = rng.uniform(*ROOM['x']), rng.uniform(*ROOM['y'])
            r = math.hypot(fx, fy)
            obs.append({'pid': None, 'xy': (fx, fy), 'z': rng.uniform(0.4, 1.6),
                        'depth_sigma': noise_cfg['depth_base'] + noise_cfg['depth_per_m2'] * r * r,
                        'lateral': noise_cfg['lateral'], 'facing': None})
        rng.shuffle(obs)
        for i, o in enumerate(obs):
            o['id'] = 'obs-%s-%d-%05d-%d' % (name, seed, k, i)
        frames.append({'t': t, 'index': k, 'obs': obs,
                       'truth_xy': {pid: p for pid, p in truth}})
    for e in events:
        e['start'] = round(t0 + e['start'], 4)
        e['end'] = round(t0 + e['end'], 4)
    return {'name': name, 'seed': seed, 'noise': noise if isinstance(noise, str) else 'custom',
            'fps': fps, 'frames': frames, 'events': events}


def _scene_rng(scene, frame_index):
    return random.Random('%s/%s/%s/records/%d' % (scene['name'], scene['seed'], scene['noise'], frame_index))


def _image_box(xy):
    """A plausible box in 1920 x 1080 pixels for a floor point (made up, not a camera model)."""
    r = max(math.hypot(xy[0], xy[1]), 0.5)
    cx = 960 + 900 * xy[0] / max(xy[1], 0.5)
    w, h = 450 / r, 1530 / r
    x0, x1 = max(0.0, cx - w / 2), min(1920.0, cx + w / 2)
    y0, y1 = max(0.0, 540 - h / 2), min(1080.0, 540 + h / 2)
    if x1 <= x0:
        x0, x1 = (1800.0, 1900.0) if cx > 960 else (20.0, 120.0)
    return [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]


def _floor_cov(xy, depth_sigma, lateral):
    """A 3x3 covariance from the along-ray and across-ray errors, with z's own."""
    r = math.hypot(xy[0], xy[1])
    ux, uy = xy[0] / r, xy[1] / r
    a, b = depth_sigma ** 2, lateral ** 2
    return [[round(a * ux * ux + b * uy * uy, 9), round((a - b) * ux * uy, 9), 0.0],
            [round((a - b) * ux * uy, 9), round(a * uy * uy + b * ux * ux, 9), 0.0],
            [0.0, 0.0, 0.0025]]


def _joints(xy, facing, rng, sigma=0.03, unsure=0.0):
    """17 joints_room entries: shoulders and hips, the rest unsupported, never zero-filled."""
    joints = [{'j': j, 'xyz': None, 'cov': None, 'status': 'unsupported', 'lift': 'none'} for j in range(17)]
    b = math.radians(facing)
    fx, fy = math.sin(b), math.cos(b)
    rx, ry = fy, -fx        # the person's right, when facing (fx, fy)
    for left, right, half, z in ((5, 6, 0.18, 1.4), (11, 12, 0.14, 0.95)):
        lifted = 'plane:%.2f' % z if rng.random() < unsure else 'stereo'
        status = 'supported' if lifted == 'stereo' else 'uncertain'
        for j, side in ((left, -1.0), (right, 1.0)):
            px = xy[0] + side * half * rx + rng.gauss(0, sigma)
            py = xy[1] + side * half * ry + rng.gauss(0, sigma)
            joints[j] = {'j': j, 'xyz': [round(px, 4), round(py, 4), z], 'status': status, 'lift': lifted,
                         'cov': [[sigma ** 2, 0.0, 0.0], [0.0, sigma ** 2, 0.0], [0.0, 0.0, 0.0025]]}
    return joints


def _keypoints(box, xy, facing):
    """17 made-up [x, y, score] pixels; the face scores high only when turned to the camera."""
    x0, y0, x1, y1 = box
    to_camera = math.degrees(math.atan2(-xy[0], -xy[1])) % 360.0
    turned = abs((facing - to_camera + 180.0) % 360.0 - 180.0)
    face = 0.8 if turned < 90.0 else 0.2
    return [[round(x0 + (x1 - x0) * ((j * 7) % 17) / 16.0, 1), round(y0 + (y1 - y0) * j / 16.0, 1),
             face if j < 5 else 0.8] for j in range(17)]


def to_records(scene):
    """A scene as Observation lines and truth frames, from schemas.json's fields.

    Returns (observations, truth_frames, crossings). Each person gets one keypoints
    Observation, as rung 2 writes. A false person is only a person_box. Nothing here
    is real.
    """
    observations, frames = [], []
    unsure = 0.2 if scene['noise'] == 'noisy' else 0.0
    source = {'sensor_id': 'den-oak', 'model': 'synthetic', 'model_version': 'synth-1'}
    for frame in scene['frames']:
        rng = _scene_rng(scene, frame['index'])
        labels = {}
        for o in frame['obs']:
            xy = (round(o['xy'][0], 5), round(o['xy'][1], 5))
            box = _image_box(xy)
            base = {'version': 'observation/v1', 'source': source, 'capture_ts': frame['t'], 'image_box': box,
                    'room_position': {'x': xy[0], 'y': xy[1], 'z': round(o['z'], 4),
                                      'cov': _floor_cov(xy, o['depth_sigma'], o['lateral'])},
                    'transform_id': 'den-oak@synthetic', 'score': 0.86, 'visibility': 'visible', 'validity': 'valid',
                    'depth_ts': frame['t']}
            if o['pid'] is None:
                rec = dict(base, obs_id=o['id'] + '-b', kind='person_box')
            else:
                rec = dict(base, obs_id=o['id'] + '-k', kind='keypoints', joint_set='coco17',
                           keypoints=_keypoints(box, xy, o['facing']),
                           joints_room=_joints(xy, o['facing'], rng, unsure=unsure))
            observations.append(rec)
            labels[rec['obs_id']] = o['pid']
        frames.append((frame['t'], labels))
    crossings = [{'crossing_id': e['id'], 'type': e['kind'], 'interval': [e['start'], e['end']]}
                 for e in scene['events']]
    return observations, frames, crossings


# --- scenes -----------------------------------------------------------------
# Each returns (walkers, events). An event marks the contact as the owner would
# annotate it: from when the two people come within about 1 m of each other, or
# one goes out of sight, until they are apart again or back in view.


def _cross(rng, gap, along_view):
    """A and B walk past each other; `gap` is the closest distance (m)."""
    speed_a, speed_b = rng.uniform(0.8, 1.3), rng.uniform(0.8, 1.3)
    if along_view:   # one walks toward the camera, the other away, left of the armchair; then the near one steps aside,
        xa, xb = -0.8 - gap / 2, -0.8 + gap / 2     # since otherwise they can hide the far one until the take ends
        a = Walker('A', [(xa, 1.3, 0.3), (xa, 4.9, 0)], speed_a)
        b = Walker('B', [(xb, 4.9, 0.3), (xb, 1.3, 0.6), (xb + 1.5, 1.3, 0)], speed_b)
    else:            # across the view, in front of the armchair
        y = rng.uniform(1.8 + gap / 2, 2.9 - gap / 2)
        a = Walker('A', [(-2.0, y - gap / 2, 0.3), (2.0, y - gap / 2, 0)], speed_a)
        b = Walker('B', [(2.0, y + gap / 2, 0.3), (-2.0, y + gap / 2, 0)], speed_b)
    return a, b


def _meet_window(a, b, near=1.0):
    times = [k / 20 for k in range(int(max(a.t_end, b.t_end) * 20) + 1)]
    close = [t for t in times if a.at(t) and b.at(t) and math.dist(a.at(t), b.at(t)) < near]
    if not close:
        return 0.0, max(a.t_end, b.t_end)
    return close[0], close[-1]


def clean_crossing(rng):
    a, b = _cross(rng, rng.uniform(0.9, 1.4), along_view=rng.random() < 0.5)
    s, e = _meet_window(a, b, near=1.6)
    return [a, b], [{'id': 'c1', 'kind': 'crossing', 'start': s, 'end': e}]


def close_crossing(rng):
    a, b = _cross(rng, rng.uniform(0.45, 0.7), along_view=rng.random() < 0.5)
    s, e = _meet_window(a, b)
    return [a, b], [{'id': 'c1', 'kind': 'crossing', 'start': s, 'end': e}]


def touching_crossing(rng):
    """Shoulders brush: the geometry alone cannot tell who is who."""
    a, b = _cross(rng, rng.uniform(0.0, 0.25), along_view=rng.random() < 0.5)
    s, e = _meet_window(a, b)
    return [a, b], [{'id': 'c1', 'kind': 'crossing', 'start': s, 'end': e}]


def armchair_occlusion(rng):
    """A walks behind the armchair and out again; B stands by the window."""
    speed = rng.uniform(0.7, 1.1)
    a = Walker('A', [(-1.6, 4.3, 0.3), (0.6, 4.3, rng.uniform(0.0, 1.5)), (1.9, 4.3, 0.5)], speed)
    b = Walker('B', [(-1.5, 1.6, a.t_end + 1.0)], 1.0)
    times = [k / 20 for k in range(int(a.t_end * 20))]
    hid = [t for t in times if hidden(a.at(t), [])]
    s, e = (hid[0], hid[-1]) if hid else (0.0, a.t_end)
    return [a, b], [{'id': 'o1', 'kind': 'occlusion', 'start': s, 'end': e}]


def crossing_behind_armchair(rng):
    """A and B cross behind the armchair: both vanish, both come back."""
    speed_a, speed_b = rng.uniform(0.7, 1.1), rng.uniform(0.7, 1.1)
    gap = rng.uniform(0.3, 0.8)
    a = Walker('A', [(-1.9, 4.2 - gap / 2, 0.3), (2.0, 4.2 - gap / 2, 0)], speed_a)
    b = Walker('B', [(2.0, 4.2 + gap / 2, 0.3), (-1.9, 4.2 + gap / 2, 0)], speed_b)
    s, e = _meet_window(a, b)
    return [a, b], [{'id': 'x1', 'kind': 'crossing_occluded', 'start': s, 'end': e}]


def near_pass(rng):
    """A and B walk toward each other, stop close, and each goes back."""
    gap = rng.uniform(0.35, 0.6)
    y = rng.uniform(2.6, 3.0)
    a = Walker('A', [(-1.9, y, 0.2), (-gap / 2, y, rng.uniform(0.5, 2.0)), (-1.9, y, 0)], rng.uniform(0.8, 1.1))
    b = Walker('B', [(1.9, y, 0.2), (gap / 2, y, rng.uniform(0.5, 2.0)), (1.9, y, 0)], rng.uniform(0.8, 1.1))
    s, e = _meet_window(a, b)
    return [a, b], [{'id': 'n1', 'kind': 'near_pass', 'start': s, 'end': e}]


def entry_exit(rng):
    """B is sitting; A comes in by the door, crosses the room and leaves."""
    b = Walker('B', [(1.2, 2.0, 1e9)], 1.0)
    a = Walker('A', [(DOOR[0], DOOR[1], 0.0), (-1.0, 4.0, 0), (0.8, 2.6, 0.8), (-1.0, 4.0, 0),
                     (DOOR[0], DOOR[1], 0)], rng.uniform(0.8, 1.2), t0=1.0, leaves=True)
    b.t_end = a.t_end + 2.0
    s, e = _meet_window(a, b, near=1.4)
    return [a, b], [{'id': 'e1', 'kind': 'near_pass', 'start': s, 'end': e}]


def swap_at_door(rng):
    """A leaves by the door and B comes in by it moments later."""
    a = Walker('A', [(0.5, 2.5, 0.5), (-1.5, 4.3, 0), (DOOR[0], DOOR[1], 0)], rng.uniform(0.9, 1.2), leaves=True)
    gap = rng.uniform(1.0, 3.0)
    b = Walker('B', [(DOOR[0], DOOR[1], 0), (-1.2, 4.0, 0), (0.6, 2.4, 1.0)], rng.uniform(0.9, 1.2),
               t0=a.t_end + gap)
    return [a, b], [{'id': 'd1', 'kind': 'door', 'start': a.t_end - 0.5, 'end': b.t0 + 1.5}]


SCENES = {
    'clean_crossing': clean_crossing,
    'close_crossing': close_crossing,
    'touching_crossing': touching_crossing,
    'armchair_occlusion': armchair_occlusion,
    'crossing_behind_armchair': crossing_behind_armchair,
    'near_pass': near_pass,
    'entry_exit': entry_exit,
    'swap_at_door': swap_at_door,
}
