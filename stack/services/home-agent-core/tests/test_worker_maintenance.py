from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import DBAPIError

from app.ledger import LedgerIntegrityError
from app.maintenance import (
    WORKER_MAINTENANCE_KERNEL_VERSION,
    WorkerMaintenanceInspector,
    validate_binding_retention_receipt,
    worker_maintenance_proof_digest,
)
from app.worker import (
    DurableWorker,
    WorkerMaintenanceFatal,
    WorkerMaintenanceLeaseLost,
    WorkerMaintenanceRegistrationContended,
    WorkerMaintenanceRetryable,
)


NOW = datetime(2026, 7, 12, 22, 0, tzinfo=UTC)
POSTMASTER_STARTED = NOW - timedelta(hours=1)
INSTANCE_ID = uuid.UUID("6ccf6ca1-aa0d-4bc4-b953-1bff7777b7bd")
OTHER_INSTANCE_ID = uuid.UUID("4cce8b39-f6dc-42ca-9491-556c57294f58")
RETENTION_RECEIPT = {
    "proposals_expired": 1,
    "staged_requests_expired": 1,
    "pending_requests_expired": 2,
    "requests_expired": 3,
    "proposals_purged": 4,
    "requests_purged": 5,
}


class Result:
    def __init__(self, *, scalar=None, rows=None) -> None:
        self.scalar = scalar
        self.rows = [] if rows is None else rows

    def scalar_one(self):
        return self.scalar

    def mappings(self):
        return self

    def all(self):
        return self.rows


class Connection:
    def __init__(self, results) -> None:
        self.results = list(results)
        self.calls = []

    async def execute(self, statement, parameters=None):
        self.calls.append((statement, parameters))
        if not self.results:
            raise AssertionError("unexpected database call")
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class Database:
    def __init__(self, results=()) -> None:
        self.connection = Connection(results)

    @asynccontextmanager
    async def transaction(self, **_kwargs):
        yield self.connection


class SimulatedDatabaseFailure(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def database_failure(sqlstate: str) -> DBAPIError:
    return DBAPIError(
        "test statement",
        {},
        SimulatedDatabaseFailure(sqlstate),
        connection_invalidated=False,
    )


def maintenance_row(**changes):
    row = {
        "state_key": "durable_worker",
        "worker_instance_id": INSTANCE_ID,
        "database_started_at": POSTMASTER_STARTED,
        "kernel_version": WORKER_MAINTENANCE_KERNEL_VERSION,
        "state": "running",
        "started_at": POSTMASTER_STARTED + timedelta(seconds=1),
        "heartbeat_at": NOW - timedelta(seconds=5),
        "heartbeat_sequence": 9,
        "maintenance_attempted_at": NOW - timedelta(seconds=30),
        "maintenance_succeeded_at": NOW - timedelta(seconds=30),
        "success_sequence": 3,
        "consecutive_failures": 0,
        "last_error_code": None,
        "stopped_at": None,
        "spool_rows_pruned": 2,
        "binding_retention_receipt": dict(RETENTION_RECEIPT),
        "updated_at": NOW - timedelta(seconds=5),
        "postmaster_started_at": POSTMASTER_STARTED,
    }
    row.update(changes)
    return row


async def inspect_row(
    row,
    *,
    expected_instance_id=INSTANCE_ID,
    observed_after=None,
):
    connection = Connection([Result(rows=[] if row is None else [row])])
    return await WorkerMaintenanceInspector(Database()).inspect_in_transaction(
        connection,
        database_now=NOW,
        expected_instance_id=expected_instance_id,
        observed_after=observed_after,
    )


@pytest.mark.asyncio
async def test_current_status_has_deterministic_internal_proof_and_public_code_only():
    status = await inspect_row(maintenance_row())

    assert status.ready is True
    assert status.code == "current"
    assert status.worker_instance_id == INSTANCE_ID
    assert status.success_sequence == 3
    assert status.kernel_version == WORKER_MAINTENANCE_KERNEL_VERSION
    assert status.maintenance_succeeded_at == NOW - timedelta(seconds=30)
    assert status.proof_digest == worker_maintenance_proof_digest(
        worker_instance_id=INSTANCE_ID,
        success_sequence=3,
        kernel_version=WORKER_MAINTENANCE_KERNEL_VERSION,
        maintenance_succeeded_at=NOW - timedelta(seconds=30),
    )
    assert status.as_public_dict() == {"status": "current"}
    assert "instance" not in str(status.as_public_dict())
    assert "2026" not in str(status.as_public_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "expected", "expected_instance_id", "observed_after"),
    [
        (None, "missing", INSTANCE_ID, None),
        (maintenance_row(), "instance_unobserved", OTHER_INSTANCE_ID, None),
        (
            maintenance_row(),
            "instance_unobserved",
            INSTANCE_ID,
            NOW - timedelta(seconds=1),
        ),
        (
            maintenance_row(heartbeat_at=NOW - timedelta(seconds=46)),
            "heartbeat_stale",
            INSTANCE_ID,
            None,
        ),
        (
            maintenance_row(
                state="starting",
                maintenance_succeeded_at=None,
                success_sequence=0,
                spool_rows_pruned=None,
                binding_retention_receipt=None,
            ),
            "maintenance_missing",
            INSTANCE_ID,
            None,
        ),
        (
            maintenance_row(
                state="degraded",
                consecutive_failures=1,
                last_error_code="worker_loop_failed",
            ),
            "maintenance_failed",
            INSTANCE_ID,
            None,
        ),
        (
            maintenance_row(
                maintenance_succeeded_at=NOW - timedelta(seconds=181)
            ),
            "maintenance_stale",
            INSTANCE_ID,
            None,
        ),
        (
            maintenance_row(state="stopping", stopped_at=NOW),
            "stopped",
            INSTANCE_ID,
            None,
        ),
        (
            maintenance_row(heartbeat_at=NOW + timedelta(microseconds=1)),
            "future",
            INSTANCE_ID,
            None,
        ),
        (
            maintenance_row(kernel_version="worker-maintenance-cycle-v2"),
            "kernel_mismatch",
            INSTANCE_ID,
            None,
        ),
        (
            maintenance_row(
                worker_instance_id=uuid.UUID(
                    "6ccf6ca1-aa0d-1bc4-b953-1bff7777b7bd"
                )
            ),
            "kernel_mismatch",
            None,
            None,
        ),
        (
            maintenance_row(
                state="degraded",
                consecutive_failures=1,
                last_error_code="private_unbounded_error",
            ),
            "kernel_mismatch",
            INSTANCE_ID,
            None,
        ),
        (
            maintenance_row(binding_retention_receipt={"wrong": 1}),
            "kernel_mismatch",
            INSTANCE_ID,
            None,
        ),
    ],
)
async def test_inspector_fails_closed_for_each_state(
    row, expected, expected_instance_id, observed_after
):
    status = await inspect_row(
        row,
        expected_instance_id=expected_instance_id,
        observed_after=observed_after,
    )

    assert status.code == expected
    assert status.ready is False
    assert status.as_public_dict() == {"status": expected}
    assert status.proof_digest is None


@pytest.mark.asyncio
async def test_inspector_uses_database_clock_and_postmaster_in_existing_transaction():
    connection = Connection(
        [
            Result(rows=[maintenance_row(database_now=NOW)]),
        ]
    )

    status = await WorkerMaintenanceInspector(Database()).inspect_in_transaction(
        connection,
        expected_instance_id=INSTANCE_ID,
    )

    assert status.code == "current"
    assert len(connection.calls) == 1
    assert "clock_timestamp" in str(connection.calls[0][0])
    assert "pg_postmaster_start_time" in str(connection.calls[0][0])


@pytest.mark.asyncio
async def test_database_clock_from_state_query_wins_over_test_fallback():
    connection = Connection(
        [Result(rows=[maintenance_row(database_now=NOW)])]
    )

    status = await WorkerMaintenanceInspector(Database()).inspect_in_transaction(
        connection,
        database_now=NOW - timedelta(days=1),
        expected_instance_id=INSTANCE_ID,
    )

    assert status.code == "current"


@pytest.mark.asyncio
async def test_inspector_returns_unavailable_without_leaking_database_failure():
    connection = Connection([RuntimeError("canary-private-database-error")])

    status = await WorkerMaintenanceInspector(Database()).inspect_in_transaction(
        connection
    )

    assert status.as_public_dict() == {"status": "unavailable"}


def test_retention_receipt_is_exact_and_aggregate_checked():
    assert validate_binding_retention_receipt(RETENTION_RECEIPT) == RETENTION_RECEIPT
    with pytest.raises(ValueError, match="schema"):
        validate_binding_retention_receipt({**RETENTION_RECEIPT, "extra": 0})
    with pytest.raises(ValueError, match="count"):
        validate_binding_retention_receipt(
            {**RETENTION_RECEIPT, "proposals_expired": True}
        )
    with pytest.raises(ValueError, match="aggregate"):
        validate_binding_retention_receipt(
            {**RETENTION_RECEIPT, "requests_expired": 99}
        )


class Spool:
    def __init__(self, result=3, error=None) -> None:
        self.result = result
        self.error = error
        self.now = None

    def prune(self, *, now):
        self.now = now
        if self.error is not None:
            raise self.error
        return self.result


class Ledger:
    def __init__(self, error=None) -> None:
        self.error = error
        self.calls = 0

    def head(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return object()


@pytest.mark.asyncio
async def test_immediate_cycle_uses_db_time_prunes_verifies_and_validates_kernel():
    database = Database([Result(scalar=NOW), Result(scalar=RETENTION_RECEIPT)])
    spool = Spool(result=7)
    ledger = Ledger()
    worker = DurableWorker(database, ledger, spool)  # type: ignore[arg-type]

    receipt = await worker._run_maintenance_cycle()

    assert receipt == RETENTION_RECEIPT
    assert spool.now == NOW
    assert ledger.calls == 1
    cycle_statement = database.connection.calls[1][0]
    assert "run_worker_maintenance_cycle" in str(cycle_statement)
    assert 7 in cycle_statement.compile().params.values()


@pytest.mark.asyncio
@pytest.mark.parametrize("sqlstate", ("40001", "55000"))
async def test_cycle_fence_loss_clears_local_registration_before_propagating(
    sqlstate: str,
) -> None:
    database = Database([Result(scalar=NOW), database_failure(sqlstate)])
    worker = DurableWorker(
        database,
        Ledger(),  # type: ignore[arg-type]
        Spool(),  # type: ignore[arg-type]
    )
    worker._maintenance_registered = True

    with pytest.raises(WorkerMaintenanceLeaseLost):
        await worker._run_maintenance_cycle()

    assert worker._maintenance_registered is False


@pytest.mark.asyncio
async def test_rejected_registration_is_contention_not_acquired_lease_loss() -> None:
    worker = DurableWorker(
        Database([Result(scalar=False)]),
        Ledger(),  # type: ignore[arg-type]
        Spool(),  # type: ignore[arg-type]
    )

    with pytest.raises(WorkerMaintenanceRegistrationContended):
        await worker._register_maintenance()

    assert worker._maintenance_registered is False


@pytest.mark.asyncio
async def test_invalid_registration_receipt_is_fatal_not_standby() -> None:
    worker = DurableWorker(
        Database([Result(scalar=None)]),
        Ledger(),  # type: ignore[arg-type]
        Spool(),  # type: ignore[arg-type]
    )

    with pytest.raises(WorkerMaintenanceFatal, match="receipt"):
        await worker._register_maintenance()

    assert worker._maintenance_registered is False


@pytest.mark.asyncio
async def test_ledger_integrity_failure_is_recorded_once_then_fatal():
    worker = DurableWorker(
        Database(),
        Ledger(LedgerIntegrityError("private detail")),  # type: ignore[arg-type]
        Spool(),  # type: ignore[arg-type]
    )
    failures = []

    async def database_now():
        return NOW

    async def record(code, _exc):
        failures.append(code)

    worker._database_now = database_now  # type: ignore[method-assign]
    worker._record_failure_best_effort = record  # type: ignore[method-assign]

    with pytest.raises(WorkerMaintenanceFatal, match="integrity"):
        await worker._run_maintenance_cycle()
    assert failures == ["worker_loop_failed"]


class ClockWorker(DurableWorker):
    def __init__(self):
        super().__init__(Database(), Ledger(), Spool())  # type: ignore[arg-type]
        self.database_clock_calls = 0
        self.seen = []

    async def _database_now(self):
        self.database_clock_calls += 1
        return NOW

    async def _claim_one(self, now):
        self.seen.append(now)
        return None

    async def _claim_topic(self, _topic, now):
        self.seen.append(now)
        return None

    async def _complete_no_effect_once(self, now):
        self.seen.append(now)
        return False


@pytest.mark.asyncio
async def test_production_run_once_uses_db_clock_but_explicit_test_time_is_preserved():
    worker = ClockWorker()

    assert await worker.run_once() is False
    assert worker.database_clock_calls == 1
    assert worker.seen == [NOW, NOW, NOW]

    worker.seen.clear()
    explicit = NOW - timedelta(days=1)
    assert await worker.run_once(now=explicit) is False
    assert worker.database_clock_calls == 1
    assert worker.seen == [explicit, explicit, explicit]


class SupervisedWorker(DurableWorker):
    def __init__(self, *, lose_lease=False):
        super().__init__(
            Database(), Ledger(), Spool(), poll_seconds=0.001  # type: ignore[arg-type]
        )
        self.events = []
        self.iterations = 0
        self.lose_lease = lose_lease

    async def _register_maintenance(self):
        self.events.append("register")
        self._maintenance_registered = True

    async def _run_maintenance_cycle(self):
        self.events.append("cycle")
        if self.iterations == 0:
            raise WorkerMaintenanceRetryable(
                "runtime_spool_prune_failed", RuntimeError("private")
            )
        return RETENTION_RECEIPT

    async def _heartbeat_maintenance(self):
        self.events.append("heartbeat")
        if self.lose_lease:
            self._maintenance_registered = False
            raise WorkerMaintenanceLeaseLost("lost")

    async def _record_failure_best_effort(self, code, _exc):
        self.events.append(f"failure:{code}")

    async def run_once(self, *, now=None):
        self.events.append("run_once")
        self.iterations += 1
        if self.iterations == 1:
            raise RuntimeError("private outbox failure")
        self._test_stop.set()
        return False

    async def _stop_maintenance(self):
        self.events.append("stop")
        self._maintenance_registered = False

    async def run_for_test(self):
        self._test_stop = __import__("asyncio").Event()
        await self.run(self._test_stop)


class ContendedStartupWorker(DurableWorker):
    def __init__(self) -> None:
        super().__init__(
            Database(),
            Ledger(),
            Spool(),
            poll_seconds=0.001,  # type: ignore[arg-type]
        )
        self.events: list[str] = []
        self.registration_attempts = 0

    async def _register_maintenance(self) -> None:
        self.events.append("register")
        self.registration_attempts += 1
        if self.registration_attempts == 1:
            raise WorkerMaintenanceRegistrationContended("contended")
        self._maintenance_registered = True

    async def _run_maintenance_cycle(self):
        self.events.append("cycle")
        return RETENTION_RECEIPT

    async def _heartbeat_maintenance(self) -> None:
        self.events.append("heartbeat")

    async def run_once(self, *, now=None) -> bool:
        self.events.append("run_once")
        self._test_stop.set()
        return False

    async def _stop_maintenance(self) -> None:
        self.events.append("stop")
        self._maintenance_registered = False

    async def run_for_test(self) -> None:
        self._test_stop = __import__("asyncio").Event()
        await self.run(self._test_stop)


@pytest.mark.asyncio
async def test_registration_contention_retries_without_running_work_or_exiting(
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.worker.WORKER_RETRY_SECONDS", 0.0)
    worker = ContendedStartupWorker()

    await worker.run_for_test()

    assert worker.events == [
        "register",
        "register",
        "cycle",
        "heartbeat",
        "run_once",
        "stop",
    ]


@pytest.mark.asyncio
async def test_fatal_registration_contract_failure_propagates_from_run() -> None:
    worker = DurableWorker(
        Database(), Ledger(), Spool(), poll_seconds=0.001  # type: ignore[arg-type]
    )

    async def fatal_registration() -> None:
        raise WorkerMaintenanceFatal("invalid registration receipt")

    worker._register_maintenance = fatal_registration  # type: ignore[method-assign]

    with pytest.raises(WorkerMaintenanceFatal, match="receipt"):
        await worker.run(__import__("asyncio").Event())


@pytest.mark.asyncio
async def test_run_supervises_retryable_failures_without_double_recording_or_dying():
    worker = SupervisedWorker()

    await worker.run_for_test()

    assert worker.events.count("failure:runtime_spool_prune_failed") == 1
    assert worker.events.count("failure:worker_loop_failed") == 1
    assert worker.events[0] == "register"
    assert worker.events[-1] == "stop"
    assert worker.iterations == 2


@pytest.mark.asyncio
async def test_fenced_lease_loss_propagates_instead_of_becoming_a_retry_loop():
    worker = SupervisedWorker(lose_lease=True)

    with pytest.raises(WorkerMaintenanceLeaseLost):
        await worker.run_for_test()
    assert worker.events.count("heartbeat") == 1
    assert "run_once" not in worker.events


@pytest.mark.asyncio
async def test_catastrophic_memory_failure_is_not_supervised_as_retryable():
    worker = SupervisedWorker()

    async def catastrophic_cycle():
        raise MemoryError("do not retry")

    worker._run_maintenance_cycle = catastrophic_cycle  # type: ignore[method-assign]

    with pytest.raises(MemoryError, match="do not retry"):
        await worker.run_for_test()
    assert not any(event.startswith("failure:") for event in worker.events)
