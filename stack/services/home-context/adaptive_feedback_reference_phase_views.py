"""Opt-in fictional reference fixture: single-invocation phase-view reuse.

No constructor rerun, public representation, grants, FIRST or authority cache.
All subsequent ordinary admission validators and atomic storage still execute.
"""
import hashlib
import math
import threading
import time
from pathlib import Path

from adaptive_feedback_reference_validation import AdaptiveFeedbackReferenceProfile, REFERENCE_PROFILE
from adaptive_feedback_validation import _PHASE_VIEWS_FALLBACK, validate_producer_epochs
from journal import JournalError

INTERFACE_VERSION = "home-adaptive-feedback-reference-phase-views-inprocess-1"
MODE = "invocation_local_phase_views_1"
CLOCK_POLICY = "fixture_locked_clock_1"
SOURCE_DIGEST = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_BRAND = object()
_METRIC_KEYS = ("prepared_invocations", "preparation_fallbacks", "current_hydrations", "prior_hydrations",
                "phase_checks", "successful_consumptions", "consume_fallbacks")
_TIME_KEYS = ("current_hydration_ms", "prior_hydration_ms", "phase_check_ms")


def selection_configuration():
    return {"interface_version": INTERFACE_VERSION, "mode": MODE, "profile": REFERENCE_PROFILE,
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
            if self._journal is not None or journal.profile != REFERENCE_PROFILE or journal.clock is not self.read:
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
                 "_clock", "_current", "_prior", "_used")

    def __init__(self, brand, owner, invocation, commit, old, grant, clock, current, prior):
        if brand is not _BRAND:
            raise JournalError("invalid_phase_views", "private phase-view brand required", 409)
        self._brand, self._owner, self._db = brand, owner, owner.j.db
        self._invocation, self._commit, self._old, self._grant = invocation, commit, old, grant
        self._clock, self._current, self._prior, self._used = clock, current, prior, False


class _InvocationPhaseViewProfile(AdaptiveFeedbackReferenceProfile):
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
        return (type(owner) is _FixtureClock and owner._journal is self.j
                and owner._lock is self.j.lock and self.j.clock is owner.read
                and self.j.dynamic is self and self.j.profile == REFERENCE_PROFILE
                and self.j.db is c["db"] and self.origins is c["origins"] and self.origins.connection is c["db"]
                and self.j.schemas is c["schemas"] and self.j.grants is c["grants"]
                and self.j.adaptive_feedback_reference_config is c["config"]
                and self.j.adaptive_feedback_config is c["config"]
                and c["config"]["config"] is c["configuration_body"]
                and self.config_digest == c["config"]["config_digest"] == c["config_digest"]
                and self.identity == c["config"]["identity"] and self.clock_profile == c["config"]["clock_profile"]
                and self._worker_producer == c["worker"] and c["source_digest"] == SOURCE_DIGEST)

    def _prepare_phase_checked_views(self, commit, old, grant, *, invocation):
        if not self._commissioned_clock():
            self._phase_diagnostics["preparation_fallbacks"] += 1
            return None
        if not self.j.db.in_transaction:
            raise JournalError("invalid_phase_views", "phase views require their active worker transaction", 409)
        clock = self._phase_clock_owner.read()
        self._phase_diagnostics["prepared_invocations"] += 1
        current = self._measured("current_hydrations", "current_hydration_ms",
                                 lambda: self._hydrate_checkpoint_outputs(commit["checkpoint"], grant, commit["input_cut"]))
        prior = (self._measured("prior_hydrations", "prior_hydration_ms",
                                lambda: self._hydrate_checkpoint_outputs(old, grant)) if old is not None else None)
        self._measured("phase_checks", "phase_check_ms", lambda: self._phase_transition({**commit, "checkpoint": current}, prior))
        return _PhaseCheckedViews(_BRAND, self, invocation, commit, old, grant, clock, current, prior)

    def _consume_phase_checked_views(self, prepared, commit, old, grant, *, invocation):
        if (type(prepared) is not _PhaseCheckedViews or prepared._brand is not _BRAND
                or prepared._owner is not self or prepared._db is not self.j.db
                or prepared._invocation is not invocation or prepared._commit is not commit
                or prepared._old is not old or prepared._grant is not grant
                or prepared._used or not self.j.db.in_transaction):
            raise JournalError("invalid_phase_views", "phase views differ from their one actual invocation", 409)
        prepared._used = True
        if not self._commissioned_clock():
            self._phase_diagnostics["consume_fallbacks"] += 1
            return _PHASE_VIEWS_FALLBACK
        if self._phase_clock_owner.read() != prepared._clock:
            self._phase_diagnostics["consume_fallbacks"] += 1
            return _PHASE_VIEWS_FALLBACK
        self._phase_diagnostics["successful_consumptions"] += 1
        return prepared._current, prepared._prior


def create_phase_view_profile(journal, selection, clock_owner):
    """Adopt initialized R71 state; caller installs it before listener startup."""
    with journal.lock:
        old = journal.dynamic
        if (selection != selection_configuration() or type(selection) is not dict
                or type(old) is not AdaptiveFeedbackReferenceProfile
                or journal.profile != REFERENCE_PROFILE or old.j is not journal
                or old.origins.connection is not journal.db or type(clock_owner) is not _FixtureClock
                or clock_owner._journal is not journal or clock_owner._lock is not journal.lock
                or journal.clock is not clock_owner.read
                or old.config_digest != journal.adaptive_feedback_reference_config["config_digest"]
                or old.identity != journal.adaptive_feedback_reference_config["identity"]
                or old.clock_profile != journal.adaptive_feedback_reference_config["clock_profile"]
                or journal.adaptive_feedback_config is not journal.adaptive_feedback_reference_config
                or journal.db.in_transaction
                or journal.db.execute("SELECT count(*) FROM workers").fetchone()[0]
                or journal.db.execute("SELECT count(*) FROM publications").fetchone()[0]
                or journal.db.execute("SELECT count(*) FROM receipts WHERE event_id LIKE 'worker-commit/%'").fetchone()[0]):
            raise ValueError("exact initialized empty reference journal and commissioned phase-view selection required")
        # Authenticated configured grant tuple must be the one initialized for
        # this worker; no constructor/DDL/grant mutation happens during adoption.
        workers = {g.producer_id for g in journal.grants if "worker_commit" in g.operations
                   and (old.identity["site_id"], old.identity["room_id"]) in g.scopes}
        if workers != {old._worker_producer}:
            raise ValueError("phase-view worker grants differ from initialized profile")
        configured = validate_producer_epochs(journal.adaptive_feedback_reference_config["producer_epochs"])
        if any({"principal_id": g.principal_id, "producer_id": g.producer_id, "producer_epoch": g.producer_epoch} not in configured
               or not g.expected_consumer_id or "worker_commit" in g.operations and g.expected_worker_id != g.producer_id
               for g in journal.grants):
            raise ValueError("phase-view grants differ from configured initialized origins")
        adopted = object.__new__(_InvocationPhaseViewProfile)
        adopted.__dict__.update(old.__dict__)
        adopted._phase_clock_owner = clock_owner
        adopted._phase_commission = {"db": journal.db, "origins": old.origins, "schemas": journal.schemas,
                                    "grants": journal.grants, "config": journal.adaptive_feedback_reference_config,
                                    "configuration_body": journal.adaptive_feedback_reference_config["config"],
                                    "config_digest": old.config_digest, "worker": old._worker_producer,
                                    "source_digest": SOURCE_DIGEST}
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
