"""Authored incumbent Store -> actual supervisor/Node/context edge, locally only."""
import asyncio
from copy import deepcopy
from decimal import Decimal
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
for directory in (ROOT/'stack/services/home-context', ROOT/'tools/den-live-runtime'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import httpx
import lab_live_runtime as runtime
import test_lab_live_acquisition as graph_fixture
from journal import digest
from live_context_edge import ContextEdge, binding_digest
from live_den_mount import _runtime_module
from live_test_helpers import METHOD, TOKEN, grant
from live_worker_test_helpers import describe
from lab_live_acquisition import utc_now


class LabLiveRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = graph_fixture.LabLiveAcquisitionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture
        _runtime_module()
        self.runtime_supervisor = importlib.import_module('home_den_runtime.supervisor')
        self.runtime_archive = importlib.import_module('home_den_runtime.lab_original_archive')
        if os.name == 'nt':
            patch = mock.patch.object(self.runtime_archive, 'sync_parent')
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(runtime, 'private_token', return_value=TOKEN)
        patch.start()
        self.addCleanup(patch.stop)
        self.composition = self.supervisor = None
        self.addCleanup(self.cleanup_supervisor)
        self.append_ready()

    def append_ready(self):
        self.f.store.append_event(self.f.session['id'], 'experiment.activated', {
            'site_id': 'victoria', 'camera_id': 'den', 'generation': 1,
            'profiles': {'b': {k: self.f.lab_profile[k] for k in
                ('profile_id', 'artifact_sha256', 'engine_epoch', 'model_version')}}})
        self.ready_id = self.f.store.append_event(self.f.session['id'], 'workers.ready', {
            'receipts': {'b': {'state': 'ready', 'session_id': self.f.session['id'],
                'engine_epoch': self.f.lab_profile['engine_epoch'], 'profile': deepcopy(self.f.lab_profile),
                'expires_at': self.f.now+30}}})

    def producer(self):
        return {'schema': 'home-incumbent-lab-producer-1', 'version': 1,
            'lab_binding': {'session_id': self.f.session['id'], 'arm': 'b', 'model_generation': 1,
                'media_generation': 0, 'lab_profile': deepcopy(self.f.lab_profile),
                'model_manifests': deepcopy(self.f.model_manifests)},
            'live_config': deepcopy(self.f.config), 'source_id': 'synthetic-source', 'revision': 'synthetic-r1',
            'method_ref': deepcopy(METHOD), 'model_ref': deepcopy(self.f.model),
            'receipt_store_id': 'synthetic-receipts', 'receipt_component_id': 'pose', 'after_id': self.ready_id,
            'limits': {'max_cycles': 4, 'max_runtime_s': 10, 'page_limit': 128},
            'journal_path': str(self.f.path/'journal.sqlite'), 'journal_grants': [deepcopy(self.f.producer_grant)],
            'publication_token_file': str(self.f.path/'publication-token'),
            'original_archive': {'filename': str(self.f.path/'originals.jsonl'),
                'max_records': 4, 'max_bytes': 1024**2, 'min_free_bytes': 1}}

    def compose(self, producer=None, *, clock=None, control_clock=None, preflight=None):
        self.composition = runtime.LabRuntimeComposition(producer or self.producer(), store=self.f.store,
            preflight=preflight or (lambda: True), control_clock=control_clock or self.f.control_clock,
            clock=clock or (lambda: self.f.now), monotonic=time.monotonic)
        return self.composition

    def create_graph(self, composition):
        graph = composition.create()
        self.f.graph, self.f.originals = graph, composition.originals
        return graph

    def cleanup_supervisor(self):
        if self.supervisor is not None:
            self.supervisor.stop()
            if self.supervisor.thread is not None:
                self.supervisor.thread.join(4)
            # Reuse the established graph cleanup if startup was interrupted.
            self.f.graph = self.supervisor.graph or self.f.graph
            self.f.originals = self.composition.originals

    def test_configuration_and_constructor_are_inert_and_reproduce_exact_graph_digest(self):
        producer = self.producer()
        expected = runtime.lab_graph_configuration(producer, session=self.f.session)
        with mock.patch('subprocess.Popen', side_effect=AssertionError('no_process_before_start')), \
                mock.patch('socket.socket', side_effect=AssertionError('no_socket_before_start')):
            composition = self.compose(producer, preflight=lambda: (_ for _ in ()).throw(AssertionError('preflight_before_start')))
        self.assertEqual(composition.acquisition_config, expected)
        self.assertEqual(composition.configuration_digest, digest(expected))
        self.assertIs(composition.store, self.f.store)
        self.assertFalse(Path(producer['journal_path']).exists())
        self.assertFalse(Path(producer['original_archive']['filename']).exists())

    def test_explicit_current_arm_profile_and_separate_generations_are_required(self):
        mutations = (
            lambda p: p['lab_binding'].update(arm='a'),
            lambda p: p['lab_binding'].update(media_generation=1),
            lambda p: p['lab_binding'].update(model_generation=0),
            lambda p: p['lab_binding']['lab_profile'].update(engine_epoch='other-epoch'),
            lambda p: p.update(source_url='https://untrusted.invalid'),
        )
        for mutate in mutations:
            producer = self.producer()
            mutate(producer)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.compose(producer)
        self.f.store.transition(self.f.session['id'], 'stopped')
        with self.assertRaisesRegex(ValueError, 'current_recording_lab_session_required'):
            self.compose()

    def test_no_start_when_original_ready_worker_has_retired_or_preflight_fails(self):
        composition = self.compose(preflight=lambda: False)
        with self.assertRaisesRegex(ValueError, 'preflight_unmet'):
            composition.create()
        self.assertFalse(Path(composition.producer['journal_path']).exists())
        self.f.store.append_event(self.f.session['id'], 'workers.cleanup', {'state': 'stopped'})
        with self.assertRaisesRegex(ValueError, 'current_incumbent_la_worker_receipts_required'):
            self.compose()

    def test_generation_change_before_retention_blocks_actual_journal_publication(self):
        self.f.add_original(mutation=lambda row: row['payload'].update(arm='b'))
        composition = self.compose()
        graph = self.create_graph(composition)
        graph.journal.attach_recorder(self.f.record)
        self.f.store.append_event(self.f.session['id'], 'experiment.activated', {
            'site_id': 'victoria', 'camera_id': 'den', 'generation': 2, 'profiles': {}})
        result = graph.step()
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(graph.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 0)
        self.assertEqual(len(composition.originals.records), 0)

    def test_cancel_and_release_leave_incumbent_store_and_worker_rows_untouched(self):
        composition = self.compose()
        before = self.f.store.list_events(self.f.session['id'])
        self.assertEqual(composition.stop_preparation()['status'], 'quiescent')
        with self.assertRaisesRegex(ValueError, 'cancelled_or_changed'):
            composition.create()
        self.assertEqual(composition.release()['status'], 'released')
        self.assertEqual(self.f.store.list_events(self.f.session['id']), before)
        self.assertEqual(self.f.store.get_session(self.f.session['id'])['state'], 'recording')

    def connected_runtime(self, producer, *, mode='controlled'):
        manifest = deepcopy(producer['live_config']['manifest'])
        worker_id, worker_token = 'authored-lab-runtime-worker', 'authored-lab-runtime-worker-token'
        seed = {'profile': 'home-live-shadow-1', 'identity': deepcopy(manifest['identity']),
            'source_pins': {key: digest(source) for key, source in manifest['sources'].items()},
            'engagement_policy': {'support_methods': [deepcopy(METHOD)], 'refinement_methods': [],
                'negative_methods': [], 'allow_experimental': True},
            'worker_id': worker_id, 'producer_id': worker_id, 'producer_epoch': 'authored-runtime-worker-epoch',
            'revision': 'synthetic-r1', 'max_output_age_s': 3, 'semantic_interval_s': 1}
        description = describe(seed)
        policy = {key: seed[key] for key in ('worker_id', 'producer_id', 'producer_epoch', 'revision',
            'source_pins', 'engagement_policy', 'max_output_age_s', 'semantic_interval_s')}
        policy.update(config_digest=description['config_digest'], schema_pins=description['schema_pins'])
        manifest.update(schema='home-live-ingress-manifest-2', version=2,
            worker_interface_digest=description['worker_interface_digest'], worker=policy)
        live = {'schema': 'home-live-ingress-config-2', 'version': 2,
            'manifest_ref': {'id': 'authored-lab-runtime-manifest', 'digest': digest(manifest)}, 'manifest': manifest}
        producer['live_config'] = live
        producer['journal_grants'][0]['live_bindings'][0]['manifest_ref'] = deepcopy(live['manifest_ref'])
        from schema_registry import schema_digest
        types = ['home-live-fact-1', 'home-live-geometry-1', 'home-live-engagement-state-1',
            'home-live-shadow-decision-1', 'home-live-worker-checkpoint-1', 'home-live-worker-commit-1', 'home-live-input-cut-1']
        producer['journal_grants'].append({'principal_id': 'authored-runtime-worker-principal', 'producer_id': worker_id,
            'producer_epoch': seed['producer_epoch'], 'expected_consumer_id': worker_id, 'expected_worker_id': worker_id,
            'token_sha256': hashlib.sha256(worker_token.encode()).hexdigest(),
            'scopes': [{'site_id': 'victoria', 'room_id': seed['identity']['room_id']}],
            'operations': ['read', 'worker_commit'], 'payload_types': types, 'revisions': ['synthetic-r1'],
            'source_ids': ['synthetic-source'], 'sensor_roots': ['camera:synthetic-oak'],
            'schema_pins': {name: schema_digest(name, profile='home-live-shadow-1') for name in types},
            'live_bindings': [{'manifest_ref': deepcopy(live['manifest_ref']), 'source_id': 'synthetic-source',
                               'capabilities': ['read', 'worker_commit']}]})
        self.context_token = 'authored-lab-runtime-context-token'
        # This minimal v2 fixture has no scene/intent contract. Its context
        # reader has exactly read authority; later scene versions add intents.
        context = grant(token=self.context_token, operations=['read'], capabilities=['read'])
        context['scopes'][0]['site_id'] = 'victoria'
        context['live_bindings'][0]['manifest_ref'] = deepcopy(live['manifest_ref'])
        producer['journal_grants'].append(context)
        graph_config = runtime.lab_graph_configuration(producer, session=self.f.session)
        session = {'schema': 'home-live-session-config-1', 'version': 1,
            'session_ref': deepcopy(manifest['identity']), 'configuration_digest': digest(graph_config),
            'mode': mode, 'max_runtime_s': 10, 'max_cycles': 4, 'interval_s': .5, 'drain_timeout_s': 3}
        return {'schema': 'home-live-runtime-config-1', 'version': 1, 'session': session,
            'worker_config': {**seed, 'manifest_ref': deepcopy(live['manifest_ref']), 'live_manifest': manifest,
                'token': worker_token}, 'node_runtime': shutil.which('node'),
            'archive_file': str(self.f.path/'session.jsonl'),
            'archive_limits': {'max_records': 1024, 'max_bytes': 8*1024**2, 'min_free_bytes': 1},
            'source_bundle_digest': 'b'*64}

    def test_wrong_worker_scope_and_archive_alias_are_rejected_before_start(self):
        producer = self.producer()
        config = self.connected_runtime(producer)
        composition = self.compose(producer)
        config['worker_config']['identity']['session_id'] = 'other-session'
        with self.assertRaisesRegex(ValueError, 'exact_lab_supervisor'):
            runtime.compose_lab_supervisor(config, composition, supervisor_preflight=lambda _: {'status': 'ready'})
        self.assertFalse(Path(producer['journal_path']).exists())

    def test_live_shadow_mode_is_preserved_and_never_omits_the_actual_worker(self):
        producer = self.producer()
        config = self.connected_runtime(producer, mode='live_shadow_unqualified')
        composition = self.compose(producer)
        self.supervisor = runtime.compose_lab_supervisor(config, composition,
            supervisor_preflight=lambda _: {'status': 'ready', 'qualification_current': False})
        status = self.supervisor.status()
        self.assertEqual(status['mode'], 'live_shadow_unqualified')
        self.assertFalse(status['acceptance_eligible'])
        self.assertFalse(status['physical_authority'])
        self.assertIsNone(self.supervisor.graph)
        missing = deepcopy(config)
        missing['worker_config'] = None
        with self.assertRaisesRegex(ValueError, 'exact_lab_supervisor'):
            runtime.compose_lab_supervisor(missing, composition,
                supervisor_preflight=lambda _: {'status': 'ready', 'qualification_current': False})
        self.assertFalse(Path(producer['journal_path']).exists())

    def test_one_round_ns_clock_keeps_original_control_and_default_graph_order(self):
        ns = 1800000001_123456789
        expected = float(Decimal(ns)/Decimal(1_000_000_000))
        older_float = math.nextafter(expected, -math.inf)
        with mock.patch('lab_live_acquisition.time.time_ns', side_effect=[ns, ns+256]), \
                mock.patch('lab_live_acquisition.time.time', return_value=older_float):
            first, later = utc_now(), utc_now()
            self.assertEqual(first, expected)
            self.assertEqual(later, float(Decimal(ns+256)/Decimal(1_000_000_000)))
            self.assertGreaterEqual(later, first)
            self.assertGreater(first, time.time())  # Reproduces the old one-ULP ordering fault.
        def control_clock():
            return {'observed_at': expected, 'clock': {'status': 'commissioned',
                **{key: self.f.source['control_clock'][key] for key in
                   ('mapping_id', 'mapping_digest', 'source_epoch', 'original_unit')},
                'original_value': str(ns), 'uncertainty_s': .001}}
        self.f.add_original(mutation=lambda row: row['payload'].update(arm='b'))
        with mock.patch('lab_live_acquisition.time.time_ns', return_value=ns), \
                mock.patch('lab_live_acquisition.time.time', return_value=older_float):
            composition = runtime.LabRuntimeComposition(self.producer(), store=self.f.store,
                preflight=lambda: True, control_clock=control_clock)
            self.composition = composition
            graph = self.create_graph(composition)
            self.assertEqual(graph.clock(), expected)
            graph.journal.attach_recorder(self.f.record)
            self.assertEqual(graph.step()['status'], 'admitted_unqualified')
            withdrawal = graph.withdraw()
            self.assertEqual(withdrawal['withdrawal'], 'recorded', withdrawal)
            self.assertEqual(graph.finalize(drain_receipt=self.f.drain_receipt(withdrawal))['status'], 'stopped')

    def test_actual_operator_supervisor_journal_node_context_and_originals_join(self):
        # This is authored input under real process/wall time, not a live trial.
        self.f.now = utc_now()
        self.append_ready()
        self.f.add_original(observed=int(self.f.now), mutation=lambda row: row['payload'].update(arm='b'))
        producer = self.producer()
        config = self.connected_runtime(producer)
        def control_clock():
            ns = time.time_ns()
            return {'observed_at': float(Decimal(ns)/Decimal(1_000_000_000)),
                'clock': {'status': 'commissioned', **{k: self.f.source['control_clock'][k] for k in
                    ('mapping_id', 'mapping_digest', 'source_epoch', 'original_unit')},
                    'original_value': str(ns), 'uncertainty_s': .001}}
        composition = self.compose(producer, clock=utc_now, control_clock=control_clock)
        self.supervisor = runtime.compose_lab_supervisor(config, composition,
            supervisor_preflight=lambda _: {'status': 'ready', 'qualification_current': False})
        withdrawals = []
        actual_factory = self.supervisor.graph_factory
        def traced_factory():
            graph = actual_factory()
            actual_withdraw = graph.withdraw
            def withdraw(reason='operator_stop'):
                result = actual_withdraw(reason)
                withdrawals.append(deepcopy(result))
                return result
            graph.withdraw = withdraw
            return graph
        self.supervisor.graph_factory = traced_factory
        binding = {'scope': composition.scope, 'manifest_ref': producer['live_config']['manifest_ref']}
        async def fallback(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 404, 'headers': []})
            await send({'type': 'http.response.body', 'body': b''})
        context = ContextEdge(fallback, binding=binding,
            token_sha256=hashlib.sha256(self.context_token.encode()).hexdigest(),
            journal_address=lambda: self.supervisor.graph.address if self.supervisor.status()['status'] in {'running', 'stopping'} else None)
        operator_token = 'authored-lab-runtime-operator-token'
        mounted = self.runtime_supervisor.SessionControlEdge(context, supervisor=self.supervisor,
            token_sha256=hashlib.sha256(operator_token.encode()).hexdigest())
        def request(operation, command_id):
            async def run():
                command = {'schema': 'home-live-session-command-1', 'version': 1, 'id': command_id,
                    'operation': operation, 'session_ref': config['session']['session_ref'],
                    'configuration_digest': config['session']['configuration_digest']}
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mounted), base_url='https://authored.invalid') as client:
                    return await client.post('/api/home-live-session/v1/'+operation, json=command,
                        headers={'Authorization': 'Bearer '+operator_token})
            return asyncio.run(run())
        self.assertEqual(request('start', 'authored-start').status_code, 200)
        deadline = time.monotonic()+8
        while time.monotonic() < deadline:
            graph = self.supervisor.graph
            if graph is not None and graph.counters['admitted']:
                break
            if self.supervisor.status()['status'] in {'refused', 'incomplete', 'stopped'}:
                self.fail(str(self.supervisor.status()))
            time.sleep(.01)
        else:
            self.fail('authored_source_not_admitted')
        self.assertIs(graph, composition.graph)
        self.assertIs(graph.journal, self.supervisor.graph.journal)
        async def read_context():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mounted), base_url='https://authored.invalid') as client:
                return await client.get('/api/home-context/v1/snapshot?site_id=victoria&room_id='+composition.scope['room_id'],
                    headers={'Authorization': 'Bearer '+self.context_token, 'X-Home-Context-Binding': binding_digest(binding)})
        response = asyncio.run(read_context())
        self.assertEqual(response.status_code, 200, response.text)
        snapshot = response.json()
        self.assertIn('unobservable', json.dumps(snapshot))
        self.supervisor.thread.join(8)
        self.assertEqual(self.supervisor.status()['status'], 'stopped',
                         {'status': self.supervisor.status(), 'withdrawals': withdrawals})
        self.assertEqual(request('status', 'authored-status').status_code, 200)
        results = request('read-results', 'authored-results')
        self.assertEqual(results.status_code, 200, results.text)
        self.assertEqual(results.json()['ordinary_replay']['status'], 'reproduced', results.text)
        originals = self.runtime_archive.read_lab_originals(producer['original_archive']['filename'])
        self.assertEqual(len(originals), 1)
        original = next(iter(originals.values()))
        self.assertEqual(original['event']['payload']['arm'], 'b')
        self.assertEqual(self.f.store.get_session(self.f.session['id'])['state'], 'recording')


if __name__ == '__main__':
    unittest.main()
