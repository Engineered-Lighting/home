"""Governed preference transactions for a separately authenticated ingress.

No route is enabled here. The private ingress binds the issuer and authenticates
the BFF; the BFF supplies its freshly checked subject and session commitment.
"""
from uuid import UUID

from sqlalchemy import select

from . import schema
from .errors import ForbiddenError, NotFoundError
from .personal_memory_contract import PreferenceAuthority, PreferenceProposalRequest, PreferenceConfirmation
from .personal_memory_storage import KIND, PersonalMemoryStorage, restore_preference_record
from .store import CoreStore


class PersonalMemoryService:
    def __init__(self, *, store, storage, admission):
        if not isinstance(store, CoreStore) or type(storage) is not PersonalMemoryStorage or not callable(admission):
            raise TypeError("governed store, preference storage and restore admission required")
        self.store, self.storage, self.admission = store, storage, admission

    async def _authority(self, connection, session, *, write, retained=None):
        if type(session) is not dict or set(session) != {"issuer_id","subject","session_commitment"}:
            raise ForbiddenError("authenticated preference session required")
        return await self.storage.resolve_authority(connection, **session, write=write, retained=retained)

    async def _staged(self, connection, authority, operation_id):
        row = (await connection.execute(select(schema.memory_transactions).where(
            schema.memory_transactions.c.transaction_id==operation_id,
            schema.memory_transactions.c.principal_id==authority.principal_id,
            schema.memory_transactions.c.kind==KIND,
        ).with_for_update())).mappings().first()
        if row is None:
            raise NotFoundError("preference operation unavailable")
        return row

    async def _delivery(self, session, *, write, retained=None):
        # A new transaction sees revocations committed after the operation.
        # Its failure suppresses delivery; it does not repeat the operation.
        await self.admission()
        async with self.store.database.transaction(serializable=True) as connection:
            return await self._authority(connection, session, write=write, retained=retained)

    async def read(self, session):
        await self.admission()
        async with self.store.database.transaction(serializable=True) as connection:
            authority = await self._authority(connection, session, write=False)
            await self.storage.read(connection, authority)
        await self.admission()
        async with self.store.database.transaction(serializable=True) as connection:
            authority = await self._authority(connection, session, write=False)
            # Read again at delivery so a completed correction/deletion cannot
            # leave a pre-deletion value in a retained response buffer.
            return await self.storage.read(connection, authority)

    async def propose(self, session, request):
        if type(request) is not PreferenceProposalRequest:
            raise TypeError("typed preference proposal required")
        await self.admission()
        async with self.store.database.transaction(serializable=True) as connection:
            authority = await self._authority(connection, session, write=True)
            existing = (await connection.execute(select(schema.memory_transactions.c.candidate).where(
                schema.memory_transactions.c.transaction_id==request.operation_id,
                schema.memory_transactions.c.principal_id==authority.principal_id,
                schema.memory_transactions.c.kind==KIND,
            ))).scalar_one_or_none()
            if existing is not None:
                retained = restore_preference_record(PreferenceAuthority,existing.get("authority"))
                authority = await self._authority(connection,session,write=True,retained=retained)
            result = await self.storage.propose(connection,authority,request)
        await self._delivery(session,write=True,retained=authority)
        return result

    async def confirm(self, session, confirmation, *, gesture_id):
        if type(confirmation) is not PreferenceConfirmation or type(gesture_id) is not UUID:
            raise TypeError("typed confirmation and one-time gesture required")
        await self.admission()
        async with self.store.database.transaction(serializable=True) as connection:
            authority = await self._authority(connection,session,write=True)
            row = await self._staged(connection,authority,confirmation.operation_id)
            retained = restore_preference_record(PreferenceAuthority,row["candidate"].get("authority"))
            authority = await self._authority(connection,session,write=True,retained=retained)
            request = restore_preference_record(PreferenceProposalRequest,row["candidate"].get("request"))
            artifact = await self.store._mint_authenticated_confirmation(connection,
                principal_id=authority.principal_id,purpose=f"{KIND}.{request.operation}.confirm",
                proposal_digest=confirmation.reviewed_digest,client_nonce=gesture_id)
            result = await self.storage.confirm(connection,authority,confirmation,
                confirmation_artifact_id=artifact)
        await self._delivery(session,write=True)
        return result

    async def outcome(self, session, confirmation):
        if type(confirmation) is not PreferenceConfirmation:
            raise TypeError("typed outcome lookup required")
        await self.admission()
        async with self.store.database.transaction(serializable=True) as connection:
            authority = await self._authority(connection,session,write=True)
            result = await self.storage.outcome(connection,authority,confirmation)
        await self._delivery(session,write=True)
        return result
