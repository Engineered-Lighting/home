from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HOME_AGENT_",
        case_sensitive=False,
        extra="ignore",
    )

    role: Literal["api", "ingest", "worker", "restore", "all"] = "api"
    port: int = Field(default=8104, ge=1, le=65535)
    database_url: SecretStr
    runtime_spool_path: Path = Path("/runtime/runtime.sqlite")
    storage_monitor_path: Path = Path("/storage-monitor/durable")
    runtime_spool_key: SecretStr | None = None
    knowledge_encryption_key: SecretStr | None = None
    erasure_ledger_path: Path = Path("/erasure-ledger/ledger.jsonl")
    erasure_ledger_head_path: Path = Path("/erasure-ledger/ledger.head.json")
    erasure_ledger_key: SecretStr | None = None
    runtime_ttl_seconds: int = Field(default=86_400, ge=300, le=86_400)
    runtime_max_bytes: int = Field(default=100 * 1024 * 1024, ge=1_048_576)
    outbox_poll_seconds: float = Field(default=1.0, ge=0.05, le=60)
    outbox_claim_lease_seconds: int = Field(default=60, ge=10, le=3600)
    restore_gate_cache_seconds: float = Field(default=1.0, ge=0, le=60)
    edge_token: SecretStr | None = Field(default=None, min_length=32)
    service_token: SecretStr | None = Field(default=None, min_length=32)
    operator_token: SecretStr | None = Field(default=None, min_length=32)
    bootstrap_token: SecretStr | None = Field(default=None, min_length=32)
    policy_version: str = Field(
        default="home-agent-mvp-v1", min_length=1, max_length=128
    )
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    rollout_mode: Literal["record_only", "shadow", "canary"] = "record_only"
    readiness_migration: str = "0003_resource_budgets"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator(
        "runtime_spool_key", "knowledge_encryption_key", "erasure_ledger_key"
    )
    @classmethod
    def validate_spool_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return value
        raw = value.get_secret_value()
        try:
            decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        except (ValueError, binascii.Error) as exc:
            raise ValueError("must be URL-safe base64") from exc
        if len(decoded) != 32:
            raise ValueError("must decode to exactly 32 bytes")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        url = value.get_secret_value()
        if not url.startswith(("postgresql+psycopg://", "postgresql://")):
            raise ValueError("PostgreSQL with psycopg is the only supported authority")
        return value

    def decoded_spool_key(self) -> bytes:
        if self.runtime_spool_key is None:
            raise ValueError("runtime spool key is disabled for this role")
        raw = self.runtime_spool_key.get_secret_value()
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))

    def decoded_knowledge_key(self) -> bytes:
        if self.knowledge_encryption_key is None:
            raise ValueError("durable knowledge key is disabled for this role")
        raw = self.knowledge_encryption_key.get_secret_value()
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))

    def decoded_erasure_ledger_key(self) -> bytes:
        if self.erasure_ledger_key is None:
            raise ValueError("erasure ledger key is disabled for this role")
        raw = self.erasure_ledger_key.get_secret_value()
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))

    def async_database_url(self) -> str:
        url = self.database_url.get_secret_value()
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url.removeprefix("postgresql://")
        return url

    @model_validator(mode="after")
    def validate_secret_separation(self) -> "Settings":
        if self.role in {"ingest", "worker", "all"} and self.runtime_spool_key is None:
            raise ValueError("ingest and worker roles require the runtime spool key")
        if (
            self.role in {"api", "ingest", "all"}
            and self.knowledge_encryption_key is None
        ):
            raise ValueError("api and ingest roles require the durable knowledge key")
        if (
            self.role in {"worker", "restore", "all"}
            and self.erasure_ledger_key is None
        ):
            raise ValueError("worker and restore roles require the erasure ledger key")
        if (
            self.runtime_spool_key is not None
            and self.knowledge_encryption_key is not None
            and hmac_compare(self.runtime_spool_key, self.knowledge_encryption_key)
        ):
            raise ValueError("runtime spool and durable knowledge keys must differ")
        if self.role in {"api", "all"} and self.service_token is None:
            raise ValueError("api role requires a BFF service credential")
        if self.role in {"ingest", "all"} and self.edge_token is None:
            raise ValueError("ingest role requires an edge credential")
        if self.role == "api" and (
            self.runtime_spool_key is not None
            or self.erasure_ledger_key is not None
            or self.edge_token is not None
        ):
            raise ValueError("api role may not receive spool, ledger, or edge secrets")
        if self.role == "ingest" and (
            self.erasure_ledger_key is not None
            or self.service_token is not None
            or self.operator_token is not None
            or self.bootstrap_token is not None
        ):
            raise ValueError("ingest role may not receive ledger or API credentials")
        if self.role == "worker" and (
            self.knowledge_encryption_key is not None
            or self.edge_token is not None
            or self.service_token is not None
            or self.operator_token is not None
            or self.bootstrap_token is not None
        ):
            raise ValueError("worker role may not receive knowledge or bearer secrets")
        if self.role == "restore" and any(
            value is not None
            for value in (
                self.runtime_spool_key,
                self.knowledge_encryption_key,
                self.edge_token,
                self.service_token,
                self.operator_token,
                self.bootstrap_token,
            )
        ):
            raise ValueError(
                "restore role may receive only database and ledger secrets"
            )
        all_secrets = [
            value
            for value in (
                self.runtime_spool_key,
                self.knowledge_encryption_key,
                self.erasure_ledger_key,
                self.edge_token,
                self.service_token,
                self.operator_token,
                self.bootstrap_token,
            )
            if value is not None
        ]
        for index, left in enumerate(all_secrets):
            for right in all_secrets[index + 1 :]:
                if hmac_compare(left, right):
                    raise ValueError("every configured secret class must be unique")
        return self


def hmac_compare(left: SecretStr, right: SecretStr) -> bool:
    import hmac

    return hmac.compare_digest(left.get_secret_value(), right.get_secret_value())
