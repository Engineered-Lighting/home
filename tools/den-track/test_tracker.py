"""Tracker behaviour on synthetic scenes and on hand-made frames.

- clean crossings keep their ids;
- an ambiguous crossing lowers confidence instead of swapping silently;
- a person hidden behind the armchair comes back on the same track;
- people enter and leave by the door; a newcomer gets a new id;
- the same input always gives the same tracks, and no output looks ahead.
"""
import unittest

import method
import pipeline
import synth
import tracker_core

VALUES = dict(method.Method().values, room=synth.ROOM_MODEL)


def run_scene(name, seed, noise='typical'):
    take_id, recs, truth = pipeline.synthetic_take(synth.scenario(name, seed, noise), VALUES)
    return recs, truth, pipeline.score_takes([(take_id, recs, truth)], VALUES)


def tracks_of(recs, truth, person, start=None, end=None):
    """The scored tracks that held this person's observations."""
    ids = {o for o, (ts, p) in truth['labels'].items() if p == person and
           (start is None or ts >= start) and (end is None or ts <= end)}
    return {r['track_id'] for r in recs if r['state'] == 'confirmed' and ids & set(r['last_obs_ids'])}


def walker(t0, xy0, v, ts, R=0.01):
    """One frame per time in ts of a person at constant velocity."""
    return [{'ids': ['o-%s-%d' % (t0, k)], 'xy': (xy0[0] + v[0] * (t - ts[0]), xy0[1] + v[1] * (t - ts[0])),
             'R': [[R, 0.0], [0.0, R]], 'heading': None} for k, t in enumerate(ts)]


class CleanCrossingTest(unittest.TestCase):
    def test_clean_crossings_keep_ids(self):
        for noise in ('clean', 'typical'):
            for seed in range(8):
                recs, truth, rep = run_scene('clean_crossing', seed, noise)
                w = rep['windows'][0]
                with self.subTest(noise=noise, seed=seed):
                    self.assertEqual(w['switches'], 0)
                    self.assertGreaterEqual(w['idf1'], 0.85)
                    self.assertEqual(len(tracks_of(recs, truth, 'A', w['start'], w['end'])), 1)
                    self.assertEqual(len(tracks_of(recs, truth, 'B', w['start'], w['end'])), 1)

    def test_clean_crossings_stay_mostly_confident(self):
        shares = [run_scene('clean_crossing', seed, 'clean')[2]['ambiguous_share'] for seed in range(8)]
        self.assertLess(sum(shares) / len(shares), 0.1)


class AmbiguousCrossingTest(unittest.TestCase):
    def test_touching_crossings_are_never_silent(self):
        flagged = 0
        for seed in range(10):
            recs, truth, rep = run_scene('touching_crossing', seed)
            w = rep['windows'][0]
            with self.subTest(seed=seed):
                self.assertEqual(w['silent_swaps'], 0)
                for s in w['switch_detail']:
                    self.assertIs(s['confident'], False)
            inside = [r for r in recs if w['start'] <= r['capture_ts'] <= w['end'] and r['state'] != 'ended']
            if any(r['association']['status'] == 'ambiguous' for r in inside):
                flagged += 1
        self.assertGreaterEqual(flagged, 9)

    def test_meeting_point_lowers_confidence_and_names_the_other_track(self):
        """A and B walk into the same spot from opposite sides and on out:
        geometry cannot tell whether they passed or bounced."""
        p = dict(VALUES['tracker'])
        tr = tracker_core.TrackerCore(p)
        ts = [100.0 + 0.25 * k for k in range(17)]
        a = walker('a', (-2.0, 3.0), (1.0, 0.0), ts)
        b = walker('b', (2.0, 3.05), (-1.0, 0.0), ts)
        seen = []
        for k, t in enumerate(ts):
            live, _ = tr.step(t, [a[k], b[k]])
            seen.append({x.id: (x.ambiguous, tr.hypotheses(x)) for x in live})
        ids = sorted(seen[-1])
        self.assertEqual(len(ids), 2)
        meet = seen[8]
        for tid in ids:
            amb, hyps = meet[tid]
            self.assertTrue(amb, 'track %s should be ambiguous where the two meet' % tid)
            self.assertEqual(hyps[0]['hypothesis'], 'same')
            self.assertAlmostEqual(sum(h['p'] for h in hyps), 1.0, places=9)
            other = [h for h in hyps if h['hypothesis'] == 'swapped']
            self.assertEqual([h['track_id'] for h in other], [i for i in ids if i != tid])
            self.assertGreater(other[0]['p'], 0.0)

    def test_far_apart_people_stay_confident(self):
        tr = tracker_core.TrackerCore(dict(VALUES['tracker']))
        ts = [100.0 + 0.25 * k for k in range(17)]
        a = walker('a', (-2.0, 1.5), (0.5, 0.0), ts)
        b = walker('b', (2.0, 4.5), (-0.5, 0.0), ts)
        for k, t in enumerate(ts):
            live, _ = tr.step(t, [a[k], b[k]])
            for x in live:
                self.assertFalse(x.ambiguous)
                self.assertEqual(tr.hypotheses(x), [])


class OcclusionTest(unittest.TestCase):
    def test_reacquired_after_the_armchair(self):
        same = 0
        for seed in range(10):
            recs, truth, rep = run_scene('armchair_occlusion', seed)
            w = rep['windows'][0]
            with self.subTest(seed=seed):
                self.assertEqual(w['switches'], 0)
            same += len(tracks_of(recs, truth, 'A')) == 1
        # A coasting track's gate is wide; now and then it takes a false
        # detection (flagged ambiguous) and A comes back on a new track.
        self.assertGreaterEqual(same, 9)

    def test_hidden_person_coasts_and_comes_back_on_the_same_id(self):
        p = dict(VALUES['tracker'], coast_after_s=0.5)
        ts = [100.0 + 0.25 * k for k in range(30)]
        a = walker('a', (-2.0, 3.0), (0.6, 0.0), ts)
        hidden = set(range(10, 18))           # 2 s behind something
        frames = [(t, 'cam', [] if k in hidden else [a[k]]) for k, t in enumerate(ts)]
        frames = [f for k, f in enumerate(frames) if k not in hidden]      # no line while nobody is seen
        tr = tracker_core.TrackerCore(p)
        states = []
        for t, _, ms in pipeline.with_ticks(frames):
            live, ended = tr.step(t, ms)
            self.assertEqual(ended, [])
            states.append((t, [(x.id, x.state) for x in live]))
        during = [s for t, s in states if ts[11] <= t <= ts[17]]
        self.assertTrue(during, 'ticks should fill the gap')
        self.assertTrue(all(s == [('T1', 'coasting')] for s in during[1:]), during)
        self.assertEqual(states[-1][1], [('T1', 'confirmed')])


class EntryExitTest(unittest.TestCase):
    def test_leaving_by_the_door_ends_the_track(self):
        whole = 0
        for seed in range(10):
            recs, truth, rep = run_scene('entry_exit', seed)
            a_tracks = tracks_of(recs, truth, 'A')
            with self.subTest(seed=seed):
                self.assertEqual(rep['silent_swaps'] + rep['silent_swaps_outside_windows'], 0)
                last_a = max(a_tracks, key=lambda tid: max(i for i, r in enumerate(recs) if r['track_id'] == tid))
                ended = [r for r in recs if r['state'] == 'ended' and r['track_id'] == last_a]
                self.assertEqual(len(ended), 1, 'A left by the door; the track should end')
                self.assertEqual(ended[0]['last_obs_ids'], [])
            whole += len(a_tracks) == 1 and len(tracks_of(recs, truth, 'B')) == 1
        # B sits while A walks past in front; now and then B's hidden track
        # drifts and B comes back on a new one (a fragment, never a swap).
        self.assertGreaterEqual(whole, 8)

    def test_newcomer_at_the_door_gets_a_new_id(self):
        for seed in range(5):
            recs, truth, rep = run_scene('swap_at_door', seed)
            a, b = tracks_of(recs, truth, 'A'), tracks_of(recs, truth, 'B')
            with self.subTest(seed=seed):
                self.assertEqual(rep['silent_swaps'] + rep['silent_swaps_outside_windows'], 0)
                self.assertFalse(a & b, 'B took over A\'s track')

    def test_track_ids_are_never_reused(self):
        recs, _, _ = run_scene('swap_at_door', 0, 'noisy')
        ended = [r['track_id'] for r in recs if r['state'] == 'ended']
        self.assertEqual(len(ended), len(set(ended)))
        last_end = {}
        for i, r in enumerate(recs):
            if r['state'] == 'ended':
                last_end[r['track_id']] = i
        for i, r in enumerate(recs):
            if r['track_id'] in last_end:
                self.assertLessEqual(i, last_end[r['track_id']])


class DeterminismTest(unittest.TestCase):
    def test_same_input_same_tracks(self):
        scene = synth.scenario('crossing_behind_armchair', 3, 'noisy')
        one = pipeline.synthetic_take(scene, VALUES)[1]
        two = pipeline.synthetic_take(synth.scenario('crossing_behind_armchair', 3, 'noisy'), VALUES)[1]
        self.assertEqual(one, two)

    def test_causal_nothing_looks_ahead(self):
        obs, _ = synth.records(synth.scenario('touching_crossing', 4, 'typical'))
        frames = pipeline.observation_frames(obs, VALUES['input'])
        full = pipeline.track_frames(frames, VALUES)
        cut = frames[len(frames) // 2][0]
        part = pipeline.track_frames([f for f in frames if f[0] <= cut], VALUES)
        self.assertEqual(part, [r for r in full if r['capture_ts'] <= cut])

    def test_out_of_order_frames_are_refused(self):
        tr = tracker_core.TrackerCore(dict(VALUES['tracker']))
        tr.step(10.0, [])
        with self.assertRaises(ValueError):
            tr.step(9.0, [])


if __name__ == '__main__':
    unittest.main()
