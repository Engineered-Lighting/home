"""Room assignment hysteresis: switch after 1.5 s OR 3 consecutive
measurements in the new polygon; boundary points keep the previous room."""
from conftest import FakeClock, make_model
from tracker import RoomHysteresis, Tracker


def make_tracker(clock):
    model = make_model()
    return Tracker(model_provider=lambda: model, clock=clock), model


def settle(tracker, clock, pos, camera="living_room", zone="living_room", n=4,
           dt=0.1):
    for _ in range(n):
        tracker.ingest_position(camera=camera, zone_id=zone, pos_xy=pos,
                                sigma=0.2, now=clock.t)
        clock.tick(dt)
        tracker.predict_step(clock.t)
    return next(iter(tracker.tracks.values()))


# ---- RoomHysteresis unit tests ------------------------------------------------

def test_hysteresis_unit_three_consecutive():
    h = RoomHysteresis()
    h.force("a")
    assert h.update("b", 0.0) == "a"
    assert h.update("b", 0.1) == "a"
    assert h.update("b", 0.2) == "b"  # 3rd consecutive measurement switches


def test_hysteresis_unit_time_based():
    h = RoomHysteresis()
    h.force("a")
    assert h.update("b", 0.0) == "a"
    assert h.update("b", 1.6) == "b"  # >= 1.5 s in the new room switches


def test_hysteresis_unit_interruption_resets():
    h = RoomHysteresis()
    h.force("a")
    assert h.update("b", 0.0) == "a"
    assert h.update("b", 0.1) == "a"
    assert h.update("a", 0.2) == "a"  # back to current -> candidate reset
    assert h.update("b", 0.3) == "a"
    assert h.update("b", 0.4) == "a"
    assert h.update("b", 0.5) == "b"


def test_hysteresis_none_keeps_previous():
    h = RoomHysteresis()
    h.force("a")
    assert h.update(None, 0.0) == "a"
    assert h.update(None, 5.0) == "a"


# ---- Tracker-level tests -------------------------------------------------------

def test_tracker_room_switch_three_measurements():
    clock = FakeClock(0.0)
    tracker, _ = make_tracker(clock)
    track = settle(tracker, clock, (3.0, 2.0))
    assert track.rooms.current == "living_room"

    # walk into the kitchen in small (gate-friendly) steps; the polygon
    # boundary sits at x=4, so 4.2 / 4.6 / 5.0 are the kitchen measurements
    kitchen_steps = 0
    for x in (3.4, 3.8, 4.2, 4.6, 5.0):
        tracker.ingest_position(camera="living_room", zone_id="living_room",
                                pos_xy=(x, 2.0), sigma=0.2, now=clock.t)
        assert len(tracker.tracks) == 1  # gated to the same track
        if x > 4.0:
            kitchen_steps += 1
        room = tracker.snapshot(clock.t)[0]["room"]
        if kitchen_steps < 3:
            assert room == "living_room"  # hysteresis holds (< 3 measurements)
        else:
            assert room == "kitchen"      # 3rd consecutive kitchen fix
        clock.tick(0.3)
        tracker.predict_step(clock.t)


def test_tracker_room_switch_time_based():
    clock = FakeClock(0.0)
    tracker, _ = make_tracker(clock)
    settle(tracker, clock, (3.6, 2.0))

    tracker.ingest_position(camera="living_room", zone_id="living_room",
                            pos_xy=(4.2, 2.0), sigma=0.2, now=clock.t)
    assert len(tracker.tracks) == 1
    assert tracker.snapshot(clock.t)[0]["room"] == "living_room"
    clock.tick(1.6)
    tracker.predict_step(clock.t)
    tracker.ingest_position(camera="living_room", zone_id="living_room",
                            pos_xy=(4.2, 2.0), sigma=0.2, now=clock.t)
    # only 2 measurements, but > 1.5 s inside the kitchen polygon
    assert tracker.snapshot(clock.t)[0]["room"] == "kitchen"


def test_boundary_point_keeps_previous_room():
    clock = FakeClock(0.0)
    tracker, _ = make_tracker(clock)
    settle(tracker, clock, (3.5, 2.0))
    # x=4.0 is the shared living_room/kitchen edge: covered by both polygons
    for _ in range(6):
        tracker.ingest_position(camera="living_room", zone_id="living_room",
                                pos_xy=(4.0, 2.0), sigma=0.2, now=clock.t)
        clock.tick(0.5)
        tracker.predict_step(clock.t)
        assert len(tracker.tracks) == 1
    assert tracker.snapshot(clock.t)[0]["room"] == "living_room"


def test_outside_all_polygons_keeps_previous_room():
    clock = FakeClock(0.0)
    tracker, _ = make_tracker(clock)
    settle(tracker, clock, (2.0, 0.3))
    # y < 0 lies outside every zone polygon, but close enough to gate
    for _ in range(6):
        tracker.ingest_position(camera="living_room", zone_id="living_room",
                                pos_xy=(2.0, -0.2), sigma=0.2, now=clock.t)
        clock.tick(0.5)
        tracker.predict_step(clock.t)
        assert len(tracker.tracks) == 1
    assert tracker.snapshot(clock.t)[0]["room"] == "living_room"


def test_zone_for_camera_mapping_and_fallback():
    model = make_model()
    assert model.zone_for_camera("workshop") == "office"  # via frigate_camera
    assert model.zone_for_camera("living_room") == "living_room"
    assert model.zone_for_camera("driveway") is None      # outdoor: unmapped
