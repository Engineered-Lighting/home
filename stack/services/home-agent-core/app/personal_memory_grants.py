"""Transactional writer for the four explicitly reviewed preference grants.

Requires a separately provisioned consent role, never the normal API or an
administrator. Call inside one SERIALIZABLE transaction and report success only
after commit and fresh outcome lookup. This module provisions no permissions.
"""
import hashlib
import re
from datetime import timedelta
from uuid import uuid5

from sqlalchemy import text

from .crypto import canonical_json
from .errors import ForbiddenError
from .personal_memory_consent import SharingAuthority, SharingReview, SharingConfirmation, SharingReviewCommitment
from .personal_memory_contract import SOURCE

ROLE = "home_agent_preference_consent"
SCOPES = (("echo_read", "echo", "memory.read"),
          ("echo_write", "echo", "personal_memory.write"),
          ("victoria_read", "victoria", "memory.read"),
          ("victoria_write", "victoria", "personal_memory.write"))


class PreferenceGrantStorage:
    def __init__(self, commitment):
        if type(commitment) is not SharingReviewCommitment:
            raise TypeError("dedicated sharing review commitment required")
        self.commitment = commitment

    async def _anchor(self, connection, *, subject, session_commitment):
        if (type(subject) is not str or not 1 <= len(subject) <= 64 or subject != subject.strip()
                or any(ord(c) < 32 or ord(c) == 127 for c in subject)
                or type(session_commitment) is not str or not re.fullmatch(r"[a-f0-9]{64}", session_commitment)):
            raise ForbiddenError("authenticated sharing session required")
        role = (await connection.execute(text("""
            SELECT current_user,session_user,current_setting('transaction_isolation'),
              EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=current_user
                AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolinherit
                AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication
                AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
                  WHERE m.member=r.oid OR m.roleid=r.oid))
              AND NOT pg_is_in_recovery() AND current_setting('transaction_read_only')='off'
        """))).one()
        if tuple(role) != (ROLE, ROLE, "serializable", True):
            raise ForbiddenError("dedicated serializable consent transaction required")
        revision = (await connection.execute(text("SELECT version_num FROM public.alembic_version"))).scalars().all()
        if revision != ["0047_personal_pref_authority_v1"]:
            raise ForbiddenError("sharing schema unavailable")
        await connection.execute(text("SELECT privacy.lock_identity_semantic_write_fence()"))
        anchor = (await connection.execute(text("""
            SELECT l.link_id,l.principal_id,l.person_id,l.revision AS link_revision,l.authorization_generation
            FROM identity.shared_subject_bindings b
            JOIN identity.shared_owner_links l ON l.link_id=b.link_id
            JOIN identity.principals p ON p.principal_id=l.principal_id AND p.person_id=l.person_id
            JOIN identity.people person ON person.person_id=l.person_id
            WHERE b.issuer_id='home-assistant:echo' AND b.subject=:subject AND b.revoked_at IS NULL
              AND l.revoked_at IS NULL AND p.kind='ha_user' AND p.status='active' AND person.status='active'
            FOR UPDATE OF l FOR SHARE OF b,p,person
        """), {"subject": subject})).mappings().one_or_none()
        if anchor is None:
            raise ForbiddenError("linked preference owner unavailable")
        bindings = (await connection.execute(text("""
            SELECT b.issuer_id FROM identity.shared_subject_bindings b
            JOIN identity.shared_issuers i ON i.issuer_id=b.issuer_id
            WHERE b.link_id=:link AND b.revoked_at IS NULL AND i.state='active'
            ORDER BY b.issuer_id FOR SHARE OF b,i
        """), {"link": anchor["link_id"]})).scalars().all()
        blocked = (await connection.execute(text("""
            SELECT privacy.identity_person_is_blocked(:person) OR EXISTS (
              SELECT 1 FROM privacy.shared_link_session_revocations
              WHERE issuer_id='home-assistant:echo' AND session_commitment=:session)
        """), {"person": anchor["person_id"], "session": session_commitment})).scalar_one()
        if bindings != ["home-assistant:echo", "home-assistant:victoria"] or blocked:
            raise ForbiddenError("current linked accounts required")
        return dict(anchor)

    async def resolve(self, connection, *, subject, session_commitment):
        anchor = await self._anchor(connection, subject=subject, session_commitment=session_commitment)
        now = (await connection.execute(text("SELECT clock_timestamp()"))).scalar_one()
        values = {}
        for name, site, capability in SCOPES:
            params = {"link": anchor["link_id"], "site": site, "source": SOURCE, "capability": capability}
            registration = (await connection.execute(text("""
                SELECT registration_revision FROM identity.shared_sources
                WHERE site_id=:site AND source_id=:source AND capability=:capability
                  AND issuer_id=('home-assistant:'||:site) AND state='active' FOR SHARE
            """), params)).scalar_one_or_none()
            if registration is None:
                raise ForbiddenError("preference source is not registered")
            grant = (await connection.execute(text("""
                SELECT grant_id,revision FROM identity.shared_source_grants
                WHERE link_id=:link AND site_id=:site AND source_id=:source AND capability=:capability
                  AND revoked_at IS NULL AND expires_at>:now
                  AND authorization_generation=:generation AND source_revision=:registration
                FOR UPDATE
            """), {**params, "now": now, "generation": anchor["authorization_generation"],
                    "registration": registration})).mappings().one_or_none()
            values[name] = {"source_revision": registration, "grant_revision": grant["revision"] if grant else 0,
                            "grant_id": grant["grant_id"] if grant else None}
        return SharingAuthority(**anchor, **values, issuer_id="home-assistant:echo", subject=subject,
                                session_commitment=session_commitment, valid_until=now+timedelta(seconds=60))

    @staticmethod
    def _identity(review, site, capability):
        grant_id = uuid5(review.operation_id, f"{SOURCE}:{site}:{capability}")
        approval = hashlib.sha256(canonical_json({"review": review.reviewed_digest, "site": site,
                                                 "source": SOURCE, "capability": capability})).hexdigest()
        return grant_id, approval

    async def confirm(self, connection, *, authority, review, confirmation):
        # Review and authority are retained server-side; only confirmation is a
        # browser contract. Fresh lookup occurs inside the same write transaction.
        if type(authority) is not SharingAuthority or type(review) is not SharingReview or type(confirmation) is not SharingConfirmation:
            raise TypeError("retained sharing review and exact confirmation required")
        authority = SharingAuthority.model_validate(authority.model_dump())
        current = await self.resolve(connection, subject=authority.subject,
                                     session_commitment=authority.session_commitment)
        if current.model_dump(exclude={"valid_until"}) != authority.model_dump(exclude={"valid_until"}):
            raise ForbiddenError("sharing authority changed after review")
        now = (await connection.execute(text("SELECT clock_timestamp()"))).scalar_one()
        self.commitment.verify(confirmation, review, authority, now=now)
        for name, site, capability in SCOPES:
            grant_id, approval = self._identity(review, site, capability)
            params = {"link": authority.link_id, "site": site, "source": SOURCE, "capability": capability}
            # The locked owner link serializes writers. Preserve prior rows and
            # monotonically advance revisions, including expired/revoked history.
            revision = (await connection.execute(text("""
                SELECT COALESCE(max(revision),0)+1 FROM identity.shared_source_grants
                WHERE link_id=:link AND site_id=:site AND source_id=:source AND capability=:capability
            """), params)).scalar_one()
            await connection.execute(text("""
                UPDATE identity.shared_source_grants SET revoked_at=:now
                WHERE link_id=:link AND site_id=:site AND source_id=:source AND capability=:capability
                  AND revoked_at IS NULL
            """), {**params, "now": now})
            await connection.execute(text("""
                INSERT INTO identity.shared_source_grants
                  (grant_id,link_id,site_id,source_id,capability,source_revision,authorization_generation,
                   revision,approval_commitment,created_at,expires_at)
                VALUES (:id,:link,:site,:source,:capability,:source_revision,:generation,:revision,:approval,:now,:expiry)
            """), {**params, "id": grant_id, "source_revision": getattr(authority, name).source_revision,
                    "generation": authority.authorization_generation, "revision": revision,
                    "approval": approval, "now": now, "expiry": review.grants_expire_at})
        finished = (await connection.execute(text("SELECT clock_timestamp()"))).scalar_one()
        if finished >= min(authority.valid_until, review.expires_at, review.grants_expire_at):
            raise ForbiddenError("sharing confirmation expired during transaction")

    async def outcome(self, connection, *, subject, session_commitment, authority, review):
        if type(authority) is not SharingAuthority or type(review) is not SharingReview:
            raise TypeError("retained sharing operation required")
        authority = SharingAuthority.model_validate(authority.model_dump())
        review = SharingReview.model_validate(review.model_dump())
        anchor = await self._anchor(connection, subject=subject, session_commitment=session_commitment)
        if any(anchor[name] != getattr(authority, name) for name in anchor):
            raise ForbiddenError("sharing outcome owner changed")
        rows = []
        for name, site, capability in SCOPES:
            grant_id, approval = self._identity(review, site, capability)
            row = (await connection.execute(text("""
                SELECT grant_id FROM identity.shared_source_grants
                WHERE grant_id=:id AND link_id=:link AND site_id=:site AND source_id=:source
                  AND capability=:capability AND source_revision=:source_revision
                  AND authorization_generation=:generation AND approval_commitment=:approval AND expires_at=:expiry
            """), {"id": grant_id, "link": authority.link_id, "site": site, "source": SOURCE,
                    "capability": capability, "source_revision": getattr(authority, name).source_revision,
                    "generation": authority.authorization_generation, "approval": approval,
                    "expiry": review.grants_expire_at})).scalar_one_or_none()
            rows.append(row)
        # Historical completion is not evidence that grants remain active. The
        # ordinary preference authority resolver decides present read/write access.
        return "committed" if all(rows) else "unknown"
