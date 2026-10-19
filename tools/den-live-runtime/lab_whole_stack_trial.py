"""Bounded incumbent-Lab proof; prepared-only unless explicitly executed.

Run from the checkout root with ``python -m tools.den-live-runtime.lab_whole_stack_trial``.
Only the existing Lab API starts/stops its paired workers. No media is fetched.
Private JSONL retains original event bodies, including lossless integer clocks.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import ssl
import time
import urllib.request
import uuid

PREFIX = '/api/perception-lab/v1'
ORIGIN = 'https://192.168.1.168:8789'
DETECTOR_SHA = '646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b'
POSE_SHA = '5c0a4bf67953e6d2ac43ce15e77dc9d5d354ae18430a47d2c5963a7bc5683e3c'
JEV_VERSION = 'jev-1.13.0'
JEV_WIRE = 'den-facts-7'
ACTIVE = {'requested', 'starting', 'recording', 'stopping', 'indeterminate'}
TERMINAL = {'stopped', 'failed', 'interrupted'}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def lease(payload, at):
    times = [payload.get(k) for k in ('observed_at', 'available_at', 'valid_until')]
    return (all(finite(t) for t in times) and finite(at)
            and times[0] <= times[1] <= at < times[2])


@dataclass(frozen=True)
class Plan:
    request_id: str
    recording_seconds: int = 30
    preparation_seconds: int = 60
    cleanup_seconds: int = 45
    page_limit: int = 100
    max_events: int = 6000

    def __post_init__(self):
        if (str(uuid.UUID(self.request_id)) != self.request_id
                or self.recording_seconds not in (15, 30)
                or self.preparation_seconds != 60 or self.cleanup_seconds != 45
                or self.page_limit != 100 or self.max_events != 6000):
            raise ValueError('fixed_trial_budget_required')

    def body(self):
        return {'camera_id': 'den', 'experiment_id': 'placement-best',
                'name': 'Den whole-stack bounded proof', 'request_id': self.request_id,
                'reasoning_enabled': True}

    def matches(self, session):
        return isinstance(session, dict) and all(session.get(k) == v for k, v in {
            'request_id': self.request_id, 'camera_id': 'den',
            'experiment_id': 'placement-best', 'site_id': 'victoria'}.items())


class PrivateWindow:
    """Exclusive bounded owner-local metadata file; not journal/replay authority."""
    def __init__(self, filename, *, max_bytes=32 * 1024**2):
        self.path = Path(filename)
        if not self.path.is_absolute() or not self.path.parent.is_dir():
            raise ValueError('existing_private_output_directory_required')
        self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                          | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        self.max_bytes, self.bytes, self.count, self.failed = max_bytes, 0, 0, False
        self.hash = hashlib.sha256()
        try:
            sync_parent(self.path.parent)
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise

    def __call__(self, kind, value):
        raw = encoded({'kind': kind, 'value': value}) + b'\n'
        if self.failed or self.bytes + len(raw) > self.max_bytes:
            self.failed = True
            raise ValueError('original_window_exhausted')
        try:
            remaining = memoryview(raw)
            while remaining:
                n = os.write(self.fd, remaining)
                if n <= 0:
                    raise OSError('original_window_write_failed')
                remaining = remaining[n:]
            os.fsync(self.fd)
        except BaseException:
            self.failed = True
            raise
        self.bytes += len(raw)
        self.count += 1
        self.hash.update(raw)

    def finalize(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        return {'status': 'incomplete' if self.failed else 'finalized',
                'records': self.count, 'bytes': self.bytes, 'sha256': self.hash.hexdigest()}


def sync_parent(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def positive_projection(event, profile, *, server_time):
    """Original B composite result, not A success or just a terminal response."""
    p = event.get('payload', {})
    if event.get('kind') != 'perception.output' or p.get('arm') != 'b':
        return None
    result = p.get('result', {})
    processing = result.get('processing', {})
    detector, pose = processing.get('detector', {}), processing.get('pose', {})
    boxes = [b for b in result.get('boxes', []) if b.get('label') == 'person'
             and finite(b.get('confidence')) and b['confidence'] >= .5]
    instances = result.get('pose_instances', [])
    supported = sum(j.get('status') == 'supported' for item in instances for j in item.get('joints', []))
    bound = (isinstance(profile, dict) and profile.get('profile_id') == 'yolo26s-rtmpose-cpu-v1'
             and all(p.get(k) == profile.get(k) and p.get(k) is not None
                     for k in ('artifact_sha256', 'engine_epoch', 'model_version')))
    positive = (bound and p.get('status') == 'completed' and p.get('current') is True
                and lease(p, server_time) and result.get('frame_id') == p.get('frame_id')
                and isinstance(p.get('media_ids'), list) and 1 <= len(p['media_ids']) <= 8
                and all(isinstance(mid, str) and mid for mid in p['media_ids'])
                and isinstance(p.get('frame_id'), str) and bool(p['frame_id'])
                and isinstance(p.get('capture_epoch'), str) and bool(p['capture_epoch'])
                and processing.get('shared_source_request') == p.get('request_id')
                and isinstance(p.get('request_id'), str) and bool(p['request_id'])
                and detector.get('artifact_sha256') == DETECTOR_SHA
                and pose.get('artifact_sha256') == POSE_SHA
                and pose.get('model_invoked') is True and pose.get('crop_count', 0) > 0
                and bool(boxes) and bool(instances) and supported > 0)
    return {'event_id': event['id'], 'arm': 'b', 'positive': bool(positive),
            **{k: p.get(k) for k in ('request_id', 'frame_id', 'capture_epoch', 'generation',
                'engine_epoch', 'artifact_sha256', 'media_ids', 'observed_at', 'available_at', 'valid_until')},
            'supported_joint_count': supported, 'pose_model_invoked': pose.get('model_invoked')}


def capture_projection(media, row, session, server_time):
    """Bind a metadata-only descriptor; preserve its original ns without conversion."""
    m = media.get('metadata', {})
    timing = m.get('source_timing_receipt', {})
    if (media.get('id') not in row['media_ids'] or media.get('media_id') != media.get('id')
            or media.get('session_id') != session['id'] or media.get('generation') != session['generation']
            or media.get('site_id') != 'victoria' or media.get('mime_type') != 'image/jpeg'
            or m.get('stream') != 'rgb' or m.get('site_id') != 'victoria' or m.get('camera_id') != 'den'
            or any(m.get(k) != row.get(k) for k in ('frame_id', 'capture_epoch', 'observed_at'))
            or m.get('clock_mapping_status') != 'verified'
            or not all(finite(m.get(k)) for k in ('observed_at', 'available_at', 'valid_until'))
            or not 0 <= m['observed_at'] <= m['available_at'] < m['valid_until']
            or not finite(server_time) or server_time < m['available_at']
            or timing.get('session') != m['capture_epoch']
            or timing.get('sequence') != m.get('sequence')
            or timing.get('timing_generation') != m.get('timing_generation')
            or timing.get('jpeg_sha256') != media.get('sha256')
            or type(timing.get('observed_at_unix_ns')) is not int):
        raise ValueError('original_rgb_descriptor_binding_mismatch')
    if not server_time < min(row['valid_until'], m['valid_until']):
        raise ValueError('original_rgb_descriptor_expired')
    return {'event_id': row['event_id'], 'media_id': media['id'], 'media_generation': media['generation'],
            'sha256': media['sha256'], 'frame_id': m['frame_id'], 'capture_epoch': m['capture_epoch'],
            'source_observed_at_unix_ns': timing['observed_at_unix_ns'],
            'effective_valid_until': min(row['valid_until'], m['valid_until'])}


def pc_projection(event, *, server_time):
    p = event.get('payload', {})
    if event.get('kind') != 'ha.observation' or p.get('quality', {}).get('source_id') != 'victoria-ha-lab':
        return None
    fields = p.get('ha', {})
    keys = ('sensor.formd_t1_sessionstate', 'sensor.formd_t1_lastactive',
            'derived.computer_active', 'derived.computer_input_recency')
    known = all(isinstance(fields.get(k), dict) and fields[k].get('status') == 'known' for k in keys)
    roots = p.get('evidence_roots')
    return {'event_id': event['id'], 'fresh': bool(lease(p, server_time) and known
            and isinstance(roots, list) and roots and p.get('snapshot_key') in roots
            and isinstance(p.get('snapshot_digest'), str) and len(p['snapshot_digest']) == 64),
            'states': {k: fields.get(k, {}).get('state') for k in keys},
            **{k: p.get(k) for k in ('snapshot_key', 'snapshot_digest', 'evidence_roots',
                                    'observed_at', 'available_at', 'valid_until')}}


def jev_proofs(events, session, generation):
    """Durable send reservation + actual matching provider completion/output.

    An abstention proves invocation. A late/stale answer is retained separately
    and never counted as current useful interpretation.
    """
    requests = {e['payload'].get('request_id'): e for e in events if e['kind'] == 'reasoning.request'}
    deliveries = {e['payload'].get('request_id'): e for e in events if e['kind'] == 'reasoning.delivery'}
    result = []
    for output in events:
        p = output['payload']
        if output['kind'] != 'reasoning.output' or p.get('arm') != 'b' or p.get('generation') != generation:
            continue
        request, delivery = requests.get(p.get('request_id')), deliveries.get(p.get('request_id'))
        if request is None or delivery is None:
            continue
        r, d = request['payload'], delivery['payload']
        packet, provenance, judgment = r.get('request'), p.get('provenance', {}), p.get('judgment', {})
        if (not isinstance(packet, dict) or r.get('status') != 'admitted_before_transport'
                or packet.get('model') != JEV_VERSION or packet.get('state', {}).get('schema') != JEV_WIRE
                or r.get('wire_version') != JEV_WIRE or provenance.get('wire_version') != JEV_WIRE
                or r.get('packet_sha256') != sha(packet) or d.get('state') != 'completed'
                or d.get('status') not in {'ready', 'abstain'} or d.get('current') is not True
                or any(x.get('arm') != 'b' or x.get('generation') != generation for x in (r, d))
                or not request['id'] < delivery['id'] < output['id']
                or p.get('mode') != 'live' or p.get('run_id') != 'jevB'
                or judgment.get('provider') != 'JEV' or judgment.get('model_version') != JEV_VERSION
                or provenance.get('response_version') != JEV_VERSION
                or provenance.get('input_digest') != r['packet_sha256']
                or provenance.get('request_id') != p.get('request_id')
                or provenance.get('session_id') != session['id']
                or provenance.get('arm') != 'b' or provenance.get('generation') != generation):
            continue
        observed, available = p.get('source_end_s'), p.get('available_at_s')
        if not all(finite(t) for t in (observed, available, session.get('started_at'), p.get('valid_until'))):
            continue
        evidence = provenance.get('evidence_ids')
        cutoff = provenance.get('observed_at')
        if (not isinstance(evidence, list) or not evidence or r.get('evidence_ids') != evidence
                or not finite(cutoff)):
            continue
        pc_rows = [e for e in events if str(e['id']) in evidence
            and (pc_projection(e, server_time=cutoff) or {}).get('fresh') is True]
        devices = packet.get('state', {}).get('devices', {})
        if (not pc_rows or devices.get('pc_input') != 'under_15s' or devices.get('pc_active') != 'on'):
            continue
        at = session['started_at'] + available
        result.append({'request_id': p['request_id'], 'generation': generation,
            'request_event_id': request['id'], 'delivery_event_id': delivery['id'],
            'output_event_id': output['id'], 'packet_sha256': r['packet_sha256'],
            'model_version': judgment['model_version'], 'provider_status': d['status'],
            'evidence_ids': deepcopy(evidence), 'observed_at': session['started_at'] + observed,
            'available_at': at, 'valid_until': p['valid_until'],
            'fresh_on_arrival': p.get('fresh_on_arrival') is True and at < p['valid_until'],
            'activity': judgment.get('activity'),
            'pc_context': {key: devices[key] for key in ('pc_input', 'pc_active')},
            'pc_original_event_ids': [e['id'] for e in pc_rows],
            'original_packet_evidence_ids': deepcopy(r.get('evidence_ids'))})
    return result


def run_trial(api, plan, retain, *, monotonic=time.monotonic, sleep=time.sleep):
    """Injected HTTP port: api(method, suffix, body=None). Never retries create."""
    summary = {'schema': 'den-existing-whole-stack-trial-1', 'request': plan.body(),
        'trial_ran': False, 'session_id': None, 'cleanup': {'confirmed': False},
        'arm_b_outputs': [], 'pc_facts': [], 'jev': [], 'event_window': {'complete': False}}
    session, after_id, originals, event_digests = None, 0, [], {}
    profile, generation, last_live, last_graph = None, None, None, None
    capture = None
    create_attempted, capture_failed = False, False

    def read(method, suffix, body=None):
        value = api(method, suffix, body)
        retain('response', {'method': method, 'suffix': suffix, 'body': value})
        return value

    def check_session(value):
        if not plan.matches(value) or (session and value.get('id') != session['id']):
            raise ValueError('original_session_binding_mismatch')
        return value

    def capture_time_after_receipt(previous_time):
        # A descriptor GET can outlive either original lease. Sample the same
        # server after receipt; calling live() here would recursively project
        # events and could select a different representative underneath us.
        sample = read('GET', '/sessions/' + session['id'] + '/live')
        current = check_session(sample.get('session'))
        if current.get('generation') != session.get('generation'):
            raise ValueError('media_generation_changed')
        at = sample.get('server_time')
        if (sample.get('session_id') != session['id'] or sample.get('site_id') != session['site_id']
                or not finite(previous_time) or not finite(at) or at < previous_time):
            raise ValueError('original_capture_clock_binding_mismatch')
        return at

    def events(server_time, *, project=True):
        nonlocal after_id, profile, generation, capture_failed, capture
        for _ in range(60):
            page = read('GET', '/sessions/' + session['id'] + '/events?after_id=' + str(after_id) + '&limit=100')
            rows = page.get('items')
            if not isinstance(rows, list) or len(rows) > plan.page_limit:
                raise ValueError('bounded_original_event_page_required')
            cursor = after_id
            for event in rows:
                if (not isinstance(event, dict) or event.get('session_id') != session['id']
                        or type(event.get('id')) is not int or event['id'] <= cursor
                        or not isinstance(event.get('kind'), str) or not isinstance(event.get('payload'), dict)):
                    raise ValueError('ordered_original_session_events_required')
                cursor = event['id']
                fingerprint = sha(event)
                if event['id'] in event_digests and event_digests[event['id']] != fingerprint:
                    raise ValueError('original_event_identity_conflict')
                if len(originals) >= plan.max_events:
                    raise ValueError('original_event_window_exhausted')
                # The whole response is durable before any successful oracle.
                if event['id'] not in event_digests:
                    originals.append(deepcopy(event))
                    event_digests[event['id']] = fingerprint
                p = event['payload']
                if any(k in p and p[k] != v for k, v in {'site_id': 'victoria',
                        'camera_id': 'den', 'session_id': session['id']}.items()):
                    raise ValueError('event_scope_mismatch')
                if event['kind'] == 'experiment.activated':
                    current = p.get('generation')
                    if type(current) is not int or current < 1 or generation not in (None, current):
                        raise ValueError('experiment_generation_changed')
                    generation, profile = current, p.get('profiles', {}).get('b')
                if p.get('generation', generation) != generation:
                    continue
                if not project:
                    continue
                row = positive_projection(event, profile, server_time=server_time)
                if row is not None:
                    summary['arm_b_outputs'].append(row)
                    if row['positive'] and capture is None:
                        found_rgb_descriptor = False
                        for mid in row['media_ids']:
                            descriptor = read('GET', '/sessions/' + session['id'] + '/media/' + mid
                                              + '/descriptor?generation=' + str(session['generation']))
                            if descriptor.get('mime_type') == 'image/jpeg' and descriptor.get('metadata', {}).get('stream') == 'rgb':
                                found_rgb_descriptor = True
                                capture_time = capture_time_after_receipt(server_time)
                                try:
                                    capture = capture_projection(descriptor, row, session, capture_time)
                                except ValueError as error:
                                    if str(error) != 'original_rgb_descriptor_expired':
                                        raise
                                    # Keep the actual expired receipt without
                                    # refreshing it; a later frame may qualify.
                                    summary['expired_capture_candidates'] = summary.get('expired_capture_candidates', 0) + 1
                                break
                        if not found_rgb_descriptor:
                            raise ValueError('original_rgb_descriptor_unavailable')
                row = pc_projection(event, server_time=server_time)
                if row is not None:
                    summary['pc_facts'].append(row)
            if page.get('after_id') != cursor:
                raise ValueError('original_event_cursor_mismatch')
            after_id = cursor
            if len(rows) < plan.page_limit:
                return
        capture_failed = True
        raise ValueError('event_page_budget_exhausted')

    def live(*, project=True):
        nonlocal session, last_live
        last_live = read('GET', '/sessions/' + session['id'] + '/live')
        current = check_session(last_live['session'])
        if current.get('generation') != session.get('generation'):
            raise ValueError('media_generation_changed')
        session = current
        events(last_live.get('server_time'), project=project)
        return current['state']

    def graph():
        nonlocal last_graph
        last_graph = read('GET', '/sessions/' + session['id'] + '/graph')
        if (last_graph.get('session_id') != session['id'] or last_graph.get('site_id') != 'victoria'
                or last_graph.get('session', {}).get('generation') != session.get('generation')):
            raise ValueError('original_graph_binding_mismatch')

    try:
        # Persist exact operation identity/body before any possible create.
        retain('plan', {'request': plan.body(), 'budgets': plan.__dict__})
        caps = read('GET', '/capabilities')
        recipe = next((x for x in caps.get('experiments', []) if x.get('id') == 'placement-best'), {})
        reasoning = caps.get('reasoning', {})
        if (caps.get('site_id') != 'victoria' or caps.get('fixture') is not False
                or caps.get('recording_allowed') is not True or recipe.get('status') != 'ready'
                or caps.get('worker_mode') != 'on_demand_bounded'
                or caps.get('storage', {}).get('max_session_seconds') != 300
                or reasoning.get('available') is not True or reasoning.get('mode') != 'explicit_bounded_session'
                or any(reasoning.get(k) != v for k, v in {'session_seconds': 300,
                    'max_session_calls': 600, 'max_calls_per_minute': 120}.items())):
            raise ValueError('existing_admission_or_resource_budget_changed')
        sessions = read('GET', '/sessions')['items']
        if any(plan.matches(s) for s in sessions):
            raise ValueError('request_id_already_exists_reconcile_without_new_trial')
        if any(s.get('state') in ACTIVE for s in sessions):
            raise ValueError('another_or_indeterminate_session_exists')
        create_attempted = True
        created = api('POST', '/sessions', plan.body())
        # Keep cleanup identity even if durable retention of this reply fails.
        if plan.matches(created):
            session = created
            summary['session_id'] = created['id']
        retain('response', {'method': 'POST', 'suffix': '/sessions', 'body': created})
        check_session(created)
        summary['trial_ran'] = True
        deadline, recording_deadline = monotonic() + plan.preparation_seconds, None
        while monotonic() < deadline:
            state = live()
            if last_graph is None and state == 'recording':
                graph()
            if state == 'recording':
                if recording_deadline is None:
                    recording_deadline = monotonic() + plan.recording_seconds
                    deadline = recording_deadline
            elif state not in {'requested', 'starting'}:
                summary['stop_reason'] = 'session_terminal_or_unavailable'
                break
            # Never stop early for either arm's success/failure.
            sleep(1)
        else:
            summary['stop_reason'] = 'bounded_recording_elapsed' if recording_deadline is not None else 'preparation_deadline'
    except Exception as error:
        summary['error'] = str(error) if type(error) is ValueError else type(error).__name__
        capture_failed = True
    finally:
        if session is None and create_attempted:
            try:
                candidates = api('GET', '/sessions')['items']
                matches = [s for s in candidates if plan.matches(s)]
                if len(matches) == 1:
                    session = matches[0]
                    summary['session_id'] = session['id']
            except Exception as error:
                summary['identity_lookup_error'] = type(error).__name__
        if session is not None and plan.matches(session):
            try:
                # Reserved cleanup is attempted even after original retention fails.
                api('POST', '/sessions/' + session['id'] + '/stop', {'request_id': plan.request_id})
                cleanup_deadline = monotonic() + plan.cleanup_seconds
                while monotonic() < cleanup_deadline:
                    # Cleanup collects originals and terminal receipts even
                    # when a rejected model/media projection ended the trial.
                    state = live(project=False)
                    cleanups = [e['payload'] for e in originals if e['kind'] == 'workers.cleanup']
                    drains = {e['payload'].get('request_id') for e in originals if e['kind'] == 'workers.local_drained'}
                    terminal = [p for p in cleanups if p.get('state') == 'stopped'
                        and (p.get('local_drain') == 'drained' or p.get('request_id') in drains)
                        and set(p.get('workers', {})) == {'a', 'b'}
                        and all(w.get('state') in {'stopped', 'failed'} for w in p['workers'].values())]
                    if state in TERMINAL and terminal:
                        graph()
                        events(last_live.get('server_time'), project=False)
                        summary['cleanup'] = {'confirmed': True, 'session_state': state,
                                              'both_workers_terminal': True, 'local_drain': 'drained'}
                        break
                    sleep(1)
            except Exception as error:
                summary['cleanup_error'] = type(error).__name__
                capture_failed = True
        summary['model_generation'] = generation
        summary['media_generation'] = session.get('generation') if session else None
        summary['representative_capture'] = capture
        summary['jev'] = jev_proofs(originals, session, generation) if session else []
        horizon = (last_graph or {}).get('horizon_event_id')
        summary['event_window'] = {'complete': not capture_failed and summary['cleanup']['confirmed']
                and type(horizon) is int and 0 < horizon <= after_id,
            'first_event_id': originals[0]['id'] if originals else None,
            'last_event_id': after_id, 'count': len(originals), 'event_digests': event_digests,
            'declared_horizon_event_id': horizon,
            'scope': 'original_session_start_through_declared_final_graph_horizon', 'raw_media_fetched': False}
        unresolved = {}
        for event in originals:
            if event['kind'] in {'reasoning.request', 'reasoning.delivery', 'reasoning.lifecycle', 'reasoning.reconciled'}:
                p = event['payload']
                rid = p.get('request_id')
                if rid:
                    unresolved[rid] = p.get('state', 'admitted')
        summary['unsettled_jev_request_ids'] = [rid for rid, state in unresolved.items()
                                               if state in {'admitted', 'dispatching', 'indeterminate'}]
        summary['family_statuses'] = [{'id': s.get('id'), 'status': s.get('status'),
            'provisioned': s.get('provisioned'), 'producing': s.get('producing'), 'reason': s.get('reason')}
            for s in (last_graph or {}).get('sources', [])]
        summary['passed'] = (summary['event_window']['complete']
            and capture is not None and any(r['positive'] for r in summary['arm_b_outputs'])
            and any(r['fresh'] for r in summary['pc_facts']) and bool(summary['jev'])
            and not summary['unsettled_jev_request_ids'])
        summary['useful_current_jev'] = any(r['fresh_on_arrival'] for r in summary['jev'])
        try:
            retain('summary', summary)
        except Exception as error:
            summary.update(passed=False, archive_error=type(error).__name__)
            summary['event_window']['complete'] = False
    return summary


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class LabHTTP:
    def __init__(self, *, token_file, ca_file):
        path = Path(token_file)
        st = path.lstat()
        if (not path.is_absolute() or not path.is_file() or path.is_symlink()
                or st.st_size > 4096 or os.name != 'nt' and (st.st_uid != os.getuid() or st.st_mode & 0o077)):
            raise ValueError('protected_existing_lab_credential_required')
        self.token = path.read_text().strip()
        if not 32 <= len(self.token) <= 512 or any(c.isspace() for c in self.token):
            raise ValueError('invalid_existing_lab_credential')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))

    def __call__(self, method, suffix, body=None):
        data = None if body is None else encoded(body)
        request = urllib.request.Request(ORIGIN + PREFIX + suffix, data=data, method=method,
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json',
                     'Accept': 'application/json', 'Accept-Encoding': 'identity'})
        with self.opener.open(request, timeout=5) as response:
            raw = response.read(2 * 1024**2 + 1)
            if len(raw) > 2 * 1024**2 or response.getheader('Content-Encoding', 'identity') != 'identity':
                raise ValueError('bounded_identity_json_required')
            return json.loads(raw)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute-approved-trial', action='store_true')
    p.add_argument('--request-id', required=True)
    p.add_argument('--token-file')
    p.add_argument('--ca-file')
    p.add_argument('--output-file')
    args = p.parse_args(argv)
    plan = Plan(args.request_id)
    if not args.execute_approved_trial:
        print(json.dumps({'status': 'prepared_only', 'network_requests': 0, 'request': plan.body(),
            'recording_seconds': plan.recording_seconds, 'preparation_seconds': plan.preparation_seconds,
            'cleanup_seconds': plan.cleanup_seconds, 'installed_worker_runtime_seconds': 360,
            'installed_maximum_requests_per_arm': 720, 'success_arm': 'b',
            'requires': ['original_detector_pose', 'fresh_pc_snapshot', 'actual_jev_completion', 'owner_cleanup']}))
        return 0
    if not all((args.token_file, args.ca_file, args.output_file)):
        p.error('execution requires existing token/CA files and a fresh absolute private output file')
    archive = PrivateWindow(args.output_file)
    try:
        result = run_trial(LabHTTP(token_file=args.token_file, ca_file=args.ca_file), plan, archive)
    finally:
        sealed = archive.finalize()
    result['archive'] = sealed
    result['passed'] = result['passed'] and sealed['status'] == 'finalized'
    print(json.dumps(result, sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
