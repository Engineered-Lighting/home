"""Encrypted confirmation recovery journal; not enabled by runtime configuration.

Provision on private durable storage with a dedicated recoverable key. Restore,
erasure and retention admission must be integrated before production enablement.
"""
import hmac
from dataclasses import dataclass, field
from typing import Literal
from pathlib import Path
import sqlite3
from uuid import UUID

from .crypto import FieldCipher, SealedValue
from .shared_link_confirmation import SharedLinkConfirmation, SharedLinkConfirmationDatabase, SharedLinkConfirmationReceipt
from .shared_link_confirmation_preparation import SharedLinkConfirmationPreparer
from .shared_link_confirmation_review import SharedLinkReviewEvidence
from .auth import ServiceIdentity

PURPOSE = "shared-link-confirmation-journal-v1"


@dataclass(frozen=True, slots=True)
class SharedLinkConfirmationJournalObservation:
    """Historical bookkeeping only; not current session or linking authority."""
    state: Literal["not_found", "prepared", "indeterminate", "completed"]
    recorded_receipt: SharedLinkConfirmationReceipt | None = field(default=None, repr=False)


class SharedLinkConfirmationJournal:
    def __init__(self, path: Path, *, cipher: FieldCipher):
        if type(cipher) is not FieldCipher or not isinstance(path, Path) or not path.is_absolute() or path.is_symlink():
            raise ValueError("invalid shared-link journal configuration")
        self._cipher = cipher
        self._db = sqlite3.connect(str(path), timeout=2, isolation_level=None)
        try:
            # Inspect before changing journal mode or limits on an existing file.
            existing_version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if existing_version not in (0, 1, 2):
                raise ValueError("unsupported journal schema")
            if existing_version == 0 and self._db.execute("SELECT count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone()[0]:
                raise ValueError("unknown journal schema")
            if existing_version in (1, 2):
                admission = self._db.execute("SELECT nonce,ciphertext,digest FROM admission WHERE id=1").fetchone()
                if admission is None or cipher.open(SealedValue(*admission), purpose=PURPOSE, artifact_id="admission") != b"shared-link-confirmation-journal-v1":
                    raise ValueError("journal key unavailable")
            if self._db.execute("PRAGMA page_size").fetchone()[0] != 4096:
                raise ValueError("unsupported journal page size")
            if self._db.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise ValueError("journal durability mode unavailable")
            self._db.execute("PRAGMA synchronous=FULL")
            if self._db.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise ValueError("journal durability mode unavailable")
            if self._db.execute("PRAGMA max_page_count=4096").fetchone()[0] != 4096:
                raise ValueError("journal storage limit exceeded")
            self._db.execute("BEGIN IMMEDIATE")
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if self._db.execute("SELECT count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone()[0]:
                    raise ValueError("unknown journal schema")
                self._db.execute("CREATE TABLE admission (id INTEGER PRIMARY KEY CHECK(id=1), nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, digest TEXT NOT NULL)")
                self._db.execute("CREATE TABLE requests (id TEXT PRIMARY KEY, state TEXT NOT NULL CHECK(state IN ('prepared','dispatching','completed')), nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, digest TEXT NOT NULL, receipt_nonce BLOB, receipt_ciphertext BLOB, receipt_digest TEXT)")
                probe = cipher.seal(b"shared-link-confirmation-journal-v1", purpose=PURPOSE, artifact_id="admission")
                self._db.execute("INSERT INTO admission VALUES (1,?,?,?)", (probe.nonce, probe.ciphertext, probe.sha256))
                self._db.execute("PRAGMA user_version=1")
            elif version not in (1, 2):
                raise ValueError("unsupported journal schema")
            row = self._db.execute("SELECT nonce,ciphertext,digest FROM admission WHERE id=1").fetchone()
            if row is None or cipher.open(SealedValue(*row), purpose=PURPOSE, artifact_id="admission") != b"shared-link-confirmation-journal-v1":
                raise ValueError("journal key unavailable")
            if version in (0, 1):
                self._db.execute("CREATE TABLE reviews (id TEXT PRIMARY KEY, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, digest TEXT NOT NULL)")
                self._db.execute("PRAGMA user_version=2")
            self._db.execute("SELECT id,nonce,ciphertext,digest FROM reviews LIMIT 0")
            self._db.commit()
        except BaseException:
            self._db.rollback()
            self._db.close()
            raise

    def prepare(self, preparer, identity, beginning, issuance, echo, victoria, choice, *, review, approval):
        """Validate approved evidence and durably retain it before dispatch.

        Existing ceremonies retain their exact submission. Changed evidence or
        IDs conflict even after an uncertain dispatch; recovery uses inspection.
        This internal method does not authenticate a browser gesture.
        """
        if type(preparer) is not SharedLinkConfirmationPreparer:
            raise TypeError("dedicated confirmation preparer required")
        value = preparer.prepare(identity, beginning, issuance, echo, victoria, choice,
                                 review=review, approval=approval)
        self.retain(value)
        return value

    def review(self, preparer, identity, beginning, issuance, echo, victoria, choice, *, recover_existing=False):
        """Freeze private evidence on disk before showing its review to the owner."""
        if type(preparer) is not SharedLinkConfirmationPreparer:
            raise TypeError("dedicated confirmation preparer required")
        review = preparer.review(identity, beginning, issuance, echo, victoria, choice)
        evidence = SharedLinkReviewEvidence(beginning=beginning, issuance=issuance,
            echo=echo, victoria=victoria, choice=choice, review=review)
        identifier = str(beginning.ceremony_id)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            if recover_existing and self._db.execute("SELECT 1 FROM reviews WHERE id=?", (identifier,)).fetchone():
                previous = self.load_review(preparer, identity, beginning.ceremony_id, choice.session_commitment)
                if (previous.approval is not None or any(getattr(previous, name) != getattr(evidence, name)
                        for name in ("beginning", "issuance", "echo", "victoria", "choice"))):
                    raise ValueError("review recovery conflict")
                self._db.commit()
                return previous
            if (self._db.execute("SELECT 1 FROM reviews WHERE id=?", (identifier,)).fetchone() or
                self._db.execute("SELECT 1 FROM requests WHERE id=?", (identifier,)).fetchone()):
                raise ValueError("review already retained; recover original context")
            if self._db.execute("SELECT count(*) FROM reviews").fetchone()[0] >= 1024:
                raise ValueError("review capacity exhausted")
            sealed = self._cipher.seal(evidence.model_dump_json().encode(), purpose=PURPOSE+":review", artifact_id=identifier)
            self._db.execute("INSERT INTO reviews VALUES (?,?,?,?)", (identifier, sealed.nonce, sealed.ciphertext, sealed.sha256))
            self._db.commit()
            return evidence
        except BaseException:
            self._db.rollback()
            raise

    def load_review(self, preparer, identity, ceremony_id, session_commitment):
        """Internal recovery with current authenticated session supplied by caller.

        Returns private evidence only while fresh; not a browser response. The
        route still enforces current session revocation and authorization.
        """
        if (type(preparer) is not SharedLinkConfirmationPreparer or
            type(identity) is not ServiceIdentity or type(ceremony_id) is not UUID or
            type(session_commitment) is not str):
            raise TypeError("trusted review context required")
        row = self._db.execute("SELECT nonce,ciphertext,digest FROM reviews WHERE id=?", (str(ceremony_id),)).fetchone()
        if row is None:
            raise ValueError("review unavailable")
        evidence = SharedLinkReviewEvidence.model_validate_json(self._cipher.open(
            SealedValue(*row), purpose=PURPOSE+":review", artifact_id=str(ceremony_id)))
        if (evidence.beginning.ceremony_id != ceremony_id or
            not hmac.compare_digest(evidence.choice.session_commitment, session_commitment)):
            raise ValueError("review unavailable")
        preparer.verify_review(*evidence.arguments(identity), review=evidence.review)
        if evidence.approval is not None:
            value = preparer.prepare(*evidence.arguments(identity), review=evidence.review, approval=evidence.approval)
            if self.inspect(value).state == "not_found":
                raise ValueError("review approval outcome inconsistent")
        return evidence

    def approve_review(self, preparer, identity, ceremony_id, approval):
        """Atomically retain the authenticated gesture and its exact submission.

        Caller authenticates the explicit approval; this method validates binding
        only. Browser-supplied proof or account evidence is never accepted here.
        """
        from .shared_link_confirmation_preparation import SharedLinkConfirmationApproval
        if type(approval) is not SharedLinkConfirmationApproval:
            raise TypeError("authenticated approval required")
        approval = SharedLinkConfirmationApproval.model_validate(approval.model_dump())
        self._db.execute("BEGIN IMMEDIATE")
        try:
            evidence = self.load_review(preparer, identity, ceremony_id, approval.session_commitment)
            if evidence.approval is not None and evidence.approval != approval:
                raise ValueError("review approval conflict")
            value = preparer.prepare(*evidence.arguments(identity), review=evidence.review, approval=approval)
            self._retain(value)
            if evidence.approval is None:
                evidence = evidence.model_copy(update={"approval": approval})
                sealed = self._cipher.seal(evidence.model_dump_json().encode(),
                    purpose=PURPOSE+":review", artifact_id=str(ceremony_id))
                self._db.execute("UPDATE reviews SET nonce=?,ciphertext=?,digest=? WHERE id=?",
                    (sealed.nonce, sealed.ciphertext, sealed.sha256, str(ceremony_id)))
            self._db.commit()
            return value
        except BaseException:
            self._db.rollback()
            raise

    def retain(self, value: SharedLinkConfirmation) -> str:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            state = self._retain(value)
            self._db.commit()
            return state
        except BaseException:
            self._db.rollback()
            raise

    def _retain(self, value: SharedLinkConfirmation) -> str:
        """Retain inside the caller's immediate transaction."""
        if type(value) is not SharedLinkConfirmation:
            raise TypeError("prepared shared-link submission required")
        verified = SharedLinkConfirmation.model_validate(value.model_dump())
        payload = verified.model_dump_json().encode()
        identifier = str(verified.ceremony_id)
        row = self._db.execute("SELECT state,nonce,ciphertext,digest FROM requests WHERE id=?", (identifier,)).fetchone()
        if row:
            existing = self._cipher.open(SealedValue(*row[1:]), purpose=PURPOSE, artifact_id=identifier)
            if not hmac.compare_digest(existing, payload):
                raise ValueError("journal request conflict")
            self.inspect(verified)
            return row[0]
        if self._db.execute("SELECT count(*) FROM requests").fetchone()[0] >= 1024:
            raise ValueError("journal capacity exhausted")
        sealed = self._cipher.seal(payload, purpose=PURPOSE, artifact_id=identifier)
        self._db.execute("INSERT INTO requests(id,state,nonce,ciphertext,digest) VALUES (?,'prepared',?,?,?)",
            (identifier, sealed.nonce, sealed.ciphertext, sealed.sha256))
        return "prepared"

    def inspect(self, value: SharedLinkConfirmation) -> SharedLinkConfirmationJournalObservation:
        """Read saved outcome for the exact original request without dispatch.

        Internal recovery only. Possession of a submission is not authentication;
        callers must recheck current authority before exposing private results.
        A recorded receipt is never current linking or retrieval authority.
        """
        if type(value) is not SharedLinkConfirmation:
            raise TypeError("prepared shared-link submission required")
        verified = SharedLinkConfirmation.model_validate(value.model_dump())
        identifier = str(verified.ceremony_id)
        row = self._db.execute("SELECT state,nonce,ciphertext,digest,receipt_nonce,receipt_ciphertext,receipt_digest FROM requests WHERE id=?",
            (identifier,)).fetchone()
        if row is None:
            return SharedLinkConfirmationJournalObservation("not_found")
        payload = self._cipher.open(SealedValue(*row[1:4]), purpose=PURPOSE, artifact_id=identifier)
        if not hmac.compare_digest(payload, verified.model_dump_json().encode()):
            raise ValueError("journal request conflict")
        if row[0] in ("prepared", "dispatching"):
            if any(part is not None for part in row[4:]):
                raise ValueError("journal outcome inconsistent")
            return SharedLinkConfirmationJournalObservation("prepared" if row[0] == "prepared" else "indeterminate")
        if row[0] != "completed" or any(part is None for part in row[4:]):
            raise ValueError("journal outcome inconsistent")
        receipt = SharedLinkConfirmationReceipt.model_validate_json(self._cipher.open(
            SealedValue(*row[4:]), purpose=PURPOSE + ":receipt", artifact_id=identifier))
        if (receipt.link_id != verified.link_id or receipt.revision != 1 or
            receipt.authorization_generation != verified.expected_generation + 1):
            raise ValueError("journal receipt identity or generation mismatch")
        return SharedLinkConfirmationJournalObservation("completed", receipt)

    def recover_request(self, identity, ceremony_id, session_commitment):
        """Recover original metadata for lookup only, including after expiry.

        Caller supplies current authenticated Echo context. This local match is
        not authority: a database lookup must precede any outcome delivery.
        Never use this path to dispatch, renew consent or construct new IDs.
        """
        if (type(identity) is not ServiceIdentity or type(ceremony_id) is not UUID or
            type(session_commitment) is not str or
            (identity.ha_issuer_id, identity.site_id) != ("home-assistant:echo", "echo")):
            raise ValueError("confirmation recovery unavailable")
        row = self._db.execute("SELECT nonce,ciphertext,digest FROM requests WHERE id=?", (str(ceremony_id),)).fetchone()
        if row is None:
            raise ValueError("confirmation recovery unavailable")
        value = SharedLinkConfirmation.model_validate_json(self._cipher.open(
            SealedValue(*row), purpose=PURPOSE, artifact_id=str(ceremony_id)))
        if (value.ceremony_id != ceremony_id or value.echo_subject != identity.ha_user_id or
            not hmac.compare_digest(value.session_commitment, session_commitment)):
            raise ValueError("confirmation recovery unavailable")
        self.inspect(value)
        return value

    def claim(self, ceremony_id: UUID) -> SharedLinkConfirmation:
        if type(ceremony_id) is not UUID:
            raise TypeError("ceremony UUID required")
        identifier = str(ceremony_id)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute("SELECT state,nonce,ciphertext,digest FROM requests WHERE id=?", (identifier,)).fetchone()
            if row is None or row[0] != "prepared":
                raise ValueError("journal request unavailable or outcome uncertain")
            value = SharedLinkConfirmation.model_validate_json(self._cipher.open(
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

    async def dispatch(self, ceremony_id: UUID, database: SharedLinkConfirmationDatabase) -> SharedLinkConfirmationReceipt:
        if type(database) is not SharedLinkConfirmationDatabase:
            raise TypeError("dedicated confirmation adapter required")
        # Commit the claim before the first await/upstream byte. Every failure,
        # including cancellation, leaves dispatching indeterminate; never retry.
        value = self.claim(ceremony_id)
        receipt = await database.confirm(value)
        self._complete(value, receipt)
        return receipt

    async def reconcile(self, value: SharedLinkConfirmation, database: SharedLinkConfirmationDatabase) -> SharedLinkConfirmationJournalObservation:
        """Inspect the exact original operation, never resend confirmation."""
        if type(database) is not SharedLinkConfirmationDatabase:
            raise TypeError("dedicated confirmation adapter required")
        observation = self.inspect(value)
        if observation.state != "indeterminate":
            return observation
        receipt = await database.inspect_confirmation(value)
        if receipt is not None:
            self._complete(value, receipt)
        return self.inspect(value)

    def _complete(self, value: SharedLinkConfirmation, receipt: SharedLinkConfirmationReceipt):
        if type(receipt) is not SharedLinkConfirmationReceipt:
            raise TypeError("confirmation receipt required")
        value = SharedLinkConfirmation.model_validate(value.model_dump())
        ceremony_id = value.ceremony_id
        receipt = SharedLinkConfirmationReceipt.model_validate(receipt.model_dump())
        if (receipt.link_id != value.link_id or receipt.revision != 1 or
            receipt.authorization_generation != value.expected_generation + 1):
            raise ValueError("journal receipt identity mismatch")
        sealed = self._cipher.seal(receipt.model_dump_json().encode(),
            purpose=PURPOSE + ":receipt", artifact_id=str(ceremony_id))
        self._db.execute("BEGIN IMMEDIATE")
        try:
            if self.inspect(value).state != "indeterminate":
                raise ValueError("journal completion conflict")
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
