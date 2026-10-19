"""private/pilot-runtime.json and how main() composes the S1.1 warm path.
Fakes only: main() serves nothing and no worker process is started."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from home_shared import camera_pilot_service as service
from home_shared import lab_live_pilot
from home_shared import model_pin
from home_shared import model_worker
from home_shared.pilot_runtime import RUNTIME_NAME, RuntimeOptions, load_runtime_options
from test_camera_pilot_service import FIXTURES, FakeLiveServer, FakeServer
from test_model_pin import write_pin


class RuntimeOptionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / RUNTIME_NAME

    def load(self, value):
        self.path.write_text(value if isinstance(value, str) else json.dumps(value))
        return load_runtime_options(self.path)

    def test_absent_means_the_s1_1_defaults(self):
        self.assertEqual(load_runtime_options(self.path), RuntimeOptions("long_lived", "sudo", 2.0, 30.0))
        self.assertEqual(self.load({"version": 1}), RuntimeOptions())

    def test_each_switch_including_the_rollbacks(self):
        self.assertEqual(self.load({"version": 1, "model_worker": "per_request", "kernel_journal": "adm",
                                    "health_max_age_seconds": 0, "kernel_scan_seconds": 0}),
                         RuntimeOptions("per_request", "adm", 0.0, 0.0))
        self.assertEqual(self.load({"version": 1, "health_max_age_seconds": 3}).health_max_age_seconds, 3.0)

    def test_live_guard_keys_default_to_the_plan_and_are_bounded(self):
        defaults = RuntimeOptions()
        self.assertEqual((defaults.yield_enabled, defaults.yield_running_threshold, defaults.live_shed_gpu_c,
                          defaults.live_shed_tctl_c, defaults.live_stop_gpu_c, defaults.live_reopens_per_hour),
                         (True, 3, 77, 88.0, 79, 3))
        value = {"version": 1, "yield_enabled": False, "yield_running_threshold": 7, "live_shed_gpu_c": 70,
                 "live_shed_tctl_c": 85.5, "live_stop_gpu_c": 80, "live_reopens_per_hour": 0}
        options = self.load(value)
        self.assertEqual((options.yield_enabled, options.yield_running_threshold, options.live_shed_gpu_c,
                          options.live_shed_tctl_c, options.live_stop_gpu_c, options.live_reopens_per_hour),
                         (False, 7, 70, 85.5, 80, 0))
        self.assertEqual(options.model_worker, "long_lived")   # S1.1 keys keep their defaults
        bad = [{"yield_enabled": 1}, {"yield_running_threshold": 0}, {"yield_running_threshold": 17},
               {"yield_running_threshold": 3.0}, {"live_shed_gpu_c": 81}, {"live_shed_gpu_c": 39},
               {"live_shed_gpu_c": 77.5}, {"live_stop_gpu_c": 81}, {"live_shed_gpu_c": 79, "live_stop_gpu_c": 78},
               {"live_shed_tctl_c": 49}, {"live_shed_tctl_c": 101}, {"live_shed_tctl_c": "88"},
               {"live_reopens_per_hour": 4}, {"live_reopens_per_hour": -1}, {"live_reopens_per_hour": True},
               {"vjepa_refuse_gpu_c": 74}]   # the V-JEPA rung is the supervisor's own setting
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load({"version": 1, **changes})

    def test_ingress_keep_alive_key_defaults_to_the_plan_and_is_bounded(self):
        self.assertEqual(RuntimeOptions().ingress_requests_per_connection, 100)
        self.assertEqual(self.load({"version": 1, "ingress_requests_per_connection": 1}).ingress_requests_per_connection, 1)
        self.assertEqual(self.load({"version": 1}).ingress_requests_per_connection, 100)
        for value in (0, 101, 50.0, True, "100", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load({"version": 1, "ingress_requests_per_connection": value})

    def test_anything_else_is_refused(self):
        bad = [{}, {"version": 2}, {"version": True}, {"version": 1, "extra": 1},
               {"version": 1, "model_worker": "pooled"}, {"version": 1, "model_worker": ["long_lived"]},
               {"version": 1, "kernel_journal": "root"}, {"version": 1, "health_max_age_seconds": 3.5},
               {"version": 1, "health_max_age_seconds": -1}, {"version": 1, "health_max_age_seconds": True},
               {"version": 1, "kernel_scan_seconds": 31}, {"version": 1, "kernel_scan_seconds": "30"}, []]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(value)
        for text in ('{"version": 1, "version": 1}', '{"version": 1, "kernel_scan_seconds": NaN}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.load(text)


class MainWarmPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.private = self.root / 'private'
        self.private.mkdir()
        for name, source in (('client.pem', 'client.pem'), ('server.pem', 'server.pem'),
                             ('server.key', 'server-test-only.key'), ('ca.pem', 'ca.pem')):
            (self.private / name).write_bytes((FIXTURES / source).read_bytes())
        write_pin(self.private / model_pin.PIN_NAME)
        FakeServer.created = []

    def enable_live(self):
        from test_lab_live_pilot import config_value
        (self.private / 'live-client.pem').write_bytes((self.private / 'server.pem').read_bytes())
        (self.private / 'live-scene.json').write_text(json.dumps(config_value()))

    def main(self, refused=None):
        """main() at its host boundary; returns (server, the warm-path globals, stderr)."""
        unprotected = lambda path, secret=False, enforce_linux=False: Path(path)
        stderr = io.StringIO()
        with patch.object(service, 'ROOT', self.root), patch.object(service, 'ENGINE', None), \
                patch.object(service, 'OPTIONS', RuntimeOptions()), patch.object(service, 'HEALTH', None), \
                patch.object(service, 'CAMERA_WORKER', None), patch.object(service, 'model_health'), \
                patch.object(service.socket, 'gethostname', return_value='EngineeredLightingServer1'), \
                patch.object(service.os, 'umask'), patch.object(service.signal, 'signal'), \
                patch.object(service, 'protected_file', unprotected), \
                patch.object(lab_live_pilot, 'protected_file', unprotected), \
                patch.object(service, 'PrivateTLSIngress', FakeServer), \
                patch.object(lab_live_pilot, 'PilotLiveIngress', FakeLiveServer), \
                patch.object(model_worker.subprocess, 'Popen', side_effect=AssertionError('no worker at start')), \
                contextlib.redirect_stderr(stderr):
            if refused is not None:
                with self.assertRaises(refused):
                    service.main()
                return None, None, stderr.getvalue()
            service.main()
            state = SimpleNamespace(options=service.OPTIONS, health=service.HEALTH, camera=service.CAMERA_WORKER)
        return FakeServer.created[0], state, stderr.getvalue()

    def test_defaults_compose_a_cache_and_one_worker_per_consumer(self):
        self.enable_live()
        server, state, log = self.main()
        self.assertEqual(state.options, RuntimeOptions())
        self.assertEqual(state.health.max_age, 2.0)
        self.assertIsInstance(state.camera, model_worker.ModelWorker)
        self.assertEqual((state.camera.name, state.camera.starts), ('camera', 0))   # started by the first request
        live = server.args[0]
        self.assertTrue(callable(live._backend.metrics))
        self.assertNotIn('model worker', log)
        # S1.4/S1.8: the live guards read OPTIONS, the cached verdict and the live worker.
        self.assertIs(live._runtime, state.options)
        self.assertIs(live._thermal, service.health_verdict)
        self.assertTrue(callable(live._metrics) and callable(live._health))
        self.assertNotIn('yield unavailable', log)

    def test_the_rollback_restores_per_request_exec_and_checks(self):
        (self.private / RUNTIME_NAME).write_text(json.dumps({"version": 1, "model_worker": "per_request",
                                                             "health_max_age_seconds": 0, "kernel_scan_seconds": 0}))
        self.enable_live()
        server, state, log = self.main()
        self.assertIsNone(state.camera)
        self.assertEqual((state.health.max_age, state.options.kernel_scan_seconds), (0.0, 0.0))
        self.assertFalse(hasattr(server.args[0]._backend, 'metrics'))
        self.assertIsNone(server.args[0]._metrics)   # no live worker: no yield, said once
        self.assertIn('live scene yield unavailable: no live model worker\n', log)

    def test_ingress_keeps_connections_alive_unless_rolled_back(self):
        server, _, _ = self.main()
        self.assertEqual(server.kwargs['policy'], service.IngressPolicy(8, 2, 3, 2, 100, 20))
        FakeServer.created = []
        (self.private / RUNTIME_NAME).write_text(json.dumps({"version": 1, "ingress_requests_per_connection": 1}))
        server, _, _ = self.main()
        # One request per connection (each response says close), as before S1.5; still 8 slots.
        self.assertEqual(server.kwargs['policy'], service.IngressPolicy(8, 2, 3, 2, 1, 20))

    def test_camera_only_still_has_the_camera_worker(self):
        server, state, _ = self.main()
        self.assertIs(type(server), FakeServer)
        self.assertIsInstance(state.camera, model_worker.ModelWorker)
        self.assertIs(server.gateway.backend, service.infer)

    def test_invalid_runtime_options_refuse_the_start(self):
        (self.private / RUNTIME_NAME).write_text(json.dumps({"version": 1, "model_worker": "pooled"}))
        _, _, log = self.main(refused=ValueError)
        self.assertEqual(log, 'camera pilot refusing to start: pilot-runtime.json unavailable: ValueError\n')
        self.assertEqual(FakeServer.created, [])
        self.assertFalse((self.private / 'admission.sqlite').exists())


if __name__ == "__main__":
    unittest.main()
