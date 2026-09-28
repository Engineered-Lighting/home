"""Private ceremony issuance composition; no browser or runtime entry point.

The caller must authenticate the Echo owner and obtain both session commitments
through the trusted per-home boundaries. Typed context alone is not authority.
"""
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .auth import ServiceIdentity
from .shared_link_commitments import SharedLinkBegin, SharedLinkCeremonyContext
from .shared_link_issuance import SharedLinkIssuanceDatabase, SharedLinkIssuanceReceipt
from .shared_link_journal import SharedLinkJournal


@dataclass(frozen=True, slots=True)
class SharedLinkIssuedCeremony:
    """Private evidence for challenge admission and retained owner review."""
    beginning: SharedLinkBegin = field(repr=False)
    issuance: SharedLinkIssuanceReceipt = field(repr=False)


class SharedLinkIssuanceService:
    def __init__(self, *, journal, database, now=lambda: datetime.now(UTC)):
        if type(journal) is not SharedLinkJournal or type(database) is not SharedLinkIssuanceDatabase or not callable(now):
            raise TypeError("dedicated issuance dependencies required")
        self._journal, self._database, self._now = journal, database, now

    async def begin(self, identity: ServiceIdentity, context: SharedLinkCeremonyContext) -> SharedLinkIssuedCeremony:
        # prepare resolves the current owner and persists before dispatch. An
        # existing ID is rejected; it never resolves to a replacement anchor.
        value = await self._journal.prepare(identity, context, self._database)
        await self._journal.dispatch(value.ceremony_id, self._database)
        return await self._deliver(value)

    async def recover(self, identity: ServiceIdentity, context: SharedLinkCeremonyContext) -> SharedLinkIssuedCeremony:
        value = self._journal.recover_request(identity, context)
        observed = self._journal.inspect(value)
        if observed.state == "indeterminate":
            observed = await self._journal.reconcile(value, self._database)
        if observed.state != "completed":
            # A prepared record does not establish that upstream accepted work.
            # Recovery cannot turn it into a dispatch or renew the ceremony.
            raise ValueError("issuance outcome unknown")
        return await self._deliver(value)

    async def _deliver(self, value):
        current = await self._database.inspect_issuance(value)
        recorded = self._journal.inspect(value).recorded_receipt
        now = self._now()
        if (current is None or current != recorded or not isinstance(now, datetime) or
            now.tzinfo is None or now.utcoffset() is None or current.expires_at <= now or
            current.created_at > now + timedelta(seconds=1)):
            raise ValueError("issuance authority or freshness unavailable")
        return SharedLinkIssuedCeremony(value, current)
