"""Explicit offline hardware grant admission on the existing Home journal.

This module supplies no HTTP route, credential discovery, wire signing or device
transport. Trusted composition supplies the current decision, reservation reader
and hash-pinned PCB codec. Constructing an unconfigured issuer grants nothing.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import uuid

GRANT_SCHEMA = "home-fixture-execution-grant-1"
LOOKUP_SCHEMA = "home-fixture-execution-grant-lookup-1"
CAP_FIELDS = ("evidence_not_after_home_us", "protection_not_after_home_us",
              "authority_not_after_home_us", "clock_qualification_not_after_home_us")
PROJECTION = ("revision_id", "predecessor_id", "motion_id", "expected_state_generation",
              "domain_mask", "field_mask", "domain_generations", "source_cut", "protection",
              "authority", "issued_home_us", "support_expiry_home_us")
PIN_NAMES = ("fixture", "firmware", "protocol", "profile", "calibration", "contract",
             "model", "home_source", "home_schema")
GEN_FIELDS = ("source_generation", "authority_generation", "ownership_generation")
MAX_U64 = (1 << 64) - 1


class FixtureAuthorityDenied(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise FixtureAuthorityDenied(message)


def u64(value):
    require(type(value) is str and re.fullmatch(r"0|[1-9][0-9]{0,19}", value) is not None,
            "canonical decimal u64 required")
    number = int(value)
    require(number <= MAX_U64, "u64 overflow")
    return number


def sha256(value):
    require(type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None,
            "lowercase sha256 required")
    return value


def _canonical(value, *, signed=False):
    """G is bounded sorted ASCII JSON, never float-based semantic time."""
    def check(item, depth=0):
        require(depth <= 8, "grant nesting bound")
        if item is None or type(item) is bool:
            return
        if type(item) is str:
            require(item.isascii(), "ASCII contract strings required")
            return
        if type(item) is int:
            require((-2**63 if signed else 0) <= item <= MAX_U64, "integer range")
            return
        if type(item) is list:
            require(len(item) <= 64, "array bound")
            for child in item:
                check(child, depth + 1)
            return
        require(type(item) is dict and len(item) <= 64 and
                all(type(key) is str and key.isascii() for key in item), "plain bounded JSON required")
        for child in item.values():
            check(child, depth + 1)
    check(value)
    result = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                        allow_nan=False).encode("ascii")
    require(len(result) <= 16384, "grant byte bound")
    return result


def canonical_bytes(value):
    return _canonical(value)


def record_bytes(value):
    """Receipts additionally contain signed fixture centidegree targets."""
    return _canonical(value, signed=True)


def parse_canonical(raw):
    require(type(raw) is bytes and len(raw) <= 16384, "bounded original bytes required")
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("ascii"), object_pairs_hook=pairs)
    except (UnicodeError, ValueError) as error:
        raise FixtureAuthorityDenied("invalid original JSON") from error
    require(canonical_bytes(value) == raw, "noncanonical original bytes")
    return value


@dataclass(frozen=True)
class FixtureAuthorityPolicy:
    issuer_id: str
    issuer_epoch: str
    fixture_id: str
    device_id: str
    context: dict
    pins: dict
    principal_id: str
    lookup_credential: str
    max_support_us: int = 3_000_000
    max_lookup_age_us: int = 250_000
    capacity: int = 4096
    clock_error_us: int = 0


class FixtureExecutionAuthority:
    """One optional extension of the factory-created journal, never another worker.

    state_provider() independently supplies current committed Home authority;
    reservation_reader(id, revision) reads the fixed authenticated HA allocator.
    Both are process capabilities. Neither is accepted in a submitted body.
    """
    def __init__(self, journal=None, *, policy=None, codec=None, state_provider=None,
                 reservation_reader=None, command_validator=None, clock=None, create=False):
        self.journal, self.policy, self.codec = journal, deepcopy(policy), codec
        self.state_provider, self.reservation_reader, self.clock = state_provider, reservation_reader, clock
        self.command_validator = command_validator
        self.failed = False
        if journal is None:
            return
        require(type(policy) is FixtureAuthorityPolicy and codec is not None and
                all(callable(x) for x in (state_provider, reservation_reader, command_validator, clock)),
                "explicit trusted issuer composition required")
        require(type(policy.max_support_us) is int and type(policy.max_lookup_age_us) is int
                and 0 < policy.max_support_us <= 3_000_000 and 0 < policy.max_lookup_age_us <= 250_000
                and type(policy.capacity) is int and 1 <= policy.capacity <= 65536
                and type(policy.clock_error_us) is int and 0 <= policy.clock_error_us < policy.max_lookup_age_us, "bounded authority policy")
        require(type(policy.issuer_id) is str and bool(policy.issuer_id) and
                type(policy.fixture_id) is str and bool(policy.fixture_id) and
                type(policy.lookup_credential) is str and bool(policy.lookup_credential), "fixed issuer identity/credential")
        for value in (policy.issuer_epoch, policy.principal_id):
            require(u64(value) > 0, "nonzero principal/issuer epoch")
        require(re.fullmatch(r"[a-f0-9]{32}", policy.device_id) is not None, "device identity")
        require(set(policy.pins) == set(PIN_NAMES), "exact profile/model/schema pins")
        for value in policy.pins.values():
            sha256(value)
        self.context_bytes = codec.encode_context(policy.context)
        self.context_digest = hashlib.sha256(self.context_bytes).hexdigest()
        require(all(policy.context["pins"][key] == policy.pins[key]
                    for key in policy.context["pins"]), "context/profile pins differ")
        marker = canonical_bytes({"issuer_id": policy.issuer_id, "issuer_epoch": policy.issuer_epoch,
                                  "fixture_id": policy.fixture_id, "device_id": policy.device_id,
                                  "context_digest": self.context_digest, "pins": policy.pins,
                                  "principal_id": policy.principal_id, "max_support_us": policy.max_support_us,
                                  "max_lookup_age_us": policy.max_lookup_age_us,
                                  "clock_error_us": policy.clock_error_us}).decode()
        with journal.lock:
            existing = journal.db.execute("SELECT name FROM sqlite_master WHERE name='fixture_execution_meta'").fetchone()
            require(existing is not None or create is True, "issuer absent; explicit commissioning required")
            if existing is None:
                journal.db.execute("CREATE TABLE fixture_execution_meta (id INTEGER PRIMARY KEY CHECK(id=1), policy TEXT NOT NULL, blocked INTEGER NOT NULL, last_clock_home_us TEXT NOT NULL)")
                journal.db.execute("INSERT INTO fixture_execution_meta VALUES (1,?,0,'0')", (marker,))
                journal.db.execute("CREATE TABLE fixture_execution_grants (request_id TEXT PRIMARY KEY, grant_id TEXT UNIQUE NOT NULL, input_sha TEXT NOT NULL, body TEXT NOT NULL, receipt TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)")
            stored = journal.db.execute("SELECT policy FROM fixture_execution_meta WHERE id=1").fetchone()
            require(stored is not None and stored[0] == marker, "issuer context/epoch mismatch; no migration")

    def describe(self):
        return {"schema": "home-fixture-execution-authority-1", "version": 1,
                "configured": self.journal is not None, "physical_permission": False,
                "production_endpoint_implemented": False, "shared_journal": self.journal is not None}

    def _ready(self):
        require(self.journal is not None and not self.failed and
                not getattr(self.journal, "_recorder_failed", False), "authority unavailable/incomplete")
        require(callable(getattr(self.journal, "_recorder", None)), "recorder READY required before grant admission")
        with self.journal.lock:
            require(self.journal.db.execute("SELECT blocked FROM fixture_execution_meta WHERE id=1").fetchone()[0] == 0,
                    "durable grant/archive outcome unconfirmed; explicit recovery required")

    def _state(self):
        state = deepcopy(self.state_provider())
        required = {*GEN_FIELDS, *CAP_FIELDS, "domain_generations", "source_cut", "protection", "authority",
                    "evidence_observed_home_us", "decision_ref", "eligible"}
        require(type(state) is dict and set(state) == required and state["eligible"] is True,
                "current committed decision authority unavailable")
        for field in (*GEN_FIELDS, *CAP_FIELDS, "evidence_observed_home_us"):
            u64(state[field])
        for field in ("source_cut", "protection", "authority", "decision_ref"):
            sha256(state[field])
        require(type(state["domain_generations"]) is list and len(state["domain_generations"]) == 8 and
                all(type(x) is int and 0 <= x < 2**32 for x in state["domain_generations"]), "domain generations")
        require(state["ownership_generation"] == self.policy.context["ownership_generation"], "ownership changed")
        return state

    def _now(self):
        # Trusted clock must already be independently qualified. The persisted
        # high-water is an additional discontinuity fence, not UTC qualification.
        now = self.clock()
        require(type(now) is int and 0 < now <= MAX_U64-self.policy.clock_error_us, "qualified integer Home clock required")
        previous = int(self.journal.db.execute("SELECT last_clock_home_us FROM fixture_execution_meta WHERE id=1").fetchone()[0])
        if now < previous:
            self.journal.db.execute("UPDATE fixture_execution_meta SET blocked=1 WHERE id=1")
            raise FixtureAuthorityDenied("Home clock rollback; explicit qualified recovery required")
        self.journal.db.execute("UPDATE fixture_execution_meta SET last_clock_home_us=? WHERE id=1", (str(now),))
        return now

    def admit(self, request_id, reservation, plan, *, admitted_home_us=None):
        self._ready()
        require(type(request_id) is str and 0 < len(request_id) <= 128 and request_id.isascii(), "request identity")
        p, r = deepcopy(plan), deepcopy(reservation)
        require(p["manual"] is False and p["domain_mask"] == 128 and p["field_mask"][:7] == [0]*7,
                "Home task issuer cannot authorize ambient/manual commands")
        require(p["field_mask"][7] in (4, 8, 12) and p["preset_id"] == "0", "closed task field scope")
        require(p["grant_id"] == r["grant_id"] and p["revision_id"] == r["revision_id"] and
                r["mode"] == "autonomous-task" and r["request_id"] == request_id, "reservation identity/route mismatch")
        require(u64(p["grant_id"]) > 0 and u64(p["revision_id"]) > 0, "reserved IDs must be nonzero")
        require(p["context_digest"] == self.context_digest, "context mismatch")
        require(p["grant_body_sha256"] == "0"*64, "grant content must be unsigned placeholder")
        original_wire = self.codec.encode_plan(p)
        require(len(original_wire) == 409, "unagreed wire length")
        input_sha = hashlib.sha256(record_bytes({"plan": p, "reservation": r})).hexdigest()
        with self.journal.lock:
            self._ready()  # A recorder/revocation failure may have won this lock.
            row = self.journal.db.execute("SELECT input_sha,receipt FROM fixture_execution_grants WHERE request_id=?", (request_id,)).fetchone()
            if row:
                require(hmac.compare_digest(row[0], input_sha), "changed immutable grant retry")
                return json.loads(row[1])
            require(self.journal.db.execute("SELECT COUNT(*) FROM fixture_execution_grants").fetchone()[0] < self.policy.capacity,
                    "grant capacity exhausted; archive/recommission explicitly")
            checked = deepcopy(self.reservation_reader(p["grant_id"], p["revision_id"]))
            require(all(checked.get(key) == r.get(key) for key in ("request_id", "grant_id", "revision_id", "mode", "intent_sha"))
                    and checked.get("state") == "reserved", "reservation not current/unconsumed")
            state = self._state()
            require(self.command_validator(deepcopy(p), deepcopy(state)) is True,
                    "independent committed-target/profile validation refused")
            now = self._now()
            if admitted_home_us is not None:
                require(type(admitted_home_us) is int and admitted_home_us == now, "caller cannot backdate grant admission")
            issued, expiry = u64(p["issued_home_us"]), u64(p["support_expiry_home_us"])
            require(u64(state["evidence_observed_home_us"]) <= issued <= now and now+self.policy.clock_error_us < expiry and
                    expiry-issued <= self.policy.max_support_us, "original support timing invalid")
            for name in CAP_FIELDS:
                require(p[name] == state[name] and now+self.policy.clock_error_us < u64(p[name]) and expiry <= u64(p[name]),
                        "original evidence/protection/authority/clock cap mismatch")
            for name in ("source_cut", "protection", "authority", "domain_generations"):
                require(type(p[name]) is type(state[name]) and p[name] == state[name], "decision/causal projection mismatch")
            content = b"C12-CONTENT-V1" + original_wire[:-40]
            g = {"schema": GRANT_SCHEMA, "version": 1, "issuer_id": self.policy.issuer_id,
                 "issuer_epoch": self.policy.issuer_epoch, "grant_id": p["grant_id"], "mode": "autonomous-task",
                 "principal_id": self.policy.principal_id, "session_principal_id": self.policy.context["principal_id"],
                 "fixture_id": self.policy.fixture_id, "device_id": self.policy.device_id,
                 "context_digest": self.context_digest, "pins": deepcopy(self.policy.pins),
                 "command_content_hex": content.hex(), "command_content_sha256": hashlib.sha256(content).hexdigest(),
                 **{key: deepcopy(p[key]) for key in PROJECTION}, **{key: state[key] for key in GEN_FIELDS},
                 "evidence_observed_home_us": state["evidence_observed_home_us"],
                 "admitted_home_us": str(now), "original_available_home_us": str(now),
                 "caps": {key: p[key] for key in CAP_FIELDS}}
            raw = canonical_bytes(g)
            p["grant_body_sha256"] = hashlib.sha256(raw).hexdigest()
            wire = self.codec.encode_plan(p)
            receipt = {"grant_body": g, "grant_body_sha256": p["grant_body_sha256"], "plan": p,
                       "wire_hex": wire.hex(), "wire_sha256": hashlib.sha256(wire).hexdigest(),
                       "context_hex": self.context_bytes.hex(), "decision_ref": state["decision_ref"],
                       "reservation": r, "physical_permission": False}
            tx_id = str(uuid.uuid4())
            recorder = self.journal._recorder
            try:
                # Lost archive ACK or process loss cannot become a clean issuer
                # merely by constructing a replacement object.
                self.journal.db.execute("UPDATE fixture_execution_meta SET blocked=1 WHERE id=1")
                recorder({"kind": "fixture_grant_prepare", "data": {"tx_id": tx_id, "request_id": request_id,
                          "input_sha256": input_sha, "receipt": receipt}})
                self.journal.db.execute("BEGIN IMMEDIATE")
                self.journal.db.execute("INSERT INTO fixture_execution_grants(request_id,grant_id,input_sha,body,receipt) VALUES (?,?,?,?,?)",
                                        (request_id, p["grant_id"], input_sha, raw.decode(), record_bytes(receipt).decode()))
                self.journal.db.execute("COMMIT")
                recorder({"kind": "fixture_grant_commit", "data": {"tx_id": tx_id, "request_id": request_id,
                          "grant_body_sha256": p["grant_body_sha256"]}})
                require(not self.failed and not self.journal._recorder_failed, "independent session failure during admission")
                self.journal.db.execute("UPDATE fixture_execution_meta SET blocked=0 WHERE id=1")
            except BaseException:
                if self.journal.db.in_transaction:
                    self.journal.db.execute("ROLLBACK")
                self.failed = True
                self.journal._recorder_failed = True
                raise
            return deepcopy(receipt)

    def validate_admission(self, grant, reservation, plan, scope=None):
        self._ready()
        with self.journal.lock:
            self._ready()
            row = self.journal.db.execute("SELECT body,receipt,revoked FROM fixture_execution_grants WHERE grant_id=?", (grant.get("grant_id"),)).fetchone()
            require(row is not None and not row[2] and canonical_bytes(grant).decode() == row[0], "grant not durably admitted")
            receipt = json.loads(row[1])
            require(record_bytes(plan) == record_bytes(receipt["plan"]) and
                    reservation["grant_id"] == receipt["reservation"]["grant_id"] and
                    reservation["revision_id"] == receipt["reservation"]["revision_id"], "admitted command/reservation changed")
            if scope is not None:
                require(scope.get("fixture_id") == self.policy.fixture_id and scope.get("device_id") == self.policy.device_id
                        and scope.get("context_digest") == self.context_digest
                        and scope.get("pins") == self.policy.pins, "outbox scope differs from issuer")
            self._current(grant, self._state(), self._now(), receipt)
            return True

    def _current(self, grant, state, now, original):
        require(type(now) is int and u64(grant["original_available_home_us"]) <= now and now+self.policy.clock_error_us <
                min(u64(grant["support_expiry_home_us"]), *(u64(grant["caps"][key]) for key in CAP_FIELDS)), "grant expired/not yet available")
        for field in (*GEN_FIELDS, "source_cut", "protection", "authority", "domain_generations"):
            require(type(grant[field]) is type(state[field]) and grant[field] == state[field], "grant superseded by current authority")
        require(all(grant["caps"][key] == state[key] for key in CAP_FIELDS),
                "original caps changed; shorten with a new reserved command/grant")
        require(original["decision_ref"] == state["decision_ref"] and
                self.command_validator(deepcopy(original["plan"]), deepcopy(state)) is True,
                "committed decision/target no longer authorizes original command")
        require(all(now+self.policy.clock_error_us < u64(state[key]) for key in CAP_FIELDS), "current qualification expired")

    def lookup(self, issuer_id, issuer_epoch, credential, grant_id, nonce):
        """Fixed authenticated process seam; caller cannot choose an issuer URL."""
        self._ready()
        require(issuer_id == self.policy.issuer_id and str(issuer_epoch) == self.policy.issuer_epoch and
                type(credential) is str and hmac.compare_digest(credential, self.policy.lookup_credential), "lookup scope/credential denied")
        require(type(nonce) is bytes and len(nonce) == 32 and any(nonce), "bounded lookup challenge")
        identity = str(grant_id)
        u64(identity)
        with self.journal.lock:
            self._ready()
            row = self.journal.db.execute("SELECT body,revoked,receipt FROM fixture_execution_grants WHERE grant_id=?", (identity,)).fetchone()
            require(row is not None, "unknown original grant")
            g = json.loads(row[0]); state = self._state(); now = self._now()
            self._current(g, state, now, json.loads(row[2]))
            require(not row[1], "grant revoked")
            # Home and HA may have independently qualified UTC errors. Remove
            # the issuer's maximum positive timestamp error from the age cap;
            # HA separately compares its conservative upper current UTC bound.
            cap = min(now+self.policy.max_lookup_age_us-self.policy.clock_error_us, u64(g["support_expiry_home_us"]),
                      *(u64(g["caps"][key]) for key in CAP_FIELDS), *(u64(state[key]) for key in CAP_FIELDS))
            record = {"schema": LOOKUP_SCHEMA, "version": 1, "grant_body": g,
                      "grant_body_sha256": hashlib.sha256(canonical_bytes(g)).hexdigest(), "admitted": True,
                      "durable": True, "current": True, "superseded": False, "revoked": False,
                      "status_observed_home_us": str(now), "status_available_home_us": str(now),
                      "status_not_after_home_us": str(cap),
                      **{"current_"+key: deepcopy(state[key]) for key in
                         (*GEN_FIELDS, "domain_generations", "source_cut", "protection", "authority")}}
            return {"issuer_id": issuer_id, "issuer_epoch": int(self.policy.issuer_epoch),
                    "request_nonce": nonce, "record": record}

    def revoke(self, grant_id, *, reason):
        self._ready(); u64(grant_id)
        require(type(reason) is str and 0 < len(reason) <= 128 and reason.isascii(), "attributed revocation reason")
        with self.journal.lock:
            self._ready()
            require(self.journal.db.execute("SELECT 1 FROM fixture_execution_grants WHERE grant_id=?", (grant_id,)).fetchone(), "unknown grant")
            try:
                now=self._now()
                self.journal.db.execute("UPDATE fixture_execution_meta SET blocked=1 WHERE id=1")
                self.journal._recorder({"kind": "fixture_grant_revocation", "data": {"grant_id": grant_id, "reason": reason, "available_home_us": str(now)}})
                self.journal.db.execute("UPDATE fixture_execution_grants SET revoked=1 WHERE grant_id=?", (grant_id,))
                require(not self.failed and not self.journal._recorder_failed, "independent session failure during revocation")
                self.journal.db.execute("UPDATE fixture_execution_meta SET blocked=0 WHERE id=1")
            except BaseException:
                self.failed = self.journal._recorder_failed = True
                raise
