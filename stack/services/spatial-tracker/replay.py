"""Track-frame recording + replay.

Every outbound WS frame is appended as one JSON line to
``{DATA_DIR}/recordings/YYYYMMDD.ndjson``.  Files rotate daily and files
older than 14 days are deleted.  ``GET /replay/sessions`` lists files;
``WS /ws/tracks?replay=YYYYMMDD`` streams a file at real-time pacing (using
the recorded ``ts`` deltas), looped.

By default consecutive *empty* frames (no tracks) are deduplicated to keep
file sizes sane at the 10 Hz broadcast rate — set ``RECORD_DEDUP_EMPTY=0``
for strict every-frame recording.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("spatial.replay")

RETAIN_DAYS = 14
DATE_RE = re.compile(r"^\d{8}$")
MAX_PACING_SLEEP_S = 5.0


class FrameRecorder:
    def __init__(self, data_dir: str | Path, retain_days: int = RETAIN_DAYS,
                 clock: Callable[[], float] = time.time,
                 dedup_empty: bool = True):
        self.dir = Path(data_dir) / "recordings"
        self.retain_days = retain_days
        self.clock = clock
        self.dedup_empty = dedup_empty
        self._current_date: Optional[str] = None
        self._fh = None
        self._last_was_empty = False

    def _date_str(self) -> str:
        return datetime.fromtimestamp(self.clock()).strftime("%Y%m%d")

    def append(self, frame: dict) -> None:
        if self.dedup_empty:
            empty = not frame.get("tracks")
            if empty and self._last_was_empty:
                return
            self._last_was_empty = empty
        d = self._date_str()
        if d != self._current_date:
            self._rotate(d)
        if self._fh is None:
            return
        try:
            self._fh.write(json.dumps(frame, separators=(",", ":")) + "\n")
            self._fh.flush()
        except Exception:
            log.exception("failed to append frame to recording")

    def _rotate(self, date_str: str) -> None:
        self.close()
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.dir / f"{date_str}.ndjson", "a", encoding="utf-8")
            self._current_date = date_str
        except Exception:
            log.exception("failed to open recording file for %s", date_str)
            self._fh = None
            self._current_date = None
            return
        self.cleanup()

    def cleanup(self) -> None:
        if not self.dir.is_dir():
            return
        cutoff = datetime.fromtimestamp(self.clock()) - timedelta(days=self.retain_days)
        for f in self.dir.glob("*.ndjson"):
            stem = f.stem
            if not DATE_RE.match(stem):
                continue
            try:
                if datetime.strptime(stem, "%Y%m%d") < cutoff:
                    f.unlink()
                    log.info("deleted old recording %s", f.name)
            except (ValueError, OSError):
                continue

    def sessions(self) -> list[dict]:
        if not self.dir.is_dir():
            return []
        out = []
        for f in sorted(self.dir.glob("*.ndjson")):
            if not DATE_RE.match(f.stem):
                continue
            try:
                size = f.stat().st_size
            except OSError:
                size = None
            out.append({"date": f.stem, "file": f.name, "size_bytes": size})
        return out

    def path_for(self, date_str: str) -> Optional[Path]:
        if not DATE_RE.match(date_str or ""):
            return None
        p = self.dir / f"{date_str}.ndjson"
        return p if p.is_file() else None

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None


async def stream_replay(path: Path, send_text: Callable,
                        sleep=asyncio.sleep, loop_forever: bool = True) -> None:
    """Stream one ndjson recording with real-time pacing, looped.

    ``send_text`` is an async callable taking the raw JSON line (e.g.
    ``websocket.send_text``).  Frames are re-stamped relative to *now* so
    clients can treat replay frames like live ones.
    """
    while True:
        prev_ts: Optional[float] = None
        sent_any = False
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    frame = json.loads(line)
                except ValueError:
                    continue
                ts = frame.get("ts")
                if prev_ts is not None and isinstance(ts, (int, float)):
                    delta = max(0.0, min(float(ts) - prev_ts, MAX_PACING_SLEEP_S))
                    if delta:
                        await sleep(delta)
                if isinstance(ts, (int, float)):
                    prev_ts = float(ts)
                frame["replay"] = True
                frame["replay_ts"] = ts
                frame["ts"] = time.time()
                await send_text(json.dumps(frame, separators=(",", ":")))
                sent_any = True
        if not loop_forever:
            return
        if not sent_any:
            await sleep(1.0)  # empty file; avoid a hot loop
