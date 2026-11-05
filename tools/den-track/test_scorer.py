"""E2 scorer: IDF1, switches and the one-sided bound on known cases."""
import itertools
import random
import unittest

import scorer_core
from scorer_math import best_matching, upper_bound


def links_from(rows, conf=1.0):
    """rows: [(person, track), ...] one per frame, frame k at time k."""
    return [(float(k), 'o%d' % k, p, t, conf if t is not None else None) for k, (p, t) in enumerate(rows)]


class BoundTest(unittest.TestCase):
    def test_zero_failures_matches_closed_form(self):
        for n in (1, 5, 20, 25, 30):
            self.assertAlmostEqual(upper_bound(0, n), 1 - 0.05 ** (1 / n), places=12)

    def test_brief_values(self):
        self.assertAlmostEqual(upper_bound(0, 20), 0.1391, places=4)   # "about 14% at 20"
        self.assertAlmostEqual(upper_bound(0, 30), 0.0950, places=4)   # "about 10% at 30"

    def test_one_failure_is_clopper_pearson(self):
        # P(X <= 1; 20, p) = 0.05 at p = 0.2161 (standard tables).
        self.assertAlmostEqual(upper_bound(1, 20), 0.2161, places=4)

    def test_edges(self):
        self.assertIsNone(upper_bound(0, 0))
        self.assertEqual(upper_bound(3, 3), 1.0)


class MatchingTest(unittest.TestCase):
    def test_against_brute_force(self):
        rng = random.Random(7)
        for _ in range(200):
            rows, cols = rng.randint(0, 4), rng.randint(0, 4)
            w = [[rng.choice((0, 0, 1, 2, 5, 9)) for _ in range(cols)] for _ in range(rows)]
            best = 0
            for perm in itertools.permutations(range(max(rows, cols, 1))):
                best = max(best, sum(w[i][perm[i]] for i in range(rows) if perm[i] < cols))
            total, pairs = best_matching(w)
            self.assertEqual(total, best)
            self.assertEqual(len({i for i, _ in pairs}), len(pairs))
            self.assertEqual(len({j for _, j in pairs}), len(pairs))


class IdF1Test(unittest.TestCase):
    def test_perfect(self):
        links = links_from([('A', 1), ('B', 2)] * 10)
        c = scorer_core.id_counts(links)
        self.assertEqual((c['idtp'], c['idfp'], c['idfn']), (20, 0, 0))
        self.assertEqual(scorer_core.idf1(c), 1.0)

    def test_full_swap_halfway_is_one_half(self):
        links = links_from([('A', 1), ('B', 2)] * 5 + [('A', 2), ('B', 1)] * 5)
        self.assertAlmostEqual(scorer_core.idf1(scorer_core.id_counts(links)), 0.5)

    def test_fragment(self):
        links = links_from([('A', 1), ('B', 2)] * 6 + [('A', 3), ('B', 2)] * 4)
        self.assertAlmostEqual(scorer_core.idf1(scorer_core.id_counts(links)), 32 / 40)

    def test_unlinked_and_false_observations(self):
        rows = [('A', 1)] * 8 + [('A', None)] * 2 + [(None, 1)] * 2
        c = scorer_core.id_counts(links_from(rows))
        self.assertEqual((c['idtp'], c['idfp'], c['idfn']), (8, 2, 2))
        self.assertAlmostEqual(scorer_core.idf1(c), 16 / 20)

    def test_nothing(self):
        self.assertIsNone(scorer_core.idf1(scorer_core.id_counts([])))


class SwitchTest(unittest.TestCase):
    def test_swap_is_silent_only_when_confident(self):
        rows = [('A', 1), ('B', 2)] * 3 + [('B', 1), ('A', 2)] * 3
        loud = links_from(rows, conf=0.4)
        quiet = links_from(rows, conf=0.99)
        self.assertEqual([s['silent'] for s in scorer_core.switches(loud, 0.9)], [False, False])
        self.assertEqual([s['silent'] for s in scorer_core.switches(quiet, 0.9)], [True, True])

    def test_threshold_is_inclusive(self):
        rows = [('A', 1)] * 2 + [('B', 1)]
        self.assertTrue(scorer_core.switches(links_from(rows, conf=0.9), 0.9)[0]['silent'])

    def test_false_observation_is_not_a_switch(self):
        rows = [('A', 1), (None, 1), ('A', 1)]
        self.assertEqual(scorer_core.switches(links_from(rows), 0.9), [])

    def test_fragment_is_not_a_switch(self):
        rows = [('A', 1)] * 3 + [('A', 2)] * 3
        self.assertEqual(scorer_core.switches(links_from(rows), 0.9), [])


class WindowTest(unittest.TestCase):
    def test_per_window_and_pooled(self):
        rows = [('A', 1), ('B', 2)] * 5 + [('A', 1), ('B', 2)] * 5 + [('B', 1), ('A', 2)] * 5
        links = links_from(rows, conf=0.99)
        windows = [{'id': 'c1', 'kind': 'crossing', 'start': 0, 'end': 9},
                   {'id': 'c2', 'kind': 'crossing', 'start': 15, 'end': 29}]
        res = scorer_core.score_windows(links, windows, 0.9, 0.9)
        w1, w2 = res['windows']
        self.assertEqual((w1['silent_swaps'], w1['idf1']), (0, 1.0))
        self.assertEqual(w2['silent_swaps'], 2)
        self.assertEqual(res['crossings_with_silent_swap'], 1)
        self.assertAlmostEqual(res['silent_swap_rate_upper_95'], upper_bound(1, 2))
        self.assertFalse(res['gate']['passed'])

    def test_switch_before_window_is_known_to_window(self):
        rows = [('A', 1)] * 5 + [('B', 1)] * 5
        links = links_from(rows, conf=0.99)
        res = scorer_core.score_windows(links, [{'id': 'w', 'start': 4.5, 'end': 9}], 0.9, 0.9)
        self.assertEqual(res['windows'][0]['silent_swaps'], 1)

    def test_gate_passes(self):
        links = links_from([('A', 1), ('B', 2)] * 10)
        windows = [{'id': 'c%d' % k, 'start': 2 * k, 'end': 2 * k + 1} for k in range(10)]
        res = scorer_core.score_windows(links, windows, 0.9, 0.9)
        self.assertTrue(res['gate']['passed'])
        self.assertAlmostEqual(res['silent_swap_rate_upper_95'], 1 - 0.05 ** (1 / 10))


if __name__ == '__main__':
    unittest.main()
