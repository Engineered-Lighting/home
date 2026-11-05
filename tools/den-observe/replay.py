#!/usr/bin/env python3
"""Replay an R take through den-observe and write rung 2's Observations. Standard library only.

    python -B tools/den-observe/replay.py --demo
    python -B tools/den-observe/replay.py --take <take folder> --room-model <file> --fake --out <folder>
    python -B tools/den-observe/replay.py --take <take folder> --room-model <file> --detections <rows.jsonl> --out <folder> [--scored]

It reads the take's bundles and a RoomModel, never a camera or a network.
--out gets observations.jsonl (one Observation per line) and run.json (what
was used, the open slots, and the gate's counts). An unscored run uses the
proposed value of every open slot and says so; --scored refuses while any
slot is open. Exit codes: 0 written, 1 scored and the gate failed, 2 refused
(nothing written).
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "den-take"))

import detections  # noqa: E402
import fake_detector  # noqa: E402
import method as method_file  # noqa: E402
import observe  # noqa: E402
import roommodel  # noqa: E402
import take as den_take  # noqa: E402  (tools/den-take/take.py, unchanged: checkout and OneDrive guards)
import takes  # noqa: E402

RUN_SCHEMA = "den-observe-run-1"
R5_BASELINE = {"expired": 115, "results": 228, "fraction": round(115 / 228, 3),
               "capture_to_completion_s": 1.46, "source": "notes.json R5, the 2026-10-22 window"}


class Refused(Exception):
    """The run cannot start or finish; nothing is written."""


def out_dir(path, *, guard=True):
    path = Path(path)
    if not path.is_absolute():
        raise Refused("--out must be an absolute folder, for example C:\\den-takes\\observe-1")
    path = path.resolve()
    if guard and den_take.inside_checkout(path):
        raise Refused("--out is inside a git checkout; observations of real takes never go into git")
    if guard and den_take.in_onedrive(path):
        raise Refused("--out is inside OneDrive; observations stay on this PC only")
    if (path / "observations.jsonl").exists() or (path / "run.json").exists():
        raise Refused("--out already holds observations; use a new folder")
    return path


def match_rows(rows, take):
    """[(row, take index)]: by rgb_seq when the row has one, else by capture time."""
    by_seq = {r["rgb_seq"]: i for i, r in enumerate(take.rows)}
    by_time = {r["t_host"]: i for i, r in enumerate(take.rows)}
    out = []
    for number, row in enumerate(rows, 1):
        frame = row["frame"]
        if frame["clock"] != "host_monotonic":
            raise Refused(f"row {number}: a take replay needs host_monotonic rows, not {frame['clock']}")
        index = by_seq.get(frame["rgb_seq"]) if "rgb_seq" in frame else by_time.get(frame["capture_ts"])
        if index is None:
            raise Refused(f"row {number}: its frame is not in the take")
        if take.rows[index]["t_host"] != frame["capture_ts"]:
            raise Refused(f"row {number}: capture_ts differs from the take's RGB time for that frame")
        out.append((row, index))
    return out


def gate(records, camera, settings, method, *, scored):
    """Rung 2's gate over the written records (counts, problems, pass)."""
    resolves = lambda tid: tid == camera.transform_id  # noqa: E731  (resolved once, at the start)
    counts = {"kind": {}, "validity": {}, "depth": {}, "freshness": {}, "joints": {}}
    problems, bad = [], 0
    for obs in records:
        found = observe.check_record(obs, resolves)
        if found:
            bad += 1
            problems.extend(f"{obs.get('obs_id')}: {p}" for p in found[:3])
        for key, value in (("kind", obs["kind"]), ("validity", obs["validity"]),
                           ("depth", obs["freshness"]["depth"]), ("freshness", obs["freshness"]["status"])):
            counts[key][value] = counts[key].get(value, 0) + 1
        for entry in obs.get("joints_room") or []:
            label = f"{entry['status']}/{entry['lift'].split(':')[0]}"
            counts["joints"][label] = counts["joints"].get(label, 0) + 1
    judged = counts["freshness"].get("fresh", 0) + counts["freshness"].get("expired", 0)
    expired = counts["freshness"].get("expired", 0)
    fraction = expired / judged if judged else None
    limit = method.get("max_expired_fraction")
    freshness = {"judged": judged, "expired": expired, "expired_fraction": fraction,
                 "unknown": counts["freshness"].get("unknown", 0), "max_age_s": settings.max_age_s,
                 "max_expired_fraction": limit, "baseline": R5_BASELINE,
                 "pass": fraction is not None and fraction <= limit and not counts["freshness"].get("unknown")}
    return {"scored": scored, "records": len(records), "records_with_problems": bad, "problems": problems[:20],
            "counts": counts, "freshness": freshness, "pass": bad == 0 and freshness["pass"]}


def replay(take_folder, room_model_path, out, *, detections_path=None, fake=False, camera_id="den-oak",
           transform_id=None, scored=False, method_path=None, guard=True, log=print):
    """Run one replay; returns the run record. Raises Refused before writing anything."""
    try:
        method = method_file.load(method_path)
    except method_file.MethodError as exc:
        raise Refused(str(exc)) from None
    if scored and method.scored() is None:
        raise Refused("a scored run needs every slot closed; open: " + ", ".join(method.describe_open()))
    if scored and fake:
        raise Refused("a scored run needs real detector rows, not --fake")
    if (detections_path is None) == (not fake):
        raise Refused("give exactly one of --detections and --fake")
    try:
        take = takes.Take(take_folder)
        clock = take.clock()
    except takes.TakeRefused as exc:
        raise Refused(str(exc)) from None
    if scored and clock["source"] != "slate":
        raise Refused("a scored run needs the take's newest check to PASS with a read start slate")
    try:
        room_model = roommodel.load(room_model_path)
        transform_id = transform_id or roommodel.current_transform_id(room_model, camera_id)
        camera = roommodel.Camera(room_model, transform_id, max_error_bound_m=method.get("max_error_bound_m"))
    except roommodel.RegistrationRefused as exc:
        raise Refused(str(exc)) from None
    if fake:
        rows = [fake_detector.row(r["t_host"], rgb_seq=r["rgb_seq"]) for r in take.rows]
        detector = fake_detector.NAME
    else:
        try:
            rows = detections.read_rows(detections_path)
        except (OSError, detections.RowError) as exc:
            raise Refused(f"detector rows: {exc}") from None
        detector = Path(detections_path).name
    if scored and any(row.get("available_ts") is None for row in rows):
        raise Refused("a scored run needs available_ts on every detector row")
    matched = match_rows(rows, take)
    target = out_dir(out, guard=guard)
    settings = observe.Settings(method)
    target.mkdir(parents=True, exist_ok=True)
    partial = target / "observations.jsonl.partial"
    records = []
    try:
        with open(partial, "x", encoding="utf-8", newline="\n") as sink:
            for row, index in matched:
                try:
                    meta, depth = take.frame(index)
                except takes.BundleError as exc:
                    raise Refused(f"{take.rows[index]['file']}: {exc}") from None
                try:
                    camera.check_frame(meta["intrinsics"])
                    found = observe.observe(row, meta, depth, camera, settings, offset_s=clock["offset_s"])
                except roommodel.RegistrationRefused as exc:
                    raise Refused(str(exc)) from None
                except ValueError as exc:
                    raise Refused(f"{take.rows[index]['file']}: {exc}") from None
                for obs in found:
                    sink.write(json.dumps(obs, separators=(",", ":"), ensure_ascii=False) + "\n")
                records.extend(found)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    summary = gate(records, camera, settings, method, scored=scored)
    run = {"schema": RUN_SCHEMA, "scored": scored, "take": take.folder.name,
           "take_check": take.check, "clock": clock, "transform_id": camera.transform_id,
           "detector": detector, "rows": len(rows), "method": method.record(), "gate": summary}
    os.replace(partial, target / "observations.jsonl")
    with open(target / "run.json", "x", encoding="utf-8", newline="\n") as sink:
        json.dump(run, sink, indent=1, ensure_ascii=False)
        sink.write("\n")
    report(run, target, log)
    return run


def report(run, target, log):
    summary = run["gate"]
    log(f"wrote {summary['records']} observations from {run['rows']} rows to {target}")
    log(f"transform_id {run['transform_id']}; clock from the {run['clock']['source']}")
    for key in ("kind", "validity", "depth", "joints"):
        log(f"{key}: " + ", ".join(f"{k} {v}" for k, v in sorted(summary["counts"][key].items())))
    fresh = summary["freshness"]
    fraction = "none judged" if fresh["expired_fraction"] is None else f"{fresh['expired_fraction']:.3f}"
    log(f"freshness: {fresh['expired']} of {fresh['judged']} expired ({fraction}) at {fresh['max_age_s']} s; "
        f"R5 baseline {R5_BASELINE['expired']} of {R5_BASELINE['results']}")
    for problem in summary["problems"]:
        log("problem: " + problem)
    open_slots = run["method"]["open_slots"]
    if open_slots:
        log(f"UNSCORED: {len(open_slots)} open slots used their proposed values: " + ", ".join(sorted(open_slots)))
    else:
        log("gate: " + ("PASS" if summary["pass"] else "FAIL") + (" (scored)" if run["scored"] else " (unscored)"))


def demo(out=None, log=print):
    """The whole path on a synthetic take, room and fake detector, in a temporary folder."""
    sys.path.insert(0, str(HERE))
    import synth
    work = Path(tempfile.mkdtemp(prefix="den-observe-demo-"))
    scene = synth.Scene()
    depth = scene.depth_mm()
    # Six frames 0.25 s apart; the fourth carries a depth frame 0.2 s too old.
    frames = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - (0.2 if i == 3 else 0.004)) for i in range(6)]
    folder = synth.make_take(work / "take", frames, depth)
    synth.write_check(folder)
    room = work / "room-model.json"
    room.write_text(json.dumps(synth.room_model(), indent=1, ensure_ascii=False), encoding="utf-8")
    pixels = {name: scene.joint_rgb(name) for name in synth.JOINTS}
    rows = [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels) for i, (t, _) in enumerate(frames)]
    detections.write_rows(work / "detections.jsonl", rows)
    log(f"demo: synthetic take, room model and fake detector rows in {work}")
    return replay(folder, room, out or work / "observe", detections_path=work / "detections.jsonl", log=log)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--demo", action="store_true", help="run on a synthetic take; needs nothing else")
    parser.add_argument("--take", help="the R take folder")
    parser.add_argument("--room-model", help="the RoomModel JSON file")
    parser.add_argument("--camera", default="den-oak", help="camera_id; its newest entry is used")
    parser.add_argument("--transform-id", help="<camera_id>@<version>, refused unless it is the newest")
    parser.add_argument("--detections", help="den-detections-1 rows (JSON Lines)")
    parser.add_argument("--fake", action="store_true", help="use the scripted fake detector instead")
    parser.add_argument("--out", help="an absolute, new folder for observations.jsonl and run.json")
    parser.add_argument("--method", help="a METHOD.md to use instead of the one beside this file")
    parser.add_argument("--scored", action="store_true", help="refuse unless every slot is closed")
    args = parser.parse_args(argv)
    try:
        if args.demo:
            run = demo(args.out)
        else:
            if not (args.take and args.room_model and args.out):
                raise Refused("--take, --room-model and --out are required (or --demo)")
            run = replay(args.take, args.room_model, args.out, detections_path=args.detections, fake=args.fake,
                         camera_id=args.camera, transform_id=args.transform_id, scored=args.scored,
                         method_path=args.method)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 1 if run["scored"] and not run["gate"]["pass"] else 0


if __name__ == "__main__":
    sys.exit(main())
