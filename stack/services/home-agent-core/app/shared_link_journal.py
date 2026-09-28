"""Encrypted issuance recovery journal; not enabled by runtime configuration.

Provision on private durable storage with a dedicated recoverable key. Restore,
erasure and retention admission must be integrated before production enablement.
"""
import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from pathlib import Path
import sqlite3
from uuid import UUID

from .crypto import FieldCipher, SealedValue
from .shared_link_commitments import SharedLinkBegin, SharedLinkCeremonyContext
from .auth import ServiceIdentity
from .shared_link_issuance import SharedLinkIssuanceDatabase, SharedLinkIssuanceReceipt

PURPOSE = "shared-link-issuance-journal-v1"


@dataclass(frozen=True, slots=True)
class SharedLinkJournalObservation:
    """Historical bookkeeping only; not current session or linking authority."""
    state: Literal["not_found", "prepared", "indeterminate", "completed"]
    recorded_receipt: SharedLinkIssuanceReceipt | None = field(default=None, repr=False)


class SharedLinkJournal:
    def __init__(self, path: Path, *, cipher: FieldCipher, now=lambda: datetime.now(UTC)):
        if type(cipher) is not FieldCipher or not callable(now) or not isinstance(path, Path) or not path.is_absolute() or path.is_symlink():
            raise ValueError("invalid shared-link journal configuration")
        self._cipher = cipher
        self._now = now
        self._db = sqlite3.connect(str(path), timeout=2, isolation_level=None)
        try:
            # Inspect before changing journal mode or limits on an existing file.
            existing_version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if existing_version not in (0, 1):
                raise ValueError("unsupported journal schema")
            if existing_version == 0 and self._db.execute("SELECT count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone()[0]:
                raise ValueError("unknown journal schema")
            if existing_version == 1:
                admission = self._db.execute("SELECT nonce,ciphertext,digest FROM admission WHERE id=1").fetchone()
                if admission is None or cipher.open(SealedValue(*admission), purpose=PURPOSE, artifact_id="admission") != b"shared-link-journal-v1":
                    raise ValueError("journal key unavailable")
            if self._db.execute("PRAGMA page_size").fetchone()[0] != 4096:
                raise ValueError("unsupported journal page size")
            if self._db.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
                raise ValueError("journal rollback mode unavailable")
            self._db.execute("PRAGMA synchronous=FULL")
            if self._db.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise ValueError("journal durable synchronization unavailable")
            if self._db.execute("PRAGMA max_page_count=4096").fetchone()[0] != 4096:
                raise ValueError("journal storage limit exceeded")
            self._db.execute("BEGIN IMMEDIATE")
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if self._db.execute("SELECT count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone()[0]:
                    raise ValueError("unknown journal schema")
                self._db.execute("CREATE TABLE admission (id INTEGER PRIMARY KEY CHECK(id=1), nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, digest TEXT NOT NULL)")
                self._db.execute("CREATE TABLE requests (id TEXT PRIMARY KEY, state TEXT NOT NULL CHECK(state IN ('prepared','dispatching','completed')), nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, digest TEXT NOT NULL, receipt_nonce BLOB, receipt_ciphertext BLOB, receipt_digest TEXT)")
                probe = cipher.seal(b"shared-link-journal-v1", purpose=PURPOSE, artifact_id="admission")
                self._db.execute("INSERT INTO admission VALUES (1,?,?,?)", (probe.nonce, probe.ciphertext, probe.sha256))
                self._db.execute("PRAGMA user_version=1")
            elif version != 1:
                raise ValueError("unsupported journal schema")
            row = self._db.execute("SELECT nonce,ciphertext,digest FROM admission WHERE id=1").fetchone()
            if row is None or cipher.open(SealedValue(*row), purpose=PURPOSE, artifact_id="admission") != b"shared-link-journal-v1":
                raise ValueError("journal key unavailable")
            self._db.commit()
        except BaseException:
            self._db.rollback()
            self._db.close()
            raise

    async def prepare(self, identity: ServiceIdentity, context: SharedLinkCeremonyContext,
                      database: SharedLinkIssuanceDatabase) -> SharedLinkBegin:
        """Resolve and durably retain a new request, without dispatching it.

        Existing IDs require explicit recovery, never re-resolution to a new
        owner anchor. Session provenance and restore admission remain caller
        requirements; this internal method does not authenticate a browser.
        """
        if type(database) is not SharedLinkIssuanceDatabase or type(context) is not SharedLinkCeremonyContext:
            raise TypeError("dedicated adapter and trusted ceremony context required")
        verified = SharedLinkCeremonyContext.model_validate(context.model_dump())
        if self._db.execute("SELECT 1 FROM requests WHERE id=?", (str(verified.ceremony_id),)).fetchone():
            raise ValueError("existing journal request requires recovery")
        value = await database.prepare_begin(identity, verified)
        if self.retain(value) != "prepared":
            raise ValueError("existing journal request requires recovery")
        return value

    def retain(self, value: SharedLinkBegin) -> str:
        if type(value) is not SharedLinkBegin:
            raise TypeError("prepared shared-link submission required")
        verified = SharedLinkBegin.model_validate(value.model_dump())
        payload = verified.model_dump_json().encode()
        identifier = str(verified.ceremony_id)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute("SELECT state,nonce,ciphertext,digest FROM requests WHERE id=?", (identifier,)).fetchone()
            if row:
                existing = self._cipher.open(SealedValue(*row[1:]), purpose=PURPOSE, artifact_id=identifier)
                if not hmac.compare_digest(existing, payload):
                    raise ValueError("journal request conflict")
                state = row[0]
            else:
                if self._db.execute("SELECT count(*) FROM requests").fetchone()[0] >= 1024:
                    raise ValueError("journal capacity exhausted")
                sealed = self._cipher.seal(payload, purpose=PURPOSE, artifact_id=identifier)
                self._db.execute("INSERT INTO requests(id,state,nonce,ciphertext,digest) VALUES (?,'prepared',?,?,?)",
                    (identifier, sealed.nonce, sealed.ciphertext, sealed.sha256))
                state = "prepared"
            self._db.commit()
            return state
        except BaseException:
            self._db.rollback()
            raise

    def inspect(self, value: SharedLinkBegin) -> SharedLinkJournalObservation:
        """Read saved outcome for the exact original request without dispatch.

        Internal recovery only. Possession of a submission is not authentication;
        callers must recheck current authority before exposing private results.
        A recorded receipt remains historical even if its deadline is in future.
        """
        if type(value) is not SharedLinkBegin:
            raise TypeError("prepared shared-link submission required")
        verified = SharedLinkBegin.model_validate(value.model_dump())
        identifier = str(verified.ceremony_id)
        row = self._db.execute("SELECT state,nonce,ciphertext,digest,receipt_nonce,receipt_ciphertext,receipt_digest FROM requests WHERE id=?",
            (identifier,)).fetchone()
        if row is None:
            return SharedLinkJournalObservation("not_found")
        payload = self._cipher.open(SealedValue(*row[1:4]), purpose=PURPOSE, artifact_id=identifier)
        if not hmac.compare_digest(payload, verified.model_dump_json().encode()):
            raise ValueError("journal request conflict")
        if row[0] in ("prepared", "dispatching"):
            if any(part is not None for part in row[4:]):
                raise ValueError("journal outcome inconsistent")
            return SharedLinkJournalObservation("prepared" if row[0] == "prepared" else "indeterminate")
        if row[0] != "completed" or any(part is None for part in row[4:]):
            raise ValueError("journal outcome inconsistent")
        receipt = SharedLinkIssuanceReceipt.model_validate_json(self._cipher.open(
            SealedValue(*row[4:]), purpose=PURPOSE + ":receipt", artifact_id=identifier))
        if (receipt.ceremony_id != verified.ceremony_id or receipt.revision != 1 or
            receipt.expires_at != receipt.created_at + timedelta(minutes=5)):
            raise ValueError("journal receipt identity or lifetime mismatch")
        return SharedLinkJournalObservation("completed", receipt)

    def claim(self, ceremony_id: UUID) -> SharedLinkBegin:
        if type(ceremony_id) is not UUID:
            raise TypeError("ceremony UUID required")
        identifier = str(ceremony_id)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute("SELECT state,nonce,ciphertext,digest FROM requests WHERE id=?", (identifier,)).fetchone()
            if row is None or row[0] != "prepared":
                raise ValueError("journal request unavailable or outcome uncertain")
            value = SharedLinkBegin.model_validate_json(self._cipher.open(
                SealedValue(*row[1:]), purpose=PURPOSE, artifact_id=identifier))
            if value.ceremony_id != ceremony_id:
                raise ValueError("journal request identity mismatch")
            if self.inspect(value).state != "prepared":
                raise ValueError("journal outcome inconsistent")
            self._db.execute("UPDATE requests SET state='dispatching' WHERE id=? AND state='prepared'", (identifier,))
            self._db.commit()
            return value
        except BaseException:
            self._db.rollback()
            raise

    def recover_request(self, identity: ServiceIdentity, context: SharedLinkCeremonyContext) -> SharedLinkBegin:
        """Recover the original owner anchor without re-resolving or dispatching.

        Both sessions and all ceremony IDs must come from trusted retained
        admission. Current database authority remains required before delivery.
        """
        if (type(identity) is not ServiceIdentity or type(context) is not SharedLinkCeremonyContext or
            (identity.ha_issuer_id, identity.site_id) != ("home-assistant:echo", "echo")):
            raise ValueError("issuance recovery context unavailable")
        context = SharedLinkCeremonyContext.model_validate(context.model_dump())
        identifier = str(context.ceremony_id)
        row = self._db.execute("SELECT nonce,ciphertext,digest FROM requests WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("issuance recovery unavailable")
        value = SharedLinkBegin.model_validate_json(self._cipher.open(
            SealedValue(*row), purpose=PURPOSE, artifact_id=identifier))
        if (value.echo_subject != identity.ha_user_id or
            any(getattr(value, name) != getattr(context, name) for name in SharedLinkCeremonyContext.model_fields)):
            raise ValueError("issuance recovery context mismatch")
        self.inspect(value)
        return value

    async def dispatch(self, ceremony_id: UUID, database: SharedLinkIssuanceDatabase) -> SharedLinkIssuanceReceipt:
        if type(database) is not SharedLinkIssuanceDatabase:
            raise TypeError("dedicated issuance adapter required")
        # Commit the claim before the first await/upstream byte. Every failure,
        # including cancellation, leaves dispatching indeterminate; never retry.
        value = self.claim(ceremony_id)
        receipt = await database.begin(value)
        self._complete(ceremony_id, receipt)
        now = self._now()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None or receipt.expires_at <= now:
            raise ValueError("journal receipt expired after durable completion")
        return receipt

    async def reconcile(self, value: SharedLinkBegin, database: SharedLinkIssuanceDatabase) -> SharedLinkJournalObservation:
        """Explicit lookup, never a resend or proof that a missing request ended."""
        if type(database) is not SharedLinkIssuanceDatabase:
            raise TypeError("dedicated issuance adapter required")
        observation = self.inspect(value)
        if observation.state != "indeterminate":
            return observation
        receipt = await database.inspect_issuance(value)
        if receipt is not None:
            self._complete(value.ceremony_id, receipt)
        return self.inspect(value)

    def _complete(self, ceremony_id: UUID, receipt: SharedLinkIssuanceReceipt):
        receipt = SharedLinkIssuanceReceipt.model_validate(receipt.model_dump())
        if (receipt.ceremony_id != ceremony_id or receipt.revision != 1 or
            receipt.expires_at != receipt.created_at + timedelta(minutes=5)):
            raise ValueError("journal receipt identity mismatch")
        sealed = self._cipher.seal(receipt.model_dump_json().encode(),
            purpose=PURPOSE + ":receipt", artifact_id=str(ceremony_id))
        self._db.execute("BEGIN IMMEDIATE")
        try:
            changed = self._db.execute("UPDATE requests SET state='completed',receipt_nonce=?,receipt_ciphertext=?,receipt_digest=? WHERE id=? AND state='dispatching'",
                (sealed.nonce, sealed.ciphertext, sealed.sha256, str(ceremony_id))).rowcount
            if changed != 1:
                raise ValueError("journal completion conflict")
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise
    def close(self):
        self._db.close()
