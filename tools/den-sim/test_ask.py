"""Tests for the ask (plan step 4, "Done means"): test 10 whole, test 19's half that no test can start ssh or scp,
and on a prepared set test 2's delete-the-truth control, test 3, test 5's body-hash half and test 8's parts.
Stdlib unittest. Each check has a control that must make it fail.

    python -B -X utf8 tools\\den-sim\\test_ask.py
    python -B -X utf8 tools\\den-sim\\test_ask.py --fill HOME --set SET --facts reading=TAKE,phone=TAKE [--stage owed|paste|both]

Run it with DEN_SIM_HOME, LOCALAPPDATA, TEMP and TMP in scratch, and DEN_SIM_SET at a prepared set for the parts
that need one; every folder a test writes is a new one under TEMP. No test can start ssh or scp: the audit hook
below, installed before anything else, refuses either executable, and its control proves it. ask_la.py's visits run
through LocalTransport: a scratch folder stands in for /dev/shm, and ask_one.py runs as a local process against a
fake vLLM on 127.0.0.1 (this file with --fake-vllm), which serves root fake-vllm and writes only under its own
temporary folder. --fill is the Node tests' way to the same fake paste: prepare.py --owed into HOME, then ask_la.py
through the local transport, with the facts named per take; it prints one JSON line.
"""
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True


def no_ssh(event, args):
    """No process this test starts may be ssh or scp, by any spelling CreateProcess or a shell would run."""
    if event == 'subprocess.Popen':
        executable, argv = args[0], args[1]
        first = argv[0] if isinstance(argv, (list, tuple)) and argv else (str(argv).split() or [''])[0]
        for name in (executable, first):
            if name and Path(str(name).strip('"')).stem.lower() in ('ssh', 'scp'):
                raise PermissionError('a test may not start %s' % Path(str(name)).stem)
    elif event in ('os.system', 'os.spawn', 'os.exec', 'os.posix_spawn', 'os.startfile') and re.search(r'\b(ssh|scp)\b', ' '.join(map(str, args)).lower()):
        raise PermissionError('a test may not start ssh or scp')


sys.addaudithook(no_ssh)

HERE = Path(__file__).resolve().parent
SERVED, FAKE_ROOT = 'qwen3-vl-30b', 'fake-vllm'
READING = {'people_visible': 'one', 'subject_posture': 'sitting', 'held_object': 'book', 'book_state': 'open', 'gaze_target': 'book', 'typing_visible': 'no',
           'screen_facing_subject': 'not_visible', 'activity_hypothesis': 'reading', 'visible_basis': ['person_visible', 'book_in_hands'], 'change_vs_prior': 'no_prior'}
PHONE = {**READING, 'held_object': 'phone', 'book_state': 'not_visible', 'gaze_target': 'phone', 'activity_hypothesis': 'phone_use', 'visible_basis': ['person_visible', 'phone_in_hands']}
UNCLEAR = {**READING, 'held_object': 'none', 'book_state': 'not_visible', 'gaze_target': 'unclear', 'activity_hypothesis': 'unclear', 'visible_basis': ['person_visible']}
FACTS = {'reading': READING, 'phone': PHONE, 'unclear': UNCLEAR}


# ---- the fake vLLM: its own process, loopback only ------------------------------------------------------

def serve_fake(state):
    """/v1/models with root fake-vllm, /fake/gpu, and /v1/chat/completions answered from state/replies.json by the
    body's sha256 ({"facts": ...}, {"content": ...} or {"status": n}; "*" for any other). Writes only under state."""
    state = Path(state)

    def loopback(event, args):
        if event in ('socket.bind', 'socket.connect') and not (type(args[1]) is tuple and args[1][:1] == ('127.0.0.1',)):
            raise PermissionError('the fake vLLM is loopback only')
        if event in ('socket.getaddrinfo',) and args[0] not in ('127.0.0.1', b'127.0.0.1'):
            raise PermissionError('the fake vLLM looks up no name')
        if event in ('socket.gethostbyname', 'socket.gethostbyaddr', 'socket.getnameinfo'):
            raise PermissionError('the fake vLLM looks up no name')
    sys.addaudithook(loopback)
    import socketserver
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Server(ThreadingHTTPServer):
        def server_bind(self):   # http.server's own looks the host name up
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = '127.0.0.1', self.server_address[1]

    def read(name, default):
        try:
            return json.loads((state / name).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return default

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def answer(self, status, value):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if read('down.json', False):
                return self.answer(503, {})
            if self.path == '/v1/models':
                return self.answer(200, {'object': 'list', 'data': [{'id': read('served.json', SERVED), 'object': 'model', 'root': FAKE_ROOT}]})
            if self.path == '/fake/gpu':
                return self.answer(200, read('gpu.json', {'temperature_c': 40, 'free_mib': 30000}))
            return self.answer(404, {})

        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            name = hashlib.sha256(body).hexdigest()
            with open(state / 'requests.jsonl', 'a', encoding='utf-8') as log:
                log.write(json.dumps({'body_sha256': name}) + '\n')
            canned = read('replies.json', {})
            reply = canned.get(name, canned.get('*', {'facts': UNCLEAR}))
            if 'status' in reply:
                return self.answer(reply['status'], {})
            content = reply['content'] if 'content' in reply else json.dumps(reply['facts'])
            return self.answer(200, {'id': 'chatcmpl-fake', 'object': 'chat.completion', 'model': SERVED, 'usage': {},
                                     'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}]})

    server = Server(('127.0.0.1', 0), Handler)
    (state / 'port.part').write_text(str(server.server_address[1]), encoding='ascii')
    os.replace(state / 'port.part', state / 'port')
    server.serve_forever()


if __name__ == '__main__' and sys.argv[1:2] == ['--fake-vllm']:   # before chain.py's socket refusal is imported below
    sys.exit(serve_fake(sys.argv[2]))


class FakeVLLM:
    def __init__(self):
        self.state = Path(tempfile.mkdtemp(prefix='den-sim-fake-vllm-'))
        self.child = subprocess.Popen([sys.executable, '-B', '-X', 'utf8', __file__, '--fake-vllm', str(self.state)])
        for _ in range(200):
            if (self.state / 'port').is_file():
                self.port = int((self.state / 'port').read_text(encoding='ascii'))
                return
            time.sleep(0.05)
        raise RuntimeError('the fake vLLM did not start')

    def set(self, name, value):
        (self.state / name).write_text(json.dumps(value), encoding='utf-8')

    def asked(self):
        log = self.state / 'requests.jsonl'
        return [json.loads(line)['body_sha256'] for line in log.read_text(encoding='utf-8').splitlines()] if log.is_file() else []

    def close(self):
        self.child.terminate()
        self.child.wait(10)
        shutil.rmtree(self.state, ignore_errors=True)


# ---- the local transport: a scratch folder for /dev/shm, local processes for ssh ---------------------------

class LocalTransport:
    """Stands in for ask_la.SSH. It never calls ssh or scp: each remote command ask_la.py sends is matched exactly and
    done here, and ask_one.py runs as a local process from the folder, against the fake vLLM."""

    def __init__(self, shm, port, remote):
        self.shm, self.port, self.remote, self.calls, self.listings = Path(shm), port, remote, [], []

    def local(self, remote):
        assert remote.startswith(self.remote), remote
        return self.shm / remote[len(self.remote):].lstrip('/')

    def shell(self, text, capture=False, timeout=None):
        self.calls.append(('shell', text))
        r = re.escape(self.remote)
        done = lambda code, out=None: subprocess.CompletedProcess(text, code, stdout=out, stderr='')
        if re.fullmatch(r'rm -rf %s && umask 077 && mkdir %s' % (r, r), text):
            shutil.rmtree(self.shm, ignore_errors=True)
            self.shm.mkdir(parents=True)
            return done(0)
        if re.fullmatch(r'umask 077 && mkdir %s/bodies' % r, text):
            try:
                (self.shm / 'bodies').mkdir()
                return done(0)
            except OSError:
                return done(1)
        if re.fullmatch(r'test -d %s/replies' % r, text):
            return done(0 if (self.shm / 'replies').is_dir() else 1)
        if re.fullmatch(r'rm -rf %s' % r, text):
            shutil.rmtree(self.shm, ignore_errors=True)
            return done(0)
        match = re.fullmatch(r'timeout (\d+)m python3 -B %s/ask_one\.py( --check)?' % r, text)
        if match:
            self.listings.append(sorted(path.relative_to(self.shm).as_posix() for path in self.shm.rglob('*')))
            ran = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(self.shm / 'ask_one.py'), *(['--check'] if match.group(2) else []), '--fake-vllm', str(self.port)],
                                 cwd=str(self.shm), capture_output=True, encoding='ascii', errors='replace', timeout=int(match.group(1)) * 60,
                                 env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
            self.last_output = ran.stdout
            return done(ran.returncode, ran.stdout if capture else None)
        return done(127)

    def put(self, paths, remote):
        self.calls.append(('put', [Path(p).name for p in paths], remote))
        target = self.local(remote)
        if not target.is_dir():
            return subprocess.CompletedProcess('put', 1)
        for path in paths:
            shutil.copyfile(path, target / Path(path).name)
        return subprocess.CompletedProcess('put', 0)

    def get(self, remote, local):
        self.calls.append(('get', remote))
        source = self.local(remote)
        if not source.is_dir():
            return subprocess.CompletedProcess('get', 1)
        shutil.copytree(source, local)
        return subprocess.CompletedProcess('get', 0)


class Recording:
    """A transport that only writes down what it was asked: for the paths that must ask nothing."""

    def __init__(self):
        self.calls = []

    def shell(self, text, capture=False, timeout=None):
        self.calls.append(text)
        return subprocess.CompletedProcess(text, 1)

    put = get = lambda self, *args: self.calls.append(args) or subprocess.CompletedProcess('x', 1)


def owed_home(home, bodies):
    """A data root whose owed folder holds these bodies, as prepare.py --owed files them."""
    owed = Path(home) / 'prepared' / 'owed'
    owed.mkdir(parents=True, exist_ok=True)
    names = []
    for body in bodies:
        name = hashlib.sha256(body).hexdigest()
        (owed / (name + '.body')).write_bytes(body)
        names.append(name)
    (owed / 'owed.json').write_text(json.dumps({'schema': 'den-sim-owed-1', 'set': 'aaaaaaaaaaaa', 'bodies': sorted(names)}), encoding='utf-8')
    return names


def paste(home, fake, console=True):
    """ask_la.py's main, as the owner's paste runs it, through the local transport."""
    import ask_la
    transport = LocalTransport(Path(tempfile.mkdtemp(prefix='den-sim-shm-')) / 'shm', fake.port, ask_la.REMOTE)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = ask_la.main(root=home, transport=transport, console=lambda: console)
    last = Path(home) / 'prepared' / 'last-ask.json'
    return code, out.getvalue(), json.loads(last.read_text(encoding='utf-8')) if last.is_file() else None, transport


def fill(home, set_dir, facts_for_take, stage='both'):
    """prepare.py --owed into home, then a fake paste whose replies say facts_for_take's words for each take's bodies.
    The take-to-body map stays in home/test-fill.json, outside prepared\\."""
    home, mapping = Path(home), Path(home) / 'test-fill.json'
    if stage in ('owed', 'both'):
        done = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(HERE / 'scene' / 'prepare.py'), '--owed', '--set', str(set_dir)],
                              env={**os.environ, 'DEN_SIM_HOME': str(home)}, capture_output=True, encoding='utf-8', timeout=900)
        if done.returncode:
            raise RuntimeError('prepare.py --owed: ' + (done.stderr or done.stdout).strip()[-300:])
        owed, take = {}, None
        for line in done.stdout.splitlines():
            if re.match(r'^  take [0-9a-f]{12}:', line):
                take = line.split()[1].rstrip(':')
                owed[take] = []
            elif re.fullmatch(r'    [0-9a-f]{64}', line):
                owed[take].append(line.strip())
        mapping.write_text(json.dumps(owed), encoding='utf-8')
    owed = json.loads(mapping.read_text(encoding='utf-8'))
    result = {'owed': owed}
    if stage in ('paste', 'both'):
        fake = FakeVLLM()
        try:
            fake.set('replies.json', {name: {'facts': FACTS[kind]} for take, kind in facts_for_take.items() for name in owed.get(take, [])})
            code, out, last, _ = paste(home, fake)
            result.update(code=code, answered=last['answered'], still_needed=last['still_needed'], root=last['root'], asked=len(fake.asked()))
        finally:
            fake.close()
    return result


# ---- the tests -----------------------------------------------------------------------------------------

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / 'scene'))
import chain  # noqa: E402  installs chain.py's socket refusal in this process too
import prepare  # noqa: E402  its chain_request, the request --owed gives chain.py

SET = Path(os.environ['DEN_SIM_SET']).resolve() if os.environ.get('DEN_SIM_SET') else None
NEEDS_SET = unittest.skipUnless(SET and (SET / 'set.json').is_file(), 'needs DEN_SIM_SET=<a prepared set>')
REFS = json.loads((HERE / 'refs.json').read_text(encoding='utf-8'))['tools']


def scratch(prefix):
    return Path(tempfile.mkdtemp(prefix=prefix))


class NoSSH(unittest.TestCase):
    def test_no_test_can_start_ssh_or_scp(self):
        for argv in (['ssh', '-V'], ['scp', '-V'], ['C:\\Windows\\System32\\OpenSSH\\ssh.exe', '-V'], 'ssh -V'):
            with self.assertRaises(PermissionError, msg=repr(argv)):
                subprocess.run(argv, capture_output=True)
        with self.assertRaises(PermissionError):
            os.system('ssh -V')
        self.assertEqual(subprocess.run([sys.executable, '-c', 'pass']).returncode, 0, 'control: any other process starts')


class Lifted(unittest.TestCase):
    """What ask_la.py lifts by git archive is held to refs.json's blobs, and the LA host lives only in la.py."""

    def test_lifted_files_hold_their_pins_and_ask_la_names_no_host(self):
        import ask_la
        folder = scratch('den-sim-lift-')
        try:
            tools = ask_la.lift(folder)
            self.assertEqual(sorted(tools), ['ask.py', 'la.py', 'qwen_scene.py'])
            core = chain.archive_trees(REFS, folder / 'tools')['core'] / chain.LAB / 'qwen_scene.py'
            self.assertEqual(tools['qwen_scene.py'].read_bytes(), core.read_bytes(), 'the qwen_scene.py bodies are built with is the blob the click uses')
            la = ask_la.load(tools['la.py'], 'den_sim_la_test')
            for name in ('ask_la.py', 'ask_one.py', 'chain.py', 'run.mjs', 'panel.js'):
                self.assertNotIn(la.LA, (HERE / name).read_text(encoding='utf-8'), name)
            calls = []
            ssh = ask_la.SSH(la, runner=lambda argv, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 0))
            ssh.shell('test -d x'), ssh.put([Path('a.body')], '/dev/shm/den-sim-ask/bodies'), ssh.get('/dev/shm/den-sim-ask/replies', Path('part'))
            self.assertEqual(calls, [la.SSH + ['test -d x'], la.SCP + ['a.body', '%s:/dev/shm/den-sim-ask/bodies/' % la.LA],
                                     la.SCP + ['-r', '%s:/dev/shm/den-sim-ask/replies' % la.LA, 'part']], 'the argument lists are la.py\'s')
            (tools['la.py']).write_text('LA = "elsewhere"\n', encoding='utf-8')
            with self.assertRaises(ask_la.Stop, msg='control: an edited lifted file is refused'):
                ask_la.lift(folder)
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class Paste(unittest.TestCase):
    """ask_la.py and ask_one.py, through the local transport against the fake vLLM."""

    @classmethod
    def setUpClass(cls):
        cls.fake = FakeVLLM()

    @classmethod
    def tearDownClass(cls):
        cls.fake.close()

    def setUp(self):
        self.home = scratch('den-sim-paste-')
        self.fake.set('replies.json', {})
        for name in ('down.json', 'served.json'):
            (self.fake.state / name).unlink(missing_ok=True)
        (self.fake.state / 'requests.jsonl').unlink(missing_ok=True)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_no_console_refuses_before_any_ssh(self):
        import ask_la
        owed_home(self.home, [b'{"a": 1}'])
        recording = Recording()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ask_la.main(root=self.home, transport=recording, console=lambda: False)
        self.assertEqual((code, recording.calls), (2, []))
        last = json.loads((self.home / 'prepared' / 'last-ask.json').read_text(encoding='utf-8'))
        self.assertEqual((last['stopped_at'], last['reason']), ('start', 'no console: run it yourself in PowerShell'))
        # The same as a process with no console at all: its stdin is not a terminal, under this file's ssh refusal.
        launcher = 'import runpy, sys; sys.path.insert(0, %r); import test_ask; sys.argv = [%r]; runpy.run_path(%r, run_name="__main__")' % (str(HERE), 'ask_la.py', str(HERE / 'ask_la.py'))
        done = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', launcher], stdin=subprocess.DEVNULL, capture_output=True, encoding='utf-8',
                              env={**os.environ, 'DEN_SIM_HOME': str(self.home)})
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn('refused, no console', done.stdout)
        self.assertNotIn('PermissionError', done.stderr, 'it never tried ssh')
        self.assertFalse((self.home / 'prepared' / 'tries.jsonl').exists(), 'a refused start is no paste')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ask_la.main(root=self.home, transport=Recording(), console=lambda: True), 2, 'control: with a console it goes on to the transport')

    def test_the_paste_files_six_field_replies_once_and_removes_the_folder(self):
        bodies = [json.dumps({'body': n}).encode() for n in range(3)]
        names = owed_home(self.home, bodies)
        self.fake.set('replies.json', {names[0]: {'facts': READING}, names[1]: {'content': 'this is not json'}, '*': {'facts': PHONE}})
        code, out, last, transport = paste(self.home, self.fake)
        self.assertEqual(code, 0, out)
        replies = self.home / 'prepared' / 'replies'
        self.assertEqual(sorted(path.stem for path in replies.glob('*.json')), sorted(names))
        for name in names:
            reply = json.loads((replies / (name + '.json')).read_text(encoding='utf-8'))
            self.assertEqual(set(reply), {'asked_unix', 'host', 'root', 'http_status', 'raw', 'elapsed_s'})
            self.assertEqual((reply['root'], reply['http_status']), (FAKE_ROOT, 200))
            self.assertEqual(last['filed'][name]['sha256'], hashlib.sha256((replies / (name + '.json')).read_bytes()).hexdigest(), 'last-ask.json vouches for each')
        self.assertEqual(sorted(self.fake.asked()), sorted(names), 'each body once: the invalid reply is kept as it came, not re-sent')
        self.assertEqual((last['answered'], last['needed'], last['still_needed'], last['root'], last['cap'], last['la_folder']), (3, 3, 0, FAKE_ROOT, 5, 'removed'))
        self.assertFalse(transport.shm.exists(), 'the folder standing in for /dev/shm is gone')
        # ask_one.py ran from a folder holding only the copied files: the tools on the check visit, and the bodies on the ask visit.
        self.assertEqual(transport.listings[0], ['ask.py', 'ask_one.py', 'qwen_scene.py'])
        self.assertEqual(transport.listings[1], ['ask.py', 'ask_one.py', 'bodies', *('bodies/%s.body' % name for name in sorted(names)), 'qwen_scene.py'])
        check = next(i for i, call in enumerate(transport.calls) if call[0] == 'shell' and call[1].endswith('--check'))
        first_body = next(i for i, call in enumerate(transport.calls) if call[0] == 'put' and any(n.endswith('.body') for n in call[1]))
        self.assertLess(check, first_body, 'the check visit comes before any body is copied')
        tries = [json.loads(line) for line in (self.home / 'prepared' / 'tries.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([(t['by'], t['kind'], t['body_sha256'], t['answered']) for t in tries], [('ask_la.py', 'asked', None, 3)], 'one line per paste')
        self.assertTrue(out.isascii())
        for text in ('this is not json', 'reading', '"raw"'):
            self.assertNotIn(text, out, 'no reply text is printed')
        code, out, again, transport = paste(self.home, self.fake)
        self.assertEqual((code, again['needed'], len(self.fake.asked())), (0, 0, 3), 'a second paste asks nothing')
        self.assertIn('nothing to ask', out)
        self.assertEqual(again['filed'], last['filed'], 'and keeps the digests the first paste took')

    def test_vllm_down_or_a_wrong_name_sends_no_image(self):
        names = owed_home(self.home, [b'{"x": 1}'])
        import ask_la
        for name, value, reason in (('down.json', True, 'vLLM on LA is not answering'), ('served.json', 'another-model', "LA's vLLM does not serve qwen3-vl-30b")):
            self.fake.set(name, value)
            code, out, last, transport = paste(self.home, self.fake)
            self.assertEqual((code, last['stopped_at'], last['reason']), (3, 'preflight', reason))
            self.assertFalse([call for call in transport.calls if call[0] == 'put' and any(n.endswith('.body') for n in call[1])], 'no body left this PC')
            self.assertEqual(self.fake.asked(), [])
            self.assertEqual(ask_la.VLLM_DOC in out, name == 'down.json')
            (self.fake.state / name).unlink()
        self.fake.set('gpu.json', {'temperature_c': 77, 'free_mib': 30000})
        code, out, last, _ = paste(self.home, self.fake)
        self.assertEqual(last['reason'], "LA's GPU is at 77 C or more")
        self.fake.set('gpu.json', {'temperature_c': 76, 'free_mib': 8192})
        code, out, last, _ = paste(self.home, self.fake)
        self.assertEqual((code, last['answered'], self.fake.asked()), (0, 1, names), 'control: just inside the gate, it asks')
        (self.fake.state / 'gpu.json').unlink()

    def test_two_misses_stop_and_a_miss_is_resent_once(self):
        names = owed_home(self.home, [json.dumps({'n': n}).encode() for n in range(3)])
        self.fake.set('replies.json', {'*': {'status': 503}})
        code, out, last, _ = paste(self.home, self.fake)
        self.assertEqual((code, last['stopped_at'], last['reason'], last['answered']), (4, 'asking', 'two misses in a row', 0))
        self.assertEqual(len(self.fake.asked()), 2, 'one body, sent twice, then the stop')
        self.assertEqual(len(set(self.fake.asked())), 1)
        self.assertIn('Paste the same line again later.', out)

    def test_a_changed_owed_body_stops_before_any_ssh(self):
        import ask_la
        names = owed_home(self.home, [b'{"y": 2}'])
        (self.home / 'prepared' / 'owed' / (names[0] + '.body')).write_bytes(b'changed')
        recording = Recording()
        with contextlib.redirect_stdout(io.StringIO()):
            ask_la.main(root=self.home, transport=recording, console=lambda: True)
        self.assertEqual(recording.calls, [])
        last = json.loads((self.home / 'prepared' / 'last-ask.json').read_text(encoding='utf-8'))
        self.assertIn('changed or is gone', last['reason'])

    def test_ask_one_reaches_loopback_only(self):
        import ask_la
        folder = scratch('den-sim-one-')
        try:
            tools = ask_la.lift(folder / 'lift')
            run = folder / 'run'
            run.mkdir()
            for path in (HERE / 'ask_one.py', tools['ask.py'], tools['qwen_scene.py']):
                shutil.copyfile(path, run / Path(path).name)
            probe = ("import sys; sys.argv = ['ask_one.py']; sys.path.insert(0, '.'); import ask_one, socket\n"
                     "for host in ('192.0.2.1', '127.0.0.1'):\n"
                     "    try:\n        socket.create_connection((host, %d), timeout=2).close(); print(host, 'connected')\n"
                     "    except PermissionError: print(host, 'refused')\n" % self.fake.port)
            done = subprocess.run([sys.executable, '-B', '-c', probe], cwd=str(run), capture_output=True, encoding='utf-8')
            self.assertEqual(done.stdout.split('\n')[:2], ['192.0.2.1 refused', '127.0.0.1 connected'], done.stderr)
        finally:
            shutil.rmtree(folder, ignore_errors=True)


def synthetic_take(folder, session='virtual-' + 'ab' * 16, frames=30, same=False):
    """A tiny take of invented NV12 frames, 0.2 s apart, with the files hop 11 reads."""
    import numpy as np
    (folder / 'frames').mkdir(parents=True)
    lines = ['n,file,rgb_seq,t_s,t_host_s']
    for i in range(frames):
        seq, name = 500 + i, 'frames/%06d-%d.npz' % (i + 1, 500 + i)
        np.savez(folder / name, rgb_nv12=np.random.default_rng(0 if same else seq).integers(16, 235, (540, 640), dtype=np.uint8))
        lines.append('%d,%s,%d,%.6f,%.3f' % (i + 1, name, seq, 0.2 * i, 1000.0 + 0.2 * i))
    (folder / 'frames.csv').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (folder / 'take.json').write_text(json.dumps({'health_before': {'body': {'session': session}}}), encoding='utf-8')
    (folder / 'run.json').write_text(json.dumps({'clock': {'offset_s': 5000.0}}), encoding='utf-8')


def routed(folder, requests):
    """requests.jsonl and bundles.jsonl: each (request id, frame indices or None for no bundle), sent just after its newest frame."""
    rows, bundles = [], []
    for request_id, indices in requests:
        newest = 6000.0 + 0.2 * (max(indices) if indices else 10)
        bundle_id = 'pb-%s' % request_id
        rows.append({'request_id': request_id, 'track_id': 'trk-1', 'sent_ts': newest + 0.1, 'bundle_id': bundle_id, 'association_safe': True})
        if indices is not None:   # 0.2 ms off each frame's own time: the join is never exact
            bundles.append({'bundle_id': bundle_id, 'frames': [{'camera_id': 'den-virtual', 'ts': 6000.0 + 0.2 * i + 0.0002} for i in indices]})
    for name, lines in (('requests.jsonl', rows), ('bundles.jsonl', bundles)):
        (folder / name).write_text(''.join(json.dumps(line) + '\n' for line in lines), encoding='utf-8')


class Walk(unittest.TestCase):
    """Test 10 on hop 11 and 12's walk, chain.py's step_ask, on invented frames: no prepared set needed."""

    @classmethod
    def setUpClass(cls):
        cls.root = scratch('den-sim-walk-')
        cls.core = chain.archive_trees(REFS, cls.root / 'tools')['core']

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        self.case = Path(tempfile.mkdtemp(dir=self.root))
        self.take, self.store = self.case / 'take', self.case / 'replies'
        self.store.mkdir()

    def ask(self, owed=None):
        work = self.case / ('work-%d' % len(list(self.case.glob('work-*'))))
        work.mkdir()
        digest = chain.sha256_file(self.take / 'frames.csv')
        return chain.step_ask(dict(core_tree=str(self.core), take=str(self.take), take_digest=digest, run_json=str(self.take / 'run.json'),
                                   requests=str(self.take / 'requests.jsonl'), bundles=str(self.take / 'bundles.jsonl'), replies=str(self.store),
                                   work=str(work), owed=owed)), work

    def reply(self, name, content=None, root='/models/real', facts=READING):
        raw = json.dumps({'id': 'x', 'object': 'chat.completion', 'model': SERVED, 'choices': [{'index': 0, 'finish_reason': 'stop',
                          'message': {'role': 'assistant', 'content': content if content is not None else json.dumps(facts)}}]})
        (self.store / (name + '.json')).write_text(json.dumps({'asked_unix': 1.8e9, 'host': 'h', 'root': root, 'http_status': 200, 'raw': raw, 'elapsed_s': 0.7}), encoding='utf-8')

    def test_a_lookup_miss_owes_the_exact_body_and_feeds_no_line(self):
        synthetic_take(self.take)
        routed(self.take, [('rq-00001', range(5, 21))])
        owed = self.case / 'owed'
        owed.mkdir()
        result, work = self.ask(owed=str(owed))
        name = result['asks'][0]['body_sha256']
        self.assertEqual((result['missing'], result['filed'], result['lines']), ([name], [name], 0))
        self.assertFalse((work / 'facts.jsonl').exists(), 'need gets no input')
        body = (owed / (name + '.body')).read_bytes()
        self.assertEqual(hashlib.sha256(body).hexdigest(), name)
        doc = json.loads(body)
        self.assertEqual((doc['model'], len([part for part in doc['messages'][1]['content'] if part['type'] == 'image_url'])), (SERVED, 3))
        self.assertIn('evening 20:00', doc['messages'][1]['content'][0]['text'])
        seqs = [still['rgb_seq'] for still in result['asks'][0]['stills']]
        self.assertEqual((seqs[0], seqs[2], seqs[1] in (512, 513)), (505, 520, True), 'newest, then nearest 1.5 s and 3 s before it: %s' % seqs)
        self.reply(name)
        again, work = self.ask()
        self.assertEqual((again['missing'], again['lines'], again['asks'][0]['body_sha256']), ([], 1, name), 'control: the reply on file is found by the same hash')

    def test_a_reply_becomes_a_real_fact_line_and_an_invalid_one_stays_invalid(self):
        synthetic_take(self.take)
        routed(self.take, [('rq-00001', range(5, 21)), ('rq-00002', range(10, 26))])
        first, _ = self.ask()
        a, b = (row['body_sha256'] for row in first['asks'])
        self.reply(a)
        self.reply(b, content='this is not json')
        result, work = self.ask()
        self.assertEqual([(row['status'], row['invalid_reason']) for row in result['asks']], [('valid', None), ('invalid', 'not_json')])
        lines = [json.loads(line) for line in (work / 'facts.jsonl').read_text(encoding='utf-8').splitlines()]
        line, digest = lines[0], chain.sha256_file(self.take / 'frames.csv')
        self.assertEqual((line['source_id'], line['request_id'], line['session_id'], line['engine_epoch']), ('den_sim_la_direct', a, digest[:32], 'den-sim-direct'))
        observation = line['observation']
        self.assertEqual((observation['text_scope'], observation['artifact_sha256'], observation['body_sha256'], observation['site_id'], observation['camera_id']),
                         ('reply_kept_render_only', None, a, 'den-sim', 'den-virtual'))
        self.assertEqual(observation['media_ids'], [still['jpeg_sha256'] for still in result['asks'][0]['stills']])
        self.assertEqual(observation['frame_ids'], [str(still['rgb_seq']) for still in result['asks'][0]['stills']])
        self.assertEqual(observation['capture_epoch'], 'virtual-' + 'ab' * 16)
        self.assertEqual(observation['facts']['activity_hypothesis'], 'reading')
        self.assertIsNone(lines[1]['observation']['facts'], 'the invalid reply stays invalid, and need reads unknown')
        text = (work / 'facts.jsonl').read_text(encoding='utf-8')
        for token in ('la-epoch', 'ring-', 'a' * 64, '1800000000'):
            self.assertNotIn(token, text, 'the real ask time and the live ids stay out')
        sys.path.insert(0, str(self.core / 'tools' / 'den-core'))
        import synthetic
        typed = synthetic.fact_line(0, 1800000000.0, 1800000001.25, facts=synthetic.TEMPLATES['reading'])
        self.assertTrue(all(token in typed for token in ('la-epoch', 'ring-', 'a' * 64)), "control: the typed builder's line holds all three")

    def test_refusals_a_repeated_still_a_non_virtual_take_a_repeated_id_and_a_bad_join(self):
        synthetic_take(self.take, same=True)
        routed(self.take, [('rq-00001', range(5, 21))])
        self.assertEqual(self.ask()[0]['refused'], 'two stills of one request are the same bytes, which is one look')
        shutil.rmtree(self.take)
        synthetic_take(self.take, session='oak-' + 'ab' * 16)
        routed(self.take, [('rq-00001', range(5, 21))])
        self.assertEqual(self.ask()[0]['refused'], "the take's session does not start virtual-, and only a render's reply text may be kept")
        shutil.rmtree(self.take)
        synthetic_take(self.take)
        routed(self.take, [('rq-00001', range(5, 21)), ('rq-00001', range(10, 26))])
        self.assertEqual(self.ask()[0]['refused'], 'request id rq-00001 repeats')
        routed(self.take, [('rq-00001', range(5, 21)), ('rq-00002', range(5, 21))])
        self.assertRegex(self.ask()[0]['refused'], r'^two requests have the same body')
        routed(self.take, [('rq-00001', range(5, 40))])   # frames past the take's end join no row
        self.assertEqual(self.ask()[0]['refused'], 'a bundle frame joins 0 frames.csv rows within 1 ms, not one')
        csv = (self.take / 'frames.csv').read_text(encoding='utf-8')
        (self.take / 'frames.csv').write_text(csv + '31,frames/000006-505.npz,999,6.0,1001.0005\n', encoding='utf-8')
        routed(self.take, [('rq-00001', range(5, 21))])
        self.assertEqual(self.ask()[0]['refused'], 'a bundle frame joins 2 frames.csv rows within 1 ms, not one')
        (self.take / 'frames.csv').write_text(csv, encoding='utf-8')
        self.assertIsNone(self.ask()[0]['refused'], 'control: one row within 1 ms of each frame joins')

    def test_no_bundle_and_a_thin_bundle_are_not_asked_and_the_walk_goes_on(self):
        synthetic_take(self.take)
        routed(self.take, [('rq-00001', None), ('rq-00002', [12]), ('rq-00003', range(5, 21))])
        result, work = self.ask()
        rows = [json.loads(line) for line in (work / 'asks.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([(row['request_id'], row['not_asked']) for row in rows],
                         [('rq-00001', 'the router wrote no bundle for this request'), ('rq-00002', 'qwen_live.spread: frames_insufficient'), ('rq-00003', None)])
        self.assertEqual(len(result['missing']), 1, 'only the request with stills is owed')
        self.assertIsNotNone(rows[2]['body_sha256'])

    def test_a_reply_that_is_not_the_six_fields_is_refused(self):
        synthetic_take(self.take)
        routed(self.take, [('rq-00001', range(5, 21))])
        name = self.ask()[0]['asks'][0]['body_sha256']
        (self.store / (name + '.json')).write_text(json.dumps({'raw': '{}', 'root': 'x'}), encoding='utf-8')
        self.assertRegex(self.ask()[0]['refused'], r'^the reply on file for body [0-9a-f]{12} is not the six fields')


def linked_copy(source, target):
    for path in source.rglob('*'):
        dest = target / path.relative_to(source)
        if path.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.link(path, dest)


def replace(path, data):
    """A file in a hard-linked copy is replaced, never written through."""
    path.unlink()
    path.write_bytes(data)


@NEEDS_SET
class OnTheSet(unittest.TestCase):
    """Tests 2, 3, 5 and 8 and test 10's controls, on clicks of the prepared set's takes with fake replies in a scratch store."""

    @classmethod
    def setUpClass(cls):
        cls.home = scratch('den-sim-onset-')
        display = json.loads((SET / 'display.json').read_text(encoding='utf-8'))['scenes']
        cls.reading, cls.phone = (next(d['take'] for d in display if d['label'] == label) for label in ('Reading on the couch', 'Holding a phone'))
        cls.filled = fill(cls.home, SET, {cls.reading: 'reading', cls.phone: 'phone'})
        cls.info = json.loads((SET / 'set.json').read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)

    def click(self, set_dir, take, store=None):
        """One click of chain.py on this take, in this process: (rows by hop, end line, work folder)."""
        work = Path(tempfile.mkdtemp(dir=self.home, prefix='click-'))
        request, _ = prepare.chain_request(set_dir, self.info, take, self.home / 'prepared', work, self.home / 'tools')
        request.update(owed=None, last_ask=str(self.home / 'prepared' / 'last-ask.json'), replies=str(store or self.home / 'prepared' / 'replies'))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                chain.chain(request)
            except chain.Stop as stop:
                print(json.dumps(dict(hop='stopped', stop=dict(hop=stop.hop, why=stop.why, answer=stop.answer))))
            except Exception as error:  # noqa: BLE001  as chain.py's main files it
                print(json.dumps(dict(hop='stopped', stop=dict(hop=chain.AT[0], why=type(error).__name__, answer=False))))
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        return {row['n']: row for row in lines if 'word' in row}, lines[-1], work / take

    def decided(self, set_dir, take, store=None):
        """What the older brain is told on a click: the adapter's label and person, and the files that fed it, byte for byte."""
        _, end, work = self.click(set_dir, take, store)
        files = {name: (work / name).read_bytes() for name in ('requests.jsonl', 'bundles.jsonl', 'asks.jsonl', 'facts.jsonl', 'den-core-log.jsonl') if (work / name).exists()}
        return dict(files, adapter=json.dumps(end.get('adapter'), sort_keys=True), stop=json.dumps(end.get('stop'), sort_keys=True))

    def test_the_fill_asked_reading_and_phone_only(self):
        owed = self.filled['owed']
        self.assertEqual(sorted(take for take, names in owed.items() if names), sorted([self.reading, self.phone]), 'standing, nobody and blank are asked nothing')
        self.assertEqual((self.filled['answered'], self.filled['still_needed'], self.filled['root']), (self.filled['asked'], 0, FAKE_ROOT))

    def test_10_and_8_a_fake_root_reads_scripted_and_the_same_input_gives_the_same_bytes(self):
        rows, end, work = self.click(SET, self.reading)
        self.assertEqual((rows[11]['word'], rows[12]['word'], rows[13]['word']), ('ADAPTER', 'SCRIPTED', 'ADAPTER'))
        self.assertEqual((end['hop'], end['adapter']['label'], end['reply']), ('done', 'reading', 'fake'))
        text = (work / 'facts.jsonl').read_text(encoding='utf-8')
        for token in ('la-epoch', 'ring-', 'a' * 64):
            self.assertNotIn(token, text)
        once, twice = self.decided(SET, self.reading), self.decided(SET, self.reading)
        self.assertEqual(once, twice, 'test 8: the same take twice, the same bytes')
        planted = dict(twice, **{'facts.jsonl': twice['facts.jsonl'].replace(b'"latency_s":', b'"read_at":%.3f,"latency_s":' % time.time(), 1)})
        self.assertNotEqual(once, planted, 'control: a clock read planted in a record shows')
        # Test 10's other control: one reply from another root in a copy of the store reads MISSING.
        store = self.home / 'two-roots'
        shutil.copytree(self.home / 'prepared' / 'replies', store, dirs_exist_ok=True)
        victim = store / (self.filled['owed'][self.reading][2] + '.json')
        reply = json.loads(victim.read_text(encoding='utf-8'))
        victim.write_text(json.dumps(dict(reply, root='/models/another')), encoding='utf-8')
        rows, end, _ = self.click(SET, self.reading, store=store)
        self.assertEqual((rows[12]['word'], end['stop']), ('MISSING', {'hop': 12, 'why': 'replies from two models', 'answer': False}))
        shutil.rmtree(store)

    def test_2_with_the_truth_and_the_display_file_gone_the_decisions_are_byte_identical(self):
        copy = self.home / 'no-truth' / SET.name
        linked_copy(SET, copy)
        shutil.rmtree(copy / 'truth')
        (copy / 'display.json').unlink()
        want = {}
        for take in (self.reading, self.phone):
            want[take] = self.decided(SET, take)
            self.assertEqual(self.decided(copy, take), want[take], take)
        # Control: a chain patched to open the truth folder fails the delete.
        original = chain.Click.own_step

        def opens_truth(click, name, request):
            for record in sorted((click.set / 'truth').glob('*/take.json')) or [click.set / 'truth' / 'none' / 'take.json']:
                json.loads(record.read_text(encoding='utf-8'))
            return original(click, name, request)
        chain.Click.own_step = opens_truth
        try:
            self.assertEqual(self.decided(SET, self.reading), want[self.reading], 'the patched chain decides alike while the truth is there')
            patched = self.decided(copy, self.reading)
        finally:
            chain.Click.own_step = original
        self.assertNotEqual(patched, want[self.reading], 'control: the patched chain fails the delete')

    def test_3_the_beam_follows_the_pixels_not_the_folder(self):
        copy = self.home / 'swapped' / SET.name
        linked_copy(SET, copy)
        for name in (self.reading, self.phone):
            os.rename(copy / name, copy / (name + '.x'))
        os.rename(copy / (self.reading + '.x'), copy / self.phone)
        os.rename(copy / (self.phone + '.x'), copy / self.reading)
        labels = lambda set_dir: {take: self.click(set_dir, take)[1]['adapter']['label'] for take in (self.reading, self.phone)}
        self.assertEqual(labels(SET), {self.reading: 'reading', self.phone: 'other'})
        self.assertEqual(labels(copy), {self.reading: 'other', self.phone: 'reading'}, 'phone\'s pixels under reading\'s id read phone, and the reverse')
        moved = copy / ('f' * 12)
        shutil.copytree(SET / self.reading, moved, copy_function=os.link)
        self.assertEqual(self.decided(copy, moved.name), self.decided(SET, self.reading), 'the same take under a new id: byte-identical')
        # Control: a chain with a table keyed by folder id follows the folder, and fails this test.
        original = chain.person_to_aim
        table = {self.reading: 'reading'}
        current = {}

        def by_folder(bundles, person):
            got, problem = original(bundles, person)
            return (dict(got, label=table.get(current['take'], 'other')) if got else got), problem
        chain.person_to_aim = by_folder
        try:
            followed = {}
            for take in (self.reading, self.phone):
                current['take'] = take
                followed[take] = self.click(copy, take)[1]['adapter']['label']
        finally:
            chain.person_to_aim = original
        self.assertNotEqual(followed, {self.reading: 'other', self.phone: 'reading'}, 'control: a folder-keyed table does not follow the pixels')

    def test_5_a_changed_still_with_frames_csv_and_the_rows_re_derived_misses_on_the_body_hash(self):
        import numpy as np
        rows, end, work = self.click(SET, self.reading)
        still = json.loads((work / 'asks.jsonl').read_text(encoding='utf-8').splitlines()[-1])['stills'][-1]['file']
        copy = self.home / 'changed' / SET.name
        linked_copy(SET, copy)
        take = copy / self.reading
        with np.load(take / still) as planes:
            arrays = {key: planes[key] for key in planes.files}
        arrays['rgb_nv12'] = arrays['rgb_nv12'].copy()
        arrays['rgb_nv12'][100:200, 200:400] = np.clip(arrays['rgb_nv12'][100:200, 200:400].astype(int) + 40, 0, 255).astype(np.uint8)
        buffer = io.BytesIO()
        np.savez(buffer, **arrays)
        replace(take / still, buffer.getvalue())
        csv = (take / 'frames.csv').read_text(encoding='utf-8').split('\n')
        csv = [','.join(fields[:7] + [hashlib.sha256(buffer.getvalue()).hexdigest()] + fields[8:]) if len(fields) > 7 and fields[1] == still else line
               for line, fields in ((line, line.split(',')) for line in csv)]
        replace(take / 'frames.csv', '\n'.join(csv).encode())
        digest = chain.sha256_file(take / 'frames.csv')
        record = json.loads((take / 'take.json').read_text(encoding='utf-8'))
        record['sha256']['frames.csv'] = digest
        replace(take / 'take.json', json.dumps(record, indent=1).encode())
        run = json.loads((take / 'detector-run.json').read_text(encoding='utf-8'))
        replace(take / 'detector-run.json', json.dumps(dict(run, take_digest=digest)).encode())
        receipt = json.loads((take / 'prepare-receipt.json').read_text(encoding='utf-8'))
        receipt['take_digest'] = digest
        receipt['hops']['6']['run']['sha256'] = chain.sha256_file(take / 'detector-run.json')
        for row in receipt['hops']['3']['wrote']:
            row['sha256'] = {'frames.csv': digest, 'take.json': chain.sha256_file(take / 'take.json')}[row['path'].rsplit('/', 1)[-1]]
        replace(take / 'prepare-receipt.json', json.dumps(receipt).encode())
        rows, end, _ = self.click(copy, self.reading)
        self.assertEqual([rows.get(n, end)['word'] if n in rows else end.get('stop') for n in (6, 7, 8, 9, 11)], ['ADAPTER', 'REAL', 'REAL', 'ADAPTER', 'ADAPTER'], 'the frame hash and the rows pass')
        self.assertEqual((rows[12]['word'], end['stop']['hop'], end['stop']['answer']), ('MISSING', 12, False))
        self.assertTrue(rows[12]['summary'].startswith('Qwen has not seen these exact frames: 1 of 5 bodies have no reply on file'), rows[12]['summary'])
        self.assertNotIn(13, rows, 'no need input, so no beam')
        self.assertEqual(self.click(SET, self.reading)[0][12]['word'], 'SCRIPTED', 'control: the unchanged take finds every reply')


if __name__ == '__main__':
    if sys.argv[1:2] == ['--fill']:   # --fill HOME --set SET --facts kind=TAKE,... [--stage owed|paste|both]
        options = dict(zip(sys.argv[3::2], sys.argv[4::2]))
        facts = {take: kind for kind, take in (pair.split('=') for pair in options['--facts'].split(','))} if options.get('--facts') else {}
        print(json.dumps(fill(Path(sys.argv[2]), Path(options['--set']), facts, options.get('--stage', 'both'))))
        sys.exit(0)
    unittest.main(verbosity=2)
