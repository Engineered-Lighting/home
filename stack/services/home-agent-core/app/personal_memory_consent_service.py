"""Retained owner review -> one grant attempt -> authoritative outcome lookup."""
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl
from uuid import UUID

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from .errors import ForbiddenError
from .personal_memory_consent import SharingConfirmation
from .personal_memory_consent_journal import ConsentJournal, MissingConsentError
from .personal_memory_grants import PreferenceGrantStorage, ROLE


class ConsentDatabase:
    def __init__(self, url):
        parsed = make_url(url)
        query = parse_qsl(url.partition("?")[2], keep_blank_values=True)
        if (parsed.drivername not in ("postgresql", "postgresql+psycopg") or parsed.username != ROLE
                or len(query) != len(parsed.query) or any(not value for _, value in query)
                or set(parsed.query) - {"sslmode", "sslrootcert"}
                or any(type(value) is not str or not value for value in parsed.query.values())
                or parsed.query and parsed.query.get("sslmode") != "verify-full"):
            raise ValueError("dedicated consent database credentials required")
        self.engine = create_async_engine(parsed.set(drivername="postgresql+psycopg"),
            isolation_level="SERIALIZABLE", pool_size=1, max_overflow=0, pool_timeout=2,
            pool_pre_ping=True, pool_recycle=300, hide_parameters=True,
            connect_args={"connect_timeout": 5, "options":
                "-c statement_timeout=7000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=10000"})

    def transaction(self):
        return self.engine.begin()

    async def close(self):
        await self.engine.dispose()


class PreferenceConsentService:
    def __init__(self, *, database, storage, journal, admission, grant_lifetime,
                 now=lambda: datetime.now(UTC)):
        if (type(database) is not ConsentDatabase or type(storage) is not PreferenceGrantStorage
                or type(journal) is not ConsentJournal or not callable(admission) or not callable(now)
                or type(grant_lifetime) is not timedelta or not timedelta(0) < grant_lifetime <= timedelta(days=365)):
            raise TypeError("provisioned consent runtime and displayed grant duration required")
        self.database, self.storage, self.journal = database, storage, journal
        self.admission, self.grant_lifetime, self.now = admission, grant_lifetime, now

    @staticmethod
    def _session(session):
        if (type(session) is not dict or set(session) != {"issuer_id", "subject", "session_commitment"}
                or session["issuer_id"] != "home-assistant:echo"):
            raise ForbiddenError("authenticated Echo consent session required")
        return {name: session[name] for name in ("subject", "session_commitment")}

    async def _review(self, scope, retained):
        if retained.state != "review":
            raise ValueError("consent already dispatched; use outcome lookup")
        await self.admission()
        async with self.database.transaction() as connection:
            current = await self.storage.resolve(connection, **scope)
            if current.model_dump(exclude={"valid_until"}) != retained.authority.model_dump(exclude={"valid_until"}):
                raise ForbiddenError("sharing authority changed after review")
        # Validate integrity and original expiry, without creating an approval or
        # extending the retained lease. This path never invokes the grant writer.
        self.storage.commitment.verify(SharingConfirmation(operation_id=retained.review.operation_id,
            reviewed_digest=retained.review.reviewed_digest), retained.review, retained.authority, now=self.now())
        return retained.review

    async def propose(self, session, operation_id):
        scope = self._session(session)
        if type(operation_id) is not UUID: raise TypeError("consent operation UUID required")
        await self.admission()
        try:
            retained = self.journal.read(operation_id, **scope)
        except MissingConsentError:
            async with self.database.transaction() as connection:
                authority = await self.storage.resolve(connection, **scope)
            now = self.now()
            review = self.storage.commitment.prepare(operation_id, authority, now=now,
                                                     grants_expire_at=now+self.grant_lifetime)
            self.journal.retain(authority, review)
            retained = self.journal.read(operation_id, **scope)
        return await self._review(scope, retained)

    async def confirm(self, session, confirmation):
        scope = self._session(session)
        if type(confirmation) is not SharingConfirmation: raise TypeError("exact owner confirmation required")
        confirmation = SharingConfirmation.model_validate(confirmation.model_dump())
        await self.admission()
        retained = self.journal.read(confirmation.operation_id, **scope)
        if confirmation.reviewed_digest != retained.review.reviewed_digest:
            raise ValueError("confirmation differs from retained review")
        if retained.state == "review":
            await self._review(scope, retained)
            # Durable claim precedes opening the writing transaction. All errors
            # after this point leave an indeterminate operation; never resend.
            retained = self.journal.claim(confirmation, **scope, commitment=self.storage.commitment, now=self.now())
            async with self.database.transaction() as connection:
                await self.storage.confirm(connection, authority=retained.authority,
                                           review=retained.review, confirmation=confirmation)
        return await self.outcome(session, confirmation.operation_id)

    async def outcome(self, session, operation_id):
        scope = self._session(session)
        retained = self.journal.read(operation_id, **scope)
        await self.admission()
        async with self.database.transaction() as connection:
            status = await self.storage.outcome(connection, **scope, authority=retained.authority, review=retained.review)
        if status == "committed":
            self.journal.mark_committed(operation_id, **scope)
        # Current authority must still permit this owner to see the result after
        # local persistence. Historical completion is not current memory access.
        await self.admission()
        async with self.database.transaction() as connection:
            status = await self.storage.outcome(connection, **scope, authority=retained.authority, review=retained.review)
        return {"version": 1, "operation_id": str(operation_id), "status": status}
