"""Explicit cross-home lighting: consent, review, one dispatch, outcome lookup.

* Consent: the owner allows lighting once (both homes), like preference
  sharing. Grants are written only after explicit confirmation.
* Propose: re-resolve authority, read each named home's inventory, resolve
  names (or ask a clarifying question) and retain a frozen, signed review.
* Confirm: re-resolve authority, verify the exact review, durably claim the
  dispatch, then ask each home once per operation. Homes run concurrently;
  within a home, authority and the review's expiry are re-checked before every
  operation, and a home that gave an uncertain answer is not asked again. An
  operation that could not start stays pending and is settled as not sent.
* Outcome: settle uncertain operations by asking each home what happened,
  never while this process is still dispatching them.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl
from uuid import UUID

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from .errors import DomainError, ForbiddenError
from .lighting_authority import (ROLE, LightingConsentConfirmation, LightingGrantStorage)
from .lighting_contract import Clarification, LightingProposalRequest
from .lighting_edge_client import EdgeLightingClient, LightingRefused, OutcomeUnknown
from .lighting_journal import DEFINITE, LightingJournal, MissingLightingRecord
from .lighting_resolution import resolve
from .lighting_review import LightingActionCommitment, LightingActionConfirmation

# Asked again at outcome lookup. The home's own 'indeterminate' is final there.
UNSETTLED = frozenset({"pending", "dispatching", "unknown"})
# An operation starts only if the home can still act and verify before the review expires.
OPERATION_BUDGET = timedelta(seconds=14)
ANCHOR = ("principal_id", "person_id", "link_id", "link_revision", "authorization_generation")


class LightingUnavailable(DomainError):
    status_code = 503
    code = "home_lighting_unavailable"


class LightingNotPermitted(DomainError):
    status_code = 403
    code = "lighting_not_permitted"


class LightingDatabase:
    def __init__(self, url):
        parsed = make_url(url)
        query = parse_qsl(url.partition("?")[2], keep_blank_values=True)
        if (parsed.drivername not in ("postgresql", "postgresql+psycopg") or parsed.username != ROLE
                or len(query) != len(parsed.query) or any(not value for _, value in query)
                or set(parsed.query) - {"sslmode", "sslrootcert"}
                or parsed.query and parsed.query.get("sslmode") != "verify-full"):
            raise ValueError("dedicated lighting database credentials required")
        self.engine = create_async_engine(parsed.set(drivername="postgresql+psycopg"),
            isolation_level="SERIALIZABLE", pool_size=1, max_overflow=0, pool_timeout=2,
            pool_pre_ping=True, pool_recycle=300, hide_parameters=True,
            connect_args={"connect_timeout": 5, "options":
                "-c statement_timeout=7000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=10000"})

    def transaction(self):
        return self.engine.begin()

    async def close(self):
        await self.engine.dispose()


def _summary(value):
    review = value.review
    results = value.results or ("pending",) * len(review.operations)
    shown = [result if result in DEFINITE else "unknown" for result in results]
    status = ("review" if value.state == "review" else
              "unknown" if "unknown" in shown else
              "done" if all(result == "succeeded" for result in shown) else
              "partial" if "succeeded" in shown else "failed")
    return {"version": 1, "operation_id": str(review.operation_id), "status": status,
            "results": [{"site_id": op.site_id, "name": op.name, "operation": op.operation,
                         "brightness": op.brightness, "status": result}
                        for op, result in zip(review.operations, shown)]}


class LightingService:
    def __init__(self, *, database, storage, commitment, journal, clients, admission, grant_lifetime,
                 now=lambda: datetime.now(UTC)):
        if (type(database) is not LightingDatabase or type(storage) is not LightingGrantStorage
                or type(commitment) is not LightingActionCommitment or type(journal) is not LightingJournal
                or not isinstance(clients, dict) or set(clients) - {"echo", "victoria"}
                or not all(type(c) is EdgeLightingClient and c.site_id == s for s, c in clients.items())
                or not callable(admission) or not callable(now)
                or type(grant_lifetime) is not timedelta or not timedelta(0) < grant_lifetime <= timedelta(days=365)):
            raise TypeError("provisioned lighting runtime required")
        self.database, self.storage, self.commitment, self.journal = database, storage, commitment, journal
        self.clients, self.admission, self.grant_lifetime, self.now = clients, admission, grant_lifetime, now
        self._dispatching = set()

    @staticmethod
    def _scope(session):
        if (type(session) is not dict or set(session) != {"issuer_id", "subject", "session_commitment"}
                or session["issuer_id"] != "home-assistant:echo"):
            raise ForbiddenError("authenticated Echo lighting session required")
        return {name: session[name] for name in ("subject", "session_commitment")}

    async def _authority(self, scope):
        await self.admission()
        async with self.database.transaction() as connection:
            return await self.storage.resolve(connection, **scope)

    async def _same_owner(self, scope, retained):
        """Admission and a fresh owner anchor gate every delivery of a retained record."""
        current = await self._authority(scope)
        if any(getattr(current, name) != getattr(retained.authority, name) for name in ANCHOR):
            raise ForbiddenError("lighting owner changed")
        return current

    @staticmethod
    def _unchanged(current, retained):
        return current.model_dump(exclude={"valid_until"}) == retained.authority.model_dump(exclude={"valid_until"})

    # Consent ------------------------------------------------------------

    async def consent_propose(self, session, operation_id):
        scope = self._scope(session)
        if type(operation_id) is not UUID:
            raise TypeError("lighting consent operation UUID required")
        try:
            retained = self.journal.read_consent(operation_id, **scope)
        except MissingLightingRecord:
            authority = await self._authority(scope)
            now = self.now()
            review = self.storage.commitment.prepare(operation_id, authority, now=now,
                                                     grants_expire_at=now + self.grant_lifetime)
            retained = self.journal.retain_consent(authority, review)
        if retained.state != "review":
            raise ValueError("lighting consent already dispatched; use outcome lookup")
        current = await self._authority(scope)
        if current.model_dump(exclude={"valid_until"}) != retained.authority.model_dump(exclude={"valid_until"}):
            raise ForbiddenError("lighting authority changed after review")
        self.storage.commitment.verify(LightingConsentConfirmation(operation_id=retained.review.operation_id,
            reviewed_digest=retained.review.reviewed_digest), retained.review, retained.authority, now=self.now())
        review = retained.review
        return {"version": 1, "operation_id": str(review.operation_id), "source": review.source,
                "applies_to": review.applies_to, "effect": review.effect,
                "grants_expire_at": review.grants_expire_at.isoformat(), "expires_at": review.expires_at.isoformat(),
                "reviewed_digest": review.reviewed_digest}

    async def consent_confirm(self, session, confirmation):
        scope = self._scope(session)
        if type(confirmation) is not LightingConsentConfirmation:
            raise TypeError("exact owner lighting consent required")
        retained = self.journal.read_consent(confirmation.operation_id, **scope)
        if confirmation.reviewed_digest != retained.review.reviewed_digest:
            raise ValueError("confirmation differs from retained review")
        if retained.state == "review":
            if not self._unchanged(await self._authority(scope), retained):
                raise ForbiddenError("lighting authority changed after review")
            # Durable claim precedes the writing transaction; never resend afterwards.
            retained = self.journal.claim_consent(confirmation, **scope, commitment=self.storage.commitment,
                                                  now=self.now())
            async with self.database.transaction() as connection:
                await self.storage.confirm(connection, authority=retained.authority, review=retained.review,
                                           confirmation=confirmation)
        return await self.consent_outcome(session, confirmation.operation_id)

    async def consent_outcome(self, session, operation_id):
        scope = self._scope(session)
        retained = self.journal.read_consent(operation_id, **scope)
        await self.admission()
        async with self.database.transaction() as connection:
            status = await self.storage.consent_outcome(connection, **scope, authority=retained.authority,
                                                        review=retained.review)
        if status == "committed":
            self.journal.mark_consent_committed(operation_id, **scope)
        return {"version": 1, "operation_id": str(operation_id), "status": status}

    async def status(self, session):
        authority = await self._authority(self._scope(session))
        return {"version": 1, "permitted": {site: authority.granted(site) for site in ("echo", "victoria")},
                "homes": sorted(self.clients)}

    # Actions ------------------------------------------------------------

    async def propose(self, session, request):
        scope = self._scope(session)
        if type(request) is not LightingProposalRequest:
            raise TypeError("typed lighting request required")
        try:
            retained = self.journal.read_action(request.operation_id, **scope)
        except MissingLightingRecord:
            retained = None
        if retained is not None:
            current = await self._same_owner(scope, retained)
            if retained.state != "review":
                return _summary(retained)
            if not self.now() < retained.review.expires_at or not self._unchanged(current, retained):
                raise ForbiddenError("lighting review expired or authority changed; start a new request")
            return {"version": 1, "status": "review", "review": retained.review.browser_view()}
        authority = await self._authority(scope)
        if any(site not in self.clients for site in request.sites):
            raise LightingUnavailable("home lighting is not configured")
        if not all(authority.granted(site) for site in request.sites):
            raise LightingNotPermitted("lighting is not permitted at every named home")
        inventories = {}
        for site in request.sites:
            try:
                inventories[site] = await self.clients[site].inventory()
            except Exception as exc:
                raise LightingUnavailable("home lighting inventory unavailable") from exc
        resolved = resolve(request, inventories)
        if type(resolved) is Clarification:
            return {"version": 1, "status": "clarify", "clarification": resolved.model_dump(mode="json")}
        review = self.commitment.prepare(request.operation_id, authority, resolved,
                                         [(site, inventories[site].revision) for site in request.sites
                                          if any(op.site_id == site for op in resolved)], now=self.now())
        retained = self.journal.retain_action(authority, review)
        return {"version": 1, "status": "review", "review": retained.review.browser_view()}

    async def confirm(self, session, confirmation):
        scope = self._scope(session)
        if type(confirmation) is not LightingActionConfirmation:
            raise TypeError("exact owner lighting confirmation required")
        retained = self.journal.read_action(confirmation.operation_id, **scope)
        if confirmation.reviewed_digest != retained.review.reviewed_digest:
            raise ValueError("confirmation differs from retained review")
        if retained.state != "review":
            return await self.outcome(session, confirmation.operation_id)
        if not self._unchanged(await self._authority(scope), retained):
            raise ForbiddenError("lighting authority changed after review")
        claimed = self.journal.claim_action(confirmation, **scope, commitment=self.commitment, now=self.now())
        review = claimed.review
        self._dispatching.add(review.operation_id)
        try:
            sites = dict.fromkeys(operation.site_id for operation in review.operations)
            await asyncio.gather(*(self._dispatch_site(scope, claimed, site) for site in sites))
            return _summary(self.journal.finish_action(review.operation_id, **scope))
        finally:
            self._dispatching.discard(review.operation_id)

    async def _dispatch_site(self, scope, claimed, site):
        review, client = claimed.review, self.clients[site]
        expires_ms = int(review.expires_at.timestamp() * 1000)
        for index, operation in enumerate(review.operations):
            if operation.site_id != site:
                continue
            # Stop, leaving the rest pending (later settled as not sent), if the review
            # is about to expire or the owner's authority changed since the last operation.
            if self.now() + OPERATION_BUDGET > review.expires_at:
                return
            try:
                current = await self._authority(scope)
            except Exception:
                return
            if not self._unchanged(current, claimed):
                return
            try:
                result = await client.execute(operation, request_id=review.operation_id, index=index,
                                              revision=review.revision(site), expires_at_ms=expires_ms)
            except LightingRefused:
                result = "not_sent"
            except OutcomeUnknown:
                result = "unknown"
            self.journal.record_result(review.operation_id, index, result, **scope)
            if result == "unknown":
                return  # an unresponsive home is not asked again in this dispatch

    async def outcome(self, session, operation_id):
        scope = self._scope(session)
        retained = self.journal.read_action(operation_id, **scope)
        await self._same_owner(scope, retained)
        if retained.state == "review" or operation_id in self._dispatching:
            # Never settle operations this process may still be sending.
            return _summary(retained)
        review = retained.review
        for index, (operation, result) in enumerate(zip(review.operations, retained.results)):
            if result not in UNSETTLED:
                continue
            try:
                found = await self.clients[operation.site_id].outcome(request_id=review.operation_id, index=index)
            except OutcomeUnknown:
                continue
            if found == "absent":
                # 'absent' is final at the home: never sent, and now never will be. It proves
                # nothing for an operation the home already acknowledged (its record may be pruned).
                if result not in ("pending", "unknown"):
                    continue
                found = "not_sent"
            retained = self.journal.record_result(review.operation_id, index, found, **scope)
        return _summary(retained)
