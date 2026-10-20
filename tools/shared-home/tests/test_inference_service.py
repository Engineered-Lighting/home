"""Local mTLS/SQLite service assembly; no home/model services."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from home_shared.admission import AdmissionController, AdmissionLedger, ResourceManifest
from home_shared.gateway import Gateway, jpeg_dimensions
from home_shared.ingress import IngressPolicy, PrivateTLSIngress
from home_shared.inference_service import InferenceService
from test_admission import FakeVerifier, manifest_fixture
from test_gateway import FakeBackend, binding_fixture, request_fixture
from test_ingress import FIXTURES, FINGERPRINT, context


class InferenceServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = AdmissionLedger(Path(self.temp.name)/'ledger.sqlite')
        self.controller = AdmissionController(self.ledger,
            ResourceManifest.from_dict(manifest_fixture()), FakeVerifier(), clock=lambda: 1000)
        self.backend = FakeBackend(self.controller)
        self.gateway = Gateway(self.controller, self.backend, clock=lambda: 1000,
                               image_validator=jpeg_dimensions)
        self.server = PrivateTLSIngress(('127.0.0.1', 0), self.gateway,
            certificate=str(FIXTURES/'server.pem'), private_key=str(FIXTURES/'server-test-only.key'),
            client_ca=str(FIXTURES/'ca.pem'), identities={FINGERPRINT: binding_fixture()},
            policy=IngressPolicy(2, 1, 1, 1))
        self.service = InferenceService(self.server, poll_seconds=.01)
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        self.assertTrue(self.service.stop(timeout_seconds=2).drained)
        self.ledger.close()
        self.temp.cleanup()

    def request(self, method, path, payload=None):
        client = http.client.HTTPSConnection('localhost', self.server.server_port,
                                            context=context(), timeout=2)
        try:
            client.request(method, path, json.dumps(payload) if payload else None,
                           {'Content-Type': 'application/json'})
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    def wait_for(self, predicate):
        deadline = time.monotonic()+2
        while time.monotonic() < deadline:
            if predicate():
                return
            self.release.wait(.01)
        self.fail('service transition did not occur')

    def test_start_closed_then_explicit_admission_delivers_real_tls_result(self):
        self.controller.enable('epoch-1')
        self.service.start()
        self.assertFalse(self.controller.readiness()['ready'])
        self.assertEqual(self.request('GET', '/healthz')[0], 200)
        self.assertEqual(self.backend.calls, [])
        self.controller.enable('epoch-1')
        self.assertEqual(self.request('POST', '/v1/inference', request_fixture())[0], 202)
        self.wait_for(lambda: self.request('GET', '/v1/inference/request-1')[1]['status'] == 'terminal')
        self.assertEqual(len(self.backend.calls), 1)
        self.assertTrue(self.service.stop(timeout_seconds=2).drained)
        self.assertFalse(self.ledger._closed)
        self.assertFalse(self.controller.readiness()['ready'])

    def test_stop_before_start_and_duplicate_owner_remain_single_use(self):
        with self.assertRaises(RuntimeError):
            InferenceService(self.server, poll_seconds=.01)
        self.assertTrue(self.service.stop(timeout_seconds=0).drained)
        with self.assertRaises(RuntimeError):
            self.service.start()
        self.assertFalse(self.ledger._closed)

    def test_listener_failure_closes_dispatch_without_leaking_exception(self):
        with patch.object(self.server, 'handle_request', side_effect=RuntimeError('private contents')):
            self.service.start()
            self.wait_for(lambda: self.service.failed)
            self.assertTrue(self.service.stop(timeout_seconds=2).drained)
        self.assertFalse(self.controller.readiness()['ready'])
        self.assertNotIn('private', self.controller.readiness()['reason'])

    def test_partial_thread_start_failure_can_be_drained_but_not_restarted(self):
        original = threading.Thread.start
        def start(thread):
            if thread.name == 'shared-home-ingress':
                raise RuntimeError('private startup contents')
            return original(thread)
        with patch.object(threading.Thread, 'start', start):
            with self.assertRaisesRegex(RuntimeError, '^inference service failed to start$'):
                self.service.start()
        self.assertTrue(self.service.failed)
        self.assertTrue(self.service.stop(timeout_seconds=2).drained)
        with self.assertRaises(RuntimeError):
            self.service.start()

    def test_bounded_stop_retains_running_reservation_and_owner(self):
        entered = threading.Event()
        def execute(request):
            entered.set()
            self.release.wait(2)
        self.backend.on_execute = execute
        self.service.start()
        self.controller.enable('epoch-1')
        self.assertEqual(self.request('POST', '/v1/inference', request_fixture())[0], 202)
        self.assertTrue(entered.wait(1))
        start = time.monotonic()
        result = self.service.stop(timeout_seconds=.01)
        self.assertLess(time.monotonic()-start, .5)
        self.assertFalse(result.dispatcher_stopped)
        record = self.ledger.get(self.backend.calls[0].ledger_id)
        self.assertEqual(record.state, 'running')
        self.assertTrue(record.reservation)
        self.assertIs(self.gateway._inference_service, self.service)
        self.assertFalse(self.ledger._closed)
        self.release.set()
        self.assertTrue(self.service.stop(timeout_seconds=2).drained)

    def test_invalid_stop_timeout_does_not_change_running_service(self):
        self.service.start()
        for value in (True, -1, float('nan'), float('inf'), 61, '1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.stop(timeout_seconds=value)
        self.assertEqual(self.request('GET', '/healthz')[0], 200)


if __name__ == '__main__':
    unittest.main()
