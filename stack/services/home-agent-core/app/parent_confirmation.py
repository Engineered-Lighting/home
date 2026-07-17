"""Dormant compiler for an eventual atomic two-parent confirmation flow.

This module authenticates no receipt, creates no confirmation artifact, and
provides no database authority. It only binds an already server-sourced context
to a private display preview and checks that preview for drift. Any deployable
writer must use a new contract version and independently verify every receipt,
privacy/erasure state, authenticated gesture, nonce, and database lock.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Iterable, NoReturn

from .crypto import canonical_json, sha256_json


CONTRACT_VERSION = "dormant-parent-confirmation-preview-v1"
RULE_VERSION = "atomic-two-parent-confirmation-compiler-v1"
DEPLOYMENT_STATUS = "non_deployable"
CAPABILITY_STATUS = "capability_disabled"
PREDICATE = "parent_of"
REQUIRED_AUTHORITY = "explicit_related_party"
REQUIRED_SUPPORT = "explicit_authority"
PRIVACY_SCOPE = "private"
MAX_PREVIEW_LIFETIME = timedelta(minutes=15)
KNOWN_PRIVACY_DIRECTIVES = frozenset(
    {"do_not_track", "ignored", "silent", "private", "auto_expire"}
)
CHILD_BLOCKING_PRIVACY_DIRECTIVES = frozenset(
    {"do_not_track", "ignored", "silent", "auto_expire"}
)
PARENT_BLOCKING_PRIVACY_DIRECTIVES = KNOWN_PRIVACY_DIRECTIVES
PROHIBITED_IMPLICATIONS = (
    "property ownership or legal title",
    "residence at any place",
    "current presence at any place",
    "generic person/place association",
)
EMPTY_OPEN_PARENT_FACT_SET_DIGEST = sha256_json(
    {
        "contract": "open-parent-fact-set-v1",
        "predicate": PREDICATE,
        "open_facts": [],
    }
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_POLICY_VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_REVIEW_CODE = re.compile(r"^[A-HJ-NP-Z2-9]{16}$")


class ParentConfirmationProtocolError(ValueError):
    """A content-free, stable failure from the dormant protocol compiler."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CurrentChildBindingSnapshot:
    principal_id: uuid.UUID = field(repr=False)
    child_person_id: uuid.UUID = field(repr=False)
    child_display_label: str = field(repr=False)
    binding_receipt_digest: str = field(repr=False)
    authenticated: bool
    binding_status: str
    person_status: str
    retrieval_blocked: bool
    privacy_directives: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class StagedParentCandidate:
    staged_candidate_id: uuid.UUID = field(repr=False)
    parent_person_id: uuid.UUID = field(repr=False)
    child_person_id: uuid.UUID = field(repr=False)
    parent_display_label: str = field(repr=False)
    review_code: str = field(repr=False)
    legacy_label_artifact_id: uuid.UUID = field(repr=False)
    source_snapshot_digest: str = field(repr=False)
    operator_review_receipt_digest: str = field(repr=False)
    legacy_role_label: str
    legacy_perspective: str
    person_status: str
    retrieval_blocked: bool
    privacy_directives: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class ParentConfirmationContext:
    request_id: uuid.UUID
    child_binding: CurrentChildBindingSnapshot = field(repr=False)
    candidates: tuple[StagedParentCandidate, ...] = field(repr=False)
    migration_run_id: uuid.UUID = field(repr=False)
    migration_review_receipt_commitment: str = field(repr=False)
    migration_projection_manifest_commitment: str = field(repr=False)
    migration_receipt_expires_at: datetime = field(repr=False)
    current_open_parent_fact_count: int
    current_open_parent_fact_set_digest: str = field(repr=False)
    policy_version: str
    policy_digest: str = field(repr=False)
    staged_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ParentRelationshipPreview:
    parent_label: str = field(repr=False)
    child_label: str = field(repr=False)
    review_code: str = field(repr=False)
    predicate: str = field(default=PREDICATE, init=False)

    def as_private_dict(self) -> dict[str, str]:
        return {
            "parent_label": self.parent_label,
            "child_label": self.child_label,
            "review_code": self.review_code,
            "predicate": self.predicate,
        }


@dataclass(frozen=True, slots=True)
class ParentConfirmationPreview:
    request_id: uuid.UUID
    title: str = field(repr=False)
    statement: str = field(repr=False)
    legacy_context_note: str = field(repr=False)
    action_label: str = field(repr=False)
    relationships: tuple[ParentRelationshipPreview, ...] = field(repr=False)
    expires_at: datetime
    preview_digest: str = field(repr=False)
    contract_version: str = field(default=CONTRACT_VERSION, init=False)
    rule_version: str = field(default=RULE_VERSION, init=False)
    deployment_status: str = field(default=DEPLOYMENT_STATUS, init=False)
    privacy_scope: str = field(default=PRIVACY_SCOPE, init=False)
    atomic_edge_count: int = field(default=2, init=False)
    not_asserted: tuple[str, ...] = field(default=PROHIBITED_IMPLICATIONS, init=False)
    authoritative: bool = field(default=False, init=False)
    commit_ready: bool = field(default=False, init=False)
    enables_writes: bool = field(default=False, init=False)
    external_receipts_verified: bool = field(default=False, init=False)
    capability_status: str = field(default=CAPABILITY_STATUS, init=False)

    def private_payload(self) -> dict[str, object]:
        """Return private display content; never log or send to shared UI."""

        return {
            "request_id": str(self.request_id),
            "contract_version": self.contract_version,
            "rule_version": self.rule_version,
            "deployment_status": self.deployment_status,
            "privacy_scope": self.privacy_scope,
            "title": self.title,
            "statement": self.statement,
            "legacy_context_note": self.legacy_context_note,
            "action_label": self.action_label,
            "relationships": [item.as_private_dict() for item in self.relationships],
            "atomic_edge_count": self.atomic_edge_count,
            "not_asserted": list(self.not_asserted),
            "expires_at": _utc_text(self.expires_at),
            "authoritative": self.authoritative,
            "commit_ready": self.commit_ready,
            "enables_writes": self.enables_writes,
            "external_receipts_verified": self.external_receipts_verified,
            "capability_status": self.capability_status,
        }

    def private_payload_with_digest(self) -> dict[str, object]:
        return {**self.private_payload(), "preview_digest": self.preview_digest}

    def content_free_audit_payload(self) -> dict[str, object]:
        return {
            "request_id": str(self.request_id),
            "contract_version": self.contract_version,
            "rule_version": self.rule_version,
            "deployment_status": self.deployment_status,
            "atomic_edge_count": self.atomic_edge_count,
            "authoritative": self.authoritative,
            "commit_ready": self.commit_ready,
            "enables_writes": self.enables_writes,
            "external_receipts_verified": self.external_receipts_verified,
            "capability_status": self.capability_status,
            "preview_digest": self.preview_digest,
        }


@dataclass(frozen=True, slots=True)
class DormantParentEdgeProposal:
    parent_person_id: uuid.UUID = field(repr=False)
    child_person_id: uuid.UUID = field(repr=False)
    perspective_principal_id: uuid.UUID = field(repr=False)
    review_code: str = field(repr=False)
    legacy_label_artifact_id: uuid.UUID = field(repr=False)
    source_snapshot_digest: str = field(repr=False)
    operator_review_receipt_digest: str = field(repr=False)
    contract_version: str = field(default=CONTRACT_VERSION, init=False)
    deployment_status: str = field(default=DEPLOYMENT_STATUS, init=False)
    predicate: str = field(default=PREDICATE, init=False)
    required_authority: str = field(default=REQUIRED_AUTHORITY, init=False)
    required_support: str = field(default=REQUIRED_SUPPORT, init=False)
    required_contradiction: str = field(default="none", init=False)
    required_freshness: str = field(default="not_applicable", init=False)
    required_coverage: str = field(default="not_applicable", init=False)
    required_resolution: str = field(default="accepted", init=False)
    privacy_scope: str = field(default=PRIVACY_SCOPE, init=False)
    valid_from_rule: str = field(default="confirmation_transaction_time", init=False)
    confirmation_support_role: str = field(default="confirmation", init=False)
    legacy_support_role: str = field(default="legacy_context", init=False)
    authoritative: bool = field(default=False, init=False)
    commit_ready: bool = field(default=False, init=False)
    enables_writes: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class ParentConfirmationIntent:
    request_id: uuid.UUID
    proposal_digest: str = field(repr=False)
    edges: tuple[DormantParentEdgeProposal, ...] = field(repr=False)
    open_parent_fact_set_digest: str = field(repr=False)
    contract_version: str = field(default=CONTRACT_VERSION, init=False)
    rule_version: str = field(default=RULE_VERSION, init=False)
    deployment_status: str = field(default=DEPLOYMENT_STATUS, init=False)
    requires_atomic_commit: bool = field(default=True, init=False)
    atomic_commit_enforced: bool = field(default=False, init=False)
    authoritative: bool = field(default=False, init=False)
    commit_ready: bool = field(default=False, init=False)
    enables_writes: bool = field(default=False, init=False)
    external_receipts_verified: bool = field(default=False, init=False)
    capability_status: str = field(default=CAPABILITY_STATUS, init=False)
    requires_serializable_commit: bool = field(default=True, init=False)
    requires_fresh_authenticated_confirmation_artifact: bool = field(
        default=True, init=False
    )
    requires_atomic_confirmation_artifact_consume: bool = field(
        default=True, init=False
    )


@dataclass(frozen=True, slots=True)
class _ValidatedContext:
    child_label: str = field(repr=False)
    candidates: tuple[tuple[StagedParentCandidate, str], ...] = field(repr=False)
    staged_at: datetime
    expires_at: datetime
    migration_expires_at: datetime


def _fail(code: str) -> NoReturn:
    raise ParentConfirmationProtocolError(code)


def _require_uuid(value: uuid.UUID) -> None:
    if (
        not isinstance(value, uuid.UUID)
        or value.int == 0
        or value.variant != uuid.RFC_4122
    ):
        _fail("invalid_identifier")


def _require_uuid7(value: uuid.UUID) -> None:
    _require_uuid(value)
    if value.version != 7:
        _fail("invalid_identifier")


def _require_hex64(value: str) -> None:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        _fail("invalid_provenance")


def _utc(value: datetime, *, code: str = "invalid_time_window") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail(code)
    try:
        if value.utcoffset() is None:
            _fail(code)
        return value.astimezone(UTC)
    except (OverflowError, ValueError):
        _fail(code)


def _utc_text(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _display_label(value: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        _fail("invalid_display_label")
    if value != unicodedata.normalize("NFC", value):
        _fail("invalid_display_label")
    if not value or len(value) > 255:
        _fail("invalid_display_label")
    if any(unicodedata.category(char).startswith("C") for char in value):
        _fail("invalid_display_label")
    return value


def _require_review_code(value: str) -> None:
    if not isinstance(value, str) or _REVIEW_CODE.fullmatch(value) is None:
        _fail("invalid_review_code")


def _privacy_is_eligible(
    directives: Iterable[str], *, blocking: frozenset[str]
) -> bool:
    try:
        observed = tuple(directives)
    except TypeError:
        return False
    if any(not isinstance(value, str) for value in observed):
        return False
    directive_set = set(observed)
    return (
        len(directive_set) == len(observed)
        and directive_set.issubset(KNOWN_PRIVACY_DIRECTIVES)
        and not (directive_set & blocking)
    )


def _validate_context(context: ParentConfirmationContext) -> _ValidatedContext:
    if not isinstance(context, ParentConfirmationContext):
        _fail("invalid_context")
    if not isinstance(context.child_binding, CurrentChildBindingSnapshot):
        _fail("invalid_child_binding")

    _require_uuid7(context.request_id)
    _require_uuid7(context.migration_run_id)
    child = context.child_binding
    _require_uuid7(child.principal_id)
    _require_uuid(child.child_person_id)
    if (
        type(child.authenticated) is not bool
        or child.authenticated is not True
        or child.binding_status != "confirmed"
        or child.person_status != "active"
        or type(child.retrieval_blocked) is not bool
        or child.retrieval_blocked
    ):
        _fail("invalid_child_binding")
    if not _privacy_is_eligible(
        child.privacy_directives, blocking=CHILD_BLOCKING_PRIVACY_DIRECTIVES
    ):
        _fail("privacy_blocked")
    child_label = _display_label(child.child_display_label)
    _require_hex64(child.binding_receipt_digest)

    if not isinstance(context.candidates, tuple) or len(context.candidates) != 2:
        _fail("invalid_candidate_set")

    staged_ids: set[uuid.UUID] = set()
    parent_ids: set[uuid.UUID] = set()
    artifact_ids: set[uuid.UUID] = set()
    review_codes: set[str] = set()
    visible_labels = {child_label.casefold()}
    validated_candidates: list[tuple[StagedParentCandidate, str]] = []
    for candidate in context.candidates:
        if not isinstance(candidate, StagedParentCandidate):
            _fail("invalid_candidate_set")
        _require_uuid7(candidate.staged_candidate_id)
        _require_uuid(candidate.parent_person_id)
        _require_uuid(candidate.child_person_id)
        _require_uuid7(candidate.legacy_label_artifact_id)
        if (
            candidate.child_person_id != child.child_person_id
            or candidate.parent_person_id == child.child_person_id
            or candidate.legacy_role_label != "parent"
            or candidate.legacy_perspective != "unknown"
            or candidate.person_status != "active"
            or type(candidate.retrieval_blocked) is not bool
            or candidate.retrieval_blocked
        ):
            _fail("invalid_candidate_set")
        if not _privacy_is_eligible(
            candidate.privacy_directives,
            blocking=PARENT_BLOCKING_PRIVACY_DIRECTIVES,
        ):
            _fail("privacy_blocked")
        _require_review_code(candidate.review_code)
        if (
            candidate.staged_candidate_id in staged_ids
            or candidate.parent_person_id in parent_ids
            or candidate.legacy_label_artifact_id in artifact_ids
            or candidate.review_code in review_codes
        ):
            _fail("invalid_candidate_set")
        staged_ids.add(candidate.staged_candidate_id)
        parent_ids.add(candidate.parent_person_id)
        artifact_ids.add(candidate.legacy_label_artifact_id)
        review_codes.add(candidate.review_code)
        _require_hex64(candidate.source_snapshot_digest)
        _require_hex64(candidate.operator_review_receipt_digest)
        parent_label = _display_label(candidate.parent_display_label)
        folded = parent_label.casefold()
        if folded in visible_labels:
            _fail("ambiguous_display_identity")
        visible_labels.add(folded)
        validated_candidates.append((candidate, parent_label))

    if (
        type(context.current_open_parent_fact_count) is not int
        or context.current_open_parent_fact_count != 0
        or context.current_open_parent_fact_set_digest
        != EMPTY_OPEN_PARENT_FACT_SET_DIGEST
    ):
        _fail("existing_parent_fact_state")

    _require_hex64(context.migration_review_receipt_commitment)
    _require_hex64(context.migration_projection_manifest_commitment)
    _require_hex64(context.policy_digest)
    if (
        not isinstance(context.policy_version, str)
        or _POLICY_VERSION.fullmatch(context.policy_version) is None
    ):
        _fail("invalid_policy")

    staged_at = _utc(context.staged_at)
    expires_at = _utc(context.expires_at)
    migration_expires_at = _utc(context.migration_receipt_expires_at)
    if (
        expires_at <= staged_at
        or expires_at - staged_at > MAX_PREVIEW_LIFETIME
        or migration_expires_at < expires_at
    ):
        _fail("invalid_time_window")

    validated_candidates.sort(key=lambda item: item[0].parent_person_id.bytes)
    return _ValidatedContext(
        child_label=child_label,
        candidates=tuple(validated_candidates),
        staged_at=staged_at,
        expires_at=expires_at,
        migration_expires_at=migration_expires_at,
    )


def _require_current_time(
    trusted_operation_time: datetime, validated: _ValidatedContext
) -> None:
    observed_at = _utc(trusted_operation_time, code="invalid_operation_time")
    if observed_at < validated.staged_at:
        _fail("preview_not_current")
    if observed_at >= validated.expires_at:
        _fail("preview_expired")


def _private_preview(
    context: ParentConfirmationContext,
    validated: _ValidatedContext,
) -> ParentConfirmationPreview:
    relationships = tuple(
        ParentRelationshipPreview(
            parent_label=parent_label,
            child_label=validated.child_label,
            review_code=candidate.review_code,
        )
        for candidate, parent_label in validated.candidates
    )
    first, second = relationships
    statement = (
        "Confirm both relationships as one private change: "
        f"{first.parent_label} [{first.review_code}] is a parent of "
        f"{validated.child_label}; {second.parent_label} [{second.review_code}] "
        f"is a parent of {validated.child_label}. Both relationships apply "
        "together or neither applies."
    )
    return ParentConfirmationPreview(
        request_id=context.request_id,
        title="Confirm both parent relationships",
        statement=statement,
        legacy_context_note=(
            "Legacy parent labels are context only. A later authenticated "
            "confirmation would create both explicit relationships together."
        ),
        action_label="Confirm both parent relationships",
        relationships=relationships,
        expires_at=validated.expires_at,
        preview_digest="",
    )


def _candidate_material(
    candidate: StagedParentCandidate, parent_label: str
) -> dict[str, object]:
    return {
        "staged_candidate_id": str(candidate.staged_candidate_id),
        "parent_person_id": str(candidate.parent_person_id),
        "child_person_id": str(candidate.child_person_id),
        "parent_display_label": parent_label,
        "review_code": candidate.review_code,
        "legacy_label_artifact_id": str(candidate.legacy_label_artifact_id),
        "source_snapshot_digest": candidate.source_snapshot_digest,
        "operator_review_receipt_digest": (candidate.operator_review_receipt_digest),
        "legacy_role_label": candidate.legacy_role_label,
        "legacy_perspective": candidate.legacy_perspective,
        "person_status": candidate.person_status,
        "retrieval_blocked": candidate.retrieval_blocked,
        "privacy_directives": sorted(candidate.privacy_directives),
    }


def _bound_material(
    context: ParentConfirmationContext,
    validated: _ValidatedContext,
    preview: ParentConfirmationPreview,
) -> dict[str, object]:
    child = context.child_binding
    candidates = [
        _candidate_material(candidate, parent_label)
        for candidate, parent_label in validated.candidates
    ]
    return {
        "domain": "home-agent:parent-confirmation:dormant-preview:v1",
        "contract_version": CONTRACT_VERSION,
        "rule_version": RULE_VERSION,
        "deployment_status": DEPLOYMENT_STATUS,
        "request_id": str(context.request_id),
        "staged_at": _utc_text(validated.staged_at),
        "expires_at": _utc_text(validated.expires_at),
        "policy": {
            "version": context.policy_version,
            "digest": context.policy_digest,
        },
        "binding": {
            "principal_id": str(child.principal_id),
            "child_person_id": str(child.child_person_id),
            "child_display_label": validated.child_label,
            "receipt_digest": child.binding_receipt_digest,
            "authenticated": child.authenticated,
            "binding_status": child.binding_status,
            "person_status": child.person_status,
            "retrieval_blocked": child.retrieval_blocked,
            "privacy_directives": sorted(child.privacy_directives),
        },
        "migration": {
            "run_id": str(context.migration_run_id),
            "review_receipt_commitment": (context.migration_review_receipt_commitment),
            "projection_manifest_commitment": (
                context.migration_projection_manifest_commitment
            ),
            "receipt_expires_at": _utc_text(validated.migration_expires_at),
        },
        "current_open_parent_facts": {
            "count": context.current_open_parent_fact_count,
            "set_digest": context.current_open_parent_fact_set_digest,
        },
        "candidate_set_digest": sha256_json(candidates),
        "candidates": candidates,
        "future_fact_constraints": {
            "predicate": PREDICATE,
            "required_authority": REQUIRED_AUTHORITY,
            "required_support": REQUIRED_SUPPORT,
            "required_contradiction": "none",
            "required_freshness": "not_applicable",
            "required_coverage": "not_applicable",
            "required_resolution": "accepted",
            "privacy_scope": PRIVACY_SCOPE,
            "valid_from_rule": "confirmation_transaction_time",
            "confirmation_support_role": "confirmation",
            "legacy_support_role": "legacy_context",
            "atomic_edge_count": 2,
        },
        "private_display": preview.private_payload(),
    }


def _preview_digest(material: dict[str, object], digest_key: bytes) -> str:
    if not isinstance(digest_key, bytes) or len(digest_key) != 32:
        _fail("invalid_digest_key")
    purpose_key = hmac.new(
        digest_key,
        b"home-agent:dormant-parent-confirmation-preview:v1",
        hashlib.sha256,
    ).digest()
    return hmac.new(purpose_key, canonical_json(material), hashlib.sha256).hexdigest()


def compile_parent_confirmation_preview(
    context: ParentConfirmationContext,
    *,
    digest_key: bytes,
    evaluated_at: datetime,
) -> ParentConfirmationPreview:
    """Compile current private display content without creating authority."""

    validated = _validate_context(context)
    _require_current_time(evaluated_at, validated)
    preview = _private_preview(context, validated)
    digest = _preview_digest(_bound_material(context, validated, preview), digest_key)
    return ParentConfirmationPreview(
        request_id=preview.request_id,
        title=preview.title,
        statement=preview.statement,
        legacy_context_note=preview.legacy_context_note,
        action_label=preview.action_label,
        relationships=preview.relationships,
        expires_at=preview.expires_at,
        preview_digest=digest,
    )


def verify_parent_confirmation_preview(
    preview: ParentConfirmationPreview,
    *,
    supplied_digest: str,
    current_context: ParentConfirmationContext,
    digest_key: bytes,
    trusted_operation_time: datetime,
) -> ParentConfirmationIntent:
    """Verify preview drift only; do not claim an authenticated confirmation."""

    validated = _validate_context(current_context)
    _require_current_time(trusted_operation_time, validated)
    if (
        not isinstance(supplied_digest, str)
        or _HEX64.fullmatch(supplied_digest) is None
        or not isinstance(preview, ParentConfirmationPreview)
        or not isinstance(preview.preview_digest, str)
        or _HEX64.fullmatch(preview.preview_digest) is None
    ):
        _fail("preview_mismatch")

    expected = compile_parent_confirmation_preview(
        current_context,
        digest_key=digest_key,
        evaluated_at=trusted_operation_time,
    )
    try:
        observed_payload = canonical_json(preview.private_payload())
    except (AttributeError, TypeError, ValueError):
        _fail("preview_mismatch")
    if not hmac.compare_digest(
        observed_payload,
        canonical_json(expected.private_payload()),
    ):
        _fail("preview_mismatch")
    if not (
        hmac.compare_digest(preview.preview_digest, supplied_digest)
        and hmac.compare_digest(expected.preview_digest, supplied_digest)
    ):
        _fail("preview_mismatch")

    child = current_context.child_binding
    edges = tuple(
        DormantParentEdgeProposal(
            parent_person_id=candidate.parent_person_id,
            child_person_id=child.child_person_id,
            perspective_principal_id=child.principal_id,
            review_code=candidate.review_code,
            legacy_label_artifact_id=candidate.legacy_label_artifact_id,
            source_snapshot_digest=candidate.source_snapshot_digest,
            operator_review_receipt_digest=(candidate.operator_review_receipt_digest),
        )
        for candidate, _ in validated.candidates
    )
    return ParentConfirmationIntent(
        request_id=current_context.request_id,
        proposal_digest=supplied_digest,
        edges=edges,
        open_parent_fact_set_digest=(
            current_context.current_open_parent_fact_set_digest
        ),
    )
