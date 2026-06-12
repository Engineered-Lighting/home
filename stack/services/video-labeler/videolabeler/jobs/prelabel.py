"""prelabel job (gpu lane): two-pass VLM labeling of person-bearing analysis
windows into the suggestion layer.

Per VLM-eligible window (person_presence > 0, not yet done for the current
model+prompt -- checkpointed/resumable via the per-window done-marker):
  1. 4 keyframes from the ORIGINAL at native res (PyAV exact seek, sharpest
     within +/-0.5s by Laplacian variance, rotation-corrected), saved as
     <=768px full frames + <=512px square person crops under
     {DATA_DIR}/keyframes/{video_id}/{window_id}/ -- these JPEGs double as
     the M3 evidence strip (GET .../windows/{id}/keyframes).
  2. Pass 1: open description citing only visible evidence; Pass 2: strict
     JSON (ACTIVE_SET + 'other' enum) -> pydantic; <=2 retries appending the
     validation error; final failure -> prelabel_status='vlm_failed'.
  3. Suggestions via labels.insert_suggestion_segments (revision NEVER
     bumps): 'other' -> stored 'unknown' + hint in evidence (custom labels
     are never invented automatically); confidence = 1 - uncertainty,
     CAPPED at 0.5 when any disagreement cross-check fires (pose geometry /
     motion / adjacent-window discontinuity).
All raw passes + the disagreement breakdown land in the sidecar
{DATA_DIR}/prelabels/{video_id}/{window_id}.json.

VRAM honesty: ONE pynvml check before the first VLM call; below 22 GB free
the job fails with 'insufficient_vram' -- the gpu-exclusive eviction module
(built separately) orchestrates freeing, this job never evicts anything.
"""
from __future__ import annotations

import json
import logging
import os

from .. import db, labels
from ..jobqueue import queue as q
from ..jobqueue import states
from ..vlm import client as vlm_client
from ..vlm import prompts, schema
from ..vlm.prompts import PROMPT_VERSION

log = logging.getLogger("videolabeler.jobs.prelabel")

MIN_VLM_FREE_VRAM_GB = 22.0
MAX_JSON_RETRIES = 2
DISAGREEMENT_CONF_CAP = 0.5

KEYFRAME_COUNT = 4
FRAME_MAX_SIDE = 768
CROP_MAX_SIDE = 512
CROP_MARGIN = 1.8
SHARP_RADIUS_S = 0.5

# disagreement thresholds (defined over the FULL ontology so ACTIVE_SET
# growth keeps the checks meaningful)
HIGH_MOTION_ACTIVITIES = frozenset(
    {"exercising", "stretching_yoga", "walking", "cleaning", "laundry"})
LOW_MOTION_ACTIVITIES = frozenset(
    {"sleeping", "resting", "watching_tv", "reading", "idle_present"})
LOW_MOTION_MAX = 0.05   # a high-motion activity below this fires
HIGH_MOTION_MIN = 0.4   # a low-motion activity above this fires
LYING_MAX_ASPECT = 1.2      # lying with a TALLER-than-this bbox fires
STANDING_MIN_ASPECT = 0.8   # standing/walking with a WIDER box fires
ADJACENT_DISAGREEMENT = 0.5  # soft: label changes are legal at transitions


def done_key(vlm_model: str, prompt_version: str = PROMPT_VERSION) -> str:
    """Per-window done-marker in analysis_windows.prelabel_status (compared
    by equality only -- colons inside the model name are harmless)."""
    return f"done:{vlm_model}:{prompt_version}"


def idempotency_key(video_id: str, vlm_model: str) -> str:
    return f"prelabel:{video_id}:{vlm_model}:{PROMPT_VERSION}"


def enqueue_for_video(conn, cfg, video_id: str):
    """Idempotent prelabel enqueue; terminal jobs are retried (the done-
    markers make re-runs cheap -- only missing/failed windows re-hit the VLM)."""
    job = q.enqueue(conn, "prelabel", {"video_id": video_id},
                    lane=states.LANE_GPU,
                    idempotency_key=idempotency_key(video_id, cfg.vlm_model))
    if job["state"] in states.TERMINAL:
        job = q.retry(conn, job["id"])
    return job


# ------------------------------------------------- disagreement checks

def pose_geometry_check(posture, pose_summary) -> float:
    """1.0 when the VLM posture contradicts the detected bbox geometry:
    lying_down with a tall box (h/w > LYING_MAX_ASPECT) or standing/walking
    with a wide box (h/w < STANDING_MIN_ASPECT)."""
    bbox = (pose_summary or {}).get("median_bbox")
    if not bbox:
        return 0.0
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if w <= 0 or h <= 0:
        return 0.0
    aspect = h / w
    if posture == "lying_down" and aspect > LYING_MAX_ASPECT:
        return 1.0
    if posture in ("standing", "walking") and aspect < STANDING_MIN_ASPECT:
        return 1.0
    return 0.0


def motion_check(activity, motion_energy) -> float:
    """1.0 on an activity/motion mismatch (e.g. exercising with
    motion_energy < 0.05, or sleeping with motion_energy > 0.4)."""
    if motion_energy is None:
        return 0.0
    if activity in HIGH_MOTION_ACTIVITIES and motion_energy < LOW_MOTION_MAX:
        return 1.0
    if activity in LOW_MOTION_ACTIVITIES and motion_energy > HIGH_MOTION_MIN:
        return 1.0
    return 0.0


def adjacency_check(activity, prev_activity) -> float:
    """Soft signal (0.5): the previous window got a different activity.
    Single-neighbor discontinuities are legitimate at transitions, so this
    never fires at full strength."""
    if prev_activity is None or activity == prev_activity:
        return 0.0
    return ADJACENT_DISAGREEMENT


def disagreement_checks(activity, posture, pose_summary, motion_energy,
                        prev_activity) -> dict:
    return {
        "pose_geometry": pose_geometry_check(posture, pose_summary),
        "motion": motion_check(activity, motion_energy),
        "adjacent": adjacency_check(activity, prev_activity),
    }


def disagreement_of(checks: dict) -> float:
    return max(checks.values()) if checks else 0.0


def cap_confidence(confidence: float, disagreement: float) -> float:
    """Any fired disagreement caps displayed confidence at 0.5."""
    conf = min(max(confidence, 0.0), 1.0)
    return min(conf, DISAGREEMENT_CONF_CAP) if disagreement > 0 else conf


# --------------------------------------------------------- vram honesty

def _free_vram_gb():
    """NVML free VRAM; None on hosts without a GPU/driver (the check is
    then skipped -- CPU dev boxes and tests)."""
    try:
        import pynvml
        pynvml.nvmlInit()
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            return pynvml.nvmlDeviceGetMemoryInfo(h).free / 2**30
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return None


# ------------------------------------------------------------ keyframes

def _ensure_keyframes(cfg, video_row, window, pose_summary) -> dict:
    """Extract-or-reuse the window's keyframe JPEGs; returns the meta dict
    {"frames": [{"t", "frame", "crop"|None}]}. Decode is from the ORIGINAL
    at native res (red-team: 480p keyframes turn far-field people to mush);
    the dominant-person crop uses pose_summary.median_bbox."""
    vid, wid = video_row["id"], window["id"]
    kdir = os.path.join(cfg.keyframes_dir, vid, wid)
    meta_path = os.path.join(kdir, "meta.json")
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            frames_ok = meta.get("frames") and all(
                os.path.isfile(os.path.join(kdir, fr["frame"]))
                for fr in meta["frames"])
            if frames_ok:
                return meta
        except (ValueError, KeyError, TypeError):
            pass  # corrupt meta -> re-extract
    from ..media import frames
    if not video_row["local_path"]:
        raise RuntimeError(f"video {vid} has no original on disk")
    src = cfg.resolve(video_row["local_path"])
    ws, we = window["start_s"], window["end_s"]
    span = we - ws
    times = [ws + (k + 0.5) * span / KEYFRAME_COUNT
             for k in range(KEYFRAME_COUNT)]
    picked = frames.extract_keyframes(src, video_row["rotation_deg"] or 0,
                                      times, sharp_radius_s=SHARP_RADIUS_S)
    if not picked:
        raise RuntimeError(f"no decodable keyframes for window {wid}")
    bbox = (pose_summary or {}).get("median_bbox")
    out = []
    os.makedirs(kdir, exist_ok=True)
    for k, (t, rgb) in enumerate(picked):
        fname = f"frame_{k}.jpg"
        frames.save_jpeg(rgb, os.path.join(kdir, fname), FRAME_MAX_SIDE)
        crop_name = None
        if bbox:
            crop = frames.square_person_crop(rgb, bbox, CROP_MARGIN)
            if crop is not None:
                crop_name = f"crop_{k}.jpg"
                frames.save_jpeg(crop, os.path.join(kdir, crop_name),
                                 CROP_MAX_SIDE)
        out.append({"t": round(float(t), 3), "frame": fname, "crop": crop_name})
    meta = {"video_id": vid, "window_id": wid, "frames": out,
            "created_at": db.iso_now()}
    tmp = meta_path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    os.replace(tmp, meta_path)
    return meta


def _image_parts(cfg, video_id, window_id, meta):
    """Keyframe JPEGs -> OpenAI image parts (full frame then crop, in time
    order) + the keyframe timestamps."""
    kdir = os.path.join(cfg.keyframes_dir, video_id, window_id)
    parts, times = [], []
    for fr in meta.get("frames", []):
        times.append(fr["t"])
        with open(os.path.join(kdir, fr["frame"]), "rb") as f:
            parts.append(vlm_client.image_part(f.read()))
        if fr.get("crop"):
            with open(os.path.join(kdir, fr["crop"]), "rb") as f:
                parts.append(vlm_client.image_part(f.read()))
    if not parts:
        raise RuntimeError(f"no keyframe images for window {window_id}")
    return parts, times


# -------------------------------------------------------------- sidecar

def _sidecar_path(cfg, video_id, window_id) -> str:
    return os.path.join(cfg.prelabels_dir, video_id, f"{window_id}.json")


def _load_sidecar(cfg, video_id, window_id):
    p = _sidecar_path(cfg, video_id, window_id)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return None


def _write_sidecar(cfg, video_id, window_id, doc) -> None:
    p = _sidecar_path(cfg, video_id, window_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, p)


# ----------------------------------------------------------- VLM passes

def _summary_of(p2):
    if not p2:
        return None
    return (f"{p2.get('activity_primary')} / {p2.get('posture')}:"
            f" {p2.get('evidence', '')}")[:400]


def _prelabel_window(cfg, client, video_row, window, pose_summary, room,
                     prev_summary) -> dict:
    """Two-pass VLM for one window. Returns the sidecar outcome doc; pass2
    is None when the model never produced valid JSON (-> vlm_failed).
    Transport errors (VlmError) BUBBLE so the whole job fails honestly and
    the runner/retry machinery handles it."""
    meta = _ensure_keyframes(cfg, video_row, window, pose_summary)
    images, ktimes = _image_parts(cfg, video_row["id"], window["id"], meta)
    ws, we = window["start_s"], window["end_s"]
    outcome = {
        "video_id": video_row["id"], "window_id": window["id"],
        "model": getattr(client, "model", cfg.vlm_model),
        "prompt_version": PROMPT_VERSION, "keyframes": meta["frames"],
        "pass1": None, "pass2_raw": [], "pass2": None, "error": None,
    }
    pass1 = client.chat(prompts.pass1_messages(room, ws, we, prev_summary,
                                               images))
    outcome["pass1"] = pass1
    prior, err = None, None
    for _attempt in range(MAX_JSON_RETRIES + 1):
        raw = client.chat(
            prompts.pass2_messages(pass1, room, ws, we, ktimes, images,
                                   prior_response=prior,
                                   validation_error=err),
            json_mode=True)
        outcome["pass2_raw"].append(raw)
        try:
            outcome["pass2"] = schema.parse_pass2(raw).model_dump()
            return outcome
        except ValueError as e:
            prior, err = raw, str(e)[:1500]
    outcome["error"] = (f"pass2 JSON invalid after {MAX_JSON_RETRIES + 1}"
                        f" attempt(s): {err}")
    return outcome


# ---------------------------------------------------------- suggestions

def _retire_window_suggestions(conn, video_id, window_id, now) -> None:
    """Re-run safety: a crash between suggestion insert and the done-marker
    would duplicate suggestions on resume -- retire this window's previous
    vlm suggestions (matched via evidence_json window_id) first."""
    rows = conn.execute(
        "SELECT id, evidence_json FROM segments WHERE video_id = ?"
        " AND source = 'vlm' AND review_state = 'prelabel' AND is_current = 1"
        " AND evidence_json LIKE ?",
        (video_id, f'%"window_id": "{window_id}"%')).fetchall()
    stale = []
    for r in rows:
        try:
            if json.loads(r["evidence_json"]).get("window_id") == window_id:
                stale.append(r["id"])
        except (ValueError, TypeError):
            continue
    if stale:
        with db.tx(conn):
            for sid in stale:
                conn.execute("UPDATE segments SET is_current = 0,"
                             " updated_at = ? WHERE id = ?", (now, sid))


def _insert_window_suggestions(conn, cfg, video_id, window, pose_summary,
                               p2, disagreement, checks) -> None:
    """Pass-2 result -> per-axis suggestion segments carrying the window
    geometry. NEVER bumps the revision (labels.insert_suggestion_segments
    contract)."""
    now = db.iso_now()
    _retire_window_suggestions(conn, video_id, window["id"], now)
    ws, we = window["start_s"], window["end_s"]
    base_ev = {
        "window_id": window["id"], "model": cfg.vlm_model,
        "prompt_version": PROMPT_VERSION,
        "evidence": p2["evidence"],
        "evidence_timestamps": p2["evidence_timestamps"],
        "disagreement": disagreement, "checks": checks,
    }
    activity = p2["activity_primary"]
    act_ev = dict(base_ev)
    if activity == "other":
        # never invent labels automatically: store 'unknown', keep the hint
        # as evidence for the human custom-label flow
        activity = "unknown"
        act_ev["other_hint"] = p2.get("activity_other_hint")
    conf_act = cap_confidence(1.0 - p2["uncertainty"]["activity"], disagreement)
    labels.insert_suggestion_segments(conn, video_id, "activity_primary", [
        {"start_s": ws, "end_s": we, "value": activity,
         "confidence": conf_act, "evidence": act_ev}])
    conf_pos = cap_confidence(1.0 - p2["uncertainty"]["posture"], disagreement)
    labels.insert_suggestion_segments(conn, video_id, "posture", [
        {"start_s": ws, "end_s": we, "value": p2["posture"],
         "confidence": conf_pos, "evidence": base_ev}])
    flags = list(p2["quality_flags"])
    if (pose_summary or {}).get("max_persons", 0) >= 2 \
            and "multiple_people" not in flags:
        # v1 is single-primary-occupant: pose-detected >=2 people always
        # carries the canonical multiple_people flag (export rules key on it)
        flags.append("multiple_people")
    if flags:
        # the pass-2 schema has no quality uncertainty: use the more
        # uncertain of the two labeled axes as a conservative stand-in
        conf_q = cap_confidence(
            1.0 - max(p2["uncertainty"]["activity"],
                      p2["uncertainty"]["posture"]), disagreement)
        labels.insert_suggestion_segments(conn, video_id, "quality", [
            {"start_s": ws, "end_s": we, "value": flags,
             "confidence": conf_q, "evidence": base_ev}])


# --------------------------------------------------------------- the job

def run(job, ctx) -> None:
    conn = ctx.conn
    cfg = ctx.cfg
    vid = q.payload(job)["video_id"]
    row = ctx.get_video(vid)
    if row is None:
        raise RuntimeError(f"video {vid} not found")
    if row["is_holdout"]:
        raise RuntimeError(
            "holdout video: prelabeling refused (plan: holdout gets a"
            " human-only eval pass after the prompt/ontology freeze)")
    wrows = conn.execute(
        "SELECT * FROM analysis_windows WHERE video_id = ? ORDER BY start_s",
        (vid,)).fetchall()
    if not wrows:
        ctx.heartbeat(progress=1.0, msg="no analysis windows")
        return
    if any(r["person_presence"] is None for r in wrows):
        raise RuntimeError("perception missing -- run the perceive job first")

    dk = done_key(cfg.vlm_model)
    todo_ids = {r["id"] for r in wrows
                if r["person_presence"] > 0 and r["prelabel_status"] != dk}
    if not todo_ids:
        ctx.heartbeat(progress=1.0,
                      msg="all windows already prelabeled or no_person")
        return

    free = _free_vram_gb()
    if free is not None and free < MIN_VLM_FREE_VRAM_GB:
        raise RuntimeError(
            f"insufficient_vram: run a gpu-exclusive window first"
            f" (free {free:.1f} GB < {MIN_VLM_FREE_VRAM_GB:.0f} GB)")

    client = vlm_client.get_client(cfg)
    room = prompts.room_hint(row["filename"])
    prev_summary = None
    prev_activity = None
    n_done = n_failed = processed = 0
    for w in wrows:
        if w["person_presence"] == 0:
            prev_summary, prev_activity = None, None
            continue
        if w["id"] not in todo_ids:
            # already done for this model+prompt: keep temporal context
            side = _load_sidecar(cfg, vid, w["id"]) or {}
            p2 = side.get("pass2") or {}
            prev_activity = p2.get("activity_primary")
            prev_summary = _summary_of(p2)
            continue
        processed += 1
        ctx.heartbeat(progress=processed / (len(todo_ids) + 1),
                      msg=f"VLM window {w['id']}")
        pose = json.loads(w["pose_summary_json"]) if w["pose_summary_json"] else {}
        outcome = _prelabel_window(cfg, client, row, w, pose, room,
                                   prev_summary)
        p2 = outcome["pass2"]
        if p2 is not None:
            checks = disagreement_checks(
                p2["activity_primary"], p2["posture"], pose,
                w["motion_energy"], prev_activity)
            outcome["checks"] = checks
            outcome["disagreement"] = disagreement_of(checks)
        _write_sidecar(cfg, vid, w["id"], outcome)
        if p2 is None:
            n_failed += 1
            with db.tx(conn):
                conn.execute("UPDATE analysis_windows SET prelabel_status ="
                             " 'vlm_failed' WHERE id = ?", (w["id"],))
            ctx.checkpoint({"last_window_id": w["id"], "done": n_done,
                            "failed": n_failed})
            prev_summary, prev_activity = None, None
            continue
        _insert_window_suggestions(conn, cfg, vid, w, pose, p2,
                                   outcome["disagreement"], outcome["checks"])
        with db.tx(conn):
            conn.execute("UPDATE analysis_windows SET prelabel_status = ?"
                         " WHERE id = ?", (dk, w["id"]))
        n_done += 1
        ctx.checkpoint({"last_window_id": w["id"], "done": n_done,
                        "failed": n_failed})
        prev_activity = p2["activity_primary"]
        prev_summary = _summary_of(p2)

    if n_done == 0 and n_failed > 0:
        raise RuntimeError(
            f"all {n_failed} eligible window(s) vlm_failed -- raw passes in"
            f" {cfg.prelabels_dir}/{vid}/")
    ctx.heartbeat(progress=1.0,
                  msg=f"prelabeled {n_done}/{len(todo_ids)} window(s),"
                      f" {n_failed} vlm_failed")
    log.info("prelabel %s: %d done, %d failed (model=%s prompt=%s)",
             vid, n_done, n_failed, cfg.vlm_model, PROMPT_VERSION)
