from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value.astimezone(UTC)


Freshness = Literal["fresh", "aging", "stale", "not_applicable"]
Coverage = Literal["sufficient", "partial", "gap", "not_applicable"]
RolloutMode = Literal["record_only", "shadow", "canary"]


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
    directive: Literal[
        "do_not_track", "ignored", "silent", "private", "auto_expire"
    ]
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


class Phase2JourneyEvaluation(StrictModel):
    visit_id: uuid.UUID
    qualifies: bool
    reason_codes: list[str]


class Phase2ReadinessView(StrictModel):
    contract: Literal["phase2-record-only-gate-v2"] = "phase2-record-only-gate-v2"
    rule_version: Literal["record-only-envelope-gate-v2"] = (
        "record-only-envelope-gate-v2"
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
    ready_to_advance: bool
    blockers: list[str]
    qualifying_envelopes_are_redacted: Literal[True] = True
    controlled_journeys_authoritative: Literal[False] = False
    location_consent_default_off: Literal[True] = True
    snapshots_count_as_events: Literal[False] = False
    gaps_count_as_events: Literal[False] = False
    journeys_are_automatically_inferred: Literal[False] = False

    _evaluated = field_validator("evaluated_at")(_aware)


class RolloutAuthorizationRequest(StrictModel):
    operator_request_id: uuid.UUID
    expected_rule_version: Literal["record-only-envelope-gate-v2"]
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
    contract: Literal["rollout-authorization-receipt-v1"] = (
        "rollout-authorization-receipt-v1"
    )
    authorization_id: uuid.UUID
    operator_request_id: uuid.UUID
    from_mode: Literal["record_only", "shadow"]
    to_mode: Literal["shadow", "canary"]
    rule_version: str
    policy_version: str
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    readiness_evaluated_at: datetime
    authorized_at: datetime

    _readiness_evaluated = field_validator("readiness_evaluated_at")(_aware)
    _authorized = field_validator("authorized_at")(_aware)


class PrincipalBindingCreate(StrictModel):
    ha_user_id: str = Field(min_length=1, max_length=64)
    person_id: uuid.UUID
    display_label: str = Field(min_length=1, max_length=255)
    confirmation_artifact_id: uuid.UUID


class PrincipalView(StrictModel):
    principal_id: uuid.UUID
    person_id: uuid.UUID
    ha_user_id: str
    status: str


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


class ParentConfirmation(StrictModel):
    parent_person_id: uuid.UUID
    child_person_id: uuid.UUID
    confirmation_artifact_id: uuid.UUID
    valid_from: datetime | None = None

    _valid_from = field_validator("valid_from")(
        lambda value: None if value is None else _aware(value)
    )


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
            "record_only",
            "shadow",
        ],
    ]


class ForgetPreviewRequest(StrictModel):
    fact_id: uuid.UUID


class AgentSnapshot(StrictModel):
    as_of: datetime
    principal_id: uuid.UUID
    person_id: uuid.UUID
    preferences: dict[str, bool]
    latest_visit: dict[str, Any] | None
    pending_initiatives: list[InitiativeView]
    coverage_gaps: list[dict[str, Any]]
    capabilities: dict[str, Literal["enabled", "disabled"]]


class MemoryInspection(StrictModel):
    transaction_id: uuid.UUID
    kind: str
    state: str
    preview: dict[str, Any]
    verifier_results: list[dict[str, Any]]
    fact_ids: list[uuid.UUID]
    created_at: datetime
    confirmed_at: datetime | None
