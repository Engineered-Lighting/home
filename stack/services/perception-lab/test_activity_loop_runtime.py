"""AL-3 deep stack in the controller and the restored Qwen facts in JEV input.

A real Store ledger with an authored controller; no camera, model, LA or brain."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import activity_loop
from activity_loop_runtime import ManagedDeepStack
from brain_publisher import BrainPublisher
from config import Settings
from store import Store
import test_qwen_scene as qwen_fixture


class Controller:
    """The Controller.start/stop contract over a real Store, with authored outputs."""
    def __init__(self, store, runtime):
        self.store, self.runtime, self.started, self.stopped = store, runtime, [], []

    def start(self, body):
        session, _ = self.store.create_session(body['camera_id'], body['experiment_id'], body['name'],
                                               body['request_id'], body.get('reasoning_enabled', False))
        self.store.transition(session['id'], 'starting')
        self.started.append(body)
        return self.store.transition(session['id'], 'recording')

    def stop(self, sid, reason='user_stop'):
        self.stopped.append((sid, reason))
        return self.store.transition(sid, 'stopped', reason)


class Placement:
    def __init__(self):
        self.packet = None
    def snapshot(self):
        return self.packet


class DeepStackTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.now = [1800000000.]
        self.store = Store(Settings(storage_root=Path(temp.name), min_free_bytes=0, max_session_seconds=300),
                           clock=lambda: self.now[0])
        self.addCleanup(self.store.close)
        self.placement = Placement()
        self.runtime = SimpleNamespace(apartment_viewing_sessions={}, reasoning_errors={})
        self.controller = Controller(self.store, self.runtime)
        self.deep = ManagedDeepStack(self.controller, roll_margin_s=20.)

    def viewing(self, sid, accepted=3):
        self.runtime.apartment_viewing_sessions[sid] = SimpleNamespace(
            metric=self.placement, status=lambda: {'accepted_frames': accepted})

    def test_rolls_recordings_before_the_budget_and_never_caps_the_run(self):
        self.deep.start()
        first = self.deep.sid
        self.assertEqual(self.controller.started[0]['experiment_id'], 'apartment-viewing')
        self.assertIs(self.controller.started[0]['reasoning_enabled'], True)
        sessions = {first}
        for _ in range(60 * 60 // 10):  # an hour in 10 s ticks
            self.now[0] += 10
            self.deep.tick()
            sessions.add(self.deep.sid)
            session = self.store.get_session(self.deep.sid)
            self.assertEqual(session['state'], 'recording')
            self.assertLess(self.now[0] - session['started_at'], 300, 'each recording stays inside its 300 s budget')
        self.assertGreaterEqual(len(sessions), 12)
        self.assertTrue(all(reason == 'activity_loop_roll' for _, reason in self.controller.stopped))
        self.assertEqual(self.deep.rolls, len(sessions) - 1)

    def test_budget_expiry_reopens_but_an_outside_stop_is_an_operator_stop(self):
        self.deep.start(); sid = self.deep.sid
        self.store.transition(sid, 'stopped', 'duration_limit')
        self.deep.tick(); self.assertNotEqual(self.deep.sid, sid); self.assertTrue(self.deep.wanted)
        self.store.transition(self.deep.sid, 'stopped', 'user_stop')
        self.deep.tick()
        self.assertIsNone(self.deep.sid); self.assertTrue(self.deep.observe()['ended_externally'])
        clock = [self.now[0]]
        loop = activity_loop.ActivityLoop(deep=self.deep, occupancy=lambda: 'on', clock=lambda: clock[0],
            publisher=BrainPublisher(origin='https://brain.invalid', home='victoria', token=lambda: 't' * 40,
                                     post=lambda body: 200))
        loop.running = True; loop.started_at = clock[0]
        loop.tick()
        self.assertEqual(loop.snapshot()['stopped_reason'], 'operator_stop')

    def test_observes_arm_b_answers_usage_health_and_room_frame_placement(self):
        self.deep.start(); sid = self.deep.sid; self.viewing(sid)
        started = self.store.get_session(sid)['started_at']
        self.assertEqual(self.deep.observe()['health']['jev'], (False, 'jev_starting'))
        box = dict(label='person', x=260., y=80., w=120., h=260.)
        result = {'objects': [{'label': 'person', 'instance_id': 'p', 'confidence': .9},
                              {'label': 'person', 'instance_id': 'd', 'confidence': .3}],
                  'boxes': [dict(box, instance_id='p', confidence=.9), dict(box, instance_id='d', confidence=.3, x=261.)],
                  'pose_instances': [{'instance_id': 'p', 'joints': [
                      {'joint_id': 0, 'pixel': [320, 140], 'status': 'supported'},
                      {'joint_id': 3, 'pixel': [312, 138], 'status': 'supported'},
                      {'joint_id': 4, 'pixel': [328, 138], 'status': 'supported'},
                      {'joint_id': 5, 'pixel': [300, 150], 'status': 'supported'},
                      {'joint_id': 6, 'pixel': [340, 150], 'status': 'supported'}]}]}
        output = {'id': self.store.append_event(sid, 'perception.output', {'arm': 'b', 'result': result, 'available_at': self.now[0]})}
        self.store.append_event(sid, 'perception.output', {'arm': 'a', 'result': {'objects': []}, 'available_at': self.now[0]})
        for arm in ('a', 'b'):
            self.store.append_event(sid, 'reasoning.delivery', {'arm': arm, 'state': 'completed'})
        answer = {'id': self.store.append_event(sid, 'reasoning.output', {'arm': 'b', 'source_end_s': 1.5, 'judgment': {
            'activity': 'reading', 'probability': .7, 'confidence': .5, 'model_version': 'jev-1.13.0',
            'diagnostics': {'facts_key': 'k' * 64, 'usage': {'input_tokens': 900, 'output_tokens': 40}}}})}
        self.placement.packet = {'renderable': True, 'frame': {'original_times': {'observed_at': started + 1.5}},
            'approximate_pose': {'instance_index': 0, 'provenance': {'source_event_id': output['id']},
                'joints': [{'id': 0, 'position_m': [1., 2., 1.5]}, {'id': 5, 'position_m': [.8, 2., 1.2]},
                           {'id': 6, 'position_m': [1.2, 2., 1.2]}]}}
        seen = self.deep.observe()
        self.assertEqual(seen['activity'], {'event_id': answer['id'], 'label': 'reading', 'confidence': .7,
            'model': 'jev-1.13.0', 'facts_key': 'k' * 64, 'observed_at': started + 1.5})
        self.assertEqual(seen['person_count'], 1, 'arm-B boxes after the duplicate merge')
        self.assertEqual(seen['usage'], {'calls': 2, 'input_tokens': 900, 'output_tokens': 40}, 'both arms are billed')
        self.assertEqual(seen['health'], {'jev': (True, None), 'deep_stack': (True, None), 'camera': (True, None)})
        person = seen['pose']['people'][0]
        self.assertEqual((seen['pose']['key'], person['head_m'], person['approximate']), (output['id'], [1., 2., 1.5], True))
        self.assertIsNotNone(person['head_pitch_deg'])
        self.store.append_event(sid, 'reasoning.delivery', {'arm': 'b', 'state': 'indeterminate'})
        self.now[0] += 11
        seen = self.deep.observe()
        # No indeterminate row is left in the ledger, so egress is open again.
        self.assertEqual(seen['health']['jev'], (False, 'jev_resuming'))
        self.assertEqual(seen['health']['deep_stack'], (False, 'deep_stack_no_recent_output'))
        self.assertEqual(seen['usage']['calls'], 3, 'counted once each, across reads')

    def test_start_failure_retries_with_backoff(self):
        attempts = []
        def refuse(body):
            attempts.append(body); raise ValueError('workers unavailable')
        self.controller.start = refuse
        self.deep.start(); self.assertIsNone(self.deep.sid); self.assertEqual(len(attempts), 1)
        self.now[0] += 5; self.deep.tick(); self.assertEqual(len(attempts), 1)
        self.now[0] += 6; self.deep.tick(); self.assertEqual(len(attempts), 2)
        self.assertEqual(self.deep.snapshot()['last_error'], 'ValueError')


FREEZE = "SELECT 1 FROM lab_reasoning_requests WHERE state='indeterminate' OR (state='dispatching' AND session_id!=?) LIMIT 1"


class EgressReleaseTests(unittest.TestCase):
    """The owner's bounded auto-release of the JEV egress freeze (always-on mode only)."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.now = [1800000000.]
        self.store = Store(Settings(storage_root=Path(temp.name), min_free_bytes=0, max_session_seconds=300),
                           clock=lambda: self.now[0])
        self.addCleanup(self.store.close)
        with self.store.transaction() as db:  # live_reasoning's table and active-arm index
            db.execute("""CREATE TABLE IF NOT EXISTS lab_reasoning_requests(id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                arm TEXT NOT NULL, generation INTEGER NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL,
                dispatched_at REAL, result TEXT)""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS reasoning_active_arm ON lab_reasoning_requests(session_id,arm)
                WHERE state IN ('dispatching','indeterminate')""")
        self.runtime = SimpleNamespace(apartment_viewing_sessions={}, reasoning_errors={})
        self.controller = Controller(self.store, self.runtime)
        self.deep = ManagedDeepStack(self.controller)
        self.deep.start(); self.sid = self.deep.sid

    def indeterminate(self, rid, sid=None, *, delivered=True, arm='b'):
        sid = sid or self.sid
        with self.store.transaction() as db:
            db.execute("INSERT INTO lab_reasoning_requests VALUES(?,?,?,0,'indeterminate','{}',?,NULL)",
                       (rid, sid, arm, self.now[0] - 5))
        if delivered:
            self.store.append_event(sid, 'reasoning.delivery', {'request_id': rid, 'arm': arm, 'generation': 0,
                                                                'state': 'indeterminate', 'available_at': self.now[0]})

    def state(self, rid):
        return self.store.db.execute('SELECT state FROM lab_reasoning_requests WHERE id=?', (rid,)).fetchone()[0]

    def frozen(self, sid=None):
        return self.store.db.execute(FREEZE, (sid or self.sid,)).fetchone() is not None

    def completed(self, sid=None):
        self.store.append_event(sid or self.sid, 'reasoning.delivery', {'arm': 'b', 'state': 'completed', 'available_at': self.now[0]})

    def jev(self):
        return self.deep.observe()['health']['jev']

    def test_abandons_its_own_request_at_120_s_with_a_ledger_receipt(self):
        self.indeterminate('r1')
        self.assertTrue(self.frozen())
        self.assertEqual(self.jev(), (False, 'jev_egress_blocked_indeterminate'))
        self.now[0] += 119.9
        self.assertEqual(self.deep.maintain(), [])
        self.assertEqual(self.state('r1'), 'indeterminate')
        self.now[0] += .1
        [receipt] = self.deep.maintain()
        self.assertEqual(self.state('r1'), 'abandoned')
        self.assertFalse(self.frozen(), 'jev_policy admits egress again')
        self.assertEqual((receipt['request_id'], receipt['reason'], receipt['abandoned_at'], receipt['waited_s']),
                         ('r1', 'activity_loop_auto_release_indeterminate', self.now[0], 120.))
        row = self.store.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='reasoning.abandoned'",
                                    (self.sid,)).fetchall()
        self.assertEqual([json.loads(r[0]) for r in row], [receipt])
        self.assertEqual(json.loads(self.store.db.execute("SELECT result FROM lab_reasoning_requests WHERE id='r1'").fetchone()[0]),
                         receipt)
        self.assertEqual(self.jev(), (False, 'jev_resuming'), 'cleared once egress resumes')
        self.completed()
        self.assertEqual(self.jev(), (True, None))
        self.assertEqual(self.deep.maintain(), [], 'released once')

    def test_lab_recordings_keep_the_freeze(self):
        # A Lab recording that ended with its JEV request unresolved.
        self.deep.stop()
        lab, _ = self.store.create_session('den', 'apartment-viewing', 'Lab', 'lab-request-1', True)
        for state in ('starting', 'recording'):
            self.store.transition(lab['id'], state)
        self.indeterminate('lab-r', lab['id'])
        self.store.transition(lab['id'], 'stopped', 'user_stop')
        self.deep.start()
        self.now[0] += 3600
        self.assertEqual(self.deep.maintain(), [])
        self.assertEqual(self.state('lab-r'), 'indeterminate')
        self.assertEqual(self.jev(), (False, 'jev_egress_blocked_indeterminate'), 'a Lab freeze blocks the loop too')
        self.assertTrue(self.deep.snapshot()['jev_release']['blocked_by_lab'])

    def test_back_off_doubles_until_jev_answers_again(self):
        waits = []
        for n, rid in enumerate(('r1', 'r2', 'r3')):
            self.indeterminate(rid)
            start = self.now[0]
            while not self.deep.maintain():
                self.now[0] += 10
            waits.append(self.now[0] - start)
        self.assertEqual(waits, [120., 240., 480.])
        self.completed()
        self.indeterminate('r4'); start = self.now[0]
        while not self.deep.maintain():
            self.now[0] += 10
        self.assertEqual(self.now[0] - start, 120., 'a completed delivery restarts the back-off')
        capped = __import__('activity_loop_runtime').EgressRelease(self.store, abandon_after_s=120, backoff_max_s=300,
                                                                   clock=lambda: self.now[0])
        capped.streak = 5
        self.assertEqual(capped.wait_s(), 300.)

    def test_a_restart_recovered_request_counts_from_when_the_loop_first_sees_it(self):
        self.indeterminate('r1', delivered=False)
        self.now[0] += 1000
        self.assertEqual(self.deep.maintain(), [], 'no delivery time: the clock starts now')
        self.now[0] += 120
        self.assertEqual([r['request_id'] for r in self.deep.maintain()], ['r1'])

    def test_the_flag_turns_it_off_and_the_config_is_bounded(self):
        from activity_loop_runtime import EgressRelease, build
        deep = ManagedDeepStack(self.controller, release=EgressRelease(self.store, enabled=False))
        self.indeterminate('r1')
        self.now[0] += 3600
        self.assertEqual(deep.maintain(), [])
        self.assertEqual(self.state('r1'), 'indeterminate')
        config = {'schema': activity_loop.SCHEMA, 'enabled': True, 'home': 'victoria', 'room': 'den', 'camera_id': 'den',
                  'experiment_id': 'apartment-viewing', 'brain': {'origin': 'https://la.example', 'token_file': '/p/t',
                  'ca_file': None}, 'timings': {}, 'pricing': {}, 'zones': None}
        activity_loop.check_config(config)
        default = build(config, controller=self.controller, occupancy=lambda: 'off', read_token=lambda p: 't' * 40)
        self.assertEqual((default.deep.release.enabled, default.deep.release.abandon_after_s), (True, 120.))
        off = build(dict(config, jev_release={'enabled': False}), controller=self.controller, occupancy=lambda: 'off',
                    read_token=lambda p: 't' * 40)
        self.assertFalse(off.deep.release.enabled)
        for bad in ({'enabled': 'no'}, {'abandon_after_s': 5}, {'abandon_after_s': 600, 'backoff_max_s': 300}, {'cap': 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                activity_loop.check_config(dict(config, jev_release=bad))

    def test_the_loop_publishes_blocked_resuming_then_ok_and_releases_while_paused(self):
        bodies = []
        loop = activity_loop.ActivityLoop(deep=self.deep, occupancy=lambda: 'on', clock=lambda: self.now[0],
            publisher=BrainPublisher(origin='https://brain.invalid', home='victoria', token=lambda: 't' * 40,
                                     post=lambda body: bodies.append(json.loads(body)) or 200))
        def jev_health():
            events = [e for b in bodies for e in b['events'] if e['type'] == 'health' and e['component'] == 'jev']
            return [(e['ok'], e['detail']) for e in events]
        loop.tick()
        self.indeterminate('r1', self.deep.sid)
        for _ in range(12):
            self.now[0] += 10; loop.tick()
        sid = self.deep.sid
        started = self.store.get_session(sid)['started_at']
        self.completed(sid)
        self.store.append_event(sid, 'reasoning.output', {'arm': 'b', 'source_end_s': self.now[0] - started,
            'judgment': {'activity': 'reading', 'probability': .8, 'model_version': 'jev-1.13.0', 'diagnostics': {}}})
        self.now[0] += 1; loop.tick()
        changes = [h for i, h in enumerate(jev_health()) if i == 0 or h != jev_health()[i - 1]]
        self.assertEqual(changes, [(False, 'jev_starting'), (False, 'jev_egress_blocked_indeterminate'),
                                   (False, 'jev_resuming'), (True, None)])
        # Paused (room empty): a blocked request of the loop's is still released on time.
        loop.occupancy = lambda: 'off'
        self.now[0] += 121; loop.tick()
        self.assertFalse(self.deep.wanted, 'paused')
        self.indeterminate('r2', sid)
        self.now[0] += 120; loop.tick()
        self.assertEqual(self.state('r2'), 'abandoned')


class QwenInJevInputTests(unittest.TestCase):
    """Qwen facts reach JEV again through the always-on perceiver's receipts."""
    def setUp(self):
        from la_qwen_context import LaQwenReceipts
        self.subscribed = []
        self.fact_log = SimpleNamespace(subscribe_receipts=self.subscribed.append,
                                        unsubscribe_receipts=self.subscribed.remove)
        self.bridge = LaQwenReceipts(self.fact_log)
        self.session = {'id': 's' * 32, 'site_id': 'victoria', 'camera_id': 'den'}
        # The activity loop's recipe, resolved from the real catalog as the profile resolver does.
        import json
        from evidence_policy import recipe_grants
        catalog = json.loads((Path(__file__).with_name('experiments.json')).read_text(encoding='utf-8'))
        recipe = next(r for r in catalog['experiments'] if r['id'] == 'apartment-viewing')
        defaults = {k: v['default'] for k, v in catalog['baseline']['families'].items()}
        self.runner = SimpleNamespace(source_policy=recipe_grants(recipe, defaults),
                                      ha_evidence={'a': recipe['a']['ha_evidence'], 'b': recipe['b']['ha_evidence']},
                                      caption_evidence={'a': recipe['a']['caption_evidence'], 'b': recipe['b']['caption_evidence']})

    def runtime(self, *, granted=True, la=True):
        return SimpleNamespace(qwen_live=None, qwen_live_pumps={}, la_qwen=self.bridge if la else None,
                               reasoning_factory=SimpleNamespace(qwen_granted=lambda: granted), store=None)

    def test_receipts_become_the_recordings_qwen_record_and_the_jev_wire_qwen_block(self):
        from service_runtime import ServiceRuntime
        import jev_wire
        self.assertEqual(self.subscribed, [self.bridge.observe])
        for t in (90., 95., 100.):
            self.bridge.observe({'reference': {}}, qwen_fixture.receipt(t))
        rows, status = ServiceRuntime._qwen_context(self.runtime(), self.session, self.runner, 101., 3)
        self.assertEqual(status, 'supplied')
        self.assertEqual(set(rows), {'a', 'b'}, 'the catalog baseline grants qwen_scene to both arms')
        record = rows['b'][0]
        self.assertEqual((record['session_id'], record['generation'], record['arm']), (self.session['id'], 3, 'b'))
        block, current = jev_wire._qwen(rows['b'], 0)
        self.assertEqual(block['coverage'], 'supplied')
        self.assertEqual(block['latest']['facts']['activity_hypothesis'], 'reading')
        self.assertEqual(current['held_object'], 'book')
        self.assertIn('history', block)

    def test_policy_withholding_and_no_perceiver_send_no_qwen_record(self):
        from service_runtime import ServiceRuntime
        self.bridge.observe({}, qwen_fixture.receipt(100.))
        self.assertEqual(ServiceRuntime._qwen_context(self.runtime(granted=False), self.session, self.runner, 101., 3),
                         ({}, 'not_granted'))
        self.assertEqual(ServiceRuntime._qwen_context(self.runtime(la=False), self.session, self.runner, 101., 3), ({}, None))
        rows, status = ServiceRuntime._qwen_context(self.runtime(), self.session, self.runner, 200., 3)
        self.assertEqual(status, 'stale', 'an old receipt is history, not current facts')

    def test_bounded_first_copy_window_and_close_unsubscribes(self):
        from la_qwen_context import LaQwenReceipts
        bridge = LaQwenReceipts(self.fact_log, capacity=4)
        first = qwen_fixture.receipt(10.)
        bridge.observe({}, first); bridge.observe({}, dict(first, facts={'changed': True}))
        self.assertIs(bridge.receipts[first['request_id']], first)
        for t in range(11, 20):
            bridge.observe({}, qwen_fixture.receipt(float(t)))
        self.assertEqual(len(bridge.receipts), 4)
        bridge.close(); self.assertNotIn(bridge.observe, self.subscribed)


if __name__ == '__main__':
    unittest.main()
