"""Bake-off arms D (local classifier over the facts) and E (rules): feature
extraction from a read-only fixture ledger, the placement-free principle,
the reasoning-off context synthesis (a twin ledger without request rows gives
the same facts key at every tick), and valid lab-ticks-1 records from both
arms. Temp ledgers and synthetic values only; nothing is sent anywhere."""
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import unittest

import bakeoff_facts as facts
import jev_wire as wire
import lab_eval
from ambient_context import shared_context, zone
from evidence_policy import source_policy
from live_inputs import live_records
from test_ha_device_freshness import BINDINGS, primary_states
from test_jev_wire import BOOK, KEYBOARD, PHONE, WRIST_ON, WRIST_PHONE, frame
from test_lab_regression import LedgerFixture

CLASSES = list(facts.CLASSES)
DURATION = 20
TIMEZONE = 'UTC'
CONFIG = {'ha_bindings': BINDINGS, 'site_timezone': TIMEZONE}
HA_EVIDENCE = {'a': True, 'b': True}
CAPTIONS = {'a': False, 'b': False}


class FactsLedger(LedgerFixture):
    """A recorded den session: arm a reads then types, arm b holds a phone, the
    room is empty for the last seconds; HA snapshots every 2 s."""

    def build(self, *, requests):
        sid, start = self.session('facts', DURATION)
        self.sid, self.start = sid, start
        self.store.append_event(sid, 'experiment.activated', {
            'generation': 1, 'experiment_id': 'fixture', 'effective_at': start, 'reasoning_windows': {'a': 10, 'b': 10},
            'ha_evidence': HA_EVIDENCE, 'caption_evidence': CAPTIONS})
        for i in range(2 * (DURATION + 5)):
            t = start - 5 + i * .5
            empty = t >= start + DURATION - 4
            self.output(t, 'a', [] if empty else [BOOK] if t < start + 10 else [KEYBOARD], [WRIST_ON], person=not empty)
            self.output(t + .2, 'b', [] if empty else [PHONE], [WRIST_PHONE], person=not empty)
        for i in range(DURATION // 2 + 4):
            t = start - 6 + 2 * i
            self.snapshot('primary-%d' % i, t, primary_states(occupancy='off' if t >= start + DURATION - 4 else 'on'))
        if requests:
            self.record_requests()
        return sid

    def output(self, t, arm, objects, joints, *, person=True):
        record = frame('x', t, objects, joints, arm=arm, session=self.sid, person=person)
        self.store.append_event(self.sid, 'perception.output', {
            'site_id': 'victoria', 'camera_id': 'den', 'session_id': self.sid, 'arm': arm, 'generation': 1,
            'status': 'completed', 'observed_at': t, 'available_at': t + .1, 'valid_until': t + 2.5,
            'frame_id': 'frame-%s-%s' % (arm, t), 'capture_epoch': 'capture-1',
            'result': {k: record[k] for k in ('objects', 'pose_instances', 'quality')}})

    def snapshot(self, key, t, states):
        self.store.append_event(self.sid, 'ha.observation', {
            'observed_at': t, 'available_at': t + .5, 'valid_until': t + 3.0, 'ha': states, 'site_id': 'victoria',
            'camera_id': 'den', 'session_id': self.sid, 'evidence_roots': ['ha:' + key], 'snapshot_key': 'ha:' + key,
            'snapshot_digest': '0' * 64, 'quality': {'timing_basis': 'reconciled_snapshot_acquisition', 'source_id': 'primary'}})

    def record_requests(self):
        """A request per arm at every tick, carrying the shared context block
        the live scheduler records (service_runtime._shared_context)."""
        session = self.store.get_session(self.sid)
        grants = source_policy(None, ha=HA_EVIDENCE, caption=CAPTIONS)
        for k in range(1, DURATION + 1):
            cutoff = self.start + k
            rows = live_records(self.store, session, 1, cutoff, window_seconds=10, retain_history=True)
            blocks = shared_context(rows, cutoff, grants=grants, tz=zone(TIMEZONE), provisioned=True, bindings=BINDINGS,
                                    ha_provisioned=True, captions={}, fact_grants={f: True for f in BINDINGS.values()})
            for arm in ('a', 'b'):
                rid = 'req-%s-%d' % (arm, k)
                payload = {'cutoff': cutoff, 'arm': arm, 'generation': 1, 'window_seconds': 10, 'context_snapshot': blocks[arm],
                           'records': [r for r in rows if r['arm'] == arm and r['source_kind'] == 'ha.observation']}
                self.store.db.execute('INSERT INTO lab_reasoning_requests VALUES(?,?,?,?,?,?,?,?)',
                                      (rid, self.sid, arm, 1, 'completed', json.dumps(payload), cutoff, None))
                self.store.append_event(self.sid, 'reasoning.request', {
                    'request_id': rid, 'arm': arm, 'wire_version': wire.WIRE_VERSION,
                    'request': {'state': {'schema': wire.WIRE_VERSION, 'now': {}, 'devices': {}}}})

    def path(self):
        return str(Path(self.directory.name) / 'lab.sqlite3')

    def features(self, config=CONFIG, name='features.jsonl'):
        out = str(Path(self.directory.name) / name)
        view = facts.LedgerView(self.path())
        try:
            sessions = facts.write_features(view, [self.sid[:8]], out, config, 'lab.sqlite3')
        finally:
            view.close()
        return out, sessions

    def check_ticks(self, file, arm):
        head, emissions = lab_eval.read_ticks(file)   # lab_eval's own validation
        self.assertEqual((head['schema'], head['arm'], head['classes']), ('lab-ticks-1', arm, CLASSES))
        with open(file, encoding='utf-8') as stream:
            raw = [json.loads(line) for line in stream.read().splitlines()[1:]]
        for item in raw:
            self.assertEqual(sorted(item['probabilities']), sorted(CLASSES))
            self.assertAlmostEqual(sum(item['probabilities'].values()), 1.0, delta=1e-9)
            self.assertTrue(all(0 <= p <= 1 for p in item['probabilities'].values()))
            self.assertEqual(item['closed_at'], item['t'] + 1)
            self.assertLessEqual(item['t'], item['available_at'])
            self.assertIn('facts_key', item)
        return head, raw


class FeatureTests(FactsLedger):
    def test_features_per_tick_from_a_read_only_ledger(self):
        self.build(requests=False)
        db = self.ledger()
        before = db.execute('SELECT COUNT(*) FROM events').fetchone()[0], os.path.getmtime(self.path())
        db.close()
        out, sessions = self.features()
        db = self.ledger()
        self.assertEqual((db.execute('SELECT COUNT(*) FROM events').fetchone()[0], os.path.getmtime(self.path())), before)
        db.close()
        self.assertEqual(sessions[self.sid]['contexts'], 'synthesised')
        self.assertEqual(sessions[self.sid]['bindings'], 'config')
        self.assertEqual(sessions[self.sid]['occupancy_entity'], 'bound')
        records, meta = facts.read_features(out)
        self.assertEqual(len(records), 2 * DURATION)
        self.assertEqual(sorted({r['t'] for r in records}), [float(k) for k in range(1, DURATION + 1)])
        self.assertEqual(meta['vocabulary'], sorted({n for r in records for n in r['features']}))
        self.assertEqual((meta['source']['monitor_side'], meta['source']['desk_seat']), (None, None))
        at = {(r['arm'], r['t']): r for r in records}
        reading = at[('a', 5.0)]['features']
        for name in ('now.cue_state=book_near_hand', 'now.people_in_view=one', 'story.current=book_near_hand',
                     'devices.pc_input=under_15s', 'ha.light.den_lamp=on', 'occupancy=occupied', 'coverage.devices=supplied'):
            self.assertIn(name, reading)
        self.assertEqual((reading['raw.persons'], reading['raw.objects.book'], reading['raw.pose_present']), (1, 1, 1))
        self.assertNotIn('ha.sensor.pc_lastactive=2026-09-29t20:00:00+00:00', reading)  # timestamps never become words
        self.assertIn('now.cue_state=keyboard_or_mouse_near_hand', at[('a', 14.0)]['features'])
        self.assertIn('now.cue_state=phone_near_hand', at[('b', 5.0)]['features'])
        self.assertEqual(at[('a', 19.0)]['people'], 'none')
        self.assertNotEqual(at[('a', 5.0)]['facts_key'], at[('a', 14.0)]['facts_key'])

    def test_no_placement_or_head_word_ever_becomes_a_feature(self):
        self.build(requests=False)
        out, _ = self.features()
        _, meta = facts.read_features(out)
        for name in meta['vocabulary']:
            self.assertFalse(name.startswith(('now.head', 'now.position', 'story.head')), name)
            self.assertNotIn('head_toward', name)
        records, _ = facts.read_features(out)
        state = {'now': {'people_in_view': 'one', 'cue_state': 'person_no_hand_cue', 'head': {'yaw': 'left'}},
                 'support': {}, 'coverage': {}}
        self.assertNotIn('now.head.yaw=left', facts.state_features(state))
        for word in ('position', 'head_toward'):
            with self.assertRaises(ValueError):
                facts.state_features({**state, 'now': {**state['now'], word: 'desk_chair'}})
        with self.assertRaises(ValueError):
            facts.state_features({**state, 'storyline': {'segments': [{'state': 'book_near_hand', 'head_toward': 'monitor_side'}]}})


class TwinLedgerTests(unittest.TestCase):
    """The same session recorded with reasoning on (request rows carry the
    shared context) and off (none): the synthesised context path gives the
    same facts key at every tick and arm."""

    def keys(self, requests):
        ledger = FactsLedger('build')
        ledger.setUp()
        try:
            ledger.build(requests=requests)
            out, sessions = ledger.features()
            records, _ = facts.read_features(out)
            return [(r['arm'], r['t'], r['facts_key'], r['occupancy']) for r in records], sessions[ledger.sid]
        finally:
            ledger.tearDown()

    def test_facts_key_is_identical_with_and_without_request_rows(self):
        recorded, recorded_summary = self.keys(True)
        synthesised, synthesised_summary = self.keys(False)
        self.assertEqual((recorded_summary['contexts'], synthesised_summary['contexts']), ('recorded', 'synthesised'))
        self.assertEqual(len(recorded), 2 * DURATION)
        self.assertEqual(recorded, synthesised)
        self.assertGreater(len({k for _, _, k, _ in recorded}), 3)


class RulesTests(FactsLedger):
    def test_rules_write_valid_tick_records(self):
        self.build(requests=False)
        features_file, _ = self.features()
        out = str(Path(self.directory.name) / 'rules.jsonl')
        self.assertEqual(facts.run_rules(features_file, out), 2 * DURATION)
        head, emissions = self.check_ticks(out, 'E')
        self.assertEqual((head['calibration'], head['consumes_qwen']), ('none', True))   # hand-set weights, not calibrated
        self.assertIn('fixed rule table', head['source']['note'])
        at = {(e['arm'], e['t']): e['probabilities'] for e in emissions}
        self.assertEqual(max(at[('a', 5.0)], key=at[('a', 5.0)].get), 'reading')
        self.assertEqual(max(at[('a', 14.0)], key=at[('a', 14.0)].get), 'computer_work')
        self.assertEqual(max(at[('b', 5.0)], key=at[('b', 5.0)].get), 'phone_use')
        self.assertEqual(at[('a', 19.0)]['empty'], 1.0)
        self.assertGreater(at[('a', 5.0)]['abstain'], 0)

    def test_rule_table_edges(self):
        base = {'session_id': 's', 'arm': 'a', 't': 1.0, 'facts_key': 'k', 'occupancy': 'occupied', 'people': 'one'}
        self.assertEqual(facts.rule_distribution({**base, 'occupancy': 'vacant', 'features': {}})['empty'], 1.0)
        self.assertEqual(facts.rule_distribution({**base, 'people': 'multiple', 'features': {}})['abstain'], 1.0)
        self.assertEqual(facts.rule_distribution({**base, 'features': {'now.camera_evidence=none': 1}})['abstain'], 1.0)
        self.assertEqual(facts.rule_distribution({**base, 'features': {}})['abstain'], 1.0)
        stale = facts.rule_distribution({**base, 'features': {'qwen.latest.facts.activity_hypothesis=reading': 1}})
        self.assertEqual(stale['abstain'], 1.0)   # a Qwen word counts only while current

    def test_nobody_is_empty_only_on_a_current_frame(self):
        base = {'session_id': 's', 'arm': 'a', 't': 1.0, 'facts_key': 'k', 'occupancy': 'occupied', 'people': 'none'}
        current = {**base, 'features': {'now.camera_evidence=current': 1}}
        self.assertTrue(facts.empty_by_rule(current))
        self.assertEqual(facts.rule_distribution(current)['empty'], 1.0)
        for camera in ('expired_historical', 'none'):
            record = {**base, 'features': {'now.camera_evidence=%s' % camera: 1, 'now.cue_state=book_near_hand': 1}}
            self.assertFalse(facts.empty_by_rule(record))
            p = facts.rule_distribution(record)
            self.assertEqual((p['empty'], p['abstain']), (0.0, 1.0))   # a stale frame's 'nobody' is no answer
        vacant = {**base, 'occupancy': 'vacant', 'features': {'now.camera_evidence=expired_historical': 1}}
        self.assertTrue(facts.empty_by_rule(vacant))                   # the occupancy gate needs no frame


class FitTests(LedgerFixture):
    DAY = 86400.0

    def labelled(self, name, day, activities):
        self.now[0] = 1.7e9 + day * self.DAY
        sid, _ = self.session(name, 40)
        for i, (start, end, label) in enumerate(activities):
            self.label(sid, 'l%d' % i, start, end, label)
        return sid

    def synthetic(self, sids):
        """Two clear cue words per session; the last ticks read no person."""
        out = str(Path(self.directory.name) / 'synthetic.jsonl')
        records = []
        for sid in sids:
            for k in range(1, 41):
                for arm in ('a', 'b'):
                    empty = k > 36
                    cue = 'book_near_hand' if k <= 20 else 'keyboard_or_mouse_near_hand'
                    features = {'now.camera_evidence=current': 1}
                    if not empty:
                        features.update({'now.cue_state=%s' % cue: 1, 'raw.persons': 1})
                    records.append({'session_id': sid, 'arm': arm, 'generation': 1, 't': float(k), 'facts_key': 'k%d' % (k > 20),
                                    'occupancy': 'occupied', 'people': 'none' if empty else 'one', 'features': features})
        with open(out, 'w', encoding='utf-8') as stream:
            stream.writelines(json.dumps(r) + '\n' for r in records)
        with open(out + '.meta.json', 'w', encoding='utf-8') as stream:
            json.dump({'schema': facts.FEATURE_SCHEMA, 'vocabulary': sorted({n for r in records for n in r['features']}),
                       'sessions': {s: {} for s in sids}, 'source': {'synthetic': True}}, stream)
        return out

    def test_fit_leaves_one_day_out_and_writes_valid_tick_records(self):
        labels = [(0, 20, 'reading'), (20, 40, 'computer_work')]
        first = self.labelled('first', 0, labels)
        second = self.labelled('second', 1, labels)
        dev = self.labelled('dev', 1, labels)
        split = str(Path(self.directory.name) / 'split.json')
        with open(split, 'w', encoding='utf-8') as stream:
            json.dump({'schema': 'lab-split-1', 'frozen_at': 0, 'site_timezone': 'UTC', 'dev': {dev: ['rules']}}, stream)
        features_file = self.synthetic([first, second, dev])
        out = str(Path(self.directory.name) / 'fit.jsonl')
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            status = facts.main(['fit', '--features', features_file, '--ledger', str(Path(self.directory.name) / 'lab.sqlite3'),
                                 '--split', split, '--out', out])
        self.assertEqual(status, 0)
        self.assertEqual(buffer.getvalue().count('fold '), 2)
        head, emissions = FactsLedger.check_ticks(self, out, 'D')
        self.assertEqual((head['calibration'], head['consumes_qwen']), ('distribution', True))
        folds = {f['fold']: f for f in head['source']['folds']}
        self.assertEqual(len(folds), 2)
        self.assertEqual([(f['train_sessions'], f['test_sessions']) for f in folds.values()], [(2, 1), (1, 1)])
        self.assertEqual([f['temperature_fit'] for f in folds.values()], ['held_back', 'in_sample'])
        self.assertEqual({e['session_id'] for e in emissions}, {first, second})   # a dev session is never test
        self.assertEqual(len(emissions), 2 * 2 * 40)
        for e in emissions:
            p = e['probabilities']
            self.assertEqual(p['abstain'], 0.0)
            if e['t'] > 36:
                self.assertEqual(p['empty'], 1.0)
            else:
                self.assertEqual(p['empty'], 0.0)
                self.assertEqual(max(p, key=p.get), 'reading' if e['t'] <= 20 else 'computer_work')

    def test_temperature_is_fitted_on_the_latest_training_sessions(self):
        train = [({'session_id': sid, 't': float(k)}, 'reading') for sid in ('s1', 's2', 's3', 's4', 's5', 's6') for k in range(2)]
        started = {'s1': 50.0, 's2': 10.0, 's3': 60.0, 's4': 20.0, 's5': 30.0, 's6': 40.0}
        weights, temperature, how = facts.calibration_split(train, started)
        self.assertEqual(how, 'held_back')
        self.assertEqual({r['session_id'] for r, _ in temperature}, {'s1', 's3'})   # ceil(20% of 6) = 2 latest
        self.assertEqual({r['session_id'] for r, _ in weights}, {'s2', 's4', 's5', 's6'})
        single = [p for p in train if p[0]['session_id'] == 's1']
        self.assertEqual(facts.calibration_split(single, started), (single, single, 'in_sample'))

    def test_gold_labels_only(self):
        intervals = [{'session_id': 's', 'start': 0, 'end': 5, 'activity': 'reading', 'gold': True, 'provenance': 'owner_reviewed'},
                     {'session_id': 's', 'start': 5, 'end': 9, 'activity': 'phone_use', 'gold': False, 'provenance': 'qwen_proposed'},
                     {'session_id': 's', 'start': 9, 'end': 12, 'activity': 'empty_room', 'gold': True, 'provenance': 'owner_statement'}]
        labels = facts.gold_labels(intervals)
        self.assertEqual(labels, {'s': [(0, 5, 'reading'), (9, 12, 'empty')]})
        self.assertEqual(facts.truth_at(labels, {'session_id': 's', 't': 3.0}), 'reading')
        self.assertIsNone(facts.truth_at(labels, {'session_id': 's', 't': 6.0}))


if __name__ == '__main__':
    unittest.main()
