"""KF convergence, coast saturation, demote-to-room, retire/re-bind."""
import numpy as np

from conftest import FakeClock, make_model
from tracker import COAST_SIGMA_MAX, Kalman2D, Tracker


def test_kf_converges_on_synthetic_walk():
    rng = np.random.default_rng(42)
    dt = 0.1
    vel = np.array([1.0, 0.5])
    truth = np.array([0.0, 0.0])
    kf = Kalman2D(truth[0], truth[1])
    for _ in range(80):
        truth = truth + vel * dt
        z = truth + rng.normal(0.0, 0.1, size=2)
        kf.predict(dt)
        kf.update(z, sigma_xy=0.1)
    assert np.linalg.norm(kf.pos() - truth) < 0.25
    assert np.linalg.norm(kf.vel() - vel) < 0.35
    # covariance should have tightened well below the prior
    assert kf.pos_sigma() < 0.2


def test_coast_saturation_freezes_prediction():
    kf = Kalman2D(1.0, 2.0)
    kf.update((1.0, 2.0), sigma_xy=0.1)
    kf.x[2:] = [0.8, -0.2]  # give it some velocity to coast with
    for _ in range(300):  # 30 s of coasting at 10 Hz
        kf.predict(0.1)
    assert kf.pos_sigma() <= COAST_SIGMA_MAX + 1e-9
    # once saturated the filter is frozen: state and covariance stop changing
    x_before, p_before = kf.x.copy(), kf.P.copy()
    for _ in range(50):
        kf.predict(0.1)
    assert np.allclose(kf.x, x_before)
    assert np.allclose(kf.P, p_before)


def _confirmed_tracker(clock):
    model = make_model()
    tracker = Tracker(model_provider=lambda: model, clock=clock)
    for _ in range(4):  # 4 hits -> confirmed (threshold is 3)
        tracker.ingest_position(camera="living_room", zone_id="living_room",
                                pos_xy=(2.0, 2.0), sigma=0.2, score=0.9,
                                frigate_id="ev1", now=clock.t)
        clock.tick(0.1)
        tracker.predict_step(clock.t)
    return tracker, model


def test_demote_to_room_after_5s_then_retire_and_rebind():
    clock = FakeClock(100.0)
    tracker, _ = _confirmed_tracker(clock)

    snap = tracker.snapshot(clock.t)
    assert len(snap) == 1
    t0 = snap[0]
    assert t0["state"] == "active"
    assert t0["pos"] is not None
    assert t0["room"] == "living_room"
    original_id = t0["id"]

    # ~3 s without measurements -> coasting, position still published
    for _ in range(30):
        clock.tick(0.1)
        tracker.predict_step(clock.t)
    snap = tracker.snapshot(clock.t)
    assert snap[0]["state"] == "coasting"
    assert snap[0]["conf_reason"] == "coasting"
    assert snap[0]["pos"] is not None

    # > 5 s -> demoted to room level: pos null, room kept
    for _ in range(30):
        clock.tick(0.1)
        tracker.predict_step(clock.t)
    snap = tracker.snapshot(clock.t)
    assert snap[0]["state"] == "room_only"
    assert snap[0]["pos"] is None
    assert snap[0]["vel"] is None
    assert snap[0]["room"] == "living_room"

    # > 30 s without signal -> retired entirely
    for _ in range(260):
        clock.tick(0.1)
        tracker.predict_step(clock.t)
    assert tracker.snapshot(clock.t) == []

    # re-acquisition within 1 m and < 30 s -> the retired id is re-bound
    clock.tick(5.0)
    tr = tracker.ingest_position(camera="living_room", zone_id="living_room",
                                 pos_xy=(2.3, 2.0), sigma=0.2, now=clock.t)
    assert tr.id == original_id
    assert tr.confirmed  # known track resumes without re-confirmation


def test_stationary_holds_position_with_frozen_covariance():
    clock = FakeClock(50.0)
    tracker, _ = _confirmed_tracker(clock)
    tracker.ingest_position(camera="living_room", zone_id="living_room",
                            pos_xy=(2.0, 2.0), sigma=0.2, stationary=True,
                            now=clock.t)
    track = next(iter(tracker.tracks.values()))
    p_before = track.kf.P.copy()
    pos_before = track.kf.pos()
    for _ in range(100):  # 10 s, well past the 5 s demotion window
        clock.tick(0.1)
        tracker.predict_step(clock.t)
    snap = tracker.snapshot(clock.t)
    assert snap[0]["state"] == "stationary_held"
    assert np.allclose(track.kf.P, p_before)
    assert np.allclose(track.kf.pos(), pos_before)


def test_non_gating_measurement_spawns_candidate_confirmed_after_3_hits():
    clock = FakeClock(10.0)
    tracker, _ = _confirmed_tracker(clock)
    assert len(tracker.tracks) == 1

    # far away (well outside the 3-sigma gate) -> new candidate track
    tracker.ingest_position(camera="kitchen", zone_id="kitchen",
                            pos_xy=(7.0, 1.0), sigma=0.2, now=clock.t)
    assert len(tracker.tracks) == 2
    snap = tracker.snapshot(clock.t)
    cand = [t for t in snap if t["room"] == "kitchen"][0]
    assert cand["state"] == "room_only"  # not confirmed -> no precise pos yet
    assert cand["pos"] is None

    for _ in range(2):
        clock.tick(0.1)
        tracker.predict_step(clock.t)
        tracker.ingest_position(camera="kitchen", zone_id="kitchen",
                                pos_xy=(7.0, 1.0), sigma=0.2, now=clock.t)
    snap = tracker.snapshot(clock.t)
    cand = [t for t in snap if t["room"] == "kitchen"][0]
    assert cand["state"] == "active"
    assert cand["pos"] is not None


def test_cross_camera_tracks_merge_within_080m():
    clock = FakeClock(10.0)
    model = make_model()
    tracker = Tracker(model_provider=lambda: model, clock=clock)
    for _ in range(4):
        tracker.ingest_position(camera="living_room", zone_id="living_room",
                                pos_xy=(2.0, 2.0), sigma=0.15, now=clock.t)
        clock.tick(0.1)
    for _ in range(4):
        tracker.ingest_position(camera="kitchen", zone_id="kitchen",
                                pos_xy=(7.0, 1.0), sigma=0.15, now=clock.t)
        clock.tick(0.1)
    confirmed = [t for t in tracker.tracks.values() if t.confirmed]
    assert len(confirmed) == 2
    older = min(confirmed, key=lambda t: t.created)

    # the two estimates converge to within 0.8 m (same person, two cameras)
    newer = max(confirmed, key=lambda t: t.created)
    newer.kf.x[:2] = [2.5, 2.0]
    tracker.predict_step(clock.t)

    confirmed = [t for t in tracker.tracks.values() if t.confirmed]
    assert len(confirmed) == 1
    assert confirmed[0].id == older.id  # older id wins
    assert {"living_room", "kitchen"} <= confirmed[0].source_cams
