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

    def test_a_second_box_of_one_person_in_a_frame_is_a_false_positive_not_also_a_miss(self):
        """A person is one instance per frame. The box the track did not take claims someone who is not there."""
        links = [(float(k), 'a%d' % k, 'A', 1, True) for k in range(10)] + [(5.0, 'dup', 'A', 2, False)]
        c = scorer_core.id_counts(links)
        self.assertEqual((c['idtp'], c['idfp'], c['idfn']), (10, 1, 0))
        self.assertEqual(c['mapping'], {'A': 1})

    def test_a_second_box_nobody_tracked_costs_nothing(self):
        links = [(float(k), 'a%d' % k, 'A', 1, True) for k in range(10)] + [(5.0, 'dup', 'A', None, False)]
        c = scorer_core.id_counts(links)
        self.assertEqual((c['idtp'], c['idfp'], c['idfn']), (10, 0, 0))

    def test_a_person_seen_twice_in_a_frame_on_no_track_is_one_miss(self):
        links = [(0.0, 'a', 'A', None, False), (0.0, 'b', 'A', None, False)]
        c = scorer_core.id_counts(links)
        self.assertEqual((c['idtp'], c['idfp'], c['idfn']), (0, 0, 1))


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


def unsure_links(rows):
    """rows: [(time, track, confident)]: observations labelled cannot tell."""
    return [(float(t), 'u%d' % t, scorer_core.UNSURE, track, confident) for t, track, confident in rows]


class CannotTellTest(unittest.TestCase):
    """A switch across cannot-tell labels happened somewhere in the span, and the span's status decides how it reads."""

    def setUp(self):
        self.links = [(0.0, 'o0', 'A', 1, True), (1.0, 'o1', 'A', 1, True), (6.0, 'o6', 'B', 1, True)]

    def one(self, unsure_rows, links=None, **kw):
        found = scorer_core.switches(links or self.links, unsure_links(unsure_rows), **kw)
        self.assertEqual(len(found), 1)
        return found[0]

    def test_doubt_reported_during_the_swap_is_not_silent_because_the_labels_resume_later(self):
        s = self.one([(2, 1, False), (3, 1, False), (4, 1, True), (5, 1, True)])
        self.assertEqual((s['silent'], s['unresolved']), (False, True))

    def test_a_swap_labelled_cannot_tell_while_confident_is_not_hidden_by_later_doubt(self):
        links = self.links[:2] + [(6.0, 'o6', 'B', 1, False)]
        s = self.one([(2, 1, True), (3, 1, True), (4, 1, False)], links)
        self.assertEqual((s['silent'], s['unresolved']), (False, True))

    def test_confident_all_through_the_span_is_silent(self):
        s = self.one([(2, 1, True), (3, 1, True)])
        self.assertEqual((s['silent'], s['unresolved']), (True, False))

    def test_in_doubt_all_through_the_span_is_flagged(self):
        links = self.links[:2] + [(6.0, 'o6', 'B', 1, False)]
        s = self.one([(2, 1, False), (3, 1, False)], links)
        self.assertEqual((s['silent'], s['unresolved']), (False, False))

    def test_unresolved_can_be_read_as_silent(self):
        s = self.one([(2, 1, False), (3, 1, True)], unresolved_is_silent=True)
        self.assertEqual((s['silent'], s['unresolved']), (True, True))

    def test_a_span_on_another_track_or_before_the_old_person_does_not_count(self):
        s = self.one([(2, 2, False), (3, 2, False)])
        self.assertEqual((s['silent'], s['unresolved']), (True, False))
        s = self.one([(-1, 1, False)])
        self.assertEqual((s['silent'], s['unresolved']), (True, False))

    def test_the_same_person_after_a_span_is_no_switch(self):
        links = [(0.0, 'o0', 'A', 1, True), (6.0, 'o6', 'A', 1, True)]
        self.assertEqual(scorer_core.switches(links, unsure_links([(2, 1, False)])), [])

    def test_the_gate_reads_the_slot(self):
        rows = [(0.0, 'a0', 'A', 1, True), (0.0, 'b0', 'B', 2, True), (6.0, 'a6', 'B', 1, True), (6.0, 'b6', 'A', 2, True)]
        windows = [{'id': 'w', 'start': 0.0, 'end': 7.0}]
        unsure = unsure_links([(2, 1, False), (3, 2, False)])
        kept = scorer_core.summarize([scorer_core.score_take(rows, windows, 2, unsure)], 0.1)
        self.assertEqual((kept['silent_swaps'], kept['unresolved_switches'], kept['flagged_switches']), (0, 2, 2))
        self.assertTrue(kept['gate']['silent_swaps_zero'])
        strict = scorer_core.summarize([scorer_core.score_take(rows, windows, 2, unsure, True)], 0.1)
        self.assertEqual((strict['silent_swaps'], strict['unresolved_switches']), (2, 2))
        self.assertFalse(strict['gate']['passed'])

    def test_a_window_of_only_cannot_tell_labels_is_no_trial(self):
        links = [(100.0, 'a', 'A', 1, True), (100.0, 'b', 'B', 2, True)]
        unsure = unsure_links([(2, 1, True), (3, 2, True)])
        res = scorer_core.summarize([scorer_core.score_take(links, [{'id': 'w', 'start': 0.0, 'end': 7.0}], 2, unsure)], 0.9)
        self.assertEqual(res['crossings'], 0)
        self.assertFalse(res['gate']['passed'])


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


RULE = {'sides': 'take', 'min_scored': 2, 'contact_reach_m': 1.6, 'unseen_s': 0.6}
COAST_S = 6.0


def walkers(spans, gap=1.0):
    """spans: {person: [(from, to), ...]}, in seconds, one observation every 0.25 s. They stand gap m apart."""
    links, positions = [], {}
    tracks = {'A': 'trk-1', 'B': 'trk-2', 'C': 'trk-3'}
    for person, rows in spans.items():
        for start, end in rows:
            t = start
            while t <= end:
                oid = '%s-%.2f' % (person, t)
                links.append((t, oid, person, tracks[person], False))
                positions[oid] = (0.0 if person == 'A' else gap, 1.0)
                t += 0.25
    return sorted(links), positions


def trial_row(spans, kind='crossing', mark=(10.0, 12.0), pad=2.0, rule=None, coast=COAST_S, **kw):
    links, positions = walkers(spans, **kw)
    window = {'id': 'w', 'kind': kind, 'mark': list(mark), 'start': mark[0] - pad, 'end': mark[1] + pad}
    return scorer_core.score_take(links, [window], 2, trial=rule or RULE, positions=positions, max_unseen_s=coast)['windows'][0]


class TrialRuleTest(unittest.TestCase):
    """A crossing is a trial only if the people in it can be followed through the owner's mark."""

    def admitted(self, spans, **kw):
        row = trial_row(spans, **kw)
        return row['admitted'], row['excluded_because']

    def test_two_people_seen_all_through_are_a_trial(self):
        self.assertEqual(self.admitted({'A': [(0, 20)], 'B': [(0, 20)]}), (True, None))

    def test_a_person_gone_from_the_mark_to_the_end_of_the_take_is_no_trial(self):
        for sides in ('take', 'window'):
            ok, why = self.admitted({'A': [(0, 20)], 'B': [(0, 10)]}, rule=dict(RULE, sides=sides))
            self.assertFalse(ok, sides)
            self.assertIn('followed through', why)
            self.assertIn('B', why)

    def test_a_person_seen_only_after_the_mark_is_no_trial(self):
        self.assertFalse(self.admitted({'A': [(0, 20)], 'B': [(13, 20)]})[0])

    def test_one_person_before_and_the_other_after_is_no_trial(self):
        self.assertFalse(self.admitted({'A': [(9.5, 9.5)], 'B': [(12.5, 12.5)]})[0])
        self.assertFalse(self.admitted({'A': [(0, 9.5)], 'B': [(12.5, 20)]})[0], 'nobody is ever seen on both sides')

    def test_a_person_hidden_past_the_window_counts_only_under_take(self):
        spans = {'A': [(0, 20)], 'B': [(0, 10), (15, 20)]}
        self.assertTrue(self.admitted(spans, rule=dict(RULE, sides='take'))[0])
        self.assertFalse(self.admitted(spans, rule=dict(RULE, sides='window'))[0])

    def test_unseen_across_the_mark_for_longer_than_a_track_coasts_is_no_trial_under_either(self):
        """The detector stalls: nobody is seen after the contact while the window lasts, and the tracks end before they are back."""
        for name, spans in (('both', {'A': [(0, 10), (20, 40)], 'B': [(0, 10), (20, 40)]}),
                            ('one', {'A': [(0, 40)], 'B': [(0, 10), (20, 40)]}),
                            ('from before the mark', {'A': [(0, 40)], 'B': [(0, 9), (17, 40)]}),
                            ('until after the mark', {'A': [(0, 40)], 'B': [(0, 11), (17.25, 40)]})):
            for sides in ('take', 'window'):
                with self.subTest(lost=name, sides=sides):
                    ok, why = self.admitted(spans, rule=dict(RULE, sides=sides))
                    self.assertFalse(ok)
                    self.assertIn('followed through', why)

    def test_the_refusal_says_it_was_the_coast_and_who_went_unseen(self):
        row = trial_row({'A': [(0, 40)], 'B': [(0, 10), (20, 40)]})
        self.assertEqual(row['followed'], ['A'])
        self.assertEqual(row['longest_unseen_s'], {'A': 0.25, 'B': 10.0})
        self.assertIn('max_coast_s', row['excluded_because'])
        self.assertIn('B', row['excluded_because'])

    def test_unseen_for_no_longer_than_a_track_coasts_is_a_trial(self):
        self.assertTrue(self.admitted({'A': [(0, 40)], 'B': [(0, 10), (16, 40)]})[0], '6 s: the track is still there to be taken')
        self.assertFalse(self.admitted({'A': [(0, 40)], 'B': [(0, 10), (16.25, 40)]})[0], '6.25 s: it ended')

    def test_the_limit_is_the_coast_it_is_given(self):
        spans = {'A': [(0, 40)], 'B': [(0, 10), (20, 40)]}
        self.assertTrue(self.admitted(spans, coast=12.0)[0])
        self.assertFalse(self.admitted(spans, coast=9.0)[0])

    def test_a_long_gap_that_does_not_touch_the_mark_does_not_count(self):
        self.assertTrue(self.admitted({'A': [(0, 40)], 'B': [(0, 1), (9, 40)]})[0], 'unseen long before the mark')
        self.assertTrue(self.admitted({'A': [(0, 40)], 'B': [(0, 13), (30, 40)]})[0], 'unseen long after the mark')

    def test_an_occlusion_longer_than_a_track_coasts_is_no_trial(self):
        ok, why = self.admitted({'A': [(0, 10), (19, 30)], 'B': [(0, 30)]}, kind='occlusion', gap=5.6)
        self.assertFalse(ok)
        self.assertIn('max_coast_s', why)
        self.assertTrue(self.admitted({'A': [(0, 10), (15, 30)], 'B': [(0, 30)]}, kind='occlusion', gap=5.6)[0])

    def test_a_door_with_nobody_seen_for_longer_than_a_track_coasts_is_no_trial(self):
        spans = {'A': [(0, 8.5)], 'B': [(15, 30)]}
        ok, why = self.admitted(spans, kind='door', gap=5.6, mark=(10.0, 13.5))
        self.assertFalse(ok, '6.5 s between the last of one and the first of the other, so no track was there to take')
        self.assertIn('max_coast_s', why)
        self.assertTrue(self.admitted({'A': [(0, 9)], 'B': [(15, 30)]}, kind='door', gap=5.6, mark=(10.0, 13.5))[0])

    def test_a_contact_distance_that_cannot_be_read_is_no_trial(self):
        links, _ = walkers({'A': [(0, 20)], 'B': [(0, 20)]})
        window = {'id': 'w', 'kind': 'crossing', 'mark': [10.0, 12.0], 'start': 8.0, 'end': 14.0}
        row = scorer_core.score_take(links, [window], 2, trial=RULE, positions={})['windows'][0]
        self.assertFalse(row['admitted'])
        self.assertIn('unknown', row['excluded_because'])

    def test_seen_only_outside_the_window_is_no_trial_under_either(self):
        spans = {'A': [(0, 7), (15, 20)], 'B': [(0, 7), (15, 20)]}
        for sides in ('take', 'window'):
            self.assertFalse(self.admitted(spans, rule=dict(RULE, sides=sides))[0], sides)

    def test_seen_plenty_outside_the_window_and_barely_in_it_is_no_trial_under_take(self):
        """Each person is seen before and after, far from the contact, and once in the window around it."""
        spans = {'A': [(0, 7), (9.5, 9.5), (15, 20)], 'B': [(0, 7), (12.5, 12.5), (15, 20)]}
        ok, why = self.admitted(spans)
        self.assertFalse(ok)
        self.assertIn('1 in the window', why)
        self.assertTrue(self.admitted({'A': [(0, 7), (9, 9.5), (15, 20)], 'B': [(0, 7), (12.5, 13), (15, 20)]})[0])

    def test_fewer_than_min_scored_on_a_side_is_no_trial(self):
        spans = {'A': [(0, 20)], 'B': [(8, 8), (10, 20)]}          # one sighting of B before the mark, at 8
        self.assertFalse(self.admitted(spans)[0])
        self.assertTrue(self.admitted(spans, rule=dict(RULE, min_scored=1))[0])

    def test_two_boxes_of_one_person_in_a_frame_are_one_sighting(self):
        links, positions = walkers({'A': [(0, 20)], 'B': [(10, 20)]})
        links = sorted(links + [(8.0, 'b1', 'B', 'trk-2', False), (8.0, 'b2', 'B', 'trk-3', False)])
        positions.update(b1=(1.0, 1.0), b2=(1.0, 1.0))
        window = {'id': 'w', 'kind': 'crossing', 'mark': [10.0, 12.0], 'start': 8.0, 'end': 14.0}
        self.assertFalse(scorer_core.score_take(links, [window], 2, trial=RULE, positions=positions)['windows'][0]['admitted'])

    def test_people_who_never_came_near_are_no_trial(self):
        ok, why = self.admitted({'A': [(0, 20)], 'B': [(0, 20)]}, gap=5.6)
        self.assertFalse(ok)
        self.assertIn('1.6', why)
        self.assertTrue(self.admitted({'A': [(0, 20)], 'B': [(0, 20)]}, gap=1.5)[0])

    def test_contact_is_judged_inside_the_window_only(self):
        links, positions = walkers({'A': [(0, 40)], 'B': [(0, 40)]}, gap=5.6)
        for link in links:          # they meet at t 30, long after the window
            if link[2] == 'B' and link[0] >= 30:
                positions[link[1]] = (1.0, 1.0)
        window = {'id': 'w', 'kind': 'crossing', 'mark': [10.0, 12.0], 'start': 8.0, 'end': 14.0}
        self.assertFalse(scorer_core.score_take(links, [window], 2, trial=RULE, positions=positions)['windows'][0]['admitted'])

    def test_contact_while_both_are_hidden_is_read_between_the_sightings(self):
        links, positions = [], {}
        for k in range(80):
            t = k * 0.25
            for person, x, track in (('A', -4.0 + t * 0.4, 'trk-1'), ('B', 4.0 - t * 0.4, 'trk-2')):
                if 9.0 < t < 12.0:
                    continue
                oid = '%s%d' % (person, k)
                links.append((t, oid, person, track, False))
                positions[oid] = (x, 0.5)
        window = {'id': 'w', 'kind': 'crossing_occluded', 'mark': [9.5, 11.5], 'start': 7.5, 'end': 13.5}
        rule = dict(RULE, contact_reach_m=0.6)
        row = scorer_core.score_take(sorted(links), [window], 2, trial=rule, positions=positions)['windows'][0]
        self.assertTrue(row['admitted'], row['excluded_because'])
        self.assertLess(row['closest_m'], 0.6)

    def test_an_occlusion_needs_someone_out_of_sight_during_the_mark(self):
        seen = {'A': [(0, 20)], 'B': [(0, 20)]}
        ok, why = self.admitted(seen, kind='occlusion', gap=5.6)
        self.assertFalse(ok)
        self.assertIn('out of sight', why)
        self.assertTrue(self.admitted({'A': [(0, 10), (12, 20)], 'B': [(0, 20)]}, kind='occlusion', gap=5.6)[0],
                        'and the one who stays in view need not be near')

    def test_an_occlusion_outside_the_mark_is_no_occlusion(self):
        self.assertFalse(self.admitted({'A': [(0, 3), (5, 20)], 'B': [(0, 20)]}, kind='occlusion')[0])

    def test_an_occlusion_still_needs_two_people_followed(self):
        self.assertFalse(self.admitted({'A': [(0, 10), (12, 20)], 'B': [(0, 10)]}, kind='occlusion')[0])

    def test_a_door_is_one_person_leaving_and_another_coming_in(self):
        self.assertTrue(self.admitted({'A': [(0, 10)], 'B': [(12, 20)]}, kind='door', gap=5.6)[0])

    def test_a_door_with_both_people_in_view_throughout_is_no_trial(self):
        ok, why = self.admitted({'A': [(0, 20)], 'B': [(0, 20)]}, kind='door')
        self.assertFalse(ok)
        self.assertIn('door', why)

    def test_a_door_with_one_sighting_each_is_no_trial(self):
        self.assertFalse(self.admitted({'A': [(9.5, 9.5)], 'B': [(12.5, 12.5)]}, kind='door')[0])

    def test_the_row_says_what_was_seen_on_each_side(self):
        row = trial_row({'A': [(0, 20)], 'B': [(0, 10)]})
        self.assertEqual(row['followed'], ['A'])
        self.assertEqual(row['scored_sides']['B'], {'window': 9, 'before': 40, 'after': 0})
        self.assertEqual(trial_row({'A': [(0, 20)], 'B': [(0, 20)]})['closest_m'], 1.0)

    def test_a_silent_swap_is_a_trial_whatever_the_window_holds(self):
        links = [(8.0, 'a', 'A', 'trk-1', True), (13.0, 'b', 'B', 'trk-1', True)]
        window = {'id': 'w', 'kind': 'crossing', 'mark': [10.0, 12.0], 'start': 8.0, 'end': 14.0}
        row = scorer_core.score_take(links, [window], 2, trial=RULE, positions={})['windows'][0]
        self.assertEqual((row['silent_swaps'], row['admitted']), (1, True))

    def test_a_window_with_no_mark_or_no_rule_needs_only_two_people(self):
        links, positions = walkers({'A': [(9, 9)], 'B': [(9, 9)]})
        row = scorer_core.score_take(links, [{'id': 'w', 'start': 8.0, 'end': 10.0}], 2, trial=RULE, positions=positions)['windows'][0]
        self.assertTrue(row['admitted'])

    def test_a_rule_that_checks_nothing_is_named(self):
        self.assertIsNone(scorer_core.trial_problem(RULE))
        self.assertIsNone(scorer_core.trial_problem(dict(RULE, sides='window')))
        for bad in (None, 5, [], {}, dict(RULE, sides='either'), dict(RULE, min_scored=0), dict(RULE, min_scored=1.5),
                    dict(RULE, min_scored=True), dict(RULE, contact_reach_m=0), dict(RULE, contact_reach_m='2'),
                    dict(RULE, unseen_s=-1), dict(RULE, contact_reach_m=float('inf'))):
            with self.subTest(rule=bad):
                self.assertIn('trial_rule', scorer_core.trial_problem(bad))


def tracks_from(links, when, tracks, confident=False):
    """The same links, from time `when` on with each person on the track `tracks` gives, and that confidence."""
    return [(t, o, p, tracks[p] if t >= when else k, confident if t >= when else c) for t, o, p, k, c in links]


SWAPPED = {'A': 'trk-2', 'B': 'trk-1'}


class ReadPastWindowTest(unittest.TestCase):
    """A followed person's sightings may lie outside the window, and those are where a trial's outcome shows."""

    def scored(self, links, positions, mark=(10.0, 12.0), rule=None):
        window = {'id': 'w', 'kind': 'crossing', 'mark': list(mark), 'start': mark[0] - 2.0, 'end': mark[1] + 2.0}
        take = scorer_core.score_take(links, [window], 2, trial=rule or RULE, positions=positions, max_unseen_s=COAST_S)
        return take, take['windows'][0]

    def counts(self, row):
        return row['idtp'], row['idfp'], row['idfn']

    def test_a_swap_seen_only_after_the_window_closes_costs_idf1_and_is_its_switch(self):
        links, positions = walkers({'A': [(0, 10), (15, 20)], 'B': [(0, 10), (15, 20)]})
        take, row = self.scored(tracks_from(links, 15, SWAPPED), positions)
        self.assertTrue(row['admitted'], row['excluded_because'])
        self.assertEqual(self.counts(row), (18, 4, 4), 'nine frames each in the window, and the first two each past it')
        self.assertLess(row['idf1'], 0.9)
        self.assertEqual((row['switches'], row['flagged_switches']), (2, 2))
        self.assertEqual(take['outside']['switches'], 0, 'read by the crossing, so not counted again outside it')
        self.assertEqual({s['time'] for s in row['switch_detail']}, {15.0})

    def test_a_swap_across_a_stall_that_began_before_the_window_opened_costs_idf1(self):
        links, positions = walkers({'A': [(0, 7), (13, 20)], 'B': [(0, 7), (13, 20)]})
        _, row = self.scored(tracks_from(links, 13, SWAPPED), positions)
        self.assertTrue(row['admitted'], row['excluded_because'])
        self.assertEqual(self.counts(row), (10, 4, 4), 'five frames each in the window, and the last two each before it')
        self.assertLess(row['idf1'], 0.9)

    def test_back_on_their_own_tracks_costs_nothing(self):
        for spans in ({'A': [(0, 10), (15, 20)], 'B': [(0, 10), (15, 20)]}, {'A': [(0, 7), (13, 20)], 'B': [(0, 7), (13, 20)]}):
            take, row = self.scored(*walkers(spans))
            self.assertEqual((row['idfp'], row['idfn'], row['switches'], take['outside']['switches']), (0, 0, 0, 0))
            self.assertEqual(row['idf1'], 1.0)
            self.assertEqual(row['idtp'], 22 if spans['A'][0][1] == 10 else 14, 'the sightings outside the window were read')

    def test_a_person_seen_enough_in_the_window_is_read_no_further_under_either_side(self):
        links, positions = walkers({'A': [(0, 20)], 'B': [(0, 20)]})
        for sides in ('take', 'window'):
            with self.subTest(sides=sides):
                take, row = self.scored(tracks_from(links, 15, SWAPPED), positions, rule=dict(RULE, sides=sides))
                self.assertEqual(self.counts(row), (50, 0, 0))
                self.assertEqual((row['switches'], take['outside']['switches']), (0, 2))

    def test_one_sighting_in_the_window_and_one_past_it(self):
        spans = {'A': [(0, 10), (14, 14), (16, 20)], 'B': [(0, 10), (14, 14), (16, 20)]}
        links, positions = walkers(spans)
        _, row = self.scored(tracks_from(links, 16, SWAPPED), positions)
        self.assertEqual(self.counts(row), (20, 2, 2), 'only the one each past the window, at 16')

    def test_only_the_nearest_sightings_are_read(self):
        links, positions = walkers({'A': [(0, 10), (15, 20)], 'B': [(0, 10), (15, 20)]})
        take, row = self.scored(tracks_from(links, 15.5, SWAPPED), positions)
        self.assertEqual(self.counts(row), (22, 0, 0), 'the swap comes at the third sighting back: a later matter, counted outside')
        self.assertEqual((row['switches'], take['outside']['switches']), (0, 2))

    def test_how_many_are_read_is_min_scored(self):
        links, positions = walkers({'A': [(0, 10), (15, 20)], 'B': [(0, 10), (15, 20)]})
        _, row = self.scored(tracks_from(links, 15, SWAPPED), positions, rule=dict(RULE, min_scored=3))
        self.assertEqual(self.counts(row), (18, 6, 6))

    def test_someone_not_followed_is_not_read_past_the_window(self):
        _, row = self.scored(*walkers({'A': [(0, 40)], 'B': [(0, 10), (20, 40)]}))
        self.assertEqual(row['followed'], ['A'])
        self.assertEqual(self.counts(row), (34, 0, 0), 'twenty-five frames of A and nine of B, all in the window')

    def test_a_silent_swap_past_the_window_counts_in_the_crossing_and_once(self):
        links, positions = walkers({'A': [(0, 10), (15, 20)], 'B': [(0, 10), (15, 20)]})
        take, row = self.scored(tracks_from(links, 15, SWAPPED, confident=True), positions)
        self.assertEqual(row['silent_swaps'], 2)
        self.assertEqual(take['outside']['silent_swaps'], 0)
        summary = scorer_core.summarize([take], 0.9)
        self.assertEqual((summary['silent_swaps'], summary['silent_swaps_outside_crossings'], summary['crossings_with_silent_swap']),
                         (2, 0, 1))
        self.assertFalse(summary['gate']['passed'])

    def test_two_boxes_of_one_person_past_the_window_are_one_sighting(self):
        links, positions = walkers({'A': [(0, 10), (15, 20)], 'B': [(0, 10), (15, 20)]})
        links = sorted(links + [(15.0, 'a2', 'A', 'trk-1', False)])
        positions['a2'] = (0.0, 1.0)
        _, row = self.scored(links, positions)
        self.assertEqual(self.counts(row), (22, 1, 0), 'A at 15 and 15.25, and the second box a false positive, as in a window')

    def test_the_mapping_rung_5_reads_stays_the_tracks_in_the_window(self):
        links, positions = walkers({'A': [(0, 8.25), (13.25, 20)], 'B': [(0, 8.25), (13.25, 20)]})
        for new in ({'A': 'trk-0a', 'B': 'trk-0b'}, {'A': 'trk-8', 'B': 'trk-9'}):
            with self.subTest(new=new):
                _, row = self.scored(tracks_from(links, 13.25, new), positions, mark=(10.0, 11.0))
                self.assertTrue(row['admitted'], row['excluded_because'])
                self.assertEqual(self.counts(row), (4, 4, 4))
                self.assertEqual(row['mapping'], {'A': 'trk-1', 'B': 'trk-2'})


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

    def test_cannot_tell_is_its_own_label_and_not_a_person(self):
        lines = good_truth_lines()
        lines[1]['labels']['o3'] = '?'
        t = truth.parse(lines)
        self.assertEqual(t.unsure, {'o3'})
        self.assertNotIn('o3', t.labels)
        self.assertEqual(t.frame_ts['o3'], 10.0)

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
            'cannot tell as a person': lambda l: l[0].update(people=['A', '?']),
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
        self.assertEqual(left, {'rejected': 1, 'no_position': 1, 'unlabelled': 0, 'unsure': 0})

    def test_cannot_tell_is_left_out_and_counted_and_keeps_its_status_for_the_switch(self):
        t = truth.parse([good_truth_lines()[0], {'kind': 'frame', 'capture_ts': 10.0, 'labels': {'o1': '?'}}])
        links, left = score.take_links(self.OBS, self.lines(status='ambiguous'), t)
        self.assertEqual(links, [])
        self.assertEqual((left['unsure'], left['unlabelled']), (1, 0))
        self.assertEqual(score.unsure_links(self.OBS, self.lines(status='ambiguous'), t),
                         [(10.0, 'o1', scorer_core.UNSURE, 't1', False)])

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
