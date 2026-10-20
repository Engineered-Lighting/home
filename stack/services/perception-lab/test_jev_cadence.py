"""Facts-bound validity, live smoothing and the facts-key cadence (plan §5.2,
§5.3, §6.1): the pure rules, the live scheduler and reasoner on a simulated
ledger clock with a local fake provider, findings and the timeline payload.
Nothing leaves the process."""
import json
import tempfile
import unittest

import jev_smoothing as smoothing
from controller import Controller
from live_reasoning import LiveReasoning
from reasoning import ACTIVITIES
from service_runtime import REASONING_CADENCE_DEFAULTS, cadence_decision, cadence_settings, occupancy_step
from test_reasoning_load import Simulation, SyncPool

SETTINGS = dict(REASONING_CADENCE_DEFAULTS)


class ValidityRuleTests(unittest.TestCase):
    """jev_smoothing.Validity and facts_bound, on session seconds."""

    def test_answer_valid_until_hold_when_key_unchanged(self):
        v = smoothing.Validity(12)
        self.assertEqual(v.observe(9.0, key='k1'), [])
        self.assertEqual(v.deliver(10.0, 'r1', 'k1'), [])
        for t in range(11, 22):
            self.assertEqual(v.observe(float(t), key='k1'), [], t)
        self.assertEqual(v.observe(22.5, key='k1'), [{'request_id': 'r1', 'valid_through': 22.0, 'reason': 'hold_max'}])
        self.assertEqual(v.observe(30.0, key='k2'), [], 'closed once')

    def test_key_change_newer_answer_and_vacancy_end_it_early(self):
        v = smoothing.Validity(12)
        v.observe(9.0, key='k1')
        v.deliver(10.0, 'r1', 'k1')
        self.assertEqual(v.observe(14.0, key='k2'), [{'request_id': 'r1', 'valid_through': 14.0, 'reason': 'facts_changed'}])
        v.deliver(15.0, 'r2', 'k2')
        self.assertEqual(v.deliver(17.0, 'r3', 'k2'), [{'request_id': 'r2', 'valid_through': 17.0, 'reason': 'superseded'}])
        self.assertEqual(v.observe(18.0, key='k2', vacant=True), [{'request_id': 'r3', 'valid_through': 18.0, 'reason': 'occupancy_vacant'}])
        v.observe(19.0, key='k3')
        v.deliver(20.0, 'r4', 'k3')
        self.assertEqual(v.local_abstain(21.0), [{'request_id': 'r4', 'valid_through': 21.0, 'reason': 'local_abstain'}])

    def test_facts_that_moved_on_while_in_flight_give_no_extension(self):
        v = smoothing.Validity(12)
        v.observe(9.0, key='k1')
        v.observe(10.0, key='k2')     # the facts changed after the offer at 9 s
        self.assertEqual(v.deliver(10.5, 'r1', 'k1'),
                         [{'request_id': 'r1', 'valid_through': 10.5, 'reason': 'facts_changed_before_arrival'}])
        # An answer without a facts key is not facts-bound: nothing to close.
        self.assertEqual(v.deliver(11.0, 'r2', None), [])
        self.assertEqual(v.observe(12.0, key='k3'), [])

    def test_facts_bound_never_shrinks_strict_validity_and_leaves_old_outputs_alone(self):
        old = {'start': 9.0, 'end': 10.5, 'available_at': 10.0, 'valid_until': 10.5, 'activities': ['reading']}
        new = dict(old, valid_through=22.0)
        closed = dict(old, start=11.0, available_at=12.0, end=13.0, valid_until=13.0, valid_through=24.0)
        result = smoothing.facts_bound([('r0', old), ('r1', new), ('r2', closed), ('r3', dict(new, start=30.0, end=31.0,
                                        available_at=30.2, valid_until=31.0, valid_through=42.2))],
                                       {'r2': 12.5, 'r3': 35.0})
        self.assertEqual(result[0], old, 'no valid_through: the strict prediction, unchanged')
        self.assertEqual((result[1]['end'], result[1]['valid_until']), (12.0, 12.0), 'cut at the next answer')
        self.assertEqual(result[2]['valid_until'], 13.0, 'a closure never goes below the strict valid_until')
        self.assertEqual(result[3]['valid_until'], 35.0)
        self.assertEqual(new['valid_until'], 10.5, 'inputs are not modified')


class CadenceRuleTests(unittest.TestCase):
    def decide(self, last, **kw):
        args = dict(now=100.0, key='k', visual=True, config=(1,), gated=False, occupied=True, interval=2.0, settings=SETTINGS)
        return cadence_decision(last, **{**args, **kw})

    def test_unchanged_facts_skip_keepalive_and_rate_limit(self):
        last = {'key': 'k', 'at': 95.0, 'config': (1,)}
        self.assertEqual(self.decide(None), ('offer', 'facts_changed', None))
        self.assertEqual(self.decide(last), ('skip', 'keepalive_not_due', None))
        self.assertEqual(self.decide(last, now=105.0), ('offer', 'keepalive', None))
        self.assertEqual(self.decide(last, occupied=False, now=200.0), ('skip', 'facts_unchanged', None))
        self.assertEqual(self.decide(last, key='k2', now=96.0), ('skip', 'rate_limited', 97.0))
        self.assertEqual(self.decide(last, key='k2'), ('offer', 'facts_changed', None))
        self.assertEqual(self.decide(last, config=(2,), now=97.5), ('offer', 'facts_changed', None))
        self.assertEqual(self.decide(last, visual=False), ('skip', 'no_visual_evidence', None))
        self.assertEqual(self.decide(last, gated=True), ('gate', 'no_person', None))
        # The per-arm minimum interval bounds keepalives too; a clock step back counts as elapsed.
        self.assertEqual(self.decide(last, now=102.0, interval=10.0, settings=dict(SETTINGS, keepalive_s=5.0)),
                         ('skip', 'keepalive_not_due', None))
        self.assertEqual(self.decide(last, now=50.0), ('offer', 'keepalive', None))
        self.assertEqual(self.decide(last, key='k2', now=96.0, settings=dict(SETTINGS, debounce_s=5.0)),
                         ('skip', 'rate_limited', 100.0))

    def test_occupancy_gates_only_after_the_hold(self):
        track = {}
        self.assertEqual(occupancy_step(track, 'unknown', 0.0, 10), (False, False, False), 'the hold starts at the first tick')
        self.assertEqual(occupancy_step(track, 'unknown', 10.5, 10), (True, False, False))
        self.assertEqual(occupancy_step(track, 'occupied', 11.0, 10), (False, False, True))
        # Inside the hold nothing is gated, but only an occupied reading makes a keepalive due.
        self.assertEqual(occupancy_step(track, 'vacant', 15.0, 10), (False, True, False))
        self.assertEqual(occupancy_step(track, 'vacant', 21.5, 10), (True, True, False))
        self.assertEqual(occupancy_step(track, 'occupied', 5.0, 10), (False, False, True), 'a clock step back')

    def test_settings_are_bounded(self):
        self.assertEqual(cadence_settings(None), SETTINGS)
        self.assertEqual(SETTINGS, {'mode': 'facts_key', 'keepalive_s': 10.0, 'debounce_s': 2.0, 'hold_max_s': 12.0,
                                    'gate_keepalive_s': 10.0})
        self.assertEqual(cadence_settings({'mode': 'new_evidence', 'keepalive_s': 20})['keepalive_s'], 20.0)
        for refused in ({'mode': 'always'}, {'keepalive_s': 1}, {'hold_max_s': 31}, {'debounce_s': True}, {'extra': 1},
                        {'gate_keepalive_s': float('nan')}, []):
            with self.subTest(refused=refused), self.assertRaises(ValueError):
                cadence_settings(refused)

    def test_live_config_key(self):
        from live_detector_app import validate_config
        from test_ha_provisioning import live_config
        self.assertEqual(validate_config(live_config(reasoning_cadence={'mode': 'new_evidence'}))['reasoning_cadence'],
                         {'mode': 'new_evidence'})
        for refused in (None, {'mode': 'x'}, {'keepalive_s': 0}):
            with self.subTest(refused=refused), self.assertRaises(ValueError):
                validate_config(live_config(reasoning_cadence=refused))


class FactsProvider:
    """A local fake of the v6 provider: controlled facts keys, instant answers."""
    wire = 'den-facts-6'

    def __init__(self, sim):
        self.sim, self.keys, self.people, self.activity, self.calls = sim, {'a': 'k1', 'b': 'k1'}, 'one', 'reading', []

    def facts_key(self, records, *, site_id, session_id, arm, generation, cutoff, window_seconds=10, shared_context=None,
                  context_snapshot=None):
        return {'key': self.keys[arm], 'people': self.people, 'camera': 'current', 'cue_state': 'book_near_hand'}

    def evaluate(self, records, *, request_id, arm, cutoff, **_):
        self.calls.append((arm, cutoff))
        probabilities = {k: (.8 if k == self.activity else .2/(len(ACTIVITIES)-1)) for k in ACTIVITIES}
        return {'status': 'ready', 'judgment': {'activity': self.activity, 'confidence': .8, 'probabilities': probabilities,
                                                'model_version': 'jev-1.13.0'},
                'provenance': {'request_id': request_id}, 'received_at': self.sim.now, 'actuation': False}


class Gate:
    hold = 10.0

    def __init__(self):
        self.state = 'occupied'

    def __call__(self):
        return {'state': self.state, 'reason': 'fixture'}


class LiveCadenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.sim = Simulation(self.directory.name, filler_files=0, offer_interval=2.0)
        self.provider = FactsProvider(self.sim)
        self.sim.runtime.reasoning_factory = self.factory
        self.sim.preview()

    def tearDown(self):
        self.sim.close()
        self.directory.cleanup()

    def factory(self, store, sid):
        reasoner = LiveReasoning(store, sid, self.provider)
        reasoner.pool.shutdown(wait=False)
        reasoner.pool = SyncPool()
        return reasoner

    def gate(self):
        gate = Gate()
        self.sim.runtime._occupancy_gate = lambda session, settings=None: gate
        return gate

    def step(self, seconds=1.0, arms=('a', 'b')):
        self.sim.advance(seconds)
        self.sim.preview()
        for arm in arms:
            self.sim.output(arm)
        self.sim.tick()

    def status(self):
        return self.sim.runtime.reasoning_scheduler_status(self.sim.sid)

    def payloads(self, kind):
        with self.sim.store.lock:
            return [json.loads(r[0]) for r in self.sim.store.db.execute(
                "SELECT payload FROM events WHERE session_id=? AND kind=? ORDER BY id", (self.sim.sid, kind))]

    def requests(self, arm='a'):
        return [c for c in self.provider.calls if c[0] == arm]

    def test_unchanged_facts_skipped_and_counted_keepalive_after_10s(self):
        self.step(0)
        self.assertEqual(len(self.requests()), 1, 'the first offer (it creates the reasoner)')
        for _ in range(9):
            self.step()
        self.assertEqual(len(self.requests()), 1, 'new frames with unchanged facts are not requested')
        status = self.status()
        self.assertEqual(status['basis'], 'facts_key_cadence')
        self.assertEqual(status['skipped']['keepalive_not_due'], {'a': 9, 'b': 9})
        self.step(1.1)
        self.assertEqual(len(self.requests()), 2, 'keepalive after 10 s while occupied')
        self.assertEqual(self.status()['offer_reasons']['keepalive'], {'a': 1, 'b': 1})
        # A facts change is offered at once when 2 s have passed; sooner it waits.
        self.provider.keys['a'] = 'k2'
        self.step(1.0)
        self.assertEqual(len(self.requests()), 2)
        self.assertEqual(self.status()['skipped']['rate_limited']['a'], 1)
        self.step(1.0)
        self.assertEqual(len(self.requests()), 3)
        self.assertEqual(self.status()['offer_reasons']['facts_changed']['a'], 1)
        self.assertEqual(len(self.requests('b')), 2, 'arm b facts unchanged: no offer')
        # Nobody in view and no occupancy gate provisioned: no keepalive.
        self.provider.people = 'none'
        for _ in range(12):
            self.step()
        self.assertEqual(len(self.requests()), 3)
        self.assertGreater(self.status()['skipped']['facts_unchanged']['a'], 0)
        self.assertEqual(self.status()['cadence']['occupancy_gate'], 'not_provisioned')

    def test_new_evidence_mode_keeps_the_old_rule(self):
        self.sim.runtime.reasoning_cadence = cadence_settings({'mode': 'new_evidence'})
        for _ in range(4):
            self.step(2.1, arms=('a',))
        self.assertEqual(len(self.requests()), 4)
        self.assertEqual(self.status()['basis'], 'new_admitted_camera_evidence')

    def test_key_change_closes_validity_and_findings_shows_both_metrics(self):
        sim, start = self.sim, self.sim.started
        sim.store.save_annotation(sim.sid, 'l1', {'at_ms': 0, 'end_ms': 60000, 'label': 'reading', 'origin': 'human',
                                                  'reviewed': True}, 0, 'owner')
        self.step(0)
        first = next(o for o in self.payloads('reasoning.output') if o['arm'] == 'a')
        self.assertEqual(first['facts_key'], 'k1')
        self.assertEqual(first['validity'], {'rule': 'facts_bound', 'hold_max_s': 12.0})
        arrived = first['prediction']['available_at']
        self.assertEqual(first['prediction']['valid_through'], arrived + 12.0)
        self.assertLess(first['prediction']['valid_until'], arrived + 2, 'the strict frame-TTL validity is unchanged')
        for _ in range(5):
            self.step()
        self.assertEqual(self.payloads('reasoning.validity'), [])
        self.provider.keys = {'a': 'k2', 'b': 'k2'}
        self.step()
        closed = {v['arm']: v for v in self.payloads('reasoning.validity')}
        self.assertEqual({v['reason'] for v in closed.values()}, {'facts_changed'})
        self.assertAlmostEqual(closed['a']['valid_through'], sim.now - start)
        self.assertEqual(closed['a']['request_id'], first['request_id'])
        findings = Controller(sim.store).findings(sim.sid)
        rows = {r['run_id']: r for r in findings['runs']}
        self.assertEqual(rows['jevA']['metric_kind'], 'raw')
        self.assertEqual(rows['jevA-smooth']['metric_kind'], 'smoothed')
        strict, valid = rows['jevA']['metrics'], rows['jevA']['metrics_valid_facts']
        self.assertGreater(valid['coverage'], strict['coverage'])
        self.assertEqual(valid['exact_match_at_coverage'], 1)
        summary = next(m for m in findings['jev_metrics'] if m['arm'] == 'a')
        self.assertEqual(summary['coverage'], strict['coverage'])
        self.assertEqual(summary['coverage_valid_facts'], valid['coverage'])
        self.assertEqual(summary['coverage_smoothed'], rows['jevA-smooth']['metrics']['coverage'])
        for name in ('exact_match_at_coverage', 'false_confident_seconds'):
            for suffix in ('', '_valid_facts', '_smoothed'):
                self.assertIn(name + suffix, summary)
        self.assertIn('reading', summary['per_activity_valid_facts'])

    def test_empty_room_answers_locally_and_requests_nothing(self):
        gate = self.gate()
        self.step(0)
        self.assertEqual(len(self.requests()), 1)
        gate.state = 'vacant'
        self.step()
        closed = self.payloads('reasoning.validity')
        self.assertEqual({v['reason'] for v in closed}, {'occupancy_vacant'}, 'a vacant reading ends validity at once')
        for _ in range(9):
            self.step()
        self.assertEqual(self.payloads('reasoning.gate'), [], 'still inside the occupancy hold')
        self.step(1.1)
        gates = self.payloads('reasoning.gate')
        self.assertEqual([(g['arm'], g['reason'], g['provider'], g['choice']) for g in gates],
                         [('a', 'occupancy_vacant', 'code_gate', 'insufficient_evidence'),
                          ('b', 'occupancy_vacant', 'code_gate', 'insufficient_evidence')])
        self.assertEqual(gates[0]['run_id'], 'jevA-gate')
        self.assertEqual(gates[0]['prediction']['activities'], [])
        for _ in range(12):
            self.step()
        self.assertEqual(len(self.requests()), 1, 'no request while the room reads vacant')
        self.assertEqual(len(self.payloads('reasoning.gate')), 4, 'a local abstention every 10 s per arm')
        self.assertEqual(self.status()['local_abstains'], {'a': 2, 'b': 2})
        self.assertGreaterEqual(self.status()['skipped']['no_person']['a'], 12)
        smoothed = [s for s in self.payloads('reasoning.smoothed') if s['arm'] == 'a']
        self.assertEqual(smoothed[-1]['source'], 'code_gate')
        self.assertEqual((smoothed[-1]['shown'], smoothed[-1]['prediction']['activities']), ('no_person', []))
        # Unknown occupancy gates the same way; the reason says why.
        gate.state = 'occupied'
        self.provider.keys = {'a': 'k3', 'b': 'k3'}
        self.step()
        self.assertEqual(len(self.requests()), 2, 'someone is back: the changed facts are offered')
        gate.state = 'unknown'
        for _ in range(12):
            self.step()
        self.assertEqual(self.payloads('reasoning.gate')[-1]['reason'], 'occupancy_unknown')
        self.assertEqual(len(self.requests()), 2)
        # Findings keeps the gate out of the scored runs and counts it.
        findings = Controller(self.sim.store).findings(self.sim.sid)
        self.assertNotIn('jevA-gate', {r['run_id'] for r in findings['runs']})
        self.assertEqual(next(m for m in findings['jev_metrics'] if m['arm'] == 'a')['local_abstains'],
                         {'occupancy_vacant': 2, 'occupancy_unknown': 1})

    def test_live_smoothing_equals_the_offline_timeline(self):
        activities = ['reading']*4 + ['phone_use']*6 + ['insufficient_evidence'] + ['phone_use']*3
        for index, activity in enumerate(activities):
            self.provider.activity = activity
            self.provider.keys = {'a': 'k%d' % index, 'b': 'k%d' % index}
            self.step(2.2 + (index % 3)*.4, arms=('a',))
        outputs = [o for o in self.payloads('reasoning.output') if o['arm'] == 'a']
        live = [s for s in self.payloads('reasoning.smoothed') if s['arm'] == 'a']
        self.assertEqual(len(outputs), len(activities))
        offline = smoothing.smooth(smoothing.answers_from_outputs(outputs))
        self.assertEqual([s['request_id'] for s in live], [o['request_id'] for o in outputs])
        self.assertEqual([s['shown'] for s in live], [p['shown'] for p in offline])
        self.assertEqual([s['prediction']['activities'] for s in live], [p['activities'] for p in offline])
        for step, expected in zip(live, offline):
            self.assertAlmostEqual(step['prediction']['start'], expected['start'])
            if expected['confidence'] is None:
                self.assertIsNone(step['prediction']['confidence'])
            else:
                self.assertAlmostEqual(step['prediction']['confidence'], expected['confidence'])
            self.assertEqual(step['run_id'], 'jevA-smooth')
        self.assertIn('phone_use', [s['shown'] for s in live])

    def test_timeline_payload_carries_the_smoothed_strip_and_facts_validity(self):
        import graph as lab_graph
        self.step(0)
        for _ in range(3):
            self.step()
        self.provider.keys = {'a': 'k2', 'b': 'k2'}
        self.step()
        tracks = lab_graph.timeline(self.sim.store, self.sim.sid)['tracks']
        strip = tracks['jev_smoothed']['a']
        self.assertEqual(strip['run_id'], 'jevA-smooth')
        self.assertIn('reading', [s['shown'] for s in strip['items']])
        self.assertEqual(strip['items'][0]['until_ms'], strip['items'][1]['t_ms'], 'a step holds until the next one')
        self.assertEqual(strip['items'][-1]['until_ms'], strip['items'][-1]['t_ms'] + 12000, 'the last step: its own hold')
        first = tracks['jev']['a']['results']['items'][0]
        self.assertGreater(first['valid_through_ms'], first['valid_until_ms'])
        closed = self.payloads('reasoning.validity')
        self.assertIn(first['valid_through_ms'], {round(c['valid_through']*1000) for c in closed} | {tracks['jev']['a']['results']['items'][1]['t_ms']})

    def test_local_records_never_reach_a_request(self):
        gate = self.gate()
        self.step(0)
        gate.state = 'vacant'
        for _ in range(12):
            self.step()
        gate.state = 'occupied'
        self.provider.keys = {'a': 'k9', 'b': 'k9'}
        self.step()
        with self.sim.store.lock:
            payloads = [r[0] for r in self.sim.store.db.execute('SELECT payload FROM lab_reasoning_requests')]
        self.assertEqual(len(payloads), 4)
        for payload in payloads:
            kinds = {r.get('source_kind') for r in json.loads(payload)['records']}
            self.assertFalse(kinds & {'reasoning.smoothed', 'reasoning.gate', 'reasoning.validity'})
            self.assertNotIn('facts_key', json.loads(payload), 'the facts key is kept locally, not in the request')


class ProviderFactsKeyTests(unittest.TestCase):
    def test_v6_provider_key_matches_the_packet_it_would_send(self):
        import test_jev_policy_v6 as v6
        import jev_wire as wire
        from jev_provider import JevProvider
        from live_inputs import live_records
        from reasoning import causal_evidence
        case = v6.ProviderV6Tests('test_unregistered_wire_refused_at_construction')
        case.setUp()
        self.addCleanup(case.tearDown)
        sent = []

        def transport(body, *_):
            sent.append(json.loads(body))
            return v6.respond(body)
        provider = case.provider(transport, wire=wire.WIRE_VERSION)
        rows = live_records(case.store, case.store.get_session(case.sid), 1, 100.0, window_seconds=10, retain_history=True)
        records = causal_evidence(rows, site_id='victoria', session_id=case.sid, arm='a', generation=1, cutoff=100.0,
                                  window_seconds=10, retain_history=True)
        shared = v6.qwen(cutoff=100.0, session=case.sid)
        keyed = provider.facts_key(records, site_id='victoria', session_id=case.sid, arm='a', generation=1, cutoff=100.0,
                                   window_seconds=10, shared_context=shared)
        result = case.evaluate(provider)
        self.assertEqual(keyed['key'], result['diagnostics']['facts_key'])
        self.assertEqual(keyed['key'], wire.facts_key(sent[0]['state']))
        self.assertEqual(keyed['people'], sent[0]['state']['now']['people_in_view'])
        v5 = case.provider(transport)
        self.assertIsNone(v5.facts_key(records, site_id='victoria', session_id=case.sid, arm='a', generation=1, cutoff=100.0))


class ReplayRuleTests(unittest.TestCase):
    """The offline check runs the live rules themselves."""

    def test_simulated_scheduler_counts_match_live_rule(self):
        from jev_cadence_replay import simulate_cadence, validity_closers
        ticks = [(float(t), 'k1' if t < 20 else 'k2', 'one', True, 'occupied' if t < 30 else 'vacant') for t in range(0, 50)]
        offers, gates, counts = simulate_cadence(ticks, settings=SETTINGS, interval=2.0, gated_by_occupancy=True, hold=10.0)
        self.assertEqual([(t, r) for t, _, r in offers], [(0.0, 'facts_changed'), (10.0, 'keepalive'), (20.0, 'facts_changed')])
        self.assertEqual(gates, [(40.0, True)])
        self.assertEqual((counts['skip_facts_unchanged'], counts['skip_no_person']), (10, 10))
        self.assertEqual((counts['calls'], counts['local_abstain']), (3, 1))
        closers = validity_closers([(t, k, o == 'vacant') for t, k, _, _, o in ticks],
                                   [(0.5, 'r1', 'k1'), (10.5, 'r2', 'k1'), (20.5, 'r3', 'k2')], gates, 12.0)
        self.assertEqual(closers, {'r1': 10.5, 'r2': 20.0, 'r3': 30.0})


if __name__ == '__main__':
    unittest.main()
