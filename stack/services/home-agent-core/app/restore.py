from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from . import schema
from .db import Database
from .erasure import apply_descriptor_erasure
from .ledger import (
    ZERO_HASH,
    EncryptedErasureLedger,
    LedgerHead,
    LedgerIntegrityError,
    read_ledger_head,
)


@dataclass(frozen=True, slots=True)
class RestoreGateStatus:
    current: bool
    code: str
    ledger_epoch: int | None
    database_epoch: int | None


@dataclass(frozen=True, slots=True)
class OutboxHealth:
    ready: bool
    code: str
    incomplete_erasure: int
    failed_erasure: int
    unsupported: int


async def outbox_health(database: Database) -> OutboxHealth:
    try:
        async with database.transaction() as connection:
            transaction_time = func.transaction_timestamp()
            strict_erasure = (
                (schema.outbox.c.topic == "privacy.erasure.completed")
                & (schema.outbox.c.state != "complete")
            )
            due_auto_expiry = (
                (schema.outbox.c.topic == "privacy.person.auto_expire")
                & (schema.outbox.c.state != "complete")
                & (
                    (schema.outbox.c.state != "pending")
                    | (schema.outbox.c.available_at <= transaction_time)
                )
            )
            row = (
                (
                    await connection.execute(
                        select(
                            func.count()
                            .filter(
                                strict_erasure | due_auto_expiry,
                            )
                            .label("incomplete_erasure"),
                            func.count()
                            .filter(
                                schema.outbox.c.topic.in_(
                                    [
                                        "privacy.erasure.completed",
                                        "privacy.person.auto_expire",
                                    ]
                                ),
                                schema.outbox.c.state == "failed",
                            )
                            .label("failed_erasure"),
                            func.count()
                            .filter(
                                schema.outbox.c.state != "complete",
                                schema.outbox.c.topic.not_in(
                                    [
                                        "privacy.erasure.completed",
                                        "privacy.person.auto_expire",
                                        "memory.committed",
                                        "memory.descriptor.correction",
                                        "memory.descriptor.retraction",
                                    ]
                                ),
                            )
                            .label("unsupported"),
                        )
                    )
                )
                .mappings()
                .one()
            )
    except Exception:
        return OutboxHealth(False, "unavailable", 0, 0, 0)
    if row["failed_erasure"]:
        code = "erasure_receipt_failed"
    elif row["unsupported"]:
        code = "unsupported_outbox_backlog"
    elif row["incomplete_erasure"]:
        code = "erasure_ledger_backlog"
    else:
        code = "current"
    return OutboxHealth(
        ready=(
            not row["incomplete_erasure"]
            and not row["failed_erasure"]
            and not row["unsupported"]
        ),
        code=code,
        incomplete_erasure=row["incomplete_erasure"],
        failed_erasure=row["failed_erasure"],
        unsupported=row["unsupported"],
    )


class RestoreQuarantineGate:
    """Compare backup-restored database state with the independent ledger head."""

    def __init__(
        self,
        database: Database,
        head_path,
        *,
        cache_seconds: float = 1.0,
    ) -> None:
        self.database = database
        self.head_path = head_path
        self.cache_seconds = cache_seconds
        self._lock = asyncio.Lock()
        self._cached: tuple[float, RestoreGateStatus] | None = None

    async def status(self, *, force: bool = False) -> RestoreGateStatus:
        now = time.monotonic()
        if (
            not force
            and self._cached is not None
            and now - self._cached[0] <= self.cache_seconds
        ):
            return self._cached[1]
        async with self._lock:
            now = time.monotonic()
            if (
                not force
                and self._cached is not None
                and now - self._cached[0] <= self.cache_seconds
            ):
                return self._cached[1]
            result = await self._load_status()
            self._cached = (now, result)
            return result

    async def _load_status(self) -> RestoreGateStatus:
        try:
            head = await asyncio.to_thread(read_ledger_head, self.head_path)
        except LedgerIntegrityError:
            return RestoreGateStatus(False, "ledger_head_unavailable", None, None)
        try:
            async with self.database.transaction() as connection:
                row = (
                    (
                        await connection.execute(
                            select(schema.erasure_ledger_state).where(
                                schema.erasure_ledger_state.c.state_key == "current"
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
        except Exception:
            return RestoreGateStatus(
                False, "ledger_state_unavailable", head.epoch, None
            )
        database_epoch = row["recorded_epoch"] if row else 0
        database_hash = row["recorded_head_hash"] if row else ZERO_HASH
        if database_epoch > head.epoch:
            return RestoreGateStatus(
                False, "database_epoch_ahead", head.epoch, database_epoch
            )
        if database_epoch < head.epoch:
            return RestoreGateStatus(
                False, "erasure_replay_required", head.epoch, database_epoch
            )
        if database_hash != head.head_hash:
            return RestoreGateStatus(
                False, "ledger_head_diverged", head.epoch, database_epoch
            )
        return RestoreGateStatus(True, "current", head.epoch, database_epoch)


class RestoreReplay:
    def __init__(self, database: Database, ledger: EncryptedErasureLedger) -> None:
        self.database = database
        self.ledger = ledger

    async def replay(self) -> int:
        head = await asyncio.to_thread(self.ledger.head)
        state = await self._database_state()
        if state.recorded_epoch > head.epoch:
            raise LedgerIntegrityError("database erasure epoch is ahead of ledger")
        if (
            state.recorded_epoch == head.epoch
            and state.recorded_head_hash != head.head_hash
        ):
            raise LedgerIntegrityError("database and erasure ledger heads diverge")
        all_entries = await asyncio.to_thread(self.ledger.entries_after, 0)
        expected_database_hash = (
            ZERO_HASH
            if state.recorded_epoch == 0
            else all_entries[state.recorded_epoch - 1].record_hash
        )
        if state.recorded_head_hash != expected_database_hash:
            raise LedgerIntegrityError("database ledger prefix hash diverges")
        entries = [entry for entry in all_entries if entry.epoch > state.recorded_epoch]
        applied = 0
        previous_epoch = state.recorded_epoch
        for entry in entries:
            if entry.epoch != previous_epoch + 1:
                raise LedgerIntegrityError("erasure replay epoch is not contiguous")
            async with self.database.transaction(
                principal_id=entry.record.principal_id, serializable=True
            ) as connection:
                existing = (
                    (
                        await connection.execute(
                            select(schema.erasure_replay_receipts).where(
                                schema.erasure_replay_receipts.c.ledger_epoch
                                == entry.epoch
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
                if entry.record.subject_kind == "person":
                    if existing is not None:
                        if (
                            existing["outbox_id"] != entry.record.outbox_id
                            or existing["record_hash"] != entry.record_hash
                            or existing["record_digest"] != entry.record_digest
                        ):
                            raise LedgerIntegrityError(
                                "person erasure replay receipt conflicts with ledger"
                            )
                    else:
                        await self._replay_person_auto_expiry(connection, entry)
                        await connection.execute(
                            insert(schema.erasure_replay_receipts).values(
                                ledger_epoch=entry.epoch,
                                outbox_id=entry.record.outbox_id,
                                erasure_request_id=entry.record.erasure_request_id,
                                record_hash=entry.record_hash,
                                record_digest=entry.record_digest,
                            )
                        )
                        applied += 1
                    await self._set_database_state(
                        connection,
                        LedgerHead(entry.epoch, entry.record_hash),
                    )
                    await connection.execute(
                        update(schema.outbox)
                        .where(
                            schema.outbox.c.outbox_id == entry.record.outbox_id,
                            schema.outbox.c.topic == "privacy.erasure.completed",
                        )
                        .values(
                            state="complete",
                            claim_token=None,
                            claimed_at=None,
                            completed_at=datetime.now(UTC),
                            last_error_code=None,
                        )
                    )
                    previous_epoch = entry.epoch
                    continue
                if existing is not None:
                    if (
                        existing["outbox_id"] != entry.record.outbox_id
                        or existing["record_hash"] != entry.record_hash
                        or existing["record_digest"] != entry.record_digest
                    ):
                        raise LedgerIntegrityError(
                            "erasure replay receipt conflicts with ledger"
                        )
                else:
                    replay_scope = {
                        "replayed_from_ledger": True,
                        "descriptor_fact_id": str(entry.record.fact_id),
                        "checkpoint_affected": entry.record.checkpoint_affected,
                        "backup_expiry_at": (
                            entry.record.backup_expiry_at.isoformat()
                            if entry.record.backup_expiry_at
                            else None
                        ),
                        "exact_residuals": entry.record.exact_residual_codes,
                    }
                    request = (
                        (
                            await connection.execute(
                                select(schema.erasure_requests)
                                .where(
                                    schema.erasure_requests.c.erasure_request_id
                                    == entry.record.erasure_request_id
                                )
                                .with_for_update()
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if request is None:
                        principal_exists = (
                            await connection.execute(
                                select(schema.principals.c.principal_id).where(
                                    schema.principals.c.principal_id
                                    == entry.record.principal_id
                                )
                            )
                        ).scalar_one_or_none()
                        if principal_exists is not None:
                            await connection.execute(
                                insert(schema.erasure_requests).values(
                                    erasure_request_id=entry.record.erasure_request_id,
                                    principal_id=entry.record.principal_id,
                                    scope=replay_scope,
                                    state="complete",
                                    policy_digest=entry.record.policy_digest,
                                    completed_at=entry.record.completed_at,
                                )
                            )
                    else:
                        existing_fact = request["scope"].get("descriptor_fact_id")
                        if (
                            request["principal_id"] != entry.record.principal_id
                            or (
                                existing_fact is not None
                                and existing_fact != str(entry.record.fact_id)
                            )
                            or request["policy_digest"] != entry.record.policy_digest
                        ):
                            raise LedgerIntegrityError(
                                "restored erasure request conflicts with ledger"
                            )
                        await connection.execute(
                            update(schema.erasure_requests)
                            .where(
                                schema.erasure_requests.c.erasure_request_id
                                == entry.record.erasure_request_id
                            )
                            .values(
                                state="complete",
                                completed_at=entry.record.completed_at,
                            )
                        )
                    await apply_descriptor_erasure(
                        connection,
                        principal_id=entry.record.principal_id,
                        fact_id=entry.record.fact_id,
                        erasure_request_id=entry.record.erasure_request_id,
                        now=datetime.now(UTC),
                        require_existing=False,
                    )
                    await connection.execute(
                        insert(schema.erasure_replay_receipts).values(
                            ledger_epoch=entry.epoch,
                            outbox_id=entry.record.outbox_id,
                            erasure_request_id=entry.record.erasure_request_id,
                            record_hash=entry.record_hash,
                            record_digest=entry.record_digest,
                        )
                    )
                    applied += 1
                await self._set_database_state(
                    connection,
                    LedgerHead(entry.epoch, entry.record_hash),
                )
                await connection.execute(
                    update(schema.outbox)
                    .where(
                        schema.outbox.c.outbox_id == entry.record.outbox_id,
                        schema.outbox.c.topic == "privacy.erasure.completed",
                    )
                    .values(
                        state="complete",
                        claim_token=None,
                        claimed_at=None,
                        completed_at=datetime.now(UTC),
                        last_error_code=None,
                    )
                )
            previous_epoch = entry.epoch
        final_state = await self._database_state()
        if (
            final_state.recorded_epoch != head.epoch
            or final_state.recorded_head_hash != head.head_hash
        ):
            raise LedgerIntegrityError("restore replay did not reach ledger head")
        return applied

    @staticmethod
    async def _replay_person_auto_expiry(connection, entry) -> None:
        person_id = entry.record.person_id
        if person_id is None:
            raise LedgerIntegrityError("person erasure ledger subject is incomplete")
        replay_counts = None
        schedule = (
            (
                await connection.execute(
                    select(schema.auto_expiry_schedules)
                    .where(
                        schema.auto_expiry_schedules.c.schedule_id
                        == entry.record.erasure_request_id,
                        schema.auto_expiry_schedules.c.person_id == person_id,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if schedule is None:
            # A backup predating both the person and its atomic directive/
            # schedule transaction contains nothing that can be resurrected.
            person_exists = (
                await connection.execute(
                    select(schema.people.c.person_id).where(
                        schema.people.c.person_id == person_id
                    )
                )
            ).scalar_one_or_none()
            if person_exists is not None:
                replay_counts = (
                    await connection.execute(
                        select(
                            func.privacy.replay_person_auto_expiry(
                                entry.record.erasure_request_id,
                                person_id,
                                entry.record.outbox_id,
                            )
                        )
                    )
                ).scalar_one()
                if not isinstance(replay_counts, dict):
                    raise LedgerIntegrityError("person erasure replay kernel failed")
                schedule = (
                    (
                        await connection.execute(
                            select(schema.auto_expiry_schedules).where(
                                schema.auto_expiry_schedules.c.schedule_id
                                == entry.record.erasure_request_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            else:
                return

        auto_receipt = (
            (
                await connection.execute(
                    select(schema.auto_expiry_receipts).where(
                        schema.auto_expiry_receipts.c.schedule_id
                        == schedule["schedule_id"]
                    )
                )
            )
            .mappings()
            .first()
        )
        if auto_receipt is None:
            counts = replay_counts
            if counts is None:
                counts = (
                    await connection.execute(
                        select(
                            func.privacy.replay_person_auto_expiry(
                                schedule["schedule_id"],
                                person_id,
                                entry.record.outbox_id,
                            )
                        )
                    )
                ).scalar_one()
            if not isinstance(counts, dict):
                raise LedgerIntegrityError("person erasure replay kernel failed")
            counts.pop("spool_entity_ids", None)
            counts.pop("spool_user_ids", None)
            counts.pop("legacy_source_untracked", None)
            counts["runtime_spool_rows"] = 0
            await connection.execute(
                insert(schema.auto_expiry_receipts).values(
                    receipt_id=__import__("uuid").uuid4(),
                    schedule_id=schedule["schedule_id"],
                    outbox_id=schedule["outbox_id"],
                    ledger_outbox_id=entry.record.outbox_id,
                    person_id=person_id,
                    operation_codes=entry.record.operation_codes,
                    cascade_counts=counts,
                    residual_codes=(
                        entry.record.legacy_untracked_codes
                        + entry.record.external_pending_codes
                    ),
                    receipt_sha256=entry.record_digest,
                    completed_at=entry.record.completed_at,
                )
            )
        elif auto_receipt["ledger_outbox_id"] != entry.record.outbox_id:
            raise LedgerIntegrityError(
                "person auto-expiry receipt conflicts with ledger"
            )
        await connection.execute(
            update(schema.auto_expiry_schedules)
            .where(
                schema.auto_expiry_schedules.c.schedule_id == schedule["schedule_id"]
            )
            .values(state="complete", completed_at=entry.record.completed_at)
        )
        await connection.execute(
            update(schema.outbox)
            .where(schema.outbox.c.outbox_id == schedule["outbox_id"])
            .values(
                state="complete",
                claim_token=None,
                claimed_at=None,
                completed_at=entry.record.completed_at,
                last_error_code=None,
            )
        )

    @dataclass(frozen=True, slots=True)
    class _DatabaseState:
        recorded_epoch: int
        recorded_head_hash: str

    async def _database_state(self) -> _DatabaseState:
        async with self.database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(schema.erasure_ledger_state).where(
                            schema.erasure_ledger_state.c.state_key == "current"
                        )
                    )
                )
                .mappings()
                .first()
            )
        return self._DatabaseState(
            recorded_epoch=row["recorded_epoch"] if row else 0,
            recorded_head_hash=row["recorded_head_hash"] if row else ZERO_HASH,
        )

    @staticmethod
    async def _set_database_state(connection, head: LedgerHead) -> None:
        await connection.execute(
            insert(schema.erasure_ledger_state)
            .values(
                state_key="current",
                recorded_epoch=head.epoch,
                recorded_head_hash=head.head_hash,
            )
            .on_conflict_do_update(
                index_elements=["state_key"],
                set_={
                    "recorded_epoch": head.epoch,
                    "recorded_head_hash": head.head_hash,
                    "updated_at": datetime.now(UTC),
                },
            )
        )
