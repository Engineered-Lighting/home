"""Export den clips from stored Lab previews for offline V-JEPA encoding (AL-T, #327).

Offline operator tool, run on the controller host (the Jetson) by an operator
with owner approval. The ledger is opened read-only and the media files are
only read; nothing in the Lab store changes. The output directory (--out,
created 0700, must be new) holds private images: it never enters the repo and
is moved only to the LA host's private tuning directory, then deleted per the
run proposal.

For every selected session, on an anchor grid of stride_s seconds:
  * frames: 8 stored RGB previews ending at the newest one at or before the
    anchor, one per slot of a step_s grid (rule grid-slot-nearest-1 of
    vjepa_live, generalised to a step; the 3.5s_8f variant is exactly the
    live selection);
  * box: vjepa_live.PersonBoxes.union over the clip frames, causal at the
    anchor exactly as the live pump does (multiple people skip the clip);
  * joints: shoulders, elbows and wrists of the single matched pose per frame,
    for the hand-region crop;
  * label: vjepa_tune_labels.label_clip when --labels is given.
clips.jsonl lists the clips; frames/<media_id>.jpg holds each used preview once.

Usage:
  python vjepa_tune_export.py --storage-root ~/perception-lab/data --out DIR --sessions PREFIX [PREFIX ...]
                              [--labels] [--manifest FILE] [--site victoria] [--timezone America/Vancouver]
                              [--variants 3.5s_8f 7s_8f] [--config vjepa_tune_config.json]
"""
import argparse
from collections import Counter
import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import zoneinfo

import lab_regression as regression
import vjepa_scene as scene

CLIPS_SCHEMA = "vjepa-den-clips-1"
FRAME_COUNT = scene.FRAME_COUNT
JOINTS = {5: "left_shoulder", 6: "right_shoulder", 7: "left_elbow", 8: "right_elbow", 9: "left_wrist", 10: "right_wrist"}


class ReadOnlyStore:
    """The two attributes vjepa_live.PersonBoxes reads, over a ?mode=ro ledger."""

    def __init__(self, db):
        self.db, self.lock = db, threading.Lock()


def session_frames(db, sid, site_id, camera_id):
    """Every verified 640x360 RGB preview of a session, oldest first."""
    frames = []
    for row in db.execute("SELECT id,size,sha256,metadata FROM media WHERE session_id=? AND mime_type='image/jpeg'", (sid,)):
        meta = json.loads(row["metadata"])
        if (meta.get("stream") != "rgb" or meta.get("clock_mapping_status") != "verified"
                or meta.get("site_id") != site_id or meta.get("camera_id") != camera_id
                or (meta.get("width"), meta.get("height")) != scene.IMAGE_SIZE
                or not all(scene.finite(meta.get(k)) for k in ("observed_at", "available_at"))
                or not isinstance(meta.get("capture_epoch"), str) or not 0 < row["size"] <= scene.MAX_IMAGE_BYTES):
            continue
        frames.append({"media_id": row["id"], "frame_id": meta.get("frame_id"), "capture_epoch": meta["capture_epoch"],
                       "observed_at": meta["observed_at"], "available_at": meta["available_at"],
                       "size": row["size"], "sha256": row["sha256"]})
    return sorted(frames, key=lambda f: (f["observed_at"], f["media_id"]))


def select_clip(latest, earlier, step, tolerance, count=FRAME_COUNT):
    """Oldest-first frames ending at latest, one per slot of a step-second grid
    within tolerance, consecutive gaps within [step/2, 1.5 step] and span within
    [(count-1) step -/+ step]; minimum summed grid distance (ties: smaller media
    ids). With step 0.5 this is vjepa_live.ClipFrames._search and its contract."""
    end = latest["observed_at"]
    min_gap, max_gap = step / 2, step * 1.5
    if step == scene.STEP_SECONDS:
        min_gap, max_gap = scene.MIN_GAP, scene.MAX_GAP
    span = step * (count - 1)
    min_span, max_span = (scene.MIN_SPAN, scene.MAX_SPAN) if step == scene.STEP_SECONDS else (span - step, span + step)
    frontier = [(0.0, (latest["media_id"],), [latest])]
    for index in range(1, count):
        target = end - step * index
        reached = []
        for frame in earlier:
            offset = abs(frame["observed_at"] - target)
            if offset > tolerance + 1e-9:
                continue
            paths = [(cost + offset, ids + (frame["media_id"],), path + [frame]) for cost, ids, path in frontier
                     if min_gap <= path[-1]["observed_at"] - frame["observed_at"] <= max_gap]
            if paths:
                reached.append(min(paths, key=lambda p: p[:2]))
        if not reached:
            return None
        frontier = reached
    done = [p for p in frontier if min_span <= end - p[2][-1]["observed_at"] <= max_span]
    return list(reversed(min(done, key=lambda p: p[:2])[2])) if done else None


def anchors(frames, stride, span):
    if not frames:
        return []
    first, last = frames[0]["observed_at"] + span, frames[-1]["observed_at"]
    n = int((last - first) // stride) + 1 if last >= first else 0
    return [first + i * stride for i in range(n)]


def clip_at(frames, anchor, step, tolerance):
    """The clip ending at the newest preview available at the anchor, or None."""
    pool = [f for f in frames if f["observed_at"] <= anchor and f["available_at"] <= anchor]
    if not pool:
        return None
    latest = pool[-1]
    if anchor - latest["observed_at"] > step:
        return None
    earlier = [f for f in pool[:-1] if f["capture_epoch"] == latest["capture_epoch"]
               and f["observed_at"] >= latest["observed_at"] - step * FRAME_COUNT - tolerance]
    return select_clip(latest, earlier, step, tolerance)


def joints_for(db, sid, frames, epoch, match_seconds):
    """Per clip frame, {joint name: [x, y]} of the single supported pose matched to it."""
    times = [f["observed_at"] for f in frames]
    rows = db.execute("""SELECT id,payload FROM events WHERE session_id=? AND kind='perception.output'
        AND json_extract(payload,'$.observed_at') BETWEEN ? AND ? ORDER BY id LIMIT 128""",
                      (sid, min(times) - match_seconds, max(times) + match_seconds)).fetchall()
    outputs = []
    for row in rows:
        value = json.loads(row["payload"])
        result = value.get("result") or {}
        if value.get("status") == "completed" and value.get("capture_epoch") == epoch and scene.finite(value.get("observed_at")):
            outputs.append((value["observed_at"], row["id"], result.get("pose_instances") or []))
    out = []
    for t in times:
        near = [o for o in outputs if abs(o[0] - t) <= match_seconds]
        joints = {}
        if near:
            poses = min(near, key=lambda o: (abs(o[0] - t), o[1]))[2]
            if len(poses) == 1:
                for joint in poses[0].get("joints") or []:
                    if (isinstance(joint, dict) and joint.get("joint_id") in JOINTS and joint.get("status") == "supported"
                            and isinstance(joint.get("pixel"), list)):
                        joints[JOINTS[joint["joint_id"]]] = [round(float(v), 1) for v in joint["pixel"]]
        out.append(joints)
    return out


def copy_frame(storage_root, sid, frame, frames_dir):
    target = frames_dir / (frame["media_id"] + ".jpg")
    if target.exists():
        return
    source = Path(storage_root) / "media" / sid / (frame["media_id"] + ".bin")
    data = source.read_bytes()
    if len(data) != frame["size"] or hashlib.sha256(data).hexdigest() != frame["sha256"]:
        raise ValueError("frame integrity failed")
    target.write_bytes(data)
    os.chmod(target, 0o600)


def export(args, config):
    import vjepa_live
    out = Path(args.out).expanduser()
    if out.exists():
        raise SystemExit("--out must be a new directory")
    out.mkdir(mode=0o700, parents=True)
    frames_dir = out / "frames"
    frames_dir.mkdir(mode=0o700)
    root = Path(args.storage_root).expanduser()
    db = regression.open_ro(str(root / "lab.sqlite3"))
    exp = config["export"]
    boxes = vjepa_live.PersonBoxes(ReadOnlyStore(db), min_frames=exp["box_min_frames"],
                                   min_confidence=exp["box_min_confidence"], match_seconds=exp["box_match_seconds"])
    contexts = None
    if args.labels:
        import vjepa_tune_labels as labels
        contexts = labels.session_contexts(db, regression.load_manifest(args.manifest))
    tz = zoneinfo.ZoneInfo(args.timezone)
    sessions = [dict(r) for r in db.execute("SELECT * FROM sessions")]
    chosen = [s for s in sessions if any(s["id"].startswith(p) for p in args.sessions)]
    counts, written = Counter(), 0
    with (out / "clips.jsonl").open("w", encoding="utf-8") as clips_out:
        clips_out.write(json.dumps({"schema": CLIPS_SCHEMA, "site_id": args.site, "camera_id": args.camera,
                                    "variants": args.variants, "stride_s": exp["stride_s"]}, sort_keys=True) + "\n")
        for session in chosen:
            sid = session["id"]
            session = dict(session, site_id=args.site, camera_id=session.get("camera_id") or args.camera)
            frames = session_frames(db, sid, args.site, session["camera_id"])
            day = datetime.datetime.fromtimestamp(session["started_at"] or 0, tz).date().isoformat()
            for variant in args.variants:
                spec = exp["span_variants"][variant]
                step, tolerance = spec["step_s"], spec["slot_tolerance_s"]
                kept = 0
                for anchor in anchors(frames, exp["stride_s"], step * (FRAME_COUNT - 1)):
                    if kept >= exp["max_clips_per_session"]:
                        counts["session_cap"] += 1
                        break
                    clip = clip_at(frames, anchor, step, tolerance)
                    if clip is None:
                        counts["frames_unavailable"] += 1
                        continue
                    try:
                        union = boxes.union(session, clip, anchor)
                    except vjepa_live.BoxUnavailable as error:
                        counts[str(error)] += 1
                        continue
                    for frame in clip:
                        copy_frame(root, sid, frame, frames_dir)
                    window = (clip[0]["observed_at"], clip[-1]["observed_at"])
                    clip_id = hashlib.sha256(("%s|%.3f|%s" % (sid, anchor, variant)).encode()).hexdigest()[:24]
                    record = {"clip_id": clip_id, "session_id": sid, "day": day, "span_variant": variant,
                              "anchor_at": anchor, "anchor_rel_s": round(anchor - (session["started_at"] or 0), 3),
                              "window_start": window[0], "window_end": window[1],
                              "frames": [{"media_id": f["media_id"], "observed_at": f["observed_at"]} for f in clip],
                              "box": union["box"], "single_person": union["single_person"],
                              "joints": joints_for(db, sid, clip, clip[-1]["capture_epoch"], exp["box_match_seconds"])}
                    if contexts is not None:
                        record["label"] = labels.label_clip(contexts[sid], window[0], window[1], config["weak"])
                    clips_out.write(json.dumps(record, sort_keys=True) + "\n")
                    kept += 1
                    written += 1
                    counts["exported"] += 1
    os.chmod(out / "clips.jsonl", 0o600)
    return written, counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--storage-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--sessions", nargs="+", required=True)
    parser.add_argument("--labels", action="store_true")
    parser.add_argument("--manifest", default=regression.MANIFEST)
    parser.add_argument("--site", default="victoria")
    parser.add_argument("--camera", default="den")
    parser.add_argument("--timezone", default="America/Vancouver")
    parser.add_argument("--variants", nargs="+", default=["3.5s_8f"])
    parser.add_argument("--config", default=str(Path(__file__).with_name("vjepa_tune_config.json")))
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    written, counts = export(args, config)
    print(json.dumps({"exported": written, "counts": dict(sorted(counts.items()))}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
