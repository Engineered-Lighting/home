"""Private confirmation adapter. This contract does not authenticate a gesture.

The coordinator must authenticate current owner/session authority, freeze the
reviewed proposal and durably retain these exact IDs before dispatch. Never bind
this model directly to browser JSON. Uncertain outcomes must not auto-retry.
"""
from datetime import UTC, datetime, timedelta
from typing import Callable
from urllib.parse import parse_qsl
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ROLE = "home_agent_shared_link_coordinator"
CALL = text("SELECT * FROM identity.confirm_shared_link_ceremony_v1("
    ":ceremony_id,:echo_subject,:session_commitment,:expected_revision,"
    ":confirmation_commitment,:proposal_digest,:proposal_id,:receipt_id,"
    ":link_id,:echo_binding_id,:victoria_binding_id)")
INSPECT = text("SELECT * FROM identity.inspect_shared_link_confirmation_v1("
    ":ceremony_id,:echo_subject,:session_commitment,:expected_revision,"
    ":confirmation_commitment,:proposal_digest,:proposal_id,:receipt_id,"
    ":link_id,:echo_binding_id,:victoria_binding_id)")


class SharedLinkConfirmation(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)
    ceremony_id: UUID = Field(repr=False)
    echo_subject: str = Field(min_length=1, max_length=64, repr=False)
    session_commitment: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    expected_revision: int = Field(gt=0, lt=9223372036854775807)
    expected_generation: int = Field(gt=0, lt=9223372036854775807)
    confirmation_commitment: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    proposal_id: UUID = Field(repr=False)
    receipt_id: UUID = Field(repr=False)
    link_id: UUID = Field(repr=False)
    echo_binding_id: UUID = Field(repr=False)
    victoria_binding_id: UUID = Field(repr=False)

    @field_validator("echo_subject")
    @classmethod
    def exact_subject(cls, value):
        if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("invalid owner subject")
        return value

    @model_validator(mode="after")
    def distinct_bindings(self):
        if self.echo_binding_id == self.victoria_binding_id:
            raise ValueError("distinct site binding IDs required")
        return self


class SharedLinkConfirmationReceipt(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)
    link_id: UUID = Field(repr=False)
    authorization_generation: int = Field(gt=0, le=9223372036854775807)
    revision: int = Field(gt=0, le=9223372036854775807)
    confirmed_at: datetime

    @field_validator("confirmed_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("confirmation requires aware timestamp")
        return value


class SharedLinkConfirmationDatabase:
    def __init__(self, url: str, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        parsed = make_url(url)
        if parsed.drivername not in ("postgresql", "postgresql+psycopg") or parsed.username != ROLE or not callable(now):
            raise ValueError("dedicated confirmation database required")
        query = parsed.query
        raw = parse_qsl(url.partition("?")[2], keep_blank_values=True)
        if (len(raw) != len(query) or any(not value for _, value in raw) or
            set(query) - {"sslmode", "sslrootcert"} or
            any(not isinstance(value, str) or not value for value in query.values()) or
            (query and query.get("sslmode") != "verify-full")):
            raise ValueError("unsupported confirmation connection options")
        self._now = now
        self.engine = create_async_engine(parsed.set(drivername="postgresql+psycopg"),
            pool_pre_ping=True, pool_size=1, max_overflow=0, pool_timeout=2, pool_recycle=300,
            hide_parameters=True, connect_args={"connect_timeout": 5, "options":
                "-c statement_timeout=7000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=10000"})

    async def confirm(self, value: SharedLinkConfirmation) -> SharedLinkConfirmationReceipt:
        if type(value) is not SharedLinkConfirmation:
            raise TypeError("trusted confirmation required")
        value = SharedLinkConfirmation.model_validate(value.model_dump())
        async with self.engine.connect() as raw:
            connection = await raw.execution_options(isolation_level="SERIALIZABLE")
            async with connection.begin():
                result = await connection.execute(CALL, value.model_dump(exclude={"expected_generation"}))
                receipt = self._receipt(dict(result.mappings().one()), value)
        # A committed receipt is historical evidence, not a grant for retrieval.
        # Delivery must recheck current owner/session/generation authority.
        return receipt

    async def inspect_confirmation(self, value: SharedLinkConfirmation) -> SharedLinkConfirmationReceipt | None:
        """Lookup only. No result leaves the original outcome indeterminate."""
        if type(value) is not SharedLinkConfirmation:
            raise TypeError("trusted confirmation required")
        value = SharedLinkConfirmation.model_validate(value.model_dump())
        async with self.engine.connect() as raw:
            connection = await raw.execution_options(isolation_level="SERIALIZABLE")
            async with connection.begin():
                result = await connection.execute(INSPECT, value.model_dump(exclude={"expected_generation"}))
                row = result.mappings().one_or_none()
                receipt = None if row is None else self._receipt(dict(row), value)
        return receipt

    def _receipt(self, row, value):
        receipt = SharedLinkConfirmationReceipt.model_validate(row)
        now = self._now()
        if (not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None or
            receipt.link_id != value.link_id or receipt.revision != 1 or
            receipt.authorization_generation != value.expected_generation + 1 or
            receipt.confirmed_at > now + timedelta(seconds=1)):
            raise ValueError("confirmation receipt mismatch")
        return receipt

    async def close(self):
        await self.engine.dispose()
