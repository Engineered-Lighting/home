"""Existing journal admission from original-shaped Lab rows; no external I/O."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'home-context'))
from journal import Journal, JournalError, digest
from live_test_helpers import configuration, grant, METHOD, NOW, PROFILE, SCOPE, TOKEN
from staged_specialist import composite_manifest
from pose_composition import POLICY, MODEL_VERSION
from rtmpose_worker import RTMPoseWorker
from lab_live_journal_join import LabLiveJournalJoin
from test_lab_live_context_adapter import event


class LabLiveJournalJoinTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='authored-lab-join-', dir=Path(__file__).parent)
        self.addCleanup(self.work.cleanup)
        self.config = configuration()
        self.source = self.config['manifest']['sources']['synthetic-source']
        self.source['association_method_refs'].append(deepcopy(METHOD))
        self.model_manifests = {
            'detector': {'sha256': '1'*64, 'device_id': 'authored-detector', 'engine_epoch': 'epoch-live'},
            'pose': {'sha256': '2'*64, 'device_id': 'authored-pose', 'engine_epoch': 'epoch-live'}}
        self.lab_profile = composite_manifest(self.model_manifests['detector'], self.model_manifests['pose'],
            profile_id='yolo26s-rtmpose-cpu-v1', engine_epoch='epoch-live', policy=POLICY,
            model_version=MODEL_VERSION, pose_preprocessing_revision=RTMPoseWorker.preprocessing_revision,
            runtime_revision='3'*64)
        self.lab_profile['artifact_sha256'] = self.lab_profile['sha256']
        self.model = {'id': 'authored-original-lab-composite', 'digest': self.lab_profile['artifact_sha256']}
        self.source['model_refs'] = [deepcopy(self.model)]
        self.repin()
        self.join = self.make_join()
        self.now = NOW+.2
        self.journal = Journal(Path(self.work.name)/'journal.sqlite', grants=[self.grant], profile=PROFILE,
            live_config=self.config, live_receipt_resolver=self.join.resolve_receipt, clock=lambda: self.now)
        self.addCleanup(self.journal.close)
        self.principal = self.journal.authenticate(TOKEN)

    def repin(self):
        self.config['manifest_ref']['digest'] = digest(self.config['manifest'])
        self.grant = grant()
        self.grant['live_bindings'][0]['manifest_ref'] = deepcopy(self.config['manifest_ref'])

    def make_join(self):
        session = {'id': SCOPE['session_id'], 'site_id': SCOPE['site_id'], 'camera_id': 'den',
            'state': 'recording'}
        return LabLiveJournalJoin(session, arm='a', generation=1, live_config=self.config,
            source_id='synthetic-source', revision='synthetic-r1', method_ref=METHOD,
            model_ref=self.model, lab_profile=self.lab_profile, model_manifests=self.model_manifests,
            receipt_store_id='synthetic-receipts', receipt_component_id='pose')

    def inputs(self, event_id=12, *, observed=NOW, seq=7):
        row, _ = event(event_id, observed=observed)
        row['session_id'] = SCOPE['session_id']
        row['payload'].update(site_id=SCOPE['site_id'], capture_epoch='synthetic-camera-epoch')
        frame_id = 'synthetic-camera-epoch-0-' + str(seq)
        row['payload']['frame_id'] = frame_id
        row['payload']['result']['frame_id'] = frame_id
        row['payload']['arm_snapshot']['frame']['id'] = frame_id
        row['payload'].update(run_id=self.lab_profile['profile_id'], model_version=self.lab_profile['model_version'],
            engine_epoch=self.lab_profile['engine_epoch'], artifact_sha256=self.lab_profile['artifact_sha256'],
            request_id='authored-request-'+str(event_id), valid_until=observed+3-.0011)
        row['payload']['result']['processing'] = {'pipeline': 'detector-pose-v1',
            'model_version': self.lab_profile['model_version'], 'manifest_sha256': self.lab_profile['artifact_sha256'],
            'pose_preprocessing_revision': RTMPoseWorker.preprocessing_revision,
            'crop_policy': deepcopy(POLICY), 'correlated_evidence': True,
            'shared_source_request': row['payload']['request_id'],
            **{stage: {'artifact_sha256': manifest['sha256'], 'device_id': manifest['device_id']}
               for stage, manifest in self.model_manifests.items()}}
        frame = {'stream': 'rgb', 'frame_id': row['payload']['frame_id'],
            'capture_epoch': 'synthetic-camera-epoch', 'sequence': seq, 'timing_generation': 0,
            'site_id': SCOPE['site_id'], 'camera_id': 'den', 'format': 'jpeg', 'width': 640, 'height': 360,
            'clock_mapping_status': 'verified', 'clock_mapping_id': 'actual-preview-validation',
            'receiver_clock_validation_id': 'actual-host-validation', 'source_timestamp_seconds': 10.,
            'source_clock': 'depthai_device_monotonic', 'timestamp_semantics': 'causal_exact_frame_exposure_end',
            'observed_at': observed, 'available_at': observed+.05, 'valid_until': row['payload']['valid_until'],
            'uncertainty_seconds': .001+.0001, 'source_timing_receipt': {
                'schema': 3, 'session': 'synthetic-camera-epoch', 'sequence': seq,
                'timing_generation': 0, 'width': 640, 'height': 360, 'frame_device_ns': 10_000_000_000,
                'validation_id': 'actual-preview-validation', 'host_clock_validation_id': 'actual-host-validation',
                'source_clock': 'depthai_device_monotonic', 'timestamp_semantics': 'causal_exact_frame_exposure_end',
                'observed_at_unix_ns': int(observed*1_000_000_000), 'uncertainty_seconds': .001,
                'host_clock_uncertainty_seconds': .0001}}
        return row, frame

    def publish(self, row, frame):
        return self.join.publish(row, frame_metadata=frame, received_at=self.now,
            journal=self.journal, grant=self.principal)

    def test_actual_journal_admits_original_fact_and_unavailable_geometry(self):
        row, frame = self.inputs()
        result = self.publish(row, frame)
        self.assertEqual(result['status'], 'admitted_unqualified')
        self.assertEqual([r['cursor'] for r in result['receipts']], [1, 2])
        rows = self.journal.db.execute('SELECT body FROM publications ORDER BY cursor').fetchall()
        import json
        publications = [json.loads(r['body']) for r in rows]
        self.assertEqual([p['payload_type'] for p in publications], ['home-live-fact-1', 'home-live-geometry-1'])
        for publication in publications:
            payload = publication['payload']
            self.assertEqual(payload['status'], 'unobservable')
            self.assertIsNone(payload['person_ref'])
            self.assertEqual(payload['observed_at'], row['payload']['observed_at'])
            self.assertEqual(payload['available_at'], row['payload']['available_at'])
            self.assertEqual(payload['valid_until'], row['payload']['valid_until'])
            ref = payload['original_receipt_refs'][0]
            self.assertEqual(self.join.original_receipt(ref['store_id'], ref['receipt_id'])['event'], row)
        self.assertTrue(all(p['status'] == 'unobservable' for p in publications[1]['payload']['parts'].values()))

    def test_retry_after_expiry_preserves_original_receipts_and_identity_mutation_refuses(self):
        row, frame = self.inputs()
        initial = self.publish(row, frame)
        self.now += 100
        self.assertEqual(self.publish(row, frame), initial)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 2)
        row['payload']['valid_until'] += 1
        with self.assertRaisesRegex(ValueError, 'lab_event_identity_conflict'):
            self.publish(row, frame)

    def test_publication_intersects_original_rgb_and_model_leases(self):
        import json
        row, frame = self.inputs()
        original = deepcopy(row)
        frame['valid_until'] = row['payload']['valid_until'] - .5
        result = self.publish(row, frame)
        self.assertEqual(result['status'], 'admitted_unqualified')
        for record in self.journal.db.execute('SELECT body FROM publications ORDER BY cursor'):
            payload = json.loads(record['body'])['payload']
            self.assertEqual(payload['valid_until'], frame['valid_until'])
            for part in payload.get('parts', {}).values():
                self.assertEqual(part['valid_until'], frame['valid_until'])
            ref = payload['original_receipt_refs'][0]
            self.assertEqual(self.join.original_receipt(ref['store_id'], ref['receipt_id'])['event'], original)
        self.assertEqual(row, original)

    def test_expired_original_rgb_cannot_use_longer_model_lease(self):
        row, frame = self.inputs()
        frame['valid_until'] = self.now
        result = self.publish(row, frame)
        self.assertEqual(result['status'], 'expired')
        self.assertEqual(result['receipts'], [])
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 0)

    def test_lost_ack_requires_exact_retry_then_settles_real_receipt(self):
        row, frame = self.inputs()
        real_append = self.journal.append_event
        failed = [False]
        def append_then_lose_ack(envelope, principal):
            value = real_append(envelope, principal)
            if not failed[0]:
                failed[0] = True
                raise TimeoutError('authored_lost_ack')
            return value
        self.journal.append_event = append_then_lose_ack
        with self.assertRaises(TimeoutError):
            self.publish(row, frame)
        later, later_frame = self.inputs(13, observed=NOW+1, seq=8)
        with self.assertRaisesRegex(ValueError, 'lab_publication_pending_exact_retry_required'):
            self.publish(later, later_frame)
        settled = self.publish(row, frame)
        self.assertEqual([r['cursor'] for r in settled['receipts']], [1, 2])
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 2)

    def test_progress_uses_previous_journal_heads_and_original_sensor_sequence(self):
        first, frame = self.inputs()
        self.publish(first, frame)
        self.now += 1
        second, frame = self.inputs(13, observed=NOW+1, seq=8)
        result = self.publish(second, frame)
        self.assertEqual([r['cursor'] for r in result['receipts']], [3, 4])

    def test_original_integer_time_wrong_frame_and_excessive_error_cannot_be_commissioned(self):
        for mutation in ('clock', 'frame', 'uncertainty'):
            with self.subTest(mutation):
                row, frame = self.inputs()
                if mutation == 'clock': frame['source_timing_receipt']['observed_at_unix_ns'] += 10_000_000
                if mutation == 'frame': frame['frame_id'] = 'other-rgb'
                if mutation == 'uncertainty': frame['source_timing_receipt']['uncertainty_seconds'] = 1
                with self.assertRaises((ValueError, JournalError)):
                    self.publish(row, frame)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 0)

    def test_missing_registration_allows_only_honest_fact_and_no_geometry(self):
        self.source['registration'] = None
        self.source['part_policies'] = {part: None for part in self.source['part_policies']}
        self.repin()
        # Explicitly fresh journal/config; never replace a running authority.
        join = self.make_join()
        journal = Journal(Path(self.work.name)/'unregistered.sqlite', grants=[self.grant], profile=PROFILE,
            live_config=self.config, live_receipt_resolver=join.resolve_receipt, clock=lambda: self.now)
        try:
            row, frame = self.inputs()
            result = join.publish(row, frame_metadata=frame, received_at=self.now,
                journal=journal, grant=journal.authenticate(TOKEN))
            self.assertEqual(len(result['receipts']), 1)
            self.assertEqual(journal.db.execute('SELECT payload_type FROM publications').fetchone()[0], 'home-live-fact-1')
        finally:
            journal.close()

    def test_arbitrary_or_changed_original_model_identity_cannot_inherit_configured_model(self):
        for target, key in (('payload', 'artifact_sha256'), ('payload', 'engine_epoch'),
                            ('payload', 'run_id'), ('detector', 'artifact_sha256'),
                            ('pose', 'artifact_sha256'), ('pose', 'device_id')):
            with self.subTest(target=target, key=key):
                row, frame = self.inputs()
                part = row['payload'] if target == 'payload' else row['payload']['result']['processing'][target]
                part[key] = 'f'*64
                with self.assertRaisesRegex(ValueError, 'original_lab_.*binding_mismatch'):
                    self.publish(row, frame)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 0)
        wrong_profile = deepcopy(self.lab_profile)
        wrong_profile['artifact_sha256'] = 'f'*64
        self.lab_profile = wrong_profile
        with self.assertRaisesRegex(ValueError, 'lab_composite_model_binding_mismatch'):
            self.make_join()

    def test_cached_ack_cannot_masquerade_as_admission_by_a_second_journal(self):
        row, frame = self.inputs()
        self.publish(row, frame)
        second = Journal(Path(self.work.name)/'other-authority.sqlite', grants=[self.grant], profile=PROFILE,
            live_config=self.config, live_receipt_resolver=self.join.resolve_receipt, clock=lambda: self.now)
        try:
            with self.assertRaisesRegex(ValueError, 'lab_join_journal_authority_changed'):
                self.join.publish(row, frame_metadata=frame, received_at=self.now,
                    journal=second, grant=second.authenticate(TOKEN))
            self.assertEqual(second.db.execute('SELECT count(*) FROM publications').fetchone()[0], 0)
        finally:
            second.close()

    def test_outer_rgb_metadata_cannot_contradict_its_original_source_receipt(self):
        for key, wrong in (('sequence', 999), ('timing_generation', 999),
                ('source_timestamp_seconds', 999.), ('source_clock', 'another-clock'),
                ('observed_at', NOW+1), ('uncertainty_seconds', 0.),
                ('clock_mapping_id', 'another-validation'), ('camera_id', 'other-camera')):
            with self.subTest(key=key):
                row, frame = self.inputs()
                frame[key] = wrong
                with self.assertRaisesRegex(ValueError, 'original_lab_rgb_metadata_inconsistent'):
                    self.publish(row, frame)
        row, frame = self.inputs()
        frame['source_timing_receipt']['sequence'] = 999
        with self.assertRaisesRegex(ValueError, 'original_lab_rgb_metadata_inconsistent'):
            self.publish(row, frame)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
