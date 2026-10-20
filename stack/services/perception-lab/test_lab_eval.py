"""Absolute evaluation of bake-off arms (lab_eval.py): the tick contract, the
split, label provenance, the held view, the window rule and the metrics, on a
temporary ledger. Nothing here opens a network connection."""
import io
import json
import math
from contextlib import redirect_stdout
from pathlib import Path
import random
import unittest

import lab_eval as ev
import lab_scoring as scoring
from test_lab_regression import LedgerFixture, answer

DAY = 86400.0
FROZEN_AT = 1_790_000_000.0
# One held-out session's labels (session seconds), aligned to the 5 s windows.
PLAN = [(0, 200, 'reading', ''), (200, 400, 'computer_work', ''), (400, 600, 'phone_use', ''), (600, 800, 'resting', ''),
        (800, 1000, 'transition', ''), (1000, 1100, 'other', 'room empty'), (1100, 1200, 'other', 'holding a closed book')]
DURATION = 1200.0


def one_hot(name):
    return {c: float(c == name) for c in ev.CLASSES}


def truth_class(t):
    for start, end, label, notes in PLAN:
        if start <= t < end:
            return 'empty' if notes == 'room empty' else label
    return 'abstain'


class EvalFixture(LedgerFixture):
    def setUp(self):
        super().setUp()
        self.folder = Path(self.directory.name)
        self.now[0] = FROZEN_AT - 2 * DAY
        self.legacy, _ = self.session('legacy', 200)
        self.now[0] = FROZEN_AT + 600  # after the freeze, but named as dev
        self.dev, _ = self.session('dev', 200)
        self.held = []
        for day in range(3):
            self.now[0] = FROZEN_AT + (day + 1) * DAY
            sid, _ = self.session('held-%d' % day, DURATION)
            self.held.append(sid)
            for i, (start, end, label, notes) in enumerate(PLAN):
                self.label(sid, 'l%d' % i, start, end, label, notes=notes)
        for sid in (self.legacy, self.dev):
            self.label(sid, 'l0', 0, 100, 'reading')
            self.label(sid, 'l1', 100, 200, 'phone_use')
        self.split = self.write('split.json', {'schema': ev.SPLIT_SCHEMA, 'frozen_at': FROZEN_AT,
                                               'site_timezone': 'America/Vancouver',
                                               'dev': {self.dev: ['tuned the desk prompt']},
                                               'slices': {self.held[0]: ['dark'], self.held[1]: ['dark', 'multi_person']}})

    def write(self, name, value):
        file = self.folder / name
        file.write_text(json.dumps(value), encoding='utf-8')
        return str(file)

    def ticks(self, name, distribution, *, calibration='distribution', consumes_qwen=False, sessions=None, arm='a'):
        """A lab-ticks-1 file with one 1 Hz emission per second of each session."""
        header = {'schema': ev.TICKS_SCHEMA, 'run': name, 'arm': 'reference', 'consumes_qwen': consumes_qwen,
                  'calibration': calibration, 'classes': list(ev.CLASSES), 'source': {'ledger': 'lab.sqlite3'}}
        lines = [json.dumps(header)]
        for sid in sessions or self.held:
            for t in range(int(DURATION)):
                lines.append(json.dumps({'session_id': sid, 'arm': arm, 't': t, 'available_at': t,
                                         'probabilities': distribution(t)}))
        file = self.folder / (name + '.jsonl')
        file.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return str(file)

    def evaluate(self, runs, **kw):
        db = self.ledger()
        try:
            return ev.evaluate(db, ev.load_split(self.split), runs, **kw)
        finally:
            db.close()


class ArmTests(EvalFixture):
    def test_perfect_arm(self):
        report = self.evaluate({'perfect': self.ticks('perfect', lambda t: one_hot(truth_class(t)))})
        held = report['runs']['perfect']['held_out']
        self.assertEqual(held['sessions'], 3)
        self.assertEqual(held['excluded_classes'], [])
        self.assertTrue(all(row['gate'] == 'ok' for row in held['classes'].values()))
        self.assertEqual(held['macro_f1'], 1.0)
        self.assertTrue(held['passes_stage1'])
        self.assertEqual(held['risk_coverage']['coverage_at_p90'], 1.0)
        self.assertEqual(held['empty_room']['detection'], 1.0)
        self.assertEqual(held['empty_room']['safety'], 0.0)
        self.assertEqual(held['calibration']['ece'], 0.0)
        self.assertEqual(held['calibration']['ece_status'], 'ok')
        self.assertEqual(held['band_precision']['precision'], 1.0)
        reading = held['classes']['reading']
        self.assertEqual((reading['tp'], reading['fn'], reading['fp']), (120, 0, 0))
        self.assertEqual(reading['n_eff'], 50.0)  # 600 s shown / 12 s per trial
        self.assertAlmostEqual(reading['precision_lb'], ev.wilson_lower(50, 50), places=4)
        self.assertEqual(held['latency']['n'], 3 * 7)
        self.assertEqual(held['bands']['bootstrap'], 'n/a (< 8 sessions)')
        self.assertEqual(sorted(report['runs']['perfect']['slices']), ['dark', 'multi_person'])
        self.assertEqual(report['runs']['perfect']['slices']['dark']['sessions'], 2)
        json.dumps(report)  # the --json output serializes

    def test_always_abstain_arm(self):
        report = self.evaluate({'abstain': self.ticks('abstain', lambda t: one_hot('abstain'))})
        held = report['runs']['abstain']['held_out']
        self.assertEqual(held['macro_f1'], 0.0)
        self.assertFalse(held['passes_stage1'])
        self.assertEqual(held['coverage'], 0.0)
        self.assertIsNone(held['risk_coverage']['coverage_at_p90'])
        self.assertEqual(held['empty_room']['detection'], 0.0)
        self.assertFalse(held['empty_room']['detection_ok'])
        self.assertEqual(held['calibration']['ece_status'], ev.INSUFFICIENT)
        self.assertIsNone(held['calibration']['ece'])  # gated: no number below MIN_CALIBRATION_TICKS
        self.assertIsNone(held['calibration']['session_ece_range'])
        # Brier counts the abstained seconds: all abstain mass, truth mass 0.
        self.assertEqual(held['calibration']['brier'], 2.0)

    def test_soft_accurate_arm_keeps_its_macro_f1_under_loo(self):
        def soft(t):
            truth = truth_class(t)
            if truth == 'empty':
                return {**one_hot('abstain'), 'empty': 0.6, 'abstain': 0.4}
            shown = truth
            if (t // 5) % 7 == 0:  # one window in seven wrong: ~86 % right, below the 0.90 target
                shown = ev.S_CLASSES[(ev.S_CLASSES.index(truth) + 1) % 5] if truth in ev.S_CLASSES else 'reading'
            return {**one_hot('abstain'), shown: 0.6, 'abstain': 0.4}
        file = self.ticks('soft', soft)
        fixed = self.evaluate({'soft': file}, tau=0.5)['runs']['soft']['held_out']
        run = self.evaluate({'soft': file})['runs']['soft']
        self.assertGreater(fixed['macro_f1'], 0.8)
        self.assertTrue(all(tau <= 0.6 for tau in run['fit']['taus_by_day'].values()), run['fit'])
        self.assertEqual(run['held_out']['macro_f1'], fixed['macro_f1'])
        # A tau that shows nothing is never 'feasible'.
        nothing = {'tau': 0.95, 'coverage': 0.1, 'with_data': ['reading'], 'class_precision': {c: None for c in ev.S_CLASSES}}
        self.assertFalse(ev.meets_precision(nothing))
        self.assertFalse(ev.meets_precision({**nothing, 'coverage': 0.0, 'with_data': []}))

    def test_a_session_the_run_skips_is_scored_as_no_answer(self):
        full = self.evaluate({'p': self.ticks('p', lambda t: one_hot(truth_class(t)))}, tau=0.5)['runs']['p']['held_out']
        header = {'schema': ev.TICKS_SCHEMA, 'run': 'skip', 'arm': 'reference', 'consumes_qwen': False,
                  'calibration': 'distribution', 'classes': list(ev.CLASSES), 'source': {}}
        lines = [json.dumps(header)] + [json.dumps({'session_id': self.held[0], 'arm': 'a', 't': t, 'available_at': t,
                                                    'probabilities': one_hot(truth_class(t))}) for t in range(150)]
        file = self.folder / 'skip.jsonl'
        file.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        run = self.evaluate({'skip': str(file)}, tau=0.5)['runs']['skip']
        held = run['held_out']
        self.assertEqual(held['sessions'], 3)
        self.assertEqual(held['missing'], ['%s/a' % sid for sid in sorted(self.held[1:])])
        self.assertLess(held['coverage'], full['coverage'])
        self.assertLess(held['macro_f1'], full['macro_f1'])
        self.assertEqual(full['missing'], [])
        # 150 answered seconds: ECE is not reported, nor any session's.
        self.assertEqual(held['calibration']['ece_status'], ev.INSUFFICIENT)
        self.assertIsNone(held['calibration']['ece'])
        self.assertIsNone(held['calibration']['session_ece_range'])
        self.assertEqual(sorted(run['legacy']['missing']), sorted(['%s/a' % self.dev, '%s/a' % self.legacy]))

    def test_random_arm(self):
        rng = random.Random(7)

        def draw(t):
            raw = [rng.random() for _ in ev.CLASSES]
            return {c: v / sum(raw) for c, v in zip(ev.CLASSES, raw)}
        report = self.evaluate({'random': self.ticks('random', draw)}, tau=0.3)
        held = report['runs']['random']['held_out']
        self.assertLess(held['macro_f1'], ev.MACRO_F1_PASS)
        self.assertFalse(held['passes_stage1'])
        self.assertEqual(report['runs']['random']['fit'], {'mode': 'fixed', 'tau': 0.3})
        self.assertIsNotNone(held['calibration']['brier'])

    def test_calibration_none_is_excluded_not_zero(self):
        report = self.evaluate({'rule': self.ticks('rule', lambda t: one_hot(truth_class(t)), calibration='none')})
        held = report['runs']['rule']['held_out']
        self.assertEqual(held['calibration'], 'none')
        self.assertIsNone(held['band_precision'])
        self.assertEqual(held['macro_f1'], 1.0)  # the decision is still scored
        with redirect_stdout(io.StringIO()) as out:
            ev.print_report(report)
        self.assertIn('calibration: none', out.getvalue())

    def test_loo_fit_picks_a_tau_per_held_out_day(self):
        report = self.evaluate({'perfect': self.ticks('perfect', lambda t: one_hot(truth_class(t)))})
        fit = report['runs']['perfect']['fit']
        self.assertEqual(fit['mode'], 'loo')
        self.assertEqual(len(fit['taus_by_day']), 3)
        self.assertEqual(fit['fallback_days'], [])


class SplitTests(EvalFixture):
    def test_assignment_days_and_influence(self):
        db = self.ledger()
        sessions, _, _ = ev.regression.gather(db, {})
        db.close()
        assignment, _ = ev.assign(sessions, ev.load_split(self.split))
        self.assertEqual(assignment[self.legacy]['assignment'], 'legacy')
        self.assertEqual(assignment[self.dev]['assignment'], 'legacy')  # after the freeze, but dev
        self.assertEqual(assignment[self.dev]['influence'], ['tuned the desk prompt'])
        self.assertEqual([assignment[s]['assignment'] for s in self.held], ['held_out'] * 3)
        self.assertEqual(len({assignment[s]['day'] for s in self.held}), 3)

    def test_refuses_to_report_a_dev_or_legacy_session_as_held_out(self):
        file = self.ticks('perfect', lambda t: one_hot(truth_class(t)))
        for sid in (self.dev, self.legacy):
            with self.assertRaises(SystemExit):
                self.evaluate({'perfect': file}, held_out=[sid])

    def test_legacy_is_its_own_slice(self):
        file = self.ticks('perfect', lambda t: one_hot(truth_class(t)), sessions=self.held + [self.dev, self.legacy])
        report = self.evaluate({'perfect': file}, held_out=[self.held[0]], tau=0.5)
        run = report['runs']['perfect']
        self.assertEqual(run['held_out']['sessions'], 1)
        self.assertEqual(run['legacy']['sessions'], 2)  # the unrequested held-out sessions join neither
        self.assertEqual(sorted(report['split']['legacy']), sorted([self.dev, self.legacy]))
        self.assertEqual({s['assignment'] for s in run['sessions'].values()}, {'held_out', 'legacy'})

    def test_a_split_key_that_is_not_a_session_id_refuses(self):
        file = self.ticks('perfect', lambda t: one_hot(truth_class(t)))
        for split in ({'dev': {self.dev[:8]: ['prefix']}}, {'slices': {self.held[0][:-1] + 'x': ['dark']}}):
            self.split = self.write('split.json', {'schema': ev.SPLIT_SCHEMA, 'frozen_at': FROZEN_AT, **split})
            with self.assertRaises(SystemExit) as raised:
                self.evaluate({'perfect': file}, tau=0.5)
            self.assertIn('not a session id', str(raised.exception))
        self.split = self.write('split.json', {'schema': ev.SPLIT_SCHEMA, 'frozen_at': FROZEN_AT, 'dev': {self.dev[:8]: []}})
        with self.assertRaises(SystemExit) as raised:
            self.evaluate({'perfect': file}, tau=0.5)
        self.assertIn(self.dev, str(raised.exception))  # the unique prefix names the full id

    def test_split_command_prints_every_session(self):
        with redirect_stdout(io.StringIO()) as out:
            ev.main(['split', '--ledger', str(self.folder / 'lab.sqlite3'), '--split', self.split])
        text = out.getvalue()
        for sid in [self.legacy, self.dev] + self.held:
            self.assertIn(sid, text)
        self.assertIn('tuned the desk prompt', text)


class TickFileTests(EvalFixture):
    def write_lines(self, name, header=None, emission=None, raw=None):
        header = {'schema': ev.TICKS_SCHEMA, 'run': 'x', 'arm': 'reference', 'consumes_qwen': False,
                  'calibration': 'distribution', 'classes': list(ev.CLASSES), 'source': {}, **(header or {})}
        emission = {'session_id': self.held[0], 'arm': 'a', 't': 3, 'available_at': 4,
                    'probabilities': one_hot('reading'), **(emission or {})}
        file = self.folder / (name + '.jsonl')
        file.write_text('\n'.join([json.dumps(header), raw if raw is not None else json.dumps(emission)]) + '\n', encoding='utf-8')
        return str(file)

    def test_a_valid_file_reads(self):
        header, emissions = ev.read_ticks(self.write_lines('good'))
        self.assertEqual((header['arm'], len(emissions)), ('reference', 1))

    def test_contract_violations_refuse(self):
        cases = {'not JSON': self.write_lines('json', raw='{"session_id": '),
                 'header arm': self.write_lines('arm', header={'arm': 'Z'}),
                 '32 hex': self.write_lines('sid', emission={'session_id': self.held[0][:8]}),
                 't after available_at': self.write_lines('t', emission={'t': 5, 'available_at': 4})}
        for message, file in cases.items():
            with self.assertRaises(SystemExit, msg=message) as raised:
                ev.read_ticks(file)
            self.assertIn(message, str(raised.exception))


class ProvenanceTests(EvalFixture):
    def test_gold_silver_and_qwen(self):
        sid = self.held[0]
        self.label(sid, 'q', 1200, 1210, 'phone_use', notes='phone in hands (Qwen 8/9)')
        self.label(sid, 'u', 1210, 1220, 'reading', reviewed=False)
        self.store.save_annotation(sid, 's', {'at_ms': 1220000, 'end_ms': 1230000, 'label': 'resting', 'origin': 'human',
                                              'reviewed': True, 'notes': 'Qwen said so', 'provenance': 'owner_statement'}, 0, 'owner')
        db = self.ledger()
        _, intervals, _ = ev.labels(db, {'schema': ev.regression.MANIFEST_SCHEMA})
        db.close()
        by_source = {x['source']: x for x in intervals}
        self.assertEqual(by_source['annotation:q']['provenance'], 'qwen_proposed')
        self.assertFalse(by_source['annotation:q']['gold'])
        self.assertFalse(by_source['annotation:u']['gold'])
        self.assertEqual(by_source['annotation:s']['provenance'], 'owner_statement')  # the stored one wins
        self.assertTrue(by_source['annotation:s']['gold'])

        kept, dropped = ev.select_labels(intervals, consumes_qwen=False, silver=False)
        self.assertNotIn('annotation:q', {x['source'] for x in kept})
        self.assertNotIn('annotation:u', {x['source'] for x in kept})
        self.assertEqual(dropped, {'silver': 2})
        kept, _ = ev.select_labels(intervals, consumes_qwen=False, silver=True)
        self.assertTrue({'annotation:q', 'annotation:u'} <= {x['source'] for x in kept})
        kept, dropped = ev.select_labels(intervals, consumes_qwen=True, silver=True)
        self.assertNotIn('annotation:q', {x['source'] for x in kept})
        self.assertIn('annotation:u', {x['source'] for x in kept})
        self.assertEqual(dropped, {'qwen_proposed': 1})

    def test_manifest_owner_statement_is_gold(self):
        manifest = {'schema': ev.regression.MANIFEST_SCHEMA,
                    'labels': [{'session_id': self.held[0], 'activity': 'reading', 'start_s': 1200, 'end_s': 1210},
                               {'session_id': self.held[0], 'activity': 'reading', 'start_s': 1210, 'end_s': 1220,
                                'provenance': 'owner_unreviewed'}]}
        db = self.ledger()
        _, intervals, _ = ev.labels(db, manifest)
        db.close()
        by_source = {x['source']: x for x in intervals}
        self.assertEqual(by_source['manifest:0']['provenance'], 'owner_statement')
        self.assertTrue(by_source['manifest:0']['reviewed'])
        self.assertTrue(by_source['manifest:0']['gold'])
        self.assertFalse(by_source['manifest:1']['gold'])

    def test_eval_defaults_to_the_private_manifest(self):
        args = ev.build_parser().parse_args(['eval', '--split', self.split, '--run', 'x=x.jsonl'])
        self.assertEqual(args.manifest, ev.regression.MANIFEST)

    def test_runs_report_their_label_selection(self):
        self.label(self.held[0], 'q', 1200, 1210, 'phone_use', notes='Qwen proposed')
        file = self.ticks('qwen', lambda t: one_hot(truth_class(t)), consumes_qwen=True)
        run = self.evaluate({'qwen': file}, silver=True, tau=0.5)['runs']['qwen']
        self.assertEqual(run['labels']['dropped'], {'qwen_proposed': 1})
        run = self.evaluate({'plain': self.ticks('plain', lambda t: one_hot(truth_class(t)))}, tau=0.5)['runs']['plain']
        self.assertEqual(run['labels']['dropped'], {'silver': 1})


class HeldViewTests(unittest.TestCase):
    def emission(self, available, closed=None, cls='reading'):
        value = {'session_id': 'x', 'arm': 'a', 't': available, 'available_at': available, 'probabilities': one_hot(cls)}
        if closed is not None:
            value['closed_at'] = closed
        return value

    def test_cut_by_next_emission_closed_at_and_hold(self):
        records = ev.held_view([self.emission(5, closed=8), self.emission(0), self.emission(20), self.emission(40, closed=39)])[('x', 'a')]
        self.assertEqual([(r['start'], r['end']) for r in records], [(0, 5), (5, 8), (20, 32)])  # closed before arrival: none
        self.assertEqual([(r['start'], r['end']) for r in ev.held_view([self.emission(0)], hold_s=3)[('x', 'a')]], [(0, 3)])

    def test_decision_at_tau(self):
        p = {**one_hot('abstain'), 'abstain': 0.3, 'reading': 0.45, 'empty': 0.25}
        self.assertEqual(ev.decide(p, 0.4), ('reading', 0.45))
        self.assertEqual(ev.decide(p, 0.5), ('abstain', 0.45))
        self.assertEqual(ev.decide({**p, 'empty': 0.5, 'reading': 0.2}, 0.1), ('empty', 0.5))
        records = ev.held_view([self.emission(0)])[('x', 'a')]
        prediction = ev.decided(records, 0.5)[0]
        self.assertEqual((prediction['activity'], prediction['confidence'], prediction['end']), ('reading', 1.0, 12))
        self.assertEqual(prediction['probabilities'], one_hot('reading'))

    def test_window_truth_needs_80_percent(self):
        labels = [{'start': 0, 'end': 4.1, 'activity': 'reading'}, {'start': 4.1, 'end': 8.5, 'activity': 'phone_use'},
                  {'start': 8.5, 'end': 15, 'activity': 'reading'}, {'start': 20, 'end': 25, 'activity': 'resting'}]
        windows, dropped = ev.window_truth(labels, 5)
        self.assertEqual(windows, [(0, 'reading'), (10, 'reading'), (20, 'resting')])
        self.assertEqual(dropped, 1)  # [5, 10): phone 3.5 s, reading 1.5 s

    def test_windows_without_an_answer_abstain(self):
        records = ev.held_view([self.emission(6)])[('x', 'a')]
        answers = ev.window_answers([(0, 'reading'), (5, 'reading')], records, 5)
        self.assertEqual(ev.window_pairs(answers, 0.5), [('reading', 'abstain'), ('reading', 'reading')])

    def test_wilson_lower_bound(self):
        self.assertAlmostEqual(ev.wilson_lower(10, 10), 0.7225, places=3)
        self.assertIsNone(ev.wilson_lower(0, 0))


class SegmentsTests(unittest.TestCase):
    """lab_scoring.segments() is what accumulate() scores."""
    LABELS = [{'start': 0, 'end': 20, 'activity': 'reading'}, {'start': 20, 'end': 50, 'activity': 'phone_use'},
              {'start': 55, 'end': 80, 'activity': 'computer_work'}, {'start': 70, 'end': 90, 'activity': 'resting'}]
    ANSWERS = [answer(1, 6, 'reading', available=2), answer(5, 14, 'phone_use', p=.95), answer(14, 30, 'reading', valid=22),
               answer(31, 45, 'phone_use', p=.5), answer(44, 60, 'insufficient_evidence'), answer(60, 90, 'computer_work', available=63),
               answer(70, 75, 'reading', p=.85), answer(84, 88, None)]

    def test_accumulate_is_a_fold_over_segments(self):
        predictions = [scoring.prediction(a) for a in self.ANSWERS]
        for support in (None, [(3.0, 47.0), (60.0, 85.0)]):
            rows = scoring.accumulate(self.LABELS, predictions, support=support)
            labelled, confusion = {}, {}
            for start, end, key, shown, _, _ in scoring.segments(self.LABELS, predictions, support=support):
                labelled[key] = labelled.get(key, 0) + end - start
                row = confusion.setdefault(key, {})
                row[shown] = row.get(shown, 0.0) + (end - start)
            self.assertEqual({k: round(v, 6) for k, v in labelled.items()}, {k: round(r['labelled_s'], 6) for k, r in rows.items()})
            self.assertEqual(confusion, {k: r['confusion'] for k, r in rows.items()})

    def test_segments_match_a_brute_force_reading_of_the_rules(self):
        predictions = [scoring.prediction(a) for a in self.ANSWERS]
        step, seconds = 0.01, {}
        for i in range(int(95 / step)):
            t = (i + 0.5) * step
            covering = sorted({l['activity'] for l in self.LABELS if l['start'] <= t < l['end']})
            if not covering:
                continue
            current = [p for p in predictions if p['start'] <= t < min(p['end'], p['valid_until']) and p['available_at'] <= t]
            shown = (max(current, key=lambda p: (p['start'], p['available_at']))['activity'] or scoring.ABSTAIN) if current \
                else scoring.NO_ANSWER
            key = ('+'.join(covering), shown)
            seconds[key] = seconds.get(key, 0) + step
        mine = {}
        for start, end, key, shown, _, _ in scoring.segments(self.LABELS, predictions):
            mine[(key, shown)] = mine.get((key, shown), 0) + end - start
        self.assertEqual(set(mine), set(seconds))
        for k, v in mine.items():
            self.assertAlmostEqual(v, seconds[k], places=1, msg=k)

    def test_prediction_passes_valid_probabilities_through(self):
        good = {**answer(0, 5, 'reading'), 'probabilities': {'reading': 0.7, 'other': 0.3}}
        self.assertEqual(scoring.prediction(good)['probabilities'], {'reading': 0.7, 'other': 0.3})
        for bad in ({'reading': 1.5}, {'reading': math.nan}, [0.5], None):
            self.assertIsNone(scoring.prediction({**good, 'probabilities': bad})['probabilities'])
        base = scoring.prediction(answer(0, 5, 'reading'))
        self.assertEqual({k: v for k, v in scoring.prediction(good).items() if k != 'probabilities'},
                         {k: v for k, v in base.items() if k != 'probabilities'})


if __name__ == '__main__':
    unittest.main()
