"""Opt-in fictional G2 fixture: single-invocation phase-view reuse.

No constructor rerun, public representation, grants, FIRST or authority cache.
All subsequent ordinary admission validators and atomic storage still execute.
"""
import hashlib
import math
import threading
import time
from pathlib import Path

import adaptive_feedback_geometry_hold_2_validation as profile_module
from adaptive_feedback_geometry_hold_2_validation import (
    AdaptiveFeedbackGeometryHold2Profile, G2_PROFILE, _PHASE_VIEWS_FALLBACK, validate_producer_epochs
)
from journal import JournalError, canonical

INTERFACE_VERSION = "home-adaptive-feedback-geometry-hold-2-phase-views-inprocess-1"
MODE = "invocation_local_phase_views_1"
CLOCK_POLICY = "fixture_locked_clock_1"
_HASH = hashlib.sha256
_READ_BYTES = Path.read_bytes
_HERE = Path(__file__).resolve().parent
SOURCE_DIGEST = _HASH(_READ_BYTES(Path(__file__))).hexdigest()
_SOURCE_PINS = tuple((name, _HASH(_READ_BYTES(_HERE / name)).hexdigest()) for name in (
    "adaptive_feedback_geometry_hold_2_phase_views.py", "adaptive_feedback_geometry_hold_2_validation.py",
    "journal.py", "schema_registry.py"))
_CONFIG_KEYS = frozenset(("identity", "clock_profile", "materials", "registered_surfaces", "config",
    "config_digest", "producer_epochs", "actor_bindings", "classifier_policy", "write_authorities"))
_WORKER_KEYS = frozenset(("registry", "profile", "clock_profile_digest", "interface_digest",
    "registered_catalogue_digest", "materials_digest", "profile_mode", "feedback_config_digest"))
_PROFILE_CLASS = AdaptiveFeedbackGeometryHold2Profile
_PROFILE_METHOD_NAMES = ("_hydrate_checkpoint_outputs", "_expand_referenced_outputs", "validate_payload", "_phase_transition",
    "_worker_transition", "_worker_transition_values", "_keyframe_recovery", "_read_transaction", "worker_commit")
_SNAPSHOT_JSON = profile_module.snapshot_json
_BINDINGS_KEYS = frozenset(("subjectBindings", "revision_authorizations", "source_origins", "duration_s"))


def _static_method(cls, name):
    for parent in type.__getattribute__(cls, "__mro__"):
        namespace = type.__getattribute__(parent, "__dict__")
        if name in namespace:
            return namespace[name]
    return None


_PROFILE_METHODS = tuple(_static_method(_PROFILE_CLASS, name) for name in _PROFILE_METHOD_NAMES)
_PROFILE_METHOD_CODES = tuple(getattr(method, "__code__", None) for method in _PROFILE_METHODS)


def _source_current(pins=_SOURCE_PINS, read=_READ_BYTES, sha=_HASH,
                    profile_class=_PROFILE_CLASS, methods=_PROFILE_METHODS, codes=_PROFILE_METHOD_CODES):
    try:
        return (Path.read_bytes is read and hashlib.sha256 is sha
                and profile_module.AdaptiveFeedbackGeometryHold2Profile is profile_class
                and profile_module.snapshot_json is _SNAPSHOT_JSON
                and all(_static_method(profile_class, name) is method
                        for name, method in zip(_PROFILE_METHOD_NAMES, methods))
                and all(getattr(method, "__code__", None) is code for method, code in zip(methods, codes))
                and all(sha(read(_HERE / name)).hexdigest() == pin for name, pin in pins))
    except OSError:
        return False

_BRAND = object()
_METRIC_KEYS = ("prepared_invocations", "preparation_fallbacks", "current_hydrations", "prior_hydrations",
                "phase_checks", "successful_consumptions", "consume_fallbacks")
_TIME_KEYS = ("current_hydration_ms", "prior_hydration_ms", "phase_check_ms")


def selection_configuration():
    return {"interface_version": INTERFACE_VERSION, "mode": MODE, "profile": G2_PROFILE,
            "source_digest": SOURCE_DIGEST, "clock_policy": CLOCK_POLICY}


class _FixtureClock:
    def __init__(self):
        self._lock = threading.RLock()
        self._journal = None
        self._value = 0.0
        self._read_callable = self._read

    @property
    def read(self):
        return self._read_callable

    def _read(self):
        with self._lock:
            return self._value

    def bind(self, journal):
        with journal.lock:
            if self._journal is not None or journal.profile != G2_PROFILE or journal.clock is not self.read:
                raise ValueError("exact fresh fixture clock binding required")
            self._journal = journal
            self._lock = journal.lock
        return self

    def advance(self, t):
        with self._lock:
            if (self._journal is None or self._journal.lock is not self._lock
                    or type(t) not in (int, float) or not math.isfinite(t) or not self._value <= t <= 120):
                raise ValueError("monotonic bound fixture clock required")
            self._value = t
            return t


def create_fixture_clock():
    return _FixtureClock()


class _PhaseCheckedViews:
    __slots__ = ("_brand", "_owner", "_db", "_invocation", "_commit", "_old", "_grant",
                 "_clock", "_current", "_prior", "_used", "_commit_text", "_old_text")

    def __init__(self, brand, owner, invocation, commit, old, grant, clock, current, prior):
        if brand is not _BRAND:
            raise JournalError("invalid_phase_views", "private phase-view brand required", 409)
        self._brand, self._owner, self._db = brand, owner, owner.j.db
        self._invocation, self._commit, self._old, self._grant = invocation, commit, old, grant
        self._clock, self._current, self._prior, self._used = clock, current, prior, False
        self._commit_text, self._old_text = canonical(commit), canonical(old)


class _InvocationPhaseViewProfile(AdaptiveFeedbackGeometryHold2Profile):
    def _worker_bindings_current(self):
        c = self._phase_commission
        try:
            active = self._worker_static_bindings
            return (active is c["worker_bindings"]
                    and self._worker_static_bindings_digest == c["worker_startup_digest"]
                    and canonical(_SNAPSHOT_JSON(active)) == c["worker_bindings_text"]
                    and _HASH(c["worker_bindings_text"].encode("utf-8")).hexdigest() == c["worker_bindings_digest"])
        except (AttributeError, JournalError, TypeError, ValueError):
            return False

    def _require_worker_bindings_current(self):
        if not self._worker_bindings_current():
            raise JournalError("invalid_phase_views", "commissioned G2 worker startup bindings changed", 409)

    def _measured(self, count, elapsed, action):
        self._phase_diagnostics[count] = min(2147483647, self._phase_diagnostics[count] + 1)
        started = time.perf_counter_ns()
        try:
            return action()
        finally:
            self._phase_diagnostics[elapsed] += (time.perf_counter_ns() - started) / 1000000

    def _commissioned_clock(self):
        owner = self._phase_clock_owner
        # Check identity before sampling, so a replacement callback is not
        # invoked at an additional position relative to the historical path.
        c = self._phase_commission
        return (_source_current() and self._worker_bindings_current() and type(self) is _InvocationPhaseViewProfile
                and not any(name in self.__dict__ for name in _PROFILE_METHOD_NAMES)
                and all(_static_method(type(self), name) is method
                        for name, method in zip(_PROFILE_METHOD_NAMES, _PROFILE_METHODS))
                and all(getattr(method, "__code__", None) is code
                        for method, code in zip(_PROFILE_METHODS, _PROFILE_METHOD_CODES))
                and type(owner) is _FixtureClock and owner._journal is self.j
                and owner._lock is self.j.lock and self.j.clock is owner.read
                and self.j.dynamic is self and self.j.profile == G2_PROFILE
                and self.j.db is c["db"] and self.origins is c["origins"] and self.origins.connection is c["db"]
                and self.j.schemas is c["schemas"] and self.j.grants is c["grants"]
                and tuple(self.j.grants) == c["grant_values"]
                and canonical(c["config"]) == c["config_text"]
                and self.j.adaptive_feedback_geometry_hold_2_config is c["config"]
                and self.j.adaptive_feedback_config is c["config"]
                and c["config"]["config"] is c["configuration_body"]
                and self.config_digest == c["config"]["config_digest"] == c["config_digest"]
                and self.identity == c["config"]["identity"] and self.clock_profile == c["config"]["clock_profile"]
                and self._worker_producer == c["worker"] and c["source_digest"] == SOURCE_DIGEST)

    def _prepare_phase_checked_views(self, commit, old, grant, *, invocation):
        self._require_worker_bindings_current()
        if not self._commissioned_clock():
            self._phase_diagnostics["preparation_fallbacks"] += 1
            return None
        if not self.j.db.in_transaction:
            raise JournalError("invalid_phase_views", "phase views require their active worker transaction", 409)
        clock = self._phase_clock_owner.read()
        self._phase_diagnostics["prepared_invocations"] += 1
        # Actual Worker2 reconstructs the raw transition against the trusted
        # startup recipe. Its returned views are not the consumed token views.
        checked = self._worker_transition(commit, old, grant)
        self._require_worker_bindings_current()
        current = self._measured("current_hydrations", "current_hydration_ms",
                                 lambda: self._expand_referenced_outputs(checked["checkpoint"], grant, commit["input_cut"]))
        prior = (self._measured("prior_hydrations", "prior_hydration_ms",
                                lambda: self._expand_referenced_outputs(checked["prior"], grant)) if old is not None else None)
        self._measured("phase_checks", "phase_check_ms", lambda: self._phase_transition({**commit, "checkpoint": current}, prior))
        return _PhaseCheckedViews(_BRAND, self, invocation, commit, old, grant, clock, current, prior)

    def _consume_phase_checked_views(self, prepared, commit, old, grant, *, invocation):
        if (type(prepared) is not _PhaseCheckedViews or prepared._brand is not _BRAND
                or prepared._owner is not self or prepared._db is not self.j.db
                or prepared._invocation is not invocation or prepared._commit is not commit
                or prepared._old is not old or prepared._grant is not grant
                or prepared._used or not self.j.db.in_transaction
                or canonical(commit) != prepared._commit_text or canonical(old) != prepared._old_text):
            raise JournalError("invalid_phase_views", "phase views differ from their one actual invocation", 409)
        prepared._used = True
        self._require_worker_bindings_current()
        if not self._commissioned_clock():
            self._phase_diagnostics["consume_fallbacks"] += 1
            return _PHASE_VIEWS_FALLBACK
        if self._phase_clock_owner.read() != prepared._clock:
            self._phase_diagnostics["consume_fallbacks"] += 1
            return _PHASE_VIEWS_FALLBACK
        self._phase_diagnostics["successful_consumptions"] += 1
        return prepared._current, prepared._prior


def create_phase_view_profile(journal, selection, clock_owner, *, worker_static_bindings=None):
    """Adopt initialized G2 state; caller installs it before listener startup."""
    with journal.lock:
        # Keep built-in JSON for the worker bridge, but freeze its canonical
        # snapshot and digests before any journal/worker I/O. The active detached
        # copy must retain both identity and exact content until consumption.
        if type(worker_static_bindings) is not dict or set(worker_static_bindings) != _BINDINGS_KEYS:
            raise ValueError("exact four G2 worker startup bindings required")
        try:
            bindings = _SNAPSHOT_JSON(worker_static_bindings)
        except JournalError as error:
            raise ValueError("bounded inert G2 worker startup bindings required") from error
        if (type(bindings["subjectBindings"]) is not list or len(bindings["subjectBindings"]) > 8
                or type(bindings["revision_authorizations"]) is not list or len(bindings["revision_authorizations"]) > 32
                or type(bindings["source_origins"]) is not dict or len(bindings["source_origins"]) > 64
                or type(bindings["duration_s"]) is not int or not 1 <= bindings["duration_s"] <= 120):
            raise ValueError("bounded exact G2 worker startup recipe required")
        bindings_text = canonical(bindings)
        bindings_digest = _HASH(bindings_text.encode("utf-8")).hexdigest()
        startup_text = canonical({"subjectBindings": bindings["subjectBindings"],
                                  "revision_authorizations": bindings["revision_authorizations"],
                                  "source_origins": bindings["source_origins"],
                                  "runtime_duration_s": bindings["duration_s"]})
        startup_digest = _HASH(startup_text.encode("utf-8")).hexdigest()
        old = journal.dynamic
        if (type(selection) is not dict or set(selection) != set(selection_configuration())
                or any(type(value) is not str for value in selection.values())
                or selection != selection_configuration() or not _source_current()
                or type(old) is not AdaptiveFeedbackGeometryHold2Profile
                or journal.profile != G2_PROFILE or old.j is not journal
                or old.origins.connection is not journal.db or type(clock_owner) is not _FixtureClock
                or clock_owner._journal is not journal or clock_owner._lock is not journal.lock
                or journal.clock is not clock_owner.read
                or old.config_digest != journal.adaptive_feedback_geometry_hold_2_config["config_digest"]
                or old.identity != journal.adaptive_feedback_geometry_hold_2_config["identity"]
                or old.clock_profile != journal.adaptive_feedback_geometry_hold_2_config["clock_profile"]
                or journal.adaptive_feedback_config is not journal.adaptive_feedback_geometry_hold_2_config
                or type(journal.adaptive_feedback_geometry_hold_2_config) is not dict
                or set(journal.adaptive_feedback_geometry_hold_2_config) != _CONFIG_KEYS
                or type(journal.adaptive_feedback_geometry_hold_2_config["config"]) is not dict
                or set(journal.adaptive_feedback_geometry_hold_2_config["config"]) != _WORKER_KEYS
                or journal.db.in_transaction
                or journal.db.execute("SELECT count(*) FROM workers").fetchone()[0]
                or journal.db.execute("SELECT count(*) FROM publications").fetchone()[0]
                or journal.db.execute("SELECT count(*) FROM receipts WHERE event_id LIKE 'worker-commit/%'").fetchone()[0]):
            raise ValueError("exact initialized empty G2 journal and commissioned phase-view selection required")
        # Authenticated configured grant tuple must be the one initialized for
        # this worker; no constructor/DDL/grant mutation happens during adoption.
        workers = {g.producer_id for g in journal.grants if "worker_commit" in g.operations
                   and (old.identity["site_id"], old.identity["room_id"]) in g.scopes}
        if workers != {old._worker_producer}:
            raise ValueError("phase-view worker grants differ from initialized profile")
        configured = validate_producer_epochs(journal.adaptive_feedback_geometry_hold_2_config["producer_epochs"])
        if any({"principal_id": g.principal_id, "producer_id": g.producer_id, "producer_epoch": g.producer_epoch} not in configured
               or not g.expected_consumer_id or "worker_commit" in g.operations and g.expected_worker_id != g.producer_id
               for g in journal.grants):
            raise ValueError("phase-view grants differ from configured initialized origins")
        adopted = object.__new__(_InvocationPhaseViewProfile)
        adopted.__dict__.update(old.__dict__)
        adopted._worker_static_bindings = bindings
        adopted._worker_static_bindings_digest = startup_digest
        adopted._phase_clock_owner = clock_owner
        adopted._phase_commission = {"db": journal.db, "origins": old.origins, "schemas": journal.schemas,
                                    "grants": journal.grants, "config": journal.adaptive_feedback_geometry_hold_2_config,
                                    "configuration_body": journal.adaptive_feedback_geometry_hold_2_config["config"],
                                    "config_digest": old.config_digest, "worker": old._worker_producer,
                                    "source_digest": SOURCE_DIGEST,
                                    "config_text": canonical(journal.adaptive_feedback_geometry_hold_2_config),
                                    "grant_values": tuple(journal.grants)}
        adopted._phase_commission.update({"worker_bindings": bindings,
                                         "worker_bindings_text": bindings_text,
                                         "worker_bindings_digest": bindings_digest,
                                         "worker_startup_digest": startup_digest})
        adopted._phase_diagnostics = {**dict.fromkeys(_METRIC_KEYS, 0), **dict.fromkeys(_TIME_KEYS, 0.0)}
        return adopted


def phase_view_metrics(profile):
    """Measured new prepare/consume path only; never an admission input."""
    if type(profile) is not _InvocationPhaseViewProfile:
        raise ValueError("commissioned phase-view profile required")
    with profile.j.lock:
        return {"interface_version": INTERFACE_VERSION, "mode": MODE,
                "scope": "worker_prepare_consume_only_inherited_reads_replay_and_fallback_lower_hydration_excluded",
                **profile._phase_diagnostics}
