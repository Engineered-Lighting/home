"""Actual fixed factory/scheduler/queue/Stop with authored external I/O only."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('queued_person_fixture', ROOT / 'tools/fixtures/den-person-jev-runtime.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class LivePersonReasoningTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.Fixture()

    def tearDown(self):
        self.f.close()

    def events(self, kind):
        return [e for e in self.f.store.list_events(self.f.session['id']) if e['kind'] == kind]

    def test_actual_factory_single_batch_two_independent_judgments_no_legacy_owner(self):
        state = self.f.step([('desk', 20, True), ('sofa', 350, True)], label='two')
        self.assertEqual(state['jev_calls'], 1)
        self.assertEqual(len(state['jev_outcome']['request_ids']), 1)
        output = self.events('reasoning.person_output')[0]['payload']
        self.assertEqual(output['arm'], 'b')
        self.assertEqual([s['judgment']['activity'] for s in output['subjects']], ['computer_work', 'reading'])
        self.assertFalse(self.events('reasoning.output'))
        self.assertFalse(self.events('reasoning.smoothed'))
        self.assertFalse(self.events('reasoning.timeline'))
        packet = state['live']['arms']['B']['persons2d']['judgments']
        self.assertEqual([s['judgment']['activity'] for s in packet['subjects']], ['computer_work', 'reading'])
        self.assertFalse(packet['engagement_authority'])
        self.assertFalse(packet['physical_permission'])
        request = self.events('reasoning.request')[0]['payload']
        self.assertEqual(request['cutoff'], output['provenance']['cutoff'])
        self.assertEqual(request['deadline'], output['provenance']['deadline'])
        self.assertEqual(request['person_context'], output['person_context'])

    def test_unknown_subject_is_vetoed_independently_after_input_order_change(self):
        self.f.step([('desk', 20, True), ('sofa', 350, True)], label='two')
        state = self.f.step([('sofa-new', 342, False), ('desk-new', 28, True)], label='one_unknown', qwen_mode='one_unknown')
        people = state['live']['arms']['B']['persons2d']
        facts = {s['track_ref']: s for s in people['semantics']['subjects']}
        judged = people['judgments']['subjects']
        available = [s for s in judged if s['status'] == 'available']
        unknown = [s for s in judged if s['status'] == 'unknown']
        self.assertEqual(len(available), 1)
        self.assertEqual(available[0]['judgment']['activity'], 'computer_work')
        self.assertEqual(len(unknown), 1)
        self.assertIsNone(unknown[0]['judgment'])
        self.assertEqual(facts[unknown[0]['track_ref']]['facts']['activity_hypothesis'], 'unclear')
        self.assertFalse(state['duplicate_outcome']['request_ids'])
        self.assertEqual(len(self.f.calls), 2)

    def test_foreign_answer_withholds_judgment_and_keeps_current_facts_and_geometry(self):
        state = self.f.step([('desk', 20, True), ('sofa', 350, True)], label='foreign', jev_mode='foreign_reply')
        people = state['live']['arms']['B']['persons2d']
        self.assertEqual(len(people['semantics']['subjects']), 2)
        self.assertNotIn('judgments', people)
        self.assertFalse(self.events('reasoning.person_output'))
        self.assertEqual(len(self.f.calls), 1)

    def test_expiry_during_transport_never_renews_or_publishes(self):
        self.f.step([('desk', 20, True), ('sofa', 350, True)], label='expired', jev_mode='late')
        self.assertFalse(self.events('reasoning.person_output'))
        self.assertEqual(len(self.f.calls), 1)
        request = self.events('reasoning.request')[0]['payload']
        self.assertLess(request['person_context']['packet']['valid_until'], self.f.f.la.now)

    def test_revoked_permission_during_transport_never_publishes(self):
        original = self.f.transport
        def revoke(*args):
            response = original(*args)
            self.f.policy['enabled'] = False
            return response
        self.f.transport = revoke
        self.f.step([('desk', 20, True), ('sofa', 350, True)], label='revoked')
        self.assertFalse(self.events('reasoning.person_output'))
        self.assertEqual(len(self.f.calls), 1)

    def test_actual_stop_during_transport_withdraws_late_answer_and_cleans_ram(self):
        original = self.f.transport
        stopped = []
        def stop(*args):
            response = original(*args)
            stopped.append(self.f.f.controller.stop(self.f.session['id'], reason='authored_validation'))
            return response
        self.f.transport = stop
        self.f.f.step([('desk', 20, True), ('sofa', 350, True)], label='stop')
        self.f.service.offer_reasoning(self.f.session['id'], [], generation=self.f.f.runner.generation,
                                       cutoff=self.f.f.la.now, only_new=True)
        self.f.reasoner = self.f.service.reasoners[self.f.session['id']]
        self.f.wait()
        self.assertEqual(stopped[0]['state'], 'stopped')
        self.assertFalse(self.events('reasoning.person_output'))
        self.f.close()
        self.assertTrue(self.f.cleanup['stop_command_confirmed'])
        self.assertEqual(self.f.cleanup['jev_active_rows'], 0)
        self.assertEqual(self.f.f.viewing_source.status()['resident_bytes'], 0)
        self.assertFalse(Path(self.f.f.directory.name).exists())


if __name__ == '__main__':
    unittest.main()
