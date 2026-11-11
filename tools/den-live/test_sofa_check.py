"""Tests for sofa_check.py (live den view, step 1). Stdlib unittest; each check that can pass by accident has a control.

    python -B -X utf8 tools\\den-live\\test_sofa_check.py

The end-to-end part needs the detector's venv (%USERPROFILE%\\ml\\den-detector) and a prepared den-sim set
(DEN_SIM_SET, else %USERPROFILE%\\den-sim\\prepared\\fbc7bda3115e), and is skipped without them. It never touches a real
camera, LA or ssh: a fake camera on 127.0.0.1 (this file with --fake-camera) serves the set's rendered takes byte for
byte, switched at each spoken cue, and the ask runs ask_la.py's two visits through a local transport against the den
simulation's own fake vLLM (test_ask.py --fake-vllm). An audit hook refuses any process start that names ssh or scp.
Everything it writes is under a new TEMP folder.
"""
import contextlib
import csv
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
SSH_WORD = re.compile(r'(?<![\w-])(ssh|scp)(?![\w-])', re.IGNORECASE)


def no_ssh(event, args):
    """test_ask.py's rule: no process a test starts may name ssh or scp."""
    if event == 'subprocess.Popen':
        words = [args[0], *(args[1] if isinstance(args[1], (list, tuple)) else [args[1]])]
        if any(word is not None and SSH_WORD.search(str(word)) for word in words):
            raise PermissionError('a test may not start ssh or scp')


HERE = Path(__file__).resolve().parent
TEST_ASK = HERE.parent / 'den-sim' / 'test_ask.py'


# ---- the fake camera: its own process, loopback only ---------------------------------------------------------

def serve_camera(state):
    """/health, /calibration and /frame.npz with a bearer token, from state/: token, calibration.json, and
    scene.json {"take": folder, "since": unix}. /frame.npz is the take's usable frame whose t_host is due by the
    wall time since the switch, looping: a camera's latest frame, at the take's own pace."""
    state = Path(state)

    def loopback(event, args):
        if event in ('socket.bind', 'socket.connect') and not (type(args[1]) is tuple and args[1][:1] == ('127.0.0.1',)):
            raise PermissionError('the fake camera is loopback only')
        if event in ('socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr', 'socket.getnameinfo') and args[:1] != ('127.0.0.1',):
            raise PermissionError('the fake camera looks up no name')
    sys.addaudithook(loopback)
    import socketserver
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    token = (state / 'token').read_text(encoding='ascii').strip()
    cache = {}

    def frames(take):
        if take not in cache:
            folder = Path(take)
            spans = [(s['first_n'], s['last_n']) for s in json.loads((folder / 'check-1.json').read_text(encoding='utf-8'))['usable']['segments']]
            rows = [r for r in csv.DictReader(io.StringIO((folder / 'frames.csv').read_text(encoding='utf-8'))) if any(a <= int(r['n']) <= b for a, b in spans)]
            cache[take] = [(float(r['t_host_s']), folder / r['file']) for r in rows]
        return cache[take]

    class Server(ThreadingHTTPServer):
        def server_bind(self):
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = '127.0.0.1', self.server_address[1]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def answer(self, status, body, kind='application/octet-stream'):
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.headers.get('Authorization') != 'Bearer ' + token:
                return self.answer(401, b'{}')
            scene = json.loads((state / 'scene.json').read_text(encoding='utf-8'))
            if self.path == '/calibration':
                return self.answer(200, (state / 'calibration.json').read_bytes(), 'application/json')
            if self.path == '/health':
                return self.answer(200, json.dumps({'healthy': True, 'session': 'fake'}).encode(), 'application/json')
            if self.path == '/frame.npz':
                listed = frames(scene['take'])
                first, span = listed[0][0], listed[-1][0] - listed[0][0]
                due = first + (time.time() - scene['since']) % span
                path = [p for t, p in listed if t <= due][-1]
                with open(state / 'served.log', 'a', encoding='utf-8') as log:
                    log.write(scene['take'] + '\n')
                return self.answer(200, path.read_bytes())
            return self.answer(404, b'{}')

    server = Server(('127.0.0.1', 0), Handler)
    (state / 'port.part').write_text(str(server.server_address[1]), encoding='ascii')
    os.replace(state / 'port.part', state / 'port')
    server.serve_forever()


if __name__ == '__main__' and sys.argv[1:2] == ['--fake-camera']:
    sys.exit(serve_camera(sys.argv[2]))

sys.addaudithook(no_ssh)
import sofa_check as sc  # noqa: E402

READING = {'people_visible': 'one', 'subject_posture': 'sitting', 'held_object': 'book', 'book_state': 'open', 'gaze_target': 'book', 'typing_visible': 'no',
           'screen_facing_subject': 'not_visible', 'activity_hypothesis': 'reading', 'visible_basis': ['person_visible', 'book_in_hands'], 'change_vs_prior': 'no_prior'}
PHONE = {**READING, 'held_object': 'phone', 'book_state': 'not_visible', 'gaze_target': 'phone', 'activity_hypothesis': 'phone_use', 'visible_basis': ['person_visible', 'phone_in_hands']}
UNCLEAR = {**READING, 'held_object': 'none', 'book_state': 'not_visible', 'gaze_target': 'unclear', 'activity_hypothesis': 'unclear', 'visible_basis': ['person_visible']}


def scratch(prefix):
    return Path(tempfile.mkdtemp(prefix=prefix))


def started(argv, state):
    child = subprocess.Popen(argv)
    for _ in range(400):
        if (state / 'port').is_file():
            return child, int((state / 'port').read_text(encoding='ascii'))
        time.sleep(0.05)
    child.terminate()
    raise RuntimeError('a fake did not start')


class FakeCamera:
    def __init__(self, calibration, take):
        self.state = scratch('den-live-fake-camera-')
        self.token = secrets.token_urlsafe(24)
        (self.state / 'token').write_text(self.token, encoding='ascii')
        shutil.copyfile(calibration, self.state / 'calibration.json')
        self.show(take)
        self.child, self.port = started([sys.executable, '-B', '-X', 'utf8', __file__, '--fake-camera', str(self.state)], self.state)
        self.url = 'http://127.0.0.1:%d' % self.port

    def show(self, take):
        part = self.state / 'scene.part'
        part.write_text(json.dumps({'take': str(take), 'since': time.time()}), encoding='utf-8')
        for _ in range(50):   # Windows refuses a replace while the server has the file open for a moment
            try:
                os.replace(part, self.state / 'scene.json')
                return
            except PermissionError:
                time.sleep(0.02)
        raise RuntimeError('could not switch the fake camera')

    def close(self):
        self.child.terminate()
        self.child.wait(10)
        shutil.rmtree(self.state, ignore_errors=True)


class FakeVLLM:
    """The den simulation's fake vLLM (test_ask.py --fake-vllm): root fake-vllm, replies by the body's sha256."""

    def __init__(self):
        self.state = scratch('den-live-fake-vllm-')
        self.child, self.port = started([sys.executable, '-B', '-X', 'utf8', str(TEST_ASK), '--fake-vllm', str(self.state)], self.state)

    def set(self, name, value):
        (self.state / name).write_text(json.dumps(value), encoding='utf-8')

    def asked(self):
        log = self.state / 'requests.jsonl'
        return [json.loads(line)['body_sha256'] for line in log.read_text(encoding='utf-8').splitlines()] if log.is_file() else []

    def close(self):
        self.child.terminate()
        self.child.wait(10)
        shutil.rmtree(self.state, ignore_errors=True)


class LocalTransport:
    """test_ask.py's stand-in for ask_la.SSH: each remote command ask_la.py sends is matched exactly and done here,
    in a scratch folder for /dev/shm, and ask_one.py runs as a local process against the fake vLLM."""

    def __init__(self, port, remote=sc.REMOTE, fail_remove_after_ask=False):
        self.shm, self.port, self.remote = scratch('den-live-shm-') / 'shm', port, remote
        self.fail_remove_after_ask, self.asked = fail_remove_after_ask, False   # the link dropping after the replies came back

    def local(self, remote):
        assert remote.startswith(self.remote), remote
        return self.shm / remote[len(self.remote):].lstrip('/')

    def shell(self, text, capture=False, timeout=None):
        r = re.escape(self.remote)
        done = lambda code, out=None: subprocess.CompletedProcess(text, code, stdout=out, stderr='')
        if re.fullmatch(r'rm -rf %s && umask 077 && mkdir %s' % (r, r), text):
            shutil.rmtree(self.shm, ignore_errors=True)
            self.shm.mkdir(parents=True)
            return done(0)
        if re.fullmatch(r'umask 077 && mkdir %s/bodies' % r, text):
            (self.shm / 'bodies').mkdir()
            return done(0)
        if re.fullmatch(r'test -d %s/replies' % r, text):
            return done(0 if (self.shm / 'replies').is_dir() else 1)
        if re.fullmatch(r'rm -rf %s' % r, text):
            if self.fail_remove_after_ask and self.asked:
                return done(255)
            shutil.rmtree(self.shm, ignore_errors=True)
            return done(0)
        match = re.fullmatch(r'timeout (\d+)m python3 -B %s/ask_one\.py( --check)?' % r, text)
        if match:
            self.asked = self.asked or not match.group(2)
            ran = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(self.shm / 'ask_one.py'), *(['--check'] if match.group(2) else []), '--fake-vllm', str(self.port)],
                                 cwd=str(self.shm), capture_output=True, encoding='ascii', errors='replace', timeout=int(match.group(1)) * 60,
                                 env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
            return done(ran.returncode, ran.stdout if capture else None)
        return done(127)

    def put(self, paths, remote, timeout=None):
        target = self.local(remote)
        if not target.is_dir():
            return subprocess.CompletedProcess('put', 1)
        for path in paths:
            shutil.copyfile(path, target / Path(path).name)
        return subprocess.CompletedProcess('put', 0)

    def get(self, remote, local, timeout=None):
        source = self.local(remote)
        if not source.is_dir():
            return subprocess.CompletedProcess('get', 1)
        shutil.copytree(source, local)
        return subprocess.CompletedProcess('get', 0)


# ---- pure parts ----------------------------------------------------------------------------------------------

def counts(frames, person=None, face_shoulders=None, torso_depth=None):
    person = frames if person is None else person
    return {'frames': frames, 'person': person, 'face_shoulders': person if face_shoulders is None else face_shoulders,
            'torso_depth': person if torso_depth is None else torso_depth, 'hips': 0, 'wrists': 0, 'two_or_more': 0}


LOOKS = [{'pose': 'reading', 'body_sha256': 'r%d' % k} for k in range(3)] + [{'pose': 'phone', 'body_sha256': 'p%d' % k} for k in range(3)]


def answers(reading, phone=(PHONE,) * 3):
    out = {'r%d' % k: (facts, None) for k, facts in enumerate(reading)}
    out.update({'p%d' % k: (facts, None) for k, facts in enumerate(phone)})
    return out


class Verdict(unittest.TestCase):
    good = {'reading': counts(100), 'phone': counts(100), 'empty': counts(100, person=0)}

    def word(self, analysis=None, said=None, why=None):
        return sc.verdict(analysis or self.good, LOOKS, said if said is not None else answers((READING,) * 3), why)[0]

    def test_sofa_seen(self):
        self.assertEqual(self.word(), 'SOFA SEEN')

    def test_two_of_three_reads_is_enough_and_one_is_not(self):
        self.assertEqual(self.word(said=answers((READING, READING, UNCLEAR))), 'SOFA SEEN')
        self.assertEqual(self.word(said=answers((READING, UNCLEAR, UNCLEAR))), 'PERSON ONLY')

    def test_reading_needs_one_person(self):
        two = {**READING, 'people_visible': 'multiple'}   # qwen_scene's words: none, one, multiple, unclear
        self.assertEqual(self.word(said=answers((two,) * 3)), 'PERSON ONLY')

    def test_settled_answers_win_over_a_late_stop(self):
        """All three reading requests answered not-reading, then the ask stopped on a phone request: Qwen's part is
        settled, so PERSON ONLY, not DETECTOR ONLY."""
        three_unclear = {'r%d' % k: (UNCLEAR, None) for k in range(3)}
        self.assertEqual(self.word(said=three_unclear, why='vLLM refused a request'), 'PERSON ONLY')
        one_missing = {'r0': (UNCLEAR, None), 'r1': (READING, None)}   # r2 could still make it 2 of 3
        self.assertEqual(self.word(said=one_missing, why='vLLM refused a request'), 'DETECTOR ONLY')

    def test_too_few_requests_built(self):
        looks = [{'pose': 'reading', 'body_sha256': 'r0'}, {'pose': 'reading', 'body_sha256': None, 'not_asked': 'qwen_live.spread: frames_insufficient'},
                 {'pose': 'reading', 'body_sha256': None, 'not_asked': 'qwen_live.spread: frames_insufficient'}]
        word, lines, _said, _reads = sc.verdict(self.good, looks, {'r0': (UNCLEAR, None)}, None)
        self.assertEqual(word, 'DETECTOR ONLY')
        self.assertIn('Only 1 of 3', lines[0])
        self.assertIn('frames_insufficient', lines[0])

    def test_a_window_with_no_frame(self):
        self.assertEqual(self.word({**self.good, 'empty': counts(0)}), 'CHECK AGAIN')
        self.assertEqual(self.word({**self.good, 'reading': counts(0)}), 'CHECK AGAIN')

    def test_invalid_replies_do_not_read(self):
        self.assertEqual(self.word(said=answers((None,) * 3)), 'PERSON ONLY')

    def test_the_eighty_percent_lines(self):
        edge = {**self.good, 'reading': counts(100, face_shoulders=80, torso_depth=80)}
        self.assertEqual(self.word(edge), 'SOFA SEEN')
        for short in ({'face_shoulders': 79}, {'torso_depth': 79}):
            self.assertEqual(self.word({**self.good, 'reading': {**counts(100), **short}}), 'NOT SEEN')

    def test_a_person_in_the_empty_window(self):
        self.assertEqual(self.word({**self.good, 'empty': counts(100, person=3)}), 'CHECK AGAIN')
        self.assertEqual(self.word({**self.good, 'empty': counts(100, person=2)}), 'SOFA SEEN')   # the control: 98% exactly

    def test_qwen_not_asked(self):
        self.assertEqual(self.word(said={}, why='vLLM on LA is not answering'), 'DETECTOR ONLY')
        self.assertEqual(self.word(said={}), 'DETECTOR ONLY')
        some = {'r0': (UNCLEAR, None)}   # one unclear answer, then the link dropped: not enough to say either way
        self.assertEqual(self.word(said=some, why='the ssh link dropped'), 'DETECTOR ONLY')
        self.assertEqual(self.word(said=some), 'DETECTOR ONLY')   # two built requests got no reply, which could still change it
        settled = {'r%d' % k: (UNCLEAR, None) for k in range(3)}
        self.assertEqual(self.word(said=settled), 'PERSON ONLY')   # the control: every reading request answered
        two = {'r0': (READING, None), 'r1': (READING, None)}   # two reads are enough, even if the ask stopped after them
        self.assertEqual(self.word(said=two, why='the ssh link dropped'), 'SOFA SEEN')

    def test_a_phone_read_as_reading_is_noted(self):
        lines = sc.verdict(self.good, LOOKS, answers((READING,) * 3, (READING, PHONE, PHONE)), None)[1]
        self.assertTrue(any('phone request reading' in line for line in lines))
        lines = sc.verdict(self.good, LOOKS, answers((READING,) * 3), None)[1]
        self.assertFalse(any('phone request reading' in line for line in lines))


class FrameFacts(unittest.TestCase):
    def row(self, people, capture=10.0):
        return {'frame': {'capture_ts': capture}, 'detections': people}

    def person(self, score=0.9, low=(), keypoints=True):
        points = [[100.0 + j, 50.0 + j, 0.2 if j in low else 0.9] for j in range(17)]
        return {'class': 'person', 'score': score, 'keypoints': points if keypoints else None}

    def facts(self, row, t_depth=10.0, measured=True):
        lifted = lambda *_: {'status': 'measured' if measured else 'unknown'}
        return sc.frame_facts(row, {'t_depth': t_depth}, None, None, lifted)

    def test_a_whole_person(self):
        got = self.facts(self.row([self.person()]))
        self.assertEqual(got, {'person': True, 'people': 1, 'face': True, 'shoulders': True, 'hips': True, 'wrists': True, 'torso_depth': True})

    def test_low_score_box_is_no_person(self):
        self.assertFalse(self.facts(self.row([self.person(score=0.49)]))['person'])
        self.assertTrue(self.facts(self.row([self.person(score=0.5)]))['person'])

    def test_joint_scores_under_point_four_are_unsupported(self):
        got = self.facts(self.row([self.person(low=(5,))]))
        self.assertFalse(got['shoulders'])
        got = self.facts(self.row([self.person(low=(0, 1, 2, 3, 4))]))
        self.assertFalse(got['face'])

    def test_torso_depth_needs_the_same_stereo_frame_and_two_joints(self):
        self.assertFalse(self.facts(self.row([self.person()]), t_depth=10.07)['torso_depth'])
        self.assertTrue(self.facts(self.row([self.person()]), t_depth=10.06)['torso_depth'])
        self.assertFalse(self.facts(self.row([self.person()]), measured=False)['torso_depth'])
        self.assertFalse(self.facts(self.row([self.person(low=(5, 6, 11))]))['torso_depth'])   # one torso joint left

    def test_no_keypoints(self):
        got = self.facts(self.row([self.person(keypoints=False)]))
        self.assertTrue(got['person'])
        self.assertFalse(got['face'] or got['torso_depth'])

    def test_people_counts_two(self):
        self.assertEqual(self.facts(self.row([self.person(), self.person(score=0.6)]))['people'], 2)


class Pools(unittest.TestCase):
    def test_three_different_pools_ending_earlier(self):
        frames = [(t * 0.25, 'f%d' % t, t, 's') for t in range(100)]   # 25 s
        got = sc.pools(frames)
        self.assertEqual(len(got), 3)
        cutoffs = [cutoff for cutoff, _ in got]
        self.assertEqual(cutoffs, sorted(cutoffs, reverse=True))
        self.assertEqual(round(cutoffs[0] - cutoffs[1], 3), 6.0)
        for cutoff, pool in got:
            self.assertTrue(all(t <= cutoff for t, *_ in pool))
            self.assertGreaterEqual(cutoff - pool[0][0], 3.0)

    def test_short_window_keeps_three_seconds_in_each(self):
        frames = [(t * 0.25, 'f%d' % t, t, 's') for t in range(29)]   # 7 s
        for cutoff, pool in sc.pools(frames):
            self.assertGreaterEqual(cutoff - pool[0][0], 3.0)

    def test_empty(self):
        self.assertEqual(sc.pools([]), [])


class Wiring(unittest.TestCase):
    def test_the_frame_size_guard(self):
        sc.check_size({'rgb_size': [640, 360]})
        with self.assertRaises(sc.Stop):
            sc.check_size({'rgb_size': [1920, 1080]})

    def test_qwen_live_list_is_chains(self):
        """chain.py installs a socket refusal on import, so it is read in a child process."""
        out = subprocess.run([sys.executable, '-B', '-c', 'import sys; sys.path.insert(0, %r); import chain; print(repr(tuple(chain.QWEN_LIVE)))' % str(HERE.parent / 'den-sim')],
                             capture_output=True, encoding='utf-8')
        self.assertEqual(out.stdout.strip(), repr(sc.QWEN_LIVE))

    def test_no_console_touches_nothing(self):
        root = scratch('den-live-root-')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = sc.main([], root=root, console=lambda: False, camera_url='http://127.0.0.1:9', speak=lambda text: self.fail('spoke'))
        self.assertEqual(code, 2)
        self.assertIn('refused, no console', out.getvalue())
        self.assertEqual(list(root.iterdir()), [])

    def test_a_missing_token_says_where_it_goes(self):
        root = scratch('den-live-root-')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = sc.main([], root=root, console=lambda: True, camera_url='http://127.0.0.1:9', token_file=str(root / 'no-token.txt'),
                           speak=lambda text: self.fail('spoke'))
        self.assertEqual(code, 2)
        self.assertIn("den-take's README, step 2", out.getvalue())
        self.assertFalse((root / 'private').exists())


# ---- end to end, on rendered takes ---------------------------------------------------------------------------

SET = Path(os.environ.get('DEN_SIM_SET') or Path.home() / 'den-sim' / 'prepared' / 'fbc7bda3115e')
SHORT = sc.Timing(countdown_s=0.0, settle_s=(1.5, 1.5, 1.5), window_s=7.0, poll_s=0.2)


def scene_takes(folder):
    labels = {scene['label']: folder / scene['take'] for scene in json.loads((folder / 'display.json').read_text(encoding='utf-8'))['scenes']}
    return {'reading': labels['Reading on the couch'], 'phone': labels['Holding a phone'], 'empty': labels['Nobody there']}


def calibration(folder):
    return folder / json.loads((folder / 'set.json').read_text(encoding='utf-8'))['fit']['folder'] / 'camera-calibration.json'


@unittest.skipUnless(sc.VENV_PY.is_file() and (SET / 'display.json').is_file(), 'needs the detector venv and a prepared den-sim set')
class OnRenders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = scratch('den-live-root-')
        cls.takes = scene_takes(SET)
        cls.camera = FakeCamera(calibration(SET), cls.takes['reading'])
        cls.vllm = FakeVLLM()
        cls.token_file = cls.root / 'camera-token.txt'
        cls.token_file.write_text(cls.camera.token, encoding='ascii')
        tools = cls.root / 'tools'
        cls.trees = {key: sc.archive(tools, key) for key in ('observe', 'register', 'core')}
        sys.path.insert(0, str(cls.trees['observe'] / 'tools' / 'den-take'))
        import take
        cls.take = take
        cls.spoken = []
        cls.folder = sc.private_folder(cls.root, take, time.time())
        cls.stats = sc.grab(take, take.Camera(cls.camera.url, cls.camera.token), cls.folder, SHORT, cls.speak, time.sleep)
        sc.child([sys.executable, '-B', str(HERE / 'sofa_check.py'), '--rig', str(cls.folder), str(cls.trees['register'])], 120, 'rig')
        sc.child([str(sc.VENV_PY), '-B', str(HERE / 'sofa_check.py'), '--analyse', str(cls.folder), str(cls.trees['observe']), str(cls.root / 'state' / 'detector')], 1800, 'analyse')
        sc.child([sys.executable, '-B', str(HERE / 'sofa_check.py'), '--bodies', str(cls.folder), str(cls.trees['core'])], 300, 'bodies')
        cls.analysis = json.loads((cls.folder / 'analysis.json').read_text(encoding='utf-8'))
        cls.looks = json.loads((cls.folder / 'looks.json').read_text(encoding='utf-8'))['looks']

    @classmethod
    def speak(cls, text):
        cls.spoken.append(text)
        for pose, cue in sc.CUES.items():
            if text == cue:
                cls.camera.show(cls.takes[pose])

    @classmethod
    def tearDownClass(cls):
        cls.camera.close()
        cls.vllm.close()

    def ask_with(self, by_pose, down=False, transport=None, keep_state=False):
        """A fresh ask folder holding the same owed bodies, the fake vLLM answering each pose's bodies as given."""
        folder = scratch('den-live-ask-')
        shutil.copytree(self.folder / 'ask' / 'owed', folder / 'ask' / 'owed')
        (folder / 'ask' / 'replies').mkdir()
        self.vllm.set('replies.json', {look['body_sha256']: {'facts': by_pose[look['pose']]} for look in self.looks if look['body_sha256']})
        self.vllm.set('down.json', down)
        if not keep_state:
            (self.root / sc.LA_STATE).unlink(missing_ok=True)
        before = len(self.vllm.asked())
        with contextlib.redirect_stdout(io.StringIO()):
            answers, why, record = sc.ask(folder, self.root, transport or LocalTransport(self.vllm.port))
        return answers, why, record, self.vllm.asked()[before:]

    def test_replies_count_when_la_drops_and_the_next_paste_clears_it(self):
        """The link drops at the last removal, after the replies came back: they count, the record says the folder
        (this check's stills) may be on LA, and the next paste copies back and removes it before anything else."""
        transport = LocalTransport(self.vllm.port, fail_remove_after_ask=True)
        answers, why, record, sent = self.ask_with({'reading': READING, 'phone': PHONE}, transport=transport)
        self.assertEqual(len(sent), 6)
        self.assertIn('removing the LA folder failed', why)
        self.assertEqual(len(answers), 6)
        self.assertEqual(sc.verdict(self.analysis, self.looks, answers, why)[0], 'SOFA SEEN')
        self.assertTrue(record['la_folder'].startswith('left'))
        self.assertTrue(sc.left_on_la(self.root))
        self.assertTrue(transport.shm.exists())   # the stand-in for LA still holds the folder
        transport.fail_remove_after_ask = False
        answers, why, record, sent = self.ask_with({'reading': READING, 'phone': PHONE}, transport=transport, keep_state=True)
        self.assertIsNone(why)
        self.assertEqual(record['la_folder'], 'removed')
        self.assertFalse(transport.shm.exists())
        self.assertFalse(sc.left_on_la(self.root))

    def test_grab_keeps_only_frames_that_arrived_in_a_window(self):
        """A slow camera whose replies straddle each window's start and end: every frame kept is in a window."""
        rows = [r for r in csv.DictReader(io.StringIO((self.takes['reading'] / 'frames.csv').read_text(encoding='utf-8')))]

        class Slow:
            host, port, served = '127.0.0.1', 0, 0

            def get(slow, path, timeout=5.0):
                if path == '/calibration':
                    return 200, calibration(SET).read_bytes()
                time.sleep(0.12)
                slow.served += 1
                return 200, (self.takes['reading'] / rows[slow.served % len(rows)]['file']).read_bytes()
        folder = sc.private_folder(scratch('den-live-grab-'), self.take, time.time())
        sc.grab(self.take, Slow(), folder, sc.Timing(0.0, (0.25, 0.25, 0.25), 1.0, 0.05), lambda text: None, time.sleep)
        windows = json.loads((folder / 'windows.json').read_text(encoding='utf-8'))
        kept = {p.name for p in (folder / 'frames').iterdir()}
        self.assertTrue(kept)
        self.assertEqual(kept, {name for pose in sc.POSES for name in windows[pose]['files']})

    def test_cues_in_order(self):
        self.assertEqual(self.spoken[1:4], [sc.CUES[pose] for pose in sc.POSES])
        self.assertEqual(self.stats['sessions'], 3)   # each render take has its own session: counted, never a stop

    def test_the_counts_on_renders(self):
        reading, phone, empty = (self.analysis[pose] for pose in sc.POSES)
        for window in (reading, phone):
            self.assertGreaterEqual(window['frames'], 15)
            self.assertGreaterEqual(window['face_shoulders'], 0.8 * window['frames'])
            self.assertGreaterEqual(window['torso_depth'], 0.8 * window['frames'])
        self.assertGreaterEqual(empty['frames'], 15)
        self.assertEqual(empty['person'], 0)

    def test_frames_kept_are_only_the_windows(self):
        kept = {p.name for p in (self.folder / 'frames').iterdir()}
        windows = json.loads((self.folder / 'windows.json').read_text(encoding='utf-8'))
        self.assertEqual(kept, {name for pose in sc.POSES for name in windows[pose]['files']})

    def test_three_bodies_a_pose(self):
        built = [look for look in self.looks if look['body_sha256']]
        self.assertEqual([look['pose'] for look in built], ['reading'] * 3 + ['phone'] * 3)
        self.assertEqual(len({look['body_sha256'] for look in built}), 6)
        for look in built:
            self.assertGreaterEqual(len(look['stills']), 2)

    def test_sofa_seen_and_its_control(self):
        answers, why, _record, sent = self.ask_with({'reading': READING, 'phone': PHONE})
        self.assertIsNone(why)
        self.assertEqual(sorted(sent), sorted(look['body_sha256'] for look in self.looks if look['body_sha256']))
        self.assertEqual(sc.verdict(self.analysis, self.looks, answers, why)[0], 'SOFA SEEN')
        answers, why, _record, _sent = self.ask_with({'reading': UNCLEAR, 'phone': PHONE})
        self.assertEqual(sc.verdict(self.analysis, self.looks, answers, why)[0], 'PERSON ONLY')

    def test_vllm_down_sends_no_image(self):
        answers, why, record, sent = self.ask_with({'reading': READING, 'phone': PHONE}, down=True)
        self.assertEqual(sent, [])
        self.assertEqual(answers, {})
        self.assertEqual(record['stopped_at'], 'preflight')
        self.assertEqual(sc.verdict(self.analysis, self.looks, answers, why)[0], 'DETECTOR ONLY')

    def test_the_paste_end_to_end(self):
        """main() as the owner's paste runs it, through every step, on the renders: the printed block and verdict.json."""
        self.vllm.set('down.json', False)
        self.vllm.set('replies.json', {'*': {'facts': READING}})   # every body reads as reading, the phone ones too
        root = scratch('den-live-paste-')
        shutil.copytree(self.root / 'tools', root / 'tools')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = sc.main([], root=root, camera_url=self.camera.url, token_file=str(self.token_file), transport=LocalTransport(self.vllm.port),
                           timing=SHORT, speak=self.speak, console=lambda: True)
        text = out.getvalue()
        self.assertEqual(code, 0, text)
        self.assertIn('Verdict:  SOFA SEEN.', text)
        self.assertIn('Qwen called a phone request reading', text)
        self.assertTrue(text.isascii())
        block = text[text.index('Sofa view check'):]   # ask_la.py's own preflight line before it prints served name, root and GPU as JSON
        for word in ('xyz', 'keypoints', 'box_xyxy', '{', '['):
            self.assertNotIn(word, block)
        checks = list((root / 'private').glob('sofa-check-*'))
        self.assertEqual(len(checks), 1)
        record = json.loads((checks[0] / 'verdict.json').read_text(encoding='utf-8'))
        self.assertEqual(record['verdict'], 'SOFA SEEN')
        self.assertEqual(record['qwen_reading_reads'], 3)


if __name__ == '__main__':
    unittest.main()
