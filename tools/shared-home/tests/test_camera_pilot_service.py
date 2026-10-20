import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from home_shared import camera_pilot_service
from home_shared.camera_pilot import BeforeDispatchRejected
from home_shared.camera_pilot_service import infer
from test_model_pin import CONTAINER_ID as CONTAINER


class ModelTransportTests(unittest.TestCase):
    def setUp(self):
        # The pinned container comes from private/model-pin.json (main()).
        engine = patch.object(camera_pilot_service, 'ENGINE', SimpleNamespace(container_id=CONTAINER))
        engine.start()
        self.addCleanup(engine.stop)

    def request(self):
        return SimpleNamespace(request_deadline=1015,max_output_tokens=128,image=b'fixture')

    def result(self):
        return dict(model='qwen3-vl-30b',usage={'completion_tokens':12},
                    choices=[dict(finish_reason='stop',message={'content':'A dark room.'})])

    def test_fixed_container_and_bounded_body_without_retry(self):
        with patch('home_shared.camera_pilot_service.model_health'),patch('home_shared.camera_pilot_service.time.time',return_value=1000),patch('home_shared.camera_pilot_service.subprocess.run',return_value=SimpleNamespace(stdout=json.dumps(self.result()))) as run:
            self.assertEqual(infer(self.request()),'A dark room.')
            self.assertEqual(run.call_count,1)
            self.assertEqual(run.call_args.args[0][:4],['docker','exec','-i',CONTAINER])
            body=json.loads(run.call_args.kwargs['input'])['body']
            self.assertEqual(body['max_tokens'],128)
            self.assertIs(body['stream'],False)
            # Deployed recovery release: one charged request may await a bounded
            # completion after UI expiry (60 s model timeout, 65 s transport bound).
            self.assertEqual(run.call_args.kwargs['timeout'],65)

    def test_incomplete_or_wrong_model_response_is_not_completion(self):
        for patch_value in ({'model':'other'}, {'usage':{'completion_tokens':129}}, {'choices':[]}):
            value={**self.result(),**patch_value}
            with self.subTest(value=patch_value),patch('home_shared.camera_pilot_service.model_health'),patch('home_shared.camera_pilot_service.time.time',return_value=1000),patch('home_shared.camera_pilot_service.subprocess.run',return_value=SimpleNamespace(stdout=json.dumps(value))):
                with self.assertRaises(ValueError):infer(self.request())

    def timed_call(self, health_error=None):
        """One camera call through the live-mode timing wrapper on a fake clock:
        model_health takes 0.12 s, the docker exec 0.59 s."""
        from home_shared import backend_timing
        now = [50.0]

        def advance(seconds, error=None):
            def step(*args, **kwargs):
                now[0] += seconds
                if error is not None:
                    raise error
                return SimpleNamespace(stdout=json.dumps(self.result()))
            return step
        stderr = io.StringIO()
        with patch.object(backend_timing, 'time', SimpleNamespace(monotonic=lambda: now[0])), \
                patch('home_shared.camera_pilot_service.model_health', side_effect=advance(.12, health_error)), \
                patch('home_shared.camera_pilot_service.time.time', return_value=1000), \
                patch('home_shared.camera_pilot_service.subprocess.run', side_effect=advance(.59)), \
                contextlib.redirect_stderr(stderr):
            call = lab_live_pilot.timed_backend(infer, 'camera pilot')
            try:
                call(self.request())
            except BeforeDispatchRejected:
                pass
        return stderr.getvalue()

    def test_timing_line_splits_health_and_exec(self):
        line = self.timed_call()
        self.assertRegex(line, r'^camera pilot backend seconds=\d+\.\d{3} result=ok health_ms=120 exec_ms=590 '
                               r'total_ms=\d+\n$')
        log = self.timed_call(health_error=ValueError('pilot resource guard closed'))
        self.assertRegex(log.splitlines()[-1], r'^camera pilot backend seconds=\d+\.\d{3} '
                                               r'result=BeforeDispatchRejected health_ms=120 exec_ms=0 total_ms=\d+$')

    def test_timeout_is_propagated_without_second_dispatch(self):
        with patch('home_shared.camera_pilot_service.model_health'),patch('home_shared.camera_pilot_service.time.time',return_value=1000),patch('home_shared.camera_pilot_service.subprocess.run',side_effect=subprocess.TimeoutExpired('fixed-worker',15)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):infer(self.request())
            self.assertEqual(run.call_count,1)


import contextlib
import hashlib
import io
import os
import ssl
import tempfile
from pathlib import Path

from home_shared import camera_pilot_service as service
from home_shared import lab_live_pilot
from home_shared import lab_live_scene
from home_shared import model_pin
from test_model_pin import EPOCH, IMAGE, NAME, RESTARTED, STARTED, inspect, write_pin

FIXTURES = Path(__file__).parent / 'fixtures' / 'gateway_client'


class FakeServer:
    """Records composition; serves nothing."""
    created = []

    def __init__(self, address, gateway, *args, **kwargs):
        self.address, self.gateway, self.args, self.kwargs = address, gateway, args, kwargs
        FakeServer.created.append(self)

    def serve_forever(self, poll_interval):
        pass

    def server_close(self):
        pass

    def wait_handlers(self, timeout):
        return True


class FakeLiveServer(FakeServer):
    pass


class MainCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        private = self.root / 'private'
        private.mkdir()
        for name, source in (('client.pem', 'client.pem'), ('server.pem', 'server.pem'),
                             ('server.key', 'server-test-only.key'), ('ca.pem', 'ca.pem')):
            (private / name).write_bytes((FIXTURES / source).read_bytes())
        self.private = private
        write_pin(private / model_pin.PIN_NAME)
        FakeServer.created = []

    @contextlib.contextmanager
    def host(self, inspect_value=None):
        """main()'s host boundary. With inspect_value the real model_health
        reads that one `docker inspect` object (every other host check passes)."""
        unprotected = lambda path, secret=False, enforce_linux=False: Path(path)
        stderr = io.StringIO()
        if inspect_value is None:
            health = patch.object(service, 'model_health')
        else:
            health = patch.object(service.subprocess, 'run',
                                  return_value=SimpleNamespace(stdout=json.dumps([inspect_value])))
        # main() installs the warm-path globals; the patches restore them after.
        with patch.object(service, 'ROOT', self.root), patch.object(service, 'ENGINE', None), \
                patch.object(service, 'OPTIONS', service.RuntimeOptions()), patch.object(service, 'HEALTH', None), \
                patch.object(service, 'CAMERA_WORKER', None), \
                patch.object(service.socket, 'gethostname', return_value='EngineeredLightingServer1'), \
                health, patch.object(service, '_host_health'), patch.object(service.os, 'umask'), \
                patch.object(service.signal, 'signal'), patch.object(service, 'protected_file', unprotected), \
                patch.object(lab_live_pilot, 'protected_file', unprotected), \
                patch.object(service, 'PrivateTLSIngress', FakeServer), \
                patch.object(lab_live_pilot, 'PilotLiveIngress', FakeLiveServer), \
                contextlib.redirect_stderr(stderr):
            yield stderr

    def run_main(self, inspect_value=None):
        with self.host(inspect_value) as stderr:
            service.main()
            self.engine = service.ENGINE
        self.assertEqual(len(FakeServer.created), 1)
        return FakeServer.created[0], stderr.getvalue()

    def refused_start(self, error, inspect_value=None):
        with self.host(inspect_value) as stderr, self.assertRaises(error):
            service.main()
        self.assertEqual(FakeServer.created, [])
        return stderr.getvalue()

    def enable_live(self, **changes):
        from test_lab_live_pilot import config_value
        (self.private / 'live-client.pem').write_bytes((self.private / 'server.pem').read_bytes())
        (self.private / 'live-scene.json').write_text(json.dumps(config_value(**changes)))

    def fingerprint(self, name):
        return hashlib.sha256(ssl.PEM_cert_to_DER_cert((self.private / name).read_text())).hexdigest()

    def test_without_live_provisioning_the_camera_composition_is_unchanged(self):
        server, log = self.run_main()
        self.assertIs(type(server), FakeServer)
        self.assertEqual(server.address, ('192.168.0.100', 9446))
        self.assertEqual(server.kwargs['identities'], {self.fingerprint('client.pem'): service.BINDING})
        # S1.5: 8 slots, each a kept-alive connection of at most 100 requests, 20 s idle.
        self.assertEqual(server.kwargs['policy'], service.IngressPolicy(8, 2, 3, 2, 100, 20))
        self.assertIs(server.gateway.backend, service.infer)
        self.assertEqual(log, '')
        connection = __import__('sqlite3').connect(self.private / 'admission.sqlite')
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
        self.assertNotIn('live_requests', tables)

    def test_disabled_live_provisioning_is_camera_only(self):
        from test_lab_live_pilot import config_value
        (self.private / 'live-scene.json').write_text(json.dumps(config_value(enabled=False)))
        server, log = self.run_main()
        self.assertIs(type(server), FakeServer)
        self.assertEqual(len(server.kwargs['identities']), 1)
        self.assertEqual(log, '')

    def test_enabled_live_provisioning_adds_the_consumer_to_the_same_process(self):
        from test_lab_live_pilot import config_value
        (self.private / 'live-client.pem').write_bytes((self.private / 'server.pem').read_bytes())
        (self.private / 'live-scene.json').write_text(json.dumps(config_value()))
        server, log = self.run_main()
        self.assertIs(type(server), FakeLiveServer)
        pilot, live = server.gateway, server.args[0]
        self.assertIs(live.pilot, pilot)
        self.assertEqual(server.kwargs['identities'], {self.fingerprint('client.pem'): service.BINDING,
                                                        self.fingerprint('live-client.pem'): live.binding})
        self.assertEqual(live.binding.consumer_id, lab_live_scene.CONSUMER_ID)
        self.assertIsNot(pilot.backend, service.infer)  # timed camera wrapper
        self.assertTrue(live.closed and pilot.closed)
        self.assertIn('live scene consumer enabled', log)

    def test_invalid_live_provisioning_never_takes_camera_chat_down(self):
        (self.private / 'live-scene.json').write_text('{"version": 1}')
        server, log = self.run_main()
        self.assertIs(type(server), FakeServer)
        self.assertIs(server.gateway.backend, service.infer)
        self.assertEqual(log, 'live scene consumer disabled: ValueError\n')

    def test_engine_identity_is_model_pin_json_and_replies_are_synchronous(self):
        self.enable_live()
        server, log = self.run_main()
        pilot, live = server.gateway, server.args[0]
        self.assertEqual((self.engine.container_name, self.engine.container_id, self.engine.pin.image,
                          self.engine.pin.started_at), (NAME, CONTAINER, IMAGE, STARTED))
        self.assertEqual(self.engine.path, self.private / model_pin.PIN_NAME)
        self.assertEqual((pilot.reply_wait_seconds, live._reply_wait), (8, 8))
        self.assertEqual(live._engine_epoch(), EPOCH)
        self.assertNotIn('engine_changed', log)

    def test_started_at_mismatch_at_startup_makes_main_fail(self):
        log = self.refused_start(ValueError, inspect(started_at=RESTARTED))
        self.assertIn('vLLM StartedAt changed; pilot refusing', log)
        self.assertFalse((self.private / 'admission.sqlite').exists())
        self.assertEqual(model_pin.load_model_pin(self.private / model_pin.PIN_NAME).started_at, STARTED)
        # The matching incarnation starts.
        server, log = self.run_main(inspect())
        self.assertIs(type(server), FakeServer)
        self.assertEqual(log, '')

    def test_auto_repin_at_startup_keeps_camera_chat_and_closes_live_until_live_scene_is_bumped(self):
        write_pin(self.private / model_pin.PIN_NAME, auto_repin=True)
        self.enable_live()
        server, log = self.run_main(inspect(Id='3' * 64, started_at=RESTARTED))
        self.assertIs(type(server), FakeLiveServer)
        epoch = model_pin.repinned_epoch(NAME, '3' * 64, RESTARTED)
        self.assertIn('engine re-pinned old=%s@%s new=333333333333@%s engine_epoch=%s'
                      % (CONTAINER[:12], STARTED, RESTARTED, epoch), log)
        self.assertIn('live scene admission closed: engine_changed', log)
        self.assertEqual(self.engine.container_id, '3' * 64)  # camera chat follows the new container
        pinned = model_pin.load_model_pin(self.private / model_pin.PIN_NAME)
        self.assertEqual((pinned.container_id, pinned.started_at, pinned.engine_epoch, pinned.repinned_from),
                         ('3' * 64, RESTARTED, epoch, (EPOCH,)))
        # Plan §15 #4: a later restart (any idle release restart) keeps camera
        # chat, and live stays closed until live-scene.json names the new epoch.
        for _ in range(2):
            FakeServer.created = []
            server, log = self.run_main(inspect(Id='3' * 64, started_at=RESTARTED))
            self.assertIs(type(server), FakeLiveServer)
            self.assertNotIn('refusing', log)
            self.assertEqual(log.count('live scene admission closed: engine_changed'), 1)
            live = server.args[0]
            self.assertIn('engine_changed', live._closed_for)
            self.assertEqual(live.config.engine_epoch, EPOCH)
            self.assertEqual(self.engine.container_id, '3' * 64)
            self.assertIs(server.gateway, live.pilot)
        # Another vLLM restart before the bump: still camera chat, live closed.
        later = '2026-10-09T01:00:00Z'
        FakeServer.created = []
        server, log = self.run_main(inspect(Id='3' * 64, started_at=later))
        newest = model_pin.repinned_epoch(NAME, '3' * 64, later)
        self.assertIn('engine_epoch=%s' % newest, log)
        FakeServer.created = []
        server, log = self.run_main(inspect(Id='3' * 64, started_at=later))
        self.assertIs(type(server), FakeLiveServer)
        self.assertIn('live scene admission closed: engine_changed', log)
        # An artifact or epoch no re-pin produced is still refused outright.
        for changes in ({'artifact_sha256': 'c' * 64}, {'engine_epoch': 'typo-epoch'}):
            with self.subTest(changes=changes):
                self.enable_live(**changes)
                FakeServer.created = []
                log = self.refused_start(model_pin.EngineIdentityMismatch, inspect(Id='3' * 64, started_at=later))
                self.assertIn('camera pilot refusing to start: live-scene.json', log)
        # ...and live opens once live-scene.json names the current epoch.
        self.enable_live(engine_epoch=newest)
        FakeServer.created = []
        server, log = self.run_main(inspect(Id='3' * 64, started_at=later))
        self.assertIs(type(server), FakeLiveServer)
        self.assertNotIn('engine_changed', log)
        self.assertEqual(server.args[0]._closed_for, {'stopping'})   # only main()'s own shutdown

    def test_more_re_pins_than_the_lineage_keeps_still_start_camera_chat_with_live_closed(self):
        # Plan §15 #4 with live parked: live-scene.json keeps the provisioned
        # epoch while vLLM restarts more often than repinned_from remembers.
        write_pin(self.private / model_pin.PIN_NAME, auto_repin=True)
        self.enable_live()
        restarts = ['2026-10-%02dT00:00:00Z' % (10 + index) for index in range(model_pin.MAX_REPINNED_FROM + 1)]
        for started in restarts:
            FakeServer.created = []
            server, log = self.run_main(inspect(Id='3' * 64, started_at=started))
            self.assertIn('engine re-pinned', log)
            self.assertIs(type(server), FakeLiveServer)
        pinned = model_pin.load_model_pin(self.private / model_pin.PIN_NAME)
        self.assertEqual(len(pinned.repinned_from), model_pin.MAX_REPINNED_FROM)
        self.assertNotIn(EPOCH, pinned.repinned_from)   # the epoch live-scene.json quotes was dropped
        FakeServer.created = []
        server, log = self.run_main(inspect(Id='3' * 64, started_at=restarts[-1]))
        self.assertIs(type(server), FakeLiveServer)
        self.assertNotIn('refusing', log)
        self.assertEqual(log.count('live scene admission closed: engine_changed'), 1)
        live = server.args[0]
        self.assertIs(server.gateway, live.pilot)
        self.assertIn('engine_changed', live._closed_for)
        self.assertEqual(self.engine.container_id, '3' * 64)
        # Another artifact is still refused outright; the bump to the current epoch opens live.
        self.enable_live(artifact_sha256='c' * 64)
        FakeServer.created = []
        self.refused_start(model_pin.EngineIdentityMismatch, inspect(Id='3' * 64, started_at=restarts[-1]))
        self.enable_live(engine_epoch=pinned.engine_epoch)
        FakeServer.created = []
        server, log = self.run_main(inspect(Id='3' * 64, started_at=restarts[-1]))
        self.assertNotIn('engine_changed', log)
        self.assertEqual(server.args[0]._closed_for, {'stopping'})

    def test_live_identity_mismatch_refuses_to_start(self):
        for changes, field in (({'engine_epoch': 'other-epoch'}, 'engine_epoch=other-epoch'),
                               ({'artifact_sha256': 'c' * 64}, 'artifact_sha256=' + 'c' * 64)):
            with self.subTest(changes=changes):
                self.enable_live(**changes)
                log = self.refused_start(model_pin.EngineIdentityMismatch)
                self.assertIn('camera pilot refusing to start: live-scene.json ' + field, log)
        # A disabled live-scene.json is not checked: camera chat starts.
        self.enable_live(enabled=False, engine_epoch='other-epoch')
        self.assertIs(type(self.run_main()[0]), FakeServer)

    def test_missing_or_invalid_model_pin_refuses_to_start(self):
        (self.private / model_pin.PIN_NAME).write_text('{"version": 1}')
        log = self.refused_start(ValueError)
        self.assertEqual(log, 'camera pilot refusing to start: model-pin.json unavailable: ValueError\n')
        (self.private / model_pin.PIN_NAME).unlink()
        log = self.refused_start(OSError)
        self.assertEqual(log, 'camera pilot refusing to start: model-pin.json unavailable: FileNotFoundError\n')


class StandalonePreflightTests(unittest.TestCase):
    """A bare `model_health()` (no main()): the standalone LA health preflight
    of deploy and recovery helpers, against model-pin.json read-only."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'private').mkdir()
        self.path = write_pin(self.root / 'private' / model_pin.PIN_NAME, auto_repin=True)
        self.protected, self.calls = [], []

    def preflight(self, inspect_value, call=None):
        def protected(path, secret=False, enforce_linux=False):
            self.protected.append((Path(path), enforce_linux))
            return Path(path)

        def run(args, **kwargs):
            self.calls.append(args)
            return SimpleNamespace(stdout=json.dumps([inspect_value]))
        stderr = io.StringIO()
        with patch.object(service, 'ROOT', self.root), patch.object(service, 'ENGINE', None), \
                patch.object(service, 'HEALTH', None), patch.object(service, 'CAMERA_WORKER', None), \
                patch.object(service, 'protected_file', protected), patch.object(service, '_host_health') as host, \
                patch.object(service.subprocess, 'run', run), patch.object(service.time, 'time', return_value=1000), \
                contextlib.redirect_stderr(stderr):
            try:
                return (call or service.model_health)(), host
            finally:
                self.assertIsNone(service.ENGINE)   # no main() state is installed
                self.stderr = stderr.getvalue()

    def test_a_bare_model_health_checks_the_provisioned_pin(self):
        before = self.path.read_bytes()
        result, host = self.preflight(inspect())
        self.assertIsNone(result)
        self.assertEqual(host.call_count, 1)   # every other host check ran
        self.assertEqual(self.calls, [['docker', 'inspect', '--type', 'container', NAME]])
        self.assertEqual(self.protected, [(self.path, True)])   # main()'s provisioning check
        self.preflight(inspect(), service.fresh_health)
        self.assertEqual(self.path.read_bytes(), before)

    def test_a_bare_preflight_never_re_pins_or_rewrites_the_pin(self):
        # "auto_repin": true belongs to the running pilot: a standalone
        # preflight fails on a restarted vLLM, as before model-pin.json.
        before = self.path.read_bytes()
        for value in (inspect(started_at=RESTARTED), inspect(Id='3' * 64, started_at=RESTARTED)):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, model_pin.UNHEALTHY):
                self.preflight(value)
            self.assertIn('vLLM StartedAt changed; pilot refusing', self.stderr)
            self.assertNotIn('engine re-pinned', self.stderr)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(os.listdir(self.root / 'private'), [model_pin.PIN_NAME])

    def test_a_bare_preflight_without_a_pin_fails_closed(self):
        self.path.unlink()
        with self.assertRaises((OSError, ValueError)):
            self.preflight(inspect())
        self.assertEqual(self.calls, [])
        (self.root / 'private' / model_pin.PIN_NAME).write_text('{"version": 1}')
        with self.assertRaises(ValueError):
            self.preflight(inspect())
        self.assertEqual(self.calls, [])

    def test_a_bare_infer_still_never_reaches_the_model(self):
        request = SimpleNamespace(request_deadline=1015, max_output_tokens=128, image=b'fixture')
        with self.assertRaises(BeforeDispatchRejected):
            self.preflight(inspect(), lambda: infer(request))
        self.assertEqual([args[:2] for args in self.calls], [['docker', 'inspect']])   # no docker exec
        self.assertIn('camera pilot preflight rejected: ValueError', self.stderr)


class RepinnedCameraChatTests(unittest.TestCase):
    """After an accepted re-pin the camera request runs on the new container."""

    def test_infer_execs_the_container_its_own_health_check_accepted(self):
        with tempfile.TemporaryDirectory() as root:
            path = write_pin(Path(root) / model_pin.PIN_NAME, auto_repin=True)
            engine = model_pin.load_engine(path, log=lambda line: None)
            calls = []

            def run(args, **kwargs):
                calls.append(args)
                if args[:2] == ['docker', 'inspect']:
                    return SimpleNamespace(stdout=json.dumps([inspect(Id='3' * 64, started_at=RESTARTED)]))
                return SimpleNamespace(stdout=json.dumps(dict(model='qwen3-vl-30b', usage={'completion_tokens': 12},
                    choices=[dict(finish_reason='stop', message={'content': 'A dark room.'})])))
            request = SimpleNamespace(request_deadline=1015, max_output_tokens=128, image=b'fixture')
            with patch.object(service, 'ENGINE', engine), patch.object(service, '_host_health'), \
                    patch.object(service.subprocess, 'run', run), patch.object(service.time, 'time', return_value=1000):
                self.assertEqual(infer(request), 'A dark room.')
            self.assertEqual(calls[0], ['docker', 'inspect', '--type', 'container', NAME])
            self.assertEqual(calls[1][:4], ['docker', 'exec', '-i', '3' * 64])
