"""Recording-independent numerical mapping of depth onto a causal RGB clock.

Callers own authentication, original-receipt validation, source association,
freshness and storage. This module performs no acquisition or I/O and does
not confer geometric qualification. Both live and recorded adapters use the
same commissioned device-clock arithmetic without changing receipt times.
"""
from copy import deepcopy
import math


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def commissioning(value):
    """Validate the existing commissioned device-clock bounds, unchanged."""
    if (not isinstance(value, dict) or not isinstance(value.get('id'), str) or not 1 <= len(value['id']) <= 256
            or not isinstance(value.get('semantics_receipt'), str) or not value['semantics_receipt']
            or not all(_finite(value.get(k)) for k in ('drift_ppm', 'max_delta_s', 'semantics_bound_s'))
            or not 0 <= value['drift_ppm'] <= 1000 or not 0 < value['max_delta_s'] <= 3
            or not 0 <= value['semantics_bound_s'] <= .1):
        raise ValueError('commissioned device-clock bounds required')
    return deepcopy(value)


def map_depth_time(rgb_metadata, depth_frame, clock_commissioning):
    """Map one depth device timestamp using an already qualified RGB anchor.

    ``rgb_metadata`` retains the original schema-3 ``source_timing_receipt``
    and its observation/uncertainty fields. Exact RGB/bundle association and
    interval admission remain the caller's responsibility. No receipt time,
    processing time or packaging timestamp enters this calculation.
    """
    clock = commissioning(clock_commissioning)
    if not isinstance(rgb_metadata, dict) or not isinstance(depth_frame, dict):
        raise ValueError('depth frame identity and device clock required')
    sequence, device_s = depth_frame.get('sequence'), depth_frame.get('device_timestamp_seconds')
    if type(sequence) is not int or sequence < 0 or not _finite(device_s):
        raise ValueError('depth frame identity and device clock required')
    receipt = rgb_metadata.get('source_timing_receipt')
    if (not isinstance(receipt, dict) or type(receipt.get('frame_device_ns')) is not int
            or receipt['frame_device_ns'] < 0 or type(receipt.get('timestamp_quantization_ns')) is not int
            or not 1000 <= receipt['timestamp_quantization_ns'] <= 1_000_000
            or not _finite(rgb_metadata.get('observed_at'))
            or not _finite(rgb_metadata.get('uncertainty_seconds'))
            or not 0 <= rgb_metadata['uncertainty_seconds'] <= 1):
        raise ValueError('same-session causal RGB exposure receipt required')
    delta = device_s-receipt['frame_device_ns']/1e9
    if abs(delta) > clock['max_delta_s']:
        raise ValueError('depth frame too far from the causal RGB anchor')
    # Device timestamps are exported as float seconds: add 1 us rounding.
    components = {'anchor_s': rgb_metadata['uncertainty_seconds'], 'drift_s': clock['drift_ppm']*1e-6*abs(delta),
                  'quantization_s': receipt['timestamp_quantization_ns']/1e9+1e-6,
                  'semantics_s': clock['semantics_bound_s']}
    return {'observed_at': rgb_metadata['observed_at']+delta,
            'uncertainty_seconds': sum(components.values()),
            'delta_device_seconds': delta, 'uncertainty_components_s': components,
            'sequence': sequence}
