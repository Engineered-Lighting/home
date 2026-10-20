"""Inputs for the coarse mount check, repaired without touching its limits.

spatial_validation.check_mount (50 mm / 3 %, more than half of at least 16
compared cells) is unchanged and its source stays pinned by the reviewed
alignment. This module only prepares what it compares:

- mask_people zeroes the depth pixels that land inside the current frame's own
  person boxes, so check_mount's existing 50 % support rule drops cells a person
  covers instead of reading a body as a moved room;
- combined_signature builds a reference from several frames taken with nobody
  near the camera (per-cell median of each frame's mount_signature cells);
- validate_replacement checks a reviewed replacement signature document.

Why: MOUNT-CHECK-RISK-20261019.md (the reviewed signature includes a person,
and a seated person alone changes 6-11 cells).
"""
import math
import re

REPLACEMENT_SCHEMA = 'home-apartment-mount-signature-replacement-1'
REPLACEMENT_KEYS = ('schema', 'version', 'revision', 'signature', 'signature_sha256', 'source_npz_sha256s',
                    'frames', 'minimum_supported_frames', 'algorithm', 'nobody_near_camera', 'lighting_note')
ALGORITHM = 'per_cell_median_of_mount_signature_cells'
MIN_FRAMES, MAX_FRAMES = 3, 9
MAX_BOXES = 8
RGB_SIZE = (640, 360)
HEX = re.compile(r'^[a-f0-9]{64}$')


class MountInputRefusal(ValueError):
    pass


def _need(ok, reason):
    if not ok:
        raise MountInputRefusal(reason)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def person_boxes(result):
    """Person boxes of one verified model output, in 640x360 source pixels."""
    _need(isinstance(result, dict), 'mount_person_boxes_invalid')
    boxes = result.get('boxes', [])
    _need(isinstance(boxes, list) and len(boxes) <= 64, 'mount_person_boxes_invalid')
    out = []
    for box in boxes:
        _need(isinstance(box, dict), 'mount_person_boxes_invalid')
        if box.get('label') != 'person':
            continue
        values = [box.get(k) for k in ('x', 'y', 'w', 'h')]
        _need(all(_finite(v) for v in values) and values[2] >= 0 and values[3] >= 0
              and -RGB_SIZE[0] <= values[0] <= 2 * RGB_SIZE[0] and -RGB_SIZE[1] <= values[1] <= 2 * RGB_SIZE[1]
              and values[2] <= 2 * RGB_SIZE[0] and values[3] <= 2 * RGB_SIZE[1], 'mount_person_boxes_invalid')
        out.append(tuple(float(v) for v in values))
    _need(len(out) <= MAX_BOXES, 'mount_person_boxes_invalid')
    return out


def nominal_camera_model(meta, calibration, calibration_content_id):
    """The same nominal EEPROM chain lift_supported_joints uses for the joints."""
    from depth_association import camera_model, eeprom_rgb_from_rectified_left
    reference = dict(id=calibration_content_id, mount_revision='diagnostic_nominal_eeprom',
        reference_intrinsics=dict(rectified_left=meta['frames']['depth']['intrinsics'], rgb=meta['frames']['rgb']['intrinsics']),
        rgb_from_rectified_left=eeprom_rgb_from_rectified_left(calibration['eeprom']))
    return camera_model(meta, reference, measuring_reprojection=True)


def mask_people(depth_mm, model, boxes):
    """Copy of depth_mm with every pixel that projects into a person box zeroed.

    Each valid rectified-left pixel is projected into RGB with its own depth.
    Returns (masked uint16 plane, number of pixels zeroed). No box, no change.
    """
    import numpy as np
    from depth_association import rgb_pixels
    depth = np.asarray(depth_mm)
    _need(depth.ndim == 2 and depth.dtype == np.uint16, 'mount_depth_plane_invalid')
    masked = depth.copy()
    if not boxes:
        return masked, 0
    valid = depth > 0
    pixels, ok = rgb_pixels(model, depth, rows=valid)
    pixels = np.asarray(pixels)
    inside = np.zeros(len(pixels), bool)
    for x, y, w, h in boxes:
        inside |= ok & (pixels[:, 0] >= x) & (pixels[:, 0] <= x + w) & (pixels[:, 1] >= y) & (pixels[:, 1] <= y + h)
    rows, cols = np.nonzero(valid)
    masked[rows[inside], cols[inside]] = 0
    return masked, int(inside.sum())


def combined_signature(depth_planes, *, grid=(8, 8)):
    """Per-cell median of several frames' mount_signature cells.

    A cell is kept only when more than half of the frames support it, so one
    frame cannot vouch for a cell alone. Same {grid, shape, cells} shape as
    spatial_validation.mount_signature.
    """
    import statistics
    from spatial_validation import mount_signature
    _need(isinstance(depth_planes, (list, tuple)) and MIN_FRAMES <= len(depth_planes) <= MAX_FRAMES,
          'mount_replacement_frame_count_invalid')
    signatures = [mount_signature(d, grid=grid) for d in depth_planes]
    shape = signatures[0]['shape']
    _need(all(s['shape'] == shape for s in signatures), 'mount_replacement_shape_changed')
    rows, cols = grid
    cells = []
    for r in range(rows):
        row = []
        for c in range(cols):
            values = [s['cells'][r][c] for s in signatures if s['cells'][r][c] is not None]
            row.append(float(statistics.median(values)) if 2 * len(values) > len(signatures) else None)
        cells.append(row)
    return {'grid': [rows, cols], 'shape': list(shape), 'cells': cells}


def validate_replacement(value, *, revision, digest):
    """Check a replacement document; return its signature. digest is the binder's own."""
    _need(isinstance(value, dict) and set(value) == set(REPLACEMENT_KEYS) and value['schema'] == REPLACEMENT_SCHEMA
          and type(value['version']) is int and value['version'] == 1, 'approximate_mount_replacement_required')
    _need(value['revision'] == revision, 'approximate_mount_replacement_revision_changed')
    signature = value['signature']
    _need(isinstance(signature, dict) and set(signature) == {'grid', 'shape', 'cells'}
          and signature['grid'] == [8, 8] and signature['shape'] == [400, 640]
          and isinstance(signature['cells'], list) and len(signature['cells']) == 8
          and all(isinstance(r, list) and len(r) == 8 and all(v is None or _finite(v) and 0 < v <= 65535 for v in r)
                  for r in signature['cells']), 'approximate_mount_replacement_signature_invalid')
    _need(value['signature_sha256'] == digest(signature), 'approximate_mount_replacement_signature_changed')
    pins = value['source_npz_sha256s']
    _need(isinstance(pins, list) and MIN_FRAMES <= len(pins) <= MAX_FRAMES and len(set(pins)) == len(pins)
          and all(isinstance(p, str) and HEX.fullmatch(p) for p in pins) and value['frames'] == len(pins)
          and value['minimum_supported_frames'] == len(pins) // 2 + 1 and value['algorithm'] == ALGORITHM
          and value['nobody_near_camera'] is True and isinstance(value['lighting_note'], str)
          and 0 < len(value['lighting_note']) <= 256, 'approximate_mount_replacement_provenance_required')
    supported = sum(v is not None for r in signature['cells'] for v in r)
    _need(supported >= 16, 'approximate_mount_replacement_too_few_cells')
    return signature
