"""Tests for detect.py (plan, "Done means", tests 6 and 19, and "Could not verify"):

- the Lab files it imports close: every import resolves inside detect.TREE at the pin, and chain.py
  archives the same tree;
- it refuses Python sockets before anything else, and the lock pins the venv and the weights: a distribution
  the lock doesn't name is refused too, and main refuses an interpreter that isn't the lock's;
- the reproduce check compares each person's box and joints within 1 px, and its scores within 0.001;
- the usable part's bounds, a frame held to its digest and timed by its compute only, and main counting a
  frame over 1.0 s, on stand-ins for the models;
- on a prepared set, in the venv: the model reproduces a take's recorded rows, a fake's rows relabelled
  with the real models' names are refused at that check, and RTMPose's left and right on the figure
  against the truth's joints (this test may read the truth: nothing downstream does).

Each has a control that must make it fail. The model parts need the venv at %USERPROFILE%\\ml\\den-detector
(or DEN_DETECT_PYTHON) and DEN_SIM_SET=<a prepared set>, and skip without them. They only read the set.

    python -B -X utf8 tools\\den-detect\\test_detect.py
"""
import ast
import io
import json
import math
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / "tools/den-sim")]
import detect  # noqa: E402

REFS = json.loads((ROOT / "tools/den-sim/refs.json").read_text(encoding="utf-8"))["tools"]
PIN = subprocess.run(["git", "-C", str(ROOT), "rev-parse", REFS["rung-2-observe"] + "^{commit}"], capture_output=True, text=True, check=True).stdout.strip()
VENV = Path(os.environ.get("DEN_DETECT_PYTHON") or Path.home() / "ml/den-detector/Scripts/python.exe")
SET = Path(os.environ["DEN_SIM_SET"]).resolve() if os.environ.get("DEN_SIM_SET") else None
NEEDS_MODELS = unittest.skipUnless(VENV.is_file() and SET and (SET / "display.json").exists(), "needs the detector's venv and DEN_SIM_SET=<a prepared set>")
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}


def tree(folder, paths=detect.TREE):
    done = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", PIN, *paths], capture_output=True, check=True)
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as tar:
        tar.extractall(folder, filter="data")
    return folder


IMPORTS = ("import sys; sys.path[:0] = [r'{0}/stack/services/perception-lab', r'{0}/tools/den-observe', r'{0}/tools/den-take']\n"
           "import ultralytics_worker, rtmpose_worker, apartment_desk_preview, la_detector_app, room_mapper, detections, check\n"
           "from pathlib import Path\n"
           "print(sorted(m for m, v in sys.modules.items() if getattr(v, '__file__', None) and Path(v.__file__).resolve().is_relative_to(Path(r'{0}').resolve())))")


class Closure(unittest.TestCase):
    def test_every_import_closes_inside_the_tree_at_the_pin(self):
        with tempfile.TemporaryDirectory(prefix="den-detect-tree-") as tmp:
            root = tree(Path(tmp)).as_posix()
            done = subprocess.run([sys.executable, "-I", "-B", "-c", IMPORTS.format(root)], capture_output=True, text=True, env=ENV)
            self.assertEqual(done.returncode, 0, done.stderr)
            loaded = {name for name in eval(done.stdout)}
            self.assertEqual({Path(p).stem for p in detect.WORKERS} - loaded, set(), "every Lab file in the tree is one the detector imports")
            os.remove(Path(root) / "stack/services/perception-lab/host_home.py")
            control = subprocess.run([sys.executable, "-I", "-B", "-c", IMPORTS.format(root)], capture_output=True, text=True, env=ENV)
            self.assertNotEqual(control.returncode, 0, "control: one file short, the import does not close")
            self.assertIn("host_home", control.stderr)

    def test_chain_archives_the_same_tree(self):
        import chain
        self.assertEqual(chain.TREES["observe"], ("rung-2-observe", list(detect.TREE)))


class Guard(unittest.TestCase):
    PROBE = ("import sys; sys.path.insert(0, r'%s'); import detect\n%s\nimport socket\n"
             "try:\n    socket.getaddrinfo('127.0.0.1', 9)\n    print('NOT REFUSED')\nexcept PermissionError as error:\n    print('refused:', error)\n")

    def test_python_sockets_are_refused_before_anything_else(self):
        run = lambda guard: subprocess.run([sys.executable, "-B", "-c", self.PROBE % (HERE, guard)], capture_output=True, text=True, env=ENV).stdout.strip()
        self.assertEqual(run("detect.guard()"), "refused: den-detect has no network (socket.getaddrinfo)")
        self.assertEqual(run("pass"), "NOT REFUSED", "control: without the guard the lookup goes through")
        main = next(node for node in ast.parse((HERE / "detect.py").read_text(encoding="utf-8")).body if isinstance(node, ast.FunctionDef) and node.name == "main")
        self.assertEqual(ast.unparse(main.body[0]), "guard()", "main refuses sockets before it parses, reads or imports anything")

    def test_the_same_list_as_every_other_den_sim_process(self):
        text = (ROOT / "tools/den-sim/scene/prepare.py").read_text(encoding="utf-8")
        listed = ast.literal_eval(text.split("\nNETWORK = ", 1)[1].split("\n\n", 1)[0])
        self.assertEqual(detect.NETWORK, listed)


class Lock(unittest.TestCase):
    def test_the_lock_pins_the_venv_and_the_weights(self):
        pinned = detect.lock()
        self.assertEqual(pinned["weights"], {"yolo26s.pt": "646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b",
                                             "rtmpose-m-body7.onnx": "5c0a4bf67953e6d2ac43ce15e77dc9d5d354ae18430a47d2c5963a7bc5683e3c",
                                             "artifact.json": "1bd8ad0a3828efc9d5b86b603ceee0639d3e7d674db98dfc3d85baa291740585"})
        want = {"torch": "2.10.0+cpu", "torchvision": "0.25.0+cpu", "ultralytics": "8.4.0", "onnxruntime": "1.23.2", "rtmlib": "0.0.16",
                "numpy": "1.26.4", "opencv-python": "4.11.0.86", "pillow": "13.0.0"}
        self.assertEqual({k: pinned["versions"][k] for k in want}, want)
        if VENV.is_file():   # measured, read only: the venv's own distributions against the lock
            found = subprocess.run([str(VENV), "-I", "-B", "-c", "import json, importlib.metadata as m; print(json.dumps({d.metadata['Name']: d.version for d in m.distributions()}))"],
                                   capture_output=True, text=True, check=True).stdout
            self.assertEqual(json.loads(found), pinned["versions"])
        text = (HERE / "requirements.lock").read_text(encoding="utf-8")
        self.assertIn("py -3.12 -m venv $env:USERPROFILE\\ml\\den-detector", text, "the install lines are there to rebuild it")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "lock").write_text(text.replace("ultralytics==8.4.0", "ultralytics==8.4.1"), encoding="utf-8")
            self.assertNotEqual(detect.lock(Path(tmp) / "lock")["versions"], pinned["versions"], "control: a changed line is told apart")

    def test_a_distribution_the_lock_does_not_name_is_told(self):
        pinned = detect.lock()["versions"]
        self.assertEqual(detect.unlocked(pinned, dict(pinned)), [], "control: the lock's own distributions")
        self.assertEqual(detect.unlocked(pinned, {**pinned, "planted-extra": "1.0"}), ["planted-extra 1.0 (the lock: not listed)"])
        self.assertEqual(detect.unlocked(pinned, {**pinned, "ultralytics": "8.4.1"}), ["ultralytics 8.4.1 (the lock: 8.4.0)"])
        short = dict(pinned)
        del short["rtmlib"]
        self.assertEqual(detect.unlocked(pinned, short), ["rtmlib missing (the lock: 0.0.16)"])

    def test_names_are_compared_as_pep_503_normalises_them(self):
        pinned = {"Jinja2": "3.1.6", "typing_extensions": "4.16.0", "cuda-bindings": "12.9.4"}
        self.assertEqual(detect.unlocked(pinned, {"jinja2": "3.1.6", "typing-extensions": "4.16.0", "cuda_bindings": "12.9.4"}), [])
        self.assertEqual(detect.unlocked(pinned, {"jinja2": "3.1.6", "typing-extensions": "4.16.0", "cuda_bindings": "12.9.3"}),
                         ["cuda-bindings 12.9.3 (the lock: 12.9.4)"], "control: a version still differs")

    def test_the_cuda_lock_is_the_cpu_lock_with_las_cuda_build(self):
        """requirements-cuda.lock is requirements.lock's distributions at the same versions, the same weights, CUDA 12.8
        torch and torchvision, onnxruntime-gpu for onnxruntime, and CUDA torch's Linux needs at the versions the LA
        trial resolved and ran (tools/la-detector-trial/requirements.lock)."""
        cpu, cuda = detect.lock(), detect.lock(detect.LOCK_CUDA)
        self.assertEqual(cuda["weights"], cpu["weights"])
        swapped = {"torch": "2.10.0+cu128", "torchvision": "0.25.0+cu128", "onnxruntime-gpu": "1.23.2"}
        self.assertEqual({k: cuda["versions"][k] for k in swapped}, swapped)
        self.assertNotIn("onnxruntime", cuda["versions"], "onnxruntime-gpu replaces it: both would clash")
        same = {k: v for k, v in cpu["versions"].items() if k not in ("torch", "torchvision", "onnxruntime")}
        self.assertEqual({k: cuda["versions"][k] for k in same}, same)
        added = set(cuda["versions"]) - set(cpu["versions"]) - set(swapped)
        self.assertTrue(added and all(k.startswith(("nvidia-", "cuda-")) or k == "triton" for k in added), added)
        trial = {}
        for line in (ROOT / "tools/la-detector-trial/requirements.lock").read_text(encoding="utf-8").splitlines():
            if "==" in line and not line.startswith((" ", "#")):
                name, _, version = line.split(" ")[0].partition("==")
                trial[name] = version
        self.assertEqual({k: cuda["versions"][k] for k in added}, {k: trial[k] for k in added})
        trial_text = (ROOT / "tools/la-detector-trial/requirements.lock").read_text(encoding="utf-8")
        urls = {line.split()[2]: line.split()[3] for line in detect.LOCK_CUDA.read_text(encoding="utf-8").splitlines()
                if line.startswith("# url ")}
        self.assertEqual(set(urls), {"torch", "torchvision"})
        for name, url in urls.items():
            self.assertIn("%s @ %s" % (name, url), trial_text, "the very wheel and sha256 the LA trial installed")
            self.assertIn("/%s-%s-" % (name, cuda["versions"][name].replace("+", "%2B")), url, "the lock's own version")
            self.assertRegex(url, r"#sha256=[0-9a-f]{64}$")

    def test_a_venv_is_held_to_the_lock_of_its_torch(self):
        self.assertEqual(detect.lock_for({"torch": "2.10.0+cu128"}), detect.lock(detect.LOCK_CUDA))
        self.assertEqual(detect.lock_for({"Torch": "2.10.0+cpu"}), detect.lock())
        self.assertEqual(detect.lock_for({}), detect.lock(), "no torch at all: the CPU lock, which then names it missing")

    def test_cuda_is_used_only_with_a_device_and_room_on_it(self):
        def torch(available=True, free=10 * 2 ** 30, total=96 * 2 ** 30, version="2.10.0+cu128", fails=False):
            capped = []

            def info(_):
                if fails:
                    raise RuntimeError("driver")
                return free, total
            cuda = SimpleNamespace(is_available=lambda: available, mem_get_info=info,
                                   set_per_process_memory_fraction=lambda fraction, device: capped.append((fraction, device)))
            return SimpleNamespace(__version__=version, cuda=cuda), capped
        fake, capped = torch()
        with mock.patch.dict(sys.modules, {"torch": fake}):
            self.assertEqual(detect.gpu_usable(), (True, None))
        self.assertEqual(capped, [(detect.GPU_CAP_BYTES / (96 * 2 ** 30), 0)], "torch's allocator is capped")
        for case, why in ((dict(available=False, version="2.10.0+cpu"), "no CUDA device"),
                          (dict(available=False), "CUDA torch, but no usable device"),
                          (dict(free=2 * 2 ** 30), "only 2.1 GB of the GPU free (3 GB needed)"),
                          (dict(fails=True), "CUDA didn't start (RuntimeError)")):
            fake, capped = torch(**case)
            with mock.patch.dict(sys.modules, {"torch": fake}):
                self.assertEqual(detect.gpu_usable(), (False, why), case)
            self.assertEqual(capped, [], "no cap without the GPU")

    def test_main_refuses_an_interpreter_that_is_not_the_locks(self):
        """This PC's own Python is not the venv: main refuses before it reads the take or loads a model."""
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run([sys.executable, "-B", str(HERE / "detect.py"), str(Path(tmp) / "no-take"), "--tools-root", tmp, "--state", str(Path(tmp) / "state")],
                                  capture_output=True, text=True, env={**ENV, "TEMP": tmp, "TMP": tmp}, cwd=tmp)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertRegex(done.stderr.strip(), r"^refused: the venv is not requirements\.lock's: .+ \(the lock: ")


def row(seq, *found):
    """A den-detections-1 row's frame and detections: (class, box) or (class, box, score, keypoints)."""
    items = [(*item, 0.9, None)[:4] for item in found]
    return {"frame": {"rgb_seq": seq}, "detections": [{"class": cls, "box_xyxy": box, "score": score, "keypoints": kp} for cls, box, score, kp in items]}


class FitToFrame(unittest.TestCase):
    """One stray box never costs the frame: every row fit_to_frame leaves, den-observe's own validate takes (at the pin)."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        tree(cls.folder.name, ("tools/den-observe",))
        cls.path = str(Path(cls.folder.name) / "tools/den-observe")
        sys.path.insert(0, cls.path)
        sys.modules.pop("detections", None)
        import detections
        cls.detections = detections

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(cls.path)
        sys.modules.pop("detections", None)
        cls.folder.cleanup()

    @staticmethod
    def box(key, x, y, w, h, label="person", score=0.9):
        return {"x": x, "y": y, "w": w, "h": h, "label": label, "confidence": score, "instance_id": key}

    @staticmethod
    def result(boxes):
        boxes = [dict(b) for b in boxes]
        return {"coordinate_space": "source_pixels", "boxes": boxes, "pose_instances": [],
                "objects": [{"instance_id": b["instance_id"], "label": b["label"]} for b in boxes]}

    def row(self, result):
        return self.detections.from_perception_lab(result, capture_ts=10.0, available_ts=10.1, rgb_seq=1, width=detect.WIDTH,
                                                   height=detect.HEIGHT, boxes_version="yolo26s-8.4.0", keypoints_version="k")

    def test_each_row_the_validator_refused_is_taken_once_fitted(self):
        person = self.box("p", 300.0, 50.0, 80.0, 200.0)
        cases = {
            "past the right edge": [self.box("a", 600.0, 10.0, 40.5, 100.0)],
            "a hair past by rounding": [self.box("a", 600.1, 10.0, 39.9 + 1e-9, 100.0)],
            "left of the frame": [self.box("a", -3.0, 10.0, 50.0, 100.0)],
            "below the frame": [self.box("a", 10.0, 300.0, 50.0, 61.0)],
            "no area past the edge": [self.box("a", 640.0, 10.0, 5.0, 100.0), person],
            "not finite": [self.box("a", float("nan"), 10.0, 5.0, 100.0), person],
            "a score over one": [self.box("a", 10.0, 10.0, 50.0, 100.0, score=1.5), person],
            "a repeated id": [self.box("a", 10.0, 10.0, 50.0, 100.0), self.box("a", 100.0, 10.0, 50.0, 100.0)],
            "too many": [self.box(str(i), float(i), 0.0, 5.0, 5.0, label="book", score=0.3) for i in range(70)]
                        + [self.box("p", 300.0, 50.0, 80.0, 200.0, score=0.2)],
        }
        for name, boxes in cases.items():
            with self.subTest(name):
                with self.assertRaises(self.detections.RowError, msg="the frame was lost before the fit"):
                    self.row(self.result(boxes))
                result = self.result(boxes)
                detect.fit_to_frame(result)
                row = self.row(result)
                self.assertTrue(row["detections"])
                for d in row["detections"]:
                    x0, y0, x1, y1 = d["box_xyxy"]
                    self.assertTrue(0 <= x0 < x1 <= detect.WIDTH and 0 <= y0 < y1 <= detect.HEIGHT, (name, d["box_xyxy"]))
                self.assertEqual({o["instance_id"] for o in result["objects"]}, {b["instance_id"] for b in result["boxes"]})

    def test_people_stay_and_in_frame_boxes_are_untouched(self):
        boxes = [self.box(str(i), float(i), 0.0, 5.0, 5.0, label="book", score=0.9) for i in range(70)]
        boxes += [self.box("p", 300.0, 50.0, 80.0, 200.0, score=0.2), self.box("q", 610.0, 40.0, 60.0, 300.0, score=0.7)]
        result = self.result(boxes)
        dropped = detect.fit_to_frame(result)
        kept = {b["instance_id"]: b for b in result["boxes"]}
        self.assertEqual(len(kept), detect.ROW_DETECTIONS)
        self.assertEqual(dropped, len(boxes) - detect.ROW_DETECTIONS)
        self.assertIn("p", kept, "a person is kept before any object, however low its score")
        self.assertEqual((kept["q"]["x"], kept["q"]["w"]), (610.0, 30.0), "clipped at the right edge")
        self.assertEqual((kept["p"]["x"], kept["p"]["y"], kept["p"]["w"], kept["p"]["h"]), (300.0, 50.0, 80.0, 200.0))

    def test_run_crops_people_from_the_fitted_boxes(self):
        crops = []
        detector = detect.Detector.__new__(detect.Detector)
        detector.yolo = SimpleNamespace(infer=lambda bgr, **kw: self.result([self.box("p", 590.0, -8.0, 70.0, 380.0)]))
        detector.distinct_people = lambda result: [o for o in result["objects"] if o["label"] == "person"]

        def pose(bgr, given, **kw):
            crops.extend(given)
            return {"pose_instances": []}
        detector.pose = SimpleNamespace(infer=pose)
        result, merged, _ = detector.run(None, "rgb-1")
        self.assertEqual(merged, 0)
        self.assertEqual(crops[0]["xyxy"], [590.0, 0.0, 640.0, 360.0], "the pose crop lies inside the frame")
        self.row(result)


class Compare(unittest.TestCase):
    def test_person_boxes_within_one_pixel(self):
        recorded = [row(1, ("person", [10, 20, 110, 300]), ("book", [50, 50, 70, 70])), row(2)]
        self.assertEqual(detect.compare(recorded, [row(1, ("person", [10.6, 20, 110, 299.2])), row(2)]), (True, 0.8, None))
        ok, worst, why = detect.compare(recorded, [row(1, ("person", [11.5, 20, 110, 300])), row(2)])
        self.assertEqual((ok, worst), (False, 1.5))
        self.assertIn("over 1 px", why)
        self.assertFalse(detect.compare(recorded, [row(1), row(2)])[0], "a person the model no longer finds")
        self.assertFalse(detect.compare(recorded, [row(3)])[0], "a frame with no recorded row")

    def test_scores_and_joints_too(self):
        """A channel swap moved boxes 0.74 px but a score 0.08: the check holds the scores and the joints as well."""
        joints = [[100.0 + j, 50.0 + j, 0.9] for j in range(17)]
        recorded = [row(1, ("person", [10, 20, 110, 300], 0.91, joints))]
        same = lambda **change: row(1, ("person", [10, 20, 110, 300], change.get("score", 0.91), change.get("keypoints", joints)))
        self.assertEqual(detect.compare(recorded, [same()]), (True, 0.0, None), "control: the same person holds")
        self.assertIn("a person's score moved 0.0600", detect.compare(recorded, [same(score=0.85)])[2])
        moved = [list(p) for p in joints]
        moved[9][0] += 1.5
        self.assertEqual(detect.compare(recorded, [same(keypoints=moved)])[:2], (False, 1.5))
        lower = [list(p) for p in joints]
        lower[9][1] += 1.5
        self.assertEqual(detect.compare(recorded, [same(keypoints=lower)])[:2], (False, 1.5), "a joint moved in y only")
        faint = [list(p) for p in joints]
        faint[3][2] = 0.5
        self.assertIn("a joint's score moved", detect.compare(recorded, [same(keypoints=faint)])[2])
        self.assertIn("17 joints recorded on a person, the model gives 0", detect.compare(recorded, [same(keypoints=None)])[2], "no crop, no joints")
        gone = [list(p) for p in joints]
        gone[4] = None
        self.assertIn("one side only", detect.compare(recorded, [same(keypoints=gone)])[2])


def fake_take(folder, frames, span):
    """frames.csv and one check-1.json whose usable part is frames span[0] to span[1]: what usable() reads."""
    folder.mkdir(parents=True)
    lines = ["n,rgb_seq,t_host_s,file,npz_sha256"] + [f"{n},{100 + n},{10 + n * 0.25},frames/{n}.npz,{'0' * 64}" for n in range(1, frames + 1)]
    (folder / "frames.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (folder / "check-1.json").write_text(json.dumps({"usable": {"segments": [{"first_n": span[0], "last_n": span[1]}]}}), encoding="utf-8")
    return folder


class Usable(unittest.TestCase):
    def test_both_bounds_are_in_and_one_check_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            take = fake_take(Path(tmp) / "take", 8, (3, 6))
            check, rows = detect.usable(take)
            self.assertEqual((check.name, [r["n"] for r in rows]), ("check-1.json", ["3", "4", "5", "6"]))
            (take / "check-2.json").write_text((take / "check-1.json").read_text(encoding="utf-8"), encoding="utf-8")
            with self.assertRaisesRegex(detect.Refused, "2 check-N.json files"):
                detect.usable(take)
            (take / "check-2.json").unlink()
            (take / "check-1.json").write_text(json.dumps({"usable": {"segments": [{"first_n": 20, "last_n": 30}]}}), encoding="utf-8")
            with self.assertRaisesRegex(detect.Refused, "usable part is empty"):
                detect.usable(take)


class Frame(unittest.TestCase):
    """Detector.frame on stand-ins for the models: the frame is held to frames.csv's digest, and available_ts is
    the capture time plus this frame's compute only."""

    def test_held_to_its_digest_and_timed_by_its_compute_only(self):
        import numpy
        with tempfile.TemporaryDirectory() as tmp:
            take = Path(tmp)
            (take / "f.npz").write_bytes(b"frame bytes")
            stub = SimpleNamespace(np=numpy, check=SimpleNamespace(read_bundle=lambda data, with_rgb: (None, bytes(detect.WIDTH * detect.HEIGHT * 3 // 2))),
                                   decode=lambda nv12, width, height: numpy.zeros((height, width, 3), numpy.uint8),
                                   run=lambda bgr, frame_id: ({"boxes": []}, 0, 0.25), row_of=lambda result, **kw: kw,
                                   pose=SimpleNamespace(manifest={"model_version": "body7"}))
            row = {"n": "5", "file": "f.npz", "npz_sha256": detect.sha256(b"frame bytes"), "rgb_seq": "7", "t_host_s": "100.0"}
            got, _, seconds = detect.Detector.frame(stub, take, row)
            self.assertEqual((got["capture_ts"], got["available_ts"], got["rgb_seq"], seconds), (100.0, 100.25, 7, 0.25))
            with self.assertRaisesRegex(detect.Refused, "frame 5 is not the one frames.csv recorded"):
                detect.Detector.frame(stub, take, {**row, "npz_sha256": "0" * 64})


class Main(unittest.TestCase):
    """main's loop on a stand-in detector, with the venv's versions stood in: a frame over 1.0 s is counted."""

    def run_main(self, slow):
        class Stub:
            preprocessing, load_ms, warm_ms = {}, 1.0, 1.0

            def __init__(self, *_):
                pass

            def frame(self, take, row):
                return {"frame": {"rgb_seq": int(row["rgb_seq"])}, "detections": []}, 0, 1.5 if row["n"] in slow else 0.05

            def files(self):
                return {}
        with tempfile.TemporaryDirectory() as tmp:
            take = fake_take(Path(tmp) / "take", 5, (2, 4))
            with mock.patch.object(detect, "contain"), mock.patch.object(detect, "installed", return_value=detect.lock()["versions"]), \
                    mock.patch.object(detect, "Detector", Stub), mock.patch("sys.stdout", io.StringIO()):
                self.assertEqual(detect.main([str(take), "--tools-root", tmp, "--state", tmp]), 0)
            return json.loads((take / "detector-run.json").read_text(encoding="utf-8"))

    def test_a_frame_over_the_limit_is_counted(self):
        run = self.run_main(slow={"3"})
        self.assertEqual((run["counts"]["frames"], run["counts"]["over_limit"], run["ms"]["max"]), (3, 1, 1500.0))
        self.assertEqual(self.run_main(slow=set())["counts"]["over_limit"], 0, "control: none over")


def usable_take(label):
    display = json.loads((SET / "display.json").read_text(encoding="utf-8"))["scenes"]
    return SET / next(d["take"] for d in display if d["label"] == label)


def run_detect(take, tools, state, *args):
    return subprocess.run([str(VENV), "-B", str(HERE / "detect.py"), str(take), "--tools-root", str(tools), "--state", str(state), *args],
                          capture_output=True, text=True, env={**ENV, "TEMP": str(state), "TMP": str(state)}, timeout=600)


@NEEDS_MODELS
class Model(unittest.TestCase):
    """In the venv, on the set's reading take, read only: --only writes nothing beside the take."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="den-detect-model-")
        cls.tools = tree(Path(cls.tmp.name) / "tools")
        cls.take = usable_take("Reading on the couch")
        _, rows = detect.usable(cls.take)
        cls.frames = [rows[0]["n"], rows[len(rows) // 3]["n"], rows[-1]["n"]]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_model_reproduces_its_rows_and_refuses_a_relabelled_fake(self):
        before = sorted(p.name for p in self.take.iterdir())
        done = run_detect(self.take, self.tools, Path(self.tmp.name) / "state", "--only", ",".join(self.frames), "--against", self.take / "detections.jsonl")
        self.assertEqual(done.returncode, 0, done.stderr)
        result = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertTrue(result["ok"])
        self.assertLessEqual(result["max_px"], detect.TOLERANCE_PX)
        print(f"\n  reproduced on frames {result['frames']} within {result['max_px']} px, {result['ms']} ms", file=sys.stderr)
        self.assertEqual(sorted(p.name for p in self.take.iterdir()), before, "nothing written beside the take")
        # Control (test 6): the scripted fake's rows for the same frames, relabelled with the real models' names.
        sys.path[:0] = [str(self.tools / "tools/den-observe")]
        import fake_detector
        recorded = [json.loads(line) for line in (self.take / "detections.jsonl").read_text(encoding="utf-8").splitlines()]
        fake = []
        for real in recorded:
            made = fake_detector.row(real["frame"]["capture_ts"], rgb_seq=real["frame"]["rgb_seq"])
            made["detector"] = dict(real["detector"])
            fake.append(made)
        planted = Path(self.tmp.name) / "relabelled.jsonl"
        planted.write_text("".join(json.dumps(r) + "\n" for r in fake), encoding="utf-8")
        self.assertEqual({r["detector"]["boxes"] for r in fake} | {r["detector"]["keypoints"] for r in fake}, set(detect.MODELS), "it carries the real names")
        refused = run_detect(self.take, self.tools, Path(self.tmp.name) / "state", "--only", ",".join(self.frames), "--against", planted)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("refused: the model did not reproduce the recorded rows", refused.stderr)

    def test_relative_paths_are_resolved_before_the_working_folder_moves(self):
        """contain() moves the working folder into --state, so a relative --tools-root or --against is resolved first."""
        tmp = Path(self.tmp.name)
        done = subprocess.run([str(VENV), "-B", str(HERE / "detect.py"), str(self.take), "--tools-root", "tools", "--state", "state-relative",
                               "--only", self.frames[0], "--against", os.path.relpath(self.take / "detections.jsonl", tmp)],
                              capture_output=True, text=True, env={**ENV, "TEMP": str(tmp), "TMP": str(tmp)}, cwd=tmp, timeout=600)
        self.assertEqual(done.returncode, 0, done.stderr[-600:])
        self.assertTrue(json.loads(done.stdout.strip().splitlines()[-1])["ok"])

    def test_left_and_right_against_the_truth(self):
        """Could not verify: whether RTMPose names left and right correctly on the figure. Per frame and pair,
        the labels count as right when the pairing as named is nearer the truth's joints than the swap."""
        word = next(d["word"] for d in json.loads((SET / "display.json").read_text(encoding="utf-8"))["scenes"] if d["take"] == self.take.name)
        truth = json.loads((SET / "truth" / word / "take.json").read_text(encoding="utf-8"))
        poses = json.loads((SET / "truth" / word / "poses.json").read_text(encoding="utf-8"))["poses"]
        names = ("left_shoulder", "right_shoulder", "left_hip", "right_hip", "left_wrist", "right_wrist", "left_eye", "right_eye")
        index = {name: i for i, name in enumerate(("nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder", "left_elbow",
                                                    "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle"))}
        rows = [json.loads(line) for line in (self.take / "detections.jsonl").read_text(encoding="utf-8").splitlines()]

        def tally(rows, swap=False):
            out = {}
            for side in names[::2]:
                pair = (side, side.replace("left", "right"))
                right = total = 0
                errors = []
                for r in rows:
                    people = [d for d in r["detections"] if d["class"] == "person" and d["keypoints"]]
                    if not people:
                        continue
                    k = people[0]["keypoints"]
                    a, b = (k[index[pair[1]]], k[index[pair[0]]]) if swap else (k[index[pair[0]]], k[index[pair[1]]])
                    t = poses[truth["pose_of_rgb_seq"][str(r["frame"]["rgb_seq"])]]["rgb_px"]
                    if not a or not b or a[2] < 0.4 or b[2] < 0.4 or pair[0] not in t or pair[1] not in t:
                        continue
                    named = math.dist(a[:2], t[pair[0]]) + math.dist(b[:2], t[pair[1]])
                    swapped = math.dist(a[:2], t[pair[1]]) + math.dist(b[:2], t[pair[0]])
                    total += 1
                    right += named < swapped
                    errors.append(named / 2)
                out[side.replace("left_", "")] = {"right": right, "of": total, "median_px": round(sorted(errors)[len(errors) // 2], 1) if errors else None}
            return out
        measured = tally(rows)
        print("\n  left and right against the truth: " + json.dumps(measured), file=sys.stderr)
        for part in ("shoulder", "hip"):
            self.assertGreater(measured[part]["of"], 0, part)
            self.assertGreaterEqual(measured[part]["right"], 0.9 * measured[part]["of"], f"{part}: {measured[part]}")
        flipped = tally(rows, swap=True)
        self.assertLess(flipped["shoulder"]["right"], 0.1 * flipped["shoulder"]["of"] + 1, "control: the same rows with the labels swapped read wrong")


if __name__ == "__main__":
    unittest.main(verbosity=2)
