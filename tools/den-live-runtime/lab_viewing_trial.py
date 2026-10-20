"""Existing-Lab, viewing-only bounded session. Inert unless explicitly executed.

This does not qualify imagery, clocks, room coordinates, JEV usefulness, or lighting.
It preserves the original metadata responses and lets the actual Apartment observer
read the same session while it records. A media/graph failure never shortens recording.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

LEGACY_SHA = '6ddf956d0c357a251d5562ddd44e3ae8429b432d6d20877b4965548ee830f7b3'
ACTIVE_JOBS = {'requested', 'queued', 'starting', 'running', 'cancelling', 'indeterminate'}


def incumbent(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != LEGACY_SHA:
        raise ValueError('exact_incumbent_trial_source_required')
    spec = importlib.util.spec_from_file_location('viewer_trial_incumbent', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def admission(caps, sessions, jobs, plan):
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
    if not isinstance(sessions, list) or not isinstance(jobs, list):
        raise ValueError('session_and_job_lists_required')
    if any(s.get('request_id') == plan.request_id for s in sessions):
        raise ValueError('request_id_consumed_reconcile_only')
    if any(s.get('state') in {'requested', 'starting', 'recording', 'stopping', 'indeterminate'} for s in sessions):
        raise ValueError('another_or_indeterminate_session_exists')
    if any(j.get('state') in ACTIVE_JOBS for j in jobs):
        raise ValueError('another_or_indeterminate_job_exists')


def run_viewing(api, plan, retain, notify, *, monotonic=time.monotonic, sleep=time.sleep):
    result = dict(schema='home-den-bounded-viewing-session-1', version=1, request_id=plan.request_id,
        session_id=None, recording_seconds=30, preparation_seconds=10, cleanup_seconds=45,
        cleanup_confirmed=False, recording_completed=False, viewing_only=True,
        physical_permission=False, room_geometry_qualified=False)
    session, attempted, cursor, generation = None, False, 0, None
    events, counts, retained_errors = {}, Counter(), []

    def record(kind, value):
        # Retention failure is visible but cannot suppress the reserved Stop.
        try:
            retain(kind, value)
        except Exception as error:
            retained_errors.append(type(error).__name__)

    def read(method, suffix, body=None):
        value = api(method, suffix, body)
        record('response', dict(method=method, suffix=suffix, body=value))
        return value

    def bound(value):
        if (not plan.matches(value) or not isinstance(value.get('id'), str)
                or not value['id'] or (session and value.get('id') != session['id'])
                or (generation is not None and value.get('generation') != generation)):
            raise ValueError('owned_session_binding_mismatch')
        return value

    def poll():
        nonlocal cursor, session
        live = read('GET', '/sessions/' + session['id'] + '/live')
        session = bound(live.get('session', {}))
        # One bounded page per poll; do not let event history starve live or Stop.
        page = read('GET', '/sessions/' + session['id'] + '/events?after_id=' + str(cursor) + '&limit=100')
        rows = page.get('items')
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError('bounded_event_page_required')
        for event in rows:
            if (event.get('session_id') != session['id'] or type(event.get('id')) is not int
                    or event['id'] <= cursor or not isinstance(event.get('payload'), dict)
                    or not isinstance(event.get('kind'), str)):
                raise ValueError('ordered_owned_events_required')
            if len(events) >= 6000:
                raise ValueError('event_limit_reached')
            payload = event['payload']
            if any(k in payload and payload[k] != v for k, v in
                   {'site_id': 'victoria', 'camera_id': 'den', 'session_id': session['id']}.items()):
                raise ValueError('event_scope_mismatch')
            events[event['id']] = event
            counts[event['kind']] += 1
            cursor = event['id']
        if page.get('after_id') != cursor:
            raise ValueError('event_cursor_mismatch')
        return session['state']

    def cleanup_ready():
        drains = {e['payload'].get('request_id') for e in events.values() if e['kind'] == 'workers.local_drained'}
        return any(p.get('state') == 'stopped'
            and (p.get('local_drain') == 'drained' or p.get('request_id') in drains)
            and set(p.get('workers', {})) == {'a', 'b'}
            and all(w.get('state') in {'stopped', 'failed'} for w in p['workers'].values())
            for p in [e['payload'] for e in events.values() if e['kind'] == 'workers.cleanup'])

    try:
        record('plan', dict(request=plan.body(), recording_seconds=30, preparation_seconds=10, cleanup_seconds=45))
        caps = read('GET', '/capabilities')
        sessions = read('GET', '/sessions').get('items')
        jobs = read('GET', '/jobs').get('items')
        admission(caps, sessions, jobs, plan)
        if retained_errors:
            raise ValueError('private_retention_unavailable_before_create')
        attempted = True
        created = api('POST', '/sessions', plan.body())
        # Retain identity before retention/notification can fail.
        if plan.matches(created):
            session = created
            result['session_id'] = created.get('id')
        record('response', dict(method='POST', suffix='/sessions', body=created))
        session = bound(created)
        generation = session.get('generation')
        if type(generation) is not int or generation < 0:
            raise ValueError('session_media_generation_required')
        notify(dict(status='created', session_id=session['id'], request_id=plan.request_id, generation=generation))
        preparation_deadline, recording_deadline = monotonic() + 10, None
        while monotonic() < (recording_deadline if recording_deadline is not None else preparation_deadline):
            try:
                state = poll()
            except Exception as error:
                record('poll_unavailable', dict(error=str(error) if isinstance(error, ValueError) else type(error).__name__))
                # Once recording starts, image/metadata failures do not end its 30s window.
                state = session.get('state')
            if state == 'recording':
                if recording_deadline is None:
                    recording_deadline = monotonic() + 30
                    notify(dict(status='recording', session_id=session['id'], request_id=plan.request_id,
                        generation=generation, recording_seconds=30, endpoint='/api/perception-lab/v1/sessions/' + session['id'] + '/live'))
            elif state not in {'requested', 'starting'}:
                result['source_stopped_early'] = state
                break
            sleep(1)
        else:
            result['recording_completed'] = recording_deadline is not None
            result['stop_reason'] = 'recording_budget_elapsed' if recording_deadline is not None else 'preparation_budget_elapsed'
    except Exception as error:
        result['error'] = str(error) if isinstance(error, ValueError) else type(error).__name__
    finally:
        if session is None and attempted:
            try:
                matches = [s for s in api('GET', '/sessions')['items'] if plan.matches(s)]
                if len(matches) == 1:
                    session = matches[0]
                    generation = session.get('generation')
                    result['session_id'] = session['id']
            except Exception as error:
                result['reconcile_error'] = type(error).__name__
        if session is not None and plan.matches(session):
            try:
                # No second create, even when the original acknowledgement was lost.
                read('POST', '/sessions/' + session['id'] + '/stop', {'request_id': plan.request_id})
                deadline = monotonic() + 45
                while monotonic() < deadline:
                    try:
                        state = poll()
                        if state in {'stopped', 'failed', 'interrupted'} and cleanup_ready():
                            result.update(cleanup_confirmed=True, final_session_state=state)
                            break
                    except Exception as error:
                        record('cleanup_poll_unavailable', dict(error=type(error).__name__))
                    sleep(1)
            except Exception as error:
                result['cleanup_error'] = type(error).__name__
        result.update(event_counts=dict(counts), event_count=len(events), retention_errors=retained_errors,
            outcome='bounded_viewing_complete' if result['recording_completed'] and result['cleanup_confirmed'] and not retained_errors else 'incomplete')
        record('summary', result)
        notify(dict(status='finished', **result))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--incumbent-source', type=Path, default=Path(__file__).with_name('lab_whole_stack_trial.py'))
    parser.add_argument('--request-id', required=True)
    parser.add_argument('--execute-reviewed-viewer', action='store_true')
    parser.add_argument('--owner-present-and-fresh-admission-reviewed', action='store_true')
    parser.add_argument('--token-file', type=Path)
    parser.add_argument('--ca-file', type=Path)
    parser.add_argument('--private-directory', type=Path)
    args = parser.parse_args(argv)
    whole = incumbent(args.incumbent_source)
    plan = whole.Plan(args.request_id)
    if not args.execute_reviewed_viewer:
        print(json.dumps(dict(status='prepared_only', network_requests=0, request=plan.body(), recording_seconds=30,
            preparation_seconds=10, cleanup_seconds=45, physical_permission=False)))
        return 0
    if not (args.owner_present_and_fresh_admission_reviewed and args.token_file and args.ca_file and args.private_directory
            and args.private_directory.is_absolute() and args.private_directory.parent.is_dir()):
        parser.error('reviewed fresh paired-host admission, protected token/CA and fresh absolute private directory required')
    # Fresh directory is the once-only operation reservation, before any API request.
    args.private_directory.mkdir(mode=0o700)
    archive = whole.PrivateWindow(args.private_directory / 'originals.jsonl', max_bytes=32 * 1024**2)
    try:
        result = run_viewing(whole.LabHTTP(token_file=args.token_file, ca_file=args.ca_file), plan, archive,
            lambda value: print(json.dumps(value, sort_keys=True), flush=True))
    finally:
        sealed = archive.finalize()
    result['archive'] = sealed
    path = args.private_directory / 'result.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(whole.encoded(result) + b'\n'); output.flush(); os.fsync(output.fileno())
    whole.sync_parent(args.private_directory)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result['outcome'] == 'bounded_viewing_complete' and sealed['status'] == 'finalized' else 1


if __name__ == '__main__':
    raise SystemExit(main())
