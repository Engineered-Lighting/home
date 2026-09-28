"""Dedicated issuance adapter; no runtime configuration or route enables it.

The caller must resolve authenticated owner/session context and satisfy restore
admission before invoking begin. A typed submission is not authentication.
"""
from datetime import UTC, datetime, timedelta
from typing import Callable
from uuid import UUID
from urllib.parse import parse_qsl

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from .shared_link_commitments import SharedLinkBegin, SharedLinkCommitments, SharedLinkCeremonyContext
from .shared_link_owner import SharedLinkOwnerAnchor
from .auth import ServiceIdentity
from .errors import ForbiddenError

COORDINATOR_ROLE = "home_agent_shared_link_coordinator"
LOOKUP = text("SELECT * FROM identity.resolve_shared_link_owner_v1(:subject)")
BEGIN = text("""SELECT * FROM identity.issue_shared_link_ceremony_v1(
 :ceremony_id,:principal_id,:person_id,:legacy_binding_id,:echo_subject,:owner_commitment,:request_commitment,
 :echo_session_commitment,:victoria_session_commitment,:echo_challenge_id,:victoria_challenge_id,
 :echo_challenge_commitment,:victoria_challenge_commitment,:key_id,:key_fingerprint)""")
INSPECT = text("""SELECT * FROM identity.inspect_shared_link_issuance_v1(
 :ceremony_id,:principal_id,:person_id,:legacy_binding_id,:echo_subject,:owner_commitment,:request_commitment,
 :echo_session_commitment,:victoria_session_commitment,:echo_challenge_id,:victoria_challenge_id,
 :echo_challenge_commitment,:victoria_challenge_commitment,:key_id,:key_fingerprint)""")


class SharedLinkIssuanceReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    ceremony_id: UUID = Field(repr=False)
    authorization_generation: int = Field(gt=0, le=9223372036854775807)
    revision: int = Field(gt=0, le=9223372036854775807)
    created_at: datetime
    expires_at: datetime
    echo_registration_revision: int = Field(gt=0, le=9223372036854775807)
    victoria_registration_revision: int = Field(gt=0, le=9223372036854775807)

    @field_validator("created_at", "expires_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("issuance time requires an offset")
        return value


class SharedLinkIssuanceDatabase:
    """Stable requests, dedicated credentials and bounded SERIALIZABLE calls.

    Never automatically retry a connection/commit failure: the ceremony may have
    committed. The coordinator must retain the original submission and IDs for
    explicit outcome reconciliation. No fallback to owner credentials is allowed.
    """
    def __init__(self, url: str, *, commitments: SharedLinkCommitments,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        if type(commitments) is not SharedLinkCommitments or not callable(now):
            raise ValueError("invalid shared-link issuance configuration")
        database_url = make_url(url)
        if database_url.drivername not in ("postgresql", "postgresql+psycopg"):
            raise ValueError("issuance database requires PostgreSQL psycopg")
        if database_url.username != COORDINATOR_ROLE:
            raise ValueError("dedicated shared-link coordinator credentials required")
        # libpq query arguments can override the authority in the URL, including
        # user/host/database. Permit only explicit, fully verified TLS settings.
        query = database_url.query
        raw_query = parse_qsl(url.partition("?")[2], keep_blank_values=True)
        if (len(raw_query) != len(query) or any(not value for _, value in raw_query) or
            set(query) - {"sslmode", "sslrootcert"} or
            any(not isinstance(value, str) or not value for value in query.values()) or
            (query and query.get("sslmode") != "verify-full")):
            raise ValueError("unsupported shared-link database connection options")
        self._commitments = commitments
        self._now = now
        self.engine = create_async_engine(database_url.set(drivername="postgresql+psycopg"),
            pool_pre_ping=True, pool_size=1, max_overflow=0, pool_timeout=2,
            pool_recycle=300, hide_parameters=True,
            connect_args={"connect_timeout": 5, "options":
                "-c statement_timeout=7000 -c lock_timeout=5000 "
                "-c idle_in_transaction_session_timeout=10000"})

    async def resolve_owner(self, identity: ServiceIdentity) -> SharedLinkOwnerAnchor:
        """Resolve trusted Echo context through the isolated database boundary.

        The returned anchor is not a grant. Issuance rechecks it in its own
        transaction, including revocation occurring after this lookup.
        """
        if (type(identity) is not ServiceIdentity or
            (identity.ha_issuer_id, identity.site_id) != ("home-assistant:echo", "echo") or
            not isinstance(identity.ha_user_id, str) or not 1 <= len(identity.ha_user_id) <= 64 or
            identity.ha_user_id != identity.ha_user_id.strip() or
            any(ord(char) < 32 or ord(char) == 127 for char in identity.ha_user_id)):
            raise ForbiddenError("shared-link owner context unavailable")
        async with self.engine.connect() as raw:
            connection = await raw.execution_options(isolation_level="SERIALIZABLE")
            async with connection.begin():
                result = await connection.execute(LOOKUP, {"subject": identity.ha_user_id})
                anchor = SharedLinkOwnerAnchor.model_validate(dict(result.mappings().one()))
        return anchor

    async def prepare_begin(self, identity: ServiceIdentity,
                            context: SharedLinkCeremonyContext) -> SharedLinkBegin:
        """Resolve owner IDs before the caller durably retains the exact request.

        No issuance occurs here. The authenticated coordinator must persist this
        returned submission before begin(), and retain it across uncertain
        outcomes rather than re-resolving an anchor for the same ceremony.
        """
        if type(context) is not SharedLinkCeremonyContext:
            raise TypeError("trusted ceremony context required")
        verified = SharedLinkCeremonyContext.model_validate(context.model_dump())
        anchor = await self.resolve_owner(identity)
        return SharedLinkBegin(**verified.model_dump(), **anchor.model_dump(),
                               echo_subject=identity.ha_user_id)

    async def begin(self, value: SharedLinkBegin) -> SharedLinkIssuanceReceipt:
        prepared = self._commitments.prepare(value)
        parameters = {**prepared.parameters(), "key_id": prepared.key_id,
                      "key_fingerprint": prepared.key_fingerprint}
        async with self.engine.connect() as raw:
            connection = await raw.execution_options(isolation_level="SERIALIZABLE")
            async with connection.begin():
                result = await connection.execute(BEGIN, parameters)
                receipt = SharedLinkIssuanceReceipt.model_validate(dict(result.mappings().one()))
                now = self._now()
                if (not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None or
                    receipt.ceremony_id != prepared.submission.ceremony_id or receipt.revision != 1 or
                    receipt.expires_at != receipt.created_at + timedelta(minutes=5) or
                    receipt.created_at > now + timedelta(seconds=1) or receipt.expires_at <= now):
                    raise ValueError("shared-link issuance receipt mismatch")
        # COMMIT and connection cleanup may outlast the remaining ceremony lease.
        # A rejection here does not roll back the committed ceremony or retry it.
        now = self._now()
        if (not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None or
            receipt.expires_at <= now or receipt.created_at > now + timedelta(seconds=1)):
            raise ValueError("committed shared-link issuance receipt is no longer fresh")
        return receipt

    async def inspect_issuance(self, value: SharedLinkBegin) -> SharedLinkIssuanceReceipt | None:
        """Lookup only: missing is uncertain, a receipt is historical evidence.

        Never invoke issuance or renew deadlines while reconciling. Current
        owner/session/registration checks occur inside the database boundary.
        """
        prepared = self._commitments.prepare(value)
        parameters = {**prepared.parameters(), "key_id": prepared.key_id,
                      "key_fingerprint": prepared.key_fingerprint}
        async with self.engine.connect() as raw:
            connection = await raw.execution_options(isolation_level="SERIALIZABLE")
            async with connection.begin():
                result = await connection.execute(INSPECT, parameters)
                row = result.mappings().one_or_none()
                if row is None:
                    return None
                receipt = SharedLinkIssuanceReceipt.model_validate(dict(row))
                if (receipt.ceremony_id != prepared.submission.ceremony_id or receipt.revision != 1 or
                    receipt.expires_at != receipt.created_at + timedelta(minutes=5)):
                    raise ValueError("shared-link historical receipt mismatch")
        return receipt

    async def close(self):
        await self.engine.dispose()
