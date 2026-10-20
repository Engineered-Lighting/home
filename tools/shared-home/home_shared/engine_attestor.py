"""Gateway lookup of protected engine receipts, never HTTP completion evidence.

The reader must point to an explicitly provisioned, protected local engine
ledger. Its writer still requires a commissioned engine/worker cleanup hook.
This adapter neither installs that hook nor supplies admission/health authority.
"""
from __future__ import annotations

import math
import time

from .admission import AdmissionLedger, ResourceManifest
from .engine_receipts import EngineReceipt, EngineReceiptReader
from .openai_backend import CompletionObservation
from .legacy_chat import ChatCompletionObservation


class EngineReceiptAttestor:
    def __init__(self, manifest: ResourceManifest, engine_epoch: str,
                 reader: EngineReceiptReader, ledger: AdmissionLedger, *, clock=time.time):
        if (not isinstance(manifest, ResourceManifest)
                or not isinstance(engine_epoch, str) or not engine_epoch
                or not isinstance(reader, EngineReceiptReader)
                or not isinstance(ledger, AdmissionLedger)):
            raise ValueError('fixed manifest, epoch and protected receipt reader required')
        self.manifest_digest = manifest.digest
        self.engine_epoch = engine_epoch
        self._manifest, self._epoch = manifest, engine_epoch
        self._reader, self._ledger, self._clock = reader, ledger, clock

    def _lookup(self, ledger_id):
        if (self.manifest_digest != self._manifest.digest or self.engine_epoch != self._epoch):
            return None
        record = self._ledger.get(ledger_id)
        if record is None or record.engine_epoch != self._epoch:
            return None
        receipt = self._reader.lookup(ledger_id)
        if (not isinstance(receipt, EngineReceipt) or receipt.cleanup_evidence_sha256 is None
                or (receipt.manifest_digest, receipt.engine_id, receipt.engine_epoch,
                    receipt.model_revision, receipt.ledger_id, receipt.request_digest)
                != (self._manifest.digest, self._manifest.engine_id, self._epoch,
                    self._manifest.model_revision, ledger_id, record.digest)):
            return None
        return receipt

    def attest(self, observation: CompletionObservation | ChatCompletionObservation, deadline: float):
        if (not isinstance(observation, (CompletionObservation, ChatCompletionObservation))
                or type(deadline) not in (int, float) or not math.isfinite(deadline)
                or deadline != observation.request_deadline):
            return None
        before = self._clock()
        if not math.isfinite(before) or before >= deadline:
            return None
        receipt = self._lookup(observation.ledger_id)
        after = self._clock()
        if (receipt is None or not math.isfinite(after) or after < before or after >= deadline
                or (observation.manifest_digest, observation.engine_epoch, observation.request_digest,
                    observation.upstream_body_sha256)
                != (receipt.manifest_digest, receipt.engine_epoch, receipt.request_digest,
                    receipt.upstream_body_sha256)):
            return None
        if isinstance(observation, CompletionObservation) and (
                observation.engine_id, observation.model_revision, observation.model_alias) != (
                receipt.engine_id, receipt.model_revision, self._manifest.model_alias):
            return None
        # Chat transport has no caller-provided engine/model identity. _lookup
        # binds those to the fixed manifest and protected engine receipt instead.
        return receipt

    def verify_terminal(self, manifest: ResourceManifest, engine_epoch: str,
                        request_id: str, evidence) -> bool:
        """Re-read durable truth, including during post-crash reconciliation.

        No observation deadline applies to release of an already charged
        reservation. An expired result still cannot become current context.
        Storage failures propagate so the controller closes admission.
        """
        if (manifest != self._manifest or engine_epoch != self._epoch
                or not isinstance(evidence, EngineReceipt)):
            return False
        receipt = self._lookup(request_id)
        return receipt is not None and receipt == evidence
