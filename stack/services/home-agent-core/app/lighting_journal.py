"""Encrypted retained lighting reviews and write-once dispatch accounting.

Local records are bookkeeping, never authorization: the service re-resolves
database authority before review delivery, dispatch and outcome delivery.

An action moves ``review`` -> ``dispatching`` exactly once, durably, before any
home is asked to act. Per-operation results then only move from ``pending`` or
an uncertain state towards a definite one; a definite result never changes.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Literal
from uuid import UUID

from pydantic import Field

from .crypto import FieldCipher, SealedValue
from .lighting_authority import (LightingAuthority, LightingConsentCommitment, LightingConsentConfirmation,
                                 LightingConsentReview)
from .lighting_contract import Contract, from_wire
from .lighting_review import LightingActionCommitment, LightingActionConfirmation, LightingActionReview

PURPOSE = "lighting-journal-v1"
Result = Literal["pending", "succeeded", "failed", "not_sent", "indeterminate", "dispatching", "unknown"]
DEFINITE = frozenset({"succeeded", "failed", "not_sent"})


class MissingLightingRecord(ValueError):
    pass


class RetainedLightingConsent(Contract):
    authority: LightingAuthority = Field(repr=False)
    review: LightingConsentReview = Field(repr=False)
    state: Literal["review", "dispatching", "committed"] = "review"


class RetainedLightingAction(Contract):
    authority: LightingAuthority = Field(repr=False)
    review: LightingActionReview = Field(repr=False)
    state: Literal["review", "dispatching", "finished"] = "review"
    results: tuple[Result, ...] = ()


class LightingJournal:
    def __init__(self, path, *, cipher):
        if (not isinstance(path, Path) or not path.is_absolute() or path.is_symlink()
                or type(cipher) is not FieldCipher):
            raise ValueError("private durable lighting journal required")
        self._cipher = cipher
        self._db = sqlite3.connect(str(path), timeout=2, isolation_level=None)
        try:
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("unknown lighting journal version")
            if version == 0 and self._db.execute(
                    "SELECT count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone()[0]:
                raise ValueError("unknown lighting journal contents")
            if self._db.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise ValueError("lighting journal durability unavailable")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("BEGIN IMMEDIATE")
            if version == 0:
                self._db.execute("CREATE TABLE records (id TEXT PRIMARY KEY,nonce BLOB NOT NULL,"
                                 "ciphertext BLOB NOT NULL,digest TEXT NOT NULL)")
                self._write("admission", PURPOSE.encode(), insert=True)
                self._db.execute("PRAGMA user_version=1")
            if self._read("admission") != PURPOSE.encode():
                raise ValueError("lighting journal key unavailable")
            self._db.commit()
        except BaseException:
            self._db.rollback()
            self._db.close()
            raise

    def _read(self, identifier):
        row = self._db.execute("SELECT nonce,ciphertext,digest FROM records WHERE id=?", (identifier,)).fetchone()
        return None if row is None else self._cipher.open(SealedValue(*row), purpose=PURPOSE, artifact_id=identifier)

    def _write(self, identifier, payload, *, insert=False):
        sealed = self._cipher.seal(payload, purpose=PURPOSE, artifact_id=identifier)
        if insert:
            self._db.execute("INSERT INTO records VALUES (?,?,?,?)",
                             (identifier, sealed.nonce, sealed.ciphertext, sealed.sha256))
        elif self._db.execute("UPDATE records SET nonce=?,ciphertext=?,digest=? WHERE id=?",
                              (sealed.nonce, sealed.ciphertext, sealed.sha256, identifier)).rowcount != 1:
            raise ValueError("lighting record unavailable")

    def _transaction(self, work):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            result = work()
            self._db.commit()
            return result
        except BaseException:
            self._db.rollback()
            raise

    def _load(self, kind, model, operation_id, subject, session_commitment):
        if type(operation_id) is not UUID:
            raise TypeError("lighting operation UUID required")
        raw = self._read(f"{kind}:{operation_id}")
        if raw is None:
            raise MissingLightingRecord("lighting operation unavailable")
        value = from_wire(model, json.loads(raw))
        if (value.review.operation_id != operation_id or value.authority.subject != subject
                or value.authority.session_commitment != session_commitment):
            raise ValueError("lighting session mismatch")
        return value

    def _retain(self, kind, value):
        identifier = f"{kind}:{value.review.operation_id}"

        def work():
            raw = self._read(identifier)
            if raw is None:
                self._write(identifier, value.model_dump_json().encode(), insert=True)
                return value
            old = from_wire(type(value), json.loads(raw))
            if old.authority != value.authority or old.review != value.review:
                raise ValueError("lighting operation conflict")
            # A repeated HTTP request never resets dispatch state.
            return old
        return self._transaction(work)

    # Consent ------------------------------------------------------------

    def retain_consent(self, authority, review):
        if type(authority) is not LightingAuthority or type(review) is not LightingConsentReview:
            raise TypeError("governed lighting consent review required")
        return self._retain("consent", RetainedLightingConsent(authority=authority, review=review))

    def read_consent(self, operation_id, *, subject, session_commitment):
        return self._load("consent", RetainedLightingConsent, operation_id, subject, session_commitment)

    def claim_consent(self, confirmation, *, subject, session_commitment, commitment, now):
        if type(confirmation) is not LightingConsentConfirmation or type(commitment) is not LightingConsentCommitment:
            raise TypeError("explicit lighting consent confirmation required")

        def work():
            value = self.read_consent(confirmation.operation_id, subject=subject, session_commitment=session_commitment)
            if value.state != "review":
                raise ValueError("lighting consent already dispatched; look up outcome")
            commitment.verify(confirmation, value.review, value.authority, now=now)
            claimed = value.model_copy(update={"state": "dispatching"})
            self._write(f"consent:{confirmation.operation_id}", claimed.model_dump_json().encode())
            return claimed
        return self._transaction(work)

    def mark_consent_committed(self, operation_id, *, subject, session_commitment):
        def work():
            value = self.read_consent(operation_id, subject=subject, session_commitment=session_commitment)
            if value.state not in ("dispatching", "committed"):
                raise ValueError("undispatched lighting consent cannot be completed")
            self._write(f"consent:{operation_id}", value.model_copy(update={"state": "committed"}).model_dump_json().encode())
        self._transaction(work)

    # Actions ------------------------------------------------------------

    def retain_action(self, authority, review):
        if type(authority) is not LightingAuthority or type(review) is not LightingActionReview:
            raise TypeError("governed lighting action review required")
        return self._retain("action", RetainedLightingAction(authority=authority, review=review))

    def read_action(self, operation_id, *, subject, session_commitment):
        return self._load("action", RetainedLightingAction, operation_id, subject, session_commitment)

    def claim_action(self, confirmation, *, subject, session_commitment, commitment, now):
        """Durably mark the action dispatched before any home is asked to act."""
        if type(confirmation) is not LightingActionConfirmation or type(commitment) is not LightingActionCommitment:
            raise TypeError("explicit lighting action confirmation required")

        def work():
            value = self.read_action(confirmation.operation_id, subject=subject, session_commitment=session_commitment)
            if value.state != "review":
                raise ValueError("lighting action already dispatched; look up outcome")
            commitment.verify(confirmation, value.review, value.authority, now=now)
            claimed = value.model_copy(update={"state": "dispatching",
                                               "results": ("pending",) * len(value.review.operations)})
            self._write(f"action:{confirmation.operation_id}", claimed.model_dump_json().encode())
            return claimed
        return self._transaction(work)

    def record_result(self, operation_id, index, result, *, subject, session_commitment):
        def work():
            value = self.read_action(operation_id, subject=subject, session_commitment=session_commitment)
            if value.state == "review" or not 0 <= index < len(value.results):
                raise ValueError("undispatched lighting operation")
            if value.results[index] in DEFINITE:
                return value  # a definite result never changes
            results = value.results[:index] + (result,) + value.results[index + 1:]
            updated = value.model_copy(update={"results": results})
            self._write(f"action:{operation_id}", updated.model_dump_json().encode())
            return updated
        return self._transaction(work)

    def finish_action(self, operation_id, *, subject, session_commitment):
        def work():
            value = self.read_action(operation_id, subject=subject, session_commitment=session_commitment)
            if value.state == "review":
                raise ValueError("undispatched lighting action cannot finish")
            updated = value.model_copy(update={"state": "finished"})
            self._write(f"action:{operation_id}", updated.model_dump_json().encode())
            return updated
        return self._transaction(work)

    def close(self):
        self._db.close()
