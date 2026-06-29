"""Frigate payload parsing: both bbox formats, full event flow, defensive
handling of odd payloads."""
from conftest import FakeClock, make_model
from tracker import ROOM_LINGER_S, Tracker, normalize_box

W, H = 1280, 720


# ---- normalize_box -----------------------------------------------------------

def test_list_xyxy():
    assert normalize_box([100, 200, 300, 560], W, H) == (100, 200, 300, 560)


def test_list_xywh_detected_when_xyxy_impossible():
    # c < a: cannot be xmax/ymax form -> read as [x, y, w, h]
    assert normalize_box([600, 300, 500, 380], W, H) == (600, 300, 1100, 680)


def test_list_ambiguous_prefers_frigate_xyxy():
    # both readings fit the frame; frigate/events uses xmin/ymin/xmax/ymax
    assert normalize_box([10, 20, 30, 40], W, H) == (10, 20, 30, 40)


def test_dict_xminymax():
    box = {"xmin": 50, "ymin": 60, "xmax": 250, "ymax": 700}
    assert normalize_box(box, W, H) == (50, 60, 250, 700)


def test_dict_xywh():
    box = {"x": 50, "y": 60, "w": 200, "h": 400}
    assert normalize_box(box, W, H) == (50, 60, 250, 460)


def test_normalized_coordinates_scaled():
    out = normalize_box([0.25, 0.5, 0.5, 1.0], W, H)
    assert out == (0.25 * W, 0.5 * H, 0.5 * W, 1.0 * H)


def test_garbage_boxes_rejected():
    assert normalize_box(None, W, H) is None
    assert normalize_box([1, 2, 3], W, H) is None
    assert normalize_box(["a", "b", "c", "d"], W, H) is None
    assert normalize_box({"foo": 1}, W, H) is None
    assert normalize_box([0, 0, 0, 0], W, H) is None  # degenerate


def test_oversized_box_clamped():
    out = normalize_box([-10, -10, W + 50, H + 50], W, H)
    assert out == (0, 0, W, H)


# ---- full event payloads ------------------------------------------------------

def event(camera="living_room", box=None, etype="update", label="person",
          eid="1718000000.123-abc", sub_label=None, stationary=False,
          score=0.85):
    return {
        "type": etype,
        "before": {},
        "after": {
            "id": eid, "camera": camera, "label": label,
            "sub_label": sub_label, "score": score, "top_score": score,
            "box": box if box is not None else [600, 200, 700, 560],
            "area": 36000, "stationary": stationary,
        },
    }


def make_tracker():
    clock = FakeClock(0.0)
    model = make_model()  # no camera calibration -> stage A (room-level)
    return Tracker(model_provider=lambda: model, clock=clock), clock


def test_event_with_xyxy_list_box_creates_room_track():
    tracker, clock = make_tracker()
    tracker.process_event(event(box=[600, 200, 700, 560]), now=clock.t)
    snap = tracker.snapshot(clock.t)
    assert len(snap) == 1
    t = snap[0]
    assert t["state"] == "room_only"
    assert t["pos"] is None
    assert t["room"] == "living_room"
    assert t["source_cams"] == ["living_room"]
    assert t["conf_reason"] == "room_only"


def test_event_with_xywh_dict_box_parses_too():
    tracker, clock = make_tracker()
    tracker.process_event(
        event(camera="kitchen", box={"x": 600, "y": 200, "w": 100, "h": 360}),
        now=clock.t)
    snap = tracker.snapshot(clock.t)
    assert len(snap) == 1
    assert snap[0]["room"] == "kitchen"


def test_sub_label_becomes_person_and_list_form_handled():
    tracker, clock = make_tracker()
    tracker.process_event(event(sub_label="marcelo"), now=clock.t)
    assert tracker.snapshot(clock.t)[0]["person"] == "marcelo"

    tracker2, clock2 = make_tracker()
    tracker2.process_event(event(sub_label=["marcelo", 0.92]), now=clock2.t)
    assert tracker2.snapshot(clock2.t)[0]["person"] == "marcelo"


def test_non_person_and_unmapped_camera_ignored():
    tracker, clock = make_tracker()
    tracker.process_event(event(label="car"), now=clock.t)
    tracker.process_event(event(camera="driveway"), now=clock.t)  # outdoor
    assert tracker.snapshot(clock.t) == []


def test_end_event_room_track_lingers_then_retires():
    # "end" only marks the track; room presence lingers ROOM_LINGER_S
    # ("last seen in <room>") before _retire_stale drops it.
    tracker, clock = make_tracker()
    tracker.process_event(event(etype="new"), now=clock.t)
    assert len(tracker.snapshot(clock.t)) == 1
    clock.tick(2.0)
    tracker.process_event(event(etype="end"), now=clock.t)
    tracker.predict_step(clock.t)
    snap = tracker.snapshot(clock.t)
    assert len(snap) == 1
    assert snap[0]["room"] == "living_room"
    clock.tick(ROOM_LINGER_S + 1.0)
    tracker.predict_step(clock.t)
    assert tracker.snapshot(clock.t) == []


def test_workshop_camera_maps_to_office_zone():
    tracker, clock = make_tracker()
    tracker.process_event(event(camera="workshop"), now=clock.t)
    assert tracker.snapshot(clock.t)[0]["room"] == "office"


def test_malformed_payloads_do_not_crash():
    tracker, clock = make_tracker()
    tracker.process_event({}, now=clock.t)
    tracker.process_event({"type": "update"}, now=clock.t)
    tracker.process_event({"type": "update", "after": None}, now=clock.t)
    tracker.process_event({"type": "update", "after": {"label": "person"}},
                          now=clock.t)
    bad = event()
    bad["after"]["box"] = "not-a-box"
    tracker.process_event(bad, now=clock.t)
    # the camera-only event above still yields a room-level track
    assert all(t["pos"] is None for t in tracker.snapshot(clock.t))


def test_calibrated_camera_event_yields_precise_track():
    """End-to-end stage B: a calibrated living_room camera turns a person
    bbox into a KF track with a floor position."""
    import numpy as np
    from tracker import CameraGeometry, R_to_quat_wxyz
    from test_raycast import K, IMAGE_SIZE, look_at_R

    C = [0.2, 2.0, 2.4]
    target = [3.0, 2.0, 0.0]
    R = look_at_R(C, target)
    geom = CameraGeometry(K=K, dist=None, image_size=IMAGE_SIZE,
                          q_wxyz=R_to_quat_wxyz(R), C=C)
    X = np.array([2.5, 2.2, 0.0])  # inside the living_room polygon
    px = geom.project(X)
    u, v = float(px[0]), float(px[1])

    cam_device = {
        "id": "cam_living", "type": "camera", "name": "Living cam",
        "ha_entity_id": None, "pos": C, "room_id": "living_room",
        "camera": {
            "frigate_name": "living_room",
            "video": {"detect_res": [1280, 720], "main_res": [1280, 720]},
            "intrinsics": {"K": K, "dist": [0, 0, 0, 0, 0],
                           "image_size": [1280, 720]},
            "extrinsics": {"q_wxyz": R_to_quat_wxyz(R), "t": None, "C": C},
        },
    }
    model = make_model(camera_calib=cam_device)
    clock = FakeClock(0.0)
    tracker = Tracker(model_provider=lambda: model, clock=clock)

    box = [u - 40, v - 180, u + 40, v]
    for _ in range(3):  # confirm the candidate
        tracker.process_event(event(box=box, score=0.9), now=clock.t)
        clock.tick(0.1)
        tracker.predict_step(clock.t)

    snap = tracker.snapshot(clock.t)
    assert len(snap) == 1
    t = snap[0]
    assert t["state"] == "active"
    assert t["pos"] is not None
    assert abs(t["pos"][0] - X[0]) < 0.05
    assert abs(t["pos"][1] - X[1]) < 0.05
    assert t["room"] == "living_room"
    assert t["cov"] is not None and len(t["cov"]) == 2
