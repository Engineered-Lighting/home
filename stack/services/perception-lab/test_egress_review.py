"""Review fixes for the cloud packet: opaque provenance, per-fact household
grants that drop (never refuse), timezone coverage, HA coverage honesty and
same-tick digest comparison. Temp stores and fake transports only."""
from copy import deepcopy
from datetime import timedelta, timezone
import json
import tempfile
import unittest
from unittest.mock import patch

from ambient_context import shared_context
from evidence_policy import source_policy
from jev_evidence import (AMBIENT_EGRESS, DEVICE_EGRESS, PRESENCE_EGRESS, OPAQUE_TOKEN, opaque, project,
                          project_context)
from reasoning import build_jev_request
import test_paired_runtime as fixtures

TZ = timezone(timedelta(hours=-7))
OCCUPANCY = 'binary_sensor.den_oak_person_occupancy'
BINDINGS = {'sun.sun': 'daylight', 'weather.forecast_home': 'weather_condition', OCCUPANCY: 'person_present',
            'person.owner': 'owner_home', 'light.den_rear_left': 'den_light', 'media_player.den_den': 'den_audio_playback'}
GRANTS = source_policy(None, ha={'a': True, 'b': True})


def ha_record(rid, observed, states, *, source='la-marcelo-presence', arm='a'):
    return {'id': rid, 'site_id': 'victoria', 'camera_id': 'den', 'session_id': 's', 'arm': arm, 'generation': 1,
            'source_kind': 'ha.observation', 'observed_at': observed, 'available_at': observed+.1, 'valid_until': observed+3,
            'ha': {e: {'state': v, 'status': 'known'} for e, v in states.items()},
            'evidence_roots': [f'ha:{source}:{rid}'], 'quality': {'source_id': source}}


class OpaqueProvenanceTests(unittest.TestCase):
    def test_roots_and_frame_identifiers_leave_only_as_stable_digests(self):
        visual = {'id': '7', 'site_id': 'victoria', 'camera_id': 'den', 'session_id': 's', 'arm': 'a', 'generation': 1,
                  'frame_id': 'marcelo-desk-cam-0-12', 'capture_epoch': 'marcelo-desk-cam', 'observed_at': 99, 'available_at': 99.2,
                  'valid_until': 101, 'evidence_roots': ['7', 'media:abc'], 'objects': [{'label': 'book'}]}
        ha = ha_record('9', 99, {OCCUPANCY: 'on'})
        projected = project([visual, ha], ha_bindings=BINDINGS)
        text = json.dumps(projected)
        for private in ('la-marcelo-presence', 'marcelo-desk', 'media:abc'):
            self.assertNotIn(private, text)
        self.assertEqual(projected[0]['frame_id'], opaque('frame', 'marcelo-desk-cam-0-12'))
        self.assertEqual(projected[0]['capture_epoch'], opaque('epoch', 'marcelo-desk-cam'))
        self.assertEqual(projected[1]['evidence_roots'], [opaque('root', 'ha:la-marcelo-presence:9')])
        self.assertTrue(all(OPAQUE_TOKEN.fullmatch(r) for p in projected for r in p['evidence_roots']))
        # Stable and idempotent: the policy's re-projection must reproduce it.
        self.assertEqual(project(projected, ha_bindings=BINDINGS), projected)
        # Shared roots stay equal, so correlation can still be detected.
        again = project([dict(ha, id='10')], ha_bindings=BINDINGS)
        self.assertEqual(again[0]['evidence_roots'], [opaque('root', 'ha:la-marcelo-presence:9')])

    def test_an_ha_record_without_a_bound_fact_is_dropped_not_sent_empty(self):
        ha = ha_record('9', 99, {'sensor.unbound': 'x', 'sun.sun': 'above_horizon'})
        self.assertEqual(project([ha], ha_bindings=BINDINGS), [])
        # Removing a fact's binding (its grant is absent) leaves no shell either.
        only_light = ha_record('9', 99, {'light.den_rear_left': 'on'})
        self.assertEqual(project([only_light], ha_bindings={k: v for k, v in BINDINGS.items() if v != 'den_light'}), [])
        self.assertEqual(project([only_light], ha_bindings=BINDINGS)[0]['ha'], {'den_light': 'on'})


class PolicyGateTests(unittest.TestCase):
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def gate(self, rows, *, bindings=None, **grants):
        from jev_policy import LabJevPolicy
        self.cutoff = 1_790_000_000.0
        self.store.clock = lambda: self.cutoff+1
        self.policy = {'schema_version': 1, 'enabled': True, 'scope': 'household_structured', 'revision': 'lab1', 'generation': 1,
                       'expires_at': self.cutoff+100, 'site_id': 'victoria', 'camera_ids': ['den'], 'model_selector': 'jev-1.13',
                       'max_calls_per_minute': 20, **grants}
        gate = LabJevPolicy(self.store, lambda: self.policy, lambda: {'state': 'on', 'received_at': self.cutoff+1},
                            ha_bindings=bindings or {})
        self.store.db.execute('CREATE TABLE IF NOT EXISTS lab_reasoning_requests(id TEXT PRIMARY KEY,session_id TEXT,state TEXT)')
        self.store.db.execute('INSERT OR REPLACE INTO lab_reasoning_requests VALUES(?,?,?)', ('r1', self.session['id'], 'dispatching'))
        self.context = {'request_id': 'r1', 'site_id': 'victoria', 'session_id': self.session['id'], 'arm': 'a', 'generation': 1}
        base = {**self.context, 'camera_id': 'den', 'observed_at': self.cutoff-.5, 'available_at': self.cutoff-.2, 'valid_until': self.cutoff+2}
        records = [{**base, **row} for row in rows]
        self.packet, _ = build_jev_request(project(records, ha_bindings=bindings or {}), model='jev-1.13', site_id='victoria',
            session_id=self.session['id'], arm='a', generation=1, cutoff=self.cutoff, retain_history=True, semantic=True)
        return gate

    def test_non_digest_roots_and_frame_ids_are_refused(self):
        gate = self.gate([{'id': 'e1', 'objects': [{'label': 'book'}], 'evidence_roots': ['ha:la-marcelo-presence:1'],
                           'frame_id': 'cam-0-1'}])
        tampered = deepcopy(self.packet)
        tampered['state']['evidence'][0]['evidence_roots'] = ['ha:la-marcelo-presence:1']
        for change in (lambda p: p['state']['evidence'][0].update(evidence_roots=['ha:la-marcelo-presence:1']),
                       lambda p: p['state']['evidence'][0].update(frame_id='cam-0-1'),
                       lambda p: p['state']['evidence'][0].update(capture_epoch='marcelo-desk'),
                       lambda p: p['state']['evidence'][0].update(evidence_roots=['frame-'+'0'*24]),
                       lambda p: p['state']['evidence'][0].update(id='a free text id')):
            broken = deepcopy(self.packet)
            change(broken)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'Structured source permission mismatch'):
                gate(self.context, broken, 'send')
        self.assertTrue(gate(self.context, self.packet, 'send')['allowed'])

    def test_device_facts_need_the_device_grant_and_legacy_facts_need_none(self):
        rows = [{'id': 'e1', 'objects': [{'label': 'book'}]},
                {'id': 'h1', 'source_kind': 'ha.observation', 'ha': {OCCUPANCY: {'state': 'on'}, 'light.den_rear_left': {'state': 'on'}}}]
        with self.assertRaisesRegex(ValueError, 'Device context egress not permitted'):
            self.gate(rows, bindings=BINDINGS)(self.context, self.packet, 'send')
        for grant in ('den-devices-2', True):
            with self.subTest(grant=grant), self.assertRaises(ValueError):
                self.gate(rows, bindings=BINDINGS, device_context=grant)(self.context, self.packet, 'send')
        self.assertTrue(self.gate(rows, bindings=BINDINGS, device_context=DEVICE_EGRESS)(self.context, self.packet, 'send')['allowed'])
        # person_present alone (pre-existing) needs no new grant.
        legacy = [rows[0], {**rows[1], 'ha': {OCCUPANCY: {'state': 'on'}}}]
        self.context['request_id'] = 'r1'
        self.store.db.execute('DELETE FROM lab_jev_egress')
        self.assertTrue(self.gate(legacy, bindings=BINDINGS)(self.context, self.packet, 'send')['allowed'])


class ContextCoverageTests(unittest.TestCase):
    cutoff = 100.0

    def blocks(self, records, **kw):
        args = dict(grants=GRANTS, tz=TZ, provisioned=True, bindings=BINDINGS, ha_provisioned=True)
        args.update(kw)
        return shared_context(records, self.cutoff, **args)

    def test_ungranted_facts_are_marked_and_ha_is_supplied_only_with_a_carried_bound_fact(self):
        rows = [ha_record(1, 99, {OCCUPANCY: 'on', 'person.owner': 'home', 'light.den_rear_left': 'on'})]
        block = self.blocks(rows)['a']
        self.assertEqual(block['ha_facts'], {'den_audio_playback': 'not_granted', 'den_light': 'not_granted',
                                             'owner_home': 'not_granted', 'person_present': 'supplied'})
        self.assertEqual(block['coverage']['ha'], 'supplied')
        granted = self.blocks(rows, fact_grants={'owner_home': True, 'den_light': True, 'den_audio_playback': True})['a']
        self.assertEqual(granted['ha_facts'], {'den_audio_playback': 'stale', 'den_light': 'supplied',
                                               'owner_home': 'supplied', 'person_present': 'supplied'})
        self.assertNotEqual(block['shared_context_digest'], granted['shared_context_digest'])
        # Rows carrying only unbound or ambient entities never make HA 'supplied'.
        ambient_only = [ha_record(1, 99, {'sun.sun': 'above_horizon', 'sensor.unbound': 'x'})]
        self.assertEqual(self.blocks(ambient_only)['a']['coverage']['ha'], 'stale')
        # Every bound device fact ungranted: not_granted, not stale.
        only_gated = {'person.owner': 'owner_home'}
        self.assertEqual(self.blocks(rows, bindings=only_gated)['a']['coverage']['ha'], 'not_granted')
        # A withheld arm reports every fact as withheld.
        grants = deepcopy(GRANTS)
        grants['ha'] = {'a': 'withheld_by_experiment', 'b': 'granted'}
        withheld = self.blocks(rows, grants=grants, lever=['ha'])
        self.assertEqual(set(withheld['a']['ha_facts'].values()), {'withheld_by_experiment'})
        self.assertEqual(withheld['a']['shared_context_digest'], withheld['b']['shared_context_digest'])
        for value in (block, granted, withheld['a']):
            self.assertEqual(project_context(value), value)

    def test_a_zone_that_cannot_load_is_timezone_unavailable_not_silent_unknown(self):
        rows = [ha_record(1, 99, {'sun.sun': 'below_horizon', 'weather.forecast_home': 'rainy'})]
        block = self.blocks(rows, tz=None)['a']
        self.assertEqual(block['coverage']['ambient'], 'timezone_unavailable')
        self.assertEqual((block['ambient']['part_of_day'], block['ambient']['local_hour']), ('unknown', 'unknown'))
        self.assertEqual((block['ambient']['daylight'], block['ambient']['weather']), ('dark', 'rainy'))
        self.assertEqual(project_context(block), block)
        # A 'timezone_unavailable' block cannot carry clock labels.
        forged = deepcopy(block)
        forged['ambient']['part_of_day'] = 'evening'
        with self.assertRaises(ValueError):
            project_context(forged)
        # Configured zone, no household grant: explicit not_granted.
        self.assertEqual(self.blocks(rows, provisioned=False, ambient_state='not_granted')['a']['coverage']['ambient'], 'not_granted')


class RuntimeGrantTests(unittest.TestCase):
    """owner_home / device facts bound without their grant: dropped per tick,
    marked in coverage, both arms still dispatch."""

    def setUp(self):
        from test_reasoning_load import Simulation
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.sim = Simulation(self.directory.name, filler_files=0)
        self.sim.preview()
        self.grants = {}
        self.sim.runtime.reasoning_factory = self.factory()

    def tearDown(self):
        self.sim.close()
        self.directory.cleanup()

    def factory(self):
        from jev_evidence import FACT_GRANTS, fact_granted
        from jev_policy import LabJevPolicy
        from jev_provider import JevProvider
        from live_reasoning import LiveReasoning
        from test_reasoning_load import SyncPool, fake_transport
        sim, test = self.sim, self

        def policy():
            return {'schema_version': 1, 'enabled': True, 'scope': 'household_structured', 'revision': 'grants', 'generation': 1,
                    'expires_at': sim.now+10_000, 'site_id': 'victoria', 'camera_ids': ['den'], 'model_selector': 'jev-1.13',
                    'max_calls_per_minute': 120, 'ambient_context': AMBIENT_EGRESS, **test.grants}

        def create(store, sid):
            provider = JevProvider(model_selector='jev-1.13', response_version='jev-1.13.0', allow_recent_history=True, semantic=True,
                ha_bindings=BINDINGS, authorize=LabJevPolicy(store, policy, lambda: {'state': 'on', 'received_at': store.clock()},
                ha_bindings=BINDINGS, site_timezone=TZ), credential=lambda: 'fixture', transport=fake_transport, clock=store.clock)
            reasoner = LiveReasoning(store, sid, provider)
            reasoner.pool.shutdown(wait=False)
            reasoner.pool = SyncPool()
            return reasoner
        create.site_timezone, create.ha_bindings = TZ, BINDINGS
        create.ambient_provisioned = lambda: True
        create.fact_grants = lambda: {f: fact_granted(f, policy()) for f in FACT_GRANTS if f in BINDINGS.values()}
        return create

    def offered(self):
        sim = self.sim
        sim.output('a')
        sim.output('b')
        sim.store.append_event(sim.sid, 'ha.observation', {'site_id': 'victoria', 'camera_id': 'den', 'observed_at': sim.now-.2,
            'available_at': sim.now-.1, 'valid_until': sim.now+3, 'quality': {'source_id': 'la-marcelo-presence'},
            'evidence_roots': ['ha:la-marcelo-presence:snap1'],
            'ha': {'person.owner': {'state': 'not_home'}, OCCUPANCY: {'state': 'on'}, 'light.den_rear_left': {'state': 'on'},
                   'sun.sun': {'state': 'above_horizon'}}})
        sim.tick()
        with sim.store.lock:
            rows = sim.store.db.execute('SELECT arm,state FROM lab_reasoning_requests ORDER BY rowid').fetchall()
            sent = [json.loads(r[0]) for r in sim.store.db.execute("SELECT payload FROM events WHERE kind='reasoning.request' ORDER BY id")]
        return dict(rows), [s['request'] for s in sent]

    def test_without_grants_the_gated_facts_are_dropped_and_both_arms_dispatch(self):
        states, packets = self.offered()
        self.assertEqual(states, {'a': 'completed', 'b': 'completed'})
        self.assertEqual(len(packets), 2)
        for packet in packets:
            ha = [r['ha'] for r in packet['state']['evidence'] if r['source_kind'] == 'ha.observation']
            self.assertEqual(ha, [{'person_present': 'on'}])
            context = packet['state']['semantic']['context']
            self.assertEqual(context['ha_facts']['owner_home'], 'not_granted')
            self.assertEqual(context['ha_facts']['den_light'], 'not_granted')
            self.assertEqual(context['coverage']['ha'], 'supplied')
            self.assertNotIn('la-marcelo-presence', json.dumps(packet))
        self.assertEqual(packets[0]['state']['semantic']['context'], packets[1]['state']['semantic']['context'])

    def test_with_grants_the_facts_are_carried(self):
        self.grants = {'owner_presence': PRESENCE_EGRESS, 'device_context': DEVICE_EGRESS}
        states, packets = self.offered()
        self.assertEqual(states, {'a': 'completed', 'b': 'completed'})
        for packet in packets:
            ha = [r['ha'] for r in packet['state']['evidence'] if r['source_kind'] == 'ha.observation']
            self.assertEqual(ha, [{'owner_home': 'away', 'person_present': 'on', 'den_light': 'on'}])
            self.assertEqual(packet['state']['semantic']['context']['ha_facts']['owner_home'], 'supplied')


class ReadinessTests(unittest.TestCase):
    def test_readiness_reports_zone_loading_and_every_grant(self):
        from managed_jev import reasoning_factory
        config = {'schema_version': 1, 'policy_file': 'policy', 'credential_file': 'key', 'response_version': 'jev-1.13.0',
                  'site_timezone': 'Not/A_Zone', 'ha_bindings': {'person.owner': 'owner_home', 'light.den_rear_left': 'den_light'}}
        policy = {'schema_version': 1, 'enabled': True, 'scope': 'household_structured', 'revision': 'r', 'generation': 1,
                  'expires_at': 4_000_000_000, 'site_id': 'victoria', 'camera_ids': ['den'], 'model_selector': 'jev-1.13.0',
                  'max_calls_per_minute': 20}
        cases = ((policy, {'ambient_context': 'not_granted', 'owner_presence': 'not_granted', 'device_context': 'not_granted',
                           'qwen_scene': 'not_granted', 'zone_loaded': False}),
                 ({**policy, 'ambient_context': AMBIENT_EGRESS, 'owner_presence': PRESENCE_EGRESS, 'device_context': DEVICE_EGRESS,
                   'structured_sources': ['lab-live-scene-1']},
                  {'ambient_context': 'granted', 'owner_presence': 'granted', 'device_context': 'granted', 'qwen_scene': 'granted',
                   'zone_loaded': False}))
        for value, expected in cases:
            with patch('managed_jev.private_json', side_effect=lambda p: dict(config if p == 'config' else value)):
                factory = reasoning_factory('config')
                ready = factory.readiness()
                self.assertTrue(ready['available'])
                self.assertEqual({k: ready[k] for k in expected}, expected)
                self.assertEqual(factory.fact_grants(), {'owner_home': expected['owner_presence'] == 'granted',
                                                         'den_light': expected['device_context'] == 'granted'})
                self.assertEqual(factory.qwen_granted(), expected['qwen_scene'] == 'granted')
        # A zone that loads reports true (injected loader; the dev host may lack tzdata).
        with patch('managed_jev.private_json', side_effect=lambda p: dict(config if p == 'config' else policy)), \
                patch('ambient_context.zone', side_effect=lambda name: TZ):
            self.assertTrue(reasoning_factory('config').readiness()['zone_loaded'])


class TickPairTests(unittest.TestCase):
    """graph digests_equal compares one tick's two requests, not each arm's latest."""

    def test_digests_are_compared_only_within_one_tick(self):
        import graph as lab_graph
        from test_reasoning_load import Simulation
        directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        sim = Simulation(directory.name, filler_files=0)
        try:
            def request(arm, cutoff, digest):
                context = {'schema': 'shared-context-2', 'shared_context_digest': digest}
                sim.store.append_event(sim.sid, 'reasoning.request', {'request_id': arm+str(cutoff), 'arm': arm, 'generation': 1,
                    'available_at': sim.now, 'request': {'state': {'observation_cutoff': cutoff, 'semantic': {'context': context}}}})
            request('a', sim.now-2, 'x')
            request('b', sim.now-2, 'x')
            request('a', sim.now-1, 'y')  # only A was offered in the newest tick
            node = next(s for s in lab_graph.graph(sim.store, sim.sid)['sources'] if s['id'] == 'ambient_context')
            self.assertEqual((node['version']['digests_equal'], node['version']['digest_basis']), (True, 'same_tick'))
            self.assertEqual(node['version']['compared_cutoff'], sim.now-2)
            request('b', sim.now-1, 'z')
            node = next(s for s in lab_graph.graph(sim.store, sim.sid)['sources'] if s['id'] == 'ambient_context')
            self.assertEqual((node['version']['digests_equal'], node['version']['compared_cutoff']), (False, sim.now-1))
            request('a', sim.now-.5, 'w')
            request('b', sim.now-.4, 'w')  # different ticks, never compared
            node = next(s for s in lab_graph.graph(sim.store, sim.sid)['sources'] if s['id'] == 'ambient_context')
            self.assertEqual((node['version']['digests_equal'], node['version']['digest_basis']), (None, 'no_same_tick_pair_in_scan'))
        finally:
            sim.close()
            directory.cleanup()


if __name__ == '__main__':
    unittest.main()
