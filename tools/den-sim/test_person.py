"""Test 11 (person) whole, test 8 on person states, and hop 13's one need per track (plan, "Done means"), on typed,
invented fixtures: no prepared set, no real room. need.py is den-core's, unchanged, read by `git show` at the pin
refs.json names and loaded by file path; the runner comparison archives den-core at that pin. Canon's field names
are read by `git show` too, and those parts skip when the commit is absent. Each check has a control that must fail.

    python -B -X utf8 tools\\den-sim\\test_person.py
"""
import copy
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import chain  # noqa: E402  installs chain.py's socket refusal in this process too
import person  # noqa: E402

PIN = json.loads((HERE / 'refs.json').read_text(encoding='utf-8'))['tools']['den-core-live']
CANON = '6772ed39'
T0 = 1800000000.0
SEAT = [2.0, 2.5, 0.45]
ROOM = [{'room_id': 'den', 'version': 'den-v2', 'cameras': [], 'landmarks': [{'landmark_id': 'sofa-seat', 'kind': 'seat', 'surface_id': 'sofa'}],
         'surfaces': [{'surface_id': 'sofa', 'kind': 'seat', 'focal': False, 'geometry': {'form': 'polygon-3d/1', 'vertices_m': [[1.0, 2.0, 0.45], [3.0, 2.0, 0.45], [3.0, 3.0, 0.45], [1.0, 3.0, 0.45]],
                                                                                         'centre_m': SEAT, 'normal_unit': [0, 0, 1], 'seat_point_m': SEAT, 'book_point_m': [2.0, 2.5, 0.75]}}]}]
POSES = {'seated': dict(shoulders=0.85, hips=0.55, face=1.1), 'standing': dict(shoulders=1.45, hips=1.0, face=1.65)}


def git(*args):
    return subprocess.run(['git', '-C', str(HERE.parents[1]), *args], capture_output=True)


def iso(t):
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(t)) + '.%03dZ' % round((t % 1) * 1000)


def scene(pose='seated', xy=(2.0, 2.4), hips=True, safe=(False, True, True, True, True), activity='reading', tracks=('trk-1',), late=None, confident=True):
    """One take's worth of hop 7 to 12 output for typed people: per track five requests 4 s apart, each reply 1 s later."""
    out = dict(asks=[], bundles=[], facts=[], tracks=[], observations=[])
    for k, track in enumerate(tracks):
        heights = POSES[pose]
        for i, sent in enumerate(T0 + 2 + 4 * n + 0.01 * k for n in range(len(safe))):
            entries, positions = [], []
            for ts in (sent - 1.5, sent - 1.0, sent - 0.5):
                joints = [dict(j=j, xyz=[xy[0], xy[1], heights['face']], status='supported') for j in person.FACE]
                joints += [dict(j=j, xyz=[xy[0] + 0.1 * (j % 2), xy[1], heights['shoulders']], status='supported') for j in person.SHOULDERS]
                joints += [dict(j=j, xyz=[xy[0], xy[1], heights['hips']], status='supported') for j in person.HIPS if hips]
                oid = 'o-%s-%.2f' % (track, ts)
                entries.append(dict(obs_id=oid, ts=ts, joints_room=joints))
                positions.append(dict(ts=ts, x=xy[0], y=xy[1], cov=[[0.01, 0.0], [0.0, 0.01]]))
                arrives = late if late is not None and i == len(safe) - 1 and ts == sent - 0.5 else ts + 0.2
                out['observations'].append(dict(obs_id=oid, capture_ts=ts, freshness=dict(available_ts=arrives)))
                heading = dict(deg=90.0, sd_deg=10.0) if late is None or (i == len(safe) - 1 and ts == sent - 0.5) else None
                out['tracks'].append(dict(track_id=track, association=dict(status='confident' if confident else 'ambiguous'), last_obs_ids=[oid], heading=heading, heading_as_of=ts if heading else None))
            body, request = '%s%02d' % (track, i), 'rq-%s-%d' % (track, i)
            out['bundles'].append(dict(bundle_id='pb-' + request, track_id=track, interval=[sent - 4, sent], association_safe=safe[i], positions=positions, keypoint_series=entries))
            out['asks'].append(dict(request_id=request, bundle_id='pb-' + request, track_id=track, sent_ts=sent, association_safe=safe[i], body_sha256=body))
            facts = dict(people_visible='one', activity_hypothesis=activity, held_object='book' if activity == 'reading' else 'phone')
            out['facts'].append(dict(schema='la-fact-log-1', source_id='den_sim_la_direct', session_id='typed', engine_epoch='typed', prompt_version='typed',
                                     request_id=body, outcome='completed', observed_at=iso(sent - 0.5), sent_at=iso(sent), available_at=iso(sent + 1.0),
                                     latency_s=1.0, observation=dict(status='valid', facts=facts)))
    return out


class Need:
    """need.py at the pin, read by git show and loaded by file path, unchanged."""
    folder = tempfile.mkdtemp(prefix='den-sim-need-')
    path = Path(folder) / 'need.py'
    path.write_bytes(git('show', '%s:tools/den-core/need.py' % PIN).stdout)
    module = person.load_need(path)


def run(fixture, room=ROOM, need=None):
    return person.states(fixture['asks'], fixture['bundles'], fixture['facts'], fixture['tracks'], fixture['observations'], room, need or Need.module)


class Test11(unittest.TestCase):
    def test_seated_gives_sofa_book_and_standing_one_metre_off_gives_handheld(self):
        records, [card] = run(scene())
        self.assertEqual((records[-1]['working_region']['surface_id'], card['on_couch'], card['failed'], card['region'], card['torso_from']),
                         ('sofa-book', True, [], 'sofa-book', 'shoulders and hips'))
        self.assertEqual(card['engagement'], 'engaged')
        records, [card] = run(scene('standing', xy=(2.0, 4.0)))   # control: 1 m off the footprint's edge, standing
        self.assertEqual((records[-1]['working_region']['surface_id'], card['on_couch'], card['failed'], card['footprint_m']), ('handheld', False, ['position', 'height'], 1.0))

    def test_hips_dropped_still_pass_on_shoulders_alone_and_standing_with_hips_dropped_fails(self):
        _, [card] = run(scene(hips=False))
        self.assertEqual((card['on_couch'], card['torso_from'], card['region']), (True, 'shoulders alone', 'sofa-book'))
        _, [card] = run(scene('standing', hips=False))   # control: on the footprint, but shoulders 1.45 m over the floor
        self.assertEqual((card['on_couch'], card['failed'], card['torso_from'], card['torso_margin_m']), (False, ['height'], 'shoulders alone', 0.25))

    def test_an_unsafe_bundle_or_a_track_not_confident_holds(self):
        records, [card] = run(scene())
        first = records[0]
        self.assertEqual((first['flags'], first['working_region'], first['field_support']['focused_engagement']['support_status'],
                          first['field_support']['focused_engagement']['hold_reason'], first['focused_engagement']['state']),
                         (['association_uncertain'], None, 'held_ambiguous', 'association', 'none'))
        self.assertEqual(len(card['need_states']), 2, 'need saw four replies, not five: starting then engaged')
        held, _ = run(scene(confident=False, safe=(True,) * 5))
        self.assertTrue(all(r['flags'] == ['association_uncertain'] and r['working_region'] is None for r in held), 'a Track not confident holds too')
        records, _ = run(scene(safe=(True,) * 5))   # control: every bundle safe, nothing held
        self.assertEqual((records[0]['flags'], records[0]['working_region']['surface_id']), ([], 'sofa-book'))

    def test_phone_gives_no_engagement(self):
        records, [card] = run(scene(activity='phone_use'))
        self.assertEqual({r['focused_engagement']['state'] for r in records}, {'none'})
        self.assertEqual((card['engagement'], card['on_couch'], card['region']), ('none', True, 'sofa-book'), 'on the couch, not reading')
        self.assertEqual(run(scene())[1][0]['engagement'], 'engaged', 'control: reading engages')

    def test_a_track_line_whose_observations_arrive_after_the_reply_gives_no_heading(self):
        last = T0 + 2 + 4 * 4 + 1.0   # the last reply's available_at
        records, _ = run(scene(late=last + 1.0))
        self.assertEqual((records[-1]['heading'], records[-1]['field_support']['heading']['support_status']), (None, 'held_unavailable'))
        records, _ = run(scene(late=last - 0.1))   # control: the same line, arrived in time
        self.assertEqual(records[-1]['heading'], {'deg': 90.0, 'sd_deg': 10.0})

    def test_need_runs_once_per_track_and_two_tracks_each_get_their_own(self):
        made = []
        counting = type('Counting', (), dict(make_policy=staticmethod(lambda: made.append(1) or Need.module.make_policy()), parse_iso=staticmethod(Need.module.parse_iso)))
        both = scene(tracks=('trk-1',))
        phone = scene(activity='phone_use', tracks=('trk-1', 'trk-2'))
        for key in both:   # trk-1 reads, trk-2 is on a phone, their replies interleaved in time
            both[key] += [row for row in phone[key] if 'trk-2' in json.dumps(row)]
        _, cards = run(both, need=counting)
        self.assertEqual((len(made), [(c['track_id'], c['engagement']) for c in cards]), (2, [('trk-1', 'engaged'), ('trk-2', 'none')]))
        shared = Need.module.make_policy()   # control: one need for both tracks mixes their words, and nobody engages
        _, cards = run(both, need=type('Shared', (), dict(make_policy=staticmethod(lambda: shared), parse_iso=staticmethod(Need.module.parse_iso))))
        self.assertNotEqual(cards[0]['engagement'], 'engaged')

    def test_nothing_after_a_replys_available_at_is_used(self):
        fixture, t = scene(), T0 + 2 + 4 * 2 + 1.0   # the third reply's available_at
        before = [r for r in run(fixture)[0] if r['emitted_ts'] <= t]
        later = copy.deepcopy(fixture)
        for fact in later['facts'][3:]:
            fact['observation']['facts']['activity_hypothesis'] = 'phone_use'
        for line in later['tracks']:
            line['heading'] = dict(deg=10.0, sd_deg=1.0) if line['last_obs_ids'][0].endswith(tuple('%.2f' % (T0 + 2 + 4 * n - 0.5) for n in (3, 4))) else line['heading']
        later['bundles'][2]['positions'].append(dict(ts=t + 0.5, x=9.0, y=9.0, cov=None))   # an entry of its own bundle past the reply
        later['observations'].append(dict(obs_id='o-late', capture_ts=t - 0.1, freshness=dict(available_ts=t + 3.0)))
        later['tracks'].append(dict(track_id='trk-1', association=dict(status='ambiguous'), last_obs_ids=['o-late'], heading=dict(deg=1.0, sd_deg=1.0), heading_as_of=t - 0.1))
        self.assertEqual([r for r in run(later)[0] if r['emitted_ts'] <= t], before)
        early = copy.deepcopy(later)
        early['bundles'][2]['positions'][-1]['ts'] = t - 0.1   # control: the same entry, known by then, moves the person
        self.assertNotEqual([r for r in run(early)[0] if r['emitted_ts'] <= t], before)

    def test_no_couch_in_the_room_model_reads_why(self):
        with self.assertRaisesRegex(person.NoCouch, 'hop 14 wrote none'):
            run(scene(), room=[dict(ROOM[0], surfaces=[], landmarks=[])])
        self.assertTrue(run(scene())[0], 'control: with the couch, states')


class Test8(unittest.TestCase):
    def test_the_same_input_twice_gives_byte_identical_person_states(self):
        folder = Path(tempfile.mkdtemp(prefix='den-sim-person-'))
        for name in ('once', 'twice'):
            person.write(folder / name, run(scene())[0])
        once, twice = (folder / 'once').read_bytes(), (folder / 'twice').read_bytes()
        self.assertEqual(once, twice)
        records = run(scene())[0]
        records[-1]['read_at'] = time.time()   # control: a clock read planted in a record
        person.write(folder / 'planted', records)
        self.assertNotEqual((folder / 'planted').read_bytes(), once)


class Canon(unittest.TestCase):
    def test_person_states_use_canons_names_and_leave_null_what_hop_15_lists(self):
        shown = git('show', '%s:docs/architecture/live-v1/schemas.json' % CANON)
        if shown.returncode:
            self.skipTest('commit %s is not on this PC' % CANON)
        record = next(r for r in json.loads(shown.stdout)['records'] if r['id'] == 'person_state')
        names = {part.strip() for field in record['fields'] for part in field['name'].split('/')}
        written = run(scene())[0][-1]
        self.assertEqual(sorted(set(written) - names), [])
        self.assertEqual(set(written['focused_engagement']) - set(record['example']['focused_engagement']), {'kind', 'onset_ts'}, 'the dialect canon allows')
        self.assertLessEqual(set(written['field_support']['position']), set(record['example']['field_support']['position']))
        self.assertEqual(sorted(set(dict(written, seat_landmark=None)) - names), ['seat_landmark'], 'control: a name canon lacks is found')
        nulls = [written['attention'][k] for k in ('head_dir', 'head_cov', 'confidence')] + [written[k] for k in ('embedding', 'episodes', 'motion', 'affected')]
        nulls += [written['visual_demand']['level'], written['visual_demand']['confidence'], written['focused_engagement']['confidence'], written['working_region']['confidence']]
        self.assertEqual(set(map(json.dumps, nulls)), {'null'})

    def test_every_number_is_a_named_constant(self):
        lines = (HERE / 'person.py').read_text(encoding='utf-8').split('\n')
        constant = re.compile(r'^[A-Z_]+(, [A-Z_]+)* = ')   # zero and one, as a clamp or the floor's height, are no threshold
        loose = lambda text: [line for line in text if re.search(r'(?<![\w.])(?!0\.0\b|1\.0\b)\d+\.\d+', line.split('#')[0]) and not constant.match(line)]
        self.assertEqual(loose(lines), [])
        self.assertEqual(len(loose(lines + ['    if away > 0.15:'])), 1, 'control: a bare number is found')


class Hop13(unittest.TestCase):
    def test_the_runner_pass_and_the_need_per_track_give_the_same_states_on_the_same_lines(self):
        folder = Path(tempfile.mkdtemp(prefix='den-sim-core-'))
        with tarfile.open(fileobj=io.BytesIO(git('archive', '--format=tar', PIN, 'tools/den-core').stdout)) as archive:
            archive.extractall(folder, filter='data')
        fixture = scene(safe=(True,) * 5)
        fixture['facts'][1]['observation']['facts']['activity_hypothesis'] = 'phone_use'   # reading, phone, then reading: a dropped line shows

        def runner_states(facts):
            (folder / 'facts.jsonl').write_text(''.join(json.dumps(line) + '\n' for line in facts), encoding='utf-8')
            return chain.step_core(dict(core_tree=str(folder), facts=str(folder / 'facts.jsonl'), log=str(folder / 'log.jsonl')))['states']
        _, [card] = run(fixture)
        self.assertEqual(card['need_states'], runner_states(fixture['facts']))
        unsafe = copy.deepcopy(fixture)   # control: the first bundle unsafe, so person.py hands need four of the five lines
        unsafe['asks'][0]['association_safe'] = unsafe['bundles'][0]['association_safe'] = False
        self.assertNotEqual(run(unsafe)[1][0]['need_states'], runner_states(fixture['facts']))
        self.assertEqual(run(unsafe)[1][0]['need_states'], runner_states(fixture['facts'][1:]), 'and on the four it took, the two agree again')


if __name__ == '__main__':
    unittest.main(verbosity=2)
