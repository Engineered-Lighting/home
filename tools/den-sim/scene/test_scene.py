"""Tests for the den simulation's recorded side, hops 1 to 5 (plan, "Done means"):

- test 4, only the camera planes differ: all of it but Home's fixture report, which is the browser's;
- test 5, one pixel, one depth: the frame-hash half (the body-hash half is step 4);
- test 9, picture independence: the re-fit half (the "not on the couch" half is step 5);
- test 2, nothing told: the random words, and the search over what exists now, the detector's rows,
  detector-run.json and detect.py's argv among it;
- test 19, safety: with DEN_SIM_PREPARE_TRACE=1, an audit-hook trace of a whole prepare run in a scratch
  data root (about 3 minutes): no Python write outside the data root, none under AppData or TEMP.

Each has a control that must make it fail. Standard library and numpy only.

    python -B -X utf8 tools\\den-sim\\scene\\test_scene.py

The parts that read a prepared set need DEN_SIM_SET=<a set folder under prepared>; without it they skip
and say so. They only read it: the re-fit writes its fits and private files under a temporary folder
of its own (in TEMP), with DEN_SIM_HOME and LOCALAPPDATA pointed there too, and the controls that change
files work on hard-linked copies whose changed files are replaced, never written through.
"""
import ast
import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import frame_hash  # noqa: E402
import prepare  # noqa: E402
import vcam  # noqa: E402

prepare.guard(loopback=False)   # this process and every Python one it starts refuse sockets

SET = Path(os.environ["DEN_SIM_SET"]).resolve() if os.environ.get("DEN_SIM_SET") else None
NEEDS_SET = unittest.skipUnless(SET and (SET / "set.json").exists(), "needs DEN_SIM_SET=<a prepared set>")
SHOTS = json.loads((HERE / "shots.json").read_text(encoding="utf-8"))
REFS = json.loads((ROOT / "tools/den-sim/refs.json").read_text(encoding="utf-8"))
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=True).stdout


def rows(take):
    return list(csv.DictReader(io.StringIO((take / "frames.csv").read_text(encoding="utf-8"))))


def member(path, name):
    with zipfile.ZipFile(path) as archive:
        return np.load(io.BytesIO(archive.read(name + ".npy")), allow_pickle=False)


def write_npz(path, arrays):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, array in arrays.items():
            with io.BytesIO() as body:
                np.lib.format.write_array(body, array, allow_pickle=False)
                archive.writestr(name + ".npy", body.getvalue())
    tmp = Path(str(path) + ".new")
    tmp.write_bytes(out.getvalue())
    os.replace(tmp, path)          # a new file: a hard link to the original is never written through
    return out.getvalue()


def linked_copy(source, target):
    """A copy of a folder made of hard links: free, and safe as long as changed files are replaced."""
    for path in source.rglob("*"):
        dest = target / path.relative_to(source)
        if path.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.link(path, dest)


def scratch():
    return tempfile.TemporaryDirectory(prefix="den-sim-scene-")


def the_set():
    info = json.loads((SET / "set.json").read_text(encoding="utf-8"))
    display = json.loads((SET / "display.json").read_text(encoding="utf-8"))["scenes"]
    by_label = {d["label"]: SET / d["take"] for d in display}
    return info, display, by_label


# ---- test 5, the frame-hash half -------------------------------------------------------------------------

def mini_take(folder, frames=4):
    """A take in den-take's format, made by hand: take.json, frames.csv and frames/*.npz."""
    rng = np.random.default_rng(3)
    (folder / "frames").mkdir(parents=True)
    table = io.StringIO()
    writer = csv.DictWriter(table, ("n", "file", "rgb_seq", "t_host_s", "npz_sha256"), lineterminator="\n")
    writer.writeheader()
    for n in range(1, frames + 1):
        name = f"frames/{n:06d}-{500 + n}.npz"
        data = write_npz(folder / name, {"depth_mm": rng.integers(500, 4000, (400, 640), dtype=np.uint16),
                                         "rgb_nv12": rng.integers(0, 255, (540, 640), dtype=np.uint8)})
        writer.writerow({"n": n, "file": name, "rgb_seq": 500 + n, "t_host_s": 7000 + n * 0.2, "npz_sha256": sha(data)})
    (folder / "frames.csv").write_text(table.getvalue(), encoding="utf-8", newline="")
    (folder / "take.json").write_text(json.dumps({"schema": "den-take-1", "sha256": {"frames.csv": sha((folder / "frames.csv").read_bytes())}}), encoding="utf-8")
    return folder


def rederive(folder, take_json=False):
    """frames.csv's hashes written again to match the files, and optionally take.json's digest of it."""
    table = rows(folder)
    for row in table:
        row["npz_sha256"] = sha((folder / row["file"]).read_bytes())
    out = io.StringIO()
    writer = csv.DictWriter(out, list(table[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(table)
    tmp = folder / "frames.csv.new"
    tmp.write_text(out.getvalue(), encoding="utf-8", newline="")
    os.replace(tmp, folder / "frames.csv")
    if take_json:
        record = json.loads((folder / "take.json").read_text(encoding="utf-8"))
        record["sha256"]["frames.csv"] = sha((folder / "frames.csv").read_bytes())
        (folder / "take.json.new").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
        os.replace(folder / "take.json.new", folder / "take.json")


class FrameHash(unittest.TestCase):
    def test_a_plus_one_and_a_swapped_depth_are_caught(self):
        with scratch() as tmp:
            take = mini_take(Path(tmp) / "take")
            whole = frame_hash.check_take(take)
            self.assertTrue(whole["ok"], whole)
            digest = whole["take_digest"]
            first, second = take / rows(take)[0]["file"], take / rows(take)[1]["file"]
            nv12 = member(first, "rgb_nv12").copy()
            nv12[100, 100] = (int(nv12[100, 100]) + 1) % 256        # +1 to one byte of one plane
            write_npz(first, {"depth_mm": member(first, "depth_mm"), "rgb_nv12": nv12})
            got = frame_hash.check_take(take)
            self.assertEqual((got["ok"], got["word"]), (False, "MISSING"))
            self.assertIn("frame 1", got["reason"])
            # Another frame's depth under this frame's pixels.
            take2 = mini_take(Path(tmp) / "take2")
            first = take2 / rows(take2)[0]["file"]
            write_npz(first, {"depth_mm": member(take2 / rows(take2)[1]["file"], "depth_mm"), "rgb_nv12": member(first, "rgb_nv12")})
            self.assertEqual(frame_hash.check_take(take2)["word"], "MISSING")
            # frames.csv re-derived to match: take.json still holds the old digest.
            rederive(take2)
            self.assertIn("take.json", frame_hash.check_take(take2)["reason"])
            # take.json re-derived too: only prepare's receipt digest can tell.
            rederive(take2, take_json=True)
            self.assertTrue(frame_hash.check_take(take2)["ok"])
            self.assertIn("not the take prepare recorded", frame_hash.check_take(take2, expected_digest=digest)["reason"])
            self.assertNotEqual(frame_hash.check_take(take2)["take_digest"], digest)

    def test_a_frame_gone_or_named_outside_the_take(self):
        with scratch() as tmp:
            take = mini_take(Path(tmp) / "take")
            (take / rows(take)[2]["file"]).unlink()
            self.assertIn("frame 3", frame_hash.check_take(take)["reason"])
            take = mini_take(Path(tmp) / "take2")
            text = (take / "frames.csv").read_text(encoding="utf-8").replace("frames/000002-502.npz", "../take/frames/000002-502.npz")
            (take / "frames.csv").write_text(text, encoding="utf-8", newline="")
            rederive(take, take_json=True)
            self.assertEqual(frame_hash.check_take(take)["word"], "MISSING")

    def test_an_empty_take_is_missing(self):
        """A frames.csv with a header and no rows hashes clean against take.json, and still reads MISSING."""
        with scratch() as tmp:
            take = mini_take(Path(tmp) / "take", frames=0)
            got = frame_hash.check_take(take)
            self.assertEqual((got["ok"], got["word"], got["reason"]), (False, "MISSING", "the take has no frames"))
            self.assertTrue(frame_hash.check_take(mini_take(Path(tmp) / "control", frames=1))["ok"], "control: one frame passes")

    @NEEDS_SET
    def test_reading_pixels_with_standing_depth(self):
        """On the real takes. Control: with the hash off, den-observe's own reader takes the swapped frame,
        and the depth under the reader's true joints moves; only the frame hash stops it."""
        _, display, take_of = the_set()
        reading, standing = take_of["Reading on the couch"], take_of["Standing by the couch with a book"]
        self.assertTrue(frame_hash.check_take(reading)["ok"])
        receipt = json.loads((reading / "prepare-receipt.json").read_text(encoding="utf-8"))
        self.assertTrue(frame_hash.check_take(reading, expected_digest=receipt["take_digest"])["ok"])
        with scratch() as tmp:
            copy = Path(tmp) / reading.name
            linked_copy(reading, copy)
            row = rows(copy)[len(rows(copy)) // 2]
            frame = copy / row["file"]
            other = standing / rows(standing)[len(rows(standing)) // 2]["file"]
            with zipfile.ZipFile(frame) as archive:
                arrays = {name[:-4]: np.load(io.BytesIO(archive.read(name)), allow_pickle=False) for name in archive.namelist()}
            original_depth = arrays["depth_mm"].copy()
            arrays["depth_mm"] = member(other, "depth_mm")
            write_npz(frame, arrays)
            got = frame_hash.check_take(copy, expected_digest=receipt["take_digest"])
            self.assertEqual(got["word"], "MISSING")
            self.assertIn(row["n"], got["reason"])
            self.assertTrue(frame_hash.check_take(reading)["ok"], "the original take must be untouched")
            # Control: den-observe's reader, unchanged at its pin, accepts the swap.
            word = next(d["word"] for d in display if d["label"] == "Reading on the couch")
            poses = json.loads((SET / "truth" / word / "poses.json").read_text(encoding="utf-8"))["poses"]
            pose = json.loads((SET / "truth" / word / "take.json").read_text(encoding="utf-8"))["pose_of_rgb_seq"][row["rgb_seq"]]
            pixels = left_pixels(poses[pose]["joints_m"])
            tree = Path(tmp) / "tree"
            data = git("archive", "--format=tar", REFS["tools"]["rung-2-observe"], "tools/den-observe", "tools/den-take")
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                archive.extractall(tree, filter="data")
            code = (f"import json, sys; sys.path[:0] = [{str(HERE)!r}, sys.argv[1]]; import prepare; prepare.guard(False); "
                    "import takes; t = takes.Take(sys.argv[2]); "
                    "i = [r['file'] for r in t.rows].index(sys.argv[3]); meta, depth = t.frame(i); "
                    "print(json.dumps([depth.at(u, v) for u, v in json.loads(sys.argv[4])]))")
            done = subprocess.run([sys.executable, "-B", "-c", code, str(tree / "tools/den-observe"), str(copy), row["file"], json.dumps(pixels)],
                                  capture_output=True, text=True, env=ENV)
            self.assertEqual(done.returncode, 0, done.stderr)
            swapped = json.loads(done.stdout)
            before = [int(original_depth[v, u]) for u, v in pixels]
            moved = [abs(a - b) / 1000 for a, b in zip(before, swapped) if a and b]
            self.assertGreaterEqual(sum(m > 0.25 for m in moved), 3, f"depth under the reader's joints moved by {moved}")


def left_pixels(joints_m):
    """The figure's joints in the left camera's image, where depth is aligned."""
    pose = np.array(SHOTS["camera"]["room_from_left"]), SHOTS["camera"]["left_k"]
    (matrix, k), out = pose, []
    for point in joints_m.values():
        p = matrix[:3, :3].T @ (np.array(point) - matrix[:3, 3])
        u, v = k["fx"] * p[0] / p[2] + k["cx"], k["fy"] * p[1] / p[2] + k["cy"]
        if 0 <= round(u) < 640 and 0 <= round(v) < 400:
            out.append([int(round(u)), int(round(v))])
    return out


# ---- test 4, only the camera planes differ -------------------------------------------------------------

def lab_decode():
    """The Lab's nv12_to_rgb, read from room_mapper.py at the base commit refs.json pins, run as written."""
    source = git("show", f"{REFS['base']['sha']}:stack/services/perception-lab/room_mapper.py").decode("utf-8")
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "nv12_to_rgb")
    space = {"_np": lambda: np}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "room_mapper.py", "exec"), space)
    return space["nv12_to_rgb"]


def full_range_nv12(rgb):
    """The control: a full-range (JPEG) encoder, which the Lab's limited-range decode must not accept."""
    r, g, b = [rgb[:, :, i].astype(np.float32) for i in range(3)]
    y, u, v = 0.299 * r + 0.587 * g + 0.114 * b, 128 - 0.168736 * r - 0.331264 * g + 0.5 * b, 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    h, w = y.shape
    uv = np.empty((h // 2, w), np.uint8)
    uv[:, 0::2] = np.clip(u.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3)) + 0.5, 0, 255).astype(np.uint8)
    uv[:, 1::2] = np.clip(v.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3)) + 0.5, 0, 255).astype(np.uint8)
    return np.clip(y + 0.5, 0, 255).astype(np.uint8), uv


def worst_patch(encode):
    """Flat 32x32 patches of the scene's colours, greys and seeded colours, through encode and the Lab's decode."""
    colours = [0xd8c24a, 0x9c3b34, 0x2c3a4f, 0x46566b, 0x53657d, 0xc68e6c, 0xf4efe2, 0x33302c, 0x1c1d21, 0x5a7fa8, 0x6b4a2f,
               0x7c6a58, 0xb9b2a5, 0x30343a, 0x101010, 0x808080, 0xe0e0e0]
    colours += [int(c) for c in np.random.default_rng(0).integers(0, 1 << 24, 200 - len(colours))]
    image, cells = np.zeros((360, 640, 3), np.uint8), []
    for n, c in enumerate(colours):
        r0, c0 = (n // 20) * 36, (n % 20) * 32
        image[r0:r0 + 32, c0:c0 + 32] = ((c >> 16) & 255, (c >> 8) & 255, c & 255)
        cells.append((r0, c0))
    y, uv = encode(image)
    back = lab_decode()(np.concatenate((y, uv)), 640, 360)
    return max(int(np.abs(back[r + 2:r + 30, c + 2:c + 30].astype(int) - image[r + 2:r + 30, c + 2:c + 30].astype(int)).max()) for r, c in cells)


VOLATILE_TAKE = ("take", "camera", "started_unix", "ended_unix", "disk_free_bytes_at_start", "counts")


def take_shape(take):
    """take.json with what time, the port and the session change taken out."""
    record = json.loads((take / "take.json").read_text(encoding="utf-8"))
    for key in VOLATILE_TAKE:
        record.pop(key, None)
    record["sha256"] = {k: v for k, v in record["sha256"].items() if k != "frames.csv"}
    record["cues"] = [(c["cue"], c["text"], c.get("attempt")) for c in record["cues"]]
    record["slates"] = [(s["slate"], s["attempt"], s["peek"]["readable"]) for s in record["slates"]]
    for key in ("health_before", "health_after"):
        record[key] = {**record[key], "body": {k: v for k, v in (record[key]["body"] or {}).items() if k != "session"}}
    return record


def meta_shape(meta):
    meta = json.loads(json.dumps(meta))
    for key in ("session", "captured_unix_ns"):
        meta.pop(key)
    for plane in meta["frames"].values():
        for key in ("sequence", "device_timestamp_seconds", "host_monotonic_timestamp_seconds"):
            plane.pop(key)
    return meta


def members(path):
    with zipfile.ZipFile(path) as archive:
        out = {}
        for name in archive.namelist():
            out[name[:-4]] = np.load(io.BytesIO(archive.read(name)), allow_pickle=False).copy()
        return out


def differences(takes, change=None):
    """Every way the takes differ outside the four camera planes. change(take name, frame index, metadata) lets a control edit one."""
    found, shapes, metas, layout = [], {}, {}, {}
    for name, take in takes.items():
        shapes[name] = take_shape(take)
        for i, row in enumerate(rows(take)):
            with zipfile.ZipFile(take / row["file"]) as archive:
                meta = json.loads(np.load(io.BytesIO(archive.read("metadata_json.npy"))).tobytes())
                listing = [(info.filename) for info in archive.infolist()]
            if change:
                change(name, i, meta)
            metas.setdefault(name, set()).add(json.dumps(meta_shape(meta), sort_keys=True))
            layout.setdefault(name, set()).add(tuple(listing))
    first = next(iter(takes))
    for name in takes:
        if shapes[name] != shapes[first]:
            found.append(f"take.json of {name} differs from {first}")
        if len(metas[name]) != 1 or metas[name] != metas[first]:
            found.append(f"bundle metadata of {name} differs ({len(metas[name])} kinds)")
        if layout[name] != layout[first]:
            found.append(f"bundle members of {name} differ")
    return found


def blank_against_reading(blank, reading, change=None):
    """Frames with the same rgb_seq: (planes other than rgb_nv12 that differ, frames whose rgb_nv12 differs, frames compared)."""
    a, b = {r["rgb_seq"]: r["file"] for r in rows(blank)}, {r["rgb_seq"]: r["file"] for r in rows(reading)}
    same_planes, rgb_differs, common = [], 0, sorted(set(a) & set(b))
    for seq in common:
        x, y = members(blank / a[seq]), members(reading / b[seq])
        if change:
            change(seq, x)
        for plane in ("depth_mm", "left", "right"):
            if not np.array_equal(x[plane], y[plane]):
                same_planes.append((seq, plane))
        rgb_differs += not np.array_equal(x["rgb_nv12"], y["rgb_nv12"])
    return same_planes, rgb_differs, len(common)


class CameraPlanes(unittest.TestCase):
    def test_shots_holds_one_figure_one_room_one_camera(self):
        self.assertEqual(set(SHOTS) & {"figure", "room", "camera", "couch"}, {"figure", "room", "camera", "couch"})
        self.assertEqual((SHOTS["figure"]["id"], SHOTS["figure"]["way"]), ("procedural-1", "up_at_chest"))
        self.assertFalse(any(isinstance(v, list) and k in ("figures", "rooms", "cameras") for k, v in SHOTS.items()))
        self.assertEqual(len(SHOTS["scenes"]), 5)
        for scene in SHOTS["scenes"]:
            self.assertLessEqual(set(scene), {"display", "person", "picture", "render_of_scene"})
            if scene["person"] is not None:
                self.assertEqual(set(scene["person"]), {"where", "at_m", "held"})
        # The stored pose is the look-at it says it is.
        cam = SHOTS["camera"]
        forward = np.subtract(cam["look_at_m"], cam["position_m"])
        forward /= np.linalg.norm(forward)
        right = np.cross(forward, [0, 0, 1])
        right /= np.linalg.norm(right)
        expected = np.column_stack([right, np.cross(forward, right), forward, cam["position_m"]])
        self.assertTrue(np.allclose(np.array(cam["room_from_left"])[:3], expected, atol=1e-9))

    def test_flat_colour_comes_back_within_3_levels(self):
        self.assertLessEqual(worst_patch(vcam.rgb_to_nv12), 3)

    def test_control_a_full_range_encoder_fails(self):
        self.assertGreater(worst_patch(full_range_nv12), 3)

    @NEEDS_SET
    def test_takes_and_metadata_match_across_scenes(self):
        info, _, _ = the_set()
        takes = {t["take"]: SET / t["take"] for t in info["takes"]}
        self.assertEqual(len(takes), 6)
        self.assertEqual(differences(takes), [])
        # Control: one metadata field changed in one frame of one take.
        victim = sorted(takes)[1]

        def edit(name, index, meta):
            if name == victim and index == 7:
                meta["frames"]["rgb"]["intrinsics"][0][0] += 1.0
        self.assertTrue(any(victim in line for line in differences(takes, edit)))

    @NEEDS_SET
    def test_scenes_differ_in_the_planes_and_blank_only_in_rgb(self):
        _, _, take_of = the_set()
        reading, phone, blank = take_of["Reading on the couch"], take_of["Holding a phone"], take_of["Blank frame"]
        a, b = {r["rgb_seq"]: r["file"] for r in rows(reading)}, {r["rgb_seq"]: r["file"] for r in rows(phone)}
        seq = sorted(set(a) & set(b))[len(set(a) & set(b)) // 2]
        x, y = members(reading / a[seq]), members(phone / b[seq])
        self.assertEqual({p for p in ("rgb_nv12", "left", "right", "depth_mm") if not np.array_equal(x[p], y[p])},
                         {"rgb_nv12", "left", "right", "depth_mm"})
        planes, rgb, common = blank_against_reading(blank, reading)
        self.assertGreater(common, 100)
        self.assertEqual(planes, [])
        self.assertEqual(rgb, common)
        # Control: one depth pixel changed in one blank frame.
        target = None

        def edit(seq, arrays):
            nonlocal target
            if target is None:
                target = seq
                arrays["depth_mm"][200, 320] += 1
        planes, _, _ = blank_against_reading(blank, reading, edit)
        self.assertEqual(planes, [(target, "depth_mm")])


# ---- test 2, the random words ------------------------------------------------------------------------------

TEXT = (".json", ".jsonl", ".csv", ".log", ".html", ".md")


def search(folder, needles, keys=("scene", "scenario")):
    """Every place under folder a needle or a JSON key appears: text files whole, and each bundle's metadata."""
    hits = []

    def look(label, text):
        hits.extend(f"{label}: {n}" for n in needles if n in text)
        if label.endswith((".json", ".jsonl")) or "metadata" in label:
            for line in (text.splitlines() if label.endswith(".jsonl") else [text]):
                try:
                    walk(json.loads(line), label)
                except ValueError:
                    pass

    def walk(value, label):
        if isinstance(value, dict):
            hits.extend(f"{label}: key {k}" for k in value if k in keys)
            for v in value.values():
                walk(v, label)
        elif isinstance(value, list):
            for v in value:
                walk(v, label)
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        label = str(path.relative_to(folder))
        if path.suffix in TEXT:
            look(label, path.read_text(encoding="utf-8", errors="replace"))
        elif path.suffix == ".npz":
            with zipfile.ZipFile(path) as archive:
                look(label + " metadata", np.load(io.BytesIO(archive.read("metadata_json.npy"))).tobytes().decode("utf-8"))
        hits.extend(f"{label}: name" for n in needles if n in path.name)
    return hits


class RandomWords(unittest.TestCase):
    def test_words_are_random_and_never_folder_ids(self):
        words = set()
        for _ in range(200):
            words.add(prepare.random_word(words))
        self.assertEqual(len(words), 200)
        self.assertTrue(all(re.fullmatch(r"[a-z]{10}", w) and not re.fullmatch(r"[0-9a-f]+", w) for w in words))

    @NEEDS_SET
    def test_each_word_lives_only_where_a_scene_name_lives(self):
        info, display, _ = the_set()
        words = [d["word"] for d in display]
        self.assertEqual(len(set(words)), 5)
        self.assertEqual([d["label"] for d in display], [s["display"] for s in SHOTS["scenes"]])
        renders = {p.name.split(".")[0] for p in (SET / "render").iterdir()}
        for d, scene in zip(display, SHOTS["scenes"]):
            truth = json.loads((SET / "truth" / d["word"] / "take.json").read_text(encoding="utf-8"))
            self.assertEqual(truth["take"], d["take"])
            self.assertEqual(d["word"] in renders, "render_of_scene" not in scene)
        self.assertEqual(renders - set(words), {"marks"})
        # The search over what exists now: the takes, the fit, the room model, the receipts, set.json, the logs.
        needles = words + [s["display"] for s in SHOTS["scenes"]]
        everything = [t["take"] for t in info["takes"]] + [info["fit"]["folder"]]
        with scratch() as tmp:
            place = Path(tmp) / "set"
            place.mkdir()
            for name in everything:
                linked_copy(SET / name, place / name)
            for name in ("set.json", "argv.jsonl", "prepare.log"):
                os.link(SET / name, place / name)
            self.assertEqual(search(place, needles), [])
            searched = [p.name for p in place.rglob("*") if p.is_file()]
            self.assertEqual((searched.count("detections.jsonl"), searched.count("detector-run.json")), (5, 5), "every scene take's rows and run record")
            self.assertEqual(sum("den-detect" in line and "detect.py" in line for line in (place / "argv.jsonl").read_text(encoding="utf-8").splitlines()), 10,
                             "detect.py's argv, twice per scene take: the rows, then the check against the model")
            # Control: a word planted in a copy of one receipt, and a planted scenario key, must be found.
            receipt = place / info["takes"][0]["take"] / "prepare-receipt.json"
            body = json.loads(receipt.read_text(encoding="utf-8"))
            body["note"] = f"planted {words[2]}"
            body["hops"]["3"]["scenario"] = "planted"
            (receipt.parent / "receipt.new").write_text(json.dumps(body), encoding="utf-8")
            os.replace(receipt.parent / "receipt.new", receipt)
            hits = search(place, needles)
            self.assertTrue(any(words[2] in h for h in hits) and any("key scenario" in h for h in hits), hits)
        tries = (SET.parent / "tries.jsonl").read_text(encoding="utf-8")
        self.assertFalse([w for w in needles if w in tries])

    @unittest.skip("waits for step 4: hop 12 still types its answers from the truth folder, so deleting it changes every click that reaches hop 12 "
                   "(the row says so). Hop 6 no longer reads it")
    def test_deleting_the_truth_and_display_files_leaves_decisions_identical(self):
        pass


def info_head(receipt):
    return receipt["harness"]["commit"]


# ---- test 9, the re-fit half -------------------------------------------------------------------------------

INVENTED = r"""
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { registerHooks } from 'node:module';
import { pathToFileURL } from 'node:url';
const [root, out, cornerText] = process.argv.slice(2);
const at = p => pathToFileURL(path.join(root, p)).href;
const THREE = at('app/src/vendor/three-0.180.0/three.module.js');
registerHooks({ resolve(specifier, context, next) {
  if (specifier === 'three') return { url: THREE, format: 'module', shortCircuit: true };
  const resolved = next(specifier, context);
  return resolved.url.includes('/vendor/three-') ? { ...resolved, format: 'module' } : resolved;
} });
const { default: Core } = await import(at('app/src/home-equipment-registration.js'));
const { default: Sofa } = await import(at('app/src/home-lighting-sofa-target.js'));
const { sealRegisteredDeskTarget } = await import(at('app/src/home-3d/registered-desk-target.mjs'));
const { authoredDocument } = await import(at('tools/fixtures/apartment-home-shell-fixtures.mjs'));
// An invented pack binding: the seal is real, the pack is not, and no household file is read.
const hex = text => createHash('sha256').update(text).digest('hex');
const binding = { content_revision: 'authored-den-sim-r1', registration_version: 1, manifest_digest: hex('invented manifest'),
  frame_digest: hex('invented frame'), canonical_collision_digest: hex('invented collision') };
const doc = Core.sealDocument({ ...Core.normalizeDocument(authoredDocument(binding, Core)), revision: 3 });
if (!Core.validateDocument(doc, { binding }).ok) throw Error('the invented export does not validate');
const corners = JSON.parse(cornerText).map(point_m => ({ point_m, normal_unit: [0, 0, 1] }));
const mark = Sofa.sealSofaTarget({ binding: doc.binding, equipment_ref: { revision: doc.revision, digest: doc.digest }, corners, confirmed: true },
  { sealDesk: sealRegisteredDeskTarget, digest: Core.digest });
fs.writeFileSync(path.join(out, 'registration-revision-3.json'), JSON.stringify({ ok: true, registration: doc }));
fs.writeFileSync(path.join(out, 'sofa-mark.json'), JSON.stringify(mark));
fs.writeFileSync(path.join(out, 'floor.json'), JSON.stringify({ walkable_polygon: [[0, 0], [4, 0], [4, 4], [0, 4]] }));
"""


def set_digests(folder):
    """Every file in a set, with its digest: renders, takes, receipts, fit, truth."""
    return {str(p.relative_to(folder)): sha(p.read_bytes()) for p in sorted(folder.rglob("*")) if p.is_file()}


def coordinates_in(text, point):
    """The camera's place written the ways a fit or a person would print it."""
    found = []
    for value in point:
        for places in (3, 2):
            token = f"{value:.{places}f}"
            if re.search(r"(?<![0-9])" + re.escape(token) + r"(?![0-9])", text):
                found.append(token)
    return found


class Refit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (SET and (SET / "set.json").exists()):
            raise unittest.SkipTest("needs DEN_SIM_SET=<a prepared set>")
        cls.tmp = tempfile.TemporaryDirectory(prefix="den-sim-refit-")
        base = Path(cls.tmp.name)
        cls.private = base / "private"
        cls.private.mkdir(parents=True)
        cls.env = {**ENV, "DEN_SIM_HOME": str(base), "LOCALAPPDATA": str(base)}
        # A sealed invented export and mark at revision 3, by Home's own sealers, on an authored pack.
        corners = [[1.1, 2.3, 0.45], [2.9, 2.3, 0.45], [2.9, 3.2, 0.45], [1.1, 3.2, 0.45]]   # a 1.8 m by 0.9 m couch
        script = base / "invent.mjs"
        script.write_text(INVENTED, encoding="utf-8")
        done = subprocess.run(["node", str(script), str(ROOT), str(cls.private), json.dumps(corners)],
                              capture_output=True, text=True, env=cls.env)
        if done.returncode:
            raise unittest.SkipTest("the invented mark could not be sealed: " + done.stderr[-300:])
        cls.mark, cls.export, cls.floor = cls.private / "sofa-mark.json", cls.private / "registration-revision-3.json", cls.private / "floor.json"
        cls.fits = cls.private / "fits"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def refit(self, *extra, script=None, set_dir=None):
        argv = [sys.executable, "-B", str(script or HERE / "prepare.py"), "--refit", "--set", str(set_dir or SET), "--mark", str(self.mark),
                "--export", str(self.export), "--floor", str(self.floor), "--fits", str(self.fits), *extra]
        return subprocess.run(argv, capture_output=True, text=True, env=self.env, timeout=120)

    def newest_fit(self):
        receipts = sorted(self.fits.glob("*/refit-receipt.json"), key=lambda p: p.stat().st_mtime)
        folder = receipts[-1].parent
        entry = json.loads((folder / "room-model.jsonl").read_text(encoding="utf-8").splitlines()[-1])["cameras"][-1]
        return folder, entry["pose"]["room_from_camera"]

    def test_a_refit_moves_the_camera_and_nothing_in_the_set(self):
        before = set_digests(SET)
        done = self.refit()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("re-fit registered den-virtual@", done.stdout)
        folder, pose = self.newest_fit()
        position = [row[3] for row in pose[:3]]
        mark = json.loads(self.mark.read_text(encoding="utf-8"))
        transform = prepare.placement(mark, prepare.floor_centre(self.floor), 0.0, SHOTS["couch"]["seat_height_m"])
        self.assertLess(math.dist(position, prepare.place(SHOTS["camera"]["position_m"], transform)), 0.01)
        self.assertEqual(coordinates_in(done.stdout + done.stderr, position), [])
        receipt = json.loads((folder / "refit-receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["inputs"]["facing"], None)
        info = json.loads((SET / "set.json").read_text(encoding="utf-8"))
        fit = SET / info["fit"]["folder"]
        self.assertEqual(receipt["inputs"]["clicks"], sha((fit / f"clicks-{info['marks_take']['take']}.json").read_bytes()))
        self.assertEqual(receipt["inputs"]["calibration"], sha((fit / "camera-calibration.json").read_bytes()))
        # A new facing: the reader turned a quarter left. The camera moves; the set doesn't.
        facing = self.private / "facing.json"
        facing.write_text('{"degrees": 90}', encoding="utf-8")
        turned = self.refit("--facing", str(facing))
        self.assertEqual(turned.returncode, 0, turned.stderr)
        _, pose2 = self.newest_fit()
        position2 = [row[3] for row in pose2[:3]]
        self.assertGreater(math.dist(position, position2), 2.0)
        self.assertEqual(coordinates_in(turned.stdout + turned.stderr, position2), [])
        again = self.refit("--facing", str(facing))
        self.assertIn("re-fit unchanged", again.stdout)
        self.assertEqual(set_digests(SET), before, "a re-fit changed a render, a take or a receipt")

    def test_b_changed_clicks_or_calibration_make_a_new_refit(self):
        """The re-fit copies the set's clicks and calibration and fits from them, so they are among its inputs:
        a changed one is never answered by an earlier fit. Control: the same files again read unchanged."""
        info = json.loads((SET / "set.json").read_text(encoding="utf-8"))
        with scratch() as tmp:
            copy = Path(tmp) / SET.name          # the set's own name, so an earlier fit of the real set can answer it
            linked_copy(SET, copy)
            self.assertEqual(self.refit(set_dir=copy).returncode, 0)
            same = self.refit(set_dir=copy)
            self.assertIn("re-fit unchanged", same.stdout)
            for name in (f"clicks-{info['marks_take']['take']}.json", "camera-calibration.json"):
                target = copy / info["fit"]["folder"] / name
                doc = json.loads(target.read_text(encoding="utf-8"))
                (target.parent / (name + ".new")).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")   # the same values, other bytes
                os.replace(target.parent / (name + ".new"), target)
                moved = self.refit(set_dir=copy)
                self.assertEqual(moved.returncode, 0, moved.stderr)
                self.assertIn("re-fit registered", moved.stdout, name)

    def test_control_a_fit_printing_to_stdout_is_found(self):
        with scratch() as tmp:
            copy = Path(tmp) / "scene"
            shutil.copytree(HERE, copy, ignore=shutil.ignore_patterns("__pycache__", "test_*"))
            text = (copy / "prepare.py").read_text(encoding="utf-8")
            planted = text.replace("register.run_fit(folder, marks_take, m=m, out=text)", "register.run_fit(folder, marks_take, m=m, out=sys.stdout)")
            self.assertNotEqual(planted, text)
            (copy / "prepare.py").write_text(planted.replace("ROOT = HERE.parents[2]", f"ROOT = Path({str(ROOT)!r})"), encoding="utf-8")
            facing = Path(tmp) / "facing.json"
            facing.write_text("-30", encoding="utf-8")
            done = self.refit("--facing", str(facing), script=copy / "prepare.py")
            self.assertEqual(done.returncode, 0, done.stderr)
            _, pose = self.newest_fit()
            self.assertTrue(coordinates_in(done.stdout, [row[3] for row in pose[:3]]))

    def test_control_a_render_that_takes_the_seat_changes_the_digests(self):
        with scratch() as tmp:
            copy, scene = Path(tmp) / SET.name, Path(tmp) / "scene"
            linked_copy(SET, copy)
            shutil.copytree(HERE, scene, ignore=shutil.ignore_patterns("__pycache__", "test_*"))
            text = (scene / "prepare.py").read_text(encoding="utf-8")
            hook = "        m, proposals = closed_method(method)\n        text = io.StringIO()"
            self.assertIn(hook, text)
            planted = text.replace(hook, "        rerender = sorted((set_dir / 'render').glob('*.rgb.rgba'))[0]\n"
                                         "        (set_dir / 'new.tmp').write_bytes(rerender.read_bytes() + json.dumps(transform).encode())\n"
                                         "        os.replace(set_dir / 'new.tmp', rerender)\n" + hook)
            (scene / "prepare.py").write_text(planted.replace("ROOT = HERE.parents[2]", f"ROOT = Path({str(ROOT)!r})"), encoding="utf-8")
            before = set_digests(copy)
            facing = Path(tmp) / "facing.json"
            facing.write_text("45", encoding="utf-8")
            done = self.refit("--facing", str(facing), script=scene / "prepare.py", set_dir=copy)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertNotEqual(set_digests(copy), before)
            self.assertEqual(set_digests(SET), {k: v for k, v in before.items()}, "the control must not touch the real set")


# ---- the data root -----------------------------------------------------------------------------------------

class DataRoot(unittest.TestCase):
    """DEN_SIM_HOME, or else <home>\\den-sim; never %LOCALAPPDATA%, which the Claude app's processes see privately."""

    def test_the_default_is_the_home_folder_and_never_localappdata(self):
        with scratch() as tmp:
            home, lad = Path(tmp) / "home", Path(tmp) / "lad"
            planted = lad / "den-sim" / "prepared" / "0123456789ab"
            planted.mkdir(parents=True)
            (planted / "set.json").write_text('{"schema": "den-sim-prepared-set-1"}', encoding="utf-8")
            env = {k: v for k, v in ENV.items() if k != "DEN_SIM_HOME"}
            env.update(USERPROFILE=str(home), HOME=str(home), LOCALAPPDATA=str(lad))
            code = (f"import sys; sys.path.insert(0, {str(HERE)!r}); import prepare; prepare.guard(False); "
                    "print(prepare.data_root())")
            done = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(Path(done.stdout.strip()), home / "den-sim")
            # Control: the planted %LOCALAPPDATA% set is not where the root points, so nothing there is read.
            self.assertNotIn(str(lad), done.stdout)
            self.assertEqual(prepare.data_root({"DEN_SIM_HOME": str(lad)}), lad)
            self.assertEqual(prepare.data_root({}), Path.home() / "den-sim")


# ---- the private transform, the guard and the receipts' tool pins ------------------------------------------

class Placement(unittest.TestCase):
    MARK = {"schema": prepare.MARK_SCHEMA, "seat": {"point_m": [2.0, 3.0, 0.47]},
            "zone": {"vertices_m": [[1.0, 2.6, 0.47], [3.0, 2.6, 0.47], [3.0, 3.4, 0.47], [1.0, 3.4, 0.47]], "centre_m": [2.0, 3.0, 0.47]}}

    def test_the_reader_faces_away_from_the_long_edge_toward_the_floor(self):
        t = prepare.placement(self.MARK, [2.0, 1.0], 0.0, 0.44)
        self.assertTrue(np.allclose(t["facing_unit"], [0, -1], atol=1e-12))
        self.assertTrue(np.allclose(prepare.place([0.0, 0.0, 0.44], t), [2.0, 3.0, 0.47]))
        self.assertTrue(np.allclose(t["facing_unit"], prepare.placement(self.MARK, [2.0, 1.0], 0.0, 0.44)["facing_unit"]))
        behind = prepare.placement(self.MARK, [2.0, 5.0], 0.0, 0.44)
        self.assertTrue(np.allclose(behind["facing_unit"], [0, 1], atol=1e-12))
        left = prepare.placement(self.MARK, [2.0, 1.0], 90.0, 0.44)      # a quarter turn to the reader's left
        self.assertTrue(np.allclose(left["facing_unit"], [1, 0], atol=1e-12))

    def test_a_seat_on_the_floor_centre_is_named_a_tie_break(self):
        """The stand-in seat: a square around the floor's centre, so no edge is longest and no side is nearer."""
        self.assertEqual(prepare.placement(self.MARK, [2.0, 1.0], 0.0, 0.44)["facing_rule"], "perpendicular to the longest edge, toward the floor's centre")
        square = {"schema": prepare.MARK_SCHEMA, "seat": {"point_m": [2.0, 2.0, 0.45]},
                  "zone": {"vertices_m": [[1.6, 1.6, 0.45], [2.4, 1.6, 0.45], [2.4, 2.4, 0.45], [1.6, 2.4, 0.45]], "centre_m": [2.0, 2.0, 0.45]}}
        tied = prepare.placement(square, [2.0, 2.0], 0.0, 0.44)
        self.assertTrue(tied["facing_rule"].startswith("a tie-break, not the stated rule"))
        self.assertTrue(np.allclose(tied["facing_unit"], [0, 1], atol=1e-12))
        # Away from the centre the square still has no single longest edge, so it is still a tie-break.
        for x, y in ((1.5, 1.5), (1.5, -1.4), (-1.2, 1.3)):
            moved = {**square, "zone": {"vertices_m": [[vx + x, vy + y, vz] for vx, vy, vz in square["zone"]["vertices_m"]],
                                        "centre_m": [2.0 + x, 2.0 + y, 0.45]}}
            self.assertTrue(prepare.placement(moved, [2.0, 2.0], 0.0, 0.44)["facing_rule"].startswith("a tie-break"), (x, y))
        # Control: a rectangle's two longest edges are parallel, so the stated rule holds.
        oblong = {**square, "zone": {"vertices_m": [[1.4, 1.6, 0.45], [2.6, 1.6, 0.45], [2.6, 2.4, 0.45], [1.4, 2.4, 0.45]],
                                     "centre_m": [3.0, 3.0, 0.45]}}
        self.assertEqual(prepare.placement(oblong, [2.0, 2.0], 0.0, 0.44)["facing_rule"], "perpendicular to the longest edge, toward the floor's centre")

    def test_a_mark_without_its_shape_is_refused(self):
        for broken in ({**self.MARK, "schema": "something-else"}, {**self.MARK, "zone": {"vertices_m": [[0, 0, 0]]}}, {**self.MARK, "seat": {}}):
            with self.assertRaises(prepare.Stop):
                prepare.placement(broken, [0, 0], 0, 0.44)


class Guard(unittest.TestCase):
    def run_guarded(self, loopback, code):
        program = f"import sys, socket; sys.path.insert(0, {str(HERE)!r}); import prepare; prepare.guard({loopback}); {code}"
        return subprocess.run([sys.executable, "-B", "-c", program], capture_output=True, text=True, env=ENV, timeout=30)

    def test_prepare_allows_loopback_only(self):
        bind = "s = socket.socket(); s.bind(('127.0.0.1', 0)); print('bound')"
        self.assertIn("bound", self.run_guarded(True, bind).stdout)
        self.assertIn("PermissionError", self.run_guarded(True, "socket.create_connection(('192.0.2.1', 80), timeout=1)").stderr)
        self.assertIn("PermissionError", self.run_guarded(True, "socket.getaddrinfo('example.com', 80)").stderr)
        # Control: the re-fit's guard refuses loopback too.
        self.assertIn("PermissionError", self.run_guarded(False, bind).stderr)


class Refused(unittest.TestCase):
    def test_a_refused_prepare_still_writes_its_display_file(self):
        """A set prepare stops on keeps a display file of the takes it recorded, so the page can open it and
        show the refusal. Here the render refuses at once: no Playwright can be found."""
        with scratch() as tmp:
            empty = Path(tmp) / "no-node-modules"
            empty.mkdir()
            env = {**ENV, "NODE_PATH": str(empty), "DEN_SIM_HOME": tmp, "LOCALAPPDATA": tmp}
            done = subprocess.run([sys.executable, "-B", "-X", "utf8", str(HERE / "prepare.py"), "--out", str(Path(tmp) / "prepared")],
                                  capture_output=True, text=True, env=env, timeout=300)
            self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
            sets = [p for p in (Path(tmp) / "prepared").iterdir() if p.is_dir() and not p.name.startswith(".")]
            self.assertEqual(len(sets), 1)
            info = json.loads((sets[0] / "set.json").read_text(encoding="utf-8"))
            self.assertEqual(info["refusal"]["hop"], 1)
            display = json.loads((sets[0] / "display.json").read_text(encoding="utf-8"))
            self.assertEqual((display["schema"], display["scenes"]), (prepare.SCHEMA_DISPLAY, []))
            self.assertFalse((Path(tmp) / "prepared" / "tries.jsonl").exists(), "a refused set is no try")


class ReceiptParts(unittest.TestCase):
    """prepare.py's receipt pieces on hand-made inputs, so they are tested without preparing a set."""

    def test_a_receipt_is_keyed_by_the_digest_of_frames_csv(self):
        with scratch() as tmp:
            take = mini_take(Path(tmp) / "take")
            got = prepare.receipt(take, "scene", {}, None, Path(tmp), 1, "2026-11-09", 0.0, {"commit": "x"})
            self.assertEqual(got["take_digest"], sha((take / "frames.csv").read_bytes()))
            self.assertNotEqual(got["take_digest"], sha((take / "take.json").read_bytes()))

    def test_a_registered_fit_reads_adapter_and_a_refused_one_missing(self):
        with scratch() as tmp:
            marks, fit = mini_take(Path(tmp) / "marks", frames=2), Path(tmp) / "fit"
            fit.mkdir()
            for name in ("worksheet.json", "camera-calibration.json", f"clicks-{marks.name}.json", "room-model.jsonl", "fit-1.json", "fit.log"):
                (fit / name).write_text("{}\n", encoding="utf-8")
            tools = {"den-register": {"commit": "c", "pin": "refs.json tools.den-register", "files": {}}}
            hop = prepare.fit_hop(fit, marks, {"result": "registered", "transform_id": "den-virtual@x.1", "fit": {"error_bound_m": 0.0066}, "reasons": []}, {}, 1, tools)
            self.assertEqual((hop["word"], hop["error_bound_mm"]), ("ADAPTER", 6.6))
            self.assertEqual({read["path"].split("/")[-1] for read in hop["read"]},
                             {"000001-501.npz", "worksheet.json", "camera-calibration.json", f"clicks-{marks.name}.json"})
            refused = prepare.fit_hop(fit, marks, {"result": "refused", "reasons": [{"reason": "slots_open"}]}, {}, 1, tools)
            self.assertEqual((refused["word"], refused["reasons"]), ("MISSING", ["slots_open"]))

    def test_an_uncommitted_harness_file_is_recorded_as_one(self):
        real = prepare.git

        def edited(*args):
            if args[0] == "hash-object" and args[1].endswith("vcam.py"):
                return b"0" * 40 + b"\n"
            return real(*args)
        with mock.patch.object(prepare, "git", edited):
            got = prepare.harness_files()
        self.assertEqual(got["uncommitted"], ["tools/den-sim/scene/vcam.py"])
        self.assertEqual(got["files"]["tools/den-sim/scene/vcam.py"]["blob"], "0" * 40)
        self.assertEqual(prepare.harness_files()["uncommitted"], [], "control: this branch's committed files")


TRACER = r"""
import json, os, sys
_sink = open(os.path.join(os.environ["DEN_SIM_TRACE"], "%d.jsonl" % os.getpid()), "a", encoding="utf-8")
_sink.write(json.dumps({"argv": sys.argv}) + "\n")
_busy, _WRITES = [False], {"os.mkdir", "os.rename", "os.replace", "os.remove", "os.rmdir", "os.link", "os.symlink", "os.truncate",
                           "shutil.copyfile", "shutil.copytree", "shutil.rmtree", "shutil.move", "tempfile.mkstemp", "tempfile.mkdtemp"}
def _hook(event, args):
    if _busy[0] or (event != "open" and event not in _WRITES):
        return
    if event == "open":
        path, mode, flags = (list(args) + [None, None, None])[:3]
        if isinstance(path, int) or not ((isinstance(mode, str) and any(c in mode for c in "wax+"))
                                         or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND))):
            return
    else:
        path = args[0] if args else None
    _busy[0] = True
    try:
        _sink.write(json.dumps({"event": event, "path": os.path.abspath(os.fsdecode(path))}) + "\n")
        _sink.flush()
    finally:
        _busy[0] = False
sys.addaudithook(_hook)
"""


@unittest.skipUnless(os.environ.get("DEN_SIM_PREPARE_TRACE") == "1", "a whole prepare run, about 3 minutes: set DEN_SIM_PREPARE_TRACE=1")
class Trace(unittest.TestCase):
    """Test 19: every Python process a prepare run starts (prepare.py, and detect.py in the venv) logs each write
    it asks for through an audit hook loaded from sitecustomize. Native code (torch, onnxruntime, Chromium under
    render.mjs) can't be hooked: TEMP points into the data root for them, detect.py chdirs into its state folder,
    and the trace below shows where the Python side wrote."""

    def test_a_prepare_run_writes_only_under_its_data_root(self):
        with scratch() as tmp:
            tmp = Path(tmp)
            home, temp, trace, hook = tmp / "home", tmp / "temp", tmp / "trace", tmp / "hook"
            for folder in (home, temp, trace, hook):
                folder.mkdir()
            (hook / "sitecustomize.py").write_text(TRACER, encoding="utf-8")
            env = {**ENV, "DEN_SIM_HOME": str(home), "TEMP": str(temp), "TMP": str(temp), "LOCALAPPDATA": str(tmp / "lad"), "APPDATA": str(tmp / "ad"),
                   "PYTHONPATH": str(hook), "DEN_SIM_TRACE": str(trace)}
            done = subprocess.run([sys.executable, "-B", "-X", "utf8", str(HERE / "prepare.py")], capture_output=True, text=True, env=env, timeout=1200)
            self.assertEqual(done.returncode, 0, done.stdout[-2000:] + done.stderr[-2000:])
            processes, writes = [], []
            for log in trace.glob("*.jsonl"):
                lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
                processes.append(" ".join(lines[0]["argv"]))
                writes += [line["path"] for line in lines[1:]]
            self.assertEqual(sum("detect.py" in p for p in processes), 10, "detect.py ran in the venv, traced: twice per scene take")
            self.assertTrue(any(p.endswith("prepare.py") for p in processes))
            inside = lambda path, root: Path(path).resolve().is_relative_to(root.resolve())
            self.assertGreater(len(writes), 100)
            self.assertEqual([w for w in writes if not inside(w, home)], [], "no write outside the data root")
            # The run's own TEMP, LOCALAPPDATA and APPDATA, and the real AppData that holds this user's TEMP.
            self.assertEqual([w for w in writes if any(inside(w, root) for root in (temp, tmp / "lad", tmp / "ad", Path.home() / "AppData"))], [], "none under AppData or TEMP")
            self.assertFalse(any(temp.iterdir()), "and nothing native landed in TEMP either")
            print(f"\n  traced {len(processes)} Python processes, {len(writes)} writes, all under the data root", file=sys.stderr)
            self.assertTrue([w for w in [str(tmp / "elsewhere.txt")] if not inside(w, home)], "control: a write outside is found")


class Archive(unittest.TestCase):
    def test_each_archived_file_is_recorded_by_the_bytes_that_were_extracted(self):
        """A receipt's blob for an archived tool comes from the file that runs, not from ls-tree, so a click
        can tell an edited archive from the pin. The control: an unedited file still records the pinned blob."""
        real_run = prepare.subprocess.run

        def edited_archive(argv, **kwargs):
            done = real_run(argv, **kwargs)
            if "archive" not in argv or "tools/den-take" not in argv:
                return done
            out = io.BytesIO()
            with tarfile.open(fileobj=io.BytesIO(done.stdout)) as source, tarfile.open(fileobj=out, mode="w") as target:
                for info in source.getmembers():
                    data = source.extractfile(info).read() if info.isfile() else None
                    if info.name == "tools/den-take/take.py":
                        data += b"\n# edited after the pin\n"
                        info.size = len(data)
                    target.addfile(info, io.BytesIO(data) if data is not None else None)
            return subprocess.CompletedProcess(done.args, done.returncode, out.getvalue(), done.stderr)

        path_before = list(sys.path)
        try:
            with scratch() as tmp, mock.patch.object(prepare.subprocess, "run", edited_archive):
                tree = Path(tmp) / "tools"
                record = prepare.archive(tree, prepare.pins())["den-take"]
                ran = (tree / "tools/den-take/take.py").read_bytes()
        finally:
            sys.path[:] = path_before
        pinned = lambda name: git("ls-tree", record["commit"], "--", name).decode().split()[2]
        take_py = record["files"]["tools/den-take/take.py"]
        self.assertTrue(ran.endswith(b"# edited after the pin\n"))
        self.assertEqual(take_py["sha256"], sha(ran))
        self.assertEqual(take_py["blob"], hashlib.sha1(b"blob %d\0" % len(ran) + ran).hexdigest())
        self.assertNotEqual(take_py["blob"], pinned("tools/den-take/take.py"))
        self.assertEqual(record["files"]["tools/den-take/check.py"]["blob"], pinned("tools/den-take/check.py"))


@NEEDS_SET
class Receipts(unittest.TestCase):
    def test_each_take_has_a_receipt_keyed_by_its_digest_with_pinned_tools(self):
        info, _, _ = the_set()
        for item in info["takes"]:
            receipt = json.loads((SET / item["take"] / "prepare-receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["take_digest"], item["digest"])
            self.assertEqual(receipt["take_digest"], sha((SET / item["take"] / "frames.csv").read_bytes()))
            self.assertEqual([receipt["hops"][h]["word"] for h in "12345"], ["SCRIPTED", "SCRIPTED", "ADAPTER", "ADAPTER", "ADAPTER"])
            six = receipt["hops"].get("6")
            if item["kind"] == "marks":
                self.assertIsNone(six, "the marks take gets no rows")
                continue
            # Hop 6's part, from detector-run.json: the rows and the run record by digest, the pinned weights,
            # the tools as they ran, and the check against the model within 1 px.
            run = json.loads((SET / item["take"] / "detector-run.json").read_text(encoding="utf-8"))
            self.assertEqual(six["rows"]["sha256"], sha((SET / item["take"] / "detections.jsonl").read_bytes()))
            self.assertEqual(six["run"]["sha256"], sha((SET / item["take"] / "detector-run.json").read_bytes()))
            self.assertEqual((run["take_digest"], run["rows"]["sha256"]), (item["digest"], six["rows"]["sha256"]))
            self.assertEqual(six["weights"], prepare.den_detect.lock()["weights"])
            self.assertEqual((six["commit"], six["counts"], run["counts"]["over_limit"]), (git("rev-parse", REFS["tools"]["rung-2-observe"]).decode().strip(), run["counts"], 0))
            self.assertEqual(six["detect_py"]["blob"], git("rev-parse", f"{info_head(receipt)}:tools/den-detect/detect.py").decode().strip())
            self.assertTrue(six["checked"]["ok"] and six["checked"]["max_px"] <= 1.0 and len(six["checked"]["frames"]) == 3, six["checked"])
            self.assertEqual(receipt["hops"]["4"]["second_person"]["answered_by"], prepare.HARNESS)
            self.assertIsNone(receipt["refusal"])
            for hop, tool in (("3", "den-take"), ("5", "den-register")):
                self.assertTrue(git("rev-parse", REFS["tools"][tool]).decode().strip() == receipt["hops"][hop]["commit"])
        self.assertEqual(json.loads((SET / info["fit"]["folder"] / "room-model.jsonl").read_text(encoding="utf-8").splitlines()[-1])["cameras"][-1]["method"],
                         "virtual-scene + fit")


if __name__ == "__main__":
    unittest.main(verbosity=2)
