"""Stereo head-pose prototype (plan section 11, stages 3-4): what the head points at.

Pure and offline: numpy only. No camera, network, ledger or model access, and
not wired into JEV. This is head pose, not eye gaze: pupils are not reliably
visible at this range, so every result carries provenance
'head_pose_not_eye_gaze'.

Frames:
- 'camera' is the OAK rectified-left (depth) frame: x right, y down, z forward, metres.
- RGB keypoints are COCO17 source pixels of the 640x360 RGB frame. They are
  lifted through a depth_association camera model: every valid depth pixel is
  projected into RGB at its own depth (EEPROM extrinsics), and the depth
  pixels landing near a keypoint give its visible surface point.
- 'room' is the site map frame (z_up_metric_floor0, metres), reached through
  the map's camera registration matrix.

Pipeline for one pose: lift the face joints (robust front-surface median in a
small patch), fit a rigid head template to the 3D nose/eyes/ears (needs the
nose and at least one ear), fall back to the calibrated 2D yaw/pitch measures
of jev_semantics.head_measures, then cast a cone from the head against the
map's named anchors.
"""
import math

NOSE, LEFT_EYE, RIGHT_EYE, LEFT_EAR, RIGHT_EAR = 0, 1, 2, 3, 4
LEFT_SHOULDER, RIGHT_SHOULDER, LEFT_ELBOW, RIGHT_ELBOW, LEFT_WRIST, RIGHT_WRIST = 5, 6, 7, 8, 9, 10
FACE = (NOSE, LEFT_EYE, RIGHT_EYE, LEFT_EAR, RIGHT_EAR)
# Rigid head template in metres, head axes (right, forward, up) from the ear
# midpoint. Subject's left is -right, so COCO left_eye/left_ear have x < 0.
# Adult averages; the fit residual is reported, never hidden.
HEAD_TEMPLATE = {NOSE: (0.0, 0.100, -0.020), LEFT_EYE: (-0.032, 0.085, 0.015), RIGHT_EYE: (0.032, 0.085, 0.015),
                 LEFT_EAR: (-0.075, 0.0, 0.0), RIGHT_EAR: (0.075, 0.0, 0.0)}
PATCH_RADIUS_PX = 4.0
FACE_DEPTH_TOLERANCE_M = 0.15   # a joint's depth this far from the face patch is background
SURFACE_TO_CENTRE_M = 0.08      # face surface to head centre along the viewing ray
FIT_RMS_MAX_M = 0.03
AGREE_DEG = 45.0                # 3D and 2D-prior directions further apart are a conflict
# 2D prior (jev_semantics.head_measures ratios) -> angles. Uncalibrated
# first guesses: yaw ratio 0.54 (turned, one ear) -> 54 deg; pitch ratio
# 0.45 is level (reading 0.47, computer 0.42), 0.34 (phone) -> -28 deg.
YAW_DEG_PER_RATIO = 100.0
PITCH_LEVEL_RATIO = 0.45
PITCH_DEG_PER_RATIO = 250.0
EAR_MERGED = 0.1                # as jev_semantics: ears closer than this x shoulder width are one ear
# Cone test.
CONE_DEG = 20.0                 # head pose vs gaze: eyes rotate about +-15-20 deg
MARGIN_DEG = 4.0                # rivals of another group this close make the answer ambiguous
DOWN_PITCH_DEG = -35.0          # below this with no anchor hit: lap_or_hands
CONTAINS_M = 0.15               # an anchor this close to the head (the chair you sit in) is skipped
DEFAULT_RADIUS_M = 0.25
GROUPS = {'monitor': 'pc', 'keyboard_area': 'pc', 'desk': 'pc'}
PROVENANCE = {'origin_family': 'same_camera_visual', 'method': 'stereo_head_pose_v1',
              'kind': 'head_pose_not_eye_gaze', 'calibration': 'unvalidated'}


def _np():
    import numpy as np
    return np


def _unit(v):
    np = _np()
    v = np.asarray(v, dtype=np.float64)
    n = float(np.linalg.norm(v))
    return v/n if n > 1e-12 else None


def _angle_deg(a, b):
    np = _np()
    a, b = _unit(a), _unit(b)
    if a is None or b is None:
        return None
    return math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(a, b))))))


def joints_of(pose):
    """{joint_id: (u, v)} for supported, finite COCO17 joints of one pose."""
    joints = {}
    for joint in (pose or {}).get('joints') or []:
        if not isinstance(joint, dict) or joint.get('status') != 'supported' or type(joint.get('joint_id')) is not int:
            continue
        pixel = joint.get('pixel')
        if (isinstance(pixel, (list, tuple)) and len(pixel) == 2
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in pixel)):
            joints[joint['joint_id']] = (float(pixel[0]), float(pixel[1]))
    return joints


def resolve_ears(joints):
    """RTMPose puts a hidden far ear on the near one (plan section 11 notes).

    Two ears closer than EAR_MERGED x shoulder width are one ear: the ear on
    the image side away from the nose, i.e. the subject's right ear when the
    nose sits to its image right (subject's right appears on image left).
    """
    joints = dict(joints)
    if LEFT_EAR in joints and RIGHT_EAR in joints:
        (lx, ly), (rx, ry) = joints[LEFT_EAR], joints[RIGHT_EAR]
        if LEFT_SHOULDER in joints and RIGHT_SHOULDER in joints:
            scale = math.dist(joints[LEFT_SHOULDER], joints[RIGHT_SHOULDER])
        else:
            scale = 30.0/EAR_MERGED  # 30 px without shoulders
        if abs(lx-rx) < EAR_MERGED*scale:
            merged = ((lx+rx)/2, (ly+ry)/2)
            del joints[LEFT_EAR], joints[RIGHT_EAR]
            nose = joints.get(NOSE)
            if nose is not None:
                joints[RIGHT_EAR if nose[0] > merged[0] else LEFT_EAR] = merged
    return joints


def front_cluster(points, *, quantile=0.2, relative=0.03, floor_m=0.04):
    """Points near the front surface: depth within tolerance of a low quantile.

    Keeps a keypoint's own surface when a patch edge leaks onto a wall behind.
    """
    np = _np()
    z = points[:, 2]
    reference = float(np.quantile(z, quantile))
    tolerance = max(floor_m, relative*reference)
    return points[np.abs(z-reference) <= tolerance]


def foreground_layer(points, *, gap_m=0.12, trim=0.02):
    """Points up to the first depth gap wider than gap_m behind the nearest
    surface: keeps a slanted object face whole (a desk front spans tens of cm
    in depth) while dropping the wall behind it."""
    np = _np()
    order = np.argsort(points[:, 2])
    z = points[order, 2]
    start = int(len(z)*trim)
    gaps = np.flatnonzero(np.diff(z[start:]) > gap_m)
    end = start+int(gaps[0])+1 if len(gaps) else len(z)
    return points[order[start:end]]


class DepthLifter:
    """Rectified-left depth pixels with their RGB projections, for RGB-pixel lookups.

    depth_mm: uint16 (H, W) aligned to rectified left; model: a
    depth_association.camera_model. rgb_roi (x0, y0, x1, y1) limits the work to
    the depth region that projects near that RGB box (a stride-4 pass finds
    it), which keeps a face lookup far below a full-frame projection.
    """

    def __init__(self, depth_mm, model, *, rgb_roi=None, margin_px=16, stride=4):
        np = _np()
        from depth_association import rgb_pixels
        depth = np.asarray(depth_mm)
        width, height = model['depth_size']
        if depth.shape != (height, width) or depth.dtype != np.uint16:
            raise ValueError('rectified-left uint16 depth matching the model required')
        valid = depth > 0
        if rgb_roi is not None:
            x0, y0, x1, y1 = (float(v) for v in rgb_roi)
            coarse = np.zeros_like(valid)
            coarse[::stride, ::stride] = valid[::stride, ::stride]
            uv, ok = rgb_pixels(model, depth, coarse)
            inside = (ok & (uv[:, 0] >= x0-margin_px) & (uv[:, 0] <= x1+margin_px)
                      & (uv[:, 1] >= y0-margin_px) & (uv[:, 1] <= y1+margin_px))
            if not inside.any():
                valid = np.zeros_like(valid)
            else:
                ys, xs = np.nonzero(coarse)
                ys, xs = ys[inside], xs[inside]
                region = np.zeros_like(valid)
                region[max(0, ys.min()-stride-2):ys.max()+stride+3, max(0, xs.min()-stride-2):xs.max()+stride+3] = True
                valid &= region
        uv, ok = rgb_pixels(model, depth, valid)
        z = depth[valid].astype(np.float64)/1000
        points = model['rays'][valid]*z[:, None]
        self.uv, self.points = uv[ok], points[ok]
        self.depth_size, self.rgb_size = (width, height), model['rgb_size']

    def select(self, *, centre=None, radius_px=None, box=None):
        np = _np()
        if box is not None:
            x0, y0, x1, y1 = box
            keep = (self.uv[:, 0] >= x0) & (self.uv[:, 0] <= x1) & (self.uv[:, 1] >= y0) & (self.uv[:, 1] <= y1)
        else:
            keep = ((self.uv-np.asarray(centre, dtype=np.float64))**2).sum(axis=1) <= radius_px*radius_px
        return self.points[keep]

    def lift(self, u, v, *, radius_px=PATCH_RADIUS_PX, min_support=3):
        """Front-surface median point near one RGB pixel, or an explicit refusal."""
        return self._summarize(self.select(centre=(u, v), radius_px=radius_px), min_support)

    def lift_box(self, box, *, min_support=20, layer='front'):
        """layer 'front': a thin front surface (faces); 'foreground': everything
        before the first depth gap (object boxes)."""
        return self._summarize(self.select(box=box), min_support, layer)

    @staticmethod
    def _summarize(points, min_support, layer='front'):
        np = _np()
        if len(points) < min_support:
            return {'status': 'unknown', 'reason': 'no_depth_support', 'support': int(len(points))}
        front = front_cluster(points) if layer == 'front' else foreground_layer(points)
        if len(front) < min_support:
            return {'status': 'unknown', 'reason': 'no_front_surface', 'support': int(len(front))}
        centre = np.median(front, axis=0)
        return {'status': 'measured', 'xyz': [float(c) for c in centre], 'support': int(len(front)),
                'spread_m': float(np.max(np.linalg.norm(front-centre, axis=1))), 'points': front}


def kabsch(source, target):
    """Proper rotation R and t minimizing |R source + t - target| (rows are points)."""
    np = _np()
    source, target = np.asarray(source, dtype=np.float64), np.asarray(target, dtype=np.float64)
    cs, ct = source.mean(axis=0), target.mean(axis=0)
    u, _, vt = np.linalg.svd((source-cs).T @ (target-ct))
    d = 1.0 if np.linalg.det(vt.T @ u.T) >= 0 else -1.0
    rotation = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
    return rotation, ct-rotation @ cs


def fit_head(points, *, up_hint=(0.0, -1.0, 0.0)):
    """Rigid template fit to lifted face joints {joint_id: xyz (camera)}.

    Needs the nose, at least one ear and three joints: the nose-to-ear offset
    is what carries yaw; eyes alone sit too close to the nose in depth.
    """
    np = _np()
    ids = [j for j in FACE if j in points]
    if NOSE not in points or not ({LEFT_EAR, RIGHT_EAR} & set(ids)) or len(ids) < 3:
        return {'status': 'unknown', 'reason': 'needs_nose_ear_and_three_face_joints', 'joints': ids}
    template = np.array([HEAD_TEMPLATE[j] for j in ids])
    observed = np.array([points[j] for j in ids], dtype=np.float64)
    rotation, translation = kabsch(template, observed)
    residuals = np.linalg.norm(template @ rotation.T+translation-observed, axis=1)
    rms = float(np.sqrt(np.mean(residuals**2)))
    forward, up = rotation @ np.array([0.0, 1.0, 0.0]), rotation @ np.array([0.0, 0.0, 1.0])
    result = {'centre': [float(v) for v in translation], 'forward': [float(v) for v in forward],
              'head_up': [float(v) for v in up], 'rms_m': rms, 'joints': ids,
              'support': 'minimal' if len(ids) == 3 else 'overdetermined'}
    if rms > FIT_RMS_MAX_M:
        return {**result, 'status': 'unknown', 'reason': 'template_residual_too_high'}
    # A head does not roll 60 degrees, and a visible nose is not facing away.
    if _angle_deg(up, up_hint) > 60:
        return {**result, 'status': 'unknown', 'reason': 'implausible_head_roll'}
    toward_camera = -np.asarray(translation)
    if _angle_deg(forward, toward_camera) > 110:
        return {**result, 'status': 'unknown', 'reason': 'implausible_facing_away'}
    return {**result, 'status': 'estimated'}


def _horizontal(v, up):
    np = _np()
    v = np.asarray(v, dtype=np.float64)
    return _unit(v-np.dot(v, up)*up)


def prior_forward(pose, head_xyz, *, up=(0.0, -1.0, 0.0)):
    """Camera-frame forward from the calibrated 2D yaw/pitch measures, or None.

    Yaw rotates the head-to-camera horizontal direction toward image right
    (positive ratio: nose right of the ears); pitch tilts it about the room up.
    """
    np = _np()
    try:
        from jev_semantics import head_measures
    except ImportError:
        return None
    support, yaw, pitch = head_measures(pose)
    if yaw is None:
        return None
    up = _unit(up)
    toward = _horizontal(-np.asarray(head_xyz, dtype=np.float64), up)
    image_right = _horizontal([1.0, 0.0, 0.0], up)
    if toward is None or image_right is None:
        return None
    yaw_rad = math.radians(max(-90.0, min(90.0, yaw*YAW_DEG_PER_RATIO)))
    pitch_rad = 0.0 if pitch is None else math.radians(max(-60.0, min(30.0, (pitch-PITCH_LEVEL_RATIO)*PITCH_DEG_PER_RATIO)))
    flat = _unit(math.cos(yaw_rad)*toward+math.sin(yaw_rad)*image_right)
    forward = math.cos(pitch_rad)*flat+math.sin(pitch_rad)*up
    return {'forward': [float(v) for v in forward], 'support': support,
            'yaw_deg': math.degrees(yaw_rad), 'pitch_deg': math.degrees(pitch_rad)}


def face_box(joints, *, grow=0.3, minimum_px=8.0):
    face = [joints[j] for j in FACE if j in joints]
    if not face:
        return None
    xs, ys = [p[0] for p in face], [p[1] for p in face]
    w, h = max(xs)-min(xs), max(ys)-min(ys)
    gx, gy = max(minimum_px, w*grow), max(minimum_px, h*grow)
    return (min(xs)-gx, min(ys)-gy, max(xs)+gx, max(ys)+gy)


def estimate_head(pose, lifter, *, up=(0.0, -1.0, 0.0)):
    """Head position and forward direction in the camera frame for one pose.

    up is the room's up direction in camera coordinates (from the map's
    registration); the default assumes a level camera.
    """
    np = _np()
    up = _unit(up)
    joints = resolve_ears(joints_of(pose))
    box = face_box(joints)
    if box is None:
        return {'status': 'unknown', 'reason': 'no_face_joints', 'provenance': dict(PROVENANCE)}
    patch = lifter.lift_box(box, min_support=6)
    lifted, refused = {}, {}
    for joint in FACE:
        if joint not in joints:
            continue
        sample = lifter.lift(*joints[joint])
        if sample['status'] != 'measured':
            refused[joint] = sample['reason']
        elif patch['status'] == 'measured' and abs(sample['xyz'][2]-patch['xyz'][2]) > FACE_DEPTH_TOLERANCE_M:
            refused[joint] = 'off_face_depth'
        else:
            lifted[joint] = sample['xyz']
    fit = fit_head(lifted, up_hint=up) if lifted else {'status': 'unknown', 'reason': 'no_lifted_face_joints'}
    if fit['status'] == 'estimated':
        head = np.asarray(fit['centre'])
    elif patch['status'] == 'measured' or lifted:
        surface = np.asarray(patch['xyz']) if patch['status'] == 'measured' else np.median(np.array(list(lifted.values())), axis=0)
        head = surface+SURFACE_TO_CENTRE_M*_unit(surface)
    else:
        return {'status': 'unknown', 'reason': 'no_face_depth', 'refused_joints': refused, 'provenance': dict(PROVENANCE)}
    prior = prior_forward(pose, head, up=up)
    result = {'head_xyz': [float(v) for v in head], 'lifted_joints': sorted(lifted), 'refused_joints': refused,
              'fit': {k: fit[k] for k in ('status', 'reason', 'rms_m', 'support') if k in fit},
              'prior': prior, 'provenance': dict(PROVENANCE)}
    if fit['status'] == 'estimated' and prior is not None:
        agreement = _angle_deg(fit['forward'], prior['forward'])
        result['agreement_deg'] = agreement
        if agreement <= AGREE_DEG:
            return {**result, 'status': 'estimated', 'forward': fit['forward'], 'method': 'face_template_3d', 'fusion': 'agrees'}
        return {**result, 'status': 'estimated', 'forward': prior['forward'], 'method': 'yaw_prior_2d', 'fusion': 'conflict_prior_used'}
    if fit['status'] == 'estimated':
        return {**result, 'status': 'estimated', 'forward': fit['forward'], 'method': 'face_template_3d', 'fusion': '3d_only'}
    if prior is not None:
        return {**result, 'status': 'estimated', 'forward': prior['forward'], 'method': 'yaw_prior_2d', 'fusion': 'prior_only'}
    return {**result, 'status': 'unknown', 'reason': 'no_head_direction'}


def to_room(head, matrix):
    """Head position and forward in the room frame through a 4x4 camera->room matrix."""
    np = _np()
    m = np.asarray(matrix, dtype=np.float64)
    if m.shape != (4, 4) or head.get('status') != 'estimated':
        raise ValueError('estimated head and 4x4 registration required')
    position = m[:3, :3] @ np.asarray(head['head_xyz'])+m[:3, 3]
    forward = _unit(m[:3, :3] @ np.asarray(head['forward']))
    return {'head_m': [float(v) for v in position], 'forward': [float(v) for v in forward],
            'pitch_deg': math.degrees(math.asin(max(-1.0, min(1.0, float(forward[2])))))}


def anchor_word(anchor):
    """The looking_at word of a map anchor: its category, else its label without a _N suffix."""
    word = anchor.get('category') or anchor.get('label') or anchor.get('name') or anchor.get('id')
    head, _, tail = str(word).rpartition('_')
    return head if head and tail.isdigit() else str(word)


def anchor_radius(anchor):
    if isinstance(anchor.get('radius_m'), (int, float)):
        return float(anchor['radius_m'])
    size = anchor.get('size_m')
    if isinstance(size, list) and size:
        return 0.5*math.sqrt(sum(float(v)**2 for v in size))
    return DEFAULT_RADIUS_M


def look_target(head_room, anchors, *, cone_deg=CONE_DEG, margin_deg=MARGIN_DEG, down_pitch_deg=DOWN_PITCH_DEG):
    """Cast the head cone against named anchors (room frame).

    Effective angle = off-axis angle minus the anchor's angular radius. The
    best anchor within the cone wins unless an anchor of another group is
    within margin_deg of it (then 'unknown', reason 'ambiguous'). No hit:
    'lap_or_hands' when pitched below down_pitch_deg, else 'elsewhere'.
    Anchors containing the head (the chair you sit in) are skipped.
    """
    np = _np()
    head, forward = np.asarray(head_room['head_m']), np.asarray(head_room['forward'])
    candidates = []
    for anchor in anchors:
        offset = np.asarray(anchor['pos'], dtype=np.float64)-head
        distance, radius = float(np.linalg.norm(offset)), anchor_radius(anchor)
        if distance <= radius+CONTAINS_M:
            continue
        off_axis = _angle_deg(forward, offset)
        angular = math.degrees(math.atan2(radius, distance))
        candidates.append({'anchor': anchor_word(anchor), 'id': anchor.get('id'), 'off_axis_deg': off_axis,
                           'effective_deg': max(0.0, off_axis-angular), 'distance_m': distance})
    candidates.sort(key=lambda c: (c['effective_deg'], c['off_axis_deg']))
    top = [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in c.items()} for c in candidates[:3]]
    inside = [c for c in candidates if c['effective_deg'] <= cone_deg]
    if not inside:
        word = 'lap_or_hands' if head_room['pitch_deg'] <= down_pitch_deg else 'elsewhere'
        return {'looking_at': word, 'reason': 'no_anchor_in_cone', 'candidates': top}
    best = inside[0]
    group = GROUPS.get(best['anchor'], best['anchor'])
    rivals = [c for c in inside[1:] if c['effective_deg']-best['effective_deg'] < margin_deg
              and GROUPS.get(c['anchor'], c['anchor']) != group]
    if rivals:
        return {'looking_at': 'unknown', 'reason': 'ambiguous', 'candidates': top}
    return {'looking_at': best['anchor'], 'reason': 'best_anchor_in_cone', 'candidates': top,
            'margin_deg': round((inside[1]['effective_deg']-best['effective_deg']) if len(inside) > 1 else cone_deg, 2)}


class Stability:
    """'steady' when one target holds >= share of the last window_s seconds and the
    head direction spread stays small; 'shifting' otherwise; 'insufficient' below
    min_samples."""

    def __init__(self, *, window_s=3.0, min_samples=3, share=0.75, spread_deg=12.0):
        self.window_s, self.min_samples, self.share, self.spread_deg = window_s, min_samples, share, spread_deg
        self.samples = []

    def update(self, t, target, forward=None):
        np = _np()
        if self.samples and t < self.samples[-1][0]:
            raise ValueError('out-of-order head sample')
        self.samples.append((t, target, forward))
        self.samples = [s for s in self.samples if t-s[0] <= self.window_s]
        if len(self.samples) < self.min_samples:
            return 'insufficient'
        targets = [s[1] for s in self.samples]
        modal = max(set(targets), key=targets.count)
        if targets.count(modal)/len(targets) < self.share or modal == 'unknown':
            return 'shifting'
        directions = [_unit(s[2]) for s in self.samples if s[2] is not None]
        if len(directions) >= 2:
            mean = _unit(np.sum(directions, axis=0))
            if mean is None or float(np.median([_angle_deg(d, mean) for d in directions])) > self.spread_deg:
                return 'shifting'
        return 'steady'


def room_up_in_camera(matrix):
    """The room's +Z expressed in camera coordinates (rotation transpose)."""
    np = _np()
    return [float(v) for v in np.asarray(matrix, dtype=np.float64)[:3, :3].T @ np.array([0.0, 0.0, 1.0])]


def looking_at(pose, depth_mm, model, matrix, anchors, *, stability=None, t=None):
    """One frame end to end: {'looking_at', 'looking_at_stability', 'head', 'target'}."""
    joints = joints_of(pose)
    box = face_box(joints)
    lifter = DepthLifter(depth_mm, model, rgb_roi=box) if box is not None else None
    head = estimate_head(pose, lifter, up=room_up_in_camera(matrix)) if lifter is not None else {
        'status': 'unknown', 'reason': 'no_face_joints', 'provenance': dict(PROVENANCE)}
    if head['status'] != 'estimated':
        target = {'looking_at': 'unknown', 'reason': head.get('reason')}
        room = None
    else:
        room = to_room(head, matrix)
        target = look_target(room, anchors)
    state = None
    if stability is not None and t is not None:
        state = stability.update(t, target['looking_at'], room['forward'] if room else None)
    return {'looking_at': target['looking_at'], 'looking_at_stability': state, 'head': head, 'room': room, 'target': target}
