"""Publisher health: mirror fresh and observer fresh and MQTT up.

The caller feeds the last time it saw the Home Assistant mirror heartbeat and
the last successful observer poll, and whether the MQTT client is connected.
``status(now)`` says whether the publisher is healthy. When it is not, the
caller must stop the heartbeat (so the generated ``publisher_fresh`` sensor
turns off and Home Assistant falls back to its legacy path) and publish every
belief entity as ``unknown``. This module only decides; it never publishes.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .stories import MIRROR_FRESH_S, OBSERVER_FRESH_S, seconds_between

UNKNOWN = "unknown"


@dataclass(frozen=True)
class HealthStatus:
    """One evaluation of the three health inputs."""

    ok: bool
    mirror_fresh: bool
    observer_fresh: bool
    mqtt_up: bool
    mirror_age_s: float | None
    observer_age_s: float | None
    reasons: tuple[str, ...]

    @property
    def heartbeat_allowed(self) -> bool:
        """The heartbeat is published only while healthy."""
        return self.ok

    def belief_state(self, value: str) -> str:
        """What to publish for a belief: the value while healthy, else unknown."""
        return value if self.ok else UNKNOWN

    def as_dict(self) -> dict:
        return {"ok": self.ok, "mirror_fresh": self.mirror_fresh, "observer_fresh": self.observer_fresh,
                "mqtt_up": self.mqtt_up, "mirror_age_s": self.mirror_age_s,
                "observer_age_s": self.observer_age_s, "reasons": list(self.reasons)}


def evaluate(now: dt.datetime, mirror_at: dt.datetime | None, observer_at: dt.datetime | None,
             mqtt_up: bool, mirror_fresh_s: float = MIRROR_FRESH_S,
             observer_fresh_s: float = OBSERVER_FRESH_S) -> HealthStatus:
    """Pure evaluation: never-seen counts as stale; a future stamp is fresh."""
    mirror_age = seconds_between(mirror_at, now)
    observer_age = seconds_between(observer_at, now)
    mirror_fresh = mirror_age is not None and mirror_age < mirror_fresh_s
    observer_fresh = observer_age is not None and observer_age < observer_fresh_s
    reasons = []
    if not mirror_fresh:
        reasons.append("mirror_stale" if mirror_at is not None else "mirror_never_seen")
    if not observer_fresh:
        reasons.append("observer_stale" if observer_at is not None else "observer_never_seen")
    if not mqtt_up:
        reasons.append("mqtt_down")
    return HealthStatus(ok=not reasons, mirror_fresh=mirror_fresh, observer_fresh=observer_fresh,
                        mqtt_up=bool(mqtt_up), mirror_age_s=mirror_age, observer_age_s=observer_age,
                        reasons=tuple(reasons))


class Health:
    """Stateful wrapper: the caller notes events, then asks ``status(now)``."""

    def __init__(self) -> None:
        self._mirror_at: dt.datetime | None = None
        self._observer_at: dt.datetime | None = None
        self._mqtt_up = False
        self._last: HealthStatus | None = None
        self.journal: list[dict] = []

    def note_mirror(self, now: dt.datetime) -> None:
        """A mirror heartbeat (or any mirror message) arrived at ``now``."""
        self._mirror_at = now

    def note_observer(self, now: dt.datetime) -> None:
        """An observer poll succeeded at ``now``. Unreachable polls are not noted."""
        self._observer_at = now

    def set_mqtt(self, up: bool) -> None:
        self._mqtt_up = bool(up)

    def status(self, now: dt.datetime) -> HealthStatus:
        """Evaluate now; journal every change of ``ok``."""
        current = evaluate(now, self._mirror_at, self._observer_at, self._mqtt_up)
        if self._last is None or self._last.ok != current.ok:
            self.journal.append({"t": now.isoformat(timespec="seconds"), "ok": current.ok,
                                 "reasons": list(current.reasons)})
        self._last = current
        return current
