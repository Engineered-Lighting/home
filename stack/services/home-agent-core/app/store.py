from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Mapping

from psycopg.types.range import Range
from sqlalchemy import and_, delete, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from . import schema
from .config import Settings
from .crypto import FieldCipher, SealedValue, canonical_json, sha256_json
from .db import Database
from .erasure import apply_descriptor_erasure, invalidate_descriptor_dependents
from .errors import ConflictError, ForbiddenError, NotFoundError
from .ids import uuid7
from .location import (
    LocationFix,
    derive_locality_presence,
    derive_specific_anchor,
    freshness_at,
    haversine_m,
)
from .memory import (
    PROHIBITED_ASSERTIONS,
    descriptor_preview_payload,
    is_typed_parents_mountain_descriptor,
    propose_parents_mountain_house,
    render_travel_arrival,
)
from .maintenance import WorkerMaintenanceInspector
from .resources import inspect_disk_budget
from .models import (
    ConfirmMemoryRequest,
    AgentSnapshot,
    DescriptorCorrectionPreviewRequest,
    DescriptorLifecycleConfirm,
    DescriptorLifecyclePreview,
    DescriptorPreview,
    DescriptorPreviewRequest,
    DescriptorRelationshipView,
    ErasureView,
    ForgetConfirm,
    ForgetPreview,
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
    MemoryInspection,
    ParentConfirmation,
    ParentPresencePersonView,
    ParentPresenceView,
    PersonCreate,
    PersonView,
    PlaceCreate,
    PreferenceUpdate,
    PrincipalBindingConfirmation,
    PrincipalBindingConfirmationView,
    PrincipalBindingProposalView,
    OperatorPrincipalBindingProposalStage,
    OperatorPrincipalBindingProposalView,
    OperatorPrincipalBindingRequestsView,
    OperatorPrincipalBindingRequestView,
    ReviewedPersonVerify,
    ResolvedPersonView,
    ReviewedAliasImport,
    ReviewedRecognitionBindingImport,
    ReviewedPrivacyDirectiveImport,
    ReviewedPersonStatusImport,
    ReviewedImportReceipt,
    SourceEntityBindingCreate,
    VisitCreate,
    VisitView,
)
from .spool import EncryptedRuntimeSpool, SpoolAppendResult


ALLOWED_EVENT_TYPES = {
    "state_changed",
    "location_fix",
    "conversation_turn",
    "coverage_gap",
    "snapshot",
}
SAFE_METADATA_KEYS = {
    "schema_version",
    "source_platform",
    "projection_type",
    "derived_from_entity",
    "tracking_type",
}

BINDING_REQUEST_TTL = timedelta(hours=24)
BINDING_PROPOSAL_TTL = timedelta(minutes=15)
BINDING_REVIEW_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
BINDING_PROPOSAL_CONTRACT = "principal-binding-proposal-v1"
OBSERVATION_ROOT_NAMESPACE = uuid.UUID("23603892-6ac5-4a54-a046-ef4edc0201ea")


def stable_observation_root(envelope: IngestEnvelope) -> uuid.UUID:
    """Derive a replay-stable root when an old Edge omitted its own root ID."""

    return uuid.uuid5(
        OBSERVATION_ROOT_NAMESPACE,
        canonical_json(
            {
                "edge_instance_id": envelope.edge_instance_id,
                "source_name": envelope.source_name,
                "epoch": str(envelope.epoch),
                "sequence": envelope.sequence,
            }
        ).decode("utf-8"),
    )


def binding_person_snapshot_digest(person: Mapping[str, Any]) -> str:
    """Bind operator review to the exact non-secret semantic person record."""

    return sha256_json(
        {
            "contract": "principal-binding-person-snapshot-v1",
            "person_id": str(person["person_id"]),
            "display_name": person["display_name"],
            "status": person["status"],
            "privacy_scope": person["privacy_scope"],
            "legacy_source_sha256": person["legacy_source_sha256"],
            "status_source_sha256": person["status_source_sha256"],
        }
    )


def _canonical_binding_timestamp(value: datetime) -> str:
    """Normalize a proposal timestamp before hashing it into the typed intent."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("principal-binding proposal timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def binding_proposal_digest(
    *,
    request_id: uuid.UUID,
    review_code: str,
    ha_user_id: str,
    person_id: uuid.UUID,
    reviewed_display_label: str,
    person_snapshot_digest: str,
    operator_request_id: uuid.UUID,
    stage_receipt_digest: str,
    staged_at: datetime,
    expires_at: datetime,
    policy_version: str,
    policy_digest: str,
) -> str:
    """Digest the complete, typed operator proposal shown to the HA subject.

    The digest is an intent commitment, not a bearer credential. Subject access is
    still authenticated and HA-user scoped; changing policy invalidates any
    outstanding 15-minute proposal rather than silently confirming old policy.
    """

    return sha256_json(
        {
            "contract": BINDING_PROPOSAL_CONTRACT,
            "request_id": str(request_id),
            "review_code": review_code,
            "ha_user_id": ha_user_id,
            "person_id": str(person_id),
            "reviewed_display_label": reviewed_display_label,
            "person_snapshot_digest": person_snapshot_digest,
            "operator_request_id": str(operator_request_id),
            "stage_receipt_digest": stage_receipt_digest,
            "staged_at": _canonical_binding_timestamp(staged_at),
            "expires_at": _canonical_binding_timestamp(expires_at),
            "policy_version": policy_version,
            "policy_digest": policy_digest,
        }
    )


class CoreStore:
    def __init__(
        self,
        database: Database,
        spool: EncryptedRuntimeSpool,
        settings: Settings,
    ) -> None:
        self.database = database
        self.spool = spool
        self.settings = settings
        self.maintenance_inspector = WorkerMaintenanceInspector(database)
        self.maintenance_observed_after: datetime | None = None
        knowledge_key = settings.decoded_knowledge_key()
        self.knowledge_cipher = FieldCipher(
            knowledge_key,
            key_id="knowledge-v1",
        )
        self._preview_digest_key = hmac.new(
            knowledge_key,
            b"home-agent:v1:private-preview-digest",
            hashlib.sha256,
        ).digest()
        self._payload_digest_key = hmac.new(
            settings.decoded_knowledge_key(),
            b"home-agent:v1:precise-location-payload-digest",
            hashlib.sha256,
        ).digest()

    def _require_rollout(self, minimum: str, capability: str) -> None:
        order = {"record_only": 0, "shadow": 1, "canary": 2}
        if order[self.settings.rollout_mode] < order[minimum]:
            from .errors import CapabilityDisabledError

            raise CapabilityDisabledError(
                f"{capability} requires rollout mode {minimum}",
                details={
                    "capability": capability,
                    "current_rollout_mode": self.settings.rollout_mode,
                    "minimum_rollout_mode": minimum,
                    "operator_policy_required": True,
                },
            )

    @staticmethod
    def _normalize_alias(value: str) -> str:
        normalized = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
        if not normalized or any(
            unicodedata.category(char).startswith("C") for char in normalized
        ):
            raise ConflictError("alias contains unsupported characters")
        return normalized

    async def ingest(self, batch: IngestBatch) -> IngestResult:
        accepted = duplicates = quarantined_count = 0
        opened_gaps: list[str] = []
        spool_markers_to_ack: list[str] = []
        acknowledgements: dict[str, int] = {}

        ordered = sorted(
            batch.envelopes,
            key=lambda item: (
                item.edge_instance_id,
                item.source_name,
                str(item.epoch),
                item.sequence,
            ),
        )
        async with self.database.transaction() as connection:
            runtime_now = (
                await connection.execute(select(func.clock_timestamp()))
            ).scalar_one()
            for envelope in ordered:
                stream_key = f"{envelope.edge_instance_id}:{envelope.source_name}"
                stream_id = await self._ensure_stream(connection, envelope)
                quarantine_reason = self._quarantine_reason(envelope)
                payload_bytes = canonical_json(envelope.payload)
                precise_location = self._is_precise_location(envelope)
                payload_hash = self._payload_digest(
                    payload_bytes, precise_location=precise_location
                )
                if quarantine_reason:
                    statement = (
                        insert(schema.quarantine)
                        .values(
                            quarantine_id=uuid7(),
                            edge_instance_id=envelope.edge_instance_id,
                            source_name=envelope.source_name,
                            epoch=envelope.epoch,
                            sequence=envelope.sequence,
                            payload_sha256=payload_hash,
                            reason_code=quarantine_reason,
                            details={"event_type": envelope.event_type},
                        )
                        .on_conflict_do_nothing(constraint="quarantine_sequence")
                    )
                    await connection.execute(statement)
                    quarantined_count += 1
                    acknowledgements[stream_key] = await self._advance_ack(
                        connection, stream_id, envelope
                    )
                    continue

                root_id = envelope.root_observation_id or stable_observation_root(
                    envelope
                )
                family_id = envelope.evidence_family_id or root_id
                (
                    spool_allowed,
                    raw_payload_status,
                    redact_identity,
                ) = await self._retention_decision(
                    connection, envelope, precise_location=precise_location
                )
                if spool_allowed:
                    # Reinspect before every raw append. A large (max-500)
                    # batch must not reuse one heartbeat beyond its liveness
                    # window if the worker dies during per-envelope work.
                    maintenance = (
                        await self.maintenance_inspector.inspect_in_transaction(
                            connection,
                            observed_after=self.maintenance_observed_after,
                        )
                    )
                    if not maintenance.ready:
                        spool_allowed = False
                        raw_payload_status = (
                            "suppressed_retention_worker_unavailable"
                        )
                        redact_identity = redact_identity or precise_location
                spool_result = SpoolAppendResult(inserted=False, evicted=())
                stored_root_id = uuid7() if redact_identity else root_id
                stored_family_id = stored_root_id if redact_identity else family_id
                durable_metadata = {
                    key: value
                    for key, value in envelope.metadata.items()
                    if key in SAFE_METADATA_KEYS and not redact_identity
                }
                durable_metadata["raw_payload_status"] = raw_payload_status
                freshness = self._freshness(
                    envelope.source_observed_at, envelope.edge_received_at
                )
                statement = (
                    insert(schema.envelopes)
                    .values(
                        envelope_id=uuid7(),
                        stream_id=stream_id,
                        sequence=envelope.sequence,
                        event_type=envelope.event_type,
                        payload_bytes=len(payload_bytes),
                        entity_id=(None if redact_identity else envelope.entity_id),
                        source_event_id=(
                            None if redact_identity else envelope.source_event_id
                        ),
                        source_observed_at=envelope.source_observed_at,
                        edge_received_at=envelope.edge_received_at,
                        payload_sha256=payload_hash,
                        root_observation_id=stored_root_id,
                        evidence_family_id=stored_family_id,
                        dependency_domain=(
                            (
                                "suppressed.precise_location"
                                if precise_location
                                else "suppressed.identity_linked"
                            )
                            if redact_identity
                            else envelope.dependency_domain
                        ),
                        freshness=freshness,
                        coverage=envelope.coverage,
                        clock_state=envelope.clock_state,
                        ha_context=(
                            {}
                            if redact_identity
                            else envelope.ha_context.model_dump(exclude_none=True)
                        ),
                        metadata=durable_metadata,
                    )
                    .on_conflict_do_nothing(index_elements=["stream_id", "sequence"])
                    .returning(schema.envelopes.c.envelope_id)
                )
                inserted = (await connection.execute(statement)).scalar_one_or_none()
                project_location = False
                if inserted is None:
                    existing_hash = (
                        await connection.execute(
                            select(schema.envelopes.c.payload_sha256).where(
                                schema.envelopes.c.stream_id == stream_id,
                                schema.envelopes.c.sequence == envelope.sequence,
                            )
                        )
                    ).scalar_one()
                    if not hmac.compare_digest(existing_hash, payload_hash):
                        await connection.execute(
                            insert(schema.quarantine)
                            .values(
                                quarantine_id=uuid7(),
                                edge_instance_id=envelope.edge_instance_id,
                                source_name=envelope.source_name,
                                epoch=envelope.epoch,
                                sequence=envelope.sequence,
                                payload_sha256=payload_hash,
                                reason_code="sequence_payload_conflict",
                                details={"event_type": envelope.event_type},
                            )
                            .on_conflict_do_nothing(constraint="quarantine_sequence")
                        )
                        quarantined_count += 1
                    else:
                        duplicates += 1
                else:
                    # A durable duplicate or sequence conflict must never gain
                    # raw retention on replay. Only a newly inserted header may
                    # populate the short-lived spool.
                    if spool_allowed:
                        spool_result = self.spool.append(
                            stream_key=stream_key,
                            epoch=str(envelope.epoch),
                            sequence=envelope.sequence,
                            observed_at=envelope.source_observed_at,
                            received_at=envelope.edge_received_at,
                            payload=envelope.payload,
                            metadata={
                                "entity_id": envelope.entity_id,
                                "root_observation_id": str(root_id),
                                "coverage": envelope.coverage,
                                **{
                                    key: value
                                    for key, value in envelope.metadata.items()
                                    if key in SAFE_METADATA_KEYS
                                },
                            },
                            user_scope_id=envelope.ha_context.user_id,
                            now=runtime_now,
                        )
                    if spool_result.conflict:
                        # A raw row can outlive a rolled-back PostgreSQL
                        # transaction. Never bind a changed replay to that
                        # orphan: remove the new header and quarantine the
                        # sequence after securely deleting the orphaned row.
                        if not self.spool.discard(
                            stream_key, str(envelope.epoch), envelope.sequence
                        ):
                            raise RuntimeError(
                                "conflicting runtime spool row disappeared"
                            )
                        await connection.execute(
                            delete(schema.envelopes).where(
                                schema.envelopes.c.envelope_id == inserted
                            )
                        )
                        await connection.execute(
                            insert(schema.quarantine)
                            .values(
                                quarantine_id=uuid7(),
                                edge_instance_id=envelope.edge_instance_id,
                                source_name=envelope.source_name,
                                epoch=envelope.epoch,
                                sequence=envelope.sequence,
                                payload_sha256=payload_hash,
                                reason_code="runtime_spool_payload_conflict",
                                details={"event_type": envelope.event_type},
                            )
                            .on_conflict_do_nothing(
                                constraint="quarantine_sequence"
                            )
                        )
                        quarantined_count += 1
                        gap_id = uuid7()
                        gap_start = min(
                            envelope.source_observed_at,
                            envelope.edge_received_at,
                        )
                        gap_end = max(
                            envelope.source_observed_at,
                            envelope.edge_received_at,
                        )
                        if gap_end <= gap_start:
                            gap_end = gap_start + timedelta(microseconds=1)
                        await connection.execute(
                            insert(schema.coverage_intervals).values(
                                coverage_id=gap_id,
                                stream_id=stream_id,
                                entity_id=(
                                    None if redact_identity else envelope.entity_id
                                ),
                                interval=Range(gap_start, gap_end, bounds="[)"),
                                status="gap",
                                reason_code="runtime_spool_payload_conflict",
                                source_sequence_start=envelope.sequence,
                                source_sequence_end=envelope.sequence,
                            )
                        )
                        opened_gaps.append(str(gap_id))
                        inserted = None
                        spool_result = SpoolAppendResult(
                            inserted=False, evicted=()
                        )
                    else:
                        accepted += 1
                        if (
                            spool_allowed
                            and envelope.event_type == "location_fix"
                            and self._optional_work_allowed()
                        ):
                            project_location = True

                # Edge retention/overflow replaces the original observation
                # with a sequence-preserving coverage_gap envelope. Its
                # sequence is contiguous by design, so it must be projected
                # explicitly rather than relying on missing-sequence logic.
                if inserted is not None and envelope.event_type == "coverage_gap":
                    sequence_start = self._bounded_sequence(
                        envelope.payload.get("sequence_start"), envelope.sequence
                    )
                    sequence_end = self._bounded_sequence(
                        envelope.payload.get("sequence_end"), sequence_start
                    )
                    if sequence_end < sequence_start:
                        sequence_end = sequence_start
                    reason_code = str(
                        envelope.payload.get("reason_code") or "edge_coverage_gap"
                    )[:64]
                    start = min(envelope.source_observed_at, envelope.edge_received_at)
                    end = max(envelope.source_observed_at, envelope.edge_received_at)
                    if end <= start:
                        end = start + timedelta(microseconds=1)
                    gap_id = uuid7()
                    await connection.execute(
                        insert(schema.coverage_intervals).values(
                            coverage_id=gap_id,
                            stream_id=stream_id,
                            entity_id=(None if redact_identity else envelope.entity_id),
                            interval=Range(start, end, bounds="[)"),
                            status="gap",
                            reason_code=reason_code,
                            source_sequence_start=sequence_start,
                            source_sequence_end=sequence_end,
                        )
                    )
                    opened_gaps.append(str(gap_id))

                if (
                    inserted is not None
                    and envelope.event_type != "coverage_gap"
                    and raw_payload_status
                    == "suppressed_retention_worker_unavailable"
                ):
                    start = min(envelope.source_observed_at, envelope.edge_received_at)
                    end = max(envelope.source_observed_at, envelope.edge_received_at)
                    if end <= start:
                        end = start + timedelta(microseconds=1)
                    gap_id = uuid7()
                    await connection.execute(
                        insert(schema.coverage_intervals).values(
                            coverage_id=gap_id,
                            stream_id=stream_id,
                            entity_id=(None if redact_identity else envelope.entity_id),
                            interval=Range(start, end, bounds="[)"),
                            status="gap",
                            reason_code="retention_worker_unavailable",
                            source_sequence_start=envelope.sequence,
                            source_sequence_end=envelope.sequence,
                        )
                    )
                    opened_gaps.append(str(gap_id))

                prior_ack = int(
                    (
                        await connection.execute(
                            select(schema.source_streams.c.last_acked_sequence).where(
                                schema.source_streams.c.stream_id == stream_id
                            )
                        )
                    ).scalar_one()
                )
                if inserted is not None and envelope.sequence > prior_ack + 1:
                    gap_id = uuid7()
                    start = min(envelope.source_observed_at, envelope.edge_received_at)
                    end = max(envelope.source_observed_at, envelope.edge_received_at)
                    if end <= start:
                        end = start + timedelta(microseconds=1)
                    await connection.execute(
                        insert(schema.coverage_intervals).values(
                            coverage_id=gap_id,
                            stream_id=stream_id,
                            entity_id=(None if redact_identity else envelope.entity_id),
                            interval=Range(start, end, bounds="[)"),
                            status="gap",
                            reason_code="missing_edge_sequence",
                            source_sequence_start=prior_ack + 1,
                            source_sequence_end=envelope.sequence - 1,
                        )
                    )
                    opened_gaps.append(str(gap_id))

                for (
                    evicted_stream,
                    evicted_epoch,
                    sequence,
                    marker_id,
                ) in spool_result.evicted:
                    now = runtime_now
                    gap_id = uuid.UUID(marker_id)
                    try:
                        edge_instance_id, source_name = evicted_stream.rsplit(":", 1)
                        evicted_stream_id = (
                            await connection.execute(
                                select(schema.source_streams.c.stream_id).where(
                                    schema.source_streams.c.edge_instance_id
                                    == edge_instance_id,
                                    schema.source_streams.c.source_name == source_name,
                                    schema.source_streams.c.epoch
                                    == uuid.UUID(evicted_epoch),
                                )
                            )
                        ).scalar_one_or_none()
                    except (ValueError, AttributeError):
                        evicted_stream_id = None
                    # A just-inserted row always has a stream. If an old cache
                    # row predates durable stream metadata, attach the gap to
                    # the current stream and preserve the reason rather than
                    # dropping evidence of loss.
                    target_stream_id = evicted_stream_id or stream_id
                    await connection.execute(
                        insert(schema.coverage_intervals).values(
                            coverage_id=gap_id,
                            stream_id=target_stream_id,
                            entity_id=(
                                envelope.entity_id
                                if target_stream_id == stream_id
                                else None
                            ),
                            interval=Range(
                                now - timedelta(microseconds=1), now, bounds="[)"
                            ),
                            status="gap",
                            reason_code="runtime_spool_overflow",
                            source_sequence_start=sequence,
                            source_sequence_end=sequence,
                        )
                    )
                    opened_gaps.append(str(gap_id))
                    spool_markers_to_ack.append(marker_id)

                # Projection runs after durable coverage materialization so a
                # fix-gap-fix batch cannot briefly manufacture an arrival.
                if project_location:
                    await self._project_location_envelope(
                        connection,
                        envelope,
                        stream_id=stream_id,
                    )

                acknowledgements[stream_key] = await self._advance_ack(
                    connection, stream_id, envelope
                )

        # Each listed marker now has a committed PostgreSQL coverage interval.
        # Deleting it keeps the runtime spool bounded; a crash before this call
        # leaves only a short-retention duplicate marker.
        self.spool.acknowledge_gap_markers(spool_markers_to_ack)

        return IngestResult(
            accepted=accepted,
            duplicates=duplicates,
            quarantined=quarantined_count,
            acknowledgements=acknowledgements,
            opened_gaps=opened_gaps,
        )

    def _optional_work_allowed(self) -> bool:
        monitor = self.settings.storage_monitor_path
        # Direct domain tests do not mount the deployment sentinel. Production
        # HTTP ingress separately fails closed if the required mount is absent.
        return (
            not monitor.exists()
            or inspect_disk_budget(monitor).optional_work_allowed
        )

    async def edge_privacy_policy(self) -> EdgePrivacyPolicyView:
        async with self.database.transaction() as connection:
            materialized_entities = set(
                (
                    await connection.execute(
                        select(schema.edge_privacy_blocks.c.entity_id).where(
                            schema.edge_privacy_blocks.c.source_system
                            == "home_assistant"
                        )
                    )
                )
                .scalars()
                .all()
            )
            now = datetime.now(UTC)
            directive_gate = or_(
                schema.privacy_directives.c.directive.in_(["do_not_track", "ignored"]),
                and_(
                    schema.privacy_directives.c.directive == "auto_expire",
                    schema.privacy_directives.c.expires_at <= now,
                ),
            )
            derived_entities = set(
                (
                    await connection.execute(
                        select(schema.source_entity_bindings.c.entity_id)
                        .select_from(
                            schema.source_entity_bindings.join(
                                schema.privacy_directives,
                                schema.source_entity_bindings.c.person_id
                                == schema.privacy_directives.c.person_id,
                            )
                        )
                        .where(
                            schema.source_entity_bindings.c.source_system
                            == "home_assistant",
                            schema.privacy_directives.c.enabled.is_(True),
                            directive_gate,
                        )
                    )
                )
                .scalars()
                .all()
            )
            materialized_users = set(
                (
                    await connection.execute(
                        select(schema.edge_privacy_user_blocks.c.ha_user_id)
                    )
                )
                .scalars()
                .all()
            )
            derived_users = set(
                (
                    await connection.execute(
                        select(schema.ha_user_bindings.c.ha_user_id)
                        .select_from(
                            schema.ha_user_bindings.join(
                                schema.privacy_directives,
                                schema.ha_user_bindings.c.person_id
                                == schema.privacy_directives.c.person_id,
                            )
                        )
                        .where(
                            schema.privacy_directives.c.enabled.is_(True),
                            directive_gate,
                        )
                    )
                )
                .scalars()
                .all()
            )
        entities = sorted(materialized_entities | derived_entities)
        users = sorted(materialized_users | derived_users)
        material = {
            "version": 1,
            "blocked_entity_ids": entities,
            "blocked_user_ids": users,
        }
        return EdgePrivacyPolicyView(
            blocked_entity_ids=entities,
            blocked_user_ids=users,
            policy_digest=sha256_json(material),
        )

    async def quarantine_invalid(
        self,
        raw: Any,
        validation_errors: list[dict[str, Any]],
    ) -> IngestResult:
        """Durably quarantine one malformed item without persisting its content.

        A structurally valid delivery header can be acknowledged after the
        content-free quarantine receipt commits. An invalid header cannot be
        safely attributed or acknowledged and will therefore remain visible to
        the edge retry/dead-letter policy.
        """

        source = raw if isinstance(raw, dict) else {}
        edge_instance_id = str(source.get("edge_instance_id") or "<missing>")[:128]
        source_name = str(source.get("source_name") or "<missing>")[:128]
        try:
            epoch = uuid.UUID(str(source.get("epoch")))
        except (ValueError, TypeError, AttributeError):
            epoch = None
        try:
            sequence = int(source.get("sequence"))
            if sequence <= 0 or sequence >= 2**63:
                sequence = None
        except (ValueError, TypeError):
            sequence = None
        raw_entity = str(source.get("entity_id") or "")
        precise_location = source.get(
            "event_type"
        ) == "location_fix" or raw_entity.startswith(
            ("person.", "device_tracker.", "zone.")
        )
        payload_hash = self._payload_digest(
            canonical_json(raw), precise_location=precise_location
        )
        details = {
            "errors": [
                {
                    "location": [str(part) for part in error.get("loc", ())],
                    "type": str(error.get("type") or "validation_error")[:64],
                }
                for error in validation_errors[:20]
            ]
        }
        acknowledgements: dict[str, int] = {}
        async with self.database.transaction() as connection:
            await connection.execute(
                insert(schema.quarantine)
                .values(
                    quarantine_id=uuid7(),
                    edge_instance_id=edge_instance_id,
                    source_name=source_name,
                    epoch=epoch,
                    sequence=sequence,
                    payload_sha256=payload_hash,
                    reason_code="schema_validation_failed",
                    details=details,
                )
                .on_conflict_do_nothing(constraint="quarantine_sequence")
            )
            if (
                edge_instance_id != "<missing>"
                and source_name != "<missing>"
                and epoch is not None
                and sequence is not None
            ):
                stream_id = await self._ensure_stream_values(
                    connection, edge_instance_id, source_name, epoch
                )
                key = f"{edge_instance_id}:{source_name}"
                acknowledgements[key] = await self._advance_ack_values(
                    connection,
                    stream_id,
                    edge_instance_id=edge_instance_id,
                    source_name=source_name,
                    epoch=epoch,
                )
        return IngestResult(
            accepted=0,
            duplicates=0,
            quarantined=1,
            acknowledgements=acknowledgements,
        )

    async def _ensure_stream(self, connection, envelope) -> uuid.UUID:
        return await self._ensure_stream_values(
            connection,
            envelope.edge_instance_id,
            envelope.source_name,
            envelope.epoch,
        )

    async def _ensure_stream_values(
        self,
        connection,
        edge_instance_id: str,
        source_name: str,
        epoch: uuid.UUID,
    ) -> uuid.UUID:
        proposed_id = uuid7()
        statement = (
            insert(schema.source_streams)
            .values(
                stream_id=proposed_id,
                edge_instance_id=edge_instance_id,
                source_name=source_name,
                epoch=epoch,
            )
            .on_conflict_do_update(
                constraint="source_stream_identity",
                set_={"updated_at": func.now()},
            )
            .returning(schema.source_streams.c.stream_id)
        )
        return (await connection.execute(statement)).scalar_one()

    async def _advance_ack(self, connection, stream_id: uuid.UUID, envelope) -> int:
        return await self._advance_ack_values(
            connection,
            stream_id,
            edge_instance_id=envelope.edge_instance_id,
            source_name=envelope.source_name,
            epoch=envelope.epoch,
        )

    async def _advance_ack_values(
        self,
        connection,
        stream_id: uuid.UUID,
        *,
        edge_instance_id: str,
        source_name: str,
        epoch: uuid.UUID,
    ) -> int:
        current = int(
            (
                await connection.execute(
                    select(schema.source_streams.c.last_acked_sequence).where(
                        schema.source_streams.c.stream_id == stream_id
                    )
                )
            ).scalar_one()
        )
        while True:
            next_sequence = current + 1
            accepted = (
                await connection.execute(
                    select(literal(1)).where(
                        or_(
                            select(schema.envelopes.c.envelope_id)
                            .where(
                                schema.envelopes.c.stream_id == stream_id,
                                schema.envelopes.c.sequence == next_sequence,
                            )
                            .exists(),
                            select(schema.quarantine.c.quarantine_id)
                            .where(
                                schema.quarantine.c.edge_instance_id
                                == edge_instance_id,
                                schema.quarantine.c.source_name == source_name,
                                schema.quarantine.c.epoch == epoch,
                                schema.quarantine.c.sequence == next_sequence,
                            )
                            .exists(),
                        )
                    )
                )
            ).scalar_one_or_none()
            if accepted is None:
                break
            current = next_sequence
        await connection.execute(
            update(schema.source_streams)
            .where(schema.source_streams.c.stream_id == stream_id)
            .values(last_acked_sequence=current, updated_at=func.now())
        )
        return current

    @staticmethod
    def _quarantine_reason(envelope) -> str | None:
        if envelope.event_type not in ALLOWED_EVENT_TYPES:
            return "event_type_not_allowlisted"
        if envelope.source_observed_at > envelope.edge_received_at + timedelta(
            minutes=5
        ):
            return "source_clock_in_future"
        if len(canonical_json(envelope.payload)) > 512 * 1024:
            return "payload_too_large"
        return None

    @staticmethod
    def _freshness(observed_at: datetime, received_at: datetime) -> str:
        age = received_at - observed_at
        if age < timedelta(0) or age > timedelta(minutes=45):
            return "stale"
        if age > timedelta(minutes=15):
            return "aging"
        return "fresh"

    @staticmethod
    def _bounded_sequence(value: Any, default: int) -> int:
        if isinstance(value, bool):
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if 0 < parsed < 2**63 else default

    @staticmethod
    def _is_precise_location(envelope) -> bool:
        if envelope.event_type == "location_fix":
            return True
        entity_id = str(envelope.entity_id or "")
        return entity_id.startswith(("person.", "device_tracker.", "zone."))

    def _payload_digest(self, payload: bytes, *, precise_location: bool) -> str:
        # All event content is private by default. A keyed digest preserves
        # replay/conflict detection without exposing short utterances, notes,
        # or malformed hostile payloads to offline dictionary attacks.
        del precise_location
        return hmac.new(self._payload_digest_key, payload, hashlib.sha256).hexdigest()

    async def _retention_decision(
        self,
        connection,
        envelope,
        *,
        precise_location: bool,
    ) -> tuple[bool, str, bool]:
        ha_user_id = str(envelope.ha_context.user_id or "").strip()
        if ha_user_id:
            materialized_user_block = (
                await connection.execute(
                    select(schema.edge_privacy_user_blocks.c.reason_code)
                    .where(schema.edge_privacy_user_blocks.c.ha_user_id == ha_user_id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if materialized_user_block is not None:
                return False, f"suppressed_{materialized_user_block}", True
            user_binding = (
                (
                    await connection.execute(
                        select(
                            schema.ha_user_bindings.c.principal_id,
                            schema.ha_user_bindings.c.person_id,
                        ).where(
                            schema.ha_user_bindings.c.ha_user_id == ha_user_id,
                            schema.ha_user_bindings.c.revoked_at.is_(None),
                        )
                    )
                )
                .mappings()
                .first()
            )
            if user_binding is None:
                # This also covers a restore from before an erased binding was
                # created. Unknown user-attributed content is never raw-spooled
                # or retained with an identity-bearing HA context.
                return False, "suppressed_unbound_user", True
            user_directives = await self._active_directives(
                connection, user_binding["person_id"]
            )
            privacy_block = next(
                (
                    directive
                    for directive in ("ignored", "do_not_track", "auto_expire")
                    if directive in user_directives
                ),
                None,
            )
            if privacy_block is not None:
                return False, f"suppressed_{privacy_block}", True

        entity_id = str(envelope.entity_id or "")
        binding_entity = (
            str(envelope.metadata.get("derived_from_entity") or entity_id)
            if entity_id.startswith("person.")
            else entity_id
        )
        if not binding_entity:
            return (
                (False, "suppressed_unbound_location", True)
                if precise_location
                else (True, "encrypted_runtime", False)
            )
        binding = (
            (
                await connection.execute(
                    select(
                        schema.source_entity_bindings.c.principal_id,
                        schema.source_entity_bindings.c.person_id,
                    ).where(
                        schema.source_entity_bindings.c.source_system
                        == "home_assistant",
                        schema.source_entity_bindings.c.entity_id == binding_entity,
                        schema.source_entity_bindings.c.revoked_at.is_(None),
                    )
                )
            )
            .mappings()
            .first()
        )
        if binding is None:
            return (
                (False, "suppressed_unbound_location", True)
                if precise_location
                else (True, "encrypted_runtime", False)
            )

        await connection.execute(
            select(
                func.set_config(
                    "app.principal_id",
                    str(binding["principal_id"]),
                    True,
                )
            )
        )
        privacy_block = (
            await connection.execute(
                select(schema.privacy_directives.c.directive)
                .where(
                    schema.privacy_directives.c.person_id == binding["person_id"],
                    schema.privacy_directives.c.enabled.is_(True),
                    or_(
                        and_(
                            schema.privacy_directives.c.directive.in_(
                                ["do_not_track", "ignored"]
                            ),
                            schema.privacy_directives.c.expires_at.is_(None),
                        ),
                        and_(
                            schema.privacy_directives.c.directive == "auto_expire",
                            schema.privacy_directives.c.expires_at <= datetime.now(UTC),
                        ),
                    ),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if privacy_block is not None:
            return False, f"suppressed_{privacy_block}", True
        if not precise_location:
            return True, "encrypted_runtime", False
        if not await self._preference_enabled(
            connection, binding["principal_id"], "location_memory"
        ):
            return False, "suppressed_location_memory_disabled", True
        return True, "encrypted_runtime", False

    async def _project_location_envelope(
        self,
        connection,
        envelope,
        *,
        stream_id: uuid.UUID,
    ) -> None:
        """Project a stable visit from an opted-in tracker stream.

        Person projections and zone copies are deliberately excluded. The
        device tracker is the physical observation root; copied HA entities do
        not become independent support.
        """

        if (
            not envelope.entity_id
            or not envelope.entity_id.startswith("device_tracker.")
            or envelope.metadata.get("projection_type") != "device_tracker"
            or envelope.coverage != "continuous"
        ):
            return
        binding = (
            (
                await connection.execute(
                    select(
                        schema.source_entity_bindings.c.principal_id,
                        schema.source_entity_bindings.c.person_id,
                    ).where(
                        schema.source_entity_bindings.c.source_system
                        == "home_assistant",
                        schema.source_entity_bindings.c.entity_id == envelope.entity_id,
                        schema.source_entity_bindings.c.revoked_at.is_(None),
                    )
                )
            )
            .mappings()
            .first()
        )
        if binding is None:
            return
        await connection.execute(
            select(
                func.set_config(
                    "app.principal_id",
                    str(binding["principal_id"]),
                    True,
                )
            )
        )
        if not await self._preference_enabled(
            connection, binding["principal_id"], "location_memory"
        ):
            return

        tracker_entities = (
            (
                await connection.execute(
                    select(schema.source_entity_bindings.c.entity_id).where(
                        schema.source_entity_bindings.c.principal_id
                        == binding["principal_id"],
                        schema.source_entity_bindings.c.source_system
                        == "home_assistant",
                        schema.source_entity_bindings.c.entity_id.like(
                            "device_tracker.%"
                        ),
                        schema.source_entity_bindings.c.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        now = datetime.now(UTC)
        recent_records = []
        seen_records: set[tuple[str, str, int]] = set()
        for entity_id in tracker_entities:
            for record in self.spool.recent_for_entity(
                entity_id,
                since=envelope.source_observed_at - timedelta(minutes=30),
                now=now,
            ):
                key = (record.stream_key, record.epoch, record.sequence)
                if key not in seen_records:
                    seen_records.add(key)
                    recent_records.append(record)
        recent_records.sort(key=lambda record: (record.observed_at, record.sequence))
        fix_records = [
            (record, fix)
            for record in recent_records
            if (fix := self._location_fix(record)) is not None
        ]
        if not fix_records:
            return
        expected_stream_key = f"{envelope.edge_instance_id}:{envelope.source_name}"
        current = next(
            (
                item
                for item in reversed(fix_records)
                if item[0].stream_key == expected_stream_key
                and item[0].epoch == str(envelope.epoch)
                and item[0].sequence == envelope.sequence
            ),
            None,
        )
        if current is None:
            return
        latest_record, latest = current
        if latest.accuracy_m is None or latest.accuracy_m > 250:
            return

        recent_other_trackers = [
            item
            for item in fix_records
            if item[1].tracker_source != latest.tracker_source
            and latest.observed_at - timedelta(minutes=30)
            <= item[1].observed_at
            <= latest.observed_at
        ]
        if recent_other_trackers:
            first_conflict = min(
                (item[1].observed_at for item in recent_other_trackers),
                default=latest.observed_at,
            )
            end = max(latest.observed_at + timedelta(microseconds=1), now)
            await connection.execute(
                insert(schema.coverage_intervals).values(
                    coverage_id=uuid7(),
                    stream_id=stream_id,
                    entity_id=envelope.entity_id,
                    interval=Range(first_conflict, end, bounds="[)"),
                    status="unknown",
                    reason_code="tracker_source_switch",
                    source_sequence_start=latest_record.sequence,
                    source_sequence_end=latest_record.sequence,
                )
            )
            await self._mark_location_conflicted(
                connection, principal_id=binding["principal_id"]
            )
            return

        cluster_records = []
        for record, fix in reversed(fix_records):
            if (
                record.stream_key != latest_record.stream_key
                or record.epoch != latest_record.epoch
            ):
                continue
            if fix.tracker_source != latest.tracker_source:
                continue
            if latest.observed_at - fix.observed_at > timedelta(minutes=30):
                break
            if (
                haversine_m(
                    latest.latitude,
                    latest.longitude,
                    fix.latitude,
                    fix.longitude,
                )
                > 250
            ):
                break
            cluster_records.append((record, fix))
        cluster_records.reverse()
        cluster = [fix for _, fix in cluster_records]
        locality_decision = derive_locality_presence(cluster)
        if not locality_decision.accepted:
            return
        if freshness_at(latest.observed_at, now=now) != "fresh":
            return
        if await self._arrival_has_material_gap(
            connection,
            stream_id=stream_id,
            first_record=cluster_records[0][0],
            last_record=cluster_records[-1][0],
        ):
            return

        specific_anchor = derive_specific_anchor(cluster)
        specific_supported = bool(
            specific_anchor.accepted
            and latest.accuracy_m is not None
            and latest.accuracy_m <= 50
        )

        locality = await self._match_locality(
            connection,
            binding["principal_id"],
            latitude=locality_decision.latitude,
            longitude=locality_decision.longitude,
            accuracy_m=float(latest.accuracy_m or 0),
        )
        if locality is None:
            await self._depart_active_visits(
                connection, principal_id=binding["principal_id"]
            )
            return

        # Locality is the coarse, reviewed boundary. Only after it uniquely
        # matches do we decrypt this principal's small set of active child
        # property locators. Zero or multiple matches stay at locality
        # resolution; ambiguity never selects a property.
        specific_property = (
            await self._match_specific_property(
                connection,
                binding["principal_id"],
                locality_id=locality["place_id"],
                latitude=specific_anchor.latitude,
                longitude=specific_anchor.longitude,
            )
            if specific_supported
            else None
        )
        resolved_place_id = (
            specific_property["place_id"]
            if specific_property is not None
            else locality["place_id"]
        )

        existing = (
            (
                await connection.execute(
                    select(schema.visits)
                    .select_from(
                        schema.visits.outerjoin(
                            schema.places,
                            schema.visits.c.place_id == schema.places.c.place_id,
                        )
                    )
                    .where(
                        schema.visits.c.principal_id == binding["principal_id"],
                        or_(
                            schema.visits.c.place_id == locality["place_id"],
                            schema.places.c.parent_place_id == locality["place_id"],
                        ),
                        schema.visits.c.state.in_(["candidate", "visiting"]),
                        func.upper(schema.visits.c.observed_range)
                        >= cluster[0].observed_at - timedelta(hours=12),
                    )
                    .order_by(func.upper(schema.visits.c.observed_range).desc())
                    .limit(1)
                    .with_for_update(of=schema.visits)
                )
            )
            .mappings()
            .first()
        )
        visit_id = existing["visit_id"] if existing else uuid7()
        if (
            existing
            and not specific_supported
            and existing["anchor_kind"] == "specific"
            and existing["place_id"] != locality["place_id"]
        ):
            resolved_place_id = existing["place_id"]
        anchor_kind = "specific" if specific_supported else "locality"
        root_observation_ids = (
            specific_anchor.root_observation_ids
            if specific_supported
            else locality_decision.root_observation_ids
        )
        if specific_supported:
            anchor_latitude = specific_anchor.latitude
            anchor_longitude = specific_anchor.longitude
            anchor_radius_m = int(specific_anchor.radius_m or 100)
        else:
            anchor_latitude = locality_decision.latitude
            anchor_longitude = locality_decision.longitude
            anchor_radius_m = min(
                250,
                max(100, int((locality_decision.maximum_accuracy_m or 250) * 2)),
            )
        anchor_payload = {
            "resolution": anchor_kind,
            "latitude": anchor_latitude,
            "longitude": anchor_longitude,
            "radius_m": anchor_radius_m,
            "root_observation_ids": [str(value) for value in root_observation_ids],
        }
        sealed = self.knowledge_cipher.seal(
            canonical_json(anchor_payload),
            purpose="visit-anchor",
            artifact_id=str(visit_id),
        )
        observed_from = (
            min(existing["observed_range"].lower, cluster[0].observed_at)
            if existing
            else cluster[0].observed_at
        )
        observed_to = max(
            existing["observed_range"].upper if existing else cluster[-1].observed_at,
            cluster[-1].observed_at,
        )
        if existing:
            values: dict[str, Any] = {
                "place_id": resolved_place_id,
                "observed_range": Range(observed_from, observed_to, bounds="[)"),
                "state": "visiting",
                "freshness": "fresh",
                "coverage": "sufficient",
            }
            if specific_supported or existing["anchor_kind"] != "specific":
                values.update(
                    anchor_ciphertext=sealed.ciphertext,
                    anchor_nonce=sealed.nonce,
                    anchor_sha256=sealed.sha256,
                    anchor_kind=anchor_kind,
                    anchor_radius_m=anchor_radius_m,
                )
            await connection.execute(
                update(schema.visits)
                .where(schema.visits.c.visit_id == visit_id)
                .values(**values)
            )
        else:
            await connection.execute(
                update(schema.visits)
                .where(
                    schema.visits.c.principal_id == binding["principal_id"],
                    schema.visits.c.state == "visiting",
                    schema.visits.c.place_id != resolved_place_id,
                )
                .values(state="departed")
            )
            await connection.execute(
                insert(schema.visits).values(
                    visit_id=visit_id,
                    principal_id=binding["principal_id"],
                    place_id=resolved_place_id,
                    first_root_observation_id=root_observation_ids[0],
                    observed_range=Range(observed_from, observed_to, bounds="[)"),
                    state="visiting",
                    freshness="fresh",
                    coverage="sufficient",
                    anchor_ciphertext=sealed.ciphertext,
                    anchor_nonce=sealed.nonce,
                    anchor_sha256=sealed.sha256,
                    anchor_kind=anchor_kind,
                    anchor_radius_m=anchor_radius_m,
                )
            )
        # A child property never self-authorizes proactive disclosure. Greeting
        # eligibility is inherited only from its uniquely matched locality.
        if locality["travel_greeting_eligible"]:
            await self._ensure_travel_initiative(
                connection,
                principal_id=binding["principal_id"],
                visit_id=visit_id,
            )

    async def _arrival_has_material_gap(
        self,
        connection,
        *,
        stream_id: uuid.UUID,
        first_record,
        last_record,
    ) -> bool:
        start_sequence = min(first_record.sequence, last_record.sequence)
        end_sequence = max(first_record.sequence, last_record.sequence)
        start_time = min(first_record.observed_at, last_record.observed_at)
        end_time = max(first_record.observed_at, last_record.observed_at)
        noncontinuous = (
            await connection.execute(
                select(func.count())
                .select_from(schema.envelopes)
                .where(
                    schema.envelopes.c.stream_id == stream_id,
                    schema.envelopes.c.sequence.between(start_sequence, end_sequence),
                    or_(
                        schema.envelopes.c.coverage.in_(
                            ["snapshot_only", "gap", "unknown"]
                        ),
                        schema.envelopes.c.event_type == "coverage_gap",
                    ),
                )
            )
        ).scalar_one()
        if noncontinuous:
            return True
        interval_gap = (
            await connection.execute(
                select(func.count())
                .select_from(schema.coverage_intervals)
                .where(
                    schema.coverage_intervals.c.stream_id == stream_id,
                    schema.coverage_intervals.c.status.in_(
                        ["gap", "unknown", "snapshot_only"]
                    ),
                    or_(
                        and_(
                            schema.coverage_intervals.c.source_sequence_start
                            <= end_sequence,
                            func.coalesce(
                                schema.coverage_intervals.c.source_sequence_end,
                                schema.coverage_intervals.c.source_sequence_start,
                            )
                            >= start_sequence,
                        ),
                        and_(
                            func.lower(schema.coverage_intervals.c.interval)
                            <= end_time,
                            func.upper(schema.coverage_intervals.c.interval)
                            >= start_time,
                        ),
                    ),
                )
            )
        ).scalar_one()
        return bool(interval_gap)

    async def _mark_location_conflicted(
        self, connection, *, principal_id: uuid.UUID
    ) -> None:
        visit_ids = (
            (
                await connection.execute(
                    select(schema.visits.c.visit_id).where(
                        schema.visits.c.principal_id == principal_id,
                        schema.visits.c.state.in_(["candidate", "visiting"]),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not visit_ids:
            return
        await connection.execute(
            update(schema.visits)
            .where(schema.visits.c.visit_id.in_(visit_ids))
            .values(state="conflicted", coverage="partial")
        )
        await connection.execute(
            update(schema.initiatives)
            .where(
                schema.initiatives.c.visit_id.in_(visit_ids),
                schema.initiatives.c.state.in_(["pending", "claimed"]),
            )
            .values(state="suppressed", suppression_reason="tracker_source_conflict")
        )

    async def _depart_active_visits(
        self, connection, *, principal_id: uuid.UUID
    ) -> None:
        visit_ids = (
            (
                await connection.execute(
                    select(schema.visits.c.visit_id).where(
                        schema.visits.c.principal_id == principal_id,
                        schema.visits.c.state.in_(["candidate", "visiting"]),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not visit_ids:
            return
        await connection.execute(
            update(schema.visits)
            .where(schema.visits.c.visit_id.in_(visit_ids))
            .values(state="departed")
        )
        await connection.execute(
            update(schema.initiatives)
            .where(
                schema.initiatives.c.visit_id.in_(visit_ids),
                schema.initiatives.c.state.in_(["pending", "claimed"]),
            )
            .values(state="expired", suppression_reason="visit_departed")
        )

    @staticmethod
    def _location_fix(record) -> LocationFix | None:
        try:
            if record.metadata.get("coverage") != "continuous":
                return None
            new_state = record.payload.get("new_state")
            attributes = (
                new_state.get("attributes") if isinstance(new_state, dict) else None
            )
            if not isinstance(attributes, dict):
                return None
            latitude = float(attributes["latitude"])
            longitude = float(attributes["longitude"])
            accuracy = float(attributes["gps_accuracy"])
            root_id = uuid.UUID(str(record.metadata["root_observation_id"]))
            entity_id = str(record.metadata["entity_id"])
            return LocationFix(
                latitude=latitude,
                longitude=longitude,
                accuracy_m=accuracy,
                observed_at=record.observed_at,
                root_observation_id=root_id,
                tracker_source=entity_id,
            )
        except (KeyError, TypeError, ValueError):
            return None

    async def _match_locality(
        self,
        connection,
        principal_id: uuid.UUID,
        *,
        latitude: float | None,
        longitude: float | None,
        accuracy_m: float,
    ) -> dict[str, Any] | None:
        if latitude is None or longitude is None:
            return None
        candidates = (
            (
                await connection.execute(
                    select(
                        schema.places.c.place_id,
                        schema.places.c.canonical_name,
                        schema.places.c.travel_greeting_eligible,
                        schema.place_locators.c.locator_id,
                        schema.place_locators.c.nonce,
                        schema.place_locators.c.ciphertext,
                        schema.place_locators.c.locator_sha256,
                        schema.place_locators.c.radius_m,
                    )
                    .select_from(
                        schema.places.join(
                            schema.place_locators,
                            schema.places.c.place_id
                            == schema.place_locators.c.place_id,
                        )
                    )
                    .where(
                        schema.places.c.owner_principal_id == principal_id,
                        schema.places.c.place_type == "locality",
                        schema.places.c.status == "active",
                        func.upper_inf(schema.place_locators.c.valid_range),
                    )
                )
            )
            .mappings()
            .all()
        )
        matches: list[dict[str, Any]] = []
        for candidate in candidates:
            try:
                raw = self.knowledge_cipher.open(
                    SealedValue(
                        nonce=bytes(candidate["nonce"]),
                        ciphertext=bytes(candidate["ciphertext"]),
                        sha256=candidate["locator_sha256"],
                    ),
                    purpose="place-locator",
                    artifact_id=str(candidate["locator_id"]),
                )
                locator = json.loads(raw)
                distance = haversine_m(
                    latitude,
                    longitude,
                    float(locator["latitude"]),
                    float(locator["longitude"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if distance <= float(candidate["radius_m"]) + accuracy_m:
                matches.append(dict(candidate))
        return matches[0] if len(matches) == 1 else None

    async def _match_specific_property(
        self,
        connection,
        principal_id: uuid.UUID,
        *,
        locality_id: uuid.UUID,
        latitude: float | None,
        longitude: float | None,
    ) -> dict[str, Any] | None:
        if latitude is None or longitude is None:
            return None
        candidates = (
            (
                await connection.execute(
                    select(
                        schema.places.c.place_id,
                        schema.place_locators.c.locator_id,
                        schema.place_locators.c.nonce,
                        schema.place_locators.c.ciphertext,
                        schema.place_locators.c.locator_sha256,
                        schema.place_locators.c.radius_m,
                    )
                    .select_from(
                        schema.places.join(
                            schema.place_locators,
                            schema.places.c.place_id
                            == schema.place_locators.c.place_id,
                        )
                    )
                    .where(
                        schema.places.c.owner_principal_id == principal_id,
                        schema.places.c.parent_place_id == locality_id,
                        schema.places.c.place_type == "property",
                        schema.places.c.status == "active",
                        func.upper_inf(schema.place_locators.c.valid_range),
                    )
                )
            )
            .mappings()
            .all()
        )
        matches: list[dict[str, Any]] = []
        for candidate in candidates:
            try:
                raw = self.knowledge_cipher.open(
                    SealedValue(
                        nonce=bytes(candidate["nonce"]),
                        ciphertext=bytes(candidate["ciphertext"]),
                        sha256=candidate["locator_sha256"],
                    ),
                    purpose="place-locator",
                    artifact_id=str(candidate["locator_id"]),
                )
                locator = json.loads(raw)
                distance = haversine_m(
                    latitude,
                    longitude,
                    float(locator["latitude"]),
                    float(locator["longitude"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if distance <= float(candidate["radius_m"]):
                matches.append(dict(candidate))
        return matches[0] if len(matches) == 1 else None

    async def _ensure_travel_initiative(
        self,
        connection,
        *,
        principal_id: uuid.UUID,
        visit_id: uuid.UUID,
    ) -> None:
        if self.settings.rollout_mode != "canary":
            return
        if await self._person_has_any_directive(
            connection,
            principal_id=principal_id,
            directives={"do_not_track", "ignored", "silent", "private", "auto_expire"},
        ):
            return
        if not await self._preference_enabled(
            connection, principal_id, "travel_greetings"
        ):
            return
        if not await self._preference_enabled(
            connection, principal_id, "location_memory"
        ):
            return
        visit = (
            (
                await connection.execute(
                    select(
                        schema.visits.c.place_id,
                        schema.visits.c.state,
                        schema.visits.c.coverage,
                        schema.visits.c.anchor_kind,
                    ).where(
                        schema.visits.c.visit_id == visit_id,
                        schema.visits.c.principal_id == principal_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        if (
            visit is None
            or visit["state"] != "visiting"
            or visit["coverage"] != "sufficient"
            or visit["anchor_kind"] != "specific"
            or visit["place_id"] is None
        ):
            return
        place = (
            (
                await connection.execute(
                    select(
                        schema.places.c.place_type,
                        schema.places.c.parent_place_id,
                    ).where(
                        schema.places.c.place_id == visit["place_id"],
                        schema.places.c.owner_principal_id == principal_id,
                        schema.places.c.place_type == "property",
                        schema.places.c.status == "active",
                    )
                )
            )
            .mappings()
            .first()
        )
        if place is None or place["parent_place_id"] is None:
            return
        locality_enabled = (
            await connection.execute(
                select(schema.places.c.travel_greeting_eligible).where(
                    schema.places.c.place_id == place["parent_place_id"],
                    schema.places.c.owner_principal_id == principal_id,
                    schema.places.c.place_type == "locality",
                    schema.places.c.status == "active",
                )
            )
        ).scalar_one_or_none()
        if locality_enabled is not True:
            return
        descriptor_fact_version_id = (
            await connection.execute(
                select(schema.fact_versions.c.fact_version_id).where(
                    schema.fact_versions.c.subject_type == "place",
                    schema.fact_versions.c.subject_id == visit["place_id"],
                    schema.fact_versions.c.predicate == "place_social_descriptor",
                    schema.fact_versions.c.perspective_principal_id == principal_id,
                    schema.fact_versions.c.authority == "explicit_subject",
                    schema.fact_versions.c.support == "explicit_authority",
                    schema.fact_versions.c.contradiction == "none",
                    schema.fact_versions.c.resolution == "accepted",
                    func.upper_inf(schema.fact_versions.c.system_range),
                )
            )
        ).scalar_one_or_none()
        if descriptor_fact_version_id is None:
            return
        await connection.execute(
            insert(schema.initiatives)
            .values(
                initiative_id=uuid7(),
                principal_id=principal_id,
                visit_id=visit_id,
                source_fact_version_id=descriptor_fact_version_id,
                purpose="travel_arrival",
                dedupe_key=f"travel-arrival:{principal_id}:{visit_id}:v1",
                sensitivity="private_location",
                allowed_surfaces=["private_tauri"],
                template_key="travel_arrival_v1",
                state="pending",
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
        )

    async def create_person(self, value: PersonCreate) -> PersonView:
        self._require_rollout("shadow", "semantic_people_write")
        person_id = value.person_id or uuid7()
        async with self.database.transaction(serializable=True) as connection:
            row = (
                (
                    await connection.execute(
                        insert(schema.people)
                        .values(
                            person_id=person_id,
                            display_name=value.display_name,
                            pronouns=value.pronouns,
                            privacy_scope=value.privacy_scope,
                            legacy_source_ref=value.legacy_source_ref,
                            legacy_source_version=value.legacy_source_version,
                            legacy_source_sha256=value.legacy_source_sha256,
                        )
                        .on_conflict_do_nothing(index_elements=["person_id"])
                        .returning(schema.people)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                row = (
                    (
                        await connection.execute(
                            select(schema.people)
                            .where(schema.people.c.person_id == person_id)
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
                expected = {
                    "display_name": value.display_name,
                    "pronouns": value.pronouns,
                    "privacy_scope": value.privacy_scope,
                    "legacy_source_ref": value.legacy_source_ref,
                    "legacy_source_version": value.legacy_source_version,
                    "legacy_source_sha256": value.legacy_source_sha256,
                    "status": "active",
                }
                if any(
                    row[key] != expected_value
                    for key, expected_value in expected.items()
                ):
                    raise ConflictError("person import projection drifted")
        return PersonView(
            person_id=row["person_id"],
            display_name=row["display_name"],
            privacy_scope=row["privacy_scope"],
            status=row["status"],
        )

    async def verify_reviewed_person(self, value: ReviewedPersonVerify) -> PersonView:
        self._require_rollout("shadow", "semantic_people_write")
        async with self.database.transaction(serializable=True) as connection:
            row = (
                (
                    await connection.execute(
                        select(schema.people)
                        .where(schema.people.c.person_id == value.person_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFoundError("reviewed person does not exist")
            if row["status"] != "active" or any(
                (
                    row["display_name"] != value.display_name,
                    row["legacy_source_sha256"] != value.legacy_source_sha256,
                )
            ):
                raise ConflictError("reviewed person projection drifted")
        return PersonView(
            person_id=row["person_id"],
            display_name=row["display_name"],
            privacy_scope=row["privacy_scope"],
            status=row["status"],
        )

    async def import_reviewed_alias(
        self, person_id: uuid.UUID, value: ReviewedAliasImport
    ) -> ReviewedImportReceipt:
        self._require_rollout("shadow", "semantic_people_write")
        alias_id = uuid7()
        normalized = self._normalize_alias(value.alias)
        async with self.database.transaction(serializable=True) as connection:
            if (
                await connection.execute(
                    select(schema.people.c.person_id).where(
                        schema.people.c.person_id == person_id,
                        schema.people.c.status != "erased",
                    )
                )
            ).scalar_one_or_none() is None:
                raise NotFoundError("reviewed person does not exist")
            inserted = (
                await connection.execute(
                    insert(schema.aliases)
                    .values(
                        alias_id=alias_id,
                        person_id=person_id,
                        alias=value.alias,
                        normalized_alias=normalized,
                        alias_kind=value.alias_kind,
                        source_ref=value.source_ref,
                        source_version=value.source_version,
                        source_snapshot_sha256=value.source_snapshot_sha256,
                    )
                    .on_conflict_do_nothing(index_elements=["normalized_alias"])
                    .returning(schema.aliases.c.alias_id)
                )
            ).scalar_one_or_none()
            if inserted is None:
                existing = (
                    (
                        await connection.execute(
                            select(schema.aliases)
                            .where(schema.aliases.c.normalized_alias == normalized)
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
                expected = {
                    "person_id": person_id,
                    "alias": value.alias,
                    "alias_kind": value.alias_kind,
                    "source_ref": value.source_ref,
                    "source_version": value.source_version,
                    "source_snapshot_sha256": value.source_snapshot_sha256,
                }
                if any(existing[key] != item for key, item in expected.items()):
                    raise ConflictError("reviewed alias collides or projection drifted")
                alias_id = existing["alias_id"]
        return ReviewedImportReceipt(record_id=alias_id, state="active")

    async def import_reviewed_recognition_binding(
        self,
        person_id: uuid.UUID,
        value: ReviewedRecognitionBindingImport,
    ) -> ReviewedImportReceipt:
        self._require_rollout("shadow", "recognition_binding_cutover")
        binding_id = uuid7()
        async with self.database.transaction(serializable=True) as connection:
            if (
                await connection.execute(
                    select(schema.people.c.person_id).where(
                        schema.people.c.person_id == person_id,
                        schema.people.c.status != "erased",
                    )
                )
            ).scalar_one_or_none() is None:
                raise NotFoundError("reviewed person does not exist")
            inserted = (
                await connection.execute(
                    insert(schema.external_recognition_bindings)
                    .values(
                        binding_id=binding_id,
                        person_id=person_id,
                        external_system=value.external_system,
                        external_id=value.external_id,
                        status=value.status,
                        source_ref=value.source_ref,
                        source_version=value.source_version,
                        source_snapshot_sha256=value.source_snapshot_sha256,
                    )
                    .on_conflict_do_nothing(constraint="recognition_external_id")
                    .returning(schema.external_recognition_bindings.c.binding_id)
                )
            ).scalar_one_or_none()
            if inserted is None:
                existing = (
                    (
                        await connection.execute(
                            select(schema.external_recognition_bindings)
                            .where(
                                schema.external_recognition_bindings.c.external_system
                                == value.external_system,
                                schema.external_recognition_bindings.c.external_id
                                == value.external_id,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
                expected = {
                    "person_id": person_id,
                    "status": value.status,
                    "source_ref": value.source_ref,
                    "source_version": value.source_version,
                    "source_snapshot_sha256": value.source_snapshot_sha256,
                }
                if any(existing[key] != item for key, item in expected.items()):
                    raise ConflictError(
                        "reviewed recognition binding collides or projection drifted"
                    )
                binding_id = existing["binding_id"]
        return ReviewedImportReceipt(record_id=binding_id, state=value.status)

    @staticmethod
    async def _cancel_ready_binding_proposals_for_person(
        connection,
        *,
        person_id: uuid.UUID,
        now: datetime,
    ) -> None:
        """Close unsafe review work through the denial-only privacy kernel."""

        receipt = (
            await connection.execute(
                select(
                    func.privacy.cancel_principal_binding_work_for_person(
                        person_id, now
                    )
                )
            )
        ).scalar_one()
        if (
            not isinstance(receipt, dict)
            or set(receipt)
            != {"proposals_cancelled", "requests_cancelled"}
            or not all(
                isinstance(receipt[key], int) and receipt[key] >= 0
                for key in receipt
            )
            or receipt["proposals_cancelled"] != receipt["requests_cancelled"]
        ):
            raise RuntimeError("principal binding cancellation receipt is invalid")

    async def import_reviewed_privacy_directive(
        self,
        person_id: uuid.UUID,
        value: ReviewedPrivacyDirectiveImport,
    ) -> ReviewedImportReceipt:
        self._require_rollout("shadow", "privacy_directive_cutover")
        if (value.directive == "auto_expire") != (value.expires_at is not None):
            raise ConflictError(
                "auto_expire requires an expiry and other directives prohibit one"
            )
        directive_id = uuid7()
        now = datetime.now(UTC)
        blocks_ready_binding = value.directive in {
            "do_not_track",
            "ignored",
            "silent",
            "auto_expire",
        }
        async with self.database.transaction(serializable=True) as connection:
            if blocks_ready_binding:
                await self._cancel_ready_binding_proposals_for_person(
                    connection,
                    person_id=person_id,
                    now=now,
                )
            if (
                await connection.execute(
                    select(schema.people.c.person_id)
                    .where(
                        schema.people.c.person_id == person_id,
                        schema.people.c.status != "erased",
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none() is None:
                raise NotFoundError("reviewed person does not exist")
            inserted = (
                await connection.execute(
                    insert(schema.privacy_directives)
                    .values(
                        directive_id=directive_id,
                        person_id=person_id,
                        directive=value.directive,
                        enabled=True,
                        expires_at=value.expires_at,
                        source_ref=value.source_ref,
                        source_version=value.source_version,
                        source_snapshot_sha256=value.source_snapshot_sha256,
                    )
                    .on_conflict_do_nothing(constraint="person_directive")
                    .returning(schema.privacy_directives.c.directive_id)
                )
            ).scalar_one_or_none()
            if inserted is None:
                existing = (
                    (
                        await connection.execute(
                            select(schema.privacy_directives)
                            .where(
                                schema.privacy_directives.c.person_id == person_id,
                                schema.privacy_directives.c.directive
                                == value.directive,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
                expected = {
                    "enabled": True,
                    "expires_at": value.expires_at,
                    "source_ref": value.source_ref,
                    "source_version": value.source_version,
                    "source_snapshot_sha256": value.source_snapshot_sha256,
                }
                if any(existing[key] != item for key, item in expected.items()):
                    raise ConflictError("privacy directive import projection drifted")
                directive_id = existing["directive_id"]
            if value.directive == "auto_expire":
                await self._ensure_auto_expiry_schedule(
                    connection,
                    person_id=person_id,
                    directive_id=directive_id,
                    due_at=value.expires_at,
                )
            if value.directive in {"do_not_track", "ignored"}:
                bindings = (
                    (
                        await connection.execute(
                            select(
                                schema.source_entity_bindings.c.binding_id,
                                schema.source_entity_bindings.c.source_system,
                                schema.source_entity_bindings.c.entity_id,
                            ).where(
                                schema.source_entity_bindings.c.person_id == person_id
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                for binding in bindings:
                    await connection.execute(
                        insert(schema.edge_privacy_blocks)
                        .values(
                            block_id=binding["binding_id"],
                            source_system=binding["source_system"],
                            entity_id=binding["entity_id"],
                            reason_code=value.directive,
                            person_id=person_id,
                        )
                        .on_conflict_do_update(
                            constraint="edge_privacy_block_entity",
                            set_={
                                "reason_code": value.directive,
                                "person_id": person_id,
                            },
                        )
                    )
                user_bindings = (
                    (
                        await connection.execute(
                            select(
                                schema.ha_user_bindings.c.binding_id,
                                schema.ha_user_bindings.c.ha_user_id,
                            ).where(schema.ha_user_bindings.c.person_id == person_id)
                        )
                    )
                    .mappings()
                    .all()
                )
                for binding in user_bindings:
                    await connection.execute(
                        insert(schema.edge_privacy_user_blocks)
                        .values(
                            block_id=binding["binding_id"],
                            ha_user_id=binding["ha_user_id"],
                            reason_code=value.directive,
                            person_id=person_id,
                        )
                        .on_conflict_do_update(
                            index_elements=["ha_user_id"],
                            set_={
                                "reason_code": value.directive,
                                "person_id": person_id,
                            },
                        )
                    )
                await connection.execute(
                    update(schema.source_entity_bindings)
                    .where(
                        schema.source_entity_bindings.c.person_id == person_id,
                        schema.source_entity_bindings.c.revoked_at.is_(None),
                    )
                    .values(revoked_at=now)
                )
        return ReviewedImportReceipt(
            record_id=directive_id,
            state="scheduled" if value.directive == "auto_expire" else "active",
        )

    async def import_reviewed_person_status(
        self, person_id: uuid.UUID, value: ReviewedPersonStatusImport
    ) -> ReviewedImportReceipt:
        self._require_rollout("shadow", "semantic_people_write")
        now = datetime.now(UTC)
        async with self.database.transaction(serializable=True) as connection:
            if value.status != "active":
                await self._cancel_ready_binding_proposals_for_person(
                    connection,
                    person_id=person_id,
                    now=now,
                )
            row = (
                (
                    await connection.execute(
                        select(schema.people)
                        .where(schema.people.c.person_id == person_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFoundError("reviewed person does not exist")
            existing_source = row["status_source_sha256"]
            if existing_source is not None:
                if any(
                    (
                        row["status"] != value.status,
                        row["status_source_ref"] != value.source_ref,
                        row["status_source_version"] != value.source_version,
                        existing_source != value.source_snapshot_sha256,
                    )
                ):
                    raise ConflictError("person status import projection drifted")
            else:
                await connection.execute(
                    update(schema.people)
                    .where(schema.people.c.person_id == person_id)
                    .values(
                        status=value.status,
                        status_source_ref=value.source_ref,
                        status_source_version=value.source_version,
                        status_source_sha256=value.source_snapshot_sha256,
                        updated_at=now,
                    )
                )
        return ReviewedImportReceipt(record_id=person_id, state="archived")

    async def _expire_principal_binding_work(
        self,
        connection,
        *,
        now: datetime,
        ha_user_id: str | None = None,
    ) -> None:
        proposal_where = [
            schema.principal_binding_proposals.c.state == "ready",
            schema.principal_binding_proposals.c.expires_at <= now,
        ]
        if ha_user_id is not None:
            proposal_where.append(
                schema.principal_binding_proposals.c.ha_user_id == ha_user_id
            )
        expired_request_ids = list(
            (
                await connection.execute(
                    update(schema.principal_binding_proposals)
                    .where(*proposal_where)
                    .values(state="expired")
                    .returning(schema.principal_binding_proposals.c.request_id)
                )
            ).scalars()
        )
        request_where = [
            schema.principal_binding_requests.c.state.in_(("pending", "staged"))
        ]
        if ha_user_id is not None:
            request_where.append(
                schema.principal_binding_requests.c.ha_user_id == ha_user_id
            )
        expiry_reason = schema.principal_binding_requests.c.expires_at <= now
        if expired_request_ids:
            expiry_reason = or_(
                expiry_reason,
                schema.principal_binding_requests.c.request_id.in_(
                    expired_request_ids
                ),
            )
        await connection.execute(
            update(schema.principal_binding_requests)
            .where(*request_where, expiry_reason)
            .values(state="expired", closed_at=now)
        )

    async def _binding_status_in_transaction(
        self,
        connection,
        ha_user_id: str,
    ) -> Literal["bound", "missing", "unavailable"]:
        privacy_blocked = (
            await connection.execute(
                select(schema.edge_privacy_user_blocks.c.block_id).where(
                    schema.edge_privacy_user_blocks.c.ha_user_id == ha_user_id
                )
            )
        ).scalar_one_or_none()
        if privacy_blocked is not None:
            return "unavailable"
        active = (
            (
                await connection.execute(
                    select(
                        schema.ha_user_bindings.c.principal_id.label(
                            "binding_principal_id"
                        ),
                        schema.ha_user_bindings.c.person_id.label(
                            "binding_person_id"
                        ),
                        schema.ha_user_bindings.c.confirmed_by_principal_id,
                        schema.ha_user_bindings.c.source_artifact_id,
                        schema.ha_user_bindings.c.proposal_id,
                        schema.principals.c.principal_id.label("principal_id"),
                        schema.principals.c.person_id.label("principal_person_id"),
                        schema.principals.c.kind.label("principal_kind"),
                        schema.principals.c.status.label("principal_status"),
                        schema.people.c.person_id.label("person_id"),
                        schema.people.c.status.label("person_status"),
                        schema.principal_binding_proposals.c.state.label(
                            "proposal_state"
                        ),
                        schema.principal_binding_proposals.c.result_principal_id,
                        schema.principal_binding_proposals.c.confirmation_artifact_id,
                    )
                    .select_from(
                        schema.ha_user_bindings.outerjoin(
                            schema.principals,
                            schema.ha_user_bindings.c.principal_id
                            == schema.principals.c.principal_id,
                        )
                        .outerjoin(
                            schema.people,
                            schema.ha_user_bindings.c.person_id
                            == schema.people.c.person_id,
                        )
                        .outerjoin(
                            schema.principal_binding_proposals,
                            schema.ha_user_bindings.c.proposal_id
                            == schema.principal_binding_proposals.c.proposal_id,
                        )
                    )
                    .where(
                        schema.ha_user_bindings.c.ha_user_id == ha_user_id,
                        schema.ha_user_bindings.c.revoked_at.is_(None),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if active is None:
            historical = (
                await connection.execute(
                    select(schema.ha_user_bindings.c.binding_id)
                    .where(schema.ha_user_bindings.c.ha_user_id == ha_user_id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            return "unavailable" if historical is not None else "missing"
        principal_id = active["binding_principal_id"]
        person_id = active["binding_person_id"]
        available = all(
            (
                active["source_artifact_id"] is not None,
                active["proposal_id"] is not None,
                active["principal_id"] == principal_id,
                active["principal_person_id"] == person_id,
                active["confirmed_by_principal_id"] == principal_id,
                active["principal_kind"] == "ha_user",
                active["principal_status"] == "active",
                active["person_id"] == person_id,
                active["person_status"] == "active",
                active["proposal_state"] == "consumed",
                active["result_principal_id"] == principal_id,
                active["confirmation_artifact_id"]
                == active["source_artifact_id"],
            )
        )
        if not available:
            return "unavailable"
        directives = await self._binding_blocking_directives(connection, person_id)
        if directives & {"auto_expire", "do_not_track", "ignored", "silent"}:
            return "unavailable"
        return "bound"

    async def _principal_binding_proposal_view(
        self,
        connection,
        *,
        ha_user_id: str,
        now: datetime,
    ) -> PrincipalBindingProposalView:
        binding_status = await self._binding_status_in_transaction(
            connection, ha_user_id
        )
        if binding_status == "bound":
            return PrincipalBindingProposalView(state="bound")
        if binding_status == "unavailable":
            return PrincipalBindingProposalView(state="unavailable")
        await self._expire_principal_binding_work(
            connection, now=now, ha_user_id=ha_user_id
        )
        row = (
            (
                await connection.execute(
                    select(
                        schema.principal_binding_requests.c.request_id,
                        schema.principal_binding_requests.c.state.label(
                            "request_state"
                        ),
                        schema.principal_binding_requests.c.review_code,
                        schema.principal_binding_proposals.c.state.label(
                            "proposal_state"
                        ),
                        schema.principal_binding_proposals.c.proposal_digest,
                        schema.principal_binding_proposals.c.expires_at,
                        schema.principal_binding_proposals.c.ha_user_id.label(
                            "proposal_ha_user_id"
                        ),
                        schema.principal_binding_proposals.c.person_id,
                        schema.principal_binding_proposals.c.operator_request_id,
                        schema.principal_binding_proposals.c.reviewed_display_label,
                        schema.principal_binding_proposals.c.person_snapshot_digest,
                        schema.principal_binding_proposals.c.stage_receipt_digest,
                        schema.principal_binding_proposals.c.staged_at,
                        schema.people.c.display_name.label("current_display_name"),
                        schema.people.c.status.label("person_status"),
                        schema.people.c.privacy_scope,
                        schema.people.c.legacy_source_sha256,
                        schema.people.c.status_source_sha256,
                    )
                    .select_from(
                        schema.principal_binding_requests.outerjoin(
                            schema.principal_binding_proposals,
                            schema.principal_binding_requests.c.request_id
                            == schema.principal_binding_proposals.c.request_id,
                        ).outerjoin(
                            schema.people,
                            schema.principal_binding_proposals.c.person_id
                            == schema.people.c.person_id,
                        )
                    )
                    .where(
                        schema.principal_binding_requests.c.ha_user_id == ha_user_id,
                        schema.principal_binding_requests.c.state.in_(
                            ("pending", "staged")
                        ),
                    )
                    .order_by(
                        schema.principal_binding_requests.c.requested_at.desc()
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return PrincipalBindingProposalView(state="not_requested")
        if row["request_state"] == "pending":
            return PrincipalBindingProposalView(
                state="awaiting_operator_review",
                review_code=row["review_code"],
            )
        if any(
            (
                row["proposal_state"] != "ready",
                row["person_status"] != "active",
                row["proposal_digest"] is None,
                row["expires_at"] is None,
                row["reviewed_display_label"] is None,
                row["person_snapshot_digest"] is None,
                row["proposal_ha_user_id"] is None,
                row["operator_request_id"] is None,
                row["stage_receipt_digest"] is None,
                row["staged_at"] is None,
            )
        ):
            return PrincipalBindingProposalView(state="unavailable")
        expected_proposal_digest = binding_proposal_digest(
            request_id=row["request_id"],
            review_code=row["review_code"],
            ha_user_id=row["proposal_ha_user_id"],
            person_id=row["person_id"],
            reviewed_display_label=row["reviewed_display_label"],
            person_snapshot_digest=row["person_snapshot_digest"],
            operator_request_id=row["operator_request_id"],
            stage_receipt_digest=row["stage_receipt_digest"],
            staged_at=row["staged_at"],
            expires_at=row["expires_at"],
            policy_version=self.settings.policy_version,
            policy_digest=self.settings.policy_digest,
        )
        if not hmac.compare_digest(
            expected_proposal_digest, row["proposal_digest"]
        ):
            return PrincipalBindingProposalView(state="unavailable")
        current_person_snapshot = binding_person_snapshot_digest(
            {
                "person_id": row["person_id"],
                "display_name": row["current_display_name"],
                "status": row["person_status"],
                "privacy_scope": row["privacy_scope"],
                "legacy_source_sha256": row["legacy_source_sha256"],
                "status_source_sha256": row["status_source_sha256"],
            }
        )
        if not hmac.compare_digest(
            current_person_snapshot, row["person_snapshot_digest"]
        ):
            return PrincipalBindingProposalView(state="unavailable")
        directives = await self._binding_blocking_directives(
            connection, row["person_id"]
        )
        if directives & {"auto_expire", "do_not_track", "ignored", "silent"}:
            return PrincipalBindingProposalView(state="unavailable")
        label = row["reviewed_display_label"]
        return PrincipalBindingProposalView(
            state="ready_for_confirmation",
            reviewed_display_label=label,
            confirmation_statement=(
                "Bind this authenticated Home Assistant account to " f"{label}."
            ),
            proposal_digest=row["proposal_digest"],
            expires_at=row["expires_at"],
        )

    async def principal_binding_proposal_status(
        self, ha_user_id: str
    ) -> PrincipalBindingProposalView:
        self._require_rollout("shadow", "principal_binding_request")
        async with self.database.transaction(ha_user_id=ha_user_id) as connection:
            return await self._principal_binding_proposal_view(
                connection, ha_user_id=ha_user_id, now=datetime.now(UTC)
            )

    async def request_principal_binding(
        self, ha_user_id: str
    ) -> PrincipalBindingProposalView:
        self._require_rollout("shadow", "principal_binding_request")
        now = datetime.now(UTC)
        for attempt in range(3):
            review_code = "".join(
                secrets.choice(BINDING_REVIEW_CODE_ALPHABET) for _ in range(16)
            )
            try:
                async with self.database.transaction(
                    ha_user_id=ha_user_id, serializable=True
                ) as connection:
                    current = await self._principal_binding_proposal_view(
                        connection, ha_user_id=ha_user_id, now=now
                    )
                    if current.state != "not_requested":
                        return current
                    await connection.execute(
                        insert(schema.principal_binding_requests).values(
                            request_id=uuid7(),
                            ha_user_id=ha_user_id,
                            review_code=review_code,
                            state="pending",
                            requested_at=now,
                            expires_at=now + BINDING_REQUEST_TTL,
                        )
                    )
                return PrincipalBindingProposalView(
                    state="awaiting_operator_review",
                    review_code=review_code,
                )
            except IntegrityError as exc:
                current = await self.principal_binding_proposal_status(ha_user_id)
                if current.state != "not_requested":
                    return current
                if attempt == 2:
                    raise ConflictError(
                        "could not allocate an opaque binding review code"
                    ) from exc
        raise RuntimeError("unreachable binding review-code retry state")

    async def cancel_principal_binding_request(
        self, ha_user_id: str
    ) -> PrincipalBindingProposalView:
        self._require_rollout("shadow", "principal_binding_request")
        now = datetime.now(UTC)
        async with self.database.transaction(
            ha_user_id=ha_user_id, serializable=True
        ) as connection:
            await self._expire_principal_binding_work(
                connection, now=now, ha_user_id=ha_user_id
            )
            request_id = (
                await connection.execute(
                    select(schema.principal_binding_requests.c.request_id)
                    .where(
                        schema.principal_binding_requests.c.ha_user_id == ha_user_id,
                        schema.principal_binding_requests.c.state.in_(
                            ("pending", "staged")
                        ),
                    )
                )
            ).scalar_one_or_none()
            if request_id is None:
                return PrincipalBindingProposalView(state="not_requested")
            # Match the proposal -> request lock order used by confirmation,
            # expiry, and privacy/person-state cancellation.
            list(
                (
                    await connection.execute(
                        select(schema.principal_binding_proposals.c.proposal_id)
                        .where(
                            schema.principal_binding_proposals.c.request_id
                            == request_id,
                            schema.principal_binding_proposals.c.state == "ready",
                        )
                        .order_by(schema.principal_binding_proposals.c.proposal_id)
                        .with_for_update()
                    )
                ).scalars()
            )
            await connection.execute(
                update(schema.principal_binding_proposals)
                .where(
                    schema.principal_binding_proposals.c.request_id == request_id,
                    schema.principal_binding_proposals.c.state == "ready",
                )
                .values(state="cancelled")
            )
            locked_request_id = (
                await connection.execute(
                    select(schema.principal_binding_requests.c.request_id)
                    .where(
                        schema.principal_binding_requests.c.request_id == request_id,
                        schema.principal_binding_requests.c.state.in_(
                            ("pending", "staged")
                        ),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if locked_request_id is None:
                return PrincipalBindingProposalView(state="not_requested")
            await connection.execute(
                update(schema.principal_binding_requests)
                .where(
                    schema.principal_binding_requests.c.request_id
                    == locked_request_id
                )
                .values(state="cancelled", closed_at=now)
            )
        return PrincipalBindingProposalView(state="not_requested")

    async def list_pending_principal_binding_requests(
        self,
    ) -> OperatorPrincipalBindingRequestsView:
        self._require_rollout("shadow", "principal_binding_operator_review")
        now = datetime.now(UTC)
        async with self.database.transaction(binding_operator=True) as connection:
            await self._expire_principal_binding_work(connection, now=now)
            rows = (
                (
                    await connection.execute(
                        select(
                            schema.principal_binding_requests.c.request_id,
                            schema.principal_binding_requests.c.review_code,
                            schema.principal_binding_requests.c.state,
                            schema.principal_binding_requests.c.requested_at,
                            schema.principal_binding_requests.c.expires_at,
                        )
                        .where(
                            schema.principal_binding_requests.c.state == "pending"
                        )
                        .order_by(
                            schema.principal_binding_requests.c.requested_at
                        )
                        .limit(100)
                    )
                )
                .mappings()
                .all()
            )
        return OperatorPrincipalBindingRequestsView(
            requests=[OperatorPrincipalBindingRequestView(**row) for row in rows]
        )

    async def stage_principal_binding_proposal(
        self, value: OperatorPrincipalBindingProposalStage
    ) -> OperatorPrincipalBindingProposalView:
        self._require_rollout("shadow", "principal_binding_operator_review")
        now = datetime.now(UTC)
        proposal_id = uuid7()
        try:
            async with self.database.transaction(
                binding_operator=True, serializable=True
            ) as connection:
                prior = (
                    (
                        await connection.execute(
                            select(
                                schema.principal_binding_proposals,
                                schema.principal_binding_requests.c.review_code.label(
                                    "request_review_code"
                                ),
                            )
                            .join(
                                schema.principal_binding_requests,
                                schema.principal_binding_proposals.c.request_id
                                == schema.principal_binding_requests.c.request_id,
                            )
                            .where(
                                schema.principal_binding_proposals.c.operator_request_id
                                == value.operator_request_id
                            )
                            .with_for_update(
                                of=schema.principal_binding_proposals
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if prior is not None:
                    expected_digest = binding_proposal_digest(
                        request_id=prior["request_id"],
                        review_code=prior["request_review_code"],
                        ha_user_id=prior["ha_user_id"],
                        person_id=prior["person_id"],
                        reviewed_display_label=prior["reviewed_display_label"],
                        person_snapshot_digest=prior["person_snapshot_digest"],
                        operator_request_id=prior["operator_request_id"],
                        stage_receipt_digest=prior["stage_receipt_digest"],
                        staged_at=prior["staged_at"],
                        expires_at=prior["expires_at"],
                        policy_version=self.settings.policy_version,
                        policy_digest=self.settings.policy_digest,
                    )
                    if any(
                        (
                            prior["request_id"] != value.request_id,
                            prior["request_review_code"] != value.review_code,
                            prior["person_id"] != value.person_id,
                            prior["state"] != "ready",
                            not hmac.compare_digest(
                                prior["proposal_digest"], expected_digest
                            ),
                        )
                    ):
                        raise ConflictError("operator request was already consumed")
                    return OperatorPrincipalBindingProposalView(
                        proposal_id=prior["proposal_id"],
                        request_id=prior["request_id"],
                        person_id=prior["person_id"],
                        reviewed_display_label=prior["reviewed_display_label"],
                        state="ready",
                        stage_receipt_digest=prior["stage_receipt_digest"],
                        staged_at=prior["staged_at"],
                        expires_at=prior["expires_at"],
                    )
                await self._expire_principal_binding_work(connection, now=now)
                request = (
                    (
                        await connection.execute(
                            select(schema.principal_binding_requests)
                            .where(
                                schema.principal_binding_requests.c.request_id
                                == value.request_id,
                                schema.principal_binding_requests.c.review_code
                                == value.review_code,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if request is None:
                    raise NotFoundError("pending binding request does not exist")
                if request["state"] != "pending" or request["expires_at"] <= now:
                    raise ConflictError("binding request is not pending")
                if (
                    await self._binding_status_in_transaction(
                        connection, request["ha_user_id"]
                    )
                ) != "missing":
                    raise ForbiddenError("HA user is unavailable for binding")
                person = (
                    (
                        await connection.execute(
                            select(schema.people)
                            .where(schema.people.c.person_id == value.person_id)
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if person is None or person["status"] != "active":
                    raise ForbiddenError("reviewed person is unavailable for binding")
                directives = await self._binding_blocking_directives(
                    connection, value.person_id
                )
                if directives & {
                    "auto_expire",
                    "do_not_track",
                    "ignored",
                    "silent",
                }:
                    raise ForbiddenError("reviewed person is unavailable for binding")
                bound_person = (
                    await connection.execute(
                        select(schema.ha_user_bindings.c.binding_id).where(
                            schema.ha_user_bindings.c.person_id == value.person_id,
                            schema.ha_user_bindings.c.revoked_at.is_(None),
                        )
                    )
                ).scalar_one_or_none()
                if bound_person is not None:
                    raise ConflictError("reviewed person is already bound")
                person_snapshot_digest = binding_person_snapshot_digest(person)
                reviewed_display_label = person["display_name"]
                expires_at = min(
                    request["expires_at"], now + BINDING_PROPOSAL_TTL
                )
                stage_receipt_digest = sha256_json(
                    {
                        "contract": "principal-binding-stage-v1",
                        "request_id": str(value.request_id),
                        "review_code": value.review_code,
                        "ha_user_id": request["ha_user_id"],
                        "person_id": str(value.person_id),
                        "reviewed_display_label": reviewed_display_label,
                        "person_snapshot_digest": person_snapshot_digest,
                        "operator_request_id": str(value.operator_request_id),
                        "policy_version": self.settings.policy_version,
                        "policy_digest": self.settings.policy_digest,
                    }
                )
                proposal_digest = binding_proposal_digest(
                    request_id=value.request_id,
                    review_code=value.review_code,
                    ha_user_id=request["ha_user_id"],
                    person_id=value.person_id,
                    reviewed_display_label=reviewed_display_label,
                    person_snapshot_digest=person_snapshot_digest,
                    operator_request_id=value.operator_request_id,
                    stage_receipt_digest=stage_receipt_digest,
                    staged_at=now,
                    expires_at=expires_at,
                    policy_version=self.settings.policy_version,
                    policy_digest=self.settings.policy_digest,
                )
                await connection.execute(
                    insert(schema.principal_binding_proposals).values(
                        proposal_id=proposal_id,
                        request_id=value.request_id,
                        ha_user_id=request["ha_user_id"],
                        person_id=value.person_id,
                        operator_request_id=value.operator_request_id,
                        reviewed_display_label=reviewed_display_label,
                        person_snapshot_digest=person_snapshot_digest,
                        proposal_digest=proposal_digest,
                        state="ready",
                        stage_receipt_digest=stage_receipt_digest,
                        staged_at=now,
                        expires_at=expires_at,
                    )
                )
                await connection.execute(
                    update(schema.principal_binding_requests)
                    .where(
                        schema.principal_binding_requests.c.request_id
                        == value.request_id,
                        schema.principal_binding_requests.c.state == "pending",
                    )
                    .values(
                        state="staged",
                        staged_at=now,
                        expires_at=expires_at,
                    )
                )
            return OperatorPrincipalBindingProposalView(
                proposal_id=proposal_id,
                request_id=value.request_id,
                person_id=value.person_id,
                reviewed_display_label=reviewed_display_label,
                state="ready",
                stage_receipt_digest=stage_receipt_digest,
                staged_at=now,
                expires_at=expires_at,
            )
        except IntegrityError as exc:
            raise ConflictError("binding proposal conflicts with active state") from exc

    async def confirm_principal_binding_proposal(
        self,
        ha_user_id: str,
        value: PrincipalBindingConfirmation,
    ) -> PrincipalBindingConfirmationView:
        self._require_rollout("shadow", "principal_binding_confirmation")
        now = datetime.now(UTC)
        principal_id = uuid7()
        expired = False
        result: PrincipalBindingConfirmationView | None = None
        try:
            async with self.database.transaction(
                principal_id=principal_id,
                ha_user_id=ha_user_id,
                serializable=True,
            ) as connection:
                proposal_row = (
                    (
                        await connection.execute(
                            select(schema.principal_binding_proposals)
                            .where(
                                schema.principal_binding_proposals.c.ha_user_id
                                == ha_user_id,
                                schema.principal_binding_proposals.c.proposal_digest
                                == value.proposal_digest,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if proposal_row is None:
                    raise NotFoundError("binding proposal does not exist")
                if not hmac.compare_digest(
                    proposal_row["proposal_digest"], value.proposal_digest
                ):
                    raise NotFoundError("binding proposal does not exist")
                request = (
                    (
                        await connection.execute(
                            select(schema.principal_binding_requests)
                            .where(
                                schema.principal_binding_requests.c.request_id
                                == proposal_row["request_id"],
                                schema.principal_binding_requests.c.ha_user_id
                                == ha_user_id,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                person = (
                    (
                        await connection.execute(
                            select(schema.people)
                            .where(
                                schema.people.c.person_id
                                == proposal_row["person_id"]
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if request is None or person is None:
                    raise ConflictError("binding proposal graph is malformed")
                proposal = {
                    **proposal_row,
                    "request_state": request["state"],
                    "request_review_code": request["review_code"],
                    "current_display_name": person["display_name"],
                    "person_status": person["status"],
                    "privacy_scope": person["privacy_scope"],
                    "legacy_source_sha256": person["legacy_source_sha256"],
                    "status_source_sha256": person["status_source_sha256"],
                }
                expected_proposal_digest = binding_proposal_digest(
                    request_id=proposal["request_id"],
                    review_code=proposal["request_review_code"],
                    ha_user_id=proposal["ha_user_id"],
                    person_id=proposal["person_id"],
                    reviewed_display_label=proposal["reviewed_display_label"],
                    person_snapshot_digest=proposal["person_snapshot_digest"],
                    operator_request_id=proposal["operator_request_id"],
                    stage_receipt_digest=proposal["stage_receipt_digest"],
                    staged_at=proposal["staged_at"],
                    expires_at=proposal["expires_at"],
                    policy_version=self.settings.policy_version,
                    policy_digest=self.settings.policy_digest,
                )
                if not hmac.compare_digest(
                    proposal["proposal_digest"], expected_proposal_digest
                ):
                    raise ConflictError("binding proposal integrity check failed")
                if proposal["state"] == "consumed":
                    if proposal["consumed_at"] is None:
                        raise ConflictError("consumed binding proposal is malformed")
                    if (
                        await self._binding_status_in_transaction(
                            connection, ha_user_id
                        )
                    ) != "bound":
                        raise ConflictError(
                            "consumed binding proposal has no available binding"
                        )
                    return PrincipalBindingConfirmationView(
                        confirmed_at=proposal["consumed_at"]
                    )
                if (
                    proposal["state"] != "ready"
                    or proposal["request_state"] != "staged"
                ):
                    raise ConflictError("binding proposal is not confirmable")
                if proposal["expires_at"] <= now:
                    await connection.execute(
                        update(schema.principal_binding_proposals)
                        .where(
                            schema.principal_binding_proposals.c.proposal_id
                            == proposal["proposal_id"]
                        )
                        .values(state="expired")
                    )
                    await connection.execute(
                        update(schema.principal_binding_requests)
                        .where(
                            schema.principal_binding_requests.c.request_id
                            == proposal["request_id"]
                        )
                        .values(state="expired", closed_at=now)
                    )
                    expired = True
                else:
                    if proposal["person_status"] != "active":
                        raise ForbiddenError("reviewed person is unavailable")
                    current_person_snapshot = binding_person_snapshot_digest(
                        {
                            "person_id": proposal["person_id"],
                            "display_name": proposal["current_display_name"],
                            "status": proposal["person_status"],
                            "privacy_scope": proposal["privacy_scope"],
                            "legacy_source_sha256": proposal[
                                "legacy_source_sha256"
                            ],
                            "status_source_sha256": proposal[
                                "status_source_sha256"
                            ],
                        }
                    )
                    if not hmac.compare_digest(
                        current_person_snapshot,
                        proposal["person_snapshot_digest"],
                    ):
                        raise ConflictError(
                            "reviewed person changed after operator staging"
                        )
                    blocked = (
                        await connection.execute(
                            select(schema.edge_privacy_user_blocks.c.block_id).where(
                                schema.edge_privacy_user_blocks.c.ha_user_id
                                == ha_user_id
                            )
                        )
                    ).scalar_one_or_none()
                    directives = await self._binding_blocking_directives(
                        connection, proposal["person_id"]
                    )
                    if blocked is not None or directives & {
                        "auto_expire",
                        "do_not_track",
                        "ignored",
                        "silent",
                    }:
                        raise ForbiddenError("binding is prohibited by privacy policy")
                    existing = (
                        await connection.execute(
                            select(schema.ha_user_bindings.c.binding_id)
                            .where(
                                schema.ha_user_bindings.c.revoked_at.is_(None),
                                or_(
                                    schema.ha_user_bindings.c.ha_user_id
                                    == ha_user_id,
                                    schema.ha_user_bindings.c.person_id
                                    == proposal["person_id"],
                                ),
                            )
                            .limit(1)
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        raise ConflictError("HA user or person is already bound")
                    await connection.execute(
                        insert(schema.principals).values(
                            principal_id=principal_id,
                            person_id=proposal["person_id"],
                            kind="ha_user",
                            display_label=proposal["reviewed_display_label"],
                            status="active",
                        )
                    )
                    confirmation_artifact_id = (
                        await self._mint_authenticated_confirmation(
                            connection,
                            principal_id=principal_id,
                            purpose="ha_user_person_binding.confirm",
                            proposal_digest=proposal["proposal_digest"],
                            client_nonce=value.confirmation_nonce,
                            operation_time=now,
                        )
                    )
                    binding_id = uuid7()
                    await connection.execute(
                        insert(schema.ha_user_bindings).values(
                            binding_id=binding_id,
                            ha_user_id=ha_user_id,
                            principal_id=principal_id,
                            person_id=proposal["person_id"],
                            confirmed_by_principal_id=principal_id,
                            confirmed_at=now,
                            source_artifact_id=confirmation_artifact_id,
                            proposal_id=proposal["proposal_id"],
                        )
                    )
                    await connection.execute(
                        update(schema.principal_binding_proposals)
                        .where(
                            schema.principal_binding_proposals.c.proposal_id
                            == proposal["proposal_id"],
                            schema.principal_binding_proposals.c.state == "ready",
                        )
                        .values(
                            state="consumed",
                            consumed_at=now,
                            result_principal_id=principal_id,
                            confirmation_artifact_id=confirmation_artifact_id,
                        )
                    )
                    await connection.execute(
                        update(schema.principal_binding_requests)
                        .where(
                            schema.principal_binding_requests.c.request_id
                            == proposal["request_id"],
                            schema.principal_binding_requests.c.state == "staged",
                        )
                        .values(state="consumed", closed_at=now)
                    )
                    result = PrincipalBindingConfirmationView(confirmed_at=now)
        except IntegrityError as exc:
            raise ConflictError("binding confirmation conflicts with active state") from exc
        if expired:
            raise ConflictError("binding proposal expired")
        if result is None:
            raise ConflictError("binding confirmation did not commit")
        return result

    async def resolve_principal(self, ha_user_id: str) -> dict[str, Any]:
        async with self.database.transaction(ha_user_id=ha_user_id) as connection:
            if (
                await self._binding_status_in_transaction(connection, ha_user_id)
                != "bound"
            ):
                raise ForbiddenError(
                    "HA user has no available confirmed principal binding"
                )
            row = (
                (
                    await connection.execute(
                        select(
                            schema.principals.c.principal_id,
                            schema.principals.c.person_id,
                            schema.principals.c.status,
                        )
                        .select_from(
                            schema.ha_user_bindings.join(
                                schema.principals,
                                schema.ha_user_bindings.c.principal_id
                                == schema.principals.c.principal_id,
                            ).join(
                                schema.people,
                                schema.ha_user_bindings.c.person_id
                                == schema.people.c.person_id,
                            )
                        )
                        .where(
                            schema.ha_user_bindings.c.ha_user_id == ha_user_id,
                            schema.ha_user_bindings.c.revoked_at.is_(None),
                            schema.principals.c.status == "active",
                            schema.people.c.status == "active",
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ForbiddenError("HA user has no available confirmed principal binding")
        return dict(row)

    async def onboarding_binding_status(
        self, ha_user_id: str
    ) -> Literal["bound", "missing", "unavailable"]:
        """Classify a binding without returning identity content.

        A historical, revoked, malformed, privacy-blocked, or otherwise
        unavailable binding is intentionally distinct from a user who has
        never completed binding. That distinction keeps onboarding fail-closed
        without exposing the bound person or principal.
        """

        async with self.database.transaction(ha_user_id=ha_user_id) as connection:
            return await self._binding_status_in_transaction(connection, ha_user_id)

    async def bind_source_entity(
        self,
        principal: dict[str, Any],
        value: SourceEntityBindingCreate,
    ) -> uuid.UUID:
        self._require_rollout("shadow", "source_entity_binding")
        binding_id = uuid7()
        async with self.database.transaction(
            principal_id=principal["principal_id"], serializable=True
        ) as connection:
            if await self._active_directives(connection, principal["person_id"]) & {
                "do_not_track",
                "ignored",
                "auto_expire",
            }:
                raise ForbiddenError("privacy directive prohibits source binding")
            blocked = (
                await connection.execute(
                    select(schema.edge_privacy_blocks.c.block_id).where(
                        schema.edge_privacy_blocks.c.source_system
                        == value.source_system,
                        schema.edge_privacy_blocks.c.entity_id == value.entity_id,
                    )
                )
            ).scalar_one_or_none()
            if blocked is not None:
                raise ForbiddenError("source entity is blocked at the HA Edge")
            existing = (
                (
                    await connection.execute(
                        select(
                            schema.source_entity_bindings.c.binding_id,
                            schema.source_entity_bindings.c.principal_id,
                        ).where(
                            schema.source_entity_bindings.c.source_system
                            == value.source_system,
                            schema.source_entity_bindings.c.entity_id
                            == value.entity_id,
                            schema.source_entity_bindings.c.revoked_at.is_(None),
                        )
                    )
                )
                .mappings()
                .first()
            )
            if existing and existing["principal_id"] != principal["principal_id"]:
                raise ConflictError(
                    "source entity is already bound to another principal"
                )
            if existing:
                return existing["binding_id"]
            confirmation_artifact_id = await self._mint_authenticated_confirmation(
                connection,
                principal_id=principal["principal_id"],
                purpose="source_entity_binding.confirm",
                proposal_digest=sha256_json(
                    {
                        "source_system": value.source_system,
                        "entity_id": value.entity_id,
                        "person_id": str(principal["person_id"]),
                    }
                ),
                client_nonce=value.confirmation_artifact_id,
            )
            await connection.execute(
                insert(schema.source_entity_bindings).values(
                    binding_id=binding_id,
                    source_system=value.source_system,
                    entity_id=value.entity_id,
                    principal_id=principal["principal_id"],
                    person_id=principal["person_id"],
                    confirmed_by_principal_id=principal["principal_id"],
                    confirmation_artifact_id=confirmation_artifact_id,
                )
            )
        return binding_id

    async def import_legacy_role(self, value: LegacyRoleImport) -> uuid.UUID:
        self._require_rollout("shadow", "legacy_role_import")
        label_id = uuid7()
        async with self.database.transaction(serializable=True) as connection:
            inserted = (
                await connection.execute(
                    insert(schema.legacy_role_labels)
                    .values(
                        label_id=label_id,
                        person_id=value.person_id,
                        role_label=value.role_label,
                        perspective="unknown",
                        source_ref=value.source_ref,
                        source_version=value.source_version,
                        source_snapshot_sha256=value.source_snapshot_sha256,
                    )
                    .on_conflict_do_nothing(constraint="legacy_person_role")
                    .returning(schema.legacy_role_labels.c.label_id)
                )
            ).scalar_one_or_none()
            if inserted is not None:
                return inserted
            existing = (
                (
                    await connection.execute(
                        select(schema.legacy_role_labels)
                        .where(
                            schema.legacy_role_labels.c.person_id == value.person_id,
                            schema.legacy_role_labels.c.source_ref == value.source_ref,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if (
                existing["role_label"] != value.role_label
                or existing["perspective"] != "unknown"
                or existing["source_version"] != value.source_version
                or existing["source_snapshot_sha256"] != value.source_snapshot_sha256
            ):
                raise ConflictError("legacy role import projection drifted")
            return existing["label_id"]

    async def import_legacy_relationship_candidate(
        self, value: LegacyRelationshipCandidateImport
    ) -> uuid.UUID:
        self._require_rollout("shadow", "legacy_relationship_candidate_import")
        if value.from_person_id == value.to_person_id:
            raise ConflictError("legacy relationship candidate cannot be a self edge")
        candidate_id = uuid7()
        async with self.database.transaction(serializable=True) as connection:
            people_count = int(
                (
                    await connection.execute(
                        select(func.count())
                        .select_from(schema.people)
                        .where(
                            schema.people.c.person_id.in_(
                                [value.from_person_id, value.to_person_id]
                            ),
                            schema.people.c.status != "erased",
                        )
                    )
                ).scalar_one()
            )
            if people_count != 2:
                raise NotFoundError("legacy relationship candidate person is absent")
            inserted = (
                await connection.execute(
                    insert(schema.legacy_relationship_candidates)
                    .values(
                        candidate_id=candidate_id,
                        from_person_id=value.from_person_id,
                        to_person_id=value.to_person_id,
                        relationship_label=value.relationship_label,
                        relationship_status=value.relationship_status,
                        perspective="unknown",
                        authoritative=False,
                        source_ref=value.source_ref,
                        source_snapshot_sha256=value.source_snapshot_sha256,
                    )
                    .on_conflict_do_nothing(index_elements=["source_ref"])
                    .returning(schema.legacy_relationship_candidates.c.candidate_id)
                )
            ).scalar_one_or_none()
            if inserted is not None:
                return inserted
            existing = (
                (
                    await connection.execute(
                        select(schema.legacy_relationship_candidates)
                        .where(
                            schema.legacy_relationship_candidates.c.source_ref
                            == value.source_ref
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            expected = {
                "from_person_id": value.from_person_id,
                "to_person_id": value.to_person_id,
                "relationship_label": value.relationship_label,
                "relationship_status": value.relationship_status,
                "perspective": "unknown",
                "authoritative": False,
                "source_snapshot_sha256": value.source_snapshot_sha256,
            }
            if any(existing[key] != item for key, item in expected.items()):
                raise ConflictError("legacy relationship candidate projection drifted")
            return existing["candidate_id"]

    async def confirm_parent(
        self,
        principal: dict[str, Any],
        value: ParentConfirmation,
    ) -> MemoryTransactionView:
        self._require_rollout("shadow", "parent_confirmation")
        if principal["person_id"] != value.child_person_id:
            raise ForbiddenError(
                "only the child principal may confirm this parent fact"
            )
        transaction_id = uuid7()
        fact_id = uuid7()
        fact_version_id = uuid7()
        now = datetime.now(UTC)
        valid_from = value.valid_from or now
        candidate = {
            "subject_person_id": str(value.parent_person_id),
            "predicate": "parent_of",
            "object_person_id": str(value.child_person_id),
        }
        async with self.database.transaction(
            principal_id=principal["principal_id"], serializable=True
        ) as connection:
            count = int(
                (
                    await connection.execute(
                        select(func.count())
                        .select_from(schema.people)
                        .where(
                            schema.people.c.person_id.in_(
                                [value.parent_person_id, value.child_person_id]
                            ),
                            schema.people.c.status == "active",
                        )
                    )
                ).scalar_one()
            )
            if count != 2:
                raise NotFoundError("parent or child person does not exist")
            existing = (
                await connection.execute(
                    select(schema.fact_versions.c.fact_id).where(
                        schema.fact_versions.c.subject_id == value.parent_person_id,
                        schema.fact_versions.c.predicate == "parent_of",
                        schema.fact_versions.c.object.contains(
                            {"person_id": str(value.child_person_id)}
                        ),
                        func.upper_inf(schema.fact_versions.c.system_range),
                        schema.fact_versions.c.resolution == "accepted",
                    )
                )
            ).scalar_one_or_none()
            if existing:
                raise ConflictError("parent relationship is already confirmed")
            confirmation_artifact_id = await self._mint_authenticated_confirmation(
                connection,
                principal_id=principal["principal_id"],
                purpose="parent_relationship.confirm",
                proposal_digest=sha256_json(candidate),
                client_nonce=value.confirmation_artifact_id,
            )
            legacy_labels = (
                (
                    await connection.execute(
                        select(schema.legacy_role_labels.c.label_id).where(
                            schema.legacy_role_labels.c.person_id
                            == value.parent_person_id,
                            schema.legacy_role_labels.c.role_label == "parent",
                            schema.legacy_role_labels.c.perspective == "unknown",
                        )
                    )
                )
                .scalars()
                .all()
            )
            legacy_reason = (
                "unique_legacy_label_attached"
                if len(legacy_labels) == 1
                else "legacy_label_absent"
                if not legacy_labels
                else "legacy_label_ambiguous_not_attached"
            )
            await connection.execute(
                insert(schema.memory_transactions).values(
                    transaction_id=transaction_id,
                    principal_id=principal["principal_id"],
                    kind="parent_confirmation",
                    state="committed",
                    candidate=candidate,
                    preview={"confirmed": candidate},
                    verifier_results=[
                        {
                            "rule": "subject_authority",
                            "outcome": "pass",
                            "reason_code": "child_confirmed",
                        },
                        {
                            "rule": "legacy_context",
                            "outcome": "pass" if len(legacy_labels) <= 1 else "defer",
                            "reason_code": legacy_reason,
                        },
                    ],
                    policy_version=self.settings.policy_version,
                    policy_digest=self.settings.policy_digest,
                    confirmation_digest=sha256_json(candidate),
                    confirmed_at=now,
                )
            )
            await connection.execute(
                insert(schema.fact_versions).values(
                    fact_version_id=fact_version_id,
                    fact_id=fact_id,
                    version=1,
                    subject_type="person",
                    subject_id=value.parent_person_id,
                    predicate="parent_of",
                    object={"person_id": str(value.child_person_id)},
                    perspective_principal_id=principal["principal_id"],
                    valid_range=Range(valid_from, None, bounds="[)"),
                    system_range=Range(now, None, bounds="[)"),
                    authority="explicit_related_party",
                    support="explicit_authority",
                    contradiction="none",
                    freshness="not_applicable",
                    coverage="not_applicable",
                    resolution="accepted",
                    privacy_scope="private",
                    memory_transaction_id=transaction_id,
                )
            )
            await connection.execute(
                insert(schema.fact_support).values(
                    support_id=uuid7(),
                    fact_version_id=fact_version_id,
                    artifact_id=confirmation_artifact_id,
                    support_role="confirmation",
                )
            )
            if len(legacy_labels) == 1:
                await connection.execute(
                    insert(schema.fact_support).values(
                        support_id=uuid7(),
                        fact_version_id=fact_version_id,
                        artifact_id=legacy_labels[0],
                        dependency_domain="legacy_identity_store",
                        support_role="legacy_context",
                    )
                )
        return MemoryTransactionView(
            transaction_id=transaction_id,
            state="committed",
            fact_id=fact_id,
        )

    async def set_preference(
        self, principal: dict[str, Any], value: PreferenceUpdate
    ) -> dict[str, Any]:
        self._require_rollout("canary", "principal_preference")
        async with self.database.transaction(
            principal_id=principal["principal_id"], serializable=True
        ) as connection:
            if (
                value.key == "travel_greetings"
                and value.enabled
                and not await self._preference_enabled(
                    connection, principal["principal_id"], "location_memory"
                )
            ):
                raise ConflictError(
                    "travel greetings require location memory to be enabled first"
                )
            confirmation_artifact_id = await self._mint_authenticated_confirmation(
                connection,
                principal_id=principal["principal_id"],
                purpose="preference.confirm",
                proposal_digest=sha256_json(
                    {"key": value.key, "enabled": value.enabled}
                ),
                client_nonce=value.confirmation_artifact_id,
            )
            await connection.execute(
                insert(schema.preferences)
                .values(
                    preference_id=uuid7(),
                    principal_id=principal["principal_id"],
                    key=value.key,
                    enabled=value.enabled,
                    source_transaction_id=confirmation_artifact_id,
                )
                .on_conflict_do_update(
                    constraint="principal_preference",
                    set_={
                        "enabled": value.enabled,
                        "source_transaction_id": confirmation_artifact_id,
                        "updated_at": func.now(),
                    },
                )
            )
            if value.key == "location_memory" and not value.enabled:
                await connection.execute(
                    insert(schema.preferences)
                    .values(
                        preference_id=uuid7(),
                        principal_id=principal["principal_id"],
                        key="travel_greetings",
                        enabled=False,
                        source_transaction_id=confirmation_artifact_id,
                    )
                    .on_conflict_do_update(
                        constraint="principal_preference",
                        set_={
                            "enabled": False,
                            "source_transaction_id": confirmation_artifact_id,
                            "updated_at": func.now(),
                        },
                    )
                )
            if (value.key == "travel_greetings" and not value.enabled) or (
                value.key == "location_memory" and not value.enabled
            ):
                await connection.execute(
                    update(schema.initiatives)
                    .where(
                        schema.initiatives.c.principal_id == principal["principal_id"],
                        schema.initiatives.c.state.in_(["pending", "claimed"]),
                        schema.initiatives.c.purpose == "travel_arrival",
                    )
                    .values(state="suppressed", suppression_reason="principal_opt_out")
                )
        return {"key": value.key, "enabled": value.enabled}

    async def create_place(
        self, principal: dict[str, Any], value: PlaceCreate
    ) -> uuid.UUID:
        self._require_rollout("shadow", "semantic_place_write")
        if value.place_type != "locality":
            raise ConflictError(
                "direct place creation is operator-locality only; properties require teaching"
            )
        if value.place_type == "locality" and value.locator is None:
            raise ConflictError("a reviewed locality requires an encrypted locator")
        place_id = uuid7()
        locator_id = uuid7() if value.locator else None
        sealed_locator = (
            self.knowledge_cipher.seal(
                canonical_json(
                    {
                        "latitude": value.locator.latitude,
                        "longitude": value.locator.longitude,
                        "radius_m": value.locator.radius_m,
                    }
                ),
                purpose="place-locator",
                artifact_id=str(locator_id),
            )
            if value.locator and locator_id
            else None
        )
        now = datetime.now(UTC)
        async with self.database.transaction(
            principal_id=principal["principal_id"], serializable=True
        ) as connection:
            confirmation_artifact_id = await self._mint_authenticated_confirmation(
                connection,
                principal_id=principal["principal_id"],
                purpose="private_locality.confirm",
                proposal_digest=sha256_json(
                    {
                        "canonical_name": value.canonical_name,
                        "place_type": value.place_type,
                        "privacy_scope": value.privacy_scope,
                        "travel_greeting_eligible": value.travel_greeting_eligible,
                        "locator": value.locator.model_dump(mode="json")
                        if value.locator
                        else None,
                    }
                ),
                client_nonce=value.locator.confirmation_artifact_id,
            )
            await connection.execute(
                insert(schema.places).values(
                    place_id=place_id,
                    place_type=value.place_type,
                    canonical_name=value.canonical_name,
                    parent_place_id=value.parent_place_id,
                    owner_principal_id=principal["principal_id"],
                    privacy_scope=value.privacy_scope,
                    travel_greeting_eligible=value.travel_greeting_eligible,
                    status="active",
                )
            )
            if value.locator and locator_id and sealed_locator:
                await connection.execute(
                    insert(schema.place_locators).values(
                        locator_id=locator_id,
                        place_id=place_id,
                        ciphertext=sealed_locator.ciphertext,
                        nonce=sealed_locator.nonce,
                        key_id=self.knowledge_cipher.key_id,
                        locator_sha256=sealed_locator.sha256,
                        radius_m=value.locator.radius_m,
                        valid_range=Range(now, None, bounds="[)"),
                    )
                )
                await connection.execute(
                    insert(schema.artifact_registry),
                    [
                        {
                            "artifact_id": place_id,
                            "artifact_kind": "place",
                            "store": "postgresql",
                            "owner_principal_id": principal["principal_id"],
                            "retention_class": "until_erased",
                        },
                        {
                            "artifact_id": locator_id,
                            "artifact_kind": "encrypted_place_locator",
                            "store": "postgresql",
                            "owner_principal_id": principal["principal_id"],
                            "retention_class": "until_erased",
                        },
                    ],
                )
                await connection.execute(
                    insert(schema.artifact_links),
                    [
                        {
                            "link_id": uuid7(),
                            "parent_artifact_id": place_id,
                            "child_artifact_id": locator_id,
                            "relation": "contains_copy",
                        },
                        {
                            "link_id": uuid7(),
                            "parent_artifact_id": confirmation_artifact_id,
                            "child_artifact_id": locator_id,
                            "relation": "supports",
                        },
                    ],
                )
        return place_id

    async def create_visit(
        self, principal: dict[str, Any], value: VisitCreate
    ) -> VisitView:
        if value.observed_to <= value.observed_from:
            raise ConflictError("visit end must follow visit start")
        if value.observed_to - value.observed_from < timedelta(minutes=10):
            raise ConflictError("visit requires ten minutes of stable dwell")
        if value.anchor.radius_m > 100:
            raise ConflictError("a specific visit anchor may not exceed 100 metres")
        visit_id = uuid7()
        anchor_value = value.anchor.model_dump(mode="json")
        sealed = self.knowledge_cipher.seal(
            canonical_json(anchor_value),
            purpose="visit-anchor",
            artifact_id=str(visit_id),
        )
        async with self.database.transaction(
            principal_id=principal["principal_id"], serializable=True
        ) as connection:
            if not await self._preference_enabled(
                connection, principal["principal_id"], "location_memory"
            ):
                raise ForbiddenError("location memory is disabled for this principal")
            state = (
                "visiting"
                if value.freshness in {"fresh", "aging"}
                and value.coverage == "sufficient"
                else "candidate"
            )
            statement = (
                insert(schema.visits)
                .values(
                    visit_id=visit_id,
                    principal_id=principal["principal_id"],
                    place_id=value.place_id,
                    first_root_observation_id=value.first_root_observation_id,
                    observed_range=Range(
                        value.observed_from, value.observed_to, bounds="[)"
                    ),
                    state=state,
                    freshness=value.freshness,
                    coverage=value.coverage,
                    anchor_ciphertext=sealed.ciphertext,
                    anchor_nonce=sealed.nonce,
                    anchor_sha256=sealed.sha256,
                    anchor_radius_m=value.anchor.radius_m,
                )
                .on_conflict_do_update(
                    constraint="stable_visit_identity",
                    set_={
                        "observed_range": Range(
                            value.observed_from, value.observed_to, bounds="[)"
                        ),
                        "state": state,
                        "freshness": value.freshness,
                        "coverage": value.coverage,
                    },
                )
                .returning(
                    schema.visits.c.visit_id,
                    schema.visits.c.principal_id,
                    schema.visits.c.place_id,
                    schema.visits.c.state,
                    schema.visits.c.freshness,
                    schema.visits.c.coverage,
                )
            )
            row = (await connection.execute(statement)).mappings().one()
            if state == "visiting":
                await self._ensure_travel_initiative(
                    connection,
                    principal_id=principal["principal_id"],
                    visit_id=row["visit_id"],
                )
        return VisitView(**row)

    async def preview_descriptor(
        self,
        principal: dict[str, Any],
        value: DescriptorPreviewRequest,
    ) -> DescriptorPreview:
        self._require_rollout("canary", "persistent_memory")
        transaction_id = uuid7()
        async with self.database.transaction(
            principal_id=principal["principal_id"], serializable=True
        ) as connection:
            visit = (
                (
                    await connection.execute(
                        select(schema.visits)
                        .where(
                            schema.visits.c.visit_id == value.visit_id,
                            schema.visits.c.principal_id == principal["principal_id"],
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if visit is None:
                raise NotFoundError("visit does not exist")
            place = None
            if visit["place_id"] is not None:
                place = (
                    (
                        await connection.execute(
                            select(schema.places).where(
                                schema.places.c.place_id == visit["place_id"],
                                schema.places.c.owner_principal_id
                                == principal["principal_id"],
                                schema.places.c.status == "active",
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
                if place is None:
                    raise ConflictError("visit place is not an active owned place")
                if place["place_type"] == "property":
                    existing_descriptor = (
                        await connection.execute(
                            select(schema.fact_versions.c.fact_id).where(
                                schema.fact_versions.c.subject_type == "place",
                                schema.fact_versions.c.subject_id == visit["place_id"],
                                schema.fact_versions.c.predicate
                                == "place_social_descriptor",
                                schema.fact_versions.c.perspective_principal_id
                                == principal["principal_id"],
                                schema.fact_versions.c.resolution == "accepted",
                                func.upper_inf(schema.fact_versions.c.system_range),
                            )
                        )
                    ).scalar_one_or_none()
                    if existing_descriptor is not None:
                        raise ConflictError(
                            "specific place already has an active descriptor; use correction"
                        )
            location_opted_in = await self._preference_enabled(
                connection, principal["principal_id"], "location_memory"
            )
            parent_ids, hidden_parent_edges = await self._confirmed_parent_resolution(
                connection, principal["person_id"]
            )
            parent_rows = (
                (
                    await connection.execute(
                        select(
                            schema.people.c.person_id,
                            schema.people.c.display_name,
                        ).where(
                            schema.people.c.person_id.in_(parent_ids),
                            schema.people.c.status == "active",
                        )
                    )
                )
                .mappings()
                .all()
            )
            parent_names = {
                row["person_id"]: row["display_name"] for row in parent_rows
            }
            resolved_parents = [
                ResolvedPersonView(
                    person_id=person_id,
                    display_name=parent_names[person_id],
                )
                for person_id in parent_ids
                if person_id in parent_names
            ]
            decision = propose_parents_mountain_house(
                exact_text=value.exact_text,
                authenticated=True,
                principal_bound=True,
                location_memory_opted_in=location_opted_in,
                active_place_count=(
                    1
                    if visit["state"] == "visiting"
                    and self._visit_is_current(visit, datetime.now(UTC))
                    else 0
                ),
                anchor_supported=(
                    visit["anchor_kind"] == "specific"
                    and visit["anchor_radius_m"] <= 100
                    and visit["freshness"] in {"fresh", "aging"}
                    and visit["coverage"] == "sufficient"
                    and self._visit_is_current(visit, datetime.now(UTC))
                ),
                parent_person_ids=parent_ids,
                role_expansion_complete=not hidden_parent_edges,
            )
            preview = descriptor_preview_payload(
                transaction_id=transaction_id,
                place_id=(
                    visit["place_id"]
                    if place is not None and place["place_type"] == "property"
                    else None
                ),
                decision=decision,
                locator_summary={
                    "coordinates": (
                        "encrypted"
                        if visit["anchor_kind"] == "specific"
                        else "not_retained"
                    ),
                    "resolution": visit["anchor_kind"],
                    "radius_m": visit["anchor_radius_m"],
                    "source_visit_id": str(visit["visit_id"]),
                    "current_place_id": str(visit["place_id"])
                    if visit["place_id"]
                    else None,
                    "specific_locator_retention": (
                        "will_retain_on_confirmation"
                        if visit["anchor_kind"] == "specific"
                        else "blocked_location_unresolved"
                    ),
                },
            )
            sealed_text = self.knowledge_cipher.seal(
                value.exact_text.encode(),
                purpose="memory-exact-text",
                artifact_id=str(transaction_id),
            )
            verification_input_digest = sha256_json(
                {
                    "exact_text_sha256": sealed_text.sha256,
                    "visit_id": str(visit["visit_id"]),
                    "visit_place_id": str(visit["place_id"]),
                    "visit_anchor_sha256": visit["anchor_sha256"],
                    "visit_anchor_kind": visit["anchor_kind"],
                    "visit_anchor_radius_m": visit["anchor_radius_m"],
                    "first_root_observation_id": str(
                        visit["first_root_observation_id"]
                    ),
                    "resolved_parent_person_ids": sorted(
                        str(item) for item in parent_ids
                    ),
                    "role_expansion_complete": not hidden_parent_edges,
                    "policy_digest": self.settings.policy_digest,
                }
            )
            verifier_results = self._verifier_receipts(
                decision.results,
                input_digest=verification_input_digest,
                phase="preview",
            )
            preview["verifier_results"] = verifier_results
            digest = self._preview_digest(preview)
            stored_preview = dict(preview)
            stored_preview["exact_descriptor"] = {
                "storage": "encrypted",
                "sha256": sealed_text.sha256,
            }
            await connection.execute(
                insert(schema.memory_transactions).values(
                    transaction_id=transaction_id,
                    principal_id=principal["principal_id"],
                    visit_id=value.visit_id,
                    kind="place_social_descriptor",
                    state=decision.state,
                    exact_text_ciphertext=sealed_text.ciphertext,
                    exact_text_nonce=sealed_text.nonce,
                    exact_text_sha256=sealed_text.sha256,
                    candidate={
                        "descriptor_code": "parents_mountain_house",
                        "role_expression": "parents_of(self)",
                        "visit_id": str(value.visit_id),
                        "visit_place_id": str(visit["place_id"]),
                        "visit_anchor_sha256": visit["anchor_sha256"],
                        "visit_anchor_kind": visit["anchor_kind"],
                        "visit_anchor_radius_m": visit["anchor_radius_m"],
                        "first_root_observation_id": str(
                            visit["first_root_observation_id"]
                        ),
                        "resolved_parent_person_ids": sorted(
                            str(item) for item in parent_ids
                        ),
                        "role_expansion_complete": not hidden_parent_edges,
                        "verification_input_digest": verification_input_digest,
                    },
                    preview=stored_preview,
                    verifier_results=verifier_results,
                    policy_version=self.settings.policy_version,
                    policy_digest=self.settings.policy_digest,
                )
            )
        return DescriptorPreview(
            transaction_id=transaction_id,
            state=decision.state,
            preview_digest=digest,
            place_id=(
                visit["place_id"]
                if place is not None and place["place_type"] == "property"
                else None
            ),
            exact_descriptor=decision.exact_descriptor,
            role_expression=decision.role_expression,
            resolved_parent_person_ids=list(decision.resolved_parent_person_ids),
            resolved_parents=resolved_parents,
            unresolved_role_expression=decision.unresolved_role_expression,
            locator=preview["locator"],
            retained=preview["retained"],
            not_asserted=preview["not_asserted"],
            verifier_results=preview["verifier_results"],
        )

    async def confirm_descriptor(
        self,
        principal: dict[str, Any],
        transaction_id: uuid.UUID,
        value: ConfirmMemoryRequest,
    ) -> MemoryTransactionView:
        self._require_rollout("canary", "persistent_memory")
        now = datetime.now(UTC)
        async with self.database.transaction(
            principal_id=principal["principal_id"], serializable=True
        ) as connection:
            transaction = (
                (
                    await connection.execute(
                        select(schema.memory_transactions)
                        .where(
                            schema.memory_transactions.c.transaction_id
                            == transaction_id,
                            schema.memory_transactions.c.principal_id
                            == principal["principal_id"],
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if transaction is None:
                raise NotFoundError("memory transaction does not exist")
            if transaction["state"] != "needs_confirmation":
                raise ConflictError(f"memory transaction is {transaction['state']}")
            exact_text = self._open_memory_exact_text(transaction)
            restored_preview = dict(transaction["preview"])
            restored_preview["exact_descriptor"] = exact_text
            if not hmac.compare_digest(
                self._preview_digest(restored_preview), value.preview_digest
            ):
                raise ConflictError("preview digest does not match reviewed proposal")

            visit = (
                (
                    await connection.execute(
                        select(schema.visits)
                        .where(
                            schema.visits.c.visit_id == transaction["visit_id"],
                            schema.visits.c.principal_id == principal["principal_id"],
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if (
                visit["state"] != "visiting"
                or visit["coverage"] != "sufficient"
                or visit["anchor_kind"] != "specific"
                or not self._visit_is_current(visit, now)
            ):
                raise ConflictError("visit is no longer safe to resolve")
            if not await self._preference_enabled(
                connection, principal["principal_id"], "location_memory"
            ):
                raise ForbiddenError("location memory was disabled before confirmation")

            candidate = dict(transaction["candidate"])
            current_anchor_snapshot = {
                "visit_place_id": str(visit["place_id"]),
                "visit_anchor_sha256": visit["anchor_sha256"],
                "visit_anchor_kind": visit["anchor_kind"],
                "visit_anchor_radius_m": visit["anchor_radius_m"],
                "first_root_observation_id": str(visit["first_root_observation_id"]),
            }
            if any(
                candidate.get(key) != current_value
                for key, current_value in current_anchor_snapshot.items()
            ):
                raise ConflictError("visit anchor changed after preview; re-preview")

            fact_id = uuid7()
            fact_version_id = uuid7()
            parent_ids, hidden_parent_edges = await self._confirmed_parent_resolution(
                connection, principal["person_id"]
            )
            preview_parent_ids = sorted(restored_preview["resolved_parent_person_ids"])
            if preview_parent_ids != sorted(str(item) for item in parent_ids):
                raise ConflictError("parent role resolution changed after preview")
            if bool(restored_preview["unresolved_role_expression"]) != (
                hidden_parent_edges or not parent_ids
            ):
                raise ConflictError("parent role visibility changed after preview")
            commit_input_digest = sha256_json(
                {
                    "exact_text_sha256": transaction["exact_text_sha256"],
                    **current_anchor_snapshot,
                    "visit_id": str(visit["visit_id"]),
                    "resolved_parent_person_ids": sorted(
                        str(item) for item in parent_ids
                    ),
                    "role_expansion_complete": not hidden_parent_edges,
                    "policy_digest": self.settings.policy_digest,
                }
            )
            if not hmac.compare_digest(
                candidate["verification_input_digest"], commit_input_digest
            ):
                raise ConflictError(
                    "verification inputs changed after preview; re-preview"
                )
            commit_decision = propose_parents_mountain_house(
                exact_text=exact_text,
                authenticated=True,
                principal_bound=True,
                location_memory_opted_in=True,
                active_place_count=1,
                anchor_supported=True,
                parent_person_ids=parent_ids,
                role_expansion_complete=not hidden_parent_edges,
            )
            if commit_decision.state != "needs_confirmation":
                raise ConflictError("commit verification changed; re-preview")
            commit_receipts = self._verifier_receipts(
                commit_decision.results,
                input_digest=commit_input_digest,
                phase="commit",
            )

            visit_place = (
                (
                    await connection.execute(
                        select(schema.places)
                        .where(
                            schema.places.c.place_id == visit["place_id"],
                            schema.places.c.owner_principal_id
                            == principal["principal_id"],
                            schema.places.c.status == "active",
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if visit_place is None:
                raise ConflictError("visit place is no longer an active owned place")
            if visit_place["place_type"] == "property":
                place_id = visit_place["place_id"]
                active_locator_count = (
                    await connection.execute(
                        select(func.count())
                        .select_from(schema.place_locators)
                        .where(
                            schema.place_locators.c.place_id == place_id,
                            func.upper_inf(schema.place_locators.c.valid_range),
                        )
                    )
                ).scalar_one()
                if active_locator_count != 1:
                    raise ConflictError(
                        "matched property does not have one active locator"
                    )
            elif visit_place["place_type"] == "locality":
                place_id = uuid7()
                locator_id = uuid7()
                await connection.execute(
                    insert(schema.places).values(
                        place_id=place_id,
                        place_type="property",
                        parent_place_id=visit_place["place_id"],
                        owner_principal_id=principal["principal_id"],
                        privacy_scope="private",
                        status="active",
                    )
                )
                # Re-encrypt for locator-specific AAD. Ciphertext is never
                # copied across purposes or artifact identities.
                anchor = self.knowledge_cipher.open(
                    SealedValue(
                        nonce=bytes(visit["anchor_nonce"]),
                        ciphertext=bytes(visit["anchor_ciphertext"]),
                        sha256=visit["anchor_sha256"],
                    ),
                    purpose="visit-anchor",
                    artifact_id=str(visit["visit_id"]),
                )
                locator = self.knowledge_cipher.seal(
                    anchor,
                    purpose="place-locator",
                    artifact_id=str(locator_id),
                )
                await connection.execute(
                    insert(schema.place_locators).values(
                        locator_id=locator_id,
                        place_id=place_id,
                        ciphertext=locator.ciphertext,
                        nonce=locator.nonce,
                        key_id=self.knowledge_cipher.key_id,
                        locator_sha256=locator.sha256,
                        radius_m=visit["anchor_radius_m"],
                        valid_range=Range(now, None, bounds="[)"),
                    )
                )
                await connection.execute(
                    update(schema.visits)
                    .where(schema.visits.c.visit_id == visit["visit_id"])
                    .values(place_id=place_id)
                )
            else:
                raise ConflictError("teaching requires a reviewed locality or property")

            confirmation_artifact_id = await self._mint_authenticated_confirmation(
                connection,
                principal_id=principal["principal_id"],
                purpose="place_social_descriptor.confirm",
                proposal_digest=value.preview_digest,
                client_nonce=value.confirmation_artifact_id,
            )
            await connection.execute(
                insert(schema.fact_versions).values(
                    fact_version_id=fact_version_id,
                    fact_id=fact_id,
                    version=1,
                    subject_type="place",
                    subject_id=place_id,
                    predicate="place_social_descriptor",
                    object={
                        "descriptor_code": "parents_mountain_house",
                        "role_expression": "parents_of(self)",
                        "resolution_at_commit": [str(item) for item in parent_ids],
                        "role_expansion_complete": not hidden_parent_edges,
                    },
                    perspective_principal_id=principal["principal_id"],
                    valid_range=Range(now, None, bounds="[)"),
                    system_range=Range(now, None, bounds="[)"),
                    authority="explicit_subject",
                    support="explicit_authority",
                    contradiction="none",
                    freshness="not_applicable",
                    coverage="not_applicable",
                    resolution="accepted",
                    privacy_scope="private",
                    memory_transaction_id=transaction_id,
                )
            )
            await connection.execute(
                insert(schema.fact_support).values(
                    support_id=uuid7(),
                    fact_version_id=fact_version_id,
                    artifact_id=confirmation_artifact_id,
                    support_role="confirmation",
                )
            )
            await connection.execute(
                insert(schema.artifact_registry),
                [
                    {
                        "artifact_id": transaction_id,
                        "artifact_kind": "memory_transaction",
                        "store": "postgresql",
                        "owner_principal_id": principal["principal_id"],
                        "retention_class": "until_erased",
                    },
                    {
                        "artifact_id": fact_id,
                        "artifact_kind": "semantic_fact",
                        "store": "postgresql",
                        "owner_principal_id": principal["principal_id"],
                        "retention_class": "until_erased",
                    },
                ],
            )
            await connection.execute(
                insert(schema.artifact_links),
                [
                    {
                        "link_id": uuid7(),
                        "parent_artifact_id": confirmation_artifact_id,
                        "child_artifact_id": transaction_id,
                        "relation": "supports",
                    },
                    {
                        "link_id": uuid7(),
                        "parent_artifact_id": transaction_id,
                        "child_artifact_id": fact_id,
                        "relation": "derived_from",
                    },
                ],
            )
            await connection.execute(
                update(schema.memory_transactions)
                .where(schema.memory_transactions.c.transaction_id == transaction_id)
                .values(
                    state="committed",
                    confirmation_digest=value.preview_digest,
                    confirmed_at=now,
                    verifier_results=[
                        *list(transaction["verifier_results"]),
                        *commit_receipts,
                    ],
                    updated_at=now,
                )
            )
            await connection.execute(
                insert(schema.outbox).values(
                    outbox_id=uuid7(),
                    topic="memory.committed",
                    aggregate_id=fact_id,
                    payload={
                        "fact_id": str(fact_id),
                        "predicate": "place_social_descriptor",
                    },
                )
            )
        return MemoryTransactionView(
            transaction_id=transaction_id,
            state="committed",
            fact_id=fact_id,
            place_id=place_id,
        )

    async def preview_descriptor_correction(
        self,
        principal: dict[str, Any],
        fact_id: uuid.UUID,
        value: DescriptorCorrectionPreviewRequest,
    ) -> DescriptorLifecyclePreview:
        self._require_rollout("canary", "persistent_memory")
        return await self._preview_descriptor_lifecycle(
            principal,
            fact_id=fact_id,
            operation="correction",
            replacement_exact_text=value.exact_text,
        )

    async def preview_descriptor_retraction(
        self,
        principal: dict[str, Any],
        fact_id: uuid.UUID,
    ) -> DescriptorLifecyclePreview:
        self._require_rollout("canary", "persistent_memory")
        return await self._preview_descriptor_lifecycle(
            principal,
            fact_id=fact_id,
            operation="retraction",
            replacement_exact_text=None,
        )

    async def _preview_descriptor_lifecycle(
        self,
        principal: dict[str, Any],
        *,
        fact_id: uuid.UUID,
        operation: str,
        replacement_exact_text: str | None,
    ) -> DescriptorLifecyclePreview:
        if operation not in {"correction", "retraction"}:
            raise ValueError("unsupported descriptor lifecycle operation")

        transaction_id = uuid7()
        async with self.database.transaction(
            principal_id=principal["principal_id"], serializable=True
        ) as connection:
            fact = await self._active_descriptor_fact(
                connection,
                principal_id=principal["principal_id"],
                fact_id=fact_id,
                accepted_only=True,
                lock=True,
            )
            source_transaction = await self._memory_transaction_for_fact(
                connection, fact, lock=True
            )
            current_exact_text = self._open_memory_exact_text(source_transaction)
            proposed_exact_text = (
                replacement_exact_text or current_exact_text
                if operation == "correction"
                else None
            )
            if (
                proposed_exact_text is not None
                and not is_typed_parents_mountain_descriptor(proposed_exact_text)
            ):
                raise ConflictError(
                    "replacement wording is outside the typed parents-house descriptor"
                )
            fact_object = dict(fact["object"])
            role_expression = fact_object.get("role_expression")
            if (
                fact["subject_type"] != "place"
                or fact_object.get("descriptor_code") != "parents_mountain_house"
                or role_expression != "parents_of(self)"
            ):
                raise ConflictError("fact is not the typed parents-house descriptor")

            (
                resolved_parent_ids,
                hidden_parent_edges,
            ) = await self._confirmed_parent_resolution(
                connection, principal["person_id"]
            )
            verifier_results = [
                {
                    "rule": "typed_descriptor",
                    "outcome": "pass",
                    "reason_code": "active_place_social_descriptor",
                },
                {
                    "rule": "principal_authority",
                    "outcome": "pass",
                    "reason_code": "authenticated_perspective_subject",
                },
                {
                    "rule": "immutable_scope",
                    "outcome": "pass",
                    "reason_code": "place_locator_and_parent_facts_unchanged",
                },
                {
                    "rule": "prohibited_implications",
                    "outcome": "pass",
                    "reason_code": "descriptor_only",
                },
            ]
            public_preview = self._descriptor_lifecycle_preview_payload(
                transaction_id=transaction_id,
                operation=operation,
                fact=fact,
                current_exact_text=current_exact_text,
                proposed_exact_text=proposed_exact_text,
                role_expression=role_expression,
                resolved_parent_ids=resolved_parent_ids,
                role_expansion_complete=not hidden_parent_edges,
                verifier_results=verifier_results,
            )
            digest = self._preview_digest(public_preview)

            sealed_proposed = None
            if proposed_exact_text is not None:
                sealed_proposed = self.knowledge_cipher.seal(
                    proposed_exact_text.encode(),
                    purpose="memory-exact-text",
                    artifact_id=str(transaction_id),
                )
            redacted_preview = dict(public_preview)
            redacted_preview["current_exact_descriptor"] = {
                "encrypted": True,
                "sha256": source_transaction["exact_text_sha256"],
            }
            if proposed_exact_text is not None and sealed_proposed is not None:
                redacted_preview["proposed_exact_descriptor"] = {
                    "encrypted": True,
                    "sha256": sealed_proposed.sha256,
                }

            await connection.execute(
                insert(schema.memory_transactions).values(
                    transaction_id=transaction_id,
                    principal_id=principal["principal_id"],
                    visit_id=source_transaction["visit_id"],
                    kind=f"place_social_descriptor_{operation}",
                    state="needs_confirmation",
                    exact_text_ciphertext=(
                        sealed_proposed.ciphertext if sealed_proposed else None
                    ),
                    exact_text_nonce=sealed_proposed.nonce if sealed_proposed else None,
                    exact_text_sha256=(
                        sealed_proposed.sha256 if sealed_proposed else None
                    ),
                    candidate={
                        "operation": operation,
                        "descriptor_fact_id": str(fact_id),
                        "base_fact_version_id": str(fact["fact_version_id"]),
                        "base_version": fact["version"],
                        "source_memory_transaction_id": str(
                            source_transaction["transaction_id"]
                        ),
                        "resolved_parent_person_ids": [
                            str(value) for value in resolved_parent_ids
                        ],
                        "role_expansion_complete": not hidden_parent_edges,
                    },
                    preview=redacted_preview,
                    verifier_results=verifier_results,
                    policy_version=self.settings.policy_version,
                    policy_digest=self.settings.policy_digest,
                )
            )

        return DescriptorLifecyclePreview(
            **public_preview,
            preview_digest=digest,
        )

    async def confirm_descriptor_correction(
        self,
        principal: dict[str, Any],
        transaction_id: uuid.UUID,
        value: DescriptorLifecycleConfirm,
    ) -> MemoryTransactionView:
        self._require_rollout("canary", "persistent_memory")
        return await self._confirm_descriptor_lifecycle(
            principal,
            transaction_id=transaction_id,
            value=value,
            operation="correction",
        )

    async def confirm_descriptor_retraction(
        self,
        principal: dict[str, Any],
        transaction_id: uuid.UUID,
        value: DescriptorLifecycleConfirm,
    ) -> MemoryTransactionView:
        self._require_rollout("canary", "persistent_memory")
        return await self._confirm_descriptor_lifecycle(
            principal,
            transaction_id=transaction_id,
            value=value,
            operation="retraction",
        )

    async def _confirm_descriptor_lifecycle(
        self,
        principal: dict[str, Any],
        *,
        transaction_id: uuid.UUID,
        value: DescriptorLifecycleConfirm,
        operation: str,
    ) -> MemoryTransactionView:
        if operation not in {"correction", "retraction"}:
            raise ValueError("unsupported descriptor lifecycle operation")

        now = datetime.now(UTC)
        async with self.database.transaction(
            principal_id=principal["principal_id"], serializable=True
        ) as connection:
            transaction = (
                (
                    await connection.execute(
                        select(schema.memory_transactions)
                        .where(
                            schema.memory_transactions.c.transaction_id
                            == transaction_id,
                            schema.memory_transactions.c.principal_id
                            == principal["principal_id"],
                            schema.memory_transactions.c.kind
                            == f"place_social_descriptor_{operation}",
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if transaction is None:
                raise NotFoundError(
                    f"descriptor {operation} transaction does not exist"
                )
            if transaction["state"] != "needs_confirmation":
                raise ConflictError(
                    f"descriptor {operation} transaction is {transaction['state']}"
                )

            candidate = dict(transaction["candidate"])
            fact_id = uuid.UUID(candidate["descriptor_fact_id"])
            try:
                fact = await self._active_descriptor_fact(
                    connection,
                    principal_id=principal["principal_id"],
                    fact_id=fact_id,
                    accepted_only=True,
                    lock=True,
                )
            except NotFoundError as exc:
                raise ConflictError(
                    "descriptor changed or was retracted after preview"
                ) from exc
            if (
                str(fact["fact_version_id"]) != candidate["base_fact_version_id"]
                or fact["version"] != candidate["base_version"]
            ):
                raise ConflictError("descriptor version changed after preview")

            source_transaction = await self._memory_transaction_for_fact(
                connection, fact, lock=True
            )
            if str(source_transaction["transaction_id"]) != candidate.get(
                "source_memory_transaction_id"
            ):
                raise ConflictError("descriptor lineage changed after preview")
            current_exact_text = self._open_memory_exact_text(source_transaction)
            proposed_exact_text = (
                self._open_memory_exact_text(transaction)
                if operation == "correction"
                else None
            )

            (
                resolved_parent_ids,
                hidden_parent_edges,
            ) = await self._confirmed_parent_resolution(
                connection, principal["person_id"]
            )
            if sorted(str(value) for value in resolved_parent_ids) != sorted(
                candidate.get("resolved_parent_person_ids", [])
            ):
                raise ConflictError("parent role resolution changed after preview")
            if candidate.get("role_expansion_complete") != (not hidden_parent_edges):
                raise ConflictError("parent role visibility changed after preview")
            if operation == "correction" and not await self._preference_enabled(
                connection, principal["principal_id"], "location_memory"
            ):
                raise ForbiddenError("location memory was disabled before confirmation")

            restored_preview = dict(transaction["preview"])
            restored_preview["current_exact_descriptor"] = current_exact_text
            restored_preview["proposed_exact_descriptor"] = proposed_exact_text
            expected_digest = self._preview_digest(restored_preview)
            if not hmac.compare_digest(expected_digest, value.preview_digest):
                raise ConflictError(f"descriptor {operation} preview digest mismatch")
            confirmation_artifact_id = await self._mint_authenticated_confirmation(
                connection,
                principal_id=principal["principal_id"],
                purpose=f"place_social_descriptor.{operation}.confirm",
                proposal_digest=value.preview_digest,
                client_nonce=value.confirmation_artifact_id,
            )

            await connection.execute(
                update(schema.fact_versions)
                .where(
                    schema.fact_versions.c.fact_version_id == fact["fact_version_id"]
                )
                .values(
                    system_range=Range(fact["system_range"].lower, now, bounds="[)")
                )
            )

            new_fact_version_id = uuid7()
            new_object = dict(fact["object"])
            new_resolution = "accepted"
            new_valid_range = fact["valid_range"]
            if operation == "correction":
                new_object["resolution_at_commit"] = [
                    str(item) for item in resolved_parent_ids
                ]
                new_object["role_expansion_complete"] = not hidden_parent_edges
                new_valid_range = Range(now, None, bounds="[)")
            else:
                new_resolution = "suppressed"

            await connection.execute(
                insert(schema.fact_versions).values(
                    fact_version_id=new_fact_version_id,
                    fact_id=fact_id,
                    version=fact["version"] + 1,
                    subject_type=fact["subject_type"],
                    subject_id=fact["subject_id"],
                    predicate="place_social_descriptor",
                    object=new_object,
                    perspective_principal_id=principal["principal_id"],
                    valid_range=new_valid_range,
                    system_range=Range(now, None, bounds="[)"),
                    authority="explicit_subject",
                    support="explicit_authority",
                    contradiction="none",
                    freshness="not_applicable",
                    coverage="not_applicable",
                    resolution=new_resolution,
                    privacy_scope=fact["privacy_scope"],
                    memory_transaction_id=transaction_id,
                )
            )
            await connection.execute(
                insert(schema.fact_support).values(
                    support_id=uuid7(),
                    fact_version_id=new_fact_version_id,
                    artifact_id=confirmation_artifact_id,
                    support_role="confirmation",
                )
            )
            await connection.execute(
                insert(schema.artifact_registry),
                [
                    {
                        "artifact_id": transaction_id,
                        "artifact_kind": "memory_transaction",
                        "store": "postgresql",
                        "owner_principal_id": principal["principal_id"],
                        "retention_class": "until_erased",
                    },
                ],
            )
            await connection.execute(
                insert(schema.artifact_links),
                [
                    {
                        "link_id": uuid7(),
                        "parent_artifact_id": source_transaction["transaction_id"],
                        "child_artifact_id": transaction_id,
                        "relation": "derived_from",
                    },
                    {
                        "link_id": uuid7(),
                        "parent_artifact_id": confirmation_artifact_id,
                        "child_artifact_id": transaction_id,
                        "relation": "supports",
                    },
                    {
                        "link_id": uuid7(),
                        "parent_artifact_id": transaction_id,
                        "child_artifact_id": fact_id,
                        "relation": "derived_from",
                    },
                ],
            )
            await invalidate_descriptor_dependents(
                connection,
                fact_id=fact_id,
                source_fact_version_id=fact["fact_version_id"],
                reason=(
                    "descriptor_corrected"
                    if operation == "correction"
                    else "descriptor_retracted"
                ),
            )
            await connection.execute(
                update(schema.memory_transactions)
                .where(schema.memory_transactions.c.transaction_id == transaction_id)
                .values(
                    state="committed",
                    confirmation_digest=value.preview_digest,
                    confirmed_at=now,
                    updated_at=now,
                )
            )
            await connection.execute(
                insert(schema.outbox).values(
                    outbox_id=uuid7(),
                    topic=f"memory.descriptor.{operation}",
                    aggregate_id=fact_id,
                    payload={
                        "fact_id": str(fact_id),
                        "closed_fact_version_id": str(fact["fact_version_id"]),
                        "new_fact_version_id": str(new_fact_version_id),
                        "new_version": fact["version"] + 1,
                        "resolution": new_resolution,
                        "invalidate_descriptor_views": True,
                    },
                )
            )

        return MemoryTransactionView(
            transaction_id=transaction_id,
            state="committed",
            fact_id=fact_id,
            place_id=fact["subject_id"],
        )

    async def _active_descriptor_fact(
        self,
        connection,
        *,
        principal_id: uuid.UUID,
        fact_id: uuid.UUID,
        accepted_only: bool,
        lock: bool,
    ) -> dict[str, Any]:
        statement = (
            select(schema.fact_versions)
            .where(
                schema.fact_versions.c.fact_id == fact_id,
                schema.fact_versions.c.predicate == "place_social_descriptor",
                schema.fact_versions.c.perspective_principal_id == principal_id,
                func.upper_inf(schema.fact_versions.c.system_range),
            )
            .order_by(schema.fact_versions.c.version.desc())
            .limit(2)
        )
        if accepted_only:
            statement = statement.where(schema.fact_versions.c.resolution == "accepted")
        if lock:
            statement = statement.with_for_update()
        rows = (await connection.execute(statement)).mappings().all()
        if not rows:
            raise NotFoundError("active descriptor fact does not exist")
        if len(rows) != 1:
            raise ConflictError("descriptor has multiple active versions")
        return dict(rows[0])

    async def _memory_transaction_for_fact(
        self,
        connection,
        fact: dict[str, Any],
        *,
        lock: bool,
    ) -> dict[str, Any]:
        statement = select(schema.memory_transactions).where(
            schema.memory_transactions.c.transaction_id == fact["memory_transaction_id"]
        )
        if lock:
            statement = statement.with_for_update()
        transaction = (await connection.execute(statement)).mappings().first()
        if transaction is None:
            raise ConflictError("descriptor memory lineage is incomplete")
        return dict(transaction)

    def _open_memory_exact_text(self, transaction: dict[str, Any]) -> str:
        if (
            transaction.get("exact_text_ciphertext") is None
            or transaction.get("exact_text_nonce") is None
            or transaction.get("exact_text_sha256") is None
        ):
            raise ConflictError("descriptor wording is unavailable")
        try:
            plaintext = self.knowledge_cipher.open(
                SealedValue(
                    nonce=bytes(transaction["exact_text_nonce"]),
                    ciphertext=bytes(transaction["exact_text_ciphertext"]),
                    sha256=transaction["exact_text_sha256"],
                ),
                purpose="memory-exact-text",
                artifact_id=str(transaction["transaction_id"]),
            )
            return plaintext.decode("utf-8")
        except (TypeError, UnicodeDecodeError, ValueError) as exc:
            raise ConflictError(
                "descriptor wording failed integrity verification"
            ) from exc

    @staticmethod
    def _descriptor_lifecycle_preview_payload(
        *,
        transaction_id: uuid.UUID,
        operation: str,
        fact: dict[str, Any],
        current_exact_text: str,
        proposed_exact_text: str | None,
        role_expression: str,
        resolved_parent_ids: list[uuid.UUID],
        role_expansion_complete: bool,
        verifier_results: list[dict[str, str]],
    ) -> dict[str, Any]:
        return {
            "transaction_id": str(transaction_id),
            "operation": operation,
            "state": "needs_confirmation",
            "descriptor_fact_id": str(fact["fact_id"]),
            "base_fact_version_id": str(fact["fact_version_id"]),
            "base_version": fact["version"],
            "new_version": fact["version"] + 1,
            "place_id": str(fact["subject_id"]),
            "current_exact_descriptor": current_exact_text,
            "proposed_exact_descriptor": proposed_exact_text,
            "role_expression": role_expression,
            "resolved_parent_person_ids": [str(value) for value in resolved_parent_ids],
            "unresolved_role_expression": (
                not resolved_parent_ids or not role_expansion_complete
            ),
            "retained": [
                "place and encrypted locator",
                "visit history and Itaipava locality",
                "independently confirmed parent facts",
                "governed descriptor version history",
            ],
            "invalidated": [
                "descriptor-derived retrieval views",
                "pending initiatives derived from the superseded descriptor version",
            ],
            "not_asserted": list(PROHIBITED_ASSERTIONS),
            "verifier_results": verifier_results,
        }

    async def _initiative_presentation_context(
        self,
        connection,
        *,
        principal_id: uuid.UUID,
        initiative: dict[str, Any],
        now: datetime,
    ) -> tuple[dict[str, Any] | None, str]:
        """Revalidate every private-location claim at presentation time."""

        if (
            initiative.get("purpose") != "travel_arrival"
            or initiative.get("template_key") != "travel_arrival_v1"
            or "private_tauri" not in (initiative.get("allowed_surfaces") or [])
        ):
            return None, "initiative_contract_invalid"
        if not await self._preference_enabled(
            connection, principal_id, "location_memory"
        ) or not await self._preference_enabled(
            connection, principal_id, "travel_greetings"
        ):
            return None, "consent_disabled"
        visit = (
            (
                await connection.execute(
                    select(schema.visits).where(
                        schema.visits.c.visit_id == initiative.get("visit_id"),
                        schema.visits.c.principal_id == principal_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        if visit is None or visit["state"] != "visiting":
            return None, "visit_not_active"
        if visit["coverage"] != "sufficient":
            return None, "coverage_insufficient"
        observed_to = visit["observed_range"].upper
        if (
            visit["freshness"] != "fresh"
            or observed_to is None
            or observed_to > now
            or now - observed_to > timedelta(minutes=15)
        ):
            return None, "visit_stale"
        if (
            visit["anchor_kind"] != "specific"
            or visit["anchor_radius_m"] > 100
            or visit["place_id"] is None
        ):
            return None, "specific_locator_unresolved"
        place = (
            (
                await connection.execute(
                    select(schema.places).where(
                        schema.places.c.place_id == visit["place_id"],
                        schema.places.c.owner_principal_id == principal_id,
                        schema.places.c.place_type == "property",
                        schema.places.c.status == "active",
                    )
                )
            )
            .mappings()
            .first()
        )
        if place is None or place["parent_place_id"] is None:
            return None, "specific_place_unavailable"
        locality_enabled = (
            await connection.execute(
                select(schema.places.c.travel_greeting_eligible).where(
                    schema.places.c.place_id == place["parent_place_id"],
                    schema.places.c.owner_principal_id == principal_id,
                    schema.places.c.place_type == "locality",
                    schema.places.c.status == "active",
                )
            )
        ).scalar_one_or_none()
        if locality_enabled is not True:
            return None, "greeting_scope_disabled"
        locators = (
            (
                await connection.execute(
                    select(schema.place_locators).where(
                        schema.place_locators.c.place_id == visit["place_id"],
                        func.upper_inf(schema.place_locators.c.valid_range),
                    )
                )
            )
            .mappings()
            .all()
        )
        if len(locators) != 1:
            return None, "specific_locator_unresolved"
        locator_row = dict(locators[0])
        descriptor = (
            (
                await connection.execute(
                    select(schema.fact_versions).where(
                        schema.fact_versions.c.subject_type == "place",
                        schema.fact_versions.c.subject_id == visit["place_id"],
                        schema.fact_versions.c.predicate == "place_social_descriptor",
                        schema.fact_versions.c.perspective_principal_id == principal_id,
                        schema.fact_versions.c.authority == "explicit_subject",
                        schema.fact_versions.c.support == "explicit_authority",
                        schema.fact_versions.c.contradiction == "none",
                        schema.fact_versions.c.resolution == "accepted",
                        func.upper_inf(schema.fact_versions.c.system_range),
                    )
                )
            )
            .mappings()
            .first()
        )
        if descriptor is None:
            return None, "descriptor_unavailable"
        if descriptor["fact_version_id"] != initiative.get("source_fact_version_id"):
            return None, "descriptor_changed"
        descriptor_object = dict(descriptor["object"])
        if (
            descriptor_object.get("descriptor_code") != "parents_mountain_house"
            or descriptor_object.get("role_expression") != "parents_of(self)"
        ):
            return None, "descriptor_contract_invalid"
        transaction = await self._memory_transaction_for_fact(
            connection,
            dict(descriptor),
            lock=False,
        )
        if transaction["state"] != "committed":
            return None, "descriptor_unavailable"
        try:
            visit_anchor = json.loads(
                self.knowledge_cipher.open(
                    SealedValue(
                        nonce=bytes(visit["anchor_nonce"]),
                        ciphertext=bytes(visit["anchor_ciphertext"]),
                        sha256=visit["anchor_sha256"],
                    ),
                    purpose="visit-anchor",
                    artifact_id=str(visit["visit_id"]),
                )
            )
            locator = json.loads(
                self.knowledge_cipher.open(
                    SealedValue(
                        nonce=bytes(locator_row["nonce"]),
                        ciphertext=bytes(locator_row["ciphertext"]),
                        sha256=locator_row["locator_sha256"],
                    ),
                    purpose="place-locator",
                    artifact_id=str(locator_row["locator_id"]),
                )
            )
            distance = haversine_m(
                float(visit_anchor["latitude"]),
                float(visit_anchor["longitude"]),
                float(locator["latitude"]),
                float(locator["longitude"]),
            )
            if distance > float(locator_row["radius_m"]):
                return None, "specific_locator_mismatch"
            exact_descriptor = self._open_memory_exact_text(transaction)
            message = render_travel_arrival(exact_descriptor)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None, "specific_locator_unresolved"
        return {
            "initiative_id": initiative["initiative_id"],
            "visit_id": visit["visit_id"],
            "state": initiative["state"],
            "purpose": initiative["purpose"],
            "template_key": initiative["template_key"],
            "message": message,
            "expires_at": initiative["expires_at"],
        }, "eligible"

    async def claim_initiative(
        self,
        principal: dict[str, Any],
        initiative_id: uuid.UUID,
        value: InitiativeClaim,
    ) -> InitiativeView:
        self._require_rollout("canary", "private_initiatives")
        if value.surface != "private_tauri":
            raise ForbiddenError("private location initiatives are Tauri-only")
        now = datetime.now(UTC)
        blocked_kind: str | None = None
        blocked_reason = "initiative_unavailable"
        result: dict[str, Any] | None = None
        async with self.database.transaction(
            principal_id=principal["principal_id"], serializable=True
        ) as connection:
            directives = await self._active_directives(
                connection, principal["person_id"]
            )
            if directives & {
                "ignored",
                "silent",
                "private",
                "do_not_track",
                "auto_expire",
            }:
                await connection.execute(
                    update(schema.initiatives)
                    .where(
                        schema.initiatives.c.principal_id == principal["principal_id"],
                        schema.initiatives.c.state.in_(["pending", "claimed"]),
                    )
                    .values(state="suppressed", suppression_reason="privacy_directive")
                )
                blocked_kind = "privacy"
                blocked_reason = "privacy_directive"
            initiative = (
                (
                    await connection.execute(
                        select(schema.initiatives)
                        .where(
                            schema.initiatives.c.initiative_id == initiative_id,
                            schema.initiatives.c.principal_id
                            == principal["principal_id"],
                            schema.initiatives.c.state == "pending",
                            schema.initiatives.c.expires_at > now,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if blocked_kind is None and initiative is None:
                blocked_kind = "conflict"
                blocked_reason = "initiative_unavailable"
            if blocked_kind is None and initiative is not None:
                context, reason = await self._initiative_presentation_context(
                    connection,
                    principal_id=principal["principal_id"],
                    initiative=dict(initiative),
                    now=now,
                )
                if context is None:
                    blocked_kind = "conflict"
                    blocked_reason = reason
                    await connection.execute(
                        update(schema.initiatives)
                        .where(schema.initiatives.c.initiative_id == initiative_id)
                        .values(state="suppressed", suppression_reason=reason[:64])
                    )
                else:
                    await connection.execute(
                        update(schema.initiatives)
                        .where(
                            schema.initiatives.c.initiative_id == initiative_id,
                            schema.initiatives.c.state == "pending",
                        )
                        .values(
                            state="claimed",
                            claimed_at=now,
                            claimed_by_session=value.session_id,
                        )
                    )
                    await connection.execute(
                        insert(schema.presentation_attempts).values(
                            attempt_id=uuid7(),
                            initiative_id=initiative_id,
                            session_id=value.session_id,
                            surface=value.surface,
                            outcome="claimed",
                        )
                    )
                    context["state"] = "claimed"
                    result = context
        if blocked_kind == "privacy":
            raise ForbiddenError("initiative suppressed by privacy directive")
        if blocked_kind is not None or result is None:
            raise ConflictError(f"initiative presentation denied: {blocked_reason}")
        return InitiativeView(**result)

    async def snapshot(self, principal: dict[str, Any]) -> AgentSnapshot:
        now = datetime.now(UTC)
        async with self.database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            directives = await self._active_directives(
                connection, principal["person_id"]
            )
            if directives & {"ignored", "silent", "auto_expire"}:
                raise ForbiddenError("principal is excluded from normal retrieval")
            preferences = {
                row["key"]: bool(row["enabled"])
                for row in (
                    await connection.execute(
                        select(
                            schema.preferences.c.key, schema.preferences.c.enabled
                        ).where(
                            schema.preferences.c.principal_id
                            == principal["principal_id"]
                        )
                    )
                ).mappings()
            }
            visit = (
                (
                    await connection.execute(
                        select(
                            schema.visits.c.visit_id,
                            schema.visits.c.place_id,
                            schema.visits.c.state,
                            schema.visits.c.freshness,
                            schema.visits.c.coverage,
                            func.lower(schema.visits.c.observed_range).label(
                                "observed_from"
                            ),
                            func.upper(schema.visits.c.observed_range).label(
                                "observed_to"
                            ),
                        )
                        .where(
                            schema.visits.c.principal_id == principal["principal_id"]
                        )
                        .order_by(func.upper(schema.visits.c.observed_range).desc())
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
        latest_visit = (
            None if "do_not_track" in directives else (dict(visit) if visit else None)
        )
        if latest_visit:
            effective_freshness = freshness_at(latest_visit["observed_to"], now=now)
            latest_visit["freshness"] = effective_freshness
            if effective_freshness == "stale" and latest_visit["state"] == "visiting":
                latest_visit["state"] = "candidate"
        return AgentSnapshot(
            as_of=now,
            principal_id=principal["principal_id"],
            person_id=principal["person_id"],
            preferences=preferences,
            latest_visit=latest_visit,
            # Coverage intervals are source-operational records and currently
            # have no defensible principal ownership for sequence-only edge
            # gap markers. Never leak their entity/times through a principal
            # snapshot. A later ownership projection can add a scoped summary.
            pending_initiatives=[],
            coverage_gaps=[],
            capabilities={
                "persistent_memory": (
                    "enabled" if self.settings.rollout_mode == "canary" else "disabled"
                ),
                "location_visits": "enabled"
                if preferences.get("location_memory", False)
                else "disabled",
                # Installation attestation narrows native transport; it does
                # not authorize initiative presentation. The store retains
                # synthetic domain methods for future review, but deployed
                # API routes and capability discovery stay locked off.
                "private_initiatives": "disabled",
                "physical_actions": "disabled",
                "active_room": "disabled",
                "learning": "disabled",
                "vjepa": "disabled",
            },
        )

    async def list_initiatives(
        self, principal: dict[str, Any]
    ) -> list[InitiativeSummaryView]:
        self._require_rollout("canary", "private_initiatives")
        now = datetime.now(UTC)
        async with self.database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            directives = await self._active_directives(
                connection, principal["person_id"]
            )
            if directives & {
                "ignored",
                "silent",
                "private",
                "do_not_track",
                "auto_expire",
            }:
                await connection.execute(
                    update(schema.initiatives)
                    .where(
                        schema.initiatives.c.principal_id == principal["principal_id"],
                        schema.initiatives.c.state.in_(["pending", "claimed"]),
                    )
                    .values(state="suppressed", suppression_reason="privacy_directive")
                )
                return []
            rows = (
                (
                    await connection.execute(
                        select(
                            schema.initiatives,
                        )
                        .where(
                            schema.initiatives.c.principal_id
                            == principal["principal_id"],
                            schema.initiatives.c.state == "pending",
                            schema.initiatives.c.expires_at > now,
                        )
                        .order_by(schema.initiatives.c.created_at)
                    )
                )
                .mappings()
                .all()
            )
            eligible: list[InitiativeSummaryView] = []
            for row in rows:
                context, _reason = await self._initiative_presentation_context(
                    connection,
                    principal_id=principal["principal_id"],
                    initiative=dict(row),
                    now=now,
                )
                if context is not None:
                    eligible.append(
                        InitiativeSummaryView(
                            initiative_id=context["initiative_id"],
                            expires_at=context["expires_at"],
                        )
                    )
        return eligible

    async def explain_descriptor_relationship(
        self,
        principal: dict[str, Any],
        place_id: uuid.UUID,
    ) -> DescriptorRelationshipView:
        self._require_rollout("canary", "descriptor_relationship_query")
        async with self.database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            directives = await self._active_directives(
                connection, principal["person_id"]
            )
            if directives & {"ignored", "silent", "auto_expire"}:
                raise ForbiddenError("principal is excluded from normal retrieval")
            place_exists = (
                await connection.execute(
                    select(schema.places.c.place_id).where(
                        schema.places.c.place_id == place_id,
                        schema.places.c.owner_principal_id == principal["principal_id"],
                        schema.places.c.place_type == "property",
                        schema.places.c.status == "active",
                    )
                )
            ).scalar_one_or_none()
            if place_exists is None:
                raise NotFoundError("specific private place does not exist")
            principal_name = (
                await connection.execute(
                    select(schema.people.c.display_name).where(
                        schema.people.c.person_id == principal["person_id"],
                        schema.people.c.status == "active",
                    )
                )
            ).scalar_one()
            parent_ids, hidden_parent_edges = await self._confirmed_parent_resolution(
                connection, principal["person_id"]
            )
            parent_rows = (
                (
                    await connection.execute(
                        select(
                            schema.people.c.person_id,
                            schema.people.c.display_name,
                        ).where(
                            schema.people.c.person_id.in_(parent_ids),
                            schema.people.c.status == "active",
                        )
                    )
                )
                .mappings()
                .all()
            )
            names = {row["person_id"]: row["display_name"] for row in parent_rows}
            resolved = [
                ResolvedPersonView(person_id=value, display_name=names[value])
                for value in parent_ids
                if value in names
            ]
            descriptors = (
                (
                    await connection.execute(
                        select(schema.fact_versions).where(
                            schema.fact_versions.c.subject_type == "place",
                            schema.fact_versions.c.subject_id == place_id,
                            schema.fact_versions.c.predicate
                            == "place_social_descriptor",
                            schema.fact_versions.c.perspective_principal_id
                            == principal["principal_id"],
                            schema.fact_versions.c.authority == "explicit_subject",
                            schema.fact_versions.c.support == "explicit_authority",
                            schema.fact_versions.c.contradiction == "none",
                            schema.fact_versions.c.resolution == "accepted",
                            func.upper_inf(schema.fact_versions.c.system_range),
                        )
                    )
                )
                .mappings()
                .all()
            )
            if len(descriptors) > 1:
                raise ConflictError("specific place has multiple active descriptors")
            if not descriptors:
                return DescriptorRelationshipView(
                    place_id=place_id,
                    descriptor_state="unavailable",
                    role_expression="parents_of(self)",
                    role_resolution="unresolved",
                    resolved_parents=resolved,
                    explanation=(
                        "No active confirmed place descriptor is available; "
                        "no place/person relationship is inferred."
                    ),
                    not_asserted=list(PROHIBITED_ASSERTIONS),
                )
            descriptor = dict(descriptors[0])
            descriptor_object = dict(descriptor["object"])
            if (
                descriptor_object.get("descriptor_code") != "parents_mountain_house"
                or descriptor_object.get("role_expression") != "parents_of(self)"
            ):
                raise ConflictError("active descriptor has an unsupported type")
            transaction = await self._memory_transaction_for_fact(
                connection, descriptor, lock=False
            )
            if transaction["state"] != "committed":
                raise ConflictError("descriptor memory is not committed")
            exact_descriptor = self._open_memory_exact_text(transaction)
        if resolved and not hidden_parent_edges:
            resolved_text = ", ".join(person.display_name for person in resolved)
            explanation = (
                f'"{exact_descriptor}" is {principal_name}\u2019s '
                f"perspective-scoped descriptor. parents_of({principal_name}) "
                f"currently resolves to {resolved_text} through independently "
                "confirmed parent facts. It does not assert ownership, residence, "
                "or current presence."
            )
        elif resolved:
            resolved_text = ", ".join(person.display_name for person in resolved)
            explanation = (
                f'"{exact_descriptor}" is {principal_name}\u2019s '
                f"perspective-scoped descriptor. The visible portion of "
                f"parents_of({principal_name}) includes {resolved_text}, but the "
                "role remains unresolved under current privacy policy. No hidden "
                "identity or additional relationship is inferred."
            )
        else:
            explanation = (
                f'"{exact_descriptor}" is {principal_name}\u2019s '
                "perspective-scoped descriptor, but parents_of(self) is currently "
                "unresolved. No names or additional relationship are invented."
            )
        return DescriptorRelationshipView(
            place_id=place_id,
            descriptor_fact_id=descriptor["fact_id"],
            descriptor_state="active_confirmed",
            exact_descriptor=exact_descriptor,
            role_expression="parents_of(self)",
            role_resolution=(
                "complete" if resolved and not hidden_parent_edges else "unresolved"
            ),
            resolved_parents=resolved,
            explanation=explanation,
            not_asserted=list(PROHIBITED_ASSERTIONS),
        )

    async def query_parent_presence(
        self,
        principal: dict[str, Any],
        place_id: uuid.UUID,
    ) -> ParentPresenceView:
        self._require_rollout("canary", "current_parent_presence_query")
        now = datetime.now(UTC)
        async with self.database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            directives = await self._active_directives(
                connection, principal["person_id"]
            )
            if directives & {"ignored", "silent", "auto_expire"}:
                raise ForbiddenError("principal is excluded from normal retrieval")
            place_exists = (
                await connection.execute(
                    select(schema.places.c.place_id).where(
                        schema.places.c.place_id == place_id,
                        schema.places.c.owner_principal_id == principal["principal_id"],
                        schema.places.c.place_type == "property",
                        schema.places.c.status == "active",
                    )
                )
            ).scalar_one_or_none()
            if place_exists is None:
                raise NotFoundError("specific private place does not exist")
            parent_ids, hidden_parent_edges = await self._confirmed_parent_resolution(
                connection, principal["person_id"]
            )
            parent_rows = (
                (
                    await connection.execute(
                        select(
                            schema.people.c.person_id,
                            schema.people.c.display_name,
                        ).where(
                            schema.people.c.person_id.in_(parent_ids),
                            schema.people.c.status == "active",
                        )
                    )
                )
                .mappings()
                .all()
            )
            names = {row["person_id"]: row["display_name"] for row in parent_rows}
            people: list[ParentPresencePersonView] = []
            for parent_id in parent_ids:
                if parent_id not in names:
                    continue
                parent_directives = await self._active_directives(connection, parent_id)
                if parent_directives & {
                    "do_not_track",
                    "ignored",
                    "silent",
                    "private",
                    "auto_expire",
                }:
                    people.append(
                        ParentPresencePersonView(
                            person_id=parent_id,
                            display_name=names[parent_id],
                            resolution="unknown",
                            freshness="not_applicable",
                            coverage="not_applicable",
                            independent_evidence_domains=[],
                            reason_code="privacy_directive_blocks_presence",
                        )
                    )
                    continue
                candidates = (
                    (
                        await connection.execute(
                            select(schema.fact_versions).where(
                                schema.fact_versions.c.subject_type == "person",
                                schema.fact_versions.c.subject_id == parent_id,
                                schema.fact_versions.c.predicate
                                == "current_presence_at_place",
                                schema.fact_versions.c.object.contains(
                                    {"place_id": str(place_id)}
                                ),
                                schema.fact_versions.c.perspective_principal_id
                                == principal["principal_id"],
                                schema.fact_versions.c.authority == "sensor",
                                schema.fact_versions.c.support
                                == "multiple_independent_roots",
                                schema.fact_versions.c.contradiction == "none",
                                schema.fact_versions.c.freshness == "fresh",
                                schema.fact_versions.c.coverage == "sufficient",
                                schema.fact_versions.c.resolution == "accepted",
                                schema.fact_versions.c.committed_at
                                >= now - timedelta(minutes=15),
                                schema.fact_versions.c.committed_at <= now,
                                schema.fact_versions.c.valid_range.contains(now),
                                func.upper_inf(schema.fact_versions.c.system_range),
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                independent_domains: list[str] = []
                for candidate in candidates:
                    supports = (
                        await connection.execute(
                            select(
                                schema.fact_support.c.root_observation_id,
                                schema.fact_support.c.dependency_domain,
                            ).where(
                                schema.fact_support.c.fact_version_id
                                == candidate["fact_version_id"],
                                schema.fact_support.c.root_observation_id.is_not(None),
                                schema.fact_support.c.dependency_domain.is_not(None),
                            )
                        )
                    ).all()
                    roots = {row[0] for row in supports}
                    domains = {str(row[1]) for row in supports}
                    if len(roots) >= 2 and len(domains) >= 2:
                        independent_domains = sorted(domains)
                        break
                present = bool(independent_domains)
                people.append(
                    ParentPresencePersonView(
                        person_id=parent_id,
                        display_name=names[parent_id],
                        resolution="present" if present else "unknown",
                        freshness="fresh" if present else "not_applicable",
                        coverage="sufficient" if present else "not_applicable",
                        independent_evidence_domains=independent_domains,
                        reason_code=(
                            "fresh_independent_presence_evidence"
                            if present
                            else "fresh_independent_evidence_unavailable"
                        ),
                    )
                )
        all_present = (
            not hidden_parent_edges
            and bool(people)
            and all(person.resolution == "present" for person in people)
        )
        return ParentPresenceView(
            place_id=place_id,
            role_expression="parents_of(self)",
            resolution="present" if all_present else "unknown",
            people=people,
            reason_code=(
                "all_resolved_parents_present"
                if all_present
                else "role_visibility_restricted"
                if hidden_parent_edges
                else "role_unresolved"
                if not people
                else "fresh_independent_evidence_unavailable"
            ),
            not_evidence=[
                "place social descriptor",
                "parent relationship facts",
                "the principal\u2019s device location",
            ],
        )

    async def inspect_memory(
        self, principal: dict[str, Any], transaction_id: uuid.UUID
    ) -> MemoryInspection:
        async with self.database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            transaction = (
                (
                    await connection.execute(
                        select(schema.memory_transactions).where(
                            schema.memory_transactions.c.transaction_id
                            == transaction_id,
                            schema.memory_transactions.c.principal_id
                            == principal["principal_id"],
                        )
                    )
                )
                .mappings()
                .first()
            )
            if transaction is None:
                raise NotFoundError("memory transaction does not exist")
            fact_ids = (
                (
                    await connection.execute(
                        select(schema.fact_versions.c.fact_id).where(
                            schema.fact_versions.c.memory_transaction_id
                            == transaction_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        return MemoryInspection(
            transaction_id=transaction_id,
            kind=transaction["kind"],
            state=transaction["state"],
            preview=dict(transaction["preview"]),
            verifier_results=list(transaction["verifier_results"]),
            fact_ids=list(fact_ids),
            created_at=transaction["created_at"],
            confirmed_at=transaction["confirmed_at"],
        )

    async def inspect_erasure(
        self, principal: dict[str, Any], request_id: uuid.UUID
    ) -> ErasureView:
        async with self.database.transaction(
            principal_id=principal["principal_id"]
        ) as connection:
            request = (
                (
                    await connection.execute(
                        select(schema.erasure_requests).where(
                            schema.erasure_requests.c.erasure_request_id == request_id,
                            schema.erasure_requests.c.principal_id
                            == principal["principal_id"],
                        )
                    )
                )
                .mappings()
                .first()
            )
            if request is None:
                raise NotFoundError("erasure request does not exist")
            tasks = (
                (
                    await connection.execute(
                        select(
                            schema.erasure_tasks.c.operation_code,
                            schema.erasure_tasks.c.state,
                            schema.erasure_tasks.c.residual_reason,
                        ).where(schema.erasure_tasks.c.erasure_request_id == request_id)
                    )
                )
                .mappings()
                .all()
            )
        live_erased = [
            row["operation_code"] for row in tasks if row["state"] == "complete"
        ]
        external_pending = [
            row["operation_code"]
            for row in tasks
            if row["state"] not in {"complete", "resolved"}
        ]
        if request["state"] == "ledger_pending":
            external_pending.append("erasure_ledger_receipt")
        return ErasureView(
            erasure_request_id=request_id,
            state=request["state"],
            live_erased=live_erased,
            external_pending=external_pending,
            preserved=list(request["scope"].get("preserve", [])),
            legacy_untracked=list(request["scope"].get("legacy_untracked", [])),
            checkpoint_affected=bool(request["scope"].get("checkpoint_affected", True)),
            backup_expiry_at=request["scope"].get("backup_expiry_at"),
            exact_residuals=list(request["scope"].get("exact_residuals", [])),
        )

    async def preview_forget_descriptor(
        self,
        principal: dict[str, Any],
        fact_id: uuid.UUID,
    ) -> ForgetPreview:
        self._require_rollout("canary", "persistent_memory")
        request_id = uuid7()
        delete_items = [
            "place social descriptor fact",
            "encrypted exact teaching wording",
            "descriptor retrieval and role-resolution views",
            "descriptor-derived initiatives and generated explanations",
        ]
        preserve = [
            "place and encrypted locator",
            "visit history and Itaipava locality",
            "independently confirmed parent facts",
        ]
        payload = {
            "erasure_request_id": str(request_id),
            "descriptor_fact_id": str(fact_id),
            "delete": delete_items,
            "preserve": preserve,
            "legacy_untracked": [],
            "checkpoint_affected": True,
            "backup_expiry_at": None,
            "exact_residuals": ["backup_expiry_unverified"],
        }
        digest = sha256_json(payload)
        async with self.database.transaction(
            principal_id=principal["principal_id"], serializable=True
        ) as connection:
            await self._active_descriptor_fact(
                connection,
                principal_id=principal["principal_id"],
                fact_id=fact_id,
                accepted_only=False,
                lock=True,
            )
            await connection.execute(
                insert(schema.erasure_requests).values(
                    erasure_request_id=request_id,
                    principal_id=principal["principal_id"],
                    scope={**payload, "preview_digest": digest},
                    state="preview",
                    policy_digest=self.settings.policy_digest,
                )
            )
        return ForgetPreview(
            erasure_request_id=request_id,
            descriptor_fact_id=fact_id,
            delete=delete_items,
            preserve=preserve,
            preview_digest=digest,
        )

    async def confirm_forget(
        self,
        principal: dict[str, Any],
        request_id: uuid.UUID,
        value: ForgetConfirm,
    ) -> ErasureView:
        self._require_rollout("canary", "persistent_memory")
        fact_id, already_complete = await self._block_forget_retrieval(
            principal, request_id, value
        )
        if already_complete:
            return await self.inspect_erasure(principal, request_id)
        try:
            await self._cleanup_blocked_forget(principal, request_id, fact_id, value)
        except Exception as exc:
            await self._record_forget_cleanup_failure(
                principal, request_id, fact_id, type(exc).__name__
            )
            raise
        return await self.inspect_erasure(principal, request_id)

    async def _block_forget_retrieval(
        self,
        principal: dict[str, Any],
        request_id: uuid.UUID,
        value: ForgetConfirm,
    ) -> tuple[uuid.UUID, bool]:
        """Commit the retrieval block before any fallible lineage cleanup."""

        async with self.database.transaction(
            principal_id=principal["principal_id"], serializable=True
        ) as connection:
            request = (
                (
                    await connection.execute(
                        select(schema.erasure_requests)
                        .where(
                            schema.erasure_requests.c.erasure_request_id == request_id,
                            schema.erasure_requests.c.principal_id
                            == principal["principal_id"],
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if request is None:
                raise NotFoundError("erasure request does not exist")
            if request["state"] in {"ledger_pending", "complete"}:
                return uuid.UUID(request["scope"]["descriptor_fact_id"]), True
            if request["state"] not in {"preview", "blocked", "failed"}:
                raise ConflictError(f"erasure request is {request['state']}")
            expected = request["scope"]["preview_digest"]
            if not hmac.compare_digest(expected, value.preview_digest):
                raise ConflictError("erasure preview digest mismatch")
            fact_id = uuid.UUID(request["scope"]["descriptor_fact_id"])
            if request["state"] == "preview":
                await self._active_descriptor_fact(
                    connection,
                    principal_id=principal["principal_id"],
                    fact_id=fact_id,
                    accepted_only=False,
                    lock=True,
                )
                await connection.execute(
                    insert(schema.retrieval_blocks)
                    .values(
                        block_id=uuid7(),
                        artifact_id=fact_id,
                        erasure_request_id=request_id,
                    )
                    .on_conflict_do_nothing(index_elements=["artifact_id"])
                )
                await connection.execute(
                    update(schema.erasure_requests)
                    .where(schema.erasure_requests.c.erasure_request_id == request_id)
                    .values(state="blocked")
                )
            else:
                block = (
                    await connection.execute(
                        select(schema.retrieval_blocks.c.block_id).where(
                            schema.retrieval_blocks.c.artifact_id == fact_id,
                            schema.retrieval_blocks.c.erasure_request_id == request_id,
                        )
                    )
                ).scalar_one_or_none()
                if block is None:
                    raise ConflictError(
                        "erasure cleanup has no durable retrieval block"
                    )
        return fact_id, False

    async def _cleanup_blocked_forget(
        self,
        principal: dict[str, Any],
        request_id: uuid.UUID,
        fact_id: uuid.UUID,
        value: ForgetConfirm,
    ) -> None:
        now = datetime.now(UTC)
        async with self.database.transaction(
            principal_id=principal["principal_id"], serializable=True
        ) as connection:
            request = (
                (
                    await connection.execute(
                        select(schema.erasure_requests)
                        .where(
                            schema.erasure_requests.c.erasure_request_id == request_id,
                            schema.erasure_requests.c.principal_id
                            == principal["principal_id"],
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if request is None:
                raise NotFoundError("erasure request does not exist")
            if request["state"] == "complete":
                return
            if request["state"] not in {"blocked", "failed"}:
                raise ConflictError(f"erasure request is {request['state']}")
            if not hmac.compare_digest(
                request["scope"]["preview_digest"], value.preview_digest
            ):
                raise ConflictError("erasure preview digest mismatch")
            await connection.execute(
                update(schema.erasure_requests)
                .where(schema.erasure_requests.c.erasure_request_id == request_id)
                .values(state="running")
            )
            await connection.execute(
                update(schema.erasure_tasks)
                .where(
                    schema.erasure_tasks.c.erasure_request_id == request_id,
                    schema.erasure_tasks.c.operation_code == "cleanup_failed",
                    schema.erasure_tasks.c.state == "failed",
                )
                .values(state="resolved")
            )
            await apply_descriptor_erasure(
                connection,
                principal_id=principal["principal_id"],
                fact_id=fact_id,
                erasure_request_id=request_id,
                now=now,
                require_existing=True,
            )
            operation_codes = (
                "scrub_descriptor_fact",
                "scrub_exact_wording",
                "invalidate_derived_views",
                "suppress_descriptor_initiatives",
            )
            for operation in operation_codes:
                await connection.execute(
                    insert(schema.erasure_tasks).values(
                        task_id=uuid7(),
                        erasure_request_id=request_id,
                        artifact_id=fact_id,
                        operation_code=operation,
                        state="complete",
                    )
                )
            await connection.execute(
                update(schema.erasure_requests)
                .where(schema.erasure_requests.c.erasure_request_id == request_id)
                .values(state="ledger_pending", completed_at=now)
            )
            await connection.execute(
                insert(schema.outbox).values(
                    outbox_id=uuid7(),
                    topic="privacy.erasure.completed",
                    aggregate_id=fact_id,
                    payload={
                        "version": 1,
                        "erasure_request_id": str(request_id),
                        "principal_id": str(principal["principal_id"]),
                        "fact_id": str(fact_id),
                        "operation_codes": list(operation_codes),
                        "policy_digest": self.settings.policy_digest,
                        "completed_at": now.isoformat(),
                        "external_pending_codes": [],
                        "legacy_untracked_codes": [],
                        "checkpoint_affected": True,
                        "backup_expiry_at": None,
                        "exact_residual_codes": ["backup_expiry_unverified"],
                    },
                )
            )

    async def _record_forget_cleanup_failure(
        self,
        principal: dict[str, Any],
        request_id: uuid.UUID,
        fact_id: uuid.UUID,
        error_class: str,
    ) -> None:
        residual = f"cleanup_error_{error_class.lower()}"[:128]
        async with self.database.transaction(
            principal_id=principal["principal_id"], serializable=True
        ) as connection:
            request = (
                await connection.execute(
                    select(schema.erasure_requests.c.state)
                    .where(
                        schema.erasure_requests.c.erasure_request_id == request_id,
                        schema.erasure_requests.c.principal_id
                        == principal["principal_id"],
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if request is None or request == "complete":
                return
            await connection.execute(
                update(schema.erasure_requests)
                .where(schema.erasure_requests.c.erasure_request_id == request_id)
                .values(state="failed")
            )
            await connection.execute(
                insert(schema.erasure_tasks).values(
                    task_id=uuid7(),
                    erasure_request_id=request_id,
                    artifact_id=fact_id,
                    operation_code="cleanup_failed",
                    state="failed",
                    residual_reason=residual,
                )
            )

    async def _mint_authenticated_confirmation(
        self,
        connection,
        *,
        principal_id: uuid.UUID,
        purpose: str,
        proposal_digest: str,
        client_nonce: uuid.UUID,
        operation_time: datetime | None = None,
    ) -> uuid.UUID:
        """Mint and consume server authority for one authenticated gesture.

        The client UUID is only a one-time nonce.  It never becomes support
        authority and its raw value is not retained.
        """

        artifact_id = uuid7()
        now = operation_time or datetime.now(UTC)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("confirmation operation time must be timezone-aware")
        now = now.astimezone(UTC)
        nonce_digest = hashlib.sha256(client_nonce.bytes).hexdigest()
        existing = (
            await connection.execute(
                select(schema.confirmation_artifacts.c.artifact_id).where(
                    schema.confirmation_artifacts.c.principal_id == principal_id,
                    schema.confirmation_artifacts.c.client_nonce_sha256 == nonce_digest,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("confirmation gesture nonce was already consumed")
        await connection.execute(
            insert(schema.confirmation_artifacts).values(
                artifact_id=artifact_id,
                principal_id=principal_id,
                purpose=purpose,
                proposal_digest=proposal_digest,
                client_nonce_sha256=nonce_digest,
                issued_at=now,
                expires_at=now + timedelta(minutes=5),
                consumed_at=now,
            )
        )
        await connection.execute(
            insert(schema.artifact_registry).values(
                artifact_id=artifact_id,
                artifact_kind="authenticated_confirmation",
                store="postgresql",
                content_sha256=proposal_digest,
                owner_principal_id=principal_id,
                retention_class="governed_history",
            )
        )
        return artifact_id

    async def _confirmed_parents(
        self, connection, child_person_id: uuid.UUID
    ) -> list[uuid.UUID]:
        visible, _hidden = await self._confirmed_parent_resolution(
            connection, child_person_id
        )
        return visible

    async def _confirmed_parent_resolution(
        self, connection, child_person_id: uuid.UUID
    ) -> tuple[list[uuid.UUID], bool]:
        rows = (
            (
                await connection.execute(
                    select(schema.fact_versions.c.subject_id).where(
                        schema.fact_versions.c.predicate == "parent_of",
                        schema.fact_versions.c.object.contains(
                            {"person_id": str(child_person_id)}
                        ),
                        func.upper_inf(schema.fact_versions.c.system_range),
                        schema.fact_versions.c.resolution == "accepted",
                    )
                )
            )
            .scalars()
            .all()
        )
        visible: list[uuid.UUID] = []
        hidden = False
        current_principal = (
            await connection.execute(
                select(func.nullif(func.current_setting("app.principal_id", True), ""))
            )
        ).scalar_one_or_none()
        current_person = None
        if current_principal:
            current_person = (
                await connection.execute(
                    select(schema.principals.c.person_id).where(
                        schema.principals.c.principal_id == uuid.UUID(current_principal)
                    )
                )
            ).scalar_one_or_none()
        for parent_id in rows:
            status = (
                await connection.execute(
                    select(schema.people.c.status).where(
                        schema.people.c.person_id == parent_id
                    )
                )
            ).scalar_one_or_none()
            if status != "active":
                hidden = True
                continue
            directives = await self._active_directives(connection, parent_id)
            if directives & {"ignored", "silent", "auto_expire"}:
                hidden = True
                continue
            if "private" in directives and current_person != parent_id:
                hidden = True
                continue
            visible.append(parent_id)
        return list(dict.fromkeys(visible)), hidden

    async def _ensure_auto_expiry_schedule(
        self,
        connection,
        *,
        person_id: uuid.UUID,
        directive_id: uuid.UUID,
        due_at: datetime | None,
    ) -> None:
        if due_at is None:
            raise ConflictError("auto-expiry schedule requires due_at")
        schedule_id = uuid7()
        outbox_id = uuid7()
        inserted = (
            await connection.execute(
                insert(schema.auto_expiry_schedules)
                .values(
                    schedule_id=schedule_id,
                    person_id=person_id,
                    directive_id=directive_id,
                    outbox_id=outbox_id,
                    due_at=due_at,
                    state="scheduled",
                )
                .on_conflict_do_nothing(constraint="person_auto_expiry_schedule")
                .returning(schema.auto_expiry_schedules.c.schedule_id)
            )
        ).scalar_one_or_none()
        if inserted is None:
            existing = (
                (
                    await connection.execute(
                        select(schema.auto_expiry_schedules)
                        .where(schema.auto_expiry_schedules.c.person_id == person_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if any(
                (
                    existing["directive_id"] != directive_id,
                    existing["due_at"] != due_at,
                    existing["state"] not in {"scheduled", "complete"},
                )
            ):
                raise ConflictError("auto-expiry schedule projection drifted")
            return
        await connection.execute(
            insert(schema.outbox).values(
                outbox_id=outbox_id,
                topic="privacy.person.auto_expire",
                aggregate_id=person_id,
                payload={
                    "version": 1,
                    "schedule_id": str(schedule_id),
                    "person_id": str(person_id),
                    "directive_id": str(directive_id),
                    "policy_digest": self.settings.policy_digest,
                },
                available_at=due_at,
            )
        )

    @staticmethod
    async def _active_directives(connection, person_id: uuid.UUID) -> set[str]:
        now = datetime.now(UTC)
        values = (
            (
                await connection.execute(
                    select(schema.privacy_directives.c.directive).where(
                        schema.privacy_directives.c.person_id == person_id,
                        schema.privacy_directives.c.enabled.is_(True),
                        or_(
                            and_(
                                schema.privacy_directives.c.directive == "auto_expire",
                                schema.privacy_directives.c.expires_at <= now,
                            ),
                            and_(
                                schema.privacy_directives.c.directive != "auto_expire",
                                or_(
                                    schema.privacy_directives.c.expires_at.is_(None),
                                    schema.privacy_directives.c.expires_at > now,
                                ),
                            ),
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        return set(values)

    @staticmethod
    async def _binding_blocking_directives(
        connection, person_id: uuid.UUID
    ) -> set[str]:
        """Return binding blockers, including scheduled future auto-expiry.

        A scheduled erasure is an immediate fail-closed identity directive for
        account binding even though other time-aware projections may remain
        usable until the due time.
        """

        values = (
            (
                await connection.execute(
                    select(schema.privacy_directives.c.directive).where(
                        schema.privacy_directives.c.person_id == person_id,
                        schema.privacy_directives.c.enabled.is_(True),
                        schema.privacy_directives.c.directive.in_(
                            ("auto_expire", "do_not_track", "ignored", "silent")
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        return set(values)

    async def _person_has_any_directive(
        self,
        connection,
        *,
        principal_id: uuid.UUID,
        directives: set[str],
    ) -> bool:
        person_id = (
            await connection.execute(
                select(schema.principals.c.person_id).where(
                    schema.principals.c.principal_id == principal_id
                )
            )
        ).scalar_one_or_none()
        if person_id is None:
            return True
        return bool((await self._active_directives(connection, person_id)) & directives)

    @staticmethod
    def _verifier_receipts(
        results,
        *,
        input_digest: str,
        phase: str,
    ) -> list[dict[str, str]]:
        return [
            {
                **result.as_dict(),
                "phase": phase,
                "rule_version": "home-agent-memory-verifier-v1",
                "input_digest": input_digest,
                "order": str(index),
            }
            for index, result in enumerate(results, start=1)
        ]

    def _preview_digest(self, payload: dict[str, Any]) -> str:
        return hmac.new(
            self._preview_digest_key,
            canonical_json(payload),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    async def _preference_enabled(
        connection, principal_id: uuid.UUID, key: str
    ) -> bool:
        value = (
            await connection.execute(
                select(schema.preferences.c.enabled).where(
                    schema.preferences.c.principal_id == principal_id,
                    schema.preferences.c.key == key,
                )
            )
        ).scalar_one_or_none()
        return bool(value)

    @staticmethod
    def _visit_is_current(visit: Any, now: datetime) -> bool:
        observed_to = visit["observed_range"].upper
        if observed_to is None:
            return False
        age = now.astimezone(UTC) - observed_to.astimezone(UTC)
        return timedelta(0) <= age <= timedelta(minutes=15)
