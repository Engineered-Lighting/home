from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .crypto import FieldCipher, SealedValue, canonical_json, sha256_json


ZERO_HASH = "0" * 64
CODE_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")


class LedgerIntegrityError(RuntimeError):
    pass


class LedgerConflictError(RuntimeError):
    pass


class ErasureLedgerRecord(BaseModel):
    """Content-minimized, encrypted erasure receipt.

    UUIDs are opaque authority identifiers. Free text, identity labels,
    coordinates, utterances, URLs, and before/after values are impossible in
    this schema.
    """

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    outbox_id: uuid.UUID
    erasure_request_id: uuid.UUID
    subject_kind: Literal["descriptor_fact", "person"] = "descriptor_fact"
    principal_id: uuid.UUID | None = None
    fact_id: uuid.UUID | None = None
    person_id: uuid.UUID | None = None
    operation_codes: list[str] = Field(min_length=1, max_length=32)
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    completed_at: datetime
    source_created_at: datetime
    external_pending_codes: list[str] = Field(default_factory=list, max_length=32)
    legacy_untracked_codes: list[str] = Field(default_factory=list, max_length=32)
    checkpoint_affected: bool
    backup_expiry_at: datetime | None = None
    exact_residual_codes: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_subject(self) -> "ErasureLedgerRecord":
        if self.subject_kind == "descriptor_fact":
            if self.principal_id is None or self.fact_id is None or self.person_id is not None:
                raise ValueError("descriptor erasure subject is incomplete")
        elif self.person_id is None or self.principal_id is not None or self.fact_id is not None:
            raise ValueError("person erasure subject is incomplete")
        return self

    @field_validator(
        "operation_codes",
        "external_pending_codes",
        "legacy_untracked_codes",
        "exact_residual_codes",
    )
    @classmethod
    def validate_codes(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("operation/residual codes must be unique")
        if any(not CODE_PATTERN.fullmatch(value) for value in values):
            raise ValueError("operation/residual code is invalid")
        return values

    @field_validator("completed_at", "source_created_at", "backup_expiry_at")
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ledger timestamps must include a UTC offset")
        return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class LedgerHead:
    epoch: int
    head_hash: str


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    epoch: int
    record_hash: str
    record_digest: str
    record: ErasureLedgerRecord


def read_ledger_head(path: Path) -> LedgerHead:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LedgerIntegrityError("erasure ledger head is missing or invalid") from exc
    if set(raw) != {"version", "epoch", "head_hash"} or raw["version"] != 1:
        raise LedgerIntegrityError("erasure ledger head schema is invalid")
    if not isinstance(raw["epoch"], int) or raw["epoch"] < 0:
        raise LedgerIntegrityError("erasure ledger head epoch is invalid")
    if not isinstance(raw["head_hash"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", raw["head_hash"]
    ):
        raise LedgerIntegrityError("erasure ledger head hash is invalid")
    if raw["epoch"] == 0 and raw["head_hash"] != ZERO_HASH:
        raise LedgerIntegrityError("empty erasure ledger head hash is invalid")
    return LedgerHead(epoch=raw["epoch"], head_hash=raw["head_hash"])


class EncryptedErasureLedger:
    """Append-only encrypted, hash-chained ledger outside database backups."""

    def __init__(
        self,
        path: Path,
        key: bytes,
        *,
        head_path: Path | None = None,
        initialize: bool = False,
    ) -> None:
        self.path = path
        self.head_path = head_path or path.with_suffix(path.suffix + ".head")
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self.cipher = FieldCipher(key, key_id="erasure-ledger-v1")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        self._initialize(allow_create=initialize)

    def _initialize(self, *, allow_create: bool) -> None:
        with self._exclusive_lock():
            ledger_existed = self.path.exists()
            head_existed = self.head_path.exists()
            if ledger_existed != head_existed:
                raise LedgerIntegrityError(
                    "erasure ledger and independent head must exist together"
                )
            if not ledger_existed and not allow_create:
                raise LedgerIntegrityError(
                    "erasure ledger is uninitialized; explicit operator init required"
                )
            file_descriptor = os.open(
                self.path, os.O_CREAT | os.O_APPEND | os.O_RDWR, 0o600
            )
            os.close(file_descriptor)
            os.chmod(self.path, 0o600)
            entries = self._read_entries_unlocked()
            expected = (
                LedgerHead(entries[-1].epoch, entries[-1].record_hash)
                if entries
                else LedgerHead(0, ZERO_HASH)
            )
            if head_existed:
                self._reconcile_head_unlocked(entries, expected)
            elif ledger_existed and entries:
                raise LedgerIntegrityError(
                    "non-empty erasure ledger is missing its independent head"
                )
            else:
                self._write_head_unlocked(expected)

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        try:
            import fcntl
        except ImportError as exc:  # pragma: no cover - deployment is Linux
            raise RuntimeError("erasure ledger requires POSIX file locking") from exc
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def head(self) -> LedgerHead:
        with self._exclusive_lock():
            entries = self._read_entries_unlocked()
            actual = (
                LedgerHead(entries[-1].epoch, entries[-1].record_hash)
                if entries
                else LedgerHead(0, ZERO_HASH)
            )
            self._reconcile_head_unlocked(entries, actual)
            return actual

    def entries_after(self, epoch: int) -> list[LedgerEntry]:
        if epoch < 0:
            raise ValueError("ledger epoch cannot be negative")
        with self._exclusive_lock():
            entries = self._read_entries_unlocked()
            head = (
                LedgerHead(entries[-1].epoch, entries[-1].record_hash)
                if entries
                else LedgerHead(0, ZERO_HASH)
            )
            self._reconcile_head_unlocked(entries, head)
            if epoch > head.epoch:
                raise LedgerIntegrityError("database ledger epoch is ahead of ledger")
            return [entry for entry in entries if entry.epoch > epoch]

    def append_once(self, record: ErasureLedgerRecord) -> LedgerEntry:
        record_payload = record.model_dump(mode="json")
        record_digest = sha256_json(record_payload)
        with self._exclusive_lock():
            entries = self._read_entries_unlocked()
            expected_head = (
                LedgerHead(entries[-1].epoch, entries[-1].record_hash)
                if entries
                else LedgerHead(0, ZERO_HASH)
            )
            self._reconcile_head_unlocked(entries, expected_head)
            for entry in entries:
                if entry.record.outbox_id == record.outbox_id:
                    if entry.record_digest != record_digest:
                        raise LedgerConflictError(
                            "outbox id already has a different erasure receipt"
                        )
                    return entry

            epoch = expected_head.epoch + 1
            artifact_id = f"{epoch}:{record.outbox_id}"
            sealed = self.cipher.seal(
                canonical_json(record_payload),
                purpose="erasure-ledger-entry",
                artifact_id=artifact_id,
            )
            envelope: dict[str, Any] = {
                "version": 1,
                "epoch": epoch,
                "outbox_id": str(record.outbox_id),
                "previous_hash": expected_head.head_hash,
                "nonce": self._encode(sealed.nonce),
                "ciphertext": self._encode(sealed.ciphertext),
                "plaintext_hmac": sealed.sha256,
                "record_digest": record_digest,
            }
            record_hash = hashlib.sha256(canonical_json(envelope)).hexdigest()
            envelope["record_hash"] = record_hash
            line = canonical_json(envelope) + b"\n"
            descriptor = os.open(self.path, os.O_APPEND | os.O_WRONLY)
            try:
                written = os.write(descriptor, line)
                if written != len(line):
                    raise OSError("short erasure ledger append")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._write_head_unlocked(LedgerHead(epoch, record_hash))
            return LedgerEntry(epoch, record_hash, record_digest, record)

    def _reconcile_head_unlocked(
        self, entries: list[LedgerEntry], actual: LedgerHead
    ) -> None:
        persisted = read_ledger_head(self.head_path)
        if persisted == actual:
            return
        # A crash can occur after the append fsync and before atomic head
        # replacement. Forward recovery is safe only when the persisted head
        # exactly names a verified prefix of the immutable hash chain.
        if persisted.epoch > actual.epoch:
            raise LedgerIntegrityError("erasure ledger head is ahead of ledger")
        prefix_hash = (
            ZERO_HASH
            if persisted.epoch == 0
            else entries[persisted.epoch - 1].record_hash
        )
        if persisted.head_hash != prefix_hash:
            raise LedgerIntegrityError(
                "erasure ledger head does not match append-only ledger"
            )
        self._write_head_unlocked(actual)

    def _read_entries_unlocked(self) -> list[LedgerEntry]:
        try:
            lines = self.path.read_bytes().splitlines()
        except OSError as exc:
            raise LedgerIntegrityError("erasure ledger cannot be read") from exc
        entries: list[LedgerEntry] = []
        previous_hash = ZERO_HASH
        for expected_epoch, line in enumerate(lines, start=1):
            if not line or len(line) > 1_048_576:
                raise LedgerIntegrityError("erasure ledger line is invalid")
            try:
                envelope = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise LedgerIntegrityError("erasure ledger line is invalid") from exc
            required = {
                "version",
                "epoch",
                "outbox_id",
                "previous_hash",
                "nonce",
                "ciphertext",
                "plaintext_hmac",
                "record_digest",
                "record_hash",
            }
            if set(envelope) != required or envelope["version"] != 1:
                raise LedgerIntegrityError("erasure ledger envelope schema is invalid")
            if envelope["epoch"] != expected_epoch:
                raise LedgerIntegrityError("erasure ledger epoch is not contiguous")
            if envelope["previous_hash"] != previous_hash:
                raise LedgerIntegrityError("erasure ledger hash chain is broken")
            record_hash = envelope.pop("record_hash")
            calculated_hash = hashlib.sha256(canonical_json(envelope)).hexdigest()
            envelope["record_hash"] = record_hash
            if not isinstance(record_hash, str) or not __import__(
                "hmac"
            ).compare_digest(record_hash, calculated_hash):
                raise LedgerIntegrityError("erasure ledger record hash is invalid")
            try:
                outbox_id = uuid.UUID(envelope["outbox_id"])
                plaintext = self.cipher.open(
                    SealedValue(
                        nonce=self._decode(envelope["nonce"]),
                        ciphertext=self._decode(envelope["ciphertext"]),
                        sha256=envelope["plaintext_hmac"],
                    ),
                    purpose="erasure-ledger-entry",
                    artifact_id=f"{expected_epoch}:{outbox_id}",
                )
                record = ErasureLedgerRecord.model_validate_json(plaintext)
            except Exception as exc:
                raise LedgerIntegrityError(
                    "erasure ledger ciphertext failed verification"
                ) from exc
            record_digest = sha256_json(record.model_dump(mode="json"))
            if record.outbox_id != outbox_id or not __import__("hmac").compare_digest(
                record_digest, envelope["record_digest"]
            ):
                raise LedgerIntegrityError("erasure ledger record binding is invalid")
            entries.append(
                LedgerEntry(expected_epoch, record_hash, record_digest, record)
            )
            previous_hash = record_hash
        return entries

    def _write_head_unlocked(self, head: LedgerHead) -> None:
        temporary = self.head_path.with_name(
            f".{self.head_path.name}.{uuid.uuid4()}.tmp"
        )
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            payload = (
                canonical_json(
                    {"version": 1, "epoch": head.epoch, "head_hash": head.head_hash}
                )
                + b"\n"
            )
            if os.write(descriptor, payload) != len(payload):
                raise OSError("short erasure ledger head write")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self.head_path)
        os.chmod(self.head_path, 0o600)
        directory = os.open(self.head_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        if not isinstance(value, str):
            raise ValueError("encoded ledger field must be a string")
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
