"""v1.1 person_position: the quick room-frame position from the existing placement.

Offline: a synthetic camera->room alignment, fake deep stack and fake brain."""
import math
import unittest

import activity_events as events
import activity_loop
import activity_pose
from apartment_view_alignment import _chest_point
from brain_publisher import BrainPublisher
from test_activity_loop import Brain, Clock, FakeDeep, healthy

# Synthetic reviewed alignment: rotate 90 deg about z, then translate (2, 1, 0).
MATRIX = [[0., -1., 0., 2.], [1., 0., 0., 1.], [0., 0., 1., 0.], [0., 0., 0., 1.]]


def joint(index, xyz, status='candidate'):
    return {'joint_id': index, 'status': status, 'camera_xyz_m': xyz, 'pixel': [320, 180]}


def room(xyz):
    return [sum(MATRIX[r][k] * xyz[k] for k in range(3)) + MATRIX[r][3] for r in range(3)]


def packet(*people):
    return {'renderable': True, 'frame': {'original_times': {'observed_at': 1800000000.}},
            'approximate_pose': {'provenance': {'source_event_id': 7}, 'instance_index': 0, 'joints': []},
            'approximate_people': [{'instance_index': i, 'position_m': p, 'method': 'shoulder_midpoint'}
                                   for i, p in enumerate(people)]}


class TransformTests(unittest.TestCase):
    def test_chest_point_is_the_room_frame_shoulder_midpoint(self):
        point, method = _chest_point([joint(5, [.2, 0., 3.]), joint(6, [.6, 0., 3.]), joint(0, [.4, -.3, 3.])], MATRIX)
        self.assertEqual(method, 'shoulder_midpoint')
        for got, want in zip(point, room([.4, 0., 3.])):
            self.assertAlmostEqual(got, want)

    def test_fallbacks_one_shoulder_then_the_supported_joint_median(self):
        point, method = _chest_point([joint(5, [.2, 0., 3.]), joint(6, [.6, 0., 3.], 'unsupported')], MATRIX)
        self.assertEqual((method, point), ('single_shoulder', room([.2, 0., 3.])))
        point, method = _chest_point([joint(11, [0., .5, 3.]), joint(12, [.4, .5, 3.2]), joint(0, [.2, -.4, 3.4])], MATRIX)
        self.assertEqual(method, 'supported_joint_median')
        for got, want in zip(point, room([.2, .5, 3.2])):
            self.assertAlmostEqual(got, want)
        self.assertIsNone(_chest_point([joint(5, [0., 0., -1.]), joint(6, [math.nan, 0., 2.])], MATRIX))
        self.assertIsNone(_chest_point(None, MATRIX))

    def test_positions_take_every_lifted_person_with_slot_ids(self):
        people = activity_pose.positions(packet([1., 2., 1.2], [3., 1., 1.1]))
        self.assertEqual(people, [{'track_id': 'slot-0', 'position_m': [1., 2., 1.2], 'approximate': True},
                                  {'track_id': 'slot-1', 'position_m': [3., 1., 1.1], 'approximate': True}])
        self.assertEqual(activity_pose.positions({'renderable': False}), [])
        self.assertEqual(activity_pose.positions(None), [])
        # A packet without approximate_people falls back to the instance-0 pose.
        legacy = {'renderable': True, 'approximate_pose': {'joints': [
            {'id': 5, 'position_m': [1., 2., 1.2]}, {'id': 6, 'position_m': [1.4, 2., 1.2]}]}}
        self.assertEqual(activity_pose.positions(legacy)[0]['position_m'], [1.2, 2., 1.2])


class EventTests(unittest.TestCase):
    def test_person_position_event_is_exact_with_a_3_s_ttl(self):
        people = [{'track_id': 'slot-0', 'position_m': [1., 2., 1.2], 'approximate': True},
                  {'track_id': 'slot-1', 'position_m': [3., 1., 1.1], 'approximate': True}]
        event = events.person_position(home='victoria', room='den', source='perception-lab/activity-loop',
                                       observed_at=1800000000., people=people, person_count=2)
        self.assertEqual(event['ttl_s'], 10.)
        self.assertEqual(set(event), {'type', 'home', 'room', 'source', 'observed_at', 'ttl_s', 'people', 'person_count'})
        for bad in ([], [dict(people[0], position_m=None)], [dict(people[0], approximate=False)],
                    [dict(people[0], head_m=[0, 0, 0])]):
            with self.subTest(bad=bad), self.assertRaises(events.EventRefused):
                events.person_position(home='victoria', room='den', source='s', observed_at=1., people=bad, person_count=1)
        publisher = BrainPublisher(origin='https://brain.invalid', home='victoria', token=lambda: 't' * 40,
                                   post=lambda body: 200)
        self.assertTrue(publisher.submit(event))


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.brain, self.deep = Clock(), Brain(), FakeDeep()
        self.occupied = 'on'
        self.loop = activity_loop.ActivityLoop(deep=self.deep, occupancy=lambda: self.occupied, clock=self.clock,
            publisher=BrainPublisher(origin='https://brain.invalid', home='victoria', token=lambda: 't' * 40,
                                     post=self.brain, clock=self.clock, monotonic=self.clock))
        self.key = 0

    def placed(self, *people, health=None, count=None):
        self.key += 1
        self.deep.seen = {'running': True, 'health': health or healthy(), 'person_count': count,
                          'position': {'key': self.key, 'observed_at': self.clock.now,
                                       'people': [{'track_id': 'slot-%d' % i, 'position_m': p, 'approximate': True}
                                                  for i, p in enumerate(people)]}}

    def run_for(self, seconds, step=.1, new_every=1):
        for i in range(int(round(seconds / step))):
            if i % new_every == 0:
                self.placed([1., 2., 1.2])
            self.clock.now += step
            self.loop.tick()

    def positions(self):
        return self.brain.events('person_position')

    def test_new_placements_publish_at_most_5_hz_and_each_lives_3_s(self):
        self.run_for(10.)                                   # a new placement every 0.1 s
        self.assertEqual(len(self.positions()), 50)
        self.assertTrue(all(e['ttl_s'] == 10. for e in self.positions()))
        before = len(self.positions())
        self.run_for(10., new_every=10)                     # the real placement rate, about 1 Hz
        self.assertEqual(len(self.positions()) - before, 10)
        self.loop.set_cadence('reduced')
        before = len(self.positions())
        self.run_for(10.)
        self.assertEqual(len(self.positions()) - before, 25, 'reduced halves it to 2.5 Hz')

    def test_every_person_is_an_entry_and_the_merged_count_is_kept(self):
        self.placed([1., 2., 1.2], [3., 1., 1.1], count=3)
        self.clock.now += 1; self.loop.tick()
        [event] = self.positions()
        self.assertEqual([p['track_id'] for p in event['people']], ['slot-0', 'slot-1'])
        self.assertEqual(event['person_count'], 3)
        self.placed([1., 2., 1.2], [3., 1., 1.1], count=None)
        self.clock.now += 1; self.loop.tick()
        self.assertEqual(self.positions()[-1]['person_count'], 2, 'never fewer than the entries')

    def test_positions_flow_while_jev_is_blocked(self):
        blocked = dict(healthy(), jev=(False, 'jev_egress_blocked_indeterminate'))
        for _ in range(5):
            self.placed([1., 2., 1.2], health=blocked)
            self.clock.now += 1; self.loop.tick()
        self.assertEqual(len(self.positions()), 5)
        self.assertEqual(self.brain.events('activity'), [])

    def test_nothing_is_published_while_the_room_is_empty(self):
        self.occupied = 'off'
        self.run_for(5.)
        self.assertEqual(self.positions(), [])
        self.assertEqual(self.loop.snapshot()['position']['detail'], 'paused_room_empty')

    def test_health_says_why_positions_are_unavailable(self):
        self.run_for(1.)
        self.assertEqual(self.loop.snapshot()['position']['ok'], True)
        for _ in range(40):
            self.deep.seen = {'running': True, 'health': healthy(), 'position_reason': 'approximate_camera_epoch_unverified'}
            self.clock.now += .1; self.loop.tick()
        status = self.loop.snapshot()['position']
        self.assertEqual((status['ok'], status['detail']), (False, 'position_unavailable:approximate_camera_epoch_unverified'))
        self.assertFalse([e for e in self.brain.events('health') if e['component'] not in events.COMPONENTS],
                         'no health component the v1 brain would refuse')

    def test_the_switch_turns_it_off(self):
        loop = activity_loop.ActivityLoop(deep=self.deep, occupancy=lambda: 'on', clock=self.clock, person_position=False,
            publisher=BrainPublisher(origin='https://brain.invalid', home='victoria', token=lambda: 't' * 40,
                                     post=self.brain, clock=self.clock, monotonic=self.clock))
        self.placed([1., 2., 1.2]); self.clock.now += 1; loop.tick()
        self.assertEqual(self.positions(), [])
        self.assertEqual(loop.snapshot()['position']['detail'], 'position_disabled')
        with self.assertRaises(ValueError):
            activity_loop.ActivityLoop(deep=self.deep, occupancy=lambda: 'on', position_interval_s=.05,
                publisher=BrainPublisher(origin='https://brain.invalid', home='victoria', token=lambda: 't' * 40,
                                         post=self.brain))


class DeepStackPositionTests(unittest.TestCase):
    def test_observe_reports_positions_or_why_not(self):
        from test_activity_loop_runtime import DeepStackTests
        case = DeepStackTests('test_start_failure_retries_with_backoff'); case.setUp()
        try:
            case.deep.start(); sid = case.deep.sid
            seen = case.deep.observe()
            self.assertNotIn('position', seen)
            self.assertEqual(seen['position_reason'], 'approximate_placement_unavailable')
            case.viewing(sid)
            case.placement.packet = packet([1., 2., 1.2], [3., 1., 1.1])
            seen = case.deep.observe()
            self.assertEqual((seen['position']['key'], len(seen['position']['people'])), (7, 2))
            case.placement.packet = {'renderable': False, 'reason': 'approximate_camera_epoch_unverified'}
            self.assertEqual(case.deep.observe()['position_reason'], 'approximate_camera_epoch_unverified')
        finally:
            for cleanup in reversed(case._cleanups):
                cleanup[0](*cleanup[1], **cleanup[2])


if __name__ == '__main__':
    unittest.main()
