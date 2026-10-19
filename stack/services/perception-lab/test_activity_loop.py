"""Always-on activity loop (AL-3): events, publisher, gating, cadence, health,
cost, Stop and routes. Offline: injectable clock, fake deep stack, fake brain."""
from copy import deepcopy
import http.server
import json
from pathlib import Path
import re
import threading
from types import SimpleNamespace
import unittest

import activity_events as events
import activity_loop
import activity_pose
from brain_publisher import BrainPublisher, http_post, origin_parts

CONTRACT = Path(__file__).resolve().parents[3] / 'docs/plans/den-activity-lighting/CONTRACT.md'
SCOPE = dict(home='victoria', room='den', source='perception-lab/activity-loop')


class Clock:
    def __init__(self, now=1800000000.):
        self.now = now
    def __call__(self):
        return self.now


class FakeDeep:
    def __init__(self):
        self.calls, self.seen = [], {}
    def start(self):
        self.calls.append('start')
    def stop(self, reason):
        self.calls.append('stop:' + reason)
    def tick(self):
        self.calls.append('tick')
    def observe(self):
        return deepcopy(self.seen)


class Brain:
    """Accepts every POST and keeps the decoded bodies."""
    def __init__(self, status=200):
        self.status, self.bodies = status, []
    def __call__(self, body):
        self.bodies.append(json.loads(body))
        return self.status
    def events(self, kind=None):
        return [e for b in self.bodies for e in b['events'] if kind is None or e['type'] == kind]


def answer(event_id, label='reading', at=1800000000.):
    return {'event_id': event_id, 'label': label, 'confidence': .8, 'model': 'jev-1.13.0',
            'facts_key': 'f' * 64, 'observed_at': at}


def person(x=1.):
    return {'track_id': 'slot-0', 'shoulders_m': {'left': [x, 2., 1.2], 'right': [x + .4, 2., 1.2]},
            'head_m': [x + .2, 2., 1.45], 'head_pitch_deg': -20., 'approximate': True}


def healthy():
    return {'jev': (True, None), 'deep_stack': (True, None), 'camera': (True, None)}


class EventContractTests(unittest.TestCase):
    def test_event_fields_ttls_and_labels_match_contract_md(self):
        text = CONTRACT.read_text(encoding='utf-8')
        self.assertIn('`{type, home, room, source, observed_at, sent_at, ttl_s}`', text)
        self.assertIn('"schema":"activity-lighting-events-1"', text)
        for kind, fields in events.EXTRA.items():
            row = next(line for line in text.splitlines() if line.startswith('| `%s` |' % kind))
            tokens = set(re.findall(r'`([a-z_0-9]+)', row))
            with self.subTest(kind=kind):
                self.assertTrue(set(fields) <= tokens, (fields, tokens))
                self.assertEqual(float(re.search(r'ttl (\d+) s', row).group(1)), events.TTL_S[kind])
        activity_row = next(line for line in text.splitlines() if line.startswith('| `activity` |'))
        self.assertTrue(set(events.LABELS) <= set(re.findall(r'`([a-z_]+)`', activity_row)))
        pose_row = next(line for line in text.splitlines() if line.startswith('| `person_pose` |'))
        self.assertTrue({'track_id', 'shoulders_m', 'head_m', 'head_pitch_deg', 'approximate'}
                        <= set(re.findall(r'([a-z_]+)', pose_row)))

    def test_builders_produce_exact_events_and_refuse_anything_else(self):
        built = [events.activity(**SCOPE, observed_at=1., label='reading', confidence=.7, person_count=1,
                                 model='jev-1.13.0', facts_key='k', zone_hint='sofa'),
                 events.person_pose(**SCOPE, observed_at=1., people=[person()], person_count=1),
                 events.health(**SCOPE, observed_at=1., component='jev', ok=False, detail='jev_failed'),
                 events.cost(**SCOPE, observed_at=1., calls=3, est_usd=None)]
        body = events.envelope([dict(e, sent_at=2.) for e in built])
        self.assertEqual(body['schema'], 'activity-lighting-events-1')
        for event in body['events']:
            self.assertEqual(set(event), set(events.COMMON) | set(events.EXTRA[event['type']]))
        bad = [lambda e: e.update(label='sleeping'), lambda e: e.update(confidence=1.5), lambda e: e.update(ttl_s=60.),
               lambda e: e.update(zone_hint='kitchen'), lambda e: e.update(extra=True), lambda e: e.pop('facts_key'),
               lambda e: e.update(type='occupancy'), lambda e: e.update(person_count=-1)]
        for change in bad:
            event = dict(built[0], sent_at=2.); change(event)
            with self.subTest(change=change), self.assertRaises(events.EventRefused):
                events.check(event)
        for change in (lambda p: p.update(approximate=False), lambda p: p.update(head_m=[1, 2]),
                       lambda p: p['shoulders_m'].pop('left'), lambda p: p.update(head_pitch_deg=120.)):
            p = person(); change(p)
            with self.subTest(change=change), self.assertRaises(events.EventRefused):
                events.person_pose(**SCOPE, observed_at=1., people=[p], person_count=1)


class PublisherTests(unittest.TestCase):
    def publisher(self, brain, **kw):
        self.clock, self.mono = Clock(), Clock(100.)
        return BrainPublisher(origin='https://brain.invalid', home='victoria', token=lambda: 't' * 40, post=brain,
                              clock=self.clock, monotonic=self.mono, **kw)

    def event(self, at=None):
        return events.health(**SCOPE, observed_at=self.clock.now if at is None else at, component='jev', ok=True, detail=None)

    def test_sends_batches_with_sent_at_and_refuses_types_it_does_not_produce(self):
        brain = Brain(); publisher = self.publisher(brain)
        publisher.submit(self.event()); self.assertEqual(publisher.flush(), 'sent')
        self.assertEqual(brain.bodies[0]['schema'], 'activity-lighting-events-1')
        self.assertEqual(brain.events()[0]['sent_at'], self.clock.now)
        occupancy = dict(type='occupancy', home='victoria', room='den', source='x', observed_at=1., ttl_s=90.,
                         present=True, people_count=1)
        with self.assertRaises(events.EventRefused):
            publisher.submit(occupancy)
        self.assertEqual(publisher.flush(), 'idle')

    def test_unreachable_brain_buffers_boundedly_backs_off_and_drops_expired(self):
        brain = Brain(status=503); publisher = self.publisher(brain, capacity=3)
        for _ in range(5):
            publisher.submit(self.event())
        self.assertEqual(publisher.snapshot()['dropped_overflow'], 2)
        self.assertEqual(publisher.flush(), 'failed'); self.assertEqual(publisher.flush(), 'waiting')
        self.mono.now += 1; self.assertEqual(publisher.flush(), 'failed')
        self.mono.now += 1.5; self.assertEqual(publisher.flush(), 'waiting', 'backoff doubles to 2 s')
        brain.status = 200; self.mono.now += 1; self.assertEqual(publisher.flush(), 'sent')
        self.assertEqual(len(brain.bodies[-1]['events']), 3, 'the bounded buffer, oldest dropped')
        brain.status = 503; publisher.submit(self.event()); self.clock.now += 151
        self.assertEqual(publisher.flush(), 'idle', 'past its ttl the brain would ignore it')
        self.assertEqual(publisher.snapshot()['dropped_expired'], 1)

    def test_contract_refusal_is_dropped_not_retried(self):
        brain = Brain(status=422); publisher = self.publisher(brain)
        publisher.submit(self.event()); self.assertEqual(publisher.flush(), 'refused')
        self.assertEqual(publisher.snapshot()['queued'], 0); self.assertEqual(publisher.snapshot()['refused'], 1)

    def test_origin_must_be_https_or_loopback_http_and_post_uses_the_fixed_route_and_bearer(self):
        for bad in ('http://brain.lan', 'https://brain/path', 'ftp://x', 'https://u:p@brain'):
            with self.subTest(origin=bad), self.assertRaises(ValueError):
                origin_parts(bad)
        seen = {}
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen.update(path=self.path, auth=self.headers['Authorization'],
                            body=json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(202); self.end_headers()
            def log_message(self, *args):
                pass
        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.handle_request); thread.start()
        try:
            event = events.health(**SCOPE, observed_at=1., component='jev', ok=True, detail=None)
            body = json.dumps(events.envelope([dict(event, sent_at=2.)])).encode()
            status = http_post('http://127.0.0.1:%d' % server.server_port, '/api/lighting-brain/v1/homes/victoria/events',
                               body, 'secret-token-' + 'x' * 30, 2.)
        finally:
            thread.join(3); server.server_close()
        self.assertEqual(status, 202)
        self.assertEqual(seen['path'], '/api/lighting-brain/v1/homes/victoria/events')
        self.assertEqual(seen['auth'], 'Bearer secret-token-' + 'x' * 30)
        self.assertEqual(seen['body']['events'][0]['type'], 'health')


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.brain, self.deep = Clock(), Brain(), FakeDeep()
        self.occupied = 'off'
        self.publisher = BrainPublisher(origin='https://brain.invalid', home='victoria', token=lambda: 't' * 40,
                                        post=self.brain, clock=self.clock, monotonic=self.clock)
        self.loop = activity_loop.ActivityLoop(deep=self.deep, occupancy=lambda: self.occupied, publisher=self.publisher,
            clock=self.clock, pricing={'usd_per_mtok_input': 2., 'usd_per_mtok_output': 8.},
            zones={'sofa': [[0, 0], [3, 0], [3, 3], [0, 3]]})

    def step(self, seconds=.5, n=1):
        for _ in range(n):
            self.clock.now += seconds
            self.loop.tick()

    def test_occupancy_gates_start_and_pauses_two_minutes_after_the_room_empties(self):
        self.step(n=4); self.assertNotIn('start', self.deep.calls)
        self.occupied = 'unknown'; self.step(); self.assertNotIn('start', self.deep.calls, 'unknown never wakes it')
        self.occupied = 'on'; self.step(); self.assertEqual(self.deep.calls.count('start'), 1)
        self.occupied = 'off'; self.step(seconds=119.); self.assertNotIn('stop:activity_loop_room_empty', self.deep.calls)
        self.occupied = 'unknown'; self.step(seconds=.5)
        self.assertNotIn('stop:activity_loop_room_empty', self.deep.calls)
        self.step(seconds=1.); self.assertIn('stop:activity_loop_room_empty', self.deep.calls)
        self.assertEqual(self.loop.snapshot()['state'], 'paused')
        self.occupied = 'on'; self.step(); self.assertEqual(self.deep.calls.count('start'), 2)

    def test_publication_cadence_activity_per_answer_pose_at_most_1hz_health_heartbeat_and_cost_per_minute(self):
        self.occupied = 'on'
        self.deep.seen = {'running': True, 'health': healthy(), 'person_count': 1,
                          'usage': {'calls': 0, 'input_tokens': 0, 'output_tokens': 0}}
        self.step()
        for i in range(1, 121):  # 60 s of 0.5 s ticks: a new answer every 4 s, a new placement every tick
            self.deep.seen['pose'] = {'key': i, 'observed_at': self.clock.now, 'people': [person()]}
            if i % 8 == 0:
                self.deep.seen['activity'] = answer(i, at=self.clock.now)
            self.deep.seen['usage'] = {'calls': i // 4, 'input_tokens': 900 * (i // 4), 'output_tokens': 50 * (i // 4)}
            self.step()
        activity = self.brain.events('activity')
        self.assertEqual(len(activity), 15, 'one event per JEV answer, never repeated')
        self.assertEqual({(e['label'], e['zone_hint'], e['person_count'], e['model']) for e in activity},
                         {('reading', 'sofa', 1, 'jev-1.13.0')})
        poses = self.brain.events('person_pose')
        self.assertTrue(55 <= len(poses) <= 61, len(poses))
        self.assertTrue(all(b['sent_at'] - a['sent_at'] >= 1 for a, b in zip(poses, poses[1:])), 'at most 1 Hz')
        self.assertEqual(poses[-1]['people'][0]['approximate'], True)
        health = self.brain.events('health')
        self.assertEqual(len([e for e in health if e['component'] == 'jev']), 2, 'on change + 60 s heartbeat')
        cost = self.brain.events('cost')
        self.assertEqual([e['calls'] for e in cost], [30])
        self.assertAlmostEqual(cost[0]['est_usd'], (27000 * 2 + 1500 * 8) / 1e6)
        self.loop.pricing = {}; self.step(seconds=60.)
        self.assertIsNone(self.brain.events('cost')[-1]['est_usd'], 'unpriced cost is null, never invented')

    def test_jev_failure_and_staleness_report_health_false(self):
        self.occupied = 'on'
        self.deep.seen = {'running': True, 'health': healthy(), 'activity': answer(1)}
        self.step()
        self.deep.seen['health'] = {**healthy(), 'jev': (False, 'jev_egress_blocked_indeterminate')}
        self.step()
        jev = [e for e in self.brain.events('health') if e['component'] == 'jev']
        self.assertEqual((jev[-1]['ok'], jev[-1]['detail']), (False, 'jev_egress_blocked_indeterminate'))
        self.deep.seen['health'] = healthy(); self.step(seconds=31.)
        jev = [e for e in self.brain.events('health') if e['component'] == 'jev']
        self.assertEqual((jev[-1]['ok'], jev[-1]['detail']), (False, 'jev_stale'), 'no new answer for 30 s')
        self.deep.seen['activity'] = answer(2); self.step()
        jev = [e for e in self.brain.events('health') if e['component'] == 'jev']
        self.assertEqual(jev[-1]['ok'], True)

    def test_stop_holds_until_resume_and_an_external_stop_is_an_operator_stop(self):
        self.occupied = 'on'; self.deep.seen = {'running': True, 'health': healthy()}; self.step()
        self.loop.stop(); self.step(n=3)
        self.assertEqual(self.deep.calls.count('start'), 1); self.assertIn('stop:operator_stop', self.deep.calls)
        self.assertEqual(self.loop.snapshot()['state'], 'stopped')
        deep = [e for e in self.brain.events('health') if e['component'] == 'deep_stack']
        self.assertEqual((deep[-1]['ok'], deep[-1]['detail']), (False, 'stopped_operator_stop'))
        self.loop.resume(); self.step(); self.assertEqual(self.deep.calls.count('start'), 2)
        self.deep.seen = {'running': False, 'ended_externally': True}; self.step()
        self.assertEqual(self.loop.snapshot()['stopped_reason'], 'operator_stop')
        self.step(n=4); self.assertEqual(self.deep.calls.count('start'), 2)

    def test_no_overall_cap_an_hour_occupied_keeps_running(self):
        self.occupied = 'on'; self.deep.seen = {'running': True, 'health': healthy()}
        for minute in range(61):
            self.deep.seen['activity'] = answer(minute + 1, at=self.clock.now + 60)
            self.step(seconds=60.)
        self.assertEqual(self.deep.calls.count('start'), 1)
        self.assertEqual(self.loop.snapshot()['state'], 'running')
        self.assertGreaterEqual(self.loop.snapshot()['running_for_s'], 3600)
        self.assertEqual(len(self.brain.events('activity')), 61)

    def test_config_is_exact_and_bounded(self):
        good = {'schema': activity_loop.SCHEMA, 'enabled': True, 'home': 'victoria', 'room': 'den', 'camera_id': 'den',
                'experiment_id': 'apartment-viewing', 'brain': {'origin': 'https://la.example', 'token_file': '/p/t',
                'ca_file': None}, 'timings': {'vacancy_pause_s': 120, 'roll_margin_s': 20},
                'pricing': {'usd_per_call': None}, 'zones': None}
        self.assertEqual(activity_loop.check_config(deepcopy(good)), good)
        for change in (lambda c: c.update(extra=1), lambda c: c.update(experiment_id='placement-best'),
                       lambda c: c['brain'].update(origin='http://la.example'), lambda c: c['timings'].update(cap_s=300),
                       lambda c: c['pricing'].update(usd_per_call=-1), lambda c: c.update(zones={'kitchen': []})):
            value = deepcopy(good); change(value)
            with self.subTest(change=change), self.assertRaises(ValueError):
                activity_loop.check_config(value)


class PoseTests(unittest.TestCase):
    def packet(self, joints):
        return {'renderable': True, 'approximate_pose': {'joints': [{'id': i, 'position_m': p} for i, p in joints.items()]}}

    def test_room_frame_shoulders_head_and_pitch_from_existing_placement(self):
        raw = {'joints': [{'joint_id': 0, 'pixel': [320, 140], 'status': 'supported'},
                          {'joint_id': 3, 'pixel': [312, 138], 'status': 'supported'},
                          {'joint_id': 4, 'pixel': [328, 138], 'status': 'supported'},
                          {'joint_id': 5, 'pixel': [300, 150], 'status': 'supported'},
                          {'joint_id': 6, 'pixel': [340, 150], 'status': 'supported'}]}
        entry = activity_pose.placed_person(self.packet({0: [1, 2, 1.5], 5: [.8, 2, 1.2], 6: [1.2, 2, 1.2]}), raw)
        self.assertEqual(entry['shoulders_m'], {'left': [.8, 2., 1.2], 'right': [1.2, 2., 1.2]})
        self.assertEqual(entry['head_m'], [1., 2., 1.5]); self.assertTrue(entry['approximate'])
        # A nose low against the shoulders looks down: positive in the contract's sign.
        self.assertAlmostEqual(entry['head_pitch_deg'], (.45 - .25) * 250)
        self.assertGreater(entry['head_pitch_deg'], 0)
        ears = activity_pose.placed_person(self.packet({3: [1, 2, 1.5], 4: [1.2, 2, 1.5], 5: [.8, 2, 1.2]}))
        self.assertEqual(ears['head_m'], [1.1, 2., 1.5]); self.assertIsNone(ears['head_pitch_deg'])
        self.assertIsNone(activity_pose.placed_person({'renderable': False}))
        zones = activity_pose.check_zones({'sofa': [[0, 0], [2, 0], [2, 3], [0, 3]], 'desk': [[3, 0], [5, 0], [5, 2]]})
        self.assertEqual(activity_pose.zone_hint(entry, zones), 'sofa')
        self.assertIsNone(activity_pose.zone_hint(entry, {}))

    def test_zone_hint_reads_al4s_targets_export_in_any_up_axis(self):
        # AL-4's lighting-brain-targets-1 export: 3D seat vertices and the seat
        # normal. Here the room frame is y-up; the test projects along the normal.
        export = {'schema': 'lighting-brain-targets-1', 'version': 1, 'home': 'victoria', 'room': 'den',
                  'targets': {'sofa': {'zone_vertices_m': [[0, .45, 0], [2, .45, 0], [2, .45, .9], [0, .45, .9]],
                                       'zone_centre_m': [1, .45, .45], 'zone_normal_unit': [0, 1, 0], 'seat_m': [1, .45, .45]},
                              'desk': {'zone_vertices_m': [[3, .75, 0], [4.5, .75, 0], [4.5, .75, .7]],
                                       'zone_centre_m': [4, .75, .2], 'zone_normal_unit': [0, 1, 0]}}}
        zones = activity_pose.check_zones(deepcopy(export), home='victoria', room='den')
        seated = {'shoulders_m': {'left': [.8, 1.05, .5], 'right': [1.2, 1.05, .5]}, 'head_m': [1, 1.3, .5]}
        self.assertEqual(activity_pose.zone_hint(seated, zones), 'sofa')
        self.assertIsNone(activity_pose.zone_hint(dict(seated, shoulders_m={'left': [2.4, 1.1, .5], 'right': [2.8, 1.1, .5]}), zones))
        self.assertEqual(activity_pose.zone_hint({'shoulders_m': {'left': None, 'right': None}, 'head_m': [4, 1.2, .2]}, zones), 'desk')
        for change in (lambda e: e.update(home='other'), lambda e: e['targets']['sofa'].update(zone_normal_unit=[0, 2, 0]),
                       lambda e: e['targets'].update(kitchen={}), lambda e: e.update(version=2)):
            value = deepcopy(export); change(value)
            with self.subTest(change=change), self.assertRaises(ValueError):
                activity_pose.check_zones(value, home='victoria', room='den')

    def test_a_zones_path_is_read_at_build_from_the_private_reader(self):
        from activity_loop_runtime import build
        config = {'schema': activity_loop.SCHEMA, 'enabled': True, 'home': 'victoria', 'room': 'den', 'camera_id': 'den',
                  'experiment_id': 'apartment-viewing', 'brain': {'origin': 'https://la.example', 'token_file': '/p/t',
                  'ca_file': None}, 'timings': {}, 'pricing': {}, 'zones': '/private/lighting-brain-targets.json'}
        activity_loop.check_config(config)
        with self.assertRaises(ValueError):
            activity_loop.check_config(dict(config, zones='relative/targets.json'))
        export = {'schema': 'lighting-brain-targets-1', 'version': 1, 'home': 'victoria', 'room': 'den',
                  'targets': {'sofa': {'zone_vertices_m': [[0, 0, .45], [2, 0, .45], [2, .9, .45]], 'zone_normal_unit': [0, 0, 1]}}}
        read = []
        loop = build(config, controller=SimpleNamespace(store=SimpleNamespace(clock=lambda: 0., settings=None), runtime=None), occupancy=lambda: 'off', read_token=lambda path: 't' * 40,
                     post=lambda body: 200, read_json=lambda path: read.append(path) or export)
        self.assertEqual(read, ['/private/lighting-brain-targets.json'])
        self.assertEqual(set(loop.zones), {'sofa'})

    def test_person_count_uses_the_duplicate_merge(self):
        box = dict(label='person', x=260., y=80., w=120., h=260.)
        result = {'objects': [{'label': 'person', 'instance_id': 'p', 'confidence': .9},
                              {'label': 'person', 'instance_id': 'd', 'confidence': .3}],
                  'boxes': [dict(box, instance_id='p', confidence=.9), dict(box, instance_id='d', confidence=.3, x=261.)]}
        self.assertEqual(activity_pose.person_count(result), 1)
        result['objects'][1]['confidence'] = result['boxes'][1]['confidence'] = .8
        self.assertEqual(activity_pose.person_count(result), 2)


class RouteTests(unittest.TestCase):
    def test_status_stop_resume_routes_and_lifespan(self):
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient
        from auth import AuthorizationError, Principal
        from activity_loop_runtime import install
        calls = []
        class Loop:
            deep = None
            def start_thread(self): calls.append('start_thread')
            def shutdown(self): calls.append('shutdown')
            def stop(self, reason): calls.append('stop:' + reason)
            def resume(self): calls.append('resume')
            def snapshot(self): return {'state': 'running', 'commands_lights': False}
        app = FastAPI()
        @app.middleware('http')
        async def principal(request: Request, call_next):
            caps = frozenset(request.headers.get('x-caps', '').split(','))
            request.state.principal = Principal('t', 'victoria', caps)
            try:
                return await call_next(request)
            except AuthorizationError:
                return JSONResponse({'error': 'unauthorized'}, status_code=401)
        install(app, Loop())
        with TestClient(app) as client:
            self.assertEqual(calls, ['start_thread'])
            self.assertEqual(client.get('/api/perception-lab/v1/activity-loop', headers={'x-caps': 'read'}).json()['state'], 'running')
            self.assertEqual(client.post('/api/perception-lab/v1/activity-loop/stop', headers={'x-caps': 'read'}).status_code, 401)
            self.assertEqual(client.post('/api/perception-lab/v1/activity-loop/stop', headers={'x-caps': 'record'}).status_code, 200)
            client.post('/api/perception-lab/v1/activity-loop/resume', headers={'x-caps': 'record'})
        self.assertEqual(calls, ['start_thread', 'stop:operator_stop', 'resume', 'shutdown'])


if __name__ == '__main__':
    unittest.main()
