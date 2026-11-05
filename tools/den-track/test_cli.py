"""The command line, the open slots and the refusals, end to end on synthetic files."""
import contextlib
import io
import json
import os
import re
import shutil
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


def reject_person(person):
    """An edit of a take's observations: every observation of this person becomes rejected, with no position."""
    def edit(obs_path, truth_path):
        labels = {}
        for line in records.read_jsonl(truth_path):
            if line['kind'] == 'frame':
                labels.update(line['labels'])
        rows = records.read_jsonl(obs_path)
        for row in rows:
            if labels.get(row['obs_id']) == person:
                row['validity'] = 'rejected:no_room_position:depth_absent'
                row.pop('room_position', None)
        records.write_jsonl(obs_path, rows)
    return edit


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
        block['scoring']['crossings_plan'] = {'crossing': 20}
        if change:
            change(block)
        path = self.path(name)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('```json den-track-method\n%s\n```\n' % json.dumps(block, indent=2))
        return path

    def small_method(self, name='METHOD-small.md', plan=None):
        """A closed method that wants only two crossings, for tests about something else: quicker than twenty."""
        def change(block):
            block['scoring']['min_crossings'] = 2
            block['scoring']['crossings_plan'] = {'crossing': 2} if plan is None else plan
        return self.closed_method(name, change)

    def make_take(self, scene='clean_crossing', seed=0, noise='clean', meth=None, edit_observations=None):
        """Synth, then track. Returns [observations, tracks, truth] paths."""
        folder = self.path('takes', '%s-%d' % (scene, seed))
        code, text = cli('synth', '--scene', scene, '--seed', str(seed), '--noise', noise, '--out', folder)
        self.assertEqual(code, 0, text)
        if edit_observations:
            edit_observations(os.path.join(folder, 'observations.jsonl'), os.path.join(folder, 'truth.jsonl'))
            lines = records.read_jsonl(os.path.join(folder, 'truth.jsonl'))
            lines[0]['observations_sha256'] = den_track.text_sha256(os.path.join(folder, 'observations.jsonl'))
            records.write_jsonl(os.path.join(folder, 'truth.jsonl'), lines)
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

    def test_track_lines_have_no_time_key_until_the_owner_names_one(self):
        take = self.make_take()
        for line in records.read_jsonl(take[1]):
            self.assertNotIn('as_of', line)
            self.assertNotIn('capture_ts', line)

    def test_once_named_every_track_line_carries_its_frame_time_ended_ones_too(self):
        def name_it(block):
            block['record']['time_field'] = 'as_of'
        meth = self.closed_method('METHOD-named.md', name_it)
        take = self.make_take('armchair_occlusion', meth=meth)
        lines = records.read_jsonl(take[1])
        by_id = {o['obs_id']: o['capture_ts'] for o in records.read_jsonl(take[0])}
        for line in lines:
            self.assertIsInstance(line['as_of'], float)
            for oid in line['last_obs_ids']:
                self.assertEqual(line['as_of'], by_id[oid])
        self.assertTrue([l for l in lines if not l['last_obs_ids']], 'a line with no observation now has a time')

    def test_a_time_field_that_is_not_a_new_name_is_refused(self):
        for n, bad in enumerate(('state', 7, '')):
            def name_it(block, bad=bad):
                block['record']['time_field'] = bad
            meth = self.closed_method('METHOD-bad-%d.md' % n, name_it)
            code, text = cli('--method', meth, 'synth', '--scene', 'near_pass', '--out', self.path('t%d' % n))
            self.assertEqual(code, 0, text)
            code, text = cli('--method', meth, 'track', '--observations', self.path('t%d' % n, 'observations.jsonl'),
                             '--out', self.path('t%d' % n, 'track'))
            self.assertEqual(code, 2, text)
            self.assertIn('time_field', text)

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

    def test_one_real_crossing_and_nineteen_empty_windows_are_refused(self):
        meth = self.closed_method()
        take = self.make_take(meth=meth)
        lines = records.read_jsonl(take[2])
        end = max(l['capture_ts'] for l in lines if l['kind'] == 'frame')
        for k in range(19):
            start = end + 10 + 8 * k
            lines.append({'kind': 'crossing', 'crossing_id': 'v%02d' % k, 'type': 'crossing', 'interval': [start, start + 2]})
        records.write_jsonl(take[2], lines)
        self.assert_refused(self.score_args([take], scored=True, meth=meth), 'at least 20')

    def one_person_take(self, seed, meth):
        """A crossing in which B is never scored: every B observation is rejected, as in a depth stall."""
        return self.make_take(seed=seed, meth=meth, edit_observations=reject_person('B'))

    def test_twenty_crossings_with_only_one_person_scored_are_refused_not_passed(self):
        meth = self.closed_method()
        takes = [self.one_person_take(seed, meth) for seed in range(20)]
        self.assert_refused(self.score_args(takes, scored=True, meth=meth), 'at least 20')

    def test_a_refusal_for_too_few_crossings_names_the_ones_left_out(self):
        meth = self.small_method()
        takes = [self.make_take(meth=meth), self.one_person_take(50, meth)]
        self.assert_refused(self.score_args(takes, scored=True, meth=meth), 'clean_crossing-50/c1')

    def test_a_crossing_with_one_person_is_excluded_named_and_not_counted(self):
        meth = self.small_method()
        takes = [self.make_take(seed=seed, meth=meth) for seed in range(2)] + [self.one_person_take(50, meth)]
        code, text = cli(*self.score_args(takes, scored=True, meth=meth))
        self.assertEqual(code, 0, text)
        self.assertIn('EXCLUDED', text)
        self.assertIn('clean_crossing-50/c1', text)
        with open(self.path('score', 'score.json'), encoding='utf-8') as f:
            report = json.load(f)
        self.assertEqual(report['crossings'], 2)
        self.assertEqual(report['crossings_marked'], 3)
        self.assertEqual(report['crossings_excluded'], [{'take': 'clean_crossing-50', 'id': 'c1', 'people_scored': ['A'],
                                                         'because': 'fewer than 2 people scored'}])
        self.assertAlmostEqual(report['silent_swap_rate_upper_95'], 1 - 0.05 ** (1 / 2))

    def test_a_take_cut_short_at_the_contact_is_excluded(self):
        def cut(obs_path, truth_path):
            lines = records.read_jsonl(truth_path)
            start = next(l for l in lines if l['kind'] == 'crossing')['interval'][0]
            kept = [l for l in lines if l['kind'] != 'frame' or l['capture_ts'] < start]
            gone = {o for l in lines if l['kind'] == 'frame' and l['capture_ts'] >= start for o in l['labels']}
            records.write_jsonl(truth_path, kept)
            records.write_jsonl(obs_path, [o for o in records.read_jsonl(obs_path) if o['obs_id'] not in gone])

        meth = self.small_method()
        short = self.make_take(seed=50, meth=meth, edit_observations=cut)
        good = [self.make_take(seed=seed, meth=meth) for seed in range(2)]
        self.assert_refused(self.score_args(good[:1] + [short], scored=True, meth=meth), 'clean_crossing-50/c1')
        code, text = cli(*self.score_args(good + [short], 'score2', scored=True, meth=meth))
        self.assertEqual(code, 0, text)
        self.assertIn('nobody scored after the mark', text)

    def test_a_crossing_that_holds_no_observation_is_excluded_named_and_not_counted(self):
        meth = self.small_method()
        takes = [self.make_take(seed=seed, meth=meth) for seed in range(2)]
        lines = records.read_jsonl(takes[0][2])
        lines.append({'kind': 'crossing', 'crossing_id': 'empty', 'type': 'crossing', 'interval': [5.0, 6.0]})
        records.write_jsonl(takes[0][2], lines)
        code, text = cli(*self.score_args(takes, scored=True, meth=meth))
        self.assertEqual(code, 0, text)
        self.assertIn('EXCLUDED', text)
        self.assertIn('clean_crossing-0/empty', text)
        with open(self.path('score', 'score.json'), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['crossings'], 2)

    def two_takes(self, meth):
        return [self.make_take(seed=seed, meth=meth) for seed in range(2)]

    def test_refused_when_a_kind_falls_short_of_the_plan(self):
        meth = self.small_method(plan={'crossing': 1, 'door': 1})
        self.assert_refused(self.score_args(self.two_takes(meth), scored=True, meth=meth), 'the plan wants 1 door, and 0 count')

    def test_an_excluded_crossing_does_not_fill_the_plan(self):
        meth = self.small_method(plan={'crossing': 3})
        takes = self.two_takes(meth) + [self.one_person_take(50, meth)]
        self.assert_refused(self.score_args(takes, scored=True, meth=meth), 'the plan wants 3 crossing, and 2 count')

    def test_more_than_planned_passes_and_the_report_says_what_counted(self):
        meth = self.small_method(plan={'crossing': 1})
        code, text = cli(*self.score_args(self.two_takes(meth), scored=True, meth=meth))
        self.assertEqual(code, 0, text)
        with open(self.path('score', 'score.json'), encoding='utf-8') as f:
            report = json.load(f)
        self.assertEqual(report['plan'], {'crossing': {'planned': 1, 'counted': 2}})

    def test_a_plan_that_checks_nothing_is_refused(self):
        meth = self.small_method()
        takes = self.two_takes(meth)
        for n, plan in enumerate(({}, {'crossing': 0}, {'dance': 20}, {'crossing': '20'}, [20])):
            other = self.small_method('METHOD-bad-%d.md' % n, plan)
            self.assert_refused(self.score_args(takes, 'bad-%d' % n, scored=True, meth=other), 'crossings_plan')

    def test_a_plan_closed_as_null_is_refused(self):
        def change(block):
            block['scoring']['crossings_plan'] = None
        meth = self.closed_method('METHOD-null.md', change)
        self.assert_refused(self.score_args([self.make_take(meth=meth)], scored=True, meth=meth), 'crossings_plan')

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


def label_span(path, start, end, change):
    """Rewrite the labels of the truth file's frames from start to end, inclusive, in frame order."""
    lines = records.read_jsonl(path)
    frames = sorted((l for l in lines if l['kind'] == 'frame'), key=lambda l: l['capture_ts'])
    for frame in frames[start:end]:
        frame['labels'] = {o: change(p) for o, p in frame['labels'].items()}
    records.write_jsonl(path, lines)


class CannotTellLabelTest(Base):
    """The scenario: the owner cannot tell who is who through the contact, and the people really did change places."""

    SWAP = {'A': 'B', 'B': 'A'}

    def swapped_take(self, meth, seed=0, edge=None):
        """Labels say cannot tell through the crossing's mark, and A and B change places after it."""
        take = self.make_take(seed=seed, meth=meth)
        lines = records.read_jsonl(take[2])
        mark = next(l for l in lines if l['kind'] == 'crossing')['interval']
        frames = sorted((l for l in lines if l['kind'] == 'frame'), key=lambda l: l['capture_ts'])
        for frame in frames:
            if mark[0] <= frame['capture_ts'] <= mark[1] + (edge or 0.0):
                frame['labels'] = {o: ('?' if p else p) for o, p in frame['labels'].items()}
            elif frame['capture_ts'] > mark[1]:
                frame['labels'] = {o: self.SWAP.get(p, p) for o, p in frame['labels'].items()}
        records.write_jsonl(take[2], lines)
        return take

    def score(self, meth, takes):
        code, text = cli(*self.score_args(takes, scored=True, meth=meth))
        with open(self.path('score', 'score.json'), encoding='utf-8') as f:
            return code, text, json.load(f)

    def test_a_label_of_cannot_tell_is_read_and_counted_not_refused(self):
        meth = self.small_method()
        takes = [self.make_take(seed=seed, meth=meth) for seed in range(2)]
        label_span(takes[0][2], 0, 2, lambda p: '?' if p else p)
        code, text, report = self.score(meth, takes)
        self.assertEqual(code, 0, text)
        self.assertGreater(report['left_out']['unsure'], 0)
        self.assertEqual(report['left_out']['unlabelled'], 0)
        self.assertIn('cannot tell', text)

    def test_a_swap_under_doubt_is_not_called_silent_because_the_labels_resume_late(self):
        meth = self.small_method()
        takes = [self.swapped_take(meth, seed) for seed in range(2)]
        _, text, report = self.score(meth, takes)
        self.assertEqual(report['silent_swaps'] + report['silent_swaps_outside_crossings'], 0, text)
        self.assertGreater(report['unresolved_switches'] + report['flagged_switches'], 0)
        self.assertTrue(report['gate']['silent_swaps_zero'])

    def test_unresolved_switches_are_reported_and_fail_the_gate_when_the_owner_reads_them_as_silent(self):
        def strict(block):
            block['scoring'].update(min_crossings=2, crossings_plan={'crossing': 2}, unresolved_switch='silent')
        meth = self.closed_method('METHOD-strict.md', strict)
        takes = [self.swapped_take(meth, seed, edge=0.75) for seed in range(2)]
        code, text, report = self.score(meth, takes)
        self.assertGreater(report['unresolved_switches'], 0, text)
        self.assertFalse(report['gate']['silent_swaps_zero'])
        self.assertEqual(code, 1, text)
        self.assertIn('unresolved', text)

    def test_an_unknown_reading_of_unresolved_switches_is_refused(self):
        def bad(block):
            block['scoring'].update(min_crossings=2, crossings_plan={'crossing': 2}, unresolved_switch='ignore')
        meth = self.closed_method('METHOD-bad.md', bad)
        takes = [self.make_take(seed=seed, meth=meth) for seed in range(2)]
        code, text = cli(*self.score_args(takes, scored=True, meth=meth))
        self.assertEqual(code, 2, text)
        self.assertIn('unresolved_switch', text)

    def test_all_cannot_tell_in_a_crossing_is_excluded_not_counted(self):
        meth = self.small_method()
        take = self.make_take(seed=50, meth=meth)
        label_span(take[2], 0, 100000, lambda p: '?' if p else p)
        good = [self.make_take(seed=seed, meth=meth) for seed in range(2)]
        code, text, report = self.score(meth, good + [take])
        self.assertEqual(code, 0, text)
        self.assertEqual((report['crossings'], report['crossings_marked']), (2, 3))
        self.assertIn('clean_crossing-50/c1', text)


class LabellingTest(Base):
    """Owner choice 2: labelled alone, blind to system output, a single labeller reported."""

    def setUp(self):
        super().setUp()
        self.meth = self.small_method()
        self.takes = [self.make_take(seed=seed, meth=self.meth) for seed in range(2)]

    def header(self, change, take=0):
        lines = records.read_jsonl(self.takes[take][2])
        change(lines[0])
        records.write_jsonl(self.takes[take][2], lines)

    def assert_refused(self, needle):
        code, text = cli(*self.score_args(self.takes, scored=True, meth=self.meth))
        self.assertEqual(code, 2, text)
        self.assertIn(needle, text)
        self.assertFalse(os.path.exists(self.path('score', 'score.json')))

    def test_labels_made_after_seeing_the_tracker_are_refused(self):
        self.header(lambda h: h.update(blind=False))
        self.assert_refused('blind')

    def test_a_truth_file_that_does_not_say_how_it_was_labelled_is_refused(self):
        self.header(lambda h: h.pop('blind'))
        self.assert_refused('blind')
        self.header(lambda h: h.update(blind=True))
        self.header(lambda h: h.pop('labeller'))
        self.assert_refused('labeller')

    def test_labels_for_other_observations_are_refused(self):
        self.header(lambda h: h.update(observations_sha256='sha256:' + '0' * 64))
        self.assert_refused('labels other observations')
        self.header(lambda h: h.pop('observations_sha256'))
        self.assert_refused('observations_sha256')

    def test_two_labellers_are_refused(self):
        self.header(lambda h: h.update(labeller='A'))
        self.assert_refused('single labeller')

    def test_the_report_says_what_the_truth_files_say_not_a_fixed_claim(self):
        code, text = cli(*self.score_args(self.takes, scored=True, meth=self.meth))
        self.assertEqual(code, 0, text)
        with open(self.path('score', 'score.json'), encoding='utf-8') as f:
            report = json.load(f)
        self.assertEqual(report['labelling'], {'labellers': ['synthetic'], 'takes': 2, 'declared_blind': 2})
        self.assertEqual((report['takes'][0]['labeller'], report['takes'][0]['blind']), ('synthetic', True))

    def test_a_practice_run_reports_a_truth_file_that_claims_nothing(self):
        self.header(lambda h: (h.pop('blind'), h.pop('labeller')))
        code, text = cli(*self.score_args(self.takes[:1]))
        self.assertEqual(code, 0, text)
        with open(self.path('score', 'score.json'), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['labelling'], {'labellers': [], 'takes': 1, 'declared_blind': 0})

    def test_the_hash_command_prints_what_a_truth_file_names(self):
        code, text = cli('hash', '--observations', self.takes[0][0])
        self.assertEqual(code, 0, text)
        self.assertEqual(text.strip(), records.read_jsonl(self.takes[0][2])[0]['observations_sha256'])


class FreshTakesTest(Base):
    """A take that has been scored is used: looking again after a retune needs fresh takes (the ladder's rule)."""

    def setUp(self):
        super().setUp()
        self.meth = self.small_method()

    def takes(self, first=0, meth=None):
        return [self.make_take(seed=seed, meth=meth or self.meth) for seed in range(first, first + 2)]

    def retrack(self, takes, name, meth=None):
        out = []
        for obs, tracks, truth in takes:
            folder = os.path.join(os.path.dirname(os.path.dirname(tracks)), name)
            code, text = cli('--method', meth or self.meth, 'track', '--observations', obs, '--out', folder)
            self.assertEqual(code, 0, text)
            out.append([obs, os.path.join(folder, 'tracks.jsonl'), truth])
        return out

    def score_once(self, takes):
        code, text = cli(*self.score_args(takes, 'score-1', scored=True, meth=self.meth))
        self.assertEqual(code, 0, text)
        with open(self.path('score-1', 'score.json'), 'rb') as f:
            return f.read()

    def retune_the_code(self):
        """From here on the tool's source is a copy with one tracker line changed, and METHOD.md is untouched."""
        folder = self.path('tool-copy')
        shutil.copytree(HERE, folder, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        path = os.path.join(folder, 'tracker_core.py')
        with open(path, encoding='utf-8', newline='') as f:
            text = f.read()
        self.assertEqual(text.count("if risk < self.p['risk_floor']:"), 1)
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(text.replace("if risk < self.p['risk_floor']:", 'if risk < 0.6:'))
        patcher = mock.patch.object(method, 'CODE_DIR', folder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_refused_as_used(self, args):
        out = args[args.index('--out') + 1]
        code, text = cli(*args)
        self.assertEqual(code, 2, text)
        self.assertIn('already scored', text)
        self.assertIn('fresh takes', text)
        self.assertFalse(os.path.exists(os.path.join(out, 'score.json')))

    def test_the_same_takes_after_a_tracker_code_change_are_refused_and_the_first_report_stays(self):
        takes = self.takes()
        first = self.score_once(takes)
        self.retune_the_code()
        self.assert_refused_as_used(self.score_args(self.retrack(takes, 'track-2'), 'score-2', scored=True, meth=self.meth))
        with open(self.path('score-1', 'score.json'), 'rb') as f:
            self.assertEqual(f.read(), first)

    def test_the_same_takes_under_a_changed_method_are_refused(self):
        takes = self.takes()
        self.score_once(takes)
        def nudge(block):
            block['scoring'].update(min_crossings=2, crossings_plan={'crossing': 2})
            block['tracker']['accel_sigma'] = 0.7
        other = self.closed_method('METHOD-other.md', nudge)
        self.assert_refused_as_used(self.score_args(self.retrack(takes, 'track-2', other), 'score-2', scored=True, meth=other))

    def test_labels_changed_after_a_score_are_refused(self):
        takes = self.takes()
        self.score_once(takes)
        lines = records.read_jsonl(takes[0][2])
        frame = next(l for l in lines if l['kind'] == 'frame' and l['labels'])
        frame['labels'] = {o: {'A': 'B', 'B': 'A'}.get(p, p) for o, p in frame['labels'].items()}
        records.write_jsonl(takes[0][2], lines)
        self.assert_refused_as_used(self.score_args(takes, 'score-2', scored=True, meth=self.meth))

    def test_a_replay_with_nothing_changed_gives_the_same_report(self):
        takes = self.takes()
        first = self.score_once(takes)
        code, text = cli(*self.score_args(takes, 'score-2', scored=True, meth=self.meth))
        self.assertEqual(code, 0, text)
        with open(self.path('score-2', 'score.json'), 'rb') as f:
            self.assertEqual(f.read(), first)
        retracked = self.retrack(takes, 'track-2')
        code, text = cli(*self.score_args(retracked, 'score-3', scored=True, meth=self.meth))
        self.assertEqual(code, 0, text)

    def test_fresh_takes_are_scored_after_a_retune(self):
        self.score_once(self.takes())
        self.retune_the_code()
        code, text = cli(*self.score_args(self.takes(first=100), 'score-2', scored=True, meth=self.meth))
        self.assertEqual(code, 0, text)
        self.assertIn('SCORED', text)

    def test_tracks_made_before_a_code_change_are_refused_on_a_first_score(self):
        takes = self.takes()
        self.retune_the_code()
        args = self.score_args(takes, scored=True, meth=self.meth)
        code, text = cli(*args)
        self.assertEqual(code, 2, text)
        self.assertIn('another version of the code', text)
        self.assertFalse(os.path.exists(self.path('score', 'score.json')))
        code, text = cli(*self.score_args(self.retrack(takes, 'track-2'), 'score-2', scored=True, meth=self.meth))
        self.assertEqual(code, 0, text)

    def test_a_take_read_in_practice_under_other_numbers_is_used(self):
        takes = self.takes()
        code, text = cli(*self.score_args(takes, 'peek'))
        self.assertEqual(code, 0, text)
        self.assertIn('PRACTICE', text)
        self.assert_refused_as_used(self.score_args(self.retrack(takes, 'track-2'), 'score-1', scored=True, meth=self.meth))

    def test_practice_is_never_refused_on_a_used_take(self):
        takes = self.takes()
        self.score_once(takes)
        code, text = cli(*self.score_args(takes, 'practice-after'))
        self.assertEqual(code, 0, text)

    def test_the_report_names_the_code_and_the_note_holds_only_hashes(self):
        takes = self.takes()
        self.score_once(takes)
        with open(self.path('score-1', 'score.json'), encoding='utf-8') as f:
            report = json.load(f)
        self.assertEqual(report['method']['code_fingerprint'], method.code_fingerprint())
        notes = records.read_jsonl(os.path.splitext(takes[0][2])[0] + den_track.SCORE_RUNS_SUFFIX)
        self.assertEqual(len(notes), 1)
        self.assertEqual(set(notes[0]), {'method_fingerprint', 'code_fingerprint', 'observations_sha256',
                                         'tracks_sha256', 'truth_sha256'})
        self.assertTrue(all(v.startswith('sha256:') for v in notes[0].values()))


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

    def test_the_code_fingerprint_follows_the_source_not_the_tests_or_line_endings(self):
        folder = self.path('source')
        os.makedirs(folder)
        with open(os.path.join(folder, 'tracker.py'), 'wb') as f:
            f.write(b'x = 1\n')
        with open(os.path.join(folder, 'test_tracker.py'), 'wb') as f:
            f.write(b'y = 1\n')
        before = method.code_fingerprint(folder)
        with open(os.path.join(folder, 'tracker.py'), 'wb') as f:
            f.write(b'x = 1\r\n')
        with open(os.path.join(folder, 'test_tracker.py'), 'wb') as f:
            f.write(b'y = 2\n')
        with open(os.path.join(folder, 'notes.md'), 'wb') as f:
            f.write(b'not code\n')
        self.assertEqual(method.code_fingerprint(folder), before)
        with open(os.path.join(folder, 'tracker.py'), 'wb') as f:
            f.write(b'x = 2\n')
        self.assertNotEqual(method.code_fingerprint(folder), before)

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

    def test_how_to_read_an_unresolved_switch_is_an_open_slot(self):
        m = method.Method()
        self.assertIn('scoring.unresolved_switch', [name for name, _ in m.open])
        self.assertEqual(m.scoring['unresolved_switch'], 'flagged')

    def test_the_time_field_is_an_open_slot_that_keeps_to_the_schema_as_proposed(self):
        m = method.Method()
        self.assertIn('record.time_field', [name for name, _ in m.open])
        self.assertIsNone(m.time_field)

    def test_the_plan_is_an_open_slot_with_the_readmes_script_as_its_proposal(self):
        m = method.Method()
        self.assertIn('scoring.crossings_plan', [name for name, _ in m.open])
        self.assertEqual(m.scoring['crossings_plan'], {'crossing': 12, 'crossing_occluded': 5, 'occlusion': 3,
                                                       'near_pass': 3, 'door': 2})

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
