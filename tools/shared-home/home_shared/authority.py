"""Version-one *staging* identity and action contracts; no production transport.

Only a trusted verifier may exchange opaque credentials for identity, owner-link,
or grant evidence. The default verifier is absent and therefore denies all access.
Never implement a verifier by reading client-supplied identity headers or booleans.
Production integration must use the governed issuer/owner/privacy mechanisms and
their acceptance gates; this SQLite store is not a replacement Core authority.

Session, lease and action handles are process-local capabilities. They deliberately
cannot be reconstructed from a wire payload; a future transport needs an audited
server-side handle/signature adapter. Retrieved text is never an input to execution.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from contextlib import contextmanager
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Callable, Protocol
from uuid import uuid4

SITES = frozenset({"echo", "victoria"})
CAPABILITIES = frozenset({"memory.read", "personal_memory.write", "lighting.execute"})


class Denied(ValueError):
    pass


class Conflict(ValueError):
    pass


class ClarificationRequired(ValueError):
    pass


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _time(value: float) -> float:
    if not isinstance(value, (float, int)) or isinstance(value, bool) or not math.isfinite(value):
        raise Denied("invalid time evidence")
    return float(value)


@dataclass(frozen=True)
class IdentityEvidence:
    issuer: str
    subject: str
    authentication_time: float
    expires_at: float
    upstream_session_id: str


@dataclass(frozen=True)
class LinkApproval:
    approval_id: str
    owner_id: str  # Existing governed person anchor, not a new inferred identity.
    identities: tuple[tuple[str, str], ...]
    expires_at: float


@dataclass(frozen=True)
class GrantApproval:
    approval_id: str
    owner_id: str
    site_id: str
    source: str
    capability: str
    expires_at: float


@dataclass(frozen=True)
class DeviceEvidence:
    device_id: str
    site_id: str
    allowed_lights: tuple[str, ...]
    expires_at: float


@dataclass(frozen=True)
class ContextEvidence:
    upstream_session_id: str
    stationary_site: str | None = None
    location_site: str | None = None
    location_time: float | None = None
    # The trusted location verifier computes containment, never a client flag.
    accuracy_radius_m: float | None = None
    distance_from_zone_center_m: float | None = None
    zone_radius_m: float | None = None


class TrustedIdentityVerifier(Protocol):
    """Installed server dependency, never selected or supplied by a request."""

    def verify_session(self, credential: str) -> IdentityEvidence: ...
    def verify_owner_link(self, credential: str) -> LinkApproval: ...
    def verify_grant(self, credential: str) -> GrantApproval: ...
    def verify_device(self, credential: str) -> DeviceEvidence: ...
    def verify_context(self, credential: str) -> ContextEvidence: ...


@dataclass(frozen=True)
class Session:
    handle: str
    evidence: IdentityEvidence


@dataclass(frozen=True)
class SharedSpeaker:
    handle: str
    evidence: DeviceEvidence


@dataclass(frozen=True)
class GrantLease:
    handle: str
    session: Session
    owner_id: str
    generation: int
    site_id: str
    source: str
    capability: str


class IdentityRegistry:
    """Local contract harness with explicit issuer bindings and revocable grants."""

    def __init__(self, path: str | Path, issuers: dict[str, str], *,
                 verifier: TrustedIdentityVerifier | None = None,
                 clock: Callable[[], float] = time.time,
                 monotonic_clock: Callable[[], float] = time.monotonic):
        if set(issuers) != SITES or len(set(issuers.values())) != 2:
            raise ValueError("distinct registered issuers are required for both sites")
        if any(not issuer.startswith("https://") for issuer in issuers.values()):
            raise ValueError("issuer registration requires HTTPS")
        self._issuers = dict(issuers)
        self._verifier = verifier
        self.clock = clock
        self.monotonic_clock = monotonic_clock
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self._speakers: dict[str, SharedSpeaker] = {}
        self._leases: dict[str, GrantLease] = {}
        self._session_deadlines: dict[str, tuple[float, float]] = {}
        self._speaker_deadlines: dict[str, float] = {}
        self.db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS contract_owner(
            owner_id TEXT PRIMARY KEY, generation INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS contract_identity(
            issuer TEXT NOT NULL, subject TEXT NOT NULL, site_id TEXT NOT NULL,
            owner_id TEXT NOT NULL REFERENCES contract_owner(owner_id),
            active INTEGER NOT NULL, revision INTEGER NOT NULL,
            PRIMARY KEY(issuer, subject));
          CREATE TABLE IF NOT EXISTS contract_grant(
            owner_id TEXT NOT NULL REFERENCES contract_owner(owner_id),
            site_id TEXT NOT NULL, source TEXT NOT NULL, capability TEXT NOT NULL,
            PRIMARY KEY(owner_id, site_id, source, capability));
          CREATE TABLE IF NOT EXISTS contract_approval(
            approval_id TEXT PRIMARY KEY);
        """)

    @contextmanager
    def _transaction(self):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def close(self) -> None:
        self._sessions.clear()
        self._speakers.clear()
        self._leases.clear()
        self._session_deadlines.clear()
        self._speaker_deadlines.clear()
        self.db.close()

    def _trusted(self) -> TrustedIdentityVerifier:
        if self._verifier is None:
            raise Denied("trusted identity adapter is not installed")
        return self._verifier

    def authenticate(self, credential: str) -> Session:
        evidence = self._trusted().verify_session(credential)
        now = _time(self.clock())
        if (evidence.issuer not in self._issuers.values() or not evidence.subject
                or not evidence.upstream_session_id
                or _time(evidence.authentication_time) > now
                or _time(evidence.expires_at) <= now):
            raise Denied("invalid authenticated session")
        session = Session(str(uuid4()), evidence)
        self._sessions[session.handle] = session
        monotonic = _time(self.monotonic_clock())
        self._session_deadlines[session.handle] = (
            monotonic + evidence.expires_at - now,
            monotonic + 300 - (now - evidence.authentication_time))
        return session

    def authenticate_speaker(self, credential: str) -> SharedSpeaker:
        evidence = self._trusted().verify_device(credential)
        if (evidence.site_id not in SITES or not evidence.device_id
                or _time(evidence.expires_at) <= self.clock()
                or any(not _light(entity) for entity in evidence.allowed_lights)):
            raise Denied("invalid shared-speaker registration")
        speaker = SharedSpeaker(str(uuid4()), evidence)
        self._speakers[speaker.handle] = speaker
        self._speaker_deadlines[speaker.handle] = _time(self.monotonic_clock()) + evidence.expires_at - self.clock()
        return speaker

    def check_session(self, session: Session, *, fresh: bool = False) -> None:
        if not isinstance(session, Session) or self._sessions.get(session.handle) is not session:
            raise Denied("session was not issued by this authority")
        now = _time(self.clock())
        deadline, freshness_deadline = self._session_deadlines[session.handle]
        monotonic = _time(self.monotonic_clock())
        if session.evidence.expires_at <= now or monotonic >= deadline:
            self._sessions.pop(session.handle, None)
            self._session_deadlines.pop(session.handle, None)
            raise Denied("session expired")
        if fresh and (monotonic >= freshness_deadline or not 0 <= now - session.evidence.authentication_time <= 300):
            self._session_deadlines[session.handle] = (deadline, min(freshness_deadline, monotonic))
            raise Denied("fresh issuer authentication is required")

    def check_speaker(self, speaker: SharedSpeaker) -> None:
        if (not isinstance(speaker, SharedSpeaker)
                or self._speakers.get(speaker.handle) is not speaker):
            raise Denied("invalid or expired shared-speaker credential")
        if (speaker.evidence.expires_at <= _time(self.clock())
                or _time(self.monotonic_clock()) >= self._speaker_deadlines[speaker.handle]):
            self._speakers.pop(speaker.handle, None)
            self._speaker_deadlines.pop(speaker.handle, None)
            raise Denied("shared-speaker credential expired")

    def invalidate_session(self, session: Session) -> None:
        self.check_session(session)
        del self._sessions[session.handle]
        self._session_deadlines.pop(session.handle, None)

    def _identity_state(self, sessions: tuple[Session, ...]) -> tuple:
        state = []
        for session in sessions:
            row = self.db.execute("""SELECT i.owner_id,i.active,i.revision,o.generation
                FROM contract_identity i JOIN contract_owner o ON o.owner_id=i.owner_id
                WHERE i.issuer=? AND i.subject=?""",
                (session.evidence.issuer, session.evidence.subject)).fetchone()
            state.append(tuple(row) if row is not None else None)
        return tuple(state)

    def owner(self, session: Session) -> str:
        self.check_session(session)
        row = self.db.execute(
            "SELECT owner_id FROM contract_identity WHERE issuer=? AND subject=? AND active=1",
            (session.evidence.issuer, session.evidence.subject)).fetchone()
        if row is None:
            raise Denied("identity has no active governed owner link")
        return row["owner_id"]

    def link_owner(self, first: Session, second: Session, confirmation: str) -> str:
        self.check_session(first, fresh=True)
        self.check_session(second, fresh=True)
        original_state = self._identity_state((first, second))
        approval = self._trusted().verify_owner_link(confirmation)
        identities = {(s.evidence.issuer, s.evidence.subject) for s in (first, second)}
        if (len({first.evidence.issuer, second.evidence.issuer}) != 2
                or set(approval.identities) != identities or len(approval.identities) != 2
                or not approval.owner_id or not approval.approval_id
                or _time(approval.expires_at) <= self.clock()):
            raise Denied("dual-issuer owner confirmation does not match")
        try:
            with self._transaction():
                self.check_session(first, fresh=True)
                self.check_session(second, fresh=True)
                if approval.expires_at <= _time(self.clock()):
                    raise Denied("owner confirmation expired before commit")
                if original_state != self._identity_state((first, second)):
                    raise Denied("identity authority changed during owner confirmation")
                self.db.execute("INSERT INTO contract_approval VALUES (?)", (approval.approval_id,))
                self.db.execute("INSERT OR IGNORE INTO contract_owner VALUES (?,0)", (approval.owner_id,))
                for issuer, subject in identities:
                    old = self.db.execute("SELECT owner_id FROM contract_identity WHERE issuer=? AND subject=?", (issuer, subject)).fetchone()
                    if old is not None and old["owner_id"] != approval.owner_id:
                        raise Denied("identity already belongs to another governed owner")
                    site = next(site for site, registered in self._issuers.items() if issuer == registered)
                    self.db.execute("""INSERT INTO contract_identity VALUES (?,?,?,?,1,1)
                        ON CONFLICT(issuer,subject) DO UPDATE SET active=1, revision=revision+1""",
                        (issuer, subject, site, approval.owner_id))
                self.db.execute("UPDATE contract_owner SET generation=generation+1 WHERE owner_id=?", (approval.owner_id,))
        except sqlite3.IntegrityError as exc:
            raise Conflict("confirmation receipt already consumed") from exc
        return approval.owner_id

    def grant(self, session: Session, receipt: str) -> None:
        owner = self.owner(session)
        original_state = self._identity_state((session,))
        approval = self._trusted().verify_grant(receipt)
        if (approval.owner_id != owner or approval.site_id not in SITES
                or approval.capability not in CAPABILITIES or not approval.source
                or approval.source == "*" or not approval.approval_id
                or _time(approval.expires_at) <= self.clock()):
            raise Denied("grant receipt does not authorize this capability")
        try:
            with self._transaction():
                if self.owner(session) != owner or original_state != self._identity_state((session,)):
                    raise Denied("owner authority changed during grant confirmation")
                if approval.expires_at <= _time(self.clock()):
                    raise Denied("grant confirmation expired before commit")
                if not self.db.execute("SELECT 1 FROM contract_identity WHERE owner_id=? AND site_id=? AND active=1", (owner, approval.site_id)).fetchone():
                    raise Denied("target home identity is not linked")
                self.db.execute("INSERT INTO contract_approval VALUES (?)", (approval.approval_id,))
                self.db.execute("INSERT OR IGNORE INTO contract_grant VALUES (?,?,?,?)", (owner, approval.site_id, approval.source, approval.capability))
                self.db.execute("UPDATE contract_owner SET generation=generation+1 WHERE owner_id=?", (owner,))
        except sqlite3.IntegrityError as exc:
            raise Conflict("grant receipt already consumed") from exc

    def revoke(self, session: Session, site_id: str, source: str) -> None:
        owner = self.owner(session)
        with self._transaction():
            if self.owner(session) != owner:
                raise Denied("owner binding changed")
            self.db.execute("DELETE FROM contract_grant WHERE owner_id=? AND site_id=? AND source=?", (owner, site_id, source))
            self.db.execute("UPDATE contract_owner SET generation=generation+1 WHERE owner_id=?", (owner,))

    def unlink(self, session: Session, site_id: str) -> None:
        owner = self.owner(session)
        if site_id not in SITES:
            raise Denied("unknown home")
        with self._transaction():
            if self.owner(session) != owner:
                raise Denied("owner binding changed")
            self.db.execute("UPDATE contract_identity SET active=0, revision=revision+1 WHERE owner_id=? AND site_id=?", (owner, site_id))
            self.db.execute("DELETE FROM contract_grant WHERE owner_id=? AND site_id=?", (owner, site_id))
            self.db.execute("UPDATE contract_owner SET generation=generation+1 WHERE owner_id=?", (owner,))

    def authorize(self, session: Session, site_id: str, source: str, capability: str) -> GrantLease:
        owner = self.owner(session)
        generation = self.db.execute("SELECT generation FROM contract_owner WHERE owner_id=?", (owner,)).fetchone()[0]
        lease = GrantLease(str(uuid4()), session, owner, generation, site_id, source, capability)
        self._leases[lease.handle] = lease
        try:
            self.check_lease(lease)
        except Exception:
            del self._leases[lease.handle]
            raise
        return lease

    def check_lease(self, lease: GrantLease) -> None:
        if not isinstance(lease, GrantLease) or self._leases.get(lease.handle) is not lease:
            raise Denied("lease was not issued by this authority")
        if self.owner(lease.session) != lease.owner_id:
            raise Denied("owner binding changed")
        generation = self.db.execute("SELECT generation FROM contract_owner WHERE owner_id=?", (lease.owner_id,)).fetchone()[0]
        grant = self.db.execute("SELECT 1 FROM contract_grant WHERE owner_id=? AND site_id=? AND source=? AND capability=?", (lease.owner_id, lease.site_id, lease.source, lease.capability)).fetchone()
        linked = self.db.execute("SELECT 1 FROM contract_identity WHERE owner_id=? AND site_id=? AND active=1", (lease.owner_id, lease.site_id)).fetchone()
        if generation != lease.generation or grant is None or linked is None:
            raise Denied("permission or owner-link generation changed")

    def context(self, session: Session, receipt: str) -> ContextEvidence:
        self.check_session(session)
        evidence = self._trusted().verify_context(receipt)
        if evidence.upstream_session_id != session.evidence.upstream_session_id:
            raise Denied("context belongs to another session")
        if evidence.stationary_site is not None and evidence.stationary_site not in SITES:
            raise Denied("unregistered command device")
        return evidence


def resolve_targets(*, explicit: tuple[str, ...] = (), stationary: str | None = None,
                    interaction: str | None = None, location: ContextEvidence | None = None,
                    now: float) -> tuple[str, ...]:
    """Inputs except explicit/interaction come only from a trusted context adapter."""
    if explicit:
        if len(set(explicit)) != len(explicit) or any(site not in SITES for site in explicit):
            raise ClarificationRequired("unknown or duplicate target home")
        return explicit
    if stationary is not None:
        if stationary not in SITES:
            raise ClarificationRequired("unknown stationary home")
        return (stationary,)
    if interaction is not None:
        if interaction not in SITES:
            raise ClarificationRequired("unknown interaction home")
        return (interaction,)
    if location is not None and location.location_site in SITES:
        values = (location.location_time, location.accuracy_radius_m,
                  location.distance_from_zone_center_m, location.zone_radius_m)
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values):
            fix_time, accuracy, distance, radius = values
            if 0 <= now - fix_time <= 300 and min(accuracy, distance, radius) >= 0 and distance + accuracy <= radius:
                return (location.location_site,)
    raise ClarificationRequired("no fresh, unambiguous home context")


def _light(entity: str) -> bool:
    return isinstance(entity, str) and re.fullmatch(r"light\.[a-z0-9_]+", entity) is not None


@dataclass(frozen=True)
class LightOperation:
    site_id: str
    entity_id: str
    operation: str
    brightness: int | None = None

    def __post_init__(self) -> None:
        if self.site_id not in SITES or not _light(self.entity_id):
            raise Denied("operation is not a registered light target")
        if self.operation not in {"on", "off", "brightness"}:
            raise Denied("unsupported physical operation")
        if self.operation == "brightness":
            if type(self.brightness) is not int or not 0 <= self.brightness <= 100:
                raise Denied("brightness must be an integer percent")
        elif self.brightness is not None:
            raise Denied("unexpected brightness")


@dataclass(frozen=True)
class ActionEnvelope:
    version: int
    request_id: str
    principal_id: str
    original_request_digest: str
    registry_revision: str
    created_at: float
    expires_at: float
    operations: tuple[LightOperation, ...]
    leases: tuple[GrantLease, ...]


class ActionPlanner:
    """Strict direct-input parser. No model output/retrieval argument exists.

    The trusted client route calls plan_direct exactly once on the original user
    event, before retrieval. Do not expose this method as a model/tool capability.
    Supported grammar: 'turn on den lights in victoria', 'set den lights to 40%',
    or 'activate evening scene in both homes'. All aliases are exact provisioned
    names. Arbitrary questions/commands require clarification and create no grant.
    """

    def __init__(self, authority: IdentityRegistry, revision: str,
                 lights: dict[tuple[str, str], tuple[str, ...]],
                 scenes: dict[tuple[str, str], tuple[LightOperation, ...]] | None = None):
        if not revision:
            raise ValueError("a registry revision is required")
        self.authority = authority
        self.revision = revision
        self._lights = {key: tuple(values) for key, values in lights.items()}
        self._scenes = {key: tuple(values) for key, values in (scenes or {}).items()}
        for (site, alias), entities in self._lights.items():
            if site not in SITES or not alias or not entities or any(not _light(e) for e in entities):
                raise ValueError("invalid light registry")
        for (site, alias), operations in self._scenes.items():
            registered = {e for (home, _), entities in self._lights.items() if home == site for e in entities}
            if not operations or any(op.site_id != site or op.entity_id not in registered for op in operations):
                raise ValueError("scene must expand to registered same-home lights")
        self._issued: dict[str, tuple[ActionEnvelope, Session | SharedSpeaker]] = {}
        self._deadlines: dict[str, float] = {}
        self._expired: set[str] = set()

    def plan_direct(self, principal: Session | SharedSpeaker, original_text: str, *,
                    request_id: str, interaction_site: str | None = None,
                    context_receipt: str | None = None) -> ActionEnvelope:
        if not isinstance(original_text, str) or not 0 < len(original_text) <= 512 or not request_id:
            raise ClarificationRequired("invalid direct command")
        if request_id in self._expired:
            raise Denied("expired action ID cannot be reissued")
        text = original_text.strip().lower()
        explicit: tuple[str, ...] = ()
        target = re.search(r" in (echo|la|victoria|both homes)$", text)
        if target:
            explicit = ("echo", "victoria") if target[1] == "both homes" else ("echo" if target[1] == "la" else target[1],)
            text = text[:target.start()]
        command = re.fullmatch(r"turn (on|off) ([a-z0-9 _-]+)", text)
        brightness = re.fullmatch(r"set ([a-z0-9 _-]+) to (\d{1,3})%", text)
        scene = re.fullmatch(r"activate ([a-z0-9 _-]+) scene", text)
        if command:
            operation, alias, value = command[1], command[2], None
        elif brightness:
            operation, alias, value = "brightness", brightness[1], int(brightness[2])
        elif scene:
            operation, alias, value = "scene", scene[1], None
        else:
            raise ClarificationRequired("unsupported direct lighting command")
        context = None
        stationary = None
        if isinstance(principal, SharedSpeaker):
            self.authority.check_speaker(principal)
            if context_receipt is not None or interaction_site is not None:
                raise Denied("shared speaker cannot claim mobile owner context")
            stationary = principal.evidence.site_id
            principal_id = "device/" + principal.evidence.device_id
        else:
            principal_id = self.authority.owner(principal)
            if context_receipt is not None:
                context = self.authority.context(principal, context_receipt)
                stationary = context.stationary_site
        now = _time(self.authority.clock())
        sites = resolve_targets(explicit=explicit, stationary=stationary, interaction=interaction_site, location=context, now=now)
        operations: list[LightOperation] = []
        leases: list[GrantLease] = []
        for site in sites:
            if operation == "scene":
                expanded = self._scenes.get((site, alias))
                if expanded is None:
                    raise ClarificationRequired("scene alias not registered at target home")
                operations.extend(expanded)
            else:
                entities = self._lights.get((site, alias))
                if entities is None:
                    raise ClarificationRequired("light alias not registered at target home")
                operations.extend(LightOperation(site, e, operation, value) for e in entities)
            if isinstance(principal, SharedSpeaker):
                if site != principal.evidence.site_id:
                    raise Denied("shared speaker has no cross-home authority")
            else:
                leases.append(self.authority.authorize(principal, site, "lighting", "lighting.execute"))
        if isinstance(principal, SharedSpeaker) and any(op.entity_id not in principal.evidence.allowed_lights for op in operations):
            raise Denied("shared-speaker light is not allowlisted")
        envelope = ActionEnvelope(1, request_id, principal_id, sha256(original_text.encode()).hexdigest(), self.revision, now, now+60, tuple(operations), tuple(leases))
        if request_id in self._issued:
            previous = self._issued[request_id][0]
            if _envelope_digest(previous) != _envelope_digest(envelope):
                raise Conflict("action request ID already has a frozen intent")
            self.validate(previous)
            return previous
        self._issued[request_id] = (envelope, principal)
        self._deadlines[request_id] = _time(self.authority.monotonic_clock())+60
        return envelope

    def validate(self, envelope: ActionEnvelope) -> None:
        issued = self._issued.get(envelope.request_id)
        if issued is None or issued[0] is not envelope:
            raise Denied("envelope was not issued at the direct-input boundary")
        if (envelope.request_id in self._expired or _time(self.authority.clock()) >= envelope.expires_at
                or _time(self.authority.monotonic_clock()) >= self._deadlines[envelope.request_id]):
            self._expired.add(envelope.request_id)
            raise Denied("action expired")
        if envelope.registry_revision != self.revision:
            raise Denied("action expired or registry changed")
        principal = issued[1]
        if isinstance(principal, SharedSpeaker):
            self.authority.check_speaker(principal)
        else:
            for lease in envelope.leases:
                self.authority.check_lease(lease)


def _envelope_digest(envelope: ActionEnvelope) -> str:
    return _digest({"version": envelope.version, "request_id": envelope.request_id,
        "principal": envelope.principal_id, "request": envelope.original_request_digest,
        "revision": envelope.registry_revision, "operations": [asdict(op) for op in envelope.operations]})


class ActionLedger:
    """Durable per-operation dispatch accounting; deliberately has no HA executor.

    An integration reserves immediately before an independently validated send,
    then records a conclusive result. Any exception after reservation is unknown.
    A second process is fenced by an OS-held lifetime lock. Reopening after a crash
    marks dispatching rows indeterminate; they cannot be automatically retried.
    """

    def __init__(self, path: str | Path, planner: ActionPlanner):
        self.planner = planner
        self._lock = open(str(path) + ".lock", "a+b")
        try:
            if os.fstat(self._lock.fileno()).st_size == 0:
                self._lock.write(b"0")
                self._lock.flush()
            self._lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            self._lock.close()
            raise Denied("action ledger already has a dispatcher") from exc
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS action_dispatch(
            request_id TEXT NOT NULL, operation_index INTEGER NOT NULL,
            digest TEXT NOT NULL, state TEXT NOT NULL, result TEXT,
            PRIMARY KEY(request_id,operation_index))""")
        with self.db:
            self.db.execute("UPDATE action_dispatch SET state='indeterminate' WHERE state='dispatching'")

    def close(self) -> None:
        self.db.close()
        self._lock.close()

    def reserve(self, envelope: ActionEnvelope, operation_index: int) -> LightOperation | None:
        self.planner.validate(envelope)
        if type(operation_index) is not int or not 0 <= operation_index < len(envelope.operations):
            raise Denied("unknown operation")
        digest = _envelope_digest(envelope)
        with self.db:
            old = self.db.execute("SELECT * FROM action_dispatch WHERE request_id=? AND operation_index=?", (envelope.request_id, operation_index)).fetchone()
            if old is not None:
                if old["digest"] != digest:
                    raise Conflict("reused action ID with different intent")
                return None  # completed, in-flight and indeterminate are all non-retryable.
            self.db.execute("INSERT INTO action_dispatch VALUES (?,?,?,'dispatching',NULL)", (envelope.request_id, operation_index, digest))
        return envelope.operations[operation_index]

    def complete(self, request_id: str, operation_index: int, outcome: str) -> None:
        if outcome not in {"succeeded", "failed", "unknown"}:
            raise ValueError("invalid action outcome")
        with self.db:
            row = self.db.execute("UPDATE action_dispatch SET state=?,result=? WHERE request_id=? AND operation_index=? AND state='dispatching'", ("indeterminate" if outcome == "unknown" else "terminal", outcome, request_id, operation_index))
            if row.rowcount != 1:
                raise Conflict("operation is not awaiting a conclusive receipt")

    def status(self, request_id: str, operation_index: int) -> str | None:
        row = self.db.execute("SELECT state FROM action_dispatch WHERE request_id=? AND operation_index=?", (request_id, operation_index)).fetchone()
        return row[0] if row else None
