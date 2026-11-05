"""The command line, the open slots and the refusals, end to end on synthetic files."""
import contextlib
import io
import json
import os
import re
import socket
import tempfile
import unittest
from unittest import mock

import den_track
import method
import records
import synth

HERE = os.path.dirname(os.path.abspath(__file__))


def cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = den_track.main(list(argv))
    return code, out.getvalue() + err.getvalue()


def no_connections(*args, **kwargs):
    raise AssertionError('den-track must not open a socket')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.assertIsNone(den_track.inside_private_risk(self.tmp.name), 'the tests need a temp folder outside git')
        patcher = mock.patch.object(socket.socket, 'connect', no_connections)
        patcher.start()
        self.addCleanup(patcher.stop)

    def path(self, *parts):
        return os.path.join(self.tmp.name, *parts)

    def closed_method(self, name='METHOD-closed.md', change=None):
        """A copy of METHOD.md with every open slot closed at its proposed value, on the synthetic room."""
        block = method.resolve(method.read_block())
        floor, exits = den_track.synth_room()
        block['room'] = {'floor_m': floor, 'exits_m': exits}
        block['freeze']['frozen_on'] = '2026-11-04'
        if change:
            change(block)
        path = self.path(name)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('```json den-track-method\n%s\n```\n' % json.dumps(block, indent=2))
        return path

    def make_take(self, scene='clean_crossing', seed=0, noise='clean', meth=None):
        """Synth, then track. Returns [observations, tracks, truth] paths."""
        folder = self.path('takes', '%s-%d' % (scene, seed))
        code, text = cli('synth', '--scene', scene, '--seed', str(seed), '--noise', noise, '--out', folder)
        self.assertEqual(code, 0, text)
        run_folder = os.path.join(folder, 'track-1')
        args = ['--method', meth] if meth else []
        code, text = cli(*args, 'track', '--observations', os.path.join(folder, 'observations.jsonl'), '--out', run_folder)
        self.assertEqual(code, 0, text)
        return [os.path.join(folder, 'observations.jsonl'), os.path.join(run_folder, 'tracks.jsonl'),
                os.path.join(folder, 'truth.jsonl')]

    def score_args(self, takes, out='score', scored=False, meth=None):
        args = ['--method', meth] if meth else []
        args.append('score')
        for take in takes:
            args += ['--take'] + take
        args += ['--out', self.path(out)]
        if scored:
            args.append('--scored')
        return args


class PracticeRunTest(Base):
    def test_track_then_score_writes_files_and_says_unscored(self):
        take = self.make_take()
        code, text = cli(*self.score_args([take]))
        self.assertEqual(code, 0, text)
        self.assertIn('PRACTICE', text)
        self.assertIn('UNSCORED', text)
        with open(self.path('score', 'score.json'), encoding='utf-8') as f:
            report = json.load(f)
        self.assertFalse(report['scored'])
        self.assertEqual(report['crossings'], 1)
        self.assertEqual(report['silent_swaps'], 0)
        self.assertEqual(report['pooled']['idf1'], 1.0)
        self.assertAlmostEqual(report['silent_swap_rate_upper_95'], 0.95)
        self.assertTrue(report['method']['open'])
        self.assertTrue(report['method']['fingerprint'].startswith('sha256:'))

    def test_track_run_names_the_method_and_counts(self):
        take = self.make_take()
        with open(os.path.join(os.path.dirname(take[1]), den_track.TRACK_RUN_FILE), encoding='utf-8') as f:
            run = json.load(f)
        self.assertEqual(run['method']['fingerprint'], method.Method().fingerprint)
        self.assertEqual(run['tracks_sha256'], den_track.text_sha256(take[1]))
        self.assertGreater(run['counts']['frames'], 5)

    def test_output_is_utf8_with_lf(self):
        take = self.make_take()
        cli(*self.score_args([take]))
        for path in (take[1], self.path('score', 'score.json'), self.path('score', 'crossings.jsonl')):
            with open(path, 'rb') as f:
                raw = f.read()
            self.assertNotIn(b'\r', raw, path)
            raw.decode('utf-8')

    def test_crossings_file_is_the_form_rung_5_reads(self):
        take = self.make_take('close_crossing')
        cli(*self.score_args([take]))
        lines = records.read_jsonl(self.path('score', 'crossings.jsonl'))
        self.assertEqual(len(lines), 1)
        self.assertEqual(set(lines[0]), {'crossing_id', 'track_ids', 'interval'})
        self.assertEqual(len(lines[0]['track_ids']), 2)

    def test_a_single_person_occlusion_is_not_a_crossing_for_rung_5(self):
        take = self.make_take('armchair_occlusion')
        cli(*self.score_args([take]))
        self.assertEqual(records.read_jsonl(self.path('score', 'crossings.jsonl')), [])

    def test_practice_writes_nothing_and_never_calls_itself_a_score(self):
        before = set(os.listdir(HERE))
        code, text = cli('practice', '--seeds', '1', '--scene', 'near_pass')
        self.assertEqual(code, 0, text)
        self.assertIn('Synthetic: never a score', text)
        self.assertIn('UNSCORED', text)
        self.assertEqual(set(os.listdir(HERE)), before)

    def test_practice_can_try_another_value_and_refuses_an_unknown_one(self):
        code, _ = cli('practice', '--seeds', '1', '--scene', 'near_pass', '--set', 'tracker.accel_sigma=0.5')
        self.assertEqual(code, 0)
        code, text = cli('practice', '--seeds', '1', '--set', 'tracker.nope=1')
        self.assertEqual(code, 2)
        self.assertIn('no such value', text)

    def test_slots_lists_every_open_slot(self):
        code, text = cli('slots')
        self.assertEqual(code, 0)
        for name, _ in method.Method().open:
            self.assertIn(name, text)


class ScoredRunTest(Base):
    def twenty(self, meth):
        return [self.make_take(seed=seed, meth=meth) for seed in range(20)]

    def assert_refused(self, args, needle):
        out = args[args.index('--out') + 1]
        code, text = cli(*args)
        self.assertEqual(code, 2, text)
        self.assertIn(needle, text)
        self.assertFalse(os.path.exists(os.path.join(out, 'score.json')), 'a refusal writes nothing')

    def test_refused_while_any_slot_is_open_and_every_slot_is_named(self):
        take = self.make_take()
        code, text = cli(*self.score_args([take], scored=True))
        self.assertEqual(code, 2, text)
        for name, _ in method.Method().open:
            self.assertIn(name, text)
        self.assertFalse(os.path.exists(self.path('score', 'score.json')))

    def test_twenty_clean_crossings_pass_and_report_the_bound(self):
        meth = self.closed_method()
        code, text = cli(*self.score_args(self.twenty(meth), scored=True, meth=meth))
        self.assertEqual(code, 0, text)
        self.assertIn('SCORED', text)
        self.assertIn('PASSED', text)
        with open(self.path('score', 'score.json'), encoding='utf-8') as f:
            report = json.load(f)
        self.assertTrue(report['scored'])
        self.assertTrue(report['gate']['passed'])
        self.assertEqual(report['method']['open'], [])
        self.assertAlmostEqual(report['silent_swap_rate_upper_95'], 1 - 0.05 ** (1 / 20))

    def test_a_silent_swap_fails_the_gate_with_exit_1(self):
        meth = self.closed_method()
        takes = self.twenty(meth)
        # The truth says the two people changed places near the end of one take, long after they
        # parted, while the tracker reports confidence. (During the contact it would be flagged.)
        lines = records.read_jsonl(takes[0][2])
        frames = [l for l in lines if l['kind'] == 'frame']
        for line in frames[-3:]:
            line['labels'] = {o: {'A': 'B', 'B': 'A'}.get(p, p) for o, p in line['labels'].items()}
        records.write_jsonl(takes[0][2], lines)
        code, text = cli(*self.score_args(takes, scored=True, meth=meth))
        self.assertEqual(code, 1, text)
        self.assertIn('FAILED', text)
        with open(self.path('score', 'score.json'), encoding='utf-8') as f:
            report = json.load(f)
        self.assertGreater(report['silent_swaps'] + report['silent_swaps_outside_crossings'], 0)
        self.assertFalse(report['gate']['silent_swaps_zero'])

    def test_refused_under_twenty_crossings(self):
        meth = self.closed_method()
        take = self.make_take(meth=meth)
        self.assert_refused(self.score_args([take], scored=True, meth=meth), 'at least 20')

    def test_refused_when_the_method_changed_after_tracking(self):
        meth = self.closed_method()
        takes = self.twenty(meth)

        def nudge(block):
            block['tracker']['accel_sigma'] = 0.7

        other = self.closed_method('METHOD-other.md', nudge)
        self.assert_refused(self.score_args(takes, scored=True, meth=other), 'another method')

    def test_refused_when_tracks_or_observations_changed(self):
        meth = self.closed_method()
        takes = self.twenty(meth)
        with open(takes[3][1], 'a', encoding='utf-8', newline='\n') as f:
            f.write('\n')
        self.assert_refused(self.score_args(takes, scored=True, meth=meth), 'tracks changed')
        takes = [t for t in takes if t is not takes[3]] + [self.make_take(seed=99, meth=meth)]
        lines = records.read_jsonl(takes[4][0])
        lines[0]['score'] = 0.5
        records.write_jsonl(takes[4][0], lines)
        self.assert_refused(self.score_args(takes, 'score2', scored=True, meth=meth), 'observations are not')

    def test_refused_with_an_unlabelled_observation(self):
        meth = self.closed_method()
        takes = self.twenty(meth)
        lines = records.read_jsonl(takes[0][2])
        frame = next(l for l in lines if l['kind'] == 'frame' and l['labels'])
        frame['labels'].pop(next(iter(frame['labels'])))
        records.write_jsonl(takes[0][2], lines)
        self.assert_refused(self.score_args(takes, scored=True, meth=meth), 'no label')

    def test_refused_with_a_crossing_that_holds_no_observation(self):
        meth = self.closed_method()
        takes = self.twenty(meth)
        lines = records.read_jsonl(takes[0][2])
        lines.append({'kind': 'crossing', 'crossing_id': 'empty', 'type': 'crossing', 'interval': [5.0, 6.0]})
        records.write_jsonl(takes[0][2], lines)
        self.assert_refused(self.score_args(takes, scored=True, meth=meth), 'empty')

    def test_refused_when_two_crossings_overlap_once_padded(self):
        meth = self.closed_method()
        takes = self.twenty(meth)
        lines = records.read_jsonl(takes[0][2])
        first = next(l for l in lines if l['kind'] == 'crossing')
        lines.append({'kind': 'crossing', 'crossing_id': 'again', 'type': 'crossing', 'interval': first['interval']})
        records.write_jsonl(takes[0][2], lines)
        self.assert_refused(self.score_args(takes, scored=True, meth=meth), 'overlap')

    def test_refused_with_no_track_run_beside_the_tracks(self):
        meth = self.closed_method()
        takes = self.twenty(meth)
        os.remove(os.path.join(os.path.dirname(takes[0][1]), den_track.TRACK_RUN_FILE))
        self.assert_refused(self.score_args(takes, scored=True, meth=meth), den_track.TRACK_RUN_FILE)


class OutputFolderTest(Base):
    def test_a_folder_inside_git_is_refused(self):
        target = os.path.join(HERE, 'not-here')
        code, text = cli('synth', '--scene', 'near_pass', '--out', target)
        self.assertEqual(code, 2)
        self.assertIn('git checkout', text)
        self.assertFalse(os.path.exists(target))

    def test_onedrive_is_refused(self):
        self.assertIn('OneDrive', den_track.inside_private_risk(self.path('OneDrive - Home', 'x')))

    def test_nothing_is_overwritten(self):
        take = self.make_take()
        code, text = cli('track', '--observations', take[0], '--out', os.path.dirname(take[1]))
        self.assertEqual(code, 2)
        self.assertIn('already holds', text)

    def test_a_missing_file_is_a_refusal_not_a_crash(self):
        code, text = cli('track', '--observations', self.path('nope.jsonl'), '--out', self.path('o'))
        self.assertEqual(code, 2)
        self.assertIn('REFUSED', text)

    def test_two_takes_may_not_share_a_name(self):
        take = self.make_take()
        code, text = cli(*self.score_args([take, take]))
        self.assertEqual(code, 2)
        self.assertIn('share a name', text)


class MethodFileTest(Base):
    def test_crlf_and_lf_give_the_same_fingerprint_and_hash(self):
        with open(method.METHOD_PATH, 'rb') as f:
            raw = f.read().replace(b'\r\n', b'\n')
        lf, crlf = self.path('lf.md'), self.path('crlf.md')
        with open(lf, 'wb') as f:
            f.write(raw)
        with open(crlf, 'wb') as f:
            f.write(raw.replace(b'\n', b'\r\n'))
        self.assertEqual(method.Method(lf).fingerprint, method.Method(crlf).fingerprint)
        self.assertEqual(den_track.text_sha256(lf), den_track.text_sha256(crlf))

    def test_closing_a_slot_changes_the_fingerprint(self):
        self.assertNotEqual(method.Method().fingerprint, method.Method(self.closed_method()).fingerprint)

    def test_open_slots_resolve_to_their_proposals_and_block_a_scored_run(self):
        m = method.Method()
        self.assertTrue(m.open)
        for name, slot in m.open:
            self.assertTrue(slot.get('why'), name)
            self.assertIn('proposed', slot)
        self.assertEqual(m.room['floor_m'], [0.0, 0.0, 4.572, 3.759])
        with self.assertRaisesRegex(method.MethodError, 'refusing a scored run'):
            m.require_closed()

    def test_a_broken_block_is_refused(self):
        p = self.path('broken.md')
        for text in ('no block here\n', '```json den-track-method\n{not json\n```\n', '```json den-track-method\n{}\n'):
            with open(p, 'w', encoding='utf-8') as f:
                f.write(text)
            with self.assertRaises(method.MethodError):
                method.read_block(p)

    def test_the_contract_numbers_are_closed(self):
        scoring = method.read_block()['scoring']
        self.assertEqual(scoring['idf1_gate'], 0.9)
        self.assertEqual(scoring['alpha'], 0.05)
        self.assertEqual(scoring['min_crossings'], 20)


class SafetyTest(unittest.TestCase):
    NETWORK = re.compile(r'^\s*(?:import|from)\s+(socket|urllib|http|ssl|ftplib|smtplib|asyncio|requests)\b', re.M)
    ABSOLUTE_DATA = re.compile(r'den-takes|den-core|sofa-look|src\\+img\\+raw', re.I)

    def sources(self):
        for name in sorted(os.listdir(HERE)):
            if name.endswith('.py') and not name.startswith('test_'):
                with open(os.path.join(HERE, name), encoding='utf-8') as f:
                    yield name, f.read()

    def test_the_tool_imports_no_network_module(self):
        for name, text in self.sources():
            self.assertIsNone(self.NETWORK.search(text), name)

    def test_the_tool_names_no_real_data_path(self):
        for name, text in self.sources():
            self.assertIsNone(self.ABSOLUTE_DATA.search(text), name)

    def test_synthetic_people_are_letters(self):
        self.assertEqual({w for s in ('near_pass',) for w in {o['pid'] for f in synth.scenario(s)['frames'] for o in f['obs']}} - {None},
                         {'A', 'B'})


if __name__ == '__main__':
    unittest.main()
