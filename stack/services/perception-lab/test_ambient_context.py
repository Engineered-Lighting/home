"""Ambient/shared context: pure facts, identical arms, egress allowlist and policy re-verification."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import tempfile
import unittest

import ambient_context as ambient
from ambient_context import facts, shared_context, zone
from evidence_policy import source_policy
from jev_evidence import AMBIENT_EGRESS, DEVICE_EGRESS, PRESENCE_EGRESS, project, project_context
from reasoning import build_jev_request, causal_evidence
import test_paired_runtime as fixtures

TZ = timezone(timedelta(hours=-7))  # Victoria in PDT; the dev host has no tzdata
BINDINGS = {'sun.sun': 'daylight', 'weather.forecast_home': 'weather_condition'}
OCCUPANCY = 'binary_sensor.den_oak_person_occupancy'
# Ambient bindings plus one grant-free device fact the packet can carry.
DEVICE_BINDINGS = {**BINDINGS, OCCUPANCY: 'person_present'}
GRANTS = source_policy(None, ha={'a': True, 'b': True})


def at(*args):
    return datetime(*args, tzinfo=TZ).timestamp()


def ha_record(rid, observed, states, *, available=None, source='victoria-den-ha', arm='a'):
    return {'id': rid, 'site_id': 'victoria', 'camera_id': 'den', 'session_id': 's', 'arm': arm, 'generation': 1,
            'source_kind': 'ha.observation', 'observed_at': observed, 'available_at': available or observed+.1,
            'valid_until': observed+3, 'ha': {e: {'state': v, 'status': 'known'} for e, v in states.items()},
            'quality': {'source_id': source}}


class AmbientFactTests(unittest.TestCase):
    def test_part_of_day_day_type_and_hour_label(self):
        cases = [((2026, 9, 29, 0, 0), 'night'), ((2026, 9, 29, 4, 59), 'night'), ((2026, 9, 29, 5, 0), 'early_morning'),
                 ((2026, 9, 29, 8, 0), 'morning'), ((2026, 9, 29, 12, 0), 'afternoon'), ((2026, 9, 29, 17, 0), 'evening'),
                 ((2026, 9, 29, 20, 59), 'evening'), ((2026, 9, 29, 21, 0), 'late_evening'), ((2026, 9, 29, 23, 59), 'late_evening')]
        for moment, part in cases:
            with self.subTest(moment=moment):
                self.assertEqual(facts(at(*moment), TZ)['part_of_day'], part)
        value = facts(at(2026, 9, 29, 20, 30), TZ, daylight='dark', weather='rainy')
        self.assertEqual({k: value[k] for k in ('day_type', 'local_hour', 'daylight', 'weather')},
                         {'day_type': 'weekday', 'local_hour': '20:00-21:00', 'daylight': 'dark', 'weather': 'rainy'})
        self.assertEqual(facts(at(2026, 10, 3, 23, 10), TZ)['day_type'], 'weekend')
        self.assertEqual(facts(at(2026, 10, 3, 23, 10), TZ)['local_hour'], '23:00-24:00')
        self.assertEqual(value['origin_family'], {'clock': ['day_type', 'local_hour', 'part_of_day'],
                                                  'independent_device': ['daylight', 'weather']})
        # Bucketed labels only: no epoch, date or offset reaches the block.
        self.assertNotIn('2026', json.dumps(value))

    def test_unloadable_zone_is_unknown_never_a_silent_other_zone(self):
        for tz in (None, zone('Not/A_Zone'), zone('../etc/passwd'), zone(''), zone(7)):
            value = facts(at(2026, 9, 29, 20, 30), tz)
            self.assertEqual((value['part_of_day'], value['day_type'], value['local_hour']), ('unknown',)*3)
        self.assertIs(zone(TZ), TZ)
        # Without tzdata (this dev host) the real name also resolves to None, not UTC.
        resolved = zone('America/Vancouver')
        self.assertTrue(resolved is None or resolved.utcoffset(datetime(2026, 9, 29)) == timedelta(hours=-7))

        class Broken(timezone.__base__):
            def utcoffset(self, dt): raise RuntimeError('broken tz')
        self.assertEqual(facts(at(2026, 9, 29, 20, 30), Broken())['part_of_day'], 'unknown')

    def test_ha_states_map_to_closed_enums(self):
        cutoff = 100
        records = [ha_record(1, 99, {'sun.sun': 'above_horizon', 'weather.forecast_home': 'partlycloudy'})]
        self.assertEqual(ambient.ha_inputs(records, cutoff, BINDINGS), {'daylight': 'day', 'weather': 'partlycloudy'})
        records = [ha_record(1, 99, {'sun.sun': 'dawn', 'weather.forecast_home': 'Rainy with a chance of meatballs'})]
        self.assertEqual(ambient.ha_inputs(records, cutoff, BINDINGS), {'daylight': 'unknown', 'weather': 'unknown'})
        self.assertEqual(ambient.ha_inputs(records, cutoff, {}), {'daylight': 'unknown', 'weather': 'unknown'})

    def test_each_entity_reads_its_newest_snapshot_not_the_newest_of_any_source(self):
        records = [ha_record(1, 97, {'sun.sun': 'below_horizon', 'weather.forecast_home': 'rainy'}),
                   ha_record(2, 99, {'person.owner': 'home'}, source='la-owner-presence'),
                   ha_record(3, 98, {'sun.sun': 'above_horizon'}),
                   ha_record(4, 99.5, {'sun.sun': 'below_horizon'}, available=101)]  # not yet available at the cutoff
        self.assertEqual(ambient.ha_inputs(records, 100, BINDINGS), {'daylight': 'day', 'weather': 'rainy'})


class SharedContextTests(unittest.TestCase):
    def setUp(self):
        self.cutoff = at(2026, 9, 29, 20, 30)
        self.records = [ha_record(1, self.cutoff-1, {'sun.sun': 'below_horizon', 'weather.forecast_home': 'rainy',
                                                     OCCUPANCY: 'on'}, arm=arm) for arm in 'ab']

    def blocks(self, **kw):
        args = dict(grants=GRANTS, tz=TZ, provisioned=True, bindings=DEVICE_BINDINGS, ha_provisioned=True)
        args.update(kw)
        return shared_context(self.records, self.cutoff, **args)

    def test_both_arms_get_one_block_and_one_digest(self):
        blocks = self.blocks()
        self.assertEqual(blocks['a'], blocks['b'])
        self.assertEqual(blocks['a']['coverage'], {'ha': 'supplied', 'ambient': 'supplied', 'caption': 'not_in_baseline',
            'qwen_scene': 'not_provisioned', 'temporal_encoder': 'not_provisioned', 'spatial': 'not_provisioned'})
        self.assertEqual(blocks['a']['ha_facts'], {'person_present': 'supplied'})
        self.assertEqual(blocks['a']['ambient']['part_of_day'], 'evening')
        self.assertEqual((blocks['a']['ambient']['daylight'], blocks['a']['ambient']['weather']), ('dark', 'rainy'))
        self.assertEqual(project_context(blocks['a']), blocks['a'])

    def test_a_withheld_lever_family_is_explicit_and_leaves_the_digest_equal(self):
        grants = deepcopy(GRANTS)
        grants['ambient'] = {'a': 'withheld_by_experiment', 'b': 'granted'}
        blocks = self.blocks(grants=grants, lever=['ambient'])
        self.assertEqual(blocks['a']['coverage']['ambient'], 'withheld_by_experiment')
        self.assertNotIn('ambient', blocks['a'])
        self.assertEqual(blocks['b']['coverage']['ambient'], 'supplied')
        self.assertEqual(blocks['a']['shared_context_digest'], blocks['b']['shared_context_digest'])
        self.assertEqual(blocks['a']['digest_scope'], ['caption', 'ha', 'qwen_scene', 'spatial', 'temporal_encoder'])
        # Without the lever declaration the same difference is a changed shared context.
        unscoped = self.blocks(grants=grants)
        self.assertNotEqual(unscoped['a']['shared_context_digest'], unscoped['b']['shared_context_digest'])

    def test_missing_sources_are_explicit(self):
        self.records = []
        blocks = self.blocks(provisioned=False, ha_provisioned=False)
        self.assertEqual(blocks['a']['coverage']['ambient'], 'not_provisioned')
        self.assertEqual(blocks['a']['coverage']['ha'], 'not_provisioned')
        self.assertNotIn('ambient', blocks['a'])
        self.assertEqual(self.blocks(provisioned=False)['a']['coverage']['ha'], 'stale')
        # A provisioned zone without HA bindings still supplies clock priors; HA facts stay unknown.
        value = self.blocks(bindings={})['a']['ambient']
        self.assertEqual((value['part_of_day'], value['daylight'], value['weather']), ('evening', 'unknown', 'unknown'))

    def test_projection_refuses_extra_fields_bad_enums_and_digest_changes(self):
        block = self.blocks()['a']
        for change in (lambda b: b.update(extra=1), lambda b: b['coverage'].update(ha='negative'),
                       lambda b: b['ambient'].update(weather='sleet'), lambda b: b['ambient'].update(local_hour='20:30-21:30'),
                       lambda b: b['ambient'].update(note='free text'), lambda b: b['coverage'].update(ambient='stale'),
                       lambda b: b.update(shared_context_digest='0'*64), lambda b: b['ambient'].update(daylight='day'),
                       lambda b: b.update(digest_scope=['ha', 'ambient']), lambda b: b['coverage'].pop('spatial')):
            broken = deepcopy(block)
            change(broken)
            with self.assertRaises(ValueError):
                project_context(broken)
        self.assertIsNone(project_context(None))


class PacketTests(unittest.TestCase):
    def setUp(self):
        self.cutoff = at(2026, 9, 29, 20, 30)
        self.block = shared_context([ha_record(1, self.cutoff-1, {'sun.sun': 'below_horizon', 'weather.forecast_home': 'rainy'})],
            self.cutoff, grants=GRANTS, tz=TZ, provisioned=True, bindings=BINDINGS, ha_provisioned=True)['a']

    def packet(self, records, **kw):
        return build_jev_request(records, model='jev-1.13', site_id='victoria', session_id='s', arm='a', generation=1,
                                 cutoff=self.cutoff, retain_history=True, context=project_context(self.block), **kw)

    def test_semantic_packet_carries_one_context_copy_and_context_questions(self):
        visual = {'id': 'v1', 'site_id': 'victoria', 'camera_id': 'den', 'session_id': 's', 'arm': 'a', 'generation': 1,
                  'observed_at': self.cutoff-.5, 'available_at': self.cutoff-.2, 'valid_until': self.cutoff+2, 'objects': [{'label': 'book'}]}
        packet, provenance = self.packet(project([visual]), semantic=True)
        self.assertEqual(packet['state']['semantic']['context'], self.block)
        self.assertNotIn('shared_context', packet['state'])
        self.assertEqual(provenance['question_version'], 'den-activity-7-semantic')
        for question in packet['questions'].values():
            self.assertIn('priors about the setting, not observations of activity', question['instructions'])
        plain, provenance = self.packet(project([visual]))
        self.assertEqual(plain['state']['shared_context'], self.block)
        self.assertEqual(provenance['question_version'], 'den-activity-5-context')

    def test_context_never_makes_a_packet_current_and_raw_ha_states_stay_local(self):
        ha = project([ha_record(1, self.cutoff-1, {'sun.sun': 'below_horizon', 'weather.forecast_home': 'rainy',
                                                     'media_player.den_den': 'playing'})],
                     ha_bindings={**BINDINGS, 'media_player.den_den': 'den_audio_playback'})
        # Ambient facts feed the context block only, never per-arm device state.
        self.assertEqual(ha[0]['ha'], {'den_audio_playback': 'playing'})
        packet, provenance = self.packet(ha, semantic=True)
        self.assertEqual(provenance['status'], 'missing_evidence')
        text = json.dumps(packet)
        for private in ('sun.sun', 'weather.forecast_home', 'below_horizon', 'media_player', 'America', 'Vancouver'):
            self.assertNotIn(private, text)


class HaSelectionTests(unittest.TestCase):
    def test_latest_only_arm_keeps_the_newest_snapshot_of_every_ha_source(self):
        visual = {'id': 10, 'site_id': 'victoria', 'camera_id': 'den', 'session_id': 's', 'arm': 'a', 'generation': 1,
                  'observed_at': 100, 'available_at': 100.2, 'valid_until': 102, 'objects': [{'label': 'book'}]}
        records = [visual, ha_record(1, 98, {'sun.sun': 'below_horizon'}), ha_record(2, 99.5, {'sun.sun': 'above_horizon'}),
                   ha_record(3, 99.8, {'person.owner': 'home'}, source='la-owner-presence'),
                   ha_record(4, 100.1, {'sun.sun': 'below_horizon'})]  # after the frame: never joins it
        chosen = causal_evidence(records, site_id='victoria', session_id='s', arm='a', generation=1, cutoff=101,
                                 window_seconds=0, retain_history=True)
        self.assertEqual(sorted(r['id'] for r in chosen), [2, 3, 10])


class PolicyContextTests(unittest.TestCase):
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def gate(self, *, ambient_grant=AMBIENT_EGRESS, presence_grant=None, device_grant=None, tz=TZ, block_tz=TZ, ha=None, bindings=BINDINGS):
        from jev_policy import LabJevPolicy
        self.cutoff = at(2026, 9, 29, 20, 30)
        self.store.clock = lambda: self.cutoff+1
        self.policy = {'schema_version': 1, 'enabled': True, 'scope': 'household_structured', 'revision': 'lab1', 'generation': 1,
                       'expires_at': self.cutoff+100, 'site_id': 'victoria', 'camera_ids': ['den'], 'model_selector': 'jev-1.13',
                       'max_calls_per_minute': 20}
        if ambient_grant:
            self.policy['ambient_context'] = ambient_grant
        if presence_grant:
            self.policy['owner_presence'] = presence_grant
        if device_grant:
            self.policy['device_context'] = device_grant
        gate = LabJevPolicy(self.store, lambda: self.policy, lambda: {'state': 'on', 'received_at': self.cutoff+1},
                            ha_bindings=bindings, site_timezone=tz)
        self.store.db.execute('CREATE TABLE IF NOT EXISTS lab_reasoning_requests(id TEXT PRIMARY KEY,session_id TEXT,state TEXT)')
        self.store.db.execute('INSERT OR REPLACE INTO lab_reasoning_requests VALUES(?,?,?)', ('r1', self.session['id'], 'dispatching'))
        self.context = {'request_id': 'r1', 'site_id': 'victoria', 'session_id': self.session['id'], 'arm': 'a', 'generation': 1}
        base = {**self.context, 'camera_id': 'den'}
        rows = [{**base, 'id': 'e1', 'observed_at': self.cutoff-.5, 'available_at': self.cutoff-.2, 'valid_until': self.cutoff+2,
                 'objects': [{'label': 'book'}]}]
        if ha:
            rows.append({**base, 'id': 'h1', 'source_kind': 'ha.observation', 'observed_at': self.cutoff-.4, 'available_at': self.cutoff-.3,
                         'valid_until': self.cutoff+2, 'ha': {e: {'state': v} for e, v in ha.items()}})
        block = shared_context([], self.cutoff, grants=GRANTS, tz=block_tz, provisioned=True)['a']
        self.packet, _ = build_jev_request(project(rows, ha_bindings=bindings), model='jev-1.13', site_id='victoria',
            session_id=self.session['id'], arm='a', generation=1, cutoff=self.cutoff, retain_history=True, semantic=True,
            context=project_context(block))
        return gate

    def test_ambient_egress_needs_the_exact_household_grant(self):
        for grant in (None, 'ambient-context-2'):
            with self.subTest(grant=grant), self.assertRaisesRegex(ValueError, 'Ambient context egress not permitted'):
                gate = self.gate(ambient_grant=grant)
                gate(self.context, self.packet, 'send')
        gate = self.gate()
        self.assertTrue(gate(self.context, self.packet, 'send')['allowed'])
        self.assertTrue(gate(self.context, self.packet, 'publish')['allowed'])

    def test_clock_facts_are_rederived_with_the_gate_zone(self):
        # Built in UTC but admitted by a Victoria gate: the local hour differs.
        gate = self.gate(block_tz=timezone.utc)
        with self.assertRaisesRegex(ValueError, 'Structured source permission mismatch'):
            gate(self.context, self.packet, 'send')
        # A gate whose zone cannot load re-derives unknown and refuses known labels.
        gate = self.gate(tz='Not/A_Zone')
        with self.assertRaisesRegex(ValueError, 'Structured source permission mismatch'):
            gate(self.context, self.packet, 'send')

    def test_a_tampered_context_or_duplicate_copy_is_refused(self):
        gate = self.gate()
        tampered = deepcopy(self.packet)
        tampered['state']['semantic']['context']['coverage']['qwen_scene'] = 'supplied'
        with self.assertRaisesRegex(ValueError, 'Structured source permission mismatch'):
            gate(self.context, tampered, 'send')
        doubled = deepcopy(self.packet)
        doubled['state']['shared_context'] = doubled['state']['semantic']['context']
        with self.assertRaisesRegex(ValueError, 'Structured source permission mismatch'):
            gate(self.context, doubled, 'send')

    def test_owner_presence_is_home_away_only_and_needs_its_own_grant(self):
        bindings = {**BINDINGS, 'person.owner': 'owner_home', 'light.den_rear_left': 'den_light'}
        with self.assertRaisesRegex(ValueError, 'Owner presence egress not permitted'):
            gate = self.gate(ha={'person.owner': 'Work', 'light.den_rear_left': 'on'}, bindings=bindings)
            gate(self.context, self.packet, 'send')
        # The den lamp is device context: its own grant, separately refused.
        with self.assertRaisesRegex(ValueError, 'Device context egress not permitted'):
            gate = self.gate(ha={'person.owner': 'Work', 'light.den_rear_left': 'on'}, bindings=bindings, presence_grant=PRESENCE_EGRESS)
            gate(self.context, self.packet, 'send')
        gate = self.gate(ha={'person.owner': 'Work', 'light.den_rear_left': 'on'}, bindings=bindings, presence_grant=PRESENCE_EGRESS,
                         device_grant=DEVICE_EGRESS)
        self.assertTrue(gate(self.context, self.packet, 'send')['allowed'])
        record = next(r for r in self.packet['state']['evidence'] if r['id'] == 'h1')
        self.assertEqual(record['ha'], {'owner_home': 'away', 'den_light': 'on'})
        # Independent of the camera: no visual correlation marker, and no zone name leaves.
        self.assertNotIn('correlated_with', record['quality'])
        self.assertNotIn('Work', json.dumps(self.packet))


class PresenceProjectionTests(unittest.TestCase):
    def test_person_states_map_to_home_away_unknown_and_only_person_entities_bind(self):
        bindings = {'person.owner': 'owner_home', 'light.den_rear_left': 'den_light'}
        for state, expected in (('home', 'home'), ('not_home', 'away'), ('Work', 'away'), (None, 'unknown')):
            record = ha_record(1, 99, {'person.owner': state, 'light.den_rear_left': 'unavailable'})
            with self.subTest(state=state):
                projected = project([record], ha_bindings=bindings)[0]
                self.assertEqual(projected['ha'], {'owner_home': expected, 'den_light': 'unknown'})
                self.assertEqual(project([projected], ha_bindings=bindings)[0]['ha'], projected['ha'])
        for entity in ('sensor.owner_phone', 'device_tracker.phone', 'derived.computer_active'):
            with self.subTest(entity=entity), self.assertRaises(ValueError):
                project([ha_record(1, 99, {})], ha_bindings={entity: 'owner_home'})


class RuntimeTickTests(unittest.TestCase):
    def setUp(self):
        from test_reasoning_load import Simulation
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.sim = Simulation(self.directory.name, filler_files=0)
        self.sim.preview()
        self.sim.runtime.reasoning_factory = self.factory()

    def tearDown(self):
        self.sim.close()
        self.directory.cleanup()

    def factory(self):
        from jev_policy import LabJevPolicy
        from jev_provider import JevProvider
        from live_reasoning import LiveReasoning
        from test_reasoning_load import SyncPool, fake_transport
        sim = self.sim

        def create(store, sid):
            policy = {'schema_version': 1, 'enabled': True, 'scope': 'household_structured', 'revision': 'ambient', 'generation': 1,
                      'expires_at': sim.now+10_000, 'site_id': 'victoria', 'camera_ids': ['den'], 'model_selector': 'jev-1.13',
                      'max_calls_per_minute': 120, 'ambient_context': AMBIENT_EGRESS}
            provider = JevProvider(model_selector='jev-1.13', response_version='jev-1.13.0', allow_recent_history=True, semantic=True,
                ha_bindings=BINDINGS, authorize=LabJevPolicy(store, lambda: dict(policy), lambda: {'state': 'on', 'received_at': store.clock()},
                ha_bindings=BINDINGS, site_timezone=TZ), credential=lambda: 'fixture', transport=fake_transport, clock=store.clock)
            reasoner = LiveReasoning(store, sid, provider)
            reasoner.pool.shutdown(wait=False)
            reasoner.pool = SyncPool()
            return reasoner
        create.site_timezone, create.ha_bindings, create.ambient_provisioned = TZ, BINDINGS, lambda: True
        return create

    def offered(self):
        sim = self.sim
        sim.output('a')
        sim.output('b')
        sim.store.append_event(sim.sid, 'ha.observation', {'site_id': 'victoria', 'camera_id': 'den', 'observed_at': sim.now-.2,
            'available_at': sim.now-.1, 'valid_until': sim.now+3, 'quality': {'source_id': 'victoria-den-ha'},
            'ha': {'sun.sun': {'state': 'above_horizon'}, 'weather.forecast_home': {'state': 'cloudy'}}})
        sim.tick()
        with sim.store.lock:
            rows = sim.store.db.execute('SELECT arm,state,payload FROM lab_reasoning_requests ORDER BY rowid').fetchall()
            sent = [json.loads(r[0]) for r in sim.store.db.execute("SELECT payload FROM events WHERE kind='reasoning.request' ORDER BY id")]
        return {r[0]: (r[1], json.loads(r[2])) for r in rows}, sent

    def test_both_arms_of_one_tick_share_the_context_digest(self):
        rows, sent = self.offered()
        self.assertEqual({arm: state for arm, (state, _) in rows.items()}, {'a': 'completed', 'b': 'completed'})
        snapshots = {arm: payload['context_snapshot'] for arm, (_, payload) in rows.items()}
        self.assertEqual(snapshots['a']['shared_context_digest'], snapshots['b']['shared_context_digest'])
        self.assertEqual(snapshots['a'], snapshots['b'])
        contexts = [packet['request']['state']['semantic']['context'] for packet in sent]
        self.assertEqual(len(contexts), 2)
        self.assertEqual(contexts[0], contexts[1])
        # 2026-09-21 07:13 PDT, a Monday.
        self.assertEqual({k: contexts[0]['ambient'][k] for k in ('part_of_day', 'day_type', 'local_hour', 'daylight', 'weather')},
                         {'part_of_day': 'early_morning', 'day_type': 'weekday', 'local_hour': '07:00-08:00',
                          'daylight': 'day', 'weather': 'cloudy'})

    def test_graph_shows_the_shared_ambient_source_and_equal_request_digests(self):
        import graph as lab_graph
        rows, _ = self.offered()
        result = lab_graph.graph(self.sim.store, self.sim.sid)
        node = next(s for s in result['sources'] if s['id'] == 'ambient_context')
        self.assertEqual(node['consumed_by'], {'a': 'consumed', 'b': 'consumed'})
        self.assertEqual(node['version']['digests_equal'], True)
        self.assertEqual(node['families'], [26, 27])
        stages = {arm: next(s for s in result['arms'][arm]['chain'] if s['stage'] == 'consumed_request') for arm in ('a', 'b')}
        self.assertEqual(stages['a']['shared_context_digest'], rows['a'][1]['context_snapshot']['shared_context_digest'])
        self.assertEqual(stages['a']['shared_context_digest'], stages['b']['shared_context_digest'])

    def test_an_ambient_lever_withholds_only_one_arm_and_keeps_the_digest(self):
        runner = self.sim.runner
        runner.source_policy = source_policy(None, ha=runner.ha_evidence)
        runner.source_policy['ambient'] = {'a': 'withheld_by_experiment', 'b': 'granted'}
        runner.lever_families = ['ambient']
        rows, sent = self.offered()
        snapshots = {arm: payload['context_snapshot'] for arm, (_, payload) in rows.items()}
        self.assertEqual(snapshots['a']['coverage']['ambient'], 'withheld_by_experiment')
        self.assertNotIn('ambient', snapshots['a'])
        self.assertIn('ambient', snapshots['b'])
        self.assertEqual(snapshots['a']['shared_context_digest'], snapshots['b']['shared_context_digest'])

    def test_without_the_household_grant_ambient_is_not_provisioned_and_dispatch_proceeds(self):
        self.sim.runtime.reasoning_factory.ambient_provisioned = lambda: False
        rows, sent = self.offered()
        self.assertEqual({arm: state for arm, (state, _) in rows.items()}, {'a': 'completed', 'b': 'completed'})
        for packet in sent:
            context = packet['request']['state']['semantic']['context']
            self.assertEqual(context['coverage']['ambient'], 'not_provisioned')
            self.assertNotIn('ambient', context)


if __name__ == '__main__':
    unittest.main()
