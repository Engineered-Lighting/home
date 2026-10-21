"""Event validation (schema activity-lighting-events-1) and the input store.

The store only records facts: the latest observation per input, plus the few
timestamps the timed rules need (when the room/zone was last occupied and the
current reading run). The rules read it; they never change it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import EVENTS_SCHEMA, BOUND_EVENTS_SCHEMA
from .geometry import ZONE_NAMES, Geometry
from .settings import Settings

EVENT_TYPES = ("occupancy", "zone", "activity", "person_pose", "person_position", "health", "cost")
ACTIVITY_LABELS = ("reading", "computer_work", "phone_use", "resting", "transition", "other",
                   "insufficient_evidence")
HEALTH_COMPONENTS = ("jev", "deep_stack", "frigate", "camera")
MAX_EVENTS_PER_BATCH = 200
MAX_PEOPLE_PER_POSE = 16
MAX_TTL_S = 3600.0
COST_WINDOW_S = 3600.0
READER_KEYS = ('session_id', 'generation', 'capture_epoch', 'engine_epoch', 'reader_epoch')
BOUND_FIELDS = {'evidence_version', 'binding', 'valid_until'}


def reader_key(event):
    binding = event.get('binding') if isinstance(event, dict) and event.get('evidence_version') == 2 else None
    return tuple(binding.get(k) for k in READER_KEYS) if isinstance(binding, dict) else None


def _check_bound(event):
    if event.get('evidence_version') != 2 or not _number(event.get('valid_until')):
        return 'v2 activity/pose needs evidence_version:2 and original valid_until'
    if (event['valid_until'] <= event['observed_at']
            or event['observed_at'] + event['ttl_s'] > event['valid_until'] + 1e-6
            or event['ttl_s'] > (12 if event['type'] == 'activity' else 10)):
        return 'bound evidence cannot extend original validity'
    b = event.get('binding')
    if b is None:
        return None  # explicit retirement, no geometry or subject assertion
    keys = {'schema', 'status', 'reason', *READER_KEYS, 'interval_start', 'instance_id',
            'source_frame_id', 'source_request_id', 'source_event_id', 'observed_at', 'available_at', 'valid_until'}
    if (not isinstance(b, dict) or set(b) != keys or b.get('schema') != 'den-sole-reader-source-1'
            or b.get('status') != 'available' or b.get('reason') is not None
            or any(not _str(b.get(k), 256) for k in ('session_id', 'capture_epoch', 'engine_epoch', 'reader_epoch',
                                                   'instance_id', 'source_frame_id', 'source_request_id'))
            or type(b.get('generation')) is not int or b['generation'] < 1
            or type(b.get('source_event_id')) is not int or b['source_event_id'] < 1
            or not all(_number(b.get(k)) for k in ('interval_start', 'observed_at', 'available_at', 'valid_until'))
            or not b['interval_start'] <= b['observed_at'] <= b['available_at'] < b['valid_until']
            or event['observed_at'] != b['observed_at'] or event['valid_until'] > b['valid_until']
            or event.get('person_count') != 1):
        return 'bound sole-reader source invalid'
    return None


class EventError(Exception):
    """Refused batch. status 422 for schema problems, as in the contract."""

    def __init__(self, status: int, error: str, detail: str, index: int | None = None):
        super().__init__(detail)
        self.status, self.error, self.detail, self.index = status, error, detail, index

    def to_json(self) -> dict:
        body = {"ok": False, "error": self.error, "detail": self.detail}
        if self.index is not None:
            body["index"] = self.index
        return body


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _int_or_none(value) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)


def _str(value, max_len: int = 160) -> bool:
    return isinstance(value, str) and 0 < len(value) <= max_len


def _vec_or_none(value) -> bool:
    return value is None or (isinstance(value, list) and len(value) == 3 and all(_number(v) for v in value))


def _check_type_fields(event: dict) -> str | None:
    kind = event["type"]
    if kind == "occupancy":
        if not isinstance(event.get("present"), bool):
            return "present must be a boolean"
        if not _int_or_none(event.get("people_count")):
            return "people_count must be a non-negative int or null"
    elif kind == "zone":
        if event.get("zone") not in ZONE_NAMES:
            return "zone must be desk or sofa"
        if not isinstance(event.get("present"), bool):
            return "present must be a boolean"
        if not _int_or_none(event.get("people_count")):
            return "people_count must be a non-negative int or null"
    elif kind == "activity":
        if event.get("label") not in ACTIVITY_LABELS:
            return "label is not a known activity label"
        confidence = event.get("confidence")
        if confidence is not None and not (_number(confidence) and 0.0 <= confidence <= 1.0):
            return "confidence must be within 0..1 or null"
        if not _int_or_none(event.get("person_count")):
            return "person_count must be a non-negative int or null"
        if not _str(event.get("model")):
            return "model is required"
        if not isinstance(event.get("facts_key"), str) or len(event["facts_key"]) > 256:
            return "facts_key must be a string"
        if event.get("zone_hint") not in (None, *ZONE_NAMES):
            return "zone_hint must be desk, sofa or null"
    elif kind == "person_pose":
        people = event.get("people")
        if not isinstance(people, list) or len(people) > MAX_PEOPLE_PER_POSE:
            return "people must be a list"
        if not _int_or_none(event.get("person_count")) or event.get("person_count") is None:
            return "person_count must be a non-negative int"
        for person in people:
            shoulders = person.get("shoulders_m") if isinstance(person, dict) else None
            if (not isinstance(person, dict) or not isinstance(person.get("track_id"), (str, int))
                    or not isinstance(shoulders, dict)
                    or not _vec_or_none(shoulders.get("left")) or not _vec_or_none(shoulders.get("right"))
                    or not _vec_or_none(person.get("head_m"))
                    or not (person.get("head_pitch_deg") is None or _number(person.get("head_pitch_deg")))
                    or person.get("approximate") is not True):
                return "people[] entries need track_id, shoulders_m{left,right}, head_m, head_pitch_deg, approximate:true"
    elif kind == "person_position":
        people = event.get("people")
        if not isinstance(people, list) or len(people) > MAX_PEOPLE_PER_POSE:
            return "people must be a list"
        if not _int_or_none(event.get("person_count")) or event.get("person_count") is None:
            return "person_count must be a non-negative int"
        for person in people:
            if (not isinstance(person, dict) or not isinstance(person.get("track_id"), (str, int))
                    or person.get("position_m") is None or not _vec_or_none(person.get("position_m"))
                    or person.get("approximate") is not True):
                return "people[] entries need track_id, position_m [x,y,z] and approximate:true"
    elif kind == "health":
        if event.get("component") not in HEALTH_COMPONENTS:
            return "component must be jev, deep_stack, frigate or camera"
        if not isinstance(event.get("ok"), bool):
            return "ok must be a boolean"
        if event.get("detail") is not None and not isinstance(event.get("detail"), str):
            return "detail must be a string or null"
    elif kind == "cost":
        if event.get("component") != "jev":
            return "component must be jev"
        if not _int_or_none(event.get("calls")) or event.get("calls") is None:
            return "calls must be a non-negative int"
        usd = event.get("est_usd")
        if usd is not None and not (_number(usd) and usd >= 0):
            return "est_usd must be a non-negative number or null"
    return None


def validate_batch(body, home: str, geometry: Geometry) -> list[dict]:
    """Whole-batch validation: one bad event refuses the batch (nothing is
    applied), so a producer never half-updates the brain."""
    if not isinstance(body, dict) or body.get("schema") not in (EVENTS_SCHEMA, BOUND_EVENTS_SCHEMA):
        raise EventError(422, "schema_invalid", f"body.schema must be {EVENTS_SCHEMA} or {BOUND_EVENTS_SCHEMA}")
    events = body.get("events")
    if not isinstance(events, list) or not events:
        raise EventError(422, "events_invalid", "body.events must be a non-empty list")
    if len(events) > MAX_EVENTS_PER_BATCH:
        raise EventError(422, "events_invalid", f"at most {MAX_EVENTS_PER_BATCH} events per batch")
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise EventError(422, "event_invalid", "event must be an object", index)
        if event.get("type") not in EVENT_TYPES:
            raise EventError(422, "unknown_event_type", f"unknown event type {event.get('type')!r}", index)
        if event.get("home") != home:
            raise EventError(422, "event_invalid", "event.home must match the route home", index)
        if event.get("room") != geometry.room:
            raise EventError(422, "event_invalid", f"event.room must be {geometry.room!r}", index)
        if not _str(event.get("source")):
            raise EventError(422, "event_invalid", "event.source is required", index)
        if not _number(event.get("observed_at")) or not _number(event.get("sent_at")):
            raise EventError(422, "event_invalid", "observed_at and sent_at must be Unix seconds", index)
        ttl = event.get("ttl_s")
        if not _number(ttl) or ttl <= 0 or ttl > MAX_TTL_S:
            raise EventError(422, "event_invalid", f"ttl_s must be within (0, {MAX_TTL_S:g}]", index)
        problem = _check_type_fields(event)
        if body['schema'] == EVENTS_SCHEMA and BOUND_FIELDS.intersection(event):
            problem = 'bound fields require activity-lighting-events-2 (no silent legacy downgrade)'
        elif body['schema'] == BOUND_EVENTS_SCHEMA and event['type'] in ('activity', 'person_pose'):
            problem = problem or _check_bound(event)
        if problem:
            raise EventError(422, "event_invalid", f"{event['type']}: {problem}", index)
    return events


@dataclass(frozen=True)
class Obs:
    observed_at: float
    ttl_s: float
    data: dict

    @property
    def expires_at(self) -> float:
        return min(self.observed_at + self.ttl_s, self.data.get('valid_until', math.inf))

    def fresh(self, now: float) -> bool:
        return now < self.expires_at if self.data.get('evidence_version') == 2 else now <= self.expires_at

    def age(self, now: float) -> float:
        return max(0.0, now - self.observed_at)


@dataclass
class ReadingRun:
    """An unbroken run of `reading` answers from JEV."""
    since: float
    last_at: float
    last_ttl_s: float
    ended_at: float | None = None

    def end(self, now: float) -> float:
        if self.ended_at is not None:
            return self.ended_at
        return min(now, self.last_at + self.last_ttl_s)


@dataclass
class Inputs:
    occupancy: Obs | None = None
    occupancy_last_present_at: float | None = None
    zones: dict[str, Obs] = field(default_factory=dict)
    zone_last_present_at: dict[str, float] = field(default_factory=dict)
    activity: Obs | None = None
    reading: ReadingRun | None = None
    pose: Obs | None = None
    position: Obs | None = None
    health: dict[str, Obs] = field(default_factory=dict)
    cost: list[tuple[float, int, float | None]] = field(default_factory=list)
    latest: dict[str, float] = field(default_factory=dict)
    bound_reader: tuple | None = None
    # Newest activity/pose answer that saw at least one person (AL-3 evidence,
    # independent of the Frigate bridge).
    last_person_evidence_at: float | None = None

    def apply(self, event: dict, now: float, settings: Settings) -> str | None:
        """Apply one validated event. Returns why it was ignored, or None."""
        observed = float(event["observed_at"])
        ttl = float(event["ttl_s"])
        expiry = min(observed + ttl, event.get('valid_until', math.inf))
        if now > expiry or event.get('evidence_version') == 2 and now == expiry:
            return "expired"
        if observed > now + settings.max_future_skew_s:
            return "future"
        kind = event["type"]
        if kind == "cost":
            self.cost.append((observed, int(event["calls"]), event.get("est_usd")))
            self.cost = [c for c in self.cost if c[0] >= now - 2 * COST_WINDOW_S]
            return None
        key = kind
        if kind == "zone":
            key = f"zone:{event['zone']}"
        elif kind == "health":
            key = f"health:{event['component']}"
        if observed < self.latest.get(key, -math.inf):
            return "stale"
        self.latest[key] = observed
        obs = Obs(observed, ttl, event)
        if kind in ("activity", "person_pose", "person_position") and (event.get("person_count") or 0) >= 1:
            last = self.last_person_evidence_at
            self.last_person_evidence_at = observed if last is None else max(last, observed)
        if kind == "occupancy":
            self.occupancy_last_present_at = _last_present(self.occupancy, obs, self.occupancy_last_present_at)
            self.occupancy = obs
        elif kind == "zone":
            zone = event["zone"]
            self.zone_last_present_at[zone] = _last_present(
                self.zones.get(zone), obs, self.zone_last_present_at.get(zone))
            self.zones[zone] = obs
        elif kind == "activity":
            self._track_reading(obs, settings)
            self.activity = obs
        elif kind == "person_pose":
            key = reader_key(event)
            if key is None or event.get('person_count') != 1 or not event.get('people'):
                self.reading, self.bound_reader = None, None
            elif self.bound_reader != key:
                if self.activity is None or reader_key(self.activity.data) != key:
                    self.reading = None
                self.bound_reader = key
            self.pose = obs
        elif kind == "person_position":
            self.position = obs
        elif kind == "health":
            self.health[event["component"]] = obs
            if event['component'] in ('deep_stack', 'camera') and event['ok'] is False:
                self.reading = None
        return None

    def _track_reading(self, obs: Obs, settings: Settings) -> None:
        data = obs.data
        confidence = data.get("confidence")
        key = reader_key(data)
        if (key is None or data.get('person_count') != 1
                or self.bound_reader is not None and key != self.bound_reader
                or data['label'] == 'insufficient_evidence'):
            self.reading = None
            return
        is_reading = data["label"] == "reading" and (confidence is None or confidence >= settings.reading_min_confidence)
        run = self.reading
        if is_reading:
            if run is None or run.ended_at is not None or obs.observed_at > run.last_at + run.last_ttl_s:
                self.reading = ReadingRun(obs.observed_at, obs.observed_at, obs.ttl_s)
            else:
                run.last_at, run.last_ttl_s = obs.observed_at, obs.ttl_s
        elif run is not None and run.ended_at is None:
            run.ended_at = min(obs.observed_at, run.last_at + run.last_ttl_s)


def _last_present(previous: Obs | None, obs: Obs, last: float | None) -> float | None:
    """When the room (or a zone) was last believed occupied.

    A `present` observation moves it forward. The first `absent` observation
    after a fresh `present` marks the moment the person left; later absent
    heartbeats don't move it, so the off timer keeps running."""
    t = obs.observed_at
    if obs.data["present"]:
        return t if last is None else max(last, t)
    if previous is not None and previous.data["present"] and t <= previous.expires_at:
        return t if last is None else max(last, t)
    return last
