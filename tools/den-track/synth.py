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
# The made-up den in the shape of METHOD.md's `room` slot, for practice runs.
ROOM_MODEL = {'exits': [list(DOOR)], 'floor': [ROOM['x'][0], ROOM['y'][0], ROOM['x'][1], ROOM['y'][1]]}
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

    def facing(self, t):
        """Bearing (deg, clockwise from +y) the person faces: where they walk,
        and while they pause, where they last walked (or will walk next)."""
        moving = [(a, b, p, q) for a, b, p, q in self.legs if p != q]
        if not moving:
            return 0.0
        leg = None
        for m in moving:
            if m[0] <= t:
                leg = m
        if leg is None:
            leg = moving[0]
        _, _, p, q = leg
        return math.degrees(math.atan2(q[0] - p[0], q[1] - p[1])) % 360.0

    def inside(self, t):
        p = self.at(t)
        return p is not None and ROOM['x'][0] <= p[0] <= ROOM['x'][1] and ROOM['y'][0] <= p[1] <= ROOM['y'][1]


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


def scenario(name, seed=0, noise='typical', fps=4.0):
    """Build one scene. Returns a dict with 'frames' (list of {t, obs: [{
    'id', 'pid', 'xy', 'z', 'depth_sigma'}]}), 'events' and 'name'."""
    rng = random.Random('%s/%s/%s' % (name, seed, noise))
    walkers, events = SCENES[name](rng)
    noise_cfg = NOISE[noise] if isinstance(noise, str) else noise
    duration = max(w.t_end for w in walkers) + 1.5
    frames = []
    n = int(duration * fps)
    t0 = 1000.0 + rng.random()
    for k in range(n):
        t = round(t0 + k / fps + rng.uniform(-0.01, 0.01), 4)
        local = t - t0
        truth = [(w.pid, w.at(local)) for w in walkers if w.inside(local)]
        obs = []
        for pid, p in truth:
            others = [q for opid, q in truth if opid != pid]
            if hidden(p, others) or rng.random() < noise_cfg['miss']:
                continue
            xy, sigma = ray_noise(p, noise_cfg, rng)
            facing = next(w.facing(local) for w in walkers if w.pid == pid)
            obs.append({'pid': pid, 'xy': xy, 'z': CHEST_HEIGHT_M + rng.gauss(0, 0.05), 'depth_sigma': sigma,
                        'true_xy': p, 'facing': facing})
        if noise_cfg['false_per_frame'] and rng.random() < noise_cfg['false_per_frame']:
            fx, fy = rng.uniform(*ROOM['x']), rng.uniform(*ROOM['y'])
            r = math.hypot(fx, fy)
            obs.append({'pid': None, 'xy': (fx, fy), 'z': rng.uniform(0.4, 1.6),
                        'depth_sigma': noise_cfg['depth_base'] + noise_cfg['depth_per_m2'] * r * r,
                        'true_xy': (fx, fy), 'facing': None})
        rng.shuffle(obs)
        for i, o in enumerate(obs):
            o['id'] = 'obs-%s-%d-%05d-%d' % (name, seed, k, i)
        frames.append({'t': t, 'index': k, 'obs': obs,
                       'truth_xy': {pid: p for pid, p in truth}})
    for e in events:
        e['start'] = round(t0 + e['start'], 4)
        e['end'] = round(t0 + e['end'], 4)
    return {'name': name, 'seed': seed, 'noise': noise if isinstance(noise, str) else 'custom',
            'fps': fps, 'frames': frames, 'events': events, 'lateral': noise_cfg['lateral']}


# --- scenes -----------------------------------------------------------------
# Each returns (walkers, events). Events mark the window the owner would
# annotate as one crossing: from a little before the two people come within
# about 1 m of each other (or one goes out of sight) to a little after.


def _cross(rng, gap, along_view):
    """A and B walk past each other; `gap` is the closest distance (m)."""
    speed_a, speed_b = rng.uniform(0.8, 1.3), rng.uniform(0.8, 1.3)
    if along_view:   # one walks toward the camera, the other away, left of the armchair
        xa, xb = -0.8 - gap / 2, -0.8 + gap / 2
        a = Walker('A', [(xa, 1.3, 0.3), (xa, 4.9, 0)], speed_a)
        b = Walker('B', [(xb, 4.9, 0.3), (xb, 1.3, 0)], speed_b)
    else:            # across the view, in front of the armchair
        y = rng.uniform(1.8 + gap / 2, 2.9 - gap / 2)
        a = Walker('A', [(-2.0, y - gap / 2, 0.3), (2.0, y - gap / 2, 0)], speed_a)
        b = Walker('B', [(2.0, y + gap / 2, 0.3), (-2.0, y + gap / 2, 0)], speed_b)
    return a, b


def _meet_window(a, b, pad=1.5, near=1.0):
    times = [k / 20 for k in range(int(max(a.t_end, b.t_end) * 20) + 1)]
    close = [t for t in times if a.at(t) and b.at(t) and math.dist(a.at(t), b.at(t)) < near]
    if not close:
        return 0.0, max(a.t_end, b.t_end)
    return max(0.0, close[0] - pad), close[-1] + pad


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
    s, e = (hid[0] - 1.5, hid[-1] + 1.5) if hid else (0.0, a.t_end)
    return [a, b], [{'id': 'o1', 'kind': 'occlusion', 'start': max(0.0, s), 'end': e}]


def crossing_behind_armchair(rng):
    """A and B cross behind the armchair: both vanish, both come back."""
    speed_a, speed_b = rng.uniform(0.7, 1.1), rng.uniform(0.7, 1.1)
    gap = rng.uniform(0.3, 0.8)
    a = Walker('A', [(-1.9, 4.2 - gap / 2, 0.3), (2.0, 4.2 - gap / 2, 0)], speed_a)
    b = Walker('B', [(2.0, 4.2 + gap / 2, 0.3), (-1.9, 4.2 + gap / 2, 0)], speed_b)
    s, e = _meet_window(a, b, pad=2.0)
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
    return [a, b], [{'id': 'e1', 'kind': 'entry_exit', 'start': s, 'end': e}]


def swap_at_door(rng):
    """A leaves by the door and B comes in by it moments later."""
    a = Walker('A', [(0.5, 2.5, 0.5), (-1.5, 4.3, 0), (DOOR[0], DOOR[1], 0)], rng.uniform(0.9, 1.2), leaves=True)
    gap = rng.uniform(1.0, 3.0)
    b = Walker('B', [(DOOR[0], DOOR[1], 0), (-1.2, 4.0, 0), (0.6, 2.4, 1.0)], rng.uniform(0.9, 1.2),
               t0=a.t_end + gap)
    return [a, b], [{'id': 'd1', 'kind': 'entry_exit', 'start': max(0.0, a.t_end - 2.5), 'end': b.t0 + 3.5}]


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


# --- as live-v1 records ---------------------------------------------------------
# What rung 2 would write for these scenes: `observation` records with the
# field names of schemas.json, and the den-track-truth/v1 lines the owner's
# annotation would give. A made-up pinhole camera turns floor points into
# image boxes, only so a person_box and a keypoints observation of the same
# person overlap the way they would in the real image.

SENSOR = 'synth-cam'
TRANSFORM = 'synth-cam@synth-1'
F_PX, CX_PX, CY_PX = 600.0, 960.0, 540.0
SHOULDER_Z, HIP_Z = 1.40, 0.95
SHOULDER_HALF_M, HIP_HALF_M = 0.19, 0.15
COCO17 = 17


def _ray_cov(xy, along, across, z_sd=0.05):
    dx, dy = xy[0] - CAMERA[0], xy[1] - CAMERA[1]
    r = math.hypot(dx, dy) or 1.0
    ux, uy = dx / r, dy / r
    a2, c2 = along * along, across * across
    return [[a2 * ux * ux + c2 * uy * uy, (a2 - c2) * ux * uy, 0.0],
            [(a2 - c2) * ux * uy, a2 * uy * uy + c2 * ux * ux, 0.0],
            [0.0, 0.0, z_sd * z_sd]]


def _rounded(m, nd=6):
    return [[round(v, nd) for v in row] for row in m]


def _box(xy, rng, jitter):
    y = max(xy[1] - CAMERA[1], 0.3)
    u = lambda x: CX_PX + F_PX * (x - CAMERA[0]) / y
    v = lambda h: CY_PX + F_PX * (CAMERA_HEIGHT_M - h) / y
    box = [u(xy[0] - 0.25), v(1.75), u(xy[0] + 0.25), v(0.0)]
    return [round(b + rng.gauss(0, jitter), 1) for b in box]


def _joints(o, rng, lateral):
    """coco17 joints_room: shoulders and hips from the measured torso point,
    the rest unsupported (null, never zero-filled)."""
    out = [{'j': j, 'xyz': None, 'cov': None, 'status': 'unsupported', 'lift': 'none'} for j in range(COCO17)]
    if o['facing'] is None:
        return out
    b = math.radians(o['facing'])
    fx, fy = math.sin(b), math.cos(b)
    rx, ry = fy, -fx                      # the person's right, seen from above
    r = math.hypot(o['xy'][0] - CAMERA[0], o['xy'][1] - CAMERA[1]) or 1.0
    ux, uy = (o['xy'][0] - CAMERA[0]) / r, (o['xy'][1] - CAMERA[1]) / r
    along = 0.6 * o['depth_sigma']
    across = 0.7 * lateral
    status = 'supported' if r < 4.6 else 'uncertain'
    for left, right, z, half in ((5, 6, SHOULDER_Z, SHOULDER_HALF_M), (11, 12, HIP_Z, HIP_HALF_M)):
        for j, side in ((left, -1), (right, 1)):
            e_along, e_across = rng.gauss(0, along), rng.gauss(0, across)
            x = o['xy'][0] + side * half * rx + e_along * ux - e_across * uy
            y = o['xy'][1] + side * half * ry + e_along * uy + e_across * ux
            out[j] = {'j': j, 'xyz': [round(x, 4), round(y, 4), round(z + rng.gauss(0, 0.03), 4)],
                      'cov': _rounded(_ray_cov((x, y), along, across, 0.03)),
                      'status': status, 'lift': 'stereo'}
    return out


def records(scene, box_share=0.7, keypoints_share=0.9):
    """(observations, truth_lines) for one scene, as live-v1 records."""
    import truth as truth_mod
    rng = random.Random('records/%s/%s/%s' % (scene['name'], scene['seed'], scene['noise']))
    lateral = scene.get('lateral', 0.04)
    observations, labels_by_frame = [], []
    for f in scene['frames']:
        labels = []
        for o in sorted(f['obs'], key=lambda o: o['id']):
            want_kp = o['pid'] is not None and rng.random() < keypoints_share
            want_box = o['pid'] is None or not want_kp or rng.random() < box_share
            common = {'schema_version': 'observation/v1', 'capture_ts': f['t'], 'depth_ts': f['t'],
                      'transform_id': TRANSFORM, 'validity': 'valid'}
            if want_kp:
                rec = dict(common, obs_id=o['id'] + '-k',
                           source={'sensor_id': SENSOR, 'model': 'synth-pose', 'model_version': 'synth-1'},
                           kind='keypoints', image_box=_box(o['true_xy'], rng, 3.0),
                           keypoints=[[None, None, None] for _ in range(COCO17)], joint_set='coco17',
                           joints_room=_joints(o, rng, lateral),
                           room_position={'x': round(o['xy'][0], 4), 'y': round(o['xy'][1], 4),
                                          'z': round(o['z'], 4),
                                          'cov': _rounded(_ray_cov(o['xy'], o['depth_sigma'], lateral))},
                           score=round(rng.uniform(0.6, 0.95), 3), visibility='visible')
                observations.append(rec)
                labels.append((rec['obs_id'], o['pid']))
            if want_box:
                along = 1.3 * o['depth_sigma']
                bx = o['xy'][0] + rng.gauss(0, 0.3 * along) if want_kp else o['xy'][0]
                by = o['xy'][1] + rng.gauss(0, 0.3 * along) if want_kp else o['xy'][1]
                rec = dict(common, obs_id=o['id'] + '-b',
                           source={'sensor_id': SENSOR, 'model': 'synth-det', 'model_version': 'synth-1'},
                           kind='person_box', image_box=_box(o['true_xy'], rng, 6.0),
                           room_position={'x': round(bx, 4), 'y': round(by, 4), 'z': round(o['z'], 4),
                                          'cov': _rounded(_ray_cov((bx, by), along, 1.3 * lateral))},
                           score=round(rng.uniform(0.4, 0.9), 3), visibility='visible')
                observations.append(rec)
                labels.append((rec['obs_id'], o['pid']))
        labels_by_frame.append((f['t'], labels))
    people = sorted({o['pid'] for f in scene['frames'] for o in f['obs'] if o['pid'] is not None})
    lines = [truth_mod.take_line('%s-%d-%s' % (scene['name'], scene['seed'], scene['noise']), people,
                                 labeller='synthetic', blind=True)]
    for t, labels in labels_by_frame:
        lines.append(truth_mod.frame_line(SENSOR, t, labels))
    for e in scene['events']:
        lines.append(truth_mod.window_line(e['id'], e['kind'], e['start'], e['end']))
    return observations, lines
