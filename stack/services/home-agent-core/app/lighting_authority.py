"""Owner authority for explicit cross-home lighting.

A dedicated ``home_agent_lighting`` database role reads the owner's linked
accounts and writes only ``core.lighting.v1`` / ``lighting.execute`` grants,
one per home, after the owner explicitly consents. Every action proposal and
execution re-resolves this authority; a changed link, generation, revoked
session or missing grant stops it. This mirrors the preference grant storage
without sharing its role, source or capabilities.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid5

from pydantic import Field, field_validator, model_validator
from sqlalchemy import text

from .crypto import canonical_json
from .errors import ForbiddenError
from .lighting_contract import CAPABILITY, SOURCE, Contract

ROLE = "home_agent_lighting"
REVISION = "0047_personal_pref_authority_v1"
SITES = ("echo", "victoria")


def _aware(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware timestamp required")
    return value


class SourceRevision(Contract):
    source_revision: int = Field(gt=0, lt=9223372036854775807)
    grant_revision: int = Field(ge=0, lt=9223372036854775807)
    grant_id: UUID | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def exact_grant(self):
        if (self.grant_revision == 0) != (self.grant_id is None):
            raise ValueError("existing grant revisions require their exact identity")
        return self


class LightingAuthority(Contract):
    """Private current lookup; never deserialize this from a browser request."""
    principal_id: UUID = Field(repr=False)
    person_id: UUID = Field(repr=False)
    link_id: UUID = Field(repr=False)
    link_revision: int = Field(gt=0, lt=9223372036854775807)
    authorization_generation: int = Field(gt=0, lt=9223372036854775807)
    issuer_id: Literal["home-assistant:echo"]
    subject: str = Field(min_length=1, max_length=64, repr=False)
    session_commitment: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    echo: SourceRevision
    victoria: SourceRevision
    valid_until: datetime

    @field_validator("valid_until", mode="before")
    @classmethod
    def wire_timestamp(cls, value, info):
        if info.mode == "json" and type(value) is str:
            return datetime.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def canonical(self):
        if self.subject.strip() != self.subject or any(ord(c) < 32 or ord(c) == 127 for c in self.subject):
            raise ValueError("invalid lighting authority")
        _aware(self.valid_until)
        return self

    def granted(self, site: str) -> bool:
        return getattr(self, site).grant_revision > 0


class LightingConsentOperation(Contract):
    version: Literal[1] = 1
    operation_id: UUID


class LightingConsentConfirmation(LightingConsentOperation):
    reviewed_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)


class LightingConsentReview(Contract):
    version: Literal[1] = 1
    operation_id: UUID
    source: Literal["core.lighting.v1"] = SOURCE
    applies_to: Literal["both_homes"] = "both_homes"
    effect: Literal["switch_allowlisted_lights_after_each_confirmation"] = \
        "switch_allowlisted_lights_after_each_confirmation"
    grants_expire_at: datetime
    expires_at: datetime
    reviewed_digest: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)

    @field_validator("grants_expire_at", "expires_at", mode="before")
    @classmethod
    def wire_timestamp(cls, value, info):
        if info.mode == "json" and type(value) is str:
            return datetime.fromisoformat(value)
        return value

    @field_validator("grants_expire_at", "expires_at")
    @classmethod
    def aware_timestamp(cls, value):
        return _aware(value)


class LightingConsentCommitment:
    def __init__(self, key):
        if type(key) is not bytes or len(key) != 32:
            raise ValueError("dedicated lighting consent key required")
        self._key = hmac.new(key, b"home-agent:lighting:v1:consent", hashlib.sha256).digest()

    def prepare(self, operation_id, authority, *, now, grants_expire_at, expires_at=None):
        if type(operation_id) is not UUID or type(authority) is not LightingAuthority:
            raise TypeError("governed lighting authority required")
        authority = LightingAuthority.model_validate(authority.model_dump())
        for value in (now, grants_expire_at):
            if not isinstance(value, datetime):
                raise ValueError("aware lighting timestamps required")
            _aware(value)
        limit = min(now + timedelta(seconds=60), authority.valid_until, grants_expire_at)
        expiry = limit if expires_at is None else expires_at
        if not isinstance(expiry, datetime) or _aware(expiry) and not now < expiry <= limit:
            raise ValueError("lighting consent review expired or extended")
        payload = {"operation_id": operation_id, "authority": authority.model_dump(), "source": SOURCE,
                   "capabilities": [CAPABILITY], "grants_expire_at": grants_expire_at, "expires_at": expiry}
        digest = hmac.new(self._key, canonical_json(payload), hashlib.sha256).hexdigest()
        return LightingConsentReview(operation_id=operation_id, grants_expire_at=grants_expire_at,
                                     expires_at=expiry, reviewed_digest=digest)

    def verify(self, confirmation, review, authority, *, now):
        if type(confirmation) is not LightingConsentConfirmation or type(review) is not LightingConsentReview:
            raise TypeError("exact retained lighting consent review required")
        expected = self.prepare(review.operation_id, authority, now=now,
                                grants_expire_at=review.grants_expire_at, expires_at=review.expires_at)
        if (confirmation.operation_id != review.operation_id
                or not hmac.compare_digest(confirmation.reviewed_digest, review.reviewed_digest)
                or not hmac.compare_digest(expected.reviewed_digest, review.reviewed_digest)):
            raise ValueError("lighting authority or reviewed scope changed")


class LightingGrantStorage:
    def __init__(self, commitment):
        if type(commitment) is not LightingConsentCommitment:
            raise TypeError("dedicated lighting consent commitment required")
        self.commitment = commitment

    async def _anchor(self, connection, *, subject, session_commitment):
        if (type(subject) is not str or not 1 <= len(subject) <= 64 or subject != subject.strip()
                or any(ord(c) < 32 or ord(c) == 127 for c in subject)
                or type(session_commitment) is not str or not re.fullmatch(r"[a-f0-9]{64}", session_commitment)):
            raise ForbiddenError("authenticated lighting session required")
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
            raise ForbiddenError("dedicated serializable lighting transaction required")
        revision = (await connection.execute(text("SELECT version_num FROM public.alembic_version"))).scalars().all()
        if revision != [REVISION]:
            raise ForbiddenError("lighting schema unavailable")
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
            raise ForbiddenError("linked lighting owner unavailable")
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
        for site in SITES:
            params = {"link": anchor["link_id"], "site": site, "source": SOURCE, "capability": CAPABILITY}
            registration = (await connection.execute(text("""
                SELECT registration_revision FROM identity.shared_sources
                WHERE site_id=:site AND source_id=:source AND capability=:capability
                  AND issuer_id=('home-assistant:'||:site) AND state='active' FOR SHARE
            """), params)).scalar_one_or_none()
            if registration is None:
                raise ForbiddenError("lighting source is not registered")
            grant = (await connection.execute(text("""
                SELECT grant_id,revision FROM identity.shared_source_grants
                WHERE link_id=:link AND site_id=:site AND source_id=:source AND capability=:capability
                  AND revoked_at IS NULL AND expires_at>:now
                  AND authorization_generation=:generation AND source_revision=:registration
                FOR UPDATE
            """), {**params, "now": now, "generation": anchor["authorization_generation"],
                    "registration": registration})).mappings().one_or_none()
            values[site] = {"source_revision": registration, "grant_revision": grant["revision"] if grant else 0,
                            "grant_id": grant["grant_id"] if grant else None}
        return LightingAuthority(**anchor, **values, issuer_id="home-assistant:echo", subject=subject,
                                 session_commitment=session_commitment, valid_until=now + timedelta(seconds=60))

    @staticmethod
    def _identity(review, site):
        grant_id = uuid5(review.operation_id, f"{SOURCE}:{site}:{CAPABILITY}")
        approval = hashlib.sha256(canonical_json({"review": review.reviewed_digest, "site": site,
                                                 "source": SOURCE, "capability": CAPABILITY})).hexdigest()
        return grant_id, approval

    async def confirm(self, connection, *, authority, review, confirmation):
        if (type(authority) is not LightingAuthority or type(review) is not LightingConsentReview
                or type(confirmation) is not LightingConsentConfirmation):
            raise TypeError("retained lighting consent review and exact confirmation required")
        current = await self.resolve(connection, subject=authority.subject,
                                     session_commitment=authority.session_commitment)
        if current.model_dump(exclude={"valid_until"}) != authority.model_dump(exclude={"valid_until"}):
            raise ForbiddenError("lighting authority changed after review")
        now = (await connection.execute(text("SELECT clock_timestamp()"))).scalar_one()
        self.commitment.verify(confirmation, review, authority, now=now)
        for site in SITES:
            grant_id, approval = self._identity(review, site)
            params = {"link": authority.link_id, "site": site, "source": SOURCE, "capability": CAPABILITY}
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
            """), {**params, "id": grant_id, "source_revision": getattr(authority, site).source_revision,
                    "generation": authority.authorization_generation, "revision": revision,
                    "approval": approval, "now": now, "expiry": review.grants_expire_at})
        finished = (await connection.execute(text("SELECT clock_timestamp()"))).scalar_one()
        if finished >= min(authority.valid_until, review.expires_at, review.grants_expire_at):
            raise ForbiddenError("lighting consent expired during transaction")

    async def consent_outcome(self, connection, *, subject, session_commitment, authority, review):
        if type(authority) is not LightingAuthority or type(review) is not LightingConsentReview:
            raise TypeError("retained lighting consent operation required")
        anchor = await self._anchor(connection, subject=subject, session_commitment=session_commitment)
        if any(anchor[name] != getattr(authority, name) for name in anchor):
            raise ForbiddenError("lighting consent owner changed")
        rows = []
        for site in SITES:
            grant_id, approval = self._identity(review, site)
            rows.append((await connection.execute(text("""
                SELECT grant_id FROM identity.shared_source_grants
                WHERE grant_id=:id AND link_id=:link AND site_id=:site AND source_id=:source
                  AND capability=:capability AND source_revision=:source_revision
                  AND authorization_generation=:generation AND approval_commitment=:approval AND expires_at=:expiry
            """), {"id": grant_id, "link": authority.link_id, "site": site, "source": SOURCE,
                    "capability": CAPABILITY, "source_revision": getattr(authority, site).source_revision,
                    "generation": authority.authorization_generation, "approval": approval,
                    "expiry": review.grants_expire_at})).scalar_one_or_none())
        return "committed" if all(rows) else "unknown"
