"""Regression set, per-activity scoring, ship gate, bounded replay driver and
the controller's automatic results after a recording."""
import argparse
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from config import Settings
from controller import AUTO_RESULTS_INTERVAL_S, Controller
import lab_regression as regression
import lab_scoring as scoring
import live_inputs
from reasoning import score_intervals
from store import Store


def answer(start, end, activity, *, available=None, valid=None, p=0.9):
    return {'start': start, 'end': end, 'available_at': start if available is None else available,
            'valid_until': end if valid is None else valid, 'activities': [] if activity is None else [activity],
            'confidence': p, 'mode': 'live', 'time_basis': 'session_relative_seconds'}


def scored(labels, answers, **kw):
    rows = scoring.accumulate(labels, [scoring.prediction(a) for a in answers], **kw)
    return {k: scoring.metrics(v, k) for k, v in rows.items()}


class ScoringTests(unittest.TestCase):
    def test_matches_the_findings_scorer_per_activity(self):
        labels = [{'start': 0, 'end': 20, 'activity': 'reading'}, {'start': 20, 'end': 50, 'activity': 'phone_use'},
                  {'start': 55, 'end': 80, 'activity': 'computer_work'}]
        answers = [answer(1, 6, 'reading', available=2), answer(5, 14, 'phone_use', p=.95), answer(14, 30, 'reading', valid=22),
                   answer(31, 45, 'phone_use', p=.5), answer(44, 60, 'insufficient_evidence'), answer(60, 90, 'computer_work', available=63),
                   answer(70, 75, 'reading', p=.85)]
        mine = scored(labels, answers)
        reference = score_intervals([{'start': l['start'], 'end': l['end'], 'activities': [l['activity']], 'reviewed': True}
                                     for l in labels], answers)['per_activity']
        for key, row in reference.items():
            self.assertAlmostEqual(mine[key]['coverage'], row['coverage'], places=3)
            self.assertAlmostEqual(mine[key]['exact_at_coverage'] or 0, row['exact_match_at_coverage'] or 0, places=3)
            self.assertAlmostEqual(mine[key]['false_confident_s'], row['false_confident_seconds'], places=3)

    def test_abstention_no_answer_confusion_false_positives_and_first_correct(self):
        labels = [{'start': 0, 'end': 10, 'activity': 'reading'}, {'start': 10, 'end': 20, 'activity': 'phone_use'}]
        answers = [answer(0, 4, 'insufficient_evidence'), answer(4, 12, 'reading'), answer(12, 15, None),
                   answer(15, 20, 'phone_use', available=17)]
        m = scored(labels, answers)
        self.assertEqual(m['reading']['abstention'], .4)
        self.assertEqual(m['reading']['exact_at_coverage'], 1)
        self.assertEqual(m['reading']['ttfc_median_s'], 4)
        phone = m['phone_use']
        self.assertEqual(phone['confusion'], {'abstain': 3.0, 'no_answer': 2.0, 'phone_use': 3.0, 'reading': 2.0})
        self.assertEqual(phone['false_positive_s'], {'reading': 2.0})
        self.assertEqual(phone['false_confident_s'], 2.0)  # reading at p 0.9 inside phone use
        self.assertEqual(phone['ttfc_median_s'], 7)  # 10 -> 17, when the right answer arrived
        self.assertEqual((phone['boundaries'], phone['missed_boundaries']), (1, 0))

    def test_the_empty_room_accepts_other_and_counts_activities_as_false_positives(self):
        self.assertEqual(scoring.scoring_key('other', 'room empty'), scoring.EMPTY_ROOM)
        self.assertEqual(scoring.scoring_key('other', 'holding closed book'), 'other')
        m = scored([{'start': 0, 'end': 10, 'activity': scoring.EMPTY_ROOM}],
                   [answer(0, 5, 'other'), answer(5, 10, 'resting', p=.85)])[scoring.EMPTY_ROOM]
        self.assertEqual((m['exact_at_coverage'], m['false_positive_s'], m['false_confident_s']), (.5, {'resting': 5.0}, 5.0))

    def test_support_restricts_time_and_boundaries(self):
        labels = [{'start': 0, 'end': 20, 'activity': 'reading'}]
        m = scored(labels, [answer(0, 20, 'reading')], support=[(5.0, 10.0)])['reading']
        self.assertEqual((m['labelled_s'], m['boundaries']), (5.0, 0))
        self.assertEqual(scoring.spans([10, 12, 40], max_gap_s=15, tail_s=5), [(10, 17), (40, 45)])
        self.assertEqual(scoring.intersect([(0, 10), (20, 30)], [(5, 25)]), [(5, 10), (20, 25)])
        self.assertEqual(scoring.intersect(None, [(1, 2)]), [(1, 2)])

    def test_draft_labels_from_bookmarks(self):
        drafts = scoring.draft_labels([(40, 'Review this moment', 3), (5, 'reading', 1), (20, 'phone', 2),
                                       (60, '6 empty room', 4), (61, 'computer', 5)], 100)
        self.assertEqual([(d['start'], d['end'], d['activity'], d['label']) for d in drafts],
                         [(5, 20, 'reading', 'reading'), (20, 40, 'phone_use', 'phone_use'), (61, 100, 'computer_work', 'computer_work')])
        self.assertTrue(all(d['reviewed'] is False and d['provenance'] == 'bookmark_draft' for d in drafts))
        for text, activity in (('2 holding closed book', 'other'), ('5 resting / TV', 'resting'), ('4 computer work', 'computer_work'),
                               ('getting up / leaving', 'transition'), ('6 empty room', 'empty_room'), ('hmm', None)):
            self.assertEqual(scoring.bookmark_activity(text), activity, text)


class LedgerFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.now = [1000.0]
        self.store = Store(Settings(storage_root=Path(self.directory.name), min_free_bytes=0), clock=lambda: self.now[0])
        self.store.db.execute('''CREATE TABLE IF NOT EXISTS lab_reasoning_requests(id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            arm TEXT NOT NULL, generation INTEGER NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL, dispatched_at REAL, result TEXT)''')

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def session(self, name, duration=100.0):
        session, _ = self.store.create_session('den', 'detection', name, 'r-' + name)
        self.store.transition(session['id'], 'starting')
        self.store.transition(session['id'], 'recording')
        start = self.now[0]
        self.now[0] += duration
        self.store.transition(session['id'], 'stopped', 'user_stop')
        return session['id'], start

    def label(self, sid, aid, start, end, label, *, reviewed=True, notes=''):
        self.store.save_annotation(sid, aid, {'at_ms': start * 1000, 'end_ms': end * 1000, 'label': label, 'origin': 'human',
                                              'reviewed': reviewed, 'notes': notes}, 0, 'owner')

    def request(self, sid, start, rid, arm, t, activity, *, wire='den-facts-6', delay=1.0, valid=3.0):
        self.store.db.execute('INSERT INTO lab_reasoning_requests VALUES(?,?,?,?,?,?,?,?)', (
            rid, sid, arm, 0, 'completed', json.dumps({'cutoff': start + t, 'records': [
                {'source_kind': 'perception.output', 'objects': [{}], 'observed_at': start + t, 'valid_until': start + t + valid}]}),
            start + t, None))
        self.store.append_event(sid, 'reasoning.request', {'request_id': rid, 'arm': arm, 'wire_version': wire})
        if activity is not False:
            self.store.append_event(sid, 'reasoning.output', {'request_id': rid, 'arm': arm, 'mode': 'live', 'run_id': 'jev' + arm.upper(),
                                                              'generation': 0, 'prediction': answer(t, t + valid, activity, available=t + delay)})

    def ledger(self):
        return regression.open_ro(str(Path(self.directory.name) / 'lab.sqlite3'))


class RegressionSetTests(LedgerFixture):
    def test_gathers_labels_with_provenance_manifest_and_exclusions(self):
        first, _ = self.session('guided')
        self.store.append_event(first, 'bookmark', {'at_ms': 1000, 'label': '1 reading'})
        self.label(first, 'a1', 3, 20, 'reading', reviewed=False)
        self.label(first, 'a2', 20, 40, 'phone_use', notes='phone in hands (Qwen 8/9)')
        self.label(first, 'a3', 45, 60, 'other', notes='room empty')
        self.label(first, 'a4', 60, 70, 'computer_work', notes='owner confirmed')
        self.label(first, 'a5', 70, 75, 'insufficient_evidence')
        unguided, _ = self.session('unguided', 50)
        manifest = {'schema': regression.MANIFEST_SCHEMA, 'labels': [{'session_id': unguided, 'activity': 'computer_work', 'end_s': None}]}
        db = self.ledger()
        sessions, intervals, excluded = regression.gather(db, manifest)
        found = {(x['activity'], x['provenance'], x['reviewed']) for x in intervals}
        self.assertEqual(found, {('reading', 'bookmark', False), ('phone_use', 'qwen_proposed', True),
                                 ('empty_room', 'owner_reviewed', True), ('computer_work', 'owner_reviewed', True),
                                 ('computer_work', 'owner_statement', False)})
        self.assertEqual([x['reason'] for x in excluded], ['not an activity'])
        table = regression.coverage(intervals)
        self.assertEqual((table['computer_work']['seconds'], table['computer_work']['sessions']), (60.0, 2))
        self.assertTrue(table['reading']['low_confidence'])
        self.assertFalse(table['computer_work']['low_confidence'])
        with self.assertRaises(SystemExit):
            regression.gather(db, {**manifest, 'labels': [{'session_id': unguided, 'activity': 'dancing'}]})
        db.close()


class GateTests(LedgerFixture):
    def build(self):
        self.a, start_a = self.session('a', 60)
        self.b, start_b = self.session('b', 60)
        for sid in (self.a, self.b):
            self.label(sid, 'l1', 0, 30, 'computer_work')
            self.label(sid, 'l2', 30, 60, 'other', notes='room empty')
        n = 0
        for sid, start in ((self.a, start_a), (self.b, start_b)):
            for t in range(0, 60, 2):
                n += 1
                self.request(sid, start, 'r%d' % n, 'a', t, 'computer_work' if t < 30 else 'other', valid=2.5)
        self.starts = {self.a: start_a, self.b: start_b}

    def replay_db(self, name, change=lambda sid, t, activity: activity, *, job='job-x', wire='den-facts-7', source='call',
                  questions='q', p=.9):
        file = Path(self.directory.name) / name
        db = sqlite3.connect(file)
        db.execute('''CREATE TABLE IF NOT EXISTS replay_results(job TEXT NOT NULL, recorded_request_id TEXT NOT NULL, question_version TEXT NOT NULL,
            arm TEXT NOT NULL, cutoff REAL NOT NULL, packet_sha256 TEXT, source TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
            PRIMARY KEY(job, recorded_request_id))''')
        for rid, sid, arm, raw in self.store.db.execute('SELECT id,session_id,arm,payload FROM lab_reasoning_requests'):
            cutoff = json.loads(raw)['cutoff']
            t = cutoff - self.starts[sid]
            activity = change(sid, t, 'computer_work' if t < 30 else 'other')
            result = {'status': 'abstain' if activity is None else 'ready', 'judgment': {
                'activity': activity or 'insufficient_evidence', 'probabilities': {activity or 'insufficient_evidence': p}},
                'diagnostics': {'request': {'state': {'schema': wire}}}}
            db.execute('INSERT INTO replay_results VALUES(?,?,?,?,?,?,?,?,?)', (job, rid, questions, arm, cutoff, None, source,
                                                                                result['status'], json.dumps(result)))
        db.commit()
        db.close()
        return str(file)

    def run_gate(self, candidate_db, tolerances=None):
        db = self.ledger()
        sessions, intervals, _ = regression.gather(db, {})
        sids = sorted({x['session_id'] for x in intervals})
        baseline = regression.make_run('live', db, sessions, sids, [], {})
        candidate = regression.make_run('job-x', db, sessions, sids, [candidate_db], {})
        result = regression.gate(baseline, candidate, intervals, tolerances or dict(regression.GATE_DEFAULTS))
        db.close()
        return result

    def test_identical_replay_passes_and_replay_takes_live_timing(self):
        self.build()
        rows, passed, support, untested = self.run_gate(self.replay_db('same.sqlite3'))
        self.assertTrue(passed)
        self.assertEqual(untested, [])
        self.assertEqual({(r['activity'], r['verdict']) for r in rows}, {('computer_work', 'pass'), ('empty_room', 'pass')})
        for r in rows:
            self.assertEqual(r['baseline']['exact_at_coverage'], r['candidate']['exact_at_coverage'])
            self.assertEqual(r['baseline']['coverage'], r['candidate']['coverage'])
            self.assertFalse(r['low_confidence'])
        self.assertEqual(regression.verdict(rows, passed, untested, regression.GATE_DEFAULTS), ('PASS', 0))

    def test_a_false_positive_in_the_empty_room_fails_even_when_the_target_improves(self):
        self.build()
        # Better? The candidate also answers computer_work in the empty room.
        db = self.replay_db('fp.sqlite3', lambda sid, t, activity: 'computer_work' if sid == self.a and 40 <= t < 50 else activity)
        rows, passed, _, untested = self.run_gate(db)
        self.assertFalse(passed)
        empty = next(r for r in rows if r['activity'] == 'empty_room')
        self.assertEqual(empty['verdict'], 'FAIL')
        self.assertTrue(any('computer_work answered in empty_room' in f for f in empty['failures']))
        self.assertTrue(any('false-confident' in f for f in empty['failures']))
        self.assertEqual(regression.verdict(rows, passed, untested, regression.GATE_DEFAULTS)[1], 1)

    def test_exact_drop_coverage_drop_and_tolerances(self):
        self.build()
        wrong = self.replay_db('wrong.sqlite3', lambda sid, t, activity: 'phone_use' if t < 8 else activity)
        rows, passed, _, _ = self.run_gate(wrong, {**regression.GATE_DEFAULTS, 'false_confident_increase_max_s': 100,
                                                    'false_positive_increase_max_s': 100})
        work = next(r for r in rows if r['activity'] == 'computer_work')
        self.assertTrue(any(f.startswith('exact@coverage') for f in work['failures']), work['failures'])
        rows, passed, _, _ = self.run_gate(wrong, {**regression.GATE_DEFAULTS, 'exact_drop_max': 1, 'false_confident_increase_max_s': 100,
                                                    'false_positive_increase_max_s': 100})
        self.assertTrue(passed)
        silent = self.replay_db('silent.sqlite3', lambda sid, t, activity: None)
        rows, passed, _, _ = self.run_gate(silent)
        self.assertFalse(passed)
        self.assertTrue(all(any(f.startswith('coverage') for f in r['failures']) for r in rows))

    def test_untested_activities_leave_the_gate_incomplete_and_low_confidence_is_marked(self):
        self.build()
        c, _ = self.session('c', 30)  # labelled, but no candidate answers there
        self.label(c, 'l1', 0, 30, 'resting')
        rows, passed, _, untested = self.run_gate(self.replay_db('partial.sqlite3'))
        self.assertIn('resting', untested)
        word, status = regression.verdict(rows, passed, untested, regression.GATE_DEFAULTS)
        self.assertEqual(status, 4)
        self.assertTrue(word.startswith('INCOMPLETE'))
        self.assertEqual(regression.verdict(rows, passed, untested, {**regression.GATE_DEFAULTS, 'require_all_activities': False})[1], 0)
        self.assertEqual(regression.verdict([], False, untested, regression.GATE_DEFAULTS)[1], 3)

    def test_tolerances_are_explicit_and_checked(self):
        self.assertEqual(regression.tolerances_from({'gate': {'exact_drop_max': .1}}, None)['exact_drop_max'], .1)
        for bad in ({'nope': 1}, {'exact_drop_max': -1}, {'require_all_activities': 1}):
            with self.assertRaises(SystemExit):
                regression.tolerances_from({'gate': bad}, None)

    def test_cli_gate_prints_a_table_and_exits_non_zero_on_failure(self):
        self.build()
        db = self.replay_db('fp.sqlite3', lambda sid, t, activity: 'resting' if t >= 30 else activity)
        manifest = Path(self.directory.name) / 'manifest.json'
        manifest.write_text(json.dumps({'schema': regression.MANIFEST_SCHEMA, 'candidates': {'cand': {'replay_db': db, 'jobs': ['job-*']}}}))
        out = io.StringIO()
        with redirect_stdout(out):
            status = regression.main(['gate', '--ledger', str(Path(self.directory.name) / 'lab.sqlite3'), '--manifest', str(manifest),
                                      '--candidate', 'cand', '--baseline', 'live'])
        self.assertEqual(status, 1)
        self.assertIn('GATE FAIL', out.getvalue())
        self.assertIn('resting answered in empty_room', out.getvalue())
        self.assertIn('Baseline is LIVE', out.getvalue())


class BaselineReplayAndNoiseTests(LedgerFixture):
    """The default baseline is a replay of the baseline wire at the
    candidate's cutoffs; repeat runs of the same packets estimate JEV's
    run-to-run noise, and a difference within it is within_noise."""
    build = GateTests.build
    replay_db = GateTests.replay_db

    def cli(self, *extra, files=()):
        out = io.StringIO()
        with redirect_stdout(out):
            status = regression.main(['gate', '--ledger', str(Path(self.directory.name) / 'lab.sqlite3'), '--manifest', 'none.json',
                                      *[a for f in files for a in ('--replay-db', f)], *extra])
        return status, out.getvalue()

    def phone(self, sid, t, activity, *, until=38):
        # Wrong and confident for a few seconds inside the empty room of one session.
        return 'computer_work' if sid == self.a and 30 <= t < until else activity

    def test_the_default_baseline_is_the_replay_of_the_baseline_wire(self):
        self.build()
        replays = self.replay_db('replays.sqlite3', job='base-den6', wire='den-facts-6')
        self.replay_db('replays.sqlite3', job='cand-den7', wire='den-facts-7')
        status, out = self.cli('--candidate', 'cand-den7', '--baseline-wire', 'den-facts-6', files=[replays])
        self.assertEqual(status, 0, out)
        self.assertIn('vs baseline base-den6', out)
        self.assertIn('Baseline replay of wire(s) den-facts-6 covers 100%', out)
        self.assertNotIn('LIVE', out)
        # Never live by default, and a missing baseline replay is named.
        with self.assertRaisesRegex(SystemExit, '--baseline-wire'):
            self.cli('--candidate', 'cand-den7', files=[replays])
        with self.assertRaisesRegex(SystemExit, 'replay --wire den-facts-5'):
            self.cli('--candidate', 'cand-den7', '--baseline-wire', 'den-facts-5', files=[replays])
        # Another question set is not the candidate's conditions.
        other = self.replay_db('other.sqlite3', job='base-den6-q2', wire='den-facts-6', questions='q2')
        self.replay_db('other.sqlite3', job='cand-den7', wire='den-facts-7')
        with self.assertRaisesRegex(SystemExit, 'no replay of den-facts-6'):
            self.cli('--candidate', 'cand-den7', '--baseline-wire', 'den-facts-6', files=[other])
        self.assertTrue(regression.GATE_DEFAULTS['require_all_activities'])

    def test_a_difference_within_the_measured_noise_is_within_noise_not_fail(self):
        self.build()
        replays = self.replay_db('replays.sqlite3', job='base', wire='den-facts-6')
        # Re-asking the same baseline packets gave 6 s of confident error once.
        self.replay_db('replays.sqlite3', lambda sid, t, activity: self.phone(sid, t, activity, until=36),
                       job='base-rep1', wire='den-facts-6', source='repeat')
        self.replay_db('replays.sqlite3', lambda sid, t, activity: self.phone(sid, t, activity, until=34),
                       job='cand', wire='den-facts-7')
        status, out = self.cli('--candidate', 'cand', '--baseline-wire', 'den-facts-6', files=[replays])
        self.assertEqual(status, 1, 'without a noise estimate nothing is excused')
        self.assertIn('Noise: not measured', out)
        status, out = self.cli('--candidate', 'cand', '--baseline-wire', 'den-facts-6', '--noise', 'base-rep*', files=[replays])
        self.assertEqual(status, 0, out)
        self.assertIn('within_noise', out)
        self.assertIn('noise band', out)
        self.assertIn('GATE PASS (within measured noise: empty_room/a)', out)
        # Beyond the band it still fails.
        self.replay_db('replays.sqlite3', lambda sid, t, activity: self.phone(sid, t, activity, until=44),
                       job='cand-worse', wire='den-facts-7')
        status, out = self.cli('--candidate', 'cand-worse', '--baseline-wire', 'den-facts-6', '--noise', 'base-rep*', files=[replays])
        self.assertEqual(status, 1, out)
        self.assertIn('GATE FAIL', out)

    def test_noise_bands_are_the_spread_of_repeated_runs_per_activity(self):
        self.build()
        replays = self.replay_db('replays.sqlite3', job='base', wire='den-facts-6')
        self.replay_db('replays.sqlite3', lambda sid, t, activity: self.phone(sid, t, activity, until=36),
                       job='base-rep1', wire='den-facts-6', source='repeat')
        db = self.ledger()
        sessions, intervals, _ = regression.gather(db, {})
        sids = sorted({x['session_id'] for x in intervals})
        base = regression.make_run('base', db, sessions, sids, [replays], {})
        rep = regression.make_run('base-rep1', db, sessions, sids, [replays], {})
        bands = regression.noise_bands([[base, rep]], intervals, regression.common_support(base, base))
        db.close()
        empty = bands[('a', 'empty_room')]
        self.assertEqual(empty['runs'], 2)
        self.assertGreater(empty['false_confident_s'], 4)
        self.assertGreater(empty['false_positive_s']['computer_work'], 4)
        self.assertEqual(bands[('a', 'computer_work')]['false_confident_s'], 0)
        self.assertEqual(regression.noise_bands([[base]], intervals, {}), {}, 'one run measures no noise')


class ReplayDriverTests(unittest.TestCase):
    def args(self, **kw):
        base = dict(snapshot='/x/snapshot.sqlite3', replay_db='/x/replay.sqlite3', config='/x/config.json', questions='v6',
                    wire='den-facts-7', desk_seat=None, facts_key=None, arms='a,b', job=None, max_calls=400, usd_per_mtok=None,
                    dry_run=False, yes=False, repeat=0)
        return argparse.Namespace(**{**base, **kw})

    def drive(self, args, planned):
        calls = []

        def run(ns, hooks=None):
            calls.append((ns.session, ns.dry_run, ns.max_calls, ns.job))
            self.repeats.append(ns.repeat)
            if ns.dry_run:
                return {'requests': planned, 'dry_run_built': planned, 'estimated_tokens': 1000 * planned, 'cache_hits': 0}
            return {'calls': min(planned, ns.max_calls)}
        with patch('jev_replay_calls.run', run), redirect_stdout(io.StringIO()):
            result = regression.replay(args, ['s1', 's2'])
        return result, calls

    def setUp(self):
        self.repeats = []

    def test_a_repeat_run_passes_through_and_needs_its_own_job(self):
        self.drive(self.args(dry_run=True, repeat=500, job='base-rep1'), 10)
        self.assertEqual(set(self.repeats), {500})
        with self.assertRaisesRegex(SystemExit, 'own --job'):
            self.drive(self.args(dry_run=True, repeat=500), 10)

    def test_dry_run_plans_every_session_and_never_sends(self):
        result, calls = self.drive(self.args(dry_run=True, yes=True), 100)
        self.assertEqual(calls, [('s1', True, 0, 'regression-den-facts-7-v6-default'), ('s2', True, 0, 'regression-den-facts-7-v6-default')])
        self.assertEqual((result['planned_calls'], result['ran']), (200, False))
        result, calls = self.drive(self.args(), 100)  # no --yes: plan only
        self.assertFalse(result['ran'])
        self.assertTrue(all(dry for _, dry, _, _ in calls))

    def test_paid_run_needs_yes_and_fits_the_cap(self):
        with self.assertRaises(SystemExit):
            self.drive(self.args(yes=True), 300)  # 600 planned > 400
        result, calls = self.drive(self.args(yes=True, job='mine'), 150)
        self.assertEqual([c for c in calls if not c[1]], [('s1', False, 400, 'mine'), ('s2', False, 250, 'mine')])
        self.assertEqual(result['calls'], 300)
        for bad in (0, 401):
            with self.assertRaises(SystemExit):
                self.drive(self.args(max_calls=bad, dry_run=True), 1)
        with self.assertRaises(SystemExit):
            self.drive(self.args(snapshot=regression.LEDGER, dry_run=True), 1)


class AutomaticResultsTests(LedgerFixture):
    def test_results_are_stored_once_after_a_recording_with_local_draft_labels(self):
        self.now[0] = 1000.0
        clock = [0.0]
        controller = Controller(self.store, monotonic=lambda: clock[0])
        sid, _ = self.session('marked', 0)
        session = self.store.get_session(sid)
        # Rebuild the recording with bookmarks and answers inside it.
        self.store.db.execute('UPDATE sessions SET started_at=?,ended_at=? WHERE id=?', (900.0, 960.0, sid))
        self.store.append_event(sid, 'bookmark', {'at_ms': 5000, 'label': 'computer'})
        self.store.append_event(sid, 'bookmark', {'at_ms': 30000, 'label': '6 empty room'})
        for i, t in enumerate(range(5, 55, 2)):
            self.request(sid, 900.0, 'q%d' % i, 'a', t, 'computer_work' if t < 30 else 'resting', valid=2.5)
        self.now[0] = 960.0 + 5  # not settled yet
        clock[0] = AUTO_RESULTS_INTERVAL_S
        controller.maintenance()
        self.assertIsNone(scoring.latest_results(self.store, sid))
        self.now[0] = 960.0 + scoring.SETTLE_S + 1
        clock[0] = 2 * AUTO_RESULTS_INTERVAL_S
        controller.maintenance()
        stored = scoring.latest_results(self.store, sid)
        self.assertEqual(stored['truth'], 'bookmark_drafts')
        self.assertEqual([(d['start'], d['end'], d['activity']) for d in stored['draft_labels']],
                         [(5, 30, 'computer_work'), (30, 60, 'empty_room')])
        self.assertEqual(stored['arms']['a']['empty_room']['false_positive_s'], {'computer_work': 1.5, 'resting': 18.0})
        self.assertEqual(stored['arms']['a']['computer_work']['exact_at_coverage'], 1)
        self.assertEqual(stored['wires'], ['den-facts-6'])
        self.assertTrue(stored['local_only'])
        # Draft labels are never saved as annotations; findings carries the stored results.
        self.assertEqual(self.store.list_annotations(sid), [])
        self.assertEqual(controller.findings(sid)['auto_results']['event_id'], stored['event_id'])
        clock[0] = 3 * AUTO_RESULTS_INTERVAL_S
        controller.maintenance()
        count = self.store.db.execute('SELECT count(*) FROM events WHERE session_id=? AND kind=?', (sid, scoring.EVENT_KIND)).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(controller.results_failures, 0)
        self.assertEqual(controller.upkeep_failures, {'expiry': 0, 'reconcile': 0, 'load': 0})

    def test_saved_labels_take_precedence_and_results_never_reach_model_inputs(self):
        sid, start = self.session('labelled', 40)
        self.label(sid, 'l1', 0, 40, 'reading', reviewed=False)
        self.store.append_event(sid, 'bookmark', {'at_ms': 1000, 'label': 'phone'})
        value = scoring.session_results(self.store, sid)
        self.assertEqual((value['truth'], value['labels_used'], len(value['draft_labels'])), ('saved_labels', 1, 1))
        self.assertNotIn(scoring.EVENT_KIND, live_inputs.KINDS)

    def test_a_failing_pass_is_counted_and_never_raises(self):
        controller = Controller(self.store, monotonic=lambda: 1e9)
        with patch('lab_scoring.pending_sessions', side_effect=RuntimeError('boom')), self.assertLogs('perception_lab.controller', 'WARNING'):
            self.assertEqual(controller.auto_results(), [])
        self.assertEqual(controller.results_failures, 1)


if __name__ == '__main__':
    unittest.main()
