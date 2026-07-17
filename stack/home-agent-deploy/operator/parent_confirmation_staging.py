"""Authenticate legacy parent labels into a dormant candidate set.

This offline module is a narrow bridge between the separately signed identity
finalizer document and the dormant parent-confirmation preview compiler.  It
does not accept selected parent identifiers, connect to PostgreSQL, authenticate
a Home Assistant subject, create a confirmation artifact, or write a fact.

The result proves only that exactly two eligible legacy ``parent`` labels and
their People rows were present in one live, doubly signed finalizer document.
Current database privacy/retrieval state, erasure completeness, the child
binding, a private confirmation gesture, and atomic commit authority all remain
explicitly unproven.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import hmac
import re
from typing import Any, Mapping, NoReturn, Sequence
import unicodedata
import uuid

from reviewed_identity_payload import (
    VerificationPolicy,
    canonical_bytes,
    keyed_commitment,
    verify_finalization_envelope,
    verify_projection_bundle,
)


CONTRACT_VERSION = "signed-finalizer-parent-candidate-staging-v1"
RULE_VERSION = "signed-parent-candidate-provenance-v1"
DEPLOYMENT_STATUS = "non_deployable"
CAPABILITY_STATUS = "capability_disabled"
COMPATIBILITY_STATUS = "coverage_unproven"
LEGACY_ROLE_LABEL = "parent"
LEGACY_PERSPECTIVE = "unknown"
BLOCKING_PARENT_DIRECTIVES = frozenset(
    {"do_not_track", "ignored", "silent", "private", "auto_expire"}
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _locked_claims() -> dict[str, object]:
    return {
        "source_signatures_verified_during_compilation": True,
        "portable_proof": False,
        "downstream_consumption_allowed": False,
        "requires_raw_artifact_reverification": True,
        "signed_snapshot_has_no_archived_parent": True,
        "child_binding_verified": False,
        "current_person_status_verified": False,
        "current_privacy_state_verified": False,
        "current_retrieval_state_verified": False,
        "erasure_coverage_complete": False,
        "fresh_confirmation_verified": False,
        "single_use_artifact_consumed": False,
        "atomic_commit_enforced": False,
        "authoritative": False,
        "commit_ready": False,
        "enables_writes": False,
        "explicit_parent_facts_created": False,
    }


class ParentCandidateStagingError(ValueError):
    """A stable, content-free failure from the offline staging compiler."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SignedLegacyParentCandidate:
    """Private candidate material derived only from signed projections."""

    parent_person_id: uuid.UUID = field(repr=False)
    display_label: str = field(repr=False)
    legacy_label_id: uuid.UUID = field(repr=False)
    person_decision_id: uuid.UUID = field(repr=False)
    role_decision_id: uuid.UUID = field(repr=False)
    person_receipt_id: uuid.UUID = field(repr=False)
    role_receipt_id: uuid.UUID = field(repr=False)
    person_receipt_commitment: str = field(repr=False)
    role_receipt_commitment: str = field(repr=False)
    person_projection_commitment: str = field(repr=False)
    role_projection_commitment: str = field(repr=False)
    person_lineage_commitment: str = field(repr=False)
    role_lineage_commitment: str = field(repr=False)
    person_source_snapshot_digest: str = field(repr=False)
    role_source_snapshot_digest: str = field(repr=False)
    legacy_role_label: str = field(default=LEGACY_ROLE_LABEL, init=False)
    legacy_perspective: str = field(default=LEGACY_PERSPECTIVE, init=False)
    legacy_authoritative: bool = field(default=False, init=False)

    def private_payload(self) -> dict[str, object]:
        """Return private operator material; callers must never log it."""

        return {
            "parent_person_id": str(self.parent_person_id),
            "display_label": self.display_label,
            "legacy_label_id": str(self.legacy_label_id),
            "person_decision_id": str(self.person_decision_id),
            "role_decision_id": str(self.role_decision_id),
            "person_receipt_id": str(self.person_receipt_id),
            "role_receipt_id": str(self.role_receipt_id),
            "person_receipt_commitment": self.person_receipt_commitment,
            "role_receipt_commitment": self.role_receipt_commitment,
            "person_projection_commitment": self.person_projection_commitment,
            "role_projection_commitment": self.role_projection_commitment,
            "person_lineage_commitment": self.person_lineage_commitment,
            "role_lineage_commitment": self.role_lineage_commitment,
            "person_source_snapshot_digest": self.person_source_snapshot_digest,
            "role_source_snapshot_digest": self.role_source_snapshot_digest,
            "legacy_role_label": self.legacy_role_label,
            "legacy_perspective": self.legacy_perspective,
            "legacy_authoritative": self.legacy_authoritative,
        }


@dataclass(frozen=True, slots=True)
class SignedParentCandidateSet:
    """Content-minimized, permanently write-disabled staging result."""

    run_id: uuid.UUID = field(repr=False)
    finalization_id: uuid.UUID = field(repr=False)
    candidates: tuple[SignedLegacyParentCandidate, ...] = field(repr=False)
    review_receipt_commitment: str = field(repr=False)
    projection_manifest_commitment: str = field(repr=False)
    finalization_commitment: str = field(repr=False)
    review_bundle_sha256: str = field(repr=False)
    finalization_envelope_sha256: str = field(repr=False)
    candidate_set_commitment: str = field(repr=False)
    policy_version: str = field(repr=False)
    policy_digest: str = field(repr=False)
    commitment_key_epoch: int = field(repr=False)
    verified_at: datetime = field(repr=False)
    review_expires_at: datetime = field(repr=False)
    contract_version: str = field(default=CONTRACT_VERSION, init=False)
    rule_version: str = field(default=RULE_VERSION, init=False)
    deployment_status: str = field(default=DEPLOYMENT_STATUS, init=False)
    capability_status: str = field(default=CAPABILITY_STATUS, init=False)
    compatibility_status: str = field(default=COMPATIBILITY_STATUS, init=False)
    candidate_count: int = field(default=2, init=False)
    source_signatures_verified_during_compilation: bool = field(
        default=True, init=False
    )
    portable_proof: bool = field(default=False, init=False)
    downstream_consumption_allowed: bool = field(default=False, init=False)
    requires_raw_artifact_reverification: bool = field(default=True, init=False)
    signed_snapshot_has_no_archived_parent: bool = field(default=True, init=False)
    child_binding_verified: bool = field(default=False, init=False)
    current_person_status_verified: bool = field(default=False, init=False)
    current_privacy_state_verified: bool = field(default=False, init=False)
    current_retrieval_state_verified: bool = field(default=False, init=False)
    erasure_coverage_complete: bool = field(default=False, init=False)
    fresh_confirmation_verified: bool = field(default=False, init=False)
    single_use_artifact_consumed: bool = field(default=False, init=False)
    atomic_commit_enforced: bool = field(default=False, init=False)
    authoritative: bool = field(default=False, init=False)
    commit_ready: bool = field(default=False, init=False)
    enables_writes: bool = field(default=False, init=False)
    explicit_parent_facts_created: bool = field(default=False, init=False)

    def private_payload(self) -> dict[str, object]:
        """Return private staging content; this is not an API response."""

        return {
            **self.content_free_audit_payload(),
            "run_id": str(self.run_id),
            "finalization_id": str(self.finalization_id),
            "review_receipt_commitment": self.review_receipt_commitment,
            "projection_manifest_commitment": self.projection_manifest_commitment,
            "finalization_commitment": self.finalization_commitment,
            "review_bundle_sha256": self.review_bundle_sha256,
            "finalization_envelope_sha256": self.finalization_envelope_sha256,
            "candidate_set_commitment": self.candidate_set_commitment,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "commitment_key_epoch": self.commitment_key_epoch,
            "verified_at": _utc_text(self.verified_at),
            "review_expires_at": _utc_text(self.review_expires_at),
            "candidates": [
                candidate.private_payload() for candidate in self.candidates
            ],
        }

    def content_free_audit_payload(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "rule_version": self.rule_version,
            "deployment_status": self.deployment_status,
            "capability_status": self.capability_status,
            "compatibility_status": self.compatibility_status,
            "candidate_count": self.candidate_count,
            **{name: getattr(self, name) for name in _locked_claims()},
        }


def _fail(code: str) -> NoReturn:
    raise ParentCandidateStagingError(code)


def _uuid(value: Any, *, require_v7: bool = False) -> uuid.UUID:
    if not isinstance(value, str):
        _fail("verified_document_invalid")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        _fail("verified_document_invalid")
    if (
        str(parsed) != value
        or parsed.int == 0
        or parsed.variant != uuid.RFC_4122
        or (require_v7 and parsed.version != 7)
    ):
        _fail("verified_document_invalid")
    return parsed


def _hex64(value: Any) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        _fail("verified_document_invalid")
    return value


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail("verified_document_invalid")
    try:
        observed = value.astimezone(UTC)
    except (OverflowError, ValueError):
        _fail("verified_document_invalid")
    return observed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("verified_document_invalid")
    return value


def _lineage_commitment(
    projection: Mapping[str, Any],
    *,
    run_id: uuid.UUID,
    person_id: uuid.UUID,
    policy: VerificationPolicy,
) -> str:
    lineage = _mapping(projection.get("lineage"))
    expected = {
        "lineage_id": projection.get("receipt_id"),
        "run_id": str(run_id),
        "receipt_id": projection.get("receipt_id"),
        "decision_id": projection.get("decision_id"),
        "decision_kind": projection.get("decision_kind"),
        "projection_table_kind": projection.get("projection_table_kind"),
        "projection_id": (
            projection.get("record", {}).get("person_id")
            if projection.get("decision_kind") == "person"
            else projection.get("record", {}).get("label_id")
        ),
        "projection_ref_commitment": projection.get("projection_ref_commitment"),
        "subjects": [{"person_id": str(person_id), "subject_role": "primary"}],
    }
    actual_core = {
        key: value for key, value in lineage.items() if key != "lineage_commitment"
    }
    if actual_core != expected:
        _fail("verified_lineage_invalid")
    actual = _hex64(lineage.get("lineage_commitment"))
    expected_commitment = keyed_commitment(
        policy.commitment_key,
        "identity-projection-lineage-v1",
        expected,
    )
    if not hmac.compare_digest(actual, expected_commitment):
        _fail("verified_lineage_invalid")
    return actual


def _projection_index(
    projections: Any,
) -> tuple[
    dict[uuid.UUID, Mapping[str, Any]],
    dict[uuid.UUID, set[str]],
    set[uuid.UUID],
    list[Mapping[str, Any]],
]:
    if not isinstance(projections, list):
        _fail("verified_document_invalid")
    people: dict[uuid.UUID, Mapping[str, Any]] = {}
    directives: dict[uuid.UUID, set[str]] = {}
    archived: set[uuid.UUID] = set()
    parent_roles: list[Mapping[str, Any]] = []
    for value in projections:
        projection = _mapping(value)
        kind = projection.get("decision_kind")
        record = _mapping(projection.get("record"))
        if kind == "person":
            person_id = _uuid(record.get("person_id"))
            if person_id in people:
                _fail("verified_document_invalid")
            people[person_id] = projection
        elif kind == "privacy_directive":
            person_id = _uuid(record.get("person_id"))
            directive = record.get("directive")
            if not isinstance(directive, str):
                _fail("verified_document_invalid")
            directives.setdefault(person_id, set()).add(directive)
        elif kind == "person_status":
            person_id = _uuid(record.get("person_id"))
            if record.get("status") != "archived":
                _fail("verified_document_invalid")
            archived.add(person_id)
        elif kind == "legacy_role_candidate" and record.get("role_label") == (
            LEGACY_ROLE_LABEL
        ):
            parent_roles.append(projection)
    return people, directives, archived, parent_roles


def _candidate(
    *,
    person_projection: Mapping[str, Any],
    role_projection: Mapping[str, Any],
    run_id: uuid.UUID,
    policy: VerificationPolicy,
) -> SignedLegacyParentCandidate:
    person_record = _mapping(person_projection.get("record"))
    role_record = _mapping(role_projection.get("record"))
    person_id = _uuid(person_record.get("person_id"))
    if _uuid(role_record.get("person_id")) != person_id:
        _fail("verified_candidate_invalid")
    if (
        role_record.get("role_label") != LEGACY_ROLE_LABEL
        or role_record.get("perspective") != LEGACY_PERSPECTIVE
    ):
        _fail("verified_candidate_invalid")
    display_label = person_record.get("display_name")
    if not isinstance(display_label, str) or not display_label:
        _fail("verified_candidate_invalid")
    person_decision_id = _uuid(person_projection.get("decision_id"), require_v7=True)
    role_decision_id = _uuid(role_projection.get("decision_id"), require_v7=True)
    legacy_label_id = _uuid(role_record.get("label_id"), require_v7=True)
    if legacy_label_id != role_decision_id:
        _fail("verified_candidate_invalid")
    person_receipt_id = _uuid(person_projection.get("receipt_id"), require_v7=True)
    role_receipt_id = _uuid(role_projection.get("receipt_id"), require_v7=True)
    person_lineage = _lineage_commitment(
        person_projection, run_id=run_id, person_id=person_id, policy=policy
    )
    role_lineage = _lineage_commitment(
        role_projection, run_id=run_id, person_id=person_id, policy=policy
    )
    return SignedLegacyParentCandidate(
        parent_person_id=person_id,
        display_label=display_label,
        legacy_label_id=legacy_label_id,
        person_decision_id=person_decision_id,
        role_decision_id=role_decision_id,
        person_receipt_id=person_receipt_id,
        role_receipt_id=role_receipt_id,
        person_receipt_commitment=_hex64(person_projection.get("receipt_commitment")),
        role_receipt_commitment=_hex64(role_projection.get("receipt_commitment")),
        person_projection_commitment=_hex64(
            person_projection.get("projection_commitment")
        ),
        role_projection_commitment=_hex64(role_projection.get("projection_commitment")),
        person_lineage_commitment=person_lineage,
        role_lineage_commitment=role_lineage,
        person_source_snapshot_digest=_hex64(person_record.get("legacy_source_sha256")),
        role_source_snapshot_digest=_hex64(role_record.get("source_snapshot_sha256")),
    )


def compile_signed_parent_candidate_set(
    raw_bundle: bytes,
    raw_finalization_envelope: bytes,
    *,
    source_records: Sequence[Mapping[str, Any]],
    policy: VerificationPolicy,
) -> SignedParentCandidateSet:
    """Re-verify signed inputs and derive exactly two dormant parent labels.

    No selector is accepted: every ``parent`` role in the verified projection
    set participates, and any count other than exactly two fails closed.
    """

    if type(raw_bundle) is not bytes or type(raw_finalization_envelope) is not bytes:
        _fail("signed_artifact_type_invalid")
    bundle = verify_projection_bundle(
        raw_bundle, source_records=source_records, policy=policy
    )
    finalizer = verify_finalization_envelope(
        bundle, raw_finalization_envelope, policy=policy
    )
    try:
        document = finalizer.document
        if (
            document.get("contract_version")
            != "reviewed-identity-migration-finalization-v1"
            or document.get("verification_status") != "candidate_unverified"
            or document.get("authoritative") is not False
            or finalizer.review_expired
            or finalizer.auto_expiry_elapsed
        ):
            _fail("verified_document_invalid")
        run_id = _uuid(document.get("run_id"), require_v7=True)
        finalization_id = _uuid(document.get("finalization_id"), require_v7=True)
        if run_id != bundle.run_id or finalization_id != finalizer.finalization_id:
            _fail("verified_document_invalid")

        people, directives, archived, parent_roles = _projection_index(
            document.get("projections")
        )
        if len(parent_roles) != 2:
            _fail("parent_candidate_count_invalid")

        candidates: list[SignedLegacyParentCandidate] = []
        observed_people: set[uuid.UUID] = set()
        observed_labels: set[str] = set()
        for role_projection in parent_roles:
            role_record = _mapping(role_projection.get("record"))
            person_id = _uuid(role_record.get("person_id"))
            if person_id in observed_people:
                _fail("duplicate_parent_candidate")
            person_projection = people.get(person_id)
            if person_projection is None:
                _fail("parent_person_missing")
            if person_id in archived:
                _fail("parent_status_blocked")
            if directives.get(person_id, set()) & BLOCKING_PARENT_DIRECTIVES:
                _fail("parent_privacy_blocked")
            candidate = _candidate(
                person_projection=person_projection,
                role_projection=role_projection,
                run_id=run_id,
                policy=policy,
            )
            folded = " ".join(
                unicodedata.normalize("NFKC", candidate.display_label).split()
            ).casefold()
            if folded in observed_labels:
                _fail("ambiguous_parent_display_label")
            observed_people.add(person_id)
            observed_labels.add(folded)
            candidates.append(candidate)

        candidates.sort(key=lambda candidate: candidate.parent_person_id.bytes)
        candidate_material = [candidate.private_payload() for candidate in candidates]
        review_receipt = _hex64(document.get("review_receipt_commitment"))
        projection_manifest = _hex64(document.get("projection_manifest_commitment"))
        finalization_commitment = _hex64(document.get("finalization_commitment"))
        review_bundle_sha256 = hashlib.sha256(raw_bundle).hexdigest()
        finalization_envelope_sha256 = hashlib.sha256(
            raw_finalization_envelope
        ).hexdigest()
        set_material = {
            "contract_version": CONTRACT_VERSION,
            "rule_version": RULE_VERSION,
            "deployment_status": DEPLOYMENT_STATUS,
            "capability_status": CAPABILITY_STATUS,
            "candidate_count": 2,
            "run_id": str(run_id),
            "finalization_id": str(finalization_id),
            "review_receipt_commitment": review_receipt,
            "projection_manifest_commitment": projection_manifest,
            "finalization_commitment": finalization_commitment,
            "review_bundle_sha256": review_bundle_sha256,
            "finalization_envelope_sha256": finalization_envelope_sha256,
            "policy_version": policy.policy_version,
            "policy_digest": policy.policy_digest,
            "commitment_key_epoch": policy.commitment_key_epoch,
            "review_expires_at": _utc_text(bundle.review_expires_at),
            "compatibility_status": COMPATIBILITY_STATUS,
            "locked_claims": _locked_claims(),
            "candidates": candidate_material,
        }
        candidate_set_commitment = keyed_commitment(
            policy.commitment_key,
            "identity-parent-candidate-set-v1",
            set_material,
        )
        return SignedParentCandidateSet(
            run_id=run_id,
            finalization_id=finalization_id,
            candidates=tuple(candidates),
            review_receipt_commitment=review_receipt,
            projection_manifest_commitment=projection_manifest,
            finalization_commitment=finalization_commitment,
            review_bundle_sha256=review_bundle_sha256,
            finalization_envelope_sha256=finalization_envelope_sha256,
            candidate_set_commitment=candidate_set_commitment,
            policy_version=policy.policy_version,
            policy_digest=policy.policy_digest,
            commitment_key_epoch=policy.commitment_key_epoch,
            verified_at=finalizer.verified_at,
            review_expires_at=bundle.review_expires_at,
        )
    except ParentCandidateStagingError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError):
        _fail("verified_document_invalid")


def reverify_signed_parent_candidate_set(
    candidate_set: SignedParentCandidateSet,
    raw_bundle: bytes,
    raw_finalization_envelope: bytes,
    *,
    source_records: Sequence[Mapping[str, Any]],
    policy: VerificationPolicy,
) -> SignedParentCandidateSet:
    """Return a freshly compiled result only when the supplied result matches it.

    A result object or serialized private payload is never portable proof by
    itself. Any future consumer must provide and re-verify the original signed
    artifacts and reconstructed source rows through this boundary.
    """

    if (
        type(candidate_set) is not SignedParentCandidateSet
        or type(candidate_set.candidates) is not tuple
        or any(
            type(candidate) is not SignedLegacyParentCandidate
            for candidate in candidate_set.candidates
        )
    ):
        _fail("candidate_set_reverification_failed")
    expected = compile_signed_parent_candidate_set(
        raw_bundle,
        raw_finalization_envelope,
        source_records=source_records,
        policy=policy,
    )
    try:
        observed_payload = candidate_set.private_payload()
        trusted_payload = expected.private_payload()
        observed_payload.pop("verified_at", None)
        trusted_payload.pop("verified_at", None)
        observed = canonical_bytes(observed_payload)
        trusted = canonical_bytes(trusted_payload)
    except (AttributeError, TypeError, ValueError):
        _fail("candidate_set_reverification_failed")
    if not hmac.compare_digest(observed, trusted):
        _fail("candidate_set_reverification_failed")
    return expected
