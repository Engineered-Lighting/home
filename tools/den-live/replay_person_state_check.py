#!/usr/bin/env python3
"""The plan's 02-3 real-replay check: den-live's person state on your sofa replays, in three lines. Workstation only.

    python -B C:\\claude\\home\\.claude\\worktrees\\den-live-registration-v2\\tools\\den-live\\replay_person_state_check.py "<your sofa replays folder>"

Each take in the folder is one trial: you sit down by its begin cue and stay seated to its hold_phone_end cue. For each,
on this PC with nothing sent anywhere, den-live's own Perceiver runs on the take's frames and the detector rows recorded
beside them; the Qwen ask (ask.Asker, with a stand-in that answers every ask "reading" at once) and the lighting preview
(preview.Preview, on one fictional fixture) read each snapshot as the live loop hands it to them
(run_local.evidence_observer).

It prints exactly three lines, and never a coordinate, a pose, an image or anything read from a file:
- since, ask less preview: the largest difference between the seated since_mono each ask's reply carries and the
  preview's seated since when that reply reaches it, over every reply. It should be 0.
- seated latency per trial: from the begin cue to the capture of the first frame published seated. Each should be
  2.5 s or less.
- revision bumps while sitting, per trial: how often the seated person's binding (track id or association revision)
  moved after seated was first published. Each should be 0.
It exits 0 when all three pass, 1 when one doesn't, and 2 when it couldn't run (one line says why).

What it assumes, since the replays' own format is private and wasn't read: den-sim's prepared format (tools/den-sim,
README, prepared\\<set>). Each take is a folder holding take.json (its cues on this PC's monotonic clock, the begin cue
among them), frames.csv (rgb_seq, t_host_s, recv_pc_mono_s, file), the frames' bundles, and detections.jsonl (the
detector rows recorded for it); a take without a begin cue or recorded rows isn't a trial, and you're seated by its
begin cue. The registration is --room-model, else the set's own fit folder (set.json's fit.folder). The tracker's
floor is --floor=x0,y0,x1,y1, else your active scan's floor padded 1 m, as the live loop uses.
How it replays: on the camera's clock (t_host_s, real time even in a set den-sim recorded faster), each frame arriving
TRANSIT_S after capture, the cues put on that clock between the frames either side; the detector takes a frame for its
recorded compute time, and while it's busy only the newest frame waits, as live; every frame is taken as lit, since
the recorded rows carry no brightness.
On den-sim's renders the latency is over 2.5 s: its scenes start seated, so den-track's association settles (about
1.9 s) before the hold can begin. A sit-down after walking in is tracked before it.
"""
import argparse
import base64
import bisect
import contextlib
import csv
import io
import json
import math
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ask  # noqa: E402
import coordinator_live  # noqa: E402
import den_live_scene as scene  # noqa: E402
import perceive  # noqa: E402
import preview  # noqa: E402
import register_now as rn  # noqa: E402  the method file's gate
import run_local as rl  # noqa: E402
import sofa_check as sc  # noqa: E402
import source  # noqa: E402
from contracts import take_scale  # noqa: E402

SINCE_MAX_S, LATENCY_MAX_S, BUMPS_MAX = 0.0, 2.5, 0
LIT = 120.0             # the luma a recorded row stands in with: above perceive's LIGHT_LUMA
COMPUTE_S = 0.07        # a row's compute time when it doesn't record one
TRANSIT_S = 0.12        # camera to this PC, as test_live's bundles take it
SCAN = "replay-scan"    # the scan revision the Perceiver's frames and the preview's binding share
FIXTURES = {"F1": {"aim_origin_m": [0.0, 0.0, 2.5], "horizontal_world_reference": [0.0, 1.0, 0.0]}}   # fictional
FACTS = {"people_visible": "one", "subject_posture": "sitting", "held_object": "book", "book_state": "open",
         "gaze_target": "book", "typing_visible": "no", "screen_facing_subject": "not_visible",
         "activity_hypothesis": "reading", "visible_basis": ["person_visible", "seated_posture"],
         "change_vs_prior": "no_prior"}


class Stop(Exception):
    """Words for the one line that says why it couldn't run: never a path or a file's content."""


# ---- the stand-ins: the ask's stills, Frigate's occupancy, Qwen ------------------------------------------------------

class Stills:
    """The detector process's stills: every request answered at once with placeholder JPEG bytes."""

    def __init__(self):
        self.on_stills = None

    def send_alone(self, header):
        if self.on_stills is not None:
            self.on_stills({"stills_id": header["id"], "frames": header["frames"],
                            "jpegs": [base64.b64encode(b"\xff\xd8" + bytes([i + 1]) * 30).decode()
                                      for i in range(len(header["frames"]))]})
        return True


class Occupied:
    def evidence(self):
        return {"raw": "on", "effective": "on", "read_age_ms": 0.0, "revision": 0, "why": None}


def answer(_body):
    return {"model": scene.SERVED_MODEL, "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "choices": [{"message": {"role": "assistant", "content": json.dumps(FACTS)}, "finish_reason": "stop"}]}


class Recorded:
    """The take's recorded detector rows, by RGB sequence, available their own compute time after capture."""

    def __init__(self, rows):
        self.rows = rows

    def detect(self, bundle, delay_s):
        row = self.rows.get(bundle.rgb_seq)
        if row is None:
            return {"error": "KeyError"}
        return {"row": dict(row, available_ts=row["frame"]["capture_ts"] + delay_s + compute_s(row)), "luma": LIT,
                "compute_s": compute_s(row)}


def compute_s(row):
    value = row.get("available_ts", 0) - row["frame"]["capture_ts"]
    return value if type(value) is float and 0 <= value < 5 else COMPUTE_S


# ---- the replays ---------------------------------------------------------------------------------------------------

def trials(folder, only=None):
    """The takes in folder that are trials, in name order: a begin cue and recorded detector rows."""
    found = []
    for take in sorted(path for path in Path(folder).iterdir() if path.is_dir()):
        if only and take.name not in only:
            continue
        try:
            doc = json.loads((take / "take.json").read_text(encoding="utf-8"))
            cues = {cue["cue"]: float(cue["pc_mono_s"]) for cue in doc["cues"]}
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if "begin" in cues and (take / "detections.jsonl").is_file() and (take / "frames.csv").is_file():
            found.append((take, cues))
    if not found:
        raise Stop("no take in that folder has a begin cue and recorded detector rows (den-sim's prepared format)")
    return found


def room_model_of(folder, given):
    if given is not None:
        return Path(given)
    try:
        fit = json.loads((Path(folder) / "set.json").read_text(encoding="utf-8"))["fit"]["folder"]
        return Path(folder) / fit / "room-model.jsonl"
    except (OSError, ValueError, KeyError, TypeError):
        raise Stop("the folder has no set.json naming its registration: pass --room-model") from None


def only_camera(path):
    try:
        ids = {camera.get("camera_id") for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()
               for camera in json.loads(line).get("cameras") or []}
    except (OSError, ValueError, AttributeError):
        raise Stop("the room model doesn't read") from None
    if len(ids) != 1:
        raise Stop("the room model has %d cameras: pass --camera" % len(ids))
    return ids.pop()


class Setup:
    """What every trial shares: the pinned trees, the registration, the floor, the coordinator's core."""

    def __init__(self, tools_root, room_model, camera_id, floor, work):
        trees = {key: sc.archive(tools_root, key) for key in ("observe", "track", "register", "coordinator")}
        self.tools = perceive.Tools(trees["observe"], trees["track"])
        import check   # den-take's, on the path Tools set: a bundle's session
        self.check = check
        m, _live = rn.load_method(rn.den_register(trees["register"]))
        self.gate, self.room_model, self.camera_id, self.work = m["gate_m"], Path(room_model), camera_id, Path(work)
        if not self.room_model.is_file():
            raise Stop("the registration's room model isn't there")
        try:
            model = self.tools.roommodel.load(self.room_model)
            transform = self.tools.roommodel.current_transform_id(model, camera_id)
            self.scale = take_scale((self.tools.roommodel.resolve(model, transform).get("mount") or {}).get("take"))
        except self.tools.roommodel.RegistrationRefused:
            raise Stop("the registration can't be used for that camera") from None
        self.method = perceive.track_method_copy(self.tools, list(floor), self.work)
        self.core, _learn = coordinator_live.load_core(trees["coordinator"])


def camera_time(pc_mono, recv, host):
    """A cue's time on this PC's monotonic clock, put on the camera's clock between the frames that arrived either side
    of it. The camera's clock is the take's own timeline, real time even in a set den-sim recorded faster."""
    if len(recv) < 2:
        return host[0] + (pc_mono - recv[0])
    i = min(max(bisect.bisect_right(recv, pc_mono) - 1, 0), len(recv) - 2)
    span = recv[i + 1] - recv[i]
    return host[i] + (pc_mono - recv[i]) * ((host[i + 1] - host[i]) / span if span > 0 else 1.0)


def paced(frames):
    """[(frame, finished_mono)] as the live loop takes them: the detector takes one frame at a time for its compute
    time, and while it's busy the mailbox keeps only the newest frame (source.Mailbox)."""
    out, waiting, free = [], None, -math.inf
    for frame in frames:   # in arrival order
        if waiting is not None and free <= frame["arrival"]:
            free = max(free, waiting["arrival"]) + waiting["compute"]
            out.append((waiting, free))
            waiting = None
        if free <= frame["arrival"]:
            free = frame["arrival"] + frame["compute"]
            out.append((frame, free))
        else:
            waiting = frame
    if waiting is not None:
        out.append((waiting, max(free, waiting["arrival"]) + waiting["compute"]))
    return out


def run_trial(setup, take, cues, index):
    """(latency_s or None, [(ask since, preview since)], bumps) for one take."""
    rows = {}
    for line in (take / "detections.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["frame"]["rgb_seq"]] = row
    listed = sorted(csv.DictReader(io.StringIO((take / "frames.csv").read_text(encoding="utf-8"))),
                    key=lambda row: float(row["recv_pc_mono_s"]))
    recv, host = [float(row["recv_pc_mono_s"]) for row in listed], [float(row["t_host_s"]) for row in listed]
    # Every time on the camera's clock, arriving TRANSIT_S after capture, as test_live's bundles do.
    frames = [{"row": row, "arrival": float(row["t_host_s"]) + TRANSIT_S, "compute": compute_s(rows[int(row["rgb_seq"])])}
              for row in listed if int(row["rgb_seq"]) in rows]
    begin = camera_time(cues["begin"], recv, host) + TRANSIT_S
    clock, wall = [0.0], [time.time()]   # the Perceiver's session ends now, after the registration's take, as live
    perceiver = perceive.Perceiver(setup.tools, room_model_path=setup.room_model, camera_id=setup.camera_id,
                                   max_error_bound_m=setup.gate, track_method_path=setup.method, home="den",
                                   detector=Recorded(rows), require_source_correction=False, content_revision=SCAN,
                                   now=lambda: wall[0], depth_scale=setup.scale)
    state = setup.work / ("trial-%d" % index)
    asker = ask.Asker(detector=Stills(), occupancy=Occupied(), post=answer, state=state, site_id="replay",
                      camera_id=setup.camera_id, clock=lambda: clock[0], wall=lambda: wall[0], start=False)
    binding = {"transform_id": perceiver.camera.transform_id, "scan_revision": SCAN, "equipment_revision": 0,
               "equipment_digest": "replay"}
    lighting = preview.Preview(setup.core, binding=binding, fixtures=FIXTURES, state=state, clock=lambda: clock[0],
                               append=lambda _row: None, threaded=False)
    sinces, offer = [], lighting.offer_inference

    def inference(reply):   # the ask's since against the preview's, as the reply reaches the preview
        sinces.append(((reply.gate or {}).get("seated_since_mono"), lighting.since))
        offer(reply)
    lighting.offer_inference = inference
    observe = rl.evidence_observer(asker, lighting)
    first, held, bumps = None, None, 0
    started = wall[0]
    try:
        for frame, finished in paced(frames):
            row = frame["row"]
            data = (take / row["file"]).read_bytes()
            session = setup.check.read_bundle(data)[0]["session"]
            clock[0], wall[0] = finished, started + (finished - frames[0]["arrival"])
            bundle = source.Bundle(data, session, int(row["rgb_seq"]), float(row["t_host_s"]),
                                   started + (frame["arrival"] - frames[0]["arrival"]), frame["arrival"])
            event = perceiver.process(bundle, {"status": "ok", "why": None}, clock[0])
            if event is None:
                continue
            snapshot = perceiver.snapshot
            observe(event, snapshot)
            while not asker.jobs.empty():
                job, args, kwargs = asker.jobs.get_nowait()
                job(*args, **kwargs)
            if snapshot.status != "fresh" or len(snapshot.tracks) != 1:
                continue
            track = snapshot.tracks[0]
            if first is None and track.seated is not None and track.seated.state:
                first, held = snapshot.frame.capture_mono, (track.track_id, track.association_revision)
            elif first is not None and (track.track_id, track.association_revision) != held:
                bumps, held = bumps + 1, (track.track_id, track.association_revision)
    finally:
        asker.close()
    return (None if first is None else first - begin), sinces, bumps


# ---- the three lines -----------------------------------------------------------------------------------------------

def lines(results):
    """The three lines, and whether all three pass."""
    pairs = [pair for _latency, sinces, _bumps in results for pair in sinces]
    known = [abs(a - p) for a, p in pairs if isinstance(a, (int, float)) and isinstance(p, (int, float))]
    missing = len(pairs) - len(known)
    worst = max(known) if known else None
    since_ok = bool(pairs) and not missing and worst <= SINCE_MAX_S
    if not pairs:
        since = "since, ask less preview: no ask was answered, so nothing to compare (should be 0)"
    else:
        since = "since, ask less preview: %s over %d replies%s (should be 0)" % (
            "-" if worst is None else "%.3f s at most" % worst, len(pairs),
            ", %d with no seated since on one side" % missing if missing else "")
    latencies = [latency for latency, _sinces, _bumps in results]
    latency_ok = all(latency is not None and latency <= LATENCY_MAX_S for latency in latencies)
    latency = "seated latency per trial: %s (each should be 2.5 s or less)" % ", ".join(
        "never seated" if value is None else "%.2f s" % value for value in latencies)
    bumps = [count for _latency, _sinces, count in results]
    bumps_ok = all(count <= BUMPS_MAX for count in bumps)
    bump = "revision bumps while sitting per trial: %s (each should be 0)" % ", ".join(str(count) for count in bumps)
    return [since, latency, bump], since_ok and latency_ok and bumps_ok


def check(folder, *, room_model=None, camera_id=None, floor=None, only=None, tools_root=None):
    """(the three lines, all passed) for the replays in folder."""
    folder = Path(folder)
    if not folder.is_dir():
        raise Stop("that replays folder isn't there")
    found = trials(folder, only)
    model = room_model_of(folder, room_model)
    camera_id = camera_id or only_camera(model)
    if floor is None:
        floor = perceive.padded(rl.scan_floor(rl.content_packs(), rl.den_site())[0])
    with tempfile.TemporaryDirectory(prefix="den-live-replay-") as work, \
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        setup = Setup(Path(tools_root) if tools_root else sc.data_root() / "tools", model, camera_id, floor, work)
        results = [run_trial(setup, take, cues, index) for index, (take, cues) in enumerate(found)]
    return lines(results)


def main(argv=None, **test):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("replays", type=Path, help="the folder of sofa replays (den-sim's prepared format)")
    parser.add_argument("--room-model", type=Path, help="the registration they were taken at, else the set's own fit")
    parser.add_argument("--camera", help="the camera id in the room model, when it has more than one")
    parser.add_argument("--floor", help="the tracker's floor box x0,y0,x1,y1 in metres, else your scan's padded")
    parser.add_argument("--take", action="append", help="only this take (repeat for more)")
    args = parser.parse_args(argv)
    floor = None
    if args.floor is not None:
        try:
            floor = [float(value) for value in args.floor.split(",")]
        except ValueError:
            floor = []
        if len(floor) != 4 or not all(map(math.isfinite, floor)) or floor[2] <= floor[0] or floor[3] <= floor[1]:
            print("replay check: stopped: --floor is four numbers, x0,y0,x1,y1, with x1 over x0 and y1 over y0.")
            return 2
    try:
        found, passed = check(args.replays, room_model=args.room_model, camera_id=args.camera, floor=floor,
                              only=args.take, **test)
    except (Stop, sc.Stop, perceive.Stop) as exc:
        print("replay check: stopped: %s." % str(exc).rstrip("."))
        return 2
    except Exception as exc:   # a type name only: its text could quote what it read
        print("replay check: stopped (%s)." % type(exc).__name__)
        return 2
    for line in found:
        print(line)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
