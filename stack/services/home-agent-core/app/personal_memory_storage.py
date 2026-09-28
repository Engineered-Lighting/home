"""Transaction-scoped preference persistence in Core's existing memory tables.

No HTTP route or credential is introduced here. Callers must authenticate the
session, apply the Core maintenance/restore gate, and open a serializable
transaction. Database permissions remain a separate deployment prerequisite.
"""
import hmac
import re
from datetime import datetime
from uuid import UUID

from psycopg.types.range import Range
from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import DBAPIError

from . import schema
from .erasure import apply_personal_preference_erasure, invalidate_descriptor_dependents
from .errors import ConflictError, ForbiddenError, NotFoundError
from .ids import uuid7
from .personal_memory_contract import (
    SOURCE, EveningLightingPreference, PreferenceAuthority, PreferenceConfirmation,
    PreferenceProposalRequest, PreferenceReview, PreferenceReviewCommitment,
)

PREDICATE = "personal_preference.evening_lighting"
KIND = "personal_preference.v1"


def restore_preference_record(model, value):
    """Decode our stored JSON fields without relaxing the public contract."""
    fields = {
        PreferenceProposalRequest: (("operation_id","expected_fact_id"), ()),
        PreferenceAuthority: (("principal_id","person_id","link_id"), ("valid_until",)),
        PreferenceReview: (("operation_id","expected_fact_id"), ("expires_at",)),
        PreferenceConfirmation: (("operation_id",), ()),
    }
    if model not in fields or type(value) is not dict:
        raise ValueError("invalid retained preference record")
    decoded = dict(value)
    ids, times = fields[model]
    for key in ids:
        raw = decoded.get(key)
        if key=="expected_fact_id" and raw is None: continue
        if type(raw) is not str or len(raw)!=36:
            raise ValueError("invalid retained preference identifier")
        parsed = UUID(raw)
        if str(parsed)!=raw: raise ValueError("noncanonical retained preference identifier")
        decoded[key] = parsed
    for key in times:
        raw = decoded.get(key)
        if type(raw) is not str or len(raw)>64:
            raise ValueError("invalid retained preference timestamp")
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("retained preference timestamp must be aware")
        decoded[key] = parsed
    return model.model_validate(decoded)


class PersonalMemoryStorage:
    def __init__(self, signer: PreferenceReviewCommitment, *, policy_digest: str, policy_version: str):
        if type(signer) is not PreferenceReviewCommitment or not re.fullmatch(r"[a-f0-9]{64}", policy_digest):
            raise ValueError("configured preference review and policy required")
        if not isinstance(policy_version, str) or not 1 <= len(policy_version) <= 128:
            raise ValueError("configured policy version required")
        self.signer, self.policy_digest, self.policy_version = signer, policy_digest, policy_version

    async def resolve_authority(self, connection, *, issuer_id, subject,
                                session_commitment, write, retained=None):
        """Resolve an authenticated BFF session, not browser-supplied owner IDs.

        The ingress credential fixes issuer_id. Its BFF must freshly validate
        subject and derive the session commitment. Retained authority may come
        only from the staged database record; it never renews the review lease.
        """
        if (issuer_id not in ("home-assistant:echo", "home-assistant:victoria") or
            type(subject) is not str or not 1 <= len(subject) <= 64 or subject != subject.strip() or
            any(ord(c) < 32 or ord(c) == 127 for c in subject) or
            type(session_commitment) is not str or not re.fullmatch(r"[a-f0-9]{64}", session_commitment) or
            type(write) is not bool or retained is not None and type(retained) is not PreferenceAuthority):
            raise ForbiddenError("authenticated preference session required")
        if (await connection.execute(text("SHOW transaction_isolation"))).scalar_one() != "serializable":
            raise ValueError("serializable preference transaction required")
        authority = await self._lookup_authority(connection, issuer_id=issuer_id,
            subject=subject, session_commitment=session_commitment, write=write)
        now = (await connection.execute(text("SELECT clock_timestamp()"))).scalar_one()
        if retained is not None:
            if (retained.model_dump(exclude={"valid_until"}) != authority.model_dump(exclude={"valid_until"}) or
                not now < retained.valid_until <= authority.valid_until):
                raise ForbiddenError("preference authority changed after review")
            authority = retained
        await self._admit(connection, authority, write=write)
        return authority

    async def _lookup_authority(self, connection, *, issuer_id, subject, session_commitment, write):
        # The fixed database function resolves identity and locks grants. The
        # application role receives no direct shared-identity table permissions.
        try:
            row = (await connection.execute(text("""
                SELECT * FROM identity.resolve_personal_preference_authority_v1(
                    :issuer,:subject,:session,:write)
            """), {"issuer":issuer_id,"subject":subject,"session":session_commitment,
                    "write":write})).mappings().one()
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) in ("42501", "22023"):
                raise ForbiddenError("current preference authority required") from error
            # Serialization failures and infrastructure faults must propagate;
            # neither is evidence that a write can safely be retried.
            raise
        return PreferenceAuthority(**dict(row),issuer_id=issuer_id,
            site_id=issuer_id.split(":",1)[1],subject=subject,
            session_commitment=session_commitment,access="write" if write else "read")

    async def _admit(self, connection, authority, *, write):
        if type(authority) is not PreferenceAuthority:
            raise TypeError("authenticated preference authority required")
        authority = PreferenceAuthority.model_validate(authority.model_dump())
        if write and authority.access != "write":
            raise ForbiddenError("write authority required")
        if (await connection.execute(text("SHOW transaction_isolation"))).scalar_one() != "serializable":
            raise ValueError("serializable preference transaction required")
        now = (await connection.execute(text("SELECT clock_timestamp()"))).scalar_one()
        if now >= authority.valid_until:
            raise ForbiddenError("preference authority expired")
        current = await self._lookup_authority(connection,issuer_id=authority.issuer_id,
            subject=authority.subject,session_commitment=authority.session_commitment,
            write=authority.access == "write")
        if (current.model_dump(exclude={"valid_until"}) != authority.model_dump(exclude={"valid_until"}) or
            authority.valid_until > current.valid_until):
            raise ForbiddenError("preference authority changed")
        # Serialize first creation as well as corrections, including absence of
        # an existing row. This lock does not establish authority.
        await connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
            {"scope": f"{PREDICATE}:{authority.person_id}:{authority.principal_id}"})
        now = (await connection.execute(text("SELECT clock_timestamp()"))).scalar_one()
        if now >= authority.valid_until:
            raise ForbiddenError("preference authority expired while waiting")
        return now

    async def _current(self, connection, authority):
        row = (await connection.execute(select(schema.fact_versions).where(
            schema.fact_versions.c.subject_id == authority.person_id,
            schema.fact_versions.c.perspective_principal_id == authority.principal_id,
            schema.fact_versions.c.predicate == PREDICATE,
        ).order_by(schema.fact_versions.c.version.desc()).limit(1).with_for_update())).mappings().first()
        if row is None:
            return None, None, 0
        blocked = (await connection.execute(select(schema.retrieval_blocks.c.block_id).where(
            schema.retrieval_blocks.c.artifact_id == row["fact_id"]))).scalar_one_or_none()
        value = None
        if blocked is None and row["resolution"] == "accepted" and row["system_range"].upper is None:
            value = EveningLightingPreference.model_validate(row["object"])
        return row, value, row["version"]

    async def read(self, connection, authority):
        await self._admit(connection, authority, write=False)
        row, value, revision = await self._current(connection, authority)
        source_site = None
        if value is not None:
            preview = (await connection.execute(select(schema.memory_transactions.c.preview).where(
                schema.memory_transactions.c.transaction_id==row["memory_transaction_id"],
                schema.memory_transactions.c.principal_id==authority.principal_id,
                schema.memory_transactions.c.kind==KIND,
            ))).scalar_one()
            source_site = preview.get("source_site")
            if source_site not in ("echo","victoria"):
                raise ConflictError("preference provenance unavailable")
        # The caller must revalidate before delivering a response after this
        # transaction ends; the returned snapshot grants no subsequent access.
        return {"revision": revision, "fact_id":row["fact_id"] if row else None, "preference": value,
                "confirmed_at": row["committed_at"] if row else None,
                "source":SOURCE,"source_site":source_site}

    async def propose(self, connection, authority, request):
        if type(request) is not PreferenceProposalRequest:
            raise TypeError("typed preference request required")
        now = await self._admit(connection, authority, write=True)
        existing = (await connection.execute(select(schema.memory_transactions).where(
            schema.memory_transactions.c.transaction_id == request.operation_id,
        ).with_for_update())).mappings().first()
        if existing is not None:
            candidate = existing["candidate"]
            if (existing["principal_id"] != authority.principal_id or existing["kind"] != KIND
                or existing["state"] != "needs_confirmation"
                or candidate.get("request") != request.model_dump(mode="json")
                or candidate.get("authority") != authority.model_dump(mode="json")):
                raise ConflictError("preference operation already exists")
            review = restore_preference_record(PreferenceReview,existing["preview"])
            if now >= review.expires_at:
                raise ConflictError("preference review expired")
            return review
        row, current, revision = await self._current(connection, authority)
        if (revision != request.expected_revision or revision >= 2147483647
            or request.expected_fact_id != (row["fact_id"] if row else None)):
            raise ConflictError("preference revision changed")
        review = self.signer.prepare(request, authority, current, now=now)
        fact_id = row["fact_id"] if current is not None else uuid7()
        await connection.execute(insert(schema.memory_transactions).values(
            transaction_id=request.operation_id,principal_id=authority.principal_id,kind=KIND,
            state="needs_confirmation",candidate={"preference_fact_id":str(fact_id),
                "request":request.model_dump(mode="json"),"authority":authority.model_dump(mode="json")},
            preview=review.model_dump(mode="json"),policy_digest=self.policy_digest,policy_version=self.policy_version,
        ))
        await connection.execute(insert(schema.artifact_registry).values(
            artifact_id=request.operation_id,artifact_kind="memory_transaction",store="postgresql",
            owner_principal_id=authority.principal_id,retention_class="until_erased"))
        return review

    async def outcome(self, connection, authority, confirmation):
        """Lookup after uncertain delivery; never creates or confirms work."""
        if type(confirmation) is not PreferenceConfirmation:
            raise TypeError("typed preference confirmation required")
        await self._admit(connection, authority, write=True)
        tx = (await connection.execute(select(schema.memory_transactions).where(
            schema.memory_transactions.c.transaction_id==confirmation.operation_id,
            schema.memory_transactions.c.principal_id==authority.principal_id,
            schema.memory_transactions.c.kind==KIND,
        ))).mappings().first()
        if tx is None or tx["state"]!="committed":
            return None
        if not hmac.compare_digest(tx["confirmation_digest"] or "", confirmation.reviewed_digest):
            raise ConflictError("preference confirmation does not match")
        fact = (await connection.execute(select(schema.fact_versions).where(
            schema.fact_versions.c.memory_transaction_id==confirmation.operation_id,
            schema.fact_versions.c.predicate==PREDICATE,
            schema.fact_versions.c.perspective_principal_id==authority.principal_id,
        ))).mappings().one()
        erasure = (await connection.execute(select(schema.erasure_requests.c.state).where(
            schema.erasure_requests.c.principal_id==authority.principal_id,
            schema.erasure_requests.c.scope.contains({"preference_fact_id":str(fact["fact_id"])}),
        ))).scalar_one_or_none()
        return {"operation_id":confirmation.operation_id,"revision":fact["version"],
                "status":("forgotten" if erasure=="complete" else "ledger_pending") if erasure else "committed",
                "historical":True}

    async def confirm(self, connection, authority, confirmation, *, confirmation_artifact_id: UUID):
        """Commit after Core mints an authenticated confirmation artifact.

        The artifact must be minted/consumed by the existing governed gesture
        path in this same transaction, never accepted as browser authority.
        """
        if type(confirmation) is not PreferenceConfirmation or type(confirmation_artifact_id) is not UUID:
            raise TypeError("governed confirmation required")
        now = await self._admit(connection, authority, write=True)
        tx = (await connection.execute(select(schema.memory_transactions).where(
            schema.memory_transactions.c.transaction_id == confirmation.operation_id,
            schema.memory_transactions.c.principal_id == authority.principal_id,
            schema.memory_transactions.c.kind == KIND,
        ).with_for_update())).mappings().first()
        if tx is None: raise NotFoundError("preference operation unavailable")
        if tx["state"] != "needs_confirmation" or tx["policy_digest"] != self.policy_digest:
            raise ConflictError("preference operation is no longer confirmable")
        request = restore_preference_record(PreferenceProposalRequest,tx["candidate"]["request"])
        retained = restore_preference_record(PreferenceAuthority,tx["candidate"]["authority"])
        review = restore_preference_record(PreferenceReview,tx["preview"])
        if retained != authority:
            raise ForbiddenError("preference authority changed after review")
        old, current, revision = await self._current(connection, authority)
        if revision != request.expected_revision or request.expected_fact_id != (old["fact_id"] if old else None):
            raise ConflictError("preference revision changed after review")
        self.signer.verify(request,authority,current,review,confirmation,now=now)
        # Core mints this immutable receipt in the same serializable transaction.
        # The API deliberately cannot update confirmation history; no additional
        # row lock or UPDATE grant is needed to read its own inserted receipt.
        gesture = (await connection.execute(select(schema.confirmation_artifacts.c.artifact_id).where(
            schema.confirmation_artifacts.c.artifact_id==confirmation_artifact_id,
            schema.confirmation_artifacts.c.principal_id==authority.principal_id,
            schema.confirmation_artifacts.c.purpose==f"{KIND}.{request.operation}.confirm",
            schema.confirmation_artifacts.c.proposal_digest==review.reviewed_digest,
            schema.confirmation_artifacts.c.consumed_at.is_not(None),
            schema.confirmation_artifacts.c.expires_at>now,
        ))).scalar_one_or_none()
        if gesture is None:
            raise ForbiddenError("governed preference confirmation unavailable")
        fact_id = UUID(tx["candidate"]["preference_fact_id"])
        if current is None:
            await connection.execute(insert(schema.artifact_registry).values(
                artifact_id=fact_id,artifact_kind="fact",store="postgresql",
                owner_principal_id=authority.principal_id,retention_class="until_erased"))
        if old is not None and not old["system_range"].isempty and old["system_range"].upper is None:
            await connection.execute(update(schema.fact_versions).where(
                schema.fact_versions.c.fact_version_id==old["fact_version_id"]
            ).values(system_range=Range(old["system_range"].lower,now,bounds="[)")))
        version_id = uuid7()
        forgetting = request.operation == "forget"
        await connection.execute(insert(schema.fact_versions).values(
            fact_version_id=version_id,fact_id=fact_id,version=revision+1,
            subject_type="person",subject_id=authority.person_id,predicate=PREDICATE,
            object={"erased":True} if forgetting else request.preference.model_dump(mode="json"),
            perspective_principal_id=authority.principal_id,valid_range=Range(now,None,bounds="[)"),
            system_range=Range(now,None,bounds="[)"),authority="explicit_subject",support="explicit_authority",
            contradiction="none",freshness="not_applicable",coverage="not_applicable",
            resolution="suppressed" if forgetting else "accepted",privacy_scope="private",
            memory_transaction_id=request.operation_id,
        ))
        await connection.execute(insert(schema.fact_support).values(
            support_id=uuid7(),fact_version_id=version_id,artifact_id=confirmation_artifact_id,support_role="confirmation"))
        edges = [(confirmation_artifact_id,request.operation_id,"supports"),
                 (request.operation_id,fact_id,"derived_from")]
        if current is not None:
            edges.append((old["memory_transaction_id"],request.operation_id,"derived_from"))
        for parent,child,relation in edges:
            await connection.execute(insert(schema.artifact_links).values(
                link_id=uuid7(),parent_artifact_id=parent,child_artifact_id=child,relation=relation))
        await connection.execute(update(schema.memory_transactions).where(
            schema.memory_transactions.c.transaction_id==request.operation_id
        ).values(state="committed",confirmation_digest=review.reviewed_digest,confirmed_at=now,updated_at=now))
        if forgetting:
            erasure_id = uuid7()
            await connection.execute(insert(schema.erasure_requests).values(
                erasure_request_id=erasure_id,principal_id=authority.principal_id,
                scope={"preference_fact_id":str(fact_id)},state="ledger_pending",
                policy_digest=self.policy_digest,completed_at=now))
            await apply_personal_preference_erasure(connection,principal_id=authority.principal_id,
                fact_id=fact_id,erasure_request_id=erasure_id,now=now,require_existing=True)
            await connection.execute(insert(schema.outbox).values(
                outbox_id=uuid7(),topic="privacy.erasure.completed",aggregate_id=fact_id,payload={
                    "version":1,"subject_kind":"personal_preference_fact","erasure_request_id":str(erasure_id),
                    "principal_id":str(authority.principal_id),"fact_id":str(fact_id),
                    "operation_codes":["scrub_personal_preference","invalidate_derived_views"],
                    "policy_digest":self.policy_digest,"completed_at":now.isoformat(),
                    "external_pending_codes":[],"legacy_untracked_codes":[],"checkpoint_affected":True,
                    "backup_expiry_at":None,"exact_residual_codes":["backup_expiry_unverified"],
                }))
        elif old is not None:
            await invalidate_descriptor_dependents(connection,fact_id=old["fact_id"],
                source_fact_version_id=old["fact_version_id"],reason="personal_preference_corrected")
        return {"operation_id":request.operation_id,"revision":revision+1,
                "status":"ledger_pending" if forgetting else "committed"}
