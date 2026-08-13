from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import importlib.util
from pathlib import Path
import sqlite3
import sys
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "stack/home-agent-deploy/operator"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, OPERATOR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(OPERATOR))
try:
    migration = _load("e5x_migration", "migrate_legacy_identity.py")
    verifier = _load("e5x_verifier", "reviewed_identity_payload.py")
    compiler = _load("e5x_packet_compiler", "reviewed_identity_packet_compiler.py")
finally:
    sys.path.remove(str(OPERATOR))


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
MARCELO = "11111111-1111-4111-8111-111111111111"
AMELIA = "22222222-2222-4222-8222-222222222222"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE identities (
          uuid TEXT PRIMARY KEY, display_name TEXT NOT NULL, pronouns TEXT,
          relationship_type TEXT, relationship_subrole TEXT, notes TEXT,
          is_private INTEGER NOT NULL, is_silent INTEGER NOT NULL,
          is_ignored INTEGER NOT NULL, is_archived INTEGER NOT NULL,
          do_not_track INTEGER NOT NULL, auto_expire_at TEXT,
          ha_person_entity_id TEXT, ha_device_tracker_entity_id TEXT,
          flags_extra TEXT, version INTEGER NOT NULL, created_at TEXT,
          updated_at TEXT
        );
        CREATE TABLE identity_aliases (
          id INTEGER PRIMARY KEY, identity_uuid TEXT NOT NULL,
          alias TEXT NOT NULL, alias_kind TEXT NOT NULL, created_at TEXT
        );
        CREATE TABLE enrollments (
          id INTEGER PRIMARY KEY, identity_uuid TEXT NOT NULL,
          frigate_person_name TEXT NOT NULL, quality REAL,
          sample_count INTEGER, created_at TEXT, retired_at TEXT
        );
        CREATE TABLE relationships (
          id INTEGER PRIMARY KEY, from_uuid TEXT NOT NULL,
          to_uuid TEXT NOT NULL, rel_type TEXT NOT NULL,
          status TEXT NOT NULL, edge_data TEXT, created_at TEXT, ended_at TEXT
        );
        CREATE TABLE preferences (id INTEGER PRIMARY KEY);
        CREATE TABLE face_crops_meta (id INTEGER PRIMARY KEY);
        CREATE TABLE change_log (id INTEGER PRIMARY KEY);
        CREATE TABLE pending_writes (id INTEGER PRIMARY KEY);
        INSERT INTO schema_meta VALUES ('schema_version','1');
        """
    )
    person_sql = "INSERT INTO identities VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    connection.execute(
        person_sql,
        (
            MARCELO,
            "Marcelo",
            "he/him",
            "me",
            None,
            "not selected",
            1,
            0,
            0,
            0,
            0,
            None,
            None,
            None,
            None,
            3,
            "2026-01-01",
            "2026-01-01",
        ),
    )
    connection.execute(
        person_sql,
        (
            AMELIA,
            "Amelia",
            "she/her",
            "family_immediate",
            "parent",
            "not selected",
            1,
            0,
            0,
            0,
            0,
            None,
            None,
            None,
            None,
            7,
            "2026-01-01",
            "2026-01-01",
        ),
    )
    connection.execute(
        "INSERT INTO identity_aliases VALUES(1,?,?,?,'2026-01-01')",
        (AMELIA, "Mom", "nickname"),
    )
    connection.execute(
        "INSERT INTO identity_aliases VALUES(2,?,?,?,'2026-01-01')",
        (AMELIA, "amelia_cluster", "frigate_name"),
    )
    connection.execute(
        "INSERT INTO enrollments VALUES(1,?,?,0.9,8,'2026-01-01',NULL)",
        (AMELIA, "amelia_cluster"),
    )
    connection.execute(
        "INSERT INTO relationships VALUES(1,?,?,?,'active','{}','x',NULL)",
        (AMELIA, MARCELO, "parent"),
    )
    connection.commit()
    connection.close()


class _Ids:
    def __init__(self) -> None:
        self.value = 1

    def __call__(self) -> uuid.UUID:
        result = uuid.UUID(f"00000000-0000-7000-8000-{self.value:012x}")
        self.value += 1
        return result


def _policy():
    review_private = Ed25519PrivateKey.generate()
    final_private = Ed25519PrivateKey.generate()

    def public_details(private):
        public = private.public_key()
        raw = public.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return public, hashlib.sha256(raw).hexdigest()

    review_public, review_fingerprint = public_details(review_private)
    final_public, final_fingerprint = public_details(final_private)
    key = bytes(range(32))
    policy = verifier.VerificationPolicy(
        review_public_key=review_public,
        review_key_fingerprint=review_fingerprint,
        finalization_public_key=final_public,
        finalization_key_fingerprint=final_fingerprint,
        commitment_key=key,
        commitment_key_epoch=1,
        commitment_key_fingerprint=hashlib.sha256(key).hexdigest(),
        policy_version="home-agent-mvp-v1",
        policy_digest=_digest("policy"),
        source_projection_contract_digest=_digest("source"),
        release_manifest_digest=_digest("release"),
        migration_tool_bundle_digest=_digest("tool"),
        core_oci_manifest_digest=_digest("oci"),
        core_schema_digest=_digest("schema"),
        core_capability_digest=_digest("capability"),
        shadow_authorization_id=uuid.UUID("00000000-0000-7000-8000-000000000999"),
        now=lambda: NOW,
    )
    return policy, review_private, final_private


def test_compiler_covers_every_source_row_and_both_signature_purposes(
    tmp_path,
) -> None:
    database = tmp_path / "identity.db"
    _create_database(database)
    plan = migration.load_plan(database)
    inventory = migration.load_source_inventory(database)
    policy, review_private, final_private = _policy()
    packet = compiler.compile_unsigned_packet(
        plan,
        inventory,
        private_review_sha256="f" * 64,
        policy=policy,
        id_factory=_Ids(),
        now=NOW,
    )
    restored = compiler.restore_unsigned_packet(
        packet.private_state(), review_public_key=policy.review_public_key
    )
    assert restored.review_signing_payload == packet.review_signing_payload
    assert restored.source_records == packet.source_records
    review_signature = review_private.sign(packet.review_signing_payload).hex()
    verified = compiler.verify_assembled_packet(
        packet,
        review_signature,
        policy=policy,
    )
    bundle = verifier.parse_canonical_json(
        packet.assemble_reviewed_bundle(review_signature)
    )

    assert bundle["manifest"]["run"]["source_item_count"] == len(inventory)
    assert len(packet.source_records) == len(inventory)
    omissions = [
        decision
        for decision in bundle["manifest"]["decisions"]
        if decision["decision_kind"] == "explicit_omission"
    ]
    assert {item["disposition"] for item in omissions} == {"out_of_scope_by_rule"}
    assert len(omissions) == 2  # schema_meta plus the frigate_name alias row
    serialized = bundle.__repr__()
    assert "parent_of" not in serialized
    assert "not selected" not in serialized

    proposal = verified.finalization_proposal
    envelope = verifier.canonical_bytes(
        {
            "contract_version": "reviewed-identity-finalization-envelope-v1",
            "finalization_id": str(verified.run_id),
            "finalization_commitment": proposal["finalization_commitment"],
            "signature_algorithm": "ed25519",
            "signing_key_fingerprint": policy.finalization_key_fingerprint,
            "signature_scope": "reviewed-identity-migration-finalization-v1",
            "finalization_signature": final_private.sign(
                verified.finalization_signing_payload(now=NOW)
            ).hex(),
        }
    )
    final = verifier.verify_finalization_envelope(
        verified,
        envelope,
        policy=policy,
    )
    assert final.document["authoritative"] is False
    assert final.document["finalization_attestation"]["signature_scope"] == (
        "reviewed-identity-migration-finalization-v1"
    )

    tampered = packet.private_state()
    tampered["unsigned_run"]["policy_digest"] = "0" * 64
    try:
        compiler.restore_unsigned_packet(
            tampered, review_public_key=policy.review_public_key
        )
    except compiler.PacketCompilerError as error:
        assert "commitment mismatch" in str(error)
    else:
        raise AssertionError("tampered signing state was restored")

    review_binding_tampered = packet.private_state()
    review_binding_tampered["private_review_sha256"] = "e" * 64
    try:
        compiler.restore_unsigned_packet(
            review_binding_tampered, review_public_key=policy.review_public_key
        )
    except compiler.PacketCompilerError as error:
        assert "review binding" in str(error)
    else:
        raise AssertionError("tampered private review binding was restored")


def test_inventory_digest_detects_unselected_source_value_change(tmp_path) -> None:
    database = tmp_path / "identity.db"
    _create_database(database)
    first = migration.load_source_inventory(database)
    connection = sqlite3.connect(database)
    connection.execute("UPDATE identity_aliases SET alias='Changed' WHERE id=2")
    connection.commit()
    connection.close()
    second = migration.load_source_inventory(database)
    first_changed = next(row for row in first if row.row_key == {"id": 2})
    second_changed = next(row for row in second if row.row_key == {"id": 2})
    assert first_changed.row_key == second_changed.row_key
    assert first_changed.values != second_changed.values
