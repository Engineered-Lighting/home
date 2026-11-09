#!/usr/bin/env python3
"""Hop 6: YOLO26s and RTMPose-m over one take's usable frames, recorded once beside the take.

    <venv>\\Scripts\\python.exe -B tools\\den-detect\\detect.py <take> --tools-root <folder> --state <folder>
    <venv>\\Scripts\\python.exe -B tools\\den-detect\\detect.py <take> --tools-root <folder> --state <folder> --only N,N,N --against <rows>

The venv is %USERPROFILE%\\ml\\den-detector, built by requirements.lock's install lines; the weights are its
models\\ folder, or --models. --tools-root holds tools/den-take, tools/den-observe and the Lab files in WORKERS
at one pin, as den-sim's chain.py archives them. No model code is new here: the Lab's UltralyticsWorker and
RTMPoseWorker load with their own checks (digests, versions, source hashes), and their authorize is answered
yes, because no Lab admission runs on this PC.

For each frame in the take's check's usable part (plan hop 6, steps 1 to 6): check.read_bundle, the Lab's
BT.601 decode to BGR, UltralyticsWorker.infer, distinct_people's duplicate-box merge, RTMPose on person boxes
at 0.5 or more (at most two), then from_perception_lab. available_ts is the capture time plus that frame's
compute only. A frame over 1.0 s of compute comes out rejected by den-observe, and is counted. Writes
detections.jsonl (den-detections-1) and detector-run.json beside the take.

With --only and --against it runs the same steps on those frames, writes nothing, and compares each person
with the recorded rows: its box and its joints within 1 px, its score and each joint's within 0.001.
That is prepare's check that the model reproduces its rows.

It refuses when the interpreter's distributions are not requirements.lock's: a version, one missing, or
one the lock doesn't list. The weights are held to the lock's digests at load.

Python sockets are refused. Ultralytics' settings and caches, and anything that would go to %TEMP%, go under
--state, never AppData. Exit 0 when done, 2 when refused, with one line on stderr.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
LOCK = HERE / "requirements.lock"
SCHEMA = "den-detector-run-1"
WORKERS = tuple("stack/services/perception-lab/" + name for name in (
    "ultralytics_worker.py", "rtmpose_worker.py", "reasoning.py", "worker_artifacts.py",   # the two workers and their imports
    "apartment_desk_preview.py",                                                            # distinct_people
    "la_detector_app.py",                                                                   # PREPROCESSING
    "room_mapper.py", "room_mesh.py", "head3d.py", "host_home.py"))                         # nv12_to_rgb and room_mapper's imports
TREE = ("tools/den-observe", "tools/den-take", *WORKERS)
MODELS = ("yolo26s", "rtmpose-m")            # the names from_perception_lab writes into each row
WEIGHTS = ("yolo26s.pt", "rtmpose-m-body7.onnx")
PERSON_MIN, MAX_PEOPLE = 0.5, 2              # pose_composition.POLICY: minimum_confidence .5, max_people 2
LIMIT_S, TOLERANCE_PX = 1.0, 1.0             # den-observe's proposed detector_max_age_s; the reproduce check
TOLERANCE_SCORE = 1e-3                       # the reproduce check on a person's score and each joint's
WIDTH, HEIGHT = 640, 360
AUTHORIZE = "answered yes by detect.py: no Lab admission runs on this PC"
NETWORK = ("socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo",
           "socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo")


class Refused(Exception):
    pass


def guard():
    def refuse(event, _args):
        if event in NETWORK:
            raise PermissionError(f"den-detect has no network ({event})")
    sys.addaudithook(refuse)


def lock(path=LOCK):
    """requirements.lock: {"versions": {distribution: version}, "weights": {file: sha256}}."""
    versions, weights = {}, {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("# sha256 "):
            _, _, name, digest = line.split()
            weights[name] = digest
        elif line.strip() and not line.startswith("#"):
            name, _, version = line.strip().partition("==")
            versions[name] = version
    return {"versions": versions, "weights": weights}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def ran(path):
    data = Path(path).read_bytes()
    return {"blob": hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(), "sha256": sha256(data)}


def usable(take):
    """(check file, frames.csv rows in its usable part). The take holds one check, as a den-sim click requires."""
    checks = sorted(take.glob("check-*.json"))
    if len(checks) != 1:
        raise Refused(f"the take holds {len(checks)} check-N.json files, not one")
    spans = [(s["first_n"], s["last_n"]) for s in json.loads(checks[0].read_text(encoding="utf-8"))["usable"]["segments"]]
    rows = [r for r in csv.DictReader(io.StringIO((take / "frames.csv").read_text(encoding="utf-8")))
            if any(a <= int(r["n"]) <= b for a, b in spans)]
    if not rows:
        raise Refused("the check's usable part is empty")
    return checks[0], rows


def contain(state):
    """Every write a library makes outside the take goes under state: Ultralytics' settings, its runs folder
    (the working folder), matplotlib's and torch's caches, and TEMP."""
    state = Path(state).resolve()
    for name in ("ultralytics", "tmp", "matplotlib", "cache"):
        (state / name).mkdir(parents=True, exist_ok=True)
    os.environ.update(YOLO_OFFLINE="true", YOLO_AUTOINSTALL="false", YOLO_CONFIG_DIR=str(state / "ultralytics"),
                      TEMP=str(state / "tmp"), TMP=str(state / "tmp"), MPLCONFIGDIR=str(state / "matplotlib"),
                      XDG_CACHE_HOME=str(state / "cache"), TORCH_HOME=str(state / "cache" / "torch"))
    os.chdir(state)
    import tempfile
    tempfile.tempdir = str(state / "tmp")


class Detector:
    def __init__(self, tools_root, models, pins):
        self.root = Path(tools_root).resolve()
        sys.path[:0] = [str(self.root / "stack/services/perception-lab"), str(self.root / "tools/den-observe"), str(self.root / "tools/den-take")]
        import numpy
        import onnxruntime
        onnxruntime.disable_telemetry_events()
        import check
        from apartment_desk_preview import distinct_people
        from detections import from_perception_lab, read_rows
        from la_detector_app import PREPROCESSING
        from room_mapper import nv12_to_rgb
        from rtmpose_worker import RTMPoseWorker
        from ultralytics_worker import UltralyticsWorker
        self.np, self.check, self.distinct_people, self.row_of, self.read_rows, self.decode = numpy, check, distinct_people, from_perception_lab, read_rows, nv12_to_rgb
        self.preprocessing = dict(PREPROCESSING)
        models = Path(models).resolve()
        artifact = models / "artifact.json"
        if not artifact.is_file() or sha256(artifact.read_bytes()) != pins["artifact.json"]:
            raise Refused("refused at load: artifact.json is missing or not the pinned one")
        manifest = json.loads(artifact.read_text(encoding="utf-8"))
        if manifest.get("sha256") != pins["rtmpose-m-body7.onnx"]:
            raise Refused("refused at load: artifact.json names other pose weights than requirements.lock")
        started = time.perf_counter()
        try:
            self.yolo = UltralyticsWorker({"runtime_version": "8.4.0", "task": "detect", "checkpoint": "yolo26s", "precision": "fp32",
                                           "sha256": pins["yolo26s.pt"], "license": "AGPL-3.0 Ultralytics; weights outside git",
                                           "device_id": "this-pc-cpu", "engine_epoch": "den-detect", "profile_id": "yolo26s-cpu-fp32",
                                           "model_version": "yolo26s-8.4.0", "device": "cpu", "format": "pt", "preprocessing": self.preprocessing},
                                          lambda *_: True)
            self.yolo.load(models / "yolo26s.pt")
            self.pose = RTMPoseWorker(manifest, lambda *_: True)
            self.pose.load(models / "rtmpose-m-body7.onnx")
        except ValueError as exc:     # the workers' own load checks: a missing file, a digest or a version
            raise Refused(f"refused at load: {exc}") from None
        self.load_ms = (time.perf_counter() - started) * 1000
        black = numpy.zeros((HEIGHT, WIDTH, 3), numpy.uint8)
        self.run(black, "warmup")
        self.pose.infer(black, [{"id": "warmup:0", "frame_id": "warmup", "detector_version": "yolo26s", "xyxy": [200.0, 20.0, 440.0, 340.0]}],
                        frame_id="warmup", request_id="warmup")   # the pose model's first call is a warm-up too
        self.warm_ms = (time.perf_counter() - started) * 1000 - self.load_ms

    def run(self, bgr, frame_id):
        """Steps 3 to 5 on one BGR frame: (result, person boxes merged away, compute seconds)."""
        started = time.perf_counter()
        result = self.yolo.infer(bgr, frame_id=frame_id, request_id=frame_id)
        kept = {o["instance_id"] for o in self.distinct_people(result)}
        merged = sum(1 for b in result["boxes"] if b["label"] == "person" and b["instance_id"] not in kept)
        result["boxes"] = [b for b in result["boxes"] if b["label"] != "person" or b["instance_id"] in kept]
        result["objects"] = [o for o in result["objects"] if o["label"] != "person" or o["instance_id"] in kept]
        people = sorted((b for b in result["boxes"] if b["label"] == "person" and b["confidence"] >= PERSON_MIN), key=lambda b: -b["confidence"])
        crops = [{"id": b["instance_id"], "frame_id": frame_id, "detector_version": "yolo26s", "xyxy": [b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]]}
                 for b in people[:MAX_PEOPLE]]
        result["pose_instances"] = self.pose.infer(bgr, crops, frame_id=frame_id, request_id=frame_id)["pose_instances"]
        return result, merged, time.perf_counter() - started

    def frame(self, take, row):
        """One usable frame to one den-detections-1 row: (row, merged, compute seconds)."""
        data = (take / row["file"]).read_bytes()
        if sha256(data) != row["npz_sha256"]:
            raise Refused(f"frame {row['n']} is not the one frames.csv recorded")
        _meta, nv12 = self.check.read_bundle(data, with_rgb=True)
        rgb = self.decode(self.np.frombuffer(nv12, self.np.uint8).reshape(HEIGHT * 3 // 2, WIDTH), WIDTH, HEIGHT)
        seq, t = int(row["rgb_seq"]), float(row["t_host_s"])
        result, merged, compute_s = self.run(self.np.ascontiguousarray(rgb[:, :, ::-1]), f"rgb-{seq}")
        return self.row_of(result, capture_ts=t, available_ts=t + compute_s, rgb_seq=seq, width=WIDTH, height=HEIGHT,
                           boxes_version="yolo26s-8.4.0", keypoints_version=self.pose.manifest["model_version"]), merged, compute_s

    def files(self):
        """Every file under the tools root this process loaded, hashed from its bytes, so a receipt can hold it to its pin."""
        out = {}
        for module in list(sys.modules.values()):
            file = getattr(module, "__file__", None)
            if file and Path(file).resolve().is_relative_to(self.root):
                out[Path(file).resolve().relative_to(self.root).as_posix()] = ran(file)
        return dict(sorted(out.items()))


def person_boxes(row):
    return [d["box_xyxy"] for d in row["detections"] if d["class"] == "person"]


def people(row):
    return [d for d in row["detections"] if d["class"] == "person"]


def differs(old, new):
    """One recorded person against the model's: (worst px over its box and joints, why it differs or None).
    A score or a joint's score off by more than TOLERANCE_SCORE, or a joint placed on one side only, differs."""
    worst = max(abs(u - v) for u, v in zip(old["box_xyxy"], new["box_xyxy"]))
    if abs(old["score"] - new["score"]) > TOLERANCE_SCORE:
        return worst, f"a person's score moved {abs(old['score'] - new['score']):.4f}, over {TOLERANCE_SCORE:g}"
    a, b = old.get("keypoints") or [], new.get("keypoints") or []
    if len(a) != len(b):
        return worst, f"{len(a)} joints recorded on a person, the model gives {len(b)}"
    for p, q in zip(a, b):
        if (p is None) != (q is None):
            return worst, "a joint recorded on one side only"
        if p is None:
            continue
        worst = max(worst, abs(p[0] - q[0]), abs(p[1] - q[1]))
        if abs(p[2] - q[2]) > TOLERANCE_SCORE:
            return worst, f"a joint's score moved {abs(p[2] - q[2]):.4f}, over {TOLERANCE_SCORE:g}"
    return worst, None


def compare(recorded, fresh):
    """Each person in fresh rows against the recorded rows of the same frames, in the rows' order: boxes and
    joints within TOLERANCE_PX, scores within TOLERANCE_SCORE. (ok, worst px, why)."""
    by_seq, worst = {r["frame"].get("rgb_seq"): r for r in recorded}, 0.0
    for row in fresh:
        seq = row["frame"]["rgb_seq"]
        old = by_seq.get(seq)
        if old is None:
            return False, worst, f"frame rgb_seq {seq} has no recorded row"
        a, b = people(old), people(row)
        if len(a) != len(b):
            return False, worst, f"frame rgb_seq {seq}: {len(a)} person boxes recorded, the model gives {len(b)}"
        for x, y in zip(a, b):
            moved, why = differs(x, y)
            worst = max(worst, moved)
            if why:
                return False, round(worst, 4), f"frame rgb_seq {seq}: {why}"
    if worst > TOLERANCE_PX:
        return False, round(worst, 4), f"a person box or joint moved {worst:.2f} px, over {TOLERANCE_PX:g} px"
    return True, round(worst, 4), None


def installed():
    """Every distribution this interpreter can import from, by name and version: one the lock doesn't name too."""
    from importlib.metadata import distributions
    return {d.metadata["Name"]: d.version for d in distributions()}


def unlocked(pinned, have):
    """Where the interpreter and the lock's versions disagree: a version, one missing, or one the lock doesn't name."""
    return [f"{name} {have.get(name) or 'missing'} (the lock: {pinned.get(name) or 'not listed'})"
            for name in sorted(set(pinned) | set(have), key=str.lower) if have.get(name) != pinned.get(name)]


def main(argv=None):
    guard()
    parser = argparse.ArgumentParser(description="YOLO26s and RTMPose-m over one take's usable frames, recorded beside it.")
    parser.add_argument("take")
    parser.add_argument("--tools-root", required=True, help="holds tools/den-take, tools/den-observe and the Lab files in WORKERS")
    parser.add_argument("--state", required=True, help="where Ultralytics' settings and caches, and TEMP, go")
    parser.add_argument("--models", default=str(Path(sys.prefix) / "models"), help="the weights (default: the venv's models folder)")
    parser.add_argument("--only", help="frame numbers (frames.csv n), comma-separated: run on these and write nothing")
    parser.add_argument("--against", help="with --only: the recorded rows to compare person boxes with")
    args = parser.parse_args(argv)
    try:
        # Every path before contain(), which moves the working folder into --state.
        take, pinned = Path(args.take).resolve(), lock()
        tools_root, models = Path(args.tools_root).resolve(), Path(args.models).resolve()
        against = Path(args.against).resolve() if args.against else None
        contain(args.state)
        have = installed()
        wrong = unlocked(pinned["versions"], have)
        if wrong:
            more = f", and {len(wrong) - 5} more" if len(wrong) > 5 else ""
            raise Refused(f"the venv is not requirements.lock's: {', '.join(wrong[:5])}{more}")
        check_file, rows = usable(take)
        if args.only:
            wanted = args.only.split(",")
            rows = [r for r in rows if r["n"] in wanted]
            if len(rows) != len(wanted):
                raise Refused("--only names a frame outside the usable part")
            if against is None:
                raise Refused("--only needs --against, the recorded rows to compare with")
        detector = Detector(tools_root, models, pinned["weights"])
        out, ms, merged, over = [], [], 0, 0
        for row in rows:
            got, dropped, compute_s = detector.frame(take, row)
            out.append(got)
            ms.append(round(compute_s * 1000, 1))
            merged += dropped
            over += compute_s > LIMIT_S
        if args.only:
            ok, worst, why = compare(detector.read_rows(against), out)
            print(json.dumps({"ok": ok, "frames": [int(r["n"]) for r in rows], "max_px": worst, "why": why, "ms": ms}))
            if not ok:
                raise Refused(f"the model did not reproduce the recorded rows: {why}")
            return 0
        rows_file = take / "detections.jsonl"
        with open(rows_file, "w", encoding="utf-8", newline="\n") as target:
            for row in out:
                target.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
        people = sum(1 for row in out if person_boxes(row))
        record = {"schema": SCHEMA, "take": take.name, "take_digest": sha256((take / "frames.csv").read_bytes()),
                  "check": {"file": check_file.name, "sha256": sha256(check_file.read_bytes())},
                  "rows": {"file": rows_file.name, "sha256": sha256(rows_file.read_bytes())},
                  "python": sys.version.split()[0], "versions": have, "device": "cpu",
                  "weights": {name: pinned["weights"][name] for name in (*WEIGHTS, "artifact.json")},
                  "load_checks": "UltralyticsWorker.load and RTMPoseWorker.load passed: weight digests, versions and rtmlib's source hashes",
                  "authorize": AUTHORIZE, "network": "YOLO_OFFLINE=true, YOLO_AUTOINSTALL=false, onnxruntime telemetry off, Python sockets refused",
                  "writes": "the rows and this file beside the take; Ultralytics' settings, caches and TEMP under the caller's --state folder",
                  "preprocessing": detector.preprocessing, "person_min": PERSON_MIN, "max_people": MAX_PEOPLE, "limit_s": LIMIT_S,
                  "available_ts": "capture time plus this frame's compute only",
                  "counts": {"frames": len(out), "with_person": people, "without_person": len(out) - people, "over_limit": over,
                             "merged_duplicates": merged, "detections": sum(len(row["detections"]) for row in out)},
                  "ms": {"per_frame": ms, "median": statistics.median(ms), "max": max(ms), "load": round(detector.load_ms), "warmup": round(detector.warm_ms)},
                  "tools": {"detect.py": ran(__file__), "requirements.lock": ran(LOCK), "files": detector.files()}}
        (take / "detector-run.json").write_text(json.dumps(record, indent=1, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
        print(f"{take.name}: a person on {people} of {len(out)} usable frames, median {record['ms']['median']:.0f} ms a frame, {over} over {LIMIT_S:g} s")
        return 0
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
