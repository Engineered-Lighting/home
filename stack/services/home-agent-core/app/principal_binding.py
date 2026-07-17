"""Dormant compiler for an eventual reviewed principal-binding ceremony.

This module authenticates no Home Assistant request, verifies no external
receipt, consumes no gesture, and writes nothing. It binds an already
server-supplied authenticated-HA snapshot to a private preview and checks that
preview against an age-bounded current context. A reviewed person nominee is
only an operator-review candidate; it is not an established ``me`` identity.
This module cannot prove the context's origin or nomination authority. A future
deployable kernel must re-read the same state inside a SERIALIZABLE transaction
and use a new contract version.
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

from .crypto import canonical_json


CONTRACT_VERSION = "dormant-principal-binding-preview-v2"
RULE_VERSION = "reviewed-person-nominee-principal-binding-compiler-v2"
DEPLOYMENT_STATUS = "non_deployable"
CAPABILITY_STATUS = "capability_disabled"
PRIVACY_SCOPE = "private"
HA_SNAPSHOT_ORIGIN = "home_assistant_authenticated_whoami"
MAX_PREVIEW_LIFETIME = timedelta(minutes=15)
MAX_TRUSTED_STATE_AGE = timedelta(minutes=15)
KNOWN_PRIVACY_DIRECTIVES = frozenset(
    {"do_not_track", "ignored", "silent", "private", "auto_expire"}
)
# Before the principal binding exists, the caller has not yet proven that it is
# the private nominee's subject. Every directive therefore blocks display,
# including ``private``.
BLOCKING_PRIVACY_DIRECTIVES = KNOWN_PRIVACY_DIRECTIVES
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_POLICY_VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class PrincipalBindingProtocolError(ValueError):
    """A stable, content-free failure from the dormant compiler."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AuthenticatedHaUserSnapshot:
    """Server-supplied output from the authenticated HA ``whoami`` view.

    ``authorization_commitment`` is a semantic state commitment and must omit
    the per-read ``snapshot_id`` and ``observed_at`` metadata.
    """

    snapshot_id: uuid.UUID = field(repr=False)
    ha_user_id: uuid.UUID = field(repr=False)
    display_label: str = field(repr=False)
    snapshot_commitment: str = field(repr=False)
    authorization_commitment: str = field(repr=False)
    observed_at: datetime = field(repr=False)
    snapshot_origin: str
    authentication_status: str
    user_status: str


@dataclass(frozen=True, slots=True)
class ReviewedPersonNominee:
    """One reviewed person proposed as the authenticated user's identity.

    The privacy and erasure commitments describe semantic state and must omit
    the per-read ``status_observed_at`` metadata so unchanged state can be
    checked against a fresh observation. The nominee remains an
    ``operator_review_candidate``; this object establishes neither nomination
    authority nor a ``me`` identity.
    """

    nominee_id: uuid.UUID = field(repr=False)
    migration_run_id: uuid.UUID = field(repr=False)
    finalization_id: uuid.UUID = field(repr=False)
    person_id: uuid.UUID = field(repr=False)
    person_display_label: str = field(repr=False)
    nominee_commitment: str = field(repr=False)
    projection_commitment: str = field(repr=False)
    finalization_commitment: str = field(repr=False)
    review_receipt_commitment: str = field(repr=False)
    privacy_state_commitment: str = field(repr=False)
    erasure_state_commitment: str = field(repr=False)
    status_observed_at: datetime = field(repr=False)
    nomination_role: str
    review_status: str
    finalization_status: str
    nomination_authority_status: str
    person_status: str
    retrieval_blocked: bool
    privacy_directives: tuple[str, ...] = field(default=(), repr=False)
    erasure_status: str = "clear"
    open_erasure_request_count: int = 0
    pending_erasure_task_count: int = 0


@dataclass(frozen=True, slots=True)
class PrincipalBindingGraphSnapshot:
    """Current server-read cardinalities for the exact HA user/person pair.

    ``graph_digest`` is a semantic state digest and must omit ``observed_at``.
    """

    ha_user_id: uuid.UUID = field(repr=False)
    person_id: uuid.UUID = field(repr=False)
    graph_digest: str = field(repr=False)
    observed_at: datetime = field(repr=False)
    reviewed_person_nominee_count: int
    active_ha_user_binding_count: int
    active_person_binding_count: int
    other_open_request_count: int
    other_ready_proposal_count: int
    conflicting_person_nominee_count: int


@dataclass(frozen=True, slots=True)
class PrincipalBindingContext:
    """One server-assembled point-in-time input to the dormant compiler."""

    request_id: uuid.UUID = field(repr=False)
    ha_user: AuthenticatedHaUserSnapshot = field(repr=False)
    person_nominees: tuple[ReviewedPersonNominee, ...] = field(repr=False)
    graph: PrincipalBindingGraphSnapshot = field(repr=False)
    policy_version: str
    policy_digest: str = field(repr=False)
    staged_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PrincipalBindingPreview:
    """Private display material with all authority switches locked off."""

    request_id: uuid.UUID = field(repr=False)
    title: str = field(repr=False)
    statement: str = field(repr=False)
    safety_note: str = field(repr=False)
    action_label: str = field(repr=False)
    expires_at: datetime
    preview_digest: str = field(repr=False)
    contract_version: str = field(default=CONTRACT_VERSION, init=False)
    rule_version: str = field(default=RULE_VERSION, init=False)
    deployment_status: str = field(default=DEPLOYMENT_STATUS, init=False)
    privacy_scope: str = field(default=PRIVACY_SCOPE, init=False)
    nominee_count: int = field(default=1, init=False)
    authoritative: bool = field(default=False, init=False)
    commit_ready: bool = field(default=False, init=False)
    enables_writes: bool = field(default=False, init=False)
    me_identity_established: bool = field(default=False, init=False)
    binding_created: bool = field(default=False, init=False)
    location_memory_enabled: bool = field(default=False, init=False)
    travel_greetings_enabled: bool = field(default=False, init=False)
    external_receipts_verified: bool = field(default=False, init=False)
    fresh_state_origin_verified: bool = field(default=False, init=False)
    nomination_authority_verified: bool = field(default=False, init=False)
    capability_status: str = field(default=CAPABILITY_STATUS, init=False)

    def private_payload(self) -> dict[str, object]:
        """Return content intended only for the authenticated private surface."""

        return {
            "request_id": str(self.request_id),
            "contract_version": self.contract_version,
            "rule_version": self.rule_version,
            "deployment_status": self.deployment_status,
            "privacy_scope": self.privacy_scope,
            "title": self.title,
            "statement": self.statement,
            "safety_note": self.safety_note,
            "action_label": self.action_label,
            "expires_at": _utc_text(self.expires_at),
            "nominee_count": self.nominee_count,
            "authoritative": self.authoritative,
            "commit_ready": self.commit_ready,
            "enables_writes": self.enables_writes,
            "me_identity_established": self.me_identity_established,
            "binding_created": self.binding_created,
            "location_memory_enabled": self.location_memory_enabled,
            "travel_greetings_enabled": self.travel_greetings_enabled,
            "external_receipts_verified": self.external_receipts_verified,
            "fresh_state_origin_verified": self.fresh_state_origin_verified,
            "nomination_authority_verified": self.nomination_authority_verified,
            "capability_status": self.capability_status,
        }

    def private_payload_with_digest(self) -> dict[str, object]:
        return {**self.private_payload(), "preview_digest": self.preview_digest}

    def content_free_audit_payload(self) -> dict[str, object]:
        """Return categorical metadata with no account, person, or label data."""

        return {
            "contract_version": self.contract_version,
            "rule_version": self.rule_version,
            "deployment_status": self.deployment_status,
            "nominee_count": self.nominee_count,
            "authoritative": self.authoritative,
            "commit_ready": self.commit_ready,
            "enables_writes": self.enables_writes,
            "me_identity_established": self.me_identity_established,
            "binding_created": self.binding_created,
            "location_memory_enabled": self.location_memory_enabled,
            "travel_greetings_enabled": self.travel_greetings_enabled,
            "external_receipts_verified": self.external_receipts_verified,
            "fresh_state_origin_verified": self.fresh_state_origin_verified,
            "nomination_authority_verified": self.nomination_authority_verified,
            "capability_status": self.capability_status,
            "preview_digest": self.preview_digest,
        }


@dataclass(frozen=True, slots=True)
class DormantPrincipalBindingIntent:
    """Verified preview drift result; deliberately unusable as write authority."""

    request_id: uuid.UUID = field(repr=False)
    ha_user_id: uuid.UUID = field(repr=False)
    person_id: uuid.UUID = field(repr=False)
    nominee_id: uuid.UUID = field(repr=False)
    finalization_id: uuid.UUID = field(repr=False)
    ha_snapshot_id: uuid.UUID = field(repr=False)
    ha_snapshot_observed_at: datetime = field(repr=False)
    nominee_status_observed_at: datetime = field(repr=False)
    graph_observed_at: datetime = field(repr=False)
    verified_at: datetime = field(repr=False)
    proposal_digest: str = field(repr=False)
    verification_state_digest: str = field(repr=False)
    graph_digest: str = field(repr=False)
    nominee_commitment: str = field(repr=False)
    finalization_commitment: str = field(repr=False)
    contract_version: str = field(default=CONTRACT_VERSION, init=False)
    rule_version: str = field(default=RULE_VERSION, init=False)
    deployment_status: str = field(default=DEPLOYMENT_STATUS, init=False)
    nominee_count: int = field(default=1, init=False)
    requires_serializable_commit: bool = field(default=True, init=False)
    requires_fresh_authenticated_ha_snapshot: bool = field(default=True, init=False)
    requires_fresh_binding_graph_snapshot: bool = field(default=True, init=False)
    requires_transaction_time_state_read: bool = field(default=True, init=False)
    requires_external_authenticated_gesture: bool = field(default=True, init=False)
    requires_single_use_artifact_consumption: bool = field(default=True, init=False)
    me_identity_established: bool = field(default=False, init=False)
    binding_created: bool = field(default=False, init=False)
    authoritative: bool = field(default=False, init=False)
    commit_ready: bool = field(default=False, init=False)
    enables_writes: bool = field(default=False, init=False)
    location_memory_enabled: bool = field(default=False, init=False)
    travel_greetings_enabled: bool = field(default=False, init=False)
    external_receipts_verified: bool = field(default=False, init=False)
    fresh_state_origin_verified: bool = field(default=False, init=False)
    nomination_authority_verified: bool = field(default=False, init=False)
    capability_status: str = field(default=CAPABILITY_STATUS, init=False)


@dataclass(frozen=True, slots=True)
class _ValidatedContext:
    ha_label: str = field(repr=False)
    person_label: str = field(repr=False)
    nominee: ReviewedPersonNominee = field(repr=False)
    staged_at: datetime
    expires_at: datetime
    trusted_state_times: tuple[datetime, ...] = field(repr=False)


def _fail(code: str) -> NoReturn:
    raise PrincipalBindingProtocolError(code)


def _require_uuid(value: uuid.UUID) -> None:
    if type(value) is not uuid.UUID or value.int == 0 or value.variant != uuid.RFC_4122:
        _fail("invalid_identifier")


def _require_uuid7(value: uuid.UUID) -> None:
    _require_uuid(value)
    if value.version != 7:
        _fail("invalid_identifier")


def _require_hex64(value: str, *, code: str = "invalid_commitment") -> None:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        _fail(code)


def _utc(value: datetime, *, code: str = "invalid_time_window") -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
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
    if type(value) is not str or value != value.strip():
        _fail("invalid_display_label")
    if value != unicodedata.normalize("NFC", value):
        _fail("invalid_display_label")
    if not value or len(value) > 255:
        _fail("invalid_display_label")
    if any(unicodedata.category(char).startswith("C") for char in value):
        _fail("invalid_display_label")
    return value


def _privacy_is_eligible(directives: Iterable[str]) -> bool:
    if type(directives) is not tuple:
        return False
    observed = directives
    if any(type(value) is not str for value in observed):
        return False
    directive_set = set(observed)
    return (
        len(directive_set) == len(observed)
        and directive_set.issubset(KNOWN_PRIVACY_DIRECTIVES)
        and not (directive_set & BLOCKING_PRIVACY_DIRECTIVES)
    )


def _require_zero_count(value: int) -> None:
    if type(value) is not int or value != 0:
        _fail("binding_graph_conflict")


def _require_literal(value: str, expected: str, *, code: str) -> None:
    if type(value) is not str or value != expected:
        _fail(code)


def _require_semantic_id_invariants(
    *,
    request_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    nominee_id: uuid.UUID,
    migration_run_id: uuid.UUID,
    finalization_id: uuid.UUID,
    ha_user_id: uuid.UUID,
    person_id: uuid.UUID,
) -> None:
    # The finalization is the terminal step of the same signed migration-run
    # activity, so those two fields intentionally share one UUID. Every other
    # identifier names a distinct semantic entity or activity.
    if migration_run_id != finalization_id:
        _fail("invalid_identifier")
    semantic_ids = (
        request_id,
        snapshot_id,
        nominee_id,
        migration_run_id,
        ha_user_id,
        person_id,
    )
    if len(set(semantic_ids)) != len(semantic_ids):
        _fail("invalid_identifier")


def _validate_context(context: PrincipalBindingContext) -> _ValidatedContext:
    if type(context) is not PrincipalBindingContext:
        _fail("invalid_context")
    if type(context.ha_user) is not AuthenticatedHaUserSnapshot:
        _fail("invalid_ha_snapshot")
    if type(context.graph) is not PrincipalBindingGraphSnapshot:
        _fail("invalid_binding_graph")

    _require_uuid7(context.request_id)
    ha_user = context.ha_user
    _require_uuid7(ha_user.snapshot_id)
    _require_uuid(ha_user.ha_user_id)
    _require_literal(
        ha_user.snapshot_origin, HA_SNAPSHOT_ORIGIN, code="invalid_ha_snapshot"
    )
    _require_literal(
        ha_user.authentication_status, "authenticated", code="invalid_ha_snapshot"
    )
    _require_literal(ha_user.user_status, "active", code="invalid_ha_snapshot")
    ha_label = _display_label(ha_user.display_label)
    _require_hex64(ha_user.snapshot_commitment)
    _require_hex64(ha_user.authorization_commitment)

    if type(context.person_nominees) is not tuple or len(context.person_nominees) != 1:
        _fail("invalid_person_nominee_set")
    nominee = context.person_nominees[0]
    if type(nominee) is not ReviewedPersonNominee:
        _fail("invalid_person_nominee")
    _require_uuid7(nominee.nominee_id)
    _require_uuid7(nominee.migration_run_id)
    _require_uuid7(nominee.finalization_id)
    _require_uuid(nominee.person_id)
    _require_semantic_id_invariants(
        request_id=context.request_id,
        snapshot_id=ha_user.snapshot_id,
        nominee_id=nominee.nominee_id,
        migration_run_id=nominee.migration_run_id,
        finalization_id=nominee.finalization_id,
        ha_user_id=ha_user.ha_user_id,
        person_id=nominee.person_id,
    )
    _require_literal(
        nominee.nomination_role,
        "operator_review_candidate",
        code="invalid_person_nominee",
    )
    _require_literal(nominee.review_status, "reviewed", code="invalid_person_nominee")
    _require_literal(
        nominee.finalization_status, "finalized", code="invalid_person_nominee"
    )
    _require_literal(
        nominee.nomination_authority_status,
        "unverified",
        code="invalid_person_nominee",
    )
    _require_literal(nominee.person_status, "active", code="invalid_person_nominee")
    if type(nominee.retrieval_blocked) is not bool:
        _fail("invalid_person_nominee")
    if nominee.retrieval_blocked:
        _fail("privacy_blocked")
    if not _privacy_is_eligible(nominee.privacy_directives):
        _fail("privacy_blocked")
    _require_literal(nominee.erasure_status, "clear", code="erasure_blocked")
    if (
        type(nominee.open_erasure_request_count) is not int
        or nominee.open_erasure_request_count != 0
        or type(nominee.pending_erasure_task_count) is not int
        or nominee.pending_erasure_task_count != 0
    ):
        _fail("erasure_blocked")
    person_label = _display_label(nominee.person_display_label)
    for commitment in (
        nominee.nominee_commitment,
        nominee.projection_commitment,
        nominee.finalization_commitment,
        nominee.review_receipt_commitment,
        nominee.privacy_state_commitment,
        nominee.erasure_state_commitment,
    ):
        _require_hex64(commitment)

    graph = context.graph
    _require_uuid(graph.ha_user_id)
    _require_uuid(graph.person_id)
    _require_hex64(graph.graph_digest, code="invalid_binding_graph")
    if graph.ha_user_id != ha_user.ha_user_id or graph.person_id != nominee.person_id:
        _fail("invalid_binding_graph")
    if (
        type(graph.reviewed_person_nominee_count) is not int
        or graph.reviewed_person_nominee_count != 1
    ):
        _fail("invalid_person_nominee_set")
    for count in (
        graph.active_ha_user_binding_count,
        graph.active_person_binding_count,
        graph.other_open_request_count,
        graph.other_ready_proposal_count,
        graph.conflicting_person_nominee_count,
    ):
        _require_zero_count(count)

    _require_hex64(context.policy_digest, code="invalid_policy")
    if (
        type(context.policy_version) is not str
        or _POLICY_VERSION.fullmatch(context.policy_version) is None
    ):
        _fail("invalid_policy")

    staged_at = _utc(context.staged_at)
    expires_at = _utc(context.expires_at)
    trusted_state_times = (
        _utc(ha_user.observed_at, code="invalid_trusted_state_time"),
        _utc(nominee.status_observed_at, code="invalid_trusted_state_time"),
        _utc(graph.observed_at, code="invalid_trusted_state_time"),
    )
    if expires_at <= staged_at or expires_at - staged_at > MAX_PREVIEW_LIFETIME:
        _fail("invalid_time_window")
    return _ValidatedContext(
        ha_label=ha_label,
        person_label=person_label,
        nominee=nominee,
        staged_at=staged_at,
        expires_at=expires_at,
        trusted_state_times=trusted_state_times,
    )


def _require_current_time(
    trusted_operation_time: datetime,
    validated: _ValidatedContext,
    *,
    require_staged_snapshot: bool,
) -> datetime:
    operation_time = _utc(trusted_operation_time, code="invalid_trusted_operation_time")
    if operation_time < validated.staged_at:
        _fail("preview_not_current")
    if operation_time >= validated.expires_at:
        _fail("preview_expired")
    for observed_at in validated.trusted_state_times:
        if require_staged_snapshot and observed_at > validated.staged_at:
            _fail("stale_trusted_state")
        if (
            operation_time < observed_at
            or operation_time - observed_at > MAX_TRUSTED_STATE_AGE
        ):
            _fail("stale_trusted_state")
    return operation_time


def _private_preview(
    context: PrincipalBindingContext, validated: _ValidatedContext
) -> PrincipalBindingPreview:
    return PrincipalBindingPreview(
        request_id=context.request_id,
        title="Review your Home Assistant identity",
        statement=(
            f"Review linking the signed-in Home Assistant account "
            f"{validated.ha_label} to the reviewed person "
            f"{validated.person_label} proposed as your identity."
        ),
        safety_note=(
            "This nominee is not an established 'me' identity. This preview "
            "creates no binding, grants no memory access, and does not enable "
            "location or agent writes."
        ),
        action_label="Review identity link",
        expires_at=validated.expires_at,
        preview_digest="",
    )


def _nominee_preview_material(
    nominee: ReviewedPersonNominee, label: str
) -> dict[str, object]:
    return {
        "nominee_id": str(nominee.nominee_id),
        "migration_run_id": str(nominee.migration_run_id),
        "finalization_id": str(nominee.finalization_id),
        "person_id": str(nominee.person_id),
        "person_display_label": label,
        "nominee_commitment": nominee.nominee_commitment,
        "projection_commitment": nominee.projection_commitment,
        "finalization_commitment": nominee.finalization_commitment,
        "review_receipt_commitment": nominee.review_receipt_commitment,
        "privacy_state_commitment": nominee.privacy_state_commitment,
        "erasure_state_commitment": nominee.erasure_state_commitment,
        "nomination_role": nominee.nomination_role,
        "review_status": nominee.review_status,
        "finalization_status": nominee.finalization_status,
        "nomination_authority_status": nominee.nomination_authority_status,
        "person_status": nominee.person_status,
        "retrieval_blocked": nominee.retrieval_blocked,
        "privacy_directives": sorted(nominee.privacy_directives),
        "erasure_status": nominee.erasure_status,
        "open_erasure_request_count": nominee.open_erasure_request_count,
        "pending_erasure_task_count": nominee.pending_erasure_task_count,
    }


def _bound_material(
    context: PrincipalBindingContext,
    validated: _ValidatedContext,
    preview: PrincipalBindingPreview,
) -> dict[str, object]:
    ha_user = context.ha_user
    graph = context.graph
    return {
        "domain": "home-agent:principal-binding:dormant-preview:v2",
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
        "ha_user_snapshot": {
            "ha_user_id": str(ha_user.ha_user_id),
            "display_label": validated.ha_label,
            "authorization_commitment": ha_user.authorization_commitment,
            "snapshot_origin": ha_user.snapshot_origin,
            "authentication_status": ha_user.authentication_status,
            "user_status": ha_user.user_status,
        },
        "reviewed_person_nominee_count": 1,
        "reviewed_person_nominee": _nominee_preview_material(
            validated.nominee, validated.person_label
        ),
        "binding_graph": {
            "ha_user_id": str(graph.ha_user_id),
            "person_id": str(graph.person_id),
            "graph_digest": graph.graph_digest,
            "reviewed_person_nominee_count": graph.reviewed_person_nominee_count,
            "active_ha_user_binding_count": graph.active_ha_user_binding_count,
            "active_person_binding_count": graph.active_person_binding_count,
            "other_open_request_count": graph.other_open_request_count,
            "other_ready_proposal_count": graph.other_ready_proposal_count,
            "conflicting_person_nominee_count": (
                graph.conflicting_person_nominee_count
            ),
        },
        "private_display": preview.private_payload(),
        "locked_authority": {
            "capability_status": CAPABILITY_STATUS,
            "authoritative": False,
            "commit_ready": False,
            "enables_writes": False,
            "me_identity_established": False,
            "binding_created": False,
            "location_memory_enabled": False,
            "travel_greetings_enabled": False,
            "external_receipts_verified": False,
            "fresh_state_origin_verified": False,
            "nomination_authority_verified": False,
        },
    }


def _preview_digest(material: dict[str, object], digest_key: bytes) -> str:
    if type(digest_key) is not bytes or len(digest_key) != 32:
        _fail("invalid_digest_key")
    purpose_key = hmac.new(
        digest_key,
        b"home-agent:dormant-principal-binding-preview:v2",
        hashlib.sha256,
    ).digest()
    return hmac.new(purpose_key, canonical_json(material), hashlib.sha256).hexdigest()


def _verification_state_digest(
    context: PrincipalBindingContext,
    validated: _ValidatedContext,
    *,
    proposal_digest: str,
    verified_at: datetime,
    digest_key: bytes,
) -> str:
    ha_user = context.ha_user
    nominee = validated.nominee
    graph = context.graph
    material = {
        "domain": "home-agent:principal-binding:fresh-verification-state:v2",
        "contract_version": CONTRACT_VERSION,
        "rule_version": RULE_VERSION,
        "request_id": str(context.request_id),
        "proposal_digest": proposal_digest,
        "staged_at": _utc_text(validated.staged_at),
        "expires_at": _utc_text(validated.expires_at),
        "verified_at": _utc_text(verified_at),
        "ha_user_snapshot": {
            "snapshot_id": str(ha_user.snapshot_id),
            "ha_user_id": str(ha_user.ha_user_id),
            "display_label": validated.ha_label,
            "snapshot_commitment": ha_user.snapshot_commitment,
            "authorization_commitment": ha_user.authorization_commitment,
            "observed_at": _utc_text(ha_user.observed_at),
            "snapshot_origin": ha_user.snapshot_origin,
            "authentication_status": ha_user.authentication_status,
            "user_status": ha_user.user_status,
        },
        "reviewed_person_nominee_state": {
            **_nominee_preview_material(nominee, validated.person_label),
            "observed_at": _utc_text(nominee.status_observed_at),
        },
        "binding_graph": {
            "ha_user_id": str(graph.ha_user_id),
            "person_id": str(graph.person_id),
            "graph_digest": graph.graph_digest,
            "observed_at": _utc_text(graph.observed_at),
            "reviewed_person_nominee_count": graph.reviewed_person_nominee_count,
            "active_ha_user_binding_count": graph.active_ha_user_binding_count,
            "active_person_binding_count": graph.active_person_binding_count,
            "other_open_request_count": graph.other_open_request_count,
            "other_ready_proposal_count": graph.other_ready_proposal_count,
            "conflicting_person_nominee_count": (
                graph.conflicting_person_nominee_count
            ),
        },
        "policy": {
            "version": context.policy_version,
            "digest": context.policy_digest,
        },
        "locked_authority": {
            "deployment_status": DEPLOYMENT_STATUS,
            "capability_status": CAPABILITY_STATUS,
            "nominee_count": 1,
            "requires_serializable_commit": True,
            "requires_fresh_authenticated_ha_snapshot": True,
            "requires_fresh_binding_graph_snapshot": True,
            "requires_transaction_time_state_read": True,
            "requires_external_authenticated_gesture": True,
            "requires_single_use_artifact_consumption": True,
            "me_identity_established": False,
            "binding_created": False,
            "authoritative": False,
            "commit_ready": False,
            "enables_writes": False,
            "location_memory_enabled": False,
            "travel_greetings_enabled": False,
            "external_receipts_verified": False,
            "fresh_state_origin_verified": False,
            "nomination_authority_verified": False,
        },
    }
    if type(digest_key) is not bytes or len(digest_key) != 32:
        _fail("invalid_digest_key")
    purpose_key = hmac.new(
        digest_key,
        b"home-agent:dormant-principal-binding-verification-state:v2",
        hashlib.sha256,
    ).digest()
    return hmac.new(purpose_key, canonical_json(material), hashlib.sha256).hexdigest()


def compile_principal_binding_preview(
    context: PrincipalBindingContext,
    *,
    digest_key: bytes,
    trusted_operation_time: datetime,
) -> PrincipalBindingPreview:
    """Compile current private display content without creating authority."""

    validated = _validate_context(context)
    _require_current_time(
        trusted_operation_time, validated, require_staged_snapshot=True
    )
    return _compile_validated_preview(context, validated, digest_key=digest_key)


def _compile_validated_preview(
    context: PrincipalBindingContext,
    validated: _ValidatedContext,
    *,
    digest_key: bytes,
) -> PrincipalBindingPreview:
    preview = _private_preview(context, validated)
    digest = _preview_digest(_bound_material(context, validated, preview), digest_key)
    return PrincipalBindingPreview(
        request_id=preview.request_id,
        title=preview.title,
        statement=preview.statement,
        safety_note=preview.safety_note,
        action_label=preview.action_label,
        expires_at=preview.expires_at,
        preview_digest=digest,
    )


def verify_principal_binding_preview(
    preview: PrincipalBindingPreview,
    *,
    supplied_digest: str,
    current_context: PrincipalBindingContext,
    digest_key: bytes,
    trusted_operation_time: datetime,
) -> DormantPrincipalBindingIntent:
    """Verify supplied current state against preview drift; never authorize.

    This pure function cannot prove that ``current_context`` came from a new
    server read. The returned intent therefore keeps fresh-state origin false
    and requires another transaction-time state read in a future commit kernel.
    """

    validated = _validate_context(current_context)
    operation_time = _require_current_time(
        trusted_operation_time, validated, require_staged_snapshot=False
    )
    if (
        type(supplied_digest) is not str
        or _HEX64.fullmatch(supplied_digest) is None
        or type(preview) is not PrincipalBindingPreview
        or type(preview.preview_digest) is not str
        or _HEX64.fullmatch(preview.preview_digest) is None
    ):
        _fail("preview_mismatch")

    expected = _compile_validated_preview(
        current_context,
        validated,
        digest_key=digest_key,
    )
    try:
        observed_payload = canonical_json(preview.private_payload())
    except (AttributeError, TypeError, ValueError):
        _fail("preview_mismatch")
    if not hmac.compare_digest(
        observed_payload, canonical_json(expected.private_payload())
    ):
        _fail("preview_mismatch")
    if not (
        hmac.compare_digest(preview.preview_digest, supplied_digest)
        and hmac.compare_digest(expected.preview_digest, supplied_digest)
    ):
        _fail("preview_mismatch")

    nominee = validated.nominee
    verification_state_digest = _verification_state_digest(
        current_context,
        validated,
        proposal_digest=supplied_digest,
        verified_at=operation_time,
        digest_key=digest_key,
    )
    return DormantPrincipalBindingIntent(
        request_id=current_context.request_id,
        ha_user_id=current_context.ha_user.ha_user_id,
        person_id=nominee.person_id,
        nominee_id=nominee.nominee_id,
        finalization_id=nominee.finalization_id,
        ha_snapshot_id=current_context.ha_user.snapshot_id,
        ha_snapshot_observed_at=_utc(current_context.ha_user.observed_at),
        nominee_status_observed_at=_utc(nominee.status_observed_at),
        graph_observed_at=_utc(current_context.graph.observed_at),
        verified_at=operation_time,
        proposal_digest=supplied_digest,
        verification_state_digest=verification_state_digest,
        graph_digest=current_context.graph.graph_digest,
        nominee_commitment=nominee.nominee_commitment,
        finalization_commitment=nominee.finalization_commitment,
    )
