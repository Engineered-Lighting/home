"""Connect retained review/approval to current governed database authority.

Inputs must come from an authenticated Echo BFF after origin/CSRF checks.
This service does not accept account evidence from the browser and does not
provision runtime keys, credentials, grants or routes.
"""
import hmac
from datetime import UTC, datetime
from uuid import UUID

from .auth import ServiceIdentity
from .shared_link_confirmation import SharedLinkConfirmationDatabase
from .shared_link_confirmation_preparation import SharedLinkConfirmationPreparer, SharedLinkConfirmationApproval
from .shared_link_confirmation_journal import SharedLinkConfirmationJournal
from .shared_link_issuance import SharedLinkIssuanceDatabase
from .shared_link_issuance_service import SharedLinkIssuedCeremony


class SharedLinkReviewService:
    def __init__(self, *, journal, preparer, issuance, confirmation, now=lambda: datetime.now(UTC)):
        if (type(journal) is not SharedLinkConfirmationJournal or
            type(preparer) is not SharedLinkConfirmationPreparer or
            type(issuance) is not SharedLinkIssuanceDatabase or
            type(confirmation) is not SharedLinkConfirmationDatabase or not callable(now)):
            raise TypeError("dedicated linking dependencies required")
        self._journal = journal
        self._preparer = preparer
        self._issuance = issuance
        self._confirmation = confirmation
        self._now = now

    async def _pending(self, evidence):
        current = await self._issuance.inspect_issuance(evidence.beginning)
        if current is None or current != evidence.issuance:
            raise ValueError("linking authority unavailable")

    async def prepare_review(self, identity, issued, echo, victoria, choice, *, recover_existing=False):
        """Retain trusted service evidence before returning the owner review.

        ``issued`` comes from the issuance service; proof receipts come from
        the authenticated per-issuer proof transport. Never deserialize browser
        account evidence into this method. This does not manufacture approval.
        """
        if type(issued) is not SharedLinkIssuedCeremony:
            raise TypeError("issued ceremony evidence required")
        arguments = (identity, issued.beginning, issued.issuance, echo, victoria, choice)
        # Reject malformed, mixed-parent, wrong-session or expired evidence
        # before any database access or durable private review write.
        self._preparer.review(*arguments)
        current = await self._issuance.inspect_issuance(issued.beginning)
        if current is None or current != issued.issuance:
            raise ValueError("linking authority unavailable")
        self._journal.review(self._preparer, *arguments, recover_existing=recover_existing)
        # Read through the existing delivery boundary: this rechecks authority
        # and freshness after SQLite persistence, then emits only review fields.
        return await self.review(identity, issued.beginning.ceremony_id, choice.session_commitment)

    async def review(self, identity: ServiceIdentity, ceremony_id: UUID, session_commitment: str):
        evidence = self._journal.load_review(self._preparer, identity, ceremony_id, session_commitment)
        if evidence.approval is not None:
            raise ValueError("review already approved")
        # Recheck owner, both sessions, issuer registrations and generation in
        # the database. A valid encrypted local record alone is not authority.
        await self._pending(evidence)
        evidence = self._journal.load_review(self._preparer, identity, ceremony_id, session_commitment)
        if evidence.approval is not None:
            raise ValueError("review already approved")
        return {"version": 1, "ceremony_id": str(ceremony_id),
            "gesture_id": str(evidence.choice.gesture_id),
            "reviewed_digest": evidence.review.proposal_digest,
            "expires_at": evidence.review.expires_at.isoformat(),
            "accounts": [
                {"site_id": "echo", "issuer_id": evidence.echo.issuer_id, "subject": evidence.echo.subject},
                {"site_id": "victoria", "issuer_id": evidence.victoria.issuer_id, "subject": evidence.victoria.subject},
            ]}

    async def confirm(self, identity: ServiceIdentity, ceremony_id: UUID, session_commitment: str,
                      *, gesture_id: UUID, reviewed_digest: str):
        evidence = self._journal.load_review(self._preparer, identity, ceremony_id, session_commitment)
        if (type(gesture_id) is not UUID or type(reviewed_digest) is not str or
            gesture_id != evidence.choice.gesture_id or
            not hmac.compare_digest(reviewed_digest, evidence.review.proposal_digest)):
            raise ValueError("approval differs from retained review")
        approval = evidence.approval
        if approval is None:
            await self._pending(evidence)
            approval = SharedLinkConfirmationApproval(gesture_id=gesture_id,
                session_commitment=session_commitment, reviewed_digest=reviewed_digest, approved_at=self._now())
        # The first approval time is durable. A repeated HTTP request must never
        # manufacture a different confirmation commitment or reset dispatch.
        value = self._journal.approve_review(self._preparer, identity, ceremony_id, approval)
        observation = self._journal.inspect(value)
        if observation.state == "prepared":
            await self._journal.dispatch(ceremony_id, self._confirmation)
        elif observation.state == "indeterminate":
            observation = await self._journal.reconcile(value, self._confirmation)
            if observation.state != "completed":
                raise ValueError("confirmation outcome unknown")
        return await self._deliver(value)

    async def outcome(self, identity: ServiceIdentity, ceremony_id: UUID, session_commitment: str):
        """Lookup the original dispatched operation without a fresh approval."""
        value = self._journal.recover_request(identity, ceremony_id, session_commitment)
        observation = self._journal.inspect(value)
        if observation.state == "indeterminate":
            observation = await self._journal.reconcile(value, self._confirmation)
        if observation.state != "completed":
            raise ValueError("confirmation outcome unknown")
        return await self._deliver(value)

    async def _deliver(self, value):
        # Even a locally completed receipt requires current authority before
        # delivery; logout/unlink/privacy changes can happen after commit.
        receipt = await self._confirmation.inspect_confirmation(value)
        if receipt is None or receipt != self._journal.inspect(value).recorded_receipt:
            raise ValueError("confirmation outcome unavailable")
        return {"version": 1, "status": "confirmed", "ceremony_id": str(value.ceremony_id), "link_id": str(receipt.link_id),
            "authorization_generation": receipt.authorization_generation,
            "revision": receipt.revision, "confirmed_at": receipt.confirmed_at.isoformat()}
