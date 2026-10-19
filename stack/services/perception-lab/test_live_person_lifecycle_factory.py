"""Actual acquisition5 -> journal with authored camera/model I/O only."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'home-context'))
from journal import digest
from live_assessment import AssessmentRecorder
import live_person_lifecycle_fixtures as lifecycle_fixtures
import test_live_interaction as interaction_tests
import test_live_supervised_composition as native_tests


class PersonLifecycleFactoryTests(unittest.TestCase):
    setUp = interaction_tests.LiveInteractionTests.setUp
    camera_pair = interaction_tests.LiveInteractionTests.camera_pair
    control_clock = interaction_tests.LiveInteractionTests.control_clock
    snapshot = interaction_tests.LiveInteractionTests.snapshot
    advance = interaction_tests.LiveInteractionTests.advance
    construct = interaction_tests.LiveInteractionTests.construct

    def prepare(self, *, qualified_methods=False):
        interaction_tests.LiveInteractionTests.prepare(self)
        self.live_config, self.journal_grant, self.lifecycle_policy = lifecycle_fixtures.commission(
            self.live_config, self.journal_grant, capture_method=self.method, spatial_method=self.spatial_method,
            interaction_method=self.interaction_method, model_refs=self.refs, qualified_methods=qualified_methods)
        self.recorder = AssessmentRecorder(dict(schema='home-live-assessment-policy-1', version=1,
            id='authored-lifecycle-assessment', max_records=24, max_bytes=8 * 1024**2))
        self.config.update(schema='home-production-acquisition-5', version=5, assessment_ref=self.recorder.policy_ref,
            lifecycle_ref={'id': self.lifecycle_policy['id'], 'digest': digest(self.lifecycle_policy)})
        self.config['limits']['max_cycles'] = 8
        self.local_grant['operations'] += ['assessment', 'lifecycle']
        self.local_grant['configuration_digest'] = digest(self.config)

    def geometries(self):
        return [row['payload'] for row in self.snapshot()['items'] if row['payload'].get('method_ref') == self.spatial_method]

    def lifecycle_rows(self):
        return [row['payload'] for row in self.snapshot()['items'] if row['payload_type'] == 'home-live-person-lifecycle-1']

    def qualified_receipt_during_detector(self, disposition, target, *, basis_refs=()):
        """The sole new double is an external qualified-method terminal receipt.

        Capture identity and positive basis are read from actual journal output;
        no completed spatial/interaction event or tracker result is injected.
        """
        predict = self.detector.model.predict
        def produce(**kwargs):
            result = predict(**kwargs)
            raw = next(row['payload'] for row in self.snapshot()['items']
                       if row['payload_type'] == 'home-live-geometry-1' and row['payload'].get('method_ref') == self.method)
            negative = disposition == 'use_ended_qualified'
            method = self.lifecycle_policy['negative_methods' if negative else 'absence_methods'][0]
            receipt = dict(schema='home-qualified-person-lifecycle-receipt-1', version=1,
                id='authored-qualified-' + str(raw['head']['position']['capture_seq']), session_ref=self.config['session_ref'],
                configuration_digest=digest(self.config), capture_id=raw['id'], source_id=self.config['source_id'],
                source_generation=raw['source_generation'], target_person_ref=deepcopy(target['person_ref']),
                target_subject_ref=deepcopy(target['subject_ref']) if negative else None,
                disposition=disposition, **deepcopy(method),
                basis_refs=deepcopy(list(basis_refs)), window={'start_at': raw['observed_at'], 'end_at': raw['observed_at']},
                observed_at=raw['observed_at'], available_at=self.now, valid_until=raw['valid_until'],
                coverage_status='not_applicable' if negative else 'qualified_current', truth_role='runtime_qualified_method_result')
            if negative:
                receipt['coverage_assessment_ref'] = None
            self.graph.lifecycle.observe_qualified(receipt)
            self.detector.model.predict = predict
            return result
        self.detector.model.predict = produce

    def test_gap_and_qualified_return_keep_exact_epoch_without_refreshing_geometry_or_source(self):
        self.prepare()
        graph = self.construct(assessment_recorder=self.recorder, lifecycle_policy=self.lifecycle_policy)
        first = graph.step()
        self.assertEqual(first['interaction'], 'completed', first)
        original = deepcopy(self.geometries()[0])
        for index in (5, 6, 11, 12):
            self.joint_scores[index] = 0
        self.advance()
        gap = graph.step()
        self.assertEqual(gap['status'], 'observed', gap)
        row, = self.lifecycle_rows()
        self.assertEqual(row['disposition'], 'observation_gap')
        self.assertEqual(row['target_person_ref'], original['person_ref'])
        self.assertEqual(row['source_generation'], original['source_generation'])
        retained, = self.geometries()
        self.assertEqual(retained, original)
        self.joint_scores[:] = [.9] * 17
        self.advance()
        returning = graph.step()
        self.assertEqual(returning['interaction'], 'completed', returning)
        current, = self.geometries()
        self.assertEqual(current['person_ref'], original['person_ref'])
        self.assertEqual(current['subject_ref'], original['subject_ref'])
        self.assertGreater(current['observed_at'], original['observed_at'])
        self.assertEqual(self.lifecycle_rows()[0]['disposition'], 'continuity_qualified')

    def test_zero_detections_are_gap_then_reset_and_never_qualified_absence(self):
        self.prepare()
        graph = self.construct(assessment_recorder=self.recorder, lifecycle_policy=self.lifecycle_policy)
        self.assertEqual(graph.step()['interaction'], 'completed')
        original = deepcopy(self.geometries()[0])
        boxes = deepcopy(self.boxes)
        self.boxes[:] = []
        self.advance()
        self.assertEqual(graph.step()['status'], 'observed')
        self.assertEqual(self.lifecycle_rows()[0]['disposition'], 'observation_gap')
        for _ in range(5):
            self.advance()
        self.assertEqual(graph.step()['status'], 'observed')
        reset, = self.lifecycle_rows()
        self.assertEqual(reset['disposition'], 'association_reset')
        self.assertEqual(reset['target_person_ref'], original['person_ref'])
        self.assertEqual(reset['source_generation'], original['source_generation'])
        self.boxes[:] = boxes
        self.advance()
        self.assertEqual(graph.step()['interaction'], 'completed')
        current = max(self.geometries(), key=lambda row: row['observed_at'])
        self.assertNotEqual(current['person_ref'], original['person_ref'])
        self.assertNotEqual(current['subject_ref'], original['subject_ref'])
        self.assertFalse(any(row['disposition'] in {'absence_qualified', 'departure_qualified'} for row in self.lifecycle_rows()))

    def test_extra_occupant_has_separate_unattributed_reset_and_cannot_inherit_identity(self):
        self.prepare()
        graph = self.construct(assessment_recorder=self.recorder, lifecycle_policy=self.lifecycle_policy)
        self.assertEqual(graph.step()['interaction'], 'completed')
        original = deepcopy(self.geometries()[0])
        self.boxes.append([20, 30, 200, 350])
        self.advance()
        cycle = graph.step()
        self.assertEqual(cycle['status'], 'observed', cycle)
        resets = self.lifecycle_rows()
        self.assertEqual(len(resets), 2)
        self.assertTrue(all(row['disposition'] == 'association_reset' for row in resets))
        self.assertTrue(any(row['target_person_ref'] == original['person_ref'] for row in resets))
        self.assertTrue(any(row['target_person_ref'] is None for row in resets))

    def test_initial_missing_hands_preserves_person_without_inventing_a_working_subject(self):
        self.prepare()
        self.joint_scores[9] = self.joint_scores[10] = 0
        graph = self.construct(assessment_recorder=self.recorder, lifecycle_policy=self.lifecycle_policy)
        cycle = graph.step()
        self.assertEqual(cycle['status'], 'observed', cycle)
        first, = self.geometries()
        self.assertIsNone(first['subject_ref'])
        self.assertEqual(first['parts']['work']['status'], 'unobservable')
        self.joint_scores[:] = [.9] * 17
        self.advance()
        self.assertEqual(graph.step()['interaction'], 'completed')
        current = max(self.geometries(), key=lambda row: row['observed_at'])
        self.assertEqual(current['person_ref'], first['person_ref'])
        self.assertIsNotNone(current['subject_ref'])
        self.assertEqual(self.lifecycle_rows()[0]['target_subject_ref'], current['subject_ref'])

    def test_source_loss_never_qualifies_absence_for_an_old_generation(self):
        self.prepare(qualified_methods=True)
        graph = self.construct(assessment_recorder=self.recorder, lifecycle_policy=self.lifecycle_policy)
        self.assertEqual(graph.step()['interaction'], 'completed')
        target = deepcopy(self.geometries()[0])
        self.camera_down = True
        self.advance()
        self.assertEqual(graph.step()['status'], 'failed')
        self.assertFalse(self.snapshot()['items'])
        self.camera_down = False
        self.advance()
        self.qualified_receipt_during_detector('absence_qualified', target)
        cycle = graph.step()
        self.assertEqual(cycle['status'], 'failed', cycle)
        self.assertEqual(cycle['reason'], 'cross_generation_lifecycle_method_unavailable', cycle)
        self.assertFalse(any(row['disposition'] in {'absence_qualified', 'departure_qualified'} for row in self.lifecycle_rows()))

    def test_nonholding_is_unknown_until_a_qualified_exact_relation_end_receipt_arrives(self):
        self.prepare(qualified_methods=True)
        graph = self.construct(assessment_recorder=self.recorder, lifecycle_policy=self.lifecycle_policy)
        self.assertEqual(graph.step()['interaction'], 'completed')
        positive = next(row for row in self.snapshot()['items'] if row['payload'].get('kind') == 'object_interaction')
        target = deepcopy(positive['payload'])
        basis = {key: positive[key] for key in ('stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest')} | {'component_id': None}
        self.materials[:] = [('book', [100, 190, 140, 210])]
        self.advance()
        self.assertEqual(graph.step()['interaction'], 'completed')
        self.assertFalse(any(row['disposition'] == 'use_ended_qualified' for row in self.lifecycle_rows()))
        self.advance()
        self.qualified_receipt_during_detector('use_ended_qualified', target, basis_refs=[basis])
        cycle = graph.step()
        self.assertEqual(cycle['interaction'], 'completed', cycle)
        ended = [row for row in self.lifecycle_rows() if row['disposition'] == 'use_ended_qualified']
        self.assertEqual(len(ended), 1, [(row['disposition'], row['method_ref']['id'], row['observed_at']) for row in self.lifecycle_rows()])
        self.assertEqual(ended[0]['target_person_ref'], target['person_ref'])
        self.assertEqual(ended[0]['target_subject_ref'], target['subject_ref'])
        self.assertIn(basis, ended[0]['basis_refs'])

    def test_explicit_coverage_receipt_can_resolve_exact_old_person_without_inheriting_new_epoch(self):
        self.prepare(qualified_methods=True)
        graph = self.construct(assessment_recorder=self.recorder, lifecycle_policy=self.lifecycle_policy)
        self.assertEqual(graph.step()['interaction'], 'completed')
        target = deepcopy(self.geometries()[0])
        boxes = deepcopy(self.boxes)
        self.boxes[:] = []
        for _ in range(5):
            self.advance()
        reset = graph.step()
        self.assertEqual(reset['status'], 'observed', reset)
        self.assertEqual(self.lifecycle_rows()[0]['disposition'], 'association_reset')
        self.advance()
        self.qualified_receipt_during_detector('absence_qualified', target)
        cycle = graph.step()
        self.assertEqual(cycle['status'], 'observed', cycle)
        absent, = [row for row in self.lifecycle_rows() if row['disposition'] == 'absence_qualified']
        self.assertEqual(absent['target_person_ref'], target['person_ref'])
        self.assertIsNone(absent['target_subject_ref'])
        self.boxes[:] = boxes
        self.advance()
        self.assertEqual(graph.step()['interaction'], 'completed')
        current = max(self.geometries(), key=lambda row: row['observed_at'])
        self.assertNotEqual(current['person_ref'], target['person_ref'])


class NativeLifecycleCompositionTests(unittest.TestCase):
    setUp = native_tests.ManagedProducerCompositionTests.setUp
    prepare = PersonLifecycleFactoryTests.prepare
    camera_pair = PersonLifecycleFactoryTests.camera_pair
    control_clock = PersonLifecycleFactoryTests.control_clock
    snapshot = PersonLifecycleFactoryTests.snapshot
    composition = native_tests.ManagedProducerCompositionTests.composition

    def test_fixed_native_builder_passes_pinned_lifecycle_policy_before_authored_sdk_load(self):
        composition = self.composition()
        self.assertEqual(self.loads, [])
        self.graph = graph = composition.create()
        def cleanup():
            withdrawal = graph.withdraw()
            if withdrawal['input_cursor'] is not None:
                graph.finalize(drain_receipt=dict(schema='home-acquisition-drain-1', version=1,
                    session_ref=self.config['session_ref'], configuration_digest=graph.configuration_digest,
                    withdrawal_receipt=withdrawal['barrier_receipt'], recorder_status='incomplete',
                    worker_status='unconfirmed', terminal_cursor=withdrawal['input_cursor']))
        self.addCleanup(cleanup)
        cycle = graph.step()
        self.assertEqual(cycle['interaction'], 'completed', cycle)
        self.assertEqual(self.loads, ['detector', 'pose'])
        self.assertTrue(any(row['payload_type'] == 'home-live-person-lifecycle-1' for row in self.snapshot()['items']))
