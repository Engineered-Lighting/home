from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest import mock
import urllib.request


MODULE_PATH = Path(__file__).parents[1] / "migrate_legacy_identity.py"
SPEC = importlib.util.spec_from_file_location("legacy_identity_migration", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = migration
SPEC.loader.exec_module(migration)


CHILD_ID = "11111111-1111-4111-8111-111111111111"
PARENT_ID = "22222222-2222-4222-8222-222222222222"
CANARIES = (
    "NOTES_CANARY",
    "FLAGS_CANARY",
    "HA_PERSON_CANARY",
    "HA_TRACKER_CANARY",
    "EDGE_DATA_CANARY",
    "PREFERENCE_VALUE_CANARY",
    "PREFERENCE_PROVENANCE_CANARY",
    "FACE_EVENT_CANARY",
    "FACE_CAMERA_CANARY",
    "DELETED_BEFORE_CANARY",
    "GENERATED_AFTER_CANARY",
    "PENDING_PAYLOAD_CANARY",
    "PENDING_ERROR_CANARY",
)


def create_legacy_db(path: Path, *, schema_version: int = 1, flagged: bool = False) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE identities (
            uuid TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            pronouns TEXT,
            relationship_type TEXT,
            relationship_subrole TEXT,
            notes TEXT,
            is_private INTEGER NOT NULL,
            is_silent INTEGER NOT NULL,
            is_ignored INTEGER NOT NULL,
            is_archived INTEGER NOT NULL,
            do_not_track INTEGER NOT NULL,
            auto_expire_at TEXT,
            ha_person_entity_id TEXT,
            ha_device_tracker_entity_id TEXT,
            flags_extra TEXT,
            version INTEGER NOT NULL,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE identity_aliases (
            id INTEGER PRIMARY KEY,
            identity_uuid TEXT NOT NULL,
            alias TEXT NOT NULL,
            alias_kind TEXT NOT NULL,
            created_at TEXT
        );
        CREATE TABLE enrollments (
            id INTEGER PRIMARY KEY,
            identity_uuid TEXT NOT NULL,
            frigate_person_name TEXT NOT NULL,
            quality REAL,
            sample_count INTEGER,
            created_at TEXT,
            retired_at TEXT
        );
        CREATE TABLE relationships (
            id INTEGER PRIMARY KEY,
            from_uuid TEXT NOT NULL,
            to_uuid TEXT NOT NULL,
            rel_type TEXT NOT NULL,
            status TEXT NOT NULL,
            edge_data TEXT,
            created_at TEXT,
            ended_at TEXT
        );
        CREATE TABLE preferences (
            id INTEGER PRIMARY KEY,
            identity_uuid TEXT,
            key TEXT,
            value TEXT,
            scope TEXT,
            source TEXT,
            confidence REAL,
            provenance TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE face_crops_meta (
            id INTEGER PRIMARY KEY,
            identity_uuid TEXT,
            frigate_event_id TEXT,
            source_camera TEXT,
            captured_at TEXT,
            score REAL,
            retention_until TEXT
        );
        CREATE TABLE change_log (
            id INTEGER PRIMARY KEY,
            before_json TEXT,
            after_json TEXT
        );
        CREATE TABLE pending_writes (
            id INTEGER PRIMARY KEY,
            payload TEXT,
            last_error TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)",
        (str(schema_version),),
    )
    connection.execute(
        "INSERT INTO identities VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            CHILD_ID,
            "Marcelo",
            "he/him",
            "me",
            None,
            "NOTES_CANARY",
            0,
            0,
            0,
            0,
            0,
            None,
            "HA_PERSON_CANARY",
            "HA_TRACKER_CANARY",
            "FLAGS_CANARY",
            3,
            "2025-01-01T00:00:00Z",
            "2025-01-01T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO identities VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            PARENT_ID,
            "Amelia",
            "she/her",
            "family_immediate",
            "parent",
            "NOTES_CANARY",
            int(flagged),
            0,
            0,
            0,
            int(flagged),
            "2030-01-01T00:00:00Z" if flagged else None,
            None,
            None,
            "FLAGS_CANARY",
            7,
            "2025-01-01T00:00:00Z",
            "2025-01-01T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO identity_aliases VALUES(1,?,?,?,'2025-01-01')",
        (PARENT_ID, "Mãe", "nickname"),
    )
    connection.execute(
        "INSERT INTO enrollments VALUES(1,?,?,0.99,99,'2025-01-01',NULL)",
        (PARENT_ID, "amelia_cluster"),
    )
    connection.execute(
        "INSERT INTO relationships VALUES(1,?,?,?,'active',?,'2025-01-01',NULL)",
        (PARENT_ID, CHILD_ID, "parent", "EDGE_DATA_CANARY"),
    )
    connection.execute(
        "INSERT INTO preferences VALUES(1,?,'generated','PREFERENCE_VALUE_CANARY',"
        "'{}','inferred',0.8,'PREFERENCE_PROVENANCE_CANARY','x','x')",
        (PARENT_ID,),
    )
    connection.execute(
        "INSERT INTO face_crops_meta VALUES(1,?,? ,?,'x',0.9,NULL)",
        (PARENT_ID, "FACE_EVENT_CANARY", "FACE_CAMERA_CANARY"),
    )
    connection.execute(
        "INSERT INTO change_log VALUES(1,?,?)",
        ("DELETED_BEFORE_CANARY", "GENERATED_AFTER_CANARY"),
    )
    connection.execute(
        "INSERT INTO pending_writes VALUES(1,?,?)",
        ("PENDING_PAYLOAD_CANARY", "PENDING_ERROR_CANARY"),
    )
    connection.commit()
    connection.close()


class FakeCore:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    def capabilities(self) -> frozenset[tuple[str, str]]:
        return frozenset(
            {
                ("post", migration.PERSON_ENDPOINT),
                ("post", migration.ROLE_ENDPOINT),
                ("post", migration.ALIAS_ENDPOINT_TEMPLATE),
                ("post", migration.RECOGNITION_BINDING_ENDPOINT_TEMPLATE),
                ("post", migration.PRIVACY_DIRECTIVE_ENDPOINT_TEMPLATE),
                ("post", migration.PERSON_STATUS_ENDPOINT_TEMPLATE),
                ("post", migration.RELATIONSHIP_CANDIDATE_ENDPOINT),
                ("post", "/v1/relationships/parent-confirmations"),
                ("field", "PersonCreate.legacy_source_sha256"),
                ("idempotency", "exact-projection-v1"),
            }
        )

    def require_shadow_rollout(self) -> None:
        return None

    def post(self, path: str, body: dict) -> dict:
        self.posts.append((path, dict(body)))
        return {"ok": True}


class ExactIdempotentCore(FakeCore):
    def __init__(self, *, crash_before_call: int | None = None) -> None:
        super().__init__()
        self._stored: dict[tuple[str, str], bytes] = {}
        self._call_count = 0
        self._crash_before_call = crash_before_call
        self._crashed = False

    @staticmethod
    def _key(path: str, body: dict) -> tuple[str, str]:
        if path == migration.PERSON_ENDPOINT:
            return (path, str(body["person_id"]))
        return (path, str(body["source_ref"]))

    def post(self, path: str, body: dict) -> dict:
        self._call_count += 1
        if (
            self._crash_before_call == self._call_count
            and not self._crashed
        ):
            self._crashed = True
            raise RuntimeError("synthetic process crash")
        key = self._key(path, body)
        projection = migration._canonical(body)
        existing = self._stored.get(key)
        if existing is not None and existing != projection:
            raise migration.CoreRequestError(409, f"POST {path}")
        self._stored[key] = projection
        self.posts.append((path, dict(body)))
        return {"replayed": existing is not None}


class LegacyIdentityMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "identity.sqlite"

    def test_prohibited_fields_and_tables_never_enter_plan_or_review(self) -> None:
        create_legacy_db(self.database)
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()

        plan = migration.load_plan(self.database)
        report = migration.review_report(plan)
        serialized = repr(plan) + json.dumps(report, sort_keys=True)

        for canary in CANARIES:
            self.assertNotIn(canary, serialized)
        self.assertEqual(
            hashlib.sha256(self.database.read_bytes()).hexdigest(), before
        )
        self.assertEqual(len(plan.people), 2)
        self.assertEqual(len(plan.aliases), 1)
        self.assertEqual(len(plan.external_bindings), 1)

    def test_sqlite_authorizer_denies_prohibited_table_reads(self) -> None:
        create_legacy_db(self.database)
        connection = migration._open_read_only(self.database)
        self.addCleanup(connection.close)
        migration._validate_schema(connection)
        migration._install_authorizer(connection)

        with self.assertRaises(sqlite3.DatabaseError):
            connection.execute("SELECT value FROM preferences").fetchall()
        with self.assertRaises(sqlite3.DatabaseError):
            connection.execute("SELECT before_json FROM change_log").fetchall()
        with self.assertRaises(sqlite3.DatabaseError):
            connection.execute("SELECT notes FROM identities").fetchall()
        with self.assertRaises(sqlite3.DatabaseError):
            connection.execute("SELECT edge_data FROM relationships").fetchall()

    def test_parent_labels_remain_non_authoritative_candidates(self) -> None:
        create_legacy_db(self.database)
        plan = migration.load_plan(self.database)
        core = FakeCore()

        preparation = migration.prepare_apply(plan, core.capabilities())
        person_operations = [
            operation
            for operation in preparation.operations
            if operation.kind == "person"
        ]
        self.assertTrue(person_operations)
        self.assertTrue(
            all(
                len(str(operation.payload["legacy_source_sha256"])) == 64
                for operation in person_operations
            )
        )
        paths = [operation.path for operation in preparation.operations]
        parent_roles = [
            role for role in plan.role_candidates if role.role_label == "parent"
        ]

        self.assertEqual(len(parent_roles), 1)
        self.assertEqual(plan.relationship_candidates[0].relationship_label, "parent")
        self.assertNotIn("/v1/relationships/parent-confirmations", paths)
        self.assertIn(migration.ROLE_ENDPOINT, paths)
        relationship_operations = [
            operation
            for operation in preparation.operations
            if operation.kind == "legacy_relationship_candidate"
        ]
        self.assertEqual(len(relationship_operations), 1)
        self.assertEqual(
            relationship_operations[0].review["authoritative_relationship"], False
        )
        self.assertEqual(relationship_operations[0].review["perspective"], "unknown")
        self.assertFalse(preparation.deferred_counts)

        result = migration.execute_apply(preparation, core)
        self.assertEqual(result.failed, 0)
        self.assertTrue(core.posts)
        self.assertNotIn(
            "/v1/relationships/parent-confirmations",
            [path for path, _body in core.posts],
        )

    def test_partial_crash_rerun_is_exactly_idempotent(self) -> None:
        create_legacy_db(self.database)
        plan = migration.load_plan(self.database)
        core = ExactIdempotentCore(crash_before_call=2)
        preparation = migration.prepare_apply(plan, core.capabilities())

        with self.assertRaisesRegex(RuntimeError, "synthetic process crash"):
            migration.execute_apply(preparation, core)

        result = migration.execute_apply(preparation, core)
        self.assertEqual(result.applied, len(preparation.operations))
        self.assertEqual(result.failed, 0)
        self.assertEqual(result.dependency_blocked, 0)
        self.assertEqual(len(core._stored), len(preparation.operations))

        first_person = next(
            operation
            for operation in preparation.operations
            if operation.kind == "person"
        )
        drifted = dict(first_person.payload)
        drifted["display_name"] = "drift-canary"
        with self.assertRaises(migration.CoreRequestError) as error:
            core.post(first_person.path, drifted)
        self.assertEqual(error.exception.status_code, 409)

    def test_apply_rejects_core_without_person_source_digest_contract(self) -> None:
        create_legacy_db(self.database)
        plan = migration.load_plan(self.database)
        with self.assertRaisesRegex(migration.MigrationError, "idempotency"):
            migration.prepare_apply(
                plan,
                frozenset({("post", migration.PERSON_ENDPOINT)}),
            )

    def test_privacy_sensitive_identity_uses_typed_directives(self) -> None:
        create_legacy_db(self.database, flagged=True)
        plan = migration.load_plan(self.database)
        preparation = migration.prepare_apply(plan, FakeCore().capabilities())

        person_ids = {
            operation.person_id
            for operation in preparation.operations
            if operation.kind == "person"
        }
        self.assertIn(PARENT_ID, person_ids)
        self.assertEqual(preparation.blocked_people, 0)
        parent_directives = [
            operation.payload["directive"]
            for operation in preparation.operations
            if operation.kind == "privacy_directive"
            and operation.person_id == PARENT_ID
        ]
        self.assertEqual(
            set(parent_directives), {"private", "do_not_track", "auto_expire"}
        )

    def test_ignored_identity_imports_only_suppressed_tombstone_and_directive(self) -> None:
        create_legacy_db(self.database)
        connection = sqlite3.connect(self.database)
        connection.execute(
            "UPDATE identities SET is_ignored = 1 WHERE uuid = ?", (PARENT_ID,)
        )
        connection.commit()
        connection.close()
        plan = migration.load_plan(self.database)
        preparation = migration.prepare_apply(plan, FakeCore().capabilities())

        parent_person = next(
            operation
            for operation in preparation.operations
            if operation.kind == "person" and operation.person_id == PARENT_ID
        )
        self.assertEqual(
            parent_person.payload["display_name"], "Suppressed legacy identity"
        )
        self.assertIsNone(parent_person.payload["pronouns"])
        self.assertTrue(
            any(
                operation.kind == "privacy_directive"
                and operation.person_id == PARENT_ID
                and operation.payload["directive"] == "ignored"
                for operation in preparation.operations
            )
        )
        self.assertFalse(
            any(
                operation.kind in {"alias", "recognition_binding", "legacy_role_candidate"}
                and operation.person_id == PARENT_ID
                for operation in preparation.operations
            )
        )

    def test_confirmation_output_is_minimal_review_not_full_legacy_content(self) -> None:
        create_legacy_db(self.database)
        plan = migration.load_plan(self.database)
        preparation = migration.prepare_apply(plan, FakeCore().capabilities())
        expected_inputs = iter(
            [
                f"APPLY LEGACY IDENTITY REVIEW {plan.digest}",
                *[
                    f"APPLY REVIEWED {operation.kind.upper()} {operation.candidate_id}"
                    for operation in preparation.operations
                ],
            ]
        )
        output: list[str] = []

        migration.collect_confirmations(
            plan,
            preparation,
            input_fn=lambda _prompt: next(expected_inputs),
            output_fn=output.append,
        )

        rendered = "\n".join(output)
        self.assertIn("Marcelo", rendered)
        self.assertIn("Amelia", rendered)
        self.assertIn(CHILD_ID, rendered)
        self.assertIn(PARENT_ID, rendered)
        self.assertIn("Mãe", rendered)
        self.assertIn("amelia_cluster", rendered)
        for canary in CANARIES:
            self.assertNotIn(canary, rendered)

    def test_review_artifact_is_minimized_and_mode_0600(self) -> None:
        create_legacy_db(self.database)
        plan = migration.load_plan(self.database)
        report = migration.review_report(plan)
        artifact = self.root / "private" / "review.json"

        if os.name == "nt":
            with self.assertRaisesRegex(migration.MigrationError, "POSIX"):
                migration.write_review_artifact(artifact, report)
            self.assertFalse(artifact.exists())
            return

        migration.write_review_artifact(artifact, report)
        text = artifact.read_text(encoding="utf-8")

        self.assertEqual(stat.S_IMODE(artifact.stat().st_mode), 0o600)
        self.assertNotIn("Marcelo", text)
        self.assertNotIn("Amelia", text)
        self.assertNotIn("Mãe", text)
        self.assertNotIn("amelia_cluster", text)
        for canary in CANARIES:
            self.assertNotIn(canary, text)
        with self.assertRaises(FileExistsError):
            migration.write_review_artifact(artifact, report)

    def test_unsupported_schema_version_fails_before_extraction(self) -> None:
        create_legacy_db(self.database, schema_version=99)
        with self.assertRaisesRegex(migration.MigrationError, "unsupported"):
            migration.load_plan(self.database)

    def test_active_legacy_journal_fails_closed(self) -> None:
        create_legacy_db(self.database)
        Path(f"{self.database}-wal").write_bytes(b"active-writer-canary")
        with self.assertRaisesRegex(migration.MigrationError, "active journal"):
            migration.load_plan(self.database)

    def test_core_token_destination_is_fixed_to_local_allowlist(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {"HOME_AGENT_MIGRATION_CORE_URL": "https://attacker.invalid"},
            ),
            mock.patch.object(migration, "_read_secret", return_value="not-used"),
            self.assertRaisesRegex(migration.MigrationError, "allowlisted"),
        ):
            migration.HttpCoreClient()

    def test_cli_rejects_all_arguments_before_prompting(self) -> None:
        with (
            mock.patch.object(sys, "argv", ["migrate", "sensitive-content"]),
            self.assertRaisesRegex(migration.MigrationError, "arguments"),
        ):
            migration.main()

    def test_counts_only_review_never_requires_rollout_or_core(self) -> None:
        create_legacy_db(self.database)
        core_client = mock.Mock(side_effect=AssertionError("Core must stay offline"))

        with (
            mock.patch.object(sys, "argv", ["migrate"]),
            mock.patch(
                "builtins.input",
                side_effect=[str(self.database), "n", "n"],
            ),
            mock.patch.object(migration, "HttpCoreClient", core_client),
        ):
            self.assertEqual(migration.main(), 0)

        core_client.assert_not_called()

    def test_apply_requires_exact_shadow_before_confirmations_or_writes(self) -> None:
        create_legacy_db(self.database)
        rejected = {
            "record_only": {
                **migration.EXPECTED_SHADOW_ROLLOUT_CONTRACT,
                "mode": "record_only",
                "semantic_people_writes": False,
            },
            "canary": {
                **migration.EXPECTED_SHADOW_ROLLOUT_CONTRACT,
                "mode": "canary",
                "persistent_memory_writes": True,
            },
            "missing_field": {
                "mode": "shadow",
                "source": "deployment_policy",
                "semantic_people_writes": True,
                "ingest_projection": True,
            },
            "extra_field": {
                **migration.EXPECTED_SHADOW_ROLLOUT_CONTRACT,
                "unexpected": False,
            },
            "non_boolean": {
                **migration.EXPECTED_SHADOW_ROLLOUT_CONTRACT,
                "mode": "shadow",
                "semantic_people_writes": 1,
                "persistent_memory_writes": 0,
            },
            "wrong_source": {
                **migration.EXPECTED_SHADOW_ROLLOUT_CONTRACT,
                "source": "client_claim",
            },
            "projection_disabled": {
                **migration.EXPECTED_SHADOW_ROLLOUT_CONTRACT,
                "ingest_projection": False,
            },
            "projection_integer": {
                **migration.EXPECTED_SHADOW_ROLLOUT_CONTRACT,
                "ingest_projection": 1,
            },
        }

        for label, response in rejected.items():
            with self.subTest(label=label):
                client = object.__new__(migration.HttpCoreClient)
                client._request = mock.Mock(return_value=response)
                confirmations = mock.Mock()
                execute = mock.Mock()
                with (
                    mock.patch.object(sys, "argv", ["migrate"]),
                    mock.patch(
                        "builtins.input",
                        side_effect=[str(self.database), "n", "y"],
                    ),
                    mock.patch.object(sys.stdin, "isatty", return_value=True),
                    mock.patch.object(sys.stdout, "isatty", return_value=True),
                    mock.patch.object(
                        migration,
                        "HttpCoreClient",
                        return_value=client,
                    ),
                    mock.patch.object(
                        migration,
                        "collect_confirmations",
                        confirmations,
                    ),
                    mock.patch.object(migration, "execute_apply", execute),
                    self.assertRaisesRegex(
                        migration.MigrationError,
                        "exact shadow rollout contract",
                    ),
                ):
                    migration.main()

                client._request.assert_called_once_with(
                    "GET", migration.ROLLOUT_ENDPOINT
                )
                confirmations.assert_not_called()
                execute.assert_not_called()

    def test_exact_shadow_rollout_contract_allows_apply_preparation(self) -> None:
        client = object.__new__(migration.HttpCoreClient)
        client._request = mock.Mock(
            return_value=dict(migration.EXPECTED_SHADOW_ROLLOUT_CONTRACT)
        )

        client.require_shadow_rollout()

        client._request.assert_called_once_with("GET", migration.ROLLOUT_ENDPOINT)

    def test_core_client_disables_environment_proxy_and_redirects(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {
                    "HOME_AGENT_MIGRATION_CORE_URL": "http://core-api:8104",
                    "HTTP_PROXY": "http://attacker.invalid:8080",
                },
            ),
            mock.patch.object(migration, "_read_secret", return_value="test-secret"),
        ):
            client = migration.HttpCoreClient()

        self.assertTrue(
            any(
                isinstance(handler, migration._NoRedirect)
                for handler in client._opener.handlers
            )
        )
        self.assertFalse(
            any(
                isinstance(handler, urllib.request.ProxyHandler)
                for handler in client._opener.handlers
            )
        )

    def test_migration_never_requests_or_stores_an_ha_user_identity(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("Authenticated operator HA user UUID", source)
        self.assertNotIn("self._ha_user_id", source)

    def test_capability_contract_is_fixed_and_does_not_use_public_openapi(self) -> None:
        client = object.__new__(migration.HttpCoreClient)
        client._request = mock.Mock(
            return_value=json.loads(
                json.dumps(migration.EXPECTED_CAPABILITY_CONTRACT)
            )
        )

        capabilities = client.capabilities()

        client._request.assert_called_once_with(
            "GET", migration.CAPABILITIES_ENDPOINT
        )
        self.assertIn(("post", migration.PERSON_ENDPOINT), capabilities)
        self.assertIn(("idempotency", "exact-projection-v1"), capabilities)

        drifted = json.loads(json.dumps(migration.EXPECTED_CAPABILITY_CONTRACT))
        drifted["person_import"]["idempotency"] = "unsafe"
        client._request = mock.Mock(return_value=drifted)
        with self.assertRaisesRegex(migration.MigrationError, "contract mismatch"):
            client.capabilities()


if __name__ == "__main__":
    unittest.main()
