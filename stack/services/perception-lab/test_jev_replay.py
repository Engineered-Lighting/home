"""Offline replay (dry run): fidelity against a ledger recorded by today's live
v5 path, causality, binding recovery and the simulated facts-key scheduler.
Temp stores and a fake transport; the harness itself never sends."""
import json
from pathlib import Path
import unittest

import jev_replay as replay
import jev_wire as wire
import test_paired_runtime as fixtures
from jev_evidence import QWEN_EGRESS
from jev_policy import LabJevPolicy
from jev_provider import JevProvider
from live_inputs import live_records
from live_reasoning import LiveReasoning
from test_jev_policy_v6 import BINDINGS, respond
from test_jev_wire import BOOK, CUP, PHONE, WRIST_ON, WRIST_PHONE, build, frame


class ReplayFidelityTests(unittest.TestCase):
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def output(self, t, objects, joints, arm, available=None):
        record = frame('x', t, objects, joints, arm=arm, session=self.session['id'])
        return self.store.append_event(self.session['id'], 'perception.output', {
            'site_id': 'victoria', 'camera_id': 'den', 'session_id': self.session['id'], 'arm': arm, 'generation': 1,
            'status': 'completed', 'observed_at': t, 'available_at': available or t + .1, 'valid_until': t + 2.5,
            'frame_id': 'frame-%s-%s' % (arm, t), 'capture_epoch': 'capture-1',
            'result': {k: record[k] for k in ('objects', 'pose_instances', 'quality')}})

    def record_session(self):
        sid = self.session['id']
        now = {'t': 100.3}
        self.store.clock = lambda: now['t']
        self.store.append_event(sid, 'reasoning.setting', {'enabled': True, 'revision': 1})
        self.store.append_event(sid, 'experiment.activated', {
            'generation': 1, 'experiment_id': 'fixture', 'reasoning_windows': {'a': 10, 'b': 10},
            'ha_evidence': {'a': True, 'b': True}, 'caption_evidence': {'a': False, 'b': False}})
        self.store.db.execute('CREATE TABLE lab_experiment_state(session_id TEXT, generation INTEGER)')
        self.store.db.execute('INSERT INTO lab_experiment_state VALUES(?,?)', (sid, 1))
        for i in range(30):
            t = 70.0 + i
            self.output(t, [BOOK, CUP], [WRIST_ON], 'a')
            self.output(t + .2, [PHONE], [WRIST_PHONE], 'b')
        self.store.append_event(sid, 'ha.observation', {
            'site_id': 'victoria', 'camera_id': 'den', 'observed_at': 99.0, 'available_at': 99.1, 'valid_until': 102.0,
            'quality': {'camera_derived_ha_facts': ['person_present']},
            'ha': {'binary_sensor.occupancy': {'state': 'on', 'origin': 'same_camera_visual'},
                   'derived.computer_input_recency': {'state': 'over_5_min'}, 'sensor.unbound': {'state': '42'}}})
        policy = {'schema_version': 1, 'enabled': True, 'scope': 'household_structured', 'revision': 'r', 'generation': 1,
                  'expires_at': 300, 'site_id': 'victoria', 'camera_ids': ['den'], 'model_selector': 'jev-1.13.0',
                  'max_calls_per_minute': 120, 'device_context': 'den-devices-1', 'structured_sources': [QWEN_EGRESS]}
        gate = LabJevPolicy(self.store, lambda: policy, lambda: {'state': 'on', 'received_at': now['t']}, ha_bindings=BINDINGS)
        provider = JevProvider(model_selector='jev-1.13.0', response_version='jev-1.13.0', authorize=gate,
                               credential=lambda: 'fixture', transport=respond, clock=self.store.clock,
                               ha_bindings=BINDINGS, allow_recent_history=True, semantic=True)
        reasoner = LiveReasoning(self.store, sid, provider)
        try:
            session = self.store.get_session(sid)
            records = live_records(self.store, session, 1, 100.0, window_seconds=10, retain_history=True)
            reasoner.offer(records, generation=1, cutoff=100.0)
            fixtures.PairedRuntimeTests.settle(self, lambda: not reasoner.futures)
        finally:
            reasoner.close()
            reasoner.pool.shutdown(wait=True)
        # A row observed before the cutoff but first available after it.
        self.output(99.9, [PHONE], [WRIST_PHONE], 'a', available=100.6)
        return sid

    def test_fidelity_rebuild_matches_recorded_v5_digest(self):
        sid = self.record_session()
        view = replay.LedgerView(Path(self.directory.name) / 'lab.sqlite3')
        try:
            report = replay.replay_session(view, sid)
        finally:
            view.close()
        self.assertEqual(report['requests'], 2)
        self.assertEqual(report['fidelity'], {'inputs_match': 2, 'evidence_match': 2, 'digest_match': 2})
        self.assertEqual(report['ha_bindings_recovered'], ['computer_input_recency', 'person_present'])
        same = report['v6_same_cutoffs']
        # The Phase-2 ring starts empty and has seen only this request's 10 s
        # window, while the ledger timeline holds 30 s: its storyline is shorter.
        self.assertEqual((same['invalid_states'], same['budget_levels'], same['ring_vs_ledger_state']), (0, {0: 2}, {'differs': 2}))
        self.assertLess(same['packet_bytes']['max'], 12*1024)
        self.assertEqual(report['recorded_v5_calls'], {'a': 1, 'b': 1})
        self.assertIn('a', report['simulated_v6_scheduler'])

    def test_audit_runs_the_v6_policy_checks_over_recorded_requests(self):
        sid = self.record_session()
        view = replay.LedgerView(Path(self.directory.name) / 'lab.sqlite3')
        try:
            report = replay.audit_session(view, sid)
            self.assertEqual((report['verdicts'], report['timeline_verified']), ({'match': 2}, {'full': 2}))
        finally:
            view.close()
        # A tampered ledger row no longer re-derives to the sidecar.
        self.store.db.execute("""UPDATE events SET payload=json_set(payload,'$.result.objects[0].label','cup')
            WHERE kind='perception.output' AND json_extract(payload,'$.observed_at')=99.0""")
        view = replay.LedgerView(Path(self.directory.name) / 'lab.sqlite3')
        try:
            self.assertEqual(replay.audit_session(view, sid)['verdicts'], {'source_mismatch': 1, 'match': 1})
        finally:
            view.close()

    def test_replay_is_causal_no_future_rows(self):
        sid = self.record_session()
        view = replay.LedgerView(Path(self.directory.name) / 'lab.sqlite3')
        try:
            session = view.get_session(sid)
            item = next(i for i in replay._recorded(view, sid) if i['arm'] == 'a')
            records, _ = replay.rebuild_inputs(view, session, replay._activations(view, sid)[1], item['payload'])
            self.assertTrue(all(r['available_at'] <= 100.0 for r in records))
            self.assertNotIn(99.9, [r['observed_at'] for r in records])
            timeline = replay.LedgerTimeline(view, session, {'ha_bindings': BINDINGS, 'zone_bindings': {}})
            self.assertTrue(all(e['available_at'] <= 100.0 for e in timeline.entries('a', 1, 100.0)))
            # The harness is read-only: the view refuses writes.
            with self.assertRaises(Exception):
                view.db.execute("INSERT INTO events(session_id,kind,created_at,payload) VALUES('x','y',0,'{}')")
        finally:
            view.close()


class ReplayUnitTests(unittest.TestCase):
    def test_bindings_are_recovered_only_when_unique(self):
        pairs = [({'ha': {'binary_sensor.a': {'state': 'on'}, 'binary_sensor.b': {'state': 'on'},
                          'derived.computer_input_recency': {'state': 'under_15s'}}},
                  {'ha': {'person_present': 'on', 'computer_input_recency': 'under_15s'}}),
                 ({'ha': {'binary_sensor.a': {'state': 'on'}, 'binary_sensor.b': {'state': 'off'}}},
                  {'ha': {'person_present': 'on'}})]
        self.assertEqual(replay.infer_ha_bindings(pairs),
                         {'binary_sensor.a': 'person_present', 'derived.computer_input_recency': 'computer_input_recency'})
        self.assertEqual(replay.infer_ha_bindings(pairs[:1]), {'derived.computer_input_recency': 'computer_input_recency'})

    def test_simulated_scheduler_counts_match_live_rule(self):
        reading = build()
        phone = build([frame('p%d' % i, 60.0 + i, [PHONE], [WRIST_PHONE]) for i in range(40)])
        empty = build([frame('e%d' % i, 60.0 + i, person=False) for i in range(40)], facts=None)
        self.assertNotEqual(wire.facts_key(reading), wire.facts_key(phone))
        self.assertEqual(replay.code_gate(empty), 'no_person')
        ticks = [(0.0, reading), (1.0, reading), (5.0, reading), (10.0, reading), (13.0, phone), (14.0, reading),
                 (16.0, reading), (17.0, empty), (20.0, empty), (48.0, empty)]
        counts = replay.simulate(ticks)
        self.assertEqual(counts['offer_changed'], 3)            # 0, 13, 16
        self.assertEqual(counts['offer_keepalive'], 1)          # 10 s unchanged with one person
        self.assertEqual(counts['skip_unchanged_facts'], 2)     # 1, 5
        self.assertEqual(counts['skip_rate_limited'], 1)        # changed at 14 but within 2 s of 13
        self.assertEqual((counts['gate_no_person'], counts['skip_gate_keepalive_not_due']), (2, 1))
        self.assertEqual(counts['calls'], 4)


if __name__ == '__main__':
    unittest.main()
