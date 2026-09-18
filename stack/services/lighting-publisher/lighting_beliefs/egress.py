"""The egress gate: may the publisher make one outbound call right now?

A call is allowed only when every switch is on:

1. the egress record file is a regular file (no symlink), not group- or
   other-writable, parses, has ``enabled: true`` and lists the requested
   scope in ``scopes``;
2. the environment has ``TYPESAFE_EGRESS == "1"``;
3. the Home Assistant kill-switch mirror reads ``on`` and its age is a finite,
   non-negative number (a few seconds of clock skew allowed) strictly below
   ``TOGGLE_MAX_AGE_S`` (a stale, absent or numerically invalid mirror means
   off);
4. fewer than ``MAX_CALLS_PER_MINUTE`` calls were allowed in the last minute
   and no call is in flight. A ticket not released within
   ``IN_FLIGHT_TIMEOUT_S`` is expired and journaled as ``in_flight_expired``
   so a crashed call cannot block the gate forever.

Every decision, allowed or denied, is appended to a JSONL journal with the
reason and the state of each check. This module holds no network code: the
toggle reader and the clock are injected, and the caller performs the call
after an allowed decision and releases the in-flight slot afterwards.
"""
from __future__ import annotations

import json
import os
import stat
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

RECORD_SCHEMA = "lighting-egress-record/v1"
ENV_FLAG = "TYPESAFE_EGRESS"
TOGGLE_MAX_AGE_S = 300.0
TOGGLE_SKEW_S = 5.0
MAX_CALLS_PER_MINUTE = 6
RATE_WINDOW_S = 60.0
IN_FLIGHT_TIMEOUT_S = 120.0
GROUP_OTHER_WRITE = stat.S_IWGRP | stat.S_IWOTH

# Check order defines which reason a denied decision reports.
CHECK_ORDER = ("record", "scope", "env", "toggle", "rate", "in_flight")


@dataclass(frozen=True)
class ToggleState:
    """The mirrored kill switch: its value and how old the mirror is."""

    value: str
    age_s: float


@dataclass
class Decision:
    """Outcome of one gate request."""

    allowed: bool
    reason: str
    scope: str
    checks: dict = field(default_factory=dict)
    ticket: int | None = None

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "reason": self.reason, "scope": self.scope,
                "checks": dict(self.checks), "ticket": self.ticket}


def load_record(path: str | os.PathLike[str],
                owner_uid: int | None = None) -> tuple[Mapping[str, Any] | None, str]:
    """Return (record, status) where status is ok, absent, not_a_file, insecure or invalid.

    The record is the first rollback rung and the only signed artifact, so it
    is checked like the roster: ``lstat`` (a symlink is ``not_a_file``), a
    regular file, and no group or other write bit (``insecure``). When
    ``owner_uid`` is given the file must be owned by that uid.
    """
    p = Path(path)
    try:
        st = p.lstat()
    except FileNotFoundError:
        return None, "absent"
    if not stat.S_ISREG(st.st_mode):
        return None, "not_a_file"
    if stat.S_IMODE(st.st_mode) & GROUP_OTHER_WRITE:
        return None, "insecure"
    if owner_uid is not None and st.st_uid != owner_uid:
        return None, "insecure"
    try:
        record = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "invalid"
    if not isinstance(record, Mapping) or record.get("schema") != RECORD_SCHEMA:
        return None, "invalid"
    return record, "ok"


def file_toggle_reader(path: str | os.PathLike[str],
                       clock: Callable[[], float] = time.time) -> Callable[[], ToggleState | None]:
    """A toggle reader over a local mirror file ``{"state": "on", "received_at": <epoch>}``.

    The M4 MQTT mirror will supply the same callable shape from memory; this
    file form serves the offline runner and the tests. ``NaN``/``Infinity``
    literals, non-numeric or boolean ``received_at`` values are rejected
    (reader returns None, the gate reports ``absent``); a ``received_at`` in
    the future yields a negative age that ``_check_toggle`` judges against
    ``TOGGLE_SKEW_S``.
    """
    def read() -> ToggleState | None:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=_reject_constant)
            received_at = data["received_at"]
            if isinstance(received_at, bool) or not isinstance(received_at, (int, float)):
                return None
            return ToggleState(str(data["state"]), clock() - float(received_at))
        except (OSError, ValueError, KeyError, TypeError):
            return None
    return read


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite JSON constant {name} not allowed")


def _valid_age(age: Any) -> bool:
    """A finite int/float (not bool) no more negative than the skew allowance."""
    if isinstance(age, bool) or not isinstance(age, (int, float)):
        return False
    age = float(age)
    if age != age or age in (float("inf"), float("-inf")):
        return False
    return age >= -TOGGLE_SKEW_S


class EgressGate:
    """Decide, journal and rate-limit outbound calls. Holds no network code."""

    def __init__(self, record_path: str | os.PathLike[str],
                 toggle_reader: Callable[[], ToggleState | None],
                 journal_path: str | os.PathLike[str],
                 env: Mapping[str, str] | None = None,
                 clock: Callable[[], float] = time.time,
                 max_calls_per_minute: int = MAX_CALLS_PER_MINUTE,
                 toggle_max_age_s: float = TOGGLE_MAX_AGE_S,
                 in_flight_timeout_s: float = IN_FLIGHT_TIMEOUT_S,
                 record_owner_uid: int | None = None):
        self.record_path = Path(record_path)
        self.toggle_reader = toggle_reader
        self.journal_path = Path(journal_path)
        self.env = os.environ if env is None else env
        self.clock = clock
        self.max_calls_per_minute = max_calls_per_minute
        self.toggle_max_age_s = toggle_max_age_s
        self.in_flight_timeout_s = in_flight_timeout_s
        self.record_owner_uid = record_owner_uid
        self._allowed_times: deque[float] = deque()
        self._in_flight: int | None = None
        self._in_flight_since: float | None = None
        self._in_flight_scope: str | None = None
        self._next_ticket = 1
        self._lock = threading.Lock()

    # -- checks -----------------------------------------------------------
    def _check_record(self, scope: str) -> tuple[dict, dict]:
        record, status = load_record(self.record_path, self.record_owner_uid)
        if record is None:
            return {"ok": False, "status": status}, {"ok": False, "status": "no_record"}
        enabled = record.get("enabled") is True
        scopes = record.get("scopes")
        scopes = [s for s in scopes if isinstance(s, str)] if isinstance(scopes, list) else []
        record_check = {"ok": enabled, "status": "enabled" if enabled else "disabled"}
        scope_check = {"ok": scope in scopes, "status": "match" if scope in scopes else "mismatch"}
        return record_check, scope_check

    def _check_env(self) -> dict:
        value = self.env.get(ENV_FLAG)
        return {"ok": value == "1", "status": "on" if value == "1" else ("absent" if value is None else "off")}

    def _check_toggle(self) -> dict:
        try:
            state = self.toggle_reader()
        except Exception:  # a broken mirror is an absent mirror
            state = None
        if state is None:
            return {"ok": False, "status": "absent"}
        if not _valid_age(state.age_s):
            return {"ok": False, "status": "invalid"}
        age = max(0.0, float(state.age_s))
        if age >= self.toggle_max_age_s:
            return {"ok": False, "status": "stale", "age_s": round(age, 1)}
        if state.value != "on":
            return {"ok": False, "status": "off", "age_s": round(age, 1)}
        return {"ok": True, "status": "on", "age_s": round(age, 1)}

    def _check_rate(self, now: float) -> dict:
        while self._allowed_times and now - self._allowed_times[0] >= RATE_WINDOW_S:
            self._allowed_times.popleft()
        used = len(self._allowed_times)
        return {"ok": used < self.max_calls_per_minute, "status": f"{used}/{self.max_calls_per_minute}"}

    def _check_in_flight(self, now: float) -> dict:
        if self._in_flight is not None and self._in_flight_since is not None \
                and now - self._in_flight_since >= self.in_flight_timeout_s:
            expired = Decision(True, "allowed", self._in_flight_scope or "", {}, self._in_flight)
            held_s = round(now - self._in_flight_since, 1)
            self._clear_in_flight()
            self._journal("in_flight_expired", expired, now, held_s=held_s)
        busy = self._in_flight is not None
        return {"ok": not busy, "status": "busy" if busy else "idle"}

    def _clear_in_flight(self) -> None:
        self._in_flight = None
        self._in_flight_since = None
        self._in_flight_scope = None

    # -- public API -------------------------------------------------------
    def request(self, scope: str) -> Decision:
        """Evaluate every check for ``scope``; allowed only when all pass."""
        if not isinstance(scope, str) or not scope:
            raise ValueError("scope must be a non-empty string")
        with self._lock:
            now = self.clock()
            record_check, scope_check = self._check_record(scope)
            checks = {
                "record": record_check,
                "scope": scope_check,
                "env": self._check_env(),
                "toggle": self._check_toggle(),
                "rate": self._check_rate(now),
                "in_flight": self._check_in_flight(now),
            }
            failing = [name for name in CHECK_ORDER if not checks[name]["ok"]]
            if failing:
                decision = Decision(False, f"{failing[0]}_{checks[failing[0]]['status']}", scope, checks)
            else:
                ticket = self._next_ticket
                self._next_ticket += 1
                self._in_flight = ticket
                self._in_flight_since = now
                self._in_flight_scope = scope
                self._allowed_times.append(now)
                decision = Decision(True, "allowed", scope, checks, ticket)
            self._journal("decision", decision, now)
            return decision

    def release(self, decision: Decision, outcome: str = "done", **details: Any) -> None:
        """Free the in-flight slot after the call; journal the outcome."""
        with self._lock:
            if not decision.allowed or decision.ticket is None:
                return
            if self._in_flight == decision.ticket:
                self._clear_in_flight()
            self._journal("release", decision, self.clock(), outcome=outcome, **details)

    def in_flight(self) -> bool:
        with self._lock:
            return self._in_flight is not None

    # -- journal ----------------------------------------------------------
    def _journal(self, event: str, decision: Decision, now: float, **extra: Any) -> None:
        entry = {
            "event": event,
            "ts": round(now, 3),
            "ts_iso": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
            "scope": decision.scope,
            "ticket": decision.ticket,
            "allowed": decision.allowed,
            "reason": decision.reason,
            "checks": decision.checks,
        }
        entry.update(extra)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
