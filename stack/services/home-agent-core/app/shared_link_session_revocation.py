"""Dedicated issuer-bound session tombstone delivery; not runtime-provisioned."""
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from .shared_auth_proof import Issuer

CALL = text("SELECT * FROM identity.revoke_shared_link_session_bound_v1(:session_commitment,:revocation_id)")


class SharedLinkSessionRevocation(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)
    session_commitment: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    revocation_id: UUID = Field(repr=False)


class SharedLinkSessionRevocationReceipt(SharedLinkSessionRevocation):
    issuer_id: Issuer
    revoked_at: datetime

    @field_validator("revoked_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("revocation time requires an offset")
        return value


class SharedLinkSessionRevocationDatabase:
    def __init__(self, url, *, issuer_id: Issuer, now=lambda: datetime.now(UTC)):
        if issuer_id not in ("home-assistant:echo", "home-assistant:victoria") or not callable(now):
            raise ValueError("registered session issuer and clock required")
        parsed = make_url(url)
        site = issuer_id.split(":")[1]
        if parsed.drivername not in ("postgresql", "postgresql+psycopg") or parsed.username != f"home_agent_shared_{site}_session_ingress":
            raise ValueError("dedicated session ingress credentials required")
        query = parse_qsl(url.partition("?")[2], keep_blank_values=True)
        if (len(query) != len(parsed.query) or any(not value for _, value in query) or
            set(parsed.query) - {"sslmode", "sslrootcert"} or
            any(not isinstance(value, str) or not value for value in parsed.query.values()) or
            parsed.query and parsed.query.get("sslmode") != "verify-full"):
            raise ValueError("unsupported session database connection options")
        self.issuer_id, self._now = issuer_id, now
        self.engine = create_async_engine(parsed.set(drivername="postgresql+psycopg"),
            pool_pre_ping=True, pool_size=2, max_overflow=0, pool_timeout=2, pool_recycle=300,
            hide_parameters=True, connect_args={"connect_timeout": 5, "options":
                "-c statement_timeout=7000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=10000"})

    def _check(self, receipt, value):
        now = self._now()
        if (receipt.issuer_id != self.issuer_id or receipt.revocation_id != value.revocation_id or
            receipt.session_commitment != value.session_commitment or not isinstance(now, datetime) or
            now.tzinfo is None or now.utcoffset() is None or receipt.revoked_at > now+timedelta(seconds=1)):
            raise ValueError("session revocation receipt mismatch")

    async def revoke(self, value: SharedLinkSessionRevocation) -> SharedLinkSessionRevocationReceipt:
        if type(value) is not SharedLinkSessionRevocation:
            raise TypeError("original session revocation required")
        value = SharedLinkSessionRevocation.model_validate(value.model_dump())
        async with self.engine.connect() as raw:
            connection = await raw.execution_options(isolation_level="SERIALIZABLE")
            async with connection.begin():
                result = await connection.execute(CALL, value.model_dump())
                receipt = SharedLinkSessionRevocationReceipt.model_validate(dict(result.mappings().one()))
                self._check(receipt, value)
        self._check(receipt, value)
        return receipt

    async def close(self):
        await self.engine.dispose()
