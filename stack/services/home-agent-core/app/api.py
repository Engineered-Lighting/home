from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import ValidationError

from .auth import (
    ServiceIdentity,
    require_bootstrap,
    require_edge,
    require_operator_bearer,
    require_native_service_identity,
    require_service_identity,
)
from .errors import (
    CapabilityDisabledError,
    OptionalWorkSuspendedError,
    StorageReadOnlyDegradedError,
    ValidationDomainError,
    WorkerMaintenanceUnavailableError,
)
from .models import (
    ConfirmMemoryRequest,
    AgentSnapshot,
    DescriptorCorrectionPreviewRequest,
    DescriptorLifecycleConfirm,
    DescriptorLifecyclePreview,
    DescriptorPreview,
    DescriptorPreviewRequest,
    DescriptorRelationshipView,
    DescriptorRetractionPreviewRequest,
    ErasureView,
    ForgetConfirm,
    ForgetPreview,
    ForgetPreviewRequest,
    IngestBatch,
    IngestEnvelope,
    IngestResult,
    EdgePrivacyPolicyView,
    InitiativeSummaryView,
    InitiativeView,
    LegacyRoleImport,
    LegacyRelationshipCandidateImport,
    MemoryTransactionView,
    OperatorCapabilities,
    OperatorImportCapability,
    OperatorPrincipalBindingProposalStage,
    OperatorPrincipalBindingProposalView,
    OperatorPrincipalBindingRequestsView,
    MemoryInspection,
    OnboardingStatusView,
    ParentPresenceView,
    PHASE3_FIXED_READINESS_BLOCKERS,
    Phase2ReadinessView,
    Phase3ReadinessBlocker,
    Phase3ReadinessView,
    PersonCreate,
    PersonView,
    PlaceCreate,
    PreferenceUpdate,
    PrincipalBindingConfirmation,
    PrincipalBindingConfirmationView,
    PrincipalBindingProposalView,
    PrincipalBindingRequestAction,
    ReviewedPersonVerify,
    ReviewedAliasImport,
    ReviewedRecognitionBindingImport,
    ReviewedPrivacyDirectiveImport,
    ReviewedPersonStatusImport,
    ReviewedImportReceipt,
    RolloutMode,
    RolloutStatus,
    SourceEntityBindingCreate,
    VisitCreate,
    VisitView,
)
from .phase2 import ControlledJourneyReference, Phase2GateInspector
from .resources import inspect_disk_budget
from .store import CoreStore


def store_from(request: Request) -> CoreStore:
    return request.app.state.store


def operator_binding_store_from(request: Request) -> CoreStore:
    store = request.app.state.operator_store
    if store is None:
        raise CapabilityDisabledError("binding operator database role is unavailable")
    return store


Service = Annotated[ServiceIdentity, Depends(require_service_identity)]
NativeService = Annotated[ServiceIdentity, Depends(require_native_service_identity)]
OperatorService = Annotated[None, Depends(require_operator_bearer)]
Store = Annotated[CoreStore, Depends(store_from)]
OperatorBindingStore = Annotated[CoreStore, Depends(operator_binding_store_from)]


PHASE3_SCHEMA_REVISION = "0006_worker_maintenance_health"


def phase3_readiness_diagnostic(
    *,
    rollout_mode: RolloutMode,
    authorization_authorized: bool,
    authorization_code: str,
) -> Phase3ReadinessView:
    """Describe only what revision 0006 can prove without private-state reads."""

    blockers: list[Phase3ReadinessBlocker] = []
    if rollout_mode != "shadow":
        predecessor_status = "mode_not_shadow"
        blockers.append("rollout_mode_not_shadow")
    elif authorization_authorized and authorization_code == "authorized":
        predecessor_status = "authorized"
    elif authorization_code == "authorization_missing":
        predecessor_status = "missing"
    elif authorization_code == "authorization_unavailable":
        predecessor_status = "unavailable"
    else:
        predecessor_status = "invalid"

    if predecessor_status != "authorized":
        blockers.append("shadow_predecessor_not_authorized")
    blockers.extend(PHASE3_FIXED_READINESS_BLOCKERS)
    return Phase3ReadinessView(
        rollout_mode=rollout_mode,
        shadow_predecessor_status=predecessor_status,
        blockers=blockers,
    )


async def principal_from(
    service: Service,
    store: Store,
) -> dict[str, Any]:
    return await store.resolve_principal(service.ha_user_id)


Principal = Annotated[dict[str, Any], Depends(principal_from)]


async def native_principal_from(
    service: NativeService,
    store: Store,
) -> dict[str, Any]:
    return await store.resolve_principal(service.ha_user_id)


NativePrincipal = Annotated[dict[str, Any], Depends(native_principal_from)]


def ingest_router() -> APIRouter:
    router = APIRouter(prefix="/v1/ingest", tags=["edge-ingest"])

    @router.get(
        "/privacy-policy",
        response_model=EdgePrivacyPolicyView,
        dependencies=[Depends(require_edge)],
    )
    async def edge_privacy_policy(store: Store) -> EdgePrivacyPolicyView:
        return await store.edge_privacy_policy()

    @router.post(
        "/envelopes",
        response_model=IngestResult,
        dependencies=[Depends(require_edge)],
    )
    async def ingest_envelopes(value: dict[str, Any], store: Store) -> IngestResult:
        items = value.get("envelopes") if isinstance(value, dict) else None
        if not isinstance(items, list) or not 1 <= len(items) <= 500:
            raise ValidationDomainError(
                "envelopes must be a list containing 1 to 500 items"
            )

        valid = []
        invalid_results: list[IngestResult] = []
        for item in items:
            try:
                valid.append(IngestEnvelope.model_validate(item))
            except ValidationError as exc:
                invalid_results.append(
                    await store.quarantine_invalid(
                        item,
                        exc.errors(
                            include_url=False,
                            include_context=False,
                            include_input=False,
                        ),
                    )
                )

        result = (
            await store.ingest(IngestBatch(envelopes=valid))
            if valid
            else IngestResult(
                accepted=0, duplicates=0, quarantined=0, acknowledgements={}
            )
        )
        acknowledgements = dict(result.acknowledgements)
        for invalid in invalid_results:
            for key, sequence in invalid.acknowledgements.items():
                acknowledgements[key] = max(acknowledgements.get(key, 0), sequence)
        return IngestResult(
            accepted=result.accepted,
            duplicates=result.duplicates,
            quarantined=result.quarantined
            + sum(item.quarantined for item in invalid_results),
            acknowledgements=acknowledgements,
            opened_gaps=result.opened_gaps,
        )

    return router


def semantic_router() -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["semantic-core"])

    @router.get("/onboarding/status", response_model=OnboardingStatusView)
    async def onboarding_status(
        service: Service,
        store: Store,
    ) -> OnboardingStatusView:
        """Return content-free onboarding progress for the authenticated user."""

        binding_status = await store.onboarding_binding_status(service.ha_user_id)
        phase2 = await Phase2GateInspector(
            store.database,
            # Phase 2 readiness is the record-only evidence gate even after a
            # deployment has advanced. The actual deployment mode is returned
            # separately below.
            rollout_mode="record_only",
            policy_version=store.settings.policy_version,
            policy_digest=store.settings.policy_digest,
        ).inspect_onboarding()

        if binding_status == "bound":
            state = "bound"
        elif binding_status == "unavailable":
            state = "contained"
        elif store.settings.rollout_mode in {"shadow", "canary"}:
            state = "identity_confirmation_required"
        else:
            state = "collecting_evidence"

        return OnboardingStatusView(
            state=state,
            rollout_mode=store.settings.rollout_mode,
            principal_bound=binding_status == "bound",
            phase2_ready=phase2.ready_to_advance,
            phase2_blockers=phase2.blockers,
        )

    @router.post(
        "/people",
        response_model=PersonView,
        status_code=status.HTTP_201_CREATED,
        openapi_extra={"x-home-agent-idempotency": "exact-legacy-projection-v1"},
    )
    async def create_person(
        value: PersonCreate,
        _service: OperatorService,
        store: Store,
        _bootstrap: None = Depends(require_bootstrap),
    ) -> PersonView:
        return await store.database.run_serializable(lambda: store.create_person(value))

    @router.get(
        "/operator-capabilities",
        response_model=OperatorCapabilities,
        response_model_exclude_none=True,
    )
    async def operator_capabilities(
        _service: OperatorService,
        _bootstrap: None = Depends(require_bootstrap),
    ) -> OperatorCapabilities:
        return OperatorCapabilities(
            contract="legacy-identity-migration-v1",
            audience="operator-bootstrap",
            person_import=OperatorImportCapability(
                method="POST",
                path="/v1/people",
                schema="PersonCreate.v2",
                source_digest_field="legacy_source_sha256",
                idempotency="exact-projection-v1",
            ),
            role_import=OperatorImportCapability(
                method="POST",
                path="/v1/people/legacy-role-labels",
                schema="LegacyRoleImport.v1",
                source_digest_field="source_snapshot_sha256",
                idempotency="exact-projection-v1",
            ),
            person_verify=OperatorImportCapability(
                method="POST",
                path="/v1/people/verify-reviewed",
                schema="ReviewedPersonVerify.v1",
            ),
            alias_import=OperatorImportCapability(
                method="POST",
                path="/v1/people/{person_id}/aliases",
                schema="ReviewedAliasImport.v1",
                source_digest_field="source_snapshot_sha256",
                idempotency="exact-projection-v1",
            ),
            recognition_binding_import=OperatorImportCapability(
                method="POST",
                path="/v1/people/{person_id}/recognition-bindings",
                schema="ReviewedRecognitionBindingImport.v1",
                source_digest_field="source_snapshot_sha256",
                idempotency="exact-projection-v1",
            ),
            privacy_directive_import=OperatorImportCapability(
                method="POST",
                path="/v1/people/{person_id}/privacy-directives",
                schema="ReviewedPrivacyDirectiveImport.v1",
                source_digest_field="source_snapshot_sha256",
                idempotency="exact-projection-v1",
            ),
            person_status_import=OperatorImportCapability(
                method="POST",
                path="/v1/people/{person_id}/status-import",
                schema="ReviewedPersonStatusImport.v1",
                source_digest_field="source_snapshot_sha256",
                idempotency="exact-projection-v1",
            ),
            relationship_candidate_import=OperatorImportCapability(
                method="POST",
                path="/v1/people/legacy-relationship-candidates",
                schema="LegacyRelationshipCandidateImport.v1",
                source_digest_field="source_snapshot_sha256",
                idempotency="exact-projection-v1",
            ),
        )

    @router.get("/operator-rollout", response_model=RolloutStatus)
    async def operator_rollout(
        _service: OperatorService,
        store: Store,
        _bootstrap: None = Depends(require_bootstrap),
    ) -> RolloutStatus:
        mode = store.settings.rollout_mode
        return RolloutStatus(
            mode=mode,
            semantic_people_writes=mode in {"shadow", "canary"},
            persistent_memory_writes=mode == "canary",
        )

    @router.get(
        "/operator-rollout/phase2-readiness",
        response_model=Phase2ReadinessView,
    )
    async def phase2_readiness(
        _service: OperatorService,
        store: Store,
        _bootstrap: None = Depends(require_bootstrap),
        controlled_principal_id: list[uuid.UUID] | None = Query(default=None),
        controlled_journey_id: list[uuid.UUID] | None = Query(default=None),
    ) -> Phase2ReadinessView:
        """Evaluate the record-only gate without discovering private visits.

        A journey is inspected only when the operator supplies a paired
        principal and visit UUID. It remains informational and cannot authorize
        live promotion. The inspector validates through RLS and returns no
        names, states, coordinates, locators, or source payloads.
        """

        principal_ids = controlled_principal_id or []
        journey_ids = controlled_journey_id or []
        if len(principal_ids) != len(journey_ids):
            raise ValidationDomainError(
                "controlled principal and journey IDs must be paired"
            )
        if len(journey_ids) > 20:
            raise ValidationDomainError(
                "at most 20 controlled journeys may be inspected at once"
            )
        references = [
            ControlledJourneyReference(principal_id=principal_id, visit_id=visit_id)
            for principal_id, visit_id in zip(
                principal_ids, journey_ids, strict=True
            )
        ]
        return await Phase2GateInspector(
            store.database,
            rollout_mode=store.settings.rollout_mode,
            policy_version=store.settings.policy_version,
            policy_digest=store.settings.policy_digest,
        ).inspect(references)

    @router.get(
        "/operator-rollout/phase3-readiness",
        response_model=Phase3ReadinessView,
    )
    async def phase3_readiness(
        request: Request,
        _service: OperatorService,
        _bootstrap: None = Depends(require_bootstrap),
    ) -> Phase3ReadinessView:
        """Return a fixed, non-authoritative revision-0006 gap diagnostic."""

        if request.query_params:
            raise ValidationDomainError(
                "Phase 3 readiness does not accept query parameters"
            )
        if await request.body():
            raise ValidationDomainError("Phase 3 readiness does not accept a body")
        settings = request.app.state.settings
        actual_revision = await request.app.state.database.migration_revision()
        if (
            settings.readiness_migration != PHASE3_SCHEMA_REVISION
            or actual_revision != settings.readiness_migration
        ):
            raise CapabilityDisabledError(
                "Phase 3 revision-0006 diagnostic is unavailable"
            )
        authorization = await request.app.state.rollout_gate.status(force=True)
        return phase3_readiness_diagnostic(
            rollout_mode=settings.rollout_mode,
            authorization_authorized=authorization.authorized,
            authorization_code=authorization.code,
        )

    @router.post("/people/verify-reviewed", response_model=PersonView)
    async def verify_reviewed_person(
        value: ReviewedPersonVerify,
        _service: OperatorService,
        store: Store,
        _bootstrap: None = Depends(require_bootstrap),
    ) -> PersonView:
        return await store.database.run_serializable(
            lambda: store.verify_reviewed_person(value)
        )

    @router.post(
        "/people/{person_id}/aliases",
        response_model=ReviewedImportReceipt,
        status_code=status.HTTP_201_CREATED,
        openapi_extra={"x-home-agent-idempotency": "exact-legacy-projection-v1"},
    )
    async def import_reviewed_alias(
        person_id: uuid.UUID,
        value: ReviewedAliasImport,
        _service: OperatorService,
        store: Store,
        _bootstrap: None = Depends(require_bootstrap),
    ) -> ReviewedImportReceipt:
        return await store.database.run_serializable(
            lambda: store.import_reviewed_alias(person_id, value)
        )

    @router.post(
        "/people/{person_id}/recognition-bindings",
        response_model=ReviewedImportReceipt,
        status_code=status.HTTP_201_CREATED,
        openapi_extra={"x-home-agent-idempotency": "exact-legacy-projection-v1"},
    )
    async def import_reviewed_recognition_binding(
        person_id: uuid.UUID,
        value: ReviewedRecognitionBindingImport,
        _service: OperatorService,
        store: Store,
        _bootstrap: None = Depends(require_bootstrap),
    ) -> ReviewedImportReceipt:
        return await store.database.run_serializable(
            lambda: store.import_reviewed_recognition_binding(person_id, value)
        )

    @router.post(
        "/people/{person_id}/privacy-directives",
        response_model=ReviewedImportReceipt,
        status_code=status.HTTP_201_CREATED,
        openapi_extra={"x-home-agent-idempotency": "exact-legacy-projection-v1"},
    )
    async def import_reviewed_privacy_directive(
        person_id: uuid.UUID,
        value: ReviewedPrivacyDirectiveImport,
        _service: OperatorService,
        store: Store,
        _bootstrap: None = Depends(require_bootstrap),
    ) -> ReviewedImportReceipt:
        return await store.database.run_serializable(
            lambda: store.import_reviewed_privacy_directive(person_id, value)
        )

    @router.post(
        "/people/{person_id}/status-import",
        response_model=ReviewedImportReceipt,
        status_code=status.HTTP_201_CREATED,
        openapi_extra={"x-home-agent-idempotency": "exact-legacy-projection-v1"},
    )
    async def import_reviewed_person_status(
        person_id: uuid.UUID,
        value: ReviewedPersonStatusImport,
        _service: OperatorService,
        store: Store,
        _bootstrap: None = Depends(require_bootstrap),
    ) -> ReviewedImportReceipt:
        return await store.database.run_serializable(
            lambda: store.import_reviewed_person_status(person_id, value)
        )

    @router.get(
        "/operator/principal-binding-requests",
        response_model=OperatorPrincipalBindingRequestsView,
    )
    async def list_principal_binding_requests(
        _service: OperatorService,
        store: OperatorBindingStore,
        _bootstrap: None = Depends(require_bootstrap),
    ) -> OperatorPrincipalBindingRequestsView:
        return await store.list_pending_principal_binding_requests()

    @router.post(
        "/operator/principal-binding-proposals",
        response_model=OperatorPrincipalBindingProposalView,
        status_code=status.HTTP_201_CREATED,
    )
    async def stage_principal_binding_proposal(
        value: OperatorPrincipalBindingProposalStage,
        _service: OperatorService,
        store: OperatorBindingStore,
        _bootstrap: None = Depends(require_bootstrap),
    ) -> OperatorPrincipalBindingProposalView:
        return await store.database.run_serializable(
            lambda: store.stage_principal_binding_proposal(value)
        )

    @router.get(
        "/principal-binding-proposal",
        response_model=PrincipalBindingProposalView,
        response_model_exclude_none=True,
    )
    async def principal_binding_proposal(
        service: Service,
        store: Store,
    ) -> PrincipalBindingProposalView:
        return await store.principal_binding_proposal_status(service.ha_user_id)

    @router.post(
        "/principal-binding-request",
        response_model=PrincipalBindingProposalView,
        response_model_exclude_none=True,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def request_principal_binding(
        _value: PrincipalBindingRequestAction,
        service: Service,
        store: Store,
    ) -> PrincipalBindingProposalView:
        return await store.database.run_serializable(
            lambda: store.request_principal_binding(service.ha_user_id)
        )

    @router.post(
        "/principal-binding-request/cancel",
        response_model=PrincipalBindingProposalView,
        response_model_exclude_none=True,
    )
    async def cancel_principal_binding_request(
        _value: PrincipalBindingRequestAction,
        service: Service,
        store: Store,
    ) -> PrincipalBindingProposalView:
        return await store.database.run_serializable(
            lambda: store.cancel_principal_binding_request(service.ha_user_id)
        )

    @router.post(
        "/principal-binding-proposal/confirm",
        response_model=PrincipalBindingConfirmationView,
    )
    async def confirm_principal_binding_proposal(
        value: PrincipalBindingConfirmation,
        service: Service,
        store: Store,
    ) -> PrincipalBindingConfirmationView:
        return await store.database.run_serializable(
            lambda: store.confirm_principal_binding_proposal(
                service.ha_user_id, value
            )
        )

    @router.post(
        "/people/legacy-role-labels",
        status_code=status.HTTP_201_CREATED,
        openapi_extra={"x-home-agent-idempotency": "exact-legacy-projection-v1"},
    )
    async def import_legacy_role(
        value: LegacyRoleImport,
        _service: OperatorService,
        store: Store,
        _bootstrap: None = Depends(require_bootstrap),
    ) -> dict[str, uuid.UUID]:
        label_id = await store.database.run_serializable(
            lambda: store.import_legacy_role(value)
        )
        return {"label_id": label_id}

    @router.post(
        "/people/legacy-relationship-candidates",
        status_code=status.HTTP_201_CREATED,
        openapi_extra={"x-home-agent-idempotency": "exact-legacy-projection-v1"},
    )
    async def import_legacy_relationship_candidate(
        value: LegacyRelationshipCandidateImport,
        _service: OperatorService,
        store: Store,
        _bootstrap: None = Depends(require_bootstrap),
    ) -> dict[str, uuid.UUID]:
        candidate_id = await store.database.run_serializable(
            lambda: store.import_legacy_relationship_candidate(value)
        )
        return {"candidate_id": candidate_id}

    @router.post("/source-entity-bindings", status_code=status.HTTP_201_CREATED)
    async def bind_source_entity(
        value: SourceEntityBindingCreate,
        principal: Principal,
        store: Store,
        _bootstrap: None = Depends(require_bootstrap),
    ) -> dict[str, uuid.UUID]:
        binding_id = await store.database.run_serializable(
            lambda: store.bind_source_entity(principal, value)
        )
        return {"binding_id": binding_id}

    @router.put("/preferences/{key}")
    async def set_preference(
        key: str,
        value: PreferenceUpdate,
        principal: Principal,
        store: Store,
    ) -> dict[str, Any]:
        if key != value.key:
            from .errors import ConflictError

            raise ConflictError("preference path and payload keys differ")
        # Path-level resource guards keep opt-out available during degradation.
        # Enabling either private-location capability is optional work. Recheck
        # the body-aware branch here because path middleware deliberately lets
        # the same route through for privacy-essential disable requests.
        if value.enabled:
            try:
                disk_state = inspect_disk_budget(
                    store.settings.storage_monitor_path
                ).state
            except Exception:
                # Resource inspection is a security boundary for opt-in. An
                # unexpected implementation/runtime failure is unavailable,
                # never permission to begin retaining private location.
                disk_state = "unavailable"
            if disk_state not in {
                "normal",
                "warn",
                "stop_optional",
                "read_only",
                "unavailable",
            }:
                disk_state = "unavailable"
            if disk_state == "stop_optional":
                raise OptionalWorkSuspendedError(
                    "preference opt-in is suspended by the resource budget",
                    details={"disk_state": disk_state},
                )
            if disk_state in {"read_only", "unavailable"}:
                raise StorageReadOnlyDegradedError(
                    "preference opt-in is disabled by the resource budget",
                    details={"disk_state": disk_state},
                )
            maintenance = await store.maintenance_inspector.inspect(
                observed_after=store.maintenance_observed_after,
            )
            if not maintenance.ready:
                raise WorkerMaintenanceUnavailableError(
                    "preference opt-in requires a current retention worker",
                    details=maintenance.as_public_dict(),
                )
        return await store.database.run_serializable(
            lambda: store.set_preference(principal, value)
        )

    @router.post("/places", status_code=status.HTTP_201_CREATED)
    async def create_place(
        value: PlaceCreate,
        principal: Principal,
        store: Store,
        _bootstrap: None = Depends(require_bootstrap),
    ) -> dict[str, uuid.UUID]:
        place_id = await store.database.run_serializable(
            lambda: store.create_place(principal, value)
        )
        return {"place_id": place_id}

    @router.post(
        "/visits", response_model=VisitView, status_code=status.HTTP_201_CREATED
    )
    async def create_visit(
        value: VisitCreate,
        principal: Principal,
        store: Store,
        _bootstrap: None = Depends(require_bootstrap),
    ) -> VisitView:
        return await store.database.run_serializable(
            lambda: store.create_visit(principal, value)
        )

    @router.get("/snapshot", response_model=AgentSnapshot)
    async def snapshot(principal: Principal, store: Store) -> AgentSnapshot:
        return await store.snapshot(principal)

    @router.get("/initiatives", response_model=list[InitiativeSummaryView])
    async def list_initiatives(
        _service: NativeService,
    ) -> list[InitiativeSummaryView]:
        raise CapabilityDisabledError(
            "private initiative presentation requires a separate reviewed gate"
        )

    @router.get(
        "/places/{place_id}/descriptor-relationship",
        response_model=DescriptorRelationshipView,
    )
    async def descriptor_relationship(
        place_id: uuid.UUID,
        principal: NativePrincipal,
        store: Store,
    ) -> DescriptorRelationshipView:
        return await store.explain_descriptor_relationship(principal, place_id)

    @router.get(
        "/places/{place_id}/parents/current-presence",
        response_model=ParentPresenceView,
    )
    async def parent_presence(
        place_id: uuid.UUID,
        principal: NativePrincipal,
        store: Store,
    ) -> ParentPresenceView:
        return await store.query_parent_presence(principal, place_id)

    @router.post(
        "/memory-transactions/descriptor-preview", response_model=DescriptorPreview
    )
    async def preview_descriptor(
        value: DescriptorPreviewRequest,
        principal: Principal,
        store: Store,
    ) -> DescriptorPreview:
        return await store.database.run_serializable(
            lambda: store.preview_descriptor(principal, value)
        )

    @router.post("/memory-transactions", response_model=DescriptorPreview)
    async def propose_memory(
        value: DescriptorPreviewRequest,
        principal: Principal,
        store: Store,
    ) -> DescriptorPreview:
        """MVP typed proposal alias used by the BFF.

        Additional transaction kinds require their own discriminated schema;
        this route never accepts a free-form predicate/object pair.
        """
        return await store.database.run_serializable(
            lambda: store.preview_descriptor(principal, value)
        )

    @router.get(
        "/memory-transactions/{transaction_id}", response_model=MemoryInspection
    )
    async def inspect_memory(
        transaction_id: uuid.UUID,
        principal: Principal,
        store: Store,
    ) -> MemoryInspection:
        return await store.inspect_memory(principal, transaction_id)

    @router.post(
        "/memory-transactions/{transaction_id}/confirm",
        response_model=MemoryTransactionView,
    )
    async def confirm_descriptor(
        transaction_id: uuid.UUID,
        value: ConfirmMemoryRequest,
        principal: Principal,
        store: Store,
    ) -> MemoryTransactionView:
        return await store.database.run_serializable(
            lambda: store.confirm_descriptor(principal, transaction_id, value)
        )

    @router.post(
        "/facts/{fact_id}/correction-preview",
        response_model=DescriptorLifecyclePreview,
    )
    async def preview_descriptor_correction(
        fact_id: uuid.UUID,
        value: DescriptorCorrectionPreviewRequest,
        principal: Principal,
        store: Store,
    ) -> DescriptorLifecyclePreview:
        return await store.database.run_serializable(
            lambda: store.preview_descriptor_correction(principal, fact_id, value)
        )

    @router.post(
        "/descriptor-corrections/{transaction_id}/confirm",
        response_model=MemoryTransactionView,
    )
    async def confirm_descriptor_correction(
        transaction_id: uuid.UUID,
        value: DescriptorLifecycleConfirm,
        principal: Principal,
        store: Store,
    ) -> MemoryTransactionView:
        return await store.database.run_serializable(
            lambda: store.confirm_descriptor_correction(
                principal, transaction_id, value
            )
        )

    @router.post(
        "/facts/{fact_id}/retraction-preview",
        response_model=DescriptorLifecyclePreview,
    )
    async def preview_descriptor_retraction(
        fact_id: uuid.UUID,
        _value: DescriptorRetractionPreviewRequest,
        principal: Principal,
        store: Store,
    ) -> DescriptorLifecyclePreview:
        return await store.database.run_serializable(
            lambda: store.preview_descriptor_retraction(principal, fact_id)
        )

    @router.post(
        "/descriptor-retractions/{transaction_id}/confirm",
        response_model=MemoryTransactionView,
    )
    async def confirm_descriptor_retraction(
        transaction_id: uuid.UUID,
        value: DescriptorLifecycleConfirm,
        principal: Principal,
        store: Store,
    ) -> MemoryTransactionView:
        return await store.database.run_serializable(
            lambda: store.confirm_descriptor_retraction(
                principal, transaction_id, value
            )
        )

    @router.post("/initiatives/{initiative_id}/claim", response_model=InitiativeView)
    async def claim_initiative(
        initiative_id: uuid.UUID,
        _service: NativeService,
    ) -> InitiativeView:
        del initiative_id
        raise CapabilityDisabledError(
            "private initiative presentation requires a separate reviewed gate"
        )

    @router.post("/facts/{fact_id}/forget-preview", response_model=ForgetPreview)
    async def preview_forget(
        fact_id: uuid.UUID,
        principal: Principal,
        store: Store,
    ) -> ForgetPreview:
        return await store.database.run_serializable(
            lambda: store.preview_forget_descriptor(principal, fact_id)
        )

    @router.post("/forget-preview", response_model=ForgetPreview)
    async def preview_forget_alias(
        value: ForgetPreviewRequest,
        principal: Principal,
        store: Store,
    ) -> ForgetPreview:
        return await store.database.run_serializable(
            lambda: store.preview_forget_descriptor(principal, value.fact_id)
        )

    @router.post("/erasure-requests/{request_id}/confirm", response_model=ErasureView)
    async def confirm_forget(
        request_id: uuid.UUID,
        value: ForgetConfirm,
        principal: Principal,
        store: Store,
    ) -> ErasureView:
        return await store.database.run_serializable(
            lambda: store.confirm_forget(principal, request_id, value)
        )

    @router.get("/erasure-requests/{request_id}", response_model=ErasureView)
    async def inspect_erasure(
        request_id: uuid.UUID,
        principal: Principal,
        store: Store,
    ) -> ErasureView:
        return await store.inspect_erasure(principal, request_id)

    @router.post("/actions")
    async def actions_disabled(_principal: Principal) -> None:
        raise CapabilityDisabledError(
            "physical actions are disabled in the Itaipava MVP"
        )

    @router.post("/active-room")
    async def active_room_disabled(_principal: Principal) -> None:
        raise CapabilityDisabledError(
            "active-room perception is disabled in the Itaipava MVP"
        )

    @router.post("/learning")
    async def learning_disabled(_principal: Principal) -> None:
        raise CapabilityDisabledError(
            "learned policies are disabled in the Itaipava MVP"
        )

    @router.post("/vjepa")
    async def vjepa_disabled(_principal: Principal) -> None:
        raise CapabilityDisabledError(
            "V-JEPA integration is disabled in the Itaipava MVP"
        )

    return router
