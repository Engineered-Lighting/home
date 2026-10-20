"""Independent scene authority with authored already-admitted causal inputs.

These are validator tests, not live capture or renderer evidence. SQLite is real;
the trusted ingress current-head predicate is an explicit unit-test capability.
"""
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace
import unittest

from journal import JournalError, canonical, digest
from live_scene import LiveSceneWorkerBoundary, INTENT, CORRECTION_INTENT, CORRECTION_STATE_VERSION
from live_test_helpers import SCOPE, person, reference


class SceneCorrectionAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE live_scene_intents(cursor INTEGER PRIMARY KEY,event_id TEXT,event TEXT,publication TEXT,application_result TEXT,settled_at REAL); CREATE TABLE live_sources(source_id TEXT,generation INTEGER); INSERT INTO live_sources VALUES("authored-source",1);')
        self.boundary = b = object.__new__(LiveSceneWorkerBoundary)
        b.supports_brightness, b.scene_state_version = True, CORRECTION_STATE_VERSION
        b.intent_types = frozenset({INTENT, CORRECTION_INTENT})
        b.j = SimpleNamespace(db=self.db)
        b.ingress = SimpleNamespace(identity=deepcopy(SCOPE), _current_payload=lambda p, t: True)
        family = reference('authored-family')
        self.example = {'schema': 'home-approved-lighting-example-1', 'version': 1, 'id': 'authored-example', 'approved': True,
            'profile_id': 'authored-profile', 'nominal': {'brightness_fraction': .3, 'cct_kelvin': 3200, 'fixture_family_ref': family},
            'requirements_ref': reference('authored-requirements')}
        profile = {'schema': 'home-approved-profile-1', 'version': 1, 'id': 'authored-profile', 'approved': True,
                   'example_refs': [{'id': self.example['id'], 'digest': digest(self.example)}]}
        choice = {'profile_ref': {'id': profile['id'], 'digest': digest(profile)}, 'example_ref': profile['example_refs'][0]}
        b.scene_policy = {'source_gap_hold_s': 10, 'catalog': {'profiles': [profile], 'examples': [self.example], 'anonymous_baseline': choice}}
        b.policy = {'max_output_age_s': 3, 'lifecycle_policy': None}
        self.person, self.subject = person(), {'subject_id': 'authored-work', 'subject_epoch': 'authored-subject'}
        key = digest([self.person, self.subject, 'authored-episode'])
        self.base = {'status': 'selected', 'scene_id': 'scene:' + digest([SCOPE, key, choice, 100])[:48], 'episode_id': 'authored-episode',
                     'person_ref': self.person, 'subject_ref': self.subject, **choice, 'selected_at': 100,
                     'nominal': deepcopy(self.example['nominal']), 'correction_refs': [], 'valid_until': 103}
        self.selection = {'key': key, 'scene': deepcopy(self.base), 'requirements_ref': self.example['requirements_ref'],
                          'support_source_ids': ['authored-source'], 'gap_since': None}
        self.old = {'authority_generation': 0, 'state': {'engagement': {'source_generations': {'authored-source': 1}},
            'scene_and_corrections': {'state': {'bindings': [], 'selections': [deepcopy(self.selection)], 'settlements': [], 'corrections': []}}}}
        self.intent = {'schema': CORRECTION_INTENT, 'version': 2, 'id': 'authored-brightness', **SCOPE,
            'manifest_ref': reference('authored-manifest'), 'actor_ref': 'authored-owner', 'origin': 'human_preference', 'truth_role': 'runtime_intent',
            'kind': 'set_brightness', 'target': deepcopy(self.person), 'set_at': 101, 'available_at': 101, 'valid_until': 103,
            'value': {'scene_id': self.base['scene_id'], 'episode_id': self.base['episode_id'], 'subject_ref': deepcopy(self.subject), 'brightness_fraction': .7}, 'reply_to': None}
        self.p = self.store_intent(self.intent)
        self.correction = {'intent_ref': b._intent_reference(self.p), 'person_ref': self.person, 'subject_ref': self.subject,
            'episode_id': self.base['episode_id'], 'scene_id': self.base['scene_id'], 'brightness_fraction': .7,
            'applied_at': 101, 'authority_generation': 0, 'source_generations': {'authored-source': 1}}
        self.scene = deepcopy(self.base)
        self.scene.update(nominal={**self.scene['nominal'], 'brightness_fraction': .7}, correction_refs=[self.intent['id']], valid_until=104)
        selected = deepcopy(self.selection)
        selected['scene'] = self.scene
        self.result = {'id': self.intent['id'], 'status': 'applied', 'reason': 'explicit_scene_brightness_set', 'decision_ref': 'authored-decision'}
        state = {'t': 101, 'seq': 1, 'authority_generation': 0, 'source_generations': {'authored-source': 1}, 'bindings': [],
                 'selections': [selected], 'settlements': [{k: self.result[k] for k in ('id', 'status', 'reason')} | {'digest': digest(self.intent)}], 'corrections': [self.correction]}
        snapshot = {'version': CORRECTION_STATE_VERSION, 'identity': deepcopy(SCOPE),
                    'config_digest': digest({'version': CORRECTION_STATE_VERSION, 'identity': SCOPE, 'scene_policy': b.scene_policy,
                                             'max_output_age_s': 3, 'lifecycle_policy': None}), 'state': state}
        self.commit = {'checkpoint': {'authority_generation': 0, 'state': {'semantic_seq': 1, 'scene_and_corrections': snapshot,
            'engagement': {'source_generations': {'authored-source': 1}}, 'intent_settlements': [self.result]}}}
        self.decision = {'id': 'authored-decision', 't': 101, 'scene': self.scene, 'intent_results': [self.result],
            'scene_continuity': {'status': 'current', 'reason': 'current_supported_episode', 'valid_until': 104},
            'goal': {'status': 'requested', **{k: self.scene[k] for k in ('person_ref', 'subject_ref', 'episode_id', 'scene_id', 'nominal')},
                     'requirements_ref': self.example['requirements_ref'], 'valid_until': 104, 'geometry_refs': []},
            'background': {'status': 'unavailable'}, 'input_cut': {'geometry_refs': []}}
        self.fact = {'payload_type': 'home-live-fact-1', 'payload': {'person_ref': self.person, 'source_id': 'authored-source',
            'status': 'supplied', 'kind': 'person_presence', 'value': 'present', 'association': {'candidate_person_refs': [], 'person_status': 'qualified'}}}
        self.publications = {1: self.fact, 10: self.p}
        self.engagements = [{**deepcopy(SCOPE), 'person_ref': self.person, 'subject_ref': self.subject,
                             'episode_id': self.base['episode_id'], 'state': 'supported', 'valid_until': 110}]

    def store_intent(self, intent, cursor=10, committed_at=101):
        p = {'stream_epoch': 'authored-stream', 'cursor': cursor, 'producer_id': 'authored-owner', 'id': intent['id'],
             'payload_digest': digest(intent), 'committed_at': committed_at, 'payload_type': intent['schema'], 'payload': deepcopy(intent)}
        self.db.execute('INSERT INTO live_scene_intents VALUES(?,?,?,?,NULL,NULL)', (cursor, intent['id'], canonical({'payload': intent}), canonical(p)))
        self.db.commit()
        return p

    def settle(self):
        with self.db:
            self.boundary.settle(self.commit, None, self.publications, self.decision, self.engagements, self.old)

    def test_actual_effect_and_original_publication_settle_atomically(self):
        self.settle()
        row = self.db.execute('SELECT * FROM live_scene_intents WHERE cursor=10').fetchone()
        self.assertEqual(json.loads(row['application_result'])['status'], 'applied')
        self.assertEqual(row['settled_at'], 101)

    def test_forged_effect_cct_original_reference_source_or_missing_row_never_stores_terminal(self):
        mutations = [lambda: self.correction.update(brightness_fraction=.8), lambda: self.scene['nominal'].update(cct_kelvin=4000),
            lambda: self.correction['intent_ref'].update(payload_digest='b' * 64), lambda: self.correction.update(source_generations={'authored-source': 2}),
            lambda: self.commit['checkpoint']['state']['scene_and_corrections']['state'].update(corrections=[])]
        for mutate in mutations:
            self.setUp()
            mutate()
            with self.subTest(mutate=mutate), self.assertRaises(JournalError):
                self.settle()
            self.assertIsNone(self.db.execute('SELECT application_result FROM live_scene_intents WHERE cursor=10').fetchone()[0])

    def test_restart_retains_original_effect_after_command_expiry(self):
        self.settle()
        self.old = deepcopy(self.commit['checkpoint'])
        state = self.commit['checkpoint']['state']
        scene_state = state['scene_and_corrections']['state']
        scene_state.update(t=104, seq=2)
        state.update(semantic_seq=2, intent_settlements=[])
        self.scene['valid_until'] = 107
        self.decision.update(id='authored-after-restart', t=104, intent_results=[])
        self.decision['goal']['valid_until'] = 107
        self.decision['scene_continuity']['valid_until'] = 107
        self.publications = {1: self.fact}
        self.settle()
        row = self.db.execute('SELECT application_result,settled_at FROM live_scene_intents WHERE cursor=10').fetchone()
        self.assertEqual(json.loads(row[0])['decision_ref'], 'authored-decision')
        self.assertEqual(row[1], 101)
        self.assertEqual(self.scene['nominal']['brightness_fraction'], .7)

    def test_original_command_window_cannot_authorize_a_late_first_effect(self):
        self.correction['applied_at'] = 104
        with self.assertRaises(JournalError):
            self.settle()

    def test_superseded_delivered_cursor_cannot_be_claimed_applied(self):
        later = deepcopy(self.intent)
        later['id'] = 'authored-later-brightness'
        later['value']['brightness_fraction'] = .5
        self.publications[11] = self.store_intent(later, 11)
        with self.assertRaises(JournalError):
            self.settle()

    def test_latest_cursor_has_one_effect_and_both_originals_get_distinct_terminal_results(self):
        later = deepcopy(self.intent)
        later['id'] = 'authored-later-brightness'
        later['value']['brightness_fraction'] = .5
        later_p = self.store_intent(later, 11)
        self.publications[11] = later_p
        self.correction.update(intent_ref=self.boundary._intent_reference(later_p), brightness_fraction=.5)
        self.scene['nominal']['brightness_fraction'] = .5
        self.scene['correction_refs'] = [later['id']]
        first = {**self.result, 'status': 'rejected', 'reason': 'superseded_scene_correction'}
        last = {**self.result, 'id': later['id']}
        self.decision['intent_results'] = [first, last]
        self.commit['checkpoint']['state']['intent_settlements'] = [first, last]
        self.commit['checkpoint']['state']['scene_and_corrections']['state']['settlements'] = [
            {k: r[k] for k in ('id', 'status', 'reason')} | {'digest': digest(i)} for r, i in ((first, self.intent), (last, later))]
        self.settle()
        terminal = [json.loads(row[0])['status'] for row in self.db.execute('SELECT application_result FROM live_scene_intents ORDER BY cursor')]
        self.assertEqual(terminal, ['rejected', 'applied'])

    def held(self):
        method = {'method_ref': reference('authored-continuity'), 'qualification_ref': reference('authored-qualified'),
                  'applicability_ref': reference('authored-applicable'), 'max_gap_s': 4}
        self.boundary.policy['lifecycle_policy'] = {'continuity_methods': [method]}
        snapshot = self.commit['checkpoint']['state']['scene_and_corrections']
        snapshot['config_digest'] = digest({'version': CORRECTION_STATE_VERSION, 'identity': SCOPE,
            'scene_policy': self.boundary.scene_policy, 'max_output_age_s': 3, 'lifecycle_policy': self.boundary.policy['lifecycle_policy']})
        self.settle()
        self.old = deepcopy(self.commit['checkpoint'])
        state = self.commit['checkpoint']['state']
        state.update(semantic_seq=2, intent_settlements=[])
        scene_state = state['scene_and_corrections']['state']
        scene_state.update(t=104, seq=2)
        scene_state['selections'][0]['gap_since'] = 104
        self.scene['valid_until'] = 106
        self.decision.update(id='authored-held', t=104, intent_results=[], scene_continuity={'status': 'held', 'reason': 'qualified_semantic_continuity', 'valid_until': 106})
        self.decision['goal']['valid_until'] = 106
        self.engagements[0].update(state='suspended', valid_until=107)
        lifecycle = {'schema': 'home-live-person-lifecycle-1', 'version': 1, 'id': 'authored-proof', **SCOPE,
            'source_id': 'authored-source', 'source_generation': 1, 'disposition': 'continuity_qualified',
            'target_person_ref': self.person, 'target_subject_ref': self.subject,
            **{k: method[k] for k in ('method_ref', 'qualification_ref', 'applicability_ref')},
            'observed_at': 104, 'available_at': 104, 'valid_until': 107, 'window': {'start_at': 102, 'end_at': 104}}
        self.publications = {14: {'payload_type': lifecycle['schema'], 'payload': lifecycle, 'committed_at': 104}}
        return lifecycle

    def test_qualified_semantic_gap_preserves_earned_scene_and_correction_with_proof_bound_goal(self):
        self.held()
        self.settle()
        self.assertEqual(self.scene['nominal']['brightness_fraction'], .7)
        self.assertEqual(self.decision['goal']['valid_until'], 106)

    def test_gap_or_unqualified_future_wrong_subject_and_overlong_proof_cannot_authorize_held_goal(self):
        for mutate in (lambda l: l.update(disposition='observation_gap'), lambda l: l.update(available_at=105),
                       lambda l: l.update(qualification_ref=reference('other')), lambda l: l.update(source_generation=2),
                       lambda l: l.update(target_subject_ref={'subject_id': 'other', 'subject_epoch': 'other'}),
                       lambda l: l['window'].update(start_at=100)):
            self.setUp()
            lifecycle = self.held()
            mutate(lifecycle)
            with self.subTest(mutate=mutate), self.assertRaises(JournalError):
                self.settle()

    def test_default_external_validator_refuses_before_any_terminal_write(self):
        clear = deepcopy(self.intent)
        clear.update(id='authored-clearance', kind='clear_person_hazards', origin='human_declaration', target=None, value={})
        self.publications = {1: self.fact, 11: self.store_intent(clear, 11)}
        self.boundary.external_intent_kinds = frozenset({'clear_person_hazards'})
        self.scene.update(nominal=deepcopy(self.example['nominal']), correction_refs=[])
        scene_state = self.commit['checkpoint']['state']['scene_and_corrections']['state']
        scene_state.update(corrections=[], settlements=[])
        result = {'id': clear['id'], 'status': 'rejected', 'reason': 'authored-no-clearance', 'decision_ref': self.decision['id']}
        self.decision['intent_results'] = [result]
        self.commit['checkpoint']['state']['intent_settlements'] = [result]
        self.decision['goal']['nominal'] = deepcopy(self.scene['nominal'])
        with self.assertRaises(JournalError):
            self.settle()
        self.assertIsNone(self.db.execute('SELECT application_result FROM live_scene_intents WHERE cursor=11').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
