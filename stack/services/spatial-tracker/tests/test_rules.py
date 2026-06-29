"""ActivityEngine: debounce / exit-hysteresis transitions with an injected
clock and state getter (no Home Assistant)."""
from conftest import FakeClock, make_model
from rules import ActivityEngine


def make_engine(clock, states):
    model = make_model()
    return ActivityEngine(
        model_provider=lambda: model,
        state_getter=lambda eid: states.get(eid),
        clock=clock,
    )


def track(room="living_room", pos=(2.5, 1.0, 0.0), vel=(0.3, 0.0), tid="t1"):
    return {
        "id": tid, "person": None, "state": "active",
        "pos": list(pos) if pos is not None else None,
        "vel": list(vel) if vel is not None else None,
        "cov": None, "room": room, "zone": None,
        "source_cams": ["living_room"], "conf": 0.9, "conf_reason": "good",
        "activity": None, "activity_conf": None, "activity_source": None,
    }


def step(engine, clock, tracks, seconds, dt=1.0, ha_alive=True):
    """Advance the engine; returns the last annotated track list."""
    out = tracks
    n = int(round(seconds / dt))
    for _ in range(n):
        clock.tick(dt)
        if ha_alive:
            engine.note_ha_alive(clock.t)
        out = engine.annotate(tracks, clock.t)
    return out


def test_watching_tv_enter_debounce_and_exit_hysteresis():
    clock = FakeClock(0.0)
    states = {"media_player.living_tv": "playing"}
    engine = make_engine(clock, states)
    # vel 0.3 m/s: too fast for idle, too slow for moving — isolates the rule
    tracks = [track(pos=(2.5, 1.0, 0.0), vel=(0.3, 0.0))]

    out = step(engine, clock, tracks, seconds=9)
    assert out[0]["activity"] is None  # still inside the 10 s debounce
    out = step(engine, clock, tracks, seconds=2)
    assert out[0]["activity"] == "watching_tv"
    assert out[0]["activity_source"] == "rules"
    assert 0.5 <= out[0]["activity_conf"] <= 0.8

    # tv stops: 20 s exit hysteresis
    states["media_player.living_tv"] = "paused"
    out = step(engine, clock, tracks, seconds=19)
    assert out[0]["activity"] == "watching_tv"  # held through hysteresis
    out = step(engine, clock, tracks, seconds=2)
    assert out[0]["activity"] is None


def test_watching_tv_requires_proximity_when_pos_known():
    clock = FakeClock(0.0)
    states = {"media_player.living_tv": "playing"}
    engine = make_engine(clock, states)
    # tv at (2.0, 0.2); track 3.6 m away in the same room
    far = [track(pos=(2.0, 3.8, 0.0), vel=(0.3, 0.0))]
    out = step(engine, clock, far, seconds=15)
    assert out[0]["activity"] is None

    # room-level (pos unknown) falls back to a room match
    engine2 = make_engine(clock, states)
    roomlevel = [track(pos=None, vel=None)]
    roomlevel[0]["state"] = "room_only"
    out = step(engine2, clock, roomlevel, seconds=11)
    assert out[0]["activity"] == "watching_tv"


def test_stale_ha_suppresses_media_rules():
    clock = FakeClock(0.0)
    states = {"media_player.living_tv": "playing"}
    engine = make_engine(clock, states)
    tracks = [track(vel=(0.3, 0.0))]
    # HA link never reported alive -> media rules suppressed
    out = step(engine, clock, tracks, seconds=30, ha_alive=False)
    assert out[0]["activity"] is None

    # once HA traffic resumes, the rule can debounce in normally
    out = step(engine, clock, tracks, seconds=11, ha_alive=True)
    assert out[0]["activity"] == "watching_tv"

    # ... and goes stale again 30 s after the last HA sign of life
    out = step(engine, clock, tracks, seconds=29, ha_alive=False)
    assert out[0]["activity"] == "watching_tv"  # not stale yet (<= 30 s)
    out = step(engine, clock, tracks, seconds=25, ha_alive=False)
    assert out[0]["activity"] is None  # stale -> condition false -> 20 s exit


def test_listening_music_only_without_tv():
    clock = FakeClock(0.0)
    states = {"media_player.sonos_living": "playing",
              "media_player.living_tv": "idle"}
    engine = make_engine(clock, states)
    tracks = [track(vel=(0.3, 0.0))]
    out = step(engine, clock, tracks, seconds=16)
    assert out[0]["activity"] == "listening_music"

    # tv starts playing -> watching_tv outranks and music condition fails
    states["media_player.living_tv"] = "playing"
    out = step(engine, clock, tracks, seconds=31)
    assert out[0]["activity"] == "watching_tv"


def test_moving_and_idle_transitions():
    clock = FakeClock(0.0)
    engine = make_engine(clock, {})
    fast = [track(vel=(0.8, 0.0))]
    out = step(engine, clock, fast, seconds=1)
    assert out[0]["activity"] is None  # 2 s debounce
    out = step(engine, clock, fast, seconds=2)
    assert out[0]["activity"] == "moving"

    # stop: moving holds 5 s (exit hysteresis), then idle after 30 s debounce
    fast[0]["vel"] = [0.0, 0.0]
    out = step(engine, clock, fast, seconds=4)
    assert out[0]["activity"] == "moving"
    out = step(engine, clock, fast, seconds=2)
    assert out[0]["activity"] is None  # moving expired, idle still debouncing
    out = step(engine, clock, fast, seconds=26)
    assert out[0]["activity"] == "idle"

    # idle has no exit hysteresis: first fast tick clears it
    fast[0]["vel"] = [0.9, 0.0]
    out = step(engine, clock, fast, seconds=1)
    assert out[0]["activity"] is None


def test_cooking_needs_kitchen_dwell_and_movement():
    clock = FakeClock(0.0)
    engine = make_engine(clock, {})
    t = track(room="kitchen", pos=(5.0, 1.0, 0.0), vel=(0.3, 0.0), tid="cook")

    # bounce around the kitchen (high movement variance) for > dwell+debounce
    out = [t]
    flip = False
    for _ in range(125):
        clock.tick(1.0)
        flip = not flip
        t["pos"] = [5.0 + (0.8 if flip else -0.8), 1.0, 0.0]
        out = engine.annotate([t], clock.t)
    assert out[0]["activity"] == "cooking"

    # a *still* person in the kitchen does not cook
    engine2 = make_engine(clock, {})
    t2 = track(room="kitchen", pos=(5.0, 1.0, 0.0), vel=(0.3, 0.0), tid="still")
    out = step(engine2, clock, [t2], seconds=130)
    assert out[0]["activity"] != "cooking"


def test_working_at_desk():
    clock = FakeClock(0.0)
    engine = make_engine(clock, {})
    # desk device at (1.0, 6.0) in the office; track 0.5 m away, still
    t = track(room="office", pos=(1.0, 6.5, 0.0), vel=(0.05, 0.0), tid="w1")
    # dwell > 120 s must hold, then 120 s enter debounce on top
    out = step(engine, clock, [t], seconds=121)
    assert out[0]["activity"] != "working"
    out = step(engine, clock, [t], seconds=125)
    assert out[0]["activity"] in ("working", "idle")
    # working outranks idle in the priority table? it does not — working is
    # listed before idle, so once on it must win:
    assert out[0]["activity"] == "working"
