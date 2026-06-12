"""path_data trail ingestion, /ingest/detection (Tracker.ingest_external)
and rate-aware measurement-noise scaling.

The endpoint itself is a thin pydantic wrapper in main.py (no httpx in the
test venv -> no TestClient); the contract semantics live in
``Tracker.ingest_external`` and are unit-tested here.
"""
import math

import numpy as np
import pytest

from conftest import FakeClock, make_model
from test_raycast import IMAGE_SIZE, K, look_at_R
from tracker import (EXTERNAL_SIGMA_MULT, HIP_SEATED_PLANE_Z, PATH_MAX_POINTS,
                     CameraGeometry, R_to_quat_wxyz, Tracker, localize_pixel)

CAM_C = [0.2, 2.0, 2.4]
CAM_TARGET = [3.0, 2.0, 0.0]


def make_calibrated(clock):
    """Tracker with a calibrated living_room camera plus the matching
    standalone CameraGeometry for projecting ground truth."""
    R = look_at_R(CAM_C, CAM_TARGET)
    q = R_to_quat_wxyz(R)
    geom = CameraGeometry(K=K, dist=None, image_size=IMAGE_SIZE, q_wxyz=q,
                          C=CAM_C)
    cam_device = {
        "id": "cam_living", "type": "camera", "name": "Living cam",
        "ha_entity_id": None, "pos": CAM_C, "room_id": "living_room",
        "camera": {
            "frigate_name": "living_room",
            "video": {"detect_res": list(IMAGE_SIZE),
                      "main_res": list(IMAGE_SIZE)},
            "intrinsics": {"K": K, "dist": [0, 0, 0, 0, 0],
                           "image_size": list(IMAGE_SIZE)},
            "extrinsics": {"q_wxyz": q, "t": None, "C": CAM_C},
        },
    }
    model = make_model(camera_calib=cam_device)
    tracker = Tracker(model_provider=lambda: model, clock=clock)
    return tracker, geom


def path_event(geom, points_xy, ts_list, camera="living_room",
               eid="1718000000.5-path", score=0.9, box=None):
    """Frigate event whose after.path_data is the projection of the given
    world floor points (normalized image coords; full history, like Frigate
    re-sends it on every message)."""
    path = []
    for (x, y), ts in zip(points_xy, ts_list):
        px = geom.project([x, y, 0.0])
        path.append([[float(px[0]) / IMAGE_SIZE[0],
                      float(px[1]) / IMAGE_SIZE[1]], float(ts)])
    return {
        "type": "update",
        "before": {},
        "after": {
            "id": eid, "camera": camera, "label": "person",
            "sub_label": None, "score": score, "top_score": score,
            "box": box, "area": 36000, "stationary": False,
            "path_data": path,
        },
    }


def kf_track(tracker):
    return next(t for t in tracker.tracks.values() if t.kind == "kf")


# ---- path_data ingestion -------------------------------------------------------

def test_path_data_trail_feeds_multiple_kf_updates():
    clock = FakeClock(1000.0)
    tracker, geom = make_calibrated(clock)
    # slow walk: 6 trail points at 5 Hz ending just before the event time
    xs = [(2.0, 2.0 + 0.05 * i) for i in range(6)]
    ts = [clock.t - 1.2 + 0.2 * i for i in range(6)]
    tracker.process_event(path_event(geom, xs, ts), now=clock.t)

    kf_tracks = [t for t in tracker.tracks.values() if t.kind == "kf"]
    assert len(kf_tracks) == 1
    tr = kf_tracks[0]
    assert tr.hits == 6        # one KF update per trail point
    assert tr.confirmed        # 6 >= CONFIRM_HITS
    # the room-level placeholder (box=None) was superseded by precise fixes
    assert not any(t.kind == "room" for t in tracker.tracks.values())
    # estimate near the trail's end (now-based KF: no predicts between the
    # back-to-back updates of one message, so allow some lag)
    assert np.linalg.norm(tr.kf.pos() - np.array(xs[-1])) < 0.15
    assert tr.rooms.current == "living_room"


def test_path_data_resend_is_deduped():
    clock = FakeClock(1000.0)
    tracker, geom = make_calibrated(clock)
    xs = [(2.0, 2.0 + 0.05 * i) for i in range(4)]
    ts = [clock.t - 0.8 + 0.2 * i for i in range(4)]
    tracker.process_event(path_event(geom, xs, ts), now=clock.t)
    tr = kf_track(tracker)
    assert tr.hits == 4

    # Frigate repeats the FULL history on the next message -> zero new updates
    clock.tick(0.5)
    tracker.process_event(path_event(geom, xs, ts), now=clock.t)
    assert tr.hits == 4

    # two genuinely new points appended -> exactly two more updates
    xs2 = xs + [(2.0, 2.25), (2.0, 2.3)]
    ts2 = ts + [clock.t - 0.2, clock.t - 0.1]
    tracker.process_event(path_event(geom, xs2, ts2), now=clock.t)
    assert tr.hits == 6


def test_path_data_age_filter_and_per_message_cap():
    clock = FakeClock(2000.0)
    tracker, geom = make_calibrated(clock)
    # 3 stale points (>10 s old) + 25 fresh -> only the newest 20 consumed
    pts = [(2.0, 1.5 + 0.02 * i) for i in range(28)]
    ts = [clock.t - 50.0 + 0.1 * i for i in range(3)]
    ts += [clock.t - 5.0 + 0.2 * i for i in range(25)]
    tracker.process_event(path_event(geom, pts, ts), now=clock.t)
    assert kf_track(tracker).hits == PATH_MAX_POINTS


def test_path_data_malformed_entries_ignored():
    clock = FakeClock(1000.0)
    tracker, geom = make_calibrated(clock)
    ev = path_event(geom, [(2.5, 2.0)], [clock.t - 0.5])
    ev["after"]["path_data"] = [
        "garbage", None, [], [[0.5], clock.t], [[0.5, 0.5]],
        [["a", "b"], clock.t], [[5.0, 0.5], clock.t - 0.1],  # not normalized
        ev["after"]["path_data"][0],                          # the valid one
    ]
    tracker.process_event(ev, now=clock.t)  # must not raise
    assert kf_track(tracker).hits == 1


# ---- /ingest/detection -> Tracker.ingest_external ------------------------------

def test_ingest_external_pose_ankles_tracks_precisely():
    clock = FakeClock(500.0)
    tracker, geom = make_calibrated(clock)
    X = np.array([2.5, 2.2, 0.0])
    px = geom.project(X)
    tr = None
    for _ in range(3):
        tr = tracker.ingest_external(camera="living_room", ts=clock.t,
                                     foot_px=[float(px[0]), float(px[1])],
                                     score=0.9, source="pose-ankles")
        clock.tick(0.1)
        tracker.predict_step(clock.t)
    assert tr is not None and tr.kind == "kf" and tr.confirmed
    assert "stagec:living_room" in tr.frigate_ids
    snap = tracker.snapshot(clock.t)
    assert len(snap) == 1
    t = snap[0]
    assert t["state"] == "active"
    assert abs(t["pos"][0] - X[0]) < 0.05
    assert abs(t["pos"][1] - X[1]) < 0.05
    assert t["room"] == "living_room"


def test_ingest_external_pose_hips_uses_seated_hip_plane():
    clock = FakeClock(500.0)
    tracker, geom = make_calibrated(clock)
    ground = np.array([2.0, 2.5])
    hip = np.array([ground[0], ground[1], HIP_SEATED_PLANE_Z])
    px = geom.project(hip)  # what a pose model would report as hip midpoint
    tr = tracker.ingest_external(camera="living_room", ts=clock.t,
                                 foot_px=[float(px[0]), float(px[1])],
                                 score=0.9, source="pose-hips")
    assert tr is not None and tr.kind == "kf"
    # ray ∩ z=0.55 hip plane, dropped to the floor -> exactly under the hips
    assert np.linalg.norm(tr.kf.pos() - ground) < 1e-6


def test_localize_pixel_source_sigma_multipliers():
    _, geom = make_calibrated(FakeClock(0.0))
    px = geom.project([2.5, 2.2, 0.0])
    u, v = float(px[0]), float(px[1])
    base = localize_pixel(geom, u, v, 1.0, from_detect=False)
    # baseline matches the Stage-B sigma model exactly
    assert math.isclose(base.sigma_xy,
                        max(0.15, 0.04 * base.range_m) * 0.5, rel_tol=1e-12)
    for source, mult in EXTERNAL_SIGMA_MULT.items():
        loc = localize_pixel(geom, u, v, 1.0, from_detect=False,
                             sigma_mult=mult, method=source)
        assert math.isclose(loc.sigma_xy, base.sigma_xy * mult, rel_tol=1e-12)
    assert EXTERNAL_SIGMA_MULT["pose-ankles"] == 0.6   # contract values
    assert EXTERNAL_SIGMA_MULT["bbox-bottom"] == 1.0


def test_ingest_external_uncalibrated_camera_is_room_evidence():
    clock = FakeClock(100.0)
    tracker, _ = make_calibrated(clock)  # kitchen camera has no calibration
    tr = tracker.ingest_external(camera="kitchen", ts=clock.t,
                                 foot_px=[640.0, 360.0], score=0.8,
                                 source="bbox-bottom", person="marcelo")
    assert tr is not None and tr.kind == "room"
    assert tr.id == "room:kitchen:stagec:kitchen"
    t = [x for x in tracker.snapshot(clock.t) if x["room"] == "kitchen"][0]
    assert t["state"] == "room_only" and t["pos"] is None
    assert t["person"] == "marcelo"


def test_ingest_external_unmapped_camera_and_bad_input_ignored():
    clock = FakeClock(100.0)
    tracker, _ = make_calibrated(clock)
    assert tracker.ingest_external(camera="driveway", ts=clock.t,
                                   foot_px=[10.0, 10.0], score=0.9,
                                   source="bbox-bottom") is None
    assert tracker.ingest_external(camera="living_room", ts=clock.t,
                                   foot_px=[10.0, 10.0], score=0.9,
                                   source="lidar") is None       # unknown
    assert tracker.ingest_external(camera="living_room", ts="nan?",
                                   foot_px=[10.0], score=0.9,
                                   source="bbox-bottom") is None  # malformed
    assert tracker.tracks == {}


def test_ingest_external_offframe_pixel_falls_back_to_room_evidence():
    clock = FakeClock(100.0)
    tracker, _ = make_calibrated(clock)
    tr = tracker.ingest_external(camera="living_room", ts=clock.t,
                                 foot_px=[99999.0, 50.0], score=0.9,
                                 source="pose-ankles")
    assert tr is not None and tr.kind == "room"
    assert tr.id == "room:living_room:stagec:living_room"


# ---- near-wall soft clamp (walkable gate) ---------------------------------------

def make_walled(clock):
    """Calibrated tracker with a walkable polygon whose south wall (y=1.5)
    sits in front of the camera; hull carries the +0.5 m measurement buffer
    exactly like ModelStore.walkable_hull."""
    from shapely.geometry import Polygon
    tracker, geom = make_calibrated(clock)
    walk = Polygon([(1.0, 1.5), (6.0, 1.5), (6.0, 4.0), (1.0, 4.0)])
    hull = walk.buffer(0.5)
    tracker.walkable_provider = lambda: hull
    return tracker, geom


def test_gate_or_clamp_semantics():
    clock = FakeClock(100.0)
    tracker, _ = make_walled(clock)
    # inside the hull -> pass-through, no sigma inflation
    (x, y), mult = tracker._gate_or_clamp([2.5, 2.2, 0.0])
    assert (x, y) == (2.5, 2.2) and mult == 1.0
    # in the buffer zone (between wall y=1.5 and hull y=1.0) -> pass-through
    (x, y), mult = tracker._gate_or_clamp([2.5, 1.2, 0.0])
    assert (x, y) == (2.5, 1.2) and mult == 1.0
    # <= 0.5 m beyond the hull -> snapped to the wall line, sigma x sqrt(3)
    (x, y), mult = tracker._gate_or_clamp([2.5, 0.8, 0.0])
    assert abs(x - 2.5) < 1e-6 and abs(y - 1.5) < 1e-6
    assert math.isclose(mult, math.sqrt(3.0))
    # > 0.5 m beyond the hull -> rejected
    assert tracker._gate_or_clamp([2.5, 0.3, 0.0]) is None


def test_ingest_external_near_wall_hit_clamps_instead_of_starving():
    """A seated person on furniture against the wall projects just past it;
    the measurement must keep feeding the KF (clamped to the wall line)
    rather than demoting the track to room_only."""
    clock = FakeClock(500.0)
    tracker, geom = make_walled(clock)
    px = geom.project([2.5, 0.8, 0.0])  # 0.2 m beyond the +0.5 m hull
    tr = None
    for _ in range(3):
        tr = tracker.ingest_external(camera="living_room", ts=clock.t,
                                     foot_px=[float(px[0]), float(px[1])],
                                     score=0.9, source="pose-ankles")
        clock.tick(0.1)
        tracker.predict_step(clock.t)
    assert tr is not None and tr.kind == "kf" and tr.confirmed
    assert np.linalg.norm(tr.kf.pos() - np.array([2.5, 1.5])) < 0.05


def test_ingest_external_far_outside_hull_stays_room_evidence():
    clock = FakeClock(500.0)
    tracker, geom = make_walled(clock)
    px = geom.project([2.5, 0.3, 0.0])  # 0.7 m beyond the hull -> implausible
    tr = tracker.ingest_external(camera="living_room", ts=clock.t,
                                 foot_px=[float(px[0]), float(px[1])],
                                 score=0.9, source="pose-ankles")
    assert tr is not None and tr.kind == "room"
    assert tr.id == "room:living_room:stagec:living_room"


# ---- rate-aware measurement noise ----------------------------------------------

def test_high_rate_burst_does_not_overtighten_covariance():
    clock = FakeClock(0.0)
    model = make_model()
    tracker = Tracker(model_provider=lambda: model, clock=clock)
    for _ in range(21):  # 20 Hz burst for 1 s
        tracker.ingest_position(camera="living_room", zone_id="living_room",
                                pos_xy=(2.0, 2.0), sigma=0.2, now=clock.t)
        clock.tick(0.05)
    tr = next(iter(tracker.tracks.values()))
    assert tr.meas_dt_ema == pytest.approx(0.05, rel=0.05)
    # unscaled R would collapse pos sigma to ~0.044 (info 4 + 20/0.04);
    # the linear-with-rate R scaling caps the information per second at the
    # 1 Hz baseline -> sigma stays ~0.19
    assert tr.kf.pos_sigma() > 0.1


def test_nominal_one_hz_stream_keeps_legacy_noise():
    clock = FakeClock(0.0)
    model = make_model()
    tracker = Tracker(model_provider=lambda: model, clock=clock)
    for _ in range(5):
        tracker.ingest_position(camera="living_room", zone_id="living_room",
                                pos_xy=(2.0, 2.0), sigma=0.2, now=clock.t)
        clock.tick(1.0)
    tr = next(iter(tracker.tracks.values()))
    # spawn (P0=0.5^2) + 4 unscaled updates at R=0.04, no predicts between:
    # sigma = sqrt(1 / (1/0.25 + 4/0.04))
    assert tr.kf.pos_sigma() == pytest.approx(math.sqrt(1.0 / 104.0),
                                              rel=0.05)


def test_active_window_adapts_to_measurement_rate():
    clock = FakeClock(0.0)
    model = make_model()
    tracker = Tracker(model_provider=lambda: model, clock=clock)
    for _ in range(4):
        tracker.ingest_position(camera="living_room", zone_id="living_room",
                                pos_xy=(2.0, 2.0), sigma=0.2, score=0.9,
                                now=clock.t)
        clock.tick(1.0)
        tracker.predict_step(clock.t)
    # a 1 Hz source 1.2 s after its last fix: still "active" (the legacy
    # fixed 1 s window flickered to coasting right as the next frame was due)
    clock.tick(0.2)
    tracker.predict_step(clock.t)
    assert tracker.snapshot(clock.t)[0]["state"] == "active"
    # but past the ACTIVE_AGE_MAX_S cap it coasts as before
    clock.tick(1.5)  # meas_age 2.7 s
    tracker.predict_step(clock.t)
    assert tracker.snapshot(clock.t)[0]["state"] == "coasting"
