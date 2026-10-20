"""Real mutual TLS plus background dispatch; synthetic model/authority only."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from home_shared.admission import AdmissionController, AdmissionLedger, ResourceManifest
from home_shared.dispatch_service import DispatchPump
from home_shared.gateway import Gateway, jpeg_dimensions
from home_shared.ingress import IngressPolicy, PrivateTLSIngress
from test_admission import FakeVerifier, manifest_fixture
from test_gateway import FakeBackend, binding_fixture, request_fixture
from test_ingress import FIXTURES, FINGERPRINT, context


class PrivateSnapshotServiceTests(unittest.TestCase):
    def test_tls_submission_is_dispatched_and_retrieved_without_chat(self):
        with tempfile.TemporaryDirectory() as directory:
            with AdmissionLedger(Path(directory)/'ledger.sqlite') as ledger:
                controller = AdmissionController(ledger, ResourceManifest.from_dict(manifest_fixture()),
                                                 FakeVerifier(), clock=lambda: 1000)
                controller.enable('epoch-1')
                backend = FakeBackend(controller)
                gateway = Gateway(controller, backend, clock=lambda: 1000, image_validator=jpeg_dimensions)
                pump = DispatchPump(gateway, poll_seconds=.01)
                server = PrivateTLSIngress(('127.0.0.1', 0), gateway,
                    certificate=str(FIXTURES/'server.pem'), private_key=str(FIXTURES/'server-test-only.key'),
                    client_ca=str(FIXTURES/'ca.pem'), identities={FINGERPRINT: binding_fixture()},
                    policy=IngressPolicy(2, 1, 1, 1))
                listener = threading.Thread(target=server.serve_forever,
                                            kwargs={'poll_interval': .01}, daemon=True)
                def request(method, path, body=None):
                    client = http.client.HTTPSConnection('localhost', server.server_port,
                                                        context=context(), timeout=2)
                    try:
                        client.request(method, path, body, {'Content-Type': 'application/json'})
                        response = client.getresponse()
                        return response.status, json.loads(response.read())
                    finally:
                        client.close()
                try:
                    listener.start()
                    pump.start()
                    status, result = request('POST', '/v1/inference', json.dumps(request_fixture()))
                    self.assertEqual(status, 202)
                    deadline = time.monotonic()+2
                    while time.monotonic() < deadline:
                        status, result = request('GET', '/v1/inference/request-1')
                        if result['status'] == 'terminal':
                            break
                        threading.Event().wait(.01)
                    self.assertEqual(status, 200)
                    self.assertEqual(result['status'], 'terminal')
                    self.assertIn('observation', result)
                    self.assertEqual(len(backend.calls), 1)
                finally:
                    stopped = pump.stop(timeout_seconds=2)
                    server.shutdown()
                    server.server_close()
                    listener.join(2)
                    self.assertTrue(stopped)
                    self.assertTrue(server.wait_handlers(2))
                    self.assertFalse(listener.is_alive())
                self.assertFalse(controller.readiness()['ready'])
                self.assertEqual(tuple(ledger.db.execute('SELECT state,reservation FROM requests').fetchone()),
                                 ('terminal', '{}'))


if __name__ == '__main__':
    unittest.main()
