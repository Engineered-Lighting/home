from __future__ import annotations

import asyncio
import os
import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.crypto import sha256_json
from app.db import Database, PrincipalBindingCommitDatabase
from app.ids import uuid7
from app.models import PrincipalBindingConfirmation
from app.principal_binding_adapter import AuthenticatedPrincipalBindingAdapter
from app.store import (
    CoreStore,
    binding_person_snapshot_digest,
    binding_proposal_digest,
)


OWNER_DATABASE_ENV = (
    "TEST_PHASE3_PRINCIPAL_BINDING_KERNEL_E5B_OWNER_DATABASE_URL"
)
BINDING_DATABASE_ENV = (
    "TEST_PHASE3_PRINCIPAL_BINDING_KERNEL_E5B_COMMITTER_DATABASE_URL"
)
OPERATOR_DATABASE_ENV = (
    "TEST_PHASE3_PRINCIPAL_BINDING_ADAPTER_E5C_OPERATOR_DATABASE_URL"
)
HOSTED_GATE_SENTINEL_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_RUN_SENTINEL"
RETAIN_DOWNGRADE_EVIDENCE_ENV = (
    "TEST_PHASE3_PRINCIPAL_BINDING_KERNEL_E5B_RETAIN_DOWNGRADE_EVIDENCE"
)
CLEANUP_DOWNGRADE_EVIDENCE_ENV = (
    "TEST_PHASE3_PRINCIPAL_BINDING_KERNEL_E5B_CLEANUP_DOWNGRADE_EVIDENCE"
)
FUNCTION = "identity.commit_authenticated_principal_binding_e5b"
POLICY_VERSION = "home-agent-mvp-v1"
REVIEW_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass(frozen=True, slots=True)
class StagedBinding:
    promotion_id: uuid.UUID
    run_id: uuid.UUID
    request_id: uuid.UUID
    proposal_id: uuid.UUID
    person_id: uuid.UUID
    ha_user_id: str
    proposal_digest: str
    nonce: uuid.UUID
    receipt_id: uuid.UUID
    principal_id: uuid.UUID
    artifact_id: uuid.UUID
    binding_id: uuid.UUID


def _runtime_configured() -> bool:
    return bool(
        os.getenv(OWNER_DATABASE_ENV) and os.getenv(BINDING_DATABASE_ENV)
    )


def _async_url(value: str):
    return make_url(value).set(drivername="postgresql+psycopg")


def _engine(env_name: str, *, serializable: bool = False) -> AsyncEngine:
    if serializable:
        return create_async_engine(
            _async_url(os.environ[env_name]),
            pool_pre_ping=True,
            hide_parameters=True,
            isolation_level="SERIALIZABLE",
        )
    return create_async_engine(
        _async_url(os.environ[env_name]),
        pool_pre_ping=True,
        hide_parameters=True,
    )


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


def _review_code() -> str:
    return "".join(secrets.choice(REVIEW_ALPHABET) for _ in range(16))


async def _stage_binding(
    owner_engine: AsyncEngine,
    *,
    label: str,
    corrupt_snapshot: bool = False,
    expired: bool = False,
    person_id: uuid.UUID | None = None,
    person_offset: int = 0,
) -> StagedBinding:
    request_id = uuid7()
    proposal_id = uuid7()
    operator_request_id = uuid7()
    nonce = uuid.uuid4()
    receipt_id = uuid7()
    principal_id = uuid7()
    artifact_id = uuid7()
    binding_id = uuid7()
    ha_user_id = f"e5b-runtime-{label}-{uuid.uuid4().hex[:12]}"
    review_code = _review_code()

    async with owner_engine.begin() as connection:
        promotion = (
            await connection.execute(
                text(
                    "SELECT promotion_id, run_id, policy_digest, committed_at "
                    "FROM operations.semantic_authority_promotions "
                    "WHERE authority_scope='identity_semantics'"
                )
            )
        ).one()
        if person_id is None:
            person = (
                await connection.execute(
                    text(
                        "SELECT p.person_id, p.display_name, p.status, "
                        "p.privacy_scope, p.legacy_source_sha256, "
                        "p.status_source_sha256 "
                        "FROM operations."
                        "reviewed_identity_migration_projection_lineage l "
                        "JOIN operations."
                        "reviewed_identity_migration_projection_subjects s "
                        "ON s.lineage_id=l.lineage_id "
                        "JOIN identity.people p ON p.person_id=s.person_id "
                        "WHERE l.run_id=:run_id "
                        "AND l.decision_kind='person' "
                        "AND l.projection_table_kind='identity.people' "
                        "AND l.projection_id=p.person_id "
                        "AND s.subject_role='primary' "
                        "AND p.status='active' "
                        "AND NOT EXISTS ("
                        "SELECT 1 FROM identity.ha_user_bindings b "
                        "WHERE b.person_id=p.person_id "
                        "AND b.revoked_at IS NULL"
                        ") "
                        "ORDER BY p.person_id LIMIT 1 OFFSET :person_offset"
                    ),
                    {
                        "run_id": promotion.run_id,
                        "person_offset": person_offset,
                    },
                )
            ).mappings().one()
        else:
            person = (
                await connection.execute(
                    text(
                        "SELECT person_id, display_name, status, "
                        "privacy_scope, legacy_source_sha256, "
                        "status_source_sha256 FROM identity.people "
                        "WHERE person_id=:person_id AND status='active'"
                    ),
                    {"person_id": person_id},
                )
            ).mappings().one()

        if expired:
            staged_at = promotion.committed_at + timedelta(microseconds=1)
            expires_at = max(
                staged_at + timedelta(microseconds=1),
                datetime.now(UTC) - timedelta(microseconds=1),
            )
        else:
            staged_at = max(
                datetime.now(UTC),
                promotion.committed_at + timedelta(microseconds=1),
            )
            expires_at = staged_at + timedelta(minutes=10)
        requested_at = staged_at - timedelta(minutes=1)
        person_snapshot_digest = binding_person_snapshot_digest(person)
        stored_snapshot_digest = (
            "f" * 64 if corrupt_snapshot else person_snapshot_digest
        )
        stage_receipt_digest = sha256_json(
            {
                "contract": "principal-binding-stage-v1",
                "request_id": str(request_id),
                "review_code": review_code,
                "ha_user_id": ha_user_id,
                "person_id": str(person["person_id"]),
                "reviewed_display_label": person["display_name"],
                "person_snapshot_digest": stored_snapshot_digest,
                "operator_request_id": str(operator_request_id),
                "policy_version": POLICY_VERSION,
                "policy_digest": promotion.policy_digest,
            }
        )
        proposal_digest = binding_proposal_digest(
            request_id=request_id,
            review_code=review_code,
            ha_user_id=ha_user_id,
            person_id=person["person_id"],
            reviewed_display_label=person["display_name"],
            person_snapshot_digest=stored_snapshot_digest,
            operator_request_id=operator_request_id,
            stage_receipt_digest=stage_receipt_digest,
            staged_at=staged_at,
            expires_at=expires_at,
            policy_version=POLICY_VERSION,
            policy_digest=promotion.policy_digest,
        )
        await connection.execute(
            text(
                "INSERT INTO identity.principal_binding_requests("
                "request_id,ha_user_id,review_code,state,requested_at,"
                "expires_at,staged_at,closed_at) "
                "VALUES(:request_id,:ha_user_id,:review_code,'staged',"
                ":requested_at,:expires_at,:staged_at,NULL)"
            ),
            {
                "request_id": request_id,
                "ha_user_id": ha_user_id,
                "review_code": review_code,
                "requested_at": requested_at,
                "expires_at": expires_at,
                "staged_at": staged_at,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO identity.principal_binding_proposals("
                "proposal_id,operator_request_id,request_id,ha_user_id,"
                "person_id,reviewed_display_label,person_snapshot_digest,"
                "proposal_digest,state,stage_receipt_digest,staged_at,"
                "expires_at,consumed_at,result_principal_id,"
                "confirmation_artifact_id) "
                "VALUES(:proposal_id,:operator_request_id,:request_id,"
                ":ha_user_id,:person_id,:label,:snapshot,:proposal_digest,"
                "'ready',:stage_digest,:staged_at,:expires_at,NULL,NULL,NULL)"
            ),
            {
                "proposal_id": proposal_id,
                "operator_request_id": operator_request_id,
                "request_id": request_id,
                "ha_user_id": ha_user_id,
                "person_id": person["person_id"],
                "label": person["display_name"],
                "snapshot": stored_snapshot_digest,
                "proposal_digest": proposal_digest,
                "stage_digest": stage_receipt_digest,
                "staged_at": staged_at,
                "expires_at": expires_at,
            },
        )

    return StagedBinding(
        promotion_id=promotion.promotion_id,
        run_id=promotion.run_id,
        request_id=request_id,
        proposal_id=proposal_id,
        person_id=person["person_id"],
        ha_user_id=ha_user_id,
        proposal_digest=proposal_digest,
        nonce=nonce,
        receipt_id=receipt_id,
        principal_id=principal_id,
        artifact_id=artifact_id,
        binding_id=binding_id,
    )


async def _commit(
    engine: AsyncEngine,
    staged: StagedBinding,
) -> datetime:
    values = _commit_values(staged)
    async with engine.begin() as connection:
        return (
            await connection.execute(
                text(
                    f"SELECT {FUNCTION}("
                    ":proposal_id,CAST(:ha_user_id AS varchar(64)),"
                    "CAST(:proposal_digest AS varchar(64)),:nonce,"
                    ":receipt_id,:principal_id,:artifact_id,:binding_id)"
                ),
                values,
            )
        ).scalar_one()


def _commit_values(
    staged: StagedBinding,
) -> dict[str, object]:
    return {
        "proposal_id": staged.proposal_id,
        "ha_user_id": staged.ha_user_id,
        "proposal_digest": staged.proposal_digest,
        "nonce": staged.nonce,
        "receipt_id": staged.receipt_id,
        "principal_id": staged.principal_id,
        "artifact_id": staged.artifact_id,
        "binding_id": staged.binding_id,
    }


async def _cleanup(
    owner_engine: AsyncEngine,
    staged: StagedBinding,
) -> None:
    async with owner_engine.begin() as connection:
        # The owner policy is intentionally SELECT-only on immutable receipts;
        # the disposable gate briefly removes FORCE solely for test teardown.
        await connection.execute(
            text(
                "ALTER TABLE operations."
                "principal_binding_authority_receipts "
                "NO FORCE ROW LEVEL SECURITY"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE privacy.artifact_registry "
                "NO FORCE ROW LEVEL SECURITY"
            )
        )
        await connection.execute(
            text(
                "DELETE FROM identity.edge_privacy_user_blocks "
                "WHERE ha_user_id=:ha_user_id"
            ),
            {"ha_user_id": staged.ha_user_id},
        )
        await connection.execute(
            text(
                "DELETE FROM identity.ha_user_bindings "
                "WHERE binding_id=:binding_id"
            ),
            {"binding_id": staged.binding_id},
        )
        await connection.execute(
            text(
                "DELETE FROM operations."
                "principal_binding_authority_receipts "
                "WHERE proposal_id=:proposal_id"
            ),
            {"proposal_id": staged.proposal_id},
        )
        await connection.execute(
            text(
                "DELETE FROM privacy.artifact_registry "
                "WHERE artifact_id=:artifact_id"
            ),
            {"artifact_id": staged.artifact_id},
        )
        await connection.execute(
            text(
                "DELETE FROM identity.principal_binding_requests "
                "WHERE request_id=:request_id"
            ),
            {"request_id": staged.request_id},
        )
        await connection.execute(
            text(
                "DELETE FROM identity.confirmation_artifacts "
                "WHERE artifact_id=:artifact_id"
            ),
            {"artifact_id": staged.artifact_id},
        )
        await connection.execute(
            text(
                "DELETE FROM identity.principals "
                "WHERE principal_id=:principal_id"
            ),
            {"principal_id": staged.principal_id},
        )
        await connection.execute(
            text(
                "ALTER TABLE privacy.artifact_registry "
                "FORCE ROW LEVEL SECURITY"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE operations."
                "principal_binding_authority_receipts "
                "FORCE ROW LEVEL SECURITY"
            )
        )


@pytest.mark.skipif(
    not os.getenv(HOSTED_GATE_SENTINEL_ENV),
    reason="not running inside the isolated hosted PostgreSQL gate",
)
def test_e5b_hosted_gate_cannot_silently_skip_runtime_contract() -> None:
    assert _runtime_configured()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5b PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5b_catalog_role_function_and_rls_contract() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    try:
        async with owner_engine.connect() as connection:
            revision = (
                await connection.execute(
                    text("SELECT version_num FROM public.alembic_version")
                )
            ).scalar_one()
            assert revision == "0016_principal_binding_e5b"
            role = (
                await connection.execute(
                    text(
                        "SELECT NOT r.rolcanlogin, NOT r.rolinherit, "
                        "NOT r.rolsuper, NOT r.rolcreatedb, "
                        "NOT r.rolcreaterole, NOT r.rolreplication, "
                        "NOT r.rolbypassrls, r.rolconnlimit=0, "
                        "r.rolvaliduntil IS NULL, "
                        "r.rolconfig IS NULL, a.rolpassword IS NULL "
                        "FROM pg_catalog.pg_roles AS r "
                        "JOIN pg_catalog.pg_authid AS a ON a.oid=r.oid "
                        "WHERE r.rolname='home_agent_identity_binding_kernel'"
                    )
                )
            ).one()
            assert all(role)
            function = (
                await connection.execute(
                    text(
                        "SELECT p.prosecdef, p.provolatile='v', "
                        "p.prorettype='timestamptz'::regtype, "
                        "p.proconfig=ARRAY["
                        "'search_path=pg_catalog, pg_temp']::text[], "
                        "r.rolname='home_agent_identity_binding_kernel' "
                        "FROM pg_catalog.pg_proc p "
                        "JOIN pg_catalog.pg_roles r ON r.oid=p.proowner "
                        "WHERE p.oid='identity."
                        "commit_authenticated_principal_binding_e5b("
                        "uuid,character varying,character varying,"
                        "uuid,uuid,uuid,uuid,uuid)'::regprocedure"
                    )
                )
            ).one()
            assert all(function)
            receipt = (
                await connection.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity, "
                        "(SELECT count(*)=4 FROM pg_catalog.pg_policy "
                        "WHERE polrelid=c.oid), "
                        "NOT has_table_privilege("
                        "'home_agent_identity_binding_kernel',c.oid,'SELECT'),"
                        "NOT has_table_privilege("
                        "'home_agent_identity_binding_kernel',c.oid,'INSERT'),"
                        "NOT has_table_privilege("
                        "'home_agent_identity_binding_kernel',c.oid,'DELETE') "
                        "FROM pg_catalog.pg_class c "
                        "WHERE c.oid='operations."
                        "principal_binding_authority_receipts'::regclass"
                    )
                )
            ).one()
            assert all(receipt)
    finally:
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5b PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5b_success_creates_one_exact_graph_and_replays() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    binding_engine = _engine(BINDING_DATABASE_ENV, serializable=True)
    staged = await _stage_binding(owner_engine, label="success")
    try:
        evaluated_at = await _commit(binding_engine, staged)
        replayed_at = await _commit(binding_engine, staged)
        assert replayed_at == evaluated_at

        async with owner_engine.begin() as connection:
            await connection.execute(
                text(
                    "ALTER TABLE privacy.artifact_registry "
                    "NO FORCE ROW LEVEL SECURITY"
                )
            )
            graph = (
                await connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM identity.principals "
                        "WHERE principal_id=:principal_id), "
                        "(SELECT count(*) "
                        "FROM identity.confirmation_artifacts "
                        "WHERE artifact_id=:artifact_id), "
                        "(SELECT count(*) FROM privacy.artifact_registry "
                        "WHERE artifact_id=:artifact_id), "
                        "(SELECT count(*) FROM operations."
                        "principal_binding_authority_receipts "
                        "WHERE receipt_id=:receipt_id "
                        "AND authority_result='current_database_authority' "
                        "AND projection_decision_kind='person' "
                        "AND projection_table_kind='identity.people' "
                        "AND projection_subject_role='primary'), "
                        "(SELECT count(*) FROM identity.ha_user_bindings "
                        "WHERE binding_id=:binding_id "
                        "AND authority_receipt_id=:receipt_id), "
                        "(SELECT count(*) "
                        "FROM identity.principal_binding_proposals "
                        "WHERE proposal_id=:proposal_id "
                        "AND state='consumed' "
                        "AND consumed_at=:evaluated_at), "
                        "(SELECT count(*) "
                        "FROM identity.principal_binding_requests "
                        "WHERE request_id=:request_id "
                        "AND state='consumed' "
                        "AND closed_at=:evaluated_at)"
                    ),
                    {
                        "principal_id": staged.principal_id,
                        "artifact_id": staged.artifact_id,
                        "receipt_id": staged.receipt_id,
                        "binding_id": staged.binding_id,
                        "proposal_id": staged.proposal_id,
                        "request_id": staged.request_id,
                        "evaluated_at": evaluated_at,
                    },
                )
            ).one()
            assert tuple(graph) == (1, 1, 1, 1, 1, 1, 1)
            await connection.execute(
                text(
                    "ALTER TABLE privacy.artifact_registry "
                    "FORCE ROW LEVEL SECURITY"
                )
            )
    finally:
        await _cleanup(owner_engine, staged)
        await binding_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured()
    or os.getenv(RETAIN_DOWNGRADE_EVIDENCE_ENV) != "1",
    reason="runner-only populated downgrade evidence is not requested",
)
async def test_e5b_retains_one_graph_for_hosted_downgrade_refusal() -> None:
    """Leave synthetic E5b evidence only for the disposable hosted phase."""
    owner_engine = _engine(OWNER_DATABASE_ENV)
    binding_engine = _engine(BINDING_DATABASE_ENV, serializable=True)
    staged = await _stage_binding(owner_engine, label="downgrade-refusal")
    committed = False
    try:
        evaluated_at = await _commit(binding_engine, staged)
        committed = True
        assert evaluated_at.tzinfo is not None
    finally:
        if not committed:
            await _cleanup(owner_engine, staged)
        await binding_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv(OWNER_DATABASE_ENV)
    or os.getenv(CLEANUP_DOWNGRADE_EVIDENCE_ENV) != "1",
    reason="runner-only downgrade evidence cleanup is not requested",
)
async def test_e5b_removes_hosted_downgrade_evidence_after_refusal() -> None:
    """Remove only the synthetic graph after its downgrade proof is complete."""

    owner_engine = _engine(OWNER_DATABASE_ENV)
    try:
        async with owner_engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            "SELECT promotion.promotion_id, "
                            "promotion.run_id, request.request_id, "
                            "proposal.proposal_id, proposal.person_id, "
                            "proposal.ha_user_id, proposal.proposal_digest, "
                            "receipt.receipt_id, receipt.principal_id, "
                            "receipt.confirmation_artifact_id AS artifact_id, "
                            "receipt.binding_id "
                            "FROM identity.principal_binding_proposals proposal "
                            "JOIN identity.principal_binding_requests request "
                            "ON request.request_id=proposal.request_id "
                            "JOIN operations."
                            "principal_binding_authority_receipts receipt "
                            "ON receipt.proposal_id=proposal.proposal_id "
                            "JOIN operations.semantic_authority_promotions "
                            "promotion "
                            "ON promotion.promotion_id=receipt.promotion_id "
                            "WHERE proposal.ha_user_id LIKE "
                            "'e5b-runtime-downgrade-refusal-%'"
                        )
                    )
                )
                .mappings()
                .all()
            )
        assert len(rows) == 1
        retained = rows[0]
        staged = StagedBinding(
            promotion_id=retained["promotion_id"],
            run_id=retained["run_id"],
            request_id=retained["request_id"],
            proposal_id=retained["proposal_id"],
            person_id=retained["person_id"],
            ha_user_id=retained["ha_user_id"],
            proposal_digest=retained["proposal_digest"],
            nonce=uuid.uuid4(),
            receipt_id=retained["receipt_id"],
            principal_id=retained["principal_id"],
            artifact_id=retained["artifact_id"],
            binding_id=retained["binding_id"],
        )
        await _cleanup(owner_engine, staged)
        async with owner_engine.connect() as connection:
            remaining = (
                await connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM identity."
                        "principal_binding_requests "
                        "WHERE request_id=:request_id) + "
                        "(SELECT count(*) FROM identity."
                        "principal_binding_proposals "
                        "WHERE proposal_id=:proposal_id) + "
                        "(SELECT count(*) FROM identity.ha_user_bindings "
                        "WHERE binding_id=:binding_id) + "
                        "(SELECT count(*) FROM identity.confirmation_artifacts "
                        "WHERE artifact_id=:artifact_id) + "
                        "(SELECT count(*) FROM identity.principals "
                        "WHERE principal_id=:principal_id)"
                    ),
                    {
                        "request_id": staged.request_id,
                        "proposal_id": staged.proposal_id,
                        "binding_id": staged.binding_id,
                        "artifact_id": staged.artifact_id,
                        "principal_id": staged.principal_id,
                    },
                )
            ).scalar_one()
        assert remaining == 0
    finally:
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5b PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5b_replay_rejects_each_mismatched_output_id() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    binding_engine = _engine(BINDING_DATABASE_ENV, serializable=True)
    staged = await _stage_binding(owner_engine, label="replay-output-ids")
    try:
        await _commit(binding_engine, staged)
        mismatches = (
            replace(staged, receipt_id=uuid7()),
            replace(staged, principal_id=uuid7()),
            replace(staged, artifact_id=uuid7()),
            replace(staged, binding_id=uuid7()),
        )
        for mismatched in mismatches:
            changed_ids = {
                mismatched.receipt_id,
                mismatched.principal_id,
                mismatched.artifact_id,
                mismatched.binding_id,
            } - {
                staged.receipt_id,
                staged.principal_id,
                staged.artifact_id,
                staged.binding_id,
            }
            assert len(changed_ids) == 1
            mismatched_id = changed_ids.pop()
            with pytest.raises(DBAPIError) as replay_error:
                await _commit(binding_engine, mismatched)
            assert _sqlstate(replay_error.value) == "23514"
            rendered = str(replay_error.value.orig)
            assert "principal_binding_e5b_graph_invalid" in rendered
            assert str(mismatched_id) not in rendered
            assert staged.ha_user_id not in rendered
    finally:
        await _cleanup(owner_engine, staged)
        await binding_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5b PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5b_replay_rejects_inactive_bound_principal() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    binding_engine = _engine(BINDING_DATABASE_ENV, serializable=True)
    staged = await _stage_binding(owner_engine, label="inactive-replay")
    try:
        await _commit(binding_engine, staged)
        async with owner_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE identity.principals SET status='inactive' "
                    "WHERE principal_id=:principal_id"
                ),
                {"principal_id": staged.principal_id},
            )
        with pytest.raises(DBAPIError) as replay_error:
            await _commit(binding_engine, staged)
        assert _sqlstate(replay_error.value) == "23514"
        assert "principal_binding_e5b_graph_invalid" in str(
            replay_error.value.orig
        )
    finally:
        await _cleanup(owner_engine, staged)
        await binding_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5b PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5b_rejects_wrong_role_isolation_and_snapshot_drift() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    owner_serializable = _engine(OWNER_DATABASE_ENV, serializable=True)
    binding_read_committed = _engine(BINDING_DATABASE_ENV)
    binding_serializable = _engine(BINDING_DATABASE_ENV, serializable=True)
    staged = await _stage_binding(
        owner_engine, label="negative", corrupt_snapshot=True
    )
    try:
        with pytest.raises(DBAPIError) as role_error:
            await _commit(owner_serializable, staged)
        assert _sqlstate(role_error.value) == "42501"

        with pytest.raises(DBAPIError) as isolation_error:
            await _commit(binding_read_committed, staged)
        assert _sqlstate(isolation_error.value) == "25000"

        with pytest.raises(DBAPIError) as drift_error:
            await _commit(binding_serializable, staged)
        assert _sqlstate(drift_error.value) == "23514"
        assert "principal_binding_e5b_graph_invalid" in str(
            drift_error.value.orig
        )
    finally:
        await _cleanup(owner_engine, staged)
        await binding_serializable.dispose()
        await binding_read_committed.dispose()
        await owner_serializable.dispose()
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5b PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5b_rejects_prior_caller_transaction_assignment() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    binding_engine = _engine(BINDING_DATABASE_ENV, serializable=True)
    staged = await _stage_binding(owner_engine, label="prior-transaction-id")
    try:
        with pytest.raises(DBAPIError) as transaction_error:
            async with binding_engine.begin() as connection:
                await connection.execute(
                    text("SELECT pg_catalog.pg_current_xact_id()")
                )
                await connection.execute(
                    text(
                        f"SELECT {FUNCTION}("
                        ":proposal_id,CAST(:ha_user_id AS varchar(64)),"
                        "CAST(:proposal_digest AS varchar(64)),:nonce,"
                        ":receipt_id,:principal_id,:artifact_id,:binding_id)"
                    ),
                    _commit_values(staged),
                )
        assert _sqlstate(transaction_error.value) == "25000"
        assert "principal_binding_e5b_transaction_invalid" in str(
            transaction_error.value.orig
        )
    finally:
        await _cleanup(owner_engine, staged)
        await binding_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5b PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5c_committer_cannot_mutate_staging_rows() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    binding_engine = _engine(BINDING_DATABASE_ENV, serializable=True)
    staged = await _stage_binding(owner_engine, label="committer-dml-denied")
    try:
        with pytest.raises(DBAPIError) as privilege_error:
            async with binding_engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE identity.principal_binding_requests "
                        "SET expires_at=expires_at "
                        "WHERE request_id=:request_id"
                    ),
                    {"request_id": staged.request_id},
                )
        assert _sqlstate(privilege_error.value) == "42501"
    finally:
        await _cleanup(owner_engine, staged)
        await binding_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5b PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5c_committer_cannot_lock_staging_rows() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    binding_engine = _engine(BINDING_DATABASE_ENV, serializable=True)
    staged = await _stage_binding(owner_engine, label="committer-lock-denied")
    try:
        with pytest.raises(DBAPIError) as privilege_error:
            async with binding_engine.begin() as connection:
                await connection.execute(
                    text(
                        "SELECT request_id "
                        "FROM identity.principal_binding_requests "
                        "WHERE request_id=:request_id FOR UPDATE"
                    ),
                    {"request_id": staged.request_id},
                )
        assert _sqlstate(privilege_error.value) == "42501"
    finally:
        await _cleanup(owner_engine, staged)
        await binding_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5b PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5b_concurrent_person_binding_has_exactly_one_winner() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    first_engine = _engine(BINDING_DATABASE_ENV, serializable=True)
    second_engine = _engine(BINDING_DATABASE_ENV, serializable=True)
    first = await _stage_binding(owner_engine, label="concurrent-first")
    # The staging schema already permits only one ready proposal per person.
    # Race two distinct output graphs for that same reviewed proposal/nonce.
    second = replace(
        first,
        receipt_id=uuid7(),
        principal_id=uuid7(),
        artifact_id=uuid7(),
        binding_id=uuid7(),
    )

    async def attempt(
        engine: AsyncEngine,
        staged: StagedBinding,
    ) -> datetime | DBAPIError:
        try:
            return await _commit(engine, staged)
        except DBAPIError as error:
            return error

    winner = first
    try:
        results = await asyncio.gather(
            attempt(first_engine, first),
            attempt(second_engine, second),
        )
        successes = [result for result in results if isinstance(result, datetime)]
        failures = [result for result in results if isinstance(result, DBAPIError)]
        assert len(successes) == 1
        assert len(failures) == 1
        failure_state = _sqlstate(failures[0])
        assert failure_state in {"23514", "40001"}
        if failure_state == "23514":
            assert "principal_binding_e5b_graph_invalid" in str(
                failures[0].orig
            )
        winner = first if isinstance(results[0], datetime) else second
    finally:
        await _cleanup(owner_engine, winner)
        await second_engine.dispose()
        await first_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5b PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5b_current_edge_privacy_block_is_content_free_denial() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    binding_engine = _engine(BINDING_DATABASE_ENV, serializable=True)
    staged = await _stage_binding(owner_engine, label="privacy")
    try:
        async with owner_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO identity.edge_privacy_user_blocks("
                    "block_id,ha_user_id,reason_code,person_id,created_at) "
                    "VALUES(:block_id,:ha_user_id,'do_not_track',"
                    ":person_id,transaction_timestamp())"
                ),
                {
                    "block_id": uuid7(),
                    "ha_user_id": staged.ha_user_id,
                    "person_id": staged.person_id,
                },
            )
        with pytest.raises(DBAPIError) as privacy_error:
            await _commit(binding_engine, staged)
        assert _sqlstate(privacy_error.value) == "42501"
        assert "principal_binding_e5b_privacy_blocked" in str(
            privacy_error.value.orig
        )
        assert staged.ha_user_id not in str(privacy_error.value.orig)
    finally:
        await _cleanup(owner_engine, staged)
        await binding_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not (
        _runtime_configured()
        and os.getenv(OPERATOR_DATABASE_ENV)
    ),
    reason="E5c PostgreSQL split credentials are not configured",
)
async def test_e5c_split_adapter_commits_after_pinned_grant_replay() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    staged = await _stage_binding(owner_engine, label="e5c-split-adapter")
    operator_database = Database(os.environ[OPERATOR_DATABASE_ENV])
    commit_database = PrincipalBindingCommitDatabase(
        os.environ[BINDING_DATABASE_ENV]
    )
    store = object.__new__(CoreStore)
    store.database = operator_database
    store.settings = SimpleNamespace(rollout_mode="shadow")
    adapter = AuthenticatedPrincipalBindingAdapter(commit_database)
    try:
        context = await store.principal_binding_commit_context(
            staged.ha_user_id,
            staged.proposal_digest,
        )
        result = await adapter.commit(
            context=context,
            ha_user_id=staged.ha_user_id,
            value=PrincipalBindingConfirmation(
                proposal_digest=staged.proposal_digest,
                confirmation_nonce=staged.nonce,
            ),
        )
        assert result.state == "bound"
        assert result.location_memory_enabled is False
        assert result.travel_greetings_enabled is False
    finally:
        await _cleanup(owner_engine, staged)
        await adapter.close()
        await operator_database.close()
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5b PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5b_expired_proposal_and_blocking_directive_fail_closed() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    binding_engine = _engine(BINDING_DATABASE_ENV, serializable=True)
    expired = await _stage_binding(
        owner_engine, label="expired", expired=True
    )
    directive: StagedBinding | None = None
    directive_id = uuid7()
    try:
        with pytest.raises(DBAPIError) as expiry_error:
            await _commit(binding_engine, expired)
        assert _sqlstate(expiry_error.value) == "23514"
        await _cleanup(owner_engine, expired)

        directive = await _stage_binding(owner_engine, label="directive")
        async with owner_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO identity.privacy_directives("
                    "directive_id,person_id,directive,enabled,expires_at,"
                    "created_at) VALUES(:directive_id,:person_id,'silent',"
                    "true,NULL,transaction_timestamp())"
                ),
                {
                    "directive_id": directive_id,
                    "person_id": directive.person_id,
                },
            )
        with pytest.raises(DBAPIError) as directive_error:
            await _commit(binding_engine, directive)
        assert _sqlstate(directive_error.value) == "42501"
        assert "principal_binding_e5b_privacy_blocked" in str(
            directive_error.value.orig
        )
    finally:
        if directive is not None:
            async with owner_engine.begin() as connection:
                await connection.execute(
                    text(
                        "DELETE FROM identity.privacy_directives "
                        "WHERE directive_id=:directive_id"
                    ),
                    {"directive_id": directive_id},
                )
            await _cleanup(owner_engine, directive)
        else:
            await _cleanup(owner_engine, expired)
        await binding_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5b PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5b_requires_exact_primary_person_lineage() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    binding_engine = _engine(BINDING_DATABASE_ENV, serializable=True)
    unreviewed_person_id = uuid7()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO identity.people("
                "person_id,display_name,status,privacy_scope,created_at,"
                "updated_at) VALUES(:person_id,'E5b unreviewed fixture',"
                "'active','private',transaction_timestamp(),"
                "transaction_timestamp())"
            ),
            {"person_id": unreviewed_person_id},
        )
    staged = await _stage_binding(
        owner_engine,
        label="lineage",
        person_id=unreviewed_person_id,
    )
    try:
        with pytest.raises(DBAPIError) as lineage_error:
            await _commit(binding_engine, staged)
        assert _sqlstate(lineage_error.value) == "23514"
    finally:
        await _cleanup(owner_engine, staged)
        async with owner_engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM identity.people WHERE person_id=:person_id"
                ),
                {"person_id": unreviewed_person_id},
            )
        await binding_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5b PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5b_output_collision_is_mapped_without_identifier_detail() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    binding_engine = _engine(BINDING_DATABASE_ENV, serializable=True)
    staged = await _stage_binding(owner_engine, label="collision")
    try:
        async with owner_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO identity.principals("
                    "principal_id,person_id,kind,display_label,status,"
                    "created_at) VALUES(:principal_id,:person_id,'service',"
                    "'E5b collision fixture','active',"
                    "transaction_timestamp())"
                ),
                {
                    "principal_id": staged.principal_id,
                    "person_id": staged.person_id,
                },
            )
        with pytest.raises(DBAPIError) as collision_error:
            await _commit(binding_engine, staged)
        assert _sqlstate(collision_error.value) == "23514"
        rendered = str(collision_error.value.orig)
        assert "principal_binding_e5b_graph_invalid" in rendered
        assert staged.ha_user_id not in rendered
        assert str(staged.principal_id) not in rendered
        assert "pk_principals" not in rendered
    finally:
        await _cleanup(owner_engine, staged)
        await binding_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5b PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5b_confirmation_nonce_is_globally_single_use_across_purposes() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    binding_engine = _engine(BINDING_DATABASE_ENV, serializable=True)
    staged = await _stage_binding(owner_engine, label="nonce-cross-purpose")
    cross_principal_id = uuid7()
    cross_artifact_id = uuid7()
    try:
        # Seed the nonce under an unrelated confirmation purpose. E5b must
        # reject it before writing any binding graph: replay resistance is
        # global to confirmation_artifacts, not scoped to the binding purpose.
        async with owner_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO identity.principals("
                    "principal_id,person_id,kind,display_label,status,"
                    "created_at) VALUES(:principal_id,:person_id,'service',"
                    "'E5b cross-purpose nonce fixture','active',"
                    "transaction_timestamp())"
                ),
                {
                    "principal_id": cross_principal_id,
                    "person_id": staged.person_id,
                },
            )
            await connection.execute(
                text(
                    "WITH operation AS ("
                    "SELECT clock_timestamp() AS operation_time"
                    ") INSERT INTO identity.confirmation_artifacts("
                    "artifact_id,principal_id,purpose,proposal_digest,"
                    "client_nonce_sha256,issued_at,expires_at,consumed_at) "
                    "SELECT :artifact_id,:principal_id,"
                    "'identity_profile.confirm',repeat('a',64),"
                    "encode(sha256(uuid_send(CAST(:nonce AS uuid))),'hex'),"
                    "operation_time,operation_time + interval '5 minutes',"
                    "operation_time FROM operation"
                ),
                {
                    "artifact_id": cross_artifact_id,
                    "principal_id": cross_principal_id,
                    "nonce": staged.nonce,
                },
            )
        with pytest.raises(DBAPIError) as nonce_error:
            await _commit(binding_engine, staged)
        assert _sqlstate(nonce_error.value) == "23514"
        assert "principal_binding_e5b_graph_invalid" in str(
            nonce_error.value.orig
        )
    finally:
        await _cleanup(owner_engine, staged)
        async with owner_engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM identity.confirmation_artifacts "
                    "WHERE artifact_id=:artifact_id"
                ),
                {"artifact_id": cross_artifact_id},
            )
            await connection.execute(
                text(
                    "DELETE FROM identity.principals "
                    "WHERE principal_id=:principal_id"
                ),
                {"principal_id": cross_principal_id},
            )
        await binding_engine.dispose()
        await owner_engine.dispose()
