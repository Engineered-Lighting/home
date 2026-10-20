"""Real bounded local producer/read-model join; external detector/model are authored."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

import qwen_person_scene as scene
from qwen_person_live import EVENT

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('person_semantics_fixture', ROOT / 'tools/fixtures/den-person-semantics-runtime.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class PersonLiveTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.Fixture()

    def tearDown(self):
        self.f.close()

    def packet(self, state):
        return state['live']['arms']['B']['persons2d']['semantics']

    def events(self):
        return [e for e in self.f.store.list_events(self.f.session['id']) if e['kind'] == EVENT]

    def test_actual_pump_la_intake_ledger_and_live_preserve_independent_people(self):
        first = self.packet(self.f.step([('desk-1', 20, True), ('sofa-1', 350, True)]))
        subjects = {p['instance_id']: p for p in first['subjects']}
        self.assertEqual(subjects['desk-1']['facts']['activity_hypothesis'], 'computer_work')
        self.assertEqual(subjects['sofa-1']['facts']['activity_hypothesis'], 'reading')
        second = self.packet(self.f.step([('sofa-2', 342, False), ('desk-2', 28, True)], mode='one_unknown'))
        by_id = {p['instance_id']: p for p in second['subjects']}
        self.assertEqual(by_id['desk-2']['track_ref'], subjects['desk-1']['track_ref'])
        self.assertEqual(by_id['sofa-2']['track_ref'], subjects['sofa-1']['track_ref'])
        self.assertEqual(by_id['desk-2']['facts']['activity_hypothesis'], 'computer_work')
        self.assertEqual(by_id['sofa-2']['facts']['activity_hypothesis'], 'unclear')
        self.assertEqual(second['scene'], {'people_visible': 'multiple'})
        self.assertEqual(second['decision']['status'], 'unavailable')
        self.assertFalse(second['engagement_authority'])
        body = self.f.transport.envelopes[-1]['body']
        context = json.loads(body['messages'][1]['content'][0]['text'])
        self.assertEqual(set(context), {'placement', 'local_time', 'subjects'})
        self.assertNotIn('track_ref', json.dumps(context))
        self.assertNotIn('instance_id', json.dumps(context))
        self.assertEqual(body['max_tokens'], 512)
        self.assertEqual(len(self.f.la.model.calls), 2)

    def test_foreign_slot_cannot_borrow_valid_other_subject_or_global_activity(self):
        packet = self.packet(self.f.step([('desk', 20, True), ('sofa', 350, True)], mode='foreign_slot'))
        self.assertTrue(all(p['status'] == 'unknown' and p['facts'] is None for p in packet['subjects']))
        self.assertIsNone(packet['scene'])
        self.assertIsNotNone(self.events()[0]['payload']['invalid_reason'])

    def test_ambiguous_membership_never_dispatches_or_attaches_prior_facts(self):
        self.f.step([('desk-1', 20, True), ('sofa-1', 350, True)])
        state = self.f.step([('cross-1', 42, True), ('cross-2', 70, True)])
        self.assertEqual(state['outcome']['reason'], 'person_membership_ambiguous')
        self.assertEqual(len(self.f.transport.envelopes), 1)
        self.assertNotIn('semantics', state['live']['arms']['B']['persons2d'])
        self.assertEqual(len(state['live']['arms']['B']['persons2d']['persons']), 2)

    def test_original_expiry_is_not_the_seven_second_inference_deadline(self):
        state = self.f.step([('desk', 20, True), ('sofa', 350, True)], mode='late')
        self.assertEqual(state['outcome']['reason'], 'person_expired')
        self.assertNotIn('semantics', state['live'].get('arms', {}).get('B', {}).get('persons2d', {}))
        payload = self.events()[0]['payload']
        self.assertGreaterEqual(payload['packet']['available_at'], payload['packet']['valid_until'])
        self.assertEqual(payload['packet']['valid_until'], payload['packet']['basis']['source']['valid_until'])

    def test_new_original_needs_its_own_interpretation_and_exact_retry_does_not_renew(self):
        state = self.f.step([('desk-1', 20, True), ('sofa-1', 350, True)])
        saved = deepcopy(self.events()[0]['payload'])
        self.f.la.now += .1
        self.assertEqual(self.f.pump.ledger.record(self.f.session['id'], saved)['state'], 'duplicate')
        self.assertEqual(self.events()[0]['payload'], saved)
        changed = deepcopy(saved)
        changed['fingerprint'] = 'f'*64
        with self.assertRaisesRegex(ValueError, 'identity_conflict'):
            self.f.pump.ledger.record(self.f.session['id'], changed)
        # A new capture has no matching older scene facts. It also lacks the
        # same-epoch 2-4-frame context, so the pump must explicitly wait.
        next_state = self.f.step([('desk-2', 28, True), ('sofa-2', 342, True)], epoch='capture-2')
        self.assertNotIn('semantics', next_state['live']['arms']['B']['persons2d'])
        self.assertEqual(len(self.f.transport.envelopes), 1)
        self.assertNotEqual(next_state['live']['arms']['B']['persons2d']['association']['track_epoch'], state['live']['arms']['B']['persons2d']['association']['track_epoch'])

    def test_stop_withdraws_layers_and_ends_further_dispatch(self):
        self.f.step([('desk', 20, True), ('sofa', 350, True)])
        stopped = self.f.close()
        self.assertEqual(stopped['arms'], {})
        self.assertEqual(self.f.pump.tick()['reason'], 'stopped')
        self.assertTrue(all(value is None for value in self.f.runner.snapshot()['active'].values()))
        self.assertTrue(all(value is None for value in self.f.runner.snapshot()['pending'].values()))
        self.assertFalse(Path(self.f.directory.name).exists())


if __name__ == '__main__':
    unittest.main()
