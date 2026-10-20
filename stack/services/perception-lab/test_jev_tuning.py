"""Overnight JEV tuning levers, all off by default: the coarse facts key
(facts-key-2), the v7 question set with veto-gate fusion, and the optional
smoothing parameters and parameter sets. Pure builders plus the real v6
policy and provider on a temp store with a fake transport."""
from copy import deepcopy
import json
import re
import unittest
from unittest.mock import patch

import jev_smoothing as smoothing
import jev_wire as wire
from jev_provider import JevProvider
from test_jev_policy_v6 import ProviderV6Tests, V6Case, respond
from test_jev_smoothing import answer
from test_jev_wire import local_for, reading_session


def state_with_head(monitor_side='image_right'):
    local = local_for(reading_session())
    return wire.build_state(local, cutoff=100.0, storyline_seconds=60, monitor_side=monitor_side)


class FactsKeyTwoTests(unittest.TestCase):
    def setUp(self):
        self.state = state_with_head()
        self.key = wire.facts_key(self.state, 'facts-key-2')

    def test_default_key_is_unchanged_and_versions_are_registered(self):
        self.assertEqual(wire.facts_key(self.state), wire.facts_key(self.state, 'facts-key-1'))
        self.assertNotEqual(self.key, wire.facts_key(self.state))
        with self.assertRaises(ValueError):
            wire.facts_key(self.state, 'facts-key-3')

    def test_ignores_low_value_churn(self):
        for change in (lambda s: s['storyline']['segments'][-1].update(span='about_60_s'),
                       lambda s: s['storyline']['segments'].insert(0, {'order': 'earliest', 'state': 'phone_near_hand',
                                                                     'span': 'under_10_s', 'stability': 'steady'}),
                       lambda s: s['qwen']['latest']['facts'].update(visible_basis=list(reversed(s['qwen']['latest']['facts']['visible_basis']))),
                       lambda s: s['qwen']['latest']['facts'].update(subject_posture='standing'),
                       lambda s: s['qwen']['history']['segments'].append(dict(s['qwen']['history']['segments'][-1], activity='resting')),
                       lambda s: s['support']['reading'].update(conflicts=['qwen_activity_resting']),
                       lambda s: s['support']['reading'].update(camera_line='qwen_only'),
                       lambda s: s['coverage'].update(pose='stale'),
                       lambda s: s['now'].update(head_toward='elsewhere'),   # 'unknown' and 'elsewhere' read alike
                       lambda s: s['now'].update(age='about_5_s')):
            changed = deepcopy(self.state)
            change(changed)
            self.assertEqual(wire.facts_key(changed, 'facts-key-2'), self.key)

    def test_changes_on_activity_relevant_facts(self):
        for change in (lambda s: s['storyline']['segments'][-1].update(state='phone_near_hand'),
                       lambda s: s['now'].update(people_in_view='multiple'),
                       lambda s: s['now'].update(camera_evidence='expired_historical'),
                       lambda s: s['now'].update(head_toward='monitor_side'),
                       lambda s: s['qwen']['latest']['facts'].update(activity_hypothesis='resting'),
                       lambda s: s['qwen']['latest']['facts'].update(held_object='phone'),
                       lambda s: s['qwen']['latest']['facts'].update(gaze_target='screen'),
                       lambda s: s['qwen']['latest'].update(state='expired_historical'),
                       lambda s: s['support']['computer_work'].update(pc_input_role='counts_camera_cue_at_pc'),
                       lambda s: s['devices'].update(pc_input='under_15s'),
                       lambda s: s['ambient'].update(daylight='light')):
            changed = deepcopy(self.state)
            change(changed)
            self.assertNotEqual(wire.facts_key(changed, 'facts-key-2'), self.key)

    def test_a_briefly_lost_hand_cue_is_held_under_10_s(self):
        state = deepcopy(self.state)
        state['storyline']['segments'] = [
            {'order': 'just_before', 'state': 'book_near_hand', 'span': 'about_40_s', 'stability': 'steady'},
            {'order': 'current', 'state': 'person_no_hand_cue', 'span': 'under_10_s', 'stability': 'steady', 'ongoing': True}]
        self.assertEqual(wire.held_cue(state), 'book')
        self.assertEqual(wire.facts_key(state, 'facts-key-2'), self.key)
        state['storyline']['segments'][-1]['span'] = 'about_20_s'
        self.assertEqual(wire.held_cue(state), 'person')
        state['storyline']['segments'][0]['state'] = 'phone_near_face'
        state['storyline']['segments'][-1]['span'] = 'under_10_s'
        self.assertEqual(wire.held_cue(state), 'phone')


class QuestionsV7Tests(unittest.TestCase):
    def test_registered_and_every_backtick_path_exists_in_a_state(self):
        self.assertIs(wire.QUESTION_SETS[wire.QUESTION_VERSION_V7], wire.QUESTIONS_V7)
        self.assertEqual(wire.QUESTION_CHOICES, {'v6': wire.QUESTION_VERSION, 'v7': wire.QUESTION_VERSION_V7,
                                                 'v8': wire.QUESTION_VERSION_10})
        nouls = [k for k, q in wire.QUESTIONS_V7.items() if q['type'] == 'noul']
        self.assertEqual(len(nouls), 7)
        from reasoning import ACTIVITIES
        self.assertEqual(set(wire.QUESTIONS_V7['activity']['criteria']), set(ACTIVITIES))
        for criteria in wire.QUESTIONS_V7['activity']['criteria'].values():
            self.assertEqual(set(criteria), {'what', 'not_for', 'examples'})
        state = state_with_head()
        text = json.dumps(wire.QUESTIONS_V7)
        paths = set(re.findall(r'`([^`]+)`', text))
        self.assertIn('now.head_toward', paths)
        for path in paths:
            value = state
            for part in path.split('.'):
                self.assertIn(part, value, path)
                value = value[part]

    def test_packet_carries_the_chosen_set_and_prepare_refuses_unknown(self):
        state = state_with_head()
        self.assertEqual(wire.packet(state, 'm', wire.QUESTION_VERSION_V7)['questions'], wire.QUESTIONS_V7)
        self.assertEqual(wire.packet(state, 'm')['questions'], wire.QUESTIONS)

    def answers(self, choice, p, **nouls):
        probabilities = {k: 0.0 for k in wire.QUESTIONS_V7['activity']['criteria']}
        probabilities[choice] = p
        probabilities['insufficient_evidence'] = round(1 - p, 6)
        answers = {'activity': {'type': 'choice', 'choice': choice, 'confidence': .5, 'probabilities': probabilities}}
        for k, q in wire.QUESTIONS_V7.items():
            if q['type'] == 'noul':
                answers[k] = {'type': 'noul', 'noul': nouls.get(k, .1)}
        return answers

    def test_gates_only_remove_never_relabel(self):
        state = state_with_head()
        fused = wire.fuse(self.answers('reading', .9, book_open_and_attended=.8), state, wire.QUESTION_VERSION_V7)
        self.assertEqual((fused['activity'], fused['gate']['passed']), ('reading', True))
        vetoed = wire.fuse(self.answers('reading', .9, book_open_and_attended=.3), state, wire.QUESTION_VERSION_V7)
        self.assertEqual((vetoed['activity'], vetoed['gate']['reason']), ('insufficient_evidence', 'book_not_attended'))
        self.assertEqual(vetoed['probabilities']['reading'], 0.0)
        self.assertAlmostEqual(sum(vetoed['probabilities'].values()), 1.0)
        low = wire.fuse(self.answers('phone_use', .5, phone_attended=.9), state, wire.QUESTION_VERSION_V7)
        self.assertEqual(low['gate']['reason'], 'choice_below_threshold')
        # computer_work: hands, or facing the monitor side while PC input counts.
        facing = self.answers('computer_work', .8, facing_monitor_side=.9)
        self.assertEqual(wire.fuse(facing, state, wire.QUESTION_VERSION_V7)['activity'], 'insufficient_evidence')
        counted = deepcopy(state)
        counted['support']['computer_work']['pc_input_role'] = 'counts_camera_cue_at_pc'
        self.assertEqual(wire.fuse(facing, counted, wire.QUESTION_VERSION_V7)['activity'], 'computer_work')
        hands = self.answers('computer_work', .8, hands_on_keyboard_or_mouse=.7)
        self.assertEqual(wire.fuse(hands, state, wire.QUESTION_VERSION_V7)['activity'], 'computer_work')
        # Ungated activities pass unchanged; nothing ever becomes another activity.
        resting = wire.fuse(self.answers('resting', .6), state, wire.QUESTION_VERSION_V7)
        self.assertEqual((resting['activity'], resting['gate']['passed']), ('resting', None))
        for choice in ('reading', 'phone_use', 'computer_work', 'resting', 'transition', 'other'):
            out = wire.fuse(self.answers(choice, .9), state, wire.QUESTION_VERSION_V7)['activity']
            self.assertIn(out, (choice, 'insufficient_evidence'))

    def test_thresholds_are_keyed_by_question_version(self):
        self.assertEqual(set(wire.THRESHOLDS), {wire.QUESTION_VERSION_V7})
        with self.assertRaises(KeyError):
            wire.fuse(self.answers('reading', .9), state_with_head(), wire.QUESTION_VERSION)


class ProviderV7Tests(V6Case):
    provider = ProviderV6Tests.provider
    evaluate = ProviderV6Tests.evaluate

    def test_v7_provider_is_admitted_by_the_policy_and_fused(self):
        bodies = []

        def transport(body, *_):
            bodies.append(json.loads(body))
            return respond(body)   # reading 0.7, every Noul 0.2
        provider = self.provider(transport, wire=wire.WIRE_VERSION, questions=wire.QUESTION_VERSION_V7, facts_key='facts-key-2')
        result = self.evaluate(provider)
        self.assertEqual(bodies[0]['questions'], wire.QUESTIONS_V7)
        self.assertEqual(result['status'], 'abstain')
        self.assertEqual(result['judgment']['activity'], 'insufficient_evidence')
        self.assertEqual(result['diagnostics']['fusion']['gate'], {'activity': 'reading', 'passed': False,
                                                                  'reason': 'book_not_attended'})
        self.assertEqual(result['diagnostics']['answers']['activity']['choice'], 'reading', 'raw answer kept')
        self.assertEqual((result['diagnostics']['question_version'], result['diagnostics']['facts_key_version']),
                         (wire.QUESTION_VERSION_V7, 'facts-key-2'))
        self.assertEqual(result['diagnostics']['facts_key'], wire.facts_key(bodies[0]['state'], 'facts-key-2'))

    def test_v6_provider_is_unchanged_by_default(self):
        result = self.evaluate(self.provider(wire=wire.WIRE_VERSION))
        self.assertNotIn('fusion', result['diagnostics'])
        self.assertEqual(result['diagnostics']['facts_key_version'], 'facts-key-1')

    def test_levers_need_the_v6_wire_and_registered_names(self):
        for kw in ({'questions': wire.QUESTION_VERSION_V7}, {'facts_key': 'facts-key-2'}):
            with self.assertRaises(ValueError):
                self.provider(**kw)
        for kw in ({'questions': 'den-activity-10'}, {'facts_key': 'facts-key-9'}):
            with self.assertRaises(ValueError):
                self.provider(wire=wire.WIRE_VERSION, **kw)


class SmoothingOptionTests(unittest.TestCase):
    def test_defaults_are_jev_smoothing_1(self):
        self.assertEqual(smoothing.PARAMETER_SETS['jev-smoothing-1'], {})
        answers = [answer(float(i), 'reading', .8) for i in range(5)] + [answer(6.0, 'insufficient_evidence', .9)]
        self.assertEqual(smoothing.smooth(answers), smoothing.smooth(answers, insufficient='flat', min_choice=0.0,
                                                                    release=None, confidence='posterior'))
        for bad in ({'insufficient': 'soft'}, {'release': 0}, {'confidence': 'max'}, {'min_choice': 1.0}, {'alpha': 'x'}):
            with self.assertRaises(ValueError):
                smoothing.params(**bad)

    def test_spread_reads_insufficient_mass_as_cannot_tell(self):
        hedged = {'reading': 0.0, 'phone_use': .4, 'insufficient_evidence': .6}
        flat = smoothing.emission({'choice': 'insufficient_evidence', 'probabilities': hedged}, smoothing.params())
        self.assertEqual(len(set(flat)), 1)
        spread = smoothing.emission({'choice': 'insufficient_evidence', 'probabilities': hedged}, smoothing.params(insufficient='spread'))
        phone, reading = smoothing.STATES.index('phone_use'), smoothing.STATES.index('reading')
        self.assertGreater(spread[phone], spread[reading])
        confident = smoothing.emission({'choice': 'resting', 'probabilities': {'resting': .95, 'insufficient_evidence': .05}},
                                       smoothing.params(insufficient='spread'))
        weak = smoothing.emission({'choice': 'resting', 'probabilities': {'resting': .6, 'insufficient_evidence': .4}},
                                  smoothing.params(insufficient='spread'))
        r = smoothing.STATES.index('resting')
        self.assertGreater(confident[r]/confident[reading], weak[r]/weak[reading])

    def test_confidence_floor_release_and_evidence_cap(self):
        low = [answer(float(i), 'reading', .45) for i in range(8)]
        self.assertTrue(all(o['shown'] == 'abstain' for o in smoothing.smooth(low, min_choice=.5)))
        answers = [answer(float(i), 'reading', .7) for i in range(10)]
        out = smoothing.smooth(answers)
        self.assertGreater(out[-1]['confidence'], .9, 'the posterior outgrows every single answer')
        capped = smoothing.smooth(answers, confidence='evidence')
        self.assertEqual(capped[-1]['activities'], ['reading'])
        self.assertAlmostEqual(capped[-1]['confidence'], .7)
        released = smoothing.smooth(answers + [answer(10.0, 'insufficient_evidence', .95)], release=.9)
        self.assertEqual(released[-1]['activities'], [])
        kept = smoothing.smooth(answers + [answer(10.0, 'insufficient_evidence', .95)])
        self.assertEqual(kept[-1]['activities'], ['reading'])


class ConfigTests(unittest.TestCase):
    def factory(self, **extra):
        from managed_jev import reasoning_factory
        config = {'schema_version': 1, 'policy_file': 'policy', 'credential_file': 'key', 'response_version': 'jev-1.13.0',
                  **extra}
        with patch('managed_jev.private_json', side_effect=lambda p: dict(config)):
            return reasoning_factory('config')

    def test_levers_are_optional_exact_names_and_default_off(self):
        plain = self.factory(wire=wire.WIRE_VERSION)
        self.assertEqual((plain.questions, plain.facts_key, plain.smoothing), ('v6', None, None))
        chosen = self.factory(wire=wire.WIRE_VERSION, questions='v7', facts_key='facts-key-2', smoothing='jev-smoothing-1')
        self.assertEqual((chosen.questions, chosen.facts_key, chosen.smoothing), ('v7', 'facts-key-2', 'jev-smoothing-1'))
        for bad in ({'wire': wire.WIRE_VERSION, 'questions': 'v8'}, {'wire': wire.WIRE_VERSION, 'facts_key': 'coarse'},
                    {'wire': wire.WIRE_VERSION, 'smoothing': 'tuned'}, {'questions': 'v7'}, {'facts_key': 'facts-key-2'}):
            with self.assertRaises(ValueError):
                self.factory(**bad)

    def test_live_reasoning_takes_only_a_registered_smoothing_set(self):
        from live_reasoning import LiveReasoning
        self.assertIn(smoothing.SMOOTHING_DEFAULT, smoothing.PARAMETER_SETS)
        with self.assertRaisesRegex(ValueError, 'smoothing'):
            reasoner = object.__new__(LiveReasoning)
            LiveReasoning.__init__(reasoner, _Store(), 's', None, smoothing='nope')


class _Store:
    def get_session(self, sid):
        return {'id': sid, 'site_id': 'victoria', 'started_at': 0.0}


if __name__ == '__main__':
    unittest.main()
