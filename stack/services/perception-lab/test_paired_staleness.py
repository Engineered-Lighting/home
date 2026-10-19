"""Paired-scheduler staleness: an arm's camera expiring while its own newer
frame is in flight (inflight_hold, awaiting_inflight_frame), the per-arm
sampling validity (runtime.arm_valid_until, arm-sampling-1), the wire's age
words for expired frames, and a replay over the recorded deliveries of session
f1739301 (test_paired_staleness_f1739301.json: session-relative timings only)."""
import heapq
import json
from pathlib import Path
import tempfile
import time
import unittest

import jev_wire as wire
from runtime import ARM_VALIDITY_CAP_S, ARM_VALIDITY_RULE, PairedRunner, arm_valid_until
from service_runtime import REASONING_CADENCE_DEFAULTS, REASONING_SKIPS, cadence_decision, inflight_hold
import test_jev_cadence as cadence

SETTINGS = dict(REASONING_CADENCE_DEFAULTS)
FIXTURE = Path(__file__).with_name('test_paired_staleness_f1739301.json')


class ArmValidityTests(unittest.TestCase):
    def frame(self, observed=10.0, valid=12.1):
        return {'observed_at': observed, 'valid_until': valid}

    def test_sampling_below_the_source_rate_extends_by_the_extra_wait_only(self):
        self.assertEqual(arm_valid_until(self.frame()), 12.1, 'unsampled: the source claim')
        self.assertEqual(arm_valid_until(self.frame(), 2), 12.1, 'at the 2 Hz source rate: unchanged')
        self.assertEqual(arm_valid_until(self.frame(), 4), 12.1, 'faster than the source: unchanged')
        self.assertAlmostEqual(arm_valid_until(self.frame(), 1), 12.6, msg='1 Hz: one period instead of one interval')
        self.assertAlmostEqual(arm_valid_until(self.frame(valid=10.5), 1), 11.0, msg='a short source claim stays short')
        self.assertEqual(arm_valid_until(self.frame(), .5), 10.0 + ARM_VALIDITY_CAP_S, 'capped at the 3 s now horizon')
        self.assertEqual(arm_valid_until(self.frame(valid=13.5), .5), 13.5, 'never shorter than the source claim')

    def test_the_runner_applies_the_arm_validity_to_dispatch_currency_and_output(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        from config import Settings
        from store import Store
        store = Store(Settings(storage_root=Path(directory.name), min_free_bytes=0), clock=lambda: 100.0)
        self.addCleanup(store.close)
        session, _ = store.create_session('den', 'detection', 'Fixture', 'request1')
        store.transition(session['id'], 'starting')
        store.transition(session['id'], 'recording')
        media = store.add_media(session['id'], b'synthetic-frame-bytes', 'image/jpeg', {'fixture': True})
        profiles = {arm: {'profile_id': arm, 'artifact_sha256': 'a'*64, 'model_version': 'fixture-1', 'engine_epoch': 'engine1'}
                    for arm in ('a', 'b')}
        calls = []

        def execute(request):
            calls.append(request)
            return {'request_id': request['request_id'], 'engine_epoch': 'engine1', 'artifact_sha256': 'a'*64,
                    'terminal': True, 'state': 'completed', 'result': {'objects': []}}
        runner = PairedRunner(store, session['id'], profiles, execute, clock=lambda: 100.0, sampling_hz={'a': 1, 'b': 2})
        self.addCleanup(runner.pool.shutdown, wait=True)
        self.addCleanup(runner.close)
        # The source claims 0.8 s; it is 1.0 s old now. The 1 Hz arm holds it
        # 0.5 s longer (current); the 2 Hz arm finds it expired before dispatch.
        runner.offer({'frame_id': 'f1', 'capture_epoch': 'c1', 'site_id': 'victoria', 'camera_id': 'den',
                      'session_id': session['id'], 'observed_at': 99.0, 'available_at': 99.1, 'valid_until': 99.8,
                      'media_ids': [media['id']]})
        deadline = time.monotonic()+2
        while not any(e['kind'] == 'perception.output' for e in store.list_events(session['id'])):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.005)
        events = store.list_events(session['id'])
        self.assertEqual([c['arm'] for c in calls], ['a'])
        self.assertEqual(calls[0]['frame']['valid_until'], 99.8, 'the executor sees the source frame unchanged')
        output = next(e['payload'] for e in events if e['kind'] == 'perception.output')
        delivery = next(e['payload'] for e in events if e['kind'] == 'perception.delivery')
        self.assertEqual((output['valid_until'], output['current']), (100.3, True))
        self.assertEqual((delivery['valid_until'], delivery['validity_rule']), (100.3, ARM_VALIDITY_RULE))
        self.assertTrue(any(e['kind'] == 'perception.expired' and e['payload']['arm'] == 'b' for e in events))


class InflightHoldRuleTests(unittest.TestCase):
    def hold(self, **kw):
        args = dict(camera='expired_historical', now=100.0, expired_at=99.9, inflight_deadline=102.5, debounce_s=2.0)
        return inflight_hold(**{**args, **kw})

    def test_hold_only_an_expired_camera_with_a_newer_frame_in_flight(self):
        self.assertEqual(self.hold(), 101.9, 'until expiry + debounce at most')
        self.assertEqual(self.hold(inflight_deadline=100.8), 100.8, 'or the in-flight frame\'s own deadline')
        self.assertIsNone(self.hold(camera='current'))
        self.assertIsNone(self.hold(inflight_deadline=None), 'nothing in flight: a real stall surfaces at once')
        self.assertIsNone(self.hold(now=102.0), 'a stalled worker surfaces debounce_s after the expiry')
        self.assertIsNone(self.hold(expired_at=None))

    def test_cadence_decision_skips_a_held_offer_and_wakes_at_the_hold(self):
        last = {'key': 'current-key', 'at': 90.0, 'config': (1,)}
        decide = lambda **kw: cadence_decision(last, **{**dict(now=100.0, key='expired-key', visual=True, config=(1,),
                                                                gated=False, occupied=True, interval=2.0, settings=SETTINGS), **kw})
        self.assertIn('awaiting_inflight_frame', REASONING_SKIPS)
        self.assertEqual(decide(hold_until=100.1), ('skip', 'awaiting_inflight_frame', 100.1))
        self.assertEqual(decide(hold_until=None), ('offer', 'facts_changed', None))
        self.assertEqual(decide(hold_until=99.0), ('offer', 'facts_changed', None), 'a passed hold never holds')
        self.assertEqual(decide(now=91.0, hold_until=100.1), ('skip', 'rate_limited', 92.0), 'rate limits come first')
        self.assertEqual(decide(key='current-key', now=101.0, hold_until=101.5), ('skip', 'awaiting_inflight_frame', 101.5),
                         'a keepalive of an expired camera waits too')


class CameraProvider(cadence.FactsProvider):
    """The fake v6 provider with a per-arm camera word in its facts key."""
    def __init__(self, sim):
        super().__init__(sim)
        self.camera = {'a': 'current', 'b': 'current'}

    def facts_key(self, records, *, arm, **kw):
        facts = super().facts_key(records, arm=arm, **kw)
        return {**facts, 'key': facts['key'] + ':' + self.camera[arm], 'camera': self.camera[arm]}


class PairedTimingRaceTests(unittest.TestCase):
    """The live scheduler and reasoner on a simulated ledger clock: arm A's
    delivery wakes the scheduler just after arm B's frame expired, while B's
    newer frame is already dispatched."""
    tearDown = cadence.LiveCadenceTests.tearDown
    factory = cadence.LiveCadenceTests.factory
    step = cadence.LiveCadenceTests.step
    status = cadence.LiveCadenceTests.status
    requests = cadence.LiveCadenceTests.requests

    def setUp(self):
        cadence.LiveCadenceTests.setUp(self)
        self.provider = CameraProvider(self.sim)

    def paired(self, arm, request_id, state, *, observed, valid, updated):
        with self.sim.store.transaction() as db:
            db.execute('INSERT OR REPLACE INTO paired_requests VALUES(?,?,?,?,?,?,?,?,?)', (
                request_id, self.sim.sid, arm, 1, 'd'*64, json.dumps({'frame': {'observed_at': observed, 'valid_until': valid},
                                                                      'sampling_hz': 1}), state, None, updated))

    def race(self):
        """B offered current, then its frame expires 0.05 s before A's delivery
        with its newer frame running; returns B's expiry and in-flight deadline."""
        self.step(0)
        self.assertEqual(len(self.requests('b')), 1)
        self.sim.advance(3.0)
        now = self.sim.now
        # B's newest delivered frame (1 Hz: source 2.1 s + 0.5 s) expired 0.05 s ago.
        self.paired('b', 'b-old', 'completed', observed=now-2.65, valid=now-2.65+2.1, updated=now-1.9)
        self.paired('b', 'b-new', 'running', observed=now-1.0, valid=now-1.0+2.1, updated=now-0.6)
        self.provider.camera['b'] = 'expired_historical'
        self.sim.preview()
        self.sim.output('a')  # A's delivery wakes the scheduler
        self.sim.tick()
        return now-0.05, now-1.0+2.6

    def test_an_expired_camera_waits_for_its_in_flight_frame_and_never_offers(self):
        expired_at, deadline = self.race()
        self.assertEqual(len(self.requests('b')), 1, 'no offer of an expired camera while its frame is in flight')
        status = self.status()
        self.assertEqual(status['skipped']['awaiting_inflight_frame'], {'a': 0, 'b': 1})
        self.assertAlmostEqual(self.sim.runtime.reasoning_schedule[self.sim.sid]['wake_at'], min(deadline, expired_at+2.0))
        # ~0.1 s later B's own frame lands: the key is the one offered before.
        self.sim.advance(0.1)
        self.paired('b', 'b-new', 'completed', observed=self.sim.now-1.1, valid=self.sim.now-1.1+2.1, updated=self.sim.now)
        self.provider.camera['b'] = 'current'
        self.sim.tick()
        self.sim.advance(2.5)
        self.sim.tick()
        self.assertEqual(len(self.requests('b')), 1, 'the flip and flip-back cost no JEV request')
        self.assertEqual(self.status()['offer_reasons']['facts_changed']['b'], 0)

    def test_a_stalled_worker_still_surfaces_within_debounce(self):
        expired_at, _ = self.race()
        self.assertEqual(len(self.requests('b')), 1)
        self.sim.advance(1.0)
        self.sim.tick()
        self.assertEqual(len(self.requests('b')), 1, 'still inside expiry + debounce_s')
        self.sim.advance(expired_at+2.0-self.sim.now+0.01)
        self.sim.tick()
        self.assertEqual(len(self.requests('b')), 2, 'the stall is offered (expired) debounce_s after the expiry')
        self.assertLessEqual(self.sim.now-expired_at, 2.1)

    def test_a_camera_stall_without_a_newer_frame_surfaces_at_once(self):
        self.step(0)
        self.sim.advance(3.0)
        now = self.sim.now
        self.paired('b', 'b-old', 'completed', observed=now-2.65, valid=now-2.65+2.1, updated=now-1.9)
        self.provider.camera['b'] = 'expired_historical'
        self.sim.output('a')
        self.sim.tick()
        self.assertEqual(len(self.requests('b')), 2)
        self.assertEqual(self.status()['skipped']['awaiting_inflight_frame']['b'], 0)


class ExpiredAgeWordTests(unittest.TestCase):
    def now_block(self, cutoff, valid):
        record = {'id': 'r1', 'source_kind': 'perception.output', 'observed_at': 100.0, 'valid_until': valid,
                  'objects': [{'label': 'person', 'box': [0, 0, 10, 10]}], 'boxes': [{'label': 'person', 'x': 0, 'y': 0, 'w': 10, 'h': 10}]}
        block, _ = wire._now([record], [], cutoff, {})
        return block['camera_evidence'], block['age']

    def test_fresh_under_3s_names_current_evidence_only(self):
        self.assertEqual(self.now_block(101.0, 102.6), ('current', 'fresh_under_3s'))
        self.assertEqual(self.now_block(102.7, 102.6), ('expired_historical', 'about_5_s'))
        self.assertEqual(self.now_block(112.0, 102.6), ('expired_historical', 'about_20_s'))
        # Every validity the runner writes keeps a current frame under 3 s old.
        self.assertLessEqual(arm_valid_until({'observed_at': 100.0, 'valid_until': 102.1}, .2), 100.0 + ARM_VALIDITY_CAP_S)


def replay_expired_offers(fixture, *, validity, hold, debounce_s=REASONING_CADENCE_DEFAULTS['debounce_s']):
    """Per arm, from the recorded deliveries alone: the expiry episodes a
    scheduler tick would see (a tick on every delivery of either arm and at
    least once a second, as live) and so could offer as expired_historical,
    and the gaps where an arm's next result landed after its previous one
    expired. validity: the arm-sampling-1 rule instead of the recorded source
    validity; hold: inflight_hold, waking at its deadline."""
    rows = [dict(zip(fixture['columns'], r)) for r in fixture['deliveries']]

    def valid(r):
        frame = {'observed_at': r['observed_at'], 'valid_until': r['valid_until']}
        return arm_valid_until(frame, fixture['sampling_hz'][r['arm']]) if validity else r['valid_until']
    result = {}
    end = max(r['completed_at'] for r in rows)
    for arm in ('a', 'b'):
        mine = sorted((r for r in rows if r['arm'] == arm), key=lambda r: r['completed_at'])
        ticks = [r['completed_at'] for r in rows] + [float(t) for t in range(int(end)+1)]
        heapq.heapify(ticks)
        episodes, word = 0, None
        while ticks:
            t = heapq.heappop(ticks)
            done = [r for r in mine if r['completed_at'] <= t]
            if not done:
                continue
            latest = done[-1]
            if valid(latest) > t:
                word = 'current'
                continue
            if word == 'expired':
                continue
            if hold:
                deadline = max((valid(r) for r in mine if r['dispatched_at'] <= t < r['completed_at']
                                and r['observed_at'] > latest['observed_at'] and valid(r) > t), default=None)
                until = inflight_hold(camera='expired_historical', now=t, expired_at=valid(latest),
                                      inflight_deadline=deadline, debounce_s=debounce_s)
                if until is not None:
                    heapq.heappush(ticks, until)
                    continue
            episodes += 1
            word = 'expired'
        gaps = sum(1 for p, n in zip(mine, mine[1:]) if n['completed_at'] > valid(p))
        result[arm] = {'expired_episodes': episodes, 'expiry_gaps': gaps}
    return result


class RecordedReplayTests(unittest.TestCase):
    """Session f1739301 (den-facts-7, both arms 1 Hz): 18 of B's answers were
    insufficient_evidence on an expired camera offered 0.00-0.09 s after A's
    delivery, with B's own newer frame in flight."""

    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))

    def test_the_recorded_rules_reproduce_the_expiry_race(self):
        recorded = replay_expired_offers(self.fixture, validity=False, hold=False)
        self.assertEqual(recorded, {'a': {'expired_episodes': 6, 'expiry_gaps': 35},
                                    'b': {'expired_episodes': 36, 'expiry_gaps': 61}})
        offers = [dict(zip(self.fixture['offers']['columns'], r)) for r in self.fixture['offers']['rows']]
        expired = [o for o in offers if o['camera_evidence'] == 'expired_historical']
        self.assertEqual((sum(o['arm'] == 'a' for o in expired), sum(o['arm'] == 'b' for o in expired)), (1, 18))

    def test_arm_validity_and_the_in_flight_hold_leave_no_expired_offer(self):
        self.assertEqual(replay_expired_offers(self.fixture, validity=False, hold=True),
                         {'a': {'expired_episodes': 2, 'expiry_gaps': 35}, 'b': {'expired_episodes': 2, 'expiry_gaps': 61}})
        self.assertEqual(replay_expired_offers(self.fixture, validity=True, hold=False),
                         {'a': {'expired_episodes': 0, 'expiry_gaps': 1}, 'b': {'expired_episodes': 2, 'expiry_gaps': 6}})
        self.assertEqual(replay_expired_offers(self.fixture, validity=True, hold=True),
                         {'a': {'expired_episodes': 0, 'expiry_gaps': 1}, 'b': {'expired_episodes': 0, 'expiry_gaps': 6}})


if __name__ == '__main__':
    unittest.main()
