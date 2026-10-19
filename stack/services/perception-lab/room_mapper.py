"""Den room-mapping CLI (plan section 11, stage 2). Run on the Victoria Jetson over SSH.

Operator tool, not part of the Lab controller or its release. It reads the
local camera owner (127.0.0.1:8090 /frame.npz and /calibration, at most 4
bundles/s, which never changes the camera) and, for pointing anchors, the Lab
ledger read-only. Snapshots, the scan-derived mesh map, the annotated JPEG and
the site maps stay in a private local directory; nothing is sent anywhere.

Workflow (docs/DEN-ROOM-MAPPING-RUNBOOK.md):
  capture          median N depth bundles into a private snapshot (people out of view)
  floor            RANSAC floor plane -> camera height, tilt, roll
  import-mesh      Scaniverse GLB/OBJ -> den frame (z_up_metric_floor0) surface samples
  measure          distance between two viewer-picked points (tape scale check)
  register         point-to-plane ICP of the snapshot against the mesh -> camera extrinsics
  anchor NAME      one anchor: --point (mesh), --pixel/--box (RGB preview), --pointing, --measured
  anchors-import   anchors labelled on the mesh (JSON)
  review           anchors in room coordinates (metres)
  grid             annotated JPEG with a pixel grid and projected anchors (kept local)
  save             versioned site map (revision + SHA, the format room_context binds)
  check-mount      fresh snapshot vs the registered mount signature
  status           what is done and what is next

Camera frame: OAK rectified left (x right, y down, z forward, metres).
Room frame: z_up_metric_floor0 (the mesh frame, or with no mesh the floor-only
frame: origin on the floor under the camera, +Y the camera heading).
"""
import argparse
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time

import room_mesh
import head3d

SITE_ID, CAMERA_ID, MAP_ID = 'victoria', 'den', 'victoria-den'
DEFAULT_DIR = '/home/engineeredlightingllm/perception-lab/private/room-map'
CAMERA_ENV = '/home/engineeredlightingllm/victoria-camera/private/camera.env'
LEDGER = '/home/engineeredlightingllm/perception-lab/data/lab.sqlite3'
STATE_SCHEMA = 'den-room-map-state-1'
SITE_MAP_SCHEMA = 'den-site-map-1'
MIN_BUNDLE_INTERVAL_S = 0.25        # the camera owner serves at most 5 bundles/s
# Supported anchor names (a numbered suffix such as window_2 adds another).
ANCHOR_RADIUS_M = {'monitor': 0.35, 'desk': 0.6, 'keyboard_area': 0.3, 'reading_chair': 0.5, 'desk_chair': 0.4,
                   'sofa': 0.9, 'window': 0.6, 'painting': 0.45, 'fireplace': 0.6, 'door': 0.5, 'bookshelf': 0.5,
                   'table': 0.5}
# Labeller points that guide registration instead of naming an anchor.
CAMERA_HINTS = ('oak_camera', 'oak_looks_at')
CHECK_TOLERANCE_M = 0.10
HAND_BEYOND_WRIST_M = 0.08
POINTING_PAIR_S = 0.15
FLOOR_THRESHOLD_M = 0.02


def _np():
    import numpy as np
    return np


def now_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def category_of(name):
    base, _, tail = name.rpartition('_')
    category = base if base and tail.isdigit() and base in ANCHOR_RADIUS_M else name
    if category not in ANCHOR_RADIUS_M:
        raise ValueError('unsupported anchor name %r (supported: %s, optionally with _2, _3 ...)'
                         % (name, ', '.join(sorted(ANCHOR_RADIUS_M))))
    return category


def parse_floats(text, count=None):
    values = [float(v) for v in str(text).replace(' ', '').split(',') if v != '']
    if count is not None and len(values) not in (count if isinstance(count, tuple) else (count,)):
        raise ValueError('expected %s comma-separated numbers, got %r' % (count, text))
    if not all(math.isfinite(v) for v in values):
        raise ValueError('finite numbers required')
    return values


# ---------------------------------------------------------------- private files

class Workspace:
    def __init__(self, root):
        self.root = Path(root)

    def ensure(self):
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        return self

    def path(self, name):
        return self.root/name

    def write_bytes(self, name, data):
        path = self.path(name)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as target:
            target.write(data)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return path

    def write_json(self, name, value):
        return self.write_bytes(name, (json.dumps(value, indent=2, sort_keys=True)+'\n').encode())

    def load_state(self):
        path = self.path('state.json')
        if not path.is_file():
            return {'schema': STATE_SCHEMA, 'site_id': SITE_ID, 'camera_id': CAMERA_ID, 'map_id': MAP_ID,
                    'created_at': now_iso(), 'snapshots': {}, 'anchors': {}, 'pending_rays': {}, 'checks': [],
                    'saved': []}
        state = json.loads(path.read_text(encoding='utf-8'))
        if state.get('schema') != STATE_SCHEMA:
            raise ValueError('unexpected room-map state schema')
        return state

    def save_state(self, state):
        state['updated_at'] = now_iso()
        self.write_json('state.json', state)


# ---------------------------------------------------------------- camera bundles

def camera_token(env_path):
    for line in Path(env_path).read_text(encoding='utf-8').splitlines():
        if line.startswith('VICTORIA_CAMERA_TOKEN='):
            return line.split('=', 1)[1].strip().strip('"').strip("'")
    raise ValueError('VICTORIA_CAMERA_TOKEN missing from the camera env file')


class CameraReader:
    """Rate-limited reads of the local camera owner's /frame.npz and /calibration."""

    def __init__(self, env_path=CAMERA_ENV, client=None):
        from preview_client import LocalPreviewClient
        self.client = client or LocalPreviewClient(lambda: camera_token(env_path))
        self.last = 0.0

    def calibration(self):
        data, _ = self.client._read('/calibration')
        value = json.loads(data)
        if not isinstance(value, dict) or value.get('depth_alignment') != 'rectified_left':
            raise ValueError('unexpected camera calibration')
        return value

    def bundle(self):
        wait = self.last+MIN_BUNDLE_INTERVAL_S-time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        data, _ = self.client._read('/frame.npz')
        return decode_bundle(data)


def decode_bundle(data):
    np = _np()
    with np.load(io.BytesIO(data), allow_pickle=False) as archive:
        if set(archive.files) != {'depth_mm', 'left', 'right', 'rgb_nv12', 'metadata_json'}:
            raise ValueError('unexpected OAK bundle members')
        bundle = {name: archive[name] for name in ('depth_mm', 'rgb_nv12')}
        meta = json.loads(archive['metadata_json'].tobytes())
    if (bundle['depth_mm'].shape != (400, 640) or bundle['depth_mm'].dtype != np.uint16
            or bundle['rgb_nv12'].shape != (540, 640) or meta.get('depth_alignment') != 'rectified_left'
            or meta.get('depth_units') != 'millimetres' or meta.get('invalid_depth') != 0):
        raise ValueError('unexpected OAK bundle shape or semantics')
    bundle['meta'] = meta
    return bundle


def median_depth(stack, *, min_valid_fraction=0.5):
    """Per-pixel median of valid depth over frames; a pixel valid in fewer than
    min_valid_fraction of frames stays a hole. Returns (depth uint16, valid
    count uint8, median absolute deviation mm uint16)."""
    np = _np()
    stack = np.asarray(stack)
    valid = stack > 0
    count = valid.sum(axis=0)
    values = np.where(valid, stack.astype(np.float32), np.nan)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        median = np.nanmedian(values, axis=0)
        mad = np.nanmedian(np.abs(values-median[None]), axis=0)
    keep = count >= max(1, math.ceil(min_valid_fraction*len(stack)))
    depth = np.where(keep, np.nan_to_num(median), 0).round().astype(np.uint16)
    return depth, count.astype(np.uint8), np.where(keep, np.nan_to_num(mad), 0).round().astype(np.uint16)


def nv12_to_rgb(nv12, width=640, height=360):
    """BT.601 limited-range NV12 (Y plane then interleaved UV) -> uint8 RGB."""
    np = _np()
    y = nv12[:height].astype(np.float32)
    uv = nv12[height:height+height//2].reshape(height//2, width//2, 2).astype(np.float32)
    u = np.repeat(np.repeat(uv[:, :, 0], 2, axis=0), 2, axis=1)-128
    v = np.repeat(np.repeat(uv[:, :, 1], 2, axis=0), 2, axis=1)-128
    c = 1.164*(y-16)
    rgb = np.stack([c+1.596*v, c-0.392*u-0.813*v, c+2.017*u], axis=-1)
    return np.clip(rgb, 0, 255).astype(np.uint8)


def calibration_id(calibration):
    try:
        from bundle_ingest import calibration_content_id
        return calibration_content_id(calibration)
    except ImportError:  # same digest as bundle_ingest, without its store imports
        from reasoning import digest
        return digest(['oak-calibration-content-v1', {k: v for k, v in calibration.items() if k != 'session'}])


_MODELS = {}


def rgbd_model(meta, calibration):
    """depth_association camera model from bundle metadata and EEPROM extrinsics.

    Per-frame intrinsics are the reference (the RGB camera is autofocus); the
    RGB-depth reprojection has no validation receipt yet, so this is the
    offline measuring mode.
    """
    from depth_association import camera_model, eeprom_rgb_from_rectified_left
    frames = meta['frames']
    key = json.dumps([frames['depth']['intrinsics'], frames['rgb']['intrinsics'],
                      frames['rgb'].get('source_distortion_coefficients'), calibration_id(calibration)])
    if key not in _MODELS:
        reference = {'id': calibration_id(calibration), 'mount_revision': 'room-map',
                     'rgb_from_rectified_left': eeprom_rgb_from_rectified_left(calibration['eeprom']),
                     'reference_intrinsics': {'rectified_left': frames['depth']['intrinsics'], 'rgb': frames['rgb']['intrinsics']}}
        _MODELS.clear()
        _MODELS[key] = camera_model(meta, reference, measuring_reprojection=True)
    return _MODELS[key]


def depth_points(depth_mm, intrinsics, *, stride=2):
    """Camera-frame points (metres) of valid depth pixels on a stride grid."""
    np = _np()
    depth = np.asarray(depth_mm)[::stride, ::stride].astype(np.float64)/1000
    k = np.asarray(intrinsics, dtype=np.float64)
    v, u = np.mgrid[0:depth.shape[0], 0:depth.shape[1]]
    u, v = u*stride, v*stride
    valid = depth > 0
    z = depth[valid]
    return np.stack([(u[valid]-k[0, 2])*z/k[0, 0], (v[valid]-k[1, 2])*z/k[1, 1], z], axis=1)


# ---------------------------------------------------------------- floor

def fit_floor(points, *, seed=0, iterations=400, threshold_m=FLOOR_THRESHOLD_M, max_tilt_deg=50.0,
              min_support=None):
    """Floor plane from camera points: the lowest well-supported horizontal plane.

    RANSAC finds the dominant near-horizontal orientation (normal within
    max_tilt_deg of camera -y); heights below the camera along that normal are
    histogrammed and the lowest dense layer is the floor (seats and table tops
    are horizontal too), then least squares refines it. The normal points up
    (toward the camera side): n.p + d = 0 with d = camera height.
    """
    np = _np()
    points = np.asarray(points, dtype=np.float64)
    if len(points) < 200:
        raise ValueError('too few depth points for a floor fit')
    rng = np.random.default_rng(seed)
    prior = np.array([0.0, -1.0, 0.0])
    cos_limit = math.cos(math.radians(max_tilt_deg))
    sample = points[rng.choice(len(points), size=min(len(points), 20000), replace=False)]
    best = None
    for _ in range(iterations):
        p0, p1, p2 = sample[rng.choice(len(sample), size=3, replace=False)]
        n = np.cross(p1-p0, p2-p0)
        norm = np.linalg.norm(n)
        if norm < 1e-9:
            continue
        n /= norm
        if n @ prior < 0:
            n = -n
        if n @ prior < cos_limit:
            continue
        d = -float(n @ p0)
        if not 0.2 < d < 3.5:
            continue
        count = int((np.abs(sample @ n+d) < threshold_m).sum())
        if best is None or count > best[0]:
            best = (count, n)
    if best is None:
        raise ValueError('no near-horizontal plane below the camera')
    normal = best[1]
    minimum = min_support or max(300, int(0.02*len(points)))
    for _ in range(3):
        below = -(points @ normal)
        histogram, edges = np.histogram(below, bins=np.arange(0.2, 3.5, 0.01))
        dense = np.convolve(histogram, np.ones(3), mode='same') >= minimum
        if not dense.any():
            raise ValueError('no dense horizontal layer below the camera')
        height = float(edges[np.flatnonzero(dense)[-1]]+0.005)
        layer = points[np.abs(below-height) < 0.04]
        centre = layer.mean(axis=0)
        normal = np.linalg.svd(layer-centre, full_matrices=False)[2][-1]
        if normal @ prior < 0:
            normal = -normal
        d = -float(normal @ centre)
        inliers = np.abs(points @ normal+d) < threshold_m
        centre = points[inliers].mean(axis=0)
        normal = np.linalg.svd(points[inliers]-centre, full_matrices=False)[2][-1]
        if normal @ prior < 0:
            normal = -normal
    d = -float(normal @ centre)
    residual = points @ normal+d
    inliers = np.abs(residual) < threshold_m
    return {'normal_camera': [float(v) for v in normal], 'camera_height_m': d,
            'tilt_down_deg': math.degrees(math.asin(max(-1.0, min(1.0, -normal[2])))),
            'roll_deg': math.degrees(math.asin(max(-1.0, min(1.0, normal[0])))),
            'inliers': int(inliers.sum()), 'inlier_fraction': float(inliers.mean()),
            'rms_m': float(np.sqrt(np.mean(residual[inliers]**2))), 'points': int(len(points))}


def floor_transform(floor):
    """Floor-only camera -> room matrix: origin on the floor under the camera, +Y the heading."""
    np = _np()
    matrix = np.eye(4)
    matrix[:3, :3] = room_mesh.level_rotation(floor['normal_camera'])
    matrix[:3, 3] = [0.0, 0.0, floor['camera_height_m']]
    return matrix


# ---------------------------------------------------------------- snapshots

def save_snapshot(workspace, bundles, calibration, *, snapshot_id=None):
    np = _np()
    depth, count, mad = median_depth([b['depth_mm'] for b in bundles])
    last = bundles[-1]
    snapshot_id = snapshot_id or time.strftime('%Y%m%dT%H%M%S')
    buffer = io.BytesIO()
    np.savez_compressed(buffer, depth_mm=depth, valid_count=count, mad_mm=mad, rgb_nv12=last['rgb_nv12'],
                        metadata_json=np.frombuffer(json.dumps(last['meta']).encode(), dtype=np.uint8),
                        calibration_json=np.frombuffer(json.dumps(calibration).encode(), dtype=np.uint8))
    name = 'snapshot-%s.npz' % snapshot_id
    workspace.write_bytes(name, buffer.getvalue())
    valid = depth > 0
    near = valid & (depth < 4000)
    moving = float((mad[near] > 50).mean()) if near.any() else 0.0
    return snapshot_id, {'file': name, 'frames': len(bundles), 'valid_fraction': float(valid.mean()),
                         'moving_fraction': moving, 'captured_at': now_iso(), 'capture_epoch': last['meta'].get('session'),
                         'calibration_content_id': calibration_id(calibration),
                         'median_depth_m': float(np.median(depth[valid]))/1000 if valid.any() else None}


def load_snapshot(workspace, state, snapshot_id=None):
    np = _np()
    snapshot_id = snapshot_id or state.get('latest_snapshot')
    if not snapshot_id or snapshot_id not in state.get('snapshots', {}):
        raise ValueError('no snapshot: run capture first')
    with np.load(workspace.path(state['snapshots'][snapshot_id]['file']), allow_pickle=False) as archive:
        snapshot = {k: archive[k] for k in ('depth_mm', 'valid_count', 'mad_mm', 'rgb_nv12')}
        snapshot['meta'] = json.loads(archive['metadata_json'].tobytes())
        snapshot['calibration'] = json.loads(archive['calibration_json'].tobytes())
    snapshot['id'] = snapshot_id
    return snapshot


# ---------------------------------------------------------------- anchors

def registration_matrix(state):
    """(4x4 camera -> room, kind) from the accepted mesh registration, else the floor."""
    np = _np()
    registration = state.get('registration')
    if registration and registration.get('status') == 'accepted':
        return np.asarray(registration['matrix'], dtype=np.float64), 'mesh_icp'
    if state.get('floor'):
        return floor_transform(state['floor']), 'floor_only'
    return None, None


def anchor_record(name, method, frame, xyz, *, radius_m=None, size_m=None, extent_points=None, **extra):
    category = category_of(name)
    record = {'name': name, 'category': category, 'method': method, 'frame': frame,
              'xyz': [float(v) for v in xyz], 'measured_at': now_iso(), **extra}
    if size_m is not None:
        record.update(shape='box', size_m=[max(0.02, float(v)) for v in size_m])
    elif extent_points is not None:
        record.update(shape='box', extent_points=[[round(float(c), 4) for c in p] for p in extent_points])
    else:
        record.update(shape='point', radius_m=float(radius_m or ANCHOR_RADIUS_M[category]))
    return record


def resolve_anchor(record, matrix):
    """Room-frame {pos, shape, radius_m | size_m} for one stored anchor."""
    np = _np()
    if record['frame'] == 'den':
        pos = np.asarray(record['xyz'], dtype=np.float64)
    else:
        if matrix is None:
            raise ValueError('camera-frame anchor %s needs a floor fit or registration' % record['name'])
        pos = room_mesh.apply(matrix, [record['xyz']])[0]
    resolved = {'pos': [round(float(v), 4) for v in pos], 'shape': record['shape']}
    if record.get('extent_points'):
        placed = room_mesh.apply(matrix, record['extent_points'])
        lo, hi = np.percentile(placed, 5, axis=0), np.percentile(placed, 95, axis=0)
        resolved.update(size_m=[round(max(0.02, float(v)), 3) for v in hi-lo])
    elif record.get('size_m'):
        resolved['size_m'] = list(record['size_m'])
    else:
        resolved['radius_m'] = record['radius_m']
    return resolved


def anchors_from_file(document, t_mesh):
    """Anchors labelled on the mesh: {"frame": scan|den|blender, "anchors": [
    {"name", "point": [x,y,z], "radius_m"?} | {"name", "points": [[x,y,z], ...]} |
    {"name", "box": {"min": [..], "max": [..]}}]}. Points and boxes become
    room-axis boxes (bounds of the converted points or corners)."""
    np = _np()
    frame = document.get('frame', 'scan')
    if frame not in room_mesh.VIEWER_FRAMES:
        raise ValueError('frame must be one of %s' % ', '.join(room_mesh.VIEWER_FRAMES))
    if frame != 'den' and t_mesh is None:
        raise ValueError('import the mesh first: scan/blender points need its T_mesh')
    records = []
    for entry in document.get('anchors') or []:
        name = entry.get('name')
        if str(name) in CAMERA_HINTS:
            continue  # registration hints, read by register --labels
        category_of(str(name))
        convert = lambda p: room_mesh.viewer_to_den(p, frame, t_mesh)
        if 'point' in entry:
            records.append(anchor_record(name, 'mesh', 'den', convert(entry['point']), radius_m=entry.get('radius_m')))
            continue
        if 'points' in entry and len(entry['points']) >= 2:
            corners = [convert(p) for p in entry['points']]
        elif 'box' in entry:
            lo, hi = entry['box']['min'], entry['box']['max']
            corners = [convert([x, y, z]) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        else:
            raise ValueError('anchor %s needs point, points (>=2) or box' % name)
        corners = np.asarray(corners)
        lo, hi = corners.min(axis=0), corners.max(axis=0)
        records.append(anchor_record(name, 'mesh', 'den', (lo+hi)/2, size_m=hi-lo))
    if not records:
        raise ValueError('no anchors in the file')
    return records


def triangulate(rays):
    """Least-squares point closest to several rays [(origin, direction)]."""
    np = _np()
    a, b = np.zeros((3, 3)), np.zeros(3)
    for origin, direction in rays:
        d = np.asarray(direction, dtype=np.float64)
        d /= np.linalg.norm(d)
        projector = np.eye(3)-np.outer(d, d)
        a += projector
        b += projector @ np.asarray(origin, dtype=np.float64)
    point = np.linalg.solve(a, b)
    distances = [float(np.linalg.norm((np.eye(3)-np.outer(d, d)/np.dot(d, d)) @ (point-np.asarray(o))))
                 for o, d in rays]
    angles = [head3d._angle_deg(rays[i][1], rays[j][1]) for i in range(len(rays)) for j in range(i+1, len(rays))]
    return point, distances, max(angles) if angles else 0.0


def select_wrist(joints, preference='auto'):
    """(wrist, elbow) joint ids: the named side, else the more extended arm."""
    sides = {'left': (head3d.LEFT_WRIST, head3d.LEFT_ELBOW, head3d.LEFT_SHOULDER),
             'right': (head3d.RIGHT_WRIST, head3d.RIGHT_ELBOW, head3d.RIGHT_SHOULDER)}
    options = [sides[preference]] if preference in sides else list(sides.values())
    best = None
    for wrist, elbow, shoulder in options:
        if wrist not in joints:
            continue
        reach = math.dist(joints[wrist], joints[shoulder]) if shoulder in joints else 0.0
        if best is None or reach > best[0]:
            best = (reach, wrist, elbow)
    return (best[1], best[2]) if best else (None, None)


# ---------------------------------------------------------------- ledger (read-only)

class LedgerReader:
    """Read-only Lab ledger view for recent pose outputs and their RGB frame clocks."""

    def __init__(self, path=LEDGER):
        self.db = sqlite3.connect('file:%s?mode=ro' % path, uri=True)
        self.db.row_factory = sqlite3.Row

    def pose_outputs(self, start, end, *, recent=20000):
        rows = self.db.execute("""SELECT id,payload FROM events WHERE kind='perception.output'
            AND id>(SELECT COALESCE(MAX(id),0)-? FROM events)
            AND json_extract(payload,'$.observed_at') BETWEEN ? AND ? ORDER BY id""", (recent, start, end)).fetchall()
        return [output for output in (self._output(row) for row in rows) if output]

    def last_event_id(self):
        return self.db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]

    def outputs_after(self, event_id):
        rows = self.db.execute("""SELECT id,payload FROM events WHERE id>? AND kind='perception.output'
            ORDER BY id""", (event_id,)).fetchall()
        return [output for output in (self._output(row) for row in rows) if output]

    def _output(self, row):
        payload = json.loads(row['payload'])
        poses = (payload.get('result') or {}).get('pose_instances') or []
        if not poses:
            return None
        frame = None
        for media_id in payload.get('media_ids') or []:
            meta_row = self.db.execute('SELECT metadata FROM media WHERE id=?', (media_id,)).fetchone()
            meta = json.loads(meta_row[0]) if meta_row else {}
            receipt = meta.get('source_timing_receipt') or {}
            if meta.get('stream') == 'rgb' and type(receipt.get('frame_device_ns')) is int:
                frame = {'capture_epoch': meta.get('capture_epoch'), 'device_s': receipt['frame_device_ns']/1e9,
                         'sequence': meta.get('sequence')}
        return {'event_id': row['id'], 'arm': payload.get('arm'), 'observed_at': payload.get('observed_at'),
                'poses': poses, 'people': sum(1 for o in (payload.get('result') or {}).get('objects') or []
                                              if isinstance(o, dict) and o.get('label') == 'person'),
                'frame': frame}

    def close(self):
        self.db.close()


def pair_bundle(frame, bundles, *, tolerance_s=POINTING_PAIR_S):
    """The bundle whose RGB device timestamp is nearest the output's RGB frame (same capture epoch)."""
    if not frame:
        return None, None
    best = None
    for bundle in bundles:
        meta = bundle['meta']
        stamp = ((meta.get('frames') or {}).get('rgb') or {}).get('device_timestamp_seconds')
        if meta.get('session') != frame['capture_epoch'] or not isinstance(stamp, (int, float)):
            continue
        skew = abs(stamp-frame['device_s'])
        if best is None or skew < best[0]:
            best = (skew, bundle)
    if best is None or best[0] > tolerance_s:
        return None, None if best is None else best[0]
    return best[1], best[0]


# ---------------------------------------------------------------- site map

def build_site_map(state, *, revision, note=None):
    """(document, binding) for the versioned site map; validated through room_context."""
    from reasoning import digest
    from room_context import apartment_map
    matrix, kind = registration_matrix(state)
    if matrix is None:
        raise ValueError('no registration: run floor (and register with a mesh) first')
    targets = []
    for name in sorted(state['anchors']):
        record = state['anchors'][name]
        resolved = resolve_anchor(record, matrix)
        target = {'id': 'anchor-'+name, 'name': name, 'category': record['category'], 'shape': resolved['shape'],
                  'pos': resolved['pos'], 'source': record['method']}
        target.update({k: resolved[k] for k in ('radius_m', 'size_m') if k in resolved})
        targets.append(target)
    if not targets:
        raise ValueError('no anchors to save')
    registration = state.get('registration') if kind == 'mesh_icp' else None
    mount = (registration or {}).get('mount_signature') or (state.get('floor') or {}).get('mount_signature')
    camera = {'source_frame': 'rectified_left', 'target_frame': room_mesh.FRAME, 'method': kind,
              'status': 'fitted', 'matrix': [[round(float(v), 6) for v in row] for row in matrix],
              'calibration_content_id': (registration or state.get('floor') or {}).get('calibration_content_id'),
              'mount_signature': mount}
    if registration:
        camera.update({k: registration.get(k) for k in ('rms_m', 'inlier_fraction', 'scale_check')},
                      mesh_sha256=(state.get('mesh') or {}).get('source_sha256'))
    model = {'revision': revision, 'targets': targets, 'zones': [],
             'meta': {'frame': room_mesh.FRAME, 'units': 'metres', 'source': 'room_mapper',
                      'site_id': SITE_ID, 'camera_id': CAMERA_ID, 'created_at': now_iso(),
                      'frame_origin': 'mesh_floor_min_corner' if kind == 'mesh_icp' else 'floor_under_camera',
                      'camera_registration': camera, **({'note': note} if note else {})}}
    binding = {'site_id': SITE_ID, 'map_id': MAP_ID, 'revision': str(revision), 'units': 'metres',
               'model_sha256': digest(model)}
    room_map = apartment_map(model, binding)
    return {'schema': SITE_MAP_SCHEMA, 'model': model, 'binding': binding}, room_map


def load_site_map(path):
    """(room_map from room_context.apartment_map, camera -> room matrix, document)."""
    from room_context import apartment_map
    document = json.loads(Path(path).read_text(encoding='utf-8'))
    if document.get('schema') != SITE_MAP_SCHEMA:
        raise ValueError('not a den site map')
    room_map = apartment_map(document['model'], document['binding'])
    matrix = document['model']['meta']['camera_registration']['matrix']
    return room_map, matrix, document


# ---------------------------------------------------------------- rendering

def project_room_points(points_room, matrix, model):
    """Room points -> RGB pixels ([u, v] or None when behind the camera)."""
    np = _np()
    from depth_association import project
    inverse = np.linalg.inv(np.asarray(matrix, dtype=np.float64))
    camera = room_mesh.apply(inverse, points_room)
    transform = model['rgb_from_left']
    rgb = camera @ transform[:3, :3].T+transform[:3, 3]
    uv, ok = project(rgb, model['rgb_k'], model['rgb_distortion'])
    return [[float(p[0]), float(p[1])] if good else None for p, good in zip(uv, ok)]


def render_grid(rgb, *, coverage=None, anchors=(), step=20, label_step=100, scale=2):
    """Annotated RGB (PIL image): coordinate grid in original 640x360 pixels,
    darkened where no depth pixel projects (pixel anchors cannot lift there),
    and projected anchors."""
    np = _np()
    from PIL import Image, ImageDraw
    image = rgb.astype(np.float32)
    if coverage is not None:
        image[~coverage] *= 0.45
    height, width = rgb.shape[:2]
    canvas = Image.fromarray(image.astype(np.uint8)).resize((width*scale, height*scale), Image.NEAREST)
    draw = ImageDraw.Draw(canvas, 'RGBA')
    for x in range(0, width+1, step):
        strong = x % label_step == 0
        draw.line([(x*scale, 0), (x*scale, height*scale)], fill=(255, 255, 0, 150 if strong else 55), width=1)
        if strong:
            draw.text((x*scale+3, 2), str(x), fill=(255, 255, 0, 255))
    for y in range(0, height+1, step):
        strong = y % label_step == 0
        draw.line([(0, y*scale), (width*scale, y*scale)], fill=(255, 255, 0, 150 if strong else 55), width=1)
        if strong:
            draw.text((3, y*scale+2), str(y), fill=(255, 255, 0, 255))
    for name, pixel in anchors:
        if pixel is None or not (0 <= pixel[0] < width and 0 <= pixel[1] < height):
            continue
        x, y = pixel[0]*scale, pixel[1]*scale
        draw.ellipse([x-7, y-7, x+7, y+7], outline=(0, 255, 255, 255), width=3)
        draw.text((x+10, y-8), name, fill=(0, 255, 255, 255))
    return canvas


# ---------------------------------------------------------------- commands

def _print(value):
    print(json.dumps(value, indent=2, sort_keys=True))


def cmd_capture(args, workspace, state):
    reader = CameraReader(args.camera_env)
    calibration = reader.calibration()
    bundles = []
    for _ in range(args.frames):
        bundle = reader.bundle()
        if bundle['meta'].get('session') != calibration.get('session'):
            raise ValueError('camera restarted during capture; run capture again')
        bundles.append(bundle)
    snapshot_id, summary = save_snapshot(workspace, bundles, calibration)
    state['snapshots'][snapshot_id] = summary
    state['latest_snapshot'] = snapshot_id
    workspace.save_state(state)
    print('snapshot %s: %d frames, %.0f%% valid depth, median %.2f m' % (
        snapshot_id, summary['frames'], 100*summary['valid_fraction'], summary['median_depth_m'] or 0))
    if summary['moving_fraction'] > 0.02:
        print('WARNING: %.1f%% of near pixels changed during capture (someone moving?). Capture again with the view still.'
              % (100*summary['moving_fraction']))


def cmd_floor(args, workspace, state):
    snapshot = load_snapshot(workspace, state, args.snapshot)
    points = depth_points(snapshot['depth_mm'], snapshot['meta']['frames']['depth']['intrinsics'])
    floor = fit_floor(points)
    from spatial_validation import mount_signature
    floor.update(snapshot=snapshot['id'], fitted_at=now_iso(), mount_signature=mount_signature(snapshot['depth_mm']),
                 calibration_content_id=calibration_id(snapshot['calibration']))
    state['floor'] = floor
    workspace.save_state(state)
    print('floor: camera height %.3f m, tilt down %.1f deg, roll %.1f deg; %d inliers (%.0f%% of points), rms %.1f mm'
          % (floor['camera_height_m'], floor['tilt_down_deg'], floor['roll_deg'], floor['inliers'],
             100*floor['inlier_fraction'], 1000*floor['rms_m']))
    if floor['inlier_fraction'] < 0.05:
        print('WARNING: little floor in view; check the height against a tape (camera lens to floor).')


def cmd_import_mesh(args, workspace, state):
    source = Path(args.mesh)
    vertices, faces = room_mesh.load_mesh(source)
    if args.frame_json:
        frame = shared_frame(json.loads(Path(args.frame_json).read_text(encoding='utf-8')), vertices)
    else:
        frame = room_mesh.scan_frame(vertices, faces, up=args.up)
    placed = room_mesh.apply(frame['T_mesh'], vertices)
    points, normals = room_mesh.sample_surface(placed, faces, spacing=args.spacing)
    sha = room_mesh.sha256_file(source)
    buffer = io.BytesIO()
    meta = room_mesh.save_mesh_map(buffer, points, normals, frame, source_name=source.name, source_sha256=sha,
                                   spacing=args.spacing)
    workspace.write_bytes('den-mesh.npz', buffer.getvalue())
    workspace.write_json('den-frame.json', {k: frame[k] for k in ('T_mesh', 'floor_rms_mm', 'extents_m', 'source_up', 'frame')}
                         | {'source_sha256': sha, 'floor_footprint_m': frame['floor_footprint_m']})
    if args.export_obj:
        path = workspace.path('den-frame.obj')
        room_mesh.write_obj(path, placed, faces)
        os.chmod(path, 0o600)
    state['mesh'] = {'file': 'den-mesh.npz', 'source_name': source.name, 'source_sha256': sha,
                     'T_mesh': frame['T_mesh'], 'imported_at': now_iso(), 'vertices': int(len(vertices)),
                     'faces': int(len(faces)), 'samples': meta['samples'], **{k: frame[k] for k in (
                         'floor_rms_mm', 'floor_area_m2', 'floor_footprint_m', 'extents_m', 'z_range_m', 'source_up')}}
    if state.get('registration'):
        state.setdefault('superseded', []).append(state.pop('registration'))
        print('NOTE: a new mesh supersedes the previous registration; run register again.')
    workspace.save_state(state)
    print('mesh %s: %d vertices, %d faces -> %d surface samples at %.0f mm' % (
        source.name, len(vertices), len(faces), meta['samples'], 1000*args.spacing))
    if frame.get('floor_footprint_m'):
        print('den frame: floor rms %.1f mm, floor footprint %.2f x %.2f m, extents %s m, ceiling ~%.2f m'
              % (frame['floor_rms_mm'], frame['floor_footprint_m'][0], frame['floor_footprint_m'][1],
                 frame['extents_m'], frame['z_range_m'][1]))
    else:
        print('shared frame from %s: extents %s m, z range %s m' % (args.frame_json, frame['extents_m'], frame['z_range_m']))


def shared_frame(document, vertices):
    """Reuse an existing scan registration (the Apartment pipeline's frame.json /
    registration.json shape: T_mesh 4x4, frame z_up_metric_floor0) so the den
    map and that view share one coordinate system. Read-only."""
    np = _np()
    matrix = np.asarray(document.get('T_mesh'), dtype=np.float64)
    if document.get('frame') != room_mesh.FRAME or matrix.shape != (4, 4) or not np.all(np.isfinite(matrix)):
        raise ValueError('frame JSON needs T_mesh (4x4) and frame z_up_metric_floor0')
    rotation = matrix[:3, :3]
    if np.abs(rotation.T @ rotation-np.eye(3)).max() > 1e-4 or abs(np.linalg.det(rotation)-1) > 1e-4:
        raise ValueError('T_mesh must be a rigid metre transform')
    placed = room_mesh.apply(matrix, vertices)
    return {'T_mesh': matrix.tolist(), 'frame': room_mesh.FRAME, 'source_up': document.get('source_up', 'from_frame_json'),
            'floor_rms_mm': document.get('floor_rms_mm'), 'floor_area_m2': None, 'floor_footprint_m': None,
            'extents_m': [round(float(v), 3) for v in placed.max(axis=0)-placed.min(axis=0)],
            'z_range_m': [round(float(placed[:, 2].min()), 3), round(float(placed[:, 2].max()), 3)]}


def cmd_measure(args, workspace, state):
    a, b = parse_floats(args.from_point, 3), parse_floats(args.to_point, 3)
    if args.frame == 'blender':  # rigid either way; converted for clarity only
        a, b = [a[0], a[2], -a[1]], [b[0], b[2], -b[1]]
    distance = math.dist(a, b)
    line = 'distance %.3f m' % distance
    if args.tape:
        error = (distance-args.tape)/args.tape
        line += '; tape %.3f m; error %+.1f%% -> %s' % (args.tape, 100*error, 'PASS' if abs(error) <= 0.02 else 'CHECK SCALE')
    print(line)


def _viewer_point(args_frame, text, state):
    values = parse_floats(text, (2, 3))
    t_mesh = (state.get('mesh') or {}).get('T_mesh')
    if args_frame != 'den' and t_mesh is None:
        raise ValueError('import the mesh first, or give den coordinates with --frame den')
    if len(values) == 2:
        if args_frame != 'den':
            raise ValueError('give x,y,z for scan/blender points')
        values.append(0.0)
    return room_mesh.viewer_to_den(values, args_frame, t_mesh)


def cmd_register(args, workspace, state):
    if not state.get('mesh'):
        raise ValueError('import the mesh first')
    snapshot = load_snapshot(workspace, state, args.snapshot)
    points, normals, mesh_meta = room_mesh.load_mesh_map(workspace.path(state['mesh']['file']))
    source = depth_points(snapshot['depth_mm'], snapshot['meta']['frames']['depth']['intrinsics'])
    if args.labels:
        # The labeller's oak_camera / oak_looks_at points (same frame as the file).
        document = json.loads(Path(args.labels).read_text(encoding='utf-8'))
        hints = {e.get('name'): e.get('point') for e in document.get('anchors') or [] if e.get('name') in CAMERA_HINTS}
        if not hints.get('oak_camera'):
            raise ValueError('labels file has no oak_camera point')
        args.frame = document.get('frame', 'scan')
        args.camera_at = ','.join(str(v) for v in hints['oak_camera'])
        if hints.get('oak_looks_at') and not args.look_at and args.heading is None:
            args.look_at = ','.join(str(v) for v in hints['oak_looks_at'])
    if not args.camera_at:
        raise ValueError('give --camera-at x,y,z or --labels with an oak_camera point')
    camera = _viewer_point(args.frame, args.camera_at, state)
    if args.no_floor:
        # Level roll and the given tilt; the clicked z is the camera height.
        tilt = math.radians(args.tilt_down)
        floor = {'normal_camera': [0.0, -math.cos(tilt), -math.sin(tilt)], 'camera_height_m': float(camera[2])}
    else:
        floor = state.get('floor')
        if not floor:
            raise ValueError('run floor first (or pass --no-floor with --camera-at x,y,z)')
    heading = args.heading
    if args.look_at:
        target = _viewer_point(args.frame, args.look_at, state)
        heading = math.degrees(math.atan2(target[1]-camera[1], target[0]-camera[0]))
    report = room_mesh.register(source, points, normals, floor, camera[:2], heading_deg=heading,
                                max_rms_m=args.max_rms, min_inlier_fraction=args.min_inliers, max_range_m=args.max_range)
    from spatial_validation import mount_signature
    report.update(snapshot=snapshot['id'], registered_at=now_iso(), mount_signature=mount_signature(snapshot['depth_mm']),
                  calibration_content_id=calibration_id(snapshot['calibration']), mesh_sha256=mesh_meta['source_sha256'])
    state['last_registration_attempt'] = {k: report[k] for k in ('status', 'reasons', 'warnings', 'rms_m', 'inlier_fraction',
                                                                 'registered_at', 'snapshot')}
    if report['status'] == 'accepted':
        state['registration'] = report
    workspace.save_state(state)
    print('registration %s: rms %s mm, inliers %.0f%% of %d in-range points' % (
        report['status'].upper(), '%.1f' % (1000*report['rms_m']) if report['rms_m'] is not None else 'n/a',
        100*report['inlier_fraction'], report['quality']['points']))
    print('camera at %s m (den), heading %.1f deg, tilt down %.1f deg, roll %.1f deg; moved %.2f m / %.1f deg from the guess'
          % (report['camera_position_m'], report['heading_deg'], report['tilt_down_deg'], report['roll_deg'],
             report['moved_from_init']['metres'], report['moved_from_init']['degrees']))
    scale = report['scale_check']
    if scale.get('status') == 'estimated':
        print('OAK-to-mesh scale %.4f (+-%.4f)' % (scale['scale'], scale['standard_error']))
    for line in report['reasons']:
        print('REFUSED: '+line)
    for line in report['warnings']:
        print('WARNING: '+line)


def _store_anchor(state, record, args):
    name = record['name']
    previous = state['anchors'].get(name)
    if getattr(args, 'check', False):
        if previous is None:
            raise ValueError('nothing to check: %s has no stored anchor' % name)
        matrix, _ = registration_matrix(state)
        a, b = resolve_anchor(previous, matrix)['pos'], resolve_anchor(record, matrix)['pos']
        distance = math.dist(a, b)
        check = {'name': name, 'method': record['method'], 'distance_m': round(distance, 4), 'at': now_iso(),
                 'result': 'PASS' if distance < CHECK_TOLERANCE_M else 'FAIL'}
        state['checks'].append(check)
        print('check %s: remeasured %.3f m from the stored anchor -> %s (tolerance %.2f m)'
              % (name, distance, check['result'], CHECK_TOLERANCE_M))
        return
    if previous is not None and not getattr(args, 'replace', False):
        raise ValueError('%s already exists: use --replace, or --check to compare' % name)
    state['anchors'][name] = record
    matrix, kind = registration_matrix(state)
    if record['frame'] == 'den' or matrix is not None:
        where = '%s m (room, %s)' % (resolve_anchor(record, matrix)['pos'], kind or 'mesh')
    else:
        where = '%s m (camera frame until floor/register)' % [round(v, 3) for v in record['xyz']]
    print('anchor %s (%s): %s' % (name, record['method'], where))


def cmd_anchor(args, workspace, state):
    np = _np()
    name = args.name
    category_of(name)
    methods = [m for m in ('point', 'pixel', 'box', 'pointing', 'measured') if getattr(args, m)]
    if len(methods) != 1:
        raise ValueError('give exactly one of --point, --pixel, --box, --pointing, --measured')
    method = methods[0]
    if method == 'point':
        record = anchor_record(name, 'mesh', 'den', _viewer_point(args.frame, args.point, state), radius_m=args.radius_m)
    elif method in ('pixel', 'box'):
        snapshot = load_snapshot(workspace, state, args.snapshot)
        lifter = head3d.DepthLifter(snapshot['depth_mm'], rgbd_model(snapshot['meta'], snapshot['calibration']))
        if method == 'pixel':
            u, v = parse_floats(args.pixel, 2)
            sample = lifter.lift(u, v, radius_px=args.radius_px)
            if sample['status'] != 'measured':
                raise ValueError('no depth at pixel %s (%s): pick a textured spot nearby (see the grid image)'
                                 % (args.pixel, sample['reason']))
            record = anchor_record(name, 'pixel', 'camera', sample['xyz'], radius_m=args.radius_m, pixel=[u, v],
                                   support=sample['support'], spread_m=sample['spread_m'], snapshot=snapshot['id'])
        else:
            box = parse_floats(args.box, 4)
            sample = lifter.lift_box(box, layer='foreground')
            if sample['status'] != 'measured':
                raise ValueError('no depth in box %s (%s)' % (args.box, sample['reason']))
            points = sample['points']
            chosen = points[np.linspace(0, len(points)-1, num=min(len(points), 200)).astype(int)]
            record = anchor_record(name, 'pixel_box', 'camera', sample['xyz'], extent_points=chosen, box=box,
                                   support=sample['support'], snapshot=snapshot['id'])
    elif method == 'measured':
        if not state.get('floor'):
            raise ValueError('run floor first: measured offsets are level right/forward/up from the camera')
        right, forward, up = parse_floats(args.measured, 3)
        level = room_mesh.level_rotation(state['floor']['normal_camera'])
        record = anchor_record(name, 'measured', 'camera', level.T @ np.array([right, forward, up]), radius_m=args.radius_m,
                               tape_right_forward_up_m=[right, forward, up])
    else:
        record = pointing_anchor(args, state)
        if record is None:
            workspace.save_state(state)
            return
    _store_anchor(state, record, args)
    workspace.save_state(state)


def pointing_anchor(args, state):
    """Owner points (ray) or holds a hand at the object (hold) while a Lab recording with pose runs."""
    np = _np()
    reader = CameraReader(args.camera_env)
    calibration = reader.calibration()
    ledger = LedgerReader(args.ledger)
    try:
        for count in range(3, 0, -1):
            print('  %d ...' % count, flush=True)
            time.sleep(1)
        print('HOLD STILL for %.0f s' % args.hold, flush=True)
        start, bundles = time.time(), []
        while time.time()-start < args.hold:
            bundles.append(reader.bundle())
        end = time.time()
        print('done; waiting %.0f s for the Lab detector' % args.settle, flush=True)
        time.sleep(args.settle)
        outputs = ledger.pose_outputs(start-0.5, end+0.5)
    finally:
        ledger.close()
    samples, skipped = [], {}
    for output in outputs:
        if len(output['poses']) != 1:
            skipped['not_one_person'] = skipped.get('not_one_person', 0)+1
            continue
        bundle, skew = pair_bundle(output['frame'], bundles)
        if bundle is None:
            skipped['no_bundle_within_%.2fs' % POINTING_PAIR_S] = skipped.get('no_bundle_within_%.2fs' % POINTING_PAIR_S, 0)+1
            continue
        joints = head3d.joints_of(output['poses'][0])
        wrist, elbow = select_wrist(joints, args.wrist)
        if wrist is None:
            skipped['no_wrist'] = skipped.get('no_wrist', 0)+1
            continue
        model = rgbd_model(bundle['meta'], calibration)
        lifter = head3d.DepthLifter(bundle['depth_mm'], model)
        w = lifter.lift(*joints[wrist], radius_px=5)
        if w['status'] != 'measured':
            skipped['wrist_depth_'+w['reason']] = skipped.get('wrist_depth_'+w['reason'], 0)+1
            continue
        wrist3d = np.asarray(w['xyz'])
        e = lifter.lift(*joints[elbow], radius_px=5) if elbow in joints else {'status': 'unknown'}
        if args.mode == 'hold':
            if e['status'] == 'measured':
                forearm = wrist3d-np.asarray(e['xyz'])
                if np.linalg.norm(forearm) > 0.05:
                    wrist3d = wrist3d+HAND_BEYOND_WRIST_M*forearm/np.linalg.norm(forearm)
            samples.append({'point': wrist3d, 'skew_s': skew})
        else:
            eyes = [lifter.lift(*joints[j]) for j in (head3d.LEFT_EYE, head3d.RIGHT_EYE, head3d.NOSE) if j in joints]
            eyes = [np.asarray(s['xyz']) for s in eyes if s['status'] == 'measured']
            if not eyes:
                skipped['no_eye_depth'] = skipped.get('no_eye_depth', 0)+1
                continue
            origin = np.mean(eyes, axis=0)
            samples.append({'origin': origin, 'direction': wrist3d-origin, 'skew_s': skew})
    print('pose outputs %d, usable %d, skipped %s' % (len(outputs), len(samples), skipped or '{}'))
    if len(samples) < 2:
        raise ValueError('fewer than 2 usable pose samples: is a Lab recording with pose running, one person in view, '
                         'hand visible? Try --hold 5, or use --pixel/--point')
    if args.mode == 'hold':
        points = np.array([s['point'] for s in samples])
        centre = np.median(points, axis=0)
        spread = float(np.max(np.linalg.norm(points-centre, axis=1)))
        if spread > 0.15:
            raise ValueError('hand moved %.2f m during the hold; hold still and repeat' % spread)
        return anchor_record(args.name, 'pointing_hold', 'camera', centre, radius_m=args.radius_m,
                             samples=len(samples), spread_m=spread)
    origin = np.median(np.array([s['origin'] for s in samples]), axis=0)
    direction = np.median(np.array([s['direction']/np.linalg.norm(s['direction']) for s in samples]), axis=0)
    pending = state['pending_rays'].setdefault(args.name, [])
    pending.append({'origin': [float(v) for v in origin], 'direction': [float(v) for v in direction],
                    'samples': len(samples), 'at': now_iso()})
    if len(pending) < 2:
        print('ray 1 stored for %s: point again from a spot at least 1 m away (same command)' % args.name)
        return None
    point, distances, angle = triangulate([(r['origin'], r['direction']) for r in pending])
    if angle < 10:
        print('rays only %.0f deg apart: point again from further to the side' % angle)
        return None
    state['pending_rays'].pop(args.name)
    return anchor_record(args.name, 'pointing_ray', 'camera', point, radius_m=args.radius_m, rays=len(pending),
                         ray_miss_m=[round(d, 3) for d in distances], ray_angle_deg=round(angle, 1))


def cmd_anchors_import(args, workspace, state):
    document = json.loads(Path(args.file).read_text(encoding='utf-8'))
    records = anchors_from_file(document, (state.get('mesh') or {}).get('T_mesh'))
    for record in records:
        _store_anchor(state, record, args)
    workspace.save_state(state)


def review_rows(state, model=None):
    np = _np()
    matrix, kind = registration_matrix(state)
    rows = []
    for name in sorted(state['anchors']):
        record = state['anchors'][name]
        try:
            resolved = resolve_anchor(record, matrix)
        except ValueError as error:
            rows.append({'name': name, 'method': record['method'], 'error': str(error)})
            continue
        row = {'name': name, 'method': record['method'], **resolved}
        if matrix is not None:
            camera = matrix[:3, 3]
            offset = np.asarray(resolved['pos'])-camera
            row['from_camera_m'] = round(float(np.linalg.norm(offset)), 3)
            if model is not None:
                row['rgb_pixel'] = [round(v, 1) for v in (project_room_points([resolved['pos']], matrix, model)[0] or [])] or None
        rows.append(row)
    return rows, kind


def cmd_review(args, workspace, state):
    model = None
    if state.get('latest_snapshot'):
        snapshot = load_snapshot(workspace, state)
        model = rgbd_model(snapshot['meta'], snapshot['calibration'])
    rows, kind = review_rows(state, model)
    if args.json:
        _print({'registration': kind, 'anchors': rows, 'checks': state['checks']})
        return
    print('frame: %s (%s)' % (room_mesh.FRAME, {'mesh_icp': 'mesh registration', 'floor_only': 'floor-only: origin under the camera',
                                                None: 'NO REGISTRATION'}[kind]))
    print('%-16s %-14s %-26s %-18s %-8s %s' % ('anchor', 'method', 'pos x,y,z (m)', 'radius/size (m)', 'range', 'rgb px'))
    for row in rows:
        if 'error' in row:
            print('%-16s %-14s %s' % (row['name'], row['method'], row['error']))
            continue
        extent = ('r %.2f' % row['radius_m']) if 'radius_m' in row else ' x '.join('%.2f' % v for v in row['size_m'])
        print('%-16s %-14s %-26s %-18s %-8s %s' % (row['name'], row['method'], ', '.join('%.2f' % v for v in row['pos']),
                                                   extent, row.get('from_camera_m', ''), row.get('rgb_pixel')))
    for check in state['checks'][-5:]:
        print('check %s (%s): %.3f m -> %s' % (check['name'], check['method'], check['distance_m'], check['result']))
    missing = sorted(set(ANCHOR_RADIUS_M)-{state['anchors'][n]['category'] for n in state['anchors']})
    if missing:
        print('not yet mapped: '+', '.join(missing))


def cmd_grid(args, workspace, state):
    snapshot = load_snapshot(workspace, state, args.snapshot)
    np = _np()
    model = rgbd_model(snapshot['meta'], snapshot['calibration'])
    rgb = nv12_to_rgb(snapshot['rgb_nv12'])
    lifter = head3d.DepthLifter(snapshot['depth_mm'], model)
    coverage = np.zeros(rgb.shape[:2], dtype=bool)
    u = np.clip(np.round(lifter.uv[:, 0]).astype(int), 0, rgb.shape[1]-1)
    v = np.clip(np.round(lifter.uv[:, 1]).astype(int), 0, rgb.shape[0]-1)
    coverage[v, u] = True
    # Fill single-pixel gaps between projected depth pixels.
    coverage[1:-1, 1:-1] |= coverage[:-2, 1:-1] & coverage[2:, 1:-1]
    coverage[1:-1, 1:-1] |= coverage[1:-1, :-2] & coverage[1:-1, 2:]
    anchors = []
    if not args.no_anchors:
        rows, _ = review_rows(state, model)
        anchors = [(row['name'], row.get('rgb_pixel')) for row in rows if 'error' not in row]
    image = render_grid(rgb, coverage=coverage, anchors=anchors, step=args.step)
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=90)
    name = 'grid-%s.jpg' % snapshot['id']
    path = workspace.write_bytes(name, buffer.getvalue())
    print('wrote %s (%dx%d; labels are 640x360 preview pixels; darker = no depth there)' % (path, *image.size))
    print('copy to the owner\'s machine: scp engineeredlightingllm@192.168.1.168:%s .' % path)


def cmd_save(args, workspace, state):
    revision = max([s['revision'] for s in state['saved']] or [0])+1
    document, room_map = build_site_map(state, revision=revision, note=args.note)
    name = 'site-map-r%03d.json' % revision
    workspace.write_json(name, document)
    workspace.write_json('site-map-latest.json', document)
    state['saved'].append({'revision': revision, 'file': name, 'model_sha256': document['binding']['model_sha256'],
                           'anchors': len(room_map['targets']), 'at': now_iso(),
                           'registration': document['model']['meta']['camera_registration']['method']})
    workspace.save_state(state)
    print('saved %s: map %s revision %d, model_sha256 %s, %d anchors, registration %s' % (
        workspace.path(name), MAP_ID, revision, document['binding']['model_sha256'], len(room_map['targets']),
        document['model']['meta']['camera_registration']['method']))


def cmd_check_mount(args, workspace, state):
    from spatial_validation import check_mount
    reference = (state.get('registration') or {}).get('mount_signature') or (state.get('floor') or {}).get('mount_signature')
    if reference is None:
        raise ValueError('no registered mount signature yet')
    reader = CameraReader(args.camera_env)
    depth, _, _ = median_depth([reader.bundle()['depth_mm'] for _ in range(args.frames)])
    result = check_mount(reference, depth)
    print('mount %s: %s' % (result['status'].upper(), {k: v for k, v in result.items() if k != 'status'}))


def cmd_status(args, workspace, state):
    steps = [('capture', bool(state.get('latest_snapshot'))), ('floor', bool(state.get('floor'))),
             ('import-mesh', bool(state.get('mesh'))), ('register', bool(state.get('registration'))),
             ('anchors', bool(state.get('anchors'))), ('save', bool(state.get('saved')))]
    for step, done in steps:
        print('%-12s %s' % (step, 'done' if done else '-'))
    attempt = state.get('last_registration_attempt')
    if attempt and attempt['status'] != 'accepted':
        print('last registration attempt refused: %s' % attempt['reasons'])
    print('anchors: %s' % (', '.join(sorted(state['anchors'])) or 'none'))
    if state.get('pending_rays'):
        print('pointing rays waiting for a second ray: %s' % ', '.join(state['pending_rays']))


def parser():
    top = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    top.add_argument('--dir', default=os.environ.get('ROOM_MAP_DIR', DEFAULT_DIR))
    top.add_argument('--camera-env', default=CAMERA_ENV)
    top.add_argument('--ledger', default=LEDGER)
    sub = top.add_subparsers(dest='command', required=True)
    p = sub.add_parser('capture')
    p.add_argument('--frames', type=int, default=15)
    p = sub.add_parser('floor')
    p.add_argument('--snapshot')
    p = sub.add_parser('import-mesh')
    p.add_argument('mesh')
    p.add_argument('--up', choices=('y', 'z'), default='y')
    p.add_argument('--spacing', type=float, default=room_mesh.SAMPLE_SPACING_M)
    p.add_argument('--export-obj', action='store_true', help='also write den-frame.obj (Z up, den metres)')
    p.add_argument('--frame-json', help='reuse an existing T_mesh (frame.json/registration.json) instead of fitting the floor')
    p = sub.add_parser('measure')
    p.add_argument('--from', dest='from_point', required=True)
    p.add_argument('--to', dest='to_point', required=True)
    p.add_argument('--frame', choices=room_mesh.VIEWER_FRAMES, default='scan')
    p.add_argument('--tape', type=float, help='tape-measured distance in metres')
    p = sub.add_parser('register')
    p.add_argument('--camera-at', help='rough OAK position x,y,z (or x,y with --frame den)')
    p.add_argument('--labels', help="labeller JSON with 'oak_camera' (and 'oak_looks_at') points")
    p.add_argument('--frame', choices=room_mesh.VIEWER_FRAMES, default='scan')
    p.add_argument('--look-at', help='a point the camera faces (e.g. the seat)')
    p.add_argument('--heading', type=float, help='optical axis heading, degrees CCW from den +X')
    p.add_argument('--snapshot')
    p.add_argument('--no-floor', action='store_true')
    p.add_argument('--tilt-down', type=float, default=10.0)
    p.add_argument('--max-rms', type=float, default=room_mesh.MAX_RMS_M)
    p.add_argument('--min-inliers', type=float, default=room_mesh.MIN_INLIER_FRACTION)
    p.add_argument('--max-range', type=float, default=room_mesh.MAX_RANGE_M)
    p = sub.add_parser('anchor')
    p.add_argument('name')
    p.add_argument('--point', help='x,y,z picked on the mesh (see --frame)')
    p.add_argument('--frame', choices=room_mesh.VIEWER_FRAMES, default='scan')
    p.add_argument('--pixel', help='u,v in the 640x360 RGB preview (grid image)')
    p.add_argument('--box', help='u0,v0,u1,v1 RGB box: extent from its front surface')
    p.add_argument('--radius-px', type=float, default=6.0)
    p.add_argument('--pointing', action='store_true')
    p.add_argument('--mode', choices=('hold', 'ray'), default='hold')
    p.add_argument('--hold', type=float, default=4.0)
    p.add_argument('--settle', type=float, default=3.0)
    p.add_argument('--wrist', choices=('auto', 'left', 'right'), default='auto')
    p.add_argument('--measured', help='tape offsets right,forward,up (m) from the camera lens, level')
    p.add_argument('--radius-m', type=float)
    p.add_argument('--snapshot')
    p.add_argument('--replace', action='store_true')
    p.add_argument('--check', action='store_true', help='remeasure and compare with the stored anchor (<0.10 m)')
    p = sub.add_parser('anchors-import')
    p.add_argument('file')
    p.add_argument('--replace', action='store_true')
    p.add_argument('--check', action='store_true')
    p = sub.add_parser('review')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('grid')
    p.add_argument('--snapshot')
    p.add_argument('--step', type=int, default=20)
    p.add_argument('--no-anchors', action='store_true')
    p = sub.add_parser('save')
    p.add_argument('--note')
    p = sub.add_parser('check-mount')
    p.add_argument('--frames', type=int, default=5)
    sub.add_parser('status')
    return top


COMMANDS = {'capture': cmd_capture, 'floor': cmd_floor, 'import-mesh': cmd_import_mesh, 'measure': cmd_measure,
            'register': cmd_register, 'anchor': cmd_anchor, 'anchors-import': cmd_anchors_import, 'review': cmd_review,
            'grid': cmd_grid, 'save': cmd_save, 'check-mount': cmd_check_mount, 'status': cmd_status}


def main(argv=None):
    args = parser().parse_args(argv)
    workspace = Workspace(args.dir).ensure()
    state = workspace.load_state()
    try:
        COMMANDS[args.command](args, workspace, state)
    except ValueError as error:
        print('error: %s' % error, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
