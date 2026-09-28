"""Dedicated, currently unprovisioned fresh-auth proof database adapter.

Only trusted proof-ingress code may construct submissions after completing HA
authentication. This type does not authenticate its caller. Never bind it directly
to a browser JSON body or route it through the legacy bare-HA-ID resolver.
"""
from datetime import UTC, datetime, timedelta
from typing import Callable, Literal
from uuid import UUID
from urllib.parse import parse_qsl

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


Issuer = Literal["home-assistant:echo", "home-assistant:victoria"]


class FreshAuthSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    proof_id: UUID
    subject: str = Field(min_length=1, max_length=64)
    session_commitment: str = Field(pattern=r"^[0-9a-f]{64}$")
    challenge_commitment: str = Field(pattern=r"^[0-9a-f]{64}$")
    authenticated_at: datetime
    registration_revision: int = Field(gt=0, le=9223372036854775807)

    @field_validator("subject")
    @classmethod
    def subject_is_exact(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("invalid proof subject")
        return value

    @field_validator("authenticated_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("proof time requires an offset")
        return value


class FreshAuthReceipt(FreshAuthSubmission):
    issuer_id: Issuer
    issued_at: datetime
    expires_at: datetime

    @field_validator("issued_at", "expires_at")
    @classmethod
    def aware_receipt_time(cls, value: datetime) -> datetime:
        return cls.aware_time(value)


class SharedAuthProofDatabase:
    """One issuer's dedicated credentials; no table reads or generic writes.

    A connection failure may mean commit outcome is unknown. Do not automatically
    retry it as a new proof; retain the original proof ID and complete submission.
    Runtime configuration and HTTP ingress intentionally do not instantiate this.
    """

    _statement = text("SELECT * FROM identity.issue_shared_auth_proof_v1("
        ":proof_id,:subject,:session_commitment,:challenge_commitment,"
        ":authenticated_at,:registration_revision)")

    def __init__(self, url: str, *, issuer_id: Issuer,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        if issuer_id not in ("home-assistant:echo", "home-assistant:victoria"):
            raise ValueError("unregistered proof issuer")
        if not callable(now):
            raise ValueError("proof clock required")
        self._now = now
        database_url = make_url(url)
        if database_url.drivername not in ("postgresql", "postgresql+psycopg"):
            raise ValueError("proof database requires PostgreSQL psycopg")
        site = "echo" if issuer_id == "home-assistant:echo" else "victoria"
        if database_url.username != f"home_agent_shared_{site}_proof_ingress":
            raise ValueError("dedicated issuer proof credentials required")
        query = database_url.query
        raw_query = parse_qsl(url.partition("?")[2], keep_blank_values=True)
        if (len(raw_query) != len(query) or any(not value for _, value in raw_query) or
            set(query) - {"sslmode", "sslrootcert"} or
            any(not isinstance(value, str) or not value for value in query.values()) or
            (query and query.get("sslmode") != "verify-full")):
            raise ValueError("unsupported proof database connection options")
        database_url = database_url.set(drivername="postgresql+psycopg")
        # Set deadlines at connection startup, preserving the kernel's requirement
        # to be the first transaction statement. Server-side limits survive a lost
        # client cancellation; they do not prove an uncertain commit rolled back.
        self.issuer_id = issuer_id
        self.engine = create_async_engine(database_url, pool_pre_ping=True, pool_size=1,
            max_overflow=0, pool_timeout=2, pool_recycle=300, hide_parameters=True,
            connect_args={"connect_timeout": 5, "options":
                "-c statement_timeout=7000 -c lock_timeout=5000 "
                "-c idle_in_transaction_session_timeout=10000"})

    async def issue(self, value: FreshAuthSubmission) -> FreshAuthReceipt:
        return await self._execute(value, self._statement, allow_missing=False)

    async def _execute(self, value: FreshAuthSubmission, statement, *, allow_missing: bool):
        if type(value) is not FreshAuthSubmission:
            raise TypeError("fresh authentication submission required")
        # model_copy/model_construct bypass Pydantic validation. Revalidate before
        # sending anything, including the exact subject and commitment fields.
        value = FreshAuthSubmission.model_validate(value.model_dump())
        async with self.engine.connect() as raw:
            connection = await raw.execution_options(isolation_level="SERIALIZABLE")
            async with connection.begin():
                result = await connection.execute(statement, value.model_dump())
                row = result.mappings().one_or_none() if allow_missing else result.mappings().one()
                if row is None:
                    # Absence is unknown, never evidence that issuance rolled back.
                    return None
                receipt = FreshAuthReceipt.model_validate(dict(row))
                if (receipt.issuer_id != self.issuer_id or
                    any(getattr(receipt, name) != getattr(value, name)
                        for name in FreshAuthSubmission.model_fields) or
                    not receipt.authenticated_at <= receipt.issued_at < receipt.expires_at or
                    receipt.expires_at > receipt.authenticated_at + timedelta(minutes=5)):
                    # Validate before the transaction commits. Identity substitution
                    # or a broken kernel result cannot be delivered as a receipt.
                    raise ValueError("fresh authentication receipt mismatch")
                self._require_fresh(receipt)
        # COMMIT or connection cleanup can consume the remaining lease. Failure
        # here is post-commit and must never trigger an automatic replacement.
        self._require_fresh(receipt)
        return receipt

    def _require_fresh(self, receipt: FreshAuthReceipt) -> None:
        now = self._now()
        if (not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None or
            receipt.expires_at <= now or receipt.issued_at > now + timedelta(seconds=1)):
            raise ValueError("fresh authentication receipt unavailable or expired")

    async def close(self) -> None:
        await self.engine.dispose()


class SharedLinkProofDatabase(SharedAuthProofDatabase):
    """Explicit 0041 challenge-bound admission; not a fallback for 0033.

    Same fixed issuer credential and receipt validation as the earlier adapter,
    but the SQL boundary must validate and atomically associate a live challenge.
    No runtime factory instantiates this without separate reviewed provisioning.
    """

    _statement = text("SELECT * FROM identity.issue_shared_link_auth_proof_v1("
        ":proof_id,:subject,:session_commitment,:challenge_commitment,"
        ":authenticated_at,:registration_revision)")

    _lookup_statement = text("SELECT * FROM identity.inspect_shared_link_auth_proof_v1("
        ":proof_id,:subject,:session_commitment,:challenge_commitment,"
        ":authenticated_at,:registration_revision)")

    async def inspect(self, value: FreshAuthSubmission) -> FreshAuthReceipt | None:
        """Look up a still-authorized proof using its entire original submission.

        This never issues a proof. Missing, expired or revoked results cannot
        authorize redispatch; retain the original uncertain delivery state.
        """
        return await self._execute(value, self._lookup_statement, allow_missing=True)
