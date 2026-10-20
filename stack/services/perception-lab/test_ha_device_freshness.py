"""Den device facts on the v6 wire, built from the real HA record shapes: a
primary HA source whose payload['ha'] is keyed by raw entity ids (plus the
lab's derived.* facts) and a separate owner-presence source, each stored as
HAIngestor stores it and read back as live_records reads it.

A recorded den session showed two different things:
* live, the packets carried every device fact whenever an HA snapshot was
  available and still valid at the cutoff, and 'stale' otherwise. With one new
  TLS connection per entity GET, a snapshot became available ~1.9 s into its
  3 s validity (measured from acquisition start), so ~40% of cutoffs had no
  valid snapshot. The freshness rule is right; the acquisition was slow
  (ha_client now reuses one connection per snapshot).
* the offline replay reported every device fact stale for the whole session:
  it recovered its HA bindings only from v5 packets (state.evidence), which
  the facts-only v6 state does not carry, so it bound nothing, projected no
  HA fact, and _devices marked every supplied fact stale.
Synthetic values only (the state words of that session)."""
from datetime import timezone
import unittest

import jev_replay as replay
import jev_wire as wire
from ambient_context import shared_context
from jev_evidence import granted_bindings, project, project_context
from persisted_evidence import observation_record
from reasoning import causal_evidence
from test_jev_wire import KEYBOARD, WRIST_ON, frame

SESSION = {'id': 's', 'site_id': 'victoria', 'camera_id': 'den'}
PRIMARY, PRESENCE = 'victoria-ha-lab', 'la-owner-presence'
# Operator bindings (entity -> fact), as the JEV provisioning file names them.
BINDINGS = {'binary_sensor.den_person_occupancy': 'person_present', 'derived.computer_active': 'computer_active',
            'derived.computer_input_recency': 'computer_input_recency', 'light.den_lamp': 'den_light',
            'media_player.den': 'den_audio_playback', 'person.owner': 'owner_home'}
GRANTS = {'ha': {'a': 'granted', 'b': 'granted'}, 'ambient': {'a': 'not_provisioned', 'b': 'not_provisioned'},
          'caption': {'a': 'not_provisioned', 'b': 'not_provisioned'},
          'qwen_scene': {'a': 'not_provisioned', 'b': 'not_provisioned'},
          'temporal_encoder': {'a': 'not_provisioned', 'b': 'not_provisioned'},
          'spatial': {'a': 'not_provisioned', 'b': 'not_provisioned'}}
PERIOD, ACQUISITION, TTL = 1.88, 1.88, 3.0     # the recorded session's cadence, latency and validity


def known(state, **extra):
    return {'state': state, 'status': 'known', **extra}


def primary_states(occupancy='on', active='on', recency='under_15s'):
    inputs = ['sensor.pc_lastactive', 'sensor.pc_session']
    return {'binary_sensor.den_person_occupancy': known(occupancy, origin='same_camera_visual'),
            'light.den_lamp': known('on'), 'media_player.den': known('playing'),
            'sensor.pc_session': known('Unlocked'), 'sensor.pc_lastactive': known('2026-09-29T20:00:00+00:00'),
            'sun.sun': known('below_horizon'),
            'derived.computer_active': known(active, provenance={'inputs': inputs, 'rule': 'computer_active_v1',
                                                                  'max_idle_seconds': 60}),
            'derived.computer_input_recency': known(recency, provenance={'inputs': inputs, 'rule': 'computer_active_v3'})}


def snapshot(event_id, t, source, states, acquisition=ACQUISITION):
    """One ha.observation row exactly as HAIngestor stores it (observed_at is
    the acquisition start, available_at the ingest time), read back through
    observation_record and given an arm as live_records does."""
    key = 'ha:%s:%s' % (source, event_id)
    payload = {'observed_at': t, 'ha': states, 'acquisition_duration_s': acquisition, 'site_id': 'victoria',
               'camera_id': 'den', 'session_id': 's', 'available_at': t + acquisition, 'valid_until': t + TTL,
               'evidence_roots': [key], 'snapshot_key': key, 'snapshot_digest': '0' * 64,
               'quality': {'timing_basis': 'reconciled_snapshot_acquisition', 'source_id': source,
                           'atomic_snapshot': False, 'acquisition_duration_s': acquisition}}
    return {**observation_record(event_id, 'ha.observation', payload, SESSION), 'arm': 'a', 'generation': 1}


def poll(start, end, *, period=PERIOD, acquisition=ACQUISITION, states=lambda index: primary_states()):
    """Both sources polled together every `period`, ingested together once the
    slower (primary) acquisition finishes: as the HA pump records them."""
    rows, t, index = [], start, 0
    while t < end:
        rows.append(snapshot(1000 + 2*index, t, PRIMARY, states(index), acquisition))
        rows.append(snapshot(1001 + 2*index, t, PRESENCE, {'person.owner': known('home')}, acquisition))
        t, index = t + period, index + 1
    return rows


def live_state(rows, cutoff, bindings=BINDINGS):
    """The v6 state the live provider builds at this cutoff (JevProvider._evaluate_facts)."""
    available = [r for r in rows if r['available_at'] <= cutoff and r['observed_at'] >= cutoff - 10]
    block = shared_context(available, cutoff, grants=GRANTS, bindings=BINDINGS, ha_provisioned=True,
                           fact_grants={f: True for f in BINDINGS.values()})['a']
    context = project_context(block)
    dropped = [f for f, v in context['ha_facts'].items() if v != 'supplied']
    bound = dict(ha_bindings=granted_bindings(bindings, dropped), zone_bindings={})
    local, packet, _ = wire.prepare(project(available, **bound), [], context, site_id='victoria', session_id='s', arm='a',
                                    generation=1, cutoff=cutoff, window_seconds=10, model='jev-1.13.0')
    wire.validate_state(packet['state'])
    return packet['state'], block, available


def frames(start, end):
    return [frame('f%d' % i, start + i * .5, [KEYBOARD], [WRIST_ON]) for i in range(int((end - start) / .5))]


class LiveDeviceFactsTests(unittest.TestCase):
    def test_raw_and_derived_entities_from_two_sources_reach_the_devices_block(self):
        # Snapshot observed 97.5, available 99.38, valid until 100.5.
        rows = frames(90, 100) + poll(97.5, 98)
        state, _, _ = live_state(rows, 100.0)
        self.assertEqual(state['devices'], {'pc_input': 'under_15s', 'pc_active': 'on', 'den_light': 'on',
                                            'den_audio': 'playing', 'owner_home': 'home', 'origin': 'independent_device'})
        self.assertEqual(state['now']['frigate_occupancy'], 'on')
        self.assertNotEqual(state['support']['computer_work']['pc_input_role'], 'unknown')
        # Keyed by fact: the presence-only snapshot never shadows the primary's facts.
        later = rows + [snapshot(9001, 97.6, PRESENCE, {'person.owner': known('away')}, .15)]
        self.assertEqual(live_state(later, 100.0)[0]['devices']['pc_input'], 'under_15s')

    def test_no_valid_snapshot_at_the_cutoff_is_stale_never_an_older_value(self):
        # Past 100.5 the 97.5 snapshot has expired; the next (observed 99.38)
        # is still being acquired until 101.26: every device fact is stale.
        rows = frames(90, 101) + poll(97.5, 100)
        state, _, _ = live_state(rows, 100.8)
        self.assertEqual({v for k, v in state['devices'].items() if k != 'origin'}, {'stale'})
        self.assertEqual(state['support']['computer_work']['pc_input_role'], 'unknown')
        self.assertEqual(live_state(rows, 101.3)[0]['devices']['pc_input'], 'under_15s')

    def test_acquisition_latency_not_the_freshness_rule_sets_the_stale_share(self):
        def stale_share(period, acquisition):
            rows = frames(80, 130) + poll(80, 130, period=period, acquisition=acquisition)
            cutoffs = [100 + i * .1 for i in range(200)]
            stale = sum(live_state(rows, c)[0]['devices']['pc_input'] == 'stale' for c in cutoffs)
            return stale / len(cutoffs)
        # One TLS connection per GET: ~1.9 s to acquire, polled back to back.
        self.assertGreater(stale_share(PERIOD, ACQUISITION), .3)
        # One keep-alive connection per snapshot (~0.55 s for 7 entities) at
        # the pump's 1 s interval: always a valid snapshot, same 3 s validity.
        self.assertEqual(stale_share(1.0, .55), 0)


class ReplayBindingRecoveryTests(unittest.TestCase):
    """jev_replay/jev_cadence_replay rebuild HA projections from bindings
    recovered from the recorded packets."""

    def recorded(self):
        cycle = [('on', 'on', 'under_15s'), ('on', 'on', '15_to_60s'), ('off', 'off', '1_to_5_min'), ('on', 'off', '1_to_5_min')]
        rows = frames(80, 130) + poll(80, 130, states=lambda i: primary_states(*cycle[(i // 3) % len(cycle)]))
        items = []
        for index, cutoff in enumerate(100 + i * .7 for i in range(40)):
            state, block, records = live_state(rows, cutoff)
            evidence = causal_evidence(records, site_id='victoria', session_id='s', arm='a', generation=1, cutoff=cutoff,
                                       window_seconds=10, retain_history=True)
            items.append({'request_id': 'r%d' % index, 'arm': 'a',
                          'payload': {'records': evidence, 'cutoff': cutoff, 'context_snapshot': block, 'arm': 'a',
                                      'generation': 1, 'site_id': 'victoria', 'session_id': 's', 'window_seconds': 10},
                          'event': {'request': {'state': state}}})
        return items

    @staticmethod
    def rebuilt(item, bindings):
        payload = item['payload']
        context = project_context(payload['context_snapshot'])
        dropped = [f for f, v in context['ha_facts'].items() if v != 'supplied']
        bound = dict(ha_bindings=granted_bindings(bindings, dropped), zone_bindings={})
        local = wire.sidecar(project(payload['records'], **bound), [], context, [], payload['cutoff'])
        return wire.build_state(local, cutoff=payload['cutoff'])

    def test_v6_packets_recover_the_bindings_and_the_replay_matches_what_went_out(self):
        items = self.recorded()
        devices = [i['event']['request']['state']['devices'] for i in items]
        self.assertIn('stale', {d['pc_input'] for d in devices})              # live staleness is kept, not hidden
        self.assertGreater(sum(d['pc_input'] != 'stale' for d in devices), len(items) // 2)
        # The v6 state has no evidence block: the old v5-only recovery bound nothing ...
        self.assertTrue(all('evidence' not in i['event']['request']['state'] for i in items))
        self.assertEqual({v for k, v in self.rebuilt(items[0], {})['devices'].items() if k != 'origin'}, {'stale'})
        # ... the v6 pairing recovers every operator binding (and no unbound entity) ...
        self.assertEqual(replay.infer_ha_bindings(replay.recorded_ha_pairs(items)), BINDINGS)
        # ... and the rebuilt devices and occupancy equal what was sent, request by request.
        for item in items:
            recorded, rebuilt = item['event']['request']['state'], self.rebuilt(item, BINDINGS)
            self.assertEqual((rebuilt.get('devices'), rebuilt['now']['frigate_occupancy']),
                             (recorded.get('devices'), recorded['now']['frigate_occupancy']))

    def test_stale_and_coverage_words_never_pair(self):
        items = [i for i in self.recorded() if i['event']['request']['state']['devices']['pc_input'] == 'stale']
        self.assertTrue(items)
        self.assertEqual(replay.recorded_ha_pairs(items), [])


if __name__ == '__main__':
    unittest.main()
