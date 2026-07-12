from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Mapping

from sqlalchemy import text

from .crypto import sha256_json
from .db import Database


WORKER_MAINTENANCE_STATE_KEY = "durable_worker"
WORKER_MAINTENANCE_KERNEL_VERSION = "worker-maintenance-cycle-v1"
WORKER_HEARTBEAT_STALE_AFTER = timedelta(seconds=45)
WORKER_MAINTENANCE_STALE_AFTER = timedelta(seconds=180)
WORKER_MAINTENANCE_FAILURE_CODES = frozenset(
    {
        "runtime_spool_prune_failed",
        "binding_retention_failed",
        "worker_loop_failed",
    }
)

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

BINDING_RETENTION_RECEIPT_KEYS = frozenset(
    {
        "proposals_expired",
        "staged_requests_expired",
        "pending_requests_expired",
        "requests_expired",
        "proposals_purged",
        "requests_purged",
    }
)


@dataclass(frozen=True, slots=True)
class WorkerMaintenanceStatus:
    """Content-free worker status with an internal rollout proof when current."""

    code: WorkerMaintenanceCode
    worker_instance_id: uuid.UUID | None = None
    success_sequence: int | None = None
    kernel_version: str | None = None
    maintenance_succeeded_at: datetime | None = None
    proof_digest: str | None = None

    @property
    def ready(self) -> bool:
        return self.code == "current"

    def as_public_dict(self) -> dict[str, WorkerMaintenanceCode]:
        # Instance IDs, sequence values, and timestamps are deliberately absent.
        return {"status": self.code}


def validate_binding_retention_receipt(value: Any) -> dict[str, int]:
    """Validate the exact content-free receipt returned by the DB kernel."""

    if not isinstance(value, Mapping) or set(value) != BINDING_RETENTION_RECEIPT_KEYS:
        raise ValueError("binding retention receipt schema is invalid")
    receipt: dict[str, int] = {}
    for key in BINDING_RETENTION_RECEIPT_KEYS:
        item = value[key]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ValueError("binding retention receipt count is invalid")
        receipt[key] = item
    if receipt["requests_expired"] != (
        receipt["staged_requests_expired"]
        + receipt["pending_requests_expired"]
    ):
        raise ValueError("binding retention receipt aggregate is invalid")
    return receipt


def worker_maintenance_proof_digest(
    *,
    worker_instance_id: uuid.UUID,
    success_sequence: int,
    kernel_version: str,
    maintenance_succeeded_at: datetime,
) -> str:
    if maintenance_succeeded_at.tzinfo is None:
        raise ValueError("maintenance success timestamp must be timezone-aware")
    canonical_success = (
        maintenance_succeeded_at.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    return sha256_json(
        {
            "worker_instance_id": str(worker_instance_id),
            "success_sequence": success_sequence,
            "kernel_version": kernel_version,
            "maintenance_succeeded_at": canonical_success,
        }
    )


class WorkerMaintenanceInspector:
    """Evaluate the singleton worker state using PostgreSQL's own clock."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def inspect(
        self,
        *,
        expected_instance_id: uuid.UUID | None = None,
        observed_after: datetime | None = None,
    ) -> WorkerMaintenanceStatus:
        try:
            async with self.database.transaction() as connection:
                return await self.inspect_in_transaction(
                    connection,
                    expected_instance_id=expected_instance_id,
                    observed_after=observed_after,
                )
        except MemoryError:
            raise
        except Exception:
            return WorkerMaintenanceStatus("unavailable")

    async def inspect_in_transaction(
        self,
        connection,
        *,
        database_now: datetime | None = None,
        expected_instance_id: uuid.UUID | None = None,
        observed_after: datetime | None = None,
    ) -> WorkerMaintenanceStatus:
        try:
            rows = (
                (
                    await connection.execute(
                        text(
                            "SELECT state.*, "
                            "pg_postmaster_start_time() AS postmaster_started_at, "
                            "clock_timestamp() AS database_now "
                            "FROM operations.worker_maintenance_state AS state "
                            "WHERE state.state_key = :state_key"
                        ),
                        {"state_key": WORKER_MAINTENANCE_STATE_KEY},
                    )
                )
                .mappings()
                .all()
            )
        except MemoryError:
            raise
        except Exception:
            return WorkerMaintenanceStatus("unavailable")
        if not rows:
            return WorkerMaintenanceStatus("missing")
        if len(rows) != 1:
            return WorkerMaintenanceStatus("kernel_mismatch")
        try:
            row = dict(rows[0])
            # The liveness clock must come from the same PostgreSQL statement
            # and snapshot as the state row. ``database_now`` exists only as a
            # fallback for pure unit fakes that do not evaluate SQL aliases; it
            # never overrides a clock returned by PostgreSQL.
            if "database_now" not in row:
                if database_now is None:
                    return WorkerMaintenanceStatus("kernel_mismatch")
                row["database_now"] = database_now
            return self._evaluate(
                row,
                expected_instance_id=expected_instance_id,
                observed_after=observed_after,
            )
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            return WorkerMaintenanceStatus("kernel_mismatch")

    @staticmethod
    def _evaluate(
        row: Mapping[str, Any],
        *,
        expected_instance_id: uuid.UUID | None,
        observed_after: datetime | None,
    ) -> WorkerMaintenanceStatus:
        database_now = _aware_datetime(row["database_now"])
        observed_after = (
            None if observed_after is None else _aware_datetime(observed_after)
        )
        postmaster_started_at = _aware_datetime(row["postmaster_started_at"])
        database_started_at = _aware_datetime(row["database_started_at"])
        started_at = _aware_datetime(row["started_at"])
        heartbeat_at = _optional_aware_datetime(row["heartbeat_at"])
        maintenance_attempted_at = _optional_aware_datetime(
            row["maintenance_attempted_at"]
        )
        maintenance_succeeded_at = _optional_aware_datetime(
            row["maintenance_succeeded_at"]
        )
        stopped_at = _optional_aware_datetime(row["stopped_at"])
        updated_at = _aware_datetime(row["updated_at"])

        timestamps = (
            postmaster_started_at,
            database_started_at,
            started_at,
            heartbeat_at,
            maintenance_attempted_at,
            maintenance_succeeded_at,
            stopped_at,
            updated_at,
        )
        if (
            observed_after is not None and observed_after > database_now
        ) or any(value is not None and value > database_now for value in timestamps):
            return WorkerMaintenanceStatus("future")

        instance_id = _uuid(row["worker_instance_id"])
        if row["state_key"] != WORKER_MAINTENANCE_STATE_KEY:
            return WorkerMaintenanceStatus("kernel_mismatch")
        if (
            expected_instance_id is not None
            and instance_id != expected_instance_id
        ) or database_started_at != postmaster_started_at:
            return WorkerMaintenanceStatus("instance_unobserved")
        if started_at < postmaster_started_at or (
            heartbeat_at is not None and heartbeat_at < postmaster_started_at
        ):
            return WorkerMaintenanceStatus("instance_unobserved")

        kernel_version = row["kernel_version"]
        if kernel_version != WORKER_MAINTENANCE_KERNEL_VERSION:
            return WorkerMaintenanceStatus("kernel_mismatch")

        state = row["state"]
        if state == "stopping" or stopped_at is not None:
            return WorkerMaintenanceStatus("stopped")
        if state not in {"starting", "running", "degraded"}:
            return WorkerMaintenanceStatus("kernel_mismatch")

        heartbeat_sequence = _nonnegative_int(row["heartbeat_sequence"])
        if heartbeat_at is None or heartbeat_sequence < 1:
            return WorkerMaintenanceStatus("instance_unobserved")
        if observed_after is not None and heartbeat_at < observed_after:
            return WorkerMaintenanceStatus("instance_unobserved")
        if database_now - heartbeat_at > WORKER_HEARTBEAT_STALE_AFTER:
            return WorkerMaintenanceStatus("heartbeat_stale")

        consecutive_failures = _nonnegative_int(row["consecutive_failures"])
        last_error_code = row["last_error_code"]
        if last_error_code is not None and (
            not isinstance(last_error_code, str)
            or last_error_code not in WORKER_MAINTENANCE_FAILURE_CODES
        ):
            return WorkerMaintenanceStatus("kernel_mismatch")
        if state == "degraded" or consecutive_failures or last_error_code is not None:
            return WorkerMaintenanceStatus("maintenance_failed")

        success_sequence = _nonnegative_int(row["success_sequence"])
        if maintenance_succeeded_at is None or success_sequence < 1:
            return WorkerMaintenanceStatus("maintenance_missing")
        if database_now - maintenance_succeeded_at > WORKER_MAINTENANCE_STALE_AFTER:
            return WorkerMaintenanceStatus("maintenance_stale")
        if maintenance_succeeded_at < postmaster_started_at:
            return WorkerMaintenanceStatus("instance_unobserved")

        spool_rows_pruned = row["spool_rows_pruned"]
        if (
            isinstance(spool_rows_pruned, bool)
            or not isinstance(spool_rows_pruned, int)
            or spool_rows_pruned < 0
        ):
            return WorkerMaintenanceStatus("kernel_mismatch")
        validate_binding_retention_receipt(row["binding_retention_receipt"])
        if state != "running":
            return WorkerMaintenanceStatus("kernel_mismatch")

        proof_digest = worker_maintenance_proof_digest(
            worker_instance_id=instance_id,
            success_sequence=success_sequence,
            kernel_version=kernel_version,
            maintenance_succeeded_at=maintenance_succeeded_at,
        )
        return WorkerMaintenanceStatus(
            "current",
            worker_instance_id=instance_id,
            success_sequence=success_sequence,
            kernel_version=kernel_version,
            maintenance_succeeded_at=maintenance_succeeded_at,
            proof_digest=proof_digest,
        )


def _aware_datetime(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("database timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _optional_aware_datetime(value: Any) -> datetime | None:
    return None if value is None else _aware_datetime(value)


def _uuid(value: Any) -> uuid.UUID:
    parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    if parsed.variant != uuid.RFC_4122 or parsed.version not in {4, 7}:
        raise ValueError("worker instance UUID version or variant is invalid")
    return parsed


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("worker sequence/count is invalid")
    return value
