"""Deterministic, HA-independent ownership of the den's room lights (one per socket; four Hue bulbs today).

While the coordinator switch is on, a fresh overlay sets the lights it names after vacancy and holds.
Overlays are never stored and lapse back to incumbent gaming or normal scenes.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from uuid import uuid4

LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
WARMEST = dict(zip(LIGHTS, (1000, 1000, 2202, 2202)))
DEFAULT_WARMEST = 2000
PLAN_VALID_S = (5, 120)
PLAN_TRANSITION_S = (0, 10)
RESTORE_TRANSITION = 5
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
    plan_id: str | None = None
    generation: int | None = None


class Controller:
    def __init__(self, lights=LIGHTS):
        self.lights = tuple(lights)
        self.enabled = False
        self.gaming_enabled = False
        self.gaming = False
        self.revision = 0
        self.observed = {}
        self.normal = {}
        self.snapshot = {}
        self.holds = {}
        self.warmest = {e: WARMEST.get(e, DEFAULT_WARMEST) for e in self.lights}
        self.occupancy = "unknown"
        self.vacant_since = None
        self.last_sample = None
        self.game_status = "unknown"
        self.playing_samples = 0
        self.idle_since = None
        self._emitted = {}
        self._restoring = False
        self.coordinator_enabled = False
        self.plan = None  # never stored: {plan_id, generation, situation, scenes, transition, valid_until}
        self.plan_generation = 0
        self.authority_epoch = uuid4().hex
        self.publisher = None
        self.claim_revision = 0
        self._accepted = None
        self._plan_lapsed = set()
        self.situation_revision = 0
        self.correction_stream_id = uuid4().hex
        self.correction_seq = 0
        self.last_correction = None

    def _changed(self):
        self.revision += 1
        # A fresh revision invalidates queued commands but retains signatures of
        # successfully dispatched desired states. Otherwise telemetry fights holds.

    def observe(self, entity_id, scene, now):
        if entity_id not in self.lights:
            return
        self.observed[entity_id] = scene
        self.normal.setdefault(entity_id, scene)

    def update_warmest(self, entity_id, kelvin):
        if entity_id in self.lights and type(kelvin) is int and 1000 <= kelvin <= 20000:
            self.warmest[entity_id] = kelvin

    def manual(self, entity_id, scene, now, *, update_normal=True):
        """Protect a light immediately. Only separately attributed actions can teach a preference."""
        if entity_id not in self.lights:
            return
        self.holds[entity_id] = Hold(scene, now)
        self.observed[entity_id] = scene
        if not self.gaming and update_normal:
            self.normal[entity_id] = scene
        self._emitted.pop(entity_id, None)
        self._changed()

    def correction_context(self, now, plan_now):
        if self.occupancy != "on" or not self.plan_fresh(plan_now):
            return None
        situation = self.plan["situation"]
        if situation.split("/")[0] not in {"gaming", "task", "sitting", "present"} or not self.fresh_game(now):
            return None
        if (situation.split("/")[0] == "gaming") != (self.game_status == "playing"):
            return None
        return self.authority_epoch, self.situation_revision, situation

    def begin_correction(self, entity_id, before, context_id, now, plan_now):
        """Reserve order at the action, including for corrections pending when Clear is pressed."""
        if self.correction_context(now, plan_now) is None or before is None:
            return None
        self.correction_seq += 1
        return {"seq": self.correction_seq, "entity_id": entity_id, "at": now,
                "actor": "owner", "context_id": context_id, "authority_epoch": self.authority_epoch,
                "before": asdict(before), "on": True, "occupied": True,
                "plan_id": self.plan["plan_id"], "situation": self.plan["situation"],
                "situation_revision": self.situation_revision}

    def confirm_correction(self, record, after):
        if self.last_correction is None or record["seq"] > self.last_correction["seq"]:
            self.last_correction = {**record, "after": asdict(after)}

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
        self.situation_revision += 1
        self._emitted.clear()
        self._changed()

    def set_enabled(self, enabled, now):
        enabled = bool(enabled)
        if enabled != self.enabled:
            self.enabled = enabled
            self.reset_authority()
        self._emitted.clear()
        self._changed()

    def set_coordinator(self, enabled, now):
        """The coordinator switch. Off, or on again, always forgets the last plan: it never comes back."""
        enabled = bool(enabled)
        if enabled == self.coordinator_enabled:
            return
        self.coordinator_enabled = enabled
        self.reset_authority()

    def reset_authority(self):
        self._replace_plan(None)
        self.authority_epoch = uuid4().hex
        self.publisher = None
        self.claim_revision = self.plan_generation = 0
        self._accepted = None

    def claim(self, epoch, publisher, claim_revision):
        if not (self.enabled and self.coordinator_enabled):
            raise ValueError("the coordinator switch is off")
        if epoch != self.authority_epoch:
            raise ValueError("stale coordinator epoch")
        if publisher == self.publisher and claim_revision in (self.claim_revision, self.claim_revision - 1):
            return
        if claim_revision != self.claim_revision:
            raise ValueError("stale coordinator claim")
        self.publisher = publisher
        self.claim_revision += 1
        self._accepted = None
        self._replace_plan(None)

    def check_publisher(self, epoch, publisher):
        if not (self.enabled and self.coordinator_enabled):
            raise ValueError("the coordinator switch is off")
        if epoch != self.authority_epoch or self.publisher is None or publisher != self.publisher:
            raise ValueError("stale coordinator authority")

    def apply_plan(self, plan_id, generation, valid_for_s, transition_s, situation, scenes, now, *,
                   epoch, publisher, release=False):
        """Accept one ordered update from the current publisher. Exact retries never extend its lease."""
        self.check_publisher(epoch, publisher)
        if type(generation) is not int or generation < self.plan_generation or generation < 1:
            raise ValueError("stale or invalid plan generation")
        if not isinstance(plan_id, str) or not 0 < len(plan_id) <= 64 or not isinstance(situation, str) or len(situation) > 32:
            raise ValueError("invalid plan id or situation")
        if not (PLAN_VALID_S[0] <= valid_for_s <= PLAN_VALID_S[1] and PLAN_TRANSITION_S[0] <= transition_s <= PLAN_TRANSITION_S[1]):
            raise ValueError("valid_for_s or transition_s out of range")
        if not isinstance(scenes, dict) or not set(scenes) <= set(self.lights):
            raise ValueError("a plan may only name this den's lights")
        if release and scenes:
            raise ValueError("a release must have empty scenes")
        loaded = {e: Scene.load(scene) for e, scene in scenes.items()}
        payload = (plan_id, valid_for_s, transition_s, situation, loaded, release)
        if generation == self.plan_generation:
            if payload != self._accepted:
                raise ValueError("conflicting plan generation")
            return
        if self.plan and not self.plan_fresh(now):
            self._replace_plan(None)
        self._replace_plan(None if release else {
            "plan_id": plan_id, "situation": situation, "scenes": loaded,
            "transition": float(transition_s), "valid_until": now + float(valid_for_s)})
        self.plan_generation = generation
        self._accepted = payload

    def _replace_plan(self, plan, *, lapse=False):
        previous, self.plan = self.plan, plan
        before = previous["scenes"] if previous else {}
        after = plan["scenes"] if plan else {}
        changed = {e for e in before.keys() | after.keys() if before.get(e) != after.get(e)}
        for e in changed:
            self._emitted.pop(e, None)
        if lapse:
            self._plan_lapsed.update(changed)
        if (previous and previous["situation"]) != (plan and plan["situation"]):
            self.situation_revision += 1
        if changed:
            self._changed()

    def plan_fresh(self, now):
        return bool(self.plan and self.enabled and self.coordinator_enabled and now < self.plan["valid_until"])

    def _plan_scene(self, entity_id, now):
        return self.plan["scenes"].get(entity_id) if self.plan_fresh(now) else None

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
            self._set_game_status("unknown")
            self.playing_samples = 0
            self.idle_since = None
            return
        if self.last_sample is not None and sampled_at <= self.last_sample:
            return
        if self.last_sample is not None and now - self.last_sample > GAMING_TTL:
            self.situation_revision += 1
        contiguous = self.last_sample is not None and sampled_at - self.last_sample <= GAMING_TTL
        if not contiguous:
            self.playing_samples = 0
            self.idle_since = None
        self.last_sample = sampled_at
        self._set_game_status(status if status in {"playing", "idle"} else "unknown")
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

    def _set_game_status(self, status):
        if status != self.game_status:
            self.situation_revision += 1
        self.game_status = status

    def _end_game(self):
        self.gaming = False
        self.normal.update(self.snapshot)
        self.snapshot = {}
        self.holds.clear()
        self._restoring = True
        self._emitted.clear()
        self._changed()

    def resume(self, entity_ids, now):
        for e in entity_ids if entity_ids is not None else self.lights:
            if e in self.lights:
                self.holds.pop(e, None)
                self._emitted.pop(e, None)
        self._changed()

    def fresh_game(self, now):
        return self.last_sample is not None and -5 <= now - self.last_sample <= GAMING_TTL and self.game_status in {"playing", "idle"}

    def desired(self, entity_id, now, plan_now=None):
        """Vacancy first, then the owner's hold, then a fresh coordinator plan (which bypasses gaming and its
        signal-loss rule for the lights it names), then gaming, then normal."""
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
        plan_now = now if plan_now is None else plan_now
        if hold and self.plan_fresh(plan_now):
            return hold.scene
        plan_scene = self._plan_scene(entity_id, plan_now)
        if plan_scene is not None:
            return plan_scene
        # Preserve an active game's scene through signal loss without making
        # ordinary occupancy arrivals depend on the optional gaming sensor.
        if self.gaming_enabled and self.gaming and not self.fresh_game(now):
            return None
        if hold:
            return hold.scene
        if self.gaming:
            return Scene(True, 38, self.warmest[entity_id])
        return self.normal.get(entity_id)

    def reconcile(self, now, plan_now=None):
        if not self.enabled:
            return []
        plan_now = now if plan_now is None else plan_now
        if (not self.gaming and self.occupancy == "off" and self.vacant_since is not None):
            expired = [e for e, h in self.holds.items()
                       if now - max(self.vacant_since, h.since) >= VACANCY_SECONDS]
            for e in expired:
                self.holds.pop(e)
            if expired:
                self._changed()
        if self.plan and plan_now >= self.plan["valid_until"]:
            self._replace_plan(None, lapse=True)
        commands = []
        for e in self.lights:
            target = self.desired(e, now, plan_now)
            if target is None or self._emitted.get(e) == target:
                continue
            if target.matches(self.observed.get(e)):
                self._emitted[e] = target
                continue
            self._emitted[e] = target
            plan_scene = self._plan_scene(e, plan_now)
            if plan_scene is not None and target == plan_scene and e not in self.holds:
                commands.append(Command(e, target, self.plan["transition"], self.revision,
                                        self.plan["plan_id"], self.plan_generation))
                continue
            restoring = (self._restoring or e in self._plan_lapsed) and target.on
            commands.append(Command(e, target, RESTORE_TRANSITION if restoring else 2, self.revision))
        self._plan_lapsed.clear()
        return commands

    def command_failed(self, entity_id):
        self._emitted.pop(entity_id, None)

    def dump(self):
        return {"version": 1, "enabled": self.enabled, "gaming_enabled": self.gaming_enabled,
                "coordinator_enabled": self.coordinator_enabled, "correction_seq": self.correction_seq,
                "correction_stream_id": self.correction_stream_id, "last_correction": self.last_correction,
                "gaming": self.gaming, "normal": {e: asdict(s) for e, s in self.normal.items()},
                "snapshot": {e: asdict(s) for e, s in self.snapshot.items()},
                "holds": {e: {"scene": asdict(h.scene), "since": h.since} for e, h in self.holds.items()}}

    @classmethod
    def restore(cls, data, lights=LIGHTS):
        """1.0.4's stored data loads unchanged; 1.0.4 ignores 1.1's extra keys, so rolling back is safe.
        Holds and scenes for a light no longer in this den's list are dropped."""
        c = cls(lights)
        if not data:
            return c
        if data.get("version") != 1:
            raise ValueError("unsupported saved controller version")
        for key in ("enabled", "gaming_enabled", "gaming"):
            if type(data.get(key)) is not bool:
                raise ValueError("invalid saved controller flags")
            setattr(c, key, data[key])
        c.coordinator_enabled = data.get("coordinator_enabled") is True
        seq = data.get("correction_seq", 0)
        c.correction_seq = seq if type(seq) is int and seq >= 0 else 0
        stream_id = data.get("correction_stream_id")
        if isinstance(stream_id, str) and stream_id:
            c.correction_stream_id = stream_id
        last = data.get("last_correction")
        if (isinstance(last, dict) and last.get("entity_id") in lights
                and type(last.get("seq")) is int and 0 < last["seq"] <= c.correction_seq
                and type(last.get("at")) in (int, float) and math.isfinite(last["at"])
                and isinstance(last.get("authority_epoch"), str)
                and type(last.get("situation_revision")) is int and isinstance(last.get("situation"), str)):
            c.last_correction = last
        for key in ("normal", "snapshot"):
            setattr(c, key, {e: Scene.load(s) for e, s in data.get(key, {}).items() if e in c.lights})
        for e, hold in data.get("holds", {}).items():
            if e in c.lights:
                since = hold["since"]
                if type(since) not in (int, float) or not math.isfinite(since):
                    raise ValueError("invalid saved hold time")
                c.holds[e] = Hold(Scene.load(hold["scene"]), since)
        return c

    def coordinator_status(self, plan_now):
        return {"enabled": self.coordinator_enabled, "active": self.enabled and self.coordinator_enabled,
                "epoch": self.authority_epoch, "publisher": self.publisher, "claim_revision": self.claim_revision,
                "generation": self.plan_generation, "fresh": self.plan_fresh(plan_now), "lights": list(self.lights),
                "warmest": dict(self.warmest),
                "plan_id": self.plan and self.plan["plan_id"], "situation": self.plan and self.plan["situation"],
                "entities": sorted(self.plan["scenes"]) if self.plan else []}

    def status(self, now, plan_now=None):
        plan_now = now if plan_now is None else plan_now
        record = self.last_correction
        if record and self.correction_context(now, plan_now) != (
                record["authority_epoch"], record["situation_revision"], record["situation"]):
            record = None
        return {"automatic": self.enabled, "gaming_enabled": self.gaming_enabled,
                "gaming_session": self.gaming, "gaming_signal": self.game_status if self.fresh_game(now) else "unknown",
                "gaming_sampled_at": self.last_sample,
                "occupancy": self.occupancy, "revision": self.revision,
                "holds": {e: {"until": "game_ends" if self.gaming else "room_vacant_2_minutes",
                              "target": asdict(h.scene)} for e, h in self.holds.items()},
                "coordinator": self.coordinator_status(plan_now),
                "correction_stream_id": self.correction_stream_id,
                "correction_watermark": self.correction_seq,
                "last_correction": {**record, "age_s": now - record["at"]} if record else None}
