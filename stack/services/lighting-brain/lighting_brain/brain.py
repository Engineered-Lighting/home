"""The stateful shell around the pure rules: ingest, overrides, decision log,
the contract state document, and change notification for the SSE stream.

Every public method takes the time from the injected clock, so tests drive a
fake clock and never sleep.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import Counter, deque
from typing import Callable

from . import OVERRIDE_SCHEMA, STATE_SCHEMA
from .geometry import Geometry
from .inputs import EventError, Inputs, validate_batch, reader_key
from .rules import RULE_ORDER, Evaluation, FixtureOutput, Override, evaluate, held_output, short_name
from .settings import Settings

log = logging.getLogger("lighting_brain")

OVERRIDE_REASONS = ("change", "aim", "undo", "redo")
# Fields that change every second without anything meaningful changing; they
# don't count as a "change" for the stream (the 5 s push still carries them).
VOLATILE = ("generated_at", "state_revision", "reading_evidence")


def _r(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def _fixture_json(fo: FixtureOutput) -> dict:
    return {
        "fixture_id": fo.fixture_id,
        "ambient": {"radial_level": fo.ambient.radial_level, "down_level": fo.ambient.down_level,
                    "cct_k": fo.ambient.cct_k, "fade_s": fo.ambient.fade_s},
        "spot": {"on": fo.spot.on, "level": fo.spot.level, "cct_k": fo.spot.cct_k,
                 "target_m": list(fo.spot.target_m) if fo.spot.target_m else None, "fade_s": fo.spot.fade_s},
        "paused_until": fo.paused_until,
        "pause_reason": fo.pause_reason,
    }


def _ambient_text(fo: FixtureOutput) -> str:
    a = fo.ambient
    if a.radial_level <= 0 and a.down_level <= 0:
        return f"{short_name(fo.fixture_id)} ambient off"
    return f"{short_name(fo.fixture_id)} ambient {a.radial_level:.0%} {a.cct_k} K"


def decision_inputs(evaluation: Evaluation) -> dict:
    f = evaluation.facts
    zones = [z for z in ("sofa", "desk") if f.zones_present.get(z)]
    inputs = {
        "occupied": f.room_present,
        "zone": "+".join(zones) if zones else None,
        "people": f.people,
        "activity": f.activity_label,
        "jev": f.health["jev"],
        "deep_stack": f.health["deep_stack"],
        "night": f.night,
        "positions": len(f.positions),
        "nearest": evaluation.nearest_fixture,
    }
    plan = evaluation.spot_plan
    if plan is not None and plan.ok:
        inputs.update({"fixture": plan.fixture_id, "pan_deg": _r(plan.pan_deg, 1), "tilt_deg": _r(plan.tilt_deg, 1)})
    return inputs


def diff_decision(previous: Evaluation | None, new: Evaluation, notes: dict) -> dict | None:
    """One decision per evaluation that changed something visible. It is
    attributed to the most significant rule among the changes."""
    if previous is None:
        return {"at": new.at, "rule": new.mode_rule, "why": f"Brain started · {new.why}",
                "inputs": decision_inputs(new), "change": f"start · mode {new.mode}"}
    changes: list[tuple[str, str]] = []
    if previous.mode != new.mode:
        changes.append((new.mode_rule, f"mode {previous.mode} → {new.mode}"))
    for fo in new.fixtures:
        po = previous.fixture(fo.fixture_id)
        name = short_name(fo.fixture_id)
        if po is None:
            continue
        if (po.paused_until is None) != (fo.paused_until is None):
            changes.append(("manual_pause", f"{name} paused" if fo.paused_until else f"{name} back to Auto"))
        if fo.paused_until is not None:
            continue
        pa, na = po.ambient, fo.ambient
        # Proximity levels move continuously as people walk; the log records
        # ambient switching on/off, changing colour or changing rule, not
        # every step (the state and stream carry the levels).
        if ((pa.radial_level > 0) != (na.radial_level > 0) or po.ambient_rule != fo.ambient_rule
                or (na.radial_level > 0 and pa.cct_k != na.cct_k)):
            changes.append((fo.ambient_rule, _ambient_text(fo)))
        if po.spot.on != fo.spot.on:
            changes.append((fo.spot_rule, f"{name} spot {'on' if fo.spot.on else 'off'}"))
    if (previous.nearest_fixture != new.nearest_fixture and new.nearest_fixture is not None
            and new.mode_rule == "proximity_ambient"):
        changes.append(("proximity_ambient", f"nearest {short_name(new.nearest_fixture)}"))
    for fid in notes:
        if not any(text.startswith(short_name(fid)) and rule == "manual_pause" for rule, text in changes):
            changes.append(("manual_pause", f"{short_name(fid)} pause updated"))
    if not changes:
        return None
    rule = min(changes, key=lambda c: RULE_ORDER.index(c[0]))[0]
    if rule == "manual_pause" and notes:
        why = "; ".join(notes.values())
    else:
        why = new.rule_whys.get(rule) or new.why
    return {"at": new.at, "rule": rule, "why": why, "inputs": decision_inputs(new),
            "change": "; ".join(text for _, text in changes)}


class Brain:
    def __init__(self, geometry: Geometry, settings: Settings, clock: Callable[[], float] = time.time):
        self.geometry = geometry
        self.settings = settings
        self.clock = clock
        self.inputs = Inputs()
        self.overrides: dict[str, Override] = {}
        self.decisions: deque = deque(maxlen=settings.decision_log_size)
        self.evaluation: Evaluation | None = None
        self.version = 0
        self.instance_id = uuid.uuid4().hex
        self.decision_sequence = 0
        self.decision_id = None
        self._desired_signature = None
        self.started_at = clock()
        self.counts: Counter = Counter()
        self._signature: str | None = None
        self._pushed_at: float | None = None
        self._notes: dict[str, str] = {}
        self._cond = threading.Condition(threading.RLock())
        self._closed = False
        self.tick()

    # ---------------------------------------------------------- writes

    def _check_home(self, home: str) -> None:
        if home != self.geometry.home:
            raise EventError(404, "unknown_home", f"this brain serves {self.geometry.home!r}")

    def ingest(self, home: str, body, allowed_types: frozenset | None = None) -> dict:
        with self._cond:
            self._check_home(home)
            now = self.clock()
            events = validate_batch(body, home, self.geometry)
            if allowed_types is not None:
                for index, event in enumerate(events):
                    if event["type"] not in allowed_types:
                        raise EventError(403, "producer_type_forbidden",
                                         f"this producer may not send {event['type']!r}", index)
            ignored = []
            for index, event in enumerate(events):
                reason = self.inputs.apply(event, now, self.settings)
                if reason:
                    self.counts[f"ignored_{reason}"] += 1
                    ignored.append({"index": index, "type": event["type"], "reason": reason})
                    log.info("ignored %s event from %s: %s", event["type"], event["source"], reason)
                else:
                    self.counts["accepted"] += 1
            self._tick_locked(now)
            return {"ok": True, "accepted": len(events) - len(ignored), "ignored": ignored}

    def set_override(self, home: str, body) -> dict:
        with self._cond:
            self._check_home(home)
            if not isinstance(body, dict) or body.get("schema") != OVERRIDE_SCHEMA:
                raise EventError(422, "schema_invalid", f"body.schema must be {OVERRIDE_SCHEMA}")
            fid = body.get("fixture_id")
            if fid not in self.geometry.fixture_ids:
                raise EventError(422, "unknown_fixture", f"unknown fixture_id {fid!r}")
            if body.get("reason") not in OVERRIDE_REASONS:
                raise EventError(422, "override_invalid", "reason must be change, aim, undo or redo")
            at = body.get("at")
            if not isinstance(at, (int, float)) or isinstance(at, bool):
                raise EventError(422, "override_invalid", "at must be Unix seconds")
            now = self.clock()
            existing = self.overrides.get(fid)
            held = existing.held if existing else held_output(self.evaluation, fid)
            # The pause runs from when the brain hears about it, so a skewed
            # client clock can't shorten or stretch it.
            until = now + self.settings.pause_max_s
            self.overrides[fid] = Override(fid, body["reason"], float(at), until, held)
            self._notes[fid] = f"Manual {body['reason']} in Home · {short_name(fid)} paused"
            self._tick_locked(now)
            return {"ok": True, "fixture_id": fid, "paused_until": until}

    def clear_override(self, home: str, fixture_id: str) -> dict:
        with self._cond:
            self._check_home(home)
            if fixture_id not in self.geometry.fixture_ids:
                raise EventError(404, "unknown_fixture", f"unknown fixture_id {fixture_id!r}")
            removed = self.overrides.pop(fixture_id, None)
            if removed:
                self._notes[fixture_id] = f"{short_name(fixture_id)} back to Auto · cleared in Home"
            self._tick_locked(self.clock())
            return {"ok": True, "fixture_id": fixture_id, "cleared": removed is not None}

    def tick(self) -> Evaluation:
        with self._cond:
            return self._tick_locked(self.clock())

    def _tick_locked(self, now: float) -> Evaluation:
        new = evaluate(self.inputs, self.overrides, self.geometry, self.settings, now, self.evaluation)
        for fid, why in new.ended_overrides:
            self.overrides.pop(fid, None)
            self._notes[fid] = why
        decision = diff_decision(self.evaluation, new, self._notes)
        self._notes = {}
        if decision:
            self.decisions.appendleft(decision)
            log.info("%s: %s (%s)", decision["rule"], decision["why"], decision["change"])
        self.evaluation = new
        desired = json.dumps([new.mode, [_fixture_json(fo) for fo in new.fixtures]], sort_keys=True)
        if desired != self._desired_signature:
            self._desired_signature = desired
            self.decision_sequence += 1
            self.decision_id = f'{self.instance_id}:{self.decision_sequence}'
        signature = json.dumps(_strip_volatile(self._state_locked(now)), sort_keys=True)
        # At most max_push_hz visible changes per second: a change inside the
        # interval waits for a later ingest or tick (tick_s <= the interval).
        interval = 1.0 / self.settings.max_push_hz
        due = self._pushed_at is None or now - self._pushed_at >= interval - 1e-6
        if signature != self._signature and due:
            self._signature = signature
            self._pushed_at = now
            self.version += 1
            self._cond.notify_all()
        return new

    # ---------------------------------------------------------- reads

    def state(self, home: str | None = None) -> dict:
        with self._cond:
            if home is not None:
                self._check_home(home)
            return self._state_locked(self.clock())

    def snapshot(self) -> tuple[int, dict]:
        with self._cond:
            return self.version, self._state_locked(self.clock())

    def wait_for_change(self, seen_version: int, timeout: float) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: self.version != seen_version or self._closed, timeout)

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    @property
    def closed(self) -> bool:
        return self._closed

    def health(self) -> dict:
        with self._cond:
            return {"ok": True, "home": self.geometry.home, "room": self.geometry.room,
                    "events_schemas": ["activity-lighting-events-1", "activity-lighting-events-2"],
                    "reading_requires_bound_evidence": True,
                    "settings_version": self.settings.settings_version,
                    "synthetic_geometry": self.geometry.synthetic,
                    "uptime_s": round(self.clock() - self.started_at, 1), "events": dict(self.counts)}

    def _state_locked(self, now: float) -> dict:
        ev = self.evaluation
        f = ev.facts
        inputs = self.inputs
        occ = inputs.occupancy
        return {
            "schema": STATE_SCHEMA,
            "home": self.geometry.home,
            "room": self.geometry.room,
            "generated_at": now,
            "state_revision": self.version,
            "decision_id": self.decision_id,
            "reading_evidence": {
                "bound": bool(inputs.activity and inputs.pose and inputs.activity.fresh(now) and inputs.pose.fresh(now)
                              and reader_key(inputs.activity.data) is not None
                              and reader_key(inputs.activity.data) == reader_key(inputs.pose.data)),
                "activity": inputs.activity.data.get('binding') if inputs.activity else None,
                "pose": inputs.pose.data.get('binding') if inputs.pose else None,
                "activity_valid_until": inputs.activity.expires_at if inputs.activity else None,
                "pose_valid_until": inputs.pose.expires_at if inputs.pose else None,
            },
            "brain": "online",
            "mode": ev.mode,
            "why": ev.why,
            "occupancy": {
                "present": f.room_present,
                "people_count": f.people if f.room_present else (0 if occ and occ.fresh(now) else None),
                "zones": {"sofa": f.zones_present["sofa"], "desk": f.zones_present["desk"]},
            },
            "activity": {"label": f.activity_label, "confidence": f.activity_confidence,
                         "age_s": _r(f.activity_age, 1), "source": "jev"},
            "fixtures": [_fixture_json(fo) for fo in ev.fixtures],
            "decisions": list(self.decisions),
            "cost": {"jev_calls_last_hour": f.jev_calls_last_hour, "est_usd_last_hour": f.est_usd_last_hour},
            "inputs": {
                "occupancy_age_s": _r(occ.age(now), 1) if occ else None,
                "activity_age_s": _r(f.activity_age, 1),
                "pose_age_s": _r(f.pose_age, 1),
                "position_age_s": _r(f.position_age, 1),
                "at_sofa": f.at_sofa,
                "health": dict(f.health),
            },
            "settings_version": self.settings.settings_version,
        }


def _strip_volatile(state: dict) -> dict:
    stripped = {k: v for k, v in state.items() if k not in VOLATILE}
    stripped["activity"] = {k: v for k, v in state["activity"].items() if k != "age_s"}
    stripped["inputs"] = {k: v for k, v in state["inputs"].items() if not k.endswith("_age_s")}
    return stripped


def sse_frame(state: dict, event_id: int) -> bytes:
    """One Server-Sent Events message carrying the full state. The JSON is
    compact, so it is always a single `data:` line."""
    data = json.dumps(state, separators=(",", ":"), ensure_ascii=False)
    return f"id: {event_id}\nevent: state\ndata: {data}\n\n".encode("utf-8")


def stream_frames(brain: Brain, period_s: float, stop: threading.Event, wait=None):
    """Full state now, then again on every change or after period_s without
    one. `wait(version, timeout)` is injectable so tests need no sleeps."""
    wait = wait or brain.wait_for_change
    event_id = 0
    while not stop.is_set() and not brain.closed:
        version, state = brain.snapshot()
        event_id += 1
        yield sse_frame(state, event_id)
        wait(version, period_s)
