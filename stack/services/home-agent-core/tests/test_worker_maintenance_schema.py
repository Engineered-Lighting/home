from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import os
from pathlib import Path
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError

from app import schema
from app.db import Database


ROOT = Path(__file__).resolve().parents[1]
LEASE_MIGRATION = ROOT / "alembic/versions/0006a_worker_lease_arbitration.py"
REQUIRED_URLS = (
    "TEST_DATABASE_URL",
    "TEST_API_DATABASE_URL",
    "TEST_INGEST_DATABASE_URL",
    "TEST_WORKER_DATABASE_URL",
    "TEST_ERASURE_DATABASE_URL",
    "TEST_ROLLOUT_DATABASE_URL",
)


def sqlstate(exc: pytest.ExceptionInfo[DBAPIError]) -> str | None:
    return getattr(exc.value.orig, "sqlstate", None)


def test_worker_maintenance_metadata_is_unlogged_content_free_and_strict() -> None:
    table = schema.worker_maintenance_state
    assert set(table.c.keys()) == {
        "state_key",
        "worker_instance_id",
        "database_started_at",
        "kernel_version",
        "state",
        "started_at",
        "heartbeat_at",
        "heartbeat_sequence",
        "maintenance_attempted_at",
        "maintenance_succeeded_at",
        "success_sequence",
        "consecutive_failures",
        "last_error_code",
        "stopped_at",
        "spool_rows_pruned",
        "binding_retention_receipt",
        "updated_at",
    }
    assert table._prefixes == ["UNLOGGED"]
    assert not set(table.c.keys()) & {
        "person_id",
        "principal_id",
        "ha_user_id",
        "entity_id",
        "payload",
        "latitude",
        "longitude",
        "utterance",
    }
    checks = " ".join(
        str(constraint.sqltext)
        for constraint in table.constraints
        if hasattr(constraint, "sqltext")
    )
    for value in (
        "durable_worker",
        "worker-maintenance-cycle-v1",
        "starting",
        "running",
        "degraded",
        "stopping",
        "proposals_expired",
        "requests_purged",
    ):
        assert value in checks

    rollout_columns = set(schema.rollout_authorizations.c.keys())
    assert {
        "worker_instance_id",
        "worker_success_sequence",
        "worker_kernel_version",
        "worker_maintenance_succeeded_at",
        "worker_proof_digest",
    } <= rollout_columns


def test_worker_lease_arbitration_is_a_narrow_deployable_0006_backport() -> None:
    source = LEASE_MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "0006a_worker_lease_arbitration"' in source
    assert 'down_revision: str | None = "0006_worker_maintenance_health"' in source
    assert "existing_heartbeat_at" in source
    assert "interval '{FRESH_LEASE_SECONDS} seconds'" in source
    assert "FRESH_LEASE_SECONDS = 45" in source
    assert "existing_database_start = database_start" in source
    assert "existing_state <> 'stopping'" in source
    assert "RETURN false" in source
    assert "pg_advisory_xact_lock(7210202606)" in source
    assert "CREATE TABLE" not in source
    assert "ALTER TABLE" not in source
    assert "not by competing workers" in source

    spec = importlib.util.spec_from_file_location(
        "worker_lease_migration", LEASE_MIGRATION
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sql = module._REGISTER_FUNCTION_TEMPLATE.format(
        fresh_owner_guard=module._FRESH_OWNER_GUARD
    )
    body_start = sql.index("AS $$") + len("AS $$")
    body = sql[body_start : sql.index("$$", body_start)]
    source_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    e1_source = (
        ROOT / "alembic/versions/0011_identity_erasure_schema_foundation.py"
    ).read_text(encoding="utf-8")
    assert source_digest == (
        "77cfba8b257505ad93ebc11be6540ad626dd661e71353af9bacb13f88dc83b9d"
    )
    assert source_digest in e1_source


@pytest.mark.skipif(
    not all(os.getenv(name) for name in REQUIRED_URLS),
    reason="owner and runtime-role PostgreSQL URLs are required",
)
@pytest.mark.asyncio
async def test_worker_maintenance_functions_fence_roles_and_commit_exact_proof() -> None:
    owner = Database(os.environ["TEST_DATABASE_URL"])
    api = Database(os.environ["TEST_API_DATABASE_URL"])
    ingest = Database(os.environ["TEST_INGEST_DATABASE_URL"])
    worker = Database(os.environ["TEST_WORKER_DATABASE_URL"])
    erasure = Database(os.environ["TEST_ERASURE_DATABASE_URL"])
    rollout = Database(os.environ["TEST_ROLLOUT_DATABASE_URL"])
    databases = (owner, api, ingest, worker, erasure, rollout)
    first_instance = uuid.uuid4()
    second_instance = uuid.uuid4()
    request_id = uuid.uuid4()
    ha_user_id = f"worker-maintenance-{uuid.uuid4().hex}"
    now = datetime.now(UTC)
    try:
        async with owner.transaction() as connection:
            await connection.execute(delete(schema.worker_maintenance_state))

        with pytest.raises(DBAPIError) as api_register:
            async with api.transaction() as connection:
                await connection.execute(
                    text("SELECT operations.register_worker_maintenance(:instance)"),
                    {"instance": first_instance},
                )
        assert sqlstate(api_register) == "42501"

        async with worker.transaction() as connection:
            assert (
                await connection.execute(
                    text("SELECT operations.register_worker_maintenance(:instance)"),
                    {"instance": first_instance},
                )
            ).scalar_one() is True
            # Exact replay of a lost response is idempotent.
            assert (
                await connection.execute(
                    text("SELECT operations.register_worker_maintenance(:instance)"),
                    {"instance": first_instance},
                )
            ).scalar_one() is True

        async with worker.transaction() as connection:
            registered = (
                (
                    await connection.execute(
                        select(schema.worker_maintenance_state)
                    )
                )
                .mappings()
                .one()
            )
        assert registered["state_key"] == "durable_worker"
        assert registered["worker_instance_id"] == first_instance
        assert registered["kernel_version"] == "worker-maintenance-cycle-v1"
        assert registered["state"] == "starting"
        assert registered["heartbeat_sequence"] == 1
        assert registered["success_sequence"] == 0
        assert registered["database_started_at"] <= registered["started_at"]

        # A second live process is a standby while the first instance has a
        # fresh heartbeat. Registration must not erase the current fence.
        async with worker.transaction() as connection:
            assert (
                await connection.execute(
                    text("SELECT operations.register_worker_maintenance(:instance)"),
                    {"instance": second_instance},
                )
            ).scalar_one() is False
            assert (
                await connection.execute(
                    text("SELECT operations.heartbeat_worker_maintenance(:instance)"),
                    {"instance": second_instance},
                )
            ).scalar_one() is False
        with pytest.raises(DBAPIError) as standby_cycle:
            async with worker.transaction() as connection:
                await connection.execute(
                    text(
                        "SELECT operations.run_worker_maintenance_cycle("
                        ":instance, 0)"
                    ),
                    {"instance": second_instance},
                )
        assert sqlstate(standby_cycle) == "55000"
        async with worker.transaction() as connection:
            assert (
                await connection.execute(
                    select(schema.worker_maintenance_state.c.worker_instance_id)
                )
            ).scalar_one() == first_instance

        # A future heartbeat after wall-clock rollback is intentionally fresh.
        # Standby must fail closed until the postmaster fence changes rather
        # than risk two workers running maintenance.
        async with owner.transaction() as connection:
            await connection.execute(
                update(schema.worker_maintenance_state).values(
                    heartbeat_at=func.clock_timestamp() + text("interval '1 hour'"),
                    updated_at=func.clock_timestamp(),
                )
            )
        async with worker.transaction() as connection:
            assert (
                await connection.execute(
                    text("SELECT operations.register_worker_maintenance(:instance)"),
                    {"instance": second_instance},
                )
            ).scalar_one() is False
        async with owner.transaction() as connection:
            await connection.execute(
                update(schema.worker_maintenance_state).values(
                    heartbeat_at=func.clock_timestamp(),
                    updated_at=func.clock_timestamp(),
                )
            )

        async def blocked_heartbeat() -> bool:
            async with worker.transaction() as connection:
                return (
                    await connection.execute(
                        text(
                            "SELECT operations.heartbeat_worker_maintenance("
                            ":instance)"
                        ),
                        {"instance": first_instance},
                    )
                ).scalar_one()

        # The database clock must be sampled after the serialization lock is
        # acquired. Otherwise a blocked call can overwrite a later heartbeat
        # with the timestamp it captured before waiting.
        heartbeat_task = None
        heartbeat_result = False
        blocker_time = None
        try:
            async with owner.transaction() as connection:
                await connection.execute(
                    text("SELECT pg_advisory_xact_lock(7210202606)")
                )
                heartbeat_task = asyncio.create_task(blocked_heartbeat())
                observed_waiter = False
                for _ in range(100):
                    observed_waiter = (
                        await connection.execute(
                            text(
                                "SELECT EXISTS ("
                                "SELECT 1 FROM pg_stat_activity "
                                "WHERE usename = 'home_agent_worker' "
                                "AND datname = current_database() "
                                "AND wait_event_type = 'Lock' "
                                "AND query LIKE "
                                "'%heartbeat_worker_maintenance%')"
                            )
                        )
                    ).scalar_one()
                    if observed_waiter:
                        break
                    await asyncio.sleep(0.01)
                assert observed_waiter
                blocker_time = (
                    await connection.execute(select(func.clock_timestamp()))
                ).scalar_one()
                await connection.execute(
                    update(schema.worker_maintenance_state).values(
                        heartbeat_at=blocker_time,
                        heartbeat_sequence=(
                            schema.worker_maintenance_state.c.heartbeat_sequence + 1
                        ),
                        updated_at=blocker_time,
                    )
                )
        finally:
            if heartbeat_task is not None:
                heartbeat_result = await asyncio.wait_for(
                    heartbeat_task, timeout=5
                )
        assert heartbeat_result is True
        assert blocker_time is not None
        async with owner.transaction() as connection:
            heartbeat_after_wait = (
                await connection.execute(
                    select(schema.worker_maintenance_state.c.heartbeat_at)
                )
            ).scalar_one()
        assert heartbeat_after_wait >= blocker_time

        with pytest.raises(DBAPIError) as worker_direct_update:
            async with worker.transaction() as connection:
                await connection.execute(
                    update(schema.worker_maintenance_state).values(state="running")
                )
        assert sqlstate(worker_direct_update) == "42501"

        requested_at = now - timedelta(minutes=20)
        expires_at = now - timedelta(minutes=10)
        async with api.transaction(ha_user_id=ha_user_id) as connection:
            await connection.execute(
                insert(schema.principal_binding_requests).values(
                    request_id=request_id,
                    ha_user_id=ha_user_id,
                    review_code="23456789ABCDEFGH",
                    state="pending",
                    requested_at=requested_at,
                    expires_at=expires_at,
                )
            )

        async with worker.transaction() as connection:
            receipt = (
                await connection.execute(
                    text(
                        "SELECT operations.run_worker_maintenance_cycle("
                        ":instance, :spool_rows)"
                    ),
                    {"instance": first_instance, "spool_rows": 3},
                )
            ).scalar_one()
        assert set(receipt) == {
            "proposals_expired",
            "staged_requests_expired",
            "pending_requests_expired",
            "requests_expired",
            "proposals_purged",
            "requests_purged",
        }
        assert all(isinstance(value, int) and value >= 0 for value in receipt.values())
        assert receipt["requests_expired"] == (
            receipt["staged_requests_expired"]
            + receipt["pending_requests_expired"]
        )
        assert receipt["pending_requests_expired"] >= 1

        async with owner.transaction() as connection:
            state = (
                (
                    await connection.execute(
                        select(schema.worker_maintenance_state)
                    )
                )
                .mappings()
                .one()
            )
            request_state = (
                await connection.execute(
                    select(schema.principal_binding_requests.c.state).where(
                        schema.principal_binding_requests.c.request_id == request_id
                    )
                )
            ).scalar_one()
        assert request_state == "expired"
        assert state["state"] == "running"
        assert state["success_sequence"] == 1
        assert state["spool_rows_pruned"] == 3
        assert state["binding_retention_receipt"] == receipt
        assert state["consecutive_failures"] == 0
        assert state["last_error_code"] is None

        async with worker.transaction() as connection:
            assert (
                await connection.execute(
                    text("SELECT operations.heartbeat_worker_maintenance(:instance)"),
                    {"instance": first_instance},
                )
            ).scalar_one() is True
            assert (
                await connection.execute(
                    text(
                        "SELECT operations.fail_worker_maintenance("
                        ":instance, 'runtime_spool_prune_failed')"
                    ),
                    {"instance": first_instance},
                )
            ).scalar_one() is True

        async with owner.transaction() as connection:
            degraded = (
                (
                    await connection.execute(
                        select(schema.worker_maintenance_state)
                    )
                )
                .mappings()
                .one()
            )
        assert degraded["state"] == "degraded"
        assert degraded["consecutive_failures"] == 1
        assert degraded["last_error_code"] == "runtime_spool_prune_failed"
        assert degraded["success_sequence"] == 1

        async with worker.transaction() as connection:
            recovered = (
                await connection.execute(
                    text(
                        "SELECT operations.run_worker_maintenance_cycle("
                        ":instance, 0)"
                    ),
                    {"instance": first_instance},
                )
            ).scalar_one()
            assert set(recovered) == set(receipt)
        async with owner.transaction() as connection:
            recovered_state = (
                (
                    await connection.execute(
                        select(schema.worker_maintenance_state)
                    )
                )
                .mappings()
                .one()
            )
        assert recovered_state["state"] == "running"
        assert recovered_state["success_sequence"] == 2
        assert recovered_state["consecutive_failures"] == 0
        assert recovered_state["last_error_code"] is None

        async with worker.transaction() as connection:
            assert (
                await connection.execute(
                    text("SELECT operations.stop_worker_maintenance(:instance)"),
                    {"instance": first_instance},
                )
            ).scalar_one() is True
            assert (
                await connection.execute(
                    text("SELECT operations.stop_worker_maintenance(:instance)"),
                    {"instance": first_instance},
                )
            ).scalar_one() is True
            assert (
                await connection.execute(
                    text("SELECT operations.heartbeat_worker_maintenance(:instance)"),
                    {"instance": first_instance},
                )
            ).scalar_one() is False

        async with worker.transaction() as connection:
            assert (
                await connection.execute(
                    text("SELECT operations.register_worker_maintenance(:instance)"),
                    {"instance": second_instance},
                )
            ).scalar_one() is True
            assert (
                await connection.execute(
                    text("SELECT operations.heartbeat_worker_maintenance(:instance)"),
                    {"instance": first_instance},
                )
            ).scalar_one() is False

        with pytest.raises(DBAPIError) as negative_spool:
            async with worker.transaction() as connection:
                await connection.execute(
                    text(
                        "SELECT operations.run_worker_maintenance_cycle("
                        ":instance, -1)"
                    ),
                    {"instance": second_instance},
                )
        assert sqlstate(negative_spool) == "22023"

        with pytest.raises(DBAPIError) as invalid_error:
            async with worker.transaction() as connection:
                await connection.execute(
                    text(
                        "SELECT operations.fail_worker_maintenance("
                        ":instance, 'raw_exception_text')"
                    ),
                    {"instance": second_instance},
                )
        assert sqlstate(invalid_error) == "22023"

        with pytest.raises(DBAPIError) as missing_error:
            async with worker.transaction() as connection:
                await connection.execute(
                    text(
                        "SELECT operations.fail_worker_maintenance("
                        ":instance, NULL)"
                    ),
                    {"instance": second_instance},
                )
        assert sqlstate(missing_error) == "22023"

        with pytest.raises(DBAPIError) as direct_expiry:
            async with worker.transaction() as connection:
                await connection.execute(
                    text(
                        "SELECT privacy.expire_principal_binding_work("
                        "transaction_timestamp())"
                    )
                )
        assert sqlstate(direct_expiry) == "42501"

        async with owner.transaction() as connection:
            persistence = (
                await connection.execute(
                    text(
                        "SELECT relpersistence, relrowsecurity, "
                        "relforcerowsecurity FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'operations' "
                        "AND c.relname = 'worker_maintenance_state'"
                    )
                )
            ).one()
            assert persistence == ("u", True, True)

            for role, can_select in {
                "home_agent_api": True,
                "home_agent_ingest": True,
                "home_agent_worker": True,
                "home_agent_rollout": True,
                "home_agent_erasure": False,
                "home_agent_binding_operator": False,
            }.items():
                acl = (
                    (
                        await connection.execute(
                            text(
                                "SELECT "
                                "has_table_privilege(:role, "
                                "'operations.worker_maintenance_state', "
                                "'SELECT') AS can_select, "
                                "has_table_privilege(:role, "
                                "'operations.worker_maintenance_state', "
                                "'INSERT') AS can_insert, "
                                "has_table_privilege(:role, "
                                "'operations.worker_maintenance_state', "
                                "'UPDATE') AS can_update, "
                                "has_table_privilege(:role, "
                                "'operations.worker_maintenance_state', "
                                "'DELETE') AS can_delete"
                            ),
                            {"role": role},
                        )
                    )
                    .mappings()
                    .one()
                )
                assert dict(acl) == {
                    "can_select": can_select,
                    "can_insert": False,
                    "can_update": False,
                    "can_delete": False,
                }

            function_acl = (
                (
                    await connection.execute(
                        text(
                            "SELECT "
                            "has_function_privilege('home_agent_worker', "
                            "'operations.run_worker_maintenance_cycle(uuid,bigint)', "
                            "'EXECUTE') AS worker_cycle, "
                            "has_function_privilege('home_agent_api', "
                            "'operations.run_worker_maintenance_cycle(uuid,bigint)', "
                            "'EXECUTE') AS api_cycle, "
                            "has_function_privilege('home_agent_worker', "
                            "'privacy.expire_principal_binding_work(timestamptz)', "
                            "'EXECUTE') AS worker_direct_expiry, "
                            "has_function_privilege('home_agent_erasure', "
                            "'privacy.expire_principal_binding_work(timestamptz)', "
                            "'EXECUTE') AS erasure_direct_expiry"
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert dict(function_acl) == {
                "worker_cycle": True,
                "api_cycle": False,
                "worker_direct_expiry": False,
                "erasure_direct_expiry": True,
            }

            function_security = (
                (
                    await connection.execute(
                        text(
                            "SELECT procedure.proname, owner.rolname, "
                            "procedure.prosecdef, procedure.proconfig "
                            "FROM pg_proc AS procedure "
                            "JOIN pg_namespace AS namespace "
                            "ON namespace.oid = procedure.pronamespace "
                            "JOIN pg_roles AS owner "
                            "ON owner.oid = procedure.proowner "
                            "WHERE namespace.nspname = 'operations' "
                            "AND procedure.proname IN ("
                            "'register_worker_maintenance',"
                            "'heartbeat_worker_maintenance',"
                            "'run_worker_maintenance_cycle',"
                            "'fail_worker_maintenance',"
                            "'stop_worker_maintenance')"
                        )
                    )
                )
                .mappings()
                .all()
            )
            assert {row["proname"] for row in function_security} == {
                "register_worker_maintenance",
                "heartbeat_worker_maintenance",
                "run_worker_maintenance_cycle",
                "fail_worker_maintenance",
                "stop_worker_maintenance",
            }
            assert all(
                row["rolname"] == "home_agent_owner"
                and row["prosecdef"] is True
                and row["proconfig"] == ["search_path=pg_catalog, pg_temp"]
                for row in function_security
            )

            view_sql = (
                await connection.execute(
                    text(
                        "SELECT pg_get_viewdef("
                        "'operations.phase2_rollout_evidence'::regclass, true)"
                    )
                )
            ).scalar_one()
            assert "suppressed_retention_worker_unavailable" in view_sql

        malformed_receipts = (
            {"proposals_expired": 0},
            {
                "proposals_expired": "0",
                "staged_requests_expired": 0,
                "pending_requests_expired": 0,
                "requests_expired": 0,
                "proposals_purged": 0,
                "requests_purged": 0,
            },
            {
                "proposals_expired": None,
                "staged_requests_expired": 0,
                "pending_requests_expired": 0,
                "requests_expired": 0,
                "proposals_purged": 0,
                "requests_purged": 0,
            },
        )
        for payload in malformed_receipts:
            with pytest.raises(DBAPIError) as malformed_receipt:
                async with owner.transaction() as connection:
                    attempt_time = datetime.now(UTC)
                    await connection.execute(
                        update(schema.worker_maintenance_state).values(
                            state="running",
                            maintenance_attempted_at=attempt_time,
                            maintenance_succeeded_at=attempt_time,
                            success_sequence=1,
                            consecutive_failures=0,
                            last_error_code=None,
                            spool_rows_pruned=0,
                            binding_retention_receipt=payload,
                        )
                    )
            assert sqlstate(malformed_receipt) == "23514"
    finally:
        async with owner.transaction() as connection:
            await connection.execute(
                delete(schema.principal_binding_requests).where(
                    schema.principal_binding_requests.c.request_id == request_id
                )
            )
            await connection.execute(delete(schema.worker_maintenance_state))
        for database in databases:
            await database.close()
