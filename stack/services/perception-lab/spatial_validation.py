"""Owner-walkthrough validation for den spatial evidence. Local files only.

Nothing here reads a camera, calls a model or contacts a host. Fits and
evaluations produce content-addressed receipts; only apply_receipts turns a
fitted registration/calibration into 'validated' records, and only when the
receipts bind the same site, camera, calibration content, mount revision,
map revision and transform. Abstentions and boundary decisions are counted
and reported, never dropped. A physically moved camera needs a new
mount_revision and a new walkthrough; old receipts never carry over.
"""
from copy import deepcopy
import math
import re
from geometry import transform_point, zone_relation, _finite
from reasoning import digest

SCHEMA = 'perception-lab-spatial-receipt-v1'
KINDS = ('registration_fit', 'reprojection', 'reference_positions', 'mount_invalidation')
FURNITURE_TYPES = ('desk', 'sofa', 'reading_chair', 'table')
RANGE_CLASSES = ('near', 'mid', 'far')
METHODS = ('stereo', 'rgb')
CRITERIA = {'min_positions': 20, 'median_horizontal_m': .25, 'p95_horizontal_m': .50,
            'depth_range_floor_m': .10, 'depth_range_fraction': .05, 'max_reprojection_px': 5.0,
            'max_confident_wrong': 0, 'max_abstention_fraction': .25}
RGB_FRAME = {'source_frame': 'rgb_preview_640x360', 'width': 640, 'height': 360}
_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
_ZONE_ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
_SHA256 = re.compile(r'[0-9a-f]{64}')


def _recomputed(value):
    """Only values the tool recomputed from recorded inputs count; typed ones never do."""
    provenance = value.get('provenance') if isinstance(value, dict) else None
    return (isinstance(provenance, dict) and provenance.get('kind') == 'recomputed'
            and isinstance(provenance.get('input_sha256'), str) and _SHA256.fullmatch(provenance['input_sha256']) is not None)


def nearest_rank(values, q):
    """Nearest-rank percentile: the ceil(q*n)-th smallest value, no interpolation."""
    ordered = sorted(values)
    if not ordered or not 0 < q <= 1:
        raise ValueError('nonempty values and 0<q<=1 required')
    return ordered[max(0, math.ceil(q*len(ordered))-1)]


def _points(values, width, minimum):
    import numpy as np
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != width or len(array) < minimum or not np.all(np.isfinite(array)):
        raise ValueError(f'at least {minimum} finite {width}D points required')
    return array


def _spread(array, minimum_m):
    import numpy as np
    # Second principal RMS spread: markers must not be (nearly) collinear.
    singular = np.linalg.svd(array-array.mean(axis=0), compute_uv=False)/math.sqrt(len(array))
    if len(singular) < 2 or singular[1] < minimum_m:
        raise ValueError('markers must span two floor directions')


def _kabsch(source, target):
    import numpy as np
    cs, ct = source.mean(axis=0), target.mean(axis=0)
    u, _, vt = np.linalg.svd((source-cs).T @ (target-ct))
    d = np.sign(np.linalg.det(vt.T @ u.T)) or 1.0
    rotation = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
    return rotation, ct-rotation @ cs


def fit_rigid(source_points, target_points, *, min_points=4, min_spread_m=.25, min_camera_height_m=.2):
    """Rigid (no scale, det=+1) camera->map fit with a residual-based bound.

    error_bound_m is the larger of the worst in-sample residual and the worst
    leave-one-out prediction error, so one marker cannot hide its own error.
    Floor-only (coplanar) markers cannot reveal mirrored input: a mirrored
    set fits by flipping the floor, so the camera must be above the floor.
    """
    import numpy as np
    source, target = _points(source_points, 3, min_points), _points(target_points, 3, min_points)
    if len(source) != len(target):
        raise ValueError('paired marker observations required')
    _spread(target, min_spread_m)
    rotation, translation = _kabsch(source, target)
    residuals = np.linalg.norm(source @ rotation.T+translation-target, axis=1)
    loo = []
    for index in range(len(source)):
        keep = np.arange(len(source)) != index
        r, t = _kabsch(source[keep], target[keep])
        loo.append(float(np.linalg.norm(r @ source[index]+t-target[index])))
    if np.all(np.abs(target[:, 2]) < 1e-9) and translation[2] < min_camera_height_m:
        raise ValueError('camera must be above the floor; check marker handedness and units')
    matrix = [[float(v) for v in rotation[i]]+[float(translation[i])] for i in range(3)]+[[0.0, 0.0, 0.0, 1.0]]
    # Reuse the production validator: orthonormal, det=+1, metres.
    transform_point({'frame': 'source', 'xyz_m': [0, 0, 0]}, matrix, source_frame='source',
                    target_frame='map', version='fit', calibration_error_m=0)
    return {'matrix': matrix, 'residuals_m': [float(v) for v in residuals],
            'rms_m': float(np.sqrt(np.mean(residuals**2))), 'max_m': float(residuals.max()),
            'loo_max_m': max(loo), 'error_bound_m': max(float(residuals.max()), max(loo)),
            'points': len(source)}


def _homography(pixels, floor):
    import numpy as np
    def normalizer(points):
        centre = points.mean(axis=0)
        scale = math.sqrt(2)/max(1e-12, float(np.mean(np.linalg.norm(points-centre, axis=1))))
        return np.array([[scale, 0, -scale*centre[0]], [0, scale, -scale*centre[1]], [0, 0, 1]])
    tp, tf = normalizer(pixels), normalizer(floor)
    p = np.c_[pixels, np.ones(len(pixels))] @ tp.T
    f = np.c_[floor, np.ones(len(floor))] @ tf.T
    rows = []
    for (u, v, _), (x, y, _) in zip(p, f):
        rows.append([-u, -v, -1, 0, 0, 0, x*u, x*v, x])
        rows.append([0, 0, 0, -u, -v, -1, y*u, y*v, y])
    h = np.linalg.svd(np.asarray(rows))[2][-1].reshape(3, 3)
    h = np.linalg.inv(tf) @ h @ tp
    w = np.c_[pixels, np.ones(len(pixels))] @ h[2]
    if np.all(w < 0):
        h, w = -h, -w
    if not np.all(w > 1e-12):
        raise ValueError('markers straddle the floor horizon')
    return h/np.abs(h).max()


def _floor_error(h, pixels, floor):
    import numpy as np
    q = np.c_[pixels, np.ones(len(pixels))] @ h.T
    return np.linalg.norm(q[:, :2]/q[:, 2:3]-floor, axis=1)


def fit_floor_homography(pixels, floor_xy, *, min_points=6, min_spread_m=.25):
    """RGB pixel -> map floor XY homography (w>0 on the floor) with LOO bound.

    Six or more markers: four would fit exactly and report no error at all.
    """
    import numpy as np
    pixels, floor = _points(pixels, 2, min_points), _points(floor_xy, 2, min_points)
    if len(pixels) != len(floor):
        raise ValueError('paired marker observations required')
    _spread(floor, min_spread_m)
    h = _homography(pixels, floor)
    residuals = _floor_error(h, pixels, floor)
    loo = []
    for index in range(len(pixels)):
        keep = np.arange(len(pixels)) != index
        loo.append(float(_floor_error(_homography(pixels[keep], floor[keep]), pixels[index:index+1], floor[index:index+1])[0]))
    return {'matrix': [[float(v) for v in row] for row in h], 'residuals_m': [float(v) for v in residuals],
            'max_m': float(residuals.max()), 'loo_max_m': max(loo),
            'error_bound_m': max(float(residuals.max()), max(loo)), 'points': len(pixels), **RGB_FRAME}


def _area(polygon):
    return sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(polygon, polygon[1:]+polygon[:1]))/2


def _cross(o, a, b):
    return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])


def _segments_meet(a, b, c, d):
    def on(p, q, r):
        return min(p[0], r[0]) <= q[0] <= max(p[0], r[0]) and min(p[1], r[1]) <= q[1] <= max(p[1], r[1])
    d1, d2, d3, d4 = _cross(c, d, a), _cross(c, d, b), _cross(a, b, c), _cross(a, b, d)
    if ((d1 > 0) != (d2 > 0) and d1 and d2) and ((d3 > 0) != (d4 > 0) and d3 and d4):
        return True
    return ((d1 == 0 and on(c, a, d)) or (d2 == 0 and on(c, b, d))
            or (d3 == 0 and on(a, c, b)) or (d4 == 0 and on(a, d, b)))


def _inside(point, polygon):
    inside = False
    for a, b in zip(polygon, polygon[1:]+polygon[:1]):
        if (a[1] > point[1]) != (b[1] > point[1]) and point[0] < (b[0]-a[0])*(point[1]-a[1])/(b[1]-a[1])+a[0]:
            inside = not inside
    return inside


def _edges(polygon):
    return list(zip(polygon, polygon[1:]+polygon[:1]))


def validate_den_map(model, *, site_id, map_id):
    """Den-local Apartment model with exactly three owner-confirmed furniture zones.

    Schema: {"revision": int|str, "meta": {"frame": "z_up_metric_floor0",
    "units": "metres"}, "zones": [{"id", "name", "zone_type" in FURNITURE_TYPES,
    "floor_polygon": [[x, y], ...] (3-32 vertices, simple, >=0.05 m2),
    "confirmation": {"by": "owner", "at": "YYYY-MM-DD"}}], "targets": [...]}.
    Zones must not overlap or touch. Returns the site binding, the managed JEV
    zone_bindings and the normalized room map.
    """
    from room_context import apartment_map
    meta = model.get('meta') if isinstance(model, dict) else None
    revision = model.get('revision') if isinstance(model, dict) else None
    if (not isinstance(meta, dict) or meta.get('frame') != 'z_up_metric_floor0' or meta.get('units', 'metres') != 'metres'
            or not ((type(revision) is int and revision >= 1) or (isinstance(revision, str) and 1 <= len(revision) <= 64))
            or not isinstance(site_id, str) or not site_id or not isinstance(map_id, str) or not map_id):
        raise ValueError('den-local metric z_up_metric_floor0 model with a revision required')
    zones = model.get('zones')
    if not isinstance(zones, list) or len(zones) != 3:
        raise ValueError('exactly three furniture zones required')
    ids, types, polygons = set(), set(), []
    for zone in zones:
        polygon = zone.get('floor_polygon') if isinstance(zone, dict) else None
        if (not isinstance(zone, dict) or not isinstance(zone.get('id'), str) or not _ZONE_ID.fullmatch(zone['id'])
                or zone['id'] in ids or zone.get('zone_type') not in FURNITURE_TYPES or zone['zone_type'] in types):
            raise ValueError('unique zone ids and unique furniture zone types required')
        confirmation = zone.get('confirmation')
        if (not isinstance(confirmation, dict) or confirmation.get('by') != 'owner'
                or not isinstance(confirmation.get('at'), str) or not _DATE.fullmatch(confirmation['at'])):
            raise ValueError('owner confirmation required for every zone')
        if (not isinstance(polygon, list) or not 3 <= len(polygon) <= 32
                or any(not isinstance(p, list) or len(p) != 2 or not _finite(p) or max(map(abs, p)) > 50 for p in polygon)
                or abs(_area(polygon)) < .05):
            raise ValueError('finite floor polygon of 3-32 vertices and >=0.05 m2 required')
        edges = _edges(polygon)
        for i, (a, b) in enumerate(edges):
            for j in range(i+2, len(edges)):
                if (i or j != len(edges)-1) and _segments_meet(a, b, *edges[j]):
                    raise ValueError('floor polygon must be simple')
        ids.add(zone['id']); types.add(zone['zone_type']); polygons.append(polygon)
    for i, first in enumerate(polygons):
        for second in polygons[i+1:]:
            if (any(_segments_meet(a, b, c, d) for a, b in _edges(first) for c, d in _edges(second))
                    or _inside(first[0], second) or _inside(second[0], first)):
                raise ValueError('furniture zones must not overlap or touch')
    binding = {'site_id': site_id, 'map_id': map_id, 'revision': str(revision), 'units': 'metres',
               'model_sha256': digest(model)}
    return {'binding': binding, 'zone_bindings': {z['id']: z['zone_type'] for z in zones},
            'room_map': apartment_map(model, binding)}


def reprojection_report(residuals_px, *, max_px=CRITERIA['max_reprojection_px'], min_pairs=6):
    """RGB-depth reprojection acceptance: nearest-rank p95 <= max_px at 640x360."""
    values = [float(v) for v in residuals_px]
    if len(values) < min_pairs or not _finite(values) or min(values) < 0:
        raise ValueError(f'at least {min_pairs} finite reprojection residuals required')
    p95 = nearest_rank(values, .95)
    return {'pairs': len(values), 'median_px': nearest_rank(values, .5), 'p95_px': p95,
            'max_px': max(values), 'criterion_px': max_px, 'status': 'passed' if p95 <= max_px else 'failed'}


def _method_report(positions, method, room_map, criteria):
    errors, abstentions, measured = [], {}, 0
    zones = {'correct': 0, 'boundary': 0, 'confident_wrong': 0, 'unknown': 0}
    classes, boundaries = set(), set()
    for position in positions:
        estimate = (position.get('estimates') or {}).get(method)
        status = estimate.get('status') if isinstance(estimate, dict) else None
        xy = estimate.get('xy') if isinstance(estimate, dict) else None
        uncertainty = estimate.get('uncertainty_m') if isinstance(estimate, dict) else None
        if (status not in ('measured', 'estimated') or not isinstance(xy, list) or len(xy) != 2 or not _finite(xy)
                or not _finite([uncertainty]) or uncertainty < 0):
            reason = estimate.get('reason') if isinstance(estimate, dict) and isinstance(estimate.get('reason'), str) else 'not_recorded'
            abstentions[reason] = abstentions.get(reason, 0)+1
            continue
        if not _recomputed(estimate):
            abstentions['not_recomputed'] = abstentions.get('not_recomputed', 0)+1
            continue
        measured += 1
        # Coverage counts only positions this method actually measured.
        classes.add(position['range_class'])
        if position.get('boundary_of') is not None:
            boundaries.add(position['boundary_of'])
        errors.append(math.dist(xy, position['truth_xy']))
        for zone in room_map['zones']:
            relation = zone_relation({'xyz_m': [xy[0], xy[1], 0.0], 'uncertainty_m': uncertainty}, zone['polygon'],
                site_id=room_map['site_id'], map_site_id=room_map['site_id'], map_version=room_map['revision'],
                floor_axes=room_map['floor_axes'])['relation']
            truth = position.get('zone_truth') == zone['id']
            if relation == 'boundary':
                zones['boundary'] += 1
            elif relation not in ('inside', 'outside'):
                zones['unknown'] += 1
            elif (relation == 'inside') == truth:
                zones['correct'] += 1
            else:
                zones['confident_wrong'] += 1
    zone_ids = {z['id'] for z in room_map['zones']}
    # A position with no recorded input for this method is not an attempt; every
    # other non-measurement (unknown, not recomputed) counts against the method.
    attempted = measured+sum(v for k, v in abstentions.items() if k != 'not_recorded')
    report = {'positions': len(positions), 'measured': measured, 'abstentions': abstentions,
              'abstained': sum(abstentions.values()), 'zone_decisions': zones,
              'abstention_fraction': (attempted-measured)/attempted if attempted else None,
              'coverage': {'range_classes': sorted(classes), 'boundaries': sorted(boundaries),
                           'status': 'passed' if classes == set(RANGE_CLASSES) and boundaries == zone_ids else 'failed'}}
    if errors:
        report.update(median_horizontal_m=nearest_rank(errors, .5), p95_horizontal_m=nearest_rank(errors, .95),
                      max_horizontal_m=max(errors))
    if not measured:
        report['status'] = 'not_evaluated'
    else:
        report['status'] = 'passed' if (measured >= criteria['min_positions']
            and report['median_horizontal_m'] <= criteria['median_horizontal_m']
            and report['p95_horizontal_m'] <= criteria['p95_horizontal_m']
            and report['coverage']['status'] == 'passed'
            and zones['confident_wrong'] <= criteria['max_confident_wrong']
            and report['abstention_fraction'] <= criteria['max_abstention_fraction']) else 'failed'
    return report


def evaluate_reference_positions(positions, room_map, *, criteria=CRITERIA, sensor_error_m=None):
    """Per-method horizontal accuracy, zone decisions and depth-range accuracy.

    positions: [{"id", "truth_xy": [x, y], "range_class": near|mid|far,
    "boundary_of": zone id|null, "zone_truth": zone id|"none",
    "estimates": {"stereo"|"rgb": {"status": "measured"|"estimated", "xy",
    "uncertainty_m", "provenance"} | {"status": "unknown", "reason"}},
    "depth_range": {"true_m", "measured_m", "provenance"}}]. Estimates and
    ranges count only with provenance {"kind": "recomputed", "input_sha256"}
    (recomputed from recorded inputs); typed values are 'not_recomputed'.
    A method passes only when its own measured positions cover every range
    class and zone boundary, with no confident wrong zone decision and a
    bounded abstention fraction. sensor_error_m is the stereo sensor error the
    estimates assumed; apply_receipts never lets live use less.
    """
    if sensor_error_m is not None and (not _finite([sensor_error_m]) or sensor_error_m < 0):
        raise ValueError('finite non-negative sensor error required')
    if not isinstance(positions, list) or len(positions) < criteria['min_positions']:
        raise ValueError(f"at least {criteria['min_positions']} reference positions required")
    zone_ids = {z['id'] for z in room_map['zones']}
    seen = set()
    for position in positions:
        truth = position.get('truth_xy') if isinstance(position, dict) else None
        if (not isinstance(position, dict) or not isinstance(position.get('id'), str) or position['id'] in seen
                or not isinstance(truth, list) or len(truth) != 2 or not _finite(truth)
                or position.get('range_class') not in RANGE_CLASSES
                or position.get('zone_truth') not in zone_ids | {'none'}
                or position.get('boundary_of') not in zone_ids | {None}):
            raise ValueError('reference position needs id, measured truth, range class and zone truth')
        seen.add(position['id'])
    coverage = {'range_classes': sorted({p['range_class'] for p in positions}),
                'boundaries': sorted({p['boundary_of'] for p in positions} - {None})}
    coverage['status'] = ('passed' if set(coverage['range_classes']) == set(RANGE_CLASSES)
                          and set(coverage['boundaries']) == zone_ids else 'failed')
    methods = {method: _method_report(positions, method, room_map, criteria) for method in METHODS}
    ratios, absolute, typed = [], [], 0
    for position in positions:
        pair = position.get('depth_range')
        if isinstance(pair, dict) and _finite([pair.get('true_m'), pair.get('measured_m')]) and pair['true_m'] > 0:
            if not _recomputed(pair):
                typed += 1
                continue
            error = abs(pair['measured_m']-pair['true_m'])
            absolute.append(error)
            ratios.append(error/max(criteria['depth_range_floor_m'], criteria['depth_range_fraction']*pair['true_m']))
    depth = {'pairs': len(ratios), 'not_recomputed': typed, 'status': 'not_evaluated'}
    if ratios:
        depth.update(p95_normalized=nearest_rank(ratios, .95), p95_abs_m=nearest_rank(absolute, .95),
                     max_abs_m=max(absolute), status='passed' if len(ratios) >= criteria['min_positions']
                     and nearest_rank(ratios, .95) <= 1 else 'failed')
    result = {'criteria': dict(criteria), 'coverage': coverage, 'methods': methods, 'depth_range': depth}
    if sensor_error_m is not None:
        result['assumptions'] = {'sensor_error_m': sensor_error_m}
    return result


def mount_signature(depth_mm, *, grid=(8, 8)):
    """Coarse static-scene signature: per-cell median of valid depth (mm) or null."""
    import numpy as np
    depth = np.asarray(depth_mm)
    rows, cols = grid
    if depth.ndim != 2 or depth.dtype != np.uint16 or not 2 <= rows <= 32 or not 2 <= cols <= 32:
        raise ValueError('uint16 depth plane and bounded grid required')
    cells = []
    for r in range(rows):
        row = []
        for c in range(cols):
            cell = depth[r*depth.shape[0]//rows:(r+1)*depth.shape[0]//rows, c*depth.shape[1]//cols:(c+1)*depth.shape[1]//cols]
            valid = cell[cell > 0]
            row.append(float(np.median(valid)) if valid.size >= .5*cell.size else None)
        cells.append(row)
    return {'grid': [rows, cols], 'shape': list(depth.shape), 'cells': cells}


def check_mount(signature, depth_mm, *, tolerance_mm=50, tolerance_fraction=.03,
                changed_fraction=.5, minimum_cells=16):
    """Guard against camera movement; not a substitute for a new walkthrough.

    'changed' when most comparable static cells moved; 'unknown' when too few
    cells are comparable. Either result must stop 'validated' geometry.
    """
    current = mount_signature(depth_mm, grid=tuple(signature['grid']))
    if current['shape'] != signature['shape']:
        return {'status': 'changed', 'reason': 'depth_shape_changed'}
    compared = changed = 0
    for reference_row, current_row in zip(signature['cells'], current['cells']):
        for reference, value in zip(reference_row, current_row):
            if reference is None or value is None:
                continue
            compared += 1
            changed += abs(value-reference) > max(tolerance_mm, tolerance_fraction*reference)
    if compared < minimum_cells:
        return {'status': 'unknown', 'compared_cells': compared, 'changed_cells': changed}
    return {'status': 'changed' if changed > changed_fraction*compared else 'unchanged',
            'compared_cells': compared, 'changed_cells': changed}


def receipt(kind, body):
    """Content-addressed receipt; its id is the sha256 of everything else."""
    if kind not in KINDS or not isinstance(body, dict):
        raise ValueError('known receipt kind and body required')
    value = {**deepcopy(body), 'schema': SCHEMA, 'kind': kind}
    value.pop('id', None)
    return {**value, 'id': 'sha256:'+digest(value)}


def verify_receipt(value):
    if not isinstance(value, dict) or value.get('schema') != SCHEMA or value.get('kind') not in KINDS:
        raise ValueError('spatial receipt required')
    body = {k: v for k, v in value.items() if k != 'id'}
    if value.get('id') != 'sha256:'+digest(body):
        raise ValueError('spatial receipt content does not match its id')
    return value


def registration_record(fit, *, site_id, camera_id, calibration_content_id, mount_revision, room_map,
                        floor=None, mount_signature=None):
    """Fitted (not yet validated) camera->map registration in the room_context shape.

    transform_version and the homography id cover the error bounds too, so an
    edited bound no longer matches its receipts.
    """
    transform_version = _transform_version(fit['matrix'], fit['error_bound_m'], calibration_content_id,
                                           mount_revision, room_map['id'], room_map['revision'])
    record = {'site_id': site_id, 'camera_id': camera_id, 'map_id': room_map['id'],
              'map_version': room_map['revision'], 'source_frame': 'rectified_left',
              'target_frame': room_map['frame'], 'calibration_version': calibration_content_id,
              'mount_revision': mount_revision, 'transform_version': transform_version,
              'status': 'fitted', 'units': 'metres', 'validation_receipt': None,
              'error_bound_m': fit['error_bound_m'], 'matrix': deepcopy(fit['matrix'])}
    if floor is not None:
        record['floor_homography_candidate'] = {
            'id': _homography_id(floor['matrix'], floor['error_bound_m'], transform_version), 'matrix': deepcopy(floor['matrix']),
            'error_bound_m': floor['error_bound_m'], **RGB_FRAME}
    if mount_signature is not None:
        record['mount_signature'] = deepcopy(mount_signature)
    return record


def _transform_version(matrix, error_bound_m, calibration_content_id, mount_revision, map_id, map_version):
    return 'tf-'+digest([matrix, error_bound_m, calibration_content_id, mount_revision, map_id, map_version])[:24]


def _homography_id(matrix, error_bound_m, transform_version):
    return 'fh-'+digest([matrix, error_bound_m, transform_version])[:24]


def binding(registration):
    return {'site_id': registration['site_id'], 'camera_id': registration['camera_id'],
            'calibration_content_id': registration['calibration_version'],
            'mount_revision': registration['mount_revision'], 'map_id': registration['map_id'],
            'map_version': registration['map_version'], 'transform_version': registration['transform_version']}


def _homography_version(registration):
    return (registration.get('floor_homography_candidate') or registration.get('floor_homography') or {}).get('id')


def fit_receipt(registration, fit, *, markers, floor=None):
    """Records the fitted matrix; inputs are bound by digest, not copied."""
    return receipt('registration_fit', {**binding(registration), 'matrix': deepcopy(registration['matrix']),
                   'fit': fit, 'floor_fit': floor, 'homography_version': _homography_version(registration),
                   'markers_sha256': digest(markers), 'status': 'fitted'})


def reference_receipt(registration, evaluation, *, positions):
    methods = evaluation['methods']
    return receipt('reference_positions', {**binding(registration), 'homography_version': _homography_version(registration),
                   'evaluation': evaluation, 'positions_sha256': digest(positions),
                   'status': {m: methods[m]['status'] for m in METHODS} | {'coverage': evaluation['coverage']['status'],
                              'depth_range': evaluation['depth_range']['status']}})


def reprojection_receipt(registration, report, *, pairs):
    return receipt('reprojection', {**binding(registration), 'report': report, 'pairs_sha256': digest(pairs),
                   'status': report['status']})


def mount_invalidation_receipt(registration, check):
    # 'unknown' (too few comparable cells) is inconclusive, not a movement.
    if check.get('status') != 'changed':
        raise ValueError('only a changed mount check invalidates')
    return receipt('mount_invalidation', {**binding(registration), 'check': check, 'status': 'invalidated'})


def apply_receipts(registration, calibration, receipts):
    """Return (registration, calibration) validated only by matching passed receipts.

    - registration.status 'validated' needs a registration_fit receipt for this
      exact matrix and error bound and a reference_positions receipt whose
      stereo method and coverage passed;
    - floor_homography needs that receipt's rgb method passed for the candidate;
    - calibration 'validated' needs the depth-range criterion passed, and its
      reprojection block needs a passed reprojection receipt; the live sensor
      error bound is never below the one the evaluation assumed;
    - a failed receipt of the same kind for the same binding vetoes that item
      ('failed_receipt'), so a passing retry cannot hide a failing attempt;
    - transform_version and the homography id must recompute from the
      registration's own contents (matrix, error bounds, binding);
    - any mount_invalidation for this mount revision refuses everything.
    Inputs are never mutated.
    """
    registration, calibration = deepcopy(registration), deepcopy(calibration)
    expected = binding(registration)
    if (calibration.get('id') != expected['calibration_content_id'] or calibration.get('mount_revision') != expected['mount_revision']
            or calibration.get('site_id') != expected['site_id'] or calibration.get('camera_id') != expected['camera_id']):
        raise ValueError('calibration and registration bindings differ')
    if expected['transform_version'] != _transform_version(registration.get('matrix'), registration.get('error_bound_m'),
            expected['calibration_content_id'], expected['mount_revision'], expected['map_id'], expected['map_version']):
        raise ValueError('registration content does not match its transform_version')
    matched = {}
    for value in receipts:
        verify_receipt(value)
        common = {k: expected[k] for k in ('site_id', 'camera_id', 'calibration_content_id', 'mount_revision')}
        if any(value.get(k) != v for k, v in common.items()):
            raise ValueError('receipt binds another camera, calibration or mount')
        if value['kind'] == 'mount_invalidation':
            raise ValueError('mount revision invalidated; register under a new mount_revision')
        if value['kind'] in ('registration_fit', 'reference_positions') and any(
                value.get(k) != expected[k] for k in ('map_id', 'map_version', 'transform_version')):
            raise ValueError('receipt binds another map revision or transform')
        matched.setdefault(value['kind'], []).append(value)
    fit = [r for r in matched.get('registration_fit', []) if r.get('matrix') == registration['matrix']
           and (r.get('fit') or {}).get('error_bound_m') == registration['error_bound_m']]
    reference = matched.get('reference_positions', [])
    summary = {'registration': 'unvalidated', 'floor_homography': 'unvalidated',
               'depth': 'unvalidated', 'reprojection': 'unvalidated'}
    current = registration.pop('floor_homography', None)
    candidate = registration.pop('floor_homography_candidate', None) or (
        {k: v for k, v in current.items() if k != 'validation_receipt'} if current else None)
    if candidate is not None and candidate.get('id') != _homography_id(
            candidate.get('matrix'), candidate.get('error_bound_m'), expected['transform_version']):
        raise ValueError('floor homography content does not match its id')
    method = lambda r, name: r['evaluation']['methods'][name]['status']
    coverage = lambda r: r['evaluation']['coverage']['status']
    stereo = [r for r in reference if method(r, 'stereo') == 'passed' and coverage(r) == 'passed']
    if any(method(r, 'stereo') == 'failed' or coverage(r) == 'failed' for r in reference):
        registration.update(status='fitted', validation_receipt=None)
        summary['registration'] = 'failed_receipt'
    elif fit and stereo:
        registration.update(status='validated', validation_receipt=stereo[0]['id'])
        summary['registration'] = 'validated'
        own = [r for r in stereo if candidate is not None and r.get('homography_version') == candidate['id']]
        rgb = [r for r in own if method(r, 'rgb') == 'passed']
        if any(method(r, 'rgb') == 'failed' for r in own):
            summary['floor_homography'] = 'failed_receipt'
        elif rgb:
            registration['floor_homography'] = {**candidate, 'validation_receipt': rgb[0]['id']}
            summary['floor_homography'] = 'validated'
    else:
        registration.update(status='fitted', validation_receipt=None)
    for key in ('validation_receipt', 'reprojection'):
        calibration.pop(key, None)
    calibration['status'] = 'unvalidated'
    # Live stereo error is never below the sensor error the evaluation assumed.
    assumed = lambda r: (r['evaluation'].get('assumptions') or {}).get('sensor_error_m')
    depth = [r for r in reference if r['evaluation']['depth_range']['status'] == 'passed'
             and _finite([assumed(r)]) and assumed(r) >= 0]
    if any(r['evaluation']['depth_range']['status'] == 'failed' for r in reference):
        summary['depth'] = 'failed_receipt'
    elif depth:
        calibration.update(status='validated', validation_receipt=depth[0]['id'],
                           error_bound_m=max(depth[0]['evaluation']['depth_range']['max_abs_m'], assumed(depth[0])))
        summary['depth'] = 'validated'
    projections = matched.get('reprojection', [])
    projected = [r for r in projections if r['report']['status'] == 'passed']
    if any(r['report']['status'] != 'passed' for r in projections):
        summary['reprojection'] = 'failed_receipt'
    elif projected:
        calibration['reprojection'] = {'receipt': projected[0]['id'], 'p95_px': projected[0]['report']['p95_px'],
                                       'max_px': projected[0]['report']['max_px']}
        summary['reprojection'] = 'validated'
    if registration.get('mount_signature') is not None:
        calibration['mount_signature'] = deepcopy(registration['mount_signature'])
    return registration, calibration, summary
