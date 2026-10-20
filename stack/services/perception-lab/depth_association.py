"""Associate RGB person regions with rectified-left depth pixels (numpy).

Depth is aligned to the rectified LEFT camera, never to RGB. Every depth pixel
is deprojected at its own depth, moved into the RGB camera with the EEPROM
extrinsics and the left rectification rotation, and projected with the RGB
frame's own intrinsics and distortion. Pixels landing in the RGB region form a
full-frame rectified-left mask; invalid-depth pixels are placed with the
region's median depth so holes still count against valid support. The chain
is used only with a passed RGB-depth reprojection receipt (<=5 px).

Tracks are session-scoped geometric continuity per arm and method; they never
name, re-identify or authorize anyone. No camera access, model call or network.
"""
from copy import deepcopy
from geometry import TrackBank, robust_support, floor_projection, _finite

VERSION = 'depth-association-v1'
MAX_REGIONS = 8
PIXEL_UNCERTAINTY_PX = 4.0
TORSO = ('left_shoulder', 'right_shoulder', 'right_hip', 'left_hip')
ANKLES = ('left_ankle', 'right_ankle')


def _array(value, shape):
    import numpy as np
    array = np.asarray(value, dtype=np.float64)
    if array.shape != shape or not np.all(np.isfinite(array)):
        raise ValueError(f'finite {shape} matrix required')
    return array


def _rigid(matrix):
    import numpy as np
    if np.abs(matrix[:3, :3].T @ matrix[:3, :3]-np.eye(3)).max() > 1e-4 or abs(np.linalg.det(matrix[:3, :3])-1) > 1e-4:
        raise ValueError('rigid rotation required')
    return matrix


def eeprom_rgb_from_rectified_left(eeprom, *, rgb_socket=0, translation_units='cm'):
    """RGB-from-rectified-left rigid transform composed from the EEPROM JSON.

    Convention (DepthAI EEPROM): cameraData[socket].extrinsics maps points of
    that camera into toCameraSocket (p_to = R p + t), translation in
    centimetres; stereoRectificationData.rectifiedRotationLeft maps raw left
    into rectified left. The reprojection receipt is what validates the chain.
    """
    import numpy as np
    scale = {'cm': .01, 'mm': .001, 'm': 1.0}[translation_units]
    cameras = {int(socket): data for socket, data in eeprom['cameraData']}
    rectification = eeprom['stereoRectificationData']
    left = int(rectification['leftCameraSocket'])
    edges = {}
    for socket, data in cameras.items():
        extrinsics = data.get('extrinsics') or {}
        target = extrinsics.get('toCameraSocket')
        if target is None or int(target) < 0 or int(target) == socket:
            continue
        transform = np.eye(4)
        transform[:3, :3] = _array(extrinsics['rotationMatrix'], (3, 3))
        transform[:3, 3] = _array([extrinsics['translation'][k] for k in 'xyz'], (3,))*scale
        _rigid(transform)
        edges.setdefault(socket, {})[int(target)] = transform
        edges.setdefault(int(target), {})[socket] = np.linalg.inv(transform)
    chain, frontier = {left: np.eye(4)}, [left]
    while frontier and rgb_socket not in chain:
        socket = frontier.pop(0)
        for target, transform in edges.get(socket, {}).items():
            if target not in chain:
                chain[target] = transform @ chain[socket]
                frontier.append(target)
    if rgb_socket not in chain:
        raise ValueError('no EEPROM extrinsic chain from left to RGB')
    unrectify = np.eye(4)
    unrectify[:3, :3] = _array(rectification['rectifiedRotationLeft'], (3, 3)).T
    result = _rigid(chain[rgb_socket] @ unrectify)
    return {'rotation': [[float(v) for v in row] for row in result[:3, :3]],
            'translation_m': [float(v) for v in result[:3, 3]]}


def _intrinsics(value):
    matrix = _array(value, (3, 3))
    if matrix[0, 0] <= 0 or matrix[1, 1] <= 0 or list(matrix[2]) != [0, 0, 1]:
        raise ValueError('pinhole intrinsic matrix required')
    return matrix


def _distortion(value):
    coefficients = [float(v) for v in (value or [])]
    if len(coefficients) > 14 or not _finite(coefficients) or any(abs(v) > 1e-12 for v in coefficients[8:]):
        raise ValueError('only the 8-term rational distortion model is supported')
    return (coefficients+[0.0]*8)[:8]


def camera_model(source_metadata, calibration, *, intrinsics_tolerance_px=1.0, measuring_reprojection=False):
    """Per-frame projection model from bundle metadata and a validated calibration.

    calibration needs: id, mount_revision, reprojection {receipt, p95_px<=5},
    rgb_from_rectified_left {rotation, translation_m} and reference_intrinsics
    {rectified_left, rgb} (3x3). Per-frame intrinsics that drift from the
    validated ones (a changed crop/scale) refuse instead of silently shifting.
    Only the offline reprojection measurement may omit the receipt.
    """
    import numpy as np
    projection = calibration.get('reprojection') or {}
    if (not calibration.get('id') or not calibration.get('mount_revision') or (not measuring_reprojection and (
            not projection.get('receipt') or not _finite([projection.get('p95_px')]) or projection['p95_px'] > 5))):
        raise ValueError('validated RGB-depth reprojection required')
    frames = source_metadata['frames']
    depth, rgb = frames['depth'], frames['rgb']
    if depth.get('transformation_valid') is not True or rgb.get('transformation_valid') is not True:
        raise ValueError('per-frame transformations required')
    model = {'depth_k': _intrinsics(depth['intrinsics']), 'rgb_k': _intrinsics(rgb['intrinsics']),
             'rgb_distortion': _distortion(rgb.get('source_distortion_coefficients')),
             'depth_size': (int(depth['width']), int(depth['height'])), 'rgb_size': (int(rgb['width']), int(rgb['height'])),
             'calibration_id': calibration['id'], 'mount_revision': calibration['mount_revision'],
             'reprojection_receipt': projection.get('receipt')}
    reference = calibration['reference_intrinsics']
    for key, name in (('depth_k', 'rectified_left'), ('rgb_k', 'rgb')):
        if np.abs(model[key]-_intrinsics(reference[name])).max() > intrinsics_tolerance_px:
            raise ValueError('frame_intrinsics_changed')
    extrinsics = calibration['rgb_from_rectified_left']
    transform = np.eye(4)
    transform[:3, :3] = _array(extrinsics['rotation'], (3, 3))
    transform[:3, 3] = _array(extrinsics['translation_m'], (3,))
    model['rgb_from_left'] = _rigid(transform)
    width, height = model['depth_size']
    v, u = np.mgrid[0:height, 0:width]
    k = model['depth_k']
    model['rays'] = np.stack([(u-k[0, 2])/k[0, 0], (v-k[1, 2])/k[1, 1], np.ones(u.shape)], axis=-1)
    return model


def project(points, k, distortion):
    """Project camera points (N,3) with OpenCV's 8-term rational model."""
    import numpy as np
    z = points[:, 2]
    ok = z > 1e-6
    safe = np.where(ok, z, 1.0)
    x, y = points[:, 0]/safe, points[:, 1]/safe
    k1, k2, p1, p2, k3, k4, k5, k6 = distortion
    r2 = x*x+y*y
    radial = (1+r2*(k1+r2*(k2+r2*k3)))/(1+r2*(k4+r2*(k5+r2*k6)))
    xd = x*radial+2*p1*x*y+p2*(r2+2*x*x)
    yd = y*radial+p1*(r2+2*y*y)+2*p2*x*y
    return np.stack([k[0, 0]*xd+k[0, 1]*yd+k[0, 2], k[1, 1]*yd+k[1, 2]], axis=1), ok


def rgb_pixels(model, depth_mm, rows=None):
    """Project rectified-left pixels (optionally a boolean selection) into RGB."""
    import numpy as np
    z = np.asarray(depth_mm, dtype=np.float64)/1000
    rays = model['rays'] if rows is None else model['rays'][rows]
    z = z if rows is None else z[rows]
    points = rays.reshape(-1, 3)*z.reshape(-1, 1)
    transform = model['rgb_from_left']
    return project(points @ transform[:3, :3].T+transform[:3, 3], model['rgb_k'], model['rgb_distortion'])


def reprojection_residuals(model, depth_mm, pairs, *, radius=2):
    """Pixel residuals of measured rectified-left/RGB marker pairs at 640x360.

    pairs: [{"left_pixel": [u, v], "rgb_pixel": [u, v]}]; each left pixel uses
    the median valid depth in a (2r+1)^2 patch. Missing depth is reported, not
    guessed.
    """
    import numpy as np
    depth = np.asarray(depth_mm)
    residuals, missing = [], []
    for index, pair in enumerate(pairs):
        u, v = (int(round(c)) for c in pair['left_pixel'])
        patch = depth[max(0, v-radius):v+radius+1, max(0, u-radius):u+radius+1]
        valid = patch[patch > 0]
        if not valid.size:
            missing.append(index)
            continue
        z = float(np.median(valid))/1000
        ray = model['rays'][v, u]
        transform = model['rgb_from_left']
        point = (transform[:3, :3] @ (ray*z)+transform[:3, 3])[None, :]
        projected, ok = project(point, model['rgb_k'], model['rgb_distortion'])
        if not ok[0]:
            missing.append(index)
            continue
        residuals.append(float(np.hypot(*(projected[0]-np.asarray(pair['rgb_pixel'], dtype=float)))))
    return residuals, missing


def region_mask(region, *, size, inset=.15, minimum_pixels=64):
    """Rasterize an RGB person region: inset box, pose torso or segmentation polygons."""
    import numpy as np
    from PIL import Image, ImageDraw
    width, height = size
    kind = region.get('kind') if isinstance(region, dict) else None
    if kind == 'box':
        box = region.get('box') or {}
        x, y, w, h = (box.get(k) for k in ('x', 'y', 'w', 'h'))
        if not _finite([x, y, w, h]) or not (0 <= x < x+w <= width and 0 <= y < y+h <= height):
            raise ValueError('source-pixel box inside the RGB frame required')
        polygons = [[(x+w*inset, y+h*inset), (x+w*(1-inset), y+h*inset),
                     (x+w*(1-inset), y+h*(1-inset)), (x+w*inset, y+h*(1-inset))]]
    elif kind == 'torso':
        points = region.get('points')
        if not isinstance(points, list) or len(points) != 4 or any(len(p) != 2 or not _finite(p) for p in points):
            raise ValueError('four torso joints required')
        polygons = [[tuple(p) for p in points]]
    elif kind == 'polygon':
        polygons = region.get('polygons')
        if (not isinstance(polygons, list) or not 1 <= len(polygons) <= 16
                or sum(len(p) for p in polygons) > 8192
                or any(not 3 <= len(p) <= 2048 or any(len(q) != 2 or not _finite(q) for q in p) for p in polygons)):
            raise ValueError('bounded segmentation polygons required')
        polygons = [[tuple(q) for q in p] for p in polygons]
    else:
        raise ValueError('box, torso or polygon region required')
    image = Image.new('1', (width, height), 0)
    draw = ImageDraw.Draw(image)
    for polygon in polygons:
        draw.polygon(polygon, fill=1)
    mask = np.asarray(image, dtype=bool)
    return mask if int(mask.sum()) >= minimum_pixels else None


def _rgb_indices(model, uv, ok):
    import numpy as np
    rgb_width, rgb_height = model['rgb_size']
    ur, vr = np.floor(uv[:, 0]+.5).astype(np.int64), np.floor(uv[:, 1]+.5).astype(np.int64)
    inside = ok & (ur >= 0) & (ur < rgb_width) & (vr >= 0) & (vr < rgb_height)
    return inside, ur, vr


def frame_projection(depth_mm, model):
    """Region-independent part of association, computed once per depth frame.

    Valid pixels are projected into RGB at their own depth; invalid pixels keep
    only their rotated rays, since each region places them at its own depth.
    """
    import numpy as np
    depth = np.asarray(depth_mm)
    width, height = model['depth_size']
    if depth.shape != (height, width) or depth.dtype != np.uint16:
        raise ValueError('rectified-left uint16 depth matching the model required')
    valid = depth > 0
    inside, ur, vr = _rgb_indices(model, *rgb_pixels(model, depth, valid))
    flat = np.flatnonzero(valid)
    holes = np.flatnonzero(~valid)
    rotation = model['rgb_from_left'][:3, :3]
    return {'depth': depth, 'valid_index': flat[inside], 'valid_ur': ur[inside], 'valid_vr': vr[inside],
            'hole_index': holes, 'hole_rays': model['rays'].reshape(-1, 3)[holes] @ rotation.T}


def associate(depth_mm, model, region, *, minimum_support=12, projection=None):
    """Rectified-left mask/ROI for one RGB region, with a preliminary sample.

    Returns {"status": "associated", roi, roi_frame, mask, camera_point, ...}
    or {"status": "unknown", "reason"}; the trusted measurement is repeated by
    RegisteredDepth from the stored bytes. Pass one frame_projection for every
    region of the same frame.
    """
    import numpy as np
    projection = projection or frame_projection(depth_mm, model)
    depth = projection['depth']
    if depth is not depth_mm and not np.array_equal(depth, np.asarray(depth_mm)):
        raise ValueError('frame projection belongs to another depth frame')
    rgb_mask = region_mask(region, size=model['rgb_size'])
    if rgb_mask is None:
        return {'status': 'unknown', 'reason': 'region_too_small'}
    measured = np.zeros(depth.size, bool)
    measured[projection['valid_index'][rgb_mask[projection['valid_vr'], projection['valid_ur']]]] = True
    measured = measured.reshape(depth.shape)
    if int(measured.sum()) < minimum_support:
        return {'status': 'unknown', 'reason': 'no_projected_support'}
    prior = float(np.median(depth[measured]))
    holes = np.zeros(depth.size, bool)
    if len(projection['hole_index']):
        transform = model['rgb_from_left']
        uv, ok = project(projection['hole_rays']*(prior/1000)+transform[:3, 3], model['rgb_k'], model['rgb_distortion'])
        inside, ur, vr = _rgb_indices(model, uv, ok)
        found = np.zeros(len(uv), bool)
        found[inside] = rgb_mask[vr[inside], ur[inside]]
        holes[projection['hole_index'][found]] = True
    holes = holes.reshape(depth.shape)
    mask = measured | holes
    ys, xs = np.nonzero(mask)
    roi = [int(xs.min()), int(ys.min()), int(xs.max())+1, int(ys.max())+1]
    sample, support = robust_support(depth, roi, mask=mask, minimum_support=minimum_support)
    if sample['status'] != 'measured':
        return {'status': 'unknown', 'reason': sample['reason'], 'roi': roi}
    u, v, z = support
    points = model['rays'][v, u]*(z/1000)[:, None]
    return {'status': 'associated', 'roi': roi, 'roi_frame': 'rectified_left', 'mask': mask,
            'camera_point': [float(c) for c in np.median(points, axis=0)], 'projected_support': int(measured.sum()),
            'hole_pixels': int(holes.sum()), 'prior_depth_m': prior/1000, 'depth_m': sample['depth_m']}


def person_regions(result, *, width, height):
    """Per detected person: segmentation polygons, else pose torso, else inset box.

    Masks/poses are used only when they share the box's instance_id; nothing is
    matched by position or guessed from another instance.
    """
    masks = {m.get('instance_id'): m for m in result.get('masks') or [] if isinstance(m, dict)}
    poses = {p.get('instance_id'): p for p in result.get('pose_instances') or [] if isinstance(p, dict)}
    regions = []
    for box in result.get('boxes') or []:
        if not isinstance(box, dict) or box.get('label') != 'person':
            continue
        instance = box.get('instance_id')
        region = {'kind': 'box', 'box': {k: box.get(k) for k in ('x', 'y', 'w', 'h')}}
        pose = poses.get(instance) if instance is not None else None
        joints = {j.get('name'): j for j in (pose or {}).get('joints', []) if isinstance(j, dict)}
        if instance is not None and instance in masks and masks[instance].get('polygons'):
            region = {'kind': 'polygon', 'polygons': masks[instance]['polygons']}
        elif all(joints.get(name, {}).get('status') == 'supported' for name in TORSO):
            region = {'kind': 'torso', 'points': [joints[name]['pixel'] for name in TORSO]}
        regions.append({'instance_id': instance, 'region': region, 'confidence': box.get('confidence'),
                        'contact': floor_contact(box, joints, width=width, height=height)})
    return regions


def floor_contact(box, joints=None, *, width, height, edge_margin_px=4):
    """Floor-contact pixel: visible only as both supported ankles in an untruncated box.

    A box bottom is reported for diagnostics but never visible: behind a desk,
    sofa or chair it is the furniture edge, not the floor, and projecting it
    would place the person on the floor beyond them. A box touching the image
    bottom edge is truncated: its contact is not visible either.
    """
    joints = joints or {}
    x, y, w, h = (box.get(k) for k in ('x', 'y', 'w', 'h'))
    if not _finite([x, y, w, h]):
        raise ValueError('finite source-pixel box required')
    visible = y+h < height-edge_margin_px
    if all(joints.get(name, {}).get('status') == 'supported' for name in ANKLES):
        left, right = (joints[name]['pixel'] for name in ANKLES)
        return {'contact_pixel': [(left[0]+right[0])/2, (left[1]+right[1])/2], 'contact_kind': 'ankle_midpoint',
                'contact_visible': visible and max(left[1], right[1]) < height-edge_margin_px}
    return {'contact_pixel': [x+w/2, min(y+h, height)], 'contact_kind': 'box_bottom_centre', 'contact_visible': False}


class SessionTracks:
    """Anonymous track ids per (session, capture epoch, arm, method); bounded memory."""
    def __init__(self, site_id, camera_id, *, max_banks=8, max_gap_s=2.0, max_distance_m=.8):
        self.site_id, self.camera_id, self.max_banks = site_id, camera_id, max_banks
        self.max_gap_s, self.max_distance_m = max_gap_s, max_distance_m
        self.banks = {}

    def assign(self, session_id, capture_epoch, arm, method, observed_at, points):
        key = (session_id, capture_epoch, arm, method)
        bank = self.banks.pop(key, None) or TrackBank(self.site_id, self.camera_id,
            f'{session_id}/{capture_epoch}/{arm}/{method}', max_gap_s=self.max_gap_s, max_distance_m=self.max_distance_m)
        self.banks[key] = bank
        while len(self.banks) > self.max_banks:
            self.banks.pop(next(iter(self.banks)))
        return [track['track_id'] for track in bank.update(points, observed_at)]


class DepthAssociator:
    """Build SpatialConsumer associations for one recorded perception.output.

    Stereo arms: each person region is associated with one clock-mapped,
    validated depth record from the same camera session. Floor-projection
    arms: the supported-ankle floor contact of each person. Failures become
    explicit abstentions; nothing is dropped and no identity is inferred.
    """
    def __init__(self, store, *, association_revision, calibration, tracks, floor_homography=None, target_frame=None):
        if not isinstance(association_revision, str) or not 1 <= len(association_revision) <= 256:
            raise ValueError('provisioned association revision required')
        self.store, self.revision, self.tracks = store, association_revision, tracks
        self.calibration, self.floor_homography, self.target_frame = deepcopy(calibration), deepcopy(floor_homography), target_frame

    def _depth(self, sid, depth_media_id, generation):
        import hashlib
        import numpy as np
        item = self.store.get_media(sid, depth_media_id, generation=generation)
        meta = item['metadata']
        if meta.get('derivation') != 'depth-clock-v1' or meta.get('format') != 'depth_u16_le':
            raise ValueError('clock-mapped depth record required')
        bundle = self.store.get_media(sid, meta['source_media_id'], generation=generation)
        with self.store.media_path(sid, item['id']).open('rb') as source:
            data = source.read(item['size']+1)
        if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('registered depth bytes failed integrity check')
        return item, meta, bundle['metadata']['source_metadata'], np.frombuffer(data, dtype='<u2').reshape(meta['height'], meta['width'])

    def _frame_size(self, sid, source, generation):
        # Dimensions come from the recorded RGB media, never from box extents.
        frames = [self.store.get_media(sid, mid, generation=generation)['metadata'] for mid in source.get('media_ids') or []]
        frames = [m for m in frames if m.get('stream') == 'rgb' and m.get('frame_id') == source.get('frame_id')]
        if len(frames) != 1 or type(frames[0].get('width')) is not int or type(frames[0].get('height')) is not int:
            raise ValueError('one recorded RGB source frame required')
        return frames[0]['width'], frames[0]['height']

    def associations(self, sid, source_event_id, source, *, localization, depth_media_id=None,
                     pixel_uncertainty_px=PIXEL_UNCERTAINTY_PX):
        session = self.store.get_session(sid)
        width, height = self._frame_size(sid, source, session['generation'])
        regions = person_regions(source.get('result') or {}, width=width, height=height)
        base = {'association_revision': self.revision, 'source_event_id': source_event_id}
        if not regions:
            return [{**base, 'abstention': 'no_person_region'}]
        # Bounded work per output: most confident regions first (detector order
        # when unscored); the rest are one counted abstention, never dropped.
        regions = sorted(regions, key=lambda r: -r['confidence'] if _finite([r['confidence']]) else 1)
        limited = [{**base, 'abstention': 'person_region_limit', 'count': len(regions)-MAX_REGIONS}] if len(regions) > MAX_REGIONS else []
        regions = regions[:MAX_REGIONS]
        if localization == 'floor_projection':
            return limited+self._floor(session, source, regions, base, pixel_uncertainty_px)
        if localization != 'stereo':
            raise ValueError('stereo or floor_projection localization required')
        if depth_media_id is None:
            return [{**base, 'abstention': 'no_depth_frame_in_window'}]
        item, meta, source_metadata, depth = self._depth(sid, depth_media_id, session['generation'])
        if meta.get('capture_epoch') != source.get('capture_epoch'):
            return [{**base, 'abstention': 'no_depth_frame_in_window'}]
        if meta.get('geometric_validity') != 'validated':
            return [{**base, 'abstention': 'depth_geometry_unvalidated'}]
        try:
            model = camera_model(source_metadata, self.calibration)
        except ValueError as error:
            if str(error) != 'frame_intrinsics_changed':
                raise
            return [{**base, 'abstention': 'frame_intrinsics_changed'}]
        if model['rgb_size'] != (width, height):
            raise ValueError('RGB detections and bundle RGB frame sizes differ')
        frame = {k: meta[k] for k in ('site_id', 'camera_id', 'frame_id', 'capture_epoch', 'observed_at')}
        frame.update(session_id=sid, media_ids=[item['id']])
        found, output, projection = [], limited, frame_projection(depth, model)
        for entry in regions:
            association = associate(depth, model, entry['region'], projection=projection)
            if association['status'] != 'associated':
                output.append({**base, 'abstention': association['reason']})
                continue
            found.append(association)
        if found:
            try:
                # Ordered by the arm's own RGB observations, which never repeat.
                ids = self.tracks.assign(sid, meta['capture_epoch'], source['arm'], 'stereo_surface',
                                         source['observed_at'], [a['camera_point'] for a in found])
            except ValueError:
                return output+[{**base, 'abstention': 'track_order_conflict'} for _ in found]
            for association, track in zip(found, ids):
                output.append({**base, 'localization': 'stereo', 'track_id': track, 'depth_frame': dict(frame),
                               'roi': association['roi'], 'roi_frame': 'rectified_left', 'visible': True,
                               'mask': association['mask']})
        return output

    def _floor(self, session, source, regions, base, pixel_uncertainty_px):
        homography = self.floor_homography
        if homography is None:
            raise ValueError('validated RGB floor homography required')
        visible, output = [], []
        for entry in regions:
            contact = entry['contact']
            if not contact['contact_visible']:
                output.append({**base, 'abstention': 'floor_contact_not_visible'})
                continue
            estimate = floor_projection(contact['contact_pixel'], homography['matrix'], calibration_version=homography['id'],
                reference_error_m=homography['error_bound_m'], target_frame=self.target_frame,
                pixel_uncertainty_px=pixel_uncertainty_px)
            if estimate['status'] != 'estimated':
                output.append({**base, 'abstention': 'beyond_floor_horizon'})
                continue
            visible.append((contact, estimate['xyz_m']))
        if visible:
            try:
                ids = self.tracks.assign(session['id'], source.get('capture_epoch'), source['arm'], 'rgb_floor_contact',
                                         source['observed_at'], [xyz for _, xyz in visible])
            except ValueError:
                return output+[{**base, 'abstention': 'track_order_conflict'} for _ in visible]
            for (contact, _), track in zip(visible, ids):
                output.append({**base, 'localization': 'floor_projection', 'track_id': track,
                               'pixel_uncertainty_px': pixel_uncertainty_px, **contact})
        return output
