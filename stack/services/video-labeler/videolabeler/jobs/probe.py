"""probe job: ffprobe the original, persist stream facts, fan out proxy +
sprite jobs (idempotency-keyed so re-probing never duplicates them)."""
from __future__ import annotations

import logging

from .. import db
from ..jobqueue import queue as q
from ..media import ffmpeg

log = logging.getLogger("videolabeler.jobs.probe")


def run(job, ctx) -> None:
    conn = ctx.conn
    vid = q.payload(job)["video_id"]
    row = ctx.get_video(vid)
    if row is None:
        raise RuntimeError(f"video {vid} not found")
    path = ctx.cfg.resolve(row["local_path"])

    ctx.heartbeat(progress=0.1, msg="ffprobe")
    info = ffmpeg.ffprobe(path)
    vstream = ffmpeg.first_video_stream(info)
    if vstream is None:
        raise RuntimeError(f"no video stream in {row['filename']}")
    fmt = info.get("format", {})
    duration = float(fmt.get("duration") or vstream.get("duration") or 0.0)
    rotation = ffmpeg.rotation_of(vstream)
    # rotated 90/270 sources display transposed; keep stored w/h as-encoded
    with db.tx(conn):
        conn.execute(
            "UPDATE videos SET duration_s = ?, fps = ?, codec = ?, width = ?,"
            " height = ?, rotation_deg = ?, import_status = 'probed', updated_at = ?"
            " WHERE id = ?",
            (duration, ffmpeg.fps_of(vstream), vstream.get("codec_name"),
             vstream.get("width"), vstream.get("height"), rotation,
             db.iso_now(), vid))

    q.enqueue(conn, "proxy", {"video_id": vid}, lane="cpu",
              idempotency_key=f"proxy:{vid}:480p")
    q.enqueue(conn, "sprite", {"video_id": vid}, lane="cpu",
              idempotency_key=f"sprite:{vid}")
    ctx.heartbeat(progress=1.0, msg=f"probed {duration:.1f}s, fanned out proxy+sprite")
    log.info("probed %s: %.1fs %sx%s rot=%d", vid, duration,
             vstream.get("width"), vstream.get("height"), rotation)
