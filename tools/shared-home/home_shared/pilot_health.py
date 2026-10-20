"""On-demand cached health verdicts for the LA camera pilot (speed plan S1.1).

No thread runs while the den is empty. A request that finds the verdict older
than max_age_seconds refreshes it once, under a lock, before dispatch; requests
that arrive while that refresh runs wait for it and share it. A refresh that
began before a request arrived is shared only while it is within max_age of
that arrival, so no request dispatches on a verdict older than the bound.

A failed or stale verdict raises ValueError; the backends turn it into
BeforeDispatchRejected('preflight_rejected') exactly as a failed check before.
Failures are cached for the same window (fail-closed). The checks and their
thresholds are the caller's and are unchanged; the verdict only also keeps
the readings the check took (GPU temperature, Tctl) for the thermal ladder.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import threading
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping


class HealthVerdictFailed(ValueError):
    """The cached verdict is a failed check."""


class HealthVerdictStale(ValueError):
    """No verdict within the age bound could be had."""


@dataclass(frozen=True)
class Verdict:
    checked_at: float              # clock() when the check began
    error: str | None              # exception class name of a failed check
    readings: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    generation: int = 0            # which refresh took it (1, 2, ...)

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def gpu_c(self) -> Any:
        return self.readings.get("gpu_c")

    @property
    def tctl_c(self) -> Any:
        return self.readings.get("tctl_c")


class HealthCache:
    """check(readings: dict) raises unless healthy and records what it read."""

    def __init__(self, check: Callable[[dict], None], *, max_age_seconds: float = 2.0,
                 clock: Callable[[], float] = time.monotonic):
        if not callable(check):
            raise ValueError("health check must be callable")
        if type(max_age_seconds) not in (int, float) or not math.isfinite(max_age_seconds) or not 0 <= max_age_seconds <= 3:
            raise ValueError("health verdict age must be 0-3 seconds")
        self._check, self.max_age, self._clock = check, float(max_age_seconds), clock
        self._lock = threading.Lock()
        self._verdict: Verdict | None = None
        self.refreshes = 0

    def latest(self) -> Verdict | None:
        """The last verdict, whatever its age (for readings; never for admission)."""
        return self._verdict

    def _usable(self, verdict: Verdict | None, arrived: float, seen: int, fresh: bool) -> bool:
        """Begun after this request arrived (a shared refresh), or at most
        max_age before it. With max_age 0 or fresh only the former: every such
        request has a check that began after it arrived, as before."""
        return verdict is not None and (verdict.generation > seen or (
            not fresh and self.max_age > 0 and arrived - verdict.checked_at <= self.max_age))

    def require(self, *, fresh: bool = False) -> Verdict:
        """A passing verdict usable for this call, or raise. fresh=True takes
        only a check begun after this call (before a model worker starts)."""
        arrived, seen = self._clock(), self.refreshes
        verdict = self._verdict
        if not self._usable(verdict, arrived, seen, fresh):
            with self._lock:
                verdict = self._verdict
                if not self._usable(verdict, arrived, seen, fresh):
                    return self._refresh()
        if not self._usable(verdict, arrived, seen, fresh):
            raise HealthVerdictStale("health verdict stale")
        if not verdict.ok:
            raise HealthVerdictFailed("health verdict failed: " + verdict.error)
        return verdict

    def _refresh(self) -> Verdict:
        """Under the lock: one check; its own caller sees the original error."""
        readings: dict = {}
        # Counted before the start is read: a request that saw the old count
        # arrived before this check began.
        self.refreshes += 1
        generation, started = self.refreshes, self._clock()
        try:
            self._check(readings)
        except BaseException as error:
            self._verdict = Verdict(started, type(error).__name__, MappingProxyType(dict(readings)), generation)
            raise
        verdict = self._verdict = Verdict(started, None, MappingProxyType(dict(readings)), generation)
        return verdict
