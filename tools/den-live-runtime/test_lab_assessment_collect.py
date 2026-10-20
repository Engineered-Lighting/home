"""Actual Lab factory and authenticated ASGI read routes; fake external I/O.

Only Windows directory-fsync is stubbed. No model/camera/host/native processes.
A prepared-only Python subprocess checks the actual owner module entry.
The coordinator executes these tests after source freeze.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import httpx
ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT / 'stack/services/perception-lab', ROOT / 'stack/services/home-context'):
    if str(folder) not in sys.path: sys.path.insert(0, str(folder))
from app import create_app, PREFIX
from test_lab_assessment_candidates import CandidateFactoryFixture, existing_candidate_inputs
from lab_assessment_collect import FixedLabReadClient, collect_existing, read_results, main, encoded, _utc_now
from lab_assessment_candidates import EXISTING_MODE

TOKEN = 'authored-candidate-read-only-' + 'x' * 32


class ActualReadAPI:
    def __init__(self, app, *, token=TOKEN):
        self.app, self.token, self.calls = app, token, []

    def request(self, method, suffix):
        self.calls.append((method, suffix))
        async def perform():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                    base_url='https://authored-lab.invalid') as client:
                return await client.request(method, PREFIX + suffix,
                    headers={'Authorization': 'Bearer ' + self.token})
        response = asyncio.run(perform())
        if response.status_code != 200:
            raise ValueError('authored_lab_read_refused')
        return response

    def __call__(self, method, suffix, body=None):
        if method != 'GET' or body is not None: raise AssertionError('collector attempted lifecycle mutation')
        return self.request(method, suffix).json()

    def read_media(self, row):
        return self.request('GET', f'/sessions/{row["session_id"]}/media/{row["id"]}?generation={row["generation"]}').content


class LabAssessmentCollectionTests(unittest.TestCase):
    def setUp(self):
        self.f = CandidateFactoryFixture(self)
        f = self.f
        settings = replace(f.store.settings, token_grants=({'subject': 'authored-candidate-reader',
            'site_id': 'victoria', 'token_sha256': hashlib.sha256(TOKEN.encode()).hexdigest(),
            'capabilities': ['read']},))
        self.app = create_app(settings=settings, runtime=f.runtime, store=f.store)
        self.api = ActualReadAPI(self.app)
        self.output = Path(f.directory.name) / 'assessment.jsonl'
        if sys.platform == 'win32':
            sync = patch('lab_whole_stack_trial.sync_parent')
            sync.start(); self.addCleanup(sync.stop)

    def run_collection(self, **kwargs):
        return collect_existing(self.api, session_id=self.f.session['id'], output_file=self.output,
            clock=lambda: self.f.now, max_attempts=kwargs.pop('max_attempts', 1), seconds=5, **kwargs)

    def records(self):
        return [json.loads(line) for line in self.output.read_bytes().splitlines()]

    def test_actual_factory_public_read_routes_collect_unknown_bound_candidates_without_new_owners(self):
        f = self.f
        store_instance = f.store.instance_id
        runner = f.runtime.runners[f.session['id']]
        with patch('store.Store.__init__', side_effect=AssertionError('opened second Store')):
            result = self.run_collection()
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(result['candidate_attempts'], 1)
        self.assertEqual(f.store.instance_id, store_instance)
        self.assertIs(f.runtime.runners[f.session['id']], runner)
        self.assertEqual(f.store.get_session(f.session['id'])['state'], 'recording')
        self.assertTrue(all(method == 'GET' for method, _ in self.api.calls))
        self.assertTrue(any('/media/' in route and '/descriptor?' in route for _, route in self.api.calls))
        self.assertTrue(any('/media/' in route and '/descriptor?' not in route for _, route in self.api.calls))
        report = read_results(self.output)
        self.assertEqual(report['private_media_records'], 3)
        self.assertIsNone(report['qualified_error_bound_m'])
        self.assertFalse(report['ordinary_lighting_replay'])
        attempt = next(r['value']['candidate'] for r in self.records() if r['kind'] == 'attempt')
        self.assertEqual(attempt['original_event'], f.row)
        self.assertIsNone(attempt['instances'][0]['joints'][0]['error_bound_m'])

    def test_empty_and_failed_raw_outputs_remain_in_denominator(self):
        f = self.f
        empty = deepcopy(f.row['payload'])
        empty['result'].update(boxes=[], objects=[], pose_instances=[])
        f.store.append_event(f.session['id'], 'perception.output', empty)
        failed = deepcopy(empty); failed['status'] = 'failed'
        f.store.append_event(f.session['id'], 'perception.output', failed)
        result = self.run_collection(max_attempts=3)
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['attempts'], 3)
        self.assertEqual(result['candidate_attempts'], 1)
        self.assertEqual(result['missing_prediction_attempts'], 2)
        rows = self.records()
        self.assertEqual(sum(r['kind'] == 'original_attempt' for r in rows), 3)
        self.assertEqual(sum(r['kind'] == 'attempt' for r in rows), 3)

    def test_missing_pair_still_finishes_with_retained_original_and_refusal(self):
        api = self.api
        class MissingPair:
            def __call__(_, method, suffix, body=None):
                value = api(method, suffix, body)
                if '/media?cursor=' in suffix:
                    value.update(items=[], next_cursor=None)
                return value
            read_media = api.read_media
        self.api = MissingPair()
        result = self.run_collection()
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['missing_prediction_attempts'], 1)
        attempt = next(r['value']['candidate'] for r in self.records() if r['kind'] == 'attempt')
        self.assertEqual(attempt['reason'], 'original_pair_unavailable')
        self.assertEqual(attempt['original_event'], self.f.row)

    def test_stale_attempt_is_retained_with_original_expiry_and_no_freshness_extension(self):
        self.f.now = self.f.row['payload']['valid_until'] + 1
        result = self.run_collection()
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['stale_attempts'], 1)
        attempt = next(r['value']['candidate'] for r in self.records() if r['kind'] == 'attempt')
        self.assertEqual(attempt['source_times']['valid_until'], self.f.row['payload']['valid_until'])
        self.assertFalse(attempt['source_fresh_at_collection'])

    def test_raw_media_change_and_truncation_fail_read_results(self):
        self.assertTrue(self.run_collection()['complete'])
        original = self.output.read_bytes()
        rows = self.records()
        media = next(r['value'] for r in rows if r['kind'] == 'media')
        media['data_b64'] = 'Y2hhbmdlZA=='
        self.output.write_bytes(b''.join(encoded(r) + b'\n' for r in rows))
        with self.assertRaisesRegex(ValueError, 'media_changed'): read_results(self.output)
        self.output.write_bytes(original[:-3])
        with self.assertRaisesRegex(ValueError, 'truncated'): read_results(self.output)

    def test_model_generation_switch_after_media_read_refuses_selection_and_stops_collector_only(self):
        api = self.api
        class Switch:
            changed = False
            def __call__(_, method, suffix, body=None):
                result = api(method, suffix, body)
                if suffix.endswith('/live') and _.changed:
                    result['experiment_generation'] += 1
                return result
            def read_media(_, row):
                raw = api.read_media(row)
                if row['mime_type'] == 'application/x-npz': _.changed = True
                return raw
        self.api = Switch()
        result = self.run_collection()
        self.assertFalse(result['complete'])
        self.assertEqual(result['stop_reason'], 'collection_model_generation_changed')
        self.assertEqual(result['attempts'], 1)
        attempt = next(r['value']['candidate'] for r in self.records() if r['kind'] == 'attempt')
        self.assertEqual(attempt['status'], 'refused')
        self.assertEqual(self.f.store.get_session(self.f.session['id'])['state'], 'recording')

    def test_credential_failure_and_owner_interrupt_never_mutate_lab_lifecycle(self):
        self.api = ActualReadAPI(self.app, token='wrong-' + 'z' * 32)
        result = self.run_collection()
        self.assertFalse(result['complete'])
        self.assertEqual(result['attempts'], 0)
        self.assertEqual(self.f.store.get_session(self.f.session['id'])['state'], 'recording')

    def test_prepare_entry_is_inert_and_result_reader_uses_no_api(self):
        with patch('socket.socket', side_effect=AssertionError('prepared command attempted network')), \
             patch('store.Store.__init__', side_effect=AssertionError('prepared command opened Store')), \
             patch('sys.stdout', new_callable=io.StringIO) as stdout:
            self.assertEqual(main([]), 0)
        self.assertEqual(json.loads(stdout.getvalue())['network_requests'], 0)
        self.assertTrue(self.run_collection()['complete'])
        with patch('lab_assessment_collect.FixedLabReadClient', side_effect=AssertionError('results attempted network')), \
             patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(main(['--read-results', str(self.output)]), 0)

    def test_actual_owner_module_entry_needs_no_pythonpath_or_object_assembly(self):
        result = subprocess.run([sys.executable, '-E', '-B', '-m',
            'tools.den-live-runtime.lab_assessment_collect'], cwd=ROOT,
            capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        prepared = json.loads(result.stdout)
        self.assertEqual(prepared['status'], 'prepared_only')
        self.assertEqual(prepared['network_requests'], 0)
        self.assertIsNone(prepared['operational_geometry'])

    def test_clean_prepared_entry_does_not_import_home_authority_graph(self):
        script = r'''
import builtins, importlib, sys
blocked = {'live_capture', 'lab_live_acquisition', 'journal', 'lab_live_runtime',
           'lab_live_desk_anchor', 'lab_live_desk_admission', 'lab_stereo_desk_capability',
           'fixture_canonical_authority'}
original_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in blocked:
        raise AssertionError('nominal entry imported Home authority: ' + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded
entry = importlib.import_module('tools.den-live-runtime.lab_assessment_collect')
assert entry.main([]) == 0
assert not blocked.intersection(sys.modules)
'''
        result = subprocess.run([sys.executable, '-E', '-B', '-c', script], cwd=ROOT,
            capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        prepared = json.loads(result.stdout)
        self.assertEqual(prepared['status'], 'prepared_only')
        self.assertEqual(prepared['network_requests'], 0)
        self.assertIsNone(prepared['operational_geometry'])

    def test_private_default_clock_rounds_original_nanoseconds_once(self):
        # This value is just above the half-ULP second boundary but below the
        # half-ULP integer-to-float boundary: dividing by float 1e9 loses it.
        ns = 1800000000000000120
        expected = float(Decimal(ns) / Decimal(1_000_000_000))
        self.assertNotEqual(ns / 1e9, expected)
        with patch('lab_assessment_collect.time.time_ns', side_effect=[ns, ns + 256]), \
             patch('lab_assessment_collect.time.time', side_effect=AssertionError('double-rounded clock used')):
            first, later = _utc_now(), _utc_now()
        self.assertEqual(first, expected)
        self.assertEqual(later, float(Decimal(ns + 256) / Decimal(1_000_000_000)))
        self.assertGreaterEqual(later, first)
        self.assertIs(collect_existing.__kwdefaults__['clock'], _utc_now)

    def test_fixed_binary_client_rejects_route_injection_hash_and_generation_before_transport(self):
        client = FixedLabReadClient.__new__(FixedLabReadClient)
        client.token = 'authored-token-not-a-real-credential'
        class NoTransport:
            def open(self, *_, **__): raise AssertionError('invalid route reached transport')
        client.opener = NoTransport()
        for route in ('/sessions/x/stop', '/sessions/x/events?after_id=0&limit=128&upstream=evil',
                      '/sessions/x/media/../descriptor?generation=1', 'https://evil.invalid'):
            with self.subTest(route=route), self.assertRaises(ValueError): client('GET', route)
        with self.assertRaises(ValueError):
            client.read_media({'id': '../escape', 'session_id': 's', 'generation': 1, 'size': 1})
        client.bind_deadline(lambda: 0)
        with self.assertRaisesRegex(ValueError, 'runtime_exhausted'):
            client('GET', '/sessions/s/live')

    def test_results_link_original_attempt_and_exact_media_descriptor_and_recompute_counts(self):
        self.assertTrue(self.run_collection()['complete'])
        original = deepcopy(self.records())
        for attack in ('event', 'media', 'counts'):
            with self.subTest(attack=attack):
                rows = deepcopy(original)
                attempt = next(r['value']['candidate'] for r in rows if r['kind'] == 'attempt')
                if attack == 'event':
                    attempt['original_event']['payload']['request_id'] = 'changed-original'
                    attempt['original_event_sha256'] = hashlib.sha256(encoded(attempt['original_event'])).hexdigest()
                if attack == 'media':
                    attempt['media_records']['rgb']['metadata']['width'] += 1
                if attack == 'counts': rows[-1]['value']['missing_prediction_attempts'] += 1
                self.output.write_bytes(b''.join(encoded(r) + b'\n' for r in rows))
                with self.assertRaises(ValueError): read_results(self.output)

    def test_page_receipt_is_first_clock_and_completion_crossing_expiry_remains_diagnostic(self):
        first = self.f.now
        api = self.api
        fixture = self.f
        class Delayed:
            def __call__(_, method, suffix, body=None): return api(method, suffix, body)
            def read_media(_, row):
                raw = api.read_media(row)
                if row['mime_type'] == 'application/x-npz': fixture.now = fixture.row['payload']['valid_until'] + 1
                return raw
        self.api = Delayed()
        result = self.run_collection()
        self.assertTrue(result['complete'], result)
        record = next(r['value'] for r in self.records() if r['kind'] == 'attempt')
        self.assertEqual(record['candidate']['first_consumer_received_at'], first)
        self.assertTrue(record['candidate']['source_fresh_at_collection'])
        self.assertFalse(record['original_support_fresh_at_completion'])
        self.assertGreater(record['calculation_completed_at'], record['candidate']['source_times']['valid_until'])
        self.assertEqual(record['collector_clock_qualification'], 'unassessed')

    def test_stop_during_pair_read_prevents_all_later_gets_and_retains_attempt(self):
        stop, api = threading.Event(), self.api
        class Stopped:
            def __call__(_, method, suffix, body=None): return api(method, suffix, body)
            def read_media(_, row):
                raw = api.read_media(row); stop.set(); return raw
        self.api = Stopped()
        result = self.run_collection(stop=stop)
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(result['stop_reason'], 'owner_stopped_collector')
        binary_reads = [r for _, r in api.calls if '/media/' in r and '/descriptor?' not in r]
        self.assertEqual(len(binary_reads), 1)
        self.assertEqual(self.f.store.get_session(self.f.session['id'])['state'], 'recording')
        self.assertEqual(sum(r['kind'] == 'attempt' for r in self.records()), 1)

    def test_dribbling_chunk_reader_checks_remaining_absolute_budget_between_reads(self):
        client = FixedLabReadClient.__new__(FixedLabReadClient)
        remaining = [1.]
        client.bind_deadline(lambda: remaining[0])
        class Dribble:
            calls = 0
            def read1(self, count):
                self.calls += 1
                remaining[0] = -1
                return b'a'
        response = Dribble()
        with self.assertRaisesRegex(ValueError, 'runtime_exhausted'):
            client._read_bounded(response, 10)
        self.assertEqual(response.calls, 1)

    def test_missing_managed_readiness_still_refuses_instead_of_inventing_it(self):
        api = self.api
        class MissingReady:
            def __call__(_, method, suffix, body=None):
                result = api(method, suffix, body)
                if '/events?' in suffix:
                    result['items'] = [e for e in result['items'] if e['kind'] != 'workers.ready']
                return result
            def read_media(_, row): return api.read_media(row)
        self.api = MissingReady()
        result = self.run_collection()
        self.assertFalse(result['complete'])
        self.assertEqual(result['stop_reason'], 'existing_worker_ready_originals_required')
        self.assertEqual(result['attempts'], 0)


class ExistingApiCollectionTests(unittest.TestCase):
    records = LabAssessmentCollectionTests.records

    def setUp(self):
        LabAssessmentCollectionTests.setUp(self)
        self.tap, self.start, self.terminal, self.media = existing_candidate_inputs(self.f)

    def run_collection(self, **kwargs):
        return collect_existing(self.api, session_id=self.f.session['id'], output_file=self.output,
            clock=lambda: self.f.now, max_attempts=kwargs.pop('max_attempts', 1),
            seconds=kwargs.pop('seconds', 5), collection_mode=EXISTING_MODE, **kwargs)

    def test_actual_factory_asgi_late_association_collects_private_successor(self):
        with patch('store.Store.__init__', side_effect=AssertionError('opened another Store')):
            result = self.run_collection()
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(result['candidate_attempts'], 1)
        self.assertEqual(result['missing_terminal_attempts'], 0)
        self.assertEqual(read_results(self.output)['private_media_records'], 4)
        row = next(r['value']['candidate'] for r in self.records() if r['kind'] == 'attempt')
        self.assertGreater(row['depth_clock']['association_created_at'], row['source_times']['available_at'])
        self.assertEqual(row['capture_terminal'], self.terminal)
        self.assertEqual(self.f.store.get_session(self.f.session['id'])['state'], 'recording')
        self.assertTrue(all(method == 'GET' for method, _ in self.api.calls))

    def test_delayed_terminal_preserves_first_page_receipt_and_waits_before_attempt(self):
        api, f = self.api, self.f
        first = f.now
        class Delayed:
            reads = 0
            def __call__(self, method, suffix, body=None):
                if '/events?' in suffix:
                    self.reads += 1
                    if self.reads > 1: f.now = first + .5
                value = api(method, suffix, body)
                if '/events?' in suffix and self.reads == 1:
                    value['items'] = [e for e in value['items'] if e['kind'] not in
                        ('assessment.capture.terminal', 'assessment.window.closed')]
                    value['after_id'] = max(e['id'] for e in value['items'])
                return value
            read_media = api.read_media
        delayed = Delayed(); self.api = delayed
        result = self.run_collection()
        self.assertTrue(result['complete'], result)
        self.assertGreaterEqual(delayed.reads, 2)
        rows = self.records()
        self.assertEqual(sum(r['kind'] == 'attempt' for r in rows), 1)
        candidate = next(r['value']['candidate'] for r in rows if r['kind'] == 'attempt')
        self.assertEqual(candidate['first_consumer_received_at'], first)
        self.assertIsNotNone(candidate['capture_terminal'])

    def test_missing_terminal_at_stop_is_incomplete_and_keeps_prediction_denominator(self):
        api = self.api
        class Missing:
            def __call__(_, method, suffix, body=None):
                value = api(method, suffix, body)
                if '/events?' in suffix:
                    value['items'] = [e for e in value['items'] if e['kind'] not in
                        ('assessment.capture.terminal', 'assessment.window.closed')]
                return value
            read_media = api.read_media
        self.api = Missing()
        class StopAfterPoll:
            stopped = False
            def is_set(self): return self.stopped
            def wait(self, _): self.stopped = True
        result = self.run_collection(stop=StopAfterPoll())
        self.assertFalse(result['complete'], result)
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(result['missing_prediction_attempts'], 1)
        self.assertEqual(result['missing_terminal_attempts'], 1)
        report = read_results(self.output)
        self.assertFalse(report['complete'])
        self.assertEqual(report['missing_terminal_attempts'], 1)
        self.assertEqual(next(r['value']['candidate'] for r in self.records() if r['kind'] == 'attempt')
                         ['instances'][0]['joints'][0]['pixel'], [320., 150.])

    def test_no_start_is_unresolved_not_silently_outside_or_complete(self):
        api = self.api
        class NoStart:
            def __call__(_, method, suffix, body=None):
                value = api(method, suffix, body)
                if '/events?' in suffix:
                    value['items'] = [e for e in value['items'] if not e['kind'].startswith('assessment.')]
                return value
            read_media = api.read_media
        self.api = NoStart()
        class StopAfterPoll:
            stopped = False
            def is_set(self): return self.stopped
            def wait(self, _): self.stopped = True
        result = self.run_collection(stop=StopAfterPoll())
        self.assertFalse(result['complete'])
        self.assertEqual(result['outside_window_outputs'], 0)
        self.assertEqual(result['unresolved_window_outputs'], 1)
        self.assertEqual(read_results(self.output)['unresolved_window_outputs'], 1)

    def test_same_frame_empty_and_failed_outputs_remain_measured_misses(self):
        f = self.f
        for failed in (False, True):
            value = deepcopy(f.row['payload'])
            value['result'].update(boxes=[], objects=[], pose_instances=[])
            if failed: value['status'] = 'failed'
            f.store.append_event(f.session['id'], 'perception.output', value)
        result = self.run_collection(max_attempts=3)
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['attempts'], 3)
        self.assertEqual(result['candidate_attempts'], 1)
        self.assertEqual(result['missing_prediction_attempts'], 2)
        self.assertEqual(read_results(self.output)['missing_prediction_attempts'], 2)

    def test_changed_retained_terminal_or_media_and_summary_fail_results_validation(self):
        self.assertTrue(self.run_collection()['complete'])
        saved = self.records()
        for attack in ('terminal', 'media', 'counts'):
            with self.subTest(attack=attack):
                rows = deepcopy(saved)
                candidate = next(r['value']['candidate'] for r in rows if r['kind'] == 'attempt')
                if attack == 'terminal': candidate['capture_terminal']['payload']['reason'] = 'changed'
                if attack == 'media': candidate['media_records']['calibration_original']['metadata']['ordinal'] += 1
                if attack == 'counts': rows[-1]['value']['missing_terminal_attempts'] += 1
                self.output.write_bytes(b''.join(encoded(r) + b'\n' for r in rows))
                with self.assertRaises(ValueError): read_results(self.output)

    def test_clean_new_mode_entry_does_not_need_modern_paired_client(self):
        script = r'''
import builtins, importlib, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'paired_preview_client', 'live_acquisition_owner', 'live_capture', 'journal'}:
        raise AssertionError('incidental operational/modern camera dependency: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
entry = importlib.import_module('tools.den-live-runtime.lab_assessment_collect')
assert entry.main(['--collection-mode', 'existing-api-assessment-1']) == 0
'''
        result = subprocess.run([sys.executable, '-E', '-B', '-c', script], cwd=ROOT,
            capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['collection_mode'], EXISTING_MODE)

    def test_closed_source_window_proves_later_original_outside_without_losing_denominator(self):
        import live_capture_fixtures as capture
        self.f.now = capture.NOW + .7
        self.f.append_pair(sequence=8, observed_at=capture.NOW + .5)
        result = self.run_collection(max_attempts=2)
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(result['outside_window_outputs'], 1)
        self.assertEqual(result['unresolved_window_outputs'], 0)
        self.assertEqual(read_results(self.output)['outside_window_outputs'], 1)

    def test_stop_during_media_read_retains_current_attempt_and_starts_no_later_get(self):
        stop, api = threading.Event(), self.api
        class Stopped:
            def __call__(_, method, suffix, body=None): return api(method, suffix, body)
            def read_media(_, row):
                raw = api.read_media(row); stop.set(); return raw
        self.api = Stopped()
        result = self.run_collection(stop=stop)
        self.assertFalse(result['complete'], result)
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(sum('/media/' in route and '/descriptor?' not in route for _, route in api.calls), 1)
        self.assertEqual(self.f.store.get_session(self.f.session['id'])['state'], 'recording')
        self.assertEqual(read_results(self.output)['attempts'], 1)

    def test_durable_exact_terminal_does_not_hide_source_incomplete_window(self):
        api = self.api
        class LostAckWindow:
            def __call__(_, method, suffix, body=None):
                value = api(method, suffix, body)
                if '/events?' in suffix:
                    # The terminal's bytes may already be durable even when
                    # its source ACK was lost. Keep that independent failure.
                    for event in value['items']:
                        if event['kind'] == 'assessment.window.closed':
                            event['payload']['reason'] = 'incomplete'
                return value
            read_media = api.read_media
        self.api = LostAckWindow()
        result = self.run_collection()
        self.assertFalse(result['complete'], result)
        self.assertEqual(result['candidate_attempts'], 1)
        self.assertFalse(read_results(self.output)['complete'])
        rows = self.records(); rows[-1]['value']['complete'] = True
        self.output.write_bytes(b''.join(encoded(r) + b'\n' for r in rows))
        with self.assertRaisesRegex(ValueError, 'incomplete_source_window'):
            read_results(self.output)

    def test_source_stop_window_does_not_claim_complete_after_retained_terminal(self):
        api = self.api
        class StoppedWindow:
            def __call__(_, method, suffix, body=None):
                value = api(method, suffix, body)
                if '/events?' in suffix:
                    for event in value['items']:
                        if event['kind'] == 'assessment.window.closed': event['payload']['reason'] = 'stop'
                return value
            read_media = api.read_media
        self.api = StoppedWindow()
        self.assertFalse(self.run_collection()['complete'])
        self.assertFalse(read_results(self.output)['complete'])


class ExistingApiFactoryCollectionTests(unittest.TestCase):
    def test_actual_two_hz_capture_one_hz_arm_retains_planned_sampling_skip(self):
        from test_existing_api_assessment_factory import ExistingApiAssessmentFactoryTests
        from live_detector_app import build_runtime
        import live_capture_fixtures as capture
        f = ExistingApiAssessmentFactoryTests('test_real_factory_keeps_chrony_and_model_offer_before_private_retention')
        self.addCleanup(f.doCleanups); f.setUp()
        # Commission the existing placement-best recipe before the actual
        # factory starts. No runtime sampling value is patched or changed.
        f.store.transition(f.session['id'], 'stopping'); f.store.transition(f.session['id'], 'stopped')
        f.session, _ = f.store.create_session('den', 'placement-best', 'Authored 2Hz capture / 1Hz arm', 'cadence-factory')
        f.store.transition(f.session['id'], 'starting'); f.store.transition(f.session['id'], 'recording')
        f.session = f.store.get_session(f.session['id'])
        f.config['existing_api_assessment']['max_captures'] = 3
        def placement(*args, **kwargs):
            return build_runtime(*args, **kwargs, experiment_id='placement-best')
        with patch('test_existing_api_assessment_factory.build_runtime', side_effect=placement): runtime = f.build()
        self.assertEqual(runtime.runners[f.session['id']].sampling_hz, {'a': 1, 'b': 1})
        f.store.append_event(f.session['id'], 'workers.ready', {'receipts': {'b': {
            'state': 'ready', 'session_id': f.session['id'], 'engine_epoch': f.profiles['b']['engine_epoch'],
            'profile': deepcopy(f.profiles['b'])}}, 'fixture_evidence': 'authored_external_lifecycle_double'})
        for index in range(3):
            f.now = capture.NOW + index * .5 + .2
            f.jpeg, f.header, f.bundle, f.calibration = capture.inputs(sequence=7 + index,
                observed_ns=int((capture.NOW + index * .5) * 1_000_000_000),
                preview_sha256=hashlib.sha256(bytes(540 * 640)).hexdigest())
            f.raw_calibration = json.dumps(f.calibration, indent=1).encode()
            f.consume(runtime)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and any(runtime.runners[f.session['id']].snapshot()['active'].values()):
                time.sleep(.005)
        settings = replace(f.store.settings, token_grants=({'subject': 'authored-candidate-reader',
            'site_id': 'victoria', 'token_sha256': hashlib.sha256(TOKEN.encode()).hexdigest(),
            'capabilities': ['read']},))
        api = ActualReadAPI(create_app(settings=settings, runtime=runtime, store=f.store))
        output = Path(f.directory.name) / 'cadence-assessment.jsonl'
        with patch('lab_whole_stack_trial.sync_parent') if sys.platform == 'win32' else _nullcontext():
            result = collect_existing(api, session_id=f.session['id'], output_file=output,
                clock=lambda: f.now, seconds=5, max_attempts=3, collection_mode=EXISTING_MODE)
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['attempts'], 2)
        self.assertEqual(result['missing_prediction_attempts'], 2)  # Both model doubles report no pose.
        self.assertEqual(result['planned_sampling_skipped_captures'], 1)
        self.assertEqual(result['capture_starts_without_model'], 0)
        report = read_results(output)
        self.assertEqual(report['planned_sampling_skipped_captures'], 1)
        rows = [json.loads(line) for line in output.read_bytes().splitlines()]
        dropped = next(r for r in rows if r['kind'] == 'model_schedule_original'
            and r['value']['event']['kind'] == 'perception.dropped')
        self.assertEqual(dropped['value']['event']['payload']['reason'], 'source_sampling_cadence')
        # Missing either exact schedule original cannot turn an unexplained
        # absent output into a normal skip in the aggregate report.
        rows.remove(dropped)
        output.write_bytes(b''.join(encoded(r) + b'\n' for r in rows))
        with self.assertRaises(ValueError): read_results(output)

    def test_config3_factory_tap_store_authenticated_collector_and_results_share_originals(self):
        from test_existing_api_assessment_factory import ExistingApiAssessmentFactoryTests
        # Reuse the source owner's complete existing-API factory fixture. This
        # path has no paired-preview media/client and no model pose prediction;
        # its retained no-pose outcome remains a measured denominator.
        f = ExistingApiAssessmentFactoryTests('test_real_factory_keeps_chrony_and_model_offer_before_private_retention')
        self.addCleanup(f.doCleanups); f.setUp()
        f.config['existing_api_assessment']['max_captures'] = 1
        runtime = f.build()
        f.store.append_event(f.session['id'], 'workers.ready', {'receipts': {'b': {
            'state': 'ready', 'session_id': f.session['id'], 'engine_epoch': f.profiles['b']['engine_epoch'],
            'profile': deepcopy(f.profiles['b'])}}, 'fixture_evidence': 'authored_external_lifecycle_double'})
        f.consume(runtime)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            outputs = [e for e in f.store.list_events(f.session['id']) if e['kind'] == 'perception.output'
                       and e['payload'].get('arm') == 'b']
            if outputs: break
            time.sleep(.005)
        self.assertTrue(outputs, 'actual factory model double produced no B original')
        settings = replace(f.store.settings, token_grants=({'subject': 'authored-candidate-reader',
            'site_id': 'victoria', 'token_sha256': hashlib.sha256(TOKEN.encode()).hexdigest(),
            'capabilities': ['read']},))
        api = ActualReadAPI(create_app(settings=settings, runtime=runtime, store=f.store))
        output = Path(f.directory.name) / 'existing-api-assessment.jsonl'
        with patch('lab_whole_stack_trial.sync_parent') if sys.platform == 'win32' else _nullcontext():
            result = collect_existing(api, session_id=f.session['id'], output_file=output,
                clock=lambda: f.now, seconds=5, max_attempts=1, collection_mode=EXISTING_MODE)
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['candidate_attempts'], 0)
        self.assertEqual(result['missing_prediction_attempts'], 1)
        report = read_results(output)
        self.assertEqual(report['private_media_records'], 4)
        self.assertEqual(report['attempts'], 1)
        self.assertFalse(report['ordinary_lighting_replay'])
        rows = [json.loads(line) for line in output.read_bytes().splitlines()]
        candidate = next(r['value']['candidate'] for r in rows if r['kind'] == 'attempt')
        self.assertEqual(candidate['original_event'], outputs[0])
        self.assertEqual(candidate['reason'], 'no_pose_prediction')
        self.assertTrue(all(method == 'GET' for method, _ in api.calls))
        self.assertFalse(any(r['metadata'].get('stream') == 'paired_preview_original'
                             for r in f.store.list_media(f.session['id'])['items']))
        self.assertEqual(f.store.get_session(f.session['id'])['state'], 'recording')


def _nullcontext():
    from contextlib import nullcontext
    return nullcontext()


if __name__ == '__main__': unittest.main()
