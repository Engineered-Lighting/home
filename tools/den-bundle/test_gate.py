"""The gate checker: the fixtures pass, and each clause catches its own fault."""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fakes    # noqa: E402
import gate     # noqa: E402
import method   # noqa: E402
import records  # noqa: E402
import synth    # noqa: E402
from router import Router, run  # noqa: E402

M = method.load_method()
R = method.load_router()
ts = synth.ts


def closed(settings, maker):
    return maker({k: (v['proposed'] if method.is_open(v) else v) for k, v in settings.block.items()})


def routed(name, start=2.0, until=70.0, script=None, scene=None):
    scene = scene or synth.SCENES[name]()
    st = records.Store(scene['observations'], scene['tracks'])
    clock = fakes.FakeClock(ts(start))
    sender = fakes.FakeSender(clock, script=script)
    r = Router(st, M, R, clock, sender)
    recs = run(r, clock, sender, ts(until))
    return scene, st, r.bundles, recs


def failures(result, clause):
    return result['clauses'][clause]['failures']


class TestFixturesPass(unittest.TestCase):
    def test_every_fixture_passes_every_clause_when_routed(self):
        for name, start in (('crossing', 2.5), ('crossing', 4.5), ('missing_depth', 3.0),
                            ('stale_observation', 3.0), ('two_tracks', 2.0), ('track_leaves', 2.0)):
            scene, st, bundles, recs = routed(name, start=start, script={4: 'refused', 7: 'error'})
            res = gate.check(bundles, st, M, scene['crossings'], recs, rule=R)
            self.assertEqual(res['verdict'], 'pass', (name, {k: v['failures'] for k, v in res['clauses'].items() if v['failures']}))
            self.assertTrue(res['practice'])
            self.assertTrue(all(res['clauses'][c]['checked'] for c in gate.ROUTER_CLAUSES + ('window', 'no_wait')))

    def test_dense_bundles_over_the_crossing_pass(self):
        scene = synth.crossing()
        st = records.Store(scene['observations'], scene['tracks'])
        from assemble import assemble
        bundles = [assemble(st, M, tid, ts(k * 0.1)) for tid in ('A', 'B') for k in range(0, 131)]
        res = gate.check(bundles, st, M, scene['crossings'])
        self.assertEqual(res['verdict'], 'pass', {k: v['failures'][:3] for k, v in res['clauses'].items()})
        self.assertGreater(res['clauses']['crossing']['checked'], 100)


class TestBundleClausesCatchFaults(unittest.TestCase):
    def setUp(self):
        self.scene, self.st, self.bundles, self.recs = routed('two_tracks', until=30.0)

    def check(self, bundles=None, requests=None, crossings=()):
        return gate.check(bundles if bundles is not None else self.bundles, self.st, M, crossings, requests, rule=R)

    def test_a_tracker_that_misses_the_crossing_fails_the_crossing_clause(self):
        scene = synth.crossing()
        for line in scene['tracks']:
            line['association'] = {'status': 'confident', 'hypotheses': [], 'since_ts': ts(0.0)}
        _, st, bundles, recs = routed(None, start=2.5, until=12.0, scene=scene)
        res = gate.check(bundles, st, M, scene['crossings'], recs, rule=R)
        self.assertEqual(res['verdict'], 'fail')
        self.assertEqual(len(failures(res, 'crossing')), res['clauses']['crossing']['checked'])
        self.assertTrue(failures(res, 'crossing'))

    def test_wrong_window_length(self):
        b = copy.deepcopy(self.bundles)
        b[0]['interval'][0] -= 0.5
        self.assertTrue(failures(self.check(b), 'window'))

    def test_a_frame_outside_the_interval(self):
        b = copy.deepcopy(self.bundles)
        b[1]['positions'][0]['ts'] = b[1]['interval'][0] - 1.0
        self.assertTrue(failures(self.check(b), 'aligned'))

    def test_a_keypoint_entry_without_its_frame(self):
        b = copy.deepcopy(self.bundles)
        b[1]['frames'] = [f for f in b[1]['frames'] if f['ts'] != b[1]['keypoint_series'][0]['ts']]
        b[1]['others'] = []
        self.assertTrue(any('has no frame' in x for x in failures(self.check(b), 'aligned')))

    def test_an_odd_or_short_crop(self):
        b = copy.deepcopy(self.bundles)
        for f in b[2]['frames']:
            f['crop_ref'] = {'box': [f['crop_ref']['box'][0] + 1] + f['crop_ref']['box'][1:], 'from_ts': f['crop_ref']['from_ts']}
        self.assertTrue(failures(self.check(b), 'aligned'))
        b = copy.deepcopy(self.bundles)
        for f in b[2]['frames']:
            f['crop_ref'] = {'box': [1000, 400, 1002, 402], 'from_ts': f['crop_ref']['from_ts']}
        self.assertTrue(any('does not hold' in x for x in failures(self.check(b), 'aligned')))

    def test_a_changed_joint_a_dropped_entry_and_an_ungated_entry(self):
        b = copy.deepcopy(self.bundles)
        b[0]['keypoint_series'][0]['joints_room'][5]['xyz'][0] += 0.01
        self.assertTrue(any('joints_room differs' in x for x in failures(self.check(b), 'verbatim')))
        b = copy.deepcopy(self.bundles)
        del b[0]['keypoint_series'][0]
        self.assertTrue(any('is left out' in x for x in failures(self.check(b), 'verbatim')))
        b = copy.deepcopy(self.bundles)
        foreign = self.st.obs[synth.oid('B', 1.0)]
        b[0]['keypoint_series'].append({'obs_id': foreign['obs_id'], 'ts': foreign['capture_ts'], 'camera_id': 'den-oak',
                                        'transform_id': foreign['transform_id'], 'keypoints': foreign['keypoints'],
                                        'joints_room': foreign['joints_room']})
        self.assertTrue(any('not association-gated' in x for x in failures(self.check(b), 'verbatim')))

    def test_an_unflagged_missing_source(self):
        scene, st, bundles, recs = routed('missing_depth', start=3.0, until=8.0)
        bundles[1]['missing'] = []
        res = gate.check(bundles, st, M, (), recs, rule=R)
        self.assertEqual(failures(res, 'missing'), ['%s: depth is absent or stale but not flagged' % bundles[1]['bundle_id']])
        scene, st, bundles, recs = routed('stale_observation', start=3.0, until=8.0)
        bundles[-1]['missing'] = ['track']
        res = gate.check(bundles, st, M, (), recs, rule=R)
        self.assertEqual(len(failures(res, 'missing')), 2)

    def test_a_bundle_that_waited(self):
        r = copy.deepcopy(self.recs)
        r[2]['sent_ts'] += 0.5
        self.assertTrue(failures(self.check(requests=r), 'no_wait'))


class TestRouterClausesCatchFaults(unittest.TestCase):
    def setUp(self):
        _, self.st, _, self.recs = routed('two_tracks', until=70.0, script={5: 'refused'})

    def broken(self, edit):
        r = copy.deepcopy(self.recs)
        edit(r)
        return gate.check_requests(r, R, self.st.order)

    def test_the_real_log_is_clean(self):
        self.assertFalse(any(gate.check_requests(self.recs, R, self.st.order).values()))

    def test_each_clause(self):
        def overlap(r):
            r[1]['sent_ts'] = r[0]['done_ts'] - 0.5
        def too_soon(r):
            r[2]['sent_ts'] = r[1]['sent_ts'] + 3.9
            r[1]['done_ts'] = r[1]['sent_ts'] + 1.0
        def no_backoff(r):
            r[5]['sent_ts'] = r[4]['done_ts'] + 29.0
        def swapped(r):
            r[2]['track_id'] = 'B'
        def unstamped(r):
            r[3]['rule'] = dict(r[3]['rule'], sha256='0' * 64)
        def not_eligible(r):
            r[1]['tracks'] = ['A']
        for edit, clause in ((overlap, 'in_flight'), (too_soon, 'interval'), (no_backoff, 'backoff'),
                             (swapped, 'round_robin'), (unstamped, 'stamped'), (not_eligible, 'round_robin')):
            self.assertTrue(self.broken(edit)[clause], clause)

    def test_more_than_15_in_a_minute(self):
        r = [{'request_id': 'rq-%d' % i, 'track_id': 'A', 'tracks': ['A'], 'sent_ts': 4.0 * i, 'done_ts': 4.0 * i + 1,
              'outcome': 'ok', 'rule': R.stamp()} for i in range(16)]
        self.assertFalse(gate.check_requests(r, R, ['A'])['per_minute'])
        r[15]['sent_ts'] = r[15]['done_ts'] = 59.5
        self.assertTrue(gate.check_requests(r, R, ['A'])['per_minute'])


class TestScored(unittest.TestCase):
    def test_a_scored_check_refuses_while_a_slot_is_open_and_runs_once_closed(self):
        scene, st, bundles, recs = routed('two_tracks', until=20.0)
        with self.assertRaises(method.MethodError):
            gate.check(bundles, st, M, (), recs, scored=True, rule=R)
        with self.assertRaises(method.MethodError):
            gate.check(bundles, st, closed(M, method.method_from), (), recs, scored=True, rule=R)
        res = gate.check(bundles, st, closed(M, method.method_from), (), recs, scored=True,
                         rule=closed(R, method.router_from))
        self.assertEqual((res['verdict'], res['practice'], res['open']), ('fail', False, []))
        self.assertTrue(failures(res, 'stamped'))     # practice records do not carry the closed rule's hash


if __name__ == '__main__':
    unittest.main()
