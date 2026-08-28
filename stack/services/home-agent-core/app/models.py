from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Imported rather than restated: the predicate vocabulary must have exactly one
# definition. A view that could name a predicate the context layer does not
# admit would be a second, weaker gate on what reaches the model.
from .context import ContextPredicate


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value.astimezone(UTC)


Freshness = Literal["fresh", "aging", "stale", "not_applicable"]
# Mirrors the fact_versions authority CHECK constraint. An owner-asserted fact
# is authorized_administrator; the two parent facts committed by the E5f kernel
# are explicit_related_party, because a second party confirmed them.
Authority = Literal[
    "explicit_subject",
    "explicit_related_party",
    "authorized_administrator",
    "sensor",
    "derived_rule",
    "model_proposal",
    "legacy_unverified",
]
Coverage = Literal["sufficient", "partial", "gap", "not_applicable"]
RolloutMode = Literal["record_only", "shadow", "canary"]
OnboardingState = Literal[
    "bound",
    "collecting_evidence",
    "identity_confirmation_required",
    "contained",
]
OnboardingPhase2Blocker = Literal[
    "rollout_mode_not_record_only",
    "no_durable_envelopes",
    "minimum_observation_window_not_elapsed",
    "qualifying_redacted_envelope_threshold_not_met",
    "worker_maintenance_not_current",
]
WorkerMaintenanceCode = Literal[
    "current",
    "missing",
    "instance_unobserved",
    "heartbeat_stale",
    "maintenance_missing",
    "maintenance_failed",
    "maintenance_stale",
    "stopped",
    "future",
    "kernel_mismatch",
    "unavailable",
]
Phase3ShadowPredecessorStatus = Literal[
    "authorized",
    "mode_not_shadow",
    "missing",
    "invalid",
    "unavailable",
]
Phase3ReadinessBlocker = Literal[
    "rollout_mode_not_shadow",
    "shadow_predecessor_not_authorized",
    "semantic_people_migration_manifest_not_recorded",
    "semantic_people_migration_completion_not_recorded",
    "privacy_cutover_not_recorded",
    "legacy_semantic_write_freeze_not_recorded",
    "subject_binding_not_evaluated",
    "parent_confirmation_protocol_capability_disabled",
]
PHASE3_FIXED_READINESS_BLOCKERS: tuple[Phase3ReadinessBlocker, ...] = (
    "semantic_people_migration_manifest_not_recorded",
    "semantic_people_migration_completion_not_recorded",
    "privacy_cutover_not_recorded",
    "legacy_semantic_write_freeze_not_recorded",
    "subject_binding_not_evaluated",
    "parent_confirmation_protocol_capability_disabled",
)


class HaContext(StrictModel):
    id: str | None = Field(default=None, max_length=128)
    user_id: str | None = Field(default=None, max_length=128)
    parent_id: str | None = Field(default=None, max_length=128)


class IngestEnvelope(StrictModel):
    edge_instance_id: str = Field(min_length=1, max_length=128)
    source_name: str = Field(min_length=1, max_length=128)
    epoch: uuid.UUID
    sequence: int = Field(gt=0)
    event_type: str = Field(min_length=1, max_length=128)
    entity_id: str | None = Field(default=None, max_length=255)
    source_event_id: str | None = Field(default=None, max_length=255)
    source_observed_at: datetime
    edge_received_at: datetime
    payload: dict[str, Any]
    root_observation_id: uuid.UUID | None = None
    evidence_family_id: uuid.UUID | None = None
    dependency_domain: str = Field(min_length=1, max_length=128)
    coverage: Literal[
        "continuous", "recorder_reconstructed", "snapshot_only", "gap", "unknown"
    ]
    clock_state: Literal["synchronized", "skewed", "unknown"] = "unknown"
    ha_context: HaContext = Field(default_factory=HaContext)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _source_time = field_validator("source_observed_at")(_aware)
    _received_time = field_validator("edge_received_at")(_aware)


class IngestBatch(StrictModel):
    envelopes: list[IngestEnvelope] = Field(min_length=1, max_length=500)


class IngestResult(StrictModel):
    accepted: int
    duplicates: int
    quarantined: int
    acknowledgements: dict[str, int]
    opened_gaps: list[str] = Field(default_factory=list)


class EdgePrivacyPolicyView(StrictModel):
    version: Literal[1] = 1
    blocked_entity_ids: list[str]
    blocked_user_ids: list[str]
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class PersonCreate(StrictModel):
    person_id: uuid.UUID | None = None
    display_name: str = Field(min_length=1, max_length=255)
    pronouns: str | None = Field(default=None, max_length=64)
    privacy_scope: Literal["private", "household"] = "private"
    legacy_source_ref: str | None = Field(default=None, max_length=255)
    legacy_source_version: int | None = Field(default=None, ge=0)
    legacy_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class PersonView(StrictModel):
    person_id: uuid.UUID
    display_name: str
    privacy_scope: str
    status: str


class OwnerPartnerAttestation(StrictModel):
    """What the owner asserts, and nothing more.

    No authority field: the kernel writes 'authorized_administrator' as a
    literal, so a caller can never claim a confirmation that did not happen.
    No identifiers either -- the adapter derives every primary key from the
    ceremony seed.
    """

    ceremony_id: uuid.UUID
    partner_person_id: uuid.UUID
    attestation_nonce: uuid.UUID

    @field_validator("ceremony_id")
    @classmethod
    def _ceremony_is_uuid7(cls, value: uuid.UUID) -> uuid.UUID:
        # Sortable and timestamped, matching the other ceremonies; the adapter
        # also relies on the embedded timestamp when deriving identifiers.
        if value.version != 7:
            raise ValueError("ceremony_id must be a UUIDv7")
        return value

    @field_validator("attestation_nonce")
    @classmethod
    def _nonce_is_uuid4(cls, value: uuid.UUID) -> uuid.UUID:
        # v4 only: a v7 nonce would leak the wall-clock time of the attestation
        # into a value that is otherwise unlinkable.
        if value.version != 4:
            raise ValueError("attestation_nonce must be a UUIDv4")
        return value


class OwnerPartnerAttestationView(StrictModel):
    receipt_id: uuid.UUID
    partner_person_id: uuid.UUID
    document_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
class PeopleDirectoryEntry(StrictModel):
    """One person as the People tab may show them.

    Deliberately narrower than the row: no legacy source refs, no timestamps.
    The tab is a view of who the system believes exists, not of how it came to
    believe it -- legacy evidence is not authority and does not leave the
    database.
    """

    person_id: uuid.UUID
    display_name: str
    pronouns: str | None = None
    status: str
    privacy_scope: str
    is_self: bool


class PeopleDirectoryView(StrictModel):
    people: list[PeopleDirectoryEntry] = Field(max_length=500)


class RelationshipEntry(StrictModel):
    """One committed relationship fact, resolved to display names.

    ``predicate`` is a closed vocabulary, not free text: it is the same value
    the context layer admits, so nothing reaches this view that could not
    already reach the model.
    """

    fact_id: uuid.UUID
    predicate: ContextPredicate
    subject_person_id: uuid.UUID
    subject_display_name: str
    object_person_id: uuid.UUID
    object_display_name: str
    authority: Authority
    committed_at: datetime

    @field_validator("committed_at")
    @classmethod
    def _committed_at_aware(cls, value: datetime) -> datetime:
        return _aware(value)


class RelationshipsView(StrictModel):
    relationships: list[RelationshipEntry] = Field(max_length=500)


class OwnerPersonCreate(StrictModel):
    """A person the owner adds, and the privacy state they are added with.

    No status field: the kernel writes 'active' as a literal, so nobody can be
    created already erased or in a state no code expects. No identifiers
    either -- the adapter derives them.
    """

    ceremony_id: uuid.UUID
    display_name: str = Field(min_length=1, max_length=255)
    pronouns: str | None = Field(default=None, max_length=64)
    privacy_scope: Literal["private", "household"] = "private"
    directive: (
        Literal["do_not_track", "ignored", "silent", "private", "auto_expire"] | None
    ) = None
    directive_expires_at: datetime | None = None

    @field_validator("ceremony_id")
    @classmethod
    def _ceremony_is_uuid7(cls, value: uuid.UUID) -> uuid.UUID:
        if value.version != 7:
            raise ValueError("ceremony_id must be a UUIDv7")
        return value

    @field_validator("directive_expires_at")
    @classmethod
    def _expiry_is_aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value)

    @model_validator(mode="after")
    def _expiry_matches_directive(self) -> "OwnerPersonCreate":
        # An auto-expiring person with no expiry never expires, which is the
        # opposite of what was asked for. The kernel refuses this too; failing
        # here gives a usable message instead of a database error.
        if self.directive == "auto_expire" and self.directive_expires_at is None:
            raise ValueError("auto_expire requires directive_expires_at")
        if self.directive != "auto_expire" and self.directive_expires_at is not None:
            raise ValueError("directive_expires_at applies only to auto_expire")
        return self


class OwnerPersonView(StrictModel):
    person_id: uuid.UUID
    display_name: str
    privacy_scope: str


class ReviewedPersonVerify(StrictModel):
    person_id: uuid.UUID
    display_name: str = Field(min_length=1, max_length=255)
    legacy_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class OperatorImportCapability(StrictModel):
    method: Literal["POST"]
    path: str
    schema_name: str = Field(alias="schema")
    source_digest_field: str | None = None
    idempotency: Literal["exact-projection-v1"] | None = None


class OperatorCapabilities(StrictModel):
    contract: Literal["legacy-identity-migration-v1"]
    audience: Literal["operator-bootstrap"]
    person_import: OperatorImportCapability
    role_import: OperatorImportCapability
    person_verify: OperatorImportCapability
    alias_import: OperatorImportCapability
    recognition_binding_import: OperatorImportCapability
    privacy_directive_import: OperatorImportCapability
    person_status_import: OperatorImportCapability
    relationship_candidate_import: OperatorImportCapability


class ReviewedAliasImport(StrictModel):
    alias: str = Field(min_length=1, max_length=255)
    alias_kind: Literal["name", "nickname"]
    source_ref: str = Field(min_length=1, max_length=255)
    source_version: int = Field(ge=0)
    source_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReviewedRecognitionBindingImport(StrictModel):
    external_system: Literal["frigate"]
    external_id: str = Field(min_length=1, max_length=255)
    status: Literal["active", "retired"]
    source_ref: str = Field(min_length=1, max_length=255)
    source_version: int = Field(ge=0)
    source_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReviewedPrivacyDirectiveImport(StrictModel):
    directive: Literal["do_not_track", "ignored", "silent", "private", "auto_expire"]
    enabled: Literal[True] = True
    expires_at: datetime | None = None
    source_ref: str = Field(min_length=1, max_length=255)
    source_version: int = Field(ge=0)
    source_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    _expires_at = field_validator("expires_at")(
        lambda value: None if value is None else _aware(value)
    )

    @field_validator("expires_at")
    @classmethod
    def validate_expiry_shape(
        cls, value: datetime | None, info: Any
    ) -> datetime | None:
        # Cross-field validation is completed in the store so validation order
        # cannot accidentally make an auto-expiry directive fail open.
        return value


class ReviewedPersonStatusImport(StrictModel):
    status: Literal["archived"]
    source_ref: str = Field(min_length=1, max_length=255)
    source_version: int = Field(ge=0)
    source_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LegacyRelationshipCandidateImport(StrictModel):
    from_person_id: uuid.UUID
    to_person_id: uuid.UUID
    relationship_label: str = Field(min_length=1, max_length=64)
    relationship_status: Literal["active", "ended", "paused"]
    source_ref: str = Field(min_length=1, max_length=255)
    source_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReviewedImportReceipt(StrictModel):
    record_id: uuid.UUID
    state: Literal["active", "retired", "archived", "scheduled"]


class RolloutStatus(StrictModel):
    mode: RolloutMode
    source: Literal["deployment_policy"] = "deployment_policy"
    semantic_people_writes: bool
    persistent_memory_writes: bool
    ingest_projection: Literal[True] = True


class OnboardingStatusView(StrictModel):
    """Content-free progress for one authenticated HA user."""

    state: OnboardingState
    rollout_mode: RolloutMode
    principal_bound: bool
    phase2_observation_days_required: Literal[7] = 7
    qualifying_redacted_envelopes_required: Literal[500] = 500
    phase2_ready: bool
    phase2_blockers: list[OnboardingPhase2Blocker]
    parent_relationship_confirmation: Literal["disabled", "enabled"] = "disabled"
    location_memory_default_off: Literal[True] = True
    travel_greetings_default_off: Literal[True] = True


class Phase2JourneyEvaluation(StrictModel):
    visit_id: uuid.UUID
    qualifies: bool
    reason_codes: list[str]


class Phase2ReadinessView(StrictModel):
    contract: Literal["phase2-record-only-gate-v3"] = "phase2-record-only-gate-v3"
    rule_version: Literal["record-only-envelope-worker-gate-v3"] = (
        "record-only-envelope-worker-gate-v3"
    )
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: str
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluated_at: datetime
    rollout_mode: RolloutMode
    started_at: datetime | None
    eligible_at: datetime | None
    elapsed_seconds: int = Field(ge=0)
    minimum_elapsed_seconds: Literal[604800] = 604800
    time_requirement_met: bool
    total_envelopes: int = Field(ge=0)
    relevant_envelopes: int = Field(ge=0)
    relevant_envelopes_required: Literal[500] = 500
    relevant_envelopes_remaining: int = Field(ge=0)
    excluded_snapshot_envelopes: int = Field(ge=0)
    excluded_gap_envelopes: int = Field(ge=0)
    quarantined_envelopes: int = Field(ge=0)
    submitted_controlled_journeys: int = Field(ge=0)
    unique_controlled_journeys: int = Field(ge=0)
    qualifying_controlled_journeys: int = Field(ge=0)
    informational_controlled_journeys_target: Literal[3] = 3
    controlled_journeys: list[Phase2JourneyEvaluation]
    evidence_requirement_met: bool
    threshold_path: Literal["events", "none"]
    worker_maintenance_status: WorkerMaintenanceCode
    worker_maintenance_current: bool
    ready_to_advance: bool
    blockers: list[str]
    qualifying_envelopes_are_redacted: Literal[True] = True
    controlled_journeys_authoritative: Literal[False] = False
    location_consent_default_off: Literal[True] = True
    snapshots_count_as_events: Literal[False] = False
    gaps_count_as_events: Literal[False] = False
    journeys_are_automatically_inferred: Literal[False] = False

    _evaluated = field_validator("evaluated_at")(_aware)


class Phase3ReadinessView(StrictModel):
    """Non-authoritative, content-free pre-Phase-3 diagnostic."""

    contract: Literal["phase3-readiness-diagnostic-v0"] = (
        "phase3-readiness-diagnostic-v0"
    )
    schema_revision: Literal["0006a_worker_lease_arbitration"] = (
        "0006a_worker_lease_arbitration"
    )
    rollout_mode: RolloutMode
    shadow_predecessor_status: Phase3ShadowPredecessorStatus
    semantic_people_migration_manifest_status: Literal["unproven"] = "unproven"
    semantic_people_migration_completion_status: Literal["unproven"] = "unproven"
    privacy_cutover_status: Literal["unproven"] = "unproven"
    legacy_semantic_write_freeze_status: Literal["unproven"] = "unproven"
    subject_binding_status: Literal["not_evaluated"] = "not_evaluated"
    parent_confirmation_protocol_status: Literal["capability_disabled"] = (
        "capability_disabled"
    )
    evidence_scope: Literal["local_database_only"] = "local_database_only"
    counts_exposed: Literal[False] = False
    identity_or_fact_state_evaluated: Literal[False] = False
    authoritative: Literal[False] = False
    enables_writes: Literal[False] = False
    ready_to_advance: Literal[False] = False
    blockers: list[Phase3ReadinessBlocker]

    @model_validator(mode="after")
    def _fixed_diagnostic_shape(self) -> "Phase3ReadinessView":
        expected: list[Phase3ReadinessBlocker] = []
        if self.rollout_mode != "shadow":
            if self.shadow_predecessor_status != "mode_not_shadow":
                raise ValueError("non-shadow rollout cannot claim a shadow predecessor")
            expected.append("rollout_mode_not_shadow")
        elif self.shadow_predecessor_status == "mode_not_shadow":
            raise ValueError("shadow rollout cannot report mode_not_shadow")
        if self.shadow_predecessor_status != "authorized":
            expected.append("shadow_predecessor_not_authorized")
        expected.extend(PHASE3_FIXED_READINESS_BLOCKERS)
        if self.blockers != expected:
            raise ValueError("Phase 3 diagnostic blockers are not canonical")
        return self


class RolloutAuthorizationRequest(StrictModel):
    operator_request_id: uuid.UUID
    expected_rule_version: Literal["record-only-envelope-worker-gate-v3"]
    expected_policy_version: str = Field(min_length=1, max_length=128)
    expected_policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("operator_request_id")
    @classmethod
    def random_request_uuid(cls, value: uuid.UUID) -> uuid.UUID:
        if value.version not in {4, 7}:
            raise ValueError("operator request ID must be a random UUIDv4 or UUIDv7")
        return value


class RolloutAuthorizationView(StrictModel):
    contract: Literal["rollout-authorization-receipt-v2"] = (
        "rollout-authorization-receipt-v2"
    )
    authorization_id: uuid.UUID
    operator_request_id: uuid.UUID
    from_mode: Literal["record_only", "shadow"]
    to_mode: Literal["shadow", "canary"]
    rule_version: str
    policy_version: str
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    worker_kernel_version: Literal["worker-maintenance-cycle-v1"]
    worker_success_sequence: int = Field(gt=0)
    worker_proof_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    readiness_evaluated_at: datetime
    authorized_at: datetime

    _readiness_evaluated = field_validator("readiness_evaluated_at")(_aware)
    _authorized = field_validator("authorized_at")(_aware)


class PrincipalView(StrictModel):
    principal_id: uuid.UUID
    person_id: uuid.UUID
    ha_user_id: str
    status: str


PrincipalBindingSubjectState = Literal[
    "not_requested",
    "awaiting_operator_review",
    "ready_for_confirmation",
    "bound",
    "unavailable",
]


class PrincipalBindingRequestAction(StrictModel):
    """An intentionally empty, strict body for subject-authenticated actions."""


class PrincipalBindingProposalView(StrictModel):
    state: PrincipalBindingSubjectState
    review_code: str | None = Field(default=None, pattern=r"^[A-HJ-NP-Z2-9]{16}$")
    reviewed_display_label: str | None = Field(default=None, max_length=255)
    confirmation_statement: str | None = Field(default=None, max_length=384)
    proposal_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime | None = None
    location_memory_default_off: Literal[True] = True
    travel_greetings_default_off: Literal[True] = True

    @field_validator("expires_at")
    @classmethod
    def _optional_expiry_aware(cls, value: datetime | None) -> datetime | None:
        return _aware(value) if value is not None else None

    @model_validator(mode="after")
    def _ready_shape(self) -> "PrincipalBindingProposalView":
        ready_values = (
            self.reviewed_display_label,
            self.confirmation_statement,
            self.proposal_digest,
            self.expires_at,
        )
        if self.state == "ready_for_confirmation":
            if any(value is None for value in ready_values):
                raise ValueError("ready binding proposal is incomplete")
            expected = (
                "Bind this authenticated Home Assistant account to "
                f"{self.reviewed_display_label}."
            )
            if self.confirmation_statement != expected:
                raise ValueError("binding confirmation statement is not canonical")
        elif any(value is not None for value in ready_values):
            raise ValueError("non-ready binding state cannot expose proposal content")
        if self.state == "awaiting_operator_review":
            if self.review_code is None:
                raise ValueError("awaiting binding request requires a review code")
        elif self.review_code is not None:
            raise ValueError("only an awaiting binding request exposes a review code")
        return self


class PrincipalBindingConfirmation(StrictModel):
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation_nonce: uuid.UUID

    @field_validator("confirmation_nonce")
    @classmethod
    def _random_nonce(cls, value: uuid.UUID) -> uuid.UUID:
        if value.version != 4:
            raise ValueError("confirmation nonce must be a random UUIDv4")
        return value


class PrincipalBindingConfirmationView(StrictModel):
    state: Literal["bound"] = "bound"
    confirmed_at: datetime
    location_memory_enabled: Literal[False] = False
    travel_greetings_enabled: Literal[False] = False

    _confirmed_at = field_validator("confirmed_at")(_aware)


class ParentRelationshipPreviewRequest(StrictModel):
    ceremony_id: uuid.UUID

    @field_validator("ceremony_id")
    @classmethod
    def _uuid7_ceremony(cls, value: uuid.UUID) -> uuid.UUID:
        if value.version != 7:
            raise ValueError("parent ceremony ID must be UUIDv7")
        return value


class ParentRelationshipPreviewCandidate(StrictModel):
    ordinal: Literal[0, 1]
    reviewed_display_label: str = Field(min_length=1, max_length=255)
    review_code: str = Field(pattern=r"^[A-HJ-NP-Z2-9]{16}$")


class ParentRelationshipPreviewView(StrictModel):
    state: Literal["ready_for_confirmation"] = "ready_for_confirmation"
    proposal_id: uuid.UUID
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime
    child_display_label: str = Field(min_length=1, max_length=255)
    candidates: list[ParentRelationshipPreviewCandidate] = Field(
        min_length=2,
        max_length=2,
    )
    confirmation_statement: str = Field(min_length=1, max_length=768)
    creates_exactly_two_parent_facts: Literal[True] = True
    does_not_assert_ownership_residence_or_presence: Literal[True] = True
    location_memory_enabled: Literal[False] = False
    travel_greetings_enabled: Literal[False] = False

    _expires_at = field_validator("expires_at")(_aware)

    @model_validator(mode="after")
    def _canonical_preview(self) -> "ParentRelationshipPreviewView":
        if [candidate.ordinal for candidate in self.candidates] != [0, 1]:
            raise ValueError("parent candidates must have canonical ordinals")
        labels = [candidate.reviewed_display_label for candidate in self.candidates]
        if len({label.casefold() for label in labels}) != 2:
            raise ValueError("parent candidate labels must be distinct")
        expected = (
            f"Confirm that {labels[0]} and {labels[1]} are parents of "
            f"{self.child_display_label}."
        )
        if self.confirmation_statement != expected:
            raise ValueError("parent confirmation statement is not canonical")
        if self.proposal_id.version != 7:
            raise ValueError("parent proposal ID must be UUIDv7")
        return self


class ParentRelationshipConfirmation(StrictModel):
    proposal_id: uuid.UUID
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation_nonce: uuid.UUID

    @model_validator(mode="after")
    def _identifier_versions(self) -> "ParentRelationshipConfirmation":
        if self.proposal_id.version != 7:
            raise ValueError("parent proposal ID must be UUIDv7")
        if self.confirmation_nonce.version != 4:
            raise ValueError("confirmation nonce must be a random UUIDv4")
        return self


class ParentRelationshipConfirmationView(StrictModel):
    state: Literal["confirmed"] = "confirmed"
    confirmed_at: datetime
    fact_count: Literal[2] = 2
    location_memory_enabled: Literal[False] = False
    travel_greetings_enabled: Literal[False] = False

    _confirmed_at = field_validator("confirmed_at")(_aware)


class ParentRelationshipStatusView(StrictModel):
    state: Literal[
        "not_started",
        "ready_for_confirmation",
        "confirmed",
    ]
    proposal_id: uuid.UUID | None = None
    proposal_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime | None = None
    child_display_label: str | None = Field(default=None, max_length=255)
    candidates: list[ParentRelationshipPreviewCandidate] = Field(
        default_factory=list,
        max_length=2,
    )
    confirmation_statement: str | None = Field(default=None, max_length=768)
    confirmed_at: datetime | None = None
    fact_count: Literal[0, 2] = 0
    creates_exactly_two_parent_facts: Literal[True] = True
    does_not_assert_ownership_residence_or_presence: Literal[True] = True
    location_memory_enabled: Literal[False] = False
    travel_greetings_enabled: Literal[False] = False

    @field_validator("expires_at", "confirmed_at")
    @classmethod
    def _optional_status_time(cls, value: datetime | None) -> datetime | None:
        return _aware(value) if value is not None else None

    @model_validator(mode="after")
    def _canonical_status(self) -> "ParentRelationshipStatusView":
        ready_values = (
            self.proposal_id,
            self.proposal_digest,
            self.expires_at,
            self.child_display_label,
            self.confirmation_statement,
        )
        if self.state == "ready_for_confirmation":
            if any(value is None for value in ready_values):
                raise ValueError("ready parent status is incomplete")
            if self.proposal_id is None or self.proposal_id.version != 7:
                raise ValueError("parent proposal ID must be UUIDv7")
            if [candidate.ordinal for candidate in self.candidates] != [0, 1]:
                raise ValueError("parent candidates must have canonical ordinals")
            labels = [candidate.reviewed_display_label for candidate in self.candidates]
            if len({label.casefold() for label in labels}) != 2:
                raise ValueError("parent candidate labels must be distinct")
            expected = (
                f"Confirm that {labels[0]} and {labels[1]} are parents of "
                f"{self.child_display_label}."
            )
            if self.confirmation_statement != expected:
                raise ValueError("parent confirmation statement is not canonical")
            if self.confirmed_at is not None or self.fact_count != 0:
                raise ValueError("ready parent status cannot be confirmed")
        elif self.state == "confirmed":
            if any(value is not None for value in ready_values):
                raise ValueError(
                    "confirmed parent status cannot expose preview content"
                )
            if self.candidates or self.confirmed_at is None or self.fact_count != 2:
                raise ValueError("confirmed parent status is incomplete")
        else:
            if (
                any(value is not None for value in ready_values)
                or self.candidates
                or self.confirmed_at is not None
                or self.fact_count != 0
            ):
                raise ValueError(
                    "not-started parent status cannot expose private content"
                )
        return self


class OperatorPrincipalBindingRequestView(StrictModel):
    request_id: uuid.UUID
    review_code: str = Field(pattern=r"^[A-HJ-NP-Z2-9]{16}$")
    state: Literal["pending"]
    requested_at: datetime
    expires_at: datetime

    _requested_at = field_validator("requested_at")(_aware)
    _expires_at = field_validator("expires_at")(_aware)


class OperatorPrincipalBindingRequestsView(StrictModel):
    requests: list[OperatorPrincipalBindingRequestView] = Field(max_length=100)


class OperatorPrincipalBindingProposalStage(StrictModel):
    request_id: uuid.UUID
    review_code: str = Field(pattern=r"^[A-HJ-NP-Z2-9]{16}$")
    person_id: uuid.UUID
    operator_request_id: uuid.UUID

    @field_validator("operator_request_id")
    @classmethod
    def _random_operator_request(cls, value: uuid.UUID) -> uuid.UUID:
        if value.version not in {4, 7}:
            raise ValueError("operator request ID must be a random UUIDv4 or UUIDv7")
        return value


class OperatorPrincipalBindingProposalView(StrictModel):
    proposal_id: uuid.UUID
    request_id: uuid.UUID
    person_id: uuid.UUID
    reviewed_display_label: str = Field(min_length=1, max_length=255)
    state: Literal["ready"]
    stage_receipt_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    staged_at: datetime
    expires_at: datetime

    _staged_at = field_validator("staged_at")(_aware)
    _expires_at = field_validator("expires_at")(_aware)


class SourceEntityBindingCreate(StrictModel):
    source_system: Literal["home_assistant"] = "home_assistant"
    entity_id: str = Field(
        pattern=r"^(person|device_tracker)\.[a-z0-9_]+$", max_length=255
    )
    confirmation_artifact_id: uuid.UUID


class LegacyRoleImport(StrictModel):
    person_id: uuid.UUID
    role_label: str = Field(min_length=1, max_length=64)
    source_ref: str = Field(min_length=1, max_length=255)
    source_version: int | None = Field(default=None, ge=0)
    source_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LocationAnchor(StrictModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_m: int = Field(ge=1, le=50_000)
    root_observation_ids: list[uuid.UUID] = Field(min_length=2)


class PlaceLocatorInput(StrictModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_m: int = Field(ge=1, le=50_000)
    confirmation_artifact_id: uuid.UUID


class PlaceCreate(StrictModel):
    canonical_name: str = Field(min_length=1, max_length=255)
    place_type: Literal["locality", "property", "room", "other"]
    parent_place_id: uuid.UUID | None = None
    privacy_scope: Literal["private", "household"] = "private"
    locator: PlaceLocatorInput | None = None
    travel_greeting_eligible: bool = False


class PrivateLocalityPreviewRequest(StrictModel):
    canonical_name: str = Field(min_length=1, max_length=255)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_m: int = Field(ge=1, le=50_000)
    travel_greeting_eligible: bool = False
    confirmation_nonce: uuid.UUID

    @field_validator("canonical_name")
    @classmethod
    def _reviewed_locality_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(ord(character) < 32 for character in normalized):
            raise ValueError("canonical locality name is invalid")
        return normalized


class PrivateLocalityConfirmRequest(PrivateLocalityPreviewRequest):
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class PrivateLocalityLocatorPreview(StrictModel):
    latitude: float
    longitude: float
    radius_m: int
    retention: Literal["encrypted_until_erased"]


class PrivateLocalityPreviewView(StrictModel):
    state: Literal["needs_confirmation"]
    canonical_name: str
    privacy_scope: Literal["private"]
    travel_greeting_eligible: bool
    locator: PrivateLocalityLocatorPreview
    preview_digest: str
    creates: list[str]
    does_not_create: list[str]


class PrivateLocalityCommitView(StrictModel):
    state: Literal["committed"]
    place_id: uuid.UUID
    canonical_name: str
    privacy_scope: Literal["private"]
    travel_greeting_eligible: bool


class PrivateLocalitySummaryView(StrictModel):
    place_id: uuid.UUID
    canonical_name: str
    travel_greeting_eligible: bool


class PrivateLocalityStatusView(StrictModel):
    state: Literal["not_configured", "configured"]
    localities: list[PrivateLocalitySummaryView]


class PreferenceUpdate(StrictModel):
    key: Literal["location_memory", "travel_greetings"]
    enabled: bool
    confirmation_artifact_id: uuid.UUID


class VisitCreate(StrictModel):
    place_id: uuid.UUID | None = None
    first_root_observation_id: uuid.UUID
    observed_from: datetime
    observed_to: datetime
    freshness: Freshness
    coverage: Coverage
    anchor: LocationAnchor

    _from = field_validator("observed_from")(_aware)
    _to = field_validator("observed_to")(_aware)


class VisitView(StrictModel):
    visit_id: uuid.UUID
    principal_id: uuid.UUID
    place_id: uuid.UUID | None
    state: str
    freshness: Freshness
    coverage: Coverage


class DescriptorPreviewRequest(StrictModel):
    visit_id: uuid.UUID
    exact_text: str = Field(min_length=1, max_length=500)


class ResolvedPersonView(StrictModel):
    person_id: uuid.UUID
    display_name: str


class DescriptorPreview(StrictModel):
    transaction_id: uuid.UUID
    state: Literal["needs_confirmation", "quarantined", "rejected"]
    preview_digest: str
    place_id: uuid.UUID | None
    exact_descriptor: str
    role_expression: str
    resolved_parent_person_ids: list[uuid.UUID]
    resolved_parents: list[ResolvedPersonView]
    unresolved_role_expression: bool
    locator: dict[str, Any]
    retained: list[str]
    not_asserted: list[str]
    verifier_results: list[dict[str, Any]]


class ConfirmMemoryRequest(StrictModel):
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation_artifact_id: uuid.UUID


class DescriptorCorrectionPreviewRequest(StrictModel):
    """Typed correction of the active parents-house descriptor.

    Omitting ``exact_text`` keeps the currently encrypted perspective-scoped
    wording while still producing a new reviewed fact version.  This supports
    refreshing the role-resolution snapshot without exposing a generic fact
    mutation API.
    """

    exact_text: str | None = Field(default=None, min_length=1, max_length=500)


class DescriptorRetractionPreviewRequest(StrictModel):
    """An intentionally empty, typed request; extra mutation fields fail."""


class DescriptorLifecycleConfirm(StrictModel):
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation_artifact_id: uuid.UUID


class DescriptorLifecyclePreview(StrictModel):
    transaction_id: uuid.UUID
    operation: Literal["correction", "retraction"]
    state: Literal["needs_confirmation"]
    descriptor_fact_id: uuid.UUID
    base_fact_version_id: uuid.UUID
    base_version: int
    new_version: int
    place_id: uuid.UUID
    current_exact_descriptor: str
    proposed_exact_descriptor: str | None
    role_expression: str
    resolved_parent_person_ids: list[uuid.UUID]
    unresolved_role_expression: bool
    retained: list[str]
    invalidated: list[str]
    not_asserted: list[str]
    verifier_results: list[dict[str, Any]]
    preview_digest: str


class MemoryTransactionView(StrictModel):
    transaction_id: uuid.UUID
    state: str
    fact_id: uuid.UUID | None = None
    place_id: uuid.UUID | None = None


class InitiativeClaim(StrictModel):
    session_id: str = Field(min_length=16, max_length=128)
    surface: Literal["private_tauri", "private_web", "voice", "shared_display"]


class InitiativeSummaryView(StrictModel):
    initiative_id: uuid.UUID
    expires_at: datetime


class InitiativeView(StrictModel):
    initiative_id: uuid.UUID
    visit_id: uuid.UUID
    state: str
    purpose: str
    template_key: str
    message: str
    expires_at: datetime


class DescriptorRelationshipView(StrictModel):
    place_id: uuid.UUID
    descriptor_fact_id: uuid.UUID | None = None
    descriptor_state: Literal["active_confirmed", "unavailable"]
    exact_descriptor: str | None = None
    role_expression: Literal["parents_of(self)"]
    role_resolution: Literal["complete", "unresolved"]
    resolved_parents: list[ResolvedPersonView]
    explanation: str
    not_asserted: list[str]


class ParentPresencePersonView(StrictModel):
    person_id: uuid.UUID
    display_name: str
    resolution: Literal["present", "unknown"]
    freshness: Freshness
    coverage: Coverage
    independent_evidence_domains: list[str]
    reason_code: str


class ParentPresenceView(StrictModel):
    place_id: uuid.UUID
    role_expression: Literal["parents_of(self)"]
    resolution: Literal["present", "unknown"]
    people: list[ParentPresencePersonView]
    reason_code: str
    not_evidence: list[str]


class ForgetPreview(StrictModel):
    erasure_request_id: uuid.UUID
    descriptor_fact_id: uuid.UUID
    delete: list[str]
    preserve: list[str]
    preview_digest: str


class ForgetConfirm(StrictModel):
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ErasureView(StrictModel):
    erasure_request_id: uuid.UUID
    state: str
    live_erased: list[str]
    external_pending: list[str]
    preserved: list[str]
    legacy_untracked: list[str] = Field(default_factory=list)
    checkpoint_affected: bool
    backup_expiry_at: datetime | None = None
    exact_residuals: list[str] = Field(default_factory=list)


class HealthView(StrictModel):
    status: Literal["ok", "degraded", "not_ready"]
    role: str
    database: str
    migration: str
    restore_gate: str
    rollout_authorization: str
    outbox: dict[str, Any]
    worker_maintenance: dict[str, WorkerMaintenanceCode]
    spool: dict[str, Any]
    resources: dict[str, Any]
    policy_version: str
    rollout_mode: RolloutMode
    capabilities: dict[
        str,
        Literal[
            "enabled",
            "disabled",
            "operator_and_confirmation_gated",
            "principal_consent_gated",
            "attested_native_consent_gated",
            "attested_native_confirmation_gated",
            "record_only",
            "shadow",
        ],
    ]


class ForgetPreviewRequest(StrictModel):
    fact_id: uuid.UUID


class AgentSnapshot(StrictModel):
    as_of: datetime
    rollout_mode: RolloutMode
    principal_id: uuid.UUID
    person_id: uuid.UUID
    preferences: dict[str, bool]
    latest_visit: dict[str, Any] | None
    pending_initiatives: list[InitiativeView]
    coverage_gaps: list[dict[str, Any]]
    capabilities: dict[
        str,
        Literal[
            "enabled",
            "disabled",
            "attested_native_consent_gated",
            "attested_native_confirmation_gated",
        ],
    ]


class MemoryInspection(StrictModel):
    transaction_id: uuid.UUID
    kind: str
    state: str
    preview: dict[str, Any]
    verifier_results: list[dict[str, Any]]
    fact_ids: list[uuid.UUID]
    created_at: datetime
    confirmed_at: datetime | None
