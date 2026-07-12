from __future__ import annotations

import asyncio
import logging
import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import or_, select, update, func
from sqlalchemy.dialects.postgresql import insert

from . import schema
from .crypto import canonical_json
from .db import Database
from .ledger import (
    EncryptedErasureLedger,
    ErasureLedgerRecord,
    LedgerEntry,
    LedgerHead,
)
from .spool import DisabledRuntimeSpool, EncryptedRuntimeSpool


LOGGER = logging.getLogger("home_agent.worker")
ERASURE_OUTBOX_TOPIC = "privacy.erasure.completed"
AUTO_EXPIRY_OUTBOX_TOPIC = "privacy.person.auto_expire"
AUDITED_NO_EXTERNAL_EFFECT_TOPICS = (
    "memory.committed",
    "memory.descriptor.correction",
    "memory.descriptor.retraction",
)


@dataclass(frozen=True, slots=True)
class OutboxClaim:
    outbox_id: uuid.UUID
    claim_token: uuid.UUID
    attempts: int
    payload: dict[str, Any]
    created_at: datetime


class DurableWorker:
    def __init__(
        self,
        database: Database,
        ledger: EncryptedErasureLedger,
        spool: EncryptedRuntimeSpool | DisabledRuntimeSpool,
        *,
        claim_lease_seconds: int = 60,
        poll_seconds: float = 1.0,
    ) -> None:
        self.database = database
        self.ledger = ledger
        self.spool = spool
        self.claim_lease = timedelta(seconds=claim_lease_seconds)
        self.poll_seconds = poll_seconds

    async def run(self, stop: asyncio.Event) -> None:
        next_prune = datetime.now(UTC)
        next_binding_expiry = next_prune
        while not stop.is_set():
            now = datetime.now(UTC)
            if now >= next_prune:
                await asyncio.to_thread(self.spool.prune)
                next_prune = now + timedelta(minutes=1)
            if now >= next_binding_expiry:
                try:
                    await self._expire_principal_binding_work(now)
                except Exception as exc:
                    LOGGER.error(
                        "binding retention failed "
                        "code=principal_binding_retention_failed error_class=%s",
                        type(exc).__name__,
                    )
                next_binding_expiry = now + timedelta(minutes=1)
            processed = await self.run_once(now=now)
            if processed:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                continue

    async def _expire_principal_binding_work(self, now: datetime) -> None:
        """Run the database-owned binding retention kernel.

        Keeping this in the durable worker ensures abandoned requests and
        proposals expire even when neither the subject nor an operator returns.
        The SECURITY DEFINER function is deliberately executable by the worker
        role but not by the browser-facing API role.
        """

        async with self.database.transaction() as connection:
            await connection.execute(
                select(func.privacy.expire_principal_binding_work(now))
            )

    async def run_once(self, *, now: datetime | None = None) -> bool:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        claim = await self._claim_one(now)
        if claim is None:
            auto_expiry_claim = await self._claim_topic(AUTO_EXPIRY_OUTBOX_TOPIC, now)
            if auto_expiry_claim is not None:
                await self._complete_auto_expiry(auto_expiry_claim, now)
                return True
            return await self._complete_no_effect_once(now)
        try:
            record = ErasureLedgerRecord.model_validate(
                {
                    "outbox_id": claim.outbox_id,
                    "source_created_at": claim.created_at,
                    **claim.payload,
                }
            )
        except ValidationError:
            await self._fail_claim(claim, now, "invalid_erasure_receipt")
            LOGGER.error(
                "erasure outbox rejected code=invalid_erasure_receipt outbox_id=%s",
                claim.outbox_id,
            )
            return True

        try:
            entry = await asyncio.to_thread(self.ledger.append_once, record)
            head = await asyncio.to_thread(self.ledger.head)
            await self._complete_claim(claim, entry, head, now)
        except Exception as exc:
            await self._retry_claim(claim, now, "erasure_ledger_write_failed")
            LOGGER.error(
                "erasure ledger write failed code=erasure_ledger_write_failed "
                "outbox_id=%s error_class=%s",
                claim.outbox_id,
                type(exc).__name__,
            )
        return True

    async def _complete_no_effect_once(self, now: datetime) -> bool:
        """Drain explicitly audited topics that have no MVP external consumer."""

        async with self.database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(schema.outbox)
                        .where(
                            schema.outbox.c.topic.in_(
                                AUDITED_NO_EXTERNAL_EFFECT_TOPICS
                            ),
                            schema.outbox.c.state == "pending",
                            schema.outbox.c.available_at <= now,
                        )
                        .order_by(schema.outbox.c.created_at, schema.outbox.c.outbox_id)
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return False
            await connection.execute(
                update(schema.outbox)
                .where(schema.outbox.c.outbox_id == row["outbox_id"])
                .values(
                    state="complete",
                    attempts=row["attempts"] + 1,
                    completed_at=now,
                    last_error_code=None,
                )
            )
            return True

    async def _claim_one(self, now: datetime) -> OutboxClaim | None:
        return await self._claim_topic(ERASURE_OUTBOX_TOPIC, now)

    async def _claim_topic(self, topic: str, now: datetime) -> OutboxClaim | None:
        stale_before = now - self.claim_lease
        claim_token = uuid.uuid4()
        async with self.database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(schema.outbox)
                        .where(
                            schema.outbox.c.topic == topic,
                            or_(
                                (
                                    (schema.outbox.c.state == "pending")
                                    & (schema.outbox.c.available_at <= now)
                                ),
                                (
                                    (schema.outbox.c.state == "running")
                                    & (schema.outbox.c.claimed_at < stale_before)
                                ),
                            ),
                        )
                        .order_by(schema.outbox.c.created_at, schema.outbox.c.outbox_id)
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            attempts = row["attempts"] + 1
            await connection.execute(
                update(schema.outbox)
                .where(schema.outbox.c.outbox_id == row["outbox_id"])
                .values(
                    state="running",
                    attempts=attempts,
                    claimed_at=now,
                    claim_token=claim_token,
                    last_error_code=None,
                )
            )
            return OutboxClaim(
                outbox_id=row["outbox_id"],
                claim_token=claim_token,
                attempts=attempts,
                payload=dict(row["payload"]),
                created_at=row["created_at"],
            )

    async def _complete_auto_expiry(self, claim: OutboxClaim, now: datetime) -> None:
        """Apply one due, idempotent person expiry and persist a safe receipt."""

        try:
            if (
                set(claim.payload)
                != {
                    "version",
                    "schedule_id",
                    "person_id",
                    "directive_id",
                    "policy_digest",
                }
                or claim.payload["version"] != 1
            ):
                raise ValueError("invalid payload shape")
            schedule_id = uuid.UUID(claim.payload["schedule_id"])
            person_id = uuid.UUID(claim.payload["person_id"])
            directive_id = uuid.UUID(claim.payload["directive_id"])
            policy_digest = str(claim.payload["policy_digest"])
            if len(policy_digest) != 64 or any(
                char not in "0123456789abcdef" for char in policy_digest
            ):
                raise ValueError("invalid policy digest")
        except (KeyError, TypeError, ValueError, AttributeError):
            await self._fail_claim(claim, now, "invalid_auto_expiry_job")
            return

        runtime_spool_rows = 0

        async def apply_auto_expiry() -> None:
            nonlocal runtime_spool_rows
            async with self.database.transaction(serializable=True) as connection:
                owns_claim = (
                    await connection.execute(
                        select(schema.outbox.c.outbox_id)
                        .where(
                            schema.outbox.c.outbox_id == claim.outbox_id,
                            schema.outbox.c.state == "running",
                            schema.outbox.c.claim_token == claim.claim_token,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if owns_claim is None:
                    return
                schedule = (
                    (
                        await connection.execute(
                            select(schema.auto_expiry_schedules)
                            .where(
                                schema.auto_expiry_schedules.c.schedule_id
                                == schedule_id,
                                schema.auto_expiry_schedules.c.person_id == person_id,
                                schema.auto_expiry_schedules.c.directive_id
                                == directive_id,
                                schema.auto_expiry_schedules.c.outbox_id
                                == claim.outbox_id,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .first()
                )
                if schedule is None or schedule["due_at"] > now:
                    raise ValueError("schedule mismatch")
                existing_receipt = (
                    (
                        await connection.execute(
                            select(schema.auto_expiry_receipts).where(
                                schema.auto_expiry_receipts.c.schedule_id == schedule_id
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
                if existing_receipt is not None:
                    ledger_state = (
                        await connection.execute(
                            select(schema.outbox.c.state).where(
                                schema.outbox.c.outbox_id
                                == existing_receipt["ledger_outbox_id"]
                            )
                        )
                    ).scalar_one_or_none()
                    if ledger_state != "complete":
                        await connection.execute(
                            update(schema.outbox)
                            .where(schema.outbox.c.outbox_id == claim.outbox_id)
                            .values(
                                state="pending",
                                available_at=now + timedelta(seconds=2),
                                claimed_at=None,
                                claim_token=None,
                                last_error_code="erasure_ledger_pending",
                            )
                        )
                    return
                directive = (
                    (
                        await connection.execute(
                            select(schema.privacy_directives)
                            .where(
                                schema.privacy_directives.c.directive_id
                                == directive_id,
                                schema.privacy_directives.c.person_id == person_id,
                                schema.privacy_directives.c.directive == "auto_expire",
                                schema.privacy_directives.c.enabled.is_(True),
                                schema.privacy_directives.c.expires_at <= now,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .first()
                )
                if directive is None:
                    raise ValueError("directive mismatch")
                counts_value = (
                    await connection.execute(
                        select(func.privacy.apply_person_auto_expiry(schedule_id))
                    )
                ).scalar_one()
                if not isinstance(counts_value, dict):
                    raise RuntimeError(
                        "auto-expiry erasure kernel returned invalid receipt"
                    )
                spool_entity_ids = counts_value.pop("spool_entity_ids", [])
                spool_user_ids = counts_value.pop("spool_user_ids", [])
                legacy_source_untracked = counts_value.pop(
                    "legacy_source_untracked", False
                )
                if not isinstance(legacy_source_untracked, bool):
                    raise RuntimeError("auto-expiry legacy residual is invalid")
                if not isinstance(spool_entity_ids, list) or any(
                    not isinstance(value, str) for value in spool_entity_ids
                ):
                    raise RuntimeError("auto-expiry spool scope is invalid")
                if not isinstance(spool_user_ids, list) or any(
                    not isinstance(value, str) for value in spool_user_ids
                ):
                    raise RuntimeError("auto-expiry user spool scope is invalid")
                counts = {str(key): int(value) for key, value in counts_value.items()}
                runtime_spool_rows += await asyncio.to_thread(
                    self.spool.delete_for_subjects,
                    spool_entity_ids,
                    spool_user_ids,
                )
                counts["runtime_spool_rows"] = runtime_spool_rows
                binding_count = counts.get("recognition_bindings", 0)

                operation_codes = [
                    "block_identity_retrieval",
                    "scrub_person_content",
                    "delete_identity_aliases",
                    "delete_recognition_bindings",
                    "delete_principal_binding_requests",
                    "delete_principal_binding_proposals",
                    "delete_ha_user_bindings",
                    "revoke_source_bindings",
                    "suppress_principal_initiatives",
                    "scrub_identity_ingest_headers",
                    "delete_runtime_spool_rows",
                    "delete_private_place_locators",
                ]
                residual_codes = (
                    ["external_recognition_delete_untracked"] if binding_count else []
                )
                if legacy_source_untracked:
                    residual_codes.append("legacy_identity_store_erasure_untracked")
                external_pending_codes = []
                if counts.get("source_entity_bindings", 0) or counts.get(
                    "ha_user_bindings", 0
                ):
                    external_pending_codes.append("edge_spool_ttl_pending")
                    residual_codes.append("edge_spool_ttl_pending")
                receipt_material = {
                    "version": 1,
                    "schedule_id": str(schedule_id),
                    "outbox_id": str(claim.outbox_id),
                    "person_id": str(person_id),
                    "operation_codes": operation_codes,
                    "cascade_counts": counts,
                    "residual_codes": residual_codes,
                    "policy_digest": policy_digest,
                    "completed_at": now.isoformat(),
                }
                receipt_sha256 = hashlib.sha256(
                    canonical_json(receipt_material)
                ).hexdigest()
                receipt_id = uuid.uuid4()
                ledger_outbox_id = uuid.uuid4()
                await connection.execute(
                    insert(schema.auto_expiry_receipts).values(
                        receipt_id=receipt_id,
                        schedule_id=schedule_id,
                        outbox_id=claim.outbox_id,
                        ledger_outbox_id=ledger_outbox_id,
                        person_id=person_id,
                        operation_codes=operation_codes,
                        cascade_counts=counts,
                        residual_codes=residual_codes,
                        receipt_sha256=receipt_sha256,
                        completed_at=now,
                    )
                )
                await connection.execute(
                    insert(schema.outbox).values(
                        outbox_id=ledger_outbox_id,
                        topic=ERASURE_OUTBOX_TOPIC,
                        aggregate_id=schedule_id,
                        payload={
                            "version": 1,
                            "erasure_request_id": str(schedule_id),
                            "subject_kind": "person",
                            "principal_id": None,
                            "fact_id": None,
                            "person_id": str(person_id),
                            "operation_codes": operation_codes,
                            "policy_digest": policy_digest,
                            "completed_at": now.isoformat(),
                            "external_pending_codes": external_pending_codes,
                            "legacy_untracked_codes": [
                                code
                                for code in residual_codes
                                if code != "edge_spool_ttl_pending"
                            ],
                            "checkpoint_affected": True,
                            "backup_expiry_at": None,
                            "exact_residual_codes": ["backup_expiry_unverified"],
                        },
                    )
                )
                await connection.execute(
                    update(schema.auto_expiry_schedules)
                    .where(schema.auto_expiry_schedules.c.schedule_id == schedule_id)
                    .values(state="ledger_pending")
                )
                await connection.execute(
                    update(schema.outbox)
                    .where(
                        schema.outbox.c.outbox_id == claim.outbox_id,
                        schema.outbox.c.claim_token == claim.claim_token,
                    )
                    .values(
                        state="pending",
                        available_at=now + timedelta(seconds=2),
                        claimed_at=None,
                        claim_token=None,
                        last_error_code="erasure_ledger_pending",
                    )
                )

        try:
            await self.database.run_serializable(apply_auto_expiry)
        except ValueError:
            await self._fail_claim(claim, now, "invalid_auto_expiry_job")
        except Exception:
            await self._retry_claim(claim, now, "auto_expiry_failed")

    async def _complete_claim(
        self,
        claim: OutboxClaim,
        entry: LedgerEntry,
        head: LedgerHead,
        now: datetime,
    ) -> None:
        async with self.database.transaction(
            principal_id=entry.record.principal_id
        ) as connection:
            owns_claim = (
                await connection.execute(
                    select(schema.outbox.c.outbox_id)
                    .where(
                        schema.outbox.c.outbox_id == claim.outbox_id,
                        schema.outbox.c.state == "running",
                        schema.outbox.c.claim_token == claim.claim_token,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if owns_claim is None:
                return
            await self._advance_ledger_state(connection, head, now)
            if entry.record.subject_kind == "descriptor_fact":
                await connection.execute(
                    update(schema.erasure_requests)
                    .where(
                        schema.erasure_requests.c.erasure_request_id
                        == entry.record.erasure_request_id,
                        schema.erasure_requests.c.state == "ledger_pending",
                    )
                    .values(state="complete", completed_at=now)
                )
            else:
                auto_receipt = (
                    (
                        await connection.execute(
                            select(schema.auto_expiry_receipts).where(
                                schema.auto_expiry_receipts.c.ledger_outbox_id
                                == claim.outbox_id,
                                schema.auto_expiry_receipts.c.person_id
                                == entry.record.person_id,
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                await connection.execute(
                    update(schema.auto_expiry_schedules)
                    .where(
                        schema.auto_expiry_schedules.c.schedule_id
                        == auto_receipt["schedule_id"]
                    )
                    .values(state="complete", completed_at=now)
                )
                await connection.execute(
                    update(schema.outbox)
                    .where(schema.outbox.c.outbox_id == auto_receipt["outbox_id"])
                    .values(
                        state="complete",
                        claim_token=None,
                        claimed_at=None,
                        completed_at=now,
                        last_error_code=None,
                    )
                )
            await connection.execute(
                update(schema.outbox)
                .where(
                    schema.outbox.c.outbox_id == claim.outbox_id,
                    schema.outbox.c.claim_token == claim.claim_token,
                )
                .values(
                    state="complete",
                    claim_token=None,
                    completed_at=now,
                    last_error_code=None,
                )
            )
            await connection.execute(
                insert(schema.erasure_replay_receipts)
                .values(
                    ledger_epoch=entry.epoch,
                    outbox_id=claim.outbox_id,
                    erasure_request_id=entry.record.erasure_request_id,
                    record_hash=entry.record_hash,
                    record_digest=entry.record_digest,
                    applied_at=now,
                )
                .on_conflict_do_nothing(index_elements=["ledger_epoch"])
            )
            receipt = (
                (
                    await connection.execute(
                        select(schema.erasure_replay_receipts).where(
                            schema.erasure_replay_receipts.c.ledger_epoch == entry.epoch
                        )
                    )
                )
                .mappings()
                .one()
            )
            if (
                receipt["outbox_id"] != claim.outbox_id
                or receipt["record_hash"] != entry.record_hash
                or receipt["record_digest"] != entry.record_digest
            ):
                raise RuntimeError("erasure replay receipt conflicts with ledger")

    @staticmethod
    async def _advance_ledger_state(
        connection, head: LedgerHead, now: datetime
    ) -> None:
        await connection.execute(
            insert(schema.erasure_ledger_state)
            .values(
                state_key="current",
                recorded_epoch=0,
                recorded_head_hash="0" * 64,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["state_key"])
        )
        state = (
            (
                await connection.execute(
                    select(schema.erasure_ledger_state)
                    .where(schema.erasure_ledger_state.c.state_key == "current")
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        if state["recorded_epoch"] > head.epoch:
            raise RuntimeError("database erasure epoch is ahead of ledger")
        if (
            state["recorded_epoch"] == head.epoch
            and state["recorded_head_hash"] != head.head_hash
        ):
            raise RuntimeError("database and erasure ledger heads diverge")
        if state["recorded_epoch"] < head.epoch:
            await connection.execute(
                update(schema.erasure_ledger_state)
                .where(schema.erasure_ledger_state.c.state_key == "current")
                .values(
                    recorded_epoch=head.epoch,
                    recorded_head_hash=head.head_hash,
                    updated_at=now,
                )
            )

    async def _retry_claim(
        self, claim: OutboxClaim, now: datetime, error_code: str
    ) -> None:
        delay = min(300, 2 ** min(claim.attempts, 8))
        async with self.database.transaction() as connection:
            await connection.execute(
                update(schema.outbox)
                .where(
                    schema.outbox.c.outbox_id == claim.outbox_id,
                    schema.outbox.c.state == "running",
                    schema.outbox.c.claim_token == claim.claim_token,
                )
                .values(
                    state="pending",
                    available_at=now + timedelta(seconds=delay),
                    claimed_at=None,
                    claim_token=None,
                    last_error_code=error_code,
                )
            )

    async def _fail_claim(
        self, claim: OutboxClaim, now: datetime, error_code: str
    ) -> None:
        async with self.database.transaction() as connection:
            await connection.execute(
                update(schema.outbox)
                .where(
                    schema.outbox.c.outbox_id == claim.outbox_id,
                    schema.outbox.c.state == "running",
                    schema.outbox.c.claim_token == claim.claim_token,
                )
                .values(
                    state="failed",
                    completed_at=now,
                    claimed_at=None,
                    claim_token=None,
                    last_error_code=error_code,
                )
            )
