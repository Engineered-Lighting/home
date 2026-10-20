"""Bounded recorded depth decoding and conservative visible-patch localization.

The caller supplies a trusted calibration and a ROI already associated with the
rectified-left image. RGB boxes cannot be passed through as depth coordinates.
No camera access, model execution, or implicit camera-to-room registration.

Timing is causal to the associated RGB observation: depth age and skew are
measured against that observation, never against processing time, and the
result expires with the source observation (at most 3 s, never extended).
"""
import hashlib
import math
from geometry import robust_support, horizontal_extent, deproject, _finite

DERIVATION = 'depth-clock-v1'
SOURCE_PREFIX = 'depth-mapped-v1:'
MAX_VALIDITY_S = 3.0


def estimate_surface(depth, roi, intrinsics, *, floor_matrix, floor_axes, error_bound_m,
                     source_frame, calibration_version, mask=None, visible=True,
                     age_s=0.0, skew_s=0.0, max_age_s=0.5, max_skew_s=0.04):
    """Pure measurement shared by the live consumer and offline validation.

    Uncertainty is the floor-plane radius of the accepted support points around
    their median point plus the commissioned sensor error. The point is a
    visible front surface, biased toward the camera, not a body centre.
    """
    sample, support = robust_support(depth, roi, mask=mask, visible=visible, age_s=age_s,
                                     skew_s=skew_s, max_age_s=max_age_s, max_skew_s=max_skew_s)
    if sample['status'] != 'measured':
        return sample
    import numpy as np
    fx, fy, cx, cy = (intrinsics[k] for k in ('fx', 'fy', 'cx', 'cy'))
    u, v, z = support
    z = z/1000
    points = np.stack([(u-cx)*z/fx, (v-cy)*z/fy, z], axis=1)
    representative, radius, vertical = horizontal_extent(points, floor_matrix, floor_axes=floor_axes)
    return {**sample, 'xyz_m': representative, 'frame': source_frame,
            'calibration_version': calibration_version, 'method': 'stereo_surface',
            'representative': 'support_median', 'pixel': [float(np.median(u)), float(np.median(v))],
            'uncertainty_m': radius+error_bound_m, 'horizontal_support_radius_m': radius,
            'vertical_support_extent_m': vertical, 'uncertainty_axes': floor_axes,
            'uncertainty_kind': 'horizontal_support_extent_plus_sensor_error',
            'front_surface_bias': 'toward_camera'}


class RegisteredDepth:
    def __init__(self, store):
        self.store = store

    def surface(self, frame, roi, calibration, *, roi_frame, as_of, reference_observed_at,
                valid_until, floor_matrix, floor_axes='xy', visible=True, mask=None,
                max_age_s=0.5, max_skew_s=0.04):
        session = self.store.get_session(frame['session_id'])
        if any(frame.get(k) != session[k] for k in ('site_id', 'camera_id')):
            raise ValueError('depth source outside session')
        ids = frame.get('media_ids')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 8:
            raise ValueError('bounded registered media required')
        items = [self.store.get_media(session['id'], mid, generation=session['generation']) for mid in ids]
        candidates = [item for item in items if item['metadata'].get('format') == 'depth_u16_le']
        if len(candidates) != 1:
            raise ValueError('one unambiguous numerical depth source required')
        item = candidates[0]
        meta = item['metadata']
        for key in ('site_id', 'camera_id', 'frame_id', 'capture_epoch'):
            if not frame.get(key) or meta.get(key) != frame[key]:
                raise ValueError('depth frame provenance mismatch')
        if (not _finite([frame.get('observed_at'), as_of, reference_observed_at, valid_until])
                or meta.get('observed_at') != frame['observed_at']):
            raise ValueError('depth observation mapping mismatch')
        # Only the server-side clock mapper writes this source key; HTTP sample
        # ingestion cannot claim validated geometry or a verified clock.
        if (meta.get('derivation') != DERIVATION or not isinstance(meta.get('source_media_id'), str)
                or item.get('source_key') != SOURCE_PREFIX+meta['source_media_id']):
            raise ValueError('server-derived clock-mapped depth required')
        if (roi_frame != 'rectified_left' or meta.get('alignment_frame') != roi_frame
                or meta.get('units') != 'millimetres' or meta.get('invalid_value') != 0
                or meta.get('geometric_validity') != 'validated' or not meta.get('depth_validation_receipt')
                or meta.get('clock_mapping_status') != 'verified' or not meta.get('clock_mapping_id')):
            raise ValueError('validated rectified-left geometry and clock required')
        width, height = meta.get('width'), meta.get('height')
        if (type(width) is not int or type(height) is not int
                or not 1 <= width <= 1280 or not 1 <= height <= 800
                or item['size'] != width * height * 2):
            raise ValueError('numerical depth dimensions or byte length invalid')
        # Bind the session-independent calibration content, not the camera
        # session digest, so a camera restart does not orphan the validation.
        if (any(calibration.get(k) != meta.get(m) for k, m in (
                ('site_id', 'site_id'), ('camera_id', 'camera_id'), ('id', 'calibration_content_id'),
                ('frame', 'alignment_frame'), ('width', 'width'), ('height', 'height'),
                ('mount_revision', 'mount_revision')))
                or not calibration.get('id') or not calibration.get('mount_revision')
                or calibration.get('validation_receipt') != meta['depth_validation_receipt']
                or not _finite([calibration.get('error_bound_m')]) or calibration['error_bound_m'] < 0):
            raise ValueError('validated calibration binding required')
        # Validate intrinsics even when sampling later abstains.
        intrinsics = calibration['intrinsics']
        deproject([0, 0], 1, intrinsics, source_frame=roi_frame, calibration_version=calibration['id'])
        with self.store.media_path(session['id'], item['id']).open('rb') as source:
            data = source.read(item['size'] + 1)
        if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('registered depth bytes failed integrity check')
        unknown = lambda reason: {'status': 'unknown', 'reason': reason, 'depth_m': None}
        # Expiry follows the source observation and never exceeds its window.
        expiry = min(valid_until, reference_observed_at+MAX_VALIDITY_S,
                     meta['valid_until'] if _finite([meta.get('valid_until')]) else math.inf)
        if not _finite([meta.get('available_at')]) or meta['available_at'] > as_of:
            return unknown('depth_not_available_at_cutoff')
        if not frame['observed_at'] <= expiry or not reference_observed_at <= as_of <= expiry:
            return unknown('outside_valid_observation_interval')
        import numpy as np
        depth = np.frombuffer(data, dtype='<u2').reshape(height, width)
        skew = frame['observed_at']-reference_observed_at
        point = estimate_surface(depth, roi, intrinsics, floor_matrix=floor_matrix, floor_axes=floor_axes,
                                 error_bound_m=calibration['error_bound_m'], source_frame=roi_frame,
                                 calibration_version=calibration['id'], mask=mask, visible=visible,
                                 age_s=max(0.0, -skew), skew_s=skew, max_age_s=max_age_s, max_skew_s=max_skew_s)
        if point['status'] != 'measured':
            return point
        return {**point, 'mount_revision': calibration['mount_revision'],
                'session_id': session['id'], 'site_id': session['site_id'],
                'camera_id': session['camera_id'], 'frame_id': frame['frame_id'],
                'capture_epoch': frame['capture_epoch'], 'media_ids': [item['id']],
                'observed_at': frame['observed_at'], 'available_at': as_of,
                'reference_observed_at': reference_observed_at, 'valid_until': expiry,
                'processing_version': 'registered-depth-v2'}
