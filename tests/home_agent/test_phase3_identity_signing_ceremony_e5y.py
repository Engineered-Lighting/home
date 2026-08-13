from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import importlib.util
from pathlib import Path
import sqlite3
import sys
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest


ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "stack/home-agent-deploy/operator"
LAUNCHER = OPERATOR / "phase3_identity_signing.sh"
INSTALLER = ROOT / "stack/home-agent-deploy/install-phase3-identity-signing.sh"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, OPERATOR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(OPERATOR))
try:
    migration = _load("signing_migration", "migrate_legacy_identity.py")
    verifier = _load("reviewed_identity_payload", "reviewed_identity_payload.py")
    compiler = _load(
        "reviewed_identity_packet_compiler", "reviewed_identity_packet_compiler.py"
    )
    ceremony = _load(
        "phase3_identity_signing_ceremony",
        "phase3_identity_signing_ceremony.py",
    )
    writer_freeze = _load(
        "phase3_writer_freeze_evidence",
        "phase3_writer_freeze_evidence.py",
    )
    privacy_evidence = _load(
        "phase3_privacy_cutover_evidence",
        "phase3_privacy_cutover_evidence.py",
    )
    semantic_cutover = _load(
        "phase3_semantic_cutover_packet",
        "phase3_semantic_cutover_packet.py",
    )
finally:
    sys.path.remove(str(OPERATOR))


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
MARCELO = "11111111-1111-4111-8111-111111111111"
AMELIA = "22222222-2222-4222-8222-222222222222"


class _Ids:
    def __init__(self) -> None:
        self.value = 1

    def __call__(self) -> uuid.UUID:
        result = uuid.UUID(f"00000000-0000-7000-8000-{self.value:012x}")
        self.value += 1
        return result


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _public(private: Ed25519PrivateKey):
    public = private.public_key()
    raw = public.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return public, hashlib.sha256(raw).hexdigest()


def _policy(
    review_private: Ed25519PrivateKey,
    finalization_private: Ed25519PrivateKey,
    *,
    now: datetime = NOW,
):
    review_public, review_fingerprint = _public(review_private)
    finalization_public, finalization_fingerprint = _public(finalization_private)
    commitment_key = bytes(range(32))
    return verifier.VerificationPolicy(
        review_public_key=review_public,
        review_key_fingerprint=review_fingerprint,
        finalization_public_key=finalization_public,
        finalization_key_fingerprint=finalization_fingerprint,
        commitment_key=commitment_key,
        commitment_key_epoch=1,
        commitment_key_fingerprint=hashlib.sha256(commitment_key).hexdigest(),
        policy_version="home-agent-mvp-v1",
        policy_digest=_digest("policy"),
        source_projection_contract_digest=_digest("source"),
        release_manifest_digest=_digest("release"),
        migration_tool_bundle_digest=_digest("tool"),
        core_oci_manifest_digest=_digest("oci"),
        core_schema_digest=_digest("schema"),
        core_capability_digest=_digest("capability"),
        shadow_authorization_id=uuid.UUID("00000000-0000-7000-8000-000000000999"),
        now=lambda: now,
    )


def _database(path: Path) -> None:
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
            None,
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
            None,
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
        "INSERT INTO enrollments VALUES(1,?,?,0.9,8,'2026-01-01',NULL)",
        (AMELIA, "amelia_cluster"),
    )
    connection.execute(
        "INSERT INTO relationships VALUES(1,?,?,?,'active','{}','x',NULL)",
        (AMELIA, MARCELO, "parent"),
    )
    connection.commit()
    connection.close()


def test_distinct_review_and_finalization_ceremony_is_resumable(tmp_path) -> None:
    database = tmp_path / "identity.db"
    _database(database)
    review_private = Ed25519PrivateKey.generate()
    finalization_private = Ed25519PrivateKey.generate()
    policy = _policy(review_private, finalization_private)
    packet = compiler.compile_unsigned_packet(
        migration.load_plan(database),
        migration.load_source_inventory(database),
        private_review_sha256="f" * 64,
        policy=policy,
        id_factory=_Ids(),
        now=NOW,
    )
    staged = ceremony.stage_state(packet, policy=policy)
    assert staged["phase"] == "staged"
    assert staged["review_signature"] is None

    review_token = ceremony.review_approval_token(staged, policy)
    assert review_token.startswith("REVIEW ")
    with pytest.raises(ceremony.SigningCeremonyError, match="did not match"):
        ceremony.approve_review(
            staged,
            policy=policy,
            review_private_key=review_private,
            approval="REVIEW " + "0" * 64,
        )
    reviewed = ceremony.approve_review(
        staged,
        policy=policy,
        review_private_key=review_private,
        approval=review_token,
    )
    assert reviewed["phase"] == "review_signed"
    assert reviewed["finalization_envelope"] is None

    finalization_token = ceremony.finalization_approval_token(reviewed, policy, now=NOW)
    assert finalization_token.startswith("FINALIZE ")
    assert finalization_token.removeprefix("FINALIZE ") != review_token.removeprefix(
        "REVIEW "
    )
    with pytest.raises(ceremony.SigningCeremonyError, match="does not match policy"):
        ceremony.approve_finalization(
            reviewed,
            policy=policy,
            finalization_private_key=review_private,
            approval=finalization_token,
            now=NOW,
        )
    finalized, document = ceremony.approve_finalization(
        reviewed,
        policy=policy,
        finalization_private_key=finalization_private,
        approval=finalization_token,
        now=NOW,
    )
    assert finalized["phase"] == "finalized"
    assert ceremony.verify_finalized_state(finalized, policy) == document
    parsed_document = verifier.parse_canonical_json(document)
    assert parsed_document["authoritative"] is False

    receipt = ceremony.content_free_receipt(finalized, policy)
    assert receipt["status"] == "finalized"
    assert "Marcelo" not in repr(receipt)
    assert "Amelia" not in repr(receipt)

    expired_policy = _policy(
        review_private,
        finalization_private,
        now=NOW + timedelta(days=1),
    )
    assert ceremony.verify_finalized_state(finalized, expired_policy) == document


def test_ceremony_rejects_policy_drift_and_state_tampering(tmp_path) -> None:
    database = tmp_path / "identity.db"
    _database(database)
    review_private = Ed25519PrivateKey.generate()
    finalization_private = Ed25519PrivateKey.generate()
    policy = _policy(review_private, finalization_private)
    packet = compiler.compile_unsigned_packet(
        migration.load_plan(database),
        migration.load_source_inventory(database),
        private_review_sha256="f" * 64,
        policy=policy,
        id_factory=_Ids(),
        now=NOW,
    )
    staged = ceremony.stage_state(packet, policy=policy)
    tampered = dict(staged)
    tampered["ceremony_policy_sha256"] = "0" * 64
    with pytest.raises(ceremony.SigningCeremonyError, match="policy drifted"):
        ceremony.review_approval_token(tampered, policy)

    private_state_tampered = dict(staged)
    private_state_tampered["unsigned_packet"] = dict(staged["unsigned_packet"])
    private_state_tampered["unsigned_packet"]["unsigned_run"] = dict(
        staged["unsigned_packet"]["unsigned_run"]
    )
    private_state_tampered["unsigned_packet"]["unsigned_run"]["policy_digest"] = (
        "0" * 64
    )
    with pytest.raises(ceremony.SigningCeremonyError, match="packet is invalid"):
        ceremony.review_approval_token(private_state_tampered, policy)


def test_host_launcher_separates_keys_and_accepts_no_paths() -> None:
    launcher = LAUNCHER.read_text(encoding="utf-8")
    installer = INSTALLER.read_text(encoding="utf-8")
    assert "[[ $# -eq 1 ]]" in launcher
    assert "LoadCredentialEncrypted=review.key:" in launcher
    assert "LoadCredentialEncrypted=finalization.key:" in launcher
    assert "LoadCredentialEncrypted=writer-freeze.key:" in launcher
    assert "LoadCredentialEncrypted=writer-freeze-policy.json:" in launcher
    assert "LoadCredentialEncrypted=privacy-probe-policy.json:" in launcher
    assert "LoadCredentialEncrypted=privacy-probe.key:" in launcher
    assert "LoadCredentialEncrypted=semantic-cutover-policy.json:" in launcher
    assert "LoadCredentialEncrypted=semantic-cutover.key:" in launcher
    assert "PrivateNetwork=yes" in launcher
    assert "IPAddressDeny=any" in launcher
    assert "ReadWritePaths=/srv/home-agent/private/phase3-identity" in launcher
    assert "sha256sum --check --status" in launcher
    assert "phase3-identity-signing-credentials-e5ae.json" in launcher
    assert "--setenv" not in launcher
    assert "STACK_TOKEN" not in launcher
    review_case = launcher.split("review)", 1)[1].split(";;", 1)[0]
    finalize_case = launcher.split("finalize)", 1)[1].split(";;", 1)[0]
    freeze_case = launcher.split("freeze-evidence)", 1)[1].split(";;", 1)[0]
    privacy_case = launcher.split("privacy-evidence)", 1)[1].split(";;", 1)[0]
    cutover_case = launcher.split("cutover-packet)", 1)[1].split(";;", 1)[0]
    assert "finalization.key" not in review_case
    assert "review.key" not in finalize_case
    assert "review.key" not in freeze_case
    assert "finalization.key" not in freeze_case
    assert "review.key" not in privacy_case
    assert "finalization.key" not in privacy_case
    assert "writer-freeze.key" not in privacy_case
    assert "review.key" not in cutover_case
    assert "finalization.key" not in cutover_case
    assert "writer-freeze.key" not in cutover_case
    assert "privacy-probe.key" not in cutover_case

    assert "[[ $# -eq 0 ]]" in installer
    assert "installed.sha256" in installer
    assert "identity signing installer never sees a key" not in installer.lower()
    assert "systemd-creds" not in installer
    assert "phase3_writer_freeze_ceremony.py" in installer
    assert "phase3_writer_freeze_evidence.py" in installer
    assert "phase3_privacy_cutover_ceremony.py" in installer
    assert "phase3_privacy_cutover_evidence.py" in installer
    assert "phase3_semantic_cutover_ceremony.py" in installer
    assert "phase3_semantic_cutover_packet.py" in installer


def test_physical_writer_freeze_is_bound_to_reviewed_source_and_distinct_key(
    tmp_path,
) -> None:
    database = tmp_path / "identity.db"
    _database(database)
    plan = migration.load_plan(database)
    review_body = {
        "source_plan_sha256": plan.digest,
        "sqlite_snapshot_sha256": "a" * 64,
    }
    review_digest = hashlib.sha256(verifier.canonical_bytes(review_body)).hexdigest()
    review_artifact = {
        "contract": "phase3-reviewed-people-private-review-e5x-v1",
        "private_review": review_body,
        "private_review_sha256": review_digest,
    }
    review_private = Ed25519PrivateKey.generate()
    finalization_private = Ed25519PrivateKey.generate()
    freeze_private = Ed25519PrivateKey.generate()
    policy = _policy(review_private, finalization_private)
    freeze_public, freeze_fingerprint = _public(freeze_private)
    freeze_policy = writer_freeze.WriterFreezePolicy(
        public_key=freeze_public,
        key_fingerprint=freeze_fingerprint,
    )
    packet = compiler.compile_unsigned_packet(
        plan,
        migration.load_source_inventory(database),
        private_review_sha256=review_digest,
        policy=policy,
        id_factory=_Ids(),
        now=NOW,
    )
    staged = ceremony.stage_state(packet, policy=policy)
    reviewed = ceremony.approve_review(
        staged,
        policy=policy,
        review_private_key=review_private,
        approval=ceremony.review_approval_token(staged, policy),
    )
    finalized, _document = ceremony.approve_finalization(
        reviewed,
        policy=policy,
        finalization_private_key=finalization_private,
        approval=ceremony.finalization_approval_token(reviewed, policy, now=NOW),
        now=NOW,
    )
    observation = verifier.canonical_bytes(
        {
            "contract": "legacy-identity-physical-freeze-observation-v1",
            "source_installation_id": ("00000000-0000-7000-8000-000000009001"),
            "semantic_generation": 2,
            "source_plan_sha256": plan.digest,
            "database_sha256": "b" * 64,
            "schema_digest": "sha256:" + "c" * 64,
            "trigger_digest": "sha256:" + "d" * 64,
            "cutover_witness_sha256": "e" * 64,
            "home_assistant_state": "stopped",
            "process_lock_result": "exclusive",
            "write_probe_result": "blocked",
            "integrity_result": "passed",
            "checkpoint_result": "complete",
            "journal_result": "clean",
            "legacy_context_cutoff_status": "enforced_cutoff",
            "recognition_mode": "nonauthoritative_only",
            "freeze_kernel_build_digest": "f" * 64,
            "observed_at": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
    )
    raw = writer_freeze.compile_writer_freeze_evidence(
        packet,
        reviewed["review_signature"],
        verifier.canonical_bytes(finalized["finalization_envelope"]),
        review_artifact,
        observation,
        identity_policy=policy,
        writer_freeze_policy=freeze_policy,
        writer_freeze_private_key=freeze_private,
        id_factory=_Ids(),
    )
    verified = writer_freeze.verify_writer_freeze_evidence(
        raw,
        identity_policy=policy,
        writer_freeze_policy=freeze_policy,
    )
    assert verified["authoritative"] is False
    assert verified["writer_evidence"]["evidence_strength"] == "observed_stopped"
    assert (
        verified["enforced_writer_freeze"]["enforcement_status"] == "enforced_offline"
    )
    assert "Marcelo" not in raw.decode("utf-8")
    assert "Amelia" not in raw.decode("utf-8")

    tampered = verifier.parse_canonical_json(raw)
    tampered["physical_observation_sha256"] = "0" * 64
    with pytest.raises(
        writer_freeze.WriterFreezeEvidenceError, match="commitment mismatch"
    ):
        writer_freeze.verify_writer_freeze_evidence(
            verifier.canonical_bytes(tampered),
            identity_policy=policy,
            writer_freeze_policy=freeze_policy,
        )

    with pytest.raises(
        writer_freeze.WriterFreezeEvidenceError, match="purpose is not distinct"
    ):
        writer_freeze.compile_writer_freeze_evidence(
            packet,
            reviewed["review_signature"],
            verifier.canonical_bytes(finalized["finalization_envelope"]),
            review_artifact,
            observation,
            identity_policy=policy,
            writer_freeze_policy=writer_freeze.WriterFreezePolicy(
                public_key=review_private.public_key(),
                key_fingerprint=_public(review_private)[1],
            ),
            writer_freeze_private_key=review_private,
            id_factory=_Ids(),
        )

    privacy_private = Ed25519PrivateKey.generate()
    privacy_public, privacy_fingerprint = _public(privacy_private)
    privacy_policy = privacy_evidence.PrivacyProbePolicy(
        public_key=privacy_public,
        key_fingerprint=privacy_fingerprint,
    )
    categories = []
    for category, assertions in privacy_evidence.CATEGORY_ASSERTIONS.items():
        categories.append(
            {
                "check_category": category,
                "check_result": "passed",
                "residual_code": "none",
                "assertions": {name: True for name in assertions},
                "probe_digest": hashlib.sha256(category.encode("ascii")).hexdigest(),
            }
        )
    raw_privacy_observation = verifier.canonical_bytes(
        {
            "contract": "phase3-privacy-cutover-observation-e5aa-v1",
            "run_id": verified["run_id"],
            "finalization_id": verified["finalization_id"],
            "freeze_id": verified["enforced_writer_freeze"]["freeze_id"],
            "policy_digest": policy.policy_digest,
            "release_manifest_digest": verified["enforced_writer_freeze"][
                "release_manifest_digest"
            ],
            "probe_bundle_digest": "1" * 64,
            "checked_at": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "categories": categories,
        }
    )
    raw_privacy = privacy_evidence.compile_privacy_cutover_evidence(
        raw_privacy_observation,
        raw,
        identity_policy=policy,
        writer_freeze_policy=freeze_policy,
        privacy_probe_policy=privacy_policy,
        privacy_probe_private_key=privacy_private,
        id_factory=_Ids(),
    )
    verified_privacy = privacy_evidence.verify_privacy_cutover_evidence(
        raw_privacy,
        identity_policy=policy,
        privacy_probe_policy=privacy_policy,
    )
    assert [item["check_category"] for item in verified_privacy["receipts"]] == [
        "ingress",
        "retrieval",
        "prompt",
        "initiative",
        "export",
        "edge_block",
    ]
    assert all(
        item["check_result"] == "passed" for item in verified_privacy["receipts"]
    )
    assert "Marcelo" not in raw_privacy.decode("utf-8")

    failed_observation = verifier.parse_canonical_json(raw_privacy_observation)
    failed_observation["categories"][0]["assertions"]["edge_policy_current"] = False
    with pytest.raises(
        privacy_evidence.PrivacyCutoverEvidenceError,
        match="assertions are incomplete",
    ):
        privacy_evidence.compile_privacy_cutover_evidence(
            verifier.canonical_bytes(failed_observation),
            raw,
            identity_policy=policy,
            writer_freeze_policy=freeze_policy,
            privacy_probe_policy=privacy_policy,
            privacy_probe_private_key=privacy_private,
            id_factory=_Ids(),
        )

    semantic_private = Ed25519PrivateKey.generate()
    semantic_public, semantic_fingerprint = _public(semantic_private)
    semantic_policy = semantic_cutover.SemanticCutoverPolicy(
        public_key=semantic_public,
        key_fingerprint=semantic_fingerprint,
    )
    erasure_receipt = {
        "contract": "phase3-erasure-current-receipt-e5j-v1",
        "backup_label": "20260813-120000F",
        "schema_revision": "0006a_worker_lease_arbitration",
        "ledger_epoch": 0,
        "ledger_head_hash": "0" * 64,
        "verified_at": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "gate_status": "current",
    }
    raw_cutover = semantic_cutover.compile_semantic_cutover_packet(
        raw,
        raw_privacy,
        erasure_receipt,
        identity_policy=policy,
        writer_freeze_policy=freeze_policy,
        privacy_probe_policy=privacy_policy,
        semantic_cutover_policy=semantic_policy,
        semantic_cutover_private_key=semantic_private,
        now=NOW,
        id_factory=_Ids(),
    )
    verified_cutover = semantic_cutover.verify_semantic_cutover_packet(
        raw_cutover,
        identity_policy=policy,
        writer_freeze_policy=freeze_policy,
        privacy_probe_policy=privacy_policy,
        semantic_cutover_policy=semantic_policy,
    )
    document = verified_cutover["cutover_document"]
    candidate = verified_cutover["semantic_authority_candidate"]
    assert len(document) == 27
    assert all(isinstance(value, str) for value in document.values())
    assert document["contract_version"] == "reviewed-identity-semantic-cutover-v1"
    assert verified_cutover["authoritative"] is False
    assert candidate["authoritative"] is False
    assert [
        candidate[f"{category}_check_id"]
        for category in privacy_evidence.CATEGORY_ORDER
    ] == [receipt["check_id"] for receipt in verified_privacy["receipts"]]
    assert "Marcelo" not in raw_cutover.decode("utf-8")
    assert "Amelia" not in raw_cutover.decode("utf-8")

    signature_tampered = verifier.parse_canonical_json(raw_cutover)
    signature_tampered["cutover_document"]["cutover_signature"] = "0" * 128
    signature_tampered["semantic_authority_candidate"]["cutover_signature"] = "0" * 128
    signature_tampered["cutover_document_sha256"] = hashlib.sha256(
        verifier.canonical_bytes(signature_tampered["cutover_document"])
    ).hexdigest()
    with pytest.raises(
        semantic_cutover.SemanticCutoverPacketError,
        match="signature failed",
    ):
        semantic_cutover.verify_semantic_cutover_packet(
            verifier.canonical_bytes(signature_tampered),
            identity_policy=policy,
            writer_freeze_policy=freeze_policy,
            privacy_probe_policy=privacy_policy,
            semantic_cutover_policy=semantic_policy,
        )

    ledger_tampered = verifier.parse_canonical_json(raw_cutover)
    ledger_tampered["erasure_current_receipt"]["ledger_epoch"] = 1
    with pytest.raises(semantic_cutover.SemanticCutoverPacketError):
        semantic_cutover.verify_semantic_cutover_packet(
            verifier.canonical_bytes(ledger_tampered),
            identity_policy=policy,
            writer_freeze_policy=freeze_policy,
            privacy_probe_policy=privacy_policy,
            semantic_cutover_policy=semantic_policy,
        )

    with pytest.raises(
        semantic_cutover.SemanticCutoverPacketError,
        match="purpose is not distinct",
    ):
        semantic_cutover.compile_semantic_cutover_packet(
            raw,
            raw_privacy,
            erasure_receipt,
            identity_policy=policy,
            writer_freeze_policy=freeze_policy,
            privacy_probe_policy=privacy_policy,
            semantic_cutover_policy=semantic_cutover.SemanticCutoverPolicy(
                public_key=privacy_public,
                key_fingerprint=privacy_fingerprint,
            ),
            semantic_cutover_private_key=privacy_private,
            now=NOW,
            id_factory=_Ids(),
        )
