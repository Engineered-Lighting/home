import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lighting_brain.brain import Brain  # noqa: E402
from lighting_brain.geometry import load_geometry  # noqa: E402
from lighting_brain.settings import Settings  # noqa: E402

EXAMPLE_GEOMETRY = ROOT / "config" / "geometry.example.json"
# Noon UTC; tests run the night window in UTC so they need no zone database.
NOON = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc).timestamp()
HOME, ROOM = "victoria", "den"
F01 = "victoria-den-proposed-fixture-01"
F02 = "victoria-den-proposed-fixture-02"
F03 = "victoria-den-mount-03-virtual-fixture"
F04 = "victoria-den-mount-04-virtual-fixture"

# A seated reader on the synthetic sofa, facing +y (away from the camera).
LEFT, RIGHT = [1.42, 0.75, 0.95], [1.78, 0.75, 0.95]
HEAD = [1.6, 0.78, 1.18]


class FakeClock:
    def __init__(self, t: float = NOON):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def set(self, t: float) -> float:
        self.t = t
        return t

    def tick(self, dt: float) -> float:
        self.t += dt
        return self.t


def test_settings(**overrides) -> Settings:
    return Settings(**{"timezone": "UTC", **overrides})


test_settings.__test__ = False  # a helper, not a test


@pytest.fixture
def geometry():
    return load_geometry(EXAMPLE_GEOMETRY)


@pytest.fixture
def settings():
    return test_settings()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def brain(geometry, settings, clock):
    return Brain(geometry, settings, clock)


# ------------------------------------------------------------ event builders

DEFAULT_TTL = {"occupancy": 90, "zone": 90, "activity": 12, "person_pose": 10, "person_position": 3,
               "health": 150, "cost": 300}


def event(kind, t, ttl=None, **fields):
    result = {"type": kind, "home": HOME, "room": ROOM, "source": "test", "observed_at": t, "sent_at": t,
              "ttl_s": DEFAULT_TTL[kind] if ttl is None else ttl, **fields}
    if kind in ('activity', 'person_pose'):
        until = t + result['ttl_s']
        binding = dict(schema='den-sole-reader-source-1', status='available', reason=None,
            session_id='synthetic-session', generation=1, capture_epoch='synthetic-camera', engine_epoch='synthetic-engine',
            reader_epoch='synthetic-reader', interval_start=NOON - 10000, instance_id='synthetic-person',
            source_frame_id=f'frame-{t}', source_request_id=f'request-{t}', source_event_id=int(t * 10),
            observed_at=t, available_at=t, valid_until=until) if fields.get('person_count') == 1 else None
        result.update(evidence_version=2, valid_until=until, binding=binding)
    return result


def occupancy(t, present=True, people=1, **kw):
    return event("occupancy", t, present=present, people_count=people if present or people is None else 0, **kw)


def zone(t, name="sofa", present=True, people=1, **kw):
    return event("zone", t, zone=name, present=present, people_count=people if present else 0, **kw)


def activity(t, label="reading", confidence=0.8, persons=1, zone_hint="sofa", **kw):
    return event("activity", t, label=label, confidence=confidence, person_count=persons, model="jev-1.13.0",
                 facts_key="test", zone_hint=zone_hint, **kw)


def person(left=LEFT, right=RIGHT, head=HEAD, pitch=20.0, track_id="t1"):
    return {"track_id": track_id, "shoulders_m": {"left": left, "right": right}, "head_m": head,
            "head_pitch_deg": pitch, "approximate": True}


def pose(t, people=None, count=None, **kw):
    people = [person()] if people is None else people
    return event("person_pose", t, people=people, person_count=len(people) if count is None else count, **kw)


def position(t, *points, count=None, **kw):
    people = [{"track_id": f"slot-{i}", "position_m": list(p), "approximate": True} for i, p in enumerate(points)]
    return event("person_position", t, people=people, person_count=len(people) if count is None else count, **kw)


def health(t, component, ok=True, detail=None, **kw):
    return event("health", t, component=component, ok=ok, detail=detail, **kw)


def cost(t, calls, usd=None, **kw):
    return event("cost", t, component="jev", calls=calls, est_usd=usd, **kw)


def batch(*events):
    return {"schema": "activity-lighting-events-2" if any(e.get('evidence_version') == 2 for e in events)
            else "activity-lighting-events-1", "events": list(events)}


def healthy(t):
    return [health(t, c) for c in ("jev", "deep_stack", "frigate", "camera")]


# The reader's chest point (shoulder midpoint) on the synthetic sofa.
SOFA_MID = (1.6, 0.75, 0.95)


def reading_scene(t, zone_hint=None):
    """Everything reading_spot_on needs, observed at t (v1.1: no zone events;
    the pose places the reader at the sofa seat)."""
    return [occupancy(t), activity(t, zone_hint=zone_hint), pose(t), *healthy(t)]
