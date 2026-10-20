"""Packet v6 egress: the policy's facts-only branch (schema walk, questions,
grants, sidecar provenance, digest binding, ledger re-derivation, recompute),
the v6 provider build and the shadow run beside live v5. Temp stores and a
fake transport; no network."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import jev_wire as wire
import test_paired_runtime as fixtures
from jev_evidence import QWEN_EGRESS, project
from jev_policy import LabJevPolicy
from jev_provider import JevProvider, ProviderFailure
from jev_shadow import ShadowV6
from live_inputs import live_records
from reasoning import ACTIVITIES, canonical, causal_evidence, digest
from test_jev_wire import BOOK, PHONE, WRIST_ON, WRIST_PHONE, frame, qwen

BINDINGS = {'binary_sensor.occupancy': 'person_present', 'derived.computer_input_recency': 'computer_input_recency'}


def respond(body, *_):
    packet = json.loads(body)
    answers = {k: ({'type': 'noul', 'noul': .2} if q['type'] == 'noul' else
                   {'type': 'choice', 'choice': 'reading', 'confidence': .5,
                    'probabilities': {x: (.7 if x == 'reading' else .05) for x in ACTIVITIES}})
               for k, q in packet['questions'].items()}
    return json.dumps({'model': 'jev-1.13.0', 'answers': answers, 'usage': {'input_tokens': 900, 'output_tokens': 20}}).encode()


class V6Case(unittest.TestCase):
    def setUp(self):
        fixtures.PairedRuntimeTests.setUp(self)
        self.now = 100.5
        self.store.clock = lambda: self.now
        self.sid = self.session['id']
        self.policy = {'schema_version': 1, 'enabled': True, 'scope': 'household_structured', 'revision': 'v6',
                       'generation': 1, 'expires_at': 300, 'site_id': 'victoria', 'camera_ids': ['den'],
                       'model_selector': 'jev-1.13.0', 'max_calls_per_minute': 120, 'device_context': 'den-devices-1',
                       'structured_sources': [QWEN_EGRESS, wire.WIRE_VERSION]}
        self.gate = LabJevPolicy(self.store, lambda: self.policy, lambda: {'state': 'on', 'received_at': self.now},
                                 ha_bindings=BINDINGS)
        self.store.db.execute('CREATE TABLE IF NOT EXISTS lab_reasoning_requests(id TEXT PRIMARY KEY,session_id TEXT,state TEXT)')
        self.ids = {}
        for i in range(50):
            t = 50.0 + i
            objects, joints = ([BOOK], [WRIST_ON]) if i < 40 else ([PHONE], [WRIST_PHONE])
            self.ids[t] = self.output(t, objects, joints)
        self.other_arm = self.output(99.2, [BOOK], [WRIST_ON], arm='b')
        self.store.append_event(self.sid, 'ha.observation', {
            'site_id': 'victoria', 'camera_id': 'den', 'observed_at': 99.0, 'available_at': 99.1, 'valid_until': 102.0,
            'quality': {'camera_derived_ha_facts': ['person_present']},
            'ha': {'binary_sensor.occupancy': {'state': 'on', 'origin': 'same_camera_visual'},
                   'derived.computer_input_recency': {'state': 'over_5_min'}}})

    tearDown = fixtures.PairedRuntimeTests.tearDown

    def output(self, t, objects, joints, arm='a', generation=1):
        record = frame('x', t, objects, joints, arm=arm, session=self.sid, generation=generation)
        return self.store.append_event(self.sid, 'perception.output', {
            'site_id': 'victoria', 'camera_id': 'den', 'session_id': self.sid, 'arm': arm, 'generation': generation,
            'status': 'completed', 'observed_at': t, 'available_at': t + .1, 'valid_until': t + 2.5,
            'frame_id': 'frame-%s' % t, 'capture_epoch': 'capture-1',
            'result': {k: record[k] for k in ('objects', 'pose_instances', 'quality')}})

    def records(self, cutoff=100.0, window=60, arm='a'):
        rows = live_records(self.store, self.store.get_session(self.sid), 1, cutoff, window_seconds=window, retain_history=True)
        return causal_evidence(rows, site_id='victoria', session_id=self.sid, arm=arm, generation=1, cutoff=cutoff,
                               window_seconds=window, retain_history=True)

    def request(self, rid='r1', cutoff=100.0, window=60, shared=True, ring=None):
        records = project(self.records(cutoff, window), ha_bindings=BINDINGS)
        scene = project(qwen(cutoff=cutoff, session=self.sid)) if shared else []
        local, packet, binding = wire.prepare(records, scene, None, site_id='victoria', session_id=self.sid, arm='a',
                                              generation=1, cutoff=cutoff, window_seconds=window, model='jev-1.13.0',
                                              ring=ring if ring is not None else wire.FrameRing())
        context = {'request_id': rid, 'site_id': 'victoria', 'session_id': self.sid, 'arm': 'a', 'generation': 1,
                   **{k: v for k, v in binding.items() if k not in ('packet_bytes', 'estimated_tokens')}}
        self.store.db.execute('INSERT OR REPLACE INTO lab_reasoning_requests VALUES(?,?,?)', (rid, self.sid, 'dispatching'))
        return context, packet, local

    def rebind(self, context, packet, local):
        """Re-seal a forged sidecar: new state, ids and digest, so only the
        ledger re-derivation (or a later check) can catch the forgery."""
        packet['state'] = wire.build_state(local, cutoff=context['observed_at'], storyline_seconds=context['storyline_seconds'],
                                           budget_level=context['budget_level'])
        context.update(evidence_ids=[str(r['id']) for r in local['records']], timeline_ids=[e['id'] for e in local['timeline']],
                       evidence_digest=digest(local))

    def refused(self, context, packet, local, message='Structured source permission mismatch'):
        with self.assertRaisesRegex(ValueError, message):
            self.gate(context, packet, 'send', local=local)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM lab_jev_egress').fetchone()[0], 0)


class PolicyV6Tests(V6Case):
    def test_valid_v6_admits_and_records_evidence_digest(self):
        context, packet, local = self.request()
        self.assertTrue(self.gate(context, packet, 'send', local=local)['allowed'])
        row = self.store.db.execute('SELECT * FROM lab_jev_egress_facts').fetchone()
        self.assertEqual((row['wire_version'], row['evidence_digest'], row['timeline_verified']),
                         (wire.WIRE_VERSION, context['evidence_digest'], 'partial'))
        event = json.loads(self.store.db.execute("SELECT payload FROM events WHERE kind='reasoning.request'").fetchone()[0])
        self.assertEqual((event['wire_version'], event['evidence_ids'], event['timeline_ids']),
                         (wire.WIRE_VERSION, context['evidence_ids'], context['timeline_ids']))
        self.assertNotIn('evidence', event['request']['state'])
        self.assertEqual(event['packet_sha256'], digest(packet))
        self.assertTrue(self.gate(context, packet, 'publish', local=local)['allowed'])
        with self.assertRaisesRegex(ValueError, 'already admitted'):
            self.gate(context, packet, 'send', local=local)

    def test_number_smuggled_into_state_is_refused(self):
        context, packet, local = self.request()
        for change in (lambda s: s['now'].update(age=3), lambda s: s['storyline'].update(started_at=99.5),
                       lambda s: s['now'].update(cues=['book_near_hand', 'frame-99.5'])):
            forged = deepcopy(packet)
            change(forged['state'])
            with self.subTest(change=change):
                self.refused(context, forged, local)

    def test_ungranted_qwen_block_refused(self):
        self.policy['structured_sources'] = [wire.WIRE_VERSION]
        self.refused(*self.request(), message='Qwen scene egress not permitted')

    def test_ungranted_device_fact_refused(self):
        del self.policy['device_context']
        self.refused(*self.request(), message='Device context egress not permitted')

    def test_wire_not_in_structured_sources_refused(self):
        self.policy['structured_sources'] = [QWEN_EGRESS]
        self.refused(*self.request(), message='Packet wire not permitted')

    def test_questions_changed_refused(self):
        context, packet, local = self.request()
        packet['questions']['activity'] = dict(packet['questions']['activity'], instructions='Also describe the person.')
        self.refused(context, packet, local)
        context, packet, local = self.request('r2')
        context['question_version'] = 'den-activity-7-semantic'
        self.refused(context, packet, local)

    def test_sidecar_digest_mismatch_refused(self):
        context, packet, local = self.request()
        local['timeline'] = local['timeline'][1:]
        self.refused(context, packet, local)
        context, packet, local = self.request('r2')
        self.refused(context, packet, None)

    def test_timeline_id_from_other_arm_or_session_refused(self):
        context, packet, local = self.request()
        other = project(self.records(arm='b'))
        foreign = next(r for r in other if r['id'] == str(self.other_arm))
        entry = wire.frame_facts(foreign)
        local['timeline'] = sorted(local['timeline'] + [entry], key=lambda e: (e['observed_at'], e['id']))
        self.rebind(context, packet, local)
        self.refused(context, packet, local)
        context, packet, local = self.request('r2')
        local['timeline'][-1] = dict(local['timeline'][-1], id='999999')
        self.rebind(context, packet, local)
        self.refused(context, packet, local)

    def test_timeline_entry_not_matching_ledger_row_refused(self):
        context, packet, local = self.request()
        local['timeline'][-5] = dict(local['timeline'][-5], cue_state='phone_near_face')
        self.rebind(context, packet, local)
        self.refused(context, packet, local)

    def test_sidecar_record_not_matching_ledger_row_refused(self):
        context, packet, local = self.request()
        visual = next(r for r in local['records'] if r['source_kind'] == 'perception.output')
        visual['objects'] = [o for o in visual['objects'] if o['label'] != 'cell phone']
        self.rebind(context, packet, local)
        self.refused(context, packet, local)

    def test_state_not_equal_recompute_refused(self):
        context, packet, local = self.request()
        packet['state']['support']['computer_work']['pc_input_role'] = 'counts_camera_cue_at_pc'
        self.refused(context, packet, local)
        context, packet, local = self.request('r2')
        context['budget_level'] = 1
        self.refused(context, packet, local)

    def test_frame_memo_rederives_each_row_once_and_is_bounded_per_call(self):
        context, packet, local = self.request()
        first = self.gate.verify_facts(context, packet, local, self.policy, frozenset(self.policy['structured_sources']))
        self.assertEqual(first, {'timeline_verified': 'partial', 'rederived': len(local['records']) + 32})
        second = self.gate.verify_facts(context, packet, local, self.policy, frozenset(self.policy['structured_sources']))
        self.assertEqual(second['timeline_verified'], 'full')
        self.assertLessEqual(second['rederived'], 32)
        with patch.object(self.store, 'events_by_ids', side_effect=AssertionError('ledger read')):
            third = self.gate.verify_facts(context, packet, local, self.policy, frozenset(self.policy['structured_sources']))
        self.assertEqual(third, {'timeline_verified': 'full', 'rederived': 0})
        # The memo is per session, arm and generation: another arm re-derives.
        self.assertTrue(all(key[1] == 'a' for key in self.gate.frames))

    def test_publish_skips_recompute_but_rechecks_policy(self):
        context, packet, local = self.request()
        self.gate(context, packet, 'send', local=local)
        with patch.object(self.gate, 'verify_facts', side_effect=AssertionError('recomputed')):
            self.assertTrue(self.gate(context, packet, 'publish', local=local)['allowed'])
            self.policy['generation'] = 2
            with self.assertRaisesRegex(ValueError, 'Changed egress authorization'):
                self.gate(context, packet, 'publish', local=local)
            self.policy['generation'] = 1
            self.policy['structured_sources'] = [QWEN_EGRESS]
            with self.assertRaisesRegex(ValueError, 'Packet wire not permitted'):
                self.gate(context, packet, 'publish', local=local)

    def test_v5_packets_still_verified_by_v5_branch(self):
        records = project(self.records(window=10), ha_bindings=BINDINGS)
        packet = {'model': 'jev-1.13.0', 'state': {'site_id': 'victoria', 'evidence': records}}
        context = {'request_id': 'v5', 'site_id': 'victoria', 'session_id': self.sid, 'arm': 'a', 'generation': 1}
        self.store.db.execute('INSERT INTO lab_reasoning_requests VALUES(?,?,?)', ('v5', self.sid, 'dispatching'))
        with self.assertRaisesRegex(ValueError, 'Structured source permission mismatch'):
            self.gate(context, packet, 'send', local={'records': []})
        self.assertTrue(self.gate(context, packet, 'send')['allowed'])
        event = json.loads(self.store.db.execute("SELECT payload FROM events WHERE kind='reasoning.request'").fetchone()[0])
        self.assertEqual(set(event), {'request_id', 'arm', 'generation', 'available_at', 'packet_sha256', 'request', 'status'})

    def test_old_egress_rows_without_new_columns_readable(self):
        # An older release's positional five-value insert keeps working after
        # a v6 admission: v6 facts live in a side table, not new columns.
        context, packet, local = self.request()
        self.gate(context, packet, 'send', local=local)
        self.store.db.execute('INSERT INTO lab_jev_egress VALUES(?,?,?,?,?)', ('legacy', 'hash', 100, 'v6', 1))
        columns = [r[1] for r in self.store.db.execute('PRAGMA table_info(lab_jev_egress)')]
        self.assertEqual(columns, ['request_id', 'packet_digest', 'admitted_at', 'revision', 'generation'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM lab_jev_egress').fetchone()[0], 2)
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM lab_jev_egress_facts WHERE request_id='legacy'").fetchone())


class ProviderV6Tests(V6Case):
    def provider(self, transport=respond, authorize=None, **kw):
        return JevProvider(model_selector='jev-1.13.0', response_version='jev-1.13.0', authorize=authorize or self.gate,
                           credential=lambda: 'fixture', transport=transport, clock=self.store.clock, ha_bindings=BINDINGS,
                           allow_recent_history=True, **kw)

    def evaluate(self, provider, rid='p1', cutoff=100.0, window=10):
        self.store.db.execute('INSERT OR REPLACE INTO lab_reasoning_requests VALUES(?,?,?)', (rid, self.sid, 'dispatching'))
        rows = live_records(self.store, self.store.get_session(self.sid), 1, cutoff, window_seconds=window, retain_history=True)
        records = causal_evidence(rows, site_id='victoria', session_id=self.sid, arm='a', generation=1, cutoff=cutoff,
                                  window_seconds=window, retain_history=True)
        return provider.evaluate(records, request_id=rid, site_id='victoria', session_id=self.sid, arm='a', generation=1,
                                 cutoff=cutoff, deadline=cutoff + 10, window_seconds=window,
                                 shared_context=qwen(cutoff=cutoff, session=self.sid))

    def test_v6_provider_sends_facts_only_and_sidecar_is_an_independent_copy(self):
        bodies, seen = [], []

        def transport(body, *_):
            bodies.append(json.loads(body))
            return respond(body)

        def authorize(context, packet, phase, local=None):
            seen.append(phase)
            result = self.gate(context, packet, phase, local=local)
            local['records'].clear()   # cannot alter what is sent or verified next
            return result
        result = self.evaluate(self.provider(transport, authorize, wire=wire.WIRE_VERSION))
        self.assertEqual((result['status'], seen), ('ready', ['send', 'publish']))
        self.assertEqual(set(bodies[0]['state']) & {'evidence', 'semantic', 'site_id', 'observation_cutoff'}, set())
        wire.validate_state(bodies[0]['state'])
        diagnostics = result['diagnostics']
        self.assertEqual((diagnostics['wire_version'], diagnostics['projection_version'], diagnostics['question_version']),
                         (wire.WIRE_VERSION, wire.PROJECTION_VERSION, wire.QUESTION_VERSION))
        event = json.loads(self.store.db.execute("SELECT payload FROM events WHERE kind='reasoning.request'").fetchone()[0])
        self.assertEqual(event['evidence_digest'], result['provenance']['evidence_digest'])
        self.assertEqual(event['request'], bodies[0])
        self.assertLessEqual(diagnostics['estimated_tokens'], wire.HARD_TOKENS)

    def test_over_budget_fails_locally_without_egress(self):
        calls = []
        with patch.object(wire, 'HARD_TOKENS', 10):
            with self.assertRaises(ProviderFailure) as caught:
                self.evaluate(self.provider(lambda *a: calls.append(a), wire=wire.WIRE_VERSION))
        self.assertEqual((caught.exception.reason, caught.exception.indeterminate), ('request_over_budget', False))
        self.assertEqual(calls, [])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM lab_jev_egress').fetchone()[0], 0)

    def test_ungranted_wire_is_a_controlled_reason(self):
        self.policy['structured_sources'] = [QWEN_EGRESS]
        with self.assertRaises(ProviderFailure) as caught:
            self.evaluate(self.provider(wire=wire.WIRE_VERSION))
        self.assertEqual(caught.exception.reason, 'household_egress_not_authorized:wire_not_permitted')

    def test_unregistered_wire_refused_at_construction(self):
        with self.assertRaises(ValueError):
            self.provider(wire='den-facts-7')


class ShadowTests(V6Case):
    def test_shadow_verifies_beside_v5_and_never_sends_v6(self):
        bodies = []

        def transport(body, *_):
            bodies.append(json.loads(body))
            return respond(body)
        shadow = ShadowV6(self.store, self.gate)
        provider = JevProvider(model_selector='jev-1.13.0', response_version='jev-1.13.0', authorize=self.gate,
                               credential=lambda: 'fixture', transport=transport, clock=self.store.clock,
                               ha_bindings=BINDINGS, allow_recent_history=True, semantic=True, shadow=shadow)
        results = []
        for rid, cutoff in (('s1', 99.0), ('s2', 100.0)):
            self.now = cutoff + .5
            results.append(ProviderV6Tests.evaluate(self, provider, rid, cutoff))
            payload = shadow.run(rid)
            with self.subTest(rid=rid):
                self.assertEqual((payload['match'], payload['reason'], payload['wire_granted']), (True, None, True))
                self.assertEqual(payload['mode'], 'shadow_not_sent')
                wire.validate_state(payload['state'])
        self.assertEqual(len(bodies), 2)
        self.assertTrue(all('evidence' in b['state'] for b in bodies))          # v5 stays the wire
        self.assertEqual([r['diagnostics']['projection_version'] for r in results], ['structured-semantic-8']*2)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM lab_jev_egress').fetchone()[0], 2)
        self.assertIsNone(self.store.db.execute("SELECT name FROM sqlite_master WHERE name='lab_jev_egress_facts'").fetchone())
        events = [json.loads(r[0]) for r in self.store.db.execute("SELECT payload FROM events WHERE kind='reasoning.shadow' ORDER BY id")]
        self.assertEqual([e['request_id'] for e in events], ['s1', 's2'])
        self.assertIsNone(shadow.run('s2'))   # each stash runs once

    def test_shadow_records_a_mismatch_without_raising(self):
        shadow = ShadowV6(self.store, self.gate)
        records = project(self.records(window=10), ha_bindings=BINDINGS)
        records[-1] = dict(records[-1], id='424242')   # no such ledger row
        shadow.stash('m1', records=records, shared=[], context=None, site_id='victoria', session_id=self.sid, arm='a',
                     generation=1, cutoff=100.0, window_seconds=10, model='jev-1.13.0')
        payload = shadow.run('m1')
        self.assertEqual((payload['match'], payload['reason']), (False, 'source_mismatch'))
        self.policy['structured_sources'] = [QWEN_EGRESS]
        shadow.stash('m2', records=project(self.records(window=10), ha_bindings=BINDINGS), shared=[], context=None,
                     site_id='victoria', session_id=self.sid, arm='a', generation=1, cutoff=100.0, window_seconds=10,
                     model='jev-1.13.0')
        payload = shadow.run('m2')
        self.assertEqual((payload['match'], payload['wire_granted']), (True, False))

class ManagedShadowTests(unittest.TestCase):
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def test_live_reasoning_runs_the_shadow_after_completion(self):
        from live_reasoning import LiveReasoning

        class Provider:
            def __init__(self):
                self.shadow = self
                self.ran = []

            def evaluate(self, records, **kw):
                raise ProviderFailure('request_invalid')

            def run(self, request_id):
                self.ran.append(request_id)
                raise RuntimeError('shadow failures never reach v5')
        provider = Provider()
        reasoning = LiveReasoning(self.store, self.session['id'], provider)
        try:
            reasoning._execute({'request_id': 'q1', 'arm': 'a', 'generation': 1, 'records': []})
            self.assertEqual(provider.ran, ['q1'])
        finally:
            reasoning.close()
            reasoning.pool.shutdown(wait=True)

    def test_shadow_wire_is_an_exact_optional_setting(self):
        from managed_jev import reasoning_factory
        config = {'schema_version': 1, 'policy_file': 'policy', 'credential_file': 'key', 'response_version': 'jev-1.13.0'}
        policy = {'schema_version': 1, 'enabled': True, 'scope': 'household_structured', 'revision': 'lab1', 'generation': 1,
                  'expires_at': 200, 'site_id': 'victoria', 'camera_ids': ['den'], 'model_selector': 'jev-1.13.0',
                  'max_calls_per_minute': 120}
        self.store.append_event(self.session['id'], 'reasoning.setting', {'enabled': True, 'revision': 1})
        for setting, shadowed in ((None, False), (wire.WIRE_VERSION, True)):
            value = dict(config, **({'shadow_wire': setting} if setting else {}))
            with patch('managed_jev.private_json', side_effect=lambda p: dict(value if p == 'config' else policy)):
                factory = reasoning_factory('config')
                reasoner = factory(self.store, self.session['id'])
                try:
                    self.assertEqual(reasoner.provider.shadow is not None, shadowed)
                    self.assertIsNone(reasoner.provider.wire)
                finally:
                    reasoner.close()
                    reasoner.pool.shutdown(wait=True)
        with patch('managed_jev.private_json', side_effect=lambda p: dict(config, shadow_wire='den-facts-7')):
            with self.assertRaises(ValueError):
                reasoning_factory('config')


class LiveV6Tests(V6Case):
    """The live switch: config 'wire': 'den-facts-6' sends v6 through the
    managed factory, the v6 policy branch and LiveReasoning, into the same
    output shape v5 consumers read. Absent, v5 is unchanged."""
    CONFIG = {'schema_version': 1, 'policy_file': 'policy', 'credential_file': 'key', 'response_version': 'jev-1.13.0',
              'ha_bindings': BINDINGS}

    def setUp(self):
        super().setUp()
        # V6Case's minimal reservation table; LiveReasoning creates the real one.
        self.store.db.execute('DROP TABLE lab_reasoning_requests')
        from service_runtime import ServiceRuntime
        ServiceRuntime(self.store, {}, None, None)
        self.store.db.execute('INSERT INTO lab_experiment_state VALUES(?,?,?)', (self.sid, 'detection', 1))
        self.store.append_event(self.sid, 'reasoning.setting', {'enabled': True, 'revision': 1})
        self.store.db.commit()
        self.reasoners = []
        self.bodies = []

    def tearDown(self):
        for reasoner in self.reasoners:
            reasoner.close()
            reasoner.pool.shutdown(wait=True)
        super().tearDown()

    def transport(self, body, *_):
        self.bodies.append(json.loads(body))
        return respond(body)

    def run_live(self, config, cutoff=100.0, rid_state='completed'):
        """One arm-a request through the managed factory; returns (reasoner, states)."""
        from managed_jev import reasoning_factory
        with patch('managed_jev.private_json', side_effect=lambda p: deepcopy(config if p == 'config' else self.policy)):
            factory = reasoning_factory('config')
            reasoner = factory(self.store, self.sid)
            self.reasoners.append(reasoner)
            # No private credential file or network in tests.
            reasoner.provider.credential = lambda: 'fixture'
            reasoner.provider.transport = self.transport
            self.now = cutoff + .5
            rows = live_records(self.store, self.store.get_session(self.sid), 1, cutoff, window_seconds=10, retain_history=True)
            reasoner.offer(rows, generation=1, cutoff=cutoff, arms=('a',),
                           shared_context={'a': qwen(cutoff=cutoff, session=self.sid)})
            fixtures.PairedRuntimeTests.settle(self, lambda: not reasoner.futures and self.states()[-1:] != ['pending']
                                               and self.states()[-1:] != ['dispatching'])
        return factory, reasoner

    def states(self):
        return [r[0] for r in self.store.db.execute('SELECT state FROM lab_reasoning_requests ORDER BY rowid')]

    def outputs(self):
        return [json.loads(r[0]) for r in self.store.db.execute("SELECT payload FROM events WHERE kind='reasoning.output' ORDER BY id")]

    def test_live_v6_goes_through_policy_and_provider_end_to_end(self):
        factory, reasoner = self.run_live(dict(self.CONFIG, wire=wire.WIRE_VERSION, shadow_wire=wire.WIRE_VERSION))
        self.assertEqual((factory.wire, factory.shadow_wire), (wire.WIRE_VERSION, None))
        self.assertEqual(reasoner.provider.wire, wire.WIRE_VERSION)
        self.assertIsNone(reasoner.provider.shadow, 'no shadow while v6 is the live wire')
        self.assertEqual(self.states(), ['completed'])
        sent = self.bodies[0]
        wire.validate_state(sent['state'])                          # closed vocabulary only
        self.assertNotIn('evidence', sent['state'])
        self.assertEqual(canonical(sent['questions']), canonical(wire.QUESTIONS))
        admitted = self.store.db.execute('SELECT request_id,packet_digest FROM lab_jev_egress').fetchall()
        facts = self.store.db.execute('SELECT request_id,wire_version,evidence_digest FROM lab_jev_egress_facts').fetchall()
        self.assertEqual(len(admitted), 1)
        self.assertEqual(admitted[0][1], digest(sent))
        self.assertEqual([tuple(f)[:2] for f in facts], [(admitted[0][0], wire.WIRE_VERSION)])
        output, = self.outputs()
        judgment = output['judgment']
        self.assertEqual((judgment['activity'], judgment['provider'], judgment['probability']), ('reading', 'JEV', .7))
        self.assertEqual(set(judgment['diagnostics']['answers']),
                         {'activity', 'reading_supported', 'phone_use_supported', 'computer_work_supported'})
        self.assertEqual(judgment['diagnostics']['wire_version'], wire.WIRE_VERSION)
        self.assertEqual(judgment['question'], wire.QUESTIONS['activity']['instructions'])
        self.assertEqual(output['provenance']['evidence_digest'], facts[0][2])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE kind='reasoning.shadow'").fetchone()[0], 0)

    def test_live_den_facts_7_sends_the_position_word_under_its_own_grant(self):
        region = {'edge': 'image_right', 'edge_margin_frac': .03, 'min_width_frac': .35, 'min_top_frac': .08}
        config = dict(self.CONFIG, wire=wire.WIRE_VERSION_7, desk_seat=region)
        self.run_live(config, cutoff=100.0)
        self.assertEqual(self.states(), ['failed'])  # the household has granted only den-facts-6
        self.assertEqual(self.bodies, [])
        self.policy['structured_sources'] = [QWEN_EGRESS, wire.WIRE_VERSION, wire.WIRE_VERSION_7]
        factory, reasoner = self.run_live(config, cutoff=101.0)
        self.assertEqual((factory.wire, factory.desk_seat, reasoner.provider.desk_seat), (wire.WIRE_VERSION_7, region, region))
        self.assertEqual(self.states(), ['failed', 'completed'])
        sent = self.bodies[0]['state']
        wire.validate_state(sent)
        self.assertEqual((sent['schema'], sent['now']['position']), (wire.WIRE_VERSION_7, 'elsewhere_in_room'))
        diagnostics = self.outputs()[-1]['judgment']['diagnostics']
        self.assertEqual((diagnostics['wire_version'], diagnostics['projection_version']),
                         (wire.WIRE_VERSION_7, 'structured-facts-10'))

    def test_live_v6_result_has_the_v5_consumer_shape(self):
        self.run_live(dict(self.CONFIG, shadow_wire=wire.WIRE_VERSION), cutoff=100.0)
        self.run_live(dict(self.CONFIG, wire=wire.WIRE_VERSION), cutoff=101.0)
        self.assertEqual(self.states(), ['completed', 'completed'])
        v5, v6 = self.outputs()
        self.assertIn('evidence', self.bodies[0]['state'])
        self.assertNotIn('evidence', self.bodies[1]['state'])
        self.assertEqual(set(v5), set(v6))
        self.assertEqual(set(v5['judgment']), set(v6['judgment']))
        self.assertEqual(set(v5['prediction']), set(v6['prediction']))
        self.assertEqual(set(v5['judgment']['probabilities']), set(v6['judgment']['probabilities']))
        self.assertEqual(set(v5['provenance']) - set(v6['provenance']), {'budget_trimmed'},
                         'v6 keeps every v5 provenance key but the v5-only trim count')
        self.assertEqual(set(v5['judgment']['diagnostics']['answers']), set(v6['judgment']['diagnostics']['answers']))
        for key in ('request', 'answers', 'usage', 'usage_status', 'projection_version', 'question_version'):
            self.assertIn(key, v6['judgment']['diagnostics'])
        for key in ('request_id', 'evidence_ids', 'observed_at', 'question_version', 'input_digest', 'fresh_evidence',
                    'authorization_revision', 'authorization_generation'):
            self.assertIn(key, v6['provenance'])
        # Graph: the result stage reads both the same way.
        import graph as lab_graph
        from controller import Controller
        result = next(s for s in lab_graph.graph(self.store, self.sid)['arms']['a']['chain'] if s['stage'] == 'result')
        self.assertEqual((result['activity'], result['provider_status'], result['probability']), ('reading', 'ready', .7))
        self.assertIn('reading_supported', result['answers'] if isinstance(result['answers'], str) else json.dumps(result['answers']))
        # Findings: both live outputs are typed live predictions of run jevA.
        findings = Controller(self.store).findings(self.sid)
        self.assertEqual([r['run_id'] for r in findings['runs'] if r.get('mode') == 'live' and r.get('metric_kind') != 'smoothed'],
                         ['jevA'])

    def test_live_v6_refuses_without_the_wire_grant(self):
        self.policy['structured_sources'] = [QWEN_EGRESS]
        self.run_live(dict(self.CONFIG, wire=wire.WIRE_VERSION))
        self.assertEqual(self.states(), ['failed'])
        result = json.loads(self.store.db.execute('SELECT result FROM lab_reasoning_requests').fetchone()[0])
        self.assertEqual(result, {'status': 'failed', 'reason': 'household_egress_not_authorized:wire_not_permitted'})
        self.assertEqual(self.bodies, [])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM lab_jev_egress').fetchone()[0], 0)

    def test_without_the_key_v5_is_unchanged(self):
        from managed_jev import reasoning_factory
        factory, reasoner = self.run_live(dict(self.CONFIG))
        self.assertEqual((factory.wire, reasoner.provider.wire, reasoner.provider.shadow), (None, None, None))
        self.assertEqual(self.states(), ['completed'])
        self.assertIn('semantic', self.bodies[0]['state'])
        self.assertEqual(self.outputs()[0]['judgment']['diagnostics']['projection_version'], 'structured-semantic-8')
        self.assertIsNone(self.store.db.execute("SELECT name FROM sqlite_master WHERE name='lab_jev_egress_facts'").fetchone())
        with patch('managed_jev.private_json', side_effect=lambda p: dict(self.CONFIG, wire='den-facts-7')):
            with self.assertRaises(ValueError):
                reasoning_factory('config')


if __name__ == '__main__':
    unittest.main()
