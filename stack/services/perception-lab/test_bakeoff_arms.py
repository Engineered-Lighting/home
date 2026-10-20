"""Bake-off adapters: stored V-JEPA probabilities (arm C) and JEV answers
(arm A, live and replay) written as lab-ticks-1 records from a read-only
ledger; the ledger is never written."""
import json
import os
from pathlib import Path
import sqlite3
import unittest

import bakeoff_jev as jev
import bakeoff_vjepa as vjepa
import lab_eval
from test_lab_regression import LedgerFixture, answer

CLASSES = ['reading', 'computer_work', 'phone_use', 'resting', 'transition', 'other', 'empty', 'abstain']


def posture(**values):
    full = {k: 0.0 for k in vjepa.scene.POSTURES}
    full.update(values)
    return full


def result(posture_values, zero_shot, reading=0.5):
    return {'posture': posture_values, 'reading': reading, 'zero_shot': zero_shot,
            'zero_shot_status': 'supplied' if zero_shot is not None else 'single_person_gate_not_met',
            'motion': 0.01, 'scene_drift': None}


class BakeoffFixture(LedgerFixture):
    def setUp(self):
        super().setUp()
        self.sid, self.start = self.session('bakeoff')
        self.out = str(Path(self.directory.name) / 'ticks.jsonl')

    def observation(self, t, outcome, *, status='valid', available=0.4, request_id=None):
        self.store.append_event(self.sid, 'vjepa.observation', {
            'request_id': request_id or 'r%s' % t, 'observed_at': self.start + t, 'available_at': self.start + t + available,
            'valid_until': self.start + t + vjepa.scene.MAX_AGE_SECONDS, 'status': status,
            'result': outcome if status == 'valid' else None})

    def ledger_snapshot(self):
        db = self.ledger()
        try:
            counts = tuple(db.execute('SELECT COUNT(*) FROM %s' % table).fetchone()[0] for table in ('events', 'lab_reasoning_requests'))
        finally:
            db.close()
        return counts, os.path.getmtime(Path(self.directory.name) / 'lab.sqlite3')

    def check_records(self, head, records, arm):
        self.assertEqual((head['schema'], head['arm'], head['classes']), ('lab-ticks-1', arm, CLASSES))
        self.assertIn(head['calibration'], ('distribution', 'none'))
        self.assertIsInstance(head['consumes_qwen'], bool)
        for record in records:
            self.assertEqual(record['session_id'], self.sid)
            self.assertIn(record['arm'], ('a', 'b'))
            self.assertEqual(sorted(record['probabilities']), sorted(CLASSES))
            self.assertTrue(all(0 <= p <= 1 for p in record['probabilities'].values()))
            self.assertAlmostEqual(sum(record['probabilities'].values()), 1.0, delta=0.01)
            self.assertLessEqual(record['t'], record['available_at'])
        # The consumer's own validator reads the same file.
        self.assertEqual(lab_eval.read_ticks(self.out), (head, records))


class VjepaTicksTests(BakeoffFixture):
    def test_distribution_rules(self):
        d = vjepa.distribution(result(posture(sitting=.8, standing=.2), {'reading': .6, 'computer_work': .1, 'phone_use': .05}))
        self.assertAlmostEqual(d['reading'], .6)
        self.assertAlmostEqual(d['transition'], .1)      # 0.5 * standing
        self.assertEqual(d['resting'], 0.0)              # a zero-shot head reaches 0.3
        self.assertAlmostEqual(d['abstain'], 1 - .6 - .1 - .05 - .1)
        self.assertEqual((d['other'], d['empty']), (0.0, 0.0))
        self.assertAlmostEqual(sum(d.values()), 1.0)
        low = vjepa.distribution(result(posture(sitting=.5, walking=.1), {'reading': .2, 'computer_work': .1, 'phone_use': .0}))
        self.assertAlmostEqual(low['resting'], .5 * (1 - .2))
        self.assertAlmostEqual(low['transition'], .1)
        self.assertAlmostEqual(low['abstain'], .2)
        self.assertAlmostEqual(sum(low.values()), 1.0)
        # Masses above 1 are normalised, never clipped away.
        over = vjepa.distribution(result(posture(walking=1.0), {'reading': 1.0, 'computer_work': 1.0, 'phone_use': 1.0}))
        self.assertAlmostEqual(sum(over.values()), 1.0)
        self.assertEqual(over['abstain'], 0.0)
        gated = vjepa.distribution(result(posture(sitting=1.0), None))
        self.assertEqual(gated['abstain'], 1.0)

    def test_writes_ticks_from_valid_observations_only_and_never_writes_the_ledger(self):
        self.observation(10.0, result(posture(sitting=.8, standing=.2), {'reading': .6, 'computer_work': .1, 'phone_use': .05}))
        self.observation(14.0, result(posture(sitting=1.0), None))
        self.observation(18.0, None, status='invalid')
        self.store.append_event(self.sid, 'vjepa.observation', {'request_id': 'broken', 'status': 'valid', 'result': {}})
        before = self.ledger_snapshot()
        db = self.ledger()
        try:
            report = vjepa.run(db, [self.sid[:8]], self.out)
        finally:
            db.close()
        self.assertEqual(report, {self.sid: {'valid': 2, 'invalid': 1, 'skipped': 1}})
        self.assertEqual(self.ledger_snapshot(), before)
        head, records = lab_eval.read_ticks(self.out)
        self.check_records(head, records, 'C')
        self.assertFalse(head['consumes_qwen'])
        self.assertEqual(head['calibration'], 'distribution')
        self.assertEqual(head['source']['model_version'], vjepa.scene.MODEL_VERSION)
        self.assertEqual(head['source']['sessions'], [self.sid])
        self.assertEqual(len(records), 4)  # a shared record, once per arm
        first = [r for r in records if r['t'] == 10.0 and r['arm'] == 'a'][0]
        self.assertAlmostEqual(first['available_at'], 10.4)
        self.assertEqual(first['closed_at'], 16.0)
        self.assertAlmostEqual(first['probabilities']['reading'], .6)
        gated = [r for r in records if r['t'] == 14.0][0]
        self.assertEqual(gated['probabilities']['abstain'], 1.0)

    def test_cli_prints_counts(self):
        import io
        from contextlib import redirect_stdout
        self.observation(10.0, result(posture(sitting=1.0), None))
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            status = vjepa.main(['--ledger', str(Path(self.directory.name) / 'lab.sqlite3'), '--session', self.sid[:8], '--out', self.out])
        self.assertEqual(status, 0)
        self.assertIn('valid 1 invalid 0 skipped 0', buffer.getvalue())


class JevLiveTicksTests(BakeoffFixture):
    def output(self, rid, arm, t, probabilities, *, delay=1.0, valid=3.0, status='ready', wire='den-facts-7', hold=None):
        activity = max(probabilities, key=probabilities.get)
        self.store.db.execute('INSERT INTO lab_reasoning_requests VALUES(?,?,?,?,?,?,?,?)', (
            rid, self.sid, arm, 0, 'completed', json.dumps({'cutoff': self.start + t, 'records': []}), self.start + t, None))
        self.store.append_event(self.sid, 'reasoning.request', {'request_id': rid, 'arm': arm, 'wire_version': wire})
        prediction = answer(t, t + valid, None if status == 'abstain' else activity, available=t + delay)
        extra = {}
        if hold is not None:   # live_reasoning._timeline: valid_through = available_at + hold_max
            prediction['valid_through'] = t + delay + hold
            extra = {'facts_key': 'k' + rid, 'validity': {'rule': 'facts_key', 'hold_max_s': hold}}
        self.store.append_event(self.sid, 'reasoning.output', {
            'request_id': rid, 'arm': arm, 'mode': 'live', 'provider_status': status, 'generation': 0,
            'judgment': {'activity': activity, 'probabilities': probabilities}, 'prediction': prediction, **extra})

    def gate(self, arm, t, reason, hold=12.0):
        # live_reasoning.local_abstain's reasoning.gate shape.
        self.store.append_event(self.sid, 'reasoning.gate', {
            'arm': arm, 'generation': 0, 'mode': 'live', 'provider': 'code_gate', 'status': 'abstain', 'reason': reason,
            'choice': 'insufficient_evidence', 'available_at': self.start + t, 'available_at_s': t, 'source_end_s': t,
            'prediction': {'time_basis': 'session_relative_seconds', 'start': t, 'end': t + hold, 'available_at': t,
                           'valid_until': t + hold, 'activities': [], 'confidence': None, 'mode': 'live'}})

    def closure(self, rid, arm, through):
        self.store.append_event(self.sid, 'reasoning.validity', {'request_id': rid, 'arm': arm, 'reason': 'facts_changed',
                                                                 'valid_through': through, 'hold_max_s': 12})

    def test_live_outputs_become_ticks_bounded_as_facts_bound(self):
        self.output('r1', 'a', 10.0, {'reading': .7, 'insufficient_evidence': .2, 'other': .1}, hold=12.0)
        self.output('r2', 'a', 20.0, {'insufficient_evidence': 1.0}, status='abstain')
        self.output('r3', 'b', 30.0, {'computer_work': .5, 'phone_use': .5, 'unknown_class': .3, 'empty': .2}, hold=12.0)
        self.output('r4', 'a', 40.0, {'reading': 1.0}, hold=12.0)
        self.closure('r1', 'a', 12.5)   # earlier than r1's strict valid_until (13.0)
        self.closure('r4', 'a', 45.0)
        self.store.append_event(self.sid, 'reasoning.output', {'request_id': 'r9', 'arm': 'a', 'mode': 'replay',
                                                               'prediction': answer(70, 73, 'reading')})
        before = self.ledger_snapshot()
        db = self.ledger()
        try:
            counts = jev.run(db, [self.sid[:8]], self.out, live=True)
        finally:
            db.close()
        self.assertEqual(counts, {self.sid: {'emitted': 4, 'gated': 0, 'skipped': 1}})
        self.assertEqual(self.ledger_snapshot(), before)
        head, records = lab_eval.read_ticks(self.out)
        self.check_records(head, records, 'A')
        self.assertTrue(head['consumes_qwen'])  # no activation recorded: granted by default
        self.assertEqual(head['run'], 'jev-den-facts-7')
        by_t = {r['t']: r for r in records}
        self.assertEqual((by_t[10.0]['available_at'], by_t[10.0]['closed_at']), (11.0, 13.0))  # never below valid_until
        self.assertEqual(by_t[10.0]['facts_key'], 'kr1')
        self.assertAlmostEqual(by_t[10.0]['probabilities']['abstain'], .2)
        self.assertEqual(by_t[20.0]['closed_at'], 23.0)    # no valid_through: the strict valid_until
        self.assertNotIn('facts_key', by_t[20.0])
        self.assertEqual(by_t[20.0]['probabilities']['abstain'], 1.0)
        self.assertEqual(by_t[30.0]['closed_at'], 31.0 + 12)  # valid_through, no closure: available_at + 12
        self.assertAlmostEqual(by_t[30.0]['probabilities']['computer_work'], .5)  # unknown classes are dropped
        self.assertEqual(by_t[30.0]['probabilities']['empty'], 0.0)              # JEV never answers 'empty'
        self.assertEqual(by_t[30.0]['arm'], 'b')
        self.assertEqual(by_t[40.0]['closed_at'], 45.0)    # a closure inside the hold cuts it

    def test_local_gate_answers_are_empty_or_abstain(self):
        self.gate('a', 50.0, 'occupancy_vacant')
        self.gate('b', 60.0, 'occupancy_unknown')
        self.gate('a', 70.0, 'something_else')
        db = self.ledger()
        try:
            counts = jev.run(db, [self.sid[:8]], self.out, live=True)
        finally:
            db.close()
        self.assertEqual(counts, {self.sid: {'emitted': 0, 'gated': 2, 'skipped': 1}})
        head, records = lab_eval.read_ticks(self.out)
        self.check_records(head, records, 'A')
        by_t = {r['t']: r for r in records}
        self.assertEqual((by_t[50.0]['arm'], by_t[50.0]['available_at'], by_t[50.0]['closed_at']), ('a', 50.0, 62.0))
        self.assertEqual(by_t[50.0]['probabilities']['empty'], 1.0)
        self.assertEqual((by_t[60.0]['arm'], by_t[60.0]['closed_at']), ('b', 72.0))
        self.assertEqual(by_t[60.0]['probabilities']['abstain'], 1.0)
        self.assertEqual(by_t[60.0]['probabilities']['empty'], 0.0)

    def test_qwen_grant_and_reference_arm_come_from_the_activation_and_flag(self):
        self.store.append_event(self.sid, 'experiment.activated', {
            'generation': 1, 'source_policy': {'qwen_scene': {'a': 'withheld_by_experiment', 'b': 'granted'}}})
        self.output('r1', 'a', 10.0, {'reading': 1.0})
        db = self.ledger()
        try:
            jev.run(db, [self.sid[:8]], self.out, live=True, reference=True)
        finally:
            db.close()
        head, records = lab_eval.read_ticks(self.out)
        self.check_records(head, records, 'reference')
        self.assertFalse(head['consumes_qwen'])  # only arm a emitted, and a is withheld


class JevReplayTicksTests(BakeoffFixture):
    def setUp(self):
        super().setUp()
        self.replay_db = str(Path(self.directory.name) / 'replay.sqlite3')
        db = sqlite3.connect(self.replay_db)
        db.execute('''CREATE TABLE replay_results(job TEXT NOT NULL, recorded_request_id TEXT NOT NULL,
            question_version TEXT NOT NULL, arm TEXT NOT NULL, cutoff REAL NOT NULL, packet_sha256 TEXT,
            source TEXT NOT NULL, status TEXT NOT NULL, result TEXT, PRIMARY KEY(job, recorded_request_id))''')
        db.commit()
        db.close()

    def replay_row(self, job, rid, arm, cutoff, status, result):
        db = sqlite3.connect(self.replay_db)
        db.execute('INSERT INTO replay_results VALUES(?,?,?,?,?,?,?,?,?)',
                   (job, rid, 'v7', arm, cutoff, 'p' * 64, 'call', status, json.dumps(result) if result is not None else None))
        db.commit()
        db.close()

    def test_replay_rows_are_timed_by_the_live_latency_and_closed_by_the_hold(self):
        # Two live answers at 1.5 s and 2.5 s latency: the median is 2.0 s.
        self.request(self.sid, self.start, 'r1', 'a', 10.0, 'reading', delay=1.5)
        self.request(self.sid, self.start, 'r2', 'a', 20.0, 'reading', delay=2.5)
        self.request(self.sid, self.start, 'r3', 'a', 30.0, False)
        diagnostics = {'request': {'state': {'schema': 'den-facts-8'}}}
        self.replay_row('job1', 'r1', 'a', self.start + 10.0, 'ready',
                        {'status': 'ready', 'judgment': {'activity': 'reading', 'probabilities': {'reading': .9, 'insufficient_evidence': .1}},
                         'diagnostics': diagnostics})
        self.replay_row('job1', 'r2', 'a', self.start + 20.0, 'abstain', {'status': 'abstain', 'judgment': {}, 'diagnostics': diagnostics})
        self.replay_row('job1', 'r3', 'a', self.start + 30.0, 'failed', None)
        self.replay_row('job1', 'other-session', 'a', 5.0, 'ready', {'status': 'ready', 'judgment': {'probabilities': {'reading': 1}}})
        self.replay_row('job2', 'r1', 'a', self.start + 10.0, 'ready', {'status': 'ready', 'judgment': {'probabilities': {'other': 1}}})
        before = self.ledger_snapshot()
        replay_before = os.path.getmtime(self.replay_db)
        db = self.ledger()
        try:
            counts = jev.run(db, [self.sid[:8]], self.out, replay_db=self.replay_db, job='job1')
        finally:
            db.close()
        self.assertEqual(counts, {self.sid: {'emitted': 2, 'skipped': 1}})
        self.assertEqual(self.ledger_snapshot(), before)
        self.assertEqual(os.path.getmtime(self.replay_db), replay_before)
        head, records = lab_eval.read_ticks(self.out)
        self.check_records(head, records, 'A')
        self.assertEqual(head['run'], 'jev-den-facts-8')
        self.assertEqual(head['source']['job'], 'job1')
        self.assertEqual(head['source']['latency_s'], {self.sid: 2.0})
        self.assertIn('not known for replays', head['source']['closures'])
        self.assertEqual([(r['t'], r['available_at'], r['closed_at']) for r in records], [(10.0, 12.0, 24.0), (20.0, 22.0, 34.0)])
        self.assertAlmostEqual(records[0]['probabilities']['reading'], .9)
        self.assertEqual(records[1]['probabilities']['abstain'], 1.0)

    def test_no_live_answers_uses_the_default_latency(self):
        self.request(self.sid, self.start, 'r1', 'b', 10.0, False)
        self.replay_row('job1', 'r1', 'b', self.start + 10.0, 'ready', {'status': 'ready', 'judgment': {'probabilities': {'phone_use': 1}}})
        db = self.ledger()
        try:
            jev.run(db, [self.sid[:8]], self.out, replay_db=self.replay_db, job='job1')
        finally:
            db.close()
        head, records = lab_eval.read_ticks(self.out)
        self.assertEqual(head['run'], 'jev')  # no diagnostics: no wire in the name
        self.assertEqual((records[0]['arm'], records[0]['available_at']), ('b', 10.0 + jev.DEFAULT_LATENCY_S))

    def test_refuses_an_unknown_job_and_an_ambiguous_source(self):
        db = self.ledger()
        try:
            with self.assertRaises(SystemExit):
                jev.run(db, [self.sid[:8]], self.out, replay_db=self.replay_db, job='missing')
        finally:
            db.close()
        with self.assertRaises(SystemExit):
            jev.main(['--ledger', str(Path(self.directory.name) / 'lab.sqlite3'), '--session', self.sid[:8], '--out', self.out])


if __name__ == '__main__':
    unittest.main()
