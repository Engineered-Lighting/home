"""Stored findings distinguish evidence causality from live delivery."""
import unittest
from controller import Controller
from store import LabError
from reasoning import score_intervals
import test_paired_runtime as fixtures


class FindingsTests(unittest.TestCase):
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def review(self):
        self.store.save_annotation(self.session['id'], 'review1', {
            'at_ms': 0, 'end_ms': 10000, 'label': 'reading',
            'origin': 'human', 'reviewed': True}, 0, 'owner')

    def output(self, kind, *, activity='reading', **fields):
        self.store.append_event(self.session['id'], kind, {
            'run_id': 'qA', 'arm': 'a', 'mode': 'causal', **fields,
            'prediction': {'time_basis': 'session_relative_seconds',
                'start': 0, 'end': 10, 'available_at': 1000, 'valid_until': 10,
                'activities': [activity], 'confidence': .9}})

    def test_causal_offline_is_not_live_and_jobs_stay_separate(self):
        self.review()
        self.output('analysis.output', job_id='job1')
        self.output('analysis.output', job_id='job2', activity='phone_use')
        rows = Controller(self.store).findings(self.session['id'])['runs']
        self.assertEqual(len(rows), 2)
        by_job = {r['job_id']: r for r in rows}
        self.assertEqual(by_job['job1']['metrics']['exact_match_at_coverage'], 1)
        self.assertEqual(by_job['job2']['metrics']['exact_match_at_coverage'], 0)
        for row in rows:
            self.assertEqual(row['execution'], 'offline')
            self.assertEqual(row['metrics']['coverage'], 1)
            self.assertEqual(row['metrics']['latency_kind'], 'offline_not_live')
            self.assertEqual(row['metrics']['coverage_kind'], 'offline_labeled_time_support')

    def test_live_delivery_gate_and_generation_isolation_remain(self):
        self.review()
        self.output('reasoning.output', mode='live', generation=1)
        self.output('reasoning.output', mode='live', generation=2)
        self.output('analysis.output', mode='diagnostic', job_id='diagnosis')
        rows = Controller(self.store).findings(self.session['id'])['runs']
        self.assertEqual({r['generation'] for r in rows}, {1, 2})
        for row in rows:
            self.assertEqual(row['metrics']['coverage'], 0)
            self.assertEqual(row['metrics']['latency_kind'], 'actual_delivery')
            self.assertEqual(row['metrics']['coverage_kind'], 'delivered_non_abstaining_time')

    def test_no_predictions_is_insufficient_even_with_reviewed_labels(self):
        result = score_intervals([{'start': 0, 'end': 10,
            'activities': ['reading'], 'reviewed': True}], [])
        self.assertEqual(result['status'], 'insufficient_predictions')
        self.assertIsNone(result['exact_match_at_coverage'])

    def test_live_coverage_stops_at_next_experiment_activation(self):
        self.review();sid=self.session['id']
        self.store.append_event(sid,'experiment.activated',{'generation':1,'effective_at':100})
        self.store.append_event(sid,'experiment.activated',{'generation':2,'effective_at':105})
        self.store.append_event(sid,'reasoning.output',{'run_id':'jevA','arm':'a','mode':'live','generation':1,
            'prediction':{'time_basis':'session_relative_seconds','start':0,'end':10,'available_at':0,
                          'valid_until':10,'activities':['reading'],'confidence':.9}})
        row=Controller(self.store).findings(sid)['runs'][0]
        self.assertEqual(row['metrics']['coverage'],.5)

    def test_reviewing_a_saved_session_label_in_place_makes_it_scored(self):
        # The Home label editor adjusts and reviews through the existing
        # annotation save (same id, expected revision); no separate review op.
        sid = self.session['id']
        draft = {'at_ms': 1000, 'end_ms': 9000, 'label': 'phone_use', 'origin': 'human',
                 'reviewed': False, 'time_basis': 'session', 'notes': 'cue schedule'}
        self.store.save_annotation(sid, 'label1', draft, 0, 'owner')
        self.output('analysis.output', job_id='job1')
        controller = Controller(self.store)
        self.assertEqual(controller.findings(sid)['reviewed_intervals'], 0)
        saved = self.store.save_annotation(sid, 'label1', {**draft, 'at_ms': 0, 'end_ms': 10000,
            'label': 'reading', 'reviewed': True}, 1, 'owner')
        self.assertEqual((saved['revision'], saved['at_ms'], saved['end_ms'], saved['label'], saved['reviewed']),
                         (2, 0, 10000, 'reading', True))
        result = controller.findings(sid)
        self.assertEqual(result['reviewed_intervals'], 1)
        self.assertEqual(result['runs'][0]['metrics']['exact_match_at_coverage'], 1)
        # A stale editor cannot overwrite the reviewed version.
        with self.assertRaises(LabError) as stale:
            self.store.save_annotation(sid, 'label1', draft, 1, 'owner')
        self.assertEqual(stale.exception.code, 'revision_conflict')
        self.assertEqual(len(self.store.list_annotations(sid)), 1)

    def test_per_activity_rows_keep_the_session_scoring_definitions(self):
        # Two reviewed labels; one live answer is right and one is wrong at
        # confidence 0.9 (>= 0.8 is confidently wrong, the controller's rule).
        truth = [{'start': 0, 'end': 10, 'activities': ['reading'], 'reviewed': True},
                 {'start': 10, 'end': 20, 'activities': ['phone_use'], 'reviewed': True}]
        predictions = [{'start': 0, 'end': 10, 'available_at': 2, 'valid_until': 10, 'activities': ['reading'], 'confidence': .5},
                       {'start': 10, 'end': 20, 'available_at': 15, 'valid_until': 20, 'activities': ['reading'], 'confidence': .9}]
        result = score_intervals(truth, predictions)
        rows = result['per_activity']
        self.assertEqual(sorted(rows), ['phone_use', 'reading'])
        self.assertEqual((rows['reading']['reviewed_seconds'], rows['reading']['covered_seconds'], rows['reading']['exact_match_at_coverage']), (10, 8, 1))
        self.assertAlmostEqual(rows['reading']['coverage'], .8)
        self.assertEqual((rows['phone_use']['covered_seconds'], rows['phone_use']['exact_match_at_coverage'], rows['phone_use']['false_confident_seconds']), (5, 0, 5))
        # The per-activity rows add up to the session totals.
        self.assertEqual(sum(r['covered_seconds'] for r in rows.values()), result['covered_seconds'])
        self.assertEqual(result['false_confident_seconds'], 5)
        self.assertEqual(score_intervals([], [])['per_activity'], {})

    def test_findings_report_the_recorded_lever_or_say_it_was_not_recorded(self):
        self.review(); sid = self.session['id']
        self.store.append_event(sid, 'experiment.activated', {'generation': 1, 'effective_at': 100, 'experiment_id': 'placement-best'})
        lever = {'paths': ['pose'], 'note': 'Pose model only: RTMPose-m (A) vs none (B).'}
        self.store.append_event(sid, 'experiment.activated', {'generation': 2, 'effective_at': 105, 'experiment_id': 'pose-added',
                                                              'lever': lever, 'baseline_sha256': 'b'*64, 'source_policy': {}})
        levers = Controller(self.store).findings(sid)['levers']
        self.assertEqual(levers, [{'generation': 1, 'experiment_id': 'placement-best', 'lever': None, 'baseline_sha256': None},
                                  {'generation': 2, 'experiment_id': 'pose-added', 'lever': lever, 'baseline_sha256': 'b'*64}])

    def test_session_list_carries_label_and_cue_counts(self):
        sid = self.session['id']
        self.store.transition(sid, 'stopped')
        other, _ = self.store.create_session('den', 'detection', 'delivery canary', 'request2')
        save = lambda aid, **kw: self.store.save_annotation(sid, aid, {'at_ms': 0, 'end_ms': 1000, 'label': 'reading', 'origin': 'human', **kw}, 0, 'owner')
        save('a', reviewed=True)
        save('b', reviewed=False)
        save('c', reviewed=True, origin='model')
        self.store.save_annotation(sid, 'd', {'at_ms': 0, 'end_ms': 1000, 'label': 'reading', 'origin': 'human', 'reviewed': True}, 0, 'owner')
        self.store.save_annotation(sid, 'd', {}, 1, 'owner', delete=True)
        self.store.append_event(sid, 'bookmark', {'at_ms': 6000, 'label': '1 reading'})
        self.store.append_event(sid, 'bookmark', {'at_ms': 9000, 'label': '2 phone'})
        listed = {s['id']: s for s in self.store.list_sessions()}
        self.assertEqual(listed[sid]['label_summary'], {'total': 3, 'reviewed': 2, 'human': 2, 'session_clock': 3, 'other_clock': 0, 'scorable': 1})
        self.assertEqual(listed[sid]['bookmark_count'], 2)
        self.assertEqual(listed[other['id']]['label_summary'], {'total': 0, 'reviewed': 0, 'human': 0, 'session_clock': 0, 'other_clock': 0, 'scorable': 0})
        self.assertEqual(listed[other['id']]['bookmark_count'], 0)
        # The single-session read is unchanged.
        self.assertNotIn('label_summary', self.store.get_session(sid))

    def test_malformed_prediction_does_not_make_findings_scored(self):
        self.review()
        self.store.append_event(self.session['id'], 'analysis.output', {
            'mode': 'causal', 'job_id': 'job1',
            'prediction': {'time_basis': 'session_relative_seconds'}})
        result = Controller(self.store).findings(self.session['id'])
        self.assertEqual(result['status'], 'insufficient_reviewed_data')
        self.assertEqual(result['runs'][0]['metrics']['status'], 'insufficient_predictions')


if __name__ == '__main__':
    unittest.main()
