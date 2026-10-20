import pytest
from conftest import HOME, NOON, activity, batch, cost, event, health, occupancy, pose, test_settings, zone

from lighting_brain.inputs import EventError, Inputs, validate_batch

S = test_settings()


def refused(body, geometry, home=HOME):
    with pytest.raises(EventError) as info:
        validate_batch(body, home, geometry)
    return info.value


def test_valid_batch_of_every_type(geometry):
    t = NOON
    events = [occupancy(t), zone(t), activity(t), pose(t), health(t, "jev"), cost(t, 3, 0.01)]
    assert validate_batch(batch(*events), HOME, geometry) == events


def test_unknown_type_is_422(geometry):
    err = refused(batch(occupancy(NOON), event("occupancy", NOON) | {"type": "weather"}), geometry)
    assert (err.status, err.error, err.index) == (422, "unknown_event_type", 1)


@pytest.mark.parametrize("mutate", [
    lambda e: e.pop("observed_at"),
    lambda e: e.update(ttl_s=0),
    lambda e: e.update(ttl_s=True),
    lambda e: e.update(source=""),
    lambda e: e.update(room="kitchen"),
    lambda e: e.update(home="la"),
    lambda e: e.update(present="yes"),
    lambda e: e.update(people_count=-1),
])
def test_bad_common_or_occupancy_fields_are_422(geometry, mutate):
    e = occupancy(NOON)
    mutate(e)
    assert refused(batch(e), geometry).status == 422


@pytest.mark.parametrize("bad", [
    activity(NOON, label="sleeping"),
    activity(NOON, confidence=1.5),
    activity(NOON, zone_hint="kitchen"),
    zone(NOON, name="kitchen"),
    health(NOON, "gpu"),
    cost(NOON, 3) | {"component": "qwen"},
    pose(NOON, people=[{"track_id": 1, "shoulders_m": {"left": [0, 0], "right": None}, "head_m": None,
                        "head_pitch_deg": None, "approximate": True}]),
    pose(NOON, people=[{"track_id": 1, "shoulders_m": {"left": None, "right": None}, "head_m": None,
                        "head_pitch_deg": None, "approximate": False}]),
])
def test_bad_type_fields_are_422(geometry, bad):
    assert refused(batch(bad), geometry).status == 422


def test_batch_shape(geometry):
    assert refused({"schema": "nope", "events": []}, geometry).error == "schema_invalid"
    assert refused({"schema": "activity-lighting-events-1", "events": []}, geometry).error == "events_invalid"


def test_expired_future_and_stale_are_ignored():
    inputs = Inputs()
    now = NOON
    assert inputs.apply(occupancy(now - 91), now, S) == "expired"
    assert inputs.apply(occupancy(now + 6), now, S) == "future"
    assert inputs.apply(occupancy(now - 5), now, S) is None
    assert inputs.apply(occupancy(now - 10), now, S) == "stale"
    assert inputs.occupancy.observed_at == now - 5
    # Stale is per key: an older desk zone event is fine after a sofa one.
    assert inputs.apply(zone(now - 2, "sofa"), now, S) is None
    assert inputs.apply(zone(now - 3, "desk"), now, S) is None


def test_absent_heartbeats_do_not_restart_the_off_timer():
    inputs = Inputs()
    t = NOON
    inputs.apply(occupancy(t), t, S)
    inputs.apply(occupancy(t + 20, present=False), t + 20, S)
    assert inputs.occupancy_last_present_at == t + 20  # the moment they left
    inputs.apply(occupancy(t + 50, present=False), t + 50, S)
    inputs.apply(occupancy(t + 80, present=False), t + 80, S)
    assert inputs.occupancy_last_present_at == t + 20


def test_reading_run_tracking():
    inputs = Inputs()
    t = NOON
    inputs.apply(activity(t), t, S)
    inputs.apply(activity(t + 4), t + 4, S)
    run = inputs.reading
    assert (run.since, run.last_at, run.ended_at) == (t, t + 4, None)
    inputs.apply(activity(t + 8, label="resting"), t + 8, S)
    assert run.ended_at == t + 8
    # A new reading answer after the run ended starts a new run.
    inputs.apply(activity(t + 9), t + 9, S)
    assert inputs.reading.since == t + 9
    # A gap longer than the ttl also starts a new run.
    inputs.apply(activity(t + 30), t + 30, S)
    assert inputs.reading.since == t + 30


def test_low_confidence_reading_does_not_count():
    inputs = Inputs()
    strict = test_settings(reading_min_confidence=0.6)
    inputs.apply(activity(NOON, confidence=0.4), NOON, strict)
    assert inputs.reading is None
    inputs.apply(activity(NOON + 1, confidence=None), NOON + 1, strict)
    assert inputs.reading is not None


def test_cost_events_accumulate():
    inputs = Inputs()
    inputs.apply(cost(NOON, 4, 0.02), NOON, S)
    inputs.apply(cost(NOON, 5, None), NOON, S)  # same timestamp is not "stale" for cost
    assert [c[1] for c in inputs.cost] == [4, 5]


def test_person_position_validation(geometry):
    from conftest import position

    good = position(NOON, (1.0, 2.0, 1.2), (3.0, 1.0, 1.1))
    assert validate_batch(batch(good), HOME, geometry) == [good]
    for bad in (
        position(NOON, (1.0, 2.0, 1.2)) | {"person_count": None},
        {**position(NOON), "people": [{"track_id": "slot-0", "position_m": None, "approximate": True}]},
        {**position(NOON), "people": [{"track_id": "slot-0", "position_m": [1, 2], "approximate": True}]},
        {**position(NOON), "people": [{"track_id": "slot-0", "position_m": [1, 2, 3], "approximate": False}]},
    ):
        assert refused(batch(bad), geometry).status == 422


def test_person_position_is_fresh_for_three_seconds_and_is_evidence():
    from conftest import position

    inputs = Inputs()
    assert inputs.apply(position(NOON, (1.0, 1.0, 1.0)), NOON, S) is None
    assert inputs.position.expires_at == NOON + 3
    assert inputs.last_person_evidence_at == NOON
    assert inputs.apply(position(NOON + 1, count=0), NOON + 1, S) is None
    assert inputs.last_person_evidence_at == NOON  # an empty frame is not evidence
