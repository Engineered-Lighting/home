"""Existing config3 originals for the explicit operational stereo successor.

This consumes the incumbent tap/Store; it never fetches a camera or runs a
model. Assessment originals stay candidate-only. Only separately commissioned
DepthClock and the existing anchor's independent qualification can support a
derived operational point. Late receipts never become early model availability.
"""
from copy import deepcopy
from decimal import Decimal
import json
import math
import threading

from assessment_existing_api import (AssessmentExistingApiTap, START_KIND, START_SCHEMA,
    TERMINAL_KIND, TERMINAL_SCHEMA, WINDOW_KIND, ASSOCIATION_SCHEMA, ASSOCIATION_STREAM,
    CALIBRATION_STREAM, validate_match, encoded, sha)
from depth_clock import DepthClock
from journal import canonical
from registered_depth import DERIVATION, SOURCE_PREFIX
from room_facts import decode_bundle
from store import Store

FAMILY = 'existing-api-assessment-1'
ROLES = {'rgb', 'bundle', 'calibration_original', 'assessment_association', 'mapped_depth'}
LIMITS = {'rgb': 256 * 1024, 'bundle': 4 * 1024**2, 'calibration_original': 24576,
          'assessment_association': 256 * 1024, 'mapped_depth': 640 * 400 * 2}
BINDING_KEYS = {'scope', 'lab_binding_digest', 'assessment_policy_sha256'}
SCOPE_KEYS = {'site_id', 'room_id', 'session_id', 'domain_epoch'}
TAP_BINDING_KEYS = {'policy_sha256', 'site_id', 'camera_id', 'session_id', 'media_generation', 'experiment_generation'}


class ExistingApiStereoRefusal(ValueError):
    pass


def need(condition, reason):
    if not condition: raise ExistingApiStereoRefusal(reason)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _exact(value, keys, reason):
    need(isinstance(value, dict) and set(value) == set(keys), reason)


def _binding(value):
    _exact(value, BINDING_KEYS, 'fixed_existing_api_assessment_binding_required')
    _exact(value['scope'], SCOPE_KEYS, 'fixed_existing_api_scope_required')
    need(all(isinstance(v, str) and v for v in value['scope'].values()), 'fixed_existing_api_scope_required')
    need(all(isinstance(value[k], str) and len(value[k]) == 64
             and all(c in '0123456789abcdef' for c in value[k])
             for k in ('lab_binding_digest', 'assessment_policy_sha256')), 'fixed_existing_api_digest_required')
    return deepcopy(value)


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'existing_api_duplicate_json_key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: need(False, 'existing_api_finite_json_required'))


def _ref(row, *, generation=False):
    return {k: row[k] for k in (('id', 'generation', 'sha256', 'size') if generation else ('id', 'sha256', 'size'))}


def _event(value, kind, sid):
    _exact(value, {'id', 'session_id', 'kind', 'created_at', 'payload'}, 'original_assessment_event_required')
    need(type(value['id']) is int and value['id'] > 0 and value['session_id'] == sid
         and value['kind'] == kind and finite(value['created_at']), 'original_assessment_event_binding_changed')
    return value['payload']


def _chain(start, terminal, rows, expected):
    """Exact original lineage, independent of any geometry/person qualification."""
    sid = expected['scope']['session_id']
    s = _event(start, START_KIND, sid)
    t = _event(terminal, TERMINAL_KIND, sid)
    _exact(s, {'schema', 'version', 'binding', 'capture', 'tap_started_at'}, 'original_assessment_start_required')
    b, c = s['binding'], s['capture']
    _exact(b, TAP_BINDING_KEYS, 'original_assessment_binding_required')
    need(s['schema'] == START_SCHEMA and type(s['version']) is int and s['version'] == 1
         and b['policy_sha256'] == expected['assessment_policy_sha256']
         and b['site_id'] == expected['scope']['site_id'] and b['session_id'] == sid
         and isinstance(b['camera_id'], str) and b['camera_id']
         and type(b['media_generation']) is int and b['media_generation'] >= 0
         and type(b['experiment_generation']) is int and b['experiment_generation'] >= 1,
         'original_assessment_start_binding_changed')
    _exact(c, {'capture_id', 'rgb_frame_id', 'capture_epoch', 'rgb_media_ref', 'rgb_header', 'rgb_times'},
           'original_assessment_capture_required')
    _exact(c['rgb_times'], {'observed_at', 'available_at', 'valid_until'}, 'original_assessment_rgb_times_required')
    need(all(finite(v) for v in c['rgb_times'].values())
         and c['rgb_times']['observed_at'] <= c['rgb_times']['available_at'] < c['rgb_times']['valid_until']
         and finite(s['tap_started_at']) and c['rgb_times']['available_at'] <= s['tap_started_at'] <= start['created_at'],
         'original_assessment_start_clock_changed')
    _exact(t, {'schema', 'version', 'binding', 'capture_id', 'started_event_id', 'attempts',
              'status', 'reason', 'association_media_ref', 'processing_completed_at'}, 'original_assessment_terminal_required')
    need(t['schema'] == TERMINAL_SCHEMA and type(t['version']) is int and t['version'] == 1
         and terminal['id'] > start['id'] and t['binding'] == b and t['capture_id'] == c['capture_id']
         and t['started_event_id'] == start['id'] and t['status'] == 'retained'
         and t['reason'] == 'exact' and finite(t['processing_completed_at'])
         and t['processing_completed_at'] >= s['tap_started_at']
         and start['created_at'] <= terminal['created_at']
         and t['processing_completed_at'] <= terminal['created_at']
         and isinstance(t['attempts'], list) and 1 <= len(t['attempts']) <= 2,
         'original_assessment_terminal_binding_changed')
    previous = s['tap_started_at']
    for index, attempt in enumerate(t['attempts'], 1):
        _exact(attempt, {'ordinal', 'npz_media_ref', 'calibration_media_ref', 'npz_read_completed_at',
                        'calibration_read_completed_at', 'result'}, 'original_assessment_attempt_required')
        n, k = attempt['npz_read_completed_at'], attempt['calibration_read_completed_at']
        need(attempt['ordinal'] == index and isinstance(attempt['result'], str)
             and (n is None or finite(n) and previous <= n <= t['processing_completed_at'])
             and (k is None or n is not None and finite(k) and n <= k <= t['processing_completed_at']),
             'original_assessment_attempt_clock_changed')
        previous = k if k is not None else n if n is not None else previous
    for role, (row, raw) in rows.items():
        need(role in ROLES and isinstance(raw, bytes) and 0 < len(raw) <= LIMITS[role]
             and row['size'] == len(raw) and row['sha256'] == sha(raw)
             and row['session_id'] == sid and row['generation'] == b['media_generation']
             and finite(row['created_at']),
             'existing_api_original_bytes_or_generation_changed')
    rgb, bundle, calibration, association = [rows[k] for k in
        ('rgb', 'bundle', 'calibration_original', 'assessment_association')]
    need(rgb[0]['mime_type'] == 'image/jpeg' and bundle[0]['mime_type'] == 'application/x-npz'
         and calibration[0]['mime_type'] == association[0]['mime_type'] == 'application/json'
         and rgb[0]['metadata'].get('stream') == 'rgb'
         and bundle[0]['metadata'].get('stream') == 'rgbd_bundle'
         and calibration[0]['metadata'].get('stream') == CALIBRATION_STREAM
         and association[0]['metadata'].get('stream') == ASSOCIATION_STREAM
         and c['rgb_media_ref'] == _ref(rgb[0], generation=True)
         and t['association_media_ref'] == _ref(association[0], generation=True),
         'existing_api_original_family_changed')
    a = _json(association[1])
    _exact(a, {'schema', 'version', 'binding', 'capture', 'started_event_id', 'selected_attempt_ordinal',
              'bundle_media_ref', 'calibration_original_media_ref', 'source_metadata', 'calibration_sha256',
              'association_created_at', 'depth_clock', 'candidate_only', 'owner_local_only',
              'operational_geometry', 'identity_authority', 'engagement_authority', 'protection_authority', 'beam_authority'},
           'original_existing_api_association_required')
    need(a['schema'] == ASSOCIATION_SCHEMA and type(a['version']) is int and a['version'] == 1
         and a['binding'] == b and a['capture'] == c and a['started_event_id'] == start['id']
         and a['candidate_only'] is True and a['owner_local_only'] is True and a['operational_geometry'] is None
         and all(a[k] is False for k in ('identity_authority', 'engagement_authority', 'protection_authority', 'beam_authority'))
         and a['depth_clock'] == {'status': 'unassessed', 'mapping': None, 'exposure_error_bound_s': None}
         and a['bundle_media_ref'] == _ref(bundle[0], generation=True)
         and a['calibration_original_media_ref'] == _ref(calibration[0], generation=True)
         and type(a['selected_attempt_ordinal']) is int and 1 <= a['selected_attempt_ordinal'] <= len(t['attempts']),
         'existing_api_original_association_changed')
    selected = t['attempts'][a['selected_attempt_ordinal'] - 1]
    need(selected['result'] == 'exact' and selected['npz_media_ref'] == a['bundle_media_ref']
         and selected['calibration_media_ref'] == a['calibration_original_media_ref']
         and finite(a['association_created_at']) and selected['calibration_read_completed_at'] is not None
         and selected['calibration_read_completed_at'] <= a['association_created_at'] <= t['processing_completed_at'],
         'existing_api_original_selected_attempt_changed')
    cm, am = calibration[0]['metadata'], association[0]['metadata']
    need(all(cm.get(k) == v for k, v in dict(site_id=b['site_id'], camera_id=b['camera_id'],
             capture_epoch=c['capture_epoch'], capture_id=c['capture_id'], ordinal=a['selected_attempt_ordinal'],
             read_completed_at=selected['calibration_read_completed_at'], stream=CALIBRATION_STREAM,
             format='raw_json', clock_mapping_status='unverified', candidate_only=True).items())
         and calibration[0]['source_key'] == 'assessment-raw-' + sha(encoded([c['capture_id'], a['selected_attempt_ordinal'], 'calibration']))
         and all(am.get(k) == v for k, v in dict(site_id=b['site_id'], camera_id=b['camera_id'],
             capture_id=c['capture_id'], frame_id=c['rgb_frame_id'], rgb_media_id=rgb[0]['id'],
             stream=ASSOCIATION_STREAM).items())
         and association[0]['source_key'] == 'assessment-association-' + c['capture_id'],
         'existing_api_original_capture_metadata_changed')
    need(rgb[0]['created_at'] <= s['tap_started_at']
         and selected['npz_read_completed_at'] <= bundle[0]['created_at'] <= a['association_created_at']
         and selected['calibration_read_completed_at'] <= calibration[0]['created_at'] <= a['association_created_at']
         and a['association_created_at'] <= association[0]['created_at'] <= t['processing_completed_at'],
         'existing_api_original_envelope_clock_changed')
    checked = validate_match(rgb[1], c['rgb_header'], bundle[1], calibration[1])
    m = rgb[0]['metadata']
    need(checked['source_metadata'] == a['source_metadata'] == bundle[0]['metadata']['source_metadata']
         and checked['calibration'] == bundle[0]['metadata']['calibration']
         and sha(encoded(checked['calibration'])) == a['calibration_sha256']
         and m['source_timing_receipt'] == checked['receipt']
         and all(m[k] == c['rgb_times'][k] for k in ('observed_at', 'available_at', 'valid_until'))
         and m['frame_id'] == c['rgb_frame_id'] and m['capture_epoch'] == c['capture_epoch']
         and m['site_id'] == b['site_id'] and m['camera_id'] == b['camera_id']
         and m['sequence'] == checked['receipt']['sequence'] and m['observed_at'] == checked['observed_at']
         and c['capture_id'] == 'assessment-' + sha(encoded([b,
             [checked['receipt']['session'], checked['receipt']['timing_generation'], checked['receipt']['sequence']]])),
         'existing_api_exact_capture_original_changed')
    return dict(association=a, calibration=checked['calibration'], source_metadata=checked['source_metadata'])


def validate_existing_api_originals(support, *, expected_assessment_binding, resolve_media):
    """Pure binary/raw-chain verification; no live Store or qualification fallback."""
    expected = _binding(expected_assessment_binding)
    from lab_live_desk_anchor import SUPPORT_KEYS
    _exact(support, SUPPORT_KEYS | {'original_family', 'assessment_originals'}, 'closed_existing_api_stereo_support_required')
    need(callable(resolve_media), 'fixed_existing_api_binary_resolver_required')
    need(support.get('schema') == 'home-desk-stereo-nose-anchor-2' and type(support.get('version')) is int
         and support['version'] == 2 and support.get('original_family') == FAMILY
         and support.get('scope') == expected['scope']
         and support.get('lab_binding_digest') == expected['lab_binding_digest'], 'existing_api_support_binding_changed')
    original = support['assessment_originals']
    _exact(original, {'binding', 'start', 'terminal'}, 'complete_existing_api_original_events_required')
    need(original['binding'] == expected, 'existing_api_original_policy_changed')
    _exact(support['media_refs'], ROLES, 'five_existing_api_binary_roles_required')
    rows = {}
    for role, requested in support['media_refs'].items():
        _exact(requested, {'id', 'sha256', 'size'}, 'exact_existing_api_binary_reference_required')
        need(type(requested['size']) is int and 0 < requested['size'] <= LIMITS[role], 'existing_api_binary_size_bound')
        found = resolve_media(deepcopy(requested))
        _exact(found, {'record', 'data'}, 'retained_existing_api_binary_required')
        need(_ref(found['record']) == requested, 'existing_api_binary_reference_changed')
        rows[role] = (found['record'], found['data'])
    checked = _chain(original['start'], original['terminal'], rows, expected)
    need(finite(support['first_consumer_received_at']) and finite(support['produced_at'])
         and _complete_available(original['start'], original['terminal'], rows)
         <= support['first_consumer_received_at'] <= support['produced_at'], 'existing_api_support_precedes_originals')
    b = original['start']['payload']['binding']
    need(support['media_generation'] == b['media_generation'] and support['model_generation'] == b['experiment_generation'],
         'existing_api_support_generation_changed')
    frame = rows['rgb'][0]['metadata']
    need(support['media_binding_digest'] == sha(encoded(dict(scope=expected['scope'],
        lab_binding_digest=expected['lab_binding_digest'], media_generation=b['media_generation'],
        camera_id=b['camera_id'], original_family=FAMILY,
        assessment_policy_sha256=expected['assessment_policy_sha256']))), 'existing_api_binary_binding_changed')
    plane, metadata = decode_bundle(rows['bundle'][1])
    depth, raw = rows['mapped_depth']
    dm = depth['metadata']
    import numpy as np
    need(depth['mime_type'] == 'application/octet-stream' and len(raw) == 640 * 400 * 2
         and np.array_equal(np.frombuffer(raw, dtype='<u2').reshape(400, 640), plane)
         and depth['source_key'] == SOURCE_PREFIX + rows['bundle'][0]['id']
         and dm.get('derivation') == DERIVATION and dm.get('format') == 'depth_u16_le'
         and dm.get('source_media_id') == rows['bundle'][0]['id'] and dm.get('source_sha256') == rows['bundle'][0]['sha256']
         and dm.get('capture_epoch') == frame['capture_epoch'] and dm.get('alignment_frame') == 'rectified_left'
         and dm.get('clock_mapping_status') == 'verified' and dm.get('geometric_validity') == 'validated'
         and dm.get('units') == 'millimetres' and dm.get('invalid_value') == 0
         and (dm.get('width'), dm.get('height')) == (640, 400), 'existing_qualified_depth_clock_media_required')
    return dict(rows=rows, plane=plane, **checked)


def _complete_available(start, terminal, rows):
    return max(start['created_at'], terminal['created_at'],
        terminal['payload']['processing_completed_at'],
        *[row['created_at'] for row, _ in rows.values()],
        rows['mapped_depth'][0]['metadata']['available_at'] if 'mapped_depth' in rows else 0)


def validate_existing_api_support(support, *, qualification, anchor, capture_record, capture_original,
                                  scope, sensor_roots, policy_ref, expected_assessment_binding, now, resolve_media):
    from lab_live_desk_anchor import SUPPORT_KEYS, _validate_stereo_components
    _exact(support, SUPPORT_KEYS | {'original_family', 'assessment_originals'}, 'closed_existing_api_stereo_support_required')
    checked = validate_existing_api_originals(support,
        expected_assessment_binding=expected_assessment_binding, resolve_media=resolve_media)
    rgb = checked['rows']['rgb'][0]
    event, frame = capture_original['event'], capture_original['frame_metadata']
    b = support['assessment_originals']['start']['payload']['binding']
    need(rgb['id'] in event['payload']['media_ids'] and rgb['metadata'] == frame
         and event['session_id'] == scope['session_id'] and event['payload']['generation'] == b['experiment_generation']
         and event['payload']['frame_id'] == frame['frame_id'] and event['payload']['capture_epoch'] == frame['capture_epoch'],
         'existing_api_original_model_capture_changed')
    actual = _complete_available(support['assessment_originals']['start'],
        support['assessment_originals']['terminal'], checked['rows'])
    need(finite(support['first_consumer_received_at']) and actual <= support['first_consumer_received_at'],
         'actual_existing_api_support_receipt_required')
    return _validate_stereo_components(support, qualification=qualification, anchor=anchor,
        capture_record=capture_record, capture_original=capture_original, scope=scope,
        sensor_roots=sensor_roots, policy_ref=policy_ref, now=now,
        media=checked['rows'], calibration=checked['calibration'], plane=checked['plane'], metadata=checked['source_metadata'])


class ExistingApiStereoSource:
    def __init__(self, store, *, expected_assessment_binding, assessment_tap, depth_clock, current, clock):
        need(isinstance(store, Store) and isinstance(assessment_tap, AssessmentExistingApiTap)
             and assessment_tap.store is store and callable(current) and callable(clock), 'fixed_existing_api_store_capabilities_required')
        self.expected = _binding(expected_assessment_binding)
        self.store, self.tap, self.depth_clock, self.current, self.clock = store, assessment_tap, depth_clock, current, clock
        need(assessment_tap.policy_sha256 == self.expected['assessment_policy_sha256']
             and assessment_tap.runtime is not None and assessment_tap.runtime.store is store
             and assessment_tap.runtime.existing_api_assessment is assessment_tap, 'actual_config3_assessment_tap_required')
        need(depth_clock is None or type(depth_clock) is DepthClock and depth_clock.store is store,
             'actual_existing_depth_clock_required')
        self.selected, self.closed, self.lock = {}, False, threading.Lock()
        self.incomplete, self.pending = False, set()

    def binding(self):
        return deepcopy(self.expected)

    def _current(self, d, *, lease=True):
        need(not self.closed and self.current(deepcopy(d)) is True, 'existing_api_capture_not_current')
        b, r = self.expected, self.tap.runtime
        sid = b['scope']['session_id']; session = self.store.get_session(sid)
        runner, pump = r.runners.get(sid), r.preview_pumps.get(sid)
        need(self.tap.store is self.store and r.store is self.store and r.existing_api_assessment is self.tap
             and self.tap.policy_sha256 == b['assessment_policy_sha256'] and not r.closed
             and sha(encoded(self.tap.policy)) == b['assessment_policy_sha256']
             and runner is not None and not runner.closed and runner.generation == d['model_generation']
             and pump is not None and not pump.stopped.is_set()
             and session['state'] == 'recording' and session['generation'] == d['media_generation']
             and session['site_id'] == b['scope']['site_id'] and session['camera_id'] == self.tap.camera_id
             and d['scope'] == b['scope'] and d['lab_binding_digest'] == b['lab_binding_digest'],
             'existing_api_runtime_generation_changed')
        frame, event = d['frame_metadata'], d['original']['event']
        need(event['session_id'] == sid and event['payload']['generation'] == d['model_generation']
             and frame['frame_id'] == event['payload']['frame_id']
             and frame['capture_epoch'] == event['payload']['capture_epoch']
             and frame['site_id'] == session['site_id'] and frame['camera_id'] == session['camera_id'],
             'existing_api_descriptor_changed')
        now = self.clock()
        need(finite(now), 'existing_api_consumer_clock_required')
        if lease:
            need(now + frame['uncertainty_seconds'] < min(frame['valid_until'], event['payload']['valid_until']),
                 'existing_api_original_lease_expired')
        return now

    def _events(self, sid, kind, field, value):
        with self.store.lock:
            rows = self.store.db.execute('SELECT id,session_id,kind,created_at,payload FROM events WHERE session_id=? AND kind=? '
                'AND json_extract(payload,?)=? ORDER BY id LIMIT 2', (sid, kind, field, value)).fetchall()
        # events_by_ids is a reasoning lookup map, not full original rows.
        # Preserve the original Store envelope including its created_at.
        return [{**dict(row), 'payload': _json(row['payload'])} for row in rows]

    def _read(self, sid, ref, generation, role):
        row = self.store.get_media(sid, ref['id'], generation=generation)
        need(_ref(row) == {k: ref[k] for k in ('id', 'sha256', 'size')}
             and type(row['size']) is int and 0 < row['size'] <= LIMITS[role], 'existing_api_store_reference_changed')
        with self.store.media_path(sid, row['id']).open('rb') as stream:
            raw = stream.read(row['size'] + 1)
        need(len(raw) == row['size'] and sha(raw) == row['sha256'], 'existing_api_store_binary_changed')
        return row, raw

    def _metadata(self, descriptor, received_at):
        now = self._current(descriptor)
        need(finite(received_at) and received_at <= now, 'existing_api_first_receipt_required')
        sid, frame = self.expected['scope']['session_id'], descriptor['frame_metadata']
        starts = self._events(sid, START_KIND, '$.capture.rgb_frame_id', frame['frame_id'])
        need(len(starts) <= 1, 'ambiguous_existing_api_capture_start')
        windows = self._events(sid, WINDOW_KIND, '$.binding.policy_sha256', self.expected['assessment_policy_sha256'])
        if not starts:
            if self.tap.state == 'incomplete':
                self.incomplete = True
                return dict(status='unavailable', reason='assessment_capture_terminal_incomplete')
            if windows:
                self.pending.discard(frame['frame_id'])
                return dict(status='unavailable', reason='assessment_window_closed')
            self.pending.add(frame['frame_id'])
            return dict(status='pending', reason='assessment_capture_start_pending')
        start = starts[0]; s = _event(start, START_KIND, sid)
        b = s['binding']
        need(b == dict(policy_sha256=self.expected['assessment_policy_sha256'], site_id=self.expected['scope']['site_id'],
            camera_id=self.tap.camera_id, session_id=sid, media_generation=descriptor['media_generation'],
            experiment_generation=descriptor['model_generation'])
            and s['capture']['rgb_frame_id'] == frame['frame_id']
            and s['capture']['rgb_media_ref']['id'] in descriptor['original']['event']['payload']['media_ids']
            and finite(s['tap_started_at']) and s['tap_started_at'] <= start['created_at'] <= now,
            'existing_api_start_descriptor_mismatch')
        terminals = self._events(sid, TERMINAL_KIND, '$.capture_id', s['capture']['capture_id'])
        need(len(terminals) <= 1, 'ambiguous_existing_api_capture_terminal')
        if not terminals:
            if windows or self.tap.state == 'incomplete':
                self.incomplete = True
                return dict(status='unavailable', reason='assessment_capture_terminal_incomplete')
            self.pending.add(frame['frame_id'])
            return dict(status='pending', reason='assessment_capture_terminal_pending')
        terminal = terminals[0]; t = _event(terminal, TERMINAL_KIND, sid)
        need(t['binding'] == b and t['started_event_id'] == start['id'] and terminal['id'] > start['id']
             and finite(t['processing_completed_at'])
             and start['created_at'] <= t['processing_completed_at'] <= terminal['created_at'] <= now,
             'existing_api_terminal_descriptor_mismatch')
        self.pending.discard(frame['frame_id'])
        if t['status'] != 'retained':
            if t['status'] == 'incomplete': self.incomplete = True
            return dict(status='unavailable', reason='assessment_capture_' + t['status'])
        return dict(status='ready', start=start, terminal=terminal)

    def metadata_status(self, descriptor, received_at):
        """No binary read/derive: hold the original while its real tap settles."""
        if not self.lock.acquire(False): return dict(status='unavailable', reason='existing_api_stereo_busy')
        try:
            result = self._metadata(descriptor, received_at)
            return dict(status='ready') if result['status'] == 'ready' else result
        except (ValueError, KeyError, TypeError, IndexError) as error:
            return dict(status='unavailable', reason=str(error) if isinstance(error, ExistingApiStereoRefusal)
                        else 'existing_api_original_unavailable')
        finally:
            self.lock.release()

    def select(self, descriptor, received_at):
        if not self.lock.acquire(False): return dict(status='unavailable', reason='existing_api_stereo_busy')
        try:
            now = self._current(descriptor)
            need(finite(received_at) and received_at <= now, 'existing_api_first_receipt_required')
            sid, frame = self.expected['scope']['session_id'], descriptor['frame_metadata']
            fingerprint = sha(encoded(descriptor)); key = frame['frame_id']
            if key in self.selected:
                old = self.selected[key]
                need(old[0] == fingerprint, 'existing_api_capture_original_changed')
                return deepcopy(old[1])
            status = self._metadata(descriptor, received_at)
            if status['status'] != 'ready': return status
            start, terminal = status['start'], status['terminal']
            s, t = start['payload'], terminal['payload']
            need(self.depth_clock is not None, 'qualified_existing_depth_clock_unavailable')
            arow = self._read(sid, t['association_media_ref'], descriptor['media_generation'], 'assessment_association')
            a = _json(arow[1])
            rows = dict(rgb=self._read(sid, s['capture']['rgb_media_ref'], descriptor['media_generation'], 'rgb'),
                bundle=self._read(sid, a['bundle_media_ref'], descriptor['media_generation'], 'bundle'),
                calibration_original=self._read(sid, a['calibration_original_media_ref'], descriptor['media_generation'], 'calibration_original'),
                assessment_association=arow)
            _chain(start, terminal, rows, self.expected)
            need(_complete_available(start, terminal, rows) <= self._current(descriptor),
                 'existing_api_support_precedes_originals')
            need(self.depth_clock.site_id == self.expected['scope']['site_id'] and self.depth_clock.camera_id == self.tap.camera_id,
                 'existing_depth_clock_scope_changed')
            mapped = self.depth_clock.derive(sid, rows['bundle'][0]['id'], rows['rgb'][0]['id'])
            rows['mapped_depth'] = self._read(sid, _ref(mapped), descriptor['media_generation'], 'mapped_depth')
            need(_complete_available(start, terminal, rows) <= self._current(descriptor),
                 'existing_api_support_precedes_originals')
            result = dict(status='selected', rows=rows, assessment_start=deepcopy(start), assessment_terminal=deepcopy(terminal))
            self.selected[key] = (fingerprint, deepcopy(result))
            return result
        except (ValueError, KeyError, TypeError, IndexError) as error:
            return dict(status='unavailable', reason=str(error) if isinstance(error, ExistingApiStereoRefusal)
                        else 'existing_api_original_unavailable')
        finally:
            self.lock.release()

    def quiesce(self):
        self.closed = True
        if self.pending or self.tap.state == 'incomplete': self.incomplete = True

    def finalize(self):
        if not self.closed or not self.lock.acquire(False):
            return dict(status='unconfirmed', buffers_cleared=False)
        try:
            if self.incomplete: return dict(status='incomplete', buffers_cleared=False)
            self.selected.clear()
            return dict(status='finalized', buffers_cleared=True)
        finally:
            self.lock.release()
