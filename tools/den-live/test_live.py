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

    def perceiver(self, take, floor=(-3.0, -3.5, 3.0, 2.5), require=False, room_model=None, luma=120.0, gate=0.10, change=None):
        copy = perceive.track_method_copy(self.tools, list(floor), scratch('den-live-method-'))
        return perceive.Perceiver(self.tools, room_model_path=room_model or FIT / 'room-model.jsonl', camera_id='den-virtual',
                                  max_error_bound_m=gate, track_method_path=copy, detector=RecordedDetector(take, luma, change),
                                  home='den', require_source_correction=require, now=lambda: 1_800_000_000.0 + self.now)

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
        self.assertEqual(set(last), {'track_id', 'state', 'xy_m', 'sigma_m', 'heading_deg', 'age_s', 'near'}, 'no joints, no image, no name')
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
                            room_model=FIT / 'room-model.jsonl', camera_id='den-virtual', page_room='authored',
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
