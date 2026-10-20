"""Authored Lab Store -> actual Journal, durable originals and worker lifecycle.

No camera, model loading, private scans or non-loopback service calls occur.
Only directory fsync (unsupported on this Windows workstation) is substituted.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
for directory in (ROOT/'stack/services/home-context', ROOT/'tools/den-live-runtime'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from config import Settings
from store import Store
from journal import digest
from live_test_helpers import configuration, grant, METHOD, NOW, TOKEN
from live_worker_test_helpers import describe
from staged_specialist import composite_manifest
from pose_composition import POLICY, MODEL_VERSION
from rtmpose_worker import RTMPoseWorker
from lab_live_journal_join import LabLiveJournalJoin
from lab_live_acquisition import LabLiveAcquisition
from lab_original_archive import LabOriginalArchive, read_lab_originals
import test_lab_live_journal_join as join_fixture


class LabLiveAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='authored-lab-graph-', dir=Path(__file__).parent)
        self.addCleanup(self.work.cleanup)
        self.path = Path(self.work.name)
        self.now, self.mono = NOW+.2, 10.
        if os.name == 'nt':
            patch = mock.patch('lab_original_archive.sync_parent')
            patch.start()
            self.addCleanup(patch.stop)
        self.store = Store(Settings(storage_root=self.path/'lab', min_free_bytes=0,
            quota_bytes=64*1024**2, developer_fixture_mode=True), clock=lambda: self.now)
        self.addCleanup(self.store.close)
        self.session, _ = self.store.create_session('den', 'placement-best', 'Authored graph test', 'authored-request')
        self.store.transition(self.session['id'], 'starting')
        self.store.transition(self.session['id'], 'recording')
        self.session = self.store.get_session(self.session['id'])
        self.config = configuration()
        self.config['manifest']['identity'].update(site_id='victoria', session_id=self.session['id'])
        self.source = self.config['manifest']['sources']['synthetic-source']
        self.source['association_method_refs'].append(deepcopy(METHOD))
        self.source['registration'] = None
        self.source['part_policies'] = {part: None for part in self.source['part_policies']}
        self.model_manifests = {
            'detector': {'sha256': '1'*64, 'device_id': 'authored-detector', 'engine_epoch': 'epoch-live'},
            'pose': {'sha256': '2'*64, 'device_id': 'authored-pose', 'engine_epoch': 'epoch-live'}}
        self.lab_profile = composite_manifest(self.model_manifests['detector'], self.model_manifests['pose'],
            profile_id='yolo26s-rtmpose-cpu-v1', engine_epoch='epoch-live', policy=POLICY,
            model_version=MODEL_VERSION, pose_preprocessing_revision=RTMPoseWorker.preprocessing_revision,
            runtime_revision='3'*64)
        self.lab_profile['artifact_sha256'] = self.lab_profile['sha256']
        self.model = {'id': 'authored-lab-composite', 'digest': self.lab_profile['artifact_sha256']}
        self.source['model_refs'] = [deepcopy(self.model)]
        self.config['manifest_ref']['digest'] = digest(self.config['manifest'])
        self.producer_grant = grant()
        self.producer_grant['scopes'][0]['site_id'] = 'victoria'
        self.producer_grant['live_bindings'][0]['manifest_ref'] = deepcopy(self.config['manifest_ref'])
        self.graph = self.originals = None
        self.addCleanup(self.cleanup_graph)

    def control_clock(self):
        policy = self.source['control_clock']
        return {'observed_at': self.now, 'clock': {'status': 'commissioned',
            **{k: policy[k] for k in ('mapping_id', 'mapping_digest', 'source_epoch', 'original_unit')},
            'original_value': str(int(self.now*1_000_000_000)), 'uncertainty_s': .001}}

    def graph_for(self, *, retainer=None, grants=None, **limits):
        join = LabLiveJournalJoin(self.session, arm='a', generation=1, live_config=self.config,
            source_id='synthetic-source', revision='synthetic-r1', method_ref=METHOD,
            model_ref=self.model, lab_profile=self.lab_profile, model_manifests=self.model_manifests,
            receipt_store_id='synthetic-receipts', receipt_component_id='pose')
        self.originals = LabOriginalArchive(self.path/'originals.jsonl',
            binding={'session_id': self.session['id'], 'method_ref': METHOD, 'model_ref': self.model}, min_free_bytes=1)
        self.graph = LabLiveAcquisition(join, self.store, journal_path=self.path/'journal.sqlite',
            journal_grants=grants or [self.producer_grant], publication_token=TOKEN,
            original_retainer=retainer or self.originals, control_clock=self.control_clock,
            clock=lambda: self.now, monotonic=lambda: self.mono, **limits)
        return self.graph

    def add_original(self, *, observed=NOW, seq=7, mutation=None):
        row, frame = join_fixture.LabLiveJournalJoinTests.inputs(self, observed=observed, seq=seq)
        row['session_id'] = self.session['id']
        row['payload']['site_id'] = frame['site_id'] = 'victoria'
        media = self.store.add_media(self.session['id'], b'authored-placeholder-not-decoded',
            'image/jpeg', frame, source_key='authored-rgb-'+str(seq))
        row['payload']['media_ids'] = [media['id']]
        if mutation:
            mutation(row)
        event_id = self.store.append_event(self.session['id'], 'perception.output', row['payload'])
        return next(row for row in self.store.list_events(self.session['id']) if row['id'] == event_id)

    def record(self, data, **kwargs):
        from live_capture import _original_digest
        if data['kind'] == 'journal_prepare':
            for original in self.graph.join.originals.values():
                self.assertIn(original['event']['id'], self.originals.records)
                self.assertEqual(self.originals.records[original['event']['id']][0], _original_digest(original))
        return {'schema': 'home-live-recorded-1', 'version': 1, 'seq': 1, 'digest': 'a'*64}

    def drain_receipt(self, withdrawal, *, recorder='drained', worker='settled'):
        return {'schema': 'home-acquisition-drain-1', 'version': 1,
            'session_ref': deepcopy(self.graph.join.scope), 'configuration_digest': self.graph.configuration_digest,
            'withdrawal_receipt': withdrawal.get('barrier_receipt'), 'recorder_status': recorder,
            'worker_status': worker, 'terminal_cursor': withdrawal.get('input_cursor') or 0}

    def cleanup_graph(self):
        if self.graph and not self.graph.finished:
            self.graph.quiesce()
            withdrawal = self.graph.withdraw()
            self.graph.finalize(drain_receipt=self.drain_receipt(withdrawal, recorder='incomplete', worker='unconfirmed'))
        if self.originals:
            self.originals.finalize()

    def test_original_store_metadata_is_durable_before_actual_journal_admission(self):
        event = self.add_original()
        graph = self.graph_for()
        self.assertEqual(graph.readiness()['state'], 'idle')
        graph.journal.attach_recorder(self.record)
        result = graph.step()
        self.assertEqual(result['status'], 'admitted_unqualified', result)
        self.assertFalse(result['operational_geometry'])
        rows = graph.journal.db.execute('SELECT body FROM publications').fetchall()
        self.assertEqual(len(rows), 1)
        fact = json.loads(rows[0][0])['payload']
        self.assertEqual(fact['status'], 'unobservable')
        self.assertIsNone(fact['person_ref'])
        self.assertEqual(fact['valid_until'], event['payload']['valid_until'])
        self.assertEqual(fact['observed_at'], event['payload']['observed_at'])
        self.assertEqual(graph.step()['status'], 'idle')
        self.assertEqual(len(self.originals.records), 1)
        withdrawal = graph.withdraw()
        self.assertEqual(withdrawal['withdrawal'], 'recorded', withdrawal)
        self.assertEqual(graph.withdraw(), withdrawal)
        self.assertEqual(graph.step()['status'], 'stopped')
        final = graph.finalize(drain_receipt=self.drain_receipt(withdrawal))
        self.assertEqual(final['status'], 'stopped', final)
        originals = read_lab_originals(self.path/'originals.jsonl', expected_digests=[fact['original_receipt_refs'][0]['digest']])
        self.assertEqual(next(iter(originals.values()))['event'], event)
        self.assertEqual(self.store.get_session(self.session['id'])['state'], 'recording')

    def test_recorder_readiness_blocks_before_original_read_or_admission(self):
        self.add_original()
        graph = self.graph_for()
        with mock.patch.object(self.store, 'list_events', side_effect=AssertionError('read_before_ready')):
            result = graph.step()
        self.assertEqual(result['reason'], 'recorder_ready_before_lab_admission_required')
        self.assertEqual(len(self.originals.records), 0)
        self.assertEqual(graph.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 0)

    def test_expired_and_other_arm_events_are_skipped_without_refreshing_clocks(self):
        self.add_original(observed=NOW-20, seq=1)
        self.add_original(seq=2, mutation=lambda row: row['payload'].update(arm='b'))
        current = self.add_original(seq=3)
        graph = self.graph_for()
        graph.journal.attach_recorder(self.record)
        result = graph.step()
        self.assertEqual(result['status'], 'admitted_unqualified', result)
        self.assertEqual(result['after_id'], current['id'])
        self.assertEqual(graph.counters['expired'], 1)
        self.assertEqual(graph.counters['admitted'], 1)
        self.assertEqual(len(self.originals.records), 1)

    def test_expiry_intersection_and_fsync_delay_cannot_extend_original_rgb_lease(self):
        self.add_original(mutation=lambda row: row['payload'].update(valid_until=NOW+3.4))
        graph = self.graph_for()
        graph.journal.attach_recorder(self.record)
        archive = self.originals
        def slow_fsync(record):
            receipt = archive(record)
            self.now = NOW+3.
            return receipt
        graph.original_retainer = slow_fsync
        result = graph.step()
        self.assertEqual(result['status'], 'expired', result)
        self.assertEqual(graph.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 0)
        self.assertEqual(len(archive.records), 1)
        original = graph.pending_original
        self.assertIsNone(original)
        graph.original_retainer = archive

    def test_original_retention_failure_and_wrong_model_cannot_supply_publications(self):
        self.add_original(mutation=lambda row: row['payload'].update(artifact_sha256='f'*64))
        graph = self.graph_for()
        graph.journal.attach_recorder(self.record)
        result = graph.step()
        self.assertEqual(result['reason'], 'original_lab_model_binding_mismatch')
        self.assertEqual(graph.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 0)
        self.assertEqual(len(self.originals.records), 1)
        with mock.patch.object(graph, 'original_retainer', side_effect=OSError('authored_fsync_failure')):
            self.assertEqual(graph.step()['reason'], 'authored_fsync_failure')
        withdrawal = graph.withdraw()
        self.assertEqual(graph.finalize(drain_receipt=self.drain_receipt(withdrawal))['status'], 'unconfirmed')

    def test_lost_ack_exact_retry_preserves_original_first_receipt_and_does_not_duplicate(self):
        self.add_original()
        graph = self.graph_for()
        graph.journal.attach_recorder(self.record)
        real_append, lost = graph.journal.append_event, [False]
        def lose_ack(envelope, principal):
            ack = real_append(envelope, principal)
            if not lost[0]:
                lost[0] = True
                raise TimeoutError('authored_lost_ack')
            return ack
        graph.journal.append_event = lose_ack
        self.assertEqual(graph.step()['status'], 'failed')
        first_received = graph.pending_original['first_consumer_received_at']
        self.now += 10
        result = graph.step()
        self.assertEqual(result['status'], 'admitted_unqualified', result)
        self.assertEqual(graph.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 1)
        original = next(iter(graph.join.originals.values()))
        self.assertEqual(original['first_consumer_received_at'], first_received)
        self.assertEqual(len(self.originals.records), 1)

    def test_stop_during_original_fsync_prevents_later_publication_and_drains(self):
        self.add_original()
        graph = self.graph_for()
        graph.journal.attach_recorder(self.record)
        entered, release = threading.Event(), threading.Event()
        archive = self.originals
        def wait_for_stop(record):
            entered.set()
            self.assertTrue(release.wait(2))
            return archive(record)
        graph.original_retainer = wait_for_stop
        result = []
        thread = threading.Thread(target=lambda: result.append(graph.step()))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(graph.quiesce()['status'], 'unconfirmed')
            self.assertEqual(graph.withdraw()['withdrawal'], 'not_recorded')
        finally:
            release.set()
            thread.join(3)
        self.assertEqual(result[0]['status'], 'stopped')
        self.assertEqual(graph.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 0)
        graph.original_retainer = archive
        withdrawal = graph.withdraw()
        self.assertEqual(withdrawal['withdrawal'], 'recorded', withdrawal)
        self.assertEqual(graph.finalize(drain_receipt=self.drain_receipt(withdrawal))['status'], 'stopped')

    def test_finalize_requires_exact_drain_and_complete_original_sidecar(self):
        self.add_original()
        graph = self.graph_for()
        graph.journal.attach_recorder(self.record)
        self.assertEqual(graph.step()['status'], 'admitted_unqualified')
        withdrawal = graph.withdraw()
        wrong = self.drain_receipt(withdrawal)
        wrong['configuration_digest'] = 'f'*64
        self.assertEqual(graph.finalize(drain_receipt=wrong)['status'], 'refused')
        self.assertFalse(graph.finished)
        with mock.patch.object(self.originals, 'finalize', return_value={'status': 'incomplete'}):
            final = graph.finalize(drain_receipt=self.drain_receipt(withdrawal))
        self.assertEqual(final['status'], 'unconfirmed')
        self.assertTrue(final['buffers_cleared'])

    def test_source_media_generation_change_and_finite_budget_are_explicit(self):
        graph = self.graph_for(max_cycles=1)
        graph.journal.attach_recorder(self.record)
        self.assertEqual(graph.step()['status'], 'idle')
        self.assertEqual(graph.step()['reason'], 'lab_graph_budget_exhausted')
        # The graph never starts or restarts the source to obtain another epoch.
        self.assertEqual(self.store.get_session(self.session['id'])['generation'], 0)

    def test_changed_borrowed_store_media_generation_refuses_before_source_read(self):
        graph = self.graph_for()
        graph.journal.attach_recorder(self.record)
        changed = self.store.get_session(self.session['id'])
        changed['generation'] += 1
        with mock.patch.object(self.store, 'get_session', return_value=changed):
            self.assertEqual(graph.step()['reason'], 'incumbent_lab_session_binding_changed')
        self.assertEqual(len(self.originals.records), 0)

    def test_actual_node_worker_consumes_fact_and_settles_withdrawal(self):
        from archive import Recorder, RecorderRPC
        from supervisor import NodeWorkerDriver
        from schema_registry import schema_digest
        self.add_original()
        manifest = deepcopy(self.config['manifest'])
        worker_id, worker_token = 'authored-lab-worker', 'authored-lab-worker-token'
        seed = {'profile': 'home-live-shadow-1', 'identity': deepcopy(manifest['identity']),
            'source_pins': {key: digest(source) for key, source in manifest['sources'].items()},
            'engagement_policy': {'support_methods': [deepcopy(METHOD)], 'refinement_methods': [],
                'negative_methods': [], 'allow_experimental': True},
            'worker_id': worker_id, 'producer_id': worker_id, 'producer_epoch': 'authored-lab-worker-epoch',
            'revision': 'synthetic-r1', 'max_output_age_s': 3, 'semantic_interval_s': 1}
        description = describe(seed)
        policy = {key: seed[key] for key in ('worker_id', 'producer_id', 'producer_epoch', 'revision',
            'source_pins', 'engagement_policy', 'max_output_age_s', 'semantic_interval_s')}
        policy.update(config_digest=description['config_digest'], schema_pins=description['schema_pins'])
        manifest.update(schema='home-live-ingress-manifest-2', version=2,
            worker_interface_digest=description['worker_interface_digest'], worker=policy)
        self.config = {'schema': 'home-live-ingress-config-2', 'version': 2,
            'manifest_ref': {'id': 'authored-lab-worker-manifest', 'digest': digest(manifest)}, 'manifest': manifest}
        self.producer_grant['live_bindings'][0]['manifest_ref'] = deepcopy(self.config['manifest_ref'])
        types = ['home-live-fact-1', 'home-live-geometry-1', 'home-live-engagement-state-1',
            'home-live-shadow-decision-1', 'home-live-worker-checkpoint-1', 'home-live-worker-commit-1', 'home-live-input-cut-1']
        worker_grant = {'principal_id': 'authored-lab-worker-principal', 'producer_id': worker_id,
            'producer_epoch': seed['producer_epoch'], 'expected_consumer_id': worker_id, 'expected_worker_id': worker_id,
            'token_sha256': hashlib.sha256(worker_token.encode()).hexdigest(),
            'scopes': [{'site_id': 'victoria', 'room_id': seed['identity']['room_id']}],
            'operations': ['read', 'worker_commit'], 'payload_types': types, 'revisions': ['synthetic-r1'],
            'source_ids': ['synthetic-source'], 'sensor_roots': ['camera:synthetic-oak'],
            'schema_pins': {name: schema_digest(name, profile='home-live-shadow-1') for name in types},
            'live_bindings': [{'manifest_ref': deepcopy(self.config['manifest_ref']), 'source_id': 'synthetic-source',
                               'capabilities': ['read', 'worker_commit']}]}
        graph = self.graph_for(grants=[self.producer_grant, worker_grant])
        worker_config = {**seed, 'manifest_ref': deepcopy(self.config['manifest_ref']), 'live_manifest': manifest,
            'token': worker_token, 'service_url': 'http://127.0.0.1:'+str(graph.address[1])}
        archive_config = {key: value for key, value in worker_config.items() if key not in {'token', 'service_url'}}
        recorder = Recorder(self.path/'session.jsonl', {'schema': 'home-live-session-manifest-1', 'version': 1,
            'worker_config': archive_config, 'worker_description': describe(archive_config),
            'session_ref': seed['identity'], 'configuration_digest': graph.configuration_digest,
            'source_bundle_digest': 'b'*64}, max_records=1024, max_bytes=8*1024**2, min_free_bytes=1)
        self.addCleanup(lambda: recorder.drain({'status': 'incomplete'}) if not recorder.closed else None)
        graph.journal.attach_recorder(recorder.record)
        rpc = RecorderRPC(recorder)
        port = rpc.start()
        self.addCleanup(rpc.close)
        driver = NodeWorkerDriver(node_runtime=shutil.which('node'), config=worker_config,
            recorder=port, controlled_time=self.now)
        self.addCleanup(driver.close)
        self.assertEqual(driver.readiness()['status'], 'ready')
        self.assertEqual(graph.step()['status'], 'admitted_unqualified')
        driver.cycle(self.now)
        self.now += .1
        withdrawal = graph.withdraw()
        self.assertEqual(withdrawal['withdrawal'], 'recorded', withdrawal)
        driver.cycle(self.now)
        drained = driver.drain(deadline_ms=1000, through_cursor=withdrawal['input_cursor'])
        self.assertEqual(drained['status'], 'settled', drained)
        self.assertEqual(recorder.drain({'status': 'complete', 'terminal_cursor': drained['input_cursor']})['status'], 'drained')
        driver.close()
        rpc.close()
        receipt = self.drain_receipt(withdrawal)
        receipt['terminal_cursor'] = drained['input_cursor']
        self.assertEqual(graph.finalize(drain_receipt=receipt)['status'], 'stopped')


if __name__ == '__main__':
    unittest.main()
