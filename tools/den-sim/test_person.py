"""Test 11 (person) whole, test 8 on person states, and hop 13's one need per track (plan, "Done means"), on typed,
invented fixtures: no prepared set, no real room. need.py is den-core's, unchanged, read by `git show` at the pin
refs.json names and loaded by file path; the runner comparison archives den-core at that pin. Canon's field names
are read by `git show` too, and those parts skip when the commit is absent. Each check has a control that must fail.

    python -B -X utf8 tools\\den-sim\\test_person.py
"""
import copy
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

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


def loaded_need():
    """need.py at the pin, read by git show and loaded by file path, unchanged. Its folder goes once it is loaded."""
    with tempfile.TemporaryDirectory(prefix='den-sim-need-') as folder:
        path = Path(folder) / 'need.py'
        path.write_bytes(git('show', '%s:tools/den-core/need.py' % PIN).stdout)
        return person.load_need(path)


class Need:
    module = loaded_need()


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
        self.assertEqual((card['replies'], card['need_replies'], card['engaged_reply']), (5, 4, 3), 'the first held, so it engaged at the third')
        held, cards = run(scene(confident=False, safe=(True,) * 5))
        self.assertEqual((cards[0]['held'], cards[0]['held_why']), (True, 'track'), 'the card says the Track held it, not the bundle')
        self.assertEqual(run(scene(safe=(False,) * 5))[1][0]['held_why'], 'bundle', 'control: an unsafe bundle')
        self.assertTrue(all(r['flags'] == ['association_uncertain'] and r['working_region'] is None for r in held), 'a Track not confident holds too')
        records, [card] = run(scene(safe=(True,) * 5))   # control: every bundle safe, nothing held
        self.assertEqual((records[0]['flags'], records[0]['working_region']['surface_id']), ([], 'sofa-book'))
        self.assertEqual((card['replies'], card['need_replies'], card['engaged_reply']), (5, 5, 2))

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


def seat_bundle(frames, t=100.0):
    """A typed bundle for person.seated: frames is [(seconds before t, {joint: (height, status)})], at the seat's own xy."""
    return {'keypoint_series': [{'ts': t - before, 'joints_room': [dict(j=j, xyz=[2.0, 2.5, z], status=status) for j, (z, status) in joints.items()]}
                                for before, joints in frames]}


def torso(shoulders, hips=None, status='supported', hip_status='supported'):
    return {**{j: (shoulders, status) for j in person.SHOULDERS}, **({j: (hips, hip_status) for j in person.HIPS} if hips is not None else {})}


class Seated(unittest.TestCase):
    """Step 5's two halves at their limits, and which joints count, on the couch ROOM holds (seat top 0.45 m)."""
    COUCH = ROOM[0]['surfaces'][0]['geometry']
    SITTING = seat_bundle([(0.5 * k, torso(0.85, 0.55)) for k in range(3)])

    def on(self, xy, bundle=None, couch=None):
        return person.seated(xy, bundle or self.SITTING, couch or self.COUCH, 100.0)

    def test_the_footprint_reach_at_its_limit_and_on_a_slanted_edge(self):
        self.assertEqual([self.on([3.0 + d, 2.5])['on'] for d in (0.149, 0.15, 0.151)], [True, True, False], 'east of an edge, by 0.149, 0.15 and 0.151 m')
        self.assertEqual([self.on([2.0, 2.0 - d])['on'] for d in (0.149, 0.151)], [True, False])
        diamond = dict(self.COUCH, vertices_m=[[1.0, 0.0, 0.45], [0.0, 1.0, 0.45], [-1.0, 0.0, 0.45], [0.0, -1.0, 0.45]], seat_point_m=[0.0, 0.0, 0.45])
        out = lambda d: [0.5 + d / 2 ** 0.5] * 2   # d metres out along a 45 degree edge's normal
        self.assertEqual([self.on(out(d), couch=diamond)['failed'] for d in (0.15, 0.151)], [[], ['position']], 'exactly 0.15 m off a slanted edge is in reach')
        self.assertGreater(person.edge_distance(*out(0.15), [v[:2] for v in diamond['vertices_m']]), person.REACH_M, 'control: the raw float lies past 0.15')

    def test_the_torso_median_at_its_limit(self):
        at = lambda median: seat_bundle([(0.5 * k, torso(median + 0.1, median - 0.1)) for k in range(3)])   # an even count: the median is the middle
        self.assertEqual([self.on([2.0, 2.5], at(0.45 + m))['failed'] for m in (0.449, 0.45, 0.451)], [[], [], ['height']])

    def test_only_supported_joints_count(self):
        uncertain = seat_bundle([(0.5 * k, torso(1.1, 1.5, hip_status='uncertain')) for k in range(3)])
        self.assertEqual((self.on([2.0, 2.5], uncertain)['on'], self.on([2.0, 2.5], uncertain)['torso_from']), (True, 'shoulders alone'))
        supported = seat_bundle([(0.5 * k, torso(1.1, 1.5)) for k in range(3)])   # control: the same hips supported count, and fail
        self.assertEqual(self.on([2.0, 2.5], supported)['failed'], ['height'])

    def test_the_median_holds_against_an_outlier(self):
        frames = [(0.5 * k, torso(0.85, 0.55)) for k in range(3)] + [(0.2, {person.HIPS[0]: (5.0, 'supported')})]
        heights = [0.85] * 6 + [0.55] * 6 + [5.0]
        self.assertEqual(self.on([2.0, 2.5], seat_bundle(frames))['failed'], [])
        self.assertGreater(sum(heights) / len(heights), 0.45 + person.ABOVE_SEAT_M, 'control: their mean is over the limit')

    def test_only_the_last_two_seconds_before_t_count(self):
        high = torso(2.0, 2.0)
        old = seat_bundle([(0.5 * k, torso(0.85, 0.55)) for k in range(3)] + [(2.5 + 0.1 * k, high) for k in range(5)])
        self.assertEqual(self.on([2.0, 2.5], old)['failed'], [], 'frames over 2 s before the newest are left out')
        inside = seat_bundle([(0.5 * k, torso(0.85, 0.55)) for k in range(3)] + [(1.5 + 0.1 * k, high) for k in range(5)])
        self.assertEqual(self.on([2.0, 2.5], inside)['failed'], ['height'], 'control: the same frames inside 2 s count')
        late = seat_bundle([(0.5 * k, torso(0.85, 0.55)) for k in range(3)] + [(-0.5 - 0.1 * k, high) for k in range(5)])
        self.assertEqual(self.on([2.0, 2.5], late)['failed'], [], 'frames after t are left out')


class Support(unittest.TestCase):
    """Canon: a support time is the capture time of the newest accepted evidence, so nothing is presented fresher than it."""

    def test_no_field_is_fresher_than_the_evidence_it_used(self):
        fixture = scene()
        captures = {b['bundle_id']: max(e['ts'] for e in b['keypoint_series'] + b['positions']) for b in fixture['bundles']}
        records, _ = run(fixture)
        for record in records:
            newest = captures[record['sources']['position'][0]['ref']]
            for name, ts in record['field_as_of'].items():
                self.assertTrue(ts is None or ts <= newest, '%s at %r, after the newest capture it rests on' % (name, ts))
            self.assertLess(newest, record['emitted_ts'], 'control: each reply arrives after its captures')
        safe = records[1]
        self.assertEqual({name: value['support_status'] for name, value in safe['field_support'].items()},
                         dict(position='observed', heading='observed', working_region='observed', attention='observed', focused_engagement='observed'))
        self.assertEqual(safe['field_as_of']['working_region'], captures[safe['sources']['position'][0]['ref']])
        self.assertEqual([s['ref'] for s in safe['sources']['working_region']], [safe['sources']['position'][0]['ref'], 'sofa-seat'])

    def test_the_head_is_the_median_of_the_supported_face_joints(self):
        fixture = scene(safe=(True,) * 5)
        newest = max(fixture['bundles'][-1]['keypoint_series'], key=lambda entry: entry['ts'])
        newest['joints_room'][len(person.FACE) - 1]['xyz'][2] = 3.0   # the newest frame's last face point, far off
        newest['joints_room'].insert(0, dict(j=0, xyz=[2.0, 2.4, 9.0], status='uncertain'))   # and an unsupported one
        head = run(fixture)[0][-1]['attention']['head_xyz']
        self.assertEqual(head, [2.0, 2.4, POSES['seated']['face']])
        self.assertNotEqual(head[2], 3.0, 'control: the last face point would not be the median')

    def test_a_held_field_keeps_the_support_it_had(self):
        fixture = scene(safe=(True, True, False, True, True))
        records, _ = run(fixture)
        before, held = [next(r for r in records if r['emitted_ts'] == t) for t in sorted({r['emitted_ts'] for r in records})[1:3]]
        self.assertEqual({name: held['field_support'][name]['support_status'] for name in ('working_region', 'attention', 'focused_engagement')}, {'working_region': 'held_ambiguous', 'attention': 'held_ambiguous', 'focused_engagement': 'held_ambiguous'})
        for name in ('working_region', 'attention'):
            self.assertEqual(held['field_support'][name]['last_support_capture_ts'], before['field_support'][name]['last_support_capture_ts'], name)
        self.assertNotEqual(held['field_as_of']['position'], before['field_as_of']['position'], 'control: the position, which is not held, moves on')


class Emit(unittest.TestCase):
    def test_a_record_is_emitted_only_when_a_value_or_a_status_changed(self):
        records, _ = run(scene(safe=(True,) * 5))   # starting, engaged, then the same thing three times
        self.assertEqual([r['focused_engagement']['state'] for r in records], ['starting', 'engaged'])
        moving = scene(safe=(True,) * 5)   # control: a person who moves a centimetre each reply
        for i, bundle in enumerate(moving['bundles']):
            for entry in bundle['positions']:
                entry['x'] += 0.01 * i
        self.assertEqual(len(run(moving)[0]), 5)


class Test8(unittest.TestCase):
    def test_the_same_input_twice_gives_byte_identical_person_states(self):
        with tempfile.TemporaryDirectory(prefix='den-sim-person-') as folder:
            folder = Path(folder)
            for name in ('once', 'twice'):
                person.write(folder / name, run(scene())[0])
            once, twice = (folder / 'once').read_bytes(), (folder / 'twice').read_bytes()
            self.assertEqual(once, twice)
            # Control: a clock read planted in person.py's own record path, in each field's support, breaks it.
            with mock.patch.object(person, 'support', lambda *args, real=person.support: dict(real(*args), read_ns=time.perf_counter_ns())):
                for name in ('planted-once', 'planted-twice'):
                    person.write(folder / name, run(scene())[0])
            self.assertNotEqual((folder / 'planted-once').read_bytes(), (folder / 'planted-twice').read_bytes())

    def test_the_tests_leave_nothing_in_the_temp_folder(self):
        with tempfile.TemporaryDirectory(prefix='den-sim-temp-') as temp:
            env = dict(os.environ, TEMP=temp, TMP=temp, TMPDIR=temp)
            done = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(HERE / 'test_person.py'), 'Test8.test_the_same_input_twice_gives_byte_identical_person_states', 'Hop13'],
                                  capture_output=True, text=True, env=env, cwd=str(HERE))
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            self.assertEqual(os.listdir(temp), [])
            subprocess.run([sys.executable, '-B', '-c', 'import tempfile; tempfile.mkdtemp()'], env=env, check=True)   # control: a folder left behind is seen
            self.assertEqual(len(os.listdir(temp)), 1)


class Canon(unittest.TestCase):
    def test_person_states_use_canons_names_and_leave_null_what_hop_15_lists(self):
        shown = git('show', '%s:docs/architecture/live-v1/schemas.json' % CANON)
        if shown.returncode:
            self.skipTest('commit %s is not on this PC' % CANON)
        record = next(r for r in json.loads(shown.stdout)['records'] if r['id'] == 'person_state')
        names = {part.strip() for field in record['fields'] for part in field['name'].split('/')}
        written = run(scene())[0][-1]
        self.assertEqual(sorted(set(written) - names), [])
        self.assertEqual(sorted(names - set(written)), [], 'every canon field is written, null or unknown where nothing has evidence')
        self.assertEqual(sorted(names - set(dict((k, v) for k, v in written.items() if k != 'posture'))), ['posture'], 'control: a missing field is found')
        self.assertEqual(written['posture'], 'unknown')
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
    def test_the_need_per_track_gives_den_cores_runner_states_on_the_same_lines(self):
        """need v0 runs once per track, in person.py, and nowhere else on a click: on the lines it was handed it says what
        den-core's real runner says on them, so the per-track pass stands in for the runner's room-level one."""
        temp = tempfile.TemporaryDirectory(prefix='den-sim-core-')
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name)
        with tarfile.open(fileobj=io.BytesIO(git('archive', '--format=tar', PIN, 'tools/den-core').stdout)) as archive:
            archive.extractall(folder, filter='data')
        fixture = scene(safe=(True,) * 5)
        fixture['facts'][1]['observation']['facts']['activity_hypothesis'] = 'phone_use'   # reading, phone, then reading: a dropped line shows
        script = folder / 'runner_states.py'
        script.write_text('\n'.join(['import json, sys', 'sys.path.insert(0, sys.argv[1])', 'import runner, sources',
                                     'items = [(sources.fact_time(v["available_at"]), "facts", v) for v in map(json.loads, open(sys.argv[2], encoding="utf-8"))]',
                                     'header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(["need"]), synthetic=True, sources=["synthetic"])',
                                     'last = max(t for t, _, _ in items)',
                                     'print(json.dumps([r["data"]["focused_engagement"]["state"] for r in log if r["kind"] == "person_state" and r["t"] <= last]))']), encoding='utf-8')

        def runner_states(facts):
            (folder / 'facts.jsonl').write_text(''.join(json.dumps(line) + '\n' for line in facts), encoding='utf-8')
            done = subprocess.run([sys.executable, '-B', str(script), str(folder / 'tools' / 'den-core'), str(folder / 'facts.jsonl')], capture_output=True, encoding='utf-8')
            return json.loads(done.stdout)
        _, [card] = run(fixture)
        self.assertEqual(card['need_states'], runner_states(fixture['facts']))
        self.assertEqual(card['engaged_reply'], 4, 'reading, phone, then reading: engaged at the fourth')
        unsafe = copy.deepcopy(fixture)   # control: the first bundle unsafe, so person.py hands need four of the five lines
        unsafe['asks'][0]['association_safe'] = unsafe['bundles'][0]['association_safe'] = False
        self.assertNotEqual(run(unsafe)[1][0]['need_states'], runner_states(fixture['facts']))
        self.assertEqual(run(unsafe)[1][0]['need_states'], runner_states(fixture['facts'][1:]), 'and on the four it took, the two agree again')
        # Hop 13's row says how many replies each track's need took, and how many were held.
        lag = scene()
        self.assertEqual(chain.need_words(run(lag)[1]).split('(hop 15), ')[1], 'on the replies its bundle was safe to take: trk-1 took 4 of 5 '
                         'replies, 1 held as not safe to tie to one person: starting then engaged. Nothing else runs need on a click.')
        safe = scene(safe=(True,) * 5)   # control: nothing held
        self.assertIn('trk-1 took 5 of 5 replies: starting then engaged.', chain.need_words(run(safe)[1]))


if __name__ == '__main__':
    unittest.main(verbosity=2)
