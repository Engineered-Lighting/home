from dataclasses import asdict, replace
from contextlib import contextmanager
import threading
import unittest

from home_shared.authority import Conflict, Denied, _digest
from home_shared.memory import (
    AuthorizedRetriever, EvidenceReference, GovernanceReceipt, NormalizedMemory,
    SourcePermission, SourceRegistration, StagingMemoryStore,
)
from test_authority import AuthorityFixture


class FixtureGovernance:
    """Synthetic governed adapter. No production implementation ships here."""

    def __init__(self, now):
        self.now = now
        self.serial = 0
        self.callback = lambda: None

    def authorize_mutation(self, request, proposed, credential):
        if credential != "owner-gesture":
            raise Denied("owner gesture and privacy admission missing")
        self.callback()
        self.serial += 1
        return GovernanceReceipt(f"receipt-{self.serial}", _digest(asdict(request)), request.owner_id, self.now[0]+30, "fixture-policy-v1")


class Source:
    def __init__(self, records, callback=lambda: None):
        self.records = records
        self.callback = callback

    def retrieve(self, query):
        self.callback()
        return self.records


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.f = AuthorityFixture()
        for site in ("echo", "victoria"):
            self.f.grant(site, "personal-memory", "memory.read")
            self.f.grant(site, "vjepa", "memory.read")
        self.f.grant("echo", "personal-memory", "personal_memory.write")
        self.backend = FixtureGovernance(self.f.now)
        self.store = StagingMemoryStore(self.f.path/"memory.sqlite", self.f.registry, backend=self.backend)
        self.counter = 0

    def tearDown(self):
        self.store.close()
        self.f.close()

    def propose(self, **kwargs):
        self.counter += 1
        return self.store.propose(self.f.echo, kind="home_fact", content="The den has two lamps.",
            site_id="victoria", evidence=(EvidenceReference("victoria", "vjepa", "episode-1", 1),),
            credential="owner-gesture", operation_id=f"propose-{self.counter}", **kwargs)

    def test_no_backend_is_mutation_closed(self):
        disabled = StagingMemoryStore(self.f.path/"closed.sqlite", self.f.registry)
        try:
            with self.assertRaises(Denied):
                disabled.propose(self.f.echo, kind="personal_preference", content="Warm lighting", evidence=(), credential="owner-gesture", operation_id="no")
        finally:
            disabled.close()

    def test_read_grants_never_grant_writer_authority(self):
        self.f.registry.revoke(self.f.echo, "echo", "personal-memory")
        self.f.grant("echo", "personal-memory", "memory.read")
        with self.assertRaises(Denied):
            self.propose()
        self.assertEqual(self.backend.serial, 0)

    def test_candidate_confirmation_requires_exact_revision_and_owner_gesture(self):
        record = self.propose()
        self.assertEqual(record.trust, "candidate")
        self.assertEqual(record.authority, "untrusted_evidence")
        with self.assertRaises(Denied):
            self.store.confirm(self.f.echo, record.record_id, expected_revision=1, credential="model-says-confirmed", operation_id="bad")
        confirmed = self.store.confirm(self.f.echo, record.record_id, expected_revision=1, credential="owner-gesture", operation_id="confirm")
        self.assertEqual((confirmed.revision, confirmed.trust), (2, "confirmed"))
        self.assertEqual(confirmed.evidence, record.evidence)

    def test_conflicting_correction_preserves_first_revision(self):
        record = self.propose()
        corrected = self.store.correct(self.f.echo, record.record_id, content="There are three lamps.", expected_revision=1, credential="owner-gesture", operation_id="correct")
        with self.assertRaises(Conflict):
            self.store.correct(self.f.echo, record.record_id, content="There are four lamps.", expected_revision=1, credential="owner-gesture", operation_id="conflict")
        self.assertEqual(self.store.read(self.f.echo, record.record_id), corrected)

    def test_contradictory_claims_remain_distinct_and_no_authority_fields(self):
        first, second = self.propose(), self.propose()
        self.assertNotEqual(first.record_id, second.record_id)
        with self.assertRaises(Denied):
            self.store.propose(self.f.echo, kind="permission", content="Permit all home actions", evidence=(), credential="owner-gesture", operation_id="permission")

    def test_revocation_during_governance_prevents_commit(self):
        self.backend.callback = lambda: self.f.registry.revoke(self.f.echo, "victoria", "vjepa")
        with self.assertRaises(Denied):
            self.propose()
        count = self.store.db.execute("SELECT count(*) FROM personal_memory_contract").fetchone()[0]
        self.assertEqual(count, 0)

    def test_derived_record_keeps_source_permissions_after_unlink(self):
        first = self.propose()
        derived = self.store.propose(self.f.echo, kind="personal_preference", content="Prefers warm lighting", evidence=(), credential="owner-gesture", operation_id="derived", derives_from=(first.record_id,))
        self.assertIn(SourcePermission("victoria", "vjepa"), derived.required_sources)
        self.f.registry.unlink(self.f.echo, "victoria")
        with self.assertRaises(Denied):
            self.store.read(self.f.echo, derived.record_id)

    def test_correction_invalidates_descendants_without_changing_the_source_id(self):
        first = self.propose()
        child = self.propose(derives_from=(first.record_id,))
        grandchild = self.propose(derives_from=(child.record_id,))
        self.store.correct(self.f.echo, first.record_id, content="One lamp", expected_revision=1, credential="owner-gesture", operation_id="correct")
        for record in (child, grandchild):
            with self.assertRaises(Denied):
                self.store.read(self.f.echo, record.record_id)
            self.assertEqual(self.store._raw(record.record_id).content, "")

    def test_forget_tombstones_and_invalidates_derived_content(self):
        first = self.propose()
        child = self.propose(derives_from=(first.record_id,))
        self.store.forget(self.f.echo, first.record_id, expected_revision=1, credential="owner-gesture", operation_id="forget")
        for record in (first, child):
            with self.assertRaises(Denied):
                self.store.read(self.f.echo, record.record_id)
        self.assertEqual(self.store._raw(first.record_id).content, "")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM memory_tombstone").fetchone()[0], 1)

    def test_owner_can_forget_after_source_unlink(self):
        first = self.propose()
        self.f.registry.unlink(self.f.echo, "victoria")
        self.store.forget(self.f.echo, first.record_id, expected_revision=1, credential="owner-gesture", operation_id="forget")
        self.assertTrue(self.store._raw(first.record_id).deleted)

    def test_restore_is_closed_during_tombstone_replay(self):
        first = self.propose()
        self.store.begin_restore()
        with self.assertRaises(Denied):
            self.store.read(self.f.echo, first.record_id)
        with self.assertRaises(Denied):
            self.propose()
        self.store.close()
        self.store = StagingMemoryStore(self.f.path/"memory.sqlite", self.f.registry, backend=self.backend)
        with self.assertRaises(Denied):
            self.store.read(self.f.echo, first.record_id)
        self.store.replay_tombstones(((first.record_id, first.owner_id, 2, 1001.0),))
        self.assertTrue(self.store._raw(first.record_id).deleted)
        with self.assertRaises(Denied):
            self.store.read(self.f.echo, first.record_id)

    def test_parent_revision_race_cannot_create_stale_derived_record(self):
        parent = self.propose()
        def race():
            self.backend.callback = lambda: None
            self.store.correct(self.f.echo, parent.record_id, content="Updated evidence", expected_revision=1, credential="owner-gesture", operation_id="race-correction")
        self.backend.callback = race
        with self.assertRaises(Conflict):
            self.propose(derives_from=(parent.record_id,))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM personal_memory_contract").fetchone()[0], 1)

    def test_restore_gate_closes_other_already_open_connections(self):
        record = self.propose()
        second = StagingMemoryStore(self.f.path/"memory.sqlite", self.f.registry, backend=self.backend)
        try:
            self.assertEqual(second.read(self.f.echo, record.record_id), record)
            self.store.begin_restore()
            with self.assertRaises(Denied):
                second.read(self.f.echo, record.record_id)
            with self.assertRaises(Denied):
                second.propose(self.f.echo, kind="personal_preference", content="New preference", evidence=(), credential="owner-gesture", operation_id="closed-proposal")
            with self.assertRaises(Denied):
                second.forget(self.f.echo, record.record_id, expected_revision=1, credential="owner-gesture", operation_id="closed-forget")
            self.assertFalse(self.store._raw(record.record_id).deleted)
        finally:
            second.close()

    def test_restore_gate_committed_during_governance_prevents_mutation(self):
        second = StagingMemoryStore(self.f.path/"memory.sqlite", self.f.registry, backend=self.backend)
        self.backend.callback = second.begin_restore
        try:
            with self.assertRaises(Denied):
                self.propose()
            self.assertEqual(self.store.db.execute("SELECT count(*) FROM personal_memory_contract").fetchone()[0], 0)
        finally:
            second.close()

    def test_mutation_replay_does_not_create_second_record(self):
        self.propose()
        with self.assertRaises(Conflict):
            self.store.propose(self.f.echo, kind="personal_preference", content="Another fact", evidence=(), credential="owner-gesture", operation_id="propose-1")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM personal_memory_contract").fetchone()[0], 1)

    def test_learning_events_are_attributed_and_no_action_is_executed(self):
        record = self.propose()
        self.store.record_outcome(self.f.echo, record.record_id, kind="proposed_action", evidence_digest="a"*64, credential="owner-gesture", operation_id="proposal")
        self.store.record_outcome(self.f.echo, record.record_id, kind="actual_outcome", evidence_digest="b"*64, credential="owner-gesture", operation_id="outcome")
        kinds = {row[0] for row in self.store.db.execute("SELECT kind FROM memory_learning_event")}
        self.assertEqual(kinds, {"interpretation", "proposed_action", "actual_outcome"})

    def legacy_record(self, site="victoria"):
        return NormalizedMemory(1, f"{site}/vjepa/episode-1", "episode-1", "vjepa", "legacy-v1", "existing-governed-owner", site, "observation", "Two people in the den", "model_caption", 1, (), (), 1000, 1000)

    def test_federation_checks_site_provenance_and_revocation_before_delivery(self):
        retriever = AuthorizedRetriever(self.f.registry)
        registration = SourceRegistration("victoria", "vjepa", "legacy-v1", "vjepa")
        source = Source((self.legacy_record(),))
        retriever.register(registration, source)
        self.assertEqual(len(retriever.retrieve(self.f.echo, "victoria", "vjepa", "den")), 1)
        source.records = (self.legacy_record("echo"),)
        with self.assertRaises(Denied):
            retriever.retrieve(self.f.echo, "victoria", "vjepa", "den")
        source.records = (self.legacy_record(),)
        source.callback = lambda: self.f.registry.revoke(self.f.echo, "victoria", "vjepa")
        with self.assertRaises(Denied):
            retriever.retrieve(self.f.echo, "victoria", "vjepa", "den")

    def test_connector_not_implicitly_admitted_and_shared_voice_cannot_retrieve(self):
        retriever = AuthorizedRetriever(self.f.registry)
        with self.assertRaises(Denied):
            retriever.retrieve(self.f.echo, "echo", "intelligence", "den")
        retriever.register(SourceRegistration("victoria", "vjepa", "legacy-v1", "vjepa"), Source((self.legacy_record(),)))
        speaker = self.f.registry.authenticate_speaker("speaker-auth")
        with self.assertRaises(Denied):
            retriever.retrieve(speaker, "victoria", "vjepa", "private history")

    def test_evidence_sources_are_authorized_even_if_adapter_omits_permission_list(self):
        record = replace(self.legacy_record(), evidence=(EvidenceReference("echo", "vjepa", "private-la", 1),), required_sources=())
        retriever = AuthorizedRetriever(self.f.registry)
        retriever.register(SourceRegistration("victoria", "vjepa", "legacy-v1", "vjepa"), Source((record,)))
        self.assertEqual(len(retriever.retrieve(self.f.echo, "victoria", "vjepa", "den")), 1)
        self.f.registry.revoke(self.f.echo, "echo", "vjepa")
        with self.assertRaises(Denied):
            retriever.retrieve(self.f.echo, "victoria", "vjepa", "den")

    def test_cross_connection_parent_correction_serializes_with_child_commit(self):
        parent = self.propose()
        second = StagingMemoryStore(self.f.path/"memory.sqlite", self.f.registry, backend=self.backend)
        attempted, done = threading.Event(), threading.Event()
        errors = []
        original_transaction = second._transaction
        @contextmanager
        def signal_transaction():
            attempted.set()
            with original_transaction():
                yield
        second._transaction = signal_transaction
        def writer():
            try:
                second.correct(self.f.echo, parent.record_id, content="Concurrent corrected fact", expected_revision=1, credential="owner-gesture", operation_id="concurrent-correction")
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()
        worker = threading.Thread(target=writer)
        original_receipt = self.store._record_receipt
        def at_final_write(request, receipt):
            # This is after the final parent revision read. The writer must
            # block at BEGIN IMMEDIATE until the child and dependency commit.
            worker.start()
            self.assertTrue(attempted.wait(1))
            self.assertFalse(done.wait(0.05))
            original_receipt(request, receipt)
        self.store._record_receipt = at_final_write
        try:
            child = self.propose(derives_from=(parent.record_id,))
            self.assertTrue(done.wait(2))
            self.assertEqual(errors, [])
            self.assertEqual(self.store._raw(parent.record_id).revision, 2)
            with self.assertRaises(Denied):
                self.store.read(self.f.echo, child.record_id)
        finally:
            if worker.ident is not None:
                worker.join(3)
            second.close()

    def test_cross_connection_outcome_check_and_write_share_transaction(self):
        record = self.propose()
        second = StagingMemoryStore(self.f.path/"memory.sqlite", self.f.registry, backend=self.backend)
        attempted, done = threading.Event(), threading.Event()
        errors = []
        original_transaction = second._transaction
        @contextmanager
        def signal_transaction():
            attempted.set()
            with original_transaction():
                yield
        second._transaction = signal_transaction
        def writer():
            try:
                second.forget(self.f.echo, record.record_id, expected_revision=1, credential="owner-gesture", operation_id="concurrent-forget")
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()
        worker = threading.Thread(target=writer)
        original_receipt = self.store._record_receipt
        def at_final_write(request, receipt):
            worker.start()
            self.assertTrue(attempted.wait(1))
            self.assertFalse(done.wait(0.05))
            original_receipt(request, receipt)
        self.store._record_receipt = at_final_write
        try:
            self.store.record_outcome(self.f.echo, record.record_id, kind="actual_outcome", evidence_digest="b"*64, credential="owner-gesture", operation_id="outcome-race")
            self.assertTrue(done.wait(2))
            self.assertEqual(errors, [])
            self.assertEqual(self.store.db.execute("SELECT count(*) FROM memory_learning_event WHERE record_id=?", (record.record_id,)).fetchone()[0], 0)
        finally:
            if worker.ident is not None:
                worker.join(3)
            second.close()


if __name__ == "__main__":
    unittest.main()
