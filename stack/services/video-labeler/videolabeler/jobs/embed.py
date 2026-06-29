"""embed job (gpu lane): V-JEPA pooled embeddings for ALL analysis windows
of one video -- INCLUDING holdout (embeddings are not labels; the holdout
firewall lives downstream: cluster runs and neighbor candidate sets exclude
holdout in SQL).

Per window: 3 evenly-placed 16-frame clips at an effective ~7.5 fps, decoded
from the ORIGINAL via PyAV at EXACT frame indices, rotation-corrected.
PERSON-CROP IS PRIMARY (the user's validated 87.8/94.1% result was
per-person crops; whole-frame short-side-384 center-crop discards far-field
people): the personcrop_v1 variant crops a 1.8x-margin square around the
dominant person bbox (pose_summary.median_bbox) and resizes to the encoder's
input size; windows without a usable bbox fall back to whole-frame
(mode 'wholeframe_fallback' in frames_json). wholeframe_v1 is the secondary
context variant. model_id = '<backbone>@<variant>'.

Each clip's encoder tokens are MEAN-POOLED to one vector; all 3 per-clip
vectors land as kind='pooled_clip' rows (clip_idx 0..2) plus their mean as
kind='pooled_window' -- fp16 .npy shards + sqlite pointer rows
(embeddings/store.py). cache_key =
sha1(model_id|checksum|kind|clip_idx|frame_indices|crop_box|preproc_v1):
re-runs skip windows whose 4 keys already exist, which is also the resume
mechanism (rows flush to disk+db every FLUSH_EVERY_WINDOWS windows AND on
any failure path, so a crashed/cancelled job loses at most one flush span).

VRAM honesty: ONE pynvml check before loading the encoder; below 4 GB free
the job fails fast. ViT-B is small -- it runs WITHOUT a gpu-exclusive
window even with the 90b pinned; this job NEVER evicts anything. CUDA OOM
while embedding halves the window batch down to 1, then fails honestly.

Tests are sqlite-only: the encoder is injected via
embeddings.backbone.set_embedder_factory and _decode_window_clips is
monkeypatched -- everything else (planning, cache keys, shards, OOM
backoff, resume) runs for real.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math

from .. import db
from ..embeddings import backbone, store
from ..jobqueue import queue as q
from ..jobqueue import states
from ..media import frames

log = logging.getLogger("videolabeler.jobs.embed")

MIN_EMBED_FREE_VRAM_GB = 4.0
CLIPS_PER_WINDOW = 3
FRAMES_PER_CLIP = 16
TARGET_CLIP_FPS = 7.5
CROP_MARGIN = 1.8
PREPROC_VERSION = "preproc_v1"
EMBED_BATCH_WINDOWS = 4      # 12 clips/forward; halved on CUDA OOM
FLUSH_EVERY_WINDOWS = 32     # shard+db flush cadence (128 rows/shard)

VARIANT_PERSONCROP = "personcrop_v1"
VARIANT_WHOLEFRAME = "wholeframe_v1"
VARIANTS = (VARIANT_PERSONCROP, VARIANT_WHOLEFRAME)
DEFAULT_VARIANT = VARIANT_PERSONCROP


def model_id_for(cfg, variant: str) -> str:
    return f"{cfg.vjepa_model_name}@{variant}"


def idempotency_key(video_id: str, model_id: str) -> str:
    return f"embed:{video_id}:{model_id}"


def enqueue_for_video(conn, cfg, video_id: str,
                      variant: str = DEFAULT_VARIANT):
    """Idempotent embed enqueue; terminal jobs are retried (cache keys make
    re-runs cheap -- only missing windows hit the encoder)."""
    job = q.enqueue(conn, "embed", {"video_id": video_id, "variant": variant},
                    lane=states.LANE_GPU,
                    idempotency_key=idempotency_key(
                        video_id, model_id_for(cfg, variant)))
    if job["state"] in states.TERMINAL:
        job = q.retry(conn, job["id"])
    return job


# ---------------------------------------------------------------- planning
# Pure python/sqlite -- no decode, no torch. Cache keys are derivable from
# the db row alone, so skip logic costs nothing.

def plan_clips(start_s: float, end_s: float, fps: float,
               duration_s: float) -> tuple:
    """CLIPS_PER_WINDOW evenly-placed FRAMES_PER_CLIP-frame clips at an
    effective ~TARGET_CLIP_FPS -> ([clip frame-index lists], frame_step).
    Indices are clamped to the window/video end; windows shorter than one
    clip span repeat the final frame (official V-JEPA pad semantics)."""
    step = max(1, int(round(fps / TARGET_CLIP_FPS)))
    last = max(0, int(math.floor((duration_s or end_s) * fps)) - 1)
    f0 = min(int(math.ceil(start_s * fps)), last)
    f1 = min(max(int(math.floor(end_s * fps)) - 1, f0), last)
    span = (FRAMES_PER_CLIP - 1) * step
    room = max(0, (f1 - f0) - span)
    clips = []
    for j in range(CLIPS_PER_WINDOW):
        frac = j / (CLIPS_PER_WINDOW - 1)
        s = f0 + int(round(room * frac))
        clips.append([min(s + i * step, f1) for i in range(FRAMES_PER_CLIP)])
    return clips, step


def cache_key(model_id: str, checksum: str, kind: str, clip_idx: int,
              frame_indices: list, crop_box) -> str:
    crop = (",".join(str(v) for v in crop_box) if crop_box else "full")
    blob = "|".join((model_id, checksum, kind, str(clip_idx),
                     ",".join(str(i) for i in frame_indices), crop,
                     PREPROC_VERSION))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def upright_dims(width: int, height: int, rotation_deg: int) -> tuple:
    """Stored stream dims -> display-orientation dims (pose bboxes are
    normalized to the rotation-corrected frame)."""
    if (rotation_deg or 0) % 180 == 90:
        return height, width
    return width, height


def plan_window(model_id: str, video_row, window, variant: str,
                pose_summary) -> dict:
    """One window's full embed plan: exact frame indices + timestamps per
    clip, the crop box (or whole-frame fallback) and all 4 cache keys."""
    fps = video_row["fps"]
    clip_indices, step = plan_clips(window["start_s"], window["end_s"], fps,
                                    video_row["duration_s"])
    crop_box, mode = None, "wholeframe"
    if variant == VARIANT_PERSONCROP:
        mode = "wholeframe_fallback"
        bbox = (pose_summary or {}).get("median_bbox")
        if bbox and (window["person_presence"] or 0) > 0:
            w, h = upright_dims(video_row["width"], video_row["height"],
                                video_row["rotation_deg"] or 0)
            crop_box = frames.square_crop_box(w, h, bbox, CROP_MARGIN)
            if crop_box is not None:
                crop_box = list(crop_box)
                mode = "personcrop"
    checksum = video_row["checksum"]
    clips = []
    all_indices: list = []
    for ci, idxs in enumerate(clip_indices):
        all_indices.extend(idxs)
        clips.append({
            "clip_idx": ci,
            "frame_indices": idxs,
            "timestamps": [round(ix / fps, 3) for ix in idxs],
            "cache_key": cache_key(model_id, checksum, "pooled_clip", ci,
                                   idxs, crop_box),
        })
    return {
        "window_id": window["id"],
        "frame_step": step,
        "crop_box": crop_box,
        "mode": mode,
        "clips": clips,
        "window_key": cache_key(model_id, checksum, "pooled_window", 0,
                                all_indices, crop_box),
    }


def plan_keys(plan: dict) -> list:
    return [c["cache_key"] for c in plan["clips"]] + [plan["window_key"]]


# ------------------------------------------------------------- gpu honesty

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


def _is_cuda_oom(exc: BaseException) -> bool:
    """torch.cuda.OutOfMemoryError without importing torch: match the class
    name anywhere in the MRO (tests raise a stand-in of the same name)."""
    return any(c.__name__ == "OutOfMemoryError" for c in type(exc).__mro__)


# ---------------------------------------------------------------- decoding

def _decode_window_clips(cfg, video_row, plan: dict, img_size: int) -> list:
    """Decode + preprocess one window's clips from the ORIGINAL: exact frame
    indices, rotation-corrected, person-crop (plan crop_box) or whole-frame
    short-side center-crop, resized to img_size. Returns CLIPS_PER_WINDOW
    [T, img, img, 3] uint8 arrays. ALL heavy imports live here; tests
    monkeypatch this function."""
    import numpy as np
    from PIL import Image
    src = cfg.resolve(video_row["local_path"])
    wanted = sorted({ix for c in plan["clips"] for ix in c["frame_indices"]})
    decoded = frames.decode_frames_at_indices(
        src, video_row["rotation_deg"] or 0, video_row["fps"], wanted)
    crop = plan["crop_box"]
    prepped: dict = {}

    def prep(ix):
        if ix in prepped:
            return prepped[ix]
        rgb = decoded[ix]
        if crop is not None:
            left, top, right, bottom = crop
            img = Image.fromarray(rgb[top:bottom, left:right])
            img = img.resize((img_size, img_size), Image.LANCZOS)
        else:
            img = Image.fromarray(rgb)
            scale = img_size / min(img.size)
            img = img.resize((max(img_size, round(img.width * scale)),
                              max(img_size, round(img.height * scale))),
                             Image.LANCZOS)
            left = (img.width - img_size) // 2
            top = (img.height - img_size) // 2
            img = img.crop((left, top, left + img_size, top + img_size))
        prepped[ix] = np.asarray(img)
        return prepped[ix]

    return [np.stack([prep(ix) for ix in c["frame_indices"]])
            for c in plan["clips"]]


# ----------------------------------------------------------------- the job

def _existing_keys(conn, model_id: str, video_id: str) -> set:
    return {r["cache_key"] for r in conn.execute(
        "SELECT e.cache_key FROM embeddings e"
        " JOIN analysis_windows w ON w.id = e.analysis_window_id"
        " WHERE e.model_id = ? AND w.video_id = ?", (model_id, video_id))}


def run(job, ctx) -> None:
    conn = ctx.conn
    cfg = ctx.cfg
    payload = q.payload(job)
    vid = payload["video_id"]
    variant = payload.get("variant") or DEFAULT_VARIANT
    if variant not in VARIANTS:
        raise RuntimeError(f"unknown embed variant '{variant}'"
                           f" (one of: {', '.join(VARIANTS)})")
    row = ctx.get_video(vid)
    if row is None:
        raise RuntimeError(f"video {vid} not found")
    if not row["local_path"]:
        raise RuntimeError(f"video {vid} has no original on disk")
    if not row["fps"] or not row["duration_s"] or not row["width"]:
        raise RuntimeError(f"video {vid} has no fps/duration/dims"
                           " -- probe must run first")
    wrows = conn.execute(
        "SELECT * FROM analysis_windows WHERE video_id = ? ORDER BY start_s",
        (vid,)).fetchall()
    if not wrows:
        ctx.heartbeat(progress=1.0, msg="no analysis windows -- nothing to embed")
        return
    if variant == VARIANT_PERSONCROP and \
            any(r["pose_summary_json"] is None for r in wrows):
        raise RuntimeError("perception missing -- run the perceive job first"
                           " (person crops need pose bboxes)")

    def build_todo(model_id):
        existing = _existing_keys(conn, model_id, vid)
        plans = []
        for w in wrows:
            pose = (json.loads(w["pose_summary_json"])
                    if w["pose_summary_json"] else {})
            pl = plan_window(model_id, row, w, variant, pose)
            if not all(k in existing for k in plan_keys(pl)):
                plans.append(pl)
        return plans

    requested_model_id = model_id_for(cfg, variant)
    todo = build_todo(requested_model_id)
    if not todo:
        ctx.heartbeat(progress=1.0,
                      msg=f"all {len(wrows)} window(s) already embedded"
                          f" ({requested_model_id})")
        return

    free = _free_vram_gb()
    if free is not None and free < MIN_EMBED_FREE_VRAM_GB:
        raise RuntimeError(
            f"insufficient_vram: free {free:.1f} GB <"
            f" {MIN_EMBED_FREE_VRAM_GB:.0f} GB (ViT-B needs no eviction"
            f" window -- something else is squatting; this job never evicts)")

    ctx.heartbeat(progress=0.02, msg="loading V-JEPA encoder")
    embedder = backbone.get_embedder(cfg)
    model_id = f"{embedder.model_id_base}@{variant}"
    if model_id != requested_model_id:
        log.warning("embedding under FALLBACK model_id %s (requested %s)",
                    model_id, requested_model_id)
        todo = build_todo(model_id)
        if not todo:
            ctx.heartbeat(progress=1.0, msg=f"already embedded ({model_id})")
            return

    writer = store.ShardWriter(cfg.data_dir, model_id)
    pending: list = []  # sqlite rows awaiting their shard flush
    n_done = 0
    next_flush = FLUSH_EVERY_WINDOWS

    def flush_pending():
        writer.flush()
        if pending:
            with db.tx(conn):
                for r in pending:
                    conn.execute(
                        "INSERT OR REPLACE INTO embeddings"
                        " (analysis_window_id, model_id, kind, clip_idx,"
                        " shard_path, row_index, dim, frames_json, cache_key,"
                        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", r)
            pending.clear()
            # resume point survives requeues; cache keys are the real skip
            q.checkpoint(conn, ctx.job_id,
                         {"model_id": model_id, "variant": variant,
                          "embedded": n_done, "of": len(todo)})

    batch = EMBED_BATCH_WINDOWS
    i = 0
    try:
        while i < len(todo):
            group = todo[i:i + batch]
            ctx.heartbeat(
                progress=0.05 + 0.9 * n_done / len(todo),
                msg=f"embedding {n_done}/{len(todo)} window(s), batch {batch}")
            clips: list = []
            for pl in group:
                clips.extend(_decode_window_clips(cfg, row, pl,
                                                  embedder.img_size))
            try:
                vecs = embedder.embed_clips(clips)
            except Exception as e:
                if _is_cuda_oom(e):
                    if batch > 1:
                        batch = max(1, batch // 2)
                        log.warning("CUDA OOM embedding %s -- halving window"
                                    " batch to %d", vid, batch)
                        continue
                    raise RuntimeError(
                        "CUDA OOM at window batch 1 -- free VRAM and retry"
                        " (this job never evicts)") from e
                raise
            if len(vecs) != len(clips):
                raise RuntimeError(f"encoder returned {len(vecs)} vectors"
                                   f" for {len(clips)} clips")
            now = db.iso_now()
            vi = 0
            for pl in group:
                cvecs = vecs[vi:vi + len(pl["clips"])]
                vi += len(pl["clips"])
                base_meta = {"crop_box": pl["crop_box"], "mode": pl["mode"],
                             "frame_step": pl["frame_step"],
                             "preproc": PREPROC_VERSION}
                for clip, vec in zip(pl["clips"], cvecs):
                    shard_path, row_idx = writer.add(vec)
                    pending.append((
                        pl["window_id"], model_id, "pooled_clip",
                        clip["clip_idx"], shard_path, row_idx, len(vec),
                        json.dumps({**base_meta,
                                    "clip_idx": clip["clip_idx"],
                                    "frame_indices": clip["frame_indices"],
                                    "timestamps": clip["timestamps"]},
                                   sort_keys=True),
                        clip["cache_key"], now))
                mean_vec = store.mean_vector(cvecs)
                shard_path, row_idx = writer.add(mean_vec)
                pending.append((
                    pl["window_id"], model_id, "pooled_window", 0,
                    shard_path, row_idx, len(mean_vec),
                    json.dumps({**base_meta, "n_clips": len(pl["clips"]),
                                "clip_frame_indices":
                                    [c["frame_indices"] for c in pl["clips"]]},
                               sort_keys=True),
                    pl["window_key"], now))
                n_done += 1
            i += len(group)
            if n_done >= next_flush:
                flush_pending()
                next_flush += FLUSH_EVERY_WINDOWS
    finally:
        # persist whatever finished -- a crash/cancel mid-video resumes via
        # cache keys instead of re-running the encoder over done windows
        flush_pending()

    ctx.heartbeat(progress=1.0,
                  msg=f"embedded {n_done}/{len(todo)} window(s)"
                      f" ({model_id}, {len(wrows)} total)")
    log.info("embed %s: %d window(s) embedded under %s (%d already present)",
             vid, n_done, model_id, len(wrows) - len(todo))
