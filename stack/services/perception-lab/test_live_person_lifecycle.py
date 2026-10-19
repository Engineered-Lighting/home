"""Authored producer lifecycle at its observation and publication ports."""
from copy import deepcopy
import unittest


def pin(name):
    from journal import digest
    return {'id': name, 'digest': digest(name)}


class LifecycleProducerTests(unittest.TestCase):
    def fixture(self):
        self.now = 101.
        self.scope = dict(site_id='authored', room_id='den', session_id='one', domain_epoch='one')
        self.method, self.qual, self.app = pin('continuity'), pin('continuity-qualification'), pin('continuity-applicability')
        self.registration = {'registration_digest': 'a' * 64}
        self.policy = dict(schema='home-local-person-lifecycle-policy-1', version=1, id='authored-lifecycle',
            capture_method_ref=pin('capture'), spatial_method_ref=pin('spatial'),
            model_refs={'detector': pin('detector'), 'pose': pin('pose')}, registration=deepcopy(self.registration),
            receipt_store_id='lifecycle-store', receipt_component_id='lifecycle',
            continuity=dict(method_ref=self.method, qualification_ref=self.qual, applicability_ref=self.app,
                max_gap_s=2, anchor_joint_ids=[5, 6, 11, 12], min_anchor_count=2,
                max_anchor_error_m=.1, max_step_m=1, max_speed_m_s=1), negative_methods=[], absence_methods=[])
        self.worker = dict(schema='home-live-person-lifecycle-policy-1', version=1,
            continuity_methods=[{key: self.policy['continuity'][key] for key in
                ('method_ref', 'qualification_ref', 'applicability_ref', 'max_gap_s')}],
            negative_methods=[], absence_methods=[], operator_clearance=None)
        self.source = dict(method_refs=[self.method, self.policy['capture_method_ref'], self.policy['spatial_method_ref']],
            association_method_refs=[self.method, self.policy['spatial_method_ref']],
            model_refs=list(self.policy['model_refs'].values()), registration=deepcopy(self.registration),
            max_age_s=3, receipt_store_ids=['lifecycle-store'], receipt_component_ids=['lifecycle'],
            producer_id='producer', producer_epoch='producer-one')
        self.source['part_policies'] = {'body': dict(model='conservative_additive_radius-1',
            bound_at_observation_m=.1, components_m=dict(capture_association=.02,depth=.02,registration=.02,estimator=.04),
            age=dict(max_age_s=3,max_speed_m_s=1,max_accel_m_s2=1),applicability_ref=self.app)}
        person = {**self.scope, 'track_epoch': 'track-one', 'pid': 1}
        self.prior = dict(person_ref=person, subject_ref={'subject_id': 'work', 'subject_epoch': 'work-one'},
            observed_at=100., anchor_m=[0, 0, 1], source_generation=1, capture_epoch='camera-one', capture_seq=1)
        self.context = dict(session_ref=self.scope, source_id='oak', configuration_digest='c' * 64,
            capture={'id': 'capture-two'}, capture_payload=dict(observed_at=101., source_generation=1,
                registration=deepcopy(self.registration), head={'position': {'capture_epoch': 'camera-one', 'capture_seq': 2, 'revision': 0}}),
            model_refs=deepcopy(self.policy['model_refs']))
        payload = self.context['capture_payload']
        payload.update(**self.scope, available_at=101., valid_until=104., manifest_ref=pin('manifest'),
            clock={'status': 'commissioned', 'mapping_id': 'clock', 'mapping_digest': 'b' * 64,
                'source_epoch': 'camera-one', 'original_unit': 'unix_s', 'original_value': '101', 'uncertainty_s': .01},
            sensor_roots=['camera:oak'], observation_roots=['rgb-two'], observation_root_times={'rgb-two': 101.},
            sensor_root_bindings={'rgb-two': ['camera:oak']}, original_receipt_refs=[dict(store_id='capture',
                receipt_id='rgb-two', digest='d' * 64, component_id='rgb', observed_at=101.,
                available_at=101., clock_mapping_id='clock')])
        self.context['publication_receipt'] = dict(stream_epoch='journal', cursor=1, producer_id='producer',
            id='capture-two', payload_digest='e' * 64)
        self.numeric = {'projection': {'error_bound_m': .01}, 'registration': {'error_bound_m': .01},
            'capture': {'observed_at':101.,'depth_observed_at':100.99},
            'source_metadata': {'frames': {'depth': {'device_timestamp_seconds':50.19}}},
            'original_receipts': [{'component_id':'rgb','rgb_receipt':{'frame_device_ns':50_200_000_000}},
                {'component_id':'depth','depth_clock_mapping':{'uncertainty_components_s':{'anchor_s':.01,'quantization_s':.001}}}]}
        self.estimate = {'joints': [{'joint_id': i, 'status': 'measured', 'xyz_m': [.2, 0, 1], 'spread_m': .01}
                                     for i in (5, 6)]}
        self.events = []
        def publish(event):
            from journal import digest
            self.events.append(deepcopy(event))
            return dict(status='accepted', id=event['id'], payload_digest=digest(event['payload']),
                stream_epoch='journal', cursor=len(self.events), producer_id='producer', committed_at=self.now)
        self.publish = publish

    def construct(self):
        from live_person_lifecycle import LivePersonLifecycle
        return LivePersonLifecycle(self.policy, worker_policy=self.worker, source=self.source,
            session_ref=self.scope, source_id='oak', configuration_digest='c' * 64,
            revision='authored', clock=lambda: self.now, publish=self.publish)

    def test_missing_observation_keeps_only_private_epoch_and_unique_measured_return_qualifies(self):
        self.fixture()
        lifecycle = self.construct()
        gap = lifecycle.consider(self.context, self.numeric, None, self.prior, person_count=0)
        self.assertEqual(gap['action'], 'gap')
        self.assertIsNone(gap['anchor_m'])
        returning = lifecycle.consider(self.context, self.numeric, self.estimate, self.prior, person_count=1)
        self.assertEqual(returning['action'], 'continue')
        self.assertEqual(returning['anchor_m'], [.2, 0, 1])
        self.assertEqual(returning['method_ref'], self.method)
        self.assertNotIn('dwell', returning)
        self.context['capture_payload']['observed_at'] = 103
        expired = lifecycle.consider(self.context, self.numeric, self.estimate, self.prior, person_count=1)
        self.assertEqual(expired['action'], 'reset')

    def test_gap_publishes_exact_original_clocks_and_retry_preserves_first_available_time(self):
        self.fixture()
        lifecycle = self.construct()
        result = lifecycle.publish_observation(self.context, 'observation_gap', prior=self.prior)
        self.assertEqual(result['status'], 'published')
        payload = result['publication']['payload']
        self.assertEqual(payload['disposition'], 'observation_gap')
        self.assertEqual(payload['target_person_ref'], self.prior['person_ref'])
        self.assertEqual(payload['target_subject_ref'], self.prior['subject_ref'])
        self.assertEqual(payload['observed_at'], 101)
        self.assertEqual(payload['window'], {'start_at': 100, 'end_at': 101})
        self.assertEqual(payload['observation_root_times'], {'rgb-two': 101})
        self.assertEqual(payload['head']['key']['predicate'], 'person_lifecycle')
        self.assertIsNone(payload['coverage_assessment_ref'])
        self.assertNotIn('parts', payload)
        original_ref = payload['original_receipt_refs'][-1]
        original = lifecycle.original_receipt(original_ref['store_id'], original_ref['receipt_id'])
        self.assertEqual(original['truth_role'], 'runtime_lifecycle_method_result')
        self.now = 102
        self.assertEqual(lifecycle.publish_observation(self.context, 'observation_gap', prior=self.prior), result)
        self.assertEqual(len(self.events), 1)

    def test_absence_requires_explicit_matching_typed_coverage_receipt_not_zero_detections(self):
        self.fixture()
        from live_person_lifecycle import LifecycleRefusal
        absent = dict(method_ref=pin('absence'), qualification_ref=pin('absence-qualification'),
                      applicability_ref=pin('absence-applicability'), coverage_assessment_ref=pin('complete-coverage'))
        self.policy['absence_methods'] = [deepcopy(absent)]
        self.worker['absence_methods'] = [deepcopy(absent)]
        self.source['method_refs'].append(absent['method_ref'])
        lifecycle = self.construct()
        self.assertEqual(lifecycle.publish_receipts(self.context, prior=self.prior), [])
        self.assertEqual(self.events, [])
        receipt = dict(schema='home-qualified-person-lifecycle-receipt-1', version=1, id='absence-one',
            session_ref=self.scope, configuration_digest='c' * 64, capture_id='capture-two', source_id='oak', source_generation=1,
            target_person_ref=self.prior['person_ref'], target_subject_ref=None, disposition='absence_qualified',
            **absent, basis_refs=[], window={'start_at': 100.5, 'end_at': 101.}, observed_at=101.,
            available_at=101., valid_until=104., coverage_status='qualified_current', truth_role='runtime_qualified_method_result')
        bad = deepcopy(receipt)
        bad['coverage_assessment_ref'] = pin('unassessed-coverage')
        with self.assertRaises(LifecycleRefusal):
            lifecycle.observe_qualified(bad)
        lifecycle.observe_qualified(receipt)
        outcome = lifecycle.publish_receipts(self.context, prior=self.prior)
        self.assertEqual(outcome[0]['publication']['payload']['disposition'], 'absence_qualified')
        self.assertEqual(outcome[0]['publication']['payload']['coverage_assessment_ref'], absent['coverage_assessment_ref'])
        self.assertEqual(len(self.events), 1)

    def test_lost_publication_ack_retries_frozen_event_and_does_not_guess_head(self):
        self.fixture()
        from live_person_lifecycle import LifecycleRefusal
        original_publish = self.publish
        def lose_first(event):
            receipt = original_publish(event)
            return {'status': 'unavailable'} if len(self.events) == 1 else receipt
        self.publish = lose_first
        lifecycle = self.construct()
        with self.assertRaises(LifecycleRefusal):
            lifecycle.publish_observation(self.context, 'observation_gap', prior=self.prior)
        self.now = 102
        result = lifecycle.publish_observation(self.context, 'observation_gap', prior=self.prior)
        self.assertEqual(self.events[0], self.events[1])
        self.assertIsNone(result['publication']['payload']['head']['supersedes'])
        self.assertEqual(result['publication']['payload']['available_at'], 101)

    def test_end_use_cannot_bypass_actual_prior_committed_relation(self):
        self.fixture()
        from live_person_lifecycle import LifecycleRefusal
        negative = dict(method_ref=pin('end-use'), qualification_ref=pin('end-use-qualification'),
                        applicability_ref=pin('end-use-applicability'))
        self.policy['negative_methods'] = [deepcopy(negative)]
        self.worker['negative_methods'] = [deepcopy(negative)]
        self.source['method_refs'].append(negative['method_ref'])
        lifecycle = self.construct()
        receipt = dict(schema='home-qualified-person-lifecycle-receipt-1', version=1, id='end-use-one',
            session_ref=self.scope, configuration_digest='c' * 64, capture_id='capture-two', source_id='oak', source_generation=1,
            target_person_ref=self.prior['person_ref'], target_subject_ref=self.prior['subject_ref'], disposition='use_ended_qualified',
            **negative, coverage_assessment_ref=None, basis_refs=[{**self.context['publication_receipt'], 'component_id': None}],
            window={'start_at': 100.5, 'end_at': 101.}, observed_at=101., available_at=101., valid_until=104.,
            coverage_status='not_applicable', truth_role='runtime_qualified_method_result')
        lifecycle.observe_qualified(receipt)
        with self.assertRaises(LifecycleRefusal):
            lifecycle.publish_observation(self.context, 'use_ended_qualified', prior=self.prior, evidence=receipt)
        self.assertEqual(self.events, [])
        # Only an actual accepted positive publication can supply this basis.
        from journal import digest
        positive_payload = deepcopy(self.context['capture_payload'])
        positive_payload.update(kind='object_interaction', status='supplied', value=True,
            person_ref=deepcopy(self.prior['person_ref']), subject_ref=deepcopy(self.prior['subject_ref']), observed_at=100.)
        positive = {'id': 'positive-one', 'payload': positive_payload}
        ack = dict(status='accepted', id=positive['id'], stream_epoch='journal', cursor=2, producer_id='producer',
                   payload_digest=digest(positive_payload), committed_at=100.)
        lifecycle.remember_interaction(positive, ack)
        receipt['id'] = 'end-use-qualified'
        receipt['basis_refs'] = [{key: ack[key] for key in ('stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest')}
                                 | {'component_id': None}]
        lifecycle.observe_qualified(receipt)
        result = lifecycle.publish_observation(self.context, 'use_ended_qualified', prior=self.prior, evidence=receipt)
        self.assertEqual(result['publication']['payload']['target_subject_ref'], self.prior['subject_ref'])
        self.assertEqual(result['publication']['payload']['disposition'], 'use_ended_qualified')

    def test_ambiguity_source_epoch_and_disabled_qualification_never_preserve_identity(self):
        self.fixture()
        lifecycle = self.construct()
        self.assertEqual(lifecycle.consider(self.context, self.numeric, self.estimate, self.prior, person_count=2)['action'], 'reset')
        self.context['capture_payload']['source_generation'] = 2
        self.assertEqual(lifecycle.consider(self.context, self.numeric, self.estimate, self.prior, person_count=1)['action'], 'reset')
        self.context['capture_payload']['source_generation'] = 1
        self.context['capture_payload']['head']['position']['capture_epoch'] = 'camera-two'
        self.assertEqual(lifecycle.consider(self.context, self.numeric, self.estimate, self.prior, person_count=1)['action'], 'reset')
        self.context['capture_payload']['head']['position']['capture_epoch'] = 'camera-one'
        self.policy['continuity'] = None
        disabled = self.construct()
        self.assertEqual(disabled.consider(self.context, self.numeric, self.estimate, self.prior, person_count=1)['action'], 'reset')


if __name__ == '__main__':
    unittest.main()
