"""Actual factory assessment, with authored camera/model external I/O only."""
from copy import deepcopy
import unittest

import live_capture_fixtures as capture
import live_spatial_fixtures as spatial
from journal import digest
from test_live_spatial import LiveSpatialTests
import test_live_interaction_optional as optional_tests


class AssessmentFactoryTests(unittest.TestCase):
    setUp = LiveSpatialTests.setUp
    prepare = LiveSpatialTests.prepare
    camera_pair = LiveSpatialTests.camera_pair
    control_clock = LiveSpatialTests.control_clock
    construct = LiveSpatialTests.construct
    snapshot = LiveSpatialTests.snapshot

    def test_missing_estimator_qualification_retains_candidate_without_operational_person(self):
        from live_assessment import AssessmentRecorder
        self.prepare()
        recorder = AssessmentRecorder({'schema': 'home-live-assessment-policy-1', 'version': 1,
            'id': 'authored-assessment', 'max_records': 24, 'max_bytes': 8 * 1024**2})
        self.config.update(schema='home-production-acquisition-4', version=4,
            interaction_method_ref=None, assessment_ref=recorder.policy_ref)
        # Version four supports spatial assessment before the optional interaction stage.
        self.local_grant['operations'].append('assessment')
        self.local_grant['configuration_digest'] = digest(self.config)
        source = self.live_config['manifest']['sources'][capture.SOURCE]
        source['part_policies']['body'] = None
        self.live_config['manifest_ref']['digest'] = digest(self.live_config['manifest'])
        self.journal_grant['live_bindings'][0]['manifest_ref'] = deepcopy(self.live_config['manifest_ref'])
        graph = self.construct(assessment_recorder=recorder)
        cycle = graph.step()
        self.assertEqual(cycle['spatial'], 'completed', cycle)
        report = recorder.export()
        self.assertEqual(len(report['records']), 1)
        candidate = report['records'][0]
        self.assertEqual(candidate['truth_role'], 'runtime_model_estimate')
        self.assertTrue(candidate['candidate_only'])
        self.assertEqual(candidate['person_ref'], None)
        self.assertEqual(candidate['parts']['work']['shape']['center_m'], spatial.WORK_CENTER)
        self.assertEqual(candidate['observed_at'], capture.NOW)
        self.assertEqual(candidate['original_receipt_refs'][0]['observed_at'], capture.NOW)
        self.assertEqual(candidate['sensor_roots'], ['camera:synthetic-oak'])
        self.assertFalse(any(row['payload'].get('status') == 'supplied' for row in self.snapshot()['items']))
        self.assertFalse(report['operational_geometry'])
        self.assertEqual(candidate['pipeline_times']['capture_received_at'], capture.NOW + .2)
        self.assertEqual(candidate['pipeline_times']['capture_committed_at'], capture.NOW + .2)
        self.assertEqual(candidate['pipeline_times']['detector_started_at'], capture.NOW + .2)


class AssessmentOptionalReceiptFactoryTests(unittest.TestCase):
    setUp = optional_tests.OptionalReceiptIntegrationTests.setUp
    prepare = optional_tests.OptionalReceiptIntegrationTests.prepare
    camera_pair = optional_tests.OptionalReceiptIntegrationTests.camera_pair
    control_clock = optional_tests.OptionalReceiptIntegrationTests.control_clock
    construct = optional_tests.OptionalReceiptIntegrationTests.construct
    snapshot = optional_tests.OptionalReceiptIntegrationTests.snapshot
    advance = optional_tests.OptionalReceiptIntegrationTests.advance
    facts = optional_tests.OptionalReceiptIntegrationTests.facts
    original = optional_tests.OptionalReceiptIntegrationTests.original
    collect = optional_tests.OptionalReceiptIntegrationTests.collect
    first_local = optional_tests.OptionalReceiptIntegrationTests.first_local
    with_terminal_before_interaction = optional_tests.OptionalReceiptIntegrationTests.with_terminal_before_interaction

    def test_actual_factory_records_la_absence_then_exact_frame_admission_and_original_fact_commits(self):
        from live_assessment import AssessmentRecorder
        self.prepare()
        recorder = AssessmentRecorder({'schema': 'home-live-assessment-policy-1', 'version': 1,
            'id': 'authored-optional-assessment', 'max_records': 24, 'max_bytes': 8 * 1024**2})
        self.config.update(schema='home-production-acquisition-4', version=4, assessment_ref=recorder.policy_ref)
        self.local_grant['operations'].append('assessment')
        self.local_grant['configuration_digest'] = digest(self.config)
        graph = self.construct(interaction_sources=self.sources, assessment_recorder=recorder)
        self.assertEqual(graph.step()['interaction'], 'completed')
        self.collect()
        self.advance()
        self.with_terminal_before_interaction()
        self.assertEqual(graph.step()['interaction'], 'completed')
        result = recorder.export()
        self.assertTrue(result['optional_la_configured'])
        self.assertEqual(result['semantic_checks'][0]['status'], 'unavailable')
        admitted = [row for row in result['semantic_checks'] if row['admitted']]
        self.assertTrue(admitted)
        self.assertTrue(all(row['exact_frame'] for row in admitted))
        self.assertEqual(admitted[0]['available_at'], self.last_receipt['first_received_at'])
        facts = [row for row in result['interaction_publications'] if row['predicate'] == 'object_interaction']
        self.assertEqual(len(facts), 2)
        self.assertTrue(all(row['value'] is True for row in facts))
        self.assertTrue(all(row['receipt']['committed_at'] >= row['available_at'] for row in facts))
        self.assertFalse(any('receipt' in row for row in result['semantic_checks']))


if __name__ == '__main__':
    unittest.main()
