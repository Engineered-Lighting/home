"""The tracker on scripted detections and synthetic scenes. Nothing here is real."""
import json
import random
import unittest

import associate
import copy
import kalman
import method
import records
import score
import scorer_core
import synth
import tracker_core
import truth


def params():
    return method.Method().tracker


def det(oid, x, y, sigma=0.05, heading=None):
    return {'id': oid, 'xy': (x, y), 'R': [[sigma ** 2, 0.0], [0.0, sigma ** 2]], 'obs_ids': [oid], 'heading': heading}


def walk(core, t0, dt, positions):
    """positions: one list of (x, y) per frame; returns the frames as {track id: (state, what it was then)}."""
    out = []
    for k, frame in enumerate(positions):
        t = t0 + k * dt
        dets = [det('o%04d-%d' % (k, i), x, y) for i, (x, y) in enumerate(frame)]
        out.append({tr.id: (state, copy.deepcopy(tr)) for tr, state in core.step(t, dets)})
    return out


def run_scene(name, seed=0, noise='typical', fps=4.0, with_boxes=False):
    data = synth.scenario(name, seed=seed, noise=noise, fps=fps)
    observations, frames, crossings = synth.to_records(data, with_boxes=with_boxes)
    x0, x1 = synth.ROOM['x']
    y0, y1 = synth.ROOM['y']
    lines, counts = records.run_tracker(observations, params(), floor=[x0, y0, x1, y1], exits=[list(synth.DOOR)])
    t = truth.Truth('%s-%d' % (name, seed), ['A', 'B'], {oid: p for _, row in frames for oid, p in row.items()},
                    {oid: ts for ts, row in frames for oid in row}, crossings)
    links, _ = score.take_links(observations, lines, t)
    return {'observations': observations, 'lines': lines, 'counts': counts, 'truth': t, 'links': links}


class KalmanTest(unittest.TestCase):
    def test_predict_moves_and_widens_update_narrows(self):
        x, P = kalman.initial((1.0, 2.0), [[0.01, 0], [0, 0.01]], 1.0)
        x[2] = 1.0
        x2, P2 = kalman.predict(x, P, 0.5, 0.65)
        self.assertAlmostEqual(x2[0], 1.5)
        self.assertGreater(P2[0][0], P[0][0])
        x3, P3 = kalman.update(x2, P2, (1.5, 2.0), [[0.01, 0], [0, 0.01]])
        self.assertLess(P3[0][0], P2[0][0])

    def test_a_coasting_mean_slows_but_its_doubt_does_not_shrink(self):
        x, P = kalman.initial((0.0, 0.0), [[0.01, 0], [0, 0.01]], 1.0)
        x[2] = 1.0
        _, full = kalman.predict(x, P, 0.5, 0.65, 1.0)
        slow, slowed = kalman.predict(x, P, 0.5, 0.65, 0.5)
        self.assertAlmostEqual(slow[2], 0.5)
        self.assertEqual(slowed, full)

    def test_covariance_stays_symmetric(self):
        x, P = kalman.initial((0.0, 0.0), [[0.02, 0.005], [0.005, 0.03]], 1.0)
        for _ in range(20):
            x, P = kalman.predict(x, P, 0.25, 0.65)
            x, P = kalman.update(x, P, (0.1, 0.1), [[0.02, 0.005], [0.005, 0.03]])
        self.assertAlmostEqual(P[0][1], P[1][0], places=12)


class AssociateTest(unittest.TestCase):
    def test_clear_links_have_no_rivals(self):
        choice, rivals = associate.associate([[5.0, 0.0], [0.0, 5.0]], [0.1, 0.1], [0.01, 0.01])
        self.assertEqual(choice, [0, 1])
        self.assertEqual(rivals, [{}, {}])

    def test_two_equal_tracks_name_each_other(self):
        choice, rivals = associate.associate([[5.0, 4.0], [4.0, 5.0]], [0.1, 0.1], [0.01, 0.01])
        self.assertEqual(choice, [0, 1])
        self.assertGreater(rivals[0][1], 0.3)
        self.assertGreater(rivals[1][0], 0.3)
        self.assertAlmostEqual(rivals[0][1], rivals[1][0])

    def test_only_a_coasting_track_doubts_a_newcomer(self):
        link = [[0.01]]
        _, in_view = associate.associate(link, [0.5], [0.01], [False])
        _, unseen = associate.associate(link, [0.5], [0.01], [True])
        self.assertEqual(in_view, [{}])
        self.assertGreater(unseen[0][None], 0.2)

    def test_a_cluster_too_big_reads_as_fully_ambiguous(self):
        choice, rivals = associate.associate([[5.0, 4.0], [4.0, 5.0]], [0.1, 0.1], [0.01, 0.01], limit=1)
        self.assertEqual(sorted(c for c in choice if c is not None), [0, 1])
        self.assertEqual(rivals, [{None: 1.0}, {None: 1.0}])


class CrossingTest(unittest.TestCase):
    def test_clean_crossings_keep_their_ids(self):
        for seed in range(12):
            run = run_scene('clean_crossing', seed=seed, noise='clean')
            self.assertEqual(scorer_core.switches(run['links']), [])
            self.assertEqual(run['counts']['tracks'], 2)
            c = scorer_core.id_counts(run['links'])
            self.assertEqual(scorer_core.idf1(c), 1.0)

    def test_an_ambiguous_crossing_lowers_confidence_instead_of_swapping_silently(self):
        flagged = 0
        for seed in range(12):
            run = run_scene('touching_crossing', seed=seed, noise='clean')
            switches = scorer_core.switches(run['links'])
            self.assertEqual([s for s in switches if s['silent']], [])
            flagged += len(switches)
            statuses = {l['association']['status'] for l in run['lines']}
            self.assertIn('ambiguous', statuses)
        self.assertGreater(flagged, 0, 'touching crossings should swap sometimes: the test must see one')

    def test_hypotheses_are_named_and_sum_to_one_while_ambiguous(self):
        run = run_scene('touching_crossing', seed=3, noise='clean')
        doubtful = [l for l in run['lines'] if l['association']['status'] == 'ambiguous']
        self.assertTrue(doubtful)
        names = set()
        for line in doubtful:
            hyps = line['association']['hypotheses']
            self.assertEqual(hyps[0]['hypothesis'], 'kept')
            self.assertAlmostEqual(sum(h['p'] for h in hyps), 1.0, places=2)
            names.update(h['hypothesis'].split(':')[0] for h in hyps)
        self.assertIn('swapped', names)
        for line in run['lines']:
            if line['association']['status'] == 'confident':
                self.assertEqual(line['association']['hypotheses'], [])

    def test_no_silent_swap_in_any_scene_on_clean_depth(self):
        for name in sorted(synth.SCENES):
            for seed in range(6):
                with self.subTest(scene=name, seed=seed):
                    run = run_scene(name, seed=seed, noise='clean')
                    self.assertEqual([s for s in scorer_core.switches(run['links']) if s['silent']], [])

    def test_status_is_ambiguous_through_the_contact_of_a_close_crossing(self):
        """Rung 5's gate wants association_safe false through every annotated crossing."""
        for seed in range(8):
            run = run_scene('close_crossing', seed=seed, noise='clean')
            start, end = run['truth'].crossings[0]['interval']
            for t, _, _, track, confident in run['links']:
                if start <= t <= end and track is not None:
                    self.assertFalse(confident, 'seed %d, t %.2f' % (seed, t))

    def test_status_returns_to_confident_and_since_ts_moves(self):
        core = tracker_core.TrackerCore(params())
        p = core.p
        dt = 0.25
        n = 6 + int(2 * p['reconfirm_s'] / dt)
        frames = [[(-3.0 + 0.5 * k, 0.0), (3.0 - 0.5 * k, 0.5)] for k in range(12)]
        frames += [[(frames[-1][0][0] + 0.5 * k, 0.0), (frames[-1][1][0] - 0.5 * k, 0.5)] for k in range(1, n)]
        lines = walk(core, 100.0, dt, frames)
        history = [(lines[k][1][1].ambiguous, lines[k][1][1].since_ts) for k in range(len(lines)) if 1 in lines[k]]
        statuses = [a for a, _ in history]
        self.assertIn(True, statuses)
        self.assertFalse(statuses[-1])
        first_doubt = statuses.index(True)
        self.assertGreater(history[-1][1], history[first_doubt][1])
        self.assertEqual(lines[-1][1][1].confidence, 1.0)


class OcclusionTest(unittest.TestCase):
    def test_re_acquisition_keeps_the_track_after_the_armchair(self):
        kept = 0
        for seed in range(10):
            run = run_scene('armchair_occlusion', seed=seed, noise='clean')
            a_tracks = {l[3] for l in run['links'] if l[2] == 'A'}
            if len(a_tracks) == 1:
                kept += 1
                tid = a_tracks.pop()
                states = [l['state'] for l in run['lines'] if l['track_id'] == tid]
                self.assertIn('coasting', states, 'a hidden person coasts')
                seen_after = states[states.index('coasting'):]
                self.assertIn('confirmed', seen_after, 'and comes back on the same track')
            self.assertEqual(scorer_core.switches(run['links']), [])
        self.assertGreaterEqual(kept, 8)

    def test_a_coasting_line_lists_no_observation_and_its_doubt_grows(self):
        core = tracker_core.TrackerCore(params())
        lines = walk(core, 100.0, 0.25, [[(0.0, 0.0)]] * 4 + [[(5.0, 5.0)]] * 4)
        spreads = []
        for frame in lines[4:]:
            state, tr = frame[1]
            self.assertEqual(tr.obs_ids, [])
            spreads.append(tr.P[0][0])
        self.assertEqual(spreads, sorted(spreads))
        self.assertEqual(lines[-1][1][0], 'coasting')

    def test_crossing_behind_the_armchair_flags_the_re_acquisition(self):
        for seed in range(8):
            run = run_scene('crossing_behind_armchair', seed=seed, noise='clean')
            self.assertEqual([s for s in scorer_core.switches(run['links']) if s['silent']], [])
            self.assertIn('ambiguous', {l['association']['status'] for l in run['lines']})

    def test_a_hidden_person_widens_the_reach_of_doubt(self):
        p = dict(params())
        core = tracker_core.TrackerCore(p)
        far = p['crossing_radius_m'] + 0.5     # beyond the radius, within it plus a little reach
        walk(core, 100.0, 0.25, [[(0.0, 0.0), (far, 0.0)]] * 4)
        lines = None
        t = 101.0
        for _ in range(8):
            lines = {tr.id: (state, tr) for tr, state in core.step(t, [det('b%.2f' % t, far, 0.0)])}
            t += 0.25
        self.assertTrue(lines[2][1].ambiguous, 'the seen one is in reach of the hidden one')


class EntryExitTest(unittest.TestCase):
    def test_a_person_who_leaves_ends_and_their_id_is_never_reused(self):
        run = run_scene('entry_exit', seed=2, noise='clean')
        ended = [l for l in run['lines'] if l['state'] == 'ended']
        self.assertTrue(ended)
        for line in ended:
            self.assertEqual(line['last_obs_ids'], [])
        order = [l['track_id'] for l in run['lines']]
        gone = {l['track_id'] for l in ended}
        for tid in gone:
            self.assertEqual(order[-1 - order[::-1].index(tid)], tid)
            last = len(order) - 1 - order[::-1].index(tid)
            self.assertEqual(run['lines'][last]['state'], 'ended', 'nothing after ended')

    def test_a_newcomer_at_the_door_after_a_silent_gap_gets_a_new_track(self):
        """No frame comes while nobody is seen; the old track's time ran out meanwhile."""
        door = (0.0, 0.0)
        core = tracker_core.TrackerCore(params(), exits=[door])
        walk(core, 100.0, 0.25, [[(0.3, 0.0)], [(0.25, 0.0)], [(0.2, 0.0)], [(0.15, 0.0)]])
        rows = core.step(102.0, [det('new', 0.2, 0.0)])
        by_state = {state: tr for tr, state in rows}
        self.assertEqual(by_state['ended'].id, 1)
        self.assertEqual(by_state['tentative'].id, 2)
        self.assertEqual(by_state['tentative'].obs_ids, ['new'])

    def test_an_unseen_track_stops_at_the_floor_edge_and_is_not_ended_there(self):
        p = params()
        core = tracker_core.TrackerCore(p, floor=(0.0, 0.0, 2.0, 2.0))
        frames = [[(1.0 + 0.2 * k, 1.0)] for k in range(4)]
        walk(core, 100.0, 0.25, frames + [[]] * 10)
        tr = core.tracks[0]
        self.assertLessEqual(tr.x[0], 2.0 + p['floor_margin_m'] + 1e-9)
        self.assertEqual(tr.state(core.t, p['coasting_after_s']), 'coasting')

    def test_a_long_gap_ends_a_track_before_it_can_take_anyone(self):
        p = params()
        core = tracker_core.TrackerCore(p)
        walk(core, 100.0, 0.25, [[(1.0, 1.0)]] * 4)
        rows = core.step(101.0 + p['max_coast_s'] + 1.0, [det('later', 1.0, 1.0)])
        self.assertEqual(sorted((tr.id, s) for tr, s in rows), [(1, 'ended'), (2, 'tentative')])

    def test_a_single_false_detection_never_confirms(self):
        core = tracker_core.TrackerCore(params())
        lines = walk(core, 100.0, 0.25, [[(1.0, 1.0)], [], [], [], []])
        states = [frame[1][0] for frame in lines if 1 in frame]
        self.assertEqual(states[0], 'tentative')
        self.assertEqual(states[-1], 'ended')
        self.assertNotIn('confirmed', states)


class DeterminismTest(unittest.TestCase):
    def test_the_same_observations_give_the_same_bytes(self):
        a = run_scene('near_pass', seed=5)
        b = run_scene('near_pass', seed=5)
        self.assertEqual(json.dumps(a['lines'], sort_keys=True), json.dumps(b['lines'], sort_keys=True))

    def test_the_input_order_does_not_matter(self):
        run = run_scene('near_pass', seed=5)
        shuffled = list(run['observations'])
        random.Random(1).shuffle(shuffled)
        x0, x1 = synth.ROOM['x']
        y0, y1 = synth.ROOM['y']
        lines, _ = records.run_tracker(shuffled, params(), floor=[x0, y0, x1, y1], exits=[list(synth.DOOR)])
        self.assertEqual(lines, run['lines'])

    def test_causal_a_later_frame_never_changes_an_earlier_line(self):
        run = run_scene('crossing_behind_armchair', seed=4)
        times = sorted({o['capture_ts'] for o in run['observations']})
        cut = times[len(times) // 2]
        early = [o for o in run['observations'] if o['capture_ts'] <= cut]
        x0, x1 = synth.ROOM['x']
        y0, y1 = synth.ROOM['y']
        lines, _ = records.run_tracker(early, params(), floor=[x0, y0, x1, y1], exits=[list(synth.DOOR)])
        self.assertEqual(lines, run['lines'][:len(lines)])

    def test_frames_out_of_order_are_refused(self):
        core = tracker_core.TrackerCore(params())
        core.step(10.0, [])
        with self.assertRaises(ValueError):
            core.step(9.0, [])


class HeadingTest(unittest.TestCase):
    @staticmethod
    def keypoints(oid, ts, x, y, left, right, status='supported', with_hips=False):
        cov = [[0.001, 0, 0], [0, 0.001, 0], [0, 0, 0.001]]
        joints = [{'j': j, 'xyz': None, 'cov': None, 'status': 'unsupported', 'lift': 'none'} for j in range(17)]
        lift = 'stereo' if status == 'supported' else 'plane:1.05'
        joints[5] = {'j': 5, 'xyz': [left[0], left[1], 1.4], 'cov': cov, 'status': status, 'lift': lift}
        joints[6] = {'j': 6, 'xyz': [right[0], right[1], 1.4], 'cov': cov, 'status': status, 'lift': lift}
        if with_hips:
            joints[11] = {'j': 11, 'xyz': [left[0], left[1], 0.9], 'cov': cov, 'status': status, 'lift': lift}
            joints[12] = {'j': 12, 'xyz': [right[0], right[1], 0.9], 'cov': cov, 'status': status, 'lift': lift}
        return {'obs_id': oid, 'capture_ts': ts, 'source': {'sensor_id': 'c'}, 'kind': 'keypoints',
                'joint_set': 'coco17', 'joints_room': joints, 'validity': 'valid',
                'room_position': {'x': x, 'y': y, 'z': 1.0, 'cov': [[0.01, 0, 0], [0, 0.01, 0], [0, 0, 0.01]]}}

    def test_bearing_runs_clockwise_from_plus_y(self):
        # Facing +y: left at -x, right at +x. Facing +x: left at +y, right at -y.
        cases = {0.0: ((-0.2, 0.0), (0.2, 0.0)), 90.0: ((0.0, 0.2), (0.0, -0.2)),
                 180.0: ((0.2, 0.0), (-0.2, 0.0)), 270.0: ((0.0, -0.2), (0.0, 0.2))}
        for bearing, (left, right) in cases.items():
            deg, sd = records.body_heading(self.keypoints('o', 1.0, 0, 0, left, right))
            self.assertAlmostEqual(deg, bearing)
            self.assertLess(sd, 15.0)

    def test_more_pairs_mean_less_doubt(self):
        _, one = records.body_heading(self.keypoints('o', 1.0, 0, 0, (-0.2, 0), (0.2, 0)))
        _, two = records.body_heading(self.keypoints('o', 1.0, 0, 0, (-0.2, 0), (0.2, 0), with_hips=True))
        self.assertLess(two, one)

    def test_a_plane_lifted_or_unsupported_pair_is_no_evidence(self):
        self.assertIsNone(records.body_heading(self.keypoints('o', 1.0, 0, 0, (-0.2, 0), (0.2, 0), status='uncertain')))
        box = {'kind': 'person_box'}
        self.assertIsNone(records.body_heading(box))

    def test_heading_as_of_moves_only_on_accepted_evidence(self):
        L, R = (0.8, 1.0), (1.2, 1.0)
        box = {'obs_id': 'b', 'capture_ts': 2.0, 'source': {'sensor_id': 'c'}, 'kind': 'person_box', 'validity': 'valid',
               'room_position': {'x': 1.0, 'y': 1.0, 'z': 1.0, 'cov': [[0.01, 0, 0], [0, 0.01, 0], [0, 0, 0.01]]}}
        observations = [self.keypoints('k0', 0.0, 1, 1, L, R), self.keypoints('k1', 0.25, 1, 1, L, R),
                        self.keypoints('k2', 0.5, 1, 1, L, R), box]
        lines, _ = records.run_tracker(observations, params())
        self.assertEqual(lines[0]['heading_as_of'], 0.0)
        self.assertEqual(lines[2]['heading_as_of'], 0.5)
        self.assertEqual(lines[2]['heading']['deg'], 0.0)
        self.assertEqual(lines[3]['heading_as_of'], 0.5, 'a box is no shoulder and hip evidence')

    def test_heading_as_of_does_not_move_on_a_prediction_or_while_ambiguous(self):
        core = tracker_core.TrackerCore(params())
        t, first = 100.0, None
        for k in range(4):
            rows = core.step(t, [det('a%d' % k, 0.0, 0.0, heading=(90.0, 5.0)),
                                 det('b%d' % k, 6.0, 0.0, heading=(270.0, 5.0))])
            first = {tr.id: tr for tr, _ in rows}[1]
            t += 0.25
        as_of = first.heading_as_of
        self.assertEqual(as_of, t - 0.25)
        core.step(t, [det('b4', 6.0, 0.0)])         # the first track is unseen: a prediction only
        self.assertEqual(first.heading_as_of, as_of)
        x = 6.0
        for k in range(5, 40):                      # the second person walks up to the first
            t += 0.25
            x = max(0.5, x - 0.3)
            core.step(t, [det('a%d' % k, 0.0, 0.0, heading=(90.0, 5.0)),
                          det('b%d' % k, x, 0.0, heading=(270.0, 5.0))])
            if not first.ambiguous:
                as_of = t
            self.assertEqual(first.heading_as_of, as_of, 'whose shoulders they are is in doubt')
        self.assertTrue(first.ambiguous)

    def test_too_noisy_a_heading_is_not_accepted(self):
        core = tracker_core.TrackerCore(params())
        rows = core.step(1.0, [det('a', 0.0, 0.0, heading=(90.0, 60.0))])
        self.assertIsNone(rows[0][0].heading)
        self.assertIsNone(rows[0][0].heading_as_of)

    def test_synthetic_heading_follows_the_body(self):
        data = synth.scenario('clean_crossing', seed=1, noise='clean')
        true_facing = {o['id'] + '-k': o['facing'] for frame in data['frames'] for o in frame['obs']}
        run = run_scene('clean_crossing', seed=1, noise='clean')
        checked = 0
        for line in run['lines']:
            if line['heading'] and line['last_obs_ids']:
                obs = next(o for o in run['observations'] if o['obs_id'] == line['last_obs_ids'][0])
                if line['heading_as_of'] == obs['capture_ts']:
                    off = (line['heading']['deg'] - true_facing[obs['obs_id']] + 180.0) % 360.0 - 180.0
                    self.assertLess(abs(off), 25.0)
                    checked += 1
        self.assertGreater(checked, 10)


if __name__ == '__main__':
    unittest.main()
