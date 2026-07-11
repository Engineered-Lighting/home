from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import ValidationError

from .auth import (
    ServiceIdentity,
    require_bootstrap,
    require_edge,
    require_operator_bearer,
    require_native_service_identity,
    require_service_identity,
)
from .errors import CapabilityDisabledError, ValidationDomainError
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
    InitiativeClaim,
    InitiativeSummaryView,
    InitiativeView,
    LegacyRoleImport,
    LegacyRelationshipCandidateImport,
    MemoryTransactionView,
    OperatorCapabilities,
    OperatorImportCapability,
    MemoryInspection,
    ParentConfirmation,
    ParentPresenceView,
    PersonCreate,
    PersonView,
    PlaceCreate,
    PreferenceUpdate,
    PrincipalBindingCreate,
    PrincipalView,
    ReviewedPersonVerify,
    ReviewedAliasImport,
    ReviewedRecognitionBindingImport,
    ReviewedPrivacyDirectiveImport,
    ReviewedPersonStatusImport,
    ReviewedImportReceipt,
    RolloutStatus,
    SourceEntityBindingCreate,
    VisitCreate,
    VisitView,
)
from .store import CoreStore


def store_from(request: Request) -> CoreStore:
    return request.app.state.store


Service = Annotated[ServiceIdentity, Depends(require_service_identity)]
NativeService = Annotated[ServiceIdentity, Depends(require_native_service_identity)]
OperatorService = Annotated[None, Depends(require_operator_bearer)]
Store = Annotated[CoreStore, Depends(store_from)]


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

    @router.post(
        "/principal-bindings",
        response_model=PrincipalView,
        status_code=status.HTTP_201_CREATED,
    )
    async def bind_principal(
        value: PrincipalBindingCreate,
        service: Service,
        store: Store,
        _bootstrap: None = Depends(require_bootstrap),
    ) -> PrincipalView:
        return await store.database.run_serializable(
            lambda: store.bind_principal(service.ha_user_id, value)
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

    @router.post(
        "/relationships/parent-confirmations", response_model=MemoryTransactionView
    )
    async def confirm_parent(
        value: ParentConfirmation,
        principal: Principal,
        store: Store,
    ) -> MemoryTransactionView:
        return await store.database.run_serializable(
            lambda: store.confirm_parent(principal, value)
        )

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
        principal: NativePrincipal,
        store: Store,
    ) -> list[InitiativeSummaryView]:
        return await store.list_initiatives(principal)

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
        value: InitiativeClaim,
        principal: NativePrincipal,
        store: Store,
    ) -> InitiativeView:
        return await store.database.run_serializable(
            lambda: store.claim_initiative(principal, initiative_id, value)
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
