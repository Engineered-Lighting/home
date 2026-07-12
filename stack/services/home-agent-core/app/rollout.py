"""Durable, content-free rollout promotion authorization.

Deployment configuration selects a requested mode; it never grants that mode.
The database receipt is the independent authority checked by every Core role at
startup. No function in this module changes deployment configuration.
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import DBAPIError

from . import schema
from .config import Settings
from .crypto import sha256_json
from .db import Database
from .errors import CapabilityDisabledError, ConflictError
from .ids import uuid7
from .models import (
    RolloutAuthorizationRequest,
    RolloutAuthorizationView,
    RolloutMode,
)
from .phase2 import PHASE2_RULE_VERSION, Phase2GateInspector


SHADOW_RULE_VERSION = PHASE2_RULE_VERSION
CANARY_RULE_VERSION = "shadow-to-canary-authorization-v1-reserved"
ROLLOUT_ADVISORY_LOCK = 7_210_202_600


@dataclass(frozen=True, slots=True)
class RolloutTransition:
    from_mode: RolloutMode
    to_mode: RolloutMode
    rule_version: str


RECORD_ONLY_TO_SHADOW = RolloutTransition(
    from_mode="record_only",
    to_mode="shadow",
    rule_version=SHADOW_RULE_VERSION,
)
SHADOW_TO_CANARY = RolloutTransition(
    from_mode="shadow",
    to_mode="canary",
    rule_version=CANARY_RULE_VERSION,
)


@dataclass(frozen=True, slots=True)
class RolloutAuthorizationStatus:
    authorized: bool
    code: str
    authorization_id: uuid.UUID | None = None


def expected_canary_input_digest(
    *, predecessor_authorization_id: uuid.UUID, policy_digest: str
) -> str:
    """Reserve a deterministic chain boundary for the future canary gate."""

    return sha256_json(
        {
            "rule_version": CANARY_RULE_VERSION,
            "policy_digest": policy_digest,
            "predecessor_authorization_id": str(predecessor_authorization_id),
        }
    )


def evaluate_rollout_receipt(
    receipt: Mapping[str, Any] | None,
    *,
    transition: RolloutTransition,
    policy_version: str,
    policy_digest: str,
    input_digest: str,
) -> RolloutAuthorizationStatus:
    """Validate only content-free receipt fields with explicit failure codes."""

    if receipt is None:
        return RolloutAuthorizationStatus(False, "authorization_missing")
    if (
        receipt.get("from_mode") != transition.from_mode
        or receipt.get("to_mode") != transition.to_mode
    ):
        return RolloutAuthorizationStatus(False, "authorization_transition_mismatch")
    if receipt.get("rule_version") != transition.rule_version:
        return RolloutAuthorizationStatus(False, "authorization_rule_stale")
    if receipt.get("policy_version") != policy_version or not hmac.compare_digest(
        str(receipt.get("policy_digest", "")), policy_digest
    ):
        return RolloutAuthorizationStatus(False, "authorization_policy_mismatch")
    if not hmac.compare_digest(str(receipt.get("input_digest", "")), input_digest):
        return RolloutAuthorizationStatus(False, "authorization_evidence_stale")
    return RolloutAuthorizationStatus(
        True,
        "authorized",
        authorization_id=receipt.get("authorization_id"),
    )


def _view(row: Mapping[str, Any]) -> RolloutAuthorizationView:
    return RolloutAuthorizationView(**dict(row))


class RolloutAuthorizationService:
    """Issue the one currently supported promotion receipt."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    async def authorize_shadow(
        self, request: RolloutAuthorizationRequest
    ) -> RolloutAuthorizationView:
        if self.settings.rollout_mode != "record_only":
            raise CapabilityDisabledError(
                "shadow authorization may be issued only from record-only rollout",
                details={
                    "current_rollout_mode": self.settings.rollout_mode,
                    "required_rollout_mode": "record_only",
                },
            )
        if request.expected_rule_version != SHADOW_RULE_VERSION:
            raise ConflictError("Phase-2 rule changed after operator review")
        if request.expected_policy_version != self.settings.policy_version:
            raise ConflictError("deployment policy version changed after operator review")
        if not hmac.compare_digest(
            request.expected_policy_digest,
            self.settings.policy_digest,
        ):
            raise ConflictError("deployment policy changed after operator review")

        # A concurrent transaction can observe the advisory-lock statement's
        # older SERIALIZABLE snapshot and lose at the unique constraint even
        # though it checked first. Retry that one table-local race so the fresh
        # snapshot can return an exact duplicate or an explicit drift conflict.
        for attempt in range(1, 4):
            try:
                return await self.database.run_serializable(
                    lambda: self._authorize_shadow_once(request)
                )
            except DBAPIError as exc:
                sqlstate = getattr(exc.orig, "sqlstate", None)
                if sqlstate != "23505" or attempt == 3:
                    raise
        raise RuntimeError("unreachable rollout authorization retry state")

    async def _authorize_shadow_once(
        self, request: RolloutAuthorizationRequest
    ) -> RolloutAuthorizationView:
        transition = RECORD_ONLY_TO_SHADOW
        async with self.database.transaction(serializable=True) as connection:
            # Serialize the single deployment transition while keeping the
            # evidence recomputation and receipt insert in one transaction.
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": ROLLOUT_ADVISORY_LOCK},
            )
            evaluated_at = (
                await connection.execute(select(func.transaction_timestamp()))
            ).scalar_one()
            readiness = await Phase2GateInspector(
                self.database,
                rollout_mode="record_only",
                policy_version=self.settings.policy_version,
                policy_digest=self.settings.policy_digest,
            ).inspect_evidence_in_transaction(
                connection,
                now=evaluated_at,
            )
            if not readiness.ready_to_advance:
                raise CapabilityDisabledError(
                    "record-only evidence is not ready for shadow authorization",
                    details={"blockers": list(readiness.blockers)},
                )
            if not hmac.compare_digest(
                request.expected_input_digest,
                readiness.input_digest,
            ):
                raise ConflictError(
                    "Phase-2 evidence changed after operator review; review again"
                )

            request_receipt = (
                (
                    await connection.execute(
                        select(schema.rollout_authorizations).where(
                            schema.rollout_authorizations.c.operator_request_id
                            == request.operator_request_id
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if request_receipt is not None:
                exact = evaluate_rollout_receipt(
                    request_receipt,
                    transition=transition,
                    policy_version=self.settings.policy_version,
                    policy_digest=self.settings.policy_digest,
                    input_digest=readiness.input_digest,
                )
                if exact.authorized:
                    return _view(request_receipt)
                raise ConflictError(
                    "operator request UUID was already consumed by a different receipt"
                )

            transition_receipt = (
                (
                    await connection.execute(
                        select(schema.rollout_authorizations).where(
                            schema.rollout_authorizations.c.from_mode
                            == transition.from_mode,
                            schema.rollout_authorizations.c.to_mode
                            == transition.to_mode,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if transition_receipt is not None:
                raise ConflictError(
                    "rollout transition was already authorized by another request"
                )

            authorization_id = uuid7()
            row = (
                (
                    await connection.execute(
                        insert(schema.rollout_authorizations)
                        .values(
                            authorization_id=authorization_id,
                            operator_request_id=request.operator_request_id,
                            from_mode=transition.from_mode,
                            to_mode=transition.to_mode,
                            rule_version=transition.rule_version,
                            policy_version=self.settings.policy_version,
                            policy_digest=self.settings.policy_digest,
                            input_digest=readiness.input_digest,
                            readiness_evaluated_at=readiness.evaluated_at,
                            authorized_at=evaluated_at,
                        )
                        .returning(schema.rollout_authorizations)
                    )
                )
                .mappings()
                .one()
            )
            return _view(row)


class RolloutAuthorizationGate:
    """Validate configured rollout against durable authorization receipts."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    async def status(self, *, force: bool = False) -> RolloutAuthorizationStatus:
        del force
        if self.settings.rollout_mode == "record_only":
            return RolloutAuthorizationStatus(True, "record_only")
        try:
            async with self.database.transaction(serializable=True) as connection:
                evaluated_at = (
                    await connection.execute(select(func.transaction_timestamp()))
                ).scalar_one()
                readiness = await Phase2GateInspector(
                    self.database,
                    rollout_mode="record_only",
                    policy_version=self.settings.policy_version,
                    policy_digest=self.settings.policy_digest,
                ).inspect_evidence_in_transaction(
                    connection,
                    now=evaluated_at,
                )
                shadow_receipt = await self._receipt(connection, RECORD_ONLY_TO_SHADOW)
                shadow = evaluate_rollout_receipt(
                    shadow_receipt,
                    transition=RECORD_ONLY_TO_SHADOW,
                    policy_version=self.settings.policy_version,
                    policy_digest=self.settings.policy_digest,
                    input_digest=readiness.input_digest,
                )
                if not shadow.authorized:
                    return shadow
                if not readiness.ready_to_advance:
                    return RolloutAuthorizationStatus(
                        False, "authorization_evidence_no_longer_ready"
                    )
                if self.settings.rollout_mode == "shadow":
                    return shadow

                canary_receipt = await self._receipt(connection, SHADOW_TO_CANARY)
                canary_input = expected_canary_input_digest(
                    predecessor_authorization_id=shadow.authorization_id,
                    policy_digest=self.settings.policy_digest,
                )
                return evaluate_rollout_receipt(
                    canary_receipt,
                    transition=SHADOW_TO_CANARY,
                    policy_version=self.settings.policy_version,
                    policy_digest=self.settings.policy_digest,
                    input_digest=canary_input,
                )
        except Exception:
            # Startup/readiness must fail closed without reflecting SQL or
            # credential details into logs or health payloads.
            return RolloutAuthorizationStatus(False, "authorization_unavailable")

    @staticmethod
    async def _receipt(connection, transition: RolloutTransition):
        return (
            (
                await connection.execute(
                    select(schema.rollout_authorizations).where(
                        schema.rollout_authorizations.c.from_mode
                        == transition.from_mode,
                        schema.rollout_authorizations.c.to_mode == transition.to_mode,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
