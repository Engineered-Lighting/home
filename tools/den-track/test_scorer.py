"""E2 scorer: IDF1, switches, the one-sided bound and ground truth, on known cases."""
import itertools
import random
import unittest

import score
import scorer_core
import truth
from scorer_math import best_matching, upper_bound


def links_from(rows, confident=True):
    """rows: [(person, track), ...] one per frame, frame k at time k."""
    return [(float(k), 'o%d' % k, p, t, confident if t is not None else False) for k, (p, t) in enumerate(rows)]


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
    def test_swap_is_silent_only_when_the_record_reported_confidence(self):
        rows = [('A', 1), ('B', 2)] * 3 + [('B', 1), ('A', 2)] * 3
        self.assertEqual([s['silent'] for s in scorer_core.switches(links_from(rows, False))], [False, False])
        self.assertEqual([s['silent'] for s in scorer_core.switches(links_from(rows, True))], [True, True])

    def test_false_observation_is_not_a_switch(self):
        rows = [('A', 1), (None, 1), ('A', 1)]
        self.assertEqual(scorer_core.switches(links_from(rows)), [])

    def test_fragment_is_not_a_switch(self):
        rows = [('A', 1)] * 3 + [('A', 2)] * 3
        self.assertEqual(scorer_core.switches(links_from(rows)), [])


class WindowTest(unittest.TestCase):
    def test_per_window_and_pooled(self):
        rows = [('A', 1), ('B', 2)] * 5 + [('A', 1), ('B', 2)] * 5 + [('B', 1), ('A', 2)] * 5
        links = links_from(rows)
        windows = [{'id': 'c1', 'kind': 'crossing', 'start': 0, 'end': 9},
                   {'id': 'c2', 'kind': 'crossing', 'start': 15, 'end': 29}]
        res = scorer_core.score_windows(links, windows, 0.9)
        w1, w2 = res['windows']
        self.assertEqual((w1['silent_swaps'], w1['idf1']), (0, 1.0))
        self.assertEqual(w2['silent_swaps'], 2)
        self.assertEqual(res['crossings_with_silent_swap'], 1)
        self.assertAlmostEqual(res['silent_swap_rate_upper_95'], upper_bound(1, 2))
        self.assertFalse(res['gate']['passed'])

    def test_switch_before_window_is_known_to_window(self):
        links = links_from([('A', 1)] * 5 + [('B', 1)] * 5)
        res = scorer_core.score_windows(links, [{'id': 'w', 'start': 4.5, 'end': 9}], 0.9)
        self.assertEqual(res['windows'][0]['silent_swaps'], 1)

    def test_gate_passes_with_the_closed_form_bound(self):
        links = links_from([('A', 1), ('B', 2)] * 10)
        windows = [{'id': 'c%d' % k, 'start': 2 * k, 'end': 2 * k + 1} for k in range(10)]
        res = scorer_core.score_windows(links, windows, 0.9)
        self.assertTrue(res['gate']['passed'])
        self.assertAlmostEqual(res['silent_swap_rate_upper_95'], 1 - 0.05 ** (1 / 10))

    def test_a_flagged_swap_is_not_silent_but_still_costs_idf1(self):
        rows = [('A', 1), ('B', 2)] * 5 + [('B', 1), ('A', 2)] * 5
        res = scorer_core.score_windows(links_from(rows, False), [{'id': 'w', 'start': 0, 'end': 19}], 0.9)
        self.assertEqual(res['silent_swaps'], 0)
        self.assertEqual(res['flagged_switches'], 2)
        self.assertTrue(res['gate']['silent_swaps_zero'])
        self.assertFalse(res['gate']['idf1_met'])

    def test_a_silent_swap_outside_every_crossing_fails_the_gate(self):
        rows = [('A', 1), ('B', 2)] * 10 + [('B', 1), ('A', 2)] * 10
        res = scorer_core.score_windows(links_from(rows), [{'id': 'w', 'start': 0, 'end': 9}], 0.9)
        self.assertEqual(res['crossings_with_silent_swap'], 0)
        self.assertEqual(res['silent_swaps_outside_crossings'], 2)
        self.assertFalse(res['gate']['silent_swaps_zero'])
        self.assertFalse(res['gate']['passed'])

    def test_ambiguity_is_reported_so_a_tracker_cannot_pass_by_doubting_everything(self):
        links = links_from([('A', 1), ('B', 2)] * 10, confident=False)
        res = scorer_core.score_windows(links, [{'id': 'w', 'start': 0, 'end': 9}], 0.9)
        self.assertEqual(res['windows'][0]['ambiguous_share'], 1.0)
        self.assertEqual(res['ambiguous_share_outside_crossings'], 1.0)

    def test_no_crossings_never_passes(self):
        res = scorer_core.score_windows(links_from([('A', 1)] * 3), [], 0.9)
        self.assertFalse(res['gate']['passed'])
        self.assertIsNone(res['silent_swap_rate_upper_95'])

    def test_a_window_without_two_people_is_no_trial(self):
        rows = [('A', 1), ('B', 2)] * 5 + [('A', 1)] * 10 + [(None, 3)] * 10
        windows = [{'id': 'two', 'start': 0, 'end': 9}, {'id': 'one', 'start': 10, 'end': 19},
                   {'id': 'none', 'start': 20, 'end': 29}, {'id': 'empty', 'start': 100, 'end': 110}]
        res = scorer_core.score_windows(links_from(rows), windows, 0.9)
        self.assertEqual(res['crossings'], 1)
        self.assertEqual(res['crossings_marked'], 4)
        self.assertEqual([(e['id'], e['people_scored']) for e in res['crossings_excluded']],
                         [('one', ['A']), ('none', []), ('empty', [])])
        self.assertAlmostEqual(res['silent_swap_rate_upper_95'], upper_bound(0, 1))
        self.assertEqual((res['pooled']['idtp'], res['pooled']['idfp']), (10, 0), 'IDF1 counts only the admitted window')

    def test_only_excluded_windows_never_pass(self):
        rows = [('A', 1)] * 20
        windows = [{'id': 'c%d' % k, 'start': 2 * k, 'end': 2 * k + 1} for k in range(10)]
        res = scorer_core.score_windows(links_from(rows), windows, 0.9)
        self.assertEqual(res['crossings'], 0)
        self.assertFalse(res['gate']['passed'])

    def test_a_silent_swap_in_a_window_with_one_person_still_fails_and_counts_in_the_bound(self):
        links = links_from([('A', 1)] * 5 + [('B', 1)] * 5)
        res = scorer_core.score_windows(links, [{'id': 'w', 'start': 4.5, 'end': 9}], 0.9)
        self.assertEqual(res['windows'][0]['people_scored'], ['B'])
        self.assertEqual(res['crossings'], 1)
        self.assertEqual(res['crossings_with_silent_swap'], 1)
        self.assertFalse(res['gate']['passed'])

    def test_a_flagged_switch_in_an_excluded_window_is_still_reported(self):
        links = links_from([('A', 1)] * 5 + [('B', 1)] * 5, confident=False)
        res = scorer_core.score_windows(links, [{'id': 'w', 'start': 4.5, 'end': 9}], 0.9)
        self.assertEqual(res['crossings'], 0)
        self.assertEqual(res['flagged_switches'], 1)


def good_truth_lines():
    return [
        {'version': truth.VERSION, 'kind': 'header', 'take': 't1', 'people': ['A', 'B']},
        {'kind': 'frame', 'capture_ts': 10.0, 'labels': {'o1': 'A', 'o2': None}},
        {'kind': 'crossing', 'crossing_id': 'c1', 'type': 'crossing', 'interval': [9.0, 11.0]},
    ]


class TruthFormatTest(unittest.TestCase):
    def test_reads_labels_and_crossings(self):
        t = truth.parse(good_truth_lines())
        self.assertEqual(t.labels, {'o1': 'A', 'o2': None})
        self.assertEqual(t.frame_ts['o1'], 10.0)
        self.assertEqual(t.crossings[0]['interval'], [9.0, 11.0])

    def test_reads_how_it_was_labelled_and_claims_nothing_when_silent(self):
        lines = good_truth_lines()
        self.assertEqual((truth.parse(lines).labeller, truth.parse(lines).blind), (None, None))
        lines[0].update(labeller='owner', blind=True, observations_sha256='sha256:abc')
        t = truth.parse(lines)
        self.assertEqual((t.labeller, t.blind, t.observations_sha256), ('owner', True, 'sha256:abc'))

    def test_refuses(self):
        def broken(change):
            lines = good_truth_lines()
            change(lines)
            return lines

        cases = {
            'no header': lambda l: l.pop(0),
            'old version': lambda l: l[0].update(version='den-track-truth/0'),
            'a name instead of a letter': lambda l: l[0].update(people=['Alice', 'B']),
            'two letters': lambda l: l[0].update(people=['AB']),
            'a person not in the header': lambda l: l[1]['labels'].update(o1='C'),
            'a label twice': lambda l: l.append({'kind': 'frame', 'capture_ts': 11.0, 'labels': {'o1': 'A'}}),
            'a crossing id twice': lambda l: l.append(dict(l[2])),
            'an unknown crossing type': lambda l: l[2].update(type='dance'),
            'a backward interval': lambda l: l[2].update(interval=[11.0, 9.0]),
            'an unknown line': lambda l: l.append({'kind': 'note'}),
            'a name as the labeller': lambda l: l[0].update(labeller='Alice'),
            'blind as a word': lambda l: l[0].update(blind='yes'),
            'a hash that is not one': lambda l: l[0].update(observations_sha256=12),
        }
        for name, change in cases.items():
            with self.subTest(name):
                with self.assertRaises(truth.TruthError):
                    truth.parse(broken(change))


class TakeLinksTest(unittest.TestCase):
    OBS = [{'obs_id': 'o1', 'capture_ts': 10.0, 'kind': 'keypoints', 'validity': 'valid',
            'room_position': {'x': 1.0, 'y': 1.0, 'z': 1.0, 'cov': [[0.01, 0, 0], [0, 0.01, 0], [0, 0, 0.01]]}},
           {'obs_id': 'o2', 'capture_ts': 10.0, 'kind': 'person_box', 'validity': 'rejected:too_small'},
           {'obs_id': 'o3', 'capture_ts': 10.0, 'kind': 'object_box', 'validity': 'valid'},
           {'obs_id': 'o4', 'capture_ts': 10.0, 'kind': 'person_box', 'validity': 'valid'}]

    def lines(self, ids=('o1',), status='confident', track='t1'):
        return [{'track_id': track, 'association': {'status': status}, 'last_obs_ids': list(ids)}]

    def test_links_carry_status_and_rejected_is_left_out(self):
        t = truth.parse([good_truth_lines()[0], {'kind': 'frame', 'capture_ts': 10.0, 'labels': {'o1': 'A'}}])
        links, left = score.take_links(self.OBS, self.lines(status='ambiguous'), t)
        self.assertEqual(links, [(10.0, 'o1', 'A', 't1', False)])
        self.assertEqual(left, {'rejected': 1, 'no_position': 1, 'unlabelled': 0})

    def test_unlabelled_is_counted(self):
        t = truth.parse([good_truth_lines()[0]])
        _, left = score.take_links(self.OBS, self.lines(), t)
        self.assertEqual(left['unlabelled'], 1)

    def test_refuses_a_label_for_nothing_and_a_wrong_frame(self):
        for frame in ({'kind': 'frame', 'capture_ts': 10.0, 'labels': {'nope': 'A'}},
                      {'kind': 'frame', 'capture_ts': 11.0, 'labels': {'o1': 'A'}}):
            t = truth.parse([good_truth_lines()[0], frame])
            with self.assertRaises(score.ScoreError):
                score.take_links(self.OBS, self.lines(), t)

    def test_refuses_a_track_listing_an_unknown_observation_or_two_tracks_on_one(self):
        t = truth.parse([good_truth_lines()[0]])
        with self.assertRaises(score.ScoreError):
            score.take_links(self.OBS, self.lines(ids=('nope',)), t)
        with self.assertRaises(score.ScoreError):
            score.take_links(self.OBS, self.lines() + self.lines(track='t2'), t)


class FaceTest(unittest.TestCase):
    def test_needs_nose_and_both_eyes(self):
        good = [[0, 0, 0.9]] * 3 + [[0, 0, 0.1]] * 14
        low = [[0, 0, 0.9], [0, 0, 0.1], [0, 0, 0.9]] + [[0, 0, 0.9]] * 14
        missing = [[None, None, None]] * 17
        self.assertTrue(score.face_visible({'keypoints': good}, 0.4))
        self.assertFalse(score.face_visible({'keypoints': low}, 0.4))
        self.assertFalse(score.face_visible({'keypoints': missing}, 0.4))
        self.assertFalse(score.face_visible({}, 0.4))


if __name__ == '__main__':
    unittest.main()
