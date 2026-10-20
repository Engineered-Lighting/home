"""Bounded paid replay (jev_replay_calls): the controller's own provider and
policy on a recorded session, a separate replay database, the response cache,
the replay grant and the call cap. A fake transport; nothing leaves."""
import argparse
import json
from pathlib import Path
import sqlite3
import unittest

import jev_replay_calls as calls
import jev_wire as wire
from jev_evidence import QWEN_EGRESS
from test_jev_policy_v6 import BINDINGS, respond
from test_jev_replay import ReplayFidelityTests

POLICY = {'schema_version': 2, 'enabled': True, 'scope': 'household_structured', 'revision': 'r', 'generation': 1,
          'site_id': 'victoria', 'camera_ids': ['den'], 'model_selector': 'jev-1.13.0', 'max_calls_per_minute': 120,
          'session_seconds': 300, 'max_session_calls': 600, 'device_context': 'den-devices-1',
          'structured_sources': [QWEN_EGRESS, wire.WIRE_VERSION, calls.REPLAY_GRANT]}
REGION = {'edge': 'image_right', 'edge_margin_frac': .03, 'min_width_frac': .35, 'min_top_frac': .08}
CONFIG = {'schema_version': 1, 'policy_file': 'policy', 'credential_file': 'key', 'response_version': 'jev-1.13.0',
          'ha_bindings': BINDINGS, 'wire': wire.WIRE_VERSION}


class ReplayCallsTests(unittest.TestCase):
    setUp = ReplayFidelityTests.setUp
    tearDown = ReplayFidelityTests.tearDown
    output = ReplayFidelityTests.output
    record_session = ReplayFidelityTests.record_session

    def run_replay(self, questions, policy=POLICY, max_calls=10, dry_run=False, sent=None, **extra):
        sid = self.record_session()
        root = Path(self.directory.name)
        ledger = root / 'lab.sqlite3'
        before = ledger.read_bytes()
        files = {'config': CONFIG, 'policy': policy, 'region': REGION}
        sent = [] if sent is None else sent

        def transport(body, token, timeout):
            sent.append(json.loads(body))
            return respond(body)
        args = argparse.Namespace(ledger=str(ledger), replay_db=str(root / 'replay.sqlite3'), config='config',
                                  session=sid[:8], questions=questions, arms='a,b', max_calls=max_calls, dry_run=dry_run,
                                  repeat=0, **extra)
        hooks = {'private_json': lambda name: json.loads(json.dumps(files[name])), 'transport': transport,
                 'credential': lambda: 'fixture'}
        try:
            result = calls.run(args, hooks)
        finally:
            self.assertEqual(ledger.read_bytes(), before, 'the ledger is never written')
        return result, sent, args

    def test_v7_replay_is_admitted_by_the_policy_cached_and_fused(self):
        result, sent, args = self.run_replay('v7')
        self.assertEqual((result['requests'], result['calls'], result['cache_hits']), (2, 2, 0))
        self.assertEqual(result['admissions_in_replay_db'], 2)
        self.assertEqual(result['question_version'], wire.QUESTION_VERSION_V7)
        self.assertTrue(all(p['questions'] == wire.QUESTIONS_V7 for p in sent))
        self.assertTrue(all('evidence' not in p['state'] for p in sent), 'facts-only wire')
        db = sqlite3.connect(args.replay_db)
        rows = db.execute('SELECT source, status, result FROM replay_results').fetchall()
        self.assertEqual([r[:2] for r in rows], [('call', 'abstain')] * 2)
        fusion = json.loads(rows[0][2])['diagnostics']['fusion']
        # The fake says reading 0.7 with every Noul 0.2: the book gate vetoes it.
        self.assertEqual(fusion['gate'], {'activity': 'reading', 'passed': False, 'reason': 'book_not_attended'})
        self.assertEqual(db.execute('SELECT count(*) FROM lab_jev_egress').fetchone()[0], 2)
        self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='reasoning.request'").fetchone()[0], 2)
        db.close()
        # A second run re-uses the cached responses: no new call or admission.
        again = calls.run(args, {'private_json': lambda name: json.loads(json.dumps({'config': CONFIG, 'policy': POLICY}[name])),
                                 'transport': lambda *a: self.fail('sent again'), 'credential': lambda: 'fixture'})
        self.assertEqual((again['calls'], again['cache_hits'], again['admissions_in_replay_db']), (0, 2, 2))

    def test_without_the_replay_grant_nothing_is_sent(self):
        policy = dict(POLICY, structured_sources=[QWEN_EGRESS, wire.WIRE_VERSION])
        sent = []
        with self.assertRaisesRegex(ValueError, 'household policy unavailable'):
            self.run_replay('v6', policy=policy, sent=sent)
        self.assertEqual(sent, [])

    def test_disabled_policy_and_call_cap(self):
        sent = []
        with self.assertRaisesRegex(ValueError, 'household policy unavailable'):
            self.run_replay('v6', policy=dict(POLICY, enabled=False), sent=sent)
        self.assertEqual(sent, [])
        self.tearDown()
        self.setUp()
        result, sent, _ = self.run_replay('v6', max_calls=1)
        self.assertEqual((len(sent), result['calls'], result.get('stopped')), (1, 1, 'call_cap'))

    def test_repeatability_probe_resends_cached_packets_through_the_policy(self):
        result, sent, args = self.run_replay('v6')
        self.assertEqual(result['calls'], 2)
        args.repeat = 1
        resent = []

        def transport(body, token, timeout):
            resent.append(body)
            return respond(body)
        probe = calls.run(args, {'private_json': lambda name: json.loads(json.dumps({'config': CONFIG, 'policy': POLICY}[name])),
                                 'transport': transport, 'credential': lambda: 'fixture'})
        self.assertEqual((len(resent), probe['calls'], probe['cache_hits'], probe['admissions_in_replay_db']), (1, 1, 0, 3))
        db = sqlite3.connect(args.replay_db)
        self.assertEqual(db.execute('SELECT count(*) FROM replay_cache').fetchone()[0], 2)
        self.assertEqual(db.execute("SELECT count(*) FROM replay_results WHERE source='repeat'").fetchone()[0], 1)
        db.close()

    def test_dry_run_verifies_without_admission_or_transport(self):
        result, sent, _ = self.run_replay('v7', dry_run=True)
        self.assertEqual((sent, result['calls'], result['dry_run_built'], result['admissions_in_replay_db']), ([], 0, 2, 0))

    def test_den_facts_7_replay_needs_its_grant_and_a_region(self):
        sent = []
        result, sent, _ = self.run_replay('v6', sent=sent, wire=wire.WIRE_VERSION_7, desk_seat='region')
        self.assertEqual((sent, result['calls'], result['failed'], result['wire_version']), ([], 2, 2, wire.WIRE_VERSION_7))
        self.tearDown()
        self.setUp()
        policy = dict(POLICY, structured_sources=POLICY['structured_sources'] + [wire.WIRE_VERSION_7])
        result, sent, args = self.run_replay('v6', policy=policy, wire=wire.WIRE_VERSION_7, desk_seat='region')
        self.assertEqual((result['calls'], result['failed'], len(sent)), (2, 0, 2))
        self.assertTrue(all(p['state']['schema'] == wire.WIRE_VERSION_7 and 'position' in p['state']['now'] for p in sent))
        self.tearDown()
        self.setUp()
        with self.assertRaisesRegex(SystemExit, 'desk_seat'):
            self.run_replay('v6', policy=policy, wire=wire.WIRE_VERSION_7)

    def test_a_session_time_window_limits_calls_not_the_ring(self):
        result, sent, _ = self.run_replay('v6', from_s=1e9)
        self.assertEqual((result['requests'], result['calls'], sent), (2, 0, []))

    def test_replay_policy_shape(self):
        policy = calls.replay_policy(POLICY, now=1000.0, job='j')
        self.assertEqual(policy['schema_version'], 1)
        self.assertEqual(policy['expires_at'], 1000.0 + calls.REPLAY_POLICY_SECONDS)
        self.assertTrue(policy['revision'].endswith(':replay:j'))
        self.assertNotIn('session_seconds', policy)
        with self.assertRaises(ValueError):
            calls.replay_policy(dict(POLICY, structured_sources=[wire.WIRE_VERSION]), now=1000.0, job='j')


if __name__ == '__main__':
    unittest.main()
