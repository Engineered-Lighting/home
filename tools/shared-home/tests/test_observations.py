"""Workstation-only SQLite/source contract tests; no production reads or network."""
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from home_shared.authority import Conflict, Denied
from home_shared.memory import AuthorizedRetriever, SourceRegistration
from home_shared.observations import (
    SCHEMA, RETENTION_SECONDS, DeletionLedger, ObservationInput, ObservationProvenance, ReadOnlyObservationSource,
    SourceBinding, Tombstone, VerifiedRestoreReceipt, VictoriaObservationStore,
)
from test_authority import AuthorityFixture


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class FixtureRestoreVerifier:
    """Trusted fixture holds the latest external ledger and signed backup hash."""

    def __init__(self, backup, ledger, now):
        self.backup = backup
        self.ledger = ledger
        self.now = now

    def verify_restore(self, request, evidence):
        if (evidence != "verified-maintenance-receipt"
                or request.backup_sha256 != self.backup.sha256
                or request.binding_digest != self.backup.binding_digest
                or request.ledger_sha256 != self.ledger.digest
                or request.deletion_checkpoint != self.ledger.checkpoint):
            raise Denied("not the authenticated latest deletion ledger and exact backup")
        return VerifiedRestoreReceipt("fixture-proof", digest(asdict(request)), self.now[0], self.now[0]+30)


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = [1000.0]
        self.binding = SourceBinding("existing-governed-owner", "den-observations", {"den_oak": "den"})
        self.store = VictoriaObservationStore(self.root/"observations.sqlite", self.binding, clock=lambda: self.now[0])
        self.others = []

    def tearDown(self):
        for store in self.others:
            store.close()
        self.store.close()
        self.temp.cleanup()

    def value(self, frame="1", **overrides):
        fields = dict(site_id="victoria", source_instance="den-observations", camera_id="den_oak", room_id="den",
            producer_session="adapter-session", producer_sequence=1, occupancy_generation=1,
            capture_session="camera-session", frame_id=frame, request_id="request-"+frame,
            observed_at=self.now[0]-1, received_at=self.now[0], description="Two people in the den.",
            observations=({"label": "person", "count": 2},))
        fields.update(overrides)
        return ObservationInput(**fields)

    def restore(self, backup):
        restored = VictoriaObservationStore.restore_from_backup(backup.path, self.root/f"restored-{len(self.others)}.sqlite", self.binding, clock=lambda: self.now[0])
        self.others.append(restored)
        return restored

    def test_stable_namespaced_identity_and_identical_replay(self):
        value = self.value()
        first = self.store.ingest(value)
        self.assertTrue(first.record_id.startswith("victoria/den-observations/"))
        self.assertEqual(first, self.store.ingest(value))
        self.assertEqual(len(self.store.retrieve("people")), 1)
        with self.assertRaises(Conflict):
            self.store.ingest(replace(value, description="Conflicting caption"))
        with self.assertRaises(Conflict):
            self.store.ingest(self.value("2"), expected_revision=1)

    def provenance(self, **changes):
        value = dict(model_revision="model/revision-1", configuration_revision="config-1",
                     clock_mapping_revision="validated-clock-1", uncertainty_seconds=.3,
                     scheduled_at=998.0, request_deadline=1013.0, valid_until=1179.0)
        value.update(changes)
        return ObservationProvenance(**value)

    def test_existing_v1_payload_reads_unknown_and_keeps_original_replay_digest(self):
        value = self.value()
        record = self.store.ingest(value)
        # Model the exact persisted v1 shape from before this additive field.
        payload = asdict(record)
        payload["observation"].pop("provenance")
        old_input = asdict(value)
        old_input.pop("provenance")
        old_digest = digest({"input": old_input, "parents": ()})
        self.store.db.execute("UPDATE observation_record SET payload=?,input_digest=? WHERE record_id=?",
                              (json.dumps(payload), old_digest, record.record_id))
        self.store.db.execute("UPDATE observation_revision SET payload=? WHERE record_id=?",
                              (json.dumps(payload), record.record_id))
        decoded = self.store.read(record.record_id)
        self.assertIsNone(decoded.observation.provenance)
        self.assertEqual(decoded.revision, 1)
        self.assertEqual(self.store.ingest(value), decoded)
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(self.store.deletion_ledger().checkpoint, 0)
        revised = self.store.revise(record.record_id, expected_revision=1, description="Corrected legacy caption")
        self.assertIsNone(revised.observation.provenance)

    def test_versioned_provenance_roundtrips_and_corrections_cannot_rewrite_it(self):
        value = self.value(provenance=self.provenance())
        record = self.store.ingest(value)
        self.assertEqual(self.store.read(record.record_id).observation.provenance, value.provenance)
        self.assertEqual(self.store.ingest(value), record)
        with self.assertRaises(Conflict):
            self.store.ingest(replace(value, provenance=self.provenance(model_revision="other-model")))
        revised = self.store.revise(record.record_id, expected_revision=1, description="Corrected source caption")
        self.assertEqual(revised.observation.provenance, value.provenance)
        self.assertEqual(self.store.read(record.record_id).observation.provenance, value.provenance)
        self.assertEqual((revised.record_id, revised.expires_at), (record.record_id, record.expires_at))

    def test_provenance_backup_restore_and_later_tombstones_preserved(self):
        survivor = self.store.ingest(self.value("survivor", provenance=self.provenance()))
        forgotten = self.store.ingest(self.value("forgotten", provenance=self.provenance()))
        legacy = self.store.ingest(self.value("legacy"))
        backup = self.store.backup(self.root/"provenance-backup.sqlite")
        self.store.forget(forgotten.record_id, expected_revision=1)
        ledger = self.store.deletion_ledger()
        restored = self.restore(backup)
        restored.replay_deletion_ledger(ledger, evidence="verified-maintenance-receipt",
                                       verifier=FixtureRestoreVerifier(backup, ledger, self.now))
        self.assertEqual(restored.read(survivor.record_id), survivor)
        self.assertIsNone(restored.read(legacy.record_id).observation.provenance)
        with self.assertRaises(Denied):
            restored.read(forgotten.record_id)
        self.assertEqual(restored.deletion_ledger(), ledger)

    def test_unknown_provenance_is_not_fabricated_and_invalid_values_fail_closed(self):
        unknown = self.store.ingest(self.value(provenance=ObservationProvenance()))
        self.assertEqual(unknown.observation.provenance, ObservationProvenance())
        for changes in ({"version": 2}, {"version": True}, {"model_revision": ""},
                        {"configuration_revision": "x"*513}, {"clock_mapping_revision": "bad\nmap"},
                        {"uncertainty_seconds": 1.1}, {"uncertainty_seconds": float("nan")},
                        {"scheduled_at": True}, {"request_deadline": 1014}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.provenance(**changes)
        for changes in ({"valid_until": 1180}, {"scheduled_at": 985, "request_deadline": 1000}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.ingest(self.value("invalid", provenance=self.provenance(**changes)))
        with self.assertRaises(ValueError):
            self.store.ingest(self.value("untyped", provenance={"version": 1}))

    def test_delimiters_cannot_collide_in_frame_identity(self):
        first = self.store.ingest(self.value("c", capture_session="a:b"))
        second = self.store.ingest(self.value("b:c", capture_session="a"))
        self.assertNotEqual(first.record_id, second.record_id)

    def test_site_source_camera_room_and_owner_binding_fail_closed(self):
        for change in ({"site_id": "echo"}, {"source_instance": "other"}, {"camera_id": "unknown"}, {"room_id": "bedroom"}):
            with self.assertRaises(Denied):
                self.store.ingest(self.value(**change))
        with self.assertRaises(ValueError):
            SourceBinding("owner", "source", {"camera": "room"}, site_id="echo")
        with self.assertRaises(Denied):
            VictoriaObservationStore(self.root/"observations.sqlite", replace(self.binding, owner_id="different-owner"))

    def test_metadata_and_content_are_bounded(self):
        for change in ({"producer_sequence": True}, {"occupancy_generation": -1}, {"frame_id": "x"*129},
                       {"description": "x"*4097}, {"observed_at": float("nan")},
                       {"received_at": 1003}, {"observations": ({"huge": "x"*65536},)},
                       {"observations": ({"nonfinite": float("inf")},)}):
            with self.assertRaises((ValueError, Denied)):
                self.store.ingest(self.value(**change))

    def test_revision_preserves_identity_evidence_and_retention_anchor(self):
        first = self.store.ingest(self.value())
        self.now[0] += 10
        second = self.store.revise(first.record_id, expected_revision=1, description="One person in the den.")
        self.assertEqual((second.record_id, second.expires_at, second.ingested_at), (first.record_id, first.expires_at, first.ingested_at))
        self.assertEqual(second.revision, 2)
        with self.assertRaises(Conflict):
            self.store.revise(first.record_id, expected_revision=1, description="Stale correction")
        with self.assertRaises(Conflict):
            self.store.forget(first.record_id, expected_revision=True)
        self.assertEqual(self.store.read(first.record_id), second)

    def test_parent_correction_invalidates_all_descendants(self):
        parent = self.store.ingest(self.value("parent"))
        child = self.store.ingest(self.value("child"), derives_from=(parent.record_id,))
        grandchild = self.store.ingest(self.value("grandchild"), derives_from=(child.record_id,))
        self.assertEqual(child.evidence[0].revision, parent.revision)
        self.store.revise(parent.record_id, expected_revision=1, description="Corrected source caption")
        for value in (child, grandchild):
            with self.assertRaises(Denied):
                self.store.read(value.record_id)
            self.assertEqual(self.store.db.execute("SELECT payload FROM observation_record WHERE record_id=?", (value.record_id,)).fetchone()[0], "{}")
        self.assertEqual(len(self.store.retrieve("Corrected")), 1)

    def test_independent_retention_read_expiry_and_bounded_janitor(self):
        first = self.store.ingest(self.value("first"))
        second = self.store.ingest(self.value("second"))
        self.now[0] = first.expires_at
        with self.assertRaises(Denied):
            self.store.read(first.record_id)
        self.assertEqual(self.store.retrieve("people"), ())
        self.assertEqual(self.store.prune(batch_size=1), 1)
        self.assertEqual(self.store.prune(batch_size=1), 1)
        self.assertEqual(self.store.prune(), 0)
        ledger = self.store.deletion_ledger()
        self.assertEqual(ledger.checkpoint, 2)
        self.assertEqual({entry.reason for entry in ledger.entries}, {"retention_expired"})
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM observation_revision WHERE payload IS NOT NULL").fetchone()[0], 0)

    def test_derived_retention_does_not_extend_parent(self):
        parent = self.store.ingest(self.value("parent"))
        self.now[0] += 100
        child = self.store.ingest(self.value("child"), derives_from=(parent.record_id,))
        self.assertEqual(child.expires_at, parent.expires_at)

    def test_read_expiry_cannot_revive_on_clock_rollback_even_before_pruning(self):
        record = self.store.ingest(self.value())
        self.now[0] = record.expires_at
        with self.assertRaises(Denied):
            self.store.read(record.record_id)
        self.now[0] = 1000
        with self.assertRaises(Denied):
            self.store.read(record.record_id)
        self.store.close()
        self.store = VictoriaObservationStore(self.root/"observations.sqlite", self.binding, clock=lambda: self.now[0])
        with self.assertRaises(Denied):
            self.store.read(record.record_id)
        self.assertEqual(self.store.prune(), 1)

    def test_forget_is_durable_and_prevents_identical_recreation(self):
        value = self.value()
        first = self.store.ingest(value)
        child = self.store.ingest(self.value("child"), derives_from=(first.record_id,))
        self.store.forget(first.record_id, expected_revision=1)
        for record_id in (first.record_id, child.record_id):
            with self.assertRaises(Denied):
                self.store.read(record_id)
        self.store.close()
        self.store = VictoriaObservationStore(self.root/"observations.sqlite", self.binding, clock=lambda: self.now[0])
        with self.assertRaises(Denied):
            self.store.ingest(value)
        self.assertEqual(self.store.deletion_ledger().checkpoint, 1)

    def test_sqlite_backup_contains_wal_and_never_overwrites_artifact(self):
        first = self.store.ingest(self.value())
        backup = self.store.backup(self.root/"backup.sqlite")
        self.assertEqual(backup.record_count, 1)
        self.assertEqual(backup.sha256, hashlib.sha256(Path(backup.path).read_bytes()).hexdigest())
        copied = sqlite3.connect(backup.path)
        try:
            self.assertEqual(copied.execute("SELECT record_id FROM observation_record").fetchone()[0], first.record_id)
            self.assertEqual(copied.execute("PRAGMA quick_check").fetchone()[0], "ok")
        finally:
            copied.close()
        with self.assertRaises(FileExistsError):
            self.store.backup(backup.path)

    def test_restored_data_stays_closed_across_restart_without_verified_replay(self):
        first = self.store.ingest(self.value())
        backup = self.store.backup(self.root/"backup.sqlite")
        restored = self.restore(backup)
        ledger = self.store.deletion_ledger()
        with self.assertRaises(Denied):
            restored.read(first.record_id)
        with self.assertRaises(Denied):
            restored.ingest(self.value("new"))
        with self.assertRaises(Denied):
            restored.replay_deletion_ledger(ledger, evidence=True)
        path = restored.path
        restored.close()
        self.others.pop()
        restored = VictoriaObservationStore(path, self.binding, clock=lambda: self.now[0])
        self.others.append(restored)
        with self.assertRaises(Denied):
            restored.retrieve("people")

    def test_restore_crash_after_copy_never_publishes_ungated_destination(self):
        record = self.store.ingest(self.value())
        backup = self.store.backup(self.root/"backup.sqlite")
        self.store.forget(record.record_id, expected_revision=1)
        target = self.root/"crashed-restore.sqlite"
        # sqlite3.connect occurs after the copy, before the durable gate write.
        with patch("home_shared.observations.sqlite3.connect", side_effect=RuntimeError("injected restore interruption")):
            with self.assertRaises(RuntimeError):
                VictoriaObservationStore.restore_from_backup(backup.path, target, self.binding)
        self.assertFalse(target.exists())
        fresh = VictoriaObservationStore(target, self.binding, clock=lambda: self.now[0])
        try:
            with self.assertRaises(KeyError):
                fresh.read(record.record_id)
        finally:
            fresh.close()

    def test_unpublished_staging_artifact_cannot_be_opened_as_a_store(self):
        staging = self.root/".observation-restore-leftover.sqlite"
        self.store.backup(staging)
        with self.assertRaises(Denied):
            VictoriaObservationStore(staging, self.binding)

    def test_restore_publication_never_overwrites_an_existing_file(self):
        self.store.ingest(self.value())
        backup = self.store.backup(self.root/"backup.sqlite")
        target = self.root/"preserved.sqlite"
        target.write_bytes(b"existing artifact")
        with self.assertRaises(FileExistsError):
            VictoriaObservationStore.restore_from_backup(backup.path, target, self.binding)
        self.assertEqual(target.read_bytes(), b"existing artifact")

    def test_verified_restore_applies_later_deletion_and_descendant_invalidation(self):
        parent = self.store.ingest(self.value("parent"))
        child = self.store.ingest(self.value("child"), derives_from=(parent.record_id,))
        survivor = self.store.ingest(self.value("survivor"))
        backup = self.store.backup(self.root/"backup.sqlite")
        self.store.forget(parent.record_id, expected_revision=1)
        ledger = self.store.deletion_ledger()
        restored = self.restore(backup)
        verifier = FixtureRestoreVerifier(backup, ledger, self.now)
        with self.assertRaises(Denied):
            restored.replay_deletion_ledger(ledger, evidence="caller-says-complete", verifier=verifier)
        restored.replay_deletion_ledger(ledger, evidence="verified-maintenance-receipt", verifier=verifier)
        for record in (parent, child):
            with self.assertRaises(Denied):
                restored.read(record.record_id)
        self.assertEqual(restored.read(survivor.record_id), survivor)
        with self.assertRaises(Denied):
            restored.replay_deletion_ledger(ledger, evidence="verified-maintenance-receipt", verifier=verifier)

    def test_replay_rejects_stale_missing_wrong_source_and_gapped_ledgers(self):
        first = self.store.ingest(self.value("first"))
        second = self.store.ingest(self.value("second"))
        self.store.forget(first.record_id, expected_revision=1)
        backup = self.store.backup(self.root/"backup.sqlite")
        self.store.forget(second.record_id, expected_revision=1)
        ledger = self.store.deletion_ledger()
        verifier = FixtureRestoreVerifier(backup, ledger, self.now)
        restored = self.restore(backup)
        invalid = (
            DeletionLedger(ledger.binding_digest, 0, ()),
            replace(ledger, entries=(ledger.entries[1],)),
            replace(ledger, binding_digest="0"*64),
            replace(ledger, entries=(ledger.entries[0], replace(ledger.entries[1], sequence=3))),
            replace(ledger, entries=(ledger.entries[0], replace(ledger.entries[1], revision=1))),
        )
        for candidate in invalid:
            with self.assertRaises((Denied, ValueError)):
                restored.replay_deletion_ledger(candidate, evidence="verified-maintenance-receipt", verifier=verifier)
        with self.assertRaises(Denied):
            restored.read(second.record_id)
        restored.replay_deletion_ledger(ledger, evidence="verified-maintenance-receipt", verifier=verifier)
        self.assertEqual(restored.retrieve("people"), ())

    def test_restore_receipt_is_bound_to_exact_backup_and_challenge(self):
        self.store.ingest(self.value("first"))
        backup = self.store.backup(self.root/"backup.sqlite")
        ledger = self.store.deletion_ledger()
        restored = self.restore(backup)
        class Forged:
            def verify_restore(inner, request, evidence):
                return VerifiedRestoreReceipt("forged", "0"*64, 1000, 1100)
        with self.assertRaises(Denied):
            restored.replay_deletion_ledger(ledger, verifier=Forged())
        other = replace(backup, sha256="0"*64)
        with self.assertRaises(Denied):
            restored.replay_deletion_ledger(ledger, evidence="verified-maintenance-receipt", verifier=FixtureRestoreVerifier(other, ledger, self.now))

    def test_no_unrelated_existing_database_is_adopted(self):
        path = self.root/"unrelated.sqlite"
        other = sqlite3.connect(path)
        other.execute("CREATE TABLE unrelated(id INTEGER)")
        other.close()
        with self.assertRaises(ValueError):
            VictoriaObservationStore(path, self.binding)
        other = sqlite3.connect(path)
        try:
            self.assertEqual(other.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("unrelated",)])
        finally:
            other.close()

    def test_read_only_source_adapter_requires_registered_grants_and_rechecks_unlink(self):
        fixture = AuthorityFixture()
        try:
            first = self.store.ingest(self.value())
            source = ReadOnlyObservationSource(self.store)
            self.assertFalse(hasattr(source, "revise"))
            retriever = AuthorizedRetriever(fixture.registry)
            retriever.register(SourceRegistration("victoria", "den-observations", SCHEMA, "den-observations"), source)
            with self.assertRaises(Denied):
                retriever.retrieve(fixture.echo, "victoria", "den-observations", "people")
            fixture.grant("victoria", "den-observations", "memory.read")
            records = retriever.retrieve(fixture.echo, "victoria", "den-observations", "people")
            self.assertEqual(records[0].record_id, first.record_id)
            self.assertEqual((records[0].camera_id, records[0].room_id), ("den_oak", "den"))
            fixture.registry.unlink(fixture.echo, "victoria")
            with self.assertRaises(Denied):
                retriever.retrieve(fixture.echo, "victoria", "den-observations", "people")
        finally:
            fixture.close()


if __name__ == "__main__":
    unittest.main()
