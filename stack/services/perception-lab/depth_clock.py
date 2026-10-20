"""Map one recorded OAK depth frame onto the causal RGB observation clock.

A new derived record is written; the original bundle and its receipt-time
views are never relabelled. The bridge is the DepthAI device clock shared by
one camera session (capture_epoch): the causal RGB receipt binds
frame_device_ns to an exposure-end UNIX observation with an uncertainty, and
the bundle carries the depth frame's device timestamp. Stereo-versus-RGB
timestamp semantics are not inferred: a commissioned bound is added, and
without commissioning nothing is mapped. Geometry is 'validated' only for the
validated calibration content and an unchanged mount; otherwise the mapped
record stays 'unvalidated' with a reason. Live recording only; no inference.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import io
import json
from bundle_ingest import calibration_content_id
from capture_timing import commissioning, map_depth_time
from reasoning import canonical, digest, finite
from registered_depth import DERIVATION, SOURCE_PREFIX, MAX_VALIDITY_S

BUNDLED_DERIVATION = 'depth-clock-bundled-v1'
BUNDLED_SOURCE_PREFIX = 'depth-mapped-bundled-v1:'


def bundled_depth_material(records, *, binding, clock):
    """Pure B-to-depth material for explicit bundled originals, never C=B."""
    from bundle_rgb_input import validate_support, encoded, sha
    depth, metadata, _ = validate_support(records, binding=binding)
    support = json.loads(records['support']['data'])
    anchor, bundle = records['anchor_b']['record'], records['bundle']['record']
    rgb = records['rgb']['record']; a = anchor['metadata']; receipt = a['source_timing_receipt']
    mapped = map_depth_time(a, metadata['frames']['depth'], clock)
    content = calibration_content_id(bundle['metadata']['calibration'])
    if bundle['metadata'].get('calibration_content_id', content) != content:
        raise ValueError('bundled depth calibration content changed')
    refs = {role: {k: item['record'][k] for k in ('id', 'sha256', 'size')} for role, item in records.items()}
    mapping = dict(method='device_clock_offset_from_bundled_B_v1', commissioning_id=clock['id'],
        semantics_receipt=clock['semantics_receipt'], depth_clock_policy_digest=sha(encoded(commissioning(clock))),
        input_policy_sha256=binding['policy_sha256'], original_family=binding['original_family'],
        original_media_refs=refs, rgb_media_id=anchor['id'], rgb_frame_id=a['frame_id'],
        rgb_validation_id=receipt['validation_id'], rgb_frame_device_ns=receipt['frame_device_ns'],
        C_media_id=rgb['id'], C_frame_id=rgb['metadata']['frame_id'],
        depth_device_seconds=metadata['frames']['depth']['device_timestamp_seconds'],
        delta_device_seconds=mapped['delta_device_seconds'],
        uncertainty_components_s=mapped['uncertainty_components_s'])
    observed, total = mapped['observed_at'], mapped['uncertainty_seconds']
    return depth, dict(site_id=binding['site_id'], camera_id=binding['camera_id'], capture_epoch=metadata['session'],
        stream='depth', modality='depth', format='depth_u16_le', width=640, height=400,
        units='millimetres', invalid_value=0, alignment_frame='rectified_left',
        calibration_id=bundle['metadata']['calibration_id'], calibration_content_id=content,
        derivation=BUNDLED_DERIVATION, source_media_id=bundle['id'], source_sha256=bundle['sha256'],
        frame_id=f"{metadata['session']}-depth-{mapped['sequence']}", sequence=mapped['sequence'],
        observed_at=observed, uncertainty_seconds=total,
        valid_until=min(observed+MAX_VALIDITY_S-total, a['valid_until'], rgb['metadata']['valid_until']),
        timeline_basis='device_clock_mapped_from_original_bundled_B', clock_mapping_status='verified',
        clock_mapping_id=BUNDLED_DERIVATION+':'+sha(encoded(mapping)), clock_mapping=mapping,
        archive_kind='sampled_latest_bundles', rgb_aligned=False)


def _bundle_key(item):
    """Only BundleIngestor writes [epoch, "rgbd", [[stream, sequence], ...]] keys."""
    try:
        key = json.loads(item.get('source_key') or '')
    except ValueError:
        return False
    return (isinstance(key, list) and len(key) == 3 and key[1] == 'rgbd' and isinstance(key[2], list)
            and key[0] == item['metadata'].get('capture_epoch'))


class DepthClock:
    def __init__(self, store, *, site_id, camera_id, clock, calibration=None):
        self.store, self.site_id, self.camera_id = store, site_id, camera_id
        self.clock = commissioning(clock)
        if calibration is not None and (not isinstance(calibration, dict) or calibration.get('status') != 'validated'
                or not calibration.get('id') or not calibration.get('mount_revision') or not calibration.get('validation_receipt')
                or calibration.get('site_id') != site_id or calibration.get('camera_id') != camera_id):
            raise ValueError('validated site-bound calibration record required')
        self.calibration = deepcopy(calibration)

    def _geometry(self, content_id, depth):
        calibration = self.calibration
        if calibration is None:
            return 'calibration_not_validated'
        if calibration['id'] != content_id:
            return 'calibration_content_changed'
        if calibration.get('mount_signature') is None:
            return 'mount_signature_missing'
        from spatial_validation import check_mount
        status = check_mount(calibration['mount_signature'], depth)['status']
        return None if status == 'unchanged' else 'mount_'+status

    def derive_bundled(self, sid, *, records, binding, started, terminal):
        """Additive same-Store derivation; old causal-JPEG derive is unchanged."""
        from bundle_rgb_input import ROLE_LIMITS, validate_support, encoded, sha
        session = self.store.get_session(sid)
        if (session['state'] != 'recording' or session['site_id'] != self.site_id
                or session['camera_id'] != self.camera_id or binding['session_id'] != sid
                or binding['media_generation'] != session['generation'] or set(records) != set(ROLE_LIMITS)):
            raise ValueError('current bundled depth source required')
        keys = {'id', 'session_id', 'generation', 'size', 'sha256', 'mime_type', 'metadata', 'created_at', 'source_key'}
        for role, item in records.items():
            row, raw = item['record'], item['data']
            actual = self.store.get_media(sid, row['id'], generation=session['generation'])
            if ({k: actual[k] for k in keys} != row or not isinstance(raw, bytes)
                    or not 0 < len(raw) <= ROLE_LIMITS[role] or len(raw) != actual['size'] or sha(raw) != actual['sha256']):
                raise ValueError('actual same-Store bundled originals required')
        validate_support(records, binding=binding, started=started, terminal=terminal)
        depth, record = bundled_depth_material(records, binding=binding, clock=self.clock)
        now = self.store.clock()
        if (record['uncertainty_seconds'] > 1 or record['observed_at']-record['uncertainty_seconds'] > now
                or not now < record['valid_until'] or record['observed_at'] < session['started_at']
                or max(item['record']['created_at'] for item in records.values()) > now):
            raise ValueError('bundled mapped depth future stale or uncertain')
        reason = self._geometry(record['calibration_content_id'], depth)
        record.update(geometric_validity='unvalidated' if reason else 'validated')
        if reason: record['geometric_status_reason'] = reason
        else: record.update(depth_validation_receipt=self.calibration['validation_receipt'], mount_revision=self.calibration['mount_revision'])
        # Physical sequence is the catalog key. Different interpretations or
        # dependencies refuse; retries preserve the first availability and lease.
        key = BUNDLED_SOURCE_PREFIX+sha(encoded([self.site_id, self.camera_id, record['capture_epoch'], record['sequence']]))
        with self.store.lock:
            prior = self.store.db.execute('SELECT id FROM media WHERE session_id=? AND source_key=?', (sid, key)).fetchone()
        payload = depth.astype('<u2', copy=False).tobytes(order='C')
        if prior:
            old = self.store.get_media(sid, prior[0], generation=session['generation'])
            if ({k: v for k, v in old['metadata'].items() if k != 'available_at'} != record
                    or old['sha256'] != sha(payload) or old['size'] != len(payload)):
                raise ValueError('first physical bundled depth interpretation changed')
            return old
        record['available_at'] = now
        if len(canonical(record).encode()) > 32768:
            raise ValueError('bounded bundled depth metadata required')
        return self.store.add_media(sid, payload, 'application/octet-stream', record, key)

    def derive(self, sid, bundle_media_id, rgb_media_id):
        session = self.store.get_session(sid)
        if session['state'] != 'recording' or session['site_id'] != self.site_id or session['camera_id'] != self.camera_id:
            raise ValueError('registered recording source required')
        key = SOURCE_PREFIX+bundle_media_id
        with self.store.lock:
            prior = self.store.db.execute('SELECT id FROM media WHERE session_id=? AND source_key=?', (sid, key)).fetchone()
        if prior:
            # One mapping per bundle; a retry never refreshes availability.
            return self.store.get_media(sid, prior[0], generation=session['generation'])
        bundle = self.store.get_media(sid, bundle_media_id, generation=session['generation'])
        rgb = self.store.get_media(sid, rgb_media_id, generation=session['generation'])
        bmeta, rmeta = bundle['metadata'], rgb['metadata']
        epoch = bmeta.get('capture_epoch')
        if (bmeta.get('stream') != 'rgbd_bundle' or bmeta.get('format') != 'numeric_npz' or not _bundle_key(bundle)
                or bmeta.get('site_id') != self.site_id or bmeta.get('camera_id') != self.camera_id):
            raise ValueError('server-ingested OAK bundle required')
        receipt = rmeta.get('source_timing_receipt')
        if (rmeta.get('stream') != 'rgb' or rmeta.get('format') != 'jpeg' or rmeta.get('clock_mapping_status') != 'verified'
                or rmeta.get('site_id') != self.site_id or rmeta.get('camera_id') != self.camera_id
                or rmeta.get('capture_epoch') != epoch or not isinstance(receipt, dict) or receipt.get('schema') != 3
                or receipt.get('timestamp_semantics') != 'causal_exact_frame_exposure_end' or receipt.get('session') != epoch
                or type(receipt.get('frame_device_ns')) is not int or receipt['frame_device_ns'] < 0
                or type(receipt.get('observed_at_unix_ns')) is not int
                or type(receipt.get('timestamp_quantization_ns')) is not int
                or not 1000 <= receipt['timestamp_quantization_ns'] <= 1_000_000
                # Match PreviewIngestor's one-round conversion of the original
                # integer. A float-first division can shift modern UTC by1ULP.
                or not finite(rmeta.get('observed_at'))
                or rmeta['observed_at'] != float(Decimal(receipt['observed_at_unix_ns']) / Decimal(1_000_000_000))
                or not finite(rmeta.get('uncertainty_seconds')) or not 0 <= rmeta['uncertainty_seconds'] <= 1):
            raise ValueError('same-session causal RGB exposure receipt required')
        with self.store.media_path(sid, bundle['id']).open('rb') as source:
            data = source.read(bundle['size']+1)
        if len(data) != bundle['size'] or hashlib.sha256(data).hexdigest() != bundle['sha256']:
            raise ValueError('recorded bundle failed integrity check')
        import numpy as np
        with np.load(io.BytesIO(data), allow_pickle=False) as archive:
            depth = archive['depth_mm']
            encoded = archive['metadata_json']
            if encoded.dtype != np.uint8 or encoded.ndim != 1 or len(encoded) > 32768:
                raise ValueError('bounded bundle metadata required')
            meta = json.loads(encoded.tobytes())
        if depth.shape != (400, 640) or depth.dtype != np.uint16 or meta != bmeta.get('source_metadata'):
            raise ValueError('bundle bytes and recorded metadata disagree')
        frame = meta['frames']['depth']
        mapped = map_depth_time(rmeta, frame, self.clock)
        sequence, device_s = mapped['sequence'], frame['device_timestamp_seconds']
        delta, components = mapped['delta_device_seconds'], mapped['uncertainty_components_s']
        total = mapped['uncertainty_seconds']
        observed, now = mapped['observed_at'], self.store.clock()
        valid_until = observed+MAX_VALIDITY_S-total
        if total > 1 or observed-total > now or now > valid_until or observed < session['started_at']:
            raise ValueError('mapped depth is future, stale or too uncertain')
        content_id = calibration_content_id(bmeta['calibration'])
        if bmeta.get('calibration_content_id', content_id) != content_id:
            raise ValueError('recorded calibration content id mismatch')
        mapping = {'method': 'device_clock_offset_from_causal_rgb_v1', 'commissioning_id': self.clock['id'],
                   'semantics_receipt': self.clock['semantics_receipt'], 'rgb_media_id': rgb['id'],
                   'rgb_frame_id': rmeta.get('frame_id'), 'rgb_validation_id': receipt.get('validation_id'),
                   'rgb_frame_device_ns': receipt['frame_device_ns'], 'depth_device_seconds': device_s,
                   'delta_device_seconds': delta, 'uncertainty_components_s': components}
        reason = self._geometry(content_id, depth)
        record = {'site_id': self.site_id, 'camera_id': self.camera_id, 'capture_epoch': epoch,
                  'stream': 'depth', 'modality': 'depth', 'format': 'depth_u16_le', 'width': 640, 'height': 400,
                  'units': 'millimetres', 'invalid_value': 0, 'alignment_frame': 'rectified_left',
                  'calibration_id': bmeta['calibration_id'], 'calibration_content_id': content_id,
                  'derivation': DERIVATION, 'source_media_id': bundle['id'], 'source_sha256': bundle['sha256'],
                  'frame_id': f'{epoch}-depth-{sequence}', 'sequence': sequence,
                  'observed_at': observed, 'available_at': now, 'valid_until': valid_until,
                  'uncertainty_seconds': total, 'session_time_ms': round((observed-session['started_at'])*1000),
                  'timeline_basis': 'device_clock_mapped', 'clock_mapping_status': 'verified',
                  'clock_mapping_id': DERIVATION+':'+digest([mapping, bundle['sha256'], rgb['sha256']]),
                  'clock_mapping': mapping, 'archive_kind': 'sampled_latest_bundles', 'rgb_aligned': False,
                  'geometric_validity': 'unvalidated' if reason else 'validated'}
        if reason:
            record['geometric_status_reason'] = reason
        else:
            record.update(depth_validation_receipt=self.calibration['validation_receipt'],
                          mount_revision=self.calibration['mount_revision'])
        if len(canonical(record).encode()) > 16384:
            raise ValueError('mapped depth metadata exceeds bound')
        payload = depth.astype('<u2', copy=False).tobytes(order='C')
        return self.store.add_media(sid, payload, 'application/octet-stream', record, key)
