"""The live body's plumbing in den-live: the stream's /body path, who the body is made for, and the loop's link to the
body step in the detector's process. body.py's own tests are test_body.py (the detector's venv).

    python -B -X utf8 tools\\den-live\\test_body_link.py

The link tests run a stand-in for detect_worker.py in the detector's venv: the real protocol and the real body step,
with rows replayed instead of the models. They're skipped without the venv; the render test also needs a den-sim set.
"""
import dataclasses
import io
import json
import math
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
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


# A stand-in for detect_worker.py: its protocol, rows replayed from a detections.jsonl (or none), and the real body step.
STUB = r'''
import json, struct, sys
sys.path.insert(0, %(here)r)
import body
rows = {}
if len(sys.argv) > 1:
    for line in open(sys.argv[1], encoding="utf-8"):
        if line.strip():
            row = json.loads(line)
            rows[row["frame"]["rgb_seq"]] = row
out = sys.stdout
def say(value):
    out.write(json.dumps(value, separators=(",", ":")) + "\n"); out.flush()
def message():
    size = sys.stdin.buffer.read(4)
    if len(size) < 4:
        return None
    n = struct.unpack(">I", size)[0]
    data = b""
    while len(data) < n:
        chunk = sys.stdin.buffer.read(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data
say({"ready": True, "load_ms": 0, "warm_ms": 0})
kept, channel = None, body.BodyChannel()
while True:
    raw = message()
    if raw is None:
        sys.exit(0)
    header = json.loads(raw)
    if header.get("kind") in ("setup_body", "body"):
        say(channel.handle(header, kept))
        continue
    data = message()
    kept = (header["id"], data)
    row = rows.get(header["rgb_seq"])
    if row is None:
        say({"id": header["id"], "error": "KeyError"})
        continue
    row = dict(row, available_ts=row["frame"]["capture_ts"] + header["delay_s"] + 0.07)
    say({"id": header["id"], "row": row, "compute_s": 0.07, "luma": 120.0})
'''


def stub_argv(rows=None):
    folder = Path(tempfile.mkdtemp(prefix='den-live-stub-'))
    script = folder / 'stub_worker.py'
    script.write_text(STUB % {'here': str(HERE)}, encoding='utf-8')
    return [str(sc.VENV_PY), '-B', str(script)] + ([str(rows)] if rows else []), folder / 'stub.log'


def bundle(value_mm=3000):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:   # a minimal depth_mm.npy: 400x640 uint16, all one value
        header = "{'descr': '<u2', 'fortran_order': False, 'shape': (400, 640), }"
        header += ' ' * (118 - len(header)) + '\n'
        z.writestr('depth_mm.npy', b'\x93NUMPY\x01\x00' + struct.pack('<H', len(header)) + header.encode('latin1')
                   + value_mm.to_bytes(2, 'little') * (400 * 640))
    return buf.getvalue()


@dataclasses.dataclass
class FakeBundle:
    data: bytes
    rgb_seq: int = 1
    t_host: float = 0.0


SETUP = {'left_k': [400.0, 400.0, 320.0, 200.0], 'room_from_left': [[[1, 0, 0], [0, 0, 1], [0, -1, 0]], [0, 0, 1.4]], 'home': 'den'}


@unittest.skipUnless(sc.VENV_PY.is_file(), "needs the detector's venv")
class Link(unittest.TestCase):
    def detector(self, rows=None):
        argv, log = stub_argv(rows)
        worker = perceive.DetectorWorker(argv, log, start_timeout_s=60)
        self.addCleanup(worker.close)
        return worker

    def wait_idle(self, link, timeout=10):
        end = time.monotonic() + timeout
        while link.busy and time.monotonic() < end:
            time.sleep(0.01)
        self.assertFalse(link.busy)

    def test_the_body_runs_in_the_detectors_process_between_frames(self):
        worker = self.detector()
        frames = []
        link = body_link.BodyLink(worker, setup=SETUP, on_frame=frames.append)
        self.assertTrue(link.alive, link.stopped)
        reply = worker.detect(FakeBundle(bundle()), 0.0)          # the row: KeyError here (no rows), and the bundle is kept
        self.assertEqual(reply, {'id': 1, 'error': 'KeyError'})
        self.assertTrue(link.submit(worker.next_id, now=0.0, capture_ts=1.0, seq=1, person=None))
        self.assertFalse(link.submit(worker.next_id, now=0.0, capture_ts=1.0, seq=1, person=None), 'never two in flight')
        self.assertEqual(link.counts['skipped_busy'], 1)
        self.wait_idle(link)
        self.assertEqual(link.why, 'nobody tracked')
        reply = worker.detect(FakeBundle(bundle(), rgb_seq=2), 0.0)   # rows still come back in turn after a body reply
        self.assertEqual(reply['id'], 2)
        person = {'track_id': 'e1-trk-1', 'xy_m': [0, 2.5], 'joints': {}}
        self.assertTrue(link.submit(worker.next_id, now=0.5, capture_ts=1.5, seq=3, person=person))
        self.wait_idle(link)
        self.assertEqual(link.why, 'learning the empty room')
        self.assertFalse(link.submit(worker.next_id, now=0.6, capture_ts=1.6, seq=4, person=person), 'at most about 3 body frames a second')
        self.assertEqual(link.counts['skipped_rate'], 1)
        self.assertTrue(link.submit(worker.next_id - 1, now=1.0, capture_ts=2.0, seq=5, person=person))
        self.wait_idle(link)
        self.assertEqual(link.why, 'frame not kept', 'only the frame just detected')
        self.assertEqual(frames, [])

    def test_a_body_that_wont_set_up_turns_only_the_body_off(self):
        worker = self.detector()
        link = body_link.BodyLink(worker, setup={'left_k': 'nonsense'}, on_frame=lambda f: None)
        self.assertFalse(link.alive)
        self.assertIn("didn't set up", link.stopped)
        self.assertFalse(link.submit(worker.next_id, now=0, capture_ts=0, seq=1, person=None))
        self.assertEqual(worker.detect(FakeBundle(bundle()), 0.0)['id'], 1, 'the detector goes on')

    def test_a_detector_that_stops_turns_the_body_off(self):
        worker = self.detector()
        link = body_link.BodyLink(worker, setup=SETUP, on_frame=lambda f: None)
        self.assertTrue(link.alive)
        worker.proc.kill()
        worker.proc.wait(10)
        end = time.monotonic() + 10
        while link.alive and time.monotonic() < end:
            time.sleep(0.01)
        self.assertFalse(link.alive)
        self.assertEqual(link.stopped, 'the detector process stopped')


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
    """The real perceiver (den-observe, den-track), the real detector link and the real body step on the den simulation's
    renders, through the registration fit on them: the empty den first, then someone reading on the couch."""

    def test_the_empty_den_is_learned_then_the_reader_is_a_body_where_their_marker_is(self):
        import array
        import base64
        import statistics
        tools = perceive.Tools(sc.archive(TL.TOOLS, 'observe'), sc.archive(TL.TOOLS, 'track'))
        sys.path.insert(0, str(tools.observe_tree / 'tools' / 'den-take'))
        copy = perceive.track_method_copy(tools, [-3.0, -3.5, 3.0, 2.5], TL.scratch('den-live-method-'))
        empty, reading = TL.SET / EMPTY_TAKE, TL.SET / READING_TAKE
        # One file of rows for both takes, as one camera session: the reading take's RGB sequence numbers moved on by
        # SHIFT, since the two renders count from the same start.
        SHIFT = 100_000
        lines = (empty / 'detections.jsonl').read_text(encoding='utf-8').splitlines()
        for line in (reading / 'detections.jsonl').read_text(encoding='utf-8').splitlines():
            if line.strip():
                row = json.loads(line)
                row['frame']['rgb_seq'] += SHIFT
                lines.append(json.dumps(row))
        rows = Path(tempfile.mkdtemp(prefix='den-live-rows-')) / 'detections.jsonl'
        rows.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        first = TL.bundles(empty, session='s1')
        second = [dataclasses.replace(b, rgb_seq=b.rgb_seq + SHIFT) for b in TL.bundles(reading, session='s1')]
        argv, log = stub_argv(rows)
        worker = perceive.DetectorWorker(argv, log, start_timeout_s=60)
        self.addCleanup(worker.close)
        clock = {'now': 0.0}
        p = perceive.Perceiver(tools, room_model_path=TL.FIT / 'room-model.jsonl', camera_id='den-virtual', max_error_bound_m=0.10,
                               track_method_path=copy, detector=worker, home='den', require_source_correction=False,
                               now=lambda: 1_800_000_000.0 + clock['now'])
        camera = p.camera
        rotation, translation = camera.room_from_left
        frames = []
        link = body_link.BodyLink(worker, setup={'left_k': list(camera.rig.left_k[:4]), 'room_from_left': [[list(r) for r in rotation], list(translation)],
                                                 'home': 'den'}, on_frame=frames.append)
        self.assertTrue(link.alive, link.stopped)
        p.body = link
        events, times = [], []

        def run(take_frames, shift):
            for b in take_frames:
                b = dataclasses.replace(b, t_host=b.t_host + shift, arrival_mono=b.arrival_mono + shift)
                clock['now'] = b.t_host + 0.35
                event = p.process(b, {'status': 'ok', 'why': None}, b.t_host + 0.35)
                if event is not None:
                    events.append(event)
                end = time.monotonic() + 10
                while link.busy and time.monotonic() < end:   # here, one frame at a time, so the body never skips for being busy
                    time.sleep(0.005)
                if link.compute_s is not None:
                    times.append(link.compute_s)
        run(first, 0.0)
        self.assertGreater(first[-1].t_host - first[0].t_host, 8.0, 'the empty take is long enough to learn the room')
        self.assertEqual(link.why, 'nobody tracked')
        run(second, first[-1].t_host - second[0].t_host + 0.2)
        self.assertGreater(len(frames), 5, (link.counts, link.why))
        last = frames[-1]
        self.assertEqual(last['schema'], 'den-live-body-1')
        mm = array.array('h', base64.b64decode(last['points']['position_mm']))
        if sys.byteorder != 'little':
            mm.byteswap()
        kind = base64.b64decode(last['points']['kind'])
        pts = [(mm[3 * i] / 1000, mm[3 * i + 1] / 1000, mm[3 * i + 2] / 1000) for i in range(len(kind))]
        body = [q for q, k in zip(pts, kind) if k <= 1]
        marker = events[-1]['tracks'][0]['xy_m']
        centre = (statistics.median(q[0] for q in body), statistics.median(q[1] for q in body))
        self.assertLess(math.dist(centre, marker), 0.35, (centre, marker))
        heights = [q[2] for q in body]
        self.assertGreater(max(heights) - min(heights), 0.5, 'a seated reader spans more than half a metre')
        self.assertGreater(max(heights), 0.9, 'up to their head')
        self.assertEqual(last['track_id'], events[-1]['tracks'][0]['track_id'])
        size = len(json.dumps(last, separators=(',', ':')))
        self.assertLess(size, 150_000, 'a frame stays small enough for a phone')
        print('\n  on renders: %d body frames; last %d points (%d front/back, %d guessed, %d held), %d KB; joints %d; body step %.0f ms median, %.0f ms max'
              % (len(frames), len(kind), len(body), kind.count(3), kind.count(5), size // 1024, len(last.get('joints') or []),
                 1000 * statistics.median(times), 1000 * max(times)))


if __name__ == '__main__':
    unittest.main(verbosity=1)
