"""The live body's plumbing in den-live: the stream's /body path, who the body is made for, and the loop's link to
body_worker.py. body.py's own tests are test_body.py (the detector's venv).

    python -B -X utf8 tools\\den-live\\test_body_link.py

The link tests run body_worker.py in the detector's venv and are skipped without it.
"""
import io
import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
import dataclasses
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import body_link  # noqa: E402
import perceive  # noqa: E402
import sofa_check as sc  # noqa: E402  the venv
import stream as people_stream  # noqa: E402


def sse(url, count, timeout=5):
    got, done = [], threading.Event()

    def read():
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
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
        finally:
            done.set()
    threading.Thread(target=read, daemon=True).start()
    return got, done


class BodyPath(unittest.TestCase):
    def setUp(self):
        self.stream = people_stream.PeopleStream(port=0, heartbeat_s=0.3, max_clients=2).start()
        self.base = 'http://127.0.0.1:%d/api/den-live/v1/homes/den/' % self.stream.port
        self.addCleanup(self.stream.close)

    def test_body_frames_go_on_their_own_path_and_never_on_the_people_stream(self):
        bodies, body_done = sse(self.base + 'body', 3)
        people, people_done = sse(self.base + 'stream', 3)
        time.sleep(0.2)
        self.stream.publish_body({'schema': 'den-live-body-1', 'seq': 4, 'track_id': 'e1-trk-1'})
        self.stream.publish({'schema': 'den-live-people-1', 'seq': 5, 'tracks': []})
        self.assertTrue(body_done.wait(5) and people_done.wait(5))
        self.assertIn(['id: 4', 'event: body', 'data: {"schema":"den-live-body-1","seq":4,"track_id":"e1-trk-1"}'], bodies)
        self.assertFalse(any('event: people' in line for block in bodies for line in block))
        self.assertFalse(any('event: body' in line for block in people for line in block))
        self.assertIn([': heartbeat'], bodies)

    def test_a_new_body_client_gets_the_newest_frame_and_the_cap_holds(self):
        self.stream.publish_body({'schema': 'den-live-body-1', 'seq': 9})
        got, done = sse(self.base + 'body', 2)
        self.assertTrue(done.wait(5))
        self.assertIn('id: 9', got[1])
        time.sleep(0.8)   # the server notices that reader's close at its next heartbeat
        with urllib.request.urlopen(self.base + 'body', timeout=5), urllib.request.urlopen(self.base + 'body', timeout=5):
            time.sleep(0.2)
            with self.assertRaises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(self.base + 'body', timeout=5)
            self.assertEqual(refused.exception.code, 503)
            with urllib.request.urlopen(self.base + 'stream', timeout=5):
                pass   # the people stream has its own cap

    def test_a_frame_without_a_seq_still_has_an_id(self):
        got, done = sse(self.base + 'body', 2)
        time.sleep(0.2)
        self.stream.publish_body({'schema': 'den-live-body-1', 'seq': None})
        self.assertTrue(done.wait(5))
        self.assertIn('id: 0', got[1])


class BodyPerson(unittest.TestCase):
    def pose(self, obs_id, joints):
        return {'obs_id': obs_id, 'kind': 'keypoints', 'validity': 'valid',
                'joints_room': [{'j': j, 'xyz': xyz, 'status': status} for j, xyz, status in joints]}

    def test_the_first_confirmed_track_with_a_pose_gives_its_room_joints(self):
        tracks = [{'track_id': 'e1-trk-2', 'state': 'coasting', 'xy_m': [3, 3]}, {'track_id': 'e1-trk-1', 'state': 'confirmed', 'xy_m': [1, 2]}]
        observations = [self.pose('o-1', [(0, [1, 2, 1.6], 'supported'), (9, [1.2, 2, 1.0], 'uncertain'), (15, None, 'unsupported'),
                                          (16, [1, 2, 0.1], 'unsupported')])]
        person = perceive.body_person(observations, tracks, [set(), {'o-1'}])
        self.assertEqual(person['track_id'], 'e1-trk-1')
        self.assertEqual(person['joints'], {'nose': [[1, 2, 1.6], 'supported'], 'left_wrist': [[1.2, 2, 1.0], 'uncertain']})

    def test_no_pose_gives_the_track_alone_and_nobody_gives_none(self):
        tracks = [{'track_id': 'e1-trk-1', 'state': 'confirmed', 'xy_m': [1, 2]}]
        self.assertEqual(perceive.body_person([], tracks, [set()]), {'track_id': 'e1-trk-1', 'xy_m': [1, 2], 'joints': {}})
        self.assertIsNone(perceive.body_person([], [], []))
        coasting = [{'track_id': 'e1-trk-3', 'state': 'coasting', 'xy_m': [0, 0]}]
        self.assertEqual(perceive.body_person([], coasting, [set()])['track_id'], 'e1-trk-3', 'someone coasting is not an empty den')

    def test_someone_not_tracked_yet_is_not_an_empty_den(self):
        seen = [{'obs_id': 'o-2', 'kind': 'person_box', 'validity': 'valid'}]
        self.assertEqual(perceive.body_person(seen, [], []), {'track_id': None, 'xy_m': None, 'joints': {}})


def bundle(depth_rows=None):
    import struct
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:   # a minimal depth_mm.npy: 400x640 uint16, all one value
        header = "{'descr': '<u2', 'fortran_order': False, 'shape': (400, 640), }"
        header += ' ' * (118 - len(header)) + '\n'
        z.writestr('depth_mm.npy', b'\x93NUMPY\x01\x00' + struct.pack('<H', len(header)) + header.encode('latin1')
                   + (depth_rows or (3000).to_bytes(2, 'little') * (400 * 640)))
    return buf.getvalue()


@unittest.skipUnless(sc.VENV_PY.is_file(), "needs the detector's venv")
class Link(unittest.TestCase):
    SETUP = {'left_k': [400.0, 400.0, 320.0, 200.0], 'room_from_left': [[[1, 0, 0], [0, 0, 1], [0, -1, 0]], [0, 0, 1.4]], 'home': 'den'}

    def link(self, frames, argv=None, **kw):
        log = Path(self.tmp) / 'body.log'
        made = body_link.BodyLink(argv or [str(sc.VENV_PY), '-B', str(HERE / 'body_worker.py')], log, setup=self.SETUP,
                                  on_frame=frames.append, **kw)
        self.addCleanup(made.close)
        return made

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix='den-live-body-')

    def wait_idle(self, link, timeout=10):
        end = time.monotonic() + timeout
        while link.busy and time.monotonic() < end:
            time.sleep(0.01)
        self.assertFalse(link.busy)

    def test_an_empty_den_is_learned_without_a_frame_and_a_busy_body_skips(self):
        frames = []
        link = self.link(frames)
        self.assertTrue(link.alive, link.stopped)
        data, t = bundle(), 0.0
        self.assertTrue(link.submit(data, now=t, capture_ts=1.0, seq=1, person=None))
        self.assertFalse(link.submit(data, now=t, capture_ts=1.0, seq=2, person=None), 'never queued behind a busy body')
        self.assertEqual(link.counts['skipped_busy'], 1)
        self.wait_idle(link)
        self.assertEqual(link.why, 'nobody tracked')
        person = {'track_id': 'e1-trk-1', 'xy_m': [0, 2.5], 'joints': {}}
        self.assertTrue(link.submit(data, now=t + 0.5, capture_ts=1.5, seq=3, person=person))
        self.wait_idle(link)
        self.assertEqual(link.why, 'learning the empty room')
        self.assertFalse(link.submit(data, now=t + 0.6, capture_ts=1.6, seq=4, person=person), 'at most about 3 body frames a second')
        self.assertEqual(link.counts['skipped_rate'], 1)
        self.assertEqual(frames, [])

    def test_a_body_process_that_wont_start_turns_the_body_off_and_nothing_else(self):
        frames = []
        link = self.link(frames, argv=[sys.executable, '-c', 'import sys; sys.exit(3)'], start_timeout_s=10)
        self.assertFalse(link.alive)
        self.assertIn("didn't start", link.stopped)
        self.assertFalse(link.submit(bundle(), now=0, capture_ts=0, seq=1, person=None))

    def test_a_bad_bundle_is_an_error_count_not_a_stop(self):
        frames = []
        link = self.link(frames)
        link.submit(b'not a bundle', now=0, capture_ts=0, seq=1, person=None)
        self.wait_idle(link)
        self.assertEqual(link.counts['errors'], 1)
        self.assertTrue(link.alive)


def _renders():
    try:
        import test_live as tl
        return tl if tl.HAVE_SET else None
    except Exception:
        return None


TL = _renders()
EMPTY_TAKE, READING_TAKE = '84c26951a000', '85b080a5815b'   # the den-sim set's "Nobody there" and "Reading on the couch"


@unittest.skipUnless(TL and sc.VENV_PY.is_file() and (TL.SET / EMPTY_TAKE / 'detections.jsonl').is_file(),
                     "needs a prepared den-sim set and the detector's venv")
class OnRenders(unittest.TestCase):
    """The real perceiver (den-observe, den-track) and the real body process on the den simulation's renders, through
    the registration fit on them: the empty den first, then someone reading on the couch."""

    def test_the_empty_den_is_learned_then_the_person_is_a_body_where_their_marker_is(self):
        import base64
        import tempfile
        tools = perceive.Tools(sc.archive(TL.TOOLS, 'observe'), sc.archive(TL.TOOLS, 'track'))
        sys.path.insert(0, str(tools.observe_tree / 'tools' / 'den-take'))
        copy = perceive.track_method_copy(tools, [-3.0, -3.5, 3.0, 2.5], TL.scratch('den-live-method-'))
        clock = {'now': 0.0}
        empty, book = TL.SET / EMPTY_TAKE, TL.SET / READING_TAKE

        class Both:   # each take's own recorded rows
            def __init__(self):
                self.a, self.b = TL.RecordedDetector(empty), TL.RecordedDetector(book)
                self.which = self.a

            def detect(self, bundle, delay_s):
                return self.which.detect(bundle, delay_s)

        detector = Both()
        p = perceive.Perceiver(tools, room_model_path=TL.FIT / 'room-model.jsonl', camera_id='den-virtual', max_error_bound_m=0.10,
                               track_method_path=copy, detector=detector, home='den', require_source_correction=False,
                               now=lambda: 1_800_000_000.0 + clock['now'])
        camera = p.camera
        rotation, translation = camera.room_from_left
        frames = []
        link = body_link.BodyLink([str(sc.VENV_PY), '-B', str(HERE / 'body_worker.py')], Path(tempfile.mkdtemp()) / 'body.log',
                                  setup={'left_k': list(camera.rig.left_k[:4]), 'room_from_left': [[list(r) for r in rotation], list(translation)],
                                         'home': 'den'}, on_frame=frames.append)
        self.addCleanup(link.close)
        self.assertTrue(link.alive, link.stopped)
        p.body = link
        events, offset = [], 0.0

        def run(take_frames, shift):
            for b in take_frames:
                b = dataclasses.replace(b, t_host=b.t_host + shift, arrival_mono=b.arrival_mono + shift)
                clock['now'] = b.t_host + 0.35
                event = p.process(b, {'status': 'ok', 'why': None}, b.t_host + 0.35)
                if event is not None:
                    events.append(event)
                end = time.monotonic() + 10
                while link.busy and time.monotonic() < end:   # one frame at a time here, so none is skipped
                    time.sleep(0.005)
        first = TL.bundles(empty, session='s1')
        run(first, 0.0)
        span = first[-1].t_host - first[0].t_host
        self.assertGreater(span, 8.0, 'the empty take is long enough to learn the room')
        self.assertEqual(link.why, 'nobody tracked')
        detector.which = detector.b
        second = TL.bundles(book, session='s1')
        run(second, first[-1].t_host - second[0].t_host + 0.2)
        self.assertGreater(len(frames), 5, (link.counts, link.why))
        last = frames[-1]
        self.assertEqual(last['schema'], 'den-live-body-1')
        import array
        import statistics
        mm = array.array('h', base64.b64decode(last['points']['position_mm']))
        if sys.byteorder != 'little':
            mm.byteswap()
        kind = base64.b64decode(last['points']['kind'])
        pts = [(mm[3 * i] / 1000, mm[3 * i + 1] / 1000, mm[3 * i + 2] / 1000) for i in range(len(kind))]
        body = [p for p, k in zip(pts, kind) if k <= 1]
        marker = events[-1]['tracks'][0]['xy_m']
        centre = (statistics.median(p[0] for p in body), statistics.median(p[1] for p in body))
        self.assertLess(math.dist(centre, marker), 0.35, (centre, marker))
        heights = [p[2] for p in body]
        self.assertGreater(max(heights) - min(heights), 0.5, 'a seated reader spans more than half a metre')
        self.assertGreater(max(heights), 0.9, 'up to their head')
        self.assertEqual(last['track_id'], events[-1]['tracks'][0]['track_id'])
        print('\n  on renders: %d body frames; last %d points (%d front/back, %d guessed, %d held); joints %d'
              % (len(frames), len(kind), len(body), kind.count(3), kind.count(5), len(last.get('joints') or [])))


if __name__ == '__main__':
    unittest.main(verbosity=1)
