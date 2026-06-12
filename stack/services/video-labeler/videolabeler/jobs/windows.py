"""windows job: materialize the immutable ANALYSIS-WINDOW grid for one video.

Grid contract (plan: SEGMENTS are canonical; this grid is machine-only and
never edited): 8s windows on a 4s stride over [0, duration]; the final
window is clamped to the video end and emission stops once a window reaches
it; a video shorter than 2s gets no windows. M2 fills person_presence /
motion_energy / pose_summary_json -- they stay NULL here. Deterministic ids
(aw_<video>_<start_ms>) + INSERT OR IGNORE keep re-runs duplicate-free, and
inserts commit in <=200-row chunks (two-writer rule: short transactions).
"""
from __future__ import annotations

import logging

from .. import db
from ..jobqueue import queue as q

log = logging.getLogger("videolabeler.jobs.windows")

WINDOW_S = 8.0
STRIDE_S = 4.0
MIN_WINDOW_S = 2.0
CHUNK_ROWS = 200


def grid(duration_s) -> list:
    """(start_s, end_s) pairs of the analysis grid. With the 8/4 geometry
    only a whole-video window can violate MIN_WINDOW_S (a clamped tail is
    always >= STRIDE_S once duration >= WINDOW_S), but the guard is kept
    explicit rather than implied."""
    if not duration_s or duration_s <= 0:
        return []
    out: list = []
    n = 0
    while True:
        start = n * STRIDE_S
        if start >= duration_s:
            break
        end = min(start + WINDOW_S, duration_s)
        if end - start + 1e-9 >= MIN_WINDOW_S:
            out.append((round(start, 3), round(end, 3)))
        if end >= duration_s:
            break
        n += 1
    return out


def window_id(video_id: str, start_s: float) -> str:
    return f"aw_{video_id}_{int(round(start_s * 1000))}"


def run(job, ctx) -> None:
    conn = ctx.conn
    vid = q.payload(job)["video_id"]
    row = ctx.get_video(vid)
    if row is None:
        raise RuntimeError(f"video {vid} not found")
    duration = row["duration_s"]
    if duration is None:
        raise RuntimeError(f"video {vid} has no duration -- probe must run first")

    windows = grid(duration)
    if not windows:
        ctx.heartbeat(progress=1.0,
                      msg=f"no windows (duration {duration:.1f}s < {MIN_WINDOW_S:.0f}s)")
        log.info("no analysis windows for %s (%.1fs)", vid, duration)
        return

    now = db.iso_now()
    inserted = 0
    ctx.heartbeat(progress=0.0, msg=f"grid: {len(windows)} windows")
    for i in range(0, len(windows), CHUNK_ROWS):
        chunk = windows[i:i + CHUNK_ROWS]
        with db.tx(conn):
            for start, end in chunk:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO analysis_windows (id, video_id,"
                    " start_s, end_s, prelabel_status, created_at)"
                    " VALUES (?, ?, ?, ?, 'pending', ?)",
                    (window_id(vid, start), vid, start, end, now))
                inserted += cur.rowcount
        ctx.heartbeat(progress=(i + len(chunk)) / len(windows),
                      msg=f"windows {i + len(chunk)}/{len(windows)}")
    log.info("analysis grid for %s: %d window(s), %d inserted (%d pre-existing)",
             vid, len(windows), inserted, len(windows) - inserted)
