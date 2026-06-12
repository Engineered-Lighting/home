"""perceive job: per-analysis-window motion energy + yolo11n-pose person
stats, plus the no_person auto-prelabel (cpu lane; yolo runs on GPU when one
is visible, CPU otherwise -- the model is tiny).

Per window (8s/4s grid rows created by jobs/windows.py):
  * frames sampled at ~1 fps from the 480p PROXY (rotation-normalized);
    falls back to the ORIGINAL when the proxy encode has not landed yet so
    the windows-tail auto-chain can never starve behind a slow encode.
  * motion_energy: fraction of downsampled-gray pixels changed (>15/255)
    between consecutive sampled frames, averaged over the window (0..1).
  * person_presence: fraction of sampled frames with >= 1 person at
    conf >= 0.35; pose_summary_json: {max_persons, median_bbox (normalized
    xyxy of the per-frame DOMINANT = largest-area person), bbox_area_frac,
    keypoint_visibility_summary}.
Windows with person_presence == 0 get auto-prelabels (activity + posture
'no_person', confidence 0.95, source='rule'); ADJACENT all-no_person windows
merge into one spanning suggestion segment instead of 8s confetti.

ALL perception imports (av/torch/ultralytics via media.frames) stay inside
functions -- the API process and the Windows test venv never load them;
tests monkeypatch _analyze_video and exercise everything else sqlite-only.
"""
from __future__ import annotations

import json
import logging
import os

from .. import db, labels
from ..jobqueue import queue as q
from ..jobqueue import states

log = logging.getLogger("videolabeler.jobs.perceive")

PERCEIVE_FPS = 1.0
YOLO_CONF = 0.35
YOLO_WEIGHTS = "yolo11n-pose.pt"
KEYPOINT_VISIBLE_CONF = 0.5
NO_PERSON_CONF = 0.95
CHUNK_ROWS = 200

_EMPTY_SUMMARY = {"max_persons": 0, "median_bbox": None,
                  "bbox_area_frac": None, "keypoint_visibility_summary": None,
                  "all_bboxes": []}


def _rank_bboxes(frs, max_ranks: int = 4) -> list:
    """Per-window person boxes by size rank: rank k holds the median bbox of
    the k-th-largest person across frames that have >= k+1 persons. Returns
    [{rank, median_bbox, n_frames}] (normalized xyxy)."""
    out = []
    for k in range(max_ranks):
        ranked = []
        for r in frs:
            persons = sorted(r["persons"], key=lambda p: -p["area_frac"])
            if len(persons) > k:
                ranked.append(persons[k])
        if not ranked:
            break
        out.append({
            "rank": k,
            "median_bbox": [round(_median([p["bbox"][i] for p in ranked]), 4)
                            for i in range(4)],
            "n_frames": len(ranked),
        })
    return out


def enqueue_for_video(conn, video_id: str, then_prelabel: bool = False):
    """Idempotent perceive enqueue (key perceive:<vid>). then_prelabel=True
    grafts the prelabel chain onto an existing job's payload (the worker
    re-reads its row at the tail, so even a RUNNING job picks it up);
    terminal jobs are retried so POST /prelabel can heal a failed chain."""
    payload = {"video_id": video_id}
    if then_prelabel:
        payload["then_prelabel"] = True
    job = q.enqueue(conn, "perceive", payload, lane=states.LANE_CPU,
                    idempotency_key=f"perceive:{video_id}")
    if then_prelabel and not q.payload(job).get("then_prelabel"):
        merged = dict(q.payload(job))
        merged["then_prelabel"] = True
        with db.tx(conn):
            conn.execute("UPDATE jobs SET payload_json = ? WHERE id = ?",
                         (json.dumps(merged), job["id"]))
        job = q.get_job(conn, job["id"])
    if job["state"] in states.TERMINAL:
        job = q.retry(conn, job["id"])
    return job


# ------------------------------------------------------- pure aggregation

def _median(vals):
    s = sorted(vals)
    n = len(s)
    if not n:
        return None
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def aggregate_window_stats(records, windows) -> dict:
    """Frame records -> per-window stats (pure python; unit-testable without
    the perception stack).

    records: [{"t": s, "persons": [{"bbox": [x1,y1,x2,y2] normalized,
               "area_frac": f, "kp_frac": f|None}], "diff": f|None}]
    windows: [(window_id, start_s, end_s)]
    -> {window_id: {"person_presence", "motion_energy", "pose_summary"}}
    """
    out: dict = {}
    for wid, ws, we in windows:
        frs = [r for r in records if ws <= r["t"] < we]
        if not frs:
            out[wid] = {"person_presence": 0.0, "motion_energy": 0.0,
                        "pose_summary": dict(_EMPTY_SUMMARY)}
            continue
        presence = sum(1 for r in frs if r["persons"]) / len(frs)
        diffs = [r["diff"] for r in frs if r["diff"] is not None]
        motion = min(1.0, max(0.0, sum(diffs) / len(diffs))) if diffs else 0.0
        dominant = [max(r["persons"], key=lambda p: p["area_frac"])
                    for r in frs if r["persons"]]
        if dominant:
            med_bbox = [round(_median([p["bbox"][i] for p in dominant]), 4)
                        for i in range(4)]
            area = round(_median([p["area_frac"] for p in dominant]), 4)
            kps = [p["kp_frac"] for p in dominant if p["kp_frac"] is not None]
            kp_summary = ({"mean_visible_frac": round(sum(kps) / len(kps), 4),
                           "n_detections": len(dominant)} if kps else
                          {"mean_visible_frac": None,
                           "n_detections": len(dominant)})
            summary = {"max_persons": max(len(r["persons"]) for r in frs),
                       "median_bbox": med_bbox, "bbox_area_frac": area,
                       "keypoint_visibility_summary": kp_summary,
                       # ALL persons, rank k = k-th largest median area —
                       # multi-person scenes are ~a third of the corpus; the
                       # future per-track pipeline (person_slot binding +
                       # per-person crops) needs every box, not just the
                       # dominant one. Median per rank across the window's
                       # frames keeps it compact and jitter-resistant.
                       "all_bboxes": _rank_bboxes(frs)}
        else:
            summary = dict(_EMPTY_SUMMARY)
        out[wid] = {"person_presence": round(presence, 4),
                    "motion_energy": round(motion, 4),
                    "pose_summary": summary}
    return out


def merge_no_person_spans(rows) -> list:
    """rows: [(start_s, end_s, person_presence)] in grid order. Runs of
    CONSECUTIVE windows with presence == 0 merge into single (start, end)
    spans (overlapping 8s/4s geometry -> max of ends)."""
    spans: list = []
    cur = None
    for start, end, presence in rows:
        if presence is not None and presence == 0:
            if cur is None:
                cur = [start, end]
            else:
                cur[1] = max(cur[1], end)
        elif cur is not None:
            spans.append((cur[0], cur[1]))
            cur = None
    if cur is not None:
        spans.append((cur[0], cur[1]))
    return spans


# ------------------------------------------------- perception (GPU/CPU)

def _load_detector(cfg):
    """ultralytics yolo11n-pose, downloaded ONCE to {DATA_DIR}/models/;
    device autodetect (the model is tiny -- CPU is fine without a GPU)."""
    import torch
    from ultralytics import YOLO
    os.makedirs(cfg.models_dir, exist_ok=True)
    weights = os.path.join(cfg.models_dir, YOLO_WEIGHTS)
    if not os.path.isfile(weights):
        from ultralytics.utils.downloads import attempt_download_asset
        log.info("downloading %s to %s", YOLO_WEIGHTS, cfg.models_dir)
        attempt_download_asset(weights)
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    return YOLO(weights), device


def _detect_persons(model, device, rgb) -> list:
    import numpy as np
    h, w = rgb.shape[:2]
    bgr = np.ascontiguousarray(rgb[:, :, ::-1])
    res = model.predict(bgr, conf=YOLO_CONF, verbose=False, device=device)[0]
    persons: list = []
    boxes = res.boxes
    if boxes is None or len(boxes) == 0:
        return persons
    xyxy = boxes.xyxy.cpu().numpy()
    kp_conf = None
    if res.keypoints is not None and res.keypoints.conf is not None:
        kp_conf = res.keypoints.conf.cpu().numpy()
    for i in range(len(xyxy)):
        x1, y1, x2, y2 = (float(v) for v in xyxy[i])
        bbox = [max(0.0, x1 / w), max(0.0, y1 / h),
                min(1.0, x2 / w), min(1.0, y2 / h)]
        area = max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])
        kp = (float((kp_conf[i] > KEYPOINT_VISIBLE_CONF).mean())
              if kp_conf is not None and i < len(kp_conf) else None)
        persons.append({"bbox": bbox, "area_frac": area, "kp_frac": kp})
    return persons


def _analyze_video(cfg, src_path, rotation_deg, windows, duration_s=None,
                   progress=None) -> dict:
    """Decode + detect over the whole file once, then aggregate per window.
    Tests monkeypatch THIS function -- everything above it in run() is
    sqlite-only."""
    from ..media import frames
    model, device = _load_detector(cfg)
    log.info("yolo11n-pose on %s over %s", device, src_path)
    records: list = []
    prev_gray = None
    for t, rgb, gray in frames.iter_sampled_frames(src_path, rotation_deg,
                                                   PERCEIVE_FPS):
        diff = (frames.motion_fraction(prev_gray, gray)
                if prev_gray is not None else None)
        prev_gray = gray
        records.append({"t": t, "persons": _detect_persons(model, device, rgb),
                        "diff": diff})
        if progress is not None and duration_s:
            progress(min(t / duration_s, 1.0))
    return aggregate_window_stats(records, windows)


# --------------------------------------------------------------- the job

def _decode_source(conn, cfg, row):
    """(path, rotation_deg): proxy when ready (already upright), ORIGINAL
    fallback otherwise -- the windows-tail chain enqueues perceive while the
    proxy encode may still be running, and waiting in a cpu slot could
    deadlock the lane."""
    art = conn.execute(
        "SELECT path FROM video_artifacts WHERE video_id = ?"
        " AND kind = 'proxy480' AND status = 'ready'", (row["id"],)).fetchone()
    if art is not None:
        p = cfg.resolve(art["path"])
        if os.path.isfile(p):
            return p, 0
    if not row["local_path"]:
        raise RuntimeError(f"video {row['id']} has no media on disk")
    return cfg.resolve(row["local_path"]), row["rotation_deg"] or 0


def run(job, ctx) -> None:
    conn = ctx.conn
    cfg = ctx.cfg
    vid = q.payload(job)["video_id"]
    row = ctx.get_video(vid)
    if row is None:
        raise RuntimeError(f"video {vid} not found")
    wrows = conn.execute(
        "SELECT * FROM analysis_windows WHERE video_id = ? ORDER BY start_s",
        (vid,)).fetchall()
    if not wrows:
        ctx.heartbeat(progress=1.0, msg="no analysis windows -- nothing to perceive")
        return

    src, rotation = _decode_source(conn, cfg, row)
    ctx.heartbeat(progress=0.0, msg=f"pose+motion over {len(wrows)} windows")
    stats = _analyze_video(
        cfg, src, rotation, [(r["id"], r["start_s"], r["end_s"]) for r in wrows],
        duration_s=row["duration_s"],
        progress=lambda f: ctx.heartbeat(progress=0.85 * f))

    # persist per-window stats in <=200-row chunks (two-writer rule)
    for i in range(0, len(wrows), CHUNK_ROWS):
        chunk = wrows[i:i + CHUNK_ROWS]
        with db.tx(conn):
            for r in chunk:
                st = stats.get(r["id"]) or {
                    "person_presence": 0.0, "motion_energy": 0.0,
                    "pose_summary": dict(_EMPTY_SUMMARY)}
                presence = st["person_presence"]
                if presence == 0:
                    status = "no_person"
                elif r["prelabel_status"] == "no_person":
                    status = "pending"  # person found on re-run: VLM-eligible again
                else:
                    status = r["prelabel_status"]
                conn.execute(
                    "UPDATE analysis_windows SET person_presence = ?,"
                    " motion_energy = ?, pose_summary_json = ?,"
                    " prelabel_status = ? WHERE id = ?",
                    (presence, st["motion_energy"],
                     json.dumps(st["pose_summary"], sort_keys=True),
                     status, r["id"]))
        ctx.heartbeat(progress=0.85 + 0.1 * (i + len(chunk)) / len(wrows),
                      msg=f"persisted {i + len(chunk)}/{len(wrows)} windows")

    # no_person auto-prelabel: retire previous rule suggestions (idempotent
    # re-run), then insert merged spanning segments on both axes
    spans = merge_no_person_spans(
        [(r["start_s"], r["end_s"],
          (stats.get(r["id"]) or {}).get("person_presence", 0.0))
         for r in wrows])
    now = db.iso_now()
    with db.tx(conn):
        conn.execute(
            "UPDATE segments SET is_current = 0, updated_at = ? WHERE"
            " video_id = ? AND source = 'rule' AND review_state = 'prelabel'"
            " AND is_current = 1", (now, vid))
    if spans:
        segs = [{"start_s": s, "end_s": e, "value": "no_person",
                 "confidence": NO_PERSON_CONF,
                 "evidence": {"rule": "no_person_auto"}} for s, e in spans]
        labels.insert_suggestion_segments(conn, vid, "activity_primary",
                                          segs, source="rule")
        labels.insert_suggestion_segments(conn, vid, "posture",
                                          segs, source="rule")
    n_empty = sum(1 for r in wrows
                  if (stats.get(r["id"]) or {}).get("person_presence") == 0)
    ctx.heartbeat(progress=1.0,
                  msg=f"{len(wrows)} windows, {n_empty} no_person"
                      f" -> {len(spans)} merged span(s)")
    log.info("perceive %s: %d windows, %d empty, %d no_person span(s)",
             vid, len(wrows), n_empty, len(spans))

    # chain: POST /prelabel grafts then_prelabel onto this job's payload --
    # re-read our own row so a mid-run graft is honored too
    fresh = q.get_job(conn, ctx.job_id)
    if fresh is not None and q.payload(fresh).get("then_prelabel"):
        from . import prelabel
        pj = prelabel.enqueue_for_video(conn, cfg, vid)
        log.info("chained prelabel job %s for %s", pj["id"], vid)
