from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


MODULE_PATH = Path(__file__).parents[1] / "reviewed_identity_payload.py"
SPEC = importlib.util.spec_from_file_location("reviewed_identity_payload", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
verifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
RUN_ID = "00000000-0000-7000-8000-000000000901"
REQUEST_ID = "00000000-0000-7000-8000-000000000902"
SOURCE_ID = "00000000-0000-7000-8000-000000000903"
DECISION_ID = "00000000-0000-7000-8000-000000000904"
RECEIPT_ID = "00000000-0000-7000-8000-000000000905"
PERSON_ID = "11111111-1111-4111-8111-111111111111"
SHADOW_ID = uuid.UUID("00000000-0000-7000-8000-000000000906")


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def canonical(value) -> bytes:
    return verifier.canonical_bytes(value)


class BundleFixture:
    def __init__(self) -> None:
        self.review_private = Ed25519PrivateKey.generate()
        public = self.review_private.public_key()
        public_bytes = public.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.commitment_key = bytes(range(32))
        self.policy = verifier.VerificationPolicy(
            review_public_key=public,
            review_key_fingerprint=hashlib.sha256(public_bytes).hexdigest(),
            commitment_key=self.commitment_key,
            commitment_key_epoch=7,
            commitment_key_fingerprint=hashlib.sha256(self.commitment_key).hexdigest(),
            policy_version="home-agent-mvp-v1",
            policy_digest=digest("policy"),
            source_projection_contract_digest=digest("projection-contract"),
            release_manifest_digest=digest("release"),
            migration_tool_bundle_digest=digest("tool"),
            core_oci_manifest_digest=digest("oci"),
            core_schema_digest=digest("schema"),
            core_capability_digest=digest("capability"),
            shadow_authorization_id=SHADOW_ID,
            now=lambda: NOW,
        )
        self.source_record = {
            "source_item_id": SOURCE_ID,
            "source_table_kind": "identities",
            "row_key": {"uuid": PERSON_ID},
            "allowed_projection": {},
        }

    def build(self) -> tuple[bytes, list[dict]]:
        record = {
            "person_id": PERSON_ID,
            "display_name": "Marcelo",
            "pronouns": None,
            "privacy_scope": "private",
            "legacy_source_ref": "legacy-identity-store:v1:identity:11111111",
            "legacy_source_version": 3,
            "legacy_source_sha256": digest("person-source"),
        }
        self.source_record["allowed_projection"] = {
            "decisions": [
                {
                    "decision_kind": "person",
                    "disposition": "apply",
                    "record": record,
                }
            ]
        }
        source = {
            "source_item_id": SOURCE_ID,
            "ordinal": 0,
            "source_table_kind": "identities",
            "row_key_commitment": verifier.keyed_commitment(
                self.commitment_key,
                "identity-source-row-key-v1",
                {
                    "run_id": RUN_ID,
                    "source_item_id": SOURCE_ID,
                    "source_table_kind": "identities",
                    "row_key": self.source_record["row_key"],
                },
            ),
            "allowed_projection_commitment": verifier.keyed_commitment(
                self.commitment_key,
                "identity-allowed-projection-v1",
                {
                    "run_id": RUN_ID,
                    "source_item_id": SOURCE_ID,
                    "source_table_kind": "identities",
                    "allowed_projection": self.source_record["allowed_projection"],
                },
            ),
        }
        candidate = verifier.keyed_commitment(
            self.commitment_key,
            "identity-candidate-v1",
            {
                "run_id": RUN_ID,
                "decision_id": DECISION_ID,
                "decision_kind": "person",
                "record": record,
            },
        )
        decision = {
            "decision_id": DECISION_ID,
            "source_item_id": SOURCE_ID,
            "ordinal": 0,
            "decision_kind": "person",
            "disposition": "apply",
            "candidate_commitment": candidate,
            "canonical_apply_decision_id": None,
            "canonical_apply_disposition": None,
            "decision_commitment": "",
        }
        decision["decision_commitment"] = verifier.keyed_commitment(
            self.commitment_key,
            "identity-decision-v1",
            {
                key: value
                for key, value in decision.items()
                if key != "decision_commitment"
            },
        )
        projection_table = "identity.people"
        projection_commitment = verifier.keyed_commitment(
            self.commitment_key,
            "identity-semantic-projection-v1",
            {
                "run_id": RUN_ID,
                "decision_id": DECISION_ID,
                "decision_kind": "person",
                "projection_table_kind": projection_table,
                "record": record,
            },
        )
        projection_ref = verifier.keyed_commitment(
            self.commitment_key,
            "identity-projection-reference-v1",
            {
                "run_id": RUN_ID,
                "decision_id": DECISION_ID,
                "projection_table_kind": projection_table,
                "record_identity": {"person_id": PERSON_ID},
            },
        )
        receipt = verifier.keyed_commitment(
            self.commitment_key,
            "identity-projection-receipt-v1",
            {
                "run_id": RUN_ID,
                "decision_id": DECISION_ID,
                "receipt_id": RECEIPT_ID,
                "candidate_commitment": candidate,
                "projection_ref_commitment": projection_ref,
                "projection_commitment": projection_commitment,
            },
        )
        projection = {
            "decision_id": DECISION_ID,
            "decision_kind": "person",
            "candidate_commitment": candidate,
            "projection_table_kind": projection_table,
            "projection_ref_commitment": projection_ref,
            "projection_commitment": projection_commitment,
            "receipt_id": RECEIPT_ID,
            "receipt_commitment": receipt,
            "record": record,
        }
        run = {
            "run_id": RUN_ID,
            "operator_request_id": REQUEST_ID,
            "contract_version": "reviewed-identity-migration-run-v1",
            "source_schema_version": 1,
            "source_projection_contract_version": "legacy-identity-source-projection-v1",
            "importer_version": "legacy-identity-importer-v1",
            "canonicalization_version": "identity-canonicalization-v1",
            "projection_version": "semantic-people-projection-v1",
            "shadow_rule_version": "record-only-envelope-worker-gate-v3",
            "commitment_algorithm": "hmac-sha256-v1",
            "commitment_key_fingerprint": self.policy.commitment_key_fingerprint,
            "commitment_key_epoch": self.policy.commitment_key_epoch,
            "source_item_count": 1,
            "decision_count": 1,
            "logical_source_manifest_commitment": verifier.keyed_commitment(
                self.commitment_key, "identity-source-manifest-v1", [source]
            ),
            "projection_manifest_commitment": verifier.keyed_commitment(
                self.commitment_key, "identity-projection-manifest-v1", [projection]
            ),
            "source_projection_contract_digest": (
                self.policy.source_projection_contract_digest
            ),
            "review_receipt_commitment": "",
            "policy_version": self.policy.policy_version,
            "policy_digest": self.policy.policy_digest,
            "shadow_authorization_id": str(SHADOW_ID),
            "release_manifest_digest": self.policy.release_manifest_digest,
            "migration_tool_bundle_digest": self.policy.migration_tool_bundle_digest,
            "core_oci_manifest_digest": self.policy.core_oci_manifest_digest,
            "core_schema_digest": self.policy.core_schema_digest,
            "core_capability_digest": self.policy.core_capability_digest,
            "signature_algorithm": "ed25519",
            "signing_key_fingerprint": self.policy.review_key_fingerprint,
            "review_signature": "",
            "expires_at": (NOW + timedelta(minutes=5)).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
        }
        receipt_run = {
            key: value
            for key, value in run.items()
            if key not in {"review_signature", "review_receipt_commitment"}
        }
        run["review_receipt_commitment"] = verifier.keyed_commitment(
            self.commitment_key,
            "identity-review-receipt-v1",
            {
                "run": receipt_run,
                "source_items": [source],
                "decisions": [decision],
                "projections": [projection],
            },
        )
        unsigned_run = {
            key: value for key, value in run.items() if key != "review_signature"
        }
        signed = canonical(
            {
                "domain": "reviewed-identity-migration-review-v1",
                "run": unsigned_run,
                "source_items": [source],
                "decisions": [decision],
                "projections": [projection],
            }
        )
        run["review_signature"] = self.review_private.sign(signed).hex()
        bundle = {
            "contract_version": "reviewed-identity-projection-bundle-v1",
            "manifest": {
                "run": run,
                "source_items": [source],
                "decisions": [decision],
            },
            "projections": [projection],
        }
        return canonical(bundle), [copy.deepcopy(self.source_record)]


class ReviewedIdentityPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = BundleFixture()

    def test_valid_bundle_returns_only_finalizer_safe_content(self) -> None:
        raw, sources = self.fixture.build()
        result = verifier.verify_projection_bundle(
            raw, source_records=sources, policy=self.fixture.policy
        )
        self.assertEqual(str(result.run_id), RUN_ID)
        document = result.finalizer_document()
        self.assertEqual(
            set(document),
            {
                "contract_version",
                "run_id",
                "review_receipt_commitment",
                "projection_manifest_commitment",
                "projections",
            },
        )
        self.assertNotIn("source_items", document)
        self.assertNotIn("row_key", json.dumps(document))
        self.assertNotIn("authoritative", document)
        self.assertNotIn("parent_of", json.dumps(document))
        mutable_copy = result.projections
        mutable_copy[0]["record"]["display_name"] = "Tampered after verification"
        self.assertEqual(
            result.finalizer_document()["projections"][0]["record"]["display_name"],
            "Marcelo",
        )

    def test_noncanonical_and_ambiguous_json_fail_before_verification(self) -> None:
        raw, sources = self.fixture.build()
        with self.assertRaisesRegex(verifier.VerificationError, "not canonical"):
            verifier.verify_projection_bundle(
                b" " + raw, source_records=sources, policy=self.fixture.policy
            )
        with self.assertRaisesRegex(verifier.VerificationError, "duplicate keys"):
            verifier.parse_canonical_json(b'{"a":1,"a":1}')
        with self.assertRaisesRegex(verifier.VerificationError, "non-integer"):
            verifier.parse_canonical_json(b'{"a":1.0}')
        with self.assertRaisesRegex(verifier.VerificationError, "control"):
            verifier.parse_canonical_json(b'{"a":"\\u0000"}')

    def test_source_drift_commitment_fails_without_echoing_content(self) -> None:
        raw, sources = self.fixture.build()
        sources[0]["allowed_projection"]["display_name"] = "Changed"
        with self.assertRaises(verifier.VerificationError) as error:
            verifier.verify_projection_bundle(
                raw, source_records=sources, policy=self.fixture.policy
            )
        self.assertEqual(
            str(error.exception), "private source record commitment mismatch"
        )
        self.assertNotIn("Changed", str(error.exception))

    def test_projection_tamper_and_signature_tamper_fail(self) -> None:
        raw, sources = self.fixture.build()
        document = json.loads(raw)
        document["projections"][0]["record"]["display_name"] = "Mallory"
        with self.assertRaisesRegex(verifier.VerificationError, "commitment mismatch"):
            verifier.verify_projection_bundle(
                canonical(document), source_records=sources, policy=self.fixture.policy
            )

        document = json.loads(raw)
        document["manifest"]["run"]["review_signature"] = "0" * 128
        with self.assertRaisesRegex(
            verifier.VerificationError, "signature verification"
        ):
            verifier.verify_projection_bundle(
                canonical(document), source_records=sources, policy=self.fixture.policy
            )

    def test_policy_drift_expiry_extra_keys_and_missing_projection_fail(self) -> None:
        raw, sources = self.fixture.build()
        for mutate, message in (
            (
                lambda value: value["manifest"]["run"].__setitem__(
                    "policy_digest", digest("other-policy")
                ),
                "policy_digest does not match",
            ),
            (
                lambda value: value["manifest"]["run"].__setitem__(
                    "expires_at", "2026-07-12T11:59:59.000000Z"
                ),
                "expired",
            ),
            (
                lambda value: value.__setitem__("actor_id", PERSON_ID),
                "shape is invalid",
            ),
            (lambda value: value.__setitem__("projections", []), "incomplete"),
        ):
            document = json.loads(raw)
            mutate(document)
            with self.assertRaisesRegex(verifier.VerificationError, message):
                verifier.verify_projection_bundle(
                    canonical(document),
                    source_records=sources,
                    policy=self.fixture.policy,
                )

    def test_key_fingerprints_are_checked_when_policy_is_constructed(self) -> None:
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            verifier.VerificationPolicy(
                review_public_key=self.fixture.policy.review_public_key,
                review_key_fingerprint="0" * 64,
                commitment_key=self.fixture.policy.commitment_key,
                commitment_key_epoch=7,
                commitment_key_fingerprint=self.fixture.policy.commitment_key_fingerprint,
                policy_version=self.fixture.policy.policy_version,
                policy_digest=self.fixture.policy.policy_digest,
                source_projection_contract_digest=(
                    self.fixture.policy.source_projection_contract_digest
                ),
                release_manifest_digest=self.fixture.policy.release_manifest_digest,
                migration_tool_bundle_digest=(
                    self.fixture.policy.migration_tool_bundle_digest
                ),
                core_oci_manifest_digest=self.fixture.policy.core_oci_manifest_digest,
                core_schema_digest=self.fixture.policy.core_schema_digest,
                core_capability_digest=self.fixture.policy.core_capability_digest,
                shadow_authorization_id=SHADOW_ID,
            )
        policy = self.fixture.policy
        with self.assertRaisesRegex(ValueError, "wrong purpose"):
            verifier.VerificationPolicy(
                review_public_key=policy.review_public_key,
                review_key_fingerprint=policy.review_key_fingerprint,
                commitment_key=policy.commitment_key,
                commitment_key_epoch=policy.commitment_key_epoch,
                commitment_key_fingerprint=policy.commitment_key_fingerprint,
                policy_version=policy.policy_version,
                policy_digest=policy.policy_digest,
                source_projection_contract_digest=policy.source_projection_contract_digest,
                release_manifest_digest=policy.release_manifest_digest,
                migration_tool_bundle_digest=policy.migration_tool_bundle_digest,
                core_oci_manifest_digest=policy.core_oci_manifest_digest,
                core_schema_digest=policy.core_schema_digest,
                core_capability_digest=policy.core_capability_digest,
                shadow_authorization_id=SHADOW_ID,
                review_key_purpose="general_signing",
            )

    def test_legacy_relationship_and_alias_cannot_widen_semantics(self) -> None:
        relationship = {
            "candidate_id": DECISION_ID,
            "from_person_id": PERSON_ID,
            "to_person_id": "22222222-2222-4222-8222-222222222222",
            "relationship_label": "parent",
            "relationship_status": "active",
            "perspective": "unknown",
            "authoritative": False,
            "source_ref": "legacy-identity-store:v1:relationship:7",
            "source_snapshot_sha256": digest("relationship"),
        }
        verifier._validate_projection_record(
            "legacy_relationship_candidate", DECISION_ID, relationship
        )
        relationship["authoritative"] = True
        with self.assertRaisesRegex(verifier.VerificationError, "claims authority"):
            verifier._validate_projection_record(
                "legacy_relationship_candidate", DECISION_ID, relationship
            )

        alias = {
            "alias_id": DECISION_ID,
            "person_id": PERSON_ID,
            "alias": "Marcelo  Lima",
            "normalized_alias": "marcelo  lima",
            "alias_kind": "name",
            "source_ref": "legacy-identity-store:v1:alias:1",
            "source_version": 3,
            "source_snapshot_sha256": digest("alias"),
        }
        with self.assertRaisesRegex(verifier.VerificationError, "normalization"):
            verifier._validate_projection_record("alias", DECISION_ID, alias)

    def test_verifier_module_has_no_network_or_database_authority(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for prohibited in (
            "import psycopg",
            "import requests",
            "import socket",
            "import sqlalchemy",
            "import urllib",
            "subprocess",
            "parent_of",
            "authoritative=true",
        ):
            self.assertNotIn(prohibited, source)


if __name__ == "__main__":
    unittest.main()
