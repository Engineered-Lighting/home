"""den-observe writer, registration, freshness and replay tests: synthetic data only.

    python -B -m unittest discover -s tools/den-observe -v

No camera, no network (connections are allowed to 127.0.0.1 only, and none
is made), no numpy. Every take, room and detector row here is synthetic.
"""
import array
import contextlib
import io
import json
import math
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import camgeom as g  # noqa: E402
import detections  # noqa: E402
import fake_detector  # noqa: E402
import method as method_file  # noqa: E402
import observe  # noqa: E402
import replay  # noqa: E402
import roommodel  # noqa: E402
import synth  # noqa: E402
import takes  # noqa: E402

SCHEMAS = HERE.parent.parent / "docs" / "architecture" / "live-v1" / "schemas.json"
RUNG1_FILE = HERE / "testdata" / "rung1-room-model.jsonl"
DEN_REGISTER = Path(os.environ.get("DEN_REGISTER_DIR") or HERE.parent / "den-register")
_REAL_CONNECT = socket.socket.connect
_DEPTH = {}


def loopback_only(self, address, *args, **kwargs):
    host = address[0] if isinstance(address, tuple) else address
    if host != "127.0.0.1":
        raise AssertionError(f"tests may only connect to 127.0.0.1, not {host}")
    return _REAL_CONNECT(self, address, *args, **kwargs)


def setUpModule():
    global _guard, _tmp
    _guard = mock.patch.object(socket.socket, "connect", loopback_only)
    _guard.start()
    _tmp = Path(tempfile.mkdtemp(prefix="den-observe-writer-test-"))


def tearDownModule():
    _guard.stop()
    shutil.rmtree(_tmp, ignore_errors=True)


def new_dir(name):
    path, n = _tmp / name, 1
    while path.exists():
        n += 1
        path = _tmp / f"{name}-{n}"
    return path


def depth(holes=(), noise_seed=None):
    """The synthetic scene's depth bytes, cached (rendering takes ~0.4 s)."""
    key = (tuple(holes), noise_seed)
    if key not in _DEPTH:
        data = synth.Scene().depth_mm(holes=list(holes))
        if noise_seed is not None:  # per-pixel stereo noise at the proposed disparity sigma
            values, rng = array.array("H"), random.Random(noise_seed)
            values.frombytes(data)
            for i, mm in enumerate(values):
                if mm:
                    z = mm / 1000.0
                    sigma = z * z * 0.25 / (synth.LEFT_K["fx"] * synth.BASELINE_M)
                    values[i] = max(1, min(65535, int(round(mm + 1000.0 * rng.gauss(0.0, sigma)))))
            data = values.tobytes()
        _DEPTH[key] = data
    return _DEPTH[key]


SCENE = synth.Scene()
PIXELS = {name: SCENE.joint_rgb(name) for name in synth.JOINTS}
NAMES = list(synth.JOINTS)


def frames(n=3, gap_s=0.004, stale=()):
    """(t_rgb, t_depth) pairs 0.25 s apart; frames in stale get depth 0.2 s too old."""
    return [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - (0.2 if i in stale else gap_s)) for i in range(n)]


def closed_method(folder, keep_open=()):
    """A copy of METHOD.md with every slot but keep_open closed at its proposed value."""
    text = (HERE / "METHOD.md").read_text(encoding="utf-8")

    def close(match):
        return match[0] if match[1] in keep_open else f"{match[1]} = {match[2]}"
    path = Path(folder) / "METHOD.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", close, text, flags=re.M),
                    encoding="utf-8")
    return path


class Case:
    """A synthetic take, room model and detector rows, written to a fresh folder."""

    def __init__(self, name, pairs=None, depth_bytes=None, *, rows=None, latency_s=0.3, check="PASS",
                 intrinsics=None, room=None):
        self.work = new_dir(name)
        pairs = pairs or frames()
        self.take = synth.make_take(self.work / "take", pairs, depth_bytes or depth(), intrinsics=intrinsics)
        if check:
            synth.write_check(self.take, verdict=check)
        self.room = self.work / "room.json"
        self.room.write_text(json.dumps(room or synth.room_model()), encoding="utf-8")
        self.rows_path = self.work / "rows.jsonl"
        if rows is None:
            rows = [fake_detector.row(t, rgb_seq=1000 + i, pixels=PIXELS, latency_s=latency_s)
                    for i, (t, _) in enumerate(pairs)]
        detections.write_rows(self.rows_path, rows)
        self.out = self.work / "out"

    def run(self, **kwargs):
        kwargs.setdefault("detections_path", self.rows_path)
        run = replay.replay(self.take, self.room, self.out, log=lambda *a: None, **kwargs)
        lines = (self.out / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        return run, [json.loads(line) for line in lines]


def inverse3(m):
    det = g.det3(m)
    cof = [[(m[(i + 1) % 3][(j + 1) % 3] * m[(i + 2) % 3][(j + 2) % 3]
             - m[(i + 1) % 3][(j + 2) % 3] * m[(i + 2) % 3][(j + 1) % 3]) for j in range(3)] for i in range(3)]
    return [[cof[j][i] / det for j in range(3)] for i in range(3)]


def mahalanobis(xyz, truth, cov):
    e = [a - b for a, b in zip(xyz, truth)]
    w = g.mat_vec(inverse3(cov), e)
    return math.sqrt(sum(a * b for a, b in zip(e, w)))


def torso_truth():
    points = [SCENE.joint_room(NAMES[j]) for j in observe.TORSO]
    return [sum(p[i] for p in points) / len(points) for i in range(3)]


def position(obs):
    rp = obs["room_position"]
    return [rp["x"], rp["y"], rp["z"]], rp["cov"]


def trace(cov):
    return cov[0][0] + cov[1][1] + cov[2][2]


class ContractTests(unittest.TestCase):
    def test_field_names_are_schemas_json_names(self):
        schema = json.loads(SCHEMAS.read_text(encoding="utf-8"))
        records = {r["id"]: r for r in schema["records"]}
        names = [f["name"] for f in records["observation"]["fields"]]
        self.assertEqual(list(observe.SCHEMA_FIELDS), names)
        camera_type = next(f["type"] for f in records["room_model"]["fields"] if f["name"] == "cameras")
        camera_fields = set(re.findall(r"\w+", camera_type))
        self.assertLessEqual(set(synth.camera_entry()), camera_fields)
        _, records_written = Case("contract").run()
        for obs in records_written:
            self.assertLessEqual(set(obs), set(names) | {"freshness"})
            self.assertEqual(obs["freshness"].keys(), {"available_ts", "age_s", "max_age_s", "status",
                                                       "valid_until", "depth", "depth_gap_s"})

    def test_every_record_passes_the_gate_checks(self):
        run, records = Case("gate-checks", frames(4, stale=(2,))).run()
        self.assertEqual(run["gate"]["records_with_problems"], 0, run["gate"]["problems"])
        for obs in records:
            self.assertEqual(observe.check_record(obs, lambda tid: tid == "den-oak@2026-11-12"), [])
            self.assertEqual(obs["joint_set"], "coco17")
            self.assertEqual(len(obs["joints_room"]), 17)
            self.assertEqual([e["j"] for e in obs["joints_room"]], list(range(17)))

    def test_the_gate_check_catches_what_it_should(self):
        _, records = Case("gate-negative").run()
        good = records[0]
        resolves = lambda tid: tid == "den-oak@2026-11-12"  # noqa: E731
        zeroed = json.loads(json.dumps(good))
        zeroed["joints_room"][0].update(status="unsupported", lift="none", xyz=[0, 0, 0])
        cases = {"no capture_ts": {k: v for k, v in good.items() if k != "capture_ts"},
                 "no freshness": {k: v for k, v in good.items() if k != "freshness"},
                 "unknown transform": dict(good, transform_id="den-oak@1999-01-01"),
                 "extra field": dict(good, colour="red"),
                 "zero-filled joint": zeroed,
                 "valid without position": {k: v for k, v in good.items() if k != "room_position"}}
        for name, obs in cases.items():
            with self.subTest(name):
                self.assertTrue(observe.check_record(obs, resolves))


class GeometryTests(unittest.TestCase):
    def test_known_geometry_gives_room_positions_within_their_covariance(self):
        _, records = Case("geometry").run()
        for obs in records:
            for entry in obs["joints_room"]:
                with self.subTest(obs=obs["obs_id"], joint=entry["j"]):
                    self.assertEqual((entry["status"], entry["lift"]), ("supported", "stereo"))
                    truth = SCENE.joint_room(NAMES[entry["j"]])
                    self.assertLess(math.dist(entry["xyz"], truth), 0.01)
                    self.assertLess(mahalanobis(entry["xyz"], truth, entry["cov"]), 1.0)
            xyz, cov = position(obs)
            self.assertLess(math.dist(xyz, torso_truth()), 0.01)
            self.assertLess(mahalanobis(xyz, torso_truth(), cov), 1.0)
            self.assertEqual(obs["validity"], "valid")
            self.assertEqual(obs["visibility"], "visible")

    def test_noisy_depth_stays_within_its_covariance(self):
        _, records = Case("noisy", frames(1), depth(noise_seed=7)).run()
        obs = records[0]
        for entry in obs["joints_room"]:
            with self.subTest(joint=entry["j"]):
                self.assertEqual(entry["lift"], "stereo")
                self.assertLess(mahalanobis(entry["xyz"], SCENE.joint_room(NAMES[entry["j"]]), entry["cov"]), 3.0)
        xyz, cov = position(obs)
        self.assertLess(mahalanobis(xyz, torso_truth(), cov), 3.0)

    def test_covariance_holds_depth_pixel_and_registration_terms(self):
        tight = synth.room_model([synth.camera_entry(error_bound_m=0.0)])
        _, records = Case("terms", frames(1), room=tight).run()
        _, loose = Case("terms-loose", frames(1)).run()
        entry, wide = records[0]["joints_room"][0], loose[0]["joints_room"][0]
        for i in range(3):  # the 3 cm registration bound adds 0.03^2 on every axis
            self.assertAlmostEqual(wide["cov"][i][i] - entry["cov"][i][i], 0.0009, places=6)
        # Along the viewing ray (about +y here) depth noise dominates; across it, pixel noise.
        z = SCENE.joint_left("nose")[2]
        along = z * z * 0.25 / (synth.LEFT_K["fx"] * synth.BASELINE_M)
        across = 4.0 * z / synth.RGB_K["fx"]
        self.assertGreater(entry["cov"][1][1], across ** 2)
        self.assertAlmostEqual(trace(entry["cov"]), along ** 2 + 2 * across ** 2, delta=0.1 * along ** 2)

    def test_boxes_lift_their_inset_region(self):
        row = fake_detector.row(5000.0, rgb_seq=1000, pixels=PIXELS)
        person = dict(row["detections"][0], id="p", keypoints=None)
        x0, y0, x1, y1 = person["box_xyxy"]
        cup = {"id": "c", "class": "cup", "score": 0.5, "keypoints": None,
               "box_xyxy": [x0 + 20, y0 + 60, x1 - 20, y0 + 120]}
        row = dict(row, detector={"boxes": "yolo26s", "keypoint_layout": "coco17"}, detections=[person, cup])
        _, records = Case("boxes", frames(1), rows=[row]).run()
        by_kind = {obs["kind"]: obs for obs in records}
        self.assertEqual(set(by_kind), {"person_box", "object_box"})
        self.assertEqual(by_kind["object_box"]["label"], "cup")
        self.assertNotIn("label", by_kind["person_box"])
        for obs in records:
            xyz, cov = position(obs)
            self.assertLess(abs(xyz[1] - synth.PANEL_Y) / math.sqrt(cov[1][1]), 3.0)
            self.assertEqual(obs["validity"], "valid")
            self.assertNotIn("joints_room", obs)


class DepthTimingTests(unittest.TestCase):
    def test_a_stale_depth_frame_takes_the_plane_fallback_with_a_flag(self):
        _, records = Case("stale", frames(2, stale=(1,))).run()
        fresh, stale = records
        self.assertEqual(stale["freshness"]["depth"], "too_old")
        self.assertAlmostEqual(stale["freshness"]["depth_gap_s"], 0.2, places=6)
        self.assertAlmostEqual(stale["capture_ts"] - stale["depth_ts"], 0.2, places=4)
        self.assertEqual(fresh["freshness"]["depth"], "used")
        for entry in stale["joints_room"]:
            with self.subTest(joint=entry["j"]):
                self.assertNotEqual(entry["lift"], "stereo")
                if entry["status"] == "uncertain":
                    h = float(entry["lift"].split(":")[1])
                    self.assertAlmostEqual(entry["xyz"][2], h, places=6)
                    self.assertLess(mahalanobis(entry["xyz"], SCENE.joint_room(NAMES[entry["j"]]), entry["cov"]), 3.0)
                else:
                    self.assertEqual((entry["xyz"], entry["cov"], entry["lift"]), (None, None, "none"))
        self.assertTrue(all(e["status"] == "uncertain" for e in stale["joints_room"]))
        xyz, cov = position(stale)
        self.assertLess(mahalanobis(xyz, torso_truth(), cov), 3.0)
        self.assertGreater(trace(cov), 10 * trace(position(fresh)[1]))
        self.assertEqual(stale["validity"], "valid")

    def test_depth_is_used_within_one_stereo_frame_either_side(self):
        pairs = [(5000.0, 5000.0 - 0.066), (5000.25, 5000.25 + 0.05), (5000.5, 5000.5 - 0.068)]
        _, records = Case("gap-edges", pairs).run()
        self.assertEqual([r["freshness"]["depth"] for r in records], ["used", "used", "too_old"])

    def test_invalid_depth_everywhere_is_absent(self):
        _, records = Case("absent", frames(1), bytes(640 * 400 * 2)).run()
        self.assertEqual(records[0]["freshness"]["depth"], "absent")
        self.assertTrue(all(e["lift"].startswith("plane:") for e in records[0]["joints_room"]))

    def test_a_box_without_usable_depth_has_no_position_and_is_rejected(self):
        row = fake_detector.row(5000.0, rgb_seq=1000, pixels=PIXELS)
        row = dict(row, detections=[dict(row["detections"][0], keypoints=None)])
        _, records = Case("stale-box", frames(1, stale=(0,)), rows=[row]).run()
        self.assertNotIn("room_position", records[0])
        self.assertEqual(records[0]["validity"], "rejected:no_room_position:depth_too_old")


class SupportTests(unittest.TestCase):
    def test_keypoint_support_and_lift(self):
        pixels = dict(PIXELS, right_wrist=None)
        row = fake_detector.row(5000.0, rgb_seq=1000, pixels=pixels, scores={"left_wrist": 0.1})
        # A depth hole over the face: the face joints (0-4) fall back to the head plane.
        left = g.project(SCENE.joint_left("nose"), g.intrinsics(synth.LEFT_K))
        hole = (int(left[0]) - 16, int(left[1]) - 16, int(left[0]) + 16, int(left[1]) + 16)
        _, records = Case("support", frames(1), depth(holes=[hole]), rows=[row]).run()
        obs = records[0]
        joints = {e["j"]: e for e in obs["joints_room"]}
        left_wrist, right_wrist, nose = (NAMES.index(n) for n in ("left_wrist", "right_wrist", "nose"))
        for j in (left_wrist, right_wrist):
            self.assertEqual(joints[j], {"j": j, "xyz": None, "cov": None, "status": "unsupported", "lift": "none"})
        self.assertEqual(obs["keypoints"][right_wrist], [None, None, None])
        self.assertEqual(obs["keypoints"][left_wrist][2], 0.1)
        for j in range(5):
            self.assertEqual((joints[j]["status"], joints[j]["lift"]), ("uncertain", "plane:1.55"))
        self.assertEqual(nose, 0)
        others = [e for j, e in joints.items() if j not in (left_wrist, right_wrist) and j >= 5]
        self.assertEqual(len(others), 10)
        self.assertTrue(all((e["status"], e["lift"]) == ("supported", "stereo") for e in others))
        self.assertFalse(any(e["xyz"] == [0, 0, 0] for e in obs["joints_room"]))
        self.assertEqual(obs["validity"], "valid")

    def test_torso_point_prefers_stereo_and_falls_back_to_planes(self):
        no_torso = {n: (p if n not in ("left_shoulder", "right_shoulder", "left_hip", "right_hip") else None)
                    for n, p in PIXELS.items()}
        rows = [fake_detector.row(5000.0, rgb_seq=1000, pixels=no_torso)]
        _, records = Case("no-torso", frames(1), rows=rows).run()
        self.assertNotIn("room_position", records[0])
        self.assertEqual(records[0]["validity"], "rejected:no_room_position:no_torso_joint")
        self.assertEqual(records[0]["visibility"], "occluded")
        one_hip = dict(PIXELS, left_hip=None)
        _, records = Case("one-hip", frames(1), rows=[fake_detector.row(5000.0, rgb_seq=1000, pixels=one_hip)]).run()
        self.assertEqual(records[0]["visibility"], "partial")
        truth = [SCENE.joint_room(n) for n in ("left_shoulder", "right_shoulder", "right_hip")]
        mean = [sum(p[i] for p in truth) / 3 for i in range(3)]
        self.assertLess(math.dist(position(records[0])[0], mean), 0.01)

    def test_a_detection_with_no_supported_joint_is_rejected(self):
        row = fake_detector.row(5000.0, rgb_seq=1000, pixels=PIXELS, scores={n: 0.1 for n in NAMES})
        _, records = Case("no-joints", frames(1), rows=[row]).run()
        self.assertEqual(records[0]["validity"], "rejected:no_supported_joints")
        self.assertTrue(all(e["status"] == "unsupported" for e in records[0]["joints_room"]))

    def test_a_box_at_the_frame_edge_is_partial(self):
        self.assertEqual(observe.visibility([0.0, 10.0, 100.0, 200.0], (640, 360), None), "partial")
        self.assertEqual(observe.visibility([10.0, 10.0, 100.0, 200.0], (640, 360), None), "visible")


class TransformIdTests(unittest.TestCase):
    def test_transform_id_resolves_to_exactly_one_camera_entry(self):
        model = synth.room_model([synth.camera_entry(), synth.camera_entry(camera_id="den-oak-2")])
        entry = roommodel.resolve(model, "den-oak@2026-11-12")
        self.assertEqual((entry["camera_id"], entry["version"]), ("den-oak", "2026-11-12"))
        _, records = Case("resolve").run()
        for obs in records:
            camera_id, version = roommodel.parse_transform_id(obs["transform_id"])
            self.assertEqual((camera_id, version), ("den-oak", "2026-11-12"))
            self.assertEqual(obs["source"]["sensor_id"], camera_id)

    def test_unresolvable_transform_ids_are_refused(self):
        twice = synth.room_model([synth.camera_entry(), synth.camera_entry()])
        for model, tid in ((synth.room_model(), "den-oak@2026-01-01"), (twice, "den-oak@2026-11-12"),
                           (synth.room_model(), "den-oak"), (synth.room_model(), "den-oak@"),
                           (synth.room_model(), "@2026-11-12"), (synth.room_model(), "a@b@c")):
            with self.subTest(tid=tid), self.assertRaises(roommodel.RegistrationRefused):
                roommodel.resolve(model, tid)


class RegistrationTests(unittest.TestCase):
    def refused(self, case, text, **kwargs):
        with self.assertRaises(replay.Refused) as caught:
            case.run(**kwargs)
        self.assertIn(text, str(caught.exception))
        self.assertFalse(case.out.exists() and any(case.out.iterdir()), "a refused run wrote something")

    def test_a_missing_registration_is_refused(self):
        self.refused(Case("missing", room=synth.room_model([])), "missing registration")
        self.refused(Case("other-camera", room=synth.room_model([synth.camera_entry(camera_id="hall")])),
                     "missing registration")
        case = Case("no-file")
        case.room.unlink()
        self.refused(case, "no readable RoomModel")

    def test_an_unvalidated_registration_is_refused(self):
        self.refused(Case("null-bound", room=synth.room_model([synth.camera_entry(error_bound_m=None)])),
                     "not validated")
        self.refused(Case("wide-bound", room=synth.room_model([synth.camera_entry(error_bound_m=0.08)])),
                     "rung 1")
        bad_pose = synth.camera_entry()
        bad_pose["pose"]["room_from_camera"][0][0] = 2.0
        self.refused(Case("scaled-pose", room=synth.room_model([bad_pose])), "orthonormal")
        words = synth.camera_entry()
        words.update(intrinsics="EEPROM (nominal)", pose="approximate visual alignment")
        self.refused(Case("words", room=synth.room_model([words])), "den-observe-camera-1")

    def test_a_superseded_registration_is_stale(self):
        model = synth.room_model([synth.camera_entry(), synth.camera_entry(version="2026-11-20")])
        case = Case("superseded", room=model)
        self.refused(case, "stale registration", transform_id="den-oak@2026-11-12")
        run, _ = case.run()
        self.assertEqual(run["transform_id"], "den-oak@2026-11-20")

    def test_frame_intrinsics_that_moved_make_the_registration_stale(self):
        same = {"rgb": synth.RGB_K, "depth": synth.LEFT_K}
        run, _ = Case("intrinsics-same", intrinsics=same).run()
        self.assertEqual(run["gate"]["records_with_problems"], 0)
        moved = {"rgb": dict(synth.RGB_K, cx=synth.RGB_K["cx"] + 3.0), "depth": synth.LEFT_K}
        self.refused(Case("intrinsics-moved", intrinsics=moved), "stale registration")

    def test_a_room_in_another_frame_is_refused(self):
        self.refused(Case("frame", room=dict(synth.room_model(), frame="y_up")), "z_up_metric_floor0")


class Rung1FileChecks:
    """The file den-register writes, as it stands after each step. `lines` is its three revisions:
    a first fit, a second fit from a later take, and check-mount's invalidated revision."""

    lines = ()

    def case(self, name, n, room_bytes=None):
        """A person in view of rung 1's registered camera, with rung 1's file as it stood after n lines."""
        first = json.loads(self.lines[0])["cameras"][0]
        scene = synth.Scene(room_from_left=g.from_matrix(first["pose"]["room_from_camera"], "pose"),
                            left_k=first["intrinsics"]["rectified_left"], panel_dx=2.6)
        pairs = frames(2)
        key = "rung1-" + json.dumps(first["pose"]["room_from_camera"])
        _DEPTH.setdefault(key, scene.depth_mm())
        pixels = {name: scene.joint_rgb(name) for name in synth.JOINTS}
        rows = [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels, latency_s=0.3) for i, (t, _) in enumerate(pairs)]
        case = Case(name, pairs, _DEPTH[key], rows=rows,
                    intrinsics={"rgb": first["intrinsics"]["rgb"], "depth": first["intrinsics"]["rectified_left"]})
        case.room = case.work / "room-model.jsonl"
        case.room.write_bytes(room_bytes if room_bytes is not None else "".join(
            line + "\n" for line in self.lines[:n]).encode("utf-8"))
        case.truth = [sum(scene.joint_room(NAMES[j])[i] for j in observe.TORSO) / 4 for i in range(3)]
        return case

    def transform_id(self, line):
        entry = json.loads(line)["cameras"][0]
        return f"{entry['camera_id']}@{entry['version']}"

    def assert_lifted(self, case, run, records, transform_id):
        self.assertEqual(run["transform_id"], transform_id)
        self.assertEqual(run["gate"]["records_with_problems"], 0, run["gate"]["problems"])
        self.assertEqual({r["transform_id"] for r in records}, {transform_id})
        self.assertEqual({r["validity"] for r in records}, {"valid"})
        for record in records:
            self.assertEqual(sum(j["lift"] == "stereo" for j in record["joints_room"]), 17)
            self.assertLess(math.dist(position(record)[0], case.truth), 0.05, "through the registered pose")

    def test_one_fit_gives_observations_in_the_room_frame(self):
        case = self.case("rung1-one", 1)
        run, records = case.run()
        self.assert_lifted(case, run, records, self.transform_id(self.lines[0]))

    def test_a_second_revision_is_read_and_its_entry_used(self):
        case = self.case("rung1-two", 2)
        run, records = case.run()
        self.assert_lifted(case, run, records, self.transform_id(self.lines[1]))

    def test_a_scored_run_passes_the_gate_on_rung_1s_file(self):
        case = self.case("rung1-scored", 2)
        run, records = case.run(scored=True, method_path=closed_method(case.work / "closed"))
        self.assert_lifted(case, run, records, self.transform_id(self.lines[1]))
        self.assertEqual(run["method"]["open_slots"], {})
        self.assertEqual(run["method"]["values_used"]["camera_entry_form"], "den-observe-camera-1")
        self.assertTrue(run["gate"]["pass"], run["gate"])

    def test_the_first_fit_is_stale_after_the_second(self):
        case = self.case("rung1-stale", 2)
        with self.assertRaises(replay.Refused) as caught:
            case.run(transform_id=self.transform_id(self.lines[0]))
        self.assertIn("stale registration", str(caught.exception))
        self.assertFalse(case.out.exists())

    def test_a_revision_check_mount_invalidated_is_refused_and_nothing_written(self):
        case = self.case("rung1-invalidated", 3)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = replay.main(["--take", str(case.take), "--room-model", str(case.room), "--detections",
                                str(case.rows_path), "--out", str(case.out)])
        self.assertEqual(code, 2)
        self.assertIn("invalidated", err.getvalue())
        self.assertIn(self.transform_id(self.lines[1]), err.getvalue())
        self.assertFalse(case.out.exists())

    def test_a_torn_last_line_is_refused_not_skipped(self):
        torn = "".join(line + "\n" for line in self.lines[:1]) + self.lines[1][:200]
        case = self.case("rung1-torn", 2, room_bytes=torn.encode("utf-8"))
        with self.assertRaises(replay.Refused) as caught:
            case.run()
        self.assertIn("no readable RoomModel", str(caught.exception))
        self.assertFalse(case.out.exists())

    def test_crlf_and_a_byte_order_mark_read_the_same(self):
        body = "".join(line + "\r\n" for line in self.lines[:2]).encode("utf-8")
        case = self.case("rung1-crlf", 2, room_bytes=b"\xef\xbb\xbf" + body)
        run, records = case.run()
        self.assert_lifted(case, run, records, self.transform_id(self.lines[1]))


class Rung1FileTests(Rung1FileChecks, unittest.TestCase):
    """A copy of den-register's real output, so these run on a branch that does not hold den-register."""

    lines = tuple(RUNG1_FILE.read_text(encoding="utf-8").splitlines())

    def test_an_empty_file_or_a_line_that_is_not_an_object_is_refused(self):
        for name, data in (("empty", b""), ("array", b"[1, 2]\n"), ("number", b'{"room_id": "den"}\n7\n')):
            with self.subTest(name):
                case = self.case(f"rung1-{name}", 1, room_bytes=data)
                with self.assertRaises(replay.Refused):
                    case.run()
                self.assertFalse(case.out.exists())


@unittest.skipUnless((DEN_REGISTER / "register.py").exists(),
                     "den-register is on its own branch until they merge; set DEN_REGISTER_DIR to run this")
class RealRung1RunTests(Rung1FileChecks, unittest.TestCase):
    """The seam itself: den-register's own synthetic fit writes the file, then den-observe reads it."""

    @classmethod
    def setUpClass(cls):
        out = new_dir("rung1-real-run")
        done = subprocess.run([sys.executable, "-B", str(HERE / "testdata" / "rung1_run.py"), str(DEN_REGISTER), str(out)],
                              capture_output=True, text=True, cwd=str(_tmp), timeout=120)
        if done.returncode:
            raise AssertionError(f"rung 1's own run failed: {done.stdout}{done.stderr}")
        cls.lines = tuple((out / "room-model.jsonl").read_text(encoding="utf-8").splitlines())

    def test_the_copy_in_testdata_still_has_the_form_den_register_writes(self):
        real = json.loads(self.lines[0])["cameras"][0]
        copy = json.loads(RUNG1_FILE.read_text(encoding="utf-8").splitlines()[0])["cameras"][0]
        self.assertEqual(set(real), set(copy))
        self.assertEqual(set(real["intrinsics"]), set(copy["intrinsics"]))
        self.assertEqual(set(real["pose"]), set(copy["pose"]))


class FreshnessTests(unittest.TestCase):
    def test_freshness_is_completion_minus_capture(self):
        pairs = frames(2)
        rows = [fake_detector.row(pairs[0][0], rgb_seq=1000, pixels=PIXELS, latency_s=0.4),
                fake_detector.row(pairs[1][0], rgb_seq=1001, pixels=PIXELS, latency_s=1.5)]
        _, records = Case("ages", pairs, rows=rows).run()
        quick, slow = (r["freshness"] for r in records)
        self.assertEqual((quick["age_s"], quick["status"], quick["max_age_s"]), (0.4, "fresh", 1.0))
        self.assertEqual((slow["age_s"], slow["status"]), (1.5, "expired"))
        self.assertAlmostEqual(quick["valid_until"] - records[0]["capture_ts"], 3.0, places=5)
        self.assertAlmostEqual(quick["available_ts"] - records[0]["capture_ts"], 0.4, places=5)
        self.assertEqual(records[1]["validity"], "rejected:detector_result_expired")

    def test_rows_without_a_completion_time_have_unknown_freshness(self):
        _, records = Case("unknown-age", frames(1), latency_s=None).run()
        self.assertEqual((records[0]["freshness"]["status"], records[0]["freshness"]["age_s"]), ("unknown", None))

    def test_an_open_freshness_slot_refuses_a_scored_run(self):
        case = Case("scored-open")
        with self.assertRaises(replay.Refused) as caught:
            case.run(scored=True)
        self.assertIn("detector_max_age_s", str(caught.exception))
        only = closed_method(case.work / "method-one-open", keep_open=("detector_max_age_s",))
        with self.assertRaises(replay.Refused) as caught:
            case.run(scored=True, method_path=only)
        self.assertIn("open: detector_max_age_s (proposed 1.0)", str(caught.exception))
        self.assertFalse(case.out.exists())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = replay.main(["--take", str(case.take), "--room-model", str(case.room), "--detections",
                                str(case.rows_path), "--out", str(case.out), "--scored"])
        self.assertEqual(code, 2)
        self.assertIn("detector_max_age_s", err.getvalue())
        self.assertFalse(case.out.exists())

    def test_a_scored_run_with_every_slot_closed(self):
        case = Case("scored", frames(4))
        closed = closed_method(case.work / "closed")
        run, records = case.run(scored=True, method_path=closed)
        self.assertTrue(run["scored"])
        self.assertEqual(run["method"]["open_slots"], {})
        self.assertTrue(run["gate"]["pass"], run["gate"])
        self.assertEqual(run["gate"]["freshness"]["baseline"]["expired"], 115)
        late = Case("scored-late", frames(4), latency_s=1.2)
        with contextlib.redirect_stdout(io.StringIO()):
            code = replay.main(["--take", str(late.take), "--room-model", str(late.room), "--detections",
                                str(late.rows_path), "--out", str(late.out), "--scored", "--method", str(closed)])
        self.assertEqual(code, 1)
        gate = json.loads((late.out / "run.json").read_text(encoding="utf-8"))["gate"]
        self.assertEqual((gate["freshness"]["expired_fraction"], gate["pass"]), (1.0, False))

    def test_a_scored_run_needs_a_slate_clock_completion_times_and_real_rows(self):
        closed = closed_method(new_dir("closed-shared"))
        for case, kwargs, text in ((Case("scored-no-check", check=None), {}, "PASS"),
                                   (Case("scored-failed-check", check="FAIL"), {}, "PASS"),
                                   (Case("scored-no-times", latency_s=None), {}, "available_ts"),
                                   (Case("scored-fake"), {"fake": True, "detections_path": None}, "--fake")):
            with self.subTest(case.work.name), self.assertRaises(replay.Refused) as caught:
                case.run(scored=True, method_path=closed, **kwargs)
            self.assertIn(text, str(caught.exception))


class ClockTests(unittest.TestCase):
    def test_the_slate_maps_host_time_to_unix(self):
        run, records = Case("clock").run()
        self.assertEqual(run["clock"]["source"], "slate")
        self.assertAlmostEqual(run["clock"]["offset_s"], synth.UNIX_OFFSET_S, places=5)
        self.assertAlmostEqual(records[0]["capture_ts"], 5000.0 + synth.UNIX_OFFSET_S, places=5)

    def test_without_a_passing_check_the_arrival_clock_is_used(self):
        for name, verdict in (("clock-none", None), ("clock-fail", "FAIL")):
            with self.subTest(name):
                take = takes.Take(Case(name, frames(1), check=verdict).take)
                clock = take.clock()
                self.assertEqual(clock["source"], "arrival")
                self.assertAlmostEqual(clock["offset_s"], synth.UNIX_OFFSET_S + synth.ARRIVAL_S, places=5)

    def test_the_newest_check_counts(self):
        case = Case("clock-newest", frames(1))
        synth.write_check(case.take, verdict="FAIL")
        self.assertEqual(takes.Take(case.take).clock()["source"], "arrival")


class MethodTests(unittest.TestCase):
    def test_reads_the_same_with_crlf_and_lf(self):
        path = new_dir("method-crlf") / "METHOD.md"
        path.parent.mkdir()
        path.write_bytes((HERE / "METHOD.md").read_bytes().replace(b"\n", b"\r\n"))
        lf, crlf = method_file.load(), method_file.load(path)
        self.assertEqual((lf.values, lf.open, lf.sha256), (crlf.values, crlf.open, crlf.sha256))
        self.assertIn("detector_max_age_s", lf.open)
        self.assertEqual(lf.open["detector_max_age_s"], 1.0)

    def test_refuses_a_malformed_block(self):
        text = (HERE / "METHOD.md").read_text(encoding="utf-8")
        cases = {"no proposal": text.replace("open     # open, proposed 1.0", "open     # open"),
                 "unknown key": text.replace("max_validity_s = 3.0", "max_validity_s = 3.0\nspeed = 2"),
                 "missing key": text.replace("max_validity_s = 3.0", ""),
                 "another form": text.replace('proposed "freshness"', 'proposed "age"'),
                 "two blocks": text + "\n```observe-v1\n```\n"}
        for name, bad in cases.items():
            with self.subTest(name), self.assertRaises(method_file.MethodError):
                method_file.parse(bad)


class ReplayTests(unittest.TestCase):
    def test_the_demo_runs_the_whole_path_offline(self):
        out = new_dir("demo")
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(replay.main(["--demo", "--out", str(out)]), 0)
        self.assertIn("UNSCORED", printed.getvalue())
        body = (out / "observations.jsonl").read_bytes()
        self.assertNotIn(b"\r", body)
        records = [json.loads(line) for line in body.decode("utf-8").splitlines()]
        self.assertEqual(len(records), 6)
        self.assertEqual([r["freshness"]["depth"] for r in records].count("too_old"), 1)
        run = json.loads((out / "run.json").read_text(encoding="utf-8"))
        self.assertEqual((run["scored"], run["gate"]["records_with_problems"]), (False, 0))
        self.assertEqual(len(run["method"]["open_slots"]), 16)

    def test_the_fake_detector_replays_any_take(self):
        case = Case("fake")
        run, records = case.run(fake=True, detections_path=None)
        self.assertEqual(run["detector"], "fake-scripted-1")
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["source"]["model"], "fake-scripted-1")

    def test_refuses_bad_outputs_and_rows(self):
        case = Case("outputs")
        with self.assertRaises(replay.Refused):
            replay.replay(case.take, case.room, "relative/out", detections_path=case.rows_path, log=lambda *a: None)
        checkout = new_dir("checkout")
        (checkout / ".git").mkdir(parents=True)
        with self.assertRaises(replay.Refused) as caught:
            replay.replay(case.take, case.room, checkout / "out", detections_path=case.rows_path,
                          log=lambda *a: None)
        self.assertIn("git checkout", str(caught.exception))
        case.run()
        with self.assertRaises(replay.Refused):
            case.run()  # never overwrites
        stray = Case("stray-row", frames(1), rows=[fake_detector.row(4000.0, rgb_seq=77, pixels=PIXELS)])
        with self.assertRaises(replay.Refused) as caught:
            stray.run()
        self.assertIn("not in the take", str(caught.exception))
        unix = Case("unix-row", frames(1), rows=[fake_detector.row(5000.0, rgb_seq=1000, pixels=PIXELS,
                                                                   clock="unix")])
        with self.assertRaises(replay.Refused):
            unix.run()


if __name__ == "__main__":
    unittest.main()
