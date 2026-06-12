"""proxy job: rotation-normalized 480p h264 +faststart preview.

Constraints: ffmpeg autorotates on transcode by default (display-matrix
applied, rotate metadata dropped) so the stored proxy is physically upright;
scale=-2:480 keeps AR with even dims; write .part + atomic rename so a crash
never leaves a half-written playable path; '-f mp4' is explicit because the
.part suffix defeats extension-based muxer inference; '-map 0:a:0?' keeps
audio only when the source has it.
"""
from __future__ import annotations

import json
import logging
import os

from .. import db
from ..jobqueue import queue as q
from ..media import ffmpeg

log = logging.getLogger("videolabeler.jobs.proxy")


def run(job, ctx) -> None:
    conn = ctx.conn
    cfg = ctx.cfg
    vid = q.payload(job)["video_id"]
    row = ctx.get_video(vid)
    if row is None:
        raise RuntimeError(f"video {vid} not found")
    src = cfg.resolve(row["local_path"])
    rel_out = f"proxies/{vid}.mp4"
    out = cfg.resolve(rel_out)
    part = out + ".part"
    os.makedirs(os.path.dirname(out), exist_ok=True)

    ctx.heartbeat(progress=0.0, msg="encoding 480p proxy")
    ffmpeg.run_ffmpeg(
        ["-i", src,
         "-map", "0:v:0", "-map", "0:a:0?",
         "-vf", "scale=-2:480",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
         "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "96k",
         "-movflags", "+faststart",
         "-f", "mp4", part],
        duration_s=row["duration_s"],
        on_progress=lambda f: ctx.heartbeat(progress=f))
    os.replace(part, out)

    now = db.iso_now()
    meta = json.dumps({"height": 480, "crf": 26, "preset": "veryfast"})
    with db.tx(conn):
        conn.execute(
            "INSERT INTO video_artifacts (video_id, kind, path, meta_json, status,"
            " created_at, updated_at) VALUES (?, 'proxy480', ?, ?, 'ready', ?, ?)"
            " ON CONFLICT (video_id, kind) DO UPDATE SET path = excluded.path,"
            " meta_json = excluded.meta_json, status = 'ready', updated_at = excluded.updated_at",
            (vid, rel_out, meta, now, now))
        conn.execute(
            "UPDATE videos SET import_status = 'ready', updated_at = ? WHERE id = ?",
            (now, vid))
    log.info("proxy ready for %s -> %s", vid, rel_out)
