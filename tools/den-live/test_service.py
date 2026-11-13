"""Tests for step 4: the camera's path allowlist, run_service.py, the unit and install.sh, release.py, install_la.py,
first_check.py and walk_test.py, the scan page's spots mode, and the token stop messages. Stdlib unittest.

    python -B -X utf8 tools\\den-live\\test_service.py

No camera, LA, Home or ssh: fakes on 127.0.0.1 stand in for the camera, the people stream and LA's half of the first
check, and an audit hook (test_sofa_check's) refuses any process that names ssh or scp. The service's end-to-end test
needs the detector's venv and a prepared den-sim set; the release's needs the venv's models and rtmlib's wheel; the
scan page's needs Node. Each skips without them. Private strings the tests plant are made at run time: the den's site
id is read as run_local reads it, an address and a token are random. Everything written is under TEMP.
"""
import atexit
import base64
import contextlib
import dataclasses
import hashlib
import io
import json
import math
import os
import random
import re
import secrets
import shutil
import socket
import string
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_sofa_check as ts  # noqa: E402  its fake camera and its no-ssh audit hook
import first_check as fc  # noqa: E402
import first_check_remote as fr  # noqa: E402
import install_la  # noqa: E402
import perceive  # noqa: E402
import register_now as rn  # noqa: E402
import release  # noqa: E402
import run_local as rl  # noqa: E402
import run_service as rs  # noqa: E402
import sofa_check as sc  # noqa: E402
import source  # noqa: E402
import stream as people_stream  # noqa: E402
import walk_test as wt  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix='den-live-test-service-'))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
TOOLS = Path(tempfile.gettempdir()) / 'den-live-test-tools'
SET = ts.SET
FIT, READING = SET / 'e9e0a238f4bd', SET / '85b080a5815b'
HAVE_SET = (FIT / 'room-model.jsonl').is_file() and (READING / 'frames.csv').is_file()
NODE = shutil.which('node')
UNIT = HERE.parents[1] / 'stack' / 'services' / 'den-live' / 'den-live.service'
INSTALL_SH = UNIT.with_name('install.sh')
MODELS = sc.VENV_PY.parents[1] / 'models'
HAVE_BINARIES = all((MODELS / name).is_file() for name in release.MODEL_FILES)
try:
    WHEEL = release.default_wheel()
except sc.Stop:
    WHEEL = None
HAVE_BINARIES = HAVE_BINARIES and WHEEL is not None and WHEEL.is_file()
TRANSFORM = 'den-virtual@2026-11-09.1'   # the set's own registration


def scratch(prefix):
    return Path(tempfile.mkdtemp(prefix=prefix, dir=TMP))


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def observe_tree():
    return sc.archive(TOOLS, 'observe')


def den_take():
    return rl.den_take(observe_tree())


def quiet(fn, *args, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = fn(*args, **kw)
    return result, out.getvalue()


class Counting:
    """A camera on 127.0.0.1 that only counts what reaches it, and answers 200."""

    def __init__(self):
        self.paths = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                owner.paths.append(self.path)
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'{}')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:%d' % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


# ---- 1. the camera's paths ------------------------------------------------------------------------------------------

class CameraPaths(unittest.TestCase):
    def setUp(self):
        self.camera = Counting()
        self.addCleanup(self.camera.close)
        self.client = source.camera_client(den_take(), self.camera.url, 'x' * 32)

    def test_any_other_path_is_refused_before_any_request(self):
        for path in ('/frame.jpg', '/health', '/', '/frame.npz?full=1', '/calibration/', '//frame.npz', '/FRAME.NPZ',
                     '/frame.npz/../frame.jpg', 'frame.npz', b'/frame.npz', None):
            with self.assertRaises(source.PathRefused, msg=path):
                self.client.get(path)
        self.assertEqual(self.camera.paths, [], 'nothing reached the camera')
        self.assertFalse(hasattr(self.client, 'health'), "den-take's /health check isn't on the client")

    def test_the_two_allowed_paths_go_through(self):
        self.assertEqual(self.client.get('/frame.npz')[0], 200)
        self.assertEqual(self.client.get('/calibration')[0], 200)
        self.assertEqual(self.camera.paths, ['/frame.npz', '/calibration'])

    def test_the_source_holds_any_camera_to_the_allowlist(self):
        raw = den_take().Camera(self.camera.url, 'x' * 32)
        src = source.CameraSource(den_take(), raw, source.Mailbox())
        self.assertIsInstance(src.camera, source.AllowedCamera)

    def test_every_tool_reaches_the_camera_through_the_allowlist(self):
        """The sofa check, step 2's take, the live loop, the service, the install and the first check: no tool builds
        den-take's Camera but source.camera_client, and every camera path any of them names is an allowed one."""
        for path in sorted(HERE.glob('*.py')):
            if path.name.startswith('test_'):
                continue
            text = path.read_text(encoding='utf-8')
            if path.name != 'source.py':
                self.assertNotRegex(text, r'\btake\.Camera\(|R\.take\.Camera\(', path.name)
                self.assertNotRegex(text, r'\.health\(\)', path.name)
            for named in re.findall(r'\.get\("(/[^"]*)"', text):
                self.assertIn(named, source.ALLOWED_PATHS, path.name)
        self.assertIn(fr.FRAME_PATH, source.ALLOWED_PATHS, "the first check's pull")
        for name in ('sofa_check.py', 'register_now.py', 'run_local.py'):
            self.assertIn('open_camera(', (HERE / name).read_text(encoding='utf-8'), name)
        self.assertIn('camera_client(', (HERE / 'run_service.py').read_text(encoding='utf-8'))


# ---- 6. the token's stop message ------------------------------------------------------------------------------------

class TokenStop(unittest.TestCase):
    """Each paste stops on a missing token file with den-take's README's three lines, verbatim, read from the pinned tree
    at run time (they name the camera host's address and account, so they're never copied into den-live)."""

    @classmethod
    def setUpClass(cls):
        cls.lines = sc.token_lines(observe_tree())
        readme = (observe_tree() / 'tools' / 'den-take' / 'README.md').read_text(encoding='utf-8').replace('\r\n', '\n')
        cls.block = readme.split(sc.TOKEN_HEADING + '\n', 1)[1].split('```powershell\n', 1)[1].split('```', 1)[0]

    def test_the_three_lines_are_the_readmes_own(self):
        self.assertEqual(len(self.lines), 3)
        self.assertEqual('\n'.join(self.lines) + '\n', self.block, 'verbatim')
        self.assertTrue(self.lines[0].startswith('New-Item ') and self.lines[1].startswith('ssh ') and self.lines[2].endswith('.Length'))

    def check(self, text):
        for line in self.lines:
            self.assertIn('\n' + line, text)
        self.assertIn("den-take's README, step 2", text)

    def test_the_sofa_check_register_now_and_run_local(self):
        root = scratch('den-live-root-')
        missing = root / 'no-token.txt'
        code, text = quiet(sc.main, [], root=root, console=lambda: True, camera_url='http://127.0.0.1:9', token_file=str(missing),
                           speak=lambda _t: self.fail('spoke'))
        self.assertEqual(code, 2)
        self.check(text)
        code, text = quiet(rn.main, [], root=root, console=lambda: True, camera_url='http://127.0.0.1:9', token_file=missing)
        self.assertEqual(code, 2)
        self.check(text)
        model = root / 'private' / 'register' / 'room-model.jsonl'
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_text('', encoding='utf-8')
        code, text = quiet(rl.main, [], root=root, console=lambda: True, camera_url='http://127.0.0.1:9', token_file=missing,
                           venv=Path(sys.executable), app=rl.APP if rl.APP.is_dir() else HERE.parents[1])
        self.assertEqual(code, 2)
        if (Path(rl.APP) / 'app' / 'src' / 'home-3d' / 'apartment-people-layer.mjs').is_file():
            self.check(text)

    def test_the_readme_has_no_copy_of_them(self):
        readme = (HERE / 'README.md').read_text(encoding='utf-8')
        self.assertNotIn(self.lines[1], readme)
        self.assertIn('three lines', readme)

    def test_lines_that_moved_fall_back_to_naming_the_readme(self):
        tree = scratch('den-live-tree-')
        (tree / 'tools' / 'den-take').mkdir(parents=True)
        (tree / 'tools' / 'den-take' / 'README.md').write_text('# den-take\n', encoding='utf-8')
        self.assertIsNone(sc.token_lines(tree))
        self.assertIn("den-take's README, step 2, has the lines", sc.token_refusal('x', 'C:\\t.txt', tree))


# ---- 2. the transit, and the stream's bind ---------------------------------------------------------------------------

class StreamBind(unittest.TestCase):
    def test_the_bind_is_exclusive_on_both_and_reusable_after_a_stop_on_linux(self):
        first = people_stream.PeopleStream(port=0).start()
        self.addCleanup(first.close)
        with self.assertRaises(OSError, msg='a second listener is refused, on Windows and on Linux'):
            people_stream.PeopleStream(port=first.port)
        # Windows binds exclusively and never sets SO_REUSEADDR; Linux sets it, so a restarted service rebinds while its
        # old clients' connections sit in TIME_WAIT.
        self.assertEqual(first.server.allow_reuse_address, not hasattr(socket, 'SO_EXCLUSIVEADDRUSE'))


# ---- 3. the service --------------------------------------------------------------------------------------------------

def make_packs(polygon, revision='rev-1'):
    packs = scratch('den-live-packs-')
    site = rl.den_site()
    folder = packs / site / revision
    folder.mkdir(parents=True)
    data = json.dumps({'walkable_polygon': polygon}).encode()
    (folder / 'floor.json').write_bytes(data)
    (folder / 'content-pack.json').write_text(json.dumps({'assets': {'floor.json': {'path': 'floor.json',
                                              'sha256': hashlib.sha256(data).hexdigest()}}}), encoding='utf-8')
    (packs / site / 'ACTIVE').write_text(revision + '\n', encoding='utf-8')
    return packs


def receipt(transform_id=TRANSFORM, revision='rev-1', result='registered'):
    return {'schema': 'den-live-register-now-1', 'fit': {'result': result, 'transform_id': transform_id, 'reasons': []},
            'scan_revision': revision}


class Service:
    """The service's inputs in TEMP: credentials, state, settings, as systemd and install.sh lay them out."""

    def __init__(self, camera_url, token, *, packs=None, the_receipt=None, port=None, model=None):
        self.base = scratch('den-live-service-')
        self.cred, self.state = self.base / 'credentials', self.base / 'state'
        self.cred.mkdir()
        self.state.mkdir()
        if model is not False:
            shutil.copyfile(model or FIT / 'room-model.jsonl', self.cred / 'room-model.jsonl')
        (self.cred / 'camera-token.txt').write_text(token, encoding='ascii')
        (self.cred / 'register-receipt.json').write_text(json.dumps(the_receipt or receipt()), encoding='utf-8')
        self.port = port or free_port()
        self.config = self.base / 'config.json'
        self.config.write_text(json.dumps({
            'schema': rs.CONFIG_SCHEMA, 'camera_url': camera_url,
            'content_packs': str(packs or make_packs([[-2.5, -3.0], [2.5, -3.0], [2.5, 2.0], [-2.5, 2.0]])),
            'site': rl.den_site(), 'venv_python': str(sc.VENV_PY), 'models': str(MODELS), 'stream_port': self.port}), encoding='utf-8')
        self.env = {'CREDENTIALS_DIRECTORY': str(self.cred), 'STATE_DIRECTORY': str(self.state)}

    def trees(self):
        return {key: sc.archive(TOOLS, key) for key in rl.TREES}


class ServiceRefusals(unittest.TestCase):
    def main(self, service=None, env=None, **kw):
        return quiet(rs.main, ['--config', str(service.config if service else TMP / 'none.json')],
                     env=env if env is not None else service.env, trees=service.trees() if service else None, **kw)

    def test_outside_systemd_it_refuses(self):
        code, text = self.main(env={})
        self.assertEqual(code, rs.EXIT_REFUSED)
        self.assertIn('run it as the den-live service', text)
        code, text = self.main(env={'CREDENTIALS_DIRECTORY': 'creds', 'STATE_DIRECTORY': 'state'})
        self.assertEqual(code, rs.EXIT_REFUSED)
        self.assertIn('must be absolute folders', text)

    def test_its_state_folder_is_the_first_systemd_lists(self):
        service = Service('http://127.0.0.1:9', 'x' * 32)
        env = dict(service.env, STATE_DIRECTORY=service.env['STATE_DIRECTORY'] + os.pathsep + str(TMP / 'second'))
        self.assertEqual(rs.credentials(env)[1], service.state)

    def test_a_missing_credential_or_settings_file(self):
        service = Service('http://127.0.0.1:9', 'x' * 32)
        (service.cred / 'camera-token.txt').unlink()
        code, text = self.main(service)
        self.assertEqual((code, text.count('\n')), (rs.EXIT_REFUSED, 1), 'one sentence')
        self.assertIn('token credential (camera-token.txt) is missing: paste install_la.py again', text)
        service = Service('http://127.0.0.1:9', 'x' * 32)
        service.config.write_text('{"schema": "other"}', encoding='utf-8')
        self.assertIn('settings file', self.main(service)[1])
        service = Service('http://camera.example:9', 'x' * 32)
        code, text = self.main(service)
        self.assertEqual(code, rs.EXIT_REFUSED)
        self.assertIn('IP address', text)

    @unittest.skipUnless(HAVE_SET and sc.VENV_PY.is_file() and HAVE_BINARIES, 'needs the venv, its models and a prepared den-sim set')
    def test_a_receipt_that_didnt_register_the_camera_and_another_scan_revision(self):
        for the_receipt, words in ((receipt('den-virtual@2026-01-01.9'), "didn't register den-virtual@2026-11-09.1"),
                                   (receipt(revision='rev-0'), 'registered on scan revision rev-0, but the active scan is rev-1')):
            service = Service('http://127.0.0.1:9', 'x' * 32, the_receipt=the_receipt)
            code, text = self.main(service, camera_id='den-virtual')
            self.assertEqual(code, rs.EXIT_REFUSED, text)
            self.assertIn(words, text)
            self.assertNotIn('Starting the detector', text, 'refused before the detector')

    def test_write_config_and_the_drop_in(self):
        base = scratch('den-live-install-')
        packs = make_packs([[0, 0], [1, 0], [1, 1]])
        settings = base / 'settings.json'
        for content_packs, protect in ((str(packs), False), (None, None)):
            settings.write_text(json.dumps({'schema': rs.SETTINGS_SCHEMA, 'camera_url': 'http://192.0.2.%d:8090' % random.randint(1, 254),
                                            'site': rl.den_site(), **({'content_packs': content_packs} if content_packs else {})}), encoding='utf-8')
            if content_packs is None:
                code, text = quiet(rs.main, ['--write-config', '--settings', str(settings), '--gateway-pid', '0', '--venv-python', '/v/bin/python',
                                             '--models', '/m', '--config', str(base / 'config.json'), '--dropin', str(base / 'd.conf')])
                self.assertEqual(code, rs.EXIT_REFUSED)
                self.assertIn("isn't running", text)
                continue
            config = rs.write_config(settings_path=settings, gateway_pid=0, venv_python='/v/bin/python', models='/m',
                                     out=base / 'config.json', dropin=base / 'd.conf')
            self.assertEqual(json.loads((base / 'config.json').read_text(encoding='utf-8')), config)
            self.assertEqual(config['stream_port'], 8111)
            dropin = (base / 'd.conf').read_text(encoding='utf-8')
            self.assertIn('IPAddressAllow=192.0.2.', dropin)
            self.assertIn('BindReadOnlyPaths=%s' % packs, dropin)
            self.assertEqual('ProtectHome=tmpfs' in dropin, protect)
        self.assertIn('ProtectHome=tmpfs', rs.dropin_text(rs.camera_address('http://192.0.2.7:8090'), Path('/home/u/code/home/app/data/home-content')))

    def test_the_qwen_asks_settings_and_drop_in(self):
        base = scratch('den-live-install-')
        packs = make_packs([[0, 0], [1, 0], [1, 1]])
        settings = base / 'settings.json'
        common = {'schema': rs.SETTINGS_SCHEMA, 'camera_url': 'http://192.0.2.5:8090', 'site': rl.den_site(), 'content_packs': str(packs)}
        settings.write_text(json.dumps(dict(common, asks={'ha_url': 'https://192.0.2.20:8123', 'ha_entity': rs.FRIGATE_ENTITY})), encoding='utf-8')
        config = rs.write_config(settings_path=settings, gateway_pid=0, venv_python='/v', models='/m', out=base / 'c.json', dropin=base / 'd.conf')
        self.assertEqual(config['asks']['ha_url'], 'https://192.0.2.20:8123')
        self.assertEqual(config['asks']['vllm_url'], 'http://127.0.0.1:8000')
        self.assertTrue(config['asks']['asks_on'])
        dropin = (base / 'd.conf').read_text(encoding='utf-8')
        self.assertIn('IPAddressAllow=192.0.2.20', dropin)
        self.assertIn('LoadCredential=ha-token.txt:/etc/den-live/private/ha-token.txt', dropin)
        # What install_la writes is what run_service reads back: with the token beside it, the ask is set up.
        creds = scratch('den-live-creds-')
        (creds / rs.HA_TOKEN).write_text('t0ken\n', encoding='utf-8')
        fixed, why = rs.asks_settings(config, creds)
        self.assertIsNone(why)
        # Without an asks block: no asks in the settings, and no credential or address in the drop-in.
        settings.write_text(json.dumps(common), encoding='utf-8')
        config = rs.write_config(settings_path=settings, gateway_pid=0, venv_python='/v', models='/m', out=base / 'c.json', dropin=base / 'd.conf')
        self.assertNotIn('asks', config)
        self.assertNotIn('ha-token', (base / 'd.conf').read_text(encoding='utf-8'))
        # A reinstall keeps the switches as they were: an ask switched off by hand stays off.
        settings.write_text(json.dumps(dict(common, asks={'ha_url': 'http://192.0.2.20:8123'})), encoding='utf-8')
        (base / 'c.json').write_text(json.dumps({'asks': {'asks_on': False, 'min_interval_s': 5, 'ha_url': 'x'}}), encoding='utf-8')
        config = rs.write_config(settings_path=settings, gateway_pid=0, venv_python='/v', models='/m', out=base / 'c.json', dropin=base / 'd.conf')
        self.assertFalse(config['asks']['asks_on'])
        self.assertEqual(config['asks']['min_interval_s'], 5)
        self.assertEqual(config['asks']['ha_url'], 'http://192.0.2.20:8123')
        for bad in ('https://ha.local:8123', 'https://192.0.2.20', 'https://u:p@192.0.2.20:8123', 'ftp://192.0.2.20:21',
                    'http://[fe80::1%25eth0]:8123', 'http://[::ffff:192.0.2.20]:8123'):
            settings.write_text(json.dumps(dict(common, asks={'ha_url': bad})), encoding='utf-8')
            with self.assertRaises(rs.Refusal) as refused:
                rs.write_config(settings_path=settings, gateway_pid=0, venv_python='/v', models='/m', out=base / 'c.json', dropin=base / 'd.conf')
            self.assertNotIn(bad, str(refused.exception))

    def test_install_la_reads_the_asks_two_private_files(self):
        folder = scratch('den-live-private-')
        token, url = folder / install_la.HA_TOKEN_FILE, folder / install_la.HA_URL_FILE
        self.assertEqual(install_la.ha_inputs(token, url), (None, None))
        token.write_text('abc123\n', encoding='utf-8')
        with self.assertRaises(install_la.Stop):
            install_la.ha_inputs(token, url)            # one without the other
        url.write_text('https://192.0.2.20:8123\n', encoding='utf-8')
        self.assertEqual(install_la.ha_inputs(token, url), ('abc123', 'https://192.0.2.20:8123'))
        token.write_bytes('﻿abc123'.encode('utf-8'))   # PowerShell 5.1's -Encoding utf8 writes a byte-order mark
        self.assertEqual(install_la.ha_inputs(token, url)[0], 'abc123')
        for bad_token, bad_url in (('two words', 'https://192.0.2.20:8123'), ('abc123', 'https://ha.local:8123'),
                                   ('abcé123', 'https://192.0.2.20:8123')):
            token.write_text(bad_token, encoding='utf-8')
            url.write_text(bad_url, encoding='utf-8')
            with self.assertRaises(install_la.Stop) as stopped:
                install_la.ha_inputs(token, url)
            self.assertNotIn(bad_url, str(stopped.exception))
            self.assertNotIn(bad_token, str(stopped.exception))

    def test_packs_the_services_own_user_couldnt_read_refuse_and_name_the_folder(self):
        packs = make_packs([[0, 0], [1, 0], [1, 1]])
        site_dir = packs / rl.den_site()
        real = os.stat

        def with_modes(modes):
            class Mode:
                def __init__(self, mode):
                    self.st_mode = mode

            def stat(path):
                return Mode(modes[Path(path)]) if Path(path) in modes else Mode(0o40755 if Path(path).is_dir() else 0o100644)
            return stat
        self.assertIsNone(rs.service_cant_read(packs, rl.den_site(), with_modes({})), 'o+rX all the way down')
        self.assertIsNone(rs.service_cant_read(packs, rl.den_site(), with_modes({packs.parent: 0o40700})), 'above the bind is no matter')
        for blocked, mode in ((site_dir, 0o40750), (site_dir / 'rev-1', 0o40700), (site_dir / 'ACTIVE', 0o100640),
                              (site_dir / 'rev-1' / 'floor.json', 0o100600)):
            self.assertEqual(rs.service_cant_read(packs, rl.den_site(), with_modes({blocked: mode})), blocked)
        settings = scratch('den-live-install-') / 'settings.json'
        settings.write_text(json.dumps({'schema': rs.SETTINGS_SCHEMA, 'camera_url': 'http://192.0.2.5:8090', 'site': rl.den_site(),
                                        'content_packs': str(packs)}), encoding='utf-8')
        with self.assertRaises(rs.Refusal) as refused:
            rs.write_config(settings_path=settings, gateway_pid=0, venv_python='/v', models='/m', out=settings.with_name('c.json'),
                            dropin=settings.with_name('d.conf'), stat=with_modes({site_dir: 0o40750}))
        self.assertIn('chmod o+rX %s' % site_dir, str(refused.exception))
        self.assertIn('leaves your files as they are', str(refused.exception))
        self.assertFalse(settings.with_name('c.json').exists(), 'nothing written')
        manifest = site_dir / 'rev-1' / 'content-pack.json'
        manifest.write_text(json.dumps({'assets': {'floor.json': {'path': '../../floor.json', 'sha256': 'a' * 64}}}), encoding='utf-8')
        with self.assertRaises(rs.Refusal):
            rs.service_cant_read(packs, rl.den_site(), real)

    def test_the_gateways_content_packs_as_server_mjs_finds_them(self):
        cwd = Path('/home/u/code/home')
        self.assertEqual(rs.packs_from({}, cwd).as_posix(), '/home/u/code/home/app/data/home-content')
        self.assertEqual(rs.packs_from({'HOME_WEB_APARTMENT_ASSETS_DIR': '/srv/apt/apartment'}, cwd).as_posix(), '/srv/apt/home-content')
        self.assertEqual(rs.packs_from({'HOME_CONTENT_PACKS_DIR': 'data/packs'}, cwd).as_posix(), '/home/u/code/home/data/packs')
        self.assertIsNone(rs.gateway_packs(0))
        self.assertEqual(fr.packs_dir({}, None, cwd).as_posix(), '/home/u/code/home/app/data/home-content')

    def test_camera_addresses(self):
        self.assertEqual(str(rs.camera_address('http://192.0.2.3:8090')), '192.0.2.3')
        for bad in ('https://192.0.2.3:8090', 'http://cam:8090', 'http://192.0.2.3', 'http://192.0.2.3:8090/x', None):
            with self.assertRaises(rs.Refusal):
                rs.camera_address(bad)


@unittest.skipUnless(HAVE_SET and sc.VENV_PY.is_file() and HAVE_BINARIES, 'needs the detector venv and a prepared den-sim set')
class ServiceLive(unittest.TestCase):
    def test_the_service_on_the_fake_camera_streams_the_reader(self):
        camera = ts.FakeCamera(FIT / 'camera-calibration.json', READING)
        self.addCleanup(camera.close)
        self.addCleanup(camera.assert_allowed_paths)
        service = Service(camera.url, camera.token)
        seen, started = {}, time.monotonic()

        def until():
            if time.monotonic() - started > 30 and time.monotonic() - seen.get('asked', 0) > 1:
                seen['asked'] = time.monotonic()
                url = 'http://127.0.0.1:%d/api/den-live/v1/homes/den/state' % service.port
                try:
                    with urllib.request.urlopen(url, timeout=5) as reply:
                        people = json.loads(reply.read())['people']
                except OSError:
                    people = None
                if people and people.get('tracks'):
                    seen['state'] = people
            return 'state' in seen or time.monotonic() - started > 120
        code, text = quiet(rs.main, ['--config', str(service.config)], env=service.env, until=until, status_every_s=5,
                           trees=service.trees(), camera_id='den-virtual', require_correction=False)
        self.assertEqual(code, 0, text)
        people = seen.get('state')
        self.assertIsNotNone(people, text)
        self.assertEqual(people['schema'], 'den-live-people-1')
        self.assertEqual(people['content_revision'], 'rev-1')
        self.assertEqual(len(people['tracks']), 1)
        self.assertIn('transit_s_median', people['health'])
        kept = sorted(p.name for p in (service.state / 'live').iterdir())
        self.assertEqual(kept, ['METHOD-den-track-floor.md', 'detector.log'], 'nothing but what run_local keeps')
        self.assertFalse(any(p.suffix == '.npz' for p in service.state.rglob('*')), 'no frame kept')
        shapes = (r'^Floor: ', r'^Registration den-virtual@\S+; the camera', r'^Starting the detector',
                  r"^The live body is on, in the detector's process\. It learns the empty room",
                  r"^The live body is off: [a-z '(),:0-9]+\. The markers go on\.$",
                  r"^The Qwen ask is off: [A-Za-z '(),:0-9_./<>-]+\.$",
                  r'^camera [a-z]+(: [a-z ]+)? \| [\d.-]+ frames a second \| \d+ people \| [\d.-]+( s)? behind \| \d+ dropped a minute \| \d+ frames')
        self.assertTrue(all(any(re.search(s, line) for s in shapes) for line in text.splitlines() if line.strip()), text)


class Stand(BaseHTTPRequestHandler):
    """A stand-in for the den home's Home Assistant (GET a state) and LA's vLLM (POST a chat completion), on loopback."""
    seen = None

    def log_message(self, *args):
        pass

    def reply(self, value):
        data = json.dumps(value).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.seen['ha'].append((self.path, self.headers.get('Authorization')))
        self.reply({'entity_id': self.path.rsplit('/', 1)[-1], 'state': 'on'})

    def do_POST(self):
        self.seen['bodies'].append(self.rfile.read(int(self.headers['Content-Length'])))
        facts = {'people_visible': 'one', 'subject_posture': 'sitting', 'held_object': 'book', 'book_state': 'open',
                 'gaze_target': 'book', 'typing_visible': 'no', 'screen_facing_subject': 'not_visible',
                 'activity_hypothesis': 'reading', 'visible_basis': ['book_in_hands'], 'change_vs_prior': 'no_prior'}
        self.reply({'model': 'qwen3-vl-30b', 'usage': {'prompt_tokens': 900, 'completion_tokens': 50},
                    'choices': [{'message': {'role': 'assistant', 'content': json.dumps(facts)}, 'finish_reason': 'stop'}]})


@unittest.skipUnless(HAVE_SET and sc.VENV_PY.is_file() and HAVE_BINARIES, 'needs the detector venv and a prepared den-sim set')
class ServiceAsks(unittest.TestCase):
    def test_the_service_asks_qwen_about_the_seated_reader(self):
        seen = {'ha': [], 'bodies': []}
        handler = type('Seen', (Stand,), {'seen': seen})
        stand = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=stand.serve_forever, daemon=True).start()
        self.addCleanup(stand.shutdown)
        url = 'http://127.0.0.1:%d' % stand.server_address[1]
        camera = ts.FakeCamera(FIT / 'camera-calibration.json', READING)
        self.addCleanup(camera.close)
        self.addCleanup(camera.assert_allowed_paths)
        service = Service(camera.url, camera.token)
        config = json.loads(service.config.read_text(encoding='utf-8'))
        config['asks'] = {'ha_url': url, 'ha_entity': 'binary_sensor.den_person_occupancy', 'vllm_url': url}
        service.config.write_text(json.dumps(config), encoding='utf-8')
        (service.cred / rs.HA_TOKEN).write_text('tok3n', encoding='utf-8')
        found, started = {}, time.monotonic()

        def until():
            if time.monotonic() - started > 30 and time.monotonic() - found.get('asked', 0) > 1:
                found['asked'] = time.monotonic()
                try:
                    with urllib.request.urlopen('http://127.0.0.1:%d/api/den-live/v1/homes/den/state' % service.port, timeout=5) as reply:
                        people = json.loads(reply.read())['people']
                except OSError:
                    people = None
                if people and any(t.get('activity') for t in people.get('tracks') or []):
                    found['state'] = people
            return 'state' in found or time.monotonic() - started > 150
        code, text = quiet(rs.main, ['--config', str(service.config)], env=service.env, until=until, status_every_s=5,
                           trees=service.trees(), camera_id='den-virtual', require_correction=False)
        self.assertEqual(code, 0, text)
        self.assertIn('The Qwen ask is on', text)
        people = found.get('state')
        self.assertIsNotNone(people, text)
        self.assertEqual(people['health']['qwen']['state'], 'asking')
        self.assertEqual(people['tracks'][0]['activity']['word'], 'reading')
        self.assertTrue(seen['ha'] and all(auth == 'Bearer tok3n' for _, auth in seen['ha']))
        body = json.loads(seen['bodies'][-1])   # the first ask can come before 3 s of frames, with 2 stills
        images = [p['image_url']['url'] for p in body['messages'][1]['content'] if p['type'] == 'image_url']
        self.assertIn(len(images), (2, 3))
        for image in images:
            jpeg = base64.b64decode(image.split(',', 1)[1])
            self.assertTrue(jpeg.startswith(b'\xff\xd8') and len(jpeg) > 5000, 'a real still from the detector process')
        lines = [json.loads(line) for path in (service.state / 'days').rglob('asks.jsonl')
                 for line in path.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(lines and lines[0]['outcome'] == 'completed', lines)
        self.assertFalse(any(p.suffix in ('.npz', '.jpg') for p in service.state.rglob('*')), 'no frame or still kept')
        self.assertNotIn('tok3n', text)


class Unit(unittest.TestCase):
    def setUp(self):
        self.text = UNIT.read_text(encoding='utf-8')
        self.lines = [line.strip() for line in self.text.splitlines() if line.strip() and not line.startswith('#')]

    def test_the_units_settings(self):
        for line in ('DynamicUser=yes', 'StateDirectory=den-live', 'MemoryMax=3G', 'CPUQuota=150%', 'Restart=always', 'RestartSec=10',
                     'RestartPreventExitStatus=3', 'NoNewPrivileges=yes', 'ProtectSystem=strict', 'ProtectHome=yes', 'PrivateTmp=yes',
                     'IPAddressDeny=any', 'IPAddressAllow=localhost',
                     'LoadCredential=room-model.jsonl:/etc/den-live/private/room-model.jsonl',
                     'LoadCredential=camera-token.txt:/etc/den-live/private/camera-token.txt',
                     'LoadCredential=register-receipt.json:/etc/den-live/private/register-receipt.json'):
            self.assertIn(line, self.lines)
        self.assertEqual(sorted(rs.CREDENTIALS.values()), sorted(l.split('=', 1)[1].split(':')[0] for l in self.lines if l.startswith('LoadCredential=')))
        self.assertEqual(rs.EXIT_REFUSED, 3)

    def test_the_stream_binds_loopback_only(self):
        self.assertEqual(people_stream.PeopleStream.__init__.__kwdefaults__['host'], '127.0.0.1')
        self.assertIn('people_stream.PeopleStream(port=stream_port, labels=labels)', (HERE / 'run_local.py').read_text(encoding='utf-8'),
                      'the service passes a port and the labels store only: the host stays loopback')


class InstallScript(unittest.TestCase):
    def test_bash_n_and_shellcheck(self):
        bash = shutil.which('bash')
        if not bash:
            self.skipTest('no bash')
        done = subprocess.run([bash, '-n', str(INSTALL_SH)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        text = INSTALL_SH.read_text(encoding='utf-8')
        self.assertIn('set -Eeuo pipefail', text)
        self.assertNotIn('\r', text)
        check = shutil.which('shellcheck')
        if check:
            done = subprocess.run([check, '-x', str(INSTALL_SH)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stdout)

    def test_its_pins_are_the_locks(self):
        text = INSTALL_SH.read_text(encoding='utf-8')
        weights, wheel = release.lock_pins((HERE.parent / 'den-detect' / 'requirements.lock').read_text(encoding='utf-8'))
        pins = dict(re.findall(r'^(\w+_sha)=([0-9a-f]{64})$', text, re.M))
        self.assertEqual(pins, {'yolo_sha': weights['yolo26s.pt'], 'pose_sha': weights['rtmpose-m-body7.onnx'],
                                'manifest_sha': weights['artifact.json'], 'wheel_sha': wheel})
        self.assertIn('wheel=%s' % release.WHEEL, text)

    def prelude(self, tail):
        """install.sh's own settings, functions and traps, up to its first step, then tail."""
        text = INSTALL_SH.read_text(encoding='utf-8')
        folder = scratch('den-live-sh-')
        (folder / 'part.sh').write_text(text.split('# ---- 1. what was copied', 1)[0] + tail, encoding='utf-8', newline='\n')
        return subprocess.run([shutil.which('bash'), str(folder / 'part.sh'), '0' * 64, 'some-host'], capture_output=True, text=True)

    def test_an_unexpected_failure_stops_with_its_line_and_the_undo(self):
        if not shutil.which('bash'):
            self.skipTest('no bash')
        done = self.prelude('say "before"\ncat -- /den-live-no-such-file\nsay "after"\n')
        self.assertEqual(done.returncode, 2)
        self.assertIn('den-live install: refused: stopped at line ', done.stdout)
        self.assertIn(': cat -- /den-live-no-such-file', done.stdout)
        self.assertNotIn('after', done.stdout)
        self.assertIn('ssh -t some-host "sudo systemctl disable --now den-live"', done.stdout)
        self.assertNotIn('Back to the release', done.stdout, 'no release was switched yet')
        done = self.prelude('release=/opt/den-live/releases/new\nprevious=/opt/den-live/releases/old\nfalse\n')
        self.assertIn('Back to the release before this one:', done.stdout)
        self.assertIn('sudo ln -sfn /opt/den-live/releases/old /opt/den-live/current; sudo systemctl restart den-live', done.stdout)
        done = self.prelude('release=/opt/den-live/releases/new\nprevious=/opt/den-live/releases/old\nrefuse "den-live stopped"\n')
        self.assertIn('refused: den-live stopped', done.stdout)
        self.assertIn('Back to the release before this one:', done.stdout, 'a late refusal says how to go back')

    def test_nothing_that_can_fail_hides_its_words(self):
        text = INSTALL_SH.read_text(encoding='utf-8')
        self.assertIn('set -Eeuo pipefail', text)
        self.assertIn("trap 'refuse \"stopped at line ${LINENO}", text)
        for line in text.splitlines():
            if '>/dev/null 2>&1' in line:
                self.assertRegex(line, r'\|\| refuse |^if ! ', 'a hidden failure must be one the script itself refuses on: ' + line)

    def test_it_refuses_before_changing_anything(self):
        bash = shutil.which('bash')
        if not bash:
            self.skipTest('no bash')
        folder = scratch('den-live-shm-')
        shutil.copyfile(INSTALL_SH, folder / 'install.sh')
        for args, words in (([], "give the tarball's sha256"), (['abc'], "give the tarball's sha256")):
            done = subprocess.run([bash, str(folder / 'install.sh'), *args], capture_output=True, text=True)
            self.assertEqual(done.returncode, 2)
            self.assertIn('refused: ' + words, done.stdout)
            self.assertIn('sudo systemctl disable --now den-live', done.stdout, 'its undo line')
        done = subprocess.run([bash, str(folder / 'install.sh'), '0' * 64, 'some-host'], capture_output=True, text=True)
        self.assertIn('ssh -t some-host "sudo systemctl disable --now den-live"', done.stdout, 'a PowerShell line, with the host')


# ---- the release -----------------------------------------------------------------------------------------------------

def other_site_word():
    """The other home's site id, read at run time from a pinned Lab file that names a device by it (never written here):
    the one word there whose digest is the scan's identifier-only place word."""
    data = release.tree_files()['trees/observe-%s/stack/services/perception-lab/la_detector_app.py' % sc.refs()['rung-2-observe']][0]
    return next(w for w in set(re.findall(r'[a-z]+', data.decode('utf-8').lower())) if release._digest(w) in release.CONTEXT_WORDS)


def made_up_name():
    return ''.join(random.choice(string.ascii_lowercase) for _ in range(9)).capitalize()


class ReleaseScan(unittest.TestCase):
    def kinds(self, line, secrets=()):
        return [kind for _, kind in release.scan_text(line + '\n', secrets)]

    def test_the_other_sites_id_counts_only_as_an_identifier(self):
        word = other_site_word()
        for line in ('%s "made it"' % word, '  %s hi' % word, 'if %s; then' % word, 'cmd | %s -n' % word):
            self.assertEqual(self.kinds(line), [], line)
        for line in ('home = "%s"' % word, "home: '%s'" % word, '%s=1' % word, 'HOME %s = 2' % word.upper(), 'url /api/%s/x' % word,
                     'base /%s' % word, 'site_id: %s' % word, 'sites/%s' % word):
            self.assertIn('place name', self.kinds(line), line)

    def test_tailnet_v6_user_folders_and_this_pcs_user(self):
        self.assertIn('tailnet address', self.kinds('addr ' + ':'.join(['fd7a', '115c', 'a1e0', '']) + '12::3'))
        name = made_up_name()
        for line in ('C:' + '\\Users\\' + name + '\\x', 'C:' + '\\\\Users\\\\' + name, 'c:/' + 'Users/' + name,
                     '/' + 'Users/' + name + '/x', 'cd /' + 'home/' + name + '/code'):
            self.assertIn("a user's own folder", self.kinds(line), line)
        for line in ('C:\\Users\\<name>\\x', '%USERPROFILE%\\den-live', '/home/u/code/home', '/home/$USER/x', '/home/<user>/',
                     'under /home or /root', '/home/ubuntu/x'):
            self.assertEqual(self.kinds(line), [], line)
        secrets = release.secrets_of('x-site', None, user=name)
        self.assertIn("this PC's user name", self.kinds('by %s, today' % name.lower(), secrets))
        self.assertEqual(self.kinds('by %ss' % name, secrets), [], 'a whole word only')
        self.assertEqual([k for _, k in release.secrets_of('x', None, user='admin')], ["the den's site id", 'the camera token'], 'no placeholder')

    def test_each_kind_is_found_and_names_no_string(self):
        site = rl.den_site()
        address = '%d.%d.%d.%d' % (10, random.randint(0, 255), random.randint(0, 255), random.randint(1, 254))
        token = secrets.token_urlsafe(24)
        camera_token = secrets.token_hex(20)
        while not release.token_like(token):
            token = secrets.token_urlsafe(24)
        cases = (('home = "%s"' % site, "the den's site id"), ('HOST=' + address, 'private address'),
                 ('t = "%s"' % token, 'token-like string'), ('x.tail' + 'abc.ts' + '.net', 'tailnet name'),
                 ('k=' + camera_token, 'the camera token'), ('%s_CAMERA_TOKEN=1' % site.upper(), 'place name'))
        for line, kind in cases:
            self.assertIn(kind, [k for _, k in release.scan_text('ok\n' + line + '\n', [(site, "the den's site id"), (camera_token, 'the camera token')])], line)
        clean = 'sha 646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b\nhttp://127.0.0.1:8111 torch-2.10.0%2Bcpu-cp312-cp312-win_amd64\n'
        self.assertEqual(release.scan_text(clean, [(site, "the den's site id")]), [], 'hashes, loopback and wheel names are not private')
        with self.assertRaises(sc.Stop) as stop:
            release.scan({'tools/den-live/planted.txt': (('a\nb = "%s"\n' % site).encode(), None, None)}, [(site, "the den's site id")])
        self.assertIn('tools/den-live/planted.txt, line 2', str(stop.exception))
        self.assertNotIn(site, str(stop.exception), 'the refusal never names the string')

    def test_known_pinned_lines_pass_only_for_their_own_blob(self):
        data, key, inner = release.tree_files()['trees/observe-%s/tools/den-take/take.py' % sc.refs()['rung-2-observe']]
        site = rl.den_site()
        self.assertEqual(release.scan({'t/take.py': (data, key, inner)}, [(site, "the den's site id")]), (1, 1))
        with self.assertRaises(sc.Stop):
            release.scan({'t/take.py': (data + b'\n# changed\n', key, inner)}, [(site, "the den's site id")])

    def test_this_branchs_files_are_clean(self):
        files = {path: (data, None, None) for path, data in release.branch_files('worktree')[0].items()}
        texts, known = release.scan(files, [(rl.den_site(), "the den's site id")])
        self.assertGreater(texts, 20)
        self.assertEqual(known, 0)


@unittest.skipUnless(HAVE_BINARIES, "needs the detector venv's models and rtmlib's wheel")
class ReleaseBuild(unittest.TestCase):
    def test_the_tarball_holds_what_it_should_and_its_sha_is_right(self):
        out = scratch('den-live-release-')
        built = release.build(out_dir=out, source='worktree')
        data = (out / release.TARBALL).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), built['sha256'])
        self.assertEqual((out / (release.TARBALL + '.sha256')).read_text(encoding='ascii'), '%s  %s\n' % (built['sha256'], release.TARBALL))
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            files = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
        for name in ('tools/den-live/run_service.py', 'tools/den-live/detect_worker.py', 'tools/den-detect/requirements.lock',
                     'tools/den-detect/detect.py', 'tools/den-sim/refs.json', 'stack/services/den-live/den-live.service',
                     'stack/services/den-live/install.sh', 'RELEASE.json', 'SHA256SUMS', 'wheels/' + release.WHEEL,
                     *('models/' + m for m in release.MODEL_FILES)):
            self.assertIn(name, files)
        sums = dict(reversed(line.split('  ', 1)) for line in files['SHA256SUMS'].decode().splitlines())
        self.assertEqual(set(sums), set(files) - {'SHA256SUMS'})
        self.assertTrue(all(hashlib.sha256(files[n]).hexdigest() == s for n, s in sums.items()))
        for key in rl.TREES:
            folder, _sha, _paths, marker = sc.tree_path(Path('trees'), key)
            self.assertEqual(files[(folder / '.complete').as_posix()].decode(), marker, 'the service finds it without git')
        self.assertFalse([n for n in files if n.startswith('trees/') and n.endswith('README.md')])
        self.assertFalse([n for n in files if n.startswith(('private', 'C:')) or '/private/' in n])
        weights, wheel = release.lock_pins(files['tools/den-detect/requirements.lock'].decode())
        self.assertTrue(all(hashlib.sha256(files['models/' + m]).hexdigest() == weights[m] for m in release.MODEL_FILES))
        doc = json.loads(files['RELEASE.json'])
        self.assertEqual((doc['source'], doc['files'], doc['wheel'][release.WHEEL]), ('worktree', len(files), wheel))
        scanned = [n for n in files if not n.endswith(release.BINARY) and n not in ('RELEASE.json', 'SHA256SUMS')]
        self.assertIn('models/artifact.json', scanned)
        self.assertEqual(doc['scan']['text_files'], len(scanned), 'every text file that goes in was scanned, the manifest too')
        again = release.build(out_dir=scratch('den-live-release-'), source='worktree')
        self.assertEqual(again['sha256'], built['sha256'], 'the same files make the same bytes')

    def test_the_unpacked_release_finds_its_trees_without_git(self):
        """release_trees() in the release as install.sh unpacks it, run by the release's own run_service.py with no git
        on the PATH; and a tree whose marker changed is refused."""
        out = scratch('den-live-release-')
        built = release.build(out_dir=out, source='worktree')
        unpacked = scratch('den-live-unpacked-')
        with tarfile.open(built['tarball']) as tar:
            tar.extractall(unpacked, filter='data')
        script = ('import json, sys; sys.path.insert(0, sys.argv[1]); import run_service as rs\n'
                  'try:\n    print(json.dumps({k: str(v) for k, v in rs.release_trees().items()}))\n'
                  'except rs.Refusal as exc:\n    print("refused: %s" % exc)\n')
        env = {'PATH': str(Path(sys.executable).parent), 'SYSTEMROOT': os.environ.get('SYSTEMROOT', '')}
        run = lambda: subprocess.run([sys.executable, '-B', '-I', '-c', script, str(unpacked / 'tools' / 'den-live')],
                                     capture_output=True, text=True, env=env, cwd=str(unpacked), timeout=60)
        done = run()
        trees = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(sorted(trees), sorted(rl.TREES))
        for key, folder in trees.items():
            self.assertEqual(Path(folder).parent.resolve(), (unpacked / 'trees').resolve(), key)
        self.assertEqual({k: v.name for k, v in rs.release_trees(unpacked / 'trees').items()}, {k: Path(v).name for k, v in trees.items()})
        marker = sc.tree_path(unpacked / 'trees', 'track')[0] / '.complete'
        marker.write_text('other\n', encoding='utf-8')
        self.assertIn('refused: the release has no whole track tree at its pin', run().stdout)

    def test_a_planted_private_string_refuses_and_writes_nothing(self):
        out = scratch('den-live-release-')
        planted = {'tools/den-live/planted.md': ('see %s.' % rl.den_site()).encode()}
        with self.assertRaises(sc.Stop) as stop:
            release.build(out_dir=out, source='worktree', extra=planted)
        self.assertIn('tools/den-live/planted.md, line 1', str(stop.exception))
        token = secrets.token_hex(16)
        with self.assertRaises(sc.Stop) as stop:
            release.build(out_dir=out, source='worktree', token=token, extra={'tools/den-live/x.json': ('{"t": "%s"}' % token).encode()})
        self.assertIn('the camera token', str(stop.exception))
        self.assertEqual(list(out.iterdir()), [], 'nothing was written')

    def test_head_names_its_commit_or_refuses_what_isnt_committed(self):
        dirty = subprocess.run(['git', '-C', str(release.ROOT), 'status', '--porcelain', '--', *release.BRANCH_PATHS],
                               capture_output=True, text=True).stdout.strip()
        if dirty:
            with self.assertRaises(sc.Stop) as stop:
                release.build(out_dir=scratch('den-live-release-'), source='HEAD')
            self.assertIn("aren't committed", str(stop.exception))
        else:
            built = release.build(out_dir=scratch('den-live-release-'), source='HEAD')
            head = subprocess.run(['git', '-C', str(release.ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
            self.assertEqual(json.loads(built['contents']['RELEASE.json'])['commit'], head)


# ---- install_la.py ---------------------------------------------------------------------------------------------------

class FakeLA:
    """install_la's transport: records each ssh and scp, runs nothing remote. install.sh's exit is set per test."""

    def __init__(self, install_code=0):
        self.calls, self.install_code, self.copied = [], install_code, {}

    def shell(self, command, interactive=False):
        self.calls.append(('ssh -t' if interactive else 'ssh', command))
        return self.install_code if 'install.sh' in command else 0

    def copy(self, paths, remote):
        self.calls.append(('scp', remote))
        self.copied = {Path(p).name: Path(p).read_bytes() for p in paths}
        return 0


ON_DEN_LIVE = lambda: ('claude/den-live', 'abc1234')   # the branch check, passed: these tests may run from any checkout


class BranchCheck(unittest.TestCase):
    """install_la installs from claude/den-live only, with no uncommitted change to a tracked file."""

    def repo(self, branch='claude/den-live'):
        repo = scratch('den-live-branch-')
        git = lambda *a: subprocess.run(['git', '-C', str(repo), *a], check=True, capture_output=True)
        git('init', '-q', '-b', branch)
        git('config', 'user.email', 'test@example.invalid')
        git('config', 'user.name', 'test')
        (repo / 'a.txt').write_text('a\n', encoding='utf-8')
        git('add', 'a.txt')
        git('commit', '-q', '-m', 'a')
        return repo

    def test_a_clean_den_live_checkout_passes_and_says_where(self):
        repo = self.repo()
        (repo / 'untracked.txt').write_text('x\n', encoding='utf-8')   # not in the release: it's built from the commit
        branch, commit = install_la.branch_check(repo)
        self.assertEqual(branch, 'claude/den-live')
        self.assertRegex(commit, r'^[0-9a-f]{7,}$')

    def test_another_branch_is_refused_by_name(self):
        with self.assertRaises(sc.Stop) as caught:
            install_la.branch_check(self.repo('claude/den-live-body'))
        self.assertIn('on claude/den-live-body; LA installs from claude/den-live only', str(caught.exception))

    def test_an_uncommitted_change_is_refused(self):
        repo = self.repo()
        (repo / 'a.txt').write_text('changed\n', encoding='utf-8')
        with self.assertRaises(sc.Stop) as caught:
            install_la.branch_check(repo)
        self.assertIn('uncommitted changes to 1 file', str(caught.exception))

    def test_a_folder_that_isnt_a_checkout_is_refused(self):
        with self.assertRaises(sc.Stop) as caught:
            install_la.branch_check(scratch('den-live-not-git-'))
        self.assertIn("isn't a git checkout", str(caught.exception))

    def test_the_paste_stops_on_the_wrong_branch_before_anything(self):
        root, transport = scratch('den-live-root-'), FakeLA()
        def elsewhere():
            raise sc.Stop('this checkout is on main; LA installs from claude/den-live only')
        code, text = quiet(install_la.main, [], root=root, console=lambda: True, transport=transport, check_branch=elsewhere)
        self.assertEqual(code, 2)
        self.assertIn('install: stopped: this checkout is on main', text)
        self.assertEqual(transport.calls, [], 'no ssh, no copy')
        self.assertFalse((root / 'private' / 'release').exists(), 'nothing built')


class InstallLA(unittest.TestCase):
    def test_it_refuses_until_step_2_has_registered(self):
        root = scratch('den-live-root-')
        transport = FakeLA()
        code, text = quiet(install_la.main, [], root=root, console=lambda: True, transport=transport, token_file=root / 't.txt',
                           check_branch=ON_DEN_LIVE)
        self.assertIn('Installing from claude/den-live at abc1234.', text)
        self.assertEqual(code, 2)
        self.assertIn("step 2 hasn't registered the camera yet", text)
        self.assertEqual(transport.calls, [], 'no ssh before step 2')
        self.assertIn('ssh -t hav-ubuntu "sudo systemctl disable --now den-live"', text)

    def test_your_labels_go_to_la_once(self):
        """The labels plan's step 3: install_la brings this PC's labels when they read, the drop-in loads them as a
        credential, and the service copies them into its own state only while it has none, so Home's saves on LA win."""
        import room_labels
        root = scratch('den-live-root-')
        self.assertIsNone(install_la.labels_seed(root), 'no labels on this PC: nothing to bring')
        sofa = {'id': 'l-sofa-1', 'name': 'sofa', 'kind': 'seat', 'box': {'centre_m': [1.0, 2.0, 0.42], 'size_m': [2.0, 0.9, 0.85], 'yaw_deg': 0},
                'on_wall': False, 'made_by': 'owner', 'status': 'confirmed', 'updated_at': None}
        store = room_labels.Store(root / 'private' / 'labels', say=lambda *_: None)
        self.assertEqual(store.put({**room_labels.empty('rev-a'), 'labels': [sofa]})[0], 200)
        path, count = install_la.labels_seed(root)
        self.assertEqual((path.name, count), (rs.LABELS_SEED, 1))
        bad = scratch('den-live-root-')
        (bad / 'private' / 'labels').mkdir(parents=True)
        (bad / 'private' / 'labels' / rs.LABELS_SEED).write_text('{not json', encoding='utf-8')
        seed, text = quiet(install_la.labels_seed, bad)
        self.assertIsNone(seed)
        self.assertIn("don't read", text)

        camera, packs = rs.camera_address('http://192.0.2.7:8090'), Path('/srv/home-content')
        self.assertIn('LoadCredential=room-labels.json:/etc/den-live/private/room-labels.json', rs.dropin_text(camera, packs, labels_seed=True))
        self.assertNotIn(rs.LABELS_SEED, rs.dropin_text(camera, packs))

        credentials = scratch('den-live-credentials-')
        shutil.copyfile(path, credentials / rs.LABELS_SEED)
        state = scratch('den-live-state-') / 'labels'
        state.mkdir()
        env = {'CREDENTIALS_DIRECTORY': str(credentials)}
        took, text = quiet(rs.seed_labels, env, state)
        self.assertTrue(took)
        self.assertIn("Your PC's 1 labels", text)
        self.assertEqual([l['name'] for l in room_labels.Store(state).get()['labels']], ['sofa'])
        (credentials / rs.LABELS_SEED).write_text(json.dumps(room_labels.empty('rev-a')), encoding='utf-8')
        self.assertFalse(quiet(rs.seed_labels, env, state)[0], 'LA has labels: a later seed never replaces them')
        self.assertEqual(len(room_labels.Store(state).get()['labels']), 1)
        self.assertFalse(rs.seed_labels({}, scratch('den-live-state-')), 'no credentials folder: nothing')

    @unittest.skipUnless(HAVE_SET and HAVE_BINARIES, 'needs a prepared den-sim set, the models and the wheel')
    def test_the_paste_copies_installs_and_cleans_up_even_on_failure(self):
        for install_code in (0, 1):
            root = scratch('den-live-root-')
            work = root / 'private' / 'register'
            work.mkdir(parents=True)
            shutil.copyfile(FIT / 'room-model.jsonl', work / 'room-model.jsonl')
            (work / 'register-now-3.json').write_text(json.dumps(receipt()), encoding='utf-8')
            token = secrets.token_urlsafe(24)
            (root / 'token.txt').write_text(token, encoding='ascii')
            transport = FakeLA(install_code)
            code, text = quiet(install_la.main, ['--host', 'la-test', '--camera', 'http://198.51.100.9:8090'], root=root,
                               console=lambda: True, transport=transport, token_file=root / 'token.txt', source='worktree',
                               camera_id='den-virtual', check_branch=ON_DEN_LIVE)
            self.assertEqual(code, 2 if install_code else 0, text)
            sha = (root / 'private' / 'release' / (release.TARBALL + '.sha256')).read_text(encoding='ascii').split()[0]
            remote = install_la.REMOTE
            self.assertEqual(transport.calls, [('ssh', 'rm -rf %s && mkdir -m 700 %s' % (remote, remote)), ('scp', remote),
                                               ('ssh -t', 'sudo bash %s/install.sh %s la-test' % (remote, sha)), ('ssh', 'rm -rf %s' % remote)])
            self.assertEqual(sorted(transport.copied), sorted([release.TARBALL, 'install.sh', 'room-model.jsonl', 'camera-token.txt',
                                                               'register-receipt.json', 'settings.json']))
            self.assertEqual(json.loads(transport.copied['register-receipt.json'])['fit']['transform_id'], TRANSFORM)
            settings = json.loads(transport.copied['settings.json'])
            self.assertEqual((settings['schema'], settings['site'], settings['camera_url']), (rs.SETTINGS_SCHEMA, rl.den_site(), 'http://198.51.100.9:8090'))
            self.assertEqual(hashlib.sha256(transport.copied[release.TARBALL]).hexdigest(), sha)
            self.assertFalse((root / 'private' / 'release' / 'outbox').exists(), 'its own copies are gone')
            self.assertNotIn(token, text)
            self.assertNotIn('198.51.100.9', text)
            self.assertIn('Removed the copies', text)
            if install_code:
                self.assertIn('install.sh stopped', text)


# ---- first_check.py --------------------------------------------------------------------------------------------------

def ok(text=''):
    return {'code': 0, 'out': text}


SERVE_ON = json.dumps({'TCP': {'443': {'TCPForward': '127.0.0.1:5181', 'TerminateTLS': 'host.example', 'ProxyProtocol': 1}}})
SERVE_NO_PROXY = json.dumps({'TCP': {'443': {'TCPForward': '127.0.0.1:5181', 'TerminateTLS': 'host.example'}}})


def canned(**over):
    facts = {'schema': 'den-live-first-check-1',
             'gateway': {'unit': 'home-web-gateway', 'active': ok('active\n'), 'pid': 4242, 'checkout_from': 'gateway'},
             'git': {'head': ok('0123456789abcdef0123456789abcdef01234567\n'), 'branch': ok('main\n'), 'date': ok('2026-11-10\n'), 'status': ok('')},
             'serve': {'json': ok(SERVE_ON), 'text': ok('https://host.example (tailnet only)\n')},
             'packs': {'found': True, 'site': True, 'revisions': ['local-a', 'local-b'], 'count': 2, 'active': 'local-b'},
             'cpu': {'cores': 32, 'loadavg': '3.21 2.10 1.00 2/900 1234\n'},
             'lab_worker': ok('active\n'),
             'install': {'python312': ok('3.12.3\n'), 'libraries': {'libGL': True, 'libglib': True, 'listed': True}, 'port_8111': False,
                         'den_live': {'code': 4, 'out': 'inactive\n'}},
             'pull': {'seconds': 60.0, 'fetch_ms': [50.0] * 200 + [140.0] * 20, 'new_frames': 288, 'bytes': [840000] * 288,
                      'statuses': {'200': 220}, 'errors': {}}}
    facts.update(over)
    return facts


class FirstCheckParsing(unittest.TestCase):
    def test_every_line_on_canned_output(self):
        got = fc.lines(canned(), 'local-b', 'la-test', '2026-11-11 21:00')
        self.assertEqual(got[1], 'Home checkout:    main at 01234567, 2026-11-10, clean (the running gateway\'s folder); gateway home-web-gateway: active')
        self.assertEqual(got[2], 'Tailscale Serve:  port 443 to 127.0.0.1:5181, TLS terminated; proxy protocol: on (--proxy-protocol=1)')
        self.assertEqual(got[3], "Content packs:    2 den revisions (local-a, local-b); active: local-b. Step 2's pack local-b: there, and active")
        self.assertEqual(got[4], 'Frames from LA:   4.8 a second over 60 s; fetch median 50 ms, 95th 140 ms; median 0.84 MB; no errors')
        self.assertEqual(got[5], "LA's CPU:         32 cores, 1-minute load 3.21; the Lab's LA worker (lab-la-detector-trial): active")
        self.assertEqual(got[6], 'For the install:  Python 3.12.3 with venv; libGL yes, libglib yes; port 8111 free; den-live not installed')
        self.assertTrue(got[7].startswith('Jetson relay (plan pick 2): not needed: 4.8 frames a second'))
        self.assertIn('Tell an agent', got[8])
        self.assertFalse(any('host.example' in line for line in got), 'no tailnet name')

    def test_serve_variants(self):
        self.assertIn('proxy protocol: off', fc.serve_line({'json': ok(SERVE_NO_PROXY)}))
        self.assertIn('off: nothing is served', fc.serve_line({'json': ok('{}')}))
        self.assertIn("couldn't read", fc.serve_line({'json': {'code': 1, 'out': ''}, 'text': {'code': 1, 'out': ''}}))
        nested = json.dumps({'Services': {'svc:x': {'TCP': {'443': {'TCPForward': '192.0.2.1:80', 'ProxyProtocol': 1}}}}})
        self.assertEqual(fc.serve_line({'json': ok(nested)}), 'Tailscale Serve:  port 443 to elsewhere; proxy protocol: on (--proxy-protocol=1)')

    def test_packs_variants(self):
        packs = canned()['packs']
        self.assertIn("Step 2's pack local-c: missing on LA", fc.packs_line(packs, 'local-c'))
        self.assertIn('there, but not the active one', fc.packs_line(packs, 'local-a'))
        self.assertIn("Step 2 hasn't registered yet", fc.packs_line(packs, None))
        self.assertIn('holds no den pack', fc.packs_line({'found': True, 'site': False}, 'x'))

    def test_the_relay_verdict(self):
        slow = canned()['pull'] | {'new_frames': 180}
        self.assertEqual(fc.relay_line(slow), 'Jetson relay (plan pick 2): needed: 3.0 frames a second, under 4.')
        laggy = canned()['pull'] | {'fetch_ms': [400.0] * 10}
        self.assertIn('a median fetch of 400 ms, over 300', fc.relay_line(laggy))
        refused = {'seconds': 60, 'fetch_ms': [5.0] * 10, 'new_frames': 0, 'bytes': [], 'statuses': {'401': 10}, 'errors': {}}
        self.assertIn('401 or 403 is the token', fc.relay_line(refused))
        self.assertEqual(fc.pull_line(refused), 'Frames from LA:   none in 60 s: 10 answered 401')

    def test_what_couldnt_be_read_says_so(self):
        facts = canned(git={'head': {'code': None, 'why': 'missing'}}, lab_worker={'code': None, 'why': 'missing'},
                       install={'python312': {'code': None, 'why': 'missing'}, 'libraries': {'listed': False}, 'port_8111': True, 'den_live': {}})
        got = fc.lines(facts, None, 'la', 'now')
        self.assertIn('not found at ~/code/home', got[1])
        self.assertIn("unknown (systemctl --user didn't answer)", got[5])
        self.assertIn('no Python 3.12 with venv', got[6])
        self.assertIn('port 8111 in use', got[6])
        self.assertEqual(fc.lines({'failed': 'KeyError'}, None, 'la', 'now')[0][-37:], 'stopped on LA (KeyError). Tell an agent this line.'[-37:])


class Frames:
    """A camera on 127.0.0.1 with a new bundle every period_s, behind a bearer token."""

    def __init__(self, token, period_s=0.2):
        owner = self
        self.started = time.monotonic()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if self.headers.get('Authorization') != 'Bearer ' + token or self.path != '/frame.npz':
                    self.send_response(401 if self.path == '/frame.npz' else 404)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                n = int((time.monotonic() - owner.started) / period_s)
                body = b'bundle %06d ' % n + b'x' * 1000
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:%d' % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class FirstCheckRemote(unittest.TestCase):
    def setUp(self):
        self.token = secrets.token_urlsafe(24)
        self.camera = Frames(self.token)
        self.addCleanup(self.camera.close)

    def test_the_pull_counts_new_bundles_and_keeps_none(self):
        got = fr.pull(self.camera.url, self.token, 2.0)
        self.assertGreaterEqual(got['new_frames'], 7)
        self.assertLessEqual(got['new_frames'], 11)
        self.assertEqual(set(got['bytes']), {len(b'bundle 000000 ') + 1000})
        self.assertEqual(set(got['statuses']), {'200'})
        self.assertEqual(fr.pull(self.camera.url, 'wrong-' + self.token, 0.6)['new_frames'], 0)

    def test_the_whole_check_through_a_local_stand_in_for_ssh(self):
        """LA's half runs here as `python -`, on the same stdin ssh would carry, with a PATH that holds no tailscale,
        systemctl or git, so it reads nothing of this PC's. The token is in that stdin only."""
        root = scratch('den-live-root-')
        (root / 'token.txt').write_text(self.token, encoding='ascii')
        seen = {}

        def runner(host, text, timeout):
            seen['argv_has_token'] = self.token in host
            seen['stdin_has_token'] = self.token in text
            env = {'PATH': str(Path(sys.executable).parent), 'SYSTEMROOT': os.environ.get('SYSTEMROOT', ''), 'PYTHONIOENCODING': 'utf-8'}
            return subprocess.run([sys.executable, '-I', '-'], input=text, capture_output=True, text=True, env=env, timeout=timeout).stdout
        found, text = quiet(fc.run, root=root, host='la-test', camera_url=self.camera.url, token_file=root / 'token.txt',
                            runner=runner, seconds=2)
        self.assertEqual(seen, {'argv_has_token': False, 'stdin_has_token': True})
        self.assertGreater(found['pull']['new_frames'], 5)
        self.assertIn('Frames from LA:   ', text)
        self.assertIn('Jetson relay (plan pick 2): ', text)
        self.assertNotIn(self.token, text)
        self.assertEqual(text, text.encode('ascii', 'replace').decode('ascii'))


# ---- the walk test ---------------------------------------------------------------------------------------------------

SPOTS = [[1.0, 1.0], [2.0, 0.5], [0.5, -1.0], [-1.0, 0.0]]
OFF = [0.10, 0.20, 0.30, 0.05]   # how far from each spot the fake person stands, along x


class FakeDen(threading.Thread):
    """den-live's people stream on 127.0.0.1, fed 10 events a second: the person stands where self.at says (or nowhere),
    capture 0.30 s before emit, a 0.12 s transit on the health line, and a second person when self.second is set."""

    def __init__(self, revision='rev-1', transit=0.12):
        super().__init__(daemon=True)
        self.stream = people_stream.PeopleStream(port=0, heartbeat_s=0.5).start()
        self.home = 'http://127.0.0.1:%d' % self.stream.port
        self.at, self.second, self.revision, self.transit = None, False, revision, transit
        self.halt, self.seq = threading.Event(), 0

    def run(self):
        while not self.halt.is_set():
            now = time.time()
            tracks = []
            if self.at is not None:
                jitter = random.uniform(-0.002, 0.002)
                tracks.append({'track_id': 'e1-trk-1', 'state': 'confirmed', 'xy_m': [self.at[0] + jitter, self.at[1]], 'sigma_m': 0.1,
                               'heading_deg': None, 'age_s': 0.0})
            if self.second:
                tracks.append({'track_id': 'e1-trk-2', 'state': 'confirmed', 'xy_m': [3.0, 3.0], 'sigma_m': 0.1, 'heading_deg': None, 'age_s': 0.0})
            self.seq += 1
            self.stream.publish({'schema': 'den-live-people-1', 'home': 'den', 'seq': self.seq, 'transform_id': 'den-oak@x',
                                 'content_revision': self.revision, 'capture_ts': now - 0.30, 'emitted_ts': now, 'mode': 'rgb',
                                 'camera': {'status': 'ok', 'why': None},
                                 'health': {'fps_in': 10.0, 'dropped_per_min': 0, 'lag_s_median': 0.3, 'transit_s_median': self.transit},
                                 'tracks': tracks})
            self.halt.wait(0.1)

    def close(self):
        self.halt.set()
        self.stream.close()


FAST = wt.Timing(first_event_s=5.0, lead_s=0.2, walk_s=0.4, window_s=1.5, empty_lead_s=0.2, empty_s=2.0, second_s=6.0,
                 together_s=0.5, after_two_s=0.6)


class WalkMath(unittest.TestCase):
    def setUp(self):
        self.den = FakeDen()
        self.den.start()
        self.addCleanup(self.den.close)
        self.root = scratch('den-live-root-')
        (self.root / 'private' / 'walk').mkdir(parents=True)
        (self.root / 'private' / 'walk' / 'spots.json').write_text(json.dumps({
            'schema': 'den-live-walk-spots-1', 'content_revision': 'rev-1',
            'spots': [{'id': 's%d' % (i + 1), 'xy_m': xy, 'z_m': 0.0} for i, xy in enumerate(SPOTS)]}), encoding='utf-8')
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)
        match = re.match(r'Stand on spot (\d)\.', text)
        if match:   # the person walks there at the cue, standing off the spot by OFF
            k = int(match.group(1)) - 1
            self.den.at = [SPOTS[k][0] + OFF[k], SPOTS[k][1]]
        if text.startswith('Empty den test') and self.den.at is not None:   # leaves 0.6 s into the window
            threading.Timer(FAST.empty_lead_s + 0.6, lambda: setattr(self.den, 'at', None)).start()
        if text.startswith('Second person test'):   # someone comes in 1 s later
            threading.Timer(1.0, lambda: setattr(self.den, 'second', True)).start()

    def test_distances_and_the_lag(self):
        out, results, lag = wt.walk(root=self.root, home=self.den.home, timing=FAST, speak=self.speak, wait=time.sleep, now='now')
        for got, want in zip(results, OFF):
            self.assertAlmostEqual(got['distance_m'], want, delta=0.02)
            self.assertEqual(got['markers'], 1)
        self.assertAlmostEqual(lag['median'], 0.42, delta=0.03, msg='0.30 s on LA plus the 0.12 s transit')
        self.assertAlmostEqual(lag['transit'], 0.12, places=3)
        self.assertEqual(out[0], 'Walk test, now: 4 floor spots, 1.5 s each')
        self.assertRegex(out[1], r'^Distance from spot: 0\.1\d, 0\.[12]\d, 0\.[23]\d, 0\.0\d m   \(goal: within 0\.5 m\)$')
        self.assertRegex(out[2], r'^Lag, capture to stream: median 0\.4\d s, 95th 0\.\d\d s   \(goal: about 1 s; camera to LA 0\.12 s, then 0\.3\d s on LA\)$')
        self.assertEqual([t for t in self.spoken if t.startswith('Stand')], ['Stand on spot %d.' % n for n in range(1, 5)])

    def test_no_transit_says_the_jetsons_clock_is_off(self):
        self.den.transit = None
        out = wt.walk(root=self.root, home=self.den.home, timing=FAST, speak=self.speak, wait=time.sleep, now='now')[0]
        self.assertIn("the Jetson's clock is off again", out[2])

    def test_another_scan_revision_stops_before_any_cue(self):
        self.den.revision = 'rev-2'
        time.sleep(0.4)   # the stream's newest event, which a new client gets first, is on rev-2 by now
        with self.assertRaises(sc.Stop) as stop:
            wt.walk(root=self.root, home=self.den.home, timing=FAST, speak=self.speak, wait=time.sleep)
        self.assertIn('picked on scan revision rev-1', str(stop.exception))
        self.assertEqual(self.spoken, [])

    def test_the_empty_den(self):
        self.den.at = [1.0, 1.0]
        lines, result = wt.empty(home=self.den.home, timing=FAST, speak=self.speak, wait=time.sleep)
        self.assertEqual(result['markers'], 1)
        self.assertAlmostEqual(result['shown_s'], 0.6, delta=0.25)
        self.assertRegex(lines[0], r'^Empty den, 2 s: 1 marker, shown \d s in all   \(goal: 0\)$')
        lines, result = wt.empty(home=self.den.home, timing=FAST, speak=self.speak, wait=time.sleep)
        self.assertEqual(lines[0], 'Empty den, 2 s: 0 markers   (goal: 0)')

    def test_the_second_person(self):
        self.den.at = [1.0, 1.0]
        lines, result = wt.second(home=self.den.home, timing=FAST, speak=self.speak, wait=time.sleep)
        self.assertIn('Two.', self.spoken)
        self.assertAlmostEqual(result['after_s'], 1.0, delta=0.35)
        self.assertGreaterEqual(result['together_s'], 0.5)
        self.assertRegex(lines[0], r'^Second person: two markers after 1 s, together for 1 s   \(goal: two\)$')

    def test_one_person_is_never_two(self):
        self.den.at = [1.0, 1.0]
        timing = dataclasses.replace(FAST, second_s=1.5)
        lines, result = wt.second(home=self.den.home, timing=timing, speak=lambda text: self.spoken.append(text), wait=time.sleep)
        self.assertIsNone(result['after_s'])
        self.assertNotIn('Two.', self.spoken)
        self.assertIn('never two markers', lines[0])


class WalkPieces(unittest.TestCase):
    def test_the_marker_shown_longest_is_measured(self):
        samples = [(0, {'tracks': [{'track_id': 'a', 'xy_m': [0.0, 0.0]}]})] * 3 + [(0, {'tracks': [{'track_id': 'b', 'xy_m': [5.0, 5.0]}]})]
        self.assertEqual(wt.spot_result(samples, [0.3, 0.4]), {'distance_m': 0.5, 'markers': 2})
        self.assertEqual(wt.spot_result([], [0, 0]), {'distance_m': None, 'markers': 0})

    def test_signin_and_missing_routes_stop_with_their_reason(self):
        class Answer(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.send_response(401)
                self.send_header('Content-Length', '0')
                self.end_headers()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Answer)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with self.assertRaises(sc.Stop) as stop:
            wt.connect('http://127.0.0.1:%d' % server.server_address[1], FAST, urllib.request.urlopen)
        self.assertIn('asked for sign-in', str(stop.exception))
        self.assertIn('deploy step 4', wt.no_stream_reason(404))

    def test_the_paste_needs_spots_and_an_https_home(self):
        root = scratch('den-live-root-')
        code, text = quiet(wt.main, ['--home', 'http://x'], root=root, console=lambda: True, speak=lambda t: None)
        self.assertIn('must start with https://', text)
        code, text = quiet(wt.main, [], root=root, console=lambda: True, speak=lambda t: None)
        self.assertIn('--home https://', text)
        code, text = quiet(wt.main, ['--home', 'https://h.example'], root=root, console=lambda: True, speak=lambda t: None,
                           timing=FAST)
        self.assertIn('paste walk_test.py --spots first', text)
        for bad in ('https://h.example/x', 'ftp://h', 'https://'):
            with self.assertRaises(sc.Stop):
                wt.stream_url(bad)
        self.assertEqual(wt.stream_url('https://h.example/'), 'https://h.example/api/den-live/v1/homes/den/stream')

    def test_spots_go_through_the_scan_page_in_its_spots_mode(self):
        root, asked = scratch('den-live-root-'), {}

        def page(work, take, mode):
            asked.update(work=work, take=take, mode=mode)
            return 0
        code, text = quiet(wt.main, ['--spots'], root=root, console=lambda: True, page=page)
        self.assertEqual(code, 0)
        self.assertEqual((asked['take'], asked['mode'], asked['work']), (None, 'spots', (root / 'private' / 'walk').resolve()))


@unittest.skipUnless(NODE, 'needs Node')
class SpotsPage(unittest.TestCase):
    """scan_pick.mjs --mode spots on the authored room: no take, no worksheet, four floor spots to spots.json."""

    def setUp(self):
        self.work = scratch('den-live-walk-')
        self.port = free_port()
        self.origin = 'http://127.0.0.1:%d' % self.port
        self.proc = subprocess.Popen([NODE, str(rn.PICK_PAGE), '--work', str(self.work), '--mode', 'spots', '--room', 'authored',
                                      '--tmp', str(TMP), '--port', str(self.port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8')
        line = self.proc.stdout.readline()
        self.assertTrue(line.startswith('ready '), line)
        with urllib.request.urlopen(self.origin + '/?apartmentRedesign=1&worldZoom=off', timeout=20) as reply:
            self.token = re.search(r'__scanPick=\{token:"([0-9a-f]+)"\}', reply.read().decode('utf-8')).group(1)

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.terminate()
        self.proc.stdout.close()
        self.proc.wait(10)

    def post(self, body):
        request = urllib.request.Request(self.origin + '/__pick/save', data=json.dumps(body).encode(), method='POST',
                                         headers={'Content-Type': 'application/json', 'Origin': self.origin, 'X-Scan-Pick': self.token})
        try:
            with urllib.request.urlopen(request, timeout=20) as reply:
                return reply.status, json.loads(reply.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b'{}')

    def test_state_and_save(self):
        with urllib.request.urlopen(self.origin + '/__pick/state', timeout=20) as reply:
            state = json.loads(reply.read())
        self.assertEqual((state['mode'], [p['id'] for p in state['points']], state['left']), ('spots', ['s1', 's2', 's3', 's4'], False))
        ref = {'asset_name': 'mesh.glb', 'asset_sha256': 'a' * 64}
        picks = {'s%d' % (i + 1): {'point_m': [xy[0], xy[1], 0.01], 'surface_ref': ref} for i, xy in enumerate(SPOTS)}
        short = dict(picks)
        del short['s4']
        self.assertEqual(self.post({'picks': short}), (422, {'ok': False, 'error': 'not_picked:s4'}))
        self.assertEqual(self.post({'picks': {**picks, 'f1': picks['s1']}}), (422, {'ok': False, 'error': 'not_a_spot:f1'}))
        self.assertEqual(self.post({'picks': picks}), (200, {'ok': True, 'saved': 4}))
        self.assertEqual(self.proc.wait(10), 0)
        doc = json.loads((self.work / 'spots.json').read_text(encoding='utf-8'))
        self.assertEqual([s['xy_m'] for s in doc['spots']], SPOTS)
        self.assertEqual(doc['schema'], 'den-live-walk-spots-1')
        self.assertTrue(doc['content_revision'])
        self.assertNotIn('1.0', self.proc.stdout.read(), 'no coordinate printed')


if __name__ == '__main__':
    unittest.main()
