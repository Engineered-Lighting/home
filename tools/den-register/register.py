#!/usr/bin/env python3
"""Rung 1: register the den camera to the room frame from measured control points.

    python -B tools\\den-register\\register.py worksheet C:\\den-register
    python -B tools\\den-register\\register.py click C:\\den-register --take C:\\den-takes\\reg-1
    python -B tools\\den-register\\register.py fit C:\\den-register --take C:\\den-takes\\reg-1
    python -B tools\\den-register\\register.py check-mount C:\\den-register --take C:\\den-takes\\later-1
    python -B tools\\den-register\\register.py resolve C:\\den-register den-oak@2026-11-10.1

Standard library only. It reads R's takes and the owner's worksheet, serves
the click page on 127.0.0.1 only, and writes into the working folder, which
must be outside every git checkout and OneDrive. It never opens a camera or
contacts another host. The method is REGISTER-V1.md.
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
from pathlib import Path

import method as method_file
import records
import rig
import rigid
import scene
import serve
import worksheet as worksheet_file
from scene import Refused

sys.path.insert(0, str(scene.DEN_TAKE))
import check  # noqa: E402  R's bundle reader, for the colour view
import take as den_take  # noqa: E402  R's git-checkout and OneDrive tests

SCHEMA_CLICKS = "den-register-clicks-1"
SCHEMA_FIT = "den-register-fit-1"
SCHEMA_MOUNT = "den-register-mount-1"
WORKSHEET = "worksheet.json"
ROOM_MODEL = "room-model.jsonl"
CALIBRATIONS = "calibration-records.jsonl"
CAMERA_CALIBRATION = "camera-calibration.json"
METHOD = "tape + fit"
RECHECK = [
    "the camera's mount: if it was bumped, turned or re-seated, the old registration no longer holds",
    "each control-point mark: if furniture carrying a mark moved, measure that mark again",
    "then record a short take, click every point on one of its frames, and fit again",
]


# ---- the working folder ------------------------------------------------------------

def work_dir(path, *, create=False):
    """An absolute folder outside every git checkout and OneDrive: it holds real measurements."""
    path = Path(path)
    if not path.is_absolute():
        raise Refused("work_folder_refused", "give an absolute folder, for example C:\\den-register")
    path = path.resolve()
    if den_take.inside_checkout(path):
        raise Refused("work_folder_refused", "the folder is inside a git checkout; measurements never go into git")
    if den_take.in_onedrive(path):
        raise Refused("work_folder_refused", "the folder is inside OneDrive; measurements stay on this PC")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    elif not path.is_dir():
        raise Refused("work_folder_refused", f"{path} does not exist; run the worksheet step first")
    return path


def write_json(path, value):
    """Write UTF-8 JSON with LF line ends, atomically."""
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes((json.dumps(value, indent=1, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
    os.replace(temporary, path)


def next_numbered(folder, stem):
    taken = [int(m[1]) for p in folder.glob(f"{stem}-*.json") if (m := re.fullmatch(rf"{stem}-([0-9]+)\.json", p.name))]
    return folder / f"{stem}-{max(taken, default=0) + 1}.json"


def clicks_path(work, take_dir):
    return work / f"clicks-{re.sub(r'[^A-Za-z0-9._-]', '_', Path(take_dir).name)}.json"


def frame_number(take_dir, frame, work=None):
    """--frame, else the frame this take's clicks were saved for, else the middle of its activity part."""
    if frame is not None:
        return frame
    saved = clicks_path(work, take_dir) if work else None
    if saved and saved.exists():
        n = read_clicks(saved)["frame"].get("n")
        if type(n) is int:
            return n
    take, frames = scene.read_take(take_dir)
    return scene.default_frame(take, frames)


# ---- clicking -------------------------------------------------------------------------

class ClickSession:
    """One frame of one take, the worksheet's points, and the clicks so far.

    Each click is sampled for depth at once (with the method's values, proposed
    ones included) so the owner sees it; the clicks file is saved after every
    change. The fit samples depth again from the bundle, never from this file.
    """

    def __init__(self, work, take_dir, n, sheet, m):
        self.take_dir, self.sheet, self.m = Path(take_dir), sheet, m
        self.take, self.row, data, self.bundle = scene.load_frame(take_dir, n)
        scene.frame_depth_skew(self.bundle, m.get("max_left_depth_skew_s"))
        self.rgb = check.read_bundle(data, with_rgb=True)[1]
        self.path = clicks_path(work, take_dir)
        self.lock, self.finished = threading.Lock(), threading.Event()
        self.ids = {p["id"] for p in sheet["points"]}
        self.clicks = {}
        saved = read_clicks(self.path) if self.path.exists() else None
        if saved and saved.get("frame", {}).get("sha256") == self.row["sha256"]:
            for pid, c in saved.get("clicks", {}).items():
                try:
                    self.clicks[pid] = self.sample(pid, c["u"], c["v"])
                except (Refused, KeyError, TypeError):
                    pass

    def sample(self, pid, u, v):
        if type(pid) is not str or pid not in self.ids:
            raise Refused("clicks_stale", f"{pid!r} is not a point in the worksheet")
        if type(u) not in (int, float) or type(v) not in (int, float):
            raise Refused("click_outside_frame", "u and v are pixel numbers")
        depth = scene.depth_at(self.bundle, u, v, window_px=self.m.get("depth_window_px"),
                               min_valid_fraction=self.m.get("min_depth_valid_fraction"))
        return {"u": float(u), "v": float(v), **depth}

    def state(self):
        with self.lock:
            return {"take": self.take_dir.name, "frame": self.row["n"],
                    "points": [{"id": p["id"], "role": p["role"], "label": p["label"]} for p in self.sheet["points"]],
                    "clicks": dict(self.clicks)}

    def image(self, name):
        return self.bundle["left"] if name == "left" else self.rgb

    def save(self):
        write_json(self.path, {"schema": SCHEMA_CLICKS, "take": self.take_dir.name,
                               "frame": {"n": self.row["n"], "file": self.row["file"], "sha256": self.row["sha256"]},
                               "saved_unix": round(time.time(), 3),
                               "clicks": {pid: {"u": c["u"], "v": c["v"]} for pid, c in sorted(self.clicks.items())}})

    def click(self, body):
        if self.finished.is_set():
            raise serve.PageError("this page is finished")
        try:
            sampled = self.sample(body.get("id"), body.get("u"), body.get("v"))
        except Refused as exc:
            raise serve.PageError(str(exc)) from None
        with self.lock:
            self.clicks[body["id"]] = sampled
            self.save()
            return {"clicks": dict(self.clicks)}

    def clear(self, body):
        if self.finished.is_set():
            raise serve.PageError("this page is finished")
        pid = body.get("id")
        with self.lock:
            if type(pid) is str:
                self.clicks.pop(pid, None)
            self.save()
            return {"clicks": dict(self.clicks)}

    def done(self):
        with self.lock:
            self.save()
        self.finished.set()


def read_clicks(path):
    try:
        clicks = json.loads(scene.utf8_text(Path(path).read_bytes(), path))
    except (OSError, json.JSONDecodeError):
        raise Refused("clicks_stale", f"{Path(path).name} does not read; click again") from None
    if (type(clicks) is not dict or clicks.get("schema") != SCHEMA_CLICKS or type(clicks.get("clicks")) is not dict
            or type(clicks.get("frame")) is not dict):
        raise Refused("clicks_stale", f"{Path(path).name} is not a clicks file")
    for pid, c in clicks["clicks"].items():
        if type(c) is not dict or not all(type(c.get(k)) in (int, float) for k in ("u", "v")):
            raise Refused("clicks_stale", f"{Path(path).name}: the click for {pid} has no pixel u and v")
    return clicks


def run_click(work, take_dir, frame=None, *, m=None, out=sys.stdout, open_browser=True, ready=None):
    work = work_dir(work)
    m = m or method_file.load()
    sheet = worksheet_file.load(work / WORKSHEET, need_positions=False)
    session = ClickSession(work, take_dir, frame_number(take_dir, frame, work), sheet, m)
    if m.open:
        print("Depth while clicking uses proposed values for: " + ", ".join(m.describe_open()), file=out)
    done = serve.serve(session, open_browser=open_browser, ready=ready, out=out)
    print(f"Clicks saved to {session.path} ({len(session.clicks)} of {len(sheet['points'])} points).", file=out)
    print(f"Next: python -B tools\\den-register\\register.py fit {work} --take {Path(take_dir)}", file=out)
    print(f"Or, if this take re-checks a registration: python -B tools\\den-register\\register.py check-mount {work} "
          f"--take {Path(take_dir)}", file=out)
    return 0 if done else 1


# ---- the fit ---------------------------------------------------------------------------

def _cm(metres):
    return f"{metres * 100:.1f} cm"


def evaluate(sheet, clicks, take, row, bundle, intrinsics, m, room_models, calibrations, *, now):
    """The verdict for one fit, and what to write if it passes. Reads and writes no file."""
    reasons = []
    gate = m["gate_m"]

    def refuse(reason, detail):
        reasons.append({"reason": reason, "detail": detail})

    # Inputs: the frame, the clicks, the depth at each click, the scan and the mount.
    try:
        scene.frame_depth_skew(bundle, m["max_left_depth_skew_s"])
    except Refused as exc:
        refuse(exc.reason, exc.detail)
    if clicks.get("frame", {}).get("sha256") != row["sha256"]:
        refuse("clicks_stale", f"the clicks were saved for another frame than frame {row['n']}; click again")
        return {"reasons": reasons}
    by_id = {p["id"]: p for p in sheet["points"]}
    unknown = sorted(set(clicks["clicks"]) - set(by_id))
    if unknown:
        refuse("clicks_stale", f"clicked ids {unknown} are not in the worksheet")
    pairs, not_clicked = [], []
    for point in sheet["points"]:
        c = clicks["clicks"].get(point["id"])
        if c is None:
            not_clicked.append(point["id"])
            continue
        try:
            depth = scene.depth_at(bundle, c.get("u"), c.get("v"), window_px=m["depth_window_px"],
                                   min_valid_fraction=m["min_depth_valid_fraction"])
        except Refused as exc:
            refuse(exc.reason, f"{point['id']}: {exc.detail}")
            continue
        camera = scene.back_project(bundle["intrinsics"], c["u"], c["v"], depth["z_m"])
        pairs.append({"id": point["id"], "role": point["role"], "u": float(c["u"]), "v": float(c["v"]),
                      "depth_m": depth["z_m"], "depth_pixels": depth["valid"], "camera_xyz_m": camera,
                      "room_xyz_m": point["room_xyz_m"]})
        if point["scan_xyz_m"] is not None:
            apart = math.dist(point["scan_xyz_m"], point["room_xyz_m"])
            pairs[-1]["tape_vs_scan_m"] = apart
            if apart > gate:
                refuse("scan_disagrees", f"{point['id']}: tape and scan positions are {_cm(apart)} apart "
                                         f"(gate {_cm(gate)}); check the references")
    for revision in room_models:
        for entry in revision.get("cameras") or []:
            gone = entry.get("invalidated") if entry.get("camera_id") == sheet["camera_id"] else None
            if gone and not (scene._finite(gone.get("take_ended_unix")) and take["ended_unix"] > gone["take_ended_unix"]):
                refuse("mount_changed", f"this take was recorded before the mount check on take {gone.get('take')} "
                                        f"found {sheet['camera_id']} moved; record a new take")
                break
    verdict = {"reasons": reasons, "pairs": pairs, "not_clicked": not_clicked}
    if reasons:
        return verdict

    # Counts and shape.
    fit_pairs = [p for p in pairs if p["role"] == "fit"]
    check_pairs = [p for p in pairs if p["role"] == "check"]
    if len(fit_pairs) < m["min_fit_pairs"]:
        refuse("too_few_pairs", f"{len(fit_pairs)} clicked fit points; at least {m['min_fit_pairs']:g} are needed")
    if len(check_pairs) < m["min_checks"]:
        refuse("too_few_checks", f"{len(check_pairs)} clicked check points; at least {m['min_checks']:g} are needed")
    if reasons:
        return verdict
    spread = rigid.spread([p["room_xyz_m"] for p in fit_pairs])
    verdict["spread_m"] = spread
    if spread < m["min_spread_m"]:
        refuse("collinear", f"the fit points spread {_cm(spread)} off their best line; at least "
                            f"{_cm(m['min_spread_m'])} is needed. Add a fit point away from that line")
        return verdict

    # The fit and the gate.
    result = rigid.fit([p["camera_xyz_m"] for p in fit_pairs], [p["room_xyz_m"] for p in fit_pairs])
    rotation, translation = result["rotation"], result["translation"]
    for p, residual in zip(fit_pairs, result["residuals_m"]):
        p["residual_m"] = residual
        if residual > gate:
            refuse("fit_residual_over_gate", f"{p['id']}: residual {_cm(residual)} (gate {_cm(gate)})")
    for p, loo in zip(fit_pairs, result["loo_m"] or []):
        p["loo_m"] = loo
        if loo > gate:
            refuse("leave_one_out_over_gate", f"{p['id']}: the other fit points put it {_cm(loo)} off (gate "
                                              f"{_cm(gate)}); rung 2 refuses a registration whose bound is over the gate")
    for p in check_pairs:
        p["predicted_room_xyz_m"] = rigid.apply(rotation, translation, p["camera_xyz_m"])
        p["error_m"] = math.dist(p["predicted_room_xyz_m"], p["room_xyz_m"])
        if p["error_m"] > gate:
            refuse("check_over_gate", f"{p['id']}: {_cm(p['error_m'])} from its measured position "
                                      f"(gate {_cm(gate)})")
    if translation[2] <= 0:
        refuse("camera_below_floor", f"the fit puts the camera {_cm(translation[2])} from the floor; "
                                     "check that x, y and z follow the room frame (right-handed, z up)")
    bound = max(result["residuals_m"] + [p["error_m"] for p in check_pairs] + (result["loo_m"] or []))
    verdict.update(fit={"rms_m": result["rms_m"], "max_m": result["max_m"], "loo_max_m": result["loo_max_m"],
                        "error_bound_m": bound, "camera_position_m": translation, "n_pairs": len(fit_pairs),
                        "n_checks": len(check_pairs)},
                   matrix=rigid.matrix4(rotation, translation))
    if reasons:
        return verdict

    # Already registered from exactly these inputs?
    digest = inputs_digest(sheet, clicks, row, m)
    earlier = next((r for r in calibrations if (r.get("inputs") or {}).get("sha256") == digest), None)
    if earlier:
        refuse("already_registered", f"these inputs were registered as {earlier['inputs'].get('transform_id')}")
        return verdict

    verdict["write"] = build_records(sheet, take, row, bundle, intrinsics, m, room_models, verdict, digest, now)
    if verdict["write"] is None:
        refuse("round_trip_failed", "the written transform_id does not resolve back to the fitted transform")
    return verdict


def inputs_digest(sheet, clicks, row, m):
    value = {"worksheet": sheet["sha256"], "frame": row["sha256"], "method": m.sha256,
             "clicks": {k: [v.get("u"), v.get("v")] for k, v in sorted(clicks["clicks"].items())}}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def build_records(sheet, take, row, bundle, intrinsics, m, room_models, verdict, digest, now):
    camera_id, fit = sheet["camera_id"], verdict["fit"]
    base = records.current(room_models, sheet["room_id"])
    previous = records.camera_entry(base, camera_id)
    date = time.strftime("%Y-%m-%d", time.localtime(take.get("ended_unix") or now))
    version = records.next_version(room_models, camera_id, date)
    tid = records.transform_id(camera_id, version)
    calibration_id = f"cal-{camera_id}-pose-{version}"
    k = bundle["intrinsics"]
    signature = scene.mount_signature(bundle)
    entry = {
        "camera_id": camera_id,
        "intrinsics": intrinsics,
        "pose": {"room_from_camera": verdict["matrix"], "camera_frame": records.CAMERA_FRAME,
                 "camera_axes": records.CAMERA_AXES, "room_frame": records.FRAME["name"], "unit": "m",
                 "position_m": records.rounded(fit["camera_position_m"])},
        "method": METHOD,
        "version": version,
        "error_bound_m": records.rounded(fit["error_bound_m"]),
        "photometric": (previous or {}).get("photometric") or dict(records.PHOTOMETRIC_UNCALIBRATED),
        "calibration_id": calibration_id,
        "mount": {"take": take.get("take"), "take_ended_unix": take.get("ended_unix"), "frame": row["n"],
                  "signature": signature, "recheck": RECHECK,
                  "marks": [{"id": p["id"], "role": p["role"], "camera_xyz_m": records.rounded(p["camera_xyz_m"])}
                            for p in verdict["pairs"]]},
    }
    revision = records.with_camera(base, entry, {"method": METHOD, "ts": round(now, 3),
                                                 "confidence": None})
    # Round trip: resolve the transform_id from the serialised file and carry a fit point through it.
    reread = [json.loads(records.line(r)) for r in room_models + [revision]]
    first = next(p for p in verdict["pairs"] if p["role"] == "fit")
    try:
        resolved = records.resolve(reread, tid)
        back = records.to_room(resolved["entry"], first["camera_xyz_m"])
    except Refused:
        return None
    fitted = rigid.apply(*rigid.split4(verdict["matrix"]), first["camera_xyz_m"])
    if resolved["invalidated"] or math.dist(back, fitted) > 1e-9:
        return None
    gate = m["gate_m"]
    record = {
        "parameter": f"RoomModel.cameras[{camera_id}].pose",
        "value": verdict["matrix"],
        "unit": "m",
        "unit_note": f"4x4 rigid room_from_camera, from {records.CAMERA_FRAME} ({records.CAMERA_AXES}) "
                     f"to {records.FRAME['name']}; "
                     "the rotation is unitless, the translation in metres",
        "method": METHOD,
        "residual_m_rms": records.rounded(fit["rms_m"]),
        "residual_m_max": records.rounded(fit["max_m"]),
        "n_pairs": fit["n_pairs"],
        "loo_m_max": records.rounded(fit["loo_max_m"]),
        "error_bound_m": records.rounded(fit["error_bound_m"]),
        "inputs": {"sha256": digest, "transform_id": tid, "worksheet_sha256": sheet["sha256"],
                   "measured_by": sheet["measured_by"], "take": take.get("take"),
                   "take_ended_unix": take.get("ended_unix"), "frame": row["n"], "frame_file": row["file"],
                   "bundle_sha256": row["sha256"], "method_file": m.file, "method_sha256": m.sha256,
                   "depth_window_px": m["depth_window_px"], "intrinsics": k,
                   "pairs": [records.rounded({key: p[key] for key in p if key != "predicted_room_xyz_m"})
                             for p in verdict["pairs"]]},
        "checks": {
            "held_out": {"tolerance_m": gate, "result": "pass",
                         "points": [{"id": p["id"], "error_m": records.rounded(p["error_m"]), "result": "pass"}
                                    for p in verdict["pairs"] if p["role"] == "check"]},
            "round_trip": {"transform_id": tid, "point": first["id"],
                           "camera_xyz_m": records.rounded(first["camera_xyz_m"]),
                           "room_xyz_m": records.rounded(back), "result": "pass"},
        },
        "ts": round(now, 3),
        "version": calibration_id,
        "supersedes": None,
    }
    if previous:
        pose = previous.get("pose")
        record.update(supersedes=previous.get("calibration_id") or records.transform_id(camera_id, previous.get("version")),
                      supersedes_method=previous.get("method"),
                      supersedes_value=pose.get("room_from_camera", pose) if type(pose) is dict else pose)
    return {"transform_id": tid, "revision": revision, "record": record}


def describe(verdict, out):
    for p in verdict.get("pairs", []):
        if "residual_m" in p:
            loo = f", leave-one-out {_cm(p['loo_m'])}" if "loo_m" in p else ""
            print(f"  fit   {p['id']:<8} residual {_cm(p['residual_m'])}{loo}", file=out)
        elif "error_m" in p:
            print(f"  check {p['id']:<8} {_cm(p['error_m'])} from its measured position", file=out)
    if verdict.get("not_clicked"):
        print("  not clicked: " + ", ".join(verdict["not_clicked"]), file=out)
    fit = verdict.get("fit")
    if fit:
        loo = "n/a with three fit points" if fit["loo_max_m"] is None else _cm(fit["loo_max_m"])
        print(f"  {fit['n_pairs']} fit pairs: rms {_cm(fit['rms_m'])}, max {_cm(fit['max_m'])}, "
              f"leave-one-out max {loo}; error bound {_cm(fit['error_bound_m'])}", file=out)
        x, y, z = fit["camera_position_m"]
        print(f"  camera at x {x:.3f}, y {y:.3f}, z {z:.3f} m in the room frame", file=out)


def run_fit(work, take_dir, frame=None, *, m=None, now=time.time, out=sys.stdout):
    """Fit, gate and (on a pass) write. Returns (exit code, verdict). 0 registered, 1 refused."""
    work = work_dir(work)
    m = m or method_file.load()
    verdict = {"reasons": []}
    sheet = take = row = None
    try:
        if m.scored() is None:
            raise Refused("slots_open", "close these in REGISTER-V1.md before a scored fit: "
                          + ", ".join(m.describe_open()))
        sheet = worksheet_file.load(work / WORKSHEET)
        path = clicks_path(work, take_dir)
        if not path.exists():
            raise Refused("clicks_stale", f"no clicks for this take; run the click step first ({path.name})")
        clicks = read_clicks(path)
        n = clicks["frame"].get("n") if frame is None else frame
        take, row, _, bundle = scene.load_frame(take_dir, n)
        intrinsics = rig.entry_intrinsics(bundle, rig.read_calibration(work / CAMERA_CALIBRATION))
        room_models = records.read_jsonl(work / ROOM_MODEL, "room_model_unreadable")
        calibrations = records.read_jsonl(work / CALIBRATIONS, "calibration_records_unreadable")
        records.current(room_models, sheet["room_id"])
        verdict = evaluate(sheet, clicks, take, row, bundle, intrinsics, m, room_models, calibrations, now=now())
    except scene.NotUtf8:
        raise  # exit 2 with nothing saved: this is no verdict on the fit
    except Refused as exc:
        verdict["reasons"] = [{"reason": exc.reason, "detail": exc.detail}]
    write = verdict.pop("write", None)
    report = {"schema": SCHEMA_FIT, "result": "refused" if verdict["reasons"] or not write else "registered",
              "transform_id": write["transform_id"] if write else None, "ts": round(now(), 3),
              "method": {"file": m.file, "sha256": m.sha256, "open": sorted(m.open)},
              "take": Path(take_dir).name, "frame": row["n"] if row else None,
              "worksheet_sha256": sheet["sha256"] if sheet else None,
              **records.rounded(verdict)}
    if write and not verdict["reasons"]:
        records.append_jsonl(work / ROOM_MODEL, write["revision"])
        records.append_jsonl(work / CALIBRATIONS, write["record"])
    path = next_numbered(work, "fit")
    write_json(path, report)
    if report["result"] == "registered":
        print(f"REGISTERED {report['transform_id']}", file=out)
        spread = rigid.spread([p["camera_xyz_m"] for p in verdict["pairs"]])
        if spread < m["mount_min_spread_m"]:
            print(f"  But the mount check could not confirm it: these marks spread {_cm(spread)} off their best line, "
                  f"and it needs {_cm(m['mount_min_spread_m'])}. Add a cross away from that line and fit again.", file=out)
        reach = marks_reach([p["camera_xyz_m"] for p in verdict["pairs"]])
        if reach < m["mount_min_reach_m"]:
            print(f"  But the mount check could not confirm it: the farthest of these marks is {reach:.2f} m from the "
                  f"camera, and it needs {m['mount_min_reach_m']:g} m, because a turn shows most far away. Click or add "
                  "a cross further off and fit again.", file=out)
    else:
        print("REFUSED", file=out)
        for r in report["reasons"]:
            print(f"  {r['reason']}: {r['detail']}", file=out)
    describe(verdict, out)
    print(f"Wrote {path.name}" + (f", {ROOM_MODEL} and {CALIBRATIONS}" if report["result"] == "registered" else ""),
          file=out)
    return (0 if report["result"] == "registered" else 1), report


# ---- the mount check -----------------------------------------------------------------------

def measure_marks(work, take_dir, row, bundle, sheet, entry, m):
    """Each mark clicked on this frame, carried through the registered transform, against its tape position.

    None when this take has no clicks. Clicks for another frame, for ids the
    worksheet does not hold, or with too little depth under them are refused.
    """
    path = clicks_path(work, take_dir)
    if not path.exists():
        return None
    clicks = read_clicks(path)
    if clicks["frame"].get("sha256") != row["sha256"]:
        raise Refused("clicks_stale", f"the clicks were saved for another frame than frame {row['n']}; click again")
    by_id = {p["id"]: p for p in sheet["points"]}
    unknown = sorted(set(clicks["clicks"]) - set(by_id))
    if unknown:
        raise Refused("clicks_stale", f"clicked ids {unknown} are not in the worksheet")
    scene.frame_depth_skew(bundle, m["max_left_depth_skew_s"])
    marks = []
    for point in sheet["points"]:
        c = clicks["clicks"].get(point["id"])
        if c is None:
            continue
        try:
            depth = scene.depth_at(bundle, c["u"], c["v"], window_px=m["depth_window_px"],
                                   min_valid_fraction=m["min_depth_valid_fraction"])
        except Refused as exc:
            raise Refused(exc.reason, f"{point['id']}: {exc.detail}") from None
        camera = scene.back_project(bundle["intrinsics"], c["u"], c["v"], depth["z_m"])
        marks.append({"id": point["id"], "role": point["role"], "camera_xyz_m": records.rounded(camera),
                      "error_m": records.rounded(math.dist(records.to_room(entry, camera), point["room_xyz_m"]))})
    return marks


def mount_record(entry, tid, m):
    """The entry's mount record, checked, or transform_unknown: an entry without one cannot be mount-checked."""
    def section(holder, name):
        return holder.get(name) if type(holder.get(name)) is dict else {}

    mount, marks = section(entry, "mount"), section(entry, "mount").get("marks")
    signature, k = section(mount, "signature"), section(section(entry, "intrinsics"), "rectified_left")
    cells = signature.get("cells")
    good = (scene._finite(mount.get("take_ended_unix")) and type(marks) is list
            and len(marks) >= m["min_fit_pairs"] + m["min_checks"]
            and all(type(mark) is dict and type(mark.get("id")) is str and type(mark.get("camera_xyz_m")) is list
                    and len(mark["camera_xyz_m"]) == 3 and all(scene._finite(v) for v in mark["camera_xyz_m"])
                    for mark in marks)
            and all(type(signature.get(name)) is list and len(signature[name]) == 2
                    and all(type(v) is int and v > 0 for v in signature[name]) for name in ("grid", "shape"))
            and type(cells) is list and all(type(row) is list and all(v is None or scene._finite(v) for v in row)
                                            for row in cells)
            and all(scene._finite(k.get(name)) for name in ("fx", "fy", "cx", "cy")))
    if not good:
        raise Refused("transform_unknown", f"{tid} has no usable mount record: register again")
    return mount


def marks_motion(mount, marks):
    """How every mark of the registration moved since then, in the camera frame; None unless every one is clicked.

    The rigid motion they show together: moved_m, the farthest it carries one of them (farthest), and off_m, the most
    one of them is off it (worst).
    """
    clicked = {mark["id"]: mark for mark in marks}
    if any(mark["id"] not in clicked for mark in mount["marks"]):
        return None
    before = [mark["camera_xyz_m"] for mark in mount["marks"]]
    after = [clicked[mark["id"]]["camera_xyz_m"] for mark in mount["marks"]]
    rotation, translation = rigid.kabsch(before, after)
    carried = [rigid.apply(rotation, translation, point) for point in before]
    moved = [math.dist(point, start) for point, start in zip(carried, before)]
    off = [math.dist(point, seen) for point, seen in zip(carried, after)]
    farthest, worst = moved.index(max(moved)), off.index(max(off))
    return {"moved_m": records.rounded(moved[farthest]), "farthest": mount["marks"][farthest]["id"],
            "off_m": records.rounded(off[worst]), "worst": mount["marks"][worst]["id"]}


def marks_reach(points):
    """How far the farthest of these camera-frame points is from the camera, in metres."""
    return max(math.hypot(*point) for point in points)


def camera_moved(motion, m):
    """Do the marks on one take look moved together past the gate, by more than they disagree?

    That alone only says they look moved. A rigid motion can absorb one far mark, so one slipped click can pass this
    with every other mark exact. An invalidation cannot be undone, so it takes two separately clicked takes that agree
    (agreeing_take). One take confirms nothing, invalidates nothing and asks for a new take; a clean re-click clears it
    (REGISTER-V1.md, "A moved camera").
    """
    return motion["moved_m"] - motion["off_m"] > m["gate_m"]


def agreeing_take(work, tid, mount, take, marks, m):
    """The report of the earlier take these marks agree with, or None.

    It is the newest mount-N.json of this registration with every mark clicked, so a take that does not show the
    movement clears it and a check with marks unclicked does not. It must have looked moved on an earlier take: the
    same take clicked again is not a second look. And the movement from it to this take must carry no mark past the
    gate. A report that does not read is no take, which only asks for one more.
    """
    numbered = [(int(found[1]), path) for path in work.glob("mount-*.json")
                if (found := re.fullmatch(r"mount-([0-9]+)\.json", path.name))]
    for _, path in sorted(numbered, reverse=True):
        try:
            report = json.loads(scene.utf8_text(path.read_bytes(), path))
        except (OSError, json.JSONDecodeError):
            continue
        if type(report) is dict and report.get("transform_id") == tid and report.get("marks_motion"):
            break
    else:
        return None
    if (not report.get("looks_moved") or not scene._finite(report.get("take_ended_unix"))
            or take["ended_unix"] <= report["take_ended_unix"]):
        return None
    ids = [mark["id"] for mark in mount["marks"]]
    now_at = {mark["id"]: mark["camera_xyz_m"] for mark in marks}
    try:
        then_at = {mark["id"]: mark["camera_xyz_m"] for mark in report["marks"]}
        start = [then_at[pid] for pid in ids]
        apart = rigid.largest_move(start, [now_at[pid] for pid in ids], start)
    except (KeyError, TypeError, ValueError):
        return None
    return report if apart <= m["gate_m"] else None


def why_not_confirmed(entry, mount, marks, m, later=None):
    """(why these marks do not confirm the registration, or None; what to do; what to record).

    REGISTER-V1.md, "A moved camera". What to do is None when it is to click the marks. later is the later frame's depth
    signature: what the view holds is read from both frames.
    """
    gate = m["gate_m"]
    clicked = {mark["id"]: mark for mark in marks}
    not_clicked = [mark["id"] for mark in mount["marks"] if mark["id"] not in clicked]
    facts = {"not_clicked": not_clicked}
    if not_clicked:
        return (f"every mark of the registration counts, and these are not clicked on this take: "
                f"{', '.join(not_clicked)}"), None, facts
    before = {mark["id"]: mark["camera_xyz_m"] for mark in mount["marks"]}
    start, after = list(before.values()), [clicked[pid]["camera_xyz_m"] for pid in before]
    view = scene.deeper_cells(mount["signature"], later) if later else mount["signature"]
    movement = rigid.largest_move(start, after, start + scene.view_points(
        view, entry["intrinsics"]["rectified_left"], m["mount_view_reach_m"]))
    facts["view_movement_m"] = records.rounded(movement)
    motion = marks_motion(mount, marks)
    if motion["off_m"] > gate:
        facts["click_again"] = [motion["worst"]]
        return (f"the marks did not move together: {motion['worst']} is {_cm(motion['off_m'])} from where the movement "
                f"they show together puts it (gate {_cm(gate)}), so its click is off or it moved on its own"), (
                    f"Click {motion['worst']} again on this take, then run this again. If it says the same and what "
                    "carries it moved, measure it again and register again."), facts
    over = [mark for mark in marks if mark["error_m"] > gate]
    if over:
        worst = max(over, key=lambda mark: mark["error_m"])
        since = ""
        if worst["id"] in before:
            apart = math.dist(before[worst["id"]], clicked[worst["id"]]["camera_xyz_m"])
            since = f", though {worst['id']} moved only {_cm(apart)} in the camera's view since the registration"
        facts["click_again"] = [mark["id"] for mark in over]
        return (", ".join(f"{mark['id']} is {_cm(mark['error_m'])}" for mark in over)
                + f" from its measured position (gate {_cm(gate)}){since}, so this alone does not show the camera "
                  "moved"), ("Click it again on this take, then run this again. If it says the same, measure it "
                             "again and register again."), facts
    if sum(v is not None for row in mount["signature"]["cells"] for v in row) < m["mount_min_cells"]:
        return (f"the registration frame has too little depth to say what the view holds (under "
                f"{m['mount_min_cells']:g} of its cells)"), "Register again from a frame with more depth in view.", facts
    if movement > gate:
        return (f"each mark is within {_cm(gate)}, but the movement they show together would put something in view "
                f"{_cm(movement)} off"), ("Record a new take and click every mark again. If it says the same, treat "
                                          "the camera as moved and register again."), facts
    spread = rigid.spread(start)
    facts["marks_spread_m"] = records.rounded(spread)
    if spread < m["mount_min_spread_m"]:
        return (f"the marks are too close to one line to show a turn about it (they spread {_cm(spread)} off it, and "
                f"{_cm(m['mount_min_spread_m'])} is needed)"), "Register again with a cross away from that line.", facts
    reach = marks_reach(start)
    facts["marks_reach_m"] = records.rounded(reach)
    if reach < m["mount_min_reach_m"]:
        return (f"the marks are too close to the camera to show a turn (the farthest is {reach:.2f} m from it, and "
                f"{m['mount_min_reach_m']:g} m is needed)"), "Register again with a cross further from the camera.", facts
    return None, None, facts


def run_check_mount(work, take_dir, frame=None, *, camera_id=None, m=None, now=time.time, out=sys.stdout):
    """Has the camera moved since the registration? 0 confirmed, 1 otherwise (REGISTER-V1.md, "A moved camera").

    A large depth change invalidates at once. Every mark moving together past the
    gate invalidates only when two separately clicked takes agree on it: the first
    says the marks look moved, writes nothing to room-model.jsonl and asks for a new
    take. Only every mark of the registration re-clicked on this later take can
    confirm: each within the gate, and the movement they show together within it
    across the whole view. A confirmation goes into room-model.jsonl, where a scored
    rung 2 replay looks.
    """
    work = work_dir(work)
    m = m or method_file.load()
    report = {"schema": SCHEMA_MOUNT, "ts": round(now(), 3), "take": Path(take_dir).name,
              "method": {"file": m.file, "sha256": m.sha256}}
    why_not = next_step = None
    try:
        if m.scored() is None:
            raise Refused("slots_open", "close these in REGISTER-V1.md before a mount check: "
                          + ", ".join(m.describe_open()))
        room_models = records.read_jsonl(work / ROOM_MODEL, "room_model_unreadable")
        if not room_models:
            raise Refused("transform_unknown", f"{ROOM_MODEL} holds no registration yet")
        latest = room_models[-1]
        sheet = worksheet_file.load(work / WORKSHEET)
        camera_id = camera_id or sheet["camera_id"]
        entry = records.camera_entry(latest, camera_id)
        if entry is None:
            raise Refused("transform_unknown", f"the current RoomModel has no registered entry for {camera_id}")
        tid = records.transform_id(camera_id, entry.get("version"))
        report["transform_id"] = tid
        mount = mount_record(entry, tid, m)
        if records.invalidation(room_models, camera_id, entry.get("version")):
            raise Refused("mount_changed", f"{tid} was invalidated already; register again")
        take, row, _, bundle = scene.load_frame(take_dir, frame_number(take_dir, frame, work))
        report.update(frame=row["n"], take_ended_unix=take["ended_unix"])
        if take["ended_unix"] > now():
            raise Refused("mount_take_in_future", f"{Path(take_dir).name} ended later than this PC's clock says it is "
                                                  "now, so it could vouch for takes not recorded yet. Check the PC's "
                                                  "clock, then record a new take")
        if take["ended_unix"] <= mount["take_ended_unix"]:
            raise Refused("mount_take_not_later", f"{Path(take_dir).name} was not recorded after the take {tid} was "
                                                  "registered from, so it says nothing about a later move")
        later = scene.mount_signature(bundle)
        result = scene.check_mount(mount["signature"], later,
                                   tolerance_mm=m["mount_tolerance_mm"],
                                   tolerance_fraction=m["mount_tolerance_fraction"],
                                   changed_fraction=m["mount_changed_fraction"],
                                   minimum_cells=m["mount_min_cells"])
        report["check"] = result
        marks = None if result["status"] == "changed" else measure_marks(work, take_dir, row, bundle, sheet, entry, m)
        motion = marks_motion(mount, marks) if marks else None
        if motion:
            report["marks_motion"] = motion
        moved = motion is not None and camera_moved(motion, m)
        first = agreeing_take(work, tid, mount, take, marks, m) if moved else None
        if result["status"] == "changed" or first:
            report["result"] = "changed"
            invalidated = {"ts": round(now(), 3), "reason": "mount_changed", "take": take.get("take"),
                           "take_ended_unix": take.get("ended_unix"), "frame": row["n"], "check": result,
                           "recheck": RECHECK}
            if first:
                report["first_take"] = first.get("take")
                invalidated.update(marks=marks, marks_motion=motion)
            records.append_jsonl(work / ROOM_MODEL, records.invalidated_revision(latest, camera_id, invalidated))
            report["recheck"] = RECHECK
        elif marks is None:
            report["result"] = "not_confirmed"
            why_not = "no marks are clicked on this take yet"
        elif moved:
            report.update(result="not_confirmed", looks_moved=True)
            why_not = (f"the marks look moved together, {motion['farthest']} by {_cm(motion['moved_m'])} in the camera's "
                       f"view since the registration (gate {_cm(m['gate_m'])}), but one take's clicks never invalidate")
            next_step = ("Record a new take and click every mark on it, then run this again. If it shows the same "
                         "movement, the registration is invalidated. If it does not, this is cleared. If you know a "
                         "click slipped, clicking it again on this take clears it too. If what carries a cross moved, "
                         "the next take will agree with this one, so measure that cross again and register again.")
        else:
            why_not, next_step, facts = why_not_confirmed(entry, mount, marks, m, later)
            report.update(facts, result="not_confirmed" if why_not else "confirmed")
            earlier = (entry.get("confirmed") or {}).get("take_ended_unix")
            if not why_not and not (scene._finite(earlier) and earlier >= take["ended_unix"]):
                records.append_jsonl(work / ROOM_MODEL, records.confirmed_revision(latest, camera_id, {
                    "ts": round(now(), 3), "take": take.get("take"), "take_ended_unix": take["ended_unix"],
                    "frame": row["n"], "worst_mark_m": max(mark["error_m"] for mark in marks),
                    "view_movement_m": facts["view_movement_m"]}))
        if marks is not None:
            report["marks"] = marks
    except scene.NotUtf8:
        raise  # exit 2 with nothing saved: this is no verdict on the mount
    except Refused as exc:
        report.update(result="refused", reason=exc.reason, detail=exc.detail)
    path = next_numbered(work, "mount")
    write_json(path, report)
    status = report["result"]
    if status == "confirmed":
        worst = max(report["marks"], key=lambda mark: mark["error_m"])
        print(f"MOUNT CONFIRMED: {report['transform_id']} still holds: all {len(report['marks'])} marks are "
              f"within {_cm(m['gate_m'])} of their measured positions (worst {worst['id']}, "
              f"{_cm(worst['error_m'])}), and the movement they show is at most "
              f"{_cm(report['view_movement_m'])} in view.", file=out)
    elif status == "changed":
        if report["check"]["status"] == "changed":
            detail = f"{report['check']['changed_cells']} of {report['check']['compared_cells']} depth cells moved"
        else:
            motion = report["marks_motion"]
            detail = (f"two takes, {report['first_take']} and {report['take']}, agree that the marks moved together, "
                      f"{motion['farthest']} by {_cm(motion['moved_m'])} in the camera's view since the registration "
                      f"(gate {_cm(m['gate_m'])})")
        print(f"MOUNT CHANGED: {report['transform_id']} is invalidated: {detail}. Re-check:", file=out)
        for item in RECHECK:
            print(f"  - {item}", file=out)
    elif status == "not_confirmed":
        print(f"MOUNT NOT CONFIRMED: {report['transform_id']} is neither confirmed nor invalidated: {why_not}. "
              f"Depth alone cannot see a small turn or slide ({report['check']['changed_cells']} of "
              f"{report['check']['compared_cells']} cells moved). A scored replay of a take after the last "
              "confirmation stays refused.", file=out)
        print(next_step or "Click every mark on this take, then run this again:", file=out)
        if not next_step or report.get("click_again"):
            print(f"  python -B tools\\den-register\\register.py click {work} --take {Path(take_dir)}", file=out)
    else:
        print(f"REFUSED {report['reason']}: {report['detail']}", file=out)
    print(f"Wrote {path.name}", file=out)
    return (0 if status == "confirmed" else 1), report


# ---- small commands ----------------------------------------------------------------------------

def run_worksheet(work, *, out=sys.stdout):
    work = work_dir(work, create=True)
    path = work / WORKSHEET
    if path.exists():
        raise Refused("worksheet_exists", f"{path} exists already; edit it instead")
    write_json(path, worksheet_file.template())
    print(f"Wrote {path}. Fill in each point's room_xyz_m in metres, then click.", file=out)
    return 0


def run_resolve(work, value, *, out=sys.stdout):
    work = work_dir(work)
    found = records.resolve(records.read_jsonl(work / ROOM_MODEL, "room_model_unreadable"), value)
    entry = found["entry"]
    x, y, z = entry["pose"]["position_m"]
    print(f"{value}: {entry['method']}, error bound {_cm(entry['error_bound_m'])}, "
          f"camera at x {x:.3f}, y {y:.3f}, z {z:.3f} m", file=out)
    confirmed = found["confirmed"]
    print(f"Mount: confirmed by take {confirmed['take']}" if confirmed
          else "Mount: not confirmed by any later take yet", file=out)
    if found["invalidated"]:
        print(f"INVALIDATED: {found['invalidated']['reason']} (take {found['invalidated']['take']}); "
              "room positions from this transform are null", file=out)
        return 1
    return 0


def main(argv=None, out=sys.stdout):
    parser = argparse.ArgumentParser(description="Register the den camera to the room frame (rung 1).")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("worksheet", help="write a worksheet to fill in")
    p.add_argument("work")
    for name, text in (("click", "click each point on one frame of a take"), ("fit", "fit, gate and write"),
                       ("check-mount", "compare a later take with the registration")):
        p = sub.add_parser(name, help=text)
        p.add_argument("work")
        p.add_argument("--take", required=True)
        p.add_argument("--frame", type=int)
        if name == "click":
            p.add_argument("--no-browser", action="store_true")
        if name == "check-mount":
            p.add_argument("--camera")
    p = sub.add_parser("resolve", help="resolve a transform_id")
    p.add_argument("work")
    p.add_argument("transform_id")
    args = parser.parse_args(argv)
    try:
        if args.command == "worksheet":
            return run_worksheet(args.work, out=out)
        if args.command == "click":
            return run_click(args.work, args.take, args.frame, out=out, open_browser=not args.no_browser)
        if args.command == "fit":
            return run_fit(args.work, args.take, args.frame, out=out)[0]
        if args.command == "check-mount":
            return run_check_mount(args.work, args.take, args.frame, camera_id=args.camera, out=out)[0]
        return run_resolve(args.work, args.transform_id, out=out)
    except Refused as exc:
        print(f"REFUSED {exc}", file=out)
        return 2


if __name__ == "__main__":
    sys.exit(main())
