"""Actual loopback gateway HTTP; model/commissioning authorities are fixtures."""
from pathlib import Path
import tempfile
import time
import unittest

from home_shared.admission import AdmissionController, AdmissionLedger
from home_shared.dispatch_token import DispatchTokenAuthority
from home_shared.gateway import validate_inference_request
from home_shared.openai_backend import OpenAISnapshotBackend, compose_snapshot_gateway, BackendUnknown
from test_admission import FakeVerifier
from test_gateway import binding_fixture
import test_openai_backend as snapshot
from test_legacy_chat import setup


def dispatch_token(head):
    lines = [line for line in head.decode('ascii').split('\r\n')
             if line.lower().startswith('x-shared-home-dispatch: ')]
    if len(lines) != 1:
        raise AssertionError('exactly one dispatch token required')
    return lines[0].split(': ', 1)[1]


class DispatchWireTests(unittest.TestCase):
    def test_snapshot_signed_after_reservation_and_bound_to_exact_body(self):
        with tempfile.TemporaryDirectory() as directory, snapshot.model_server() as (port, calls):
            subject = snapshot.manifest(port)
            signer = DispatchTokenAuthority(subject.digest, 'fixture-epoch', b'k' * 32, max_deadline_seconds=15)
            backend = OpenAISnapshotBackend(subject, 'fixture-epoch',
                attestor=snapshot.SyntheticAttestor(subject), input_authority=snapshot.SyntheticInputAuthority(subject),
                dispatch_authority=signer)
            with AdmissionLedger(Path(directory) / 'gateway.db') as ledger:
                controller = AdmissionController(ledger, subject, FakeVerifier())
                controller.enable('fixture-epoch')
                gateway = compose_snapshot_gateway(controller, backend)
                payload = snapshot.payload()
                request = validate_inference_request(payload, binding_fixture(), time.time())
                with self.assertRaises(BackendUnknown):
                    backend._dispatch_header(request, 'a' * 64, 3)
                gateway.submit(payload, binding_fixture())
                gateway.dispatch_once()
                self.assertEqual(len(calls), 1)
                binding = signer.verify(dispatch_token(calls[0][0]), backend._body(request))
                self.assertEqual(binding.ledger_id, request.ledger_id)
                self.assertEqual(binding.request_digest, request.digest)
                self.assertNotEqual(binding.request_digest, binding.upstream_body_sha256)

    def test_existing_chat_transport_carries_authenticated_binding(self):
        with snapshot.model_server() as (port, calls), setup(port) as (backend, request, controller, _):
            signer = DispatchTokenAuthority(backend.manifest.digest, 'fixture-epoch', b'k' * 32,
                                            max_deadline_seconds=15)
            backend._dispatch_authority = signer
            backend.execute(request, request.request_deadline)
            binding = signer.verify(dispatch_token(calls[0][0]), request.body)
            self.assertEqual(binding.request_digest, request.digest)
            self.assertTrue(controller.ledger.get(request.ledger_id).reservation)


if __name__ == '__main__':
    unittest.main()
