"""Independent literal error/containment and temporal denominator examples."""
from copy import deepcopy
import unittest


class IndependentAssessmentScoringTests(unittest.TestCase):
    def fixture(self):
        binding = {'session_ref': {'site_id': 'fictional', 'room_id': 'den', 'session_id': 'probe',
                                  'domain_epoch': 'one'}, 'source_id': 'oak', 'configuration_digest': 'a' * 64}
        shape = {'kind': 'aabb', 'center_m': [1, 0, 0], 'half_extents_m': [.5, .5, .5]}
        rows = {'schema': 'home-live-assessment-measurements-1', 'version': 1, 'binding': binding,
                'complete': True, 'publications': [], 'records': [{
                    'schema': 'home-live-assessment-candidate-1', 'version': 1, 'id': 'one', 'capture_id': 'one',
                    'candidate_only': True, 'truth_role': 'runtime_model_estimate', 'observed_at': 100,
                    'available_at': 100, 'valid_until': 102,
                    'parts': {'work': {'status': 'candidate', 'shape': shape}}, 'interaction': None}]}
        reference = deepcopy(shape)
        reference['center_m'] = [0, 0, 0]
        refs = {'schema': 'home-live-assessment-references-1', 'version': 1, 'binding': binding,
                'truth_role': 'independent_evaluation_reference', 'frame': 'z_up_metric_floor0', 'unit': 'm',
                'reference_protocol_ref': {'id': 'blind-survey', 'digest': 'b' * 64},
                'requirements': {}, 'windows': [{'id': 'hold-one', 'start_at': 100, 'end_at': 104,
                    'reference_uncertainty_m': 0, 'activity': 'focused',
                    'parts': {'work': {'status': 'available', 'shape': reference}}}]}
        return rows, refs

    def test_shifted_and_missing_estimates_keep_complete_reference_denominator(self):
        from live_assessment_scoring import score_assessment
        rows, refs = self.fixture()
        report = score_assessment(rows, refs)
        task = report['containment']['task']
        self.assertEqual(task['eligible_s'], 4)
        self.assertEqual(task['undercovered_s'], 4)
        self.assertEqual(task['missing_prediction_s'], 2)
        self.assertEqual(report['work_center_error_m']['p95'], 1)
        self.assertEqual(report['work_center_error_m']['eligible_s'], 2)
        self.assertEqual(report['complete_protection']['qualified_available_s'], 0)
        self.assertEqual(report['decision'], 'block')
        self.assertFalse(report['operational_geometry'])

    def test_reference_uncertainty_is_expanded_instead_of_subtracted_from_error(self):
        from live_assessment_scoring import score_assessment
        rows, refs = self.fixture()
        rows['records'][0]['parts']['work']['shape']['center_m'] = [0, 0, 0]
        refs['windows'][0]['end_at'] = 102
        refs['windows'][0]['reference_uncertainty_m'] = .01
        report = score_assessment(rows, refs)
        self.assertEqual(report['work_center_error_m']['p95'], 0)
        self.assertEqual(report['containment']['task']['undercovered_s'], 2)

    def test_late_result_cannot_cover_before_arrival_or_replace_newer_observation(self):
        from live_assessment_scoring import score_assessment
        rows, refs = self.fixture()
        newer = deepcopy(rows['records'][0])
        newer.update(id='two', capture_id='two', observed_at=101, available_at=101, valid_until=104)
        newer['parts']['work']['shape']['center_m'] = [0, 0, 0]
        rows['records'][0].update(observed_at=100, available_at=102, valid_until=104)
        rows['records'].append(newer)
        report = score_assessment(rows, refs)
        self.assertEqual(report['containment']['task']['missing_prediction_s'], 1)
        self.assertEqual(report['containment']['task']['contained_s'], 3)
        self.assertEqual(report['work_center_error_m']['p95'], 0)

    def test_qualified_availability_begins_at_commit_and_ends_at_required_part_expiry(self):
        from live_assessment_scoring import score_assessment
        rows, refs = self.fixture()
        rows['records'][0]['valid_until'] = 104
        shape = deepcopy(rows['records'][0]['parts']['work']['shape'])
        parts = ('body', 'head', 'hand_left', 'hand_right', 'protection')
        for name in parts:
            rows['records'][0]['parts'][name] = {'status': 'candidate', 'shape': shape}
        rows['publications'] = [{'capture_id': 'one', 'status': 'supplied', 'available_at': 100,
            'valid_until': 104, 'receipt': {'committed_at': 101},
            'parts': {name: {'status': 'supplied', 'effective_valid_until': 102} for name in parts}}]
        report = score_assessment(rows, refs)
        self.assertEqual(report['complete_protection']['candidate_available_s'], 4)
        self.assertEqual(report['complete_protection']['qualified_available_s'], 1)

    def test_newer_unavailable_head_cannot_resurrect_older_still_leased_candidate(self):
        from live_assessment_scoring import score_assessment
        rows, refs = self.fixture()
        rows['records'][0]['valid_until'] = 104
        rows['records'][0]['parts']['work']['shape']['center_m'] = [0, 0, 0]
        newer = deepcopy(rows['records'][0])
        newer.update(id='two', capture_id='two', observed_at=101, available_at=101, valid_until=102,
                     parts={'work': {'status': 'unobservable'}})
        rows['records'].append(newer)
        report = score_assessment(rows, refs)
        self.assertEqual(report['containment']['task']['contained_s'], 1)
        self.assertEqual(report['containment']['task']['missing_prediction_s'], 3)

    def test_every_part_reports_envelope_error_and_missing_denominators(self):
        from live_assessment_scoring import score_assessment, PARTS
        rows, refs = self.fixture()
        shape = deepcopy(refs['windows'][0]['parts']['work']['shape'])
        for index, name in enumerate(PARTS):
            refs['windows'][0]['parts'][name] = {'status': 'available', 'shape': deepcopy(shape)}
            prediction = deepcopy(shape)
            prediction['center_m'][0] = index + 1
            rows['records'][0]['parts'][name] = {'status': 'candidate', 'shape': prediction}
        report = score_assessment(rows, refs)
        for index, name in enumerate(PARTS):
            part = report['part_errors_m'][name]
            self.assertEqual(part['envelope_center']['p95'], index + 1)
            self.assertEqual(part['envelope_boundary_max']['p95'], index + 1)
            self.assertEqual(part['reference_eligible_s'], 4)
            self.assertEqual(part['missing_prediction_s'], 2)
            self.assertEqual(part['envelope_center']['eligible_s'], 2)

    def test_complete_service_gaps_runs_and_lease_use_actual_commit(self):
        from live_assessment_scoring import score_assessment, PARTS
        rows, refs = self.fixture()
        shape = rows['records'][0]['parts']['work']['shape']
        rows['records'][0].update(available_at=100.5, valid_until=104)
        for name in PARTS:
            rows['records'][0]['parts'][name] = {'status': 'candidate', 'shape': shape}
        rows['publications'] = [{'capture_id': 'one', 'status': 'supplied', 'available_at': 101,
            'valid_until': 104, 'receipt': {'committed_at': 101.5},
            'parts': {name: {'status': 'supplied', 'effective_valid_until': 102} for name in PARTS}}]
        report = score_assessment(rows, refs)
        complete = report['complete_protection']
        self.assertEqual(complete['candidate_longest_service_s'], 3.5)
        self.assertEqual(complete['candidate_longest_gap_s'], .5)
        self.assertEqual(complete['qualified_longest_service_s'], .5)
        self.assertEqual(complete['qualified_longest_gap_s'], 2)
        self.assertEqual(report['usable_lease_s']['candidate_at_available']['p95'], 3.5)
        self.assertEqual(report['usable_lease_s']['complete_protection_at_commit']['p95'], .5)

    def test_stage_timings_use_recorded_clocks_and_missing_downstream_blocks_overall(self):
        from live_assessment_scoring import score_assessment
        rows, refs = self.fixture()
        rows['records'][0].update(available_at=101, detector_available_at=100.5, pose_available_at=100.8,
            pipeline_times={'capture_started_at': 100, 'capture_received_at': 100.1,
                'capture_available_at': 100.2, 'capture_committed_at': 100.25,
                'detector_started_at': 100.3, 'pose_started_at': 100.6})
        rows['publications'] = [{'capture_id': 'one', 'status': 'unobservable', 'available_at': 101.1,
            'valid_until': 102, 'receipt': {'committed_at': 101.3}, 'parts': {}}]
        report = score_assessment(rows, refs)
        timings = report['pipeline_timings_s']
        self.assertAlmostEqual(timings['capture']['p95'], .2)
        self.assertAlmostEqual(timings['detector_inference']['p95'], .2)
        self.assertAlmostEqual(timings['pose_inference']['p95'], .2)
        self.assertAlmostEqual(timings['spatial_publication_to_commit']['p95'], .2)
        self.assertAlmostEqual(timings['observation_to_spatial_commit']['p95'], 1.3)
        self.assertEqual(timings['observation_to_display']['unavailable_records'], 1)
        self.assertEqual(timings['observation_to_worker_commit']['status'], 'unavailable')
        self.assertEqual(report['overall_readiness']['decision'], 'block')
        self.assertIn('display_measurements_unavailable', report['overall_readiness']['blockers'])
        self.assertEqual(report['decision_scope'], 'held_pose_estimator_assessment_only')

    def test_exact_frame_la_yield_gaps_and_first_support_do_not_invent_engagement(self):
        from live_assessment_scoring import score_assessment
        rows, refs = self.fixture()
        rows['records'][0].update(available_at=101, valid_until=104, interaction={'status': 'supported'})
        rows['optional_la_configured'] = True
        rows['semantic_checks'] = [{'capture_id': 'one', 'checked_at': 101.5,
            'status': 'fresh_proposal', 'reason': 'exact_current_capture_receipt',
            'exact_frame': True, 'admitted': True, 'observed_at': 100,
            'available_at': 101.25, 'valid_until': 102.5}]
        rows['interaction_publications'] = [{'capture_id': 'one', 'predicate': 'object_interaction',
            'status': 'supplied', 'value': True, 'available_at': 101.5, 'effective_valid_until': 103,
            'receipt': {'committed_at': 102}}]
        report = score_assessment(rows, refs)
        la = report['optional_la']
        self.assertEqual(la['checked_capture_count'], 1)
        self.assertEqual(la['exact_frame_yield_fraction'], 1)
        self.assertEqual(la['admitted_s'], 1)
        self.assertEqual(la['longest_unavailable_gap_s'], 1.5)
        self.assertEqual(report['time_to_support_s']['candidate_holding']['p95'], 1)
        self.assertEqual(report['time_to_support_s']['published_object_interaction']['p95'], 2)
        self.assertEqual(report['time_to_support_s']['reducer_engagement']['status'], 'unavailable')
        self.assertEqual(report['pipeline_timings_s']['observation_to_object_interaction_commit']['p95'], 2)
        self.assertEqual(report['pipeline_timings_s']['observation_to_la_available']['p95'], 1.25)
        self.assertEqual(report['pipeline_timings_s']['la_available_to_admission']['p95'], .25)
        self.assertFalse(any('worker' in reason or 'display' in reason
                             for reason in report['producer_feasibility']['blockers']))
        rows.pop('semantic_checks')
        rows['optional_la_configured'] = False
        no_la = score_assessment(rows, refs)['optional_la']
        self.assertEqual(no_la['status'], 'unavailable')
        self.assertIsNone(no_la['exact_frame_yield_fraction'])
        self.assertEqual(no_la['unavailable_s'], 4)
        rows['optional_la_configured'] = True
        rows['semantic_checks'] = [{'capture_id': 'one', 'checked_at': 101,
            'status': 'unavailable', 'reason': 'la_receipt_unavailable', 'exact_frame': False,
            'admitted': False, 'observed_at': None, 'available_at': None, 'valid_until': None}]
        no_receipt = score_assessment(rows, refs)['optional_la']
        self.assertEqual(no_receipt['status'], 'unavailable')
        self.assertEqual(no_receipt['exact_frame_yield_fraction'], 0)
        self.assertEqual(no_receipt['checked_capture_count'], 1)

    def test_unknown_reference_gaps_do_not_join_disjoint_service_runs(self):
        from live_assessment_scoring import score_assessment
        rows, refs = self.fixture()
        refs['windows'][0]['end_at'] = 101
        second = deepcopy(refs['windows'][0])
        second.update(id='hold-two', start_at=200, end_at=201)
        refs['windows'].append(second)
        report = score_assessment(rows, refs)
        self.assertEqual(report['longest_candidate_gap_s']['head'], 1)


if __name__ == '__main__':
    unittest.main()
