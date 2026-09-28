"""Encrypted retained consent and write-once dispatch accounting.

Local records are bookkeeping, never authorization. The service must recheck
database authority before review delivery, execution and outcome delivery.
"""
from pathlib import Path
import sqlite3
from typing import Literal
from uuid import UUID

from pydantic import Field

from .crypto import FieldCipher, SealedValue
from .personal_memory_contract import Contract
from .personal_memory_consent import SharingAuthority, SharingReview, SharingConfirmation, SharingReviewCommitment

PURPOSE = "personal-preference-consent-journal-v1"


class MissingConsentError(ValueError):
    pass


class RetainedConsent(Contract):
    authority: SharingAuthority = Field(repr=False)
    review: SharingReview = Field(repr=False)
    state: Literal["review", "dispatching", "committed"] = "review"


class ConsentJournal:
    def __init__(self, path, *, cipher):
        if (not isinstance(path, Path) or not path.is_absolute() or path.is_symlink()
                or type(cipher) is not FieldCipher):
            raise ValueError("private durable consent journal required")
        self._cipher = cipher
        self._db = sqlite3.connect(str(path), timeout=2, isolation_level=None)
        try:
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1): raise ValueError("unknown consent journal version")
            if version == 0 and self._db.execute("SELECT count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone()[0]:
                raise ValueError("unknown consent journal contents")
            if version == 1:
                self._check_key()
            if self._db.execute("PRAGMA page_size").fetchone()[0] != 4096:
                raise ValueError("unsupported consent journal page size")
            if self._db.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise ValueError("consent journal durability unavailable")
            self._db.execute("PRAGMA synchronous=FULL")
            if self._db.execute("PRAGMA max_page_count=4096").fetchone()[0] != 4096:
                raise ValueError("consent journal storage limit exceeded")
            self._db.execute("BEGIN IMMEDIATE")
            if version == 0:
                self._db.execute("CREATE TABLE records (id TEXT PRIMARY KEY,nonce BLOB NOT NULL,ciphertext BLOB NOT NULL,digest TEXT NOT NULL)")
                self._write("admission", PURPOSE.encode(), insert=True)
                self._db.execute("PRAGMA user_version=1")
            self._check_key()
            self._db.commit()
        except BaseException:
            self._db.rollback()
            self._db.close()
            raise

    def _check_key(self):
        if self._read("admission") != PURPOSE.encode():
            raise ValueError("consent recovery key unavailable")

    def _read(self, identifier):
        row = self._db.execute("SELECT nonce,ciphertext,digest FROM records WHERE id=?", (identifier,)).fetchone()
        return None if row is None else self._cipher.open(SealedValue(*row), purpose=PURPOSE, artifact_id=identifier)

    def _write(self, identifier, payload, *, insert=False):
        sealed = self._cipher.seal(payload, purpose=PURPOSE, artifact_id=identifier)
        if insert:
            self._db.execute("INSERT INTO records VALUES (?,?,?,?)", (identifier,sealed.nonce,sealed.ciphertext,sealed.sha256))
        else:
            if self._db.execute("UPDATE records SET nonce=?,ciphertext=?,digest=? WHERE id=?",
                                (sealed.nonce,sealed.ciphertext,sealed.sha256,identifier)).rowcount != 1:
                raise ValueError("consent record unavailable")

    def retain(self, authority, review):
        if type(authority) is not SharingAuthority or type(review) is not SharingReview:
            raise TypeError("governed consent review required")
        value = RetainedConsent(authority=SharingAuthority.model_validate(authority.model_dump()),
                                review=SharingReview.model_validate(review.model_dump()))
        identifier = str(review.operation_id)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            raw = self._read(identifier)
            if raw is not None:
                old = RetainedConsent.model_validate_json(raw)
                if old.authority != value.authority or old.review != value.review:
                    raise ValueError("consent operation conflict")
                # Never reset dispatch state when an HTTP request is repeated.
            else:
                self._write(identifier, value.model_dump_json().encode(), insert=True)
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise

    def read(self, operation_id, *, subject, session_commitment):
        if type(operation_id) is not UUID:
            raise TypeError("consent operation UUID required")
        raw = self._read(str(operation_id))
        if raw is None: raise MissingConsentError("consent unavailable")
        value = RetainedConsent.model_validate_json(raw)
        if (value.review.operation_id != operation_id or value.authority.subject != subject
                or value.authority.session_commitment != session_commitment):
            raise ValueError("consent session mismatch")
        return value

    def claim(self, confirmation, *, subject, session_commitment, commitment, now):
        if type(confirmation) is not SharingConfirmation or type(commitment) is not SharingReviewCommitment:
            raise TypeError("explicit consent confirmation required")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            value = self.read(confirmation.operation_id, subject=subject, session_commitment=session_commitment)
            if value.state != "review": raise ValueError("consent already dispatched; look up outcome")
            commitment.verify(confirmation, value.review, value.authority, now=now)
            claimed = value.model_copy(update={"state": "dispatching"})
            self._write(str(confirmation.operation_id), claimed.model_dump_json().encode())
            self._db.commit()
            return claimed
        except BaseException:
            self._db.rollback()
            raise

    def mark_committed(self, operation_id, *, subject, session_commitment):
        """Call only after affirmative database outcome evidence, never timeout."""
        self._db.execute("BEGIN IMMEDIATE")
        try:
            value = self.read(operation_id, subject=subject, session_commitment=session_commitment)
            if value.state not in ("dispatching", "committed"):
                raise ValueError("undispatched consent cannot be completed")
            self._write(str(operation_id), value.model_copy(update={"state": "committed"}).model_dump_json().encode())
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise

    def close(self):
        self._db.close()
