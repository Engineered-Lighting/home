"""Real durable stores; engine cleanup authority remains a test fixture."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from home_shared.admission import AdmissionController, AdmissionLedger, ResourceManifest
from home_shared.engine_attestor import EngineReceiptAttestor
from home_shared.engine_receipts import EngineReceiptReader, EngineReceiptStore, ReceiptUnavailable
from home_shared.openai_backend import CompletionObservation
from home_shared.legacy_chat import ChatCompletionObservation
from test_admission import FakeVerifier, manifest_fixture


class AttestorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.manifest = ResourceManifest.from_dict(manifest_fixture())
        self.now = 1000.
        self.ledger_path = root / 'gateway.db'
        self.ledger = AdmissionLedger(self.ledger_path)
        self.addCleanup(lambda: self.ledger.close())
        self.controller = AdmissionController(self.ledger, self.manifest, FakeVerifier(), clock=lambda: self.now)
        self.controller.enable('epoch-1')
        self.controller.submit('req-1', 'victoria', 'fake-victoria', 'b' * 64, 'background', 1015.)
        self.controller.claim_next()
        self.controller.mark_running('req-1')
        self.authority = object()
        bindings = dict(manifest_digest=self.manifest.digest, engine_id=self.manifest.engine_id,
                        engine_epoch='epoch-1', model_revision=self.manifest.model_revision)
        self.store = EngineReceiptStore(root / 'engine.db', **bindings,
                                       cleanup_authority=self.authority, create=True)
        self.addCleanup(self.store.close)
        self.reader = EngineReceiptReader(root / 'engine.db', **bindings)
        self.addCleanup(self.reader.close)
        self.attestor = EngineReceiptAttestor(self.manifest, 'epoch-1', self.reader, self.ledger,
                                             clock=lambda: self.now)
        self.observation = CompletionObservation(self.manifest.digest, self.manifest.engine_id,
            'epoch-1', self.manifest.model_revision, self.manifest.model_alias, 'req-1', 'source-1',
            'b' * 64, 'c' * 64, 'chatcmpl-1', 'd' * 64, 'stop', 10, 5, 1000., 1001., 1015.)

    def register(self, digest='b' * 64):
        self.store.register('req-1', digest, 'c' * 64, ('internal-1',))

    def complete(self, digest='b' * 64):
        return self.store.record_cleanup('req-1', digest, 'c' * 64, ('internal-1',),
                                        authority=self.authority, evidence_sha256='e' * 64)

    def test_response_and_registration_alone_never_release(self):
        self.assertIsNone(self.attestor.attest(self.observation, 1015.))
        self.register()
        self.assertIsNone(self.attestor.attest(self.observation, 1015.))
        receipt = self.complete()
        self.assertEqual(self.attestor.attest(self.observation, 1015.), receipt)
        self.assertTrue(self.attestor.verify_terminal(self.manifest, 'epoch-1', 'req-1', receipt))

    def test_wrong_body_or_identity_and_forged_receipt_fail(self):
        self.register()
        receipt = self.complete()
        for field, value in [('upstream_body_sha256', 'f' * 64), ('request_digest', 'f' * 64),
                             ('engine_epoch', 'epoch-2'), ('model_alias', 'other'),
                             ('model_revision', 'f' * 64), ('manifest_digest', 'f' * 64)]:
            with self.subTest(field=field):
                self.assertIsNone(self.attestor.attest(replace(self.observation, **{field: value}), 1015.))
        self.assertFalse(self.attestor.verify_terminal(self.manifest, 'epoch-1', 'req-1',
            replace(receipt, cleanup_evidence_sha256='f' * 64)))
        self.assertFalse(self.attestor.verify_terminal(self.manifest, 'epoch-2', 'req-1', receipt))

    def test_durable_receipt_must_match_gateway_request(self):
        self.register('f' * 64)
        receipt = self.complete('f' * 64)
        self.assertIsNone(self.attestor.attest(self.observation, 1015.))
        self.assertFalse(self.attestor.verify_terminal(self.manifest, 'epoch-1', 'req-1', receipt))

    def test_restart_reconciles_charge_but_expired_result_stays_invalid(self):
        self.register()
        receipt = self.complete()
        self.ledger.close()
        self.ledger = AdmissionLedger(self.ledger_path)
        self.assertEqual(self.ledger.get('req-1').state, 'indeterminate')
        self.now = 1100.
        attestor = EngineReceiptAttestor(self.manifest, 'epoch-1', self.reader, self.ledger,
                                        clock=lambda: self.now)
        self.assertIsNone(attestor.attest(self.observation, 1015.))
        controller = AdmissionController(self.ledger, self.manifest, attestor, clock=lambda: self.now)
        controller.finish('req-1', receipt)
        self.assertEqual(self.ledger.get('req-1').state, 'terminal')
        self.assertFalse(self.ledger.get('req-1').reservation)
        self.assertFalse(controller.readiness()['ready'])

    def test_lookup_failure_keeps_reservation(self):
        self.register()
        receipt = self.complete()
        self.reader.close()
        with self.assertRaises(ReceiptUnavailable):
            self.attestor.verify_terminal(self.manifest, 'epoch-1', 'req-1', receipt)
        self.assertTrue(self.ledger.get('req-1').reservation)

    def test_chat_json_and_stream_require_the_same_durable_engine_evidence(self):
        self.register()
        for streaming in (False, True):
            observation = ChatCompletionObservation(self.manifest.digest, 'epoch-1', 'req-1',
                'b' * 64, 'c' * 64, 'd' * 64, 200, streaming, 1015.)
            self.assertIsNone(self.attestor.attest(observation, 1015.))
        receipt = self.complete()
        for streaming in (False, True):
            observation = ChatCompletionObservation(self.manifest.digest, 'epoch-1', 'req-1',
                'b' * 64, 'c' * 64, 'd' * 64, 200, streaming, 1015.)
            self.assertEqual(self.attestor.attest(observation, 1015.), receipt)
            self.assertIsNone(self.attestor.attest(replace(observation,
                upstream_body_sha256='f' * 64), 1015.))


if __name__ == '__main__':
    unittest.main()
