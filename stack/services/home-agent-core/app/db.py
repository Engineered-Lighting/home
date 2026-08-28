from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import dataclass
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine


@dataclass(frozen=True, slots=True)
class PrincipalBindingKernelCall:
    proposal_id: uuid.UUID
    authenticated_ha_user_id: str
    proposal_digest: str
    confirmation_nonce: uuid.UUID
    authority_receipt_id: uuid.UUID
    principal_id: uuid.UUID
    confirmation_artifact_id: uuid.UUID
    binding_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ParentRelationshipStageKernelCall:
    authenticated_ha_user_id: str
    request_id: uuid.UUID
    proposal_id: uuid.UUID
    operator_request_id: uuid.UUID
    proposal_edge_id_0: uuid.UUID
    proposal_edge_id_1: uuid.UUID
    review_code_0: str
    review_code_1: str


@dataclass(frozen=True, slots=True)
class ParentRelationshipStageKernelResult:
    request_id: uuid.UUID
    proposal_id: uuid.UUID
    proposal_digest: str
    expires_at: datetime
    child_display_label: str
    parent_0_display_label: str
    parent_0_review_code: str
    parent_1_display_label: str
    parent_1_review_code: str


@dataclass(frozen=True, slots=True)
class ParentRelationshipCommitKernelCall:
    authenticated_ha_user_id: str
    proposal_id: uuid.UUID
    proposal_digest: str
    confirmation_nonce: uuid.UUID
    confirmation_artifact_id: uuid.UUID
    memory_transaction_id: uuid.UUID
    authority_receipt_id: uuid.UUID
    fact_id_0: uuid.UUID
    fact_version_id_0: uuid.UUID
    confirmation_support_id_0: uuid.UUID
    legacy_support_id_0: uuid.UUID
    receipt_edge_id_0: uuid.UUID
    fact_id_1: uuid.UUID
    fact_version_id_1: uuid.UUID
    confirmation_support_id_1: uuid.UUID
    legacy_support_id_1: uuid.UUID
    receipt_edge_id_1: uuid.UUID


@dataclass(frozen=True, slots=True)
class OwnerPartnerCommitKernelCall:
    """Every identifier is derived, never client-supplied.

    A caller that could choose its own primary keys could collide two rows or
    replay someone else's ceremony, so the adapter hashes them from the
    ceremony seed and the kernel refuses a repeated value.
    """

    authenticated_ha_user_id: str
    ceremony_id: uuid.UUID
    partner_person_id: uuid.UUID
    document_digest: str
    memory_transaction_id: uuid.UUID
    fact_id_self: uuid.UUID
    fact_id_partner: uuid.UUID
    fact_version_id_self: uuid.UUID
    fact_version_id_partner: uuid.UUID
    support_id_self: uuid.UUID
    support_id_partner: uuid.UUID
    receipt_id: uuid.UUID
    receipt_edge_id_0: uuid.UUID
    receipt_edge_id_1: uuid.UUID
    attestation_artifact_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ParentRelationshipStatusKernelResult:
    state: str
    proposal_id: uuid.UUID | None
    proposal_digest: str | None
    expires_at: datetime | None
    child_display_label: str | None
    parent_0_display_label: str | None
    parent_0_review_code: str | None
    parent_1_display_label: str | None
    parent_1_review_code: str | None
    confirmed_at: datetime | None
    fact_count: int


class PrincipalBindingCommitDatabase:
    """A commit-only pool whose first transaction statement is the E5b kernel."""

    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=0,
            pool_recycle=300,
            hide_parameters=True,
        )

    async def commit(self, value: PrincipalBindingKernelCall) -> datetime:
        async with self.engine.connect() as raw_connection:
            connection = await raw_connection.execution_options(
                isolation_level="SERIALIZABLE"
            )
            async with connection.begin():
                # Keep this as the first statement in the transaction. The
                # security-definer kernel validates the login role, isolation,
                # XID state, current authority, privacy, and complete graph.
                return (
                    await connection.execute(
                        text(
                            "SELECT identity."
                            "commit_authenticated_principal_binding_e5b("
                            ":proposal_id,:ha_user_id,:proposal_digest,"
                            ":confirmation_nonce,:receipt_id,:principal_id,"
                            ":artifact_id,:binding_id)"
                        ),
                        {
                            "proposal_id": value.proposal_id,
                            "ha_user_id": value.authenticated_ha_user_id,
                            "proposal_digest": value.proposal_digest,
                            "confirmation_nonce": value.confirmation_nonce,
                            "receipt_id": value.authority_receipt_id,
                            "principal_id": value.principal_id,
                            "artifact_id": value.confirmation_artifact_id,
                            "binding_id": value.binding_id,
                        },
                    )
                ).scalar_one()

    async def close(self) -> None:
        await self.engine.dispose()


class ParentRelationshipAuthorityDatabase:
    """A table-blind pool exposing only reviewed parent-authority kernels."""

    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=0,
            pool_recycle=300,
            hide_parameters=True,
        )

    async def stage(
        self, value: ParentRelationshipStageKernelCall
    ) -> ParentRelationshipStageKernelResult:
        async with self.engine.connect() as raw_connection:
            connection = await raw_connection.execution_options(
                isolation_level="SERIALIZABLE"
            )
            async with connection.begin():
                row = (
                    (
                        await connection.execute(
                            text(
                                "SELECT * FROM identity."
                                "stage_authenticated_parent_relationship_e5e("
                                "CAST(:ha_user_id AS varchar),:request_id,"
                                ":proposal_id,:operator_request_id,"
                                ":proposal_edge_id_0,:proposal_edge_id_1,"
                                "CAST(:review_code_0 AS varchar),"
                                "CAST(:review_code_1 AS varchar))"
                            ),
                            {
                                "ha_user_id": value.authenticated_ha_user_id,
                                "request_id": value.request_id,
                                "proposal_id": value.proposal_id,
                                "operator_request_id": value.operator_request_id,
                                "proposal_edge_id_0": value.proposal_edge_id_0,
                                "proposal_edge_id_1": value.proposal_edge_id_1,
                                "review_code_0": value.review_code_0,
                                "review_code_1": value.review_code_1,
                            },
                        )
                    )
                    .mappings()
                    .one()
                )
        return ParentRelationshipStageKernelResult(**row)

    async def commit_owner_partner(
        self, value: OwnerPartnerCommitKernelCall
    ) -> uuid.UUID:
        """Record an owner-attested partnership and return its receipt id.

        SERIALIZABLE is not optional: the kernel refuses any other isolation
        level, because its checks read state it then writes against.
        """

        async with self.engine.connect() as raw_connection:
            connection = await raw_connection.execution_options(
                isolation_level="SERIALIZABLE"
            )
            async with connection.begin():
                receipt_id = (
                    await connection.execute(
                        text(
                            "SELECT identity."
                            "commit_owner_partner_relationship_e5k("
                            ":ceremony_id,"
                            "CAST(:ha_user_id AS text),"
                            ":partner_person_id,"
                            "CAST(:document_digest AS text),"
                            ":memory_transaction_id,"
                            ":fact_id_self,:fact_id_partner,"
                            ":fact_version_id_self,:fact_version_id_partner,"
                            ":support_id_self,:support_id_partner,"
                            ":receipt_id,"
                            ":receipt_edge_id_0,:receipt_edge_id_1,"
                            ":attestation_artifact_id)"
                        ),
                        {
                            "ceremony_id": value.ceremony_id,
                            "ha_user_id": value.authenticated_ha_user_id,
                            "partner_person_id": value.partner_person_id,
                            "document_digest": value.document_digest,
                            "memory_transaction_id": value.memory_transaction_id,
                            "fact_id_self": value.fact_id_self,
                            "fact_id_partner": value.fact_id_partner,
                            "fact_version_id_self": value.fact_version_id_self,
                            "fact_version_id_partner": (
                                value.fact_version_id_partner
                            ),
                            "support_id_self": value.support_id_self,
                            "support_id_partner": value.support_id_partner,
                            "receipt_id": value.receipt_id,
                            "receipt_edge_id_0": value.receipt_edge_id_0,
                            "receipt_edge_id_1": value.receipt_edge_id_1,
                            "attestation_artifact_id": (
                                value.attestation_artifact_id
                            ),
                        },
                    )
                ).scalar_one()
        return receipt_id

    async def commit(self, value: ParentRelationshipCommitKernelCall) -> datetime:
        async with self.engine.connect() as raw_connection:
            connection = await raw_connection.execution_options(
                isolation_level="SERIALIZABLE"
            )
            async with connection.begin():
                row = (
                    await connection.execute(
                        text(
                            "SELECT * FROM identity."
                            "commit_authenticated_parent_relationship_e5f("
                            "CAST(:ha_user_id AS varchar),:proposal_id,"
                            "CAST(:proposal_digest AS varchar),"
                            ":confirmation_nonce,:confirmation_artifact_id,"
                            ":memory_transaction_id,:authority_receipt_id,"
                            ":fact_id_0,:fact_version_id_0,"
                            ":confirmation_support_id_0,"
                            ":legacy_support_id_0,:receipt_edge_id_0,"
                            ":fact_id_1,:fact_version_id_1,"
                            ":confirmation_support_id_1,"
                            ":legacy_support_id_1,:receipt_edge_id_1)"
                        ),
                        {
                            "ha_user_id": value.authenticated_ha_user_id,
                            "proposal_id": value.proposal_id,
                            "proposal_digest": value.proposal_digest,
                            "confirmation_nonce": value.confirmation_nonce,
                            "confirmation_artifact_id": (
                                value.confirmation_artifact_id
                            ),
                            "memory_transaction_id": (value.memory_transaction_id),
                            "authority_receipt_id": value.authority_receipt_id,
                            "fact_id_0": value.fact_id_0,
                            "fact_version_id_0": value.fact_version_id_0,
                            "confirmation_support_id_0": (
                                value.confirmation_support_id_0
                            ),
                            "legacy_support_id_0": value.legacy_support_id_0,
                            "receipt_edge_id_0": value.receipt_edge_id_0,
                            "fact_id_1": value.fact_id_1,
                            "fact_version_id_1": value.fact_version_id_1,
                            "confirmation_support_id_1": (
                                value.confirmation_support_id_1
                            ),
                            "legacy_support_id_1": value.legacy_support_id_1,
                            "receipt_edge_id_1": value.receipt_edge_id_1,
                        },
                    )
                ).one()
        return row.committed_at

    async def status(
        self, authenticated_ha_user_id: str
    ) -> ParentRelationshipStatusKernelResult:
        async with self.engine.connect() as raw_connection:
            connection = await raw_connection.execution_options(
                isolation_level="SERIALIZABLE"
            )
            async with connection.begin():
                row = (
                    (
                        await connection.execute(
                            text(
                                "SELECT * FROM identity."
                                "recover_authenticated_parent_relationship_e5h("
                                "CAST(:ha_user_id AS varchar))"
                            ),
                            {"ha_user_id": authenticated_ha_user_id},
                        )
                    )
                    .mappings()
                    .one()
                )
        return ParentRelationshipStatusKernelResult(**row)

    async def close(self) -> None:
        await self.engine.dispose()


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            pool_recycle=1800,
        )

    @asynccontextmanager
    async def transaction(
        self,
        *,
        principal_id: uuid.UUID | None = None,
        ha_user_id: str | None = None,
        binding_operator: bool = False,
        serializable: bool = False,
    ) -> AsyncIterator[AsyncConnection]:
        if binding_operator and (ha_user_id is not None or principal_id is not None):
            raise ValueError(
                "subject and binding-operator database scopes are mutually exclusive"
            )
        async with self.engine.connect() as connection:
            if serializable:
                connection = await connection.execution_options(
                    isolation_level="SERIALIZABLE"
                )
            async with connection.begin():
                if binding_operator:
                    # Custom GUCs are caller-controlled and therefore cannot grant
                    # operator authority.  session_user is the authenticated login
                    # role and is not changed by SECURITY DEFINER functions.  The
                    # owner exception exists only for migrations and integration
                    # tests; production API configuration supplies the isolated
                    # home_agent_binding_operator credential.
                    session_user = (
                        await connection.execute(text("SELECT session_user"))
                    ).scalar_one()
                    if session_user not in {
                        "home_agent_binding_operator",
                        "home_agent_owner",
                    }:
                        raise PermissionError(
                            "database session is not the principal-binding operator"
                        )
                if principal_id is not None:
                    await connection.execute(
                        text(
                            "SELECT set_config('app.principal_id', :principal_id, true)"
                        ),
                        {"principal_id": str(principal_id)},
                    )
                if ha_user_id is not None:
                    if not 1 <= len(ha_user_id) <= 64 or any(
                        ord(character) < 32 for character in ha_user_id
                    ):
                        raise ValueError("HA user ID is invalid for transaction scope")
                    await connection.execute(
                        text("SELECT set_config('app.ha_user_id', :ha_user_id, true)"),
                        {"ha_user_id": ha_user_id},
                    )
                yield connection

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as connection:
                return bool((await connection.execute(text("SELECT 1"))).scalar_one())
        except Exception:
            return False

    async def current_time(self) -> datetime:
        """Return PostgreSQL's wall clock for cross-process safety boundaries."""

        async with self.transaction() as connection:
            return (
                await connection.execute(text("SELECT clock_timestamp()"))
            ).scalar_one()

    async def migration_revision(self) -> str | None:
        try:
            async with self.engine.connect() as connection:
                result = await connection.execute(
                    text("SELECT version_num FROM alembic_version")
                )
                return result.scalar_one_or_none()
        except Exception:
            return None

    async def close(self) -> None:
        await self.engine.dispose()

    async def run_serializable(self, operation, *, max_attempts: int = 3):
        """Retry a complete idempotent domain operation on serialization/deadlock.

        Domain methods generate opaque IDs and keep external effects in the
        transactional outbox, so replaying the operation after PostgreSQL rolls
        back is safe.
        """

        for attempt in range(1, max_attempts + 1):
            try:
                return await operation()
            except DBAPIError as exc:
                sqlstate = getattr(exc.orig, "sqlstate", None)
                if sqlstate not in {"40001", "40P01"} or attempt == max_attempts:
                    raise
                await asyncio.sleep(random.uniform(0.01, 0.05) * attempt)
        raise RuntimeError("unreachable serialization retry state")
