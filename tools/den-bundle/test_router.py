"""Router v0 against each rule in ROUTER-V0.md, on a fake clock with a fake sender."""
import copy
import os
import socket
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fakes    # noqa: E402
import method   # noqa: E402
import records  # noqa: E402
import synth    # noqa: E402
from router import Router, run  # noqa: E402

M = method.load_method()
R = method.load_router()
ts = synth.ts


def rule_with(**changes):
    block = copy.deepcopy(R.block)
    block.update(changes)
    return method.router_from(block)


def closed(settings, maker):
    return maker({k: (v['proposed'] if method.is_open(v) else v) for k, v in settings.block.items()})


def replay(name, start=2.0, until=70.0, script=None, latency=1.25, rule=R, scene=None):
    scene = scene or synth.SCENES[name]()
    st = records.Store(scene['observations'], scene['tracks'])
    clock = fakes.FakeClock(ts(start))
    sender = fakes.FakeSender(clock, latency_s=latency, script=script)
    r = Router(st, M, rule, clock, sender)
    recs = run(r, clock, sender, ts(until))
    return r, recs, sender


def sent(recs):
    return [x for x in recs if x['outcome'] != 'no_box']


def rel(t):
    return round(t - synth.T0, 3)


class TestCaps(unittest.TestCase):
    def test_at_most_one_request_every_4_s_and_one_in_flight(self):
        _, recs, sender = replay('two_tracks')
        starts = [x['sent_ts'] for x in sent(recs)]
        self.assertEqual([rel(t) for t in starts], [2.0 + 4 * i for i in range(18)])
        for prev, nxt in zip(sent(recs), sent(recs)[1:]):
            self.assertGreaterEqual(nxt['sent_ts'], prev['done_ts'])
            self.assertGreaterEqual(nxt['sent_ts'] - prev['sent_ts'], 4.0 - 1e-9)
        self.assertEqual(len(sender.sent), 18)

    def test_a_slow_answer_holds_the_next_request(self):
        _, recs, _ = replay('two_tracks', until=30.0, latency=6.5)
        self.assertEqual([rel(x['sent_ts']) for x in sent(recs)], [2.0, 8.5, 15.0, 21.5, 28.0])

    def test_at_most_15_starts_in_any_minute(self):
        _, recs, _ = replay('two_tracks', until=70.0, latency=0.2, rule=rule_with(min_interval_s=1))
        starts = [x['sent_ts'] for x in sent(recs)]
        self.assertEqual([rel(t) for t in starts[:16]], [2.0 + i for i in range(15)] + [62.0])
        for t in starts:
            self.assertLessEqual(sum(1 for s in starts if t - 60 < s <= t), 15)

    def test_the_minute_cap_never_binds_before_the_4_s_rule_does(self):
        _, recs, _ = replay('two_tracks', until=70.0, latency=0.2)
        starts = [x['sent_ts'] for x in sent(recs)]
        self.assertEqual(max(sum(1 for s in starts if t - 60 < s <= t) for t in starts), 15)


class TestRoundRobin(unittest.TestCase):
    def test_two_tracks_alternate_at_8_s_each(self):
        _, recs, _ = replay('two_tracks', until=40.0)
        self.assertEqual([x['track_id'] for x in recs], ['A', 'B'] * 5)
        self.assertTrue(all(x['per_person_period_s'] == 8 and x['tracks'] == ['A', 'B'] for x in recs))

    def test_a_track_that_leaves_is_dropped_and_the_other_gets_every_slot(self):
        _, recs, _ = replay('track_leaves', until=40.0)
        got = [(rel(x['sent_ts']), x['track_id'], x['per_person_period_s']) for x in recs]
        self.assertEqual(got[:6], [(2.0, 'A', 8), (6.0, 'B', 8), (10.0, 'A', 8), (14.0, 'B', 8),
                                   (18.0, 'A', 8), (22.0, 'B', 8)])
        self.assertEqual(got[6:], [(t, 'A', 4) for t in (26.0, 30.0, 34.0, 38.0)])
        self.assertEqual(recs[5]['missing'], ['track', 'person_box', 'keypoints'])

    def test_a_late_coming_track_joins_the_rotation(self):
        scene = synth.two_tracks()
        late = {o['obs_id'] for o in scene['observations'] if o['obs_id'].startswith('o-B-') and o['capture_ts'] < ts(12.0)}
        scene['tracks'] = [l for l in scene['tracks'] if not set(l['last_obs_ids']) & late]
        _, recs, _ = replay(None, scene=scene, until=30.0)
        self.assertEqual([(rel(x['sent_ts']), x['track_id']) for x in recs],
                         [(2.0, 'A'), (6.0, 'A'), (10.0, 'A'), (14.0, 'B'), (18.0, 'A'), (22.0, 'B'), (26.0, 'A'), (30.0, 'B')])

    def test_only_confirmed_tracks_are_served(self):
        for state in ('tentative', 'coasting', 'ended'):
            scene = synth.two_tracks()
            for line in scene['tracks']:
                if line['track_id'] == 'B':
                    line['state'] = state
            _, recs, _ = replay(None, scene=scene, until=20.0)
            self.assertEqual({x['track_id'] for x in recs}, {'A'}, state)

    def test_a_bundle_with_no_box_is_logged_not_sent_and_the_turn_passes(self):
        scene = synth.two_tracks()
        for o in scene['observations']:
            if o['obs_id'].startswith('o-B-'):
                o['image_box'] = None
        _, recs, sender = replay(None, scene=scene, until=12.0)
        self.assertEqual([(rel(x['sent_ts']), x['track_id'], x['outcome']) for x in recs],
                         [(2.0, 'A', 'ok'), (6.0, 'B', 'no_box'), (6.0, 'A', 'ok'), (10.0, 'B', 'no_box'),
                          (10.0, 'A', 'ok')])
        self.assertEqual(len(sender.sent), 3)


class TestRefusalsAndErrors(unittest.TestCase):
    def test_a_refusal_stops_every_request_for_30_s_from_the_answer(self):
        _, recs, _ = replay('two_tracks', until=60.0, script={3: 'refused'})
        third = recs[2]
        self.assertEqual((third['outcome'], rel(third['done_ts']), rel(third['backoff_until'])), ('refused', 11.25, 41.25))
        self.assertEqual([(rel(x['sent_ts']), x['track_id']) for x in recs[3:6]], [(41.25, 'B'), (45.25, 'A'), (49.25, 'B')])

    def test_an_error_backs_off_the_same_way(self):
        _, recs, _ = replay('two_tracks', until=50.0, script={1: 'error'})
        self.assertEqual(recs[0]['outcome'], 'error')
        self.assertEqual(rel(recs[1]['sent_ts']), 2.0 + 1.25 + 30)

    def test_a_sender_that_raises_is_an_error_at_once(self):
        _, recs, _ = replay('two_tracks', until=40.0, script={1: 'raise'})
        self.assertEqual((recs[0]['outcome'], rel(recs[0]['done_ts']), rel(recs[1]['sent_ts'])), ('error', 2.0, 32.0))
        self.assertIn('refused to connect', recs[0]['detail'])

    def test_no_answer_times_out_and_backs_off(self):
        _, recs, _ = replay('two_tracks', until=60.0, script={1: 'silent'})
        timeout = R['timeout_s']
        self.assertEqual((recs[0]['outcome'], rel(recs[0]['done_ts'])), ('timeout', 2.0 + timeout))
        self.assertEqual(rel(recs[1]['sent_ts']), 2.0 + timeout + 30)

    def test_a_late_answer_after_the_timeout_is_ignored(self):
        scene = synth.two_tracks()
        st = records.Store(scene['observations'], scene['tracks'])
        clock = fakes.FakeClock(ts(2.0))
        sender = fakes.FakeSender(clock, script={1: 'silent'})
        r = Router(st, M, R, clock, sender)
        first = r.tick()
        clock.set(ts(2.0) + R['timeout_s'] + 1)
        self.assertIsNone(r.tick())
        self.assertEqual(first['outcome'], 'timeout')
        until = r.backoff_until
        self.assertFalse(r.on_result(first['request_id'], 'error'))
        self.assertEqual(r.backoff_until, until)
        self.assertFalse(r.on_result('rq-99999', 'ok'))
        with self.assertRaises(ValueError):
            r.on_result(first['request_id'], 'maybe')

    def test_refused_starts_count_toward_both_caps(self):
        _, recs, _ = replay('two_tracks', until=70.0, latency=0.2, script={i: 'refused' for i in range(1, 40)},
                            rule=rule_with(min_interval_s=1, backoff_s=1))
        starts = [x['sent_ts'] for x in sent(recs)]
        for prev, nxt in zip(starts, starts[1:]):
            self.assertGreaterEqual(nxt - prev, 1.2 - 1e-9)        # answer at +0.2, then the 1 s back-off
        for t in starts:
            self.assertLessEqual(sum(1 for s in starts if t - 60 < s <= t), 15)
        self.assertEqual([rel(t) for t in starts], [round(2.0 + 1.2 * k, 3) for k in range(15)] +
                         [62.0, 63.2, 64.4, 65.6, 66.8, 68.0, 69.2])


class TestRecords(unittest.TestCase):
    def test_every_request_names_the_rule_and_method_versions_and_hashes(self):
        _, recs, sender = replay('crossing', until=30.0)
        for x in recs:
            self.assertEqual(x['version'], 'router_request/v0')
            self.assertEqual(x['rule'], {'version': 'router-v0', 'sha256': R.sha256, 'open': ['timeout_s']})
            self.assertEqual(x['method']['sha256'], M.sha256)
            self.assertEqual(x['method']['open'], ['window_s', 'max_gap_s', 'pad_fraction', 'pad_min_px'])
        for _, request in sender.sent:
            self.assertEqual(request['rule']['sha256'], R.sha256)

    def test_missing_sources_never_delay_a_request(self):
        _, recs, _ = replay('missing_depth', start=3.0, until=8.0)
        self.assertEqual([rel(x['sent_ts']) for x in recs], [3.0, 7.0])
        self.assertEqual([x['missing'] for x in recs], [[], ['depth']])
        for x in recs:
            self.assertEqual(x['interval'][1], x['sent_ts'])

    def test_crossing_requests_carry_association_safe_false(self):
        _, recs, _ = replay('crossing', start=3.5, until=13.0)
        self.assertEqual([(rel(x['sent_ts']), x['association_safe']) for x in recs],
                         [(3.5, True), (7.5, False), (11.5, True)])
        _, recs, _ = replay('crossing', start=4.5, until=13.0)
        self.assertEqual([(rel(x['sent_ts']), x['association_safe']) for x in recs],
                         [(4.5, False), (8.5, False), (12.5, True)])


class TestScoredAndOffline(unittest.TestCase):
    def test_a_scored_routed_run_refuses_while_a_slot_is_open(self):
        st = records.Store([], [])
        clock = fakes.FakeClock(0)
        with self.assertRaises(method.MethodError) as cm:
            Router(st, M, closed(R, method.router_from), clock, fakes.FakeSender(clock), scored=True)
        self.assertIn('window_s', str(cm.exception))
        with self.assertRaises(method.MethodError) as cm:
            Router(st, closed(M, method.method_from), R, clock, fakes.FakeSender(clock), scored=True)
        self.assertIn('timeout_s', str(cm.exception))
        Router(st, closed(M, method.method_from), closed(R, method.router_from), clock, fakes.FakeSender(clock), scored=True)

    def test_the_router_opens_no_socket(self):
        with mock.patch.object(socket, 'socket', side_effect=AssertionError('no network in rung 5')), \
                mock.patch.object(socket, 'create_connection', side_effect=AssertionError('no network in rung 5')):
            _, recs, _ = replay('two_tracks', until=30.0)
        self.assertTrue(recs)


if __name__ == '__main__':
    unittest.main()
