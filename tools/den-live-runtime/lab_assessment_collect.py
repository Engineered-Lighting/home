"""Bounded private N21 collection from the existing authenticated Lab READ API.

Attach to a recording started in Perception Lab. This command never starts or
stops Lab workers, opens another Store, publishes observations or actuates.
Raw household media stays in the explicitly chosen private JSONL result file.
Seconds bounds admission/elapsed checks, not hard whole-call cancellation:
HTTP opening/headers retain the existing socket inactivity timeout. Runtime
supervision remains responsible for a hard process/session duration cap.
"""
from __future__ import annotations
import argparse
import base64
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / 'stack/services/perception-lab'
for directory in (LAB, ROOT / 'stack/services/home-context', ROOT / 'tools/shared-home'):
    if str(directory) not in sys.path:
        sys.path.append(str(directory))

from lab_assessment_candidates import (LabAssessmentCandidates, ROLES, LIMITS, AUTHORITY, need, finite, sha,
                                       EXISTING_MODE, EXISTING_ROLES, EXISTING_LIMITS, BUNDLED_MODE,
                                       BUNDLED_LIMITS, BUNDLED_START, BUNDLED_TERMINAL, BUNDLED_WINDOW,
                                       original_json)
try:
    from .lab_whole_stack_trial import LabHTTP, PrivateWindow, encoded, sync_parent, ORIGIN, PREFIX
except ImportError:
    from lab_whole_stack_trial import LabHTTP, PrivateWindow, encoded, sync_parent, ORIGIN, PREFIX

ID = r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}'
JSON_ROUTE = re.compile(r'/sessions/' + ID + r'(?:/live|/events\?after_id=\d+&limit=128'
    r'|/media\?cursor=\d+&limit=200|/media/' + ID + r'/descriptor\?generation=\d+)?\Z')


class FrozenDeskMethodExecution:
    """Private, fixed-method measurement; never an operational qualification.

    Freeze the independently chosen method before this collector reads model
    output. A new current clock proof brackets the numeric operation, rather
    than reusing an expired camera/body-receipt proof as its completion time.
    """
    def __init__(self, qualification, *, session_id):
        folder = ROOT / 'tools/den-assessment'
        if str(folder) not in sys.path: sys.path.append(str(folder))
        from desk_method_export import validate_qualification
        self.qualification = validate_qualification(qualification)
        need(self.qualification is not None and self.qualification['version'] == 3
             and self.qualification['context']['scope']['session_id'] == session_id,
             'fixed_bundled_execution_qualification_required')
        self.method = self.qualification['measurement_method']
        self.frozen = dict(schema='home-desk-frozen-method-1', version=1,
            qualification=deepcopy(self.qualification), qualification_sha256=sha(encoded(self.qualification)),
            executor_sha256=sha(Path(__file__).read_bytes()),
            clock_reader_sha256=sha((LAB / 'bundle_rgb_input.py').read_bytes()),
            operational_geometry=None, physical_authority=False)

    def check_binding(self, binding):
        q = self.qualification; scope = q['context']['scope']; original = binding['bundled_rgb_binding']
        need(self.method['lab_profile'] == binding['lab_profile']
             and original['session_id'] == scope['session_id'] and original['site_id'] == scope['site_id']
             and original['camera_id'] == q['projection']['camera_id']
             and original['policy_sha256'] == q['bundle_binding']['bundle_input_policy_sha256'],
             'frozen_method_collection_binding_changed')

    def execute(self, event, *, frame, records, start, terminal):
        from bundle_rgb_input import QualifiedReceiverClock, decode_bundle, host_execution_interval
        from lab_live_desk_anchor import _projected_pose, sample_visible_nose_depth
        from lab_assessment_candidates import bounded_reason
        from desk_method_export import _bundled_method
        _bundled_method(self.qualification)  # Refuse changed executable method bytes.
        need(sha(Path(__file__).read_bytes()) == self.frozen['executor_sha256']
             and sha((LAB / 'bundle_rgb_input.py').read_bytes()) == self.frozen['clock_reader_sha256'],
             'frozen_method_executor_changed')
        q, m = self.qualification, self.method
        payload = event['payload']; scope = q['context']['scope']
        need(event['session_id'] == scope['session_id'] and payload['site_id'] == scope['site_id']
             and payload['camera_id'] == q['projection']['camera_id']
             and all(payload.get(k) == m['lab_profile'][v] for k, v in
                     (('run_id','profile_id'), ('model_version','model_version'),
                      ('engine_epoch','engine_epoch'), ('artifact_sha256','artifact_sha256'))),
             'frozen_method_model_scope_changed')
        complete = frame is not None and records is not None and set(records) == set(BUNDLED_LIMITS)
        row = dict(schema='home-desk-method-execution-1', version=1,
            qualification_sha256=self.frozen['qualification_sha256'],
            executor_sha256=self.frozen['executor_sha256'], clock_reader_sha256=self.frozen['clock_reader_sha256'],
            source_event_id=event['id'], original_event_sha256=sha(encoded(event)),
            frame_record_sha256=sha(encoded(frame)) if complete else None,
            capture_started_sha256=sha(encoded(start)) if start is not None else None,
            capture_terminal_sha256=sha(encoded(terminal)) if terminal is not None else None,
            media_refs={role: {k: item['record'][k] for k in ('id','sha256','size')}
                        for role, item in (records if complete else {}).items()},
            sample=None, clock_proof=None, timing=None, status='refused', reason=None,
            operational_geometry=None, geometry_authority=False, identity_authority=False,
            protection_authority=False, beam_authority=False, physical_authority=False)
        clock = QualifiedReceiverClock()
        try:
            need(complete, 'exact_method_originals_unavailable')
            plane, _, metadata = decode_bundle(records['npz']['data'])
            _, pixel = _projected_pose(dict(event=event))
            before = clock.begin_current()
            try:
                row['sample'] = sample_visible_nose_depth(plane, metadata, q['projection'], q['registration'], pixel,
                    radius_px=m['sampling']['radius_px'], min_support=m['sampling']['min_support'],
                    max_spread_m=m['sampling']['max_spread_m'])
            except ValueError as error:
                row['reason'] = bounded_reason(error)
            row['clock_proof'] = clock.finish(before)
            row['timing'] = host_execution_interval(row['clock_proof'])
            _bundled_method(self.qualification)
            need(sha(Path(__file__).read_bytes()) == self.frozen['executor_sha256']
                 and sha((LAB / 'bundle_rgb_input.py').read_bytes()) == self.frozen['clock_reader_sha256'],
                 'frozen_method_executor_changed')
            if row['sample'] is not None: row.update(status='measured', reason=None)
        except (ValueError, OSError) as error:
            row.update(status='refused', reason=bounded_reason(error), clock_proof=None, timing=None)
        return row

    def validate_record(self, row, capture, source_attempt):
        """Recompute retained bytes and clock arithmetic, without live I/O."""
        from bundle_rgb_input import decode_bundle, host_execution_interval
        from lab_live_desk_anchor import _projected_pose, sample_visible_nose_depth
        from lab_assessment_candidates import bounded_reason
        event, records = capture['event'], capture['records']
        expected = dict(schema='home-desk-method-execution-1', version=1,
            qualification_sha256=self.frozen['qualification_sha256'],
            executor_sha256=self.frozen['executor_sha256'], clock_reader_sha256=self.frozen['clock_reader_sha256'],
            source_event_id=event['id'], original_event_sha256=sha(encoded(event)),
            frame_record_sha256=sha(encoded(capture['frame_record'])) if capture['frame_record'] is not None else None,
            capture_started_sha256=sha(encoded(source_attempt['capture_started'])) if source_attempt['capture_started'] is not None else None,
            capture_terminal_sha256=sha(encoded(source_attempt['capture_terminal'])) if source_attempt['capture_terminal'] is not None else None,
            media_refs={role: {k: item['record'][k] for k in ('id','sha256','size')} for role, item in records.items()},
            operational_geometry=None, geometry_authority=False, identity_authority=False,
            protection_authority=False, beam_authority=False, physical_authority=False)
        need(isinstance(row, dict) and set(row) == set(expected) | {'sample','clock_proof','timing','status','reason'}
             and all(row[k] == value for k, value in expected.items()), 'retained_exact_method_binding_changed')
        measured, refusal = None, None
        try:
            need(capture['frame_record'] is not None and set(records) == set(BUNDLED_LIMITS),
                 'exact_method_originals_unavailable')
            plane, _, metadata = decode_bundle(records['npz']['data'])
            _, pixel = _projected_pose(dict(event=event)); q, m = self.qualification, self.method
            measured = sample_visible_nose_depth(plane, metadata, q['projection'], q['registration'], pixel,
                radius_px=m['sampling']['radius_px'], min_support=m['sampling']['min_support'],
                max_spread_m=m['sampling']['max_spread_m'])
        except ValueError as error:
            refusal = bounded_reason(error)
        if row['clock_proof'] is None:
            need(row['timing'] is None and row['status'] == 'refused' and isinstance(row['reason'], str)
                 and bool(row['reason']) and (row['sample'] is None or row['sample'] == measured),
                 'unqualified_method_completion_cannot_be_service')
        else:
            need(row['timing'] == host_execution_interval(row['clock_proof'])
                 and row['sample'] == measured and row['reason'] == refusal
                 and row['status'] == ('measured' if measured is not None else 'refused'),
                 'retained_exact_method_result_or_clock_changed')
        return deepcopy(row)


def _utc_now():
    """One epoch-second rounding; no operational acquisition dependency."""
    return float(Decimal(time.time_ns()) / Decimal(1_000_000_000))


def identity(value):
    need(isinstance(value, str) and re.fullmatch(ID, value) is not None, 'bounded_lab_identity_required')
    return value


class FixedLabReadClient(LabHTTP):
    def bind_deadline(self, remaining):
        need(callable(remaining) and not hasattr(self, '_remaining'), 'one_fixed_collection_deadline_required')
        self._remaining = remaining

    def _timeout(self):
        remaining = self._remaining() if hasattr(self, '_remaining') else 5
        need(finite(remaining) and remaining > 0, 'collection_runtime_exhausted')
        return min(5, remaining)

    def _read_bounded(self, response, maximum):
        reader = getattr(response, 'read1', None)
        need(callable(reader), 'bounded_http_chunk_reader_required')
        chunks, size = [], 0
        while True:
            self._timeout()  # Absolute budget/Stop between single socket reads.
            chunk = reader(min(65536, maximum + 1 - size))
            self._timeout()
            if not chunk: return b''.join(chunks)
            chunks.append(chunk); size += len(chunk)
            need(size <= maximum, 'bounded_http_body_required')

    def __call__(self, method, suffix, body=None):
        need(method == 'GET' and body is None and JSON_ROUTE.fullmatch(suffix) is not None,
             'fixed_lab_read_route_required')
        request = urllib.request.Request(ORIGIN + PREFIX + suffix, method='GET', headers={
            'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json', 'Accept-Encoding': 'identity'})
        with self.opener.open(request, timeout=self._timeout()) as response:
            raw = self._read_bounded(response, 2 * 1024**2)
            need(response.status == 200 and len(raw) <= 2 * 1024**2
                 and response.getheader('Content-Encoding', 'identity') == 'identity', 'bounded_identity_json_required')
            return json.loads(raw)

    def read_media(self, record):
        sid, mid = identity(record['session_id']), identity(record['id'])
        generation, size = record['generation'], record['size']
        need(type(generation) is int and generation >= 0 and type(size) is int and 0 < size <= 4 * 1024**2,
             'bounded_original_media_required')
        suffix = f'/sessions/{sid}/media/{mid}?generation={generation}'
        request = urllib.request.Request(ORIGIN + PREFIX + suffix, method='GET', headers={
            'Authorization': 'Bearer ' + self.token, 'Accept-Encoding': 'identity',
            'Accept': record['mime_type']})
        with self.opener.open(request, timeout=self._timeout()) as response:
            raw = self._read_bounded(response, size)
            need(response.status == 200 and response.getheader('Content-Encoding', 'identity') == 'identity'
                 and response.getheader('ETag') == '"' + record['sha256'] + '"'
                 and response.getheader('Content-Type', '').split(';')[0] == record['mime_type']
                 and len(raw) == size and sha(raw) == record['sha256'], 'original_media_delivery_changed')
            return raw


def session_from(live, sid):
    session = live.get('session', {})
    need(session.get('id') == sid and live.get('session_id') == sid
         and live.get('site_id') == session.get('site_id')
         and session.get('state') == 'recording' and type(session.get('generation')) is int,
         'current_recording_lab_session_required')
    return session


def ready_binding(events, session, arm):
    activations = [e for e in events if e['kind'] == 'experiment.activated']
    ready = [e for e in events if e['kind'] == 'workers.ready']
    need(activations and ready, 'existing_worker_ready_originals_required')
    active, receipt = activations[-1], ready[-1]
    need(receipt['id'] > active['id'] and not any(e['kind'] == 'workers.cleanup'
         and e['id'] > receipt['id'] for e in events), 'current_worker_ready_originals_required')
    p = active['payload']; r = receipt['payload'].get('receipts', {}).get(arm, {})
    selected, profile = p.get('profiles', {}).get(arm), r.get('profile')
    # The incumbent activation records four identity fields; the managed
    # readiness original retains the full profile. Match the exact same four
    # fields as LabRuntimeComposition._current, retaining both originals.
    fields = ('profile_id', 'artifact_sha256', 'engine_epoch', 'model_version')
    need(isinstance(selected, dict) and isinstance(profile, dict) and r.get('state') == 'ready'
         and all(isinstance(selected.get(k), str) and selected[k] and selected[k] == profile.get(k) for k in fields)
         and r.get('session_id') == session['id'] and r.get('engine_epoch') == profile.get('engine_epoch')
         and p.get('site_id') == session['site_id'] and p.get('camera_id') == session['camera_id']
         and type(p.get('generation')) is int and p['generation'] > 0,
         'fixed_ready_la_profile_required')
    return p['generation'], profile, [deepcopy(active), deepcopy(receipt)]


def collect_existing(api, *, session_id, output_file, arm='b', max_attempts=24, seconds=30,
                     max_bytes=128 * 1024**2, clock=_utc_now, monotonic=time.monotonic,
                     stop=None, collection_mode='paired-preview-1', frozen_method=None):
    """Use the same entry for real fixed HTTPS and authored actual-app ASGI I/O."""
    need(collection_mode in ('paired-preview-1', EXISTING_MODE, BUNDLED_MODE), 'known_private_collection_mode_required')
    need(frozen_method is None or collection_mode == BUNDLED_MODE, 'frozen_method_requires_bundled_collection')
    if collection_mode in (EXISTING_MODE, BUNDLED_MODE):
        return _collect_existing_api(api, session_id=session_id, output_file=output_file, arm=arm,
            max_attempts=max_attempts, seconds=seconds, max_bytes=max_bytes, clock=clock,
            monotonic=monotonic, stop=stop, collection_mode=collection_mode, frozen_method=frozen_method)
    sid = identity(session_id)
    need(arm == 'b' and type(max_attempts) is int and 1 <= max_attempts <= 24
         and finite(seconds) and 0 < seconds <= 120 and type(max_bytes) is int
         and 1024 <= max_bytes <= 128 * 1024**2, 'bounded_la_candidate_collection_required')
    stop = stop or threading.Event()
    window = PrivateWindow(output_file, max_bytes=max_bytes)
    started, began = clock(), monotonic()
    summary = {'schema': 'home-lab-assessment-collection-1', 'version': 1, 'session_id': sid,
        'started_at': started, 'ended_at': None, 'attempts': 0, 'candidate_attempts': 0,
        'missing_prediction_attempts': 0, 'stale_attempts': 0, 'refusals': {}, 'complete': False,
        'stop_reason': None, 'operational_geometry': None, 'physical_authority': False,
        'collector_clock_qualification': 'unassessed', 'collector_clock_error_bound_s': None,
        'availability_role': 'diagnostic_candidate_presence_not_qualified_service',
        'independent_error_assessed': False, 'room_envelope_assessed': False}
    current, retained, attempted, originals, first_reads = None, {}, set(), set(), {}
    observed_matching = set()
    expected_generation = None
    if callable(getattr(api, 'bind_deadline', None)):
        api.bind_deadline(lambda: 0 if stop.is_set() else seconds - (monotonic() - began))
    def within():
        need(not stop.is_set(), 'owner_stopped_collector')
        elapsed = monotonic() - began
        need(finite(elapsed) and elapsed >= 0 and elapsed < seconds, 'collection_runtime_exhausted')
    def remember_page(page):
        first = clock()
        need(finite(first) and first >= 0 and isinstance(page.get('items'), list)
             and len(page['items']) <= 128, 'bounded_original_event_page_required')
        for event in page['items']:
            key = event['id']; fingerprint = sha(encoded(event))
            need(type(key) is int and key > 0 and (key not in first_reads or first_reads[key][1] == fingerprint),
                 'candidate_event_identity_changed')
            first_reads.setdefault(key, (first, fingerprint))
    def current_live():
        within()
        live = api('GET', '/sessions/' + sid + '/live')
        within()
        found = session_from(live, sid)
        if current is not None:
            need(all(found.get(k) == current.get(k) for k in ('id', 'site_id', 'camera_id', 'generation')),
                 'collection_session_generation_changed')
        if expected_generation is not None:
            need(live.get('experiment_generation') == expected_generation, 'collection_model_generation_changed')
        return found
    def media_record(mid):
        within()
        row = api('GET', f'/sessions/{sid}/media/{identity(mid)}/descriptor?generation={current["generation"]}')
        within()
        need(row.get('id') == mid and row.get('session_id') == sid
             and row.get('generation') == current['generation'], 'collection_media_generation_changed')
        return row
    def fetch(row, limit):
        within()
        need(type(row.get('size')) is int and 0 < row['size'] <= limit, 'candidate_media_size_bound')
        # Base64 plus metadata must fit BEFORE downloading another object.
        need(window.bytes + ((row['size'] + 2) // 3) * 4 + len(encoded(row)) + 1024 <= max_bytes,
             'candidate_collection_byte_bound')
        raw = api.read_media(row)
        within()
        need(isinstance(raw, bytes) and len(raw) == row['size'] and sha(raw) == row['sha256'],
             'candidate_media_integrity_refused')
        material = {'record': deepcopy(row), 'data_b64': base64.b64encode(raw).decode('ascii')}
        fingerprint = sha(encoded(material))
        old = retained.get(row['id'])
        need(old is None or old == fingerprint, 'candidate_media_identity_changed')
        if old is None:
            window('media', material)
            retained[row['id']] = fingerprint
        return {'record': deepcopy(row), 'data': raw}
    def pair_for(event):
        ids = event['payload'].get('media_ids', [])
        need(isinstance(ids, list) and len(ids) <= 32, 'original_model_media_ids_required')
        rows, cursor = [], 0
        for _ in range(8):
            within()
            page = api('GET', f'/sessions/{sid}/media?cursor={cursor}&limit=200')
            within()
            need(page.get('generation') == current['generation'], 'collection_media_generation_changed')
            rows.extend(page['items'])
            if page['next_cursor'] is None:
                break
            need(type(page['next_cursor']) is int and page['next_cursor'] > cursor, 'bounded_media_page_progress_required')
            cursor = page['next_cursor']
        else:
            raise ValueError('candidate_media_page_bound')
        pairs = [r for r in rows if r.get('metadata', {}).get('stream') == 'paired_preview_original'
                 and r['metadata'].get('rgb_media_id') in ids
                 and r['metadata'].get('frame_id') == event['payload'].get('frame_id')]
        need(len(pairs) == 1, 'original_pair_unavailable')
        pair = fetch(media_record(pairs[0]['id']), LIMITS['paired_original'])
        original = json.loads(pair['data'])
        rgb = fetch(media_record(identity(original['rgb_media_id'])), LIMITS['rgb'])
        bundle = fetch(media_record(identity(original['bundle_media_id'])), LIMITS['bundle'])
        return rgb['record'], {'rgb': rgb, 'bundle': bundle, 'paired_original': pair}
    try:
        current = current_live()
        initial, after = [], 0
        for _ in range(8):
            within()
            page = api('GET', f'/sessions/{sid}/events?after_id={after}&limit=128')
            remember_page(page)
            within()
            initial.extend(page['items'])
            if len(page['items']) < 128:
                break
            need(page['after_id'] > after, 'bounded_event_progress_required')
            after = page['after_id']
        else:
            raise ValueError('candidate_initial_history_bound')
        generation, profile, readiness = ready_binding(initial, current, arm)
        expected_generation = generation
        candidate = LabAssessmentCandidates(current, arm=arm, generation=generation,
            media_generation=current['generation'], lab_profile=profile, max_attempts=max_attempts)
        observed_matching.update(e['id'] for e in initial if candidate.matches(e))
        window('binding', {'schema': 'home-lab-assessment-binding-1', 'version': 1,
            'binding': deepcopy(candidate.binding), 'binding_digest': candidate.binding_digest,
            'original_session': deepcopy(current), 'original_readiness': readiness, 'started_at': started,
            'limits': {'max_attempts': max_attempts, 'seconds': seconds, 'max_bytes': max_bytes}})
        after, pending = 0, initial
        while summary['attempts'] < max_attempts and not stop.is_set():
            within()
            current_live()
            if pending is None:
                page = api('GET', f'/sessions/{sid}/events?after_id={after}&limit=128')
                remember_page(page)
                observed_matching.update(e['id'] for e in page['items'] if candidate.matches(e))
                within()
                pending = page['items']
            for event in pending:
                need(type(event.get('id')) is int and event['id'] > 0, 'original_event_identity_required')
                after = max(after, event['id'])
                if not candidate.matches(event) or event['id'] in attempted:
                    continue
                received = first_reads[event['id']][0]
                window('original_attempt', {'event': deepcopy(event), 'first_consumer_received_at': received})
                originals.add(event['id'])
                frame, media, reason = None, None, None
                try:
                    frame, media = pair_for(event)
                    current_live()
                except Exception as error:
                    from lab_assessment_candidates import bounded_reason
                    reason = bounded_reason(error)
                row = candidate.collect(event, received_at=received, frame_record=frame,
                                        paired_records=media, refusal_reason=reason)
                completed = clock()
                need(finite(completed) and completed >= received, 'candidate_completion_clock_refused')
                # The collector's UTC is unassessed against the controller.
                # These are explicit diagnostic arithmetic, never a new lease.
                rgb_until = frame.get('metadata', {}).get('valid_until') if frame is not None else None
                horizon = event['payload'].get('valid_until')
                fresh_completion = (finite(horizon) and finite(rgb_until)
                                    and completed < min(horizon, rgb_until))
                try:
                    within()
                    current_live()
                except Exception as error:
                    from lab_assessment_candidates import bounded_reason
                    reason = bounded_reason(error)
                    if reason != 'collection_runtime_exhausted':
                        # Retain the computed diagnostic but make selection and
                        # the collection's failed current fence explicit.
                        row['post_calculation_refusal'] = reason
                window('attempt', {'candidate': row, 'calculation_completed_at': completed,
                    'original_rgb_valid_until': rgb_until,
                    'original_support_fresh_at_completion': bool(fresh_completion),
                    'collector_clock_qualification': 'unassessed'})
                attempted.add(event['id'])
                summary['attempts'] += 1
                has_sample = any(j['status'] == 'candidate' for i in row['instances'] for j in i['joints'])
                summary['candidate_attempts'] += int(has_sample)
                summary['missing_prediction_attempts'] += int(not has_sample)
                summary['stale_attempts'] += int(not row['source_fresh_at_collection'])
                summary['refusals'][row['reason']] = summary['refusals'].get(row['reason'], 0) + 1
                if reason in {'collection_session_generation_changed', 'collection_model_generation_changed',
                              'current_recording_lab_session_required'}:
                    raise ValueError(reason)
                if reason in {'collection_runtime_exhausted', 'owner_stopped_collector'}: raise ValueError(reason)
                if summary['attempts'] >= max_attempts or stop.is_set():
                    break
            pending = None
            if summary['attempts'] < max_attempts and not stop.is_set():
                stop.wait(.1)
        summary.update(complete=True, stop_reason='owner_stopped_collector' if stop.is_set() else 'attempt_budget_reached')
    except KeyboardInterrupt:
        summary.update(complete=originals == attempted, stop_reason='owner_stopped_collector')
    except Exception as error:
        from lab_assessment_candidates import bounded_reason
        reason = bounded_reason(error)
        summary.update(complete=reason in {'collection_runtime_exhausted', 'owner_stopped_collector'}, stop_reason=reason)
    finally:
        if originals != attempted:
            summary['complete'] = False
        summary['unprocessed_matching_outputs'] = len(observed_matching - attempted)
        if summary['unprocessed_matching_outputs'] and summary['attempts'] < max_attempts:
            summary['complete'] = False
        summary['ended_at'] = clock()
        try:
            window('collection_end', summary)
        except Exception:
            summary['complete'] = False
            summary['stop_reason'] = 'candidate_collection_retention_incomplete'
        summary['archive'] = window.finalize()
        summary['complete'] = summary['complete'] and summary['archive']['status'] == 'finalized'
    return summary


def _collect_existing_api(api, *, session_id, output_file, arm, max_attempts, seconds,
                          max_bytes, clock, monotonic, stop, collection_mode=EXISTING_MODE, frozen_method=None):
    """Private successor for the old capture API; no Lab lifecycle mutation.

    A model row is frozen when its event page first arrives, and is settled only
    once its immutable capture terminal is present or collection ends. A source
    window boundary, rather than absence of a start, establishes outside-window.
    """
    bundled = collection_mode == BUNDLED_MODE
    if bundled:
        START_KIND, TERMINAL_KIND, WINDOW_KIND = BUNDLED_START, BUNDLED_TERMINAL, BUNDLED_WINDOW
    else:
        from assessment_existing_api import START_KIND, TERMINAL_KIND
        WINDOW_KIND = 'assessment.window.closed'
    sid = identity(session_id)
    execution = FrozenDeskMethodExecution(frozen_method, session_id=sid) if frozen_method is not None else None
    need(arm == 'b' and type(max_attempts) is int and 1 <= max_attempts <= 24
         and finite(seconds) and 0 < seconds <= 120 and type(max_bytes) is int
         and 1024 <= max_bytes <= 128 * 1024**2, 'bounded_la_candidate_collection_required')
    stop = stop or threading.Event()
    window = PrivateWindow(output_file, max_bytes=max_bytes)
    started, began = clock(), monotonic()
    summary = {'schema': 'home-lab-assessment-collection-2', 'version': 2,
        'collection_mode': collection_mode, 'session_id': sid, 'started_at': started, 'ended_at': None,
        'attempts': 0, 'candidate_attempts': 0, 'missing_prediction_attempts': 0, 'stale_attempts': 0,
        'refusals': {}, 'complete': False, 'stop_reason': None, 'operational_geometry': None,
        'physical_authority': False, 'collector_clock_qualification': 'unassessed',
        'collector_clock_error_bound_s': None,
        'availability_role': 'diagnostic_candidate_presence_not_qualified_service',
        'independent_error_assessed': False, 'room_envelope_assessed': False,
        'missing_terminal_attempts': 0, 'outside_window_outputs': 0, 'unresolved_window_outputs': 0,
        'capture_starts_without_model': 0, 'planned_sampling_skipped_captures': 0, 'missing_capture_terminals': 0}
    if bundled:
        summary.update(schema='home-lab-assessment-collection-3', version=3, producer_refused_captures=0)
    current = candidate = expected_generation = source_binding = closure = None
    retained, first_reads, outputs, starts, terminals, assessment_events, settled = {}, {}, {}, {}, {}, {}, set()
    outside_frames, valid = {}, True
    offers, sampling_drops, schedule_originals = {}, {}, {}
    capture_media = {}
    bundled_supports, producer_refused, retained_snapshots = {}, set(), {}
    after = 0
    if callable(getattr(api, 'bind_deadline', None)):
        api.bind_deadline(lambda: 0 if stop.is_set() else seconds - (monotonic() - began))

    def within():
        need(not stop.is_set(), 'owner_stopped_collector')
        elapsed = monotonic() - began
        need(finite(elapsed) and 0 <= elapsed < seconds, 'collection_runtime_exhausted')

    def live():
        within()
        value = api('GET', '/sessions/' + sid + '/live')
        within()
        found = session_from(value, sid)
        if current is not None:
            need(all(found[k] == current[k] for k in ('id', 'site_id', 'camera_id', 'generation')),
                 'collection_session_generation_changed')
        if expected_generation is not None:
            need(value.get('experiment_generation') == expected_generation, 'collection_model_generation_changed')
        return found

    def page_read():
        nonlocal after
        within()
        page = api('GET', f'/sessions/{sid}/events?after_id={after}&limit=128')
        first = clock()
        need(finite(first) and first >= 0 and isinstance(page.get('items'), list) and len(page['items']) <= 128,
             'bounded_original_event_page_required')
        for event in page['items']:
            key = event.get('id'); digest = sha(encoded(event))
            need(type(key) is int and key > 0 and event.get('session_id') == sid
                 and (key not in first_reads or first_reads[key][1] == digest), 'candidate_event_identity_changed')
            first_reads.setdefault(key, (first, digest))
        within()
        if page['items']:
            need(type(page.get('after_id')) is int and page['after_id'] >= max(e['id'] for e in page['items'])
                 and page['after_id'] > after, 'bounded_event_progress_required')
            after = page['after_id']
        return page['items']

    def record_for(ref):
        within()
        need(isinstance(ref, dict) and set(ref) == {'id', 'generation', 'sha256', 'size'}
             and ref['generation'] == current['generation'], 'assessment_media_ref_changed')
        row = api('GET', f'/sessions/{sid}/media/{identity(ref["id"])}/descriptor?generation={current["generation"]}')
        within()
        need(row.get('session_id') == sid and all(row.get(k) == v for k, v in ref.items()),
             'assessment_media_descriptor_changed')
        return row

    def fetch(ref, role):
        if bundled and isinstance(ref, dict) and ref.get('id') in retained_snapshots:
            found = retained_snapshots[ref['id']]
            need(set(ref) == {'id', 'generation', 'sha256', 'size'}
                 and all(found['record'].get(k) == v for k, v in ref.items())
                 and 0 < len(found['data']) <= BUNDLED_LIMITS[role], 'bundled_cached_media_ref_changed')
            return deepcopy(found)
        row = record_for(ref)
        limits = BUNDLED_LIMITS if bundled else EXISTING_LIMITS
        need(type(row.get('size')) is int and 0 < row['size'] <= limits[role], 'candidate_media_size_bound')
        need(window.bytes + ((row['size'] + 2) // 3) * 4 + len(encoded(row)) + 1024 <= max_bytes,
             'candidate_collection_byte_bound')
        within()
        raw = api.read_media(row)
        within()
        need(isinstance(raw, bytes) and len(raw) == row['size'] and sha(raw) == row['sha256'],
             'candidate_media_integrity_refused')
        value = {'record': deepcopy(row), 'data_b64': base64.b64encode(raw).decode('ascii')}
        digest = sha(encoded(value))
        need(row['id'] not in retained or retained[row['id']] == digest, 'candidate_media_identity_changed')
        if row['id'] not in retained:
            window('media', value); retained[row['id']] = digest
        found = {'record': row, 'data': raw}
        if bundled: retained_snapshots[row['id']] = deepcopy(found)
        return found

    def ingest(events):
        nonlocal source_binding, candidate, closure
        for event in events:
            kind, payload = event.get('kind'), event.get('payload', {})
            if (kind == 'perception.offered' and payload.get('arm') == arm
                    and payload.get('generation') == expected_generation):
                key = payload.get('request_id')
                need(isinstance(key, str) and len(key) == 64 and isinstance(payload.get('frame_id'), str)
                     and (key not in offers or offers[key] == event), 'assessment_offered_identity_changed')
                offers[key] = deepcopy(event)
            elif (kind == 'perception.dropped' and payload.get('arm') == arm
                  and payload.get('reason') == 'source_sampling_cadence'):
                key = payload.get('request_id')
                need(isinstance(key, str) and (key not in sampling_drops or sampling_drops[key] == event),
                     'assessment_sampling_drop_identity_changed')
                sampling_drops[key] = deepcopy(event)
            else:
                key = None
            if key is not None and event['id'] not in schedule_originals:
                need(len(schedule_originals) < 192, 'bounded_assessment_schedule_originals_required')
                schedule_originals[event['id']] = deepcopy(event)
                window('model_schedule_original', {'event': deepcopy(event),
                    'first_consumer_received_at': first_reads[event['id']][0]})
            if kind == 'perception.output' and payload.get('arm') == arm and payload.get('generation') == expected_generation:
                if event['id'] not in outputs:
                    need(len(outputs) < 96, 'bounded_pending_model_outputs_required')
                    outputs[event['id']] = deepcopy(event)
                    window('original_attempt', {'event': deepcopy(event),
                        'first_consumer_received_at': first_reads[event['id']][0]})
            if kind not in (START_KIND, TERMINAL_KIND, WINDOW_KIND):
                continue
            binding = payload.get('binding', {})
            need(binding.get('session_id') == sid and binding.get('site_id') == current['site_id']
                 and binding.get('camera_id') == current['camera_id'] and binding.get('media_generation') == current['generation']
                 and binding.get('experiment_generation') == expected_generation, 'assessment_source_binding_changed')
            if source_binding is None:
                source_binding = deepcopy(binding)
                candidate = LabAssessmentCandidates(current, arm=arm, generation=expected_generation,
                    media_generation=current['generation'], lab_profile=profile, max_attempts=max_attempts,
                    collection_mode=collection_mode, **({'bundled_rgb_binding': source_binding} if bundled else
                                                       {'existing_api_binding': source_binding}))
                if execution is not None: execution.check_binding(candidate.binding)
                window('binding', {'schema': 'home-lab-assessment-binding-3' if bundled else 'home-lab-assessment-binding-2',
                    'version': 3 if bundled else 2,
                    'binding': deepcopy(candidate.binding), 'binding_digest': candidate.binding_digest,
                    'original_session': deepcopy(current), 'original_readiness': readiness, 'started_at': started,
                    'limits': {'max_attempts': max_attempts, 'seconds': seconds, 'max_bytes': max_bytes}})
            need(binding == source_binding, 'assessment_source_binding_changed')
            if event['id'] not in assessment_events:
                need(len(assessment_events) < 49, 'bounded_assessment_originals_required')
                assessment_events[event['id']] = deepcopy(event)
                window('assessment_original', {'event': deepcopy(event),
                    'first_consumer_received_at': first_reads[event['id']][0]})
            if kind == START_KIND:
                c = payload if bundled else payload.get('capture', {}); key = c.get('capture_id')
                need(isinstance(key, str) and key and (len(starts) < (8 if bundled else 24) or key in starts),
                     'bounded_assessment_capture_starts_required')
                need(key not in starts or starts[key] == event, 'assessment_capture_start_identity_changed')
                starts[key] = deepcopy(event)
            elif kind == TERMINAL_KIND:
                key = payload.get('capture_id')
                need(isinstance(key, str) and key and (key not in terminals or terminals[key] == event),
                     'assessment_capture_terminal_identity_changed')
                terminals[key] = deepcopy(event)
            else:
                need(closure is None or closure == event, 'assessment_window_identity_changed')
                closure = deepcopy(event)

    def closed_window():
        if closure is None: return False
        p = closure['payload']
        if bundled:
            need(set(p) == {'schema', 'version', 'binding', 'capture_count', 'max_captures', 'reason', 'closed_at'}
                 and p['schema'] == 'home-lab-bundled-rgb-window-1' and type(p['version']) is int and p['version'] == 1
                 and p['binding'] == source_binding and p['reason'] in ('exhausted', 'incomplete', 'stop')
                 and type(p['capture_count']) is int and type(p['max_captures']) is int
                 and p['capture_count'] == len(starts) and 0 <= p['capture_count'] <= p['max_captures'] <= 8
                 and p['max_captures'] > 0 and (p['reason'] != 'exhausted' or p['capture_count'] == p['max_captures'])
                 and finite(p['closed_at']) and p['closed_at'] >= 0
                 and all(p['closed_at'] >= s['payload']['started_at'] and closure['id'] > s['id'] for s in starts.values())
                 and all(p['closed_at'] >= t['payload']['completed_at'] and closure['id'] > t['id'] for t in terminals.values()),
                 'bundled_window_binding_changed')
            return True
        need(set(p) == {'schema', 'version', 'binding', 'last_capture_id', 'capture_count', 'max_captures',
             'retained_bytes', 'max_retained_bytes', 'last_original', 'reason', 'closed_at'}
             and p['schema'] == 'home-lab-existing-api-assessment-window-1' and p['version'] == 1
             and p['binding'] == source_binding and p['reason'] in ('exhausted', 'incomplete', 'stop')
             and type(p['capture_count']) is int and p['capture_count'] == len(starts)
             and type(p['max_captures']) is int and 1 <= p['capture_count'] <= p['max_captures'] <= 24
             and type(p['retained_bytes']) is int and type(p['max_retained_bytes']) is int
             and 0 <= p['retained_bytes'] <= p['max_retained_bytes'] <= 40 * 1024**2
             and finite(p['closed_at']) and p['last_capture_id'] in starts,
             'assessment_window_binding_changed')
        last_start = starts[p['last_capture_id']]['payload']
        receipt = json.loads(base64.urlsafe_b64decode(last_start['capture']['rgb_header'] + '=' *
                           ((-len(last_start['capture']['rgb_header'])) % 4)))
        need(p['last_original'] == {'capture_epoch': receipt['session'],
             'timing_generation': receipt['timing_generation'], 'sequence': receipt['sequence']}
             and p['closed_at'] >= last_start['tap_started_at'], 'assessment_window_original_changed')
        return True

    def remember_outside_frame(event):
        if event['id'] in outside_frames: return
        ids = event['payload'].get('media_ids', [])
        need(isinstance(ids, list) and len(ids) <= 32, 'original_model_media_ids_required')
        found = []
        for mid in ids:
            within()
            row = api('GET', f'/sessions/{sid}/media/{identity(mid)}/descriptor?generation={current["generation"]}')
            within()
            p, m = event['payload'], row.get('metadata', {})
            if (row.get('id') == mid and row.get('session_id') == sid
                    and row.get('generation') == current['generation'] and row.get('mime_type') == 'image/jpeg'
                    and m.get('frame_id') == p.get('frame_id') and m.get('capture_epoch') == p.get('capture_epoch')
                    and m.get('observed_at') == p.get('observed_at') and m.get('site_id') == current['site_id']
                    and m.get('camera_id') == current['camera_id']):
                found.append(row)
        need(len(found) == 1, 'unmatched_model_original_frame_unavailable')
        outside_frames[event['id']] = deepcopy(found[0])
        window('outside_frame', {'event_id': event['id'], 'record': deepcopy(found[0])})

    def start_for(event):
        p = event['payload']
        if bundled:
            found = [starts[key] for key, (frame, _) in capture_media.items() if frame is not None
                and frame.get('metadata', {}).get('frame_id') == p.get('frame_id')
                and frame.get('metadata', {}).get('capture_epoch') == p.get('capture_epoch')
                and frame.get('id') in p.get('media_ids', [])]
            need(len(found) <= 1, 'bundled_model_capture_ambiguous')
            return found[0] if found else None
        found = [s for s in starts.values() if s['payload']['capture'].get('rgb_frame_id') == p.get('frame_id')
            and s['payload']['capture'].get('capture_epoch') == p.get('capture_epoch')
            and s['payload']['capture'].get('rgb_media_ref', {}).get('id') in p.get('media_ids', [])]
        need(len(found) <= 1, 'assessment_model_capture_ambiguous')
        return found[0] if found else None

    def retain_capture(start, terminal):
        nonlocal valid
        key = start['payload']['capture_id'] if bundled else start['payload']['capture']['capture_id']
        if key in capture_media: return capture_media[key]
        if bundled:
            from bundle_rgb_input import support_refs
            candidate._bundled_capture(None, start, terminal)
            anchor = fetch(start['payload']['anchor_b_media_ref'], 'anchor_b')
            ref = terminal['payload']['support_media_ref']
            if ref is None:
                valid = False
                capture_media[key] = (None, None)
                return capture_media[key]
            support = fetch(ref, 'support')
            body = original_json(support['data'])
            refs = support_refs(body)
            need(isinstance(refs, dict) and set(refs) <= set(BUNDLED_LIMITS) - {'support'},
                 'exact_bundled_media_roles_required')
            records = {'support': support}
            for role, original_ref in refs.items(): records[role] = fetch(original_ref, role)
            need(records.get('anchor_b') == anchor, 'bundled_start_anchor_changed')
            frame = records.get('rgb', {}).get('record')
            try:
                candidate._bundled_media(None, frame, records, start, terminal)
                need(terminal['payload']['status'] == body.get('status') == 'retained',
                     'bundled_retained_terminal_changed')
            except ValueError as error:
                reason = str(error)
                if (terminal['payload']['status'] == body.get('status') == 'refused'
                        and reason == 'bundled_source_refused_' + body.get('reason', '')):
                    producer_refused.add(key)
                elif (terminal['payload']['status'] == body.get('status') == 'incomplete'
                      and reason == 'bundled_source_incomplete_' + body.get('reason', '')):
                    valid = False
                else: raise
            bundled_supports[key] = deepcopy(body)
            live()
            capture_media[key] = (frame, records)
            return capture_media[key]
        rgb = fetch(start['payload']['capture']['rgb_media_ref'], 'rgb')
        try:
            # A source frame may have an original planned-sampling drop
            # instead of a model output. Validate against its ACTUAL stored
            # frame metadata, without manufacturing a perception.output.
            candidate._existing_capture(None, start, terminal, frame_record=rgb['record'])
        except ValueError as error:
            if not str(error).startswith('assessment_capture_miss_'): valid = False
        attempted_media = {}
        for attempt in terminal['payload']['attempts']:
            for field, role in (('npz_media_ref', 'bundle'), ('calibration_media_ref', 'calibration_original')):
                if attempt.get(field) is not None:
                    value = fetch(attempt[field], role)
                    attempted_media[value['record']['id']] = value
        media = None
        if terminal['payload']['status'] == 'retained':
            association = fetch(terminal['payload']['association_media_ref'], 'association')
            a = json.loads(association['data'])
            media = {'rgb': rgb, 'bundle': attempted_media[a['bundle_media_ref']['id']],
                'calibration_original': attempted_media[a['calibration_original_media_ref']['id']],
                'association': association}
        live()
        capture_media[key] = (rgb['record'], media)
        return capture_media[key]

    def settle(event, start, terminal, *, final=False):
        nonlocal valid
        if terminal is None and not final: return
        need(summary['attempts'] < max_attempts, 'candidate_attempt_budget_exhausted')
        received = first_reads[event['id']][0]
        frame = media = None; reason = None
        try:
            if bundled: candidate._bundled_capture(event, start, terminal)
            else: candidate._existing_capture(event, start, terminal)
        except ValueError as error:
            if bundled or not str(error).startswith('assessment_capture_miss_'): valid = False
        try:
            if terminal is not None:
                need(not final, 'assessment_media_not_collected_before_stop')
                frame, media = retain_capture(start, terminal)
        except Exception as error:
            from lab_assessment_candidates import bounded_reason
            reason = bounded_reason(error)
            valid = False
        if execution is not None:
            method_row = execution.execute(event, frame=frame, records=media, start=start, terminal=terminal)
            window('exact_method_execution', method_row)
        row = candidate.collect(event, received_at=received, frame_record=frame, paired_records=media,
            refusal_reason=reason, capture_started=start, capture_terminal=terminal)
        completed = clock()
        need(finite(completed) and completed >= received, 'candidate_completion_clock_refused')
        until = frame.get('metadata', {}).get('valid_until') if frame is not None else None
        horizon = event['payload'].get('valid_until')
        window('attempt', {'candidate': row, 'calculation_completed_at': completed,
            'original_rgb_valid_until': until,
            'original_support_fresh_at_completion': bool(finite(until) and finite(horizon) and completed < min(until, horizon)),
            'collector_clock_qualification': 'unassessed'})
        settled.add(event['id']); summary['attempts'] += 1
        has_sample = any(j['status'] == 'candidate' for inst in row['instances'] for j in inst['joints'])
        summary['candidate_attempts'] += int(has_sample)
        summary['missing_prediction_attempts'] += int(not has_sample)
        summary['stale_attempts'] += int(not row['source_fresh_at_collection'])
        summary['refusals'][row['reason']] = summary['refusals'].get(row['reason'], 0) + 1
        summary['missing_terminal_attempts'] += int(terminal is None)
        if terminal is None or terminal['payload'].get('status') == 'incomplete': valid = False
        if reason in {'collection_session_generation_changed', 'collection_model_generation_changed',
                      'current_recording_lab_session_required', 'owner_stopped_collector', 'collection_runtime_exhausted'}:
            valid = False
            if not final: raise ValueError(reason)

    try:
        if execution is not None: window('exact_method_frozen', deepcopy(execution.frozen))
        current = live()
        initial = []
        for _ in range(8):
            events = page_read(); initial.extend(events)
            if len(events) < 128: break
        else: raise ValueError('candidate_initial_history_bound')
        expected_generation, profile, readiness = ready_binding(initial, current, arm)
        ingest(initial)
        while True:
            within(); live()
            if bundled:
                for key, start in starts.items():
                    if key in terminals and key not in capture_media: retain_capture(start, terminals[key])
            for event in outputs.values():
                if event['id'] in settled: continue
                start = start_for(event)
                if start is not None:
                    key = start['payload']['capture_id'] if bundled else start['payload']['capture']['capture_id']
                    settle(event, start, terminals.get(key))
            # Captures skipped by the incumbent sampler still have original
            # measurement attempts. Retain their raw receipts/media as well;
            # never manufacture a perception.output for that source frame.
            for key, start in starts.items():
                if key in terminals and key not in capture_media:
                    retain_capture(start, terminals[key])
            if summary['attempts'] >= max_attempts:
                summary.update(complete=True, stop_reason='attempt_budget_reached'); break
            if closed_window():
                for event in ([] if bundled else outputs.values()):
                    if event['id'] not in settled and start_for(event) is None:
                        remember_outside_frame(event)
            matched_now = {s['payload']['capture_id'] for e in outputs.values() if (s := start_for(e)) is not None} if bundled else set()
            skipped_now = set()
            if bundled:
                for key, (frame, _) in capture_media.items():
                    if frame is None: continue
                    meta = frame['metadata']
                    request_id = sha(encoded([sid, expected_generation, arm, meta['capture_epoch'], meta['frame_id']]))
                    offered, dropped = offers.get(request_id), sampling_drops.get(request_id)
                    if offered is not None and dropped is not None and offered['id'] < dropped['id']:
                        skipped_now.add(key)
            source_accounted = not bundled or set(starts) <= matched_now | skipped_now | producer_refused
            if closure is not None and len(terminals) == len(starts) and source_accounted and all(
                    start_for(e) is None or e['id'] in settled for e in outputs.values()):
                summary.update(complete=True, stop_reason='source_window_closed'); break
            stop.wait(.1)
            ingest(page_read())
    except (KeyboardInterrupt, Exception) as error:
        from lab_assessment_candidates import bounded_reason
        reason = 'owner_stopped_collector' if isinstance(error, KeyboardInterrupt) else bounded_reason(error)
        summary.update(complete=reason in {'collection_runtime_exhausted', 'owner_stopped_collector'}, stop_reason=reason)
    finally:
        try:
            # No network after Stop/deadline. Frozen pending outputs still get
            # terminal/missing-terminal diagnostic rows and their denominator.
            for event in outputs.values():
                if event['id'] in settled: continue
                start = start_for(event)
                if start is not None and summary['attempts'] < max_attempts:
                    key = start['payload']['capture_id'] if bundled else start['payload']['capture']['capture_id']
                    settle(event, start, terminals.get(key), final=True)
                    continue
                outside = False
                if closure is not None and not bundled:
                    value = closure['payload']; last = value.get('last_original', {})
                    try:
                        # The original frame suffix is not a sequence proof.
                        # Resolve a retained start/header only; unmatched rows
                        # need an original model timing receipt to prove later.
                        receipt = outside_frames.get(event['id'], {}).get('metadata', {}).get('source_timing_receipt', {})
                        outside = (value.get('reason') == 'exhausted' and value.get('capture_count') == len(starts)
                            and isinstance(receipt, dict) and receipt.get('session') == last.get('capture_epoch')
                            and receipt.get('timing_generation') == last.get('timing_generation')
                            and type(receipt.get('sequence')) is int and receipt['sequence'] > last.get('sequence', float('inf')))
                    except (TypeError, KeyError): pass
                label = 'outside_window' if outside else 'unresolved_window'
                window(label, {'event_id': event['id'], 'reason': label})
                summary[label + '_outputs'] += 1
            matched = {(start_for(e)['payload']['capture_id'] if bundled else start_for(e)['payload']['capture']['capture_id'])
                       for e in outputs.values() if start_for(e) is not None}
            skipped = set()
            for key, start in starts.items():
                if key in matched: continue
                if bundled:
                    frame = capture_media.get(key, (None, None))[0]
                    if frame is None: continue
                    capture = {'capture_epoch': frame['metadata']['capture_epoch'], 'rgb_frame_id': frame['metadata']['frame_id']}
                else:
                    capture = start['payload']['capture']
                request_id = sha(encoded([sid, expected_generation, arm, capture['capture_epoch'], capture['rgb_frame_id']]))
                offered, dropped = offers.get(request_id), sampling_drops.get(request_id)
                if (offered is not None and dropped is not None and offered['id'] < dropped['id']
                        and offered['payload'].get('frame_id') == capture['rgb_frame_id']):
                    skipped.add(key)
            summary['planned_sampling_skipped_captures'] = len(skipped)
            summary['capture_starts_without_model'] = len(set(starts) - matched - skipped - producer_refused)
            if bundled: summary['producer_refused_captures'] = len(producer_refused)
            summary['missing_capture_terminals'] = len(set(starts) - set(terminals))
            if (not valid or set(terminals) - set(starts) or summary['missing_terminal_attempts']
                    or summary['missing_capture_terminals'] or summary['unresolved_window_outputs'] or summary['capture_starts_without_model']):
                summary['complete'] = False
            if any(t['payload'].get('status') == 'incomplete' for t in terminals.values()): summary['complete'] = False
            if closure is not None and closure['payload'].get('reason') in ('incomplete', 'stop'): summary['complete'] = False
            if bundled and (closure is None or not closed_window()): summary['complete'] = False
            summary['unprocessed_matching_outputs'] = len(outputs) - len(settled) - summary['outside_window_outputs'] - summary['unresolved_window_outputs']
            if summary['unprocessed_matching_outputs']: summary['complete'] = False
            summary['ended_at'] = clock()
            window('collection_end', summary)
        except Exception:
            summary.update(complete=False, stop_reason='candidate_collection_retention_incomplete')
        summary['archive'] = window.finalize()
        summary['complete'] = summary['complete'] and summary['archive']['status'] == 'finalized'
    return summary


def _verify_bundled_archive(result, bindings, source_events, schedule, originals, attempts, exclusions, media, *, captures=None, attempt_receipts=None):
    """Verify this closed family from the same already-read private archive."""
    need(result.get('schema') == 'home-lab-assessment-collection-3' and type(result.get('version')) is int
         and result['version'] == 3 and result.get('collection_mode') == BUNDLED_MODE
         and result.get('operational_geometry') is None and result.get('physical_authority') is False,
         'retained_bundled_collection_changed')
    need(len(bindings) == 1 or not source_events and not attempts and result.get('complete') is False,
         'retained_bundled_binding_missing')
    counts = {'missing_terminal_attempts': sum(c['capture_terminal'] is None for c in attempts.values()),
              'outside_window_outputs': 0, 'unresolved_window_outputs': len(exclusions),
              'planned_sampling_skipped_captures': 0, 'producer_refused_captures': 0,
              'capture_starts_without_model': 0, 'missing_capture_terminals': 0}
    need(all(label == 'unresolved_window' for label in exclusions.values()), 'bundled_outside_window_proof_unavailable')
    if not bindings:
        result['missing_source_media_records'] = 0
        return counts
    row = bindings[0]
    need(set(row) == {'schema', 'version', 'binding', 'binding_digest', 'original_session', 'original_readiness',
         'started_at', 'limits'} and row['schema'] == 'home-lab-assessment-binding-3'
         and type(row['version']) is int and row['version'] == 3
         and sha(encoded(row['binding'])) == row['binding_digest'], 'retained_bundled_binding_changed')
    bound = row['binding']
    candidate = LabAssessmentCandidates(row['original_session'], arm=bound['arm'], generation=bound['model_generation'],
        media_generation=bound['media_generation'], lab_profile=bound['lab_profile'], sampling=bound['sampling'],
        max_attempts=row['limits']['max_attempts'], collection_mode=BUNDLED_MODE,
        bundled_rgb_binding=bound['bundled_rgb_binding'])
    need(candidate.binding == bound and candidate.binding_digest == row['binding_digest']
         and result['session_id'] == bound['session']['id'], 'retained_bundled_binding_changed')
    generation, profile, _ = ready_binding(row['original_readiness'], row['original_session'], bound['arm'])
    need(generation == bound['model_generation'] and profile == bound['lab_profile']
         and not set(source_events).intersection(originals) and not set(source_events).intersection(schedule)
         and not set(originals).intersection(schedule), 'retained_bundled_original_identity_changed')
    starts, terminals, closures = {}, {}, []
    for event in source_events.values():
        kind, value = event['kind'], event['payload']
        need(event.get('session_id') == bound['session']['id'] and value.get('binding') == bound['bundled_rgb_binding'],
             'retained_bundled_source_binding_changed')
        if kind == BUNDLED_START:
            key = value['capture_id']
            need(key not in starts and len(starts) < 8, 'retained_bundled_capture_start_changed')
            starts[key] = event
        elif kind == BUNDLED_TERMINAL:
            key = value['capture_id']
            need(key not in terminals, 'retained_bundled_capture_terminal_changed')
            terminals[key] = event
        elif kind == BUNDLED_WINDOW:
            closures.append(event)
        else:
            raise ValueError('retained_bundled_source_kind_changed')
    need(not set(terminals) - set(starts) and len(closures) <= 1, 'retained_bundled_source_window_changed')
    source_binding = bound['bundled_rgb_binding']
    for closure in closures:
        value = closure['payload']
        need(set(value) == {'schema', 'version', 'binding', 'capture_count', 'max_captures', 'reason', 'closed_at'}
             and value['schema'] == 'home-lab-bundled-rgb-window-1' and type(value['version']) is int and value['version'] == 1
             and value['binding'] == source_binding and value['reason'] in ('exhausted', 'stop', 'incomplete')
             and type(value['capture_count']) is int and type(value['max_captures']) is int
             and value['capture_count'] == len(starts) and 0 <= value['capture_count'] <= value['max_captures'] <= 8
             and value['max_captures'] > 0
             and (value['reason'] != 'exhausted' or value['capture_count'] == value['max_captures'])
             and finite(value['closed_at']) and value['closed_at'] >= 0
             and all(value['closed_at'] >= s['payload']['started_at'] and closure['id'] > s['id'] for s in starts.values())
             and all(value['closed_at'] >= t['payload']['completed_at'] and closure['id'] > t['id'] for t in terminals.values()),
             'retained_bundled_window_changed')
    missing_media, source_frames, source_records, refused = set(), {}, {}, set()
    def snapshot(ref):
        need(isinstance(ref, dict) and set(ref) == {'id', 'generation', 'sha256', 'size'},
             'retained_bundled_media_ref_changed')
        found = media.get(ref['id'])
        if found is None:
            missing_media.add(ref['id'])
            return None
        need(all(found['record'].get(k) == v for k, v in ref.items()), 'retained_bundled_media_ref_changed')
        return found
    from bundle_rgb_input import support_refs
    incomplete_sources = set()
    for key, start in starts.items():
        anchor = snapshot(start['payload']['anchor_b_media_ref'])
        terminal = terminals.get(key)
        if terminal is None: continue
        candidate._bundled_capture(None, start, terminal)
        ref = terminal['payload']['support_media_ref']
        if ref is None:
            incomplete_sources.add(key)
            continue
        support = snapshot(ref)
        if support is None: continue
        need(len(support['data']) <= BUNDLED_LIMITS['support'], 'retained_bundled_support_bound')
        body = original_json(support['data'])
        refs = support_refs(body)
        need(isinstance(refs, dict) and set(refs) <= set(BUNDLED_LIMITS) - {'support'},
             'exact_bundled_media_roles_required')
        records = {'support': support}
        for role, original_ref in refs.items():
            found = snapshot(original_ref)
            if found is not None: records[role] = found
        if anchor is None or set(records) != {'support'} | set(refs): continue
        need(records.get('anchor_b') == anchor, 'retained_bundled_start_anchor_changed')
        frame = records.get('rgb', {}).get('record')
        try:
            candidate._bundled_media(None, frame, records, start, terminal)
            need(terminal['payload']['status'] == body.get('status') == 'retained',
                 'retained_bundled_terminal_changed')
        except ValueError as error:
            if (terminal['payload']['status'] == body.get('status') == 'refused'
                    and str(error) == 'bundled_source_refused_' + body.get('reason', '')):
                refused.add(key)
            elif (terminal['payload']['status'] == body.get('status') == 'incomplete'
                  and str(error) == 'bundled_source_incomplete_' + body.get('reason', '')):
                incomplete_sources.add(key)
            else: raise
        source_frames[key], source_records[key] = frame, records
    matched = set()
    for original in originals.values():
        event, payload = original['event'], original['event']['payload']
        need(candidate.matches(event), 'retained_bundled_model_binding_changed')
        found = [key for key, frame in source_frames.items() if frame is not None
            and frame.get('metadata', {}).get('frame_id') == payload.get('frame_id')
            and frame.get('metadata', {}).get('capture_epoch') == payload.get('capture_epoch')
            and frame.get('id') in payload.get('media_ids', [])]
        need(len(found) <= 1, 'retained_bundled_model_capture_ambiguous')
        if not found:
            need(event['id'] in exclusions or result.get('complete') is False,
                 'retained_bundled_model_capture_missing')
            continue
        key = found[0]
        need(key not in refused and key not in matched, 'retained_bundled_model_capture_changed')
        matched.add(key)
        c = attempts.get(event['id'])
        if c is None: continue
        need(c.get('capture_started') == starts[key] and c.get('capture_terminal') == terminals.get(key),
             'retained_bundled_attempt_capture_changed')
        if c.get('original_frame_record') == source_frames[key]:
            expected = candidate.collect(event, received_at=original['first_consumer_received_at'],
                frame_record=source_frames[key], paired_records=source_records[key],
                capture_started=starts[key], capture_terminal=terminals.get(key))
            need(expected == c, 'retained_bundled_candidate_changed')
        else:
            need(result.get('complete') is False and c.get('original_frame_record') is None and not c['media_records']
                 and c.get('status') == 'refused' and all(j.get('camera_xyz_m') is None and j.get('status') != 'candidate'
                 for inst in c['instances'] for j in inst['joints']), 'unverified_bundled_metric_candidate')
    skipped = set()
    for key, frame in source_frames.items():
        if key in matched or key in refused or frame is None: continue
        meta = frame['metadata']
        request_id = sha(encoded([bound['session']['id'], bound['model_generation'], bound['arm'],
                                 meta['capture_epoch'], meta['frame_id']]))
        offered = [e for e in schedule.values() if e['kind'] == 'perception.offered'
            and e.get('session_id') == bound['session']['id'] and e['payload'].get('request_id') == request_id
            and e['payload'].get('arm') == bound['arm'] and e['payload'].get('generation') == bound['model_generation']
            and e['payload'].get('frame_id') == meta['frame_id']]
        dropped = [e for e in schedule.values() if e['kind'] == 'perception.dropped'
            and e.get('session_id') == bound['session']['id'] and e['payload'].get('request_id') == request_id
            and e['payload'].get('arm') == bound['arm'] and e['payload'].get('reason') == 'source_sampling_cadence']
        if len(offered) == len(dropped) == 1 and offered[0]['id'] < dropped[0]['id']: skipped.add(key)
    counts.update(planned_sampling_skipped_captures=len(skipped), producer_refused_captures=len(refused),
        capture_starts_without_model=len(set(starts) - matched - skipped - refused),
        missing_capture_terminals=len(set(starts) - set(terminals)))
    result['missing_source_media_records'] = len(missing_media)
    need(not result.get('complete') or len(closures) == 1 and closures[0]['payload']['reason'] == 'exhausted'
         and not missing_media and not incomplete_sources
         and not any(counts[k] for k in ('missing_terminal_attempts', 'missing_capture_terminals',
                    'unresolved_window_outputs', 'capture_starts_without_model')),
         'incomplete_bundled_window_reported_complete')
    refusal_counts = {}
    for c in attempts.values(): refusal_counts[c['reason']] = refusal_counts.get(c['reason'], 0) + 1
    need(result.get('refusals') == refusal_counts and result.get('unprocessed_matching_outputs') == 0,
         'retained_bundled_summary_changed')
    if captures is not None:
        need(len(attempts) <= 24, 'bounded_verified_bundled_attempts_required')
        for key, attempt in attempts.items():
            original = originals[key]
            payload = original['event']['payload']
            found = [capture_id for capture_id, frame in source_frames.items() if frame is not None
                and frame['metadata'].get('frame_id') == payload.get('frame_id')
                and frame['metadata'].get('capture_epoch') == payload.get('capture_epoch')
                and frame['id'] in payload.get('media_ids', [])]
            capture_id = found[0] if found else None
            # Only complete, already-validated snapshots cross this private
            # handoff. Missing originals retain their attempt/denominator.
            complete = (capture_id is not None and capture_id not in refused
                and capture_id not in incomplete_sources
                and set(source_records.get(capture_id, {})) == set(BUNDLED_LIMITS)
                and attempt.get('original_frame_record') == source_frames[capture_id])
            captures.append(deepcopy(dict(source_event_id=key, event=original['event'],
                first_consumer_received_at=original['first_consumer_received_at'],
                calculation_completed_at=attempt_receipts[key],
                frame_record=source_frames[capture_id] if complete else None,
                records=source_records[capture_id] if complete else {},
                capture_started=starts[capture_id] if complete else None,
                capture_terminal=terminals[capture_id] if complete else None)))
    return counts


def _read_results(filename, *, include_attempts=False, include_bundled_originals=False,
                  expected_execution_qualification=None):
    path = Path(filename)
    need(path.is_absolute() and path.is_file() and not path.is_symlink()
         and path.stat().st_size <= 128 * 1024**2, 'bounded_private_candidate_results_required')
    raw = path.read_bytes()
    need(raw.endswith(b'\n'), 'candidate_results_truncated')
    rows = [json.loads(line) for line in raw.splitlines()]
    need(rows and rows[-1]['kind'] == 'collection_end', 'candidate_results_incomplete')
    media, originals, attempts, assessment_originals, exclusions, outside_frames = {}, {}, {}, {}, {}, {}
    media_snapshots = {}
    attempt_receipts = {}
    schedule_originals = {}
    bundled = rows[-1]['value'].get('collection_mode') == BUNDLED_MODE
    successor = rows[-1]['value'].get('collection_mode') in (EXISTING_MODE, BUNDLED_MODE)
    if bundled:
        rows = [original_json(line) for line in raw.splitlines()]
    bindings = [r['value'] for r in rows if r['kind'] == 'binding']
    if successor:
        need(len(bindings) == 1 or not any(r['kind'] == 'attempt' for r in rows), 'retained_assessment_binding_missing')
    for row in rows:
        if row['kind'] == 'original_attempt':
            original = row['value']; key = original['event']['id']
            need(key not in originals, 'duplicate_retained_candidate_original')
            originals[key] = original
        if successor and row['kind'] == 'assessment_original':
            event = row['value']['event']; key = event['id']
            need(key not in assessment_originals, 'duplicate_retained_assessment_original')
            assessment_originals[key] = event
        if successor and row['kind'] in ('outside_window', 'unresolved_window'):
            key = row['value']['event_id']
            need(key not in exclusions, 'duplicate_retained_window_outcome')
            exclusions[key] = row['kind']
        if successor and row['kind'] == 'outside_frame':
            key = row['value']['event_id']
            need(key not in outside_frames, 'duplicate_retained_outside_frame')
            outside_frames[key] = row['value']['record']
        if successor and row['kind'] == 'model_schedule_original':
            event = row['value']['event']; key = event['id']
            need(key not in schedule_originals and event['kind'] in ('perception.offered', 'perception.dropped'),
                 'retained_sampling_original_changed')
            schedule_originals[key] = event
        if row['kind'] != 'media':
            continue
        v = row['value']; record = v['record']; data = base64.b64decode(v['data_b64'], validate=True)
        need(len(data) == record['size'] and sha(data) == record['sha256'], 'retained_candidate_media_changed')
        need(record['id'] not in media or media[record['id']] == record, 'retained_candidate_media_identity_changed')
        media[record['id']] = record
        if bundled:
            need(0 < len(data) <= 4 * 1024**2, 'bounded_retained_bundled_media_required')
            media_snapshots[record['id']] = {'record': deepcopy(record), 'data': data}
    for row in rows:
        if row['kind'] == 'attempt':
            c = row['value']['candidate']
            need(c['original_event_sha256'] == sha(encoded(c['original_event'])), 'retained_candidate_original_changed')
            key = c['source_event_id']; original = originals.get(key)
            need(original is not None and original['event'] == c['original_event']
                 and original['first_consumer_received_at'] == c['first_consumer_received_at']
                 and key not in attempts, 'retained_candidate_original_binding_changed')
            need(all(c.get(k) == v for k, v in AUTHORITY.items()), 'candidate_operational_authority_refused')
            if successor:
                need(c.get('schema') == ('home-lab-assessment-attempt-3' if bundled else 'home-lab-assessment-attempt-2')
                     and type(c.get('version')) is int and c.get('version') == (3 if bundled else 2)
                     and c.get('collection_mode') == (BUNDLED_MODE if bundled else EXISTING_MODE) and bindings
                     and c['binding_digest'] == bindings[0]['binding_digest']
                     and sha(encoded(bindings[0]['binding'])) == c['binding_digest'], 'retained_assessment_binding_changed')
                start, terminal = c.get('capture_started'), c.get('capture_terminal')
                need(isinstance(start, dict) and assessment_originals.get(start['id']) == start,
                     'retained_capture_start_missing_or_changed')
                need(terminal is None or isinstance(terminal, dict) and assessment_originals.get(terminal['id']) == terminal,
                     'retained_capture_terminal_missing_or_changed')
                need(terminal is not None or rows[-1]['value'].get('complete') is False,
                     'missing_capture_terminal_cannot_be_complete')
            for record in c['media_records'].values():
                need(media.get(record['id']) == record, 'retained_candidate_media_missing_or_changed')
            attempts[key] = c
            if include_bundled_originals:
                completed = row['value'].get('calculation_completed_at')
                need(completed is None or finite(completed) and completed >= original['first_consumer_received_at'],
                     'retained_bundled_full_read_receipt_required')
                attempt_receipts[key] = completed
    result = deepcopy(rows[-1]['value'])
    need(set(originals) == set(attempts) | set(exclusions) and not set(attempts).intersection(exclusions),
         'retained_candidate_attempt_incomplete')
    counts = {'attempts': len(attempts), 'candidate_attempts': sum(any(j['status'] == 'candidate'
        for inst in c['instances'] for j in inst['joints']) for c in attempts.values()),
        'stale_attempts': sum(not c['source_fresh_at_collection'] for c in attempts.values())}
    counts['missing_prediction_attempts'] = counts['attempts'] - counts['candidate_attempts']
    captures = [] if include_bundled_originals else None
    if include_bundled_originals:
        need(bundled, 'explicit_bundled_verified_originals_required')
    if bundled:
        counts.update(_verify_bundled_archive(result, bindings, assessment_originals, schedule_originals,
                      originals, attempts, exclusions, media_snapshots, captures=captures, attempt_receipts=attempt_receipts))
    elif successor:
        counts['missing_terminal_attempts'] = sum(c['capture_terminal'] is None for c in attempts.values())
        counts['outside_window_outputs'] = sum(v == 'outside_window' for v in exclusions.values())
        counts['unresolved_window_outputs'] = sum(v == 'unresolved_window' for v in exclusions.values())
        starts = [e for e in assessment_originals.values() if e['kind'] == 'assessment.capture.started']
        matched = {s['payload']['capture']['capture_id'] for s in starts for original in originals.values()
            if s['payload']['capture'].get('rgb_frame_id') == original['event']['payload'].get('frame_id')
            and s['payload']['capture'].get('capture_epoch') == original['event']['payload'].get('capture_epoch')
            and s['payload']['capture'].get('rgb_media_ref', {}).get('id') in original['event']['payload'].get('media_ids', [])}
        skipped = set()
        if bindings:
            bound = bindings[0]['binding']
            for start in starts:
                capture = start['payload']['capture']; key = capture['capture_id']
                if key in matched: continue
                request_id = sha(encoded([bound['session']['id'], bound['model_generation'], bound['arm'],
                                         capture['capture_epoch'], capture['rgb_frame_id']]))
                offered = [e for e in schedule_originals.values() if e['kind'] == 'perception.offered'
                    and e.get('session_id') == bound['session']['id'] and e['payload'].get('request_id') == request_id
                    and e['payload'].get('arm') == bound['arm'] and e['payload'].get('generation') == bound['model_generation']
                    and e['payload'].get('frame_id') == capture['rgb_frame_id']]
                dropped = [e for e in schedule_originals.values() if e['kind'] == 'perception.dropped'
                    and e.get('session_id') == bound['session']['id'] and e['payload'].get('request_id') == request_id
                    and e['payload'].get('arm') == bound['arm'] and e['payload'].get('reason') == 'source_sampling_cadence']
                if len(offered) == len(dropped) == 1 and offered[0]['id'] < dropped[0]['id']: skipped.add(key)
        counts['planned_sampling_skipped_captures'] = len(skipped)
        counts['capture_starts_without_model'] = len({s['payload']['capture']['capture_id'] for s in starts} - matched - skipped)
        terminal_ids = {e['payload']['capture_id'] for e in assessment_originals.values()
                        if e['kind'] == 'assessment.capture.terminal'}
        counts['missing_capture_terminals'] = len({s['payload']['capture']['capture_id'] for s in starts} - terminal_ids)
        closures = [e for e in assessment_originals.values() if e['kind'] == 'assessment.window.closed']
        need(not result.get('complete') or not any(c['payload'].get('reason') in ('incomplete', 'stop') for c in closures),
             'incomplete_source_window_reported_complete')
        for key, label in exclusions.items():
            if label != 'outside_window': continue
            need(len(closures) == 1 and closures[0]['payload']['reason'] == 'exhausted'
                 and key in outside_frames, 'retained_outside_window_proof_missing')
            frame, event, last = outside_frames[key], originals[key]['event'], closures[0]['payload']['last_original']
            meta, payload = frame['metadata'], event['payload']
            receipt = meta['source_timing_receipt']
            need(frame['id'] in payload.get('media_ids', []) and frame['session_id'] == event['session_id']
                 and meta['frame_id'] == payload.get('frame_id') and meta['capture_epoch'] == payload.get('capture_epoch')
                 and meta['observed_at'] == payload.get('observed_at') and receipt['session'] == last['capture_epoch']
                 and receipt['timing_generation'] == last['timing_generation'] and receipt['sequence'] > last['sequence'],
                 'retained_outside_window_proof_changed')
        missing_media = set()
        for source in assessment_originals.values():
            value = source['payload']
            refs = []
            if source['kind'] == 'assessment.capture.started': refs.append(value['capture']['rgb_media_ref'])
            if source['kind'] == 'assessment.capture.terminal':
                if value.get('association_media_ref') is not None: refs.append(value['association_media_ref'])
                refs.extend(a[k] for a in value['attempts'] for k in ('npz_media_ref', 'calibration_media_ref')
                            if a.get(k) is not None)
            for ref in refs:
                record = media.get(ref['id'])
                if record is None: missing_media.add(ref['id'])
                else: need(all(record[k] == v for k, v in ref.items()), 'retained_assessment_media_ref_changed')
        result['missing_source_media_records'] = len(missing_media)
        need(not result.get('complete') or not missing_media, 'complete_assessment_original_media_missing')
        need(not result.get('complete') or not any(counts[k] for k in
             ('missing_terminal_attempts', 'missing_capture_terminals', 'unresolved_window_outputs', 'capture_starts_without_model')),
             'incomplete_capture_window_reported_complete')
    need(all(result.get(k) == v for k, v in counts.items()), 'retained_candidate_summary_changed')
    result.update(counts)
    result.update(private_media_records=len(media), ordinary_lighting_replay=False,
                  archive_sha256=sha(raw), qualified_error_bound_m=None)
    if include_attempts or include_bundled_originals:
        need(successor and len(bindings) == 1
             and bindings[0].get('schema') == ('home-lab-assessment-binding-3' if bundled else 'home-lab-assessment-binding-2')
             and bindings[0].get('version') == (3 if bundled else 2), 'verified_existing_assessment_binding_required')
        verified = {'binding': deepcopy(bindings[0]), 'attempts': deepcopy(list(attempts.values())),
                    'summary': deepcopy(result), 'collection_sha256': sha(raw)}
        if include_bundled_originals:
            output = dict(collection=verified, original_family=BUNDLED_MODE,
                input_binding=bindings[0]['binding']['bundled_rgb_binding'], captures=captures)
            if expected_execution_qualification is not None:
                fixed = [(i,r['value']) for i,r in enumerate(rows) if r['kind'] == 'exact_method_frozen']
                need(len(fixed) == 1 and re.fullmatch('[0-9a-f]{64}', expected_execution_qualification) is not None,
                     'one_independently_pinned_execution_method_required')
                position, frozen = fixed[0]
                executor = FrozenDeskMethodExecution(frozen['qualification'], session_id=result['session_id'])
                executor.check_binding(bindings[0]['binding'])
                need(frozen == executor.frozen and frozen['qualification_sha256'] == expected_execution_qualification,
                     'retained_frozen_execution_method_changed')
                need(all(i > position for i,r in enumerate(rows) if r['kind'] in
                         ('original_attempt','exact_method_execution')), 'method_must_precede_prediction_reads')
                saved = [r['value'] for r in rows if r['kind'] == 'exact_method_execution']
                by_id = {r['source_event_id']: r for r in saved}
                need(len(saved) == len(by_id) == len(captures) and set(by_id) == {c['source_event_id'] for c in captures},
                     'every_attempt_requires_one_exact_method_outcome')
                output.update(frozen_method=deepcopy(frozen), method_executions=[
                    executor.validate_record(by_id[c['source_event_id']], c, attempts[c['source_event_id']]) for c in captures])
            return deepcopy(output)
        return verified
    return result


def read_results(filename):
    """Historical aggregate result; never exposes private nominal estimates."""
    return _read_results(filename)


def read_verified_attempts(filename):
    """Owner-local scoring input after the SAME exact-byte verification.

    No second file read, network request, model call or operational authority.
    Unknown metric/person/clock qualifications remain original and unavailable.
    The owner-local assessment capability may consume this result; Home routes
    and ordinary aggregate read-results keep their historical result shape.
    """
    return _read_results(filename, include_attempts=True)


def read_verified_bundled_originals(filename):
    """Private same-read seven-original input for explicit qualification3.

    The historical four-key reader is unchanged. Every retained attempt has
    one entry here, including unavailable/refused attempts with null originals.
    No Store/network/model fallback, new clock claim or browser capability.
    """
    return _read_results(filename, include_bundled_originals=True)


def read_verified_method_executions(filename, *, expected_qualification_sha256):
    """Private opt-in same-read execution originals; old reader shapes stay fixed.

    This establishes recorded numeric work and its conditional host-clock bounds,
    not camera timing, independent geometry truth or a method-service pass.
    """
    return _read_results(filename, include_bundled_originals=True,
        expected_execution_qualification=expected_qualification_sha256)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-id')
    parser.add_argument('--token-file')
    parser.add_argument('--ca-file')
    parser.add_argument('--output-file')
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--max-attempts', type=int, default=24)
    parser.add_argument('--collection-mode', choices=('paired-preview-1', EXISTING_MODE, BUNDLED_MODE), default='paired-preview-1')
    parser.add_argument('--frozen-method-file', help='Private qualification3 frozen before reading predictions')
    parser.add_argument('--collect-existing-session', action='store_true')
    parser.add_argument('--read-results')
    args = parser.parse_args(argv)
    if args.read_results:
        print(json.dumps(read_results(args.read_results), sort_keys=True)); return 0
    if not args.collect_existing_session:
        prepared = {'status': 'prepared_only', 'network_requests': 0,
            'entry': 'attach_existing_recording', 'stop': 'Ctrl+C stops collector only',
            'requires': ['existing_Lab_recording', 'paired_preview_raw_media',
                         'existing_read_credential_and_CA', 'fresh_private_output_file'],
            'independent_error_bound_m': None, 'operational_geometry': None}
        if args.collection_mode == EXISTING_MODE:
            prepared.update(collection_mode=EXISTING_MODE)
            prepared['requires'][1] = 'existing_api_assessment_start_terminal_and_original_media'
        elif args.collection_mode == BUNDLED_MODE:
            prepared.update(collection_mode=BUNDLED_MODE)
            prepared['requires'][1] = 'bundled_rgb_start_terminal_support_and_original_media'
        print(json.dumps(prepared)); return 0
    if not all((args.session_id, args.token_file, args.ca_file, args.output_file)):
        parser.error('collection requires session, protected token/CA and private output file')
    frozen = None
    if args.frozen_method_file:
        path = Path(args.frozen_method_file)
        need(args.collection_mode == BUNDLED_MODE and path.is_absolute() and not path.is_symlink()
             and path.is_file() and 0 < path.stat().st_size <= 64*1024, 'bounded_private_frozen_method_file_required')
        with path.open('rb') as stream: raw = stream.read(64*1024+1)
        need(0 < len(raw) <= 64*1024, 'bounded_private_frozen_method_file_required')
        frozen = original_json(raw)
    result = collect_existing(FixedLabReadClient(token_file=args.token_file, ca_file=args.ca_file),
        session_id=args.session_id, output_file=args.output_file,
        seconds=args.seconds, max_attempts=args.max_attempts, collection_mode=args.collection_mode, frozen_method=frozen)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
