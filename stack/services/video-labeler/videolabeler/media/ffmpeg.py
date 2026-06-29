"""ffmpeg/ffprobe subprocess helpers + `-progress pipe:1` parsing.

Constraints: stderr is merged into stdout so a chatty encode can never
deadlock a PIPE (we read one stream); non-progress lines are kept as the
error tail. out_time_ms is historically MICROseconds in ffmpeg's progress
output — treated the same as out_time_us.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from collections import deque
from typing import Callable, Optional

_PROGRESS_KEYS = {
    "frame", "fps", "bitrate", "total_size", "out_time_us", "out_time_ms",
    "out_time", "dup_frames", "drop_frames", "speed", "progress",
}


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def ffprobe(path: str) -> dict:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed rc={res.returncode}: {res.stderr[-2000:]}")
    return json.loads(res.stdout)


def run_ffmpeg(args: list[str], duration_s: Optional[float] = None,
               on_progress: Optional[Callable[[float], None]] = None) -> None:
    """Run ffmpeg with the given input/output args; report 0..1 progress via
    on_progress when duration_s is known. Raises RuntimeError on rc != 0."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats",
           "-progress", "pipe:1", "-y", *[str(a) for a in args]]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace")
    noise: deque[str] = deque(maxlen=40)
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        key, sep, val = line.partition("=")
        if sep and key in _PROGRESS_KEYS:
            if key in ("out_time_us", "out_time_ms") and duration_s and on_progress:
                try:
                    frac = min(int(val) / 1e6 / duration_s, 1.0)
                except ValueError:
                    continue
                on_progress(frac)
        else:
            noise.append(line)
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg failed rc={rc}: {' | '.join(noise)[-2000:]}")


def first_video_stream(info: dict) -> Optional[dict]:
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            return s
    return None


def fps_of(stream: dict) -> Optional[float]:
    for key in ("avg_frame_rate", "r_frame_rate"):
        val = stream.get(key)
        if not val or val == "0/0":
            continue
        num, _, den = val.partition("/")
        try:
            d = float(den) if den else 1.0
            if d == 0:
                continue
            return round(float(num) / d, 4)
        except ValueError:
            continue
    return None


def rotation_of(stream: dict) -> int:
    """Display rotation in degrees (0/90/180/270) from side_data or tags."""
    rot = None
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            rot = sd["rotation"]
            break
    if rot is None:
        rot = (stream.get("tags") or {}).get("rotate")
    try:
        return (int(float(rot)) % 360 + 360) % 360
    except (TypeError, ValueError):
        return 0
