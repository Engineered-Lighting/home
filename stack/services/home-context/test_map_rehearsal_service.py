"""Deterministic profile/terminal authority checks; no sockets or hosted services."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from journal import Journal, JournalError, canonical, digest
from schema_registry import SchemaRegistry, schema_digest


class MapRehearsalServiceTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[3]
        scratch = (root / ".tmp" / "map-rehearsal-service-tests").resolve()
        if root not in scratch.parents:
            raise RuntimeError("test scratch escaped workspace")
        scratch.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="home-map-service-unit-", dir=scratch)
        self.path = Path(self.directory.name) / "journal.sqlite"
        self.identity = dict(site_id="echo", room_id="living_room", session_id="unit-map-session", domain_epoch="unit-map-domain")
        self.config = dict(identity=self.identity, binding_digest="a" * 64, config_digest="b" * 64, registration_generation=1)
        families = ["home-engagement-state-3", "home-lighting-policy-decision-2", "home-map-rehearsal-control-decision-1"]
        self.grants = [dict(principal_id="unit-map-worker", producer_id="unit-map-worker", token_sha256=hashlib.sha256(b"unit-token").hexdigest(),
                            scopes=[dict(site_id="echo", room_id="living_room")], operations=["read", "worker_commit"],
                            payload_types=families, revisions=["1.0", "2.0", "3.0"], schema_pins={name:schema_digest(name) for name in families}, expected_worker_id="unit-map-worker")]
        self.journal = self.open()
        self.grant = self.journal.grants[0]

    def open(self, config=None, reopen=False):
        return Journal(str(self.path), grants=self.grants, profile="home-map-rehearsal-synthetic-1", clock=lambda:0,
                       map_rehearsal_config=config or self.config, map_rehearsal_reopen=reopen)

    def tearDown(self):
        self.journal.close()
        self.directory.cleanup()

    def wrong_scope_commit(self, name="wrong-session"):
        return dict(schema="home-map-rehearsal-worker-commit-1", version=1, **{**self.identity, "session_id":name},
                    id="commit:scope-negative", journal_epoch=self.journal._meta("epoch"), checkpoint={})

    def test_new_roots_refuse_old_profile_grants(self):
        family="home-map-rehearsal-binding-1"
        grant=SimpleNamespace(schema_pins={family:schema_digest(family)},payload_types=[family])
        for profile in ["legacy", "dynamic-book-synthetic-1"]:
            with self.subTest(profile=profile), self.assertRaisesRegex(ValueError,"isolated profile"):
                SchemaRegistry([grant],profile=profile)

    def test_scope_rejection_is_durable_and_immutable(self):
        body=self.wrong_scope_commit()
        with self.assertRaises(JournalError) as error:
            self.journal.dynamic.worker_commit(body,self.grant)
        self.assertEqual(error.exception.code,"scope_mismatch")
        terminal=self.journal.dynamic.lookup_commit(body["id"],self.grant,"echo","living_room")
        self.assertEqual(terminal["status"],"rejected")
        self.assertEqual(terminal["request_canonical_json"],canonical(body))
        self.assertEqual(terminal["request_digest"],digest(body))
        with self.assertRaises(JournalError) as changed:
            self.journal.dynamic.worker_commit(self.wrong_scope_commit("corrected-but-same-id"),self.grant)
        self.assertEqual(changed.exception.code,"identity_conflict")
        self.assertEqual(self.journal.db.execute("SELECT count(*) FROM workers").fetchone()[0],0)

    def test_rejection_reserve_rolls_back_when_not_funded(self):
        body=self.wrong_scope_commit()
        with patch.object(self.journal.dynamic,"_storage_budget",side_effect=JournalError("storage_budget","unit reserve exhausted",409)):
            with self.assertRaises(JournalError) as error:
                self.journal.dynamic.worker_commit(body,self.grant)
        self.assertEqual(error.exception.code,"storage_budget")
        terminal=self.journal.dynamic.lookup_commit(body["id"],self.grant,"echo","living_room")
        self.assertEqual(terminal["status"],"unknown")
        self.assertEqual(self.journal.db.execute("SELECT count(*) FROM map_commit_dispositions").fetchone()[0],0)

    def test_reopen_requires_exact_configuration_before_writes(self):
        self.journal.close()
        before=self.path.read_bytes()
        for changed in [{**self.config,"binding_digest":"c"*64},{**self.config,"identity":{**self.identity,"session_id":"replacement-session"}}]:
            with self.assertRaisesRegex(ValueError,"reopen configuration mismatch"):
                self.open(changed,reopen=True)
            self.assertEqual(self.path.read_bytes(),before)
        self.journal=self.open(reopen=True)
        self.assertEqual(self.journal._meta("profile"),"home-map-rehearsal-synthetic-1")
        self.assertEqual(self.journal._meta("map-rehearsal-config"),canonical(self.config))


if __name__=="__main__":unittest.main()
