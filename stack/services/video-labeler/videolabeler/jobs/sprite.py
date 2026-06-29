"""sprite job: scrub thumbnails as tiled JPEG sheets + manifest.

Geometry contract (frozen with the frontend): 160x90 tiles, 10 columns,
interval_s = max(1, ceil(duration/300)) so a video never exceeds 300 tiles;
sheets hold <= 100 tiles (10x10). The manifest lives in the artifact row's
meta_json; sheet count is taken from what ffmpeg actually wrote (truth beats
arithmetic on edge durations).
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from .. import db
from ..jobqueue import queue as q
from ..media import ffmpeg

log = logging.getLogger("videolabeler.jobs.sprite")

TILE_W, TILE_H, COLS, ROWS = 160, 90, 10, 10
MAX_TILES = 300


def run(job, ctx) -> None:
    conn = ctx.conn
    cfg = ctx.cfg
    vid = q.payload(job)["video_id"]
    row = ctx.get_video(vid)
    if row is None:
        raise RuntimeError(f"video {vid} not found")
    duration = row["duration_s"]
    if not duration or duration <= 0:
        raise RuntimeError(f"video {vid} has no duration — probe must run first")
    src = cfg.resolve(row["local_path"])

    interval = max(1, math.ceil(duration / MAX_TILES))
    count = min(MAX_TILES, int(duration // interval) + 1)
    outdir = Path(cfg.thumbs_dir) / vid
    outdir.mkdir(parents=True, exist_ok=True)

    ctx.heartbeat(progress=0.0, msg=f"sprite sheets (interval {interval}s)")
    vf = (f"fps=1/{interval},"
          f"scale={TILE_W}:{TILE_H}:force_original_aspect_ratio=decrease,"
          f"pad={TILE_W}:{TILE_H}:-1:-1,"
          f"tile={COLS}x{ROWS}")
    ffmpeg.run_ffmpeg(
        ["-i", src, "-vf", vf, "-q:v", "4", "-start_number", "0",
         str(outdir / "sheet_%d.jpg")],
        duration_s=duration,
        on_progress=lambda f: ctx.heartbeat(progress=f))

    n_sheets = len(list(outdir.glob("sheet_*.jpg")))
    if n_sheets == 0:
        raise RuntimeError(f"sprite generation produced no sheets for {vid}")
    count = min(count, n_sheets * COLS * ROWS)
    meta = {"tile_w": TILE_W, "tile_h": TILE_H, "cols": COLS,
            "interval_s": interval, "count": count, "sheets": n_sheets}
    now = db.iso_now()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO video_artifacts (video_id, kind, path, meta_json, status,"
            " created_at, updated_at) VALUES (?, 'sprite', ?, ?, 'ready', ?, ?)"
            " ON CONFLICT (video_id, kind) DO UPDATE SET path = excluded.path,"
            " meta_json = excluded.meta_json, status = 'ready', updated_at = excluded.updated_at",
            (vid, f"thumbs/{vid}", json.dumps(meta), now, now))
    log.info("sprite ready for %s: %d tiles over %d sheet(s)", vid, count, n_sheets)
