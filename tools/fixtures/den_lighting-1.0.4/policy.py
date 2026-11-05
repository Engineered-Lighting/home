"""Deterministic, HA-independent ownership of the four physical Victoria lights."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
WARMEST = dict(zip(LIGHTS, (1000, 1000, 2202, 2202)))
VACANCY_SECONDS = 120
GAMING_TTL = 45


@dataclass(frozen=True)
class Scene:
    on: bool
    brightness: int | None = None
    kelvin: int | None = None

    @classmethod
    def load(cls, data):
        if not isinstance(data, dict) or type(data.get("on")) is not bool:
            raise ValueError("invalid saved scene")
        b, k = data.get("brightness"), data.get("kelvin")
        if b is not None and (type(b) is not int or not 0 <= b <= 255):
            raise ValueError("invalid brightness")
        if k is not None and (type(k) is not int or not 1000 <= k <= 20000):
            raise ValueError("invalid temperature")
        return cls(data["on"], b, k)

    def matches(self, actual):
        return (actual is not None and self.on == actual.on and
                (not self.on or ((self.brightness is None or
                  actual.brightness is not None and abs(self.brightness - actual.brightness) <= 1) and
                 (self.kelvin is None or actual.kelvin is not None and
                  abs(1e6 / self.kelvin - 1e6 / actual.kelvin) <= 2))))


@dataclass(frozen=True)
class Hold:
    scene: Scene
    since: float


@dataclass(frozen=True)
class Command:
    entity_id: str
    target: Scene
    transition: float
    revision: int


class Controller:
    def __init__(self):
        self.enabled = False
        self.gaming_enabled = False
        self.gaming = False
        self.revision = 0
        self.observed = {}
        self.normal = {}
        self.snapshot = {}
        self.holds = {}
        self.warmest = dict(WARMEST)
        self.occupancy = "unknown"
        self.vacant_since = None
        self.last_sample = None
        self.game_status = "unknown"
        self.playing_samples = 0
        self.idle_since = None
        self._emitted = {}
        self._restoring = False

    def _changed(self):
        self.revision += 1
        # A fresh revision invalidates queued commands but retains signatures of
        # successfully dispatched desired states. Otherwise telemetry fights holds.

    def observe(self, entity_id, scene, now):
        if entity_id not in LIGHTS:
            return
        self.observed[entity_id] = scene
        self.normal.setdefault(entity_id, scene)

    def update_warmest(self, entity_id, kelvin):
        if entity_id in LIGHTS and type(kelvin) is int and 1000 <= kelvin <= 20000:
            self.warmest[entity_id] = kelvin

    def manual(self, entity_id, scene, now, *, update_normal=True):
        if entity_id not in LIGHTS:
            return
        self.holds[entity_id] = Hold(scene, now)
        self.observed[entity_id] = scene
        if not self.gaming and update_normal:
            self.normal[entity_id] = scene
        self._emitted.pop(entity_id, None)
        self._changed()

    def set_occupancy(self, value, now):
        value = value if value in {"on", "off"} else "unknown"
        previous, since = self.occupancy, self.vacant_since
        if value == previous:
            return
        if value == "on" and previous == "off" and since is not None and now - since >= VACANCY_SECONDS:
            # Normal arrival is last-level ON, while a held manual OFF still wins.
            self.normal = {e: Scene(True, s.brightness, s.kelvin) for e, s in self.normal.items()}
        self.occupancy = value
        self.vacant_since = now if value == "off" else None
        self._emitted.clear()
        self._changed()

    def set_enabled(self, enabled, now):
        self.enabled = bool(enabled)
        self._emitted.clear()
        self._changed()

    def set_gaming_enabled(self, enabled, now):
        enabled = bool(enabled)
        if self.gaming_enabled == enabled:
            return
        self.gaming_enabled = enabled
        if not enabled and self.gaming:
            self._end_game()
        self.playing_samples = 0
        self.idle_since = None
        self._emitted.clear()
        self._changed()

    def gaming_sample(self, status, sampled_at, now):
        if (type(sampled_at) not in (int, float) or not math.isfinite(sampled_at)
                or not -5 <= now - sampled_at <= GAMING_TTL):
            self.game_status = "unknown"
            self.playing_samples = 0
            self.idle_since = None
            return
        if self.last_sample is not None and sampled_at <= self.last_sample:
            return
        contiguous = self.last_sample is not None and sampled_at - self.last_sample <= GAMING_TTL
        if not contiguous:
            self.playing_samples = 0
            self.idle_since = None
        self.last_sample = sampled_at
        self.game_status = status if status in {"playing", "idle"} else "unknown"
        if self.game_status == "playing":
            self.playing_samples += 1
            self.idle_since = None
            if self.gaming_enabled and not self.gaming and self.playing_samples >= 2:
                # Vacancy-off is temporary delivery, not a replacement normal
                # scene. HA OFF states omit level/CCT; retain known preferences
                # so a game started while vacant cannot erase arrival settings.
                self.snapshot = {e: Scene(s.on,
                    s.brightness if s.brightness is not None else self.observed.get(e, s).brightness,
                    s.kelvin if s.kelvin is not None else self.observed.get(e, s).kelvin)
                    for e, s in self.normal.items()}
                self.gaming = True
                self._restoring = False
                self._emitted.clear()
                self._changed()
        elif self.game_status == "idle":
            self.playing_samples = 0
            if self.idle_since is None:
                self.idle_since = sampled_at
            if self.gaming and sampled_at - self.idle_since >= 30:
                self._end_game()
        else:
            self.playing_samples = 0
            self.idle_since = None

    def _end_game(self):
        self.gaming = False
        self.normal.update(self.snapshot)
        self.snapshot = {}
        self.holds.clear()
        self._restoring = True
        self._emitted.clear()
        self._changed()

    def resume(self, entity_ids, now):
        for e in entity_ids if entity_ids is not None else LIGHTS:
            if e in LIGHTS:
                self.holds.pop(e, None)
                self._emitted.pop(e, None)
        self._changed()

    def fresh_game(self, now):
        return self.last_sample is not None and -5 <= now - self.last_sample <= GAMING_TTL and self.game_status in {"playing", "idle"}

    def desired(self, entity_id, now):
        if self.occupancy == "unknown":
            return None
        hold = self.holds.get(entity_id)
        if self.occupancy == "off":
            since = self.vacant_since
            if not self.gaming and hold:
                since = max(since, hold.since)
            if since is not None and now - since >= VACANCY_SECONDS:
                return Scene(False)
            return None
        # Preserve an active game's scene through signal loss without making
        # ordinary occupancy arrivals depend on the optional gaming sensor.
        if self.gaming_enabled and self.gaming and not self.fresh_game(now):
            return None
        if hold:
            return hold.scene
        if self.gaming:
            return Scene(True, 38, self.warmest[entity_id])
        return self.normal.get(entity_id)

    def reconcile(self, now):
        if not self.enabled:
            return []
        if (not self.gaming and self.occupancy == "off" and self.vacant_since is not None):
            expired = [e for e, h in self.holds.items()
                       if now - max(self.vacant_since, h.since) >= VACANCY_SECONDS]
            for e in expired:
                self.holds.pop(e)
            if expired:
                self._changed()
        commands = []
        for e in LIGHTS:
            target = self.desired(e, now)
            if target is None or self._emitted.get(e) == target:
                continue
            if target.matches(self.observed.get(e)):
                self._emitted[e] = target
                continue
            self._emitted[e] = target
            transition = 5 if self._restoring and target.on else 2
            commands.append(Command(e, target, transition, self.revision))
        return commands

    def command_failed(self, entity_id):
        self._emitted.pop(entity_id, None)

    def dump(self):
        return {"version": 1, "enabled": self.enabled, "gaming_enabled": self.gaming_enabled,
                "gaming": self.gaming, "normal": {e: asdict(s) for e, s in self.normal.items()},
                "snapshot": {e: asdict(s) for e, s in self.snapshot.items()},
                "holds": {e: {"scene": asdict(h.scene), "since": h.since} for e, h in self.holds.items()}}

    @classmethod
    def restore(cls, data):
        c = cls()
        if not data:
            return c
        if data.get("version") != 1:
            raise ValueError("unsupported saved controller version")
        for key in ("enabled", "gaming_enabled", "gaming"):
            if type(data.get(key)) is not bool:
                raise ValueError("invalid saved controller flags")
            setattr(c, key, data[key])
        for key in ("normal", "snapshot"):
            setattr(c, key, {e: Scene.load(s) for e, s in data.get(key, {}).items() if e in LIGHTS})
        for e, hold in data.get("holds", {}).items():
            if e in LIGHTS:
                since = hold["since"]
                if type(since) not in (int, float) or not math.isfinite(since):
                    raise ValueError("invalid saved hold time")
                c.holds[e] = Hold(Scene.load(hold["scene"]), since)
        return c

    def status(self, now):
        return {"automatic": self.enabled, "gaming_enabled": self.gaming_enabled,
                "gaming_session": self.gaming, "gaming_signal": self.game_status if self.fresh_game(now) else "unknown",
                "occupancy": self.occupancy, "revision": self.revision,
                "holds": {e: {"until": "game_ends" if self.gaming else "room_vacant_2_minutes",
                              "target": asdict(h.scene)} for e, h in self.holds.items()}}
