"""Fixed same-Store bundled-C originals, without camera or model ownership.

The public unix_s scalar is the original value of an explicitly DERIVED UTC
interval record. It is never an original camera Unix timestamp or JPEG receipt.
Operational geometry additionally needs independent commissioning/association.
"""
from copy import deepcopy
from decimal import Decimal
import math
import threading

from bundle_rgb_input import (FAMILY, START_KIND, TERMINAL_KIND, WINDOW_KIND,
    ROLE_LIMITS, BundledRgbInput, BundledRgbInputFactory, support_refs,
    validate_support, encoded, sha, _json)
from depth_clock import DepthClock, BUNDLED_DERIVATION, bundled_depth_material
from store import Store

CLOCK_SCHEMA = 'home-lab-bundled-rgb-clock-original-1'
BASE_ROLES = frozenset(ROLE_LIMITS)
ROLES = BASE_ROLES | {'mapped_depth'}
LIMITS = {**ROLE_LIMITS, 'mapped_depth': 640*400*2}
BINDING_KEYS = {'scope', 'lab_binding_digest', 'bundle_input_policy_sha256'}
CLOCK_KEYS = {'schema', 'version', 'original_family', 'binding', 'media_refs',
    'started_event_id', 'terminal_event_id', 'component', 'derived_interval',
    'clock', 'first_consumer_received_at'}
ORIGINAL_KEYS = {'binding', 'start', 'terminal', 'clock_original'}
ROW_KEYS = {'id', 'session_id', 'generation', 'size', 'sha256', 'mime_type',
            'metadata', 'created_at', 'source_key'}


class BundledRgbStereoRefusal(ValueError):
    pass


def need(value, reason):
    if not value: raise BundledRgbStereoRefusal(reason)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def exact(value, keys, reason):
    need(isinstance(value, dict) and set(value) == set(keys), reason)


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _binding(value):
    exact(value, BINDING_KEYS, 'fixed_bundled_binding_required')
    exact(value['scope'], {'site_id', 'room_id', 'session_id', 'domain_epoch'}, 'fixed_bundled_scope_required')
    need(all(isinstance(v, str) and v for v in value['scope'].values())
         and _sha(value['lab_binding_digest']) and _sha(value['bundle_input_policy_sha256']),
         'fixed_bundled_binding_required')
    return deepcopy(value)


def _ref(row):
    return {k: row[k] for k in ('id', 'sha256', 'size')}


def _event(value, kind, sid):
    exact(value, {'id', 'session_id', 'kind', 'created_at', 'payload'}, 'full_bundled_event_required')
    need(value['kind'] == kind and value['session_id'] == sid and type(value['id']) is int
         and value['id'] > 0 and finite(value['created_at']), 'bundled_event_binding_changed')
    return value['payload']


def _checked(records, binding, start, terminal, event=None, frame=None):
    """Recheck original envelopes/bytes in addition to the incumbent pure math."""
    need(set(records) == BASE_ROLES, 'seven_bundled_original_roles_required')
    sid = binding['scope']['session_id']
    s, t = _event(start, START_KIND, sid), _event(terminal, TERMINAL_KIND, sid)
    b = s.get('binding')
    exact(b, {'original_family', 'policy_sha256', 'site_id', 'camera_id', 'session_id', 'media_generation', 'experiment_generation'},
          'bundled_source_binding_required')
    need(b['original_family'] == FAMILY and b['policy_sha256'] == binding['bundle_input_policy_sha256'] and b['site_id'] == binding['scope']['site_id']
         and b['session_id'] == sid and isinstance(b['camera_id'], str) and b['camera_id']
         and type(b['media_generation']) is int and b['media_generation'] >= 0
         and type(b['experiment_generation']) is int and b['experiment_generation'] >= 1,
         'bundled_source_binding_changed')
    snapshots = {}
    for role, (row, raw) in records.items():
        exact(row, ROW_KEYS, 'full_bundled_media_record_required')
        need(isinstance(raw, bytes) and 0 < len(raw) <= LIMITS[role]
             and len(raw) == row['size'] and sha(raw) == row['sha256']
             and row['session_id'] == sid and row['generation'] == b['media_generation']
             and finite(row['created_at']) and isinstance(row['metadata'], dict),
             'bundled_original_bytes_or_generation_changed')
        m = row['metadata']
        need(m.get('site_id', b['site_id']) == b['site_id']
             and m.get('camera_id', b['camera_id']) == b['camera_id'], 'bundled_media_scope_changed')
        snapshots[role] = {'record': row, 'data': raw}
    need(frame is None or frame == records['rgb'][0]['metadata'], 'bundled_C_frame_metadata_changed')
    plane, metadata, extra = validate_support(snapshots, binding=b, event=event,
                                              frame=records['rgb'][0] if event is not None else None,
                                              started=start, terminal=terminal)
    support = _json(records['support'][1])
    families = {'support': ('application/json', 'bundled_rgb_input_original', None),
        'anchor_a': ('image/jpeg', 'bundled_rgb_clock_anchor', 'jpeg'),
        'anchor_b': ('image/jpeg', 'bundled_rgb_clock_anchor', 'jpeg'),
        'npz': ('application/x-npz', 'bundled_rgb_raw_npz', None),
        'calibration_original': ('application/json', 'bundled_rgb_raw_calibration', None),
        'bundle': ('application/x-npz', 'rgbd_bundle', 'numeric_npz'),
        'rgb': ('image/png', 'rgb', 'png')}
    for role, (row, _) in records.items():
        need((row['mime_type'], row['metadata'].get('stream'), row['metadata'].get('format')) == families[role],
             'bundled_original_media_family_changed')
    for role, suffix in (('support', ':support'), ('npz', ':raw'), ('calibration_original', ':cal')):
        need(records[role][0]['source_key'] == support['capture_id']+suffix, 'bundled_original_source_key_changed')
    need(start['created_at'] <= support['created_at'] <= t['completed_at'] <= terminal['created_at']
         and s['started_at'] <= start['created_at']
         and max(row['created_at'] for role, (row, _) in records.items() if role != 'support') <= support['created_at']
         and records['support'][0]['created_at'] <= t['completed_at'],
         'bundled_original_causal_availability_changed')
    if event is not None:
        p, m = event['payload'], records['rgb'][0]['metadata']
        _event(event, 'perception.output', sid)
        need(p['arm'] == 'b' and p['generation'] == b['experiment_generation']
             and p['observed_at'] == m['observed_at'] and m['available_at'] <= p['available_at'] <= event['created_at']
             and p['available_at'] >= terminal['created_at'], 'bundled_model_causal_availability_changed')
    return support, plane, metadata, extra['calibration'], b


def build_bundled_clock_original(records, *, expected_bundle_binding, capture_clock_policy,
                                 started, terminal, received_at, event=None, frame=None):
    """Pure derived-original builder; received_at is the actual full-read receipt."""
    binding = _binding(expected_bundle_binding)
    support, _, meta, _, _ = _checked(records, binding, started, terminal, event, frame)
    timing = support['timing']; rgb = records['rgb'][0]['metadata']
    exact(capture_clock_policy, {'mapping_id', 'mapping_digest', 'source_epoch', 'original_unit',
          'epoch_origin_s', 'max_uncertainty_s'}, 'fixed_bundled_clock_policy_required')
    need(capture_clock_policy['original_unit'] == 'unix_s' and capture_clock_policy['epoch_origin_s'] == 0
         and capture_clock_policy['source_epoch'] == meta['session']
         and _sha(capture_clock_policy['mapping_digest']), 'derived_bundled_clock_mapping_required')
    original_value = format(Decimal(timing['midpoint_unix_ns'])/Decimal(10**9), 'f')
    observed = float(Decimal(original_value))
    need(observed == timing['observed_at'] == rgb['observed_at'], 'bundled_clock_midpoint_changed')
    rounded = Decimal.from_float(observed)
    radius = max(rounded-Decimal(timing['lower_unix_ns'])/10**9,
                 Decimal(timing['upper_unix_ns'])/10**9-rounded)
    uncertainty = float(radius)
    if Decimal.from_float(uncertainty) < radius:
        uncertainty = math.nextafter(uncertainty, math.inf)
    clock = {'status': 'commissioned', **{k: capture_clock_policy[k] for k in
        ('mapping_id', 'mapping_digest', 'source_epoch', 'original_unit')},
        'original_value': original_value, 'uncertainty_s': uncertainty}
    from live_ingress_validation import validate_clock
    validate_clock(clock, capture_clock_policy, observed)
    need(finite(received_at) and max(terminal['created_at'], *[r['created_at'] for r, _ in records.values()]) <= received_at,
         'actual_bundled_full_read_receipt_required')
    component = meta['frames']['rgb']
    return dict(schema=CLOCK_SCHEMA, version=1, original_family=FAMILY, binding=binding,
        media_refs={k: _ref(r) for k, (r, _) in records.items()},
        started_event_id=started['id'], terminal_event_id=terminal['id'], component={
            'capture_epoch': meta['session'], 'sequence': component['sequence'],
            'device_timestamp_seconds': component['device_timestamp_seconds'],
            'device_timestamp_unit': 'seconds', 'device_timestamp_representation': 'json_number',
            'npz_sha256': records['npz'][0]['sha256'], 'frame_metadata': deepcopy(component)},
        derived_interval=deepcopy(timing), clock=clock, first_consumer_received_at=received_at)


def validate_bundled_rgb_originals(originals, *, media_refs, expected_bundle_binding,
                                   capture_clock_policy, resolve_media, event=None, frame=None):
    exact(originals, ORIGINAL_KEYS, 'closed_bundled_originals_required')
    binding = _binding(expected_bundle_binding)
    need(originals['binding'] == binding and callable(resolve_media)
         and set(media_refs) in (BASE_ROLES, ROLES), 'fixed_bundled_original_resolver_required')
    records = {}
    for role, ref in media_refs.items():
        exact(ref, {'id', 'sha256', 'size'}, 'exact_bundled_media_ref_required')
        need(isinstance(ref['id'], str) and ref['id'] and _sha(ref['sha256'])
             and type(ref['size']) is int and 0 < ref['size'] <= LIMITS[role], 'bounded_bundled_media_ref_required')
        found = resolve_media(deepcopy(ref)); exact(found, {'record', 'data'}, 'retained_bundled_media_required')
        need(_ref(found['record']) == ref, 'bundled_media_reference_changed')
        records[role] = (found['record'], found['data'])
    need(len({r['id'] for r, _ in records.values()}) == len(records), 'unique_bundled_media_identities_required')
    base = {k: records[k] for k in BASE_ROLES}
    old = originals['clock_original']; exact(old, CLOCK_KEYS, 'closed_bundled_clock_original_required')
    clock = build_bundled_clock_original(base, expected_bundle_binding=binding,
        capture_clock_policy=capture_clock_policy, started=originals['start'], terminal=originals['terminal'],
        received_at=old['first_consumer_received_at'], event=event, frame=frame)
    need(old == clock, 'bundled_clock_original_changed')
    support, plane, metadata, calibration, _ = _checked(base, binding, originals['start'], originals['terminal'], event, frame)
    return dict(rows=records, support=support, calibration=calibration, plane=plane,
                source_metadata=metadata, clock_original=clock)


def validate_bundled_mapped_depth(rows, *, support, binding, depth_clock_policy, projection):
    """Recompute B-to-depth and exact original plane, without Store or derive."""
    need(set(rows) == ROLES, 'eight_bundled_stereo_roles_required')
    base = {k: {'record': rows[k][0], 'data': rows[k][1]} for k in BASE_ROLES}
    plane, expected = bundled_depth_material(base, binding=binding, clock=depth_clock_policy)
    row, raw = rows['mapped_depth']; exact(row, ROW_KEYS, 'full_bundled_depth_record_required')
    dm = row['metadata']
    need(projection.get('status') == 'validated' and projection.get('site_id') == binding['site_id']
         and projection.get('camera_id') == binding['camera_id'], 'qualified_bundled_projection_required')
    need(raw == plane.astype('<u2', copy=False).tobytes(order='C') and sha(raw) == row['sha256']
         and len(raw) == row['size'] == LIMITS['mapped_depth'] and row['mime_type'] == 'application/octet-stream'
         and all(dm.get(k) == v for k, v in expected.items())
         and dm.get('derivation') == BUNDLED_DERIVATION and dm.get('geometric_validity') == 'validated'
         and dm.get('depth_validation_receipt') == projection['validation_receipt']
         and dm.get('mount_revision') == projection['mount_revision']
         and dm['calibration_content_id'] == projection['id']
         and finite(dm.get('available_at')) and dm['available_at'] <= row['created_at']
         and dm['available_at'] >= max(r['created_at'] for r, _ in rows.values() if r['id'] != row['id']),
         'original_bundled_depth_mapping_changed')
    from spatial_validation import check_mount
    need(check_mount(projection['mount_signature'], plane)['status'] == 'unchanged', 'qualified_bundled_mount_changed')
    return deepcopy(dm)


def validate_bundled_rgb_support(support, *, qualification, anchor, capture_record, capture_original,
        scope, sensor_roots, policy_ref, expected_bundle_binding, capture_clock_policy, now, resolve_media):
    from lab_live_desk_anchor import SUPPORT_KEYS, _validate_stereo_components, _qualification
    exact(support, SUPPORT_KEYS | {'original_family', 'bundled_originals'}, 'closed_bundled_stereo_support_required')
    need(support['schema'] == 'home-desk-stereo-nose-anchor-3' and support['version'] == 3
         and support['original_family'] == FAMILY and capture_original['schema'] == 'home-original-lab-observation-2'
         and support['bundled_originals'] == capture_original['bundled_originals']
         and capture_original['clock_original'] == support['bundled_originals']['clock_original']
         and qualification['clock_mapping'] == capture_clock_policy, 'bundled_stereo_capture_original_changed')
    checked = validate_bundled_rgb_originals(support['bundled_originals'], media_refs=support['media_refs'],
        expected_bundle_binding=expected_bundle_binding, capture_clock_policy=capture_clock_policy,
        resolve_media=resolve_media, event=capture_original['event'], frame=capture_original['frame_metadata'])
    _, _, _, projection, depth_policy = _qualification(qualification, scope=scope,
        policy_ref=policy_ref, now=now, clock_unit='unix_s')
    validate_bundled_mapped_depth(checked['rows'], support=checked['support'],
        binding=checked['support']['binding'], depth_clock_policy=depth_policy, projection=projection)
    clock = checked['clock_original']
    need(clock['first_consumer_received_at'] <= capture_original['first_consumer_received_at']
         and support['first_consumer_received_at'] >= max(r['created_at'] for r, _ in checked['rows'].values()),
         'bundled_stereo_actual_receipt_required')
    expected_media = {'scope': scope, 'lab_binding_digest': support['lab_binding_digest'],
        'media_generation': support['media_generation'], 'camera_id': capture_original['frame_metadata']['camera_id'],
        'original_family': FAMILY, 'bundle_input_policy_sha256': expected_bundle_binding['bundle_input_policy_sha256']}
    need(support['media_binding_digest'] == sha(encoded(expected_media)), 'bundled_media_retention_binding_changed')
    return _validate_stereo_components(support, qualification=qualification, anchor=anchor,
        capture_record=capture_record, capture_original=capture_original, scope=scope,
        sensor_roots=sensor_roots, policy_ref=policy_ref, now=now, media=checked['rows'],
        calibration=checked['calibration'], plane=checked['plane'], metadata=checked['source_metadata'], bundled_clock=clock)


class BundledRgbStereoSource:
    def __init__(self, store, *, expected_bundle_binding, incumbent_runtime, capture_clock_policy,
                 depth_clock, current, clock):
        from service_runtime import ServiceRuntime
        need(type(store) is Store and type(incumbent_runtime) is ServiceRuntime and incumbent_runtime.store is store
             and callable(current) and callable(clock), 'fixed_bundled_runtime_required')
        self.store, self.runtime, self.current, self.clock = store, incumbent_runtime, current, clock
        self.expected = _binding(expected_bundle_binding)
        self.capture_clock_policy = deepcopy(capture_clock_policy)
        self.policy_digest = sha(encoded(capture_clock_policy))
        need(depth_clock is None or type(depth_clock) is DepthClock and depth_clock.store is store,
             'fixed_bundled_depth_clock_required')
        self.depth_clock = depth_clock
        self.lock, self.closed, self.incomplete = threading.Lock(), False, False
        self.prepared, self.selected, self.pending = {}, {}, set()

    def binding(self): return deepcopy(self.expected)

    def _current(self, event, frame):
        r, b = self.runtime, self.expected; sid = b['scope']['session_id']; s = self.store.get_session(sid)
        source = r.bundled_rgb_sessions.get(sid); factory = r.bundled_rgb_sources.get(s['camera_id'])
        runner, pump = r.runners.get(sid), r.preview_pumps.get(sid)
        context = dict(scope=b['scope'], lab_binding_digest=b['lab_binding_digest'], event=event,
                       frame_metadata=frame, model_generation=event['payload']['generation'], media_generation=s['generation'])
        need(frame.get('original_family') == FAMILY and frame.get('frame_id') == event['payload'].get('frame_id')
             and frame.get('capture_epoch') == event['payload'].get('capture_epoch')
             and frame.get('observed_at') == event['payload'].get('observed_at'), 'bundled_descriptor_changed')
        need(not self.closed and not r.closed and r.store is self.store and self.current(deepcopy(context)) is True
             and sha(encoded(self.capture_clock_policy)) == self.policy_digest
             and type(source) is BundledRgbInput and type(factory) is BundledRgbInputFactory
             and source.runtime is r and factory.runtime is r and source.store is factory.store is self.store
             and source.policy_sha256 == sha(encoded(source.policy)) == sha(encoded(factory.policy)) == b['bundle_input_policy_sha256']
             and not source.stopped.is_set() and not source.incomplete
             and runner is not None and not runner.closed and runner.generation == event['payload']['generation']
             and pump is not None and not pump.stopped.is_set() and s['state'] == 'recording'
             and s['site_id'] == b['scope']['site_id'] and frame['site_id'] == s['site_id']
             and frame['camera_id'] == s['camera_id'] and event['session_id'] == sid,
             'bundled_runtime_generation_changed')
        now = self.clock()
        need(finite(now) and now + frame['uncertainty_seconds'] < min(frame['valid_until'], event['payload']['valid_until']),
             'bundled_original_lease_expired')
        return now, source, s

    def _events(self, kind, field, value):
        with self.store.lock:
            rows = self.store.db.execute('SELECT id,session_id,kind,created_at,payload FROM events WHERE session_id=? '
                'AND kind=? AND json_extract(payload,?)=? ORDER BY id LIMIT 2',
                (self.expected['scope']['session_id'], kind, field, value)).fetchall()
        return [{**dict(row), 'payload': _json(row['payload'])} for row in rows]

    def _metadata(self, event, frame):
        now, source, session = self._current(event, frame)
        try:
            return self._proof_metadata(event, frame, now, source, session)
        except (ValueError, KeyError, TypeError, IndexError, OSError):
            self.incomplete = True
            raise

    def _proof_metadata(self, event, frame, now, source, session):
        with self.store.lock:
            found = self.store.db.execute("SELECT id FROM media WHERE session_id=? AND json_extract(metadata,'$.stream')=? "
                "AND json_extract(metadata,'$.capture_id') IS NOT NULL ORDER BY created_at LIMIT 64",
                (session['id'], 'bundled_rgb_input_original')).fetchall()
        matches = []
        for item in found:
            row, raw = self._read({'id': item['id']}, session['generation'], 'support', by_id=True)
            support = _json(raw)
            if (support.get('selected_bundled_frame_ref') or {}).get('frame_id') == frame['frame_id']:
                matches.append((row, raw, support))
        need(len(matches) <= 1, 'ambiguous_bundled_C_original')
        if not matches:
            self.incomplete = True
            return dict(status='unavailable', reason='bundled_C_original_unavailable')
        row, raw, support = matches[0]
        starts = self._events(START_KIND, '$.capture_id', support['capture_id'])
        terminals = self._events(TERMINAL_KIND, '$.capture_id', support['capture_id'])
        need(len(starts) == 1 and len(terminals) <= 1, 'unique_bundled_capture_events_required')
        if not terminals:
            if source.window_closed or source.incomplete:
                self.incomplete = True
                return dict(status='unavailable', reason='bundled_terminal_incomplete')
            self.pending.add(event['id'])
            return dict(status='pending', reason='bundled_terminal_pending')
        self.pending.discard(event['id'])
        need(terminals[0]['created_at'] <= now, 'bundled_terminal_future')
        return dict(status='ready', support=(row, raw, support), start=starts[0], terminal=terminals[0])

    def _read(self, ref, generation, role, by_id=False):
        row = self.store.get_media(self.expected['scope']['session_id'], ref['id'], generation=generation)
        need(type(row['size']) is int and 0 < row['size'] <= LIMITS[role]
             and (by_id or _ref(row) == {k: ref[k] for k in ('id', 'sha256', 'size')}), 'bundled_store_ref_changed')
        with self.store.media_path(row['session_id'], row['id']).open('rb') as stream: raw = stream.read(row['size']+1)
        need(len(raw) == row['size'] and sha(raw) == row['sha256'], 'bundled_store_binary_changed')
        return {k: deepcopy(row[k]) for k in ROW_KEYS}, raw

    def _capture(self, event, frame, received_at):
        now, _, session = self._current(event, frame)
        need(finite(received_at) and event['payload']['available_at'] <= received_at <= now, 'actual_bundled_capture_receipt_required')
        key, fingerprint = event['id'], sha(encoded([event, frame]))
        if key in self.prepared:
            entry = self.prepared[key]; need(entry['fingerprint'] == fingerprint, 'bundled_capture_original_changed')
            if entry.get('result') is not None: return deepcopy(entry['result'])
        else:
            need(len(self.prepared) < 24, 'bundled_consumer_budget_exhausted')
            entry = dict(fingerprint=fingerprint, received_at=received_at); self.prepared[key] = entry
        status = self._metadata(event, frame)
        if status['status'] != 'ready': return status
        row, raw, support = status['support']
        rows = dict(support=(row, raw))
        try:
            for role, ref in support_refs(support).items(): rows[role] = self._read(ref, session['generation'], role)
        except (ValueError, KeyError, TypeError, IndexError, OSError):
            self.incomplete = True
            raise
        at = self.clock()
        try:
            original = build_bundled_clock_original(rows, expected_bundle_binding=self.expected,
                capture_clock_policy=self.capture_clock_policy, started=status['start'], terminal=status['terminal'],
                received_at=at, event=event, frame=frame)
        except (ValueError, KeyError, TypeError, IndexError) as error:
            # A commissioned ceiling refusal is unavailable qualification. A
            # missing/changed original proof must remain incomplete at Stop.
            if str(error) != 'invalid_clock': self.incomplete = True
            raise
        self._current(event, frame)
        originals = dict(binding=self.binding(), start=status['start'], terminal=status['terminal'], clock_original=original)
        result = dict(status='selected', rows=rows, bundled_originals=originals, clock_original=original)
        entry['result'] = deepcopy(result)
        return result

    def capture(self, event, frame_metadata, received_at):
        if not self.lock.acquire(False): return dict(status='unavailable', reason='bundled_stereo_busy')
        try: return self._capture(event, frame_metadata, received_at)
        except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
            return dict(status='unavailable', reason=str(error) if type(error) is BundledRgbStereoRefusal else 'bundled_original_unavailable')
        finally: self.lock.release()

    def metadata_status(self, descriptor, received_at):
        if not self.lock.acquire(False): return dict(status='unavailable', reason='bundled_stereo_busy')
        try:
            now, _, _ = self._current(descriptor['original']['event'], descriptor['frame_metadata'])
            need(finite(received_at) and descriptor['original']['event']['payload']['available_at'] <= received_at <= now,
                 'actual_bundled_capture_receipt_required')
            result = self._metadata(descriptor['original']['event'], descriptor['frame_metadata'])
            return dict(status='ready') if result['status'] == 'ready' else result
        except (ValueError, KeyError, TypeError, IndexError, OSError): return dict(status='unavailable', reason='bundled_original_unavailable')
        finally: self.lock.release()

    def select(self, descriptor, received_at):
        if not self.lock.acquire(False): return dict(status='unavailable', reason='bundled_stereo_busy')
        try:
            event, frame = descriptor['original']['event'], descriptor['frame_metadata']
            result = self._capture(event, frame, received_at)
            if result['status'] != 'selected': return result
            need(descriptor['original']['bundled_originals'] == result['bundled_originals']
                 and descriptor['original']['clock_original'] == result['clock_original'], 'bundled_admitted_capture_changed')
            need(self.depth_clock is not None and self.depth_clock.site_id == self.expected['scope']['site_id']
                 and self.depth_clock.camera_id == frame['camera_id'], 'qualified_bundled_depth_clock_unavailable')
            key = event['id']
            if key in self.selected: return deepcopy(self.selected[key])
            records = {k: {'record': row, 'data': raw} for k, (row, raw) in result['rows'].items()}
            mapped = self.depth_clock.derive_bundled(event['session_id'], records=records,
                binding=result['bundled_originals']['start']['payload']['binding'],
                started=result['bundled_originals']['start'], terminal=result['bundled_originals']['terminal'])
            need(mapped['metadata'].get('geometric_validity') == 'validated', 'qualified_bundled_depth_geometry_unavailable')
            result['rows']['mapped_depth'] = self._read(_ref(mapped), descriptor['media_generation'], 'mapped_depth')
            self._current(event, frame)
            self.selected[key] = deepcopy(result)
            return result
        except (ValueError, KeyError, TypeError, IndexError) as error:
            return dict(status='unavailable', reason=str(error) if type(error) is BundledRgbStereoRefusal else 'bundled_original_unavailable')
        finally: self.lock.release()

    def quiesce(self):
        self.closed = True
        if self.pending: self.incomplete = True

    def finalize(self):
        if not self.closed or not self.lock.acquire(False): return dict(status='unconfirmed', buffers_cleared=False)
        try:
            if self.incomplete: return dict(status='incomplete', buffers_cleared=False)
            self.prepared.clear(); self.selected.clear()
            return dict(status='finalized', buffers_cleared=True)
        finally: self.lock.release()
