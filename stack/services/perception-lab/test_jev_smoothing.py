"""Code-owned JEV timeline: HMM filtering, tempering, gates and hysteresis. Pure."""
import unittest

import jev_smoothing as smoothing
from reasoning import score_intervals

OPTIONS = ('reading', 'computer_work', 'phone_use', 'resting', 'transition', 'other', 'insufficient_evidence')


def answer(t, choice, p, **extra):
    rest = (1-p)/(len(OPTIONS)-1)
    return {'t': t, 'choice': choice, 'probabilities': {o: (p if o == choice else rest) for o in OPTIONS}, **extra}


class SmoothingTests(unittest.TestCase):
    def test_transition_rows_sum_to_one_and_powers_compose(self):
        matrix = smoothing.transition_matrix(.97, .8, .5)
        for row in matrix:
            self.assertAlmostEqual(sum(row), 1.0)
        t = smoothing.STATES.index('transition')
        reading = smoothing.STATES.index('reading')
        self.assertAlmostEqual(matrix[reading][t], .015)
        table = smoothing.powers(matrix)
        self.assertEqual(sorted(table), list(range(1, 16)))
        self.assertLess(table[10][reading][reading], table[1][reading][reading])
        for row in table[15]:
            self.assertAlmostEqual(sum(row), 1.0)

    def test_hmm_flat_on_insufficient_evidence(self):
        base = [answer(float(i), 'reading', .8) for i in range(5)]
        with_flat = base + [answer(6.0, 'insufficient_evidence', .9)]
        propagated = base + [{'t': 6.0, 'probabilities': None}]
        a, b = smoothing.smooth(with_flat)[-1], smoothing.smooth(propagated)[-1]
        self.assertEqual(a['posterior'], b['posterior'])
        self.assertEqual(a['activities'], ['reading'])

    def test_hard_no_person_emission(self):
        answers = [answer(float(i), 'reading', .9) for i in range(10)] + [{'t': 11.0, 'gate': 'no_person'},
                                                                          {'t': 12.0, 'gate': 'no_person'}]
        out = smoothing.smooth(answers)
        self.assertEqual(out[9]['activities'], ['reading'])
        self.assertEqual(out[-1]['shown'], 'no_person')
        self.assertEqual(out[-1]['activities'], [])
        self.assertGreater(out[-1]['posterior']['no_person'], .99)

    def test_multiple_people_gate_forces_abstention(self):
        out = smoothing.smooth([answer(float(i), 'reading', .9) for i in range(5)] + [{'t': 6.0, 'gate': 'multiple_people'}])
        self.assertEqual((out[-1]['activities'], out[-1]['shown'], out[-1]['confidence']), ([], 'abstain', None))

    def test_hysteresis_requires_margin(self):
        answers = [answer(float(i), 'reading', .9) for i in range(8)]
        out = smoothing.smooth(answers + [answer(9.0, 'phone_use', .9)])
        self.assertEqual(out[-1]['activities'], ['reading'])   # one contrary answer does not switch
        more = answers + [answer(9.0 + i, 'phone_use', .9) for i in range(8)]
        switched = smoothing.smooth(more)
        shown = [o['shown'] for o in switched]
        self.assertEqual(shown[-1], 'phone_use')
        first = shown.index('phone_use')
        posterior = switched[first]['posterior']
        self.assertGreaterEqual(posterior['phone_use'], .60)
        self.assertGreaterEqual(posterior['phone_use'] - posterior['reading'], .15)
        # A huge margin keeps the first activity shown or abstains, never relabels early.
        sticky = smoothing.smooth(more, margin=.95)
        self.assertNotIn('phone_use', [o['shown'] for o in sticky[8:10]])

    def test_tempering_limits_repeated_answer_confidence(self):
        answers = [answer(float(i), 'reading', .55) for i in range(4)]
        tempered = smoothing.smooth(answers)[-1]['posterior']['reading']
        raw = smoothing.smooth(answers, alpha=1.0)[-1]['posterior']['reading']
        self.assertLess(tempered, raw)

    def test_continues_feature_makes_the_segment_stickier(self):
        answers = [answer(float(i), 'reading', .9) for i in range(6)] + [answer(7.0, 'phone_use', .7)]
        plain = smoothing.smooth(answers)[-1]['posterior']['reading']
        sticky = smoothing.smooth(answers[:-1] + [dict(answers[-1], continues=.9)])[-1]['posterior']['reading']
        self.assertGreater(sticky, plain)

    def test_outputs_score_and_sweep_is_free(self):
        answers = [answer(float(i), 'reading', .8, available_at=i + .5) for i in range(0, 30, 2)]
        out = smoothing.smooth(answers)
        labels = [{'start': 0, 'end': 30, 'activities': ['reading'], 'reviewed': True, 'source': 'human'}]
        scored = score_intervals(labels, out, run_mode='offline')
        self.assertGreater(scored['coverage'], .8)
        self.assertEqual(scored['exact_match_at_coverage'], 1.0)
        results = list(smoothing.sweep(answers, {'alpha': [.6, 1.0], 'stay': [.95, .97]}))
        self.assertEqual(len(results), 4)
        with self.assertRaises(ValueError):
            smoothing.smooth(answers, unknown=1)
        with self.assertRaises(ValueError):
            smoothing.smooth(list(reversed(answers)))

    def test_answers_from_recorded_outputs(self):
        outputs = [{'prediction': {'available_at': 5.0, 'mode': 'live'},
                    'judgment': {'activity': 'reading', 'probabilities': {'reading': .7, 'insufficient_evidence': .3}}},
                   {'prediction': {'available_at': 'bad'}}]
        self.assertEqual(smoothing.answers_from_outputs(outputs),
                         [{'t': 5.0, 'available_at': 5.0, 'probabilities': {'reading': .7, 'insufficient_evidence': .3},
                           'choice': 'reading', 'mode': 'live'}])


if __name__ == '__main__':
    unittest.main()
