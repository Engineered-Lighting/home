"""Stage one reviewed Person projection as a dormant binding nominee.

This module is deliberately offline and non-deployable.  It re-verifies the
exact signed identity-review bundle, its distinct finalization envelope, and
the reconstructed private source rows before resolving one operator-supplied
*person projection receipt*.  The receipt selector is a nomination input, not
proof that the operator is authenticated or authorized, and never establishes
that the selected person is ``me``.

The result is an in-process convenience object only.  It cannot be serialized
into portable proof, consumed by a runtime service, or used to create a
principal binding.  Any future consumer must re-verify the raw artifacts and
must separately prove the current HA subject, operator nomination authority,
privacy/erasure state, fresh confirmation gesture, and atomic database commit.
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
    parse_canonical_json,
    verify_finalization_envelope,
    verify_projection_bundle,
)


CONTRACT_VERSION = "reviewed-principal-binding-nominee-staging-v1"
RULE_VERSION = "exact-person-receipt-nomination-v1"
DEPLOYMENT_STATUS = "non_deployable"
CAPABILITY_STATUS = "capability_disabled"
COMPATIBILITY_STATUS = "coverage_unproven"
NOMINATION_ROLE = "operator_review_candidate"
NOMINATION_AUTHORITY_STATUS = "unverified"
BLOCKING_NOMINEE_DIRECTIVES = frozenset(
    {"do_not_track", "ignored", "silent", "private", "auto_expire"}
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _locked_claims() -> dict[str, object]:
    return {
        "source_signatures_verified_during_compilation": True,
        "signed_person_projection_verified": True,
        "exact_receipt_selector_resolved": True,
        "signed_snapshot_has_no_archived_nominee": True,
        "signed_snapshot_has_no_blocking_nominee_directive": True,
        "portable_proof": False,
        "downstream_consumption_allowed": False,
        "requires_raw_artifact_reverification": True,
        "operator_identity_verified": False,
        "operator_nomination_authority_verified": False,
        "selector_authority_verified": False,
        "ha_identity_verified": False,
        "binding_graph_verified": False,
        "current_person_status_verified": False,
        "current_privacy_state_verified": False,
        "current_retrieval_state_verified": False,
        "erasure_coverage_complete": False,
        "fresh_confirmation_verified": False,
        "single_use_artifact_consumed": False,
        "serializable_commit_enforced": False,
        "me_identity_established": False,
        "binding_created": False,
        "authoritative": False,
        "commit_ready": False,
        "enables_writes": False,
        "location_memory_enabled": False,
        "travel_greetings_enabled": False,
    }


class PrincipalNomineeStagingError(ValueError):
    """A stable, content-free failure from the offline staging compiler."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SignedReviewedPersonProjection:
    """Private Person material authenticated by the two source signatures."""

    person_id: uuid.UUID = field(repr=False)
    display_label: str = field(repr=False)
    decision_id: uuid.UUID = field(repr=False)
    receipt_id: uuid.UUID = field(repr=False)
    candidate_commitment: str = field(repr=False)
    receipt_commitment: str = field(repr=False)
    projection_ref_commitment: str = field(repr=False)
    projection_commitment: str = field(repr=False)
    lineage_commitment: str = field(repr=False)
    source_snapshot_digest: str = field(repr=False)
    person_record_commitment: str = field(repr=False)
    nomination_role: str = field(default=NOMINATION_ROLE, init=False)
    nomination_authority_status: str = field(
        default=NOMINATION_AUTHORITY_STATUS, init=False
    )
    me_identity_established: bool = field(default=False, init=False)

    def private_payload(self) -> dict[str, object]:
        """Return private operator material; callers must never log it."""

        return {
            "person_id": str(self.person_id),
            "display_label": self.display_label,
            "decision_id": str(self.decision_id),
            "receipt_id": str(self.receipt_id),
            "candidate_commitment": self.candidate_commitment,
            "receipt_commitment": self.receipt_commitment,
            "projection_ref_commitment": self.projection_ref_commitment,
            "projection_commitment": self.projection_commitment,
            "lineage_commitment": self.lineage_commitment,
            "source_snapshot_digest": self.source_snapshot_digest,
            "person_record_commitment": self.person_record_commitment,
            "nomination_role": self.nomination_role,
            "nomination_authority_status": self.nomination_authority_status,
            "me_identity_established": self.me_identity_established,
        }


@dataclass(frozen=True, slots=True)
class ReviewedPrincipalNominee:
    """Content-minimized, permanently write-disabled nomination result."""

    run_id: uuid.UUID = field(repr=False)
    finalization_id: uuid.UUID = field(repr=False)
    nominee: SignedReviewedPersonProjection = field(repr=False)
    review_receipt_commitment: str = field(repr=False)
    projection_manifest_commitment: str = field(repr=False)
    finalization_commitment: str = field(repr=False)
    review_bundle_sha256: str = field(repr=False)
    finalization_envelope_sha256: str = field(repr=False)
    source_record_snapshot_commitment: str = field(repr=False)
    nomination_commitment: str = field(repr=False)
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
    nomination_role: str = field(default=NOMINATION_ROLE, init=False)
    nomination_authority_status: str = field(
        default=NOMINATION_AUTHORITY_STATUS, init=False
    )
    nominee_count: int = field(default=1, init=False)
    source_signatures_verified_during_compilation: bool = field(
        default=True, init=False
    )
    signed_person_projection_verified: bool = field(default=True, init=False)
    exact_receipt_selector_resolved: bool = field(default=True, init=False)
    signed_snapshot_has_no_archived_nominee: bool = field(default=True, init=False)
    signed_snapshot_has_no_blocking_nominee_directive: bool = field(
        default=True, init=False
    )
    portable_proof: bool = field(default=False, init=False)
    downstream_consumption_allowed: bool = field(default=False, init=False)
    requires_raw_artifact_reverification: bool = field(default=True, init=False)
    operator_identity_verified: bool = field(default=False, init=False)
    operator_nomination_authority_verified: bool = field(default=False, init=False)
    selector_authority_verified: bool = field(default=False, init=False)
    ha_identity_verified: bool = field(default=False, init=False)
    binding_graph_verified: bool = field(default=False, init=False)
    current_person_status_verified: bool = field(default=False, init=False)
    current_privacy_state_verified: bool = field(default=False, init=False)
    current_retrieval_state_verified: bool = field(default=False, init=False)
    erasure_coverage_complete: bool = field(default=False, init=False)
    fresh_confirmation_verified: bool = field(default=False, init=False)
    single_use_artifact_consumed: bool = field(default=False, init=False)
    serializable_commit_enforced: bool = field(default=False, init=False)
    me_identity_established: bool = field(default=False, init=False)
    binding_created: bool = field(default=False, init=False)
    authoritative: bool = field(default=False, init=False)
    commit_ready: bool = field(default=False, init=False)
    enables_writes: bool = field(default=False, init=False)
    location_memory_enabled: bool = field(default=False, init=False)
    travel_greetings_enabled: bool = field(default=False, init=False)

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
            "source_record_snapshot_commitment": (
                self.source_record_snapshot_commitment
            ),
            "nomination_commitment": self.nomination_commitment,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "commitment_key_epoch": self.commitment_key_epoch,
            "verified_at": _utc_text(self.verified_at),
            "review_expires_at": _utc_text(self.review_expires_at),
            "nominee": self.nominee.private_payload(),
        }

    def content_free_audit_payload(self) -> dict[str, object]:
        """Return only categorical, non-identifying protocol state."""

        return {
            "contract_version": self.contract_version,
            "rule_version": self.rule_version,
            "deployment_status": self.deployment_status,
            "capability_status": self.capability_status,
            "compatibility_status": self.compatibility_status,
            "nomination_role": self.nomination_role,
            "nomination_authority_status": self.nomination_authority_status,
            "nominee_count": self.nominee_count,
            **{name: getattr(self, name) for name in _locked_claims()},
        }


def _fail(code: str) -> NoReturn:
    raise PrincipalNomineeStagingError(code)


def _uuid(value: Any, *, require_v7: bool = False) -> uuid.UUID:
    try:
        parsed = value if type(value) is uuid.UUID else uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        _fail("verified_document_invalid")
    if (
        parsed.int == 0
        or parsed.variant != uuid.RFC_4122
        or (require_v7 and parsed.version != 7)
    ):
        _fail("verified_document_invalid")
    return parsed


def _selector_uuid(value: Any) -> uuid.UUID:
    if type(value) is not uuid.UUID:
        _fail("person_receipt_selector_invalid")
    if value.int == 0 or value.variant != uuid.RFC_4122 or value.version != 7:
        _fail("person_receipt_selector_invalid")
    return value


def _hex64(value: Any) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        _fail("verified_document_invalid")
    return value


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail("verified_document_invalid")
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("verified_document_invalid")
    return value


def _freeze_source_records(
    source_records: Sequence[Mapping[str, Any]],
) -> tuple[tuple[Mapping[str, Any], ...], bytes]:
    """Capture caller-owned rows once and return only plain parsed values.

    The verifier and the staging commitment must consume the same immutable
    logical snapshot.  Parsing our one canonical capture prevents a stateful
    sequence or mapping from presenting different rows to those two consumers.
    """

    if isinstance(source_records, (str, bytes, bytearray)):
        _fail("source_record_snapshot_invalid")
    try:
        captured = [value for value in source_records]
        frozen_value = parse_canonical_json(canonical_bytes(captured))
        if type(frozen_value) is not list:
            _fail("source_record_snapshot_invalid")
        records = tuple(_mapping(value) for value in frozen_value)
        identities = [
            _uuid(value.get("source_item_id"), require_v7=True) for value in records
        ]
    except (TypeError, ValueError):
        _fail("source_record_snapshot_invalid")
    if len(set(identities)) != len(identities):
        _fail("source_record_snapshot_invalid")
    ordered = [
        record
        for _, record in sorted(
            zip(identities, records, strict=True), key=lambda item: item[0].bytes
        )
    ]
    try:
        return records, canonical_bytes(ordered)
    except (TypeError, ValueError):
        _fail("source_record_snapshot_invalid")


def _normalized_display(value: Any) -> str:
    if not isinstance(value, str) or not value:
        _fail("verified_document_invalid")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    if not normalized or any(
        unicodedata.category(character).startswith("C") for character in normalized
    ):
        _fail("verified_document_invalid")
    return normalized


def _lineage_commitment(
    projection: Mapping[str, Any],
    *,
    run_id: uuid.UUID,
    person_id: uuid.UUID,
    policy: VerificationPolicy,
) -> str:
    lineage = _mapping(projection.get("lineage"))
    expected_subjects = [{"person_id": str(person_id), "subject_role": "primary"}]
    if (
        lineage.get("lineage_id") != projection.get("receipt_id")
        or lineage.get("run_id") != str(run_id)
        or lineage.get("receipt_id") != projection.get("receipt_id")
        or lineage.get("decision_id") != projection.get("decision_id")
        or lineage.get("decision_kind") != "person"
        or lineage.get("projection_table_kind") != "identity.people"
        or lineage.get("projection_id") != str(person_id)
        or lineage.get("projection_ref_commitment")
        != projection.get("projection_ref_commitment")
        or lineage.get("subjects") != expected_subjects
    ):
        _fail("verified_person_lineage_invalid")
    material = {
        key: item for key, item in lineage.items() if key != "lineage_commitment"
    }
    actual = _hex64(lineage.get("lineage_commitment"))
    expected = keyed_commitment(
        policy.commitment_key, "identity-projection-lineage-v1", material
    )
    if not hmac.compare_digest(actual, expected):
        _fail("verified_person_lineage_invalid")
    return actual


def _projection_state(
    projections: Any,
) -> tuple[
    tuple[Mapping[str, Any], ...],
    dict[uuid.UUID, set[str]],
    set[uuid.UUID],
]:
    if not isinstance(projections, list):
        _fail("verified_document_invalid")
    people: list[Mapping[str, Any]] = []
    directives: dict[uuid.UUID, set[str]] = {}
    archived: set[uuid.UUID] = set()
    for value in projections:
        projection = _mapping(value)
        record = _mapping(projection.get("record"))
        kind = projection.get("decision_kind")
        if kind == "person":
            people.append(projection)
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
    return tuple(people), directives, archived


def _selected_person_projection(
    people: Sequence[Mapping[str, Any]], selector: uuid.UUID
) -> Mapping[str, Any]:
    matches = [
        projection
        for projection in people
        if _uuid(projection.get("receipt_id"), require_v7=True) == selector
    ]
    if len(matches) != 1:
        _fail("person_projection_receipt_not_found")
    return matches[0]


def _reject_display_ambiguity(
    people: Sequence[Mapping[str, Any]], selected: Mapping[str, Any]
) -> None:
    selected_record = _mapping(selected.get("record"))
    selected_person = _uuid(selected_record.get("person_id"))
    selected_label = _normalized_display(selected_record.get("display_name"))
    for projection in people:
        record = _mapping(projection.get("record"))
        person_id = _uuid(record.get("person_id"))
        if person_id == selected_person:
            continue
        if _normalized_display(record.get("display_name")) == selected_label:
            _fail("ambiguous_nominee_display_label")


def _person_projection(
    projection: Mapping[str, Any],
    *,
    run_id: uuid.UUID,
    policy: VerificationPolicy,
) -> SignedReviewedPersonProjection:
    if (
        projection.get("decision_kind") != "person"
        or projection.get("projection_table_kind") != "identity.people"
    ):
        _fail("selected_receipt_is_not_person_projection")
    record = _mapping(projection.get("record"))
    person_id = _uuid(record.get("person_id"))
    display_label = record.get("display_name")
    _normalized_display(display_label)
    decision_id = _uuid(projection.get("decision_id"), require_v7=True)
    receipt_id = _uuid(projection.get("receipt_id"), require_v7=True)
    lineage = _lineage_commitment(
        projection, run_id=run_id, person_id=person_id, policy=policy
    )
    person_record_commitment = keyed_commitment(
        policy.commitment_key,
        "identity-principal-nominee-person-record-v1",
        record,
    )
    return SignedReviewedPersonProjection(
        person_id=person_id,
        display_label=display_label,
        decision_id=decision_id,
        receipt_id=receipt_id,
        candidate_commitment=_hex64(projection.get("candidate_commitment")),
        receipt_commitment=_hex64(projection.get("receipt_commitment")),
        projection_ref_commitment=_hex64(projection.get("projection_ref_commitment")),
        projection_commitment=_hex64(projection.get("projection_commitment")),
        lineage_commitment=lineage,
        source_snapshot_digest=_hex64(record.get("legacy_source_sha256")),
        person_record_commitment=person_record_commitment,
    )


def compile_reviewed_principal_nominee(
    raw_review_bundle: bytes,
    raw_finalization_envelope: bytes,
    *,
    source_records: Sequence[Mapping[str, Any]],
    selected_person_projection_receipt_id: uuid.UUID,
    policy: VerificationPolicy,
) -> ReviewedPrincipalNominee:
    """Re-verify signed Person provenance and compile one dormant nominee.

    ``selected_person_projection_receipt_id`` is the sole selector.  Matching a
    signed receipt proves which reviewed Person row was nominated, but this
    function authenticates neither the selector's caller nor the caller's
    authority to nominate that row.
    """

    if (
        type(raw_review_bundle) is not bytes
        or type(raw_finalization_envelope) is not bytes
    ):
        _fail("signed_artifact_type_invalid")
    selector = _selector_uuid(selected_person_projection_receipt_id)
    frozen_source_records, source_snapshot = _freeze_source_records(source_records)

    bundle = verify_projection_bundle(
        raw_review_bundle,
        source_records=frozen_source_records,
        policy=policy,
    )
    finalizer = verify_finalization_envelope(
        bundle,
        raw_finalization_envelope,
        policy=policy,
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

        people, directives, archived = _projection_state(document.get("projections"))
        selected = _selected_person_projection(people, selector)
        selected_record = _mapping(selected.get("record"))
        person_id = _uuid(selected_record.get("person_id"))
        if person_id in archived:
            _fail("nominee_status_blocked")
        if directives.get(person_id, set()) & BLOCKING_NOMINEE_DIRECTIVES:
            _fail("nominee_privacy_blocked")
        _reject_display_ambiguity(people, selected)
        nominee = _person_projection(selected, run_id=run_id, policy=policy)
        if nominee.receipt_id != selector:
            _fail("person_projection_receipt_not_found")

        review_receipt = _hex64(document.get("review_receipt_commitment"))
        projection_manifest = _hex64(document.get("projection_manifest_commitment"))
        finalization_commitment = _hex64(document.get("finalization_commitment"))
        review_bundle_sha256 = hashlib.sha256(raw_review_bundle).hexdigest()
        finalization_envelope_sha256 = hashlib.sha256(
            raw_finalization_envelope
        ).hexdigest()
        source_record_snapshot_commitment = keyed_commitment(
            policy.commitment_key,
            "identity-principal-nominee-source-record-snapshot-v1",
            {"canonical_source_records": source_snapshot.hex()},
        )
        nomination_material = {
            "contract_version": CONTRACT_VERSION,
            "rule_version": RULE_VERSION,
            "deployment_status": DEPLOYMENT_STATUS,
            "capability_status": CAPABILITY_STATUS,
            "compatibility_status": COMPATIBILITY_STATUS,
            "nomination_role": NOMINATION_ROLE,
            "nomination_authority_status": NOMINATION_AUTHORITY_STATUS,
            "nominee_count": 1,
            "run_id": str(run_id),
            "finalization_id": str(finalization_id),
            "selected_person_projection_receipt_id": str(selector),
            "review_receipt_commitment": review_receipt,
            "projection_manifest_commitment": projection_manifest,
            "finalization_commitment": finalization_commitment,
            "review_bundle_sha256": review_bundle_sha256,
            "finalization_envelope_sha256": finalization_envelope_sha256,
            "source_record_snapshot_commitment": (source_record_snapshot_commitment),
            "policy_version": policy.policy_version,
            "policy_digest": policy.policy_digest,
            "commitment_key_epoch": policy.commitment_key_epoch,
            "review_expires_at": _utc_text(bundle.review_expires_at),
            "locked_claims": _locked_claims(),
            "nominee": nominee.private_payload(),
        }
        nomination_commitment = keyed_commitment(
            policy.commitment_key,
            "identity-principal-binding-nominee-v1",
            nomination_material,
        )
        return ReviewedPrincipalNominee(
            run_id=run_id,
            finalization_id=finalization_id,
            nominee=nominee,
            review_receipt_commitment=review_receipt,
            projection_manifest_commitment=projection_manifest,
            finalization_commitment=finalization_commitment,
            review_bundle_sha256=review_bundle_sha256,
            finalization_envelope_sha256=finalization_envelope_sha256,
            source_record_snapshot_commitment=source_record_snapshot_commitment,
            nomination_commitment=nomination_commitment,
            policy_version=policy.policy_version,
            policy_digest=policy.policy_digest,
            commitment_key_epoch=policy.commitment_key_epoch,
            verified_at=finalizer.verified_at,
            review_expires_at=bundle.review_expires_at,
        )
    except PrincipalNomineeStagingError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError):
        _fail("verified_document_invalid")


def reverify_reviewed_principal_nominee(
    nominee: ReviewedPrincipalNominee,
    raw_review_bundle: bytes,
    raw_finalization_envelope: bytes,
    *,
    source_records: Sequence[Mapping[str, Any]],
    selected_person_projection_receipt_id: uuid.UUID,
    policy: VerificationPolicy,
) -> ReviewedPrincipalNominee:
    """Return a fresh result only when the supplied nomination still matches.

    The receipt selector is deliberately supplied again.  This function binds
    an exact selection to reverified source artifacts, but it still cannot prove
    who supplied that selection or whether they were authorized to do so.
    """

    if (
        type(nominee) is not ReviewedPrincipalNominee
        or type(nominee.nominee) is not SignedReviewedPersonProjection
    ):
        _fail("principal_nominee_reverification_failed")
    expected = compile_reviewed_principal_nominee(
        raw_review_bundle,
        raw_finalization_envelope,
        source_records=source_records,
        selected_person_projection_receipt_id=(selected_person_projection_receipt_id),
        policy=policy,
    )
    try:
        observed_payload = nominee.private_payload()
        trusted_payload = expected.private_payload()
        observed_payload.pop("verified_at", None)
        trusted_payload.pop("verified_at", None)
        observed = canonical_bytes(observed_payload)
        trusted = canonical_bytes(trusted_payload)
    except (AttributeError, TypeError, ValueError):
        _fail("principal_nominee_reverification_failed")
    if not hmac.compare_digest(observed, trusted):
        _fail("principal_nominee_reverification_failed")
    return expected
