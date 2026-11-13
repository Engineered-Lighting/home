"""Tests for step 3's live loop: source.py, perceive.py, stream.py, run_local.py, live_page.mjs. Stdlib unittest.

    python -B -X utf8 tools\\den-live\\test_live.py

No camera, LA or ssh. The parts on renders need a prepared den-sim set (DEN_SIM_SET, else
%USERPROFILE%\\den-sim\\prepared\\fbc7bda3115e):
- Most drive the perceiver with the set's own recorded detector rows (YOLO26s and RTMPose-m, run once when the set was
  prepared), so they need no venv.
- The end-to-end part runs run_local.run with the real detector process in its venv, test_sofa_check's fake camera
  serving the set's takes, and the live page on the authored room. It needs the venv and Node.
The set's truth (where each scene's person is) is read by these tests only. Everything written is under TEMP.
"""
import atexit
import contextlib
import csv
import io
import json
import math
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_sofa_check as ts  # noqa: E402  its fake camera and its no-ssh audit hook
import perceive  # noqa: E402
import run_local as rl  # noqa: E402
import sofa_check as sc  # noqa: E402
import source  # noqa: E402
import stream as people_stream  # noqa: E402

SET = ts.SET
FIT, READING = SET / 'e9e0a238f4bd', SET / '85b080a5815b'
TMP = Path(tempfile.mkdtemp(prefix='den-live-test-'))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
TOOLS = Path(tempfile.gettempdir()) / 'den-live-test-tools'
NODE = shutil.which('node')
HAVE_SET = (FIT / 'room-model.jsonl').is_file() and (READING / 'detections.jsonl').is_file()


def scratch(prefix):
    return Path(tempfile.mkdtemp(prefix=prefix, dir=TMP))


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def truth(take):
    for folder in (SET / 'truth').iterdir():
        doc = json.loads((folder / 'take.json').read_text(encoding='utf-8'))
        if doc['take'] == take.name:
            return doc['person']['at_m']
    raise LookupError(take)


# ---- the clock, the floor, the mailbox ------------------------------------------------------------------------

class Clock(unittest.TestCase):
    def test_the_least_delayed_arrivals_set_the_offset(self):
        clock = perceive.ClockOffset(window=50, quantile=0.05)
        for i in range(50):
            clock.add(100.0 + i * 0.25, 1000.0 + i * 0.25 + 0.1 + (0.4 if i % 3 else 0.0))   # transit 0.1 s, often 0.5 s
        self.assertAlmostEqual(clock.offset(), 900.1, places=6)
        clock.reset()
        clock.add(5.0, 10.0)
        self.assertEqual(clock.offset(), 5.0)


@unittest.skipUnless(HAVE_SET, 'needs a prepared den-sim set')
class Floor(unittest.TestCase):
    def test_bounds_padded_and_the_method_copy(self):
        bounds = perceive.floor_bounds([[1, 2], [5.5, 2], [5.5, 6.25], [1, 6.25]])
        self.assertEqual(bounds, [1, 2, 5.5, 6.25])
        self.assertEqual(perceive.padded(bounds), [0, 1, 6.5, 7.25])
        tools = perceive.Tools(sc.archive(TOOLS, 'observe'), sc.archive(TOOLS, 'track'))
        copy = perceive.track_method_copy(tools, [0.0, 1.0, 6.5, 7.25], scratch('den-live-method-'))
        method = tools.track_method.Method(str(copy))
        self.assertEqual(method.room['floor_m'], [0.0, 1.0, 6.5, 7.25])
        self.assertIn('room.floor_m', [name for name, _ in method.open], 'still open: the run stays unscored')
        own = tools.track_method.Method(str(tools.track_tree / 'tools' / 'den-track' / 'METHOD.md'))
        self.assertNotEqual(method.fingerprint, own.fingerprint, 'the copy says it differs')

    def test_a_polygon_that_is_no_floor(self):
        for polygon in (None, [[0, 0], [1, 1]], [[0, 0], [0, 1], [0, 2]], [[0, 0], [1, 'x'], [2, 2]]):
            with self.assertRaises(ValueError):
                perceive.floor_bounds(polygon)


class Mailbox(unittest.TestCase):
    def test_the_newest_waits_and_older_ones_count_as_dropped(self):
        box = source.Mailbox()
        for i in range(3):
            box.put(i)
        self.assertEqual(box.take(0.1), 2)
        self.assertEqual((box.received, box.dropped), (3, 2))
        self.assertIsNone(box.take(0.05))


# ---- the stream ------------------------------------------------------------------------------------------------

class Stream(unittest.TestCase):
    def setUp(self):
        self.stream = people_stream.PeopleStream(port=0, heartbeat_s=0.3, max_clients=2).start()
        self.base = 'http://127.0.0.1:%d/api/den-live/v1/homes/den/' % self.stream.port
        self.addCleanup(self.stream.close)

    def get(self, path, host=None):
        request = urllib.request.Request(self.base + path, headers={'Host': host} if host else {})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def events(self, count, timeout=5):
        """Read count SSE blocks from one stream, in a thread."""
        got, done = [], threading.Event()

        def read():
            with urllib.request.urlopen(self.base + 'stream', timeout=timeout) as response:
                block = []
                for raw in response:
                    line = raw.decode('utf-8').rstrip('\r\n')
                    if line:
                        block.append(line)
                        continue
                    if block:
                        got.append(block)
                        block = []
                    if len(got) >= count:
                        break
            done.set()
        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        return got, done

    def test_state_and_events(self):
        self.assertEqual(self.get('state'), (200, {'people': None}))
        got, done = self.events(4)
        time.sleep(0.2)
        self.stream.publish({'schema': 'den-live-people-1', 'seq': 7, 'tracks': []})
        self.assertTrue(done.wait(5))
        self.assertEqual(got[0], ['retry: 2000'])
        self.assertIn(['id: 7', 'event: people', 'data: {"schema":"den-live-people-1","seq":7,"tracks":[]}'], got)
        self.assertIn([': heartbeat'], got, 'a silent pipeline still says it is there')
        self.assertEqual(self.get('state')[1]['people']['seq'], 7)

    def test_refusals(self):
        self.assertEqual(self.get('state', host='localhost:%d' % self.stream.port)[0], 403)
        self.assertEqual(self.get('elsewhere')[0], 404)
        with urllib.request.urlopen(self.base + 'stream', timeout=5), urllib.request.urlopen(self.base + 'stream', timeout=5):
            time.sleep(0.2)
            self.assertEqual(self.get('stream')[0], 503, 'at most max_clients streams')
        with self.assertRaises(OSError, msg='a second server never shares a port in use, on Windows too'):
            people_stream.PeopleStream(port=self.stream.port)
        request = urllib.request.Request(self.base + 'state', data=b'{}', method='POST')
        with self.assertRaises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(refused.exception.code, 501)


# ---- den-person-state-1: the snapshot on disk, numbers only (contract 2) -------------------------------------------

class PersonStateLog(unittest.TestCase):
    SNAPSHOT_KEYS = {'schema', 'service_run_id', 'camera_epoch', 'seq', 'frame', 'status', 'presence', 'tracks',
                     'eligible_track_id', 'eligibility_revision'}
    FRAME_KEYS = {'service_run_id', 'camera_epoch', 'frame_id', 'capture_mono', 'arrival_mono', 'capture_ts',
                  'transform_id', 'scan_revision', 'depth_calibration_sha256'}
    TRACK_KEYS = {'track_id', 'state', 'xy_m', 'sigma_m', 'position_basis', 'current_obs_ids', 'association',
                  'supported_pose', 'seated'}
    WORDS = {'den-person-state-1', 'confirmed', 'coasting', 'stereo', 'plane', 'coast', 'confident', 'ambiguous',
             'supported', 'hips', 'head', 'fresh', 'stale', 'starting', 'offline', 'refused', 'degraded', 'present',
             'empty', 'unknown'}

    @staticmethod
    def snapshot(run='a' * 32, seq=7):
        from contracts import FrameRef, PlaneRef, SeatedState, TrackEvidence, TrackingSnapshot
        frame = FrameRef(run, 2, 41, 'camera-session', 'rgb', PlaneRef(9, 1.0, 1.0, 640, 360), None, None, 'c' * 64,
                         'den-oak@2026-11-13.1', 'scan-r4', 1234.56789, 1234.6789, 1_800_000_000.123456)
        pose = {'left_hip': ((1.0, 2.0, 0.5), 'supported'), 'nose': ((1.0, 2.1, 1.12345), 'supported')}
        track = TrackEvidence(f'{run}-e2-trk-1', 'confirmed', (1.23456, -0.5), 0.1234, 'stereo', ('o-den-oak-1234567-0',),
                              'confident', 3, 1230.5, pose, near='on the sofa', seated=SeatedState(True, 1231.25, 'hips'))
        return TrackingSnapshot(run, 2, seq, frame, 'fresh', 'too dark', 'present', (track,), track.track_id, 4)

    def test_each_snapshot_is_one_numbers_only_line_with_the_contracts_fields_and_an_anchor(self):
        import dataclasses
        import person_state
        state = scratch('den-live-person-state-')
        monos = iter([500.0, 500.5, 501.0])
        log = person_state.Log(state, clock=lambda: next(monos), wall=lambda: 1_800_000_000.0)

        class Asker:   # the ask's eligibility on the snapshot is what's logged, as the coordinator gets it
            snapshot = None

            def observe(self, event, snapshot):
                self.snapshot = dataclasses.replace(snapshot, eligibility_revision=9)
                return event

            def pop_reply(self):
                return None
        observe = rl.evidence_observer(Asker(), None, log)
        event = {'schema': 'den-live-people-1', 'tracks': []}
        self.assertIs(observe(event, self.snapshot()), event)
        observe(event, self.snapshot(seq=8))
        observe(event, self.snapshot(run='b' * 32, seq=1))   # another run: its own anchor
        path = state / 'days' / '2027-01-15' / 'person-state.jsonl'   # beside the ask's asks.jsonl
        text = path.read_text(encoding='utf-8')
        lines = [json.loads(line) for line in text.splitlines()]
        self.assertEqual([('anchor' in line, line['service_run_id']) for line in lines],
                         [(True, 'a' * 32), (False, 'a' * 32), (False, 'a' * 32), (True, 'b' * 32), (False, 'b' * 32)])
        self.assertEqual(lines[0], {'schema': 'den-person-state-1', 'service_run_id': 'a' * 32,
                                    'anchor': {'mono': 500.0, 'ts': 1_800_000_000.0}})
        self.assertEqual(lines[3]['anchor']['mono'], 501.0)
        line = lines[1]
        self.assertEqual(set(line), self.SNAPSHOT_KEYS)
        self.assertEqual(set(line['frame']), self.FRAME_KEYS)
        track, = line['tracks']
        self.assertEqual(set(track), self.TRACK_KEYS)
        self.assertEqual(track['association'], {'status': 'confident', 'revision': 3, 'since_mono': 1230.5})
        self.assertEqual(track['seated'], {'state': True, 'since_mono': 1231.25, 'basis': 'hips'})
        self.assertEqual((track['xy_m'], track['supported_pose']['nose']), ([1.235, -0.5], [[1.0, 2.1, 1.123], 'supported']))
        self.assertEqual((line['frame']['capture_mono'], line['frame']['depth_calibration_sha256']), (1234.568, 'c' * 64))
        self.assertEqual((line['eligibility_revision'], line['seq']), (9, 7))
        # Numbers only: every word is a closed state or one of the snapshot's own ids; no reason, no near phrase.
        ids = {'a' * 32, 'a' * 32 + '-e2-trk-1', 'o-den-oak-1234567-0', 'den-oak@2026-11-13.1', 'scan-r4', 'c' * 64}

        def leaves(value):
            if isinstance(value, dict):
                for item in value.values():
                    yield from leaves(item)
            elif isinstance(value, list):
                for item in value:
                    yield from leaves(item)
            else:
                yield value
        for value in leaves(line):
            self.assertTrue(value is None or type(value) in (bool, int, float) or value in self.WORDS | ids, value)
        self.assertNotIn('on the sofa', text)
        self.assertNotIn('too dark', text)
        self.assertNotIn('camera-session', text)

    def test_each_day_file_stops_at_its_own_cap_and_the_status_line_names_it(self):
        """asks.jsonl and person-state.jsonl each stop at their own day's cap: a line that would pass it is skipped and
        counted for that UTC day, and the status line says so, naming the file. Person state filling its share never
        stops an ask. The next UTC day starts afresh, its counts too. The real caps are 50 MB and 300 MB."""
        import ask
        import person_state
        self.assertEqual((ask.AskLog.CAP_BYTES, person_state.CAP_BYTES), (50 * 1024 * 1024, 300 * 1024 * 1024))
        self.assertEqual(ask.AskLog(scratch('x-')).cap, 50 * 1024 * 1024, "the ask's own log, as the Asker makes it")
        self.assertEqual(person_state.Log(scratch('x-')).out.cap, 300 * 1024 * 1024)
        state, wall = scratch('den-live-person-state-'), [1_800_000_000.0]
        log = person_state.Log(state, clock=lambda: 500.0, wall=lambda: wall[0], cap=4000)
        asks = ask.AskLog(state, cap=2500, wall=lambda: wall[0])
        day = state / 'days' / '2027-01-15'
        self.assertTrue(asks.write({'ask': 1}, wall[0]))
        seq = 0
        while not log.skipped:
            seq += 1
            log.write(self.snapshot(seq=seq))
        size = lambda name: (day / name).stat().st_size
        self.assertLessEqual(size('person-state.jsonl'), 4000)
        self.assertGreater(size('person-state.jsonl'), 4000 - 1000, 'it filled its day, a line short at most')
        kept = (day / 'person-state.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(kept), seq, 'the anchor and every snapshot before the cap')
        log.write(self.snapshot(seq=seq + 1))
        self.assertTrue(asks.write({'ask': 2, 'pad': 'x' * 1000}, wall[0]), 'person state filled its own share only')
        self.assertTrue(asks.write({'ask': 3, 'pad': 'x' * 1000}, wall[0]))
        self.assertFalse(asks.write({'ask': 4, 'pad': 'x' * 1000}, wall[0]), "the ask log's own cap")
        self.assertEqual((log.skipped, asks.skipped), (2, 1))
        self.assertLessEqual(size('asks.jsonl'), 2500)

        class P:
            counts = {'frames': 40, 'refused_depth': 0, 'detector_errors': 0, 'unreadable': 0, 'out_of_order': 0, 'expired': 0}
        line = rl.status_line(P(), {'camera': {'status': 'ok'}, 'health': {}, 'tracks': []}, [log, asks])
        self.assertTrue(line.endswith("| 40 frames, 2 lines not kept today: the day's person-state.jsonl is at its cap, "
                                      "1 line not kept today: the day's asks.jsonl is at its cap"), line)
        self.assertNotIn('not kept', rl.status_line(P(), {'camera': {'status': 'ok'}}, [person_state.Log(scratch('x-'))]))
        wall[0] += 86400   # the next UTC day: its own files, its own anchor, its own counts
        self.assertEqual((log.skipped, asks.skipped), (0, 0), 'the counts are per UTC day')
        self.assertNotIn('not kept', rl.status_line(P(), {'camera': {'status': 'ok'}}, [log, asks]))
        log.write(self.snapshot(seq=seq + 2))
        lines = [json.loads(text) for text in (state / 'days' / '2027-01-16' / 'person-state.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(['anchor' in line for line in lines], [True, False])
        self.assertTrue(asks.write({'ask': 5, 'pad': 'x' * 1000}, wall[0]))
        self.assertEqual((log.skipped, asks.skipped), (0, 0))

    def test_with_nobody_in_view_about_a_line_a_second_and_any_change_at_once(self):
        """An empty den at 4-5 frames a second wrote about 200 MB of person state a day, so a few person-hours filled the
        day. With nobody in view a line goes at most once in each second of the monotonic clock; every snapshot with
        someone in view, and any change of presence or status, goes at once."""
        import dataclasses
        import person_state
        state, mono = scratch('den-live-person-state-'), [1000.0]
        log = person_state.Log(state, clock=lambda: mono[0], wall=lambda: 1_800_000_000.0 + mono[0])

        def nobody(seq, **change):
            return dataclasses.replace(self.snapshot(seq=seq), **{'tracks': (), 'presence': 'empty', 'eligible_track_id': None,
                                                                    **change})
        path = state / 'days' / '2027-01-15' / 'person-state.jsonl'
        seqs = lambda: [line['seq'] for line in map(json.loads, path.read_text(encoding='utf-8').splitlines()) if 'anchor' not in line]
        for seq in range(1, 301):   # a minute of an empty den at 5 frames a second
            log.write(nobody(seq))
            mono[0] += 0.2
        self.assertIn(len(seqs()), (60, 61), 'about one line a second')
        self.assertEqual(log.thinned, 300 - len(seqs()))
        written, mono[0] = len(seqs()), 2000.0
        log.write(nobody(301))                         # this second's line
        log.write(nobody(302))                         # nothing changed, the same second: thinned
        log.write(nobody(303, presence='unknown'))     # the same second, presence changed: at once
        log.write(self.snapshot(seq=304))              # someone in view: every snapshot
        log.write(self.snapshot(seq=305))
        log.write(nobody(306))                         # they left
        log.write(nobody(307, status='stale'))         # status changed
        log.write(nobody(308, status='stale'))         # thinned
        self.assertEqual(seqs()[written:], [301, 303, 304, 305, 306, 307])
        mono[0] += 1.0
        log.write(nobody(309, status='stale'))
        self.assertEqual(seqs()[-1], 309, 'the next second')

    def test_a_log_that_cant_write_never_stops_the_loop(self):
        import person_state
        blocked = scratch('den-live-person-state-') / 'not-a-folder'
        blocked.write_text('x', encoding='utf-8')
        log = person_state.Log(blocked)
        event = {'tracks': []}
        self.assertIs(rl.evidence_observer(None, None, log)(event, self.snapshot()), event)
        self.assertEqual(log.failed, 1)


# ---- the perceiver, on recorded rows -----------------------------------------------------------------------------

class RecordedDetector:
    """The set's own detector rows for a take, by RGB sequence: what YOLO26s and RTMPose-m said when the set was prepared."""

    def __init__(self, take, luma=120.0, change=None):
        self.change = change   # a test's edit of a row before it goes out
        self.rows = {}
        for line in (take / 'detections.jsonl').read_text(encoding='utf-8').splitlines():
            if line.strip():
                row = json.loads(line)
                self.rows[row['frame']['rgb_seq']] = row
        self.luma = luma

    def detect(self, bundle, delay_s):
        row = self.rows.get(bundle.rgb_seq)
        if row is None:
            return {'error': 'KeyError'}
        row = dict(row, available_ts=row['frame']['capture_ts'] + delay_s + 0.07)
        if self.change:
            row = self.change(json.loads(json.dumps(row)))
        return {'row': row, 'compute_s': 0.07, 'luma': self.luma(bundle) if callable(self.luma) else self.luma}


def bundles(take, keep=lambda i: True, transit=0.12, session=None):
    """The take's frames that have recorded rows, as the source hands them over, arriving transit after capture."""
    import check
    rows = [r for r in csv.DictReader(io.StringIO((take / 'frames.csv').read_text(encoding='utf-8')))]
    recorded = RecordedDetector(take).rows
    out = []
    for i, row in enumerate(r for r in rows if int(r['rgb_seq']) in recorded):
        if not keep(i):
            continue
        data = (take / row['file']).read_bytes()
        meta = check.read_bundle(data)[0]
        t = float(row['t_host_s'])
        out.append(source.Bundle(data, session or meta['session'], int(row['rgb_seq']), t, 1_800_000_000.0 + t + transit, t + transit))
    return out


@unittest.skipUnless(HAVE_SET, 'needs a prepared den-sim set')
class Perceiving(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = perceive.Tools(sc.archive(TOOLS, 'observe'), sc.archive(TOOLS, 'track'))
        sys.path.insert(0, str(cls.tools.observe_tree / 'tools' / 'den-take'))

    def setUp(self):
        self.now = 0.0   # seconds on a fake wall clock that starts after the set's registration
        self.wall = 0.0  # a step in that wall clock, as time sync makes one

    def perceiver(self, take, floor=(-3.0, -3.5, 3.0, 2.5), require=False, room_model=None, luma=120.0, gate=0.10, change=None,
                  source_correction=None, reinstall=False):
        copy = perceive.track_method_copy(self.tools, list(floor), scratch('den-live-method-'))
        return perceive.Perceiver(self.tools, room_model_path=room_model or FIT / 'room-model.jsonl', camera_id='den-virtual',
                                  max_error_bound_m=gate, track_method_path=copy, detector=RecordedDetector(take, luma, change),
                                  home='den', require_source_correction=require, now=lambda: 1_800_000_000.0 + self.now,
                                  source_correction=source_correction, reinstall=reinstall)

    def run_take(self, perceiver, frames):
        events = []
        for bundle in frames:   # processed 0.35 s after capture; the wall clock may stand apart (self.wall)
            self.now = bundle.t_host + 0.35 + self.wall
            event = perceiver.process(bundle, {'status': 'ok', 'why': None}, bundle.t_host + 0.35)
            if event is not None:
                events.append(event)
        return events

    def test_a_reader_on_the_couch_is_one_marker_where_they_sit(self):
        p = self.perceiver(READING)
        events = self.run_take(p, bundles(READING))
        ids = {t['track_id'] for e in events for t in e['tracks']}
        self.assertEqual(ids, {f'{p.run_id}-e1-trk-1'})
        last = events[-1]['tracks'][0]
        self.assertLess(math.dist(last['xy_m'], truth(READING)), 0.3, last['xy_m'])
        self.assertEqual(last['state'], 'confirmed')
        self.assertEqual(set(last), {'track_id', 'state', 'xy_m', 'sigma_m', 'heading_deg', 'age_s', 'near', 'service_run_id',
                                     'camera_epoch', 'association_revision'}, 'no joints, no image, no name')
        self.assertEqual((last['service_run_id'], last['camera_epoch']), (p.run_id, 1))
        self.assertIsNone(last['near'], 'no labels, no phrase')
        self.assertEqual(events[-1]['schema'], 'den-live-people-1')
        self.assertTrue(all(t['state'] in ('confirmed', 'coasting') for e in events for t in e['tracks']), 'never a tentative one')
        # 0.35 s from capture to event, less the 0.12 s transit the arrival-anchored clock takes as its offset: the lag
        # it reports leaves out the camera-to-PC transit, which plan L1 puts at about 0.1-0.3 s.
        self.assertGreaterEqual(events[-1]['health']['lag_s_median'], 0.23)
        self.assertIn('lift', events[-1]['health']['timings_ms'])

    def test_the_labels_give_each_marker_its_near_phrase(self):
        """Step 2 of the labels plan: a sofa labelled where the reader sits puts "on the sofa" on their marker; labels
        placed on another scan revision put nothing there."""
        import room_labels
        x, y = truth(READING)
        sofa = {'id': 'l-sofa-1', 'name': 'sofa', 'kind': 'seat', 'box': {'centre_m': [x, y, 0.42], 'size_m': [2.0, 0.9, 0.85], 'yaw_deg': 0},
                'on_wall': False, 'made_by': 'owner', 'status': 'confirmed', 'updated_at': None}
        for revision, near in ((None, 'on the sofa'), ('another-scan', None)):
            with self.subTest(content_revision=revision):
                store = room_labels.Store(scratch('den-live-labels-'), say=lambda *_: None)
                self.assertEqual(store.put({**room_labels.empty('this-scan'), 'labels': [sofa]})[0], 200)
                p = self.perceiver(READING)
                p.labels = store
                if revision:
                    p.scope['content_revision'] = revision
                events = self.run_take(p, bundles(READING))
                self.assertEqual(events[-1]['tracks'][0]['near'], near)

    def test_a_floor_box_holding_the_seat_keeps_one_reader_one_track(self):
        """The den simulation's finding on your real sofa, in the live loop: a reader sitting past den-track's floor
        box plus its 0.4 m margin. With a frame in three dropped, the gaps pass unseen_after_s, the track is clamped to
        the box's edge, and the next sighting starts a new one. A box that holds the seat keeps them one track.
        (The set's standing take can't show this: the detector finds no one in it, as the den simulation's README says.)"""
        frames = bundles(READING, keep=lambda i: i % 3 != 2)
        missing, holding = self.perceiver(READING, floor=(1.0, 1.0, 5.0, 5.0)), self.perceiver(READING)
        lost, held = self.run_take(missing, frames), self.run_take(holding, frames)
        shown = lambda events: {t['track_id'] for e in events for t in e['tracks']}
        self.assertGreater(missing.core.next_id - 1, 1, 'the control: the seat 0.6 m past the margin splits the reader')
        self.assertEqual((holding.core.next_id - 1, len(shown(held))), (1, 1))
        self.assertLess(math.dist(held[-1]['tracks'][0]['xy_m'], truth(READING)), 0.3)

    def test_frames_without_the_cameras_correction_are_refused(self):
        p = self.perceiver(READING, require=True)
        events = self.run_take(p, bundles(READING)[:10])
        self.assertEqual(p.counts['refused_depth'], 10)
        self.assertTrue(all(e['tracks'] == [] and e['camera']['why'] == 'depth not corrected at the camera' for e in events))

    def test_frames_with_the_correction_go_on(self):
        import test_register_now as tr
        frames = [source.Bundle(tr.with_metadata(b.data, lambda m: m.update(depth_correction=dict(tr.CORRECTED))), b.session,
                                b.rgb_seq, b.t_host, b.arrival_unix, b.arrival_mono) for b in bundles(READING)[:12]]
        p = self.perceiver(READING, require=True)
        events = self.run_take(p, frames)
        self.assertEqual(p.counts['refused_depth'], 0)
        self.assertTrue(events[-1]['tracks'])

    def test_frames_on_another_correction_than_the_receipts_are_refused(self):
        """The review's case: a camera release re-measures its factor, and den-live would stack the receipt's scale on
        another correction. Every frame must say applied with the receipt's factor and calibration hash, whatever the
        scale; one with another is refused and counted, and the status and why say to register again with a new take:
        register_now.py without --again, whose old take would find its registration qualified already. The LA service's
        why adds install_la.py after. The control: the receipt's own correction goes on."""
        import test_register_now as tr
        receipt = (tr.CORRECTED['factor'], tr.CORRECTED['calibration_sha256'])

        def corrected(**changes):
            record = dict(tr.CORRECTED, **changes)
            return [source.Bundle(tr.with_metadata(b.data, lambda m: m.update(depth_correction=record)), b.session,
                                  b.rgb_seq, b.t_host, b.arrival_unix, b.arrival_mono) for b in bundles(READING)[:12]]
        cases = [(require, corrected(**changes)) for require in (True, False)
                 for changes in ({'factor': 0.901}, {'calibration_sha256': 'd' * 64})]
        cases += [(False, corrected(applied=False)), (False, bundles(READING)[:12])]   # not required, yet not this one
        for require, frames in cases:
            with self.subTest(require=require):
                p = self.perceiver(READING, require=require, source_correction=receipt)
                events = self.run_take(p, frames)
                self.assertEqual(p.counts['refused_depth'], 12)
                self.assertEqual(len(events), 12)
                self.assertTrue(all(e['tracks'] == [] for e in events))
                self.assertEqual({(e['perception_status'], e['camera']['why']) for e in events},
                                 {('refused', perceive.CORRECTION_CHANGED)})
                self.assertIn('paste register_now.py without --again, which records a new take', perceive.CORRECTION_CHANGED)
                self.assertIn('12 refused for depth', rl.status_line(p, events[-1]))
                self.assertIn(': %s |' % perceive.CORRECTION_CHANGED, rl.status_line(p, events[-1]))
        p = self.perceiver(READING, require=True, source_correction=receipt, reinstall=True)   # the LA service's
        events = self.run_take(p, corrected(factor=0.901))
        self.assertEqual({e['camera']['why'] for e in events},
                         {perceive.CORRECTION_CHANGED + ', then paste install_la.py again'})
        self.assertEqual({e['perception_status'] for e in events}, {'refused'})
        p = self.perceiver(READING, require=True, source_correction=receipt)
        events = self.run_take(p, corrected())
        self.assertEqual(p.counts['refused_depth'], 0, 'the control: the receipt\'s own correction goes on')
        self.assertTrue(events[-1]['tracks'])

    def test_time_going_back_is_a_new_epoch_and_a_small_step_back_is_skipped(self):
        frames = bundles(READING)
        p = self.perceiver(READING)
        events = self.run_take(p, frames[:20] + frames[:20])   # the fake camera's loop: the clock goes back 5 s
        self.assertEqual(p.sessions, 2)
        self.assertEqual(p.counts['out_of_order'], 1, 'require advancing frames after the clock regression')
        self.assertEqual({t['track_id'] for t in events[-1]['tracks']}, {f'{p.run_id}-e2-trk-1'}, 'its tracks are its own')
        p = self.perceiver(READING)
        first = frames[5]
        again = source.Bundle(first.data, first.session, first.rgb_seq, first.t_host - 0.05, first.arrival_unix - 0.05, first.arrival_mono - 0.05)
        self.run_take(p, frames[:6] + [again])
        self.assertEqual((p.counts['out_of_order'], p.sessions), (1, 1))

    def test_too_dark_says_so_and_comes_back(self):
        dark = {'on': True}
        p = self.perceiver(READING, luma=lambda bundle: 20.0 if dark['on'] else 120.0)
        frames = bundles(READING)
        events = self.run_take(p, frames[:30])
        self.assertEqual(events[-1]['mode'], 'none')
        self.assertEqual(events[-1]['camera']['why'], 'too dark')
        self.assertEqual(events[-1]['tracks'], [])
        dark['on'] = False
        events = self.run_take(p, frames[30:60])
        self.assertEqual(events[-1]['mode'], 'rgb')
        self.assertTrue(events[-1]['tracks'])

    def test_a_step_in_the_wall_clock_changes_nothing(self):
        """The tracker runs on the monotonic clock, so time sync stepping the wall clock back 30 s mid-run neither
        stalls it nor sends it backwards (the review's case)."""
        frames = bundles(READING)
        p = self.perceiver(READING)
        self.run_take(p, frames[:30])
        self.wall = -30.0
        events = self.run_take(p, frames[30:])
        self.assertEqual((p.counts['out_of_order'], p.sessions), (0, 1))
        self.assertEqual({t['track_id'] for t in events[-1]['tracks']}, {f'{p.run_id}-e1-trk-1'})
        self.assertGreaterEqual(events[-1]['health']['lag_s_median'], 0.23)
        self.assertIn('lift', events[-1]['health']['timings_ms'])

    def test_person_boxes_under_half_are_never_markers_on_their_own(self):
        """Plan L3: a box under 0.5 reaches the tracker but not the markers. The control is the same frames at their own
        scores, where the reader shows."""
        def weak(row):
            for d in row['detections']:
                if d['class'] == 'person':
                    d['score'], d['keypoints'] = 0.3, None
            return row
        p = self.perceiver(READING, change=weak)
        events = self.run_take(p, bundles(READING))
        self.assertGreaterEqual(p.core.next_id - 1, 1, 'the tracker still has them')
        self.assertTrue(all(e['tracks'] == [] for e in events), 'but no marker')
        self.assertGreater(p.counts['weak_only_hidden'], 0)

    def test_a_heading_older_than_three_seconds_is_left_off(self):
        frames, late = bundles(READING), {'on': False}

        def no_pose_later(row):
            if late['on']:
                for d in row['detections']:
                    d['keypoints'] = None   # still a person box at 0.92: the track goes on, its heading doesn't update
            return row
        p = self.perceiver(READING, change=no_pose_later)
        events = self.run_take(p, frames[:30])
        self.assertIsNotNone(events[-1]['tracks'][0]['heading_deg'], 'the control: a fresh heading is sent')
        late['on'] = True
        events = self.run_take(p, frames[30:])
        self.assertTrue(events[-1]['tracks'], 'the reader is still shown')
        self.assertIsNone(events[-1]['tracks'][0]['heading_deg'])

    def test_frames_that_are_all_dropped_still_publish_why(self):
        """The review's case: a detector that fails on every frame. The loop publishes an idle event each idle_s with
        the reason, so the page never reads a running loop as stopped."""
        class Failing:
            def detect(self, bundle, delay_s):
                return {'error': 'RuntimeError'}

        class Source:
            def state(self):
                return {'status': 'ok', 'why': None}
        p = self.perceiver(READING)
        p.detector = Failing()
        box, published = source.Mailbox(), []
        loop = perceive.Loop(Source(), box, p, published.append, idle_s=0.3)
        loop.start()
        for bundle in bundles(READING)[:12]:
            box.put(bundle)
            time.sleep(0.1)
        loop.halt.set()
        loop.join(5)
        self.assertIsNone(loop.stop_reason)
        self.assertEqual(p.counts['detector_errors'], 12)
        self.assertGreaterEqual(len(published), 2)
        self.assertTrue(all(e['camera']['why'] == 'detector errors' and e['tracks'] == [] for e in published))

    def test_health_ages_out(self):
        p = self.perceiver(READING)
        frames = bundles(READING)[:20]
        self.run_take(p, frames)
        later = frames[-1].t_host + 0.35 + 30
        health = p.idle({'status': 'offline', 'why': 'no new frame for 30 s'}, later)['health']
        self.assertEqual(health, {'timings_ms': {}, 'fps_in': None, 'dropped_per_min': 0, 'lag_s_median': None, 'transit_s_median': None},
                         'nothing old speaks for now')

    def test_idle_keeps_the_epoch_while_a_fresh_frame_is_at_the_detector(self):
        """The review's case: after a 2.6 s stall, a frame captured 2.9 s after the last fresh one is at the detector
        when an idle tick passes 3.0 s. The epoch stays, and finish takes the frame. The control: the same idle tick
        with no frame at the detector starts the next epoch."""
        state = {'status': 'ok', 'why': None}
        frames = bundles(READING)
        for pending in (True, False):
            with self.subTest(pending=pending):
                p = self.perceiver(READING)
                self.run_take(p, frames[:1])
                start = p.last_capture
                for at in (1.5, 2.5):
                    p.idle(state, start + at)
                self.assertEqual(p.sessions, 1)
                late = frames[1]
                t_host = frames[0].t_host + 2.9   # captured 2.9 s after the last fresh frame, on the same clock offset
                late = source.Bundle(late.data, late.session, late.rgb_seq, t_host, late.arrival_unix + 2.0,
                                     t_host + (frames[0].arrival_mono - frames[0].t_host))
                prepared = p.prepare(late, start + 3.0) if pending else None
                p.idle(state, start + 3.5)
                if not pending:
                    self.assertEqual(p.sessions, 2, 'the control: no frame at the detector, so 3.5 s is a new epoch')
                    continue
                self.assertAlmostEqual(prepared.ref.capture_mono, start + 2.9, places=6)
                self.assertEqual(p.sessions, 1, 'a fresh frame at the detector holds the epoch')
                event = p.finish(prepared, p.detector.detect(late, 0.7), state, start + 3.6)
                self.assertIsNotNone(event, 'finish took the frame')
                self.assertEqual((event['perception_status'], p.sessions, p.counts['expired']), ('fresh', 1, 0))

    def test_the_transit_is_on_the_health_line_and_the_trackers_clock_is_unchanged(self):
        """Plan L1, step 4: arrival less captured_unix_ns, its median, only between 0 and 5 s. The tracker's clock stays
        arrival-anchored, so its lag is the same with the Jetson's clock right or wrong."""
        import test_register_now as tr

        def stamped(clock_error_s):
            out = []
            for b in bundles(READING)[:30]:   # arriving 0.12 s after capture, by the camera's clock plus its error
                captured = int(round((1_800_000_000.0 + b.t_host + clock_error_s) * 1e9))
                data = tr.with_metadata(b.data, lambda meta: meta.update(captured_unix_ns=captured))
                out.append(source.Bundle(data, b.session, b.rgb_seq, b.t_host, b.arrival_unix, b.arrival_mono))
            return out
        p = self.perceiver(READING)
        health = self.run_take(p, stamped(0.0))[-1]['health']
        self.assertAlmostEqual(health['transit_s_median'], 0.12, places=3)
        self.assertGreaterEqual(health['lag_s_median'], 0.23)
        for wrong in (-56 * 365.25 * 86400, 0.5, -6.0):   # the 1970 clock; captured after it arrived; over 5 s in transit
            p = self.perceiver(READING)
            health = self.run_take(p, stamped(wrong))[-1]['health']
            self.assertIsNone(health['transit_s_median'], wrong)
            self.assertGreaterEqual(health['lag_s_median'], 0.23, 'measured local work is included')

    def test_events_name_the_registration_and_the_scan(self):
        p = self.perceiver(READING)
        event = self.run_take(p, bundles(READING)[:3])[-1]
        self.assertEqual(event['transform_id'], 'den-virtual@2026-11-09.1')
        self.assertIn('content_revision', event)

    def test_a_registration_it_cant_use_stops_it(self):
        revision = json.loads((FIT / 'room-model.jsonl').read_text(encoding='utf-8').strip().splitlines()[-1])
        moved = scratch('den-live-model-') / 'room-model.jsonl'
        entry = revision['cameras'][0]
        moved.write_text(json.dumps(dict(revision, cameras=[dict(entry, invalidated={'take_ended_unix': 1.0})])) + '\n', encoding='utf-8')
        with self.assertRaises(perceive.Stop) as stop:
            self.perceiver(READING, room_model=moved)
        self.assertIn('invalidated', str(stop.exception))
        with self.assertRaises(perceive.Stop) as stop:
            self.perceiver(READING, gate=0.001)
        self.assertIn('above', str(stop.exception))


# ---- the scan's floor, for run_local ------------------------------------------------------------------------------

class ScanFloor(unittest.TestCase):
    def pack(self, polygon, tamper=False):
        import hashlib
        packs = scratch('den-live-packs-')
        site = rl.den_site()
        folder = packs / site / 'rev-1'
        folder.mkdir(parents=True)
        data = json.dumps({'walkable_polygon': polygon}).encode()
        (folder / 'floor.json').write_bytes(data + (b' ' if tamper else b''))
        (folder / 'content-pack.json').write_text(json.dumps({'assets': {'floor.json': {'path': 'floor.json',
                                                  'sha256': hashlib.sha256(data).hexdigest()}}}), encoding='utf-8')
        (packs / site / 'ACTIVE').write_text('rev-1\n', encoding='utf-8')
        return packs, site

    def test_the_active_packs_floor(self):
        packs, site = self.pack([[0.5, 1], [4, 1], [4, 3.25], [0.5, 3.25]])
        self.assertEqual(rl.scan_floor(packs, site), ([0.5, 1, 4, 3.25], 'rev-1'))

    def test_a_floor_file_that_changed_stops(self):
        packs, site = self.pack([[0, 0], [1, 0], [1, 1]], tamper=True)
        with self.assertRaises(sc.Stop):
            rl.scan_floor(packs, site)

    def test_the_registrations_scan_revision_comes_from_its_receipt(self):
        work = scratch('den-live-register-')
        receipts = [{'fit': {'result': 'refused', 'transform_id': None}, 'scan_revision': 'rev-0'},
                    {'fit': {'result': 'registered', 'transform_id': 'den-oak@2026-11-12.1'}, 'scan_revision': 'rev-1'},
                    {'fit': {'result': 'registered', 'transform_id': 'den-oak@2026-11-12.2'}, 'scan_revision': 'rev-2'}]
        for n, receipt in enumerate(receipts, 1):
            (work / ('register-now-%d.json' % n)).write_text(json.dumps(receipt), encoding='utf-8')
        self.assertEqual(rl.registered_revision(work, 'den-oak@2026-11-12.1'), 'rev-1')
        self.assertEqual(rl.registered_revision(work, 'den-oak@2026-11-12.2'), 'rev-2')
        self.assertIsNone(rl.registered_revision(work, 'den-oak@2026-11-12.3'))

    def test_the_status_line_says_what_was_dropped(self):
        class P:
            counts = {'frames': 40, 'refused_depth': 0, 'detector_errors': 40, 'unreadable': 0, 'out_of_order': 0, 'expired': 0}
        line = rl.status_line(P(), {'camera': {'status': 'ok', 'why': 'detector errors'}, 'health': {}, 'tracks': []})
        self.assertEqual(line, 'camera ok: detector errors | - frames a second | 0 people | - behind | 0 dropped a minute | 40 frames, 40 detector errors')

    def test_the_site_is_read_not_written(self):
        site = rl.den_site()
        self.assertRegex(site, r'^[a-z0-9-]+$')
        self.assertNotIn(site, (HERE / 'run_local.py').read_text(encoding='utf-8'), 'no site is named in den-live code')


# ---- step 2's receipt: no receipt, or none qualified, starts nothing (contract 1) -------------------------------------

FIT_TRANSFORM = 'den-virtual@2026-11-09.1'   # the set's own registration


def qualified_receipt(transform_id=FIT_TRANSFORM, **changes):
    """A den-live-register-now-2 receipt that registered and qualified transform_id, as register_now.py writes one."""
    receipt = {'schema': 'den-live-register-now-2', 'fit': {'result': 'registered', 'transform_id': transform_id, 'reasons': []},
               'take': '20261113-200000-x1-m', 'depth_scale': 1.0, 'scan_revision': 'rev-1', 'qualified': True,
               'sofa_check_errors_m': {'c1': 0.02, 'c2': 0.03, 'c3': 0.02},
               'held_out': {'take': '20261113-201000-held', 'within_noise': 0.93, 'worst_bin_m': -0.03, 'ok': True}}
    receipt.update(changes)
    return receipt


def registered_work(root, receipt=None, model=FIT / 'room-model.jsonl'):
    """<root>/private/register/ with the set's room model and, unless receipt is False, one step 2 receipt."""
    work = Path(root) / 'private' / 'register'
    work.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(model, work / 'room-model.jsonl')
    if receipt is not False:
        (work / 'register-now-1.json').write_text(json.dumps(receipt or qualified_receipt()), encoding='utf-8')
    return work / 'room-model.jsonl'


class Qualified(unittest.TestCase):
    def refused(self, receipt):
        with self.assertRaises(ValueError) as caught:
            rl.qualified_registration(receipt, FIT_TRANSFORM)
        return str(caught.exception)

    def test_a_qualified_receipt_gives_its_scan_and_depth_scale(self):
        none = {'correction_factor': None, 'calibration_sha256': None}
        self.assertEqual(rl.qualified_registration(qualified_receipt(), FIT_TRANSFORM),
                         {'scan_revision': 'rev-1', 'depth_scale': 1.0, **none})
        scaled = qualified_receipt(depth_scale=0.971, take='20261113-200000-x0.971-m')
        self.assertEqual(rl.qualified_registration(scaled, FIT_TRANSFORM), {'scan_revision': 'rev-1', 'depth_scale': 0.971, **none})

    def test_the_receipts_camera_correction_comes_with_it(self):
        """source_correction as register_now.check_source records it: every frame applied, one factor, one hash."""
        found = {'frames': 98, 'applied': 98, 'factors': [0.889], 'calibrations': ['c' * 64], 'why': []}
        got = rl.qualified_registration(qualified_receipt(source_correction=found), FIT_TRANSFORM)
        self.assertEqual((got['correction_factor'], got['calibration_sha256']), (0.889, 'c' * 64))
        for other in (None, dict(found, applied=97), dict(found, factors=[0.889, 0.901]), dict(found, factors=[]),
                      dict(found, calibrations=['None']), dict(found, calibrations=['c' * 64, 'd' * 64]),
                      dict(found, frames=0, applied=0), dict(found, factors=[1.7])):
            with self.subTest(other=other):
                got = rl.qualified_registration(qualified_receipt(source_correction=other), FIT_TRANSFORM)
                self.assertEqual((got['correction_factor'], got['calibration_sha256']), (None, None))

    def test_every_other_receipt_is_refused_in_words(self):
        v1 = {'schema': 'den-live-register-now-1', 'fit': {'result': 'registered', 'transform_id': FIT_TRANSFORM}, 'scan_revision': 'rev-1'}
        for receipt, words in (
                (qualified_receipt('den-virtual@2026-01-01.9'), "step 2's receipt didn't register %s" % FIT_TRANSFORM),
                (qualified_receipt(fit={'result': 'refused', 'transform_id': None}), "didn't register"),
                (v1, 'is from before registrations were qualified'),
                (qualified_receipt(qualified=False), 'step 2 registered %s but didn\'t qualify it' % FIT_TRANSFORM),
                (qualified_receipt(qualified='yes'), "didn't qualify it"),
                (qualified_receipt(scan_revision=None), "doesn't say which scan it was picked on"),
                (qualified_receipt(depth_scale=1.2, take='t-x1.2-m'), 'has no depth scale den-live could apply'),
                (qualified_receipt(depth_scale=None), 'has no depth scale den-live could apply'),
                (qualified_receipt(depth_scale=0.97), 'asks for depth x0.97, but its take was made at x1'),
                (None, "didn't register")):
            self.assertIn(words, self.refused(receipt))

    def test_the_newest_receipt_that_registered_it_decides(self):
        work = scratch('den-live-register-')
        (work / 'register-now-1.json').write_text(json.dumps(qualified_receipt()), encoding='utf-8')
        (work / 'register-now-2.json').write_text(json.dumps(qualified_receipt(qualified=False)), encoding='utf-8')
        with self.assertRaises(rl.Refusal) as caught:
            rl.receipt_registration(work, FIT_TRANSFORM)
        self.assertEqual(str(caught.exception), "step 2 registered %s but didn't qualify it: paste register_now.py again" % FIT_TRANSFORM)
        (work / 'register-now-3.json').write_text(json.dumps(qualified_receipt(scan_revision='rev-3')), encoding='utf-8')
        self.assertEqual(rl.receipt_registration(work, FIT_TRANSFORM)['scan_revision'], 'rev-3')


@unittest.skipUnless(HAVE_SET, 'needs a prepared den-sim set')
class RunLocalRefuses(unittest.TestCase):
    """run_local.py on a room model whose receipt is missing or unqualified: one sentence naming the paste, before the
    camera is read or the detector starts."""

    def run_local(self, receipt):
        root = scratch('den-live-root-')
        model = registered_work(root, receipt)
        app = root / 'app-checkout'
        (app / 'app' / 'src' / 'home-3d').mkdir(parents=True)
        (app / 'app' / 'src' / 'home-3d' / 'apartment-people-layer.mjs').touch()
        (root / 'token.txt').write_text('t' * 32, encoding='ascii')
        started = []
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                unittest.mock.patch.object(rl.sc, 'archive', lambda tools_root, key, real=sc.archive: real(TOOLS, key)), \
                unittest.mock.patch.object(rl.perceive, 'DetectorWorker', lambda *a, **k: started.append(1)):
            code = rl.main([], root=root, console=lambda: True, camera_url='http://127.0.0.1:9', token_file=root / 'token.txt',
                           venv=Path(sys.executable), app=app, room_model=model, camera_id='den-virtual',
                           floor=[-2.5, -3.0, 2.5, 2.0], require_correction=False, until=lambda: True)
        self.assertEqual(started, [], 'refused before the detector')
        return code, out.getvalue()

    def test_a_room_model_with_no_receipt(self):
        code, text = self.run_local(False)
        self.assertEqual(code, 2)
        self.assertEqual(text.strip(), 'live: stopped: no step 2 receipt registered %s: paste register_now.py again' % FIT_TRANSFORM)

    def test_a_receipt_that_registered_it_but_didnt_qualify_it(self):
        code, text = self.run_local(qualified_receipt(qualified=False))
        self.assertEqual(code, 2)
        self.assertEqual(text.strip(), "live: stopped: step 2 registered %s but didn't qualify it: paste register_now.py again"
                         % FIT_TRANSFORM)


# ---- step 2's depth scale, applied before any lift (contract 1, den-live-register-now-2's depth_scale) ---------------

def model_made_at(scale, folder=None):
    """The set's room model with its registration's take renamed as register_now.py names a copy at this scale."""
    folder = folder or scratch('den-live-model-')
    lines = (FIT / 'room-model.jsonl').read_text(encoding='utf-8').strip().splitlines()
    revision = json.loads(lines[-1])
    for camera in revision['cameras']:
        camera['mount']['take'] = '20261113-200000-x%g-m' % scale
    (folder / 'room-model.jsonl').write_text('\n'.join(lines[:-1] + [json.dumps(revision)]) + '\n', encoding='utf-8')
    return folder / 'room-model.jsonl'


@unittest.skipUnless(HAVE_SET, 'needs a prepared den-sim set')
class DepthScale(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = perceive.Tools(sc.archive(TOOLS, 'observe'), sc.archive(TOOLS, 'track'))
        sys.path.insert(0, str(cls.tools.observe_tree / 'tools' / 'den-take'))

    def perceiver(self, room_model, scale):
        copy = perceive.track_method_copy(self.tools, [-3.0, -3.5, 3.0, 2.5], scratch('den-live-method-'))
        return perceive.Perceiver(self.tools, room_model_path=room_model, camera_id='den-virtual', max_error_bound_m=0.10,
                                  track_method_path=copy, detector=RecordedDetector(READING), home='den',
                                  require_source_correction=False, now=lambda: 1_800_000_000.0, depth_scale=scale)

    def test_den_live_refuses_a_scale_it_doesnt_apply(self):
        """A registration fitted on depth x0.971 with den-live applying none, and the other way round: refused."""
        for model, scale, words in ((model_made_at(0.971), 1.0, 'was fitted on depth x0.971, and den-live would apply x1'),
                                    (FIT / 'room-model.jsonl', 0.971, 'was fitted on depth x1, and den-live would apply x0.971'),
                                    (model_made_at(1.2), 1.2, "den-live's depth scale must lie within 0.9-1.1")):
            with self.assertRaises(perceive.Refused) as caught:
                self.perceiver(model, scale)
            self.assertIn(words, str(caught.exception))
        self.assertEqual(self.perceiver(model_made_at(0.971), 0.971).depth_scale, 0.971)

    def test_every_depth_plane_is_scaled_before_the_lift_and_one_changes_nothing(self):
        frames = bundles(READING)[:3]
        plain, scaled = self.perceiver(FIT / 'room-model.jsonl', 1.0), self.perceiver(model_made_at(0.971), 0.971)
        for p in (plain, scaled):
            p.process(frames[0], {'status': 'ok', 'why': None}, frames[0].t_host + 0.35)
        raw = self.tools.takes.read_frame(frames[1].data)[1]
        same = plain.prepare(frames[1], frames[1].t_host + 0.35).depth
        self.assertEqual(list(same.mm), list(raw.mm), 'at 1.0 the plane is the bundle\'s own')
        self.assertIs(plain.scaled(raw), raw)
        got = scaled.prepare(frames[1], frames[1].t_host + 0.35).depth
        self.assertEqual((got.width, got.height, got.t_host, got.sequence), (raw.width, raw.height, raw.t_host, raw.sequence))
        self.assertEqual(list(got.mm), [round(v * 0.971) if v else 0 for v in raw.mm])

    def test_the_lift_sees_the_scaled_depth(self):
        """The same reading take, lifted at x1 and at x0.97 on registrations made at those scales: the reader's marker
        comes 3% nearer the camera along its ray, as the depth says."""
        def last(p):
            events = []
            for bundle in bundles(READING):
                event = p.process(bundle, {'status': 'ok', 'why': None}, bundle.t_host + 0.35)
                if event is not None and event['tracks']:
                    events.append(event)
            return events[-1]['tracks'][0]['xy_m']
        plain, scaled = self.perceiver(FIT / 'room-model.jsonl', 1.0), self.perceiver(model_made_at(0.97), 0.97)
        camera = plain.camera.room_from_left[1][:2]   # the left camera's place: depth runs from it
        near, far = math.dist(last(scaled), camera), math.dist(last(plain), camera)
        self.assertAlmostEqual(near / far, 0.97, delta=0.012)


@unittest.skipUnless(HAVE_SET, 'needs a prepared den-sim set')
class RunLocalScale(unittest.TestCase):
    """run_local.py hands step 2's depth scale to the live loop, and refuses a receipt whose scale the room model's
    registration wasn't made at, before the detector starts."""

    def run_local(self, receipt, model, require=False, seen=None):
        root = scratch('den-live-root-')
        work = root / 'private' / 'register'
        work.mkdir(parents=True)
        shutil.copyfile(model, work / 'room-model.jsonl')
        (work / 'register-now-1.json').write_text(json.dumps(receipt), encoding='utf-8')
        app = root / 'app-checkout'
        (app / 'app' / 'src' / 'home-3d').mkdir(parents=True)
        (app / 'app' / 'src' / 'home-3d' / 'apartment-people-layer.mjs').touch()
        (root / 'token.txt').write_text('t' * 32, encoding='ascii')

        def detector(*args, **kw):
            raise perceive.Stop('the test stops here, at the detector')

        def perceiver(*args, real=perceive.Perceiver, **kw):
            made = real(*args, **kw)
            if seen is not None:
                seen.append(made)
            return made
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                unittest.mock.patch.object(rl.sc, 'archive', lambda tools_root, key, real=sc.archive: real(TOOLS, key)), \
                unittest.mock.patch.object(rl.perceive, 'DetectorWorker', detector), \
                unittest.mock.patch.object(rl.perceive, 'Perceiver', perceiver):
            code = rl.main([], root=root, console=lambda: True, camera_url='http://127.0.0.1:9', token_file=root / 'token.txt',
                           venv=Path(sys.executable), app=app, room_model=work / 'room-model.jsonl', camera_id='den-virtual',
                           floor=[-2.5, -3.0, 2.5, 2.0], require_correction=require, until=lambda: True)
        return code, out.getvalue()

    def test_the_receipts_correction_reaches_the_perceiver_and_a_receipt_naming_none_is_refused(self):
        """The camera's correction the take was made on goes to the Perceiver. With the correction required, a receipt
        that names none (one from before source_correction, say) is refused before the detector starts."""
        found = {'frames': 98, 'applied': 98, 'factors': [0.889], 'calibrations': ['c' * 64], 'why': []}
        seen = []
        code, text = self.run_local(qualified_receipt(source_correction=found), FIT / 'room-model.jsonl', require=True, seen=seen)
        self.assertIn('live: stopped: the test stops here, at the detector', text)
        self.assertEqual([p.source_correction for p in seen], [(0.889, 'c' * 64)])
        seen = []
        code, text = self.run_local(qualified_receipt(), FIT / 'room-model.jsonl', require=True, seen=seen)
        self.assertEqual((code, seen), (2, []))
        self.assertEqual(text.strip(), "live: stopped: step 2's receipt for %s doesn't name the camera's depth correction its "
                                       "take was made on: register again with step 2's register_now.py" % FIT_TRANSFORM)
        code, text = self.run_local(qualified_receipt(), FIT / 'room-model.jsonl', require=False, seen=seen)
        self.assertEqual([p.source_correction for p in seen], [None], 'not required (den-sim\'s renders): nothing to hold to')

    def test_a_qualified_scale_reaches_the_perceiver(self):
        code, text = self.run_local(qualified_receipt(depth_scale=0.971, take='20261113-200000-x0.971-m'), model_made_at(0.971))
        self.assertIn("Registration %s; the camera's correction not required; den-live's depth scale x0.971." % FIT_TRANSFORM, text)
        self.assertIn('live: stopped: the test stops here, at the detector', text)

    def test_den_live_refuses_a_receipt_with_a_scale_the_registration_wasnt_made_at(self):
        code, text = self.run_local(qualified_receipt(depth_scale=0.971, take='20261113-200000-x0.971-m'), FIT / 'room-model.jsonl')
        self.assertEqual(code, 2)
        self.assertEqual(text.strip(), "live: stopped: %s was fitted on depth x1, and den-live would apply x0.971: register "
                                       "again with step 2's register_now.py" % FIT_TRANSFORM)
        code, text = self.run_local(qualified_receipt(depth_scale=0.971), model_made_at(0.971))
        self.assertEqual(code, 2)
        self.assertIn('asks for depth x0.971, but its take was made at x1: paste register_now.py again', text)


# ---- the plan's 02-3 real-replay check (replay_person_state_check.py) ----------------------------------------------

class ReplayVerdict(unittest.TestCase):
    """The three lines' verdicts, the live loop's pacing and the cues' clock, on synthetic numbers."""

    def test_three_lines_and_what_passes(self):
        import replay_person_state_check as check
        good = [(2.1, [(5.0, 5.0), (7.5, 7.5)], 0), (2.45, [(9.0, 9.0)], 0)]
        lines, passed = check.lines(good)
        self.assertEqual(lines, ['since, ask less preview: 0.000 s at most over 3 replies (should be 0)',
                                 'seated latency per trial: 2.10 s, 2.45 s (each should be 2.5 s or less)',
                                 'revision bumps while sitting per trial: 0, 0 (each should be 0)'])
        self.assertTrue(passed)
        for bad, words in (([(2.1, [(5.0, 4.75)], 0)], '0.250 s at most over 1 replies'),
                           ([(2.6, [(5.0, 5.0)], 0)], '2.60 s'),
                           ([(None, [(5.0, 5.0)], 0)], 'never seated'),
                           ([(2.1, [(5.0, 5.0)], 1)], 'per trial: 1 ('),
                           ([(2.1, [(5.0, None)], 0)], '1 with no seated since on one side'),
                           ([(2.1, [], 0)], 'no ask was answered')):
            with self.subTest(words=words):
                lines, passed = check.lines(bad)
                self.assertFalse(passed)
                self.assertIn(words, ' | '.join(lines))

    def test_the_detector_takes_the_newest_frame_when_free(self):
        import replay_person_state_check as check
        frames = [{'arrival': 10.0 + i * 0.05, 'compute': 0.12, 'n': i} for i in range(8)]
        taken = check.paced(frames)
        self.assertEqual([(frame['n'], round(done, 2)) for frame, done in taken],
                         [(0, 10.12), (2, 10.24), (4, 10.36), (7, 10.48)], 'free at 10.36, the mailbox holds frame 7')
        slow = [{'arrival': 10.0 + i * 0.25, 'compute': 0.07, 'n': i} for i in range(3)]
        self.assertEqual([frame['n'] for frame, _ in check.paced(slow)], [0, 1, 2], 'a free detector takes every frame')

    def test_a_cue_goes_on_the_cameras_clock(self):
        import replay_person_state_check as check
        recv, host = [100.0, 100.05, 100.1], [5.0, 5.25, 5.5]   # recorded five times faster than the camera's clock
        self.assertAlmostEqual(check.camera_time(100.07, recv, host), 5.35)
        self.assertAlmostEqual(check.camera_time(99.99, recv, host), 4.95, msg='before the first frame, its own pace')


@unittest.skipUnless(HAVE_SET, 'needs a prepared den-sim set')
class ReplayCheck(unittest.TestCase):
    """replay_person_state_check.py on den-sim's renders: exactly three lines of words and numbers, never a coordinate."""
    SHAPES = (r'^since, ask less preview: (?:\d+\.\d{3} s at most over \d+ replies(?:, \d+ with no seated since on one '
              r'side)?|no ask was answered, so nothing to compare) \(should be 0\)$',
              r'^seated latency per trial: (?:never seated|\d+\.\d\d s)(?:, (?:never seated|\d+\.\d\d s))* '
              r'\(each should be 2\.5 s or less\)$',
              r'^revision bumps while sitting per trial: \d+(?:, \d+)* \(each should be 0\)$')

    def run_check(self, *argv, folder=SET):
        import replay_person_state_check as check
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.main([str(folder), '--floor=-3,-3.5,3,2.5', *argv], tools_root=TOOLS)
        lines = out.getvalue().splitlines()
        if code != 2:
            self.assertEqual(len(lines), 3, 'exactly three lines')
            for line, shape in zip(lines, self.SHAPES):
                self.assertRegex(line, shape)
        return code, lines

    def test_the_reading_take_has_one_since_and_no_bumps(self):
        code, lines = self.run_check('--take', READING.name)
        self.assertRegex(lines[0], r'^since, ask less preview: 0\.000 s at most over [1-9]\d* replies \(should be 0\)$')
        self.assertEqual(lines[2], 'revision bumps while sitting per trial: 0 (each should be 0)')
        latency = float(re.search(r'(\d+\.\d\d) s', lines[1]).group(1))
        # den-sim's scenes start seated: den-track's association settles first (about 1.9 s from the first frame), and
        # only then can the 2 s hold begin. A sit-down after walking in is tracked before it, so it has no such wait.
        self.assertTrue(3.0 < latency < 4.5, latency)
        self.assertEqual(code, 0 if latency <= 2.5 else 1)

    def test_a_standing_take_is_never_seated_and_never_asked(self):
        standing = next(json.loads((folder / 'take.json').read_text(encoding='utf-8'))['take'] for folder in
                        (SET / 'truth').iterdir() if json.loads((folder / 'take.json').read_text(encoding='utf-8'))
                        ['person']['where'].startswith('standing'))
        code, lines = self.run_check('--take', standing)
        self.assertEqual(code, 1)
        self.assertEqual(lines[:2], ['since, ask less preview: no ask was answered, so nothing to compare (should be 0)',
                                     'seated latency per trial: never seated (each should be 2.5 s or less)'])

    def test_a_folder_with_no_trial_stops_in_one_line(self):
        code, lines = self.run_check(folder=scratch('den-live-replays-'))
        self.assertEqual((code, lines), (2, ["replay check: stopped: no take in that folder has a begin cue and recorded "
                                             "detector rows (den-sim's prepared format)."]))


# ---- end to end: the real detector, the fake camera, the page ------------------------------------------------------

@unittest.skipUnless(HAVE_SET and NODE and sc.VENV_PY.is_file() and (rl.APP / 'app' / 'src' / 'home-3d' / 'apartment-people-layer.mjs').is_file(),
                     'needs the detector venv, Node, the den-live-home worktree and a prepared den-sim set')
class Live(unittest.TestCase):
    def test_the_reading_take_live(self):
        root = scratch('den-live-root-')
        camera = ts.FakeCamera(FIT / 'camera-calibration.json', READING)
        self.addCleanup(camera.close)
        self.addCleanup(camera.assert_allowed_paths)   # runs first: the live loop asks for /frame.npz only
        token = root / 'token.txt'
        token.write_text(camera.token, encoding='ascii')
        page_port, stream_port, seen, started = free_port(), free_port(), {}, time.monotonic()

        def until():
            if 'page' not in seen and 'url' in seen and time.monotonic() - started > 25:
                with urllib.request.urlopen(seen['url'] + 'api/den-live/v1/homes/den/state', timeout=5) as response:
                    seen['page'] = json.loads(response.read())['people']
                with urllib.request.urlopen(seen['url'] + 'api/den-live/v1/homes/den/stream', timeout=5) as response:
                    for raw in response:
                        if raw.startswith(b'event: people'):
                            seen['sse'] = True
                            break
                with self.assertRaises(urllib.error.HTTPError) as refused:
                    urllib.request.urlopen(f'http://127.0.0.1:{stream_port}/api/den-live/v1/homes/den/body', timeout=5)
                with refused.exception as response:
                    self.assertEqual(response.code, 503)
                    self.assertEqual(json.load(response)['error'], 'body_unavailable')
            elapsed = time.monotonic() - started
            if elapsed > 60 or (elapsed > 40 and 'url' not in seen):
                return True
            if elapsed <= 40:
                return False
            # End on a frame with the reader tracked. The fake camera loops its take, and each wrap starts a new epoch
            # with no confirmed track for about half a second; ending there made the last event empty now and then.
            with urllib.request.urlopen(seen['url'] + 'api/den-live/v1/homes/den/state', timeout=5) as response:
                return len(json.loads(response.read())['people']['tracks']) == 1
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = rl.run(root=root, camera_url=camera.url, token_file=token, venv=sc.VENV_PY, app=rl.APP, packs=None,
                            room_model=registered_work(root), camera_id='den-virtual', page_room='authored',
                            open_page=lambda url: seen.update(url=url), until=until, status_every_s=5,
                            floor=[-2.5, -3.0, 2.5, 2.0], require_correction=False, page_extra=('--tmp', str(root)),
                            page_port=page_port, stream_port=stream_port)
        text = out.getvalue()
        self.assertEqual(text, text.encode('ascii', 'replace').decode('ascii'), 'the output is ASCII')
        self.assertRegex(text, r'camera ok \| \d+\.\d frames a second \| 1 people \| 0\.\d\d s behind \| \d+ dropped a minute \| \d+ frames')
        latest = result['latest']
        self.assertEqual(len(latest['tracks']), 1)
        self.assertLess(math.dist(latest['tracks'][0]['xy_m'], truth(READING)), 0.3)
        self.assertLess(latest['health']['lag_s_median'], 1.0)
        self.assertEqual(result['counts']['detector_errors'], 0)
        self.assertEqual(seen['page']['schema'], 'den-live-people-1', 'the page passes the stream through its own origin')
        self.assertTrue(seen.get('sse'))
        # No coordinate is printed: every line is one of the shapes run_local prints, whose only numbers are counts, rates
        # and times. The control: a line holding a position would match none of them.
        shapes = (r'^Starting the detector', r'^Registration den-virtual@\S+; the camera', r'^Live: open http://127\.0\.0\.1:\d+/ ',
                  r'^camera ok(?:: camera clock moved back)? \| [\d.-]+ frames a second \| \d+ people \| '
                  r'[\d.-]+( s)? behind \| \d+ dropped a minute \| \d+ frames(?:, \d+ out of order)?(?:, \d+ too late)?$')
        self.assertIsNone(result['body'])
        known = lambda line: any(re.search(shape, line) for shape in shapes)
        self.assertTrue(all(known(line) for line in text.splitlines() if line.strip()), text)
        self.assertFalse(known('camera ok | 4.0 frames a second | 1 people at [0.01, 0.04] | 0.25 s behind'), 'the control')


class Wiring(unittest.TestCase):
    def test_no_console_touches_nothing(self):
        root = scratch('den-live-root-')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rl.main([], root=root, console=lambda: False), 2)
        self.assertIn('no console', out.getvalue())
        self.assertEqual(list(root.iterdir()), [])

    def test_no_registration_says_paste_step_2_first(self):
        root = scratch('den-live-root-')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rl.main([], root=root, console=lambda: True, token_file=root / 'missing.txt'), 2)
        self.assertIn("no registration yet: paste step 2's line", out.getvalue())


if __name__ == '__main__':
    unittest.main()
