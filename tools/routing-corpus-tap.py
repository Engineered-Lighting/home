#!/usr/bin/env python3
"""routing-corpus-tap.py — subscribe to the metrics-sidecar chat-tee
SSE feed and append every conversation completion as JSONL.

This gives me a secondary corroboration source for routing analysis.
The PRIMARY source is /config/external_routing.log on HAOS (which sees
ALL turns including external-routed ones). This tap captures the
richer local-turn detail that vLLM emits — assistant text, tool calls,
TTFT/duration — for the subset of turns that hit vLLM.

Usage:

    # one-shot — pull the recent buffer and exit
    python3 routing-corpus-tap.py --backfill 50

    # long-running — stream forever, append to JSONL
    python3 routing-corpus-tap.py

    # custom output file
    python3 routing-corpus-tap.py --out routing-corpus.jsonl

The output file rotates at 50 MB / 30 days (whichever first).

Operational hygiene:
  - Heartbeat to stderr every 60 s with running stats.
  - Exponential-backoff reconnect (1s → 30s cap) on disconnect.
  - JSONL on stdout-equivalent (configurable file) — NEVER mixed
    with diagnostic prints (those go to stderr).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


DEFAULT_URL  = "http://192.168.0.100:8092/conversations/stream"
DEFAULT_OUT  = Path(__file__).resolve().parent / "routing-corpus.jsonl"
ROTATE_BYTES = 50 * 1024 * 1024     # 50 MB
ROTATE_DAYS  = 30
HEARTBEAT_S  = 60.0
BACKOFF_MIN  = 1.0
BACKOFF_MAX  = 30.0


def _stderr(msg: str) -> None:
    """Print to stderr with a timestamp so heartbeats are time-ordered."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", file=sys.stderr, flush=True)


def _maybe_rotate(path: Path) -> None:
    """Rotate the output file if it exceeds size/age caps.

    Rotation = rename to .<ts>.jsonl + start fresh. Old rotated files
    older than 30 days are deleted.
    """
    if not path.exists():
        return
    try:
        st = path.stat()
        too_big = st.st_size > ROTATE_BYTES
        age_days = (time.time() - st.st_mtime) / 86400.0
        too_old = age_days > ROTATE_DAYS
        if too_big or too_old:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            rotated = path.with_suffix(f".{ts}.jsonl")
            path.rename(rotated)
            _stderr(f"rotated → {rotated.name} (size={st.st_size}, age={age_days:.1f}d)")
        # Prune ancient rotated siblings.
        for sibling in path.parent.glob(path.stem + ".*.jsonl"):
            try:
                age = (time.time() - sibling.stat().st_mtime) / 86400.0
                if age > ROTATE_DAYS:
                    sibling.unlink()
                    _stderr(f"pruned old rotation → {sibling.name}")
            except OSError:
                pass
    except OSError as e:
        _stderr(f"rotate check failed: {e}")


def _parse_sse(buf: str):
    """Iterator over (event_name, payload) tuples from a buffer. Yields
    only complete frames (separated by \\n\\n); leftover partial frame
    stays in the caller's buffer.

    Returns: (consumed_chars, list_of_frames)
    """
    frames = []
    while True:
        sep = buf.find("\n\n")
        if sep < 0:
            break
        frame = buf[:sep]
        buf = buf[sep + 2:]
        if not frame or frame.startswith(":"):
            continue  # heartbeat / comment
        event_name = "message"
        data_lines = []
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                v = line[5:]
                if v.startswith(" "):
                    v = v[1:]
                data_lines.append(v)
        if not data_lines:
            continue
        try:
            parsed = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            parsed = {"raw": "\n".join(data_lines)}
        frames.append((event_name, parsed))
    return buf, frames


def stream(url: str, out_path: Path, backfill: Optional[int]) -> int:
    """Connect to the SSE endpoint, parse frames, append JSONL. Returns
    exit code (only on one-shot --backfill).

    Long-running: never returns under normal operation; the caller
    Ctrl-Cs to stop.
    """
    if backfill is not None:
        target_url = f"{url}?backfill_n={backfill}"
    else:
        target_url = url

    backoff = BACKOFF_MIN
    events_total = 0
    bytes_total = 0
    last_heartbeat = time.monotonic()

    while True:
        try:
            _stderr(f"connecting to {target_url}")
            req = urllib.request.Request(target_url, headers={"Accept": "text/event-stream"})
            # Open with a long read timeout — the server sends pings
            # every 20s so a 30s no-data window means connection's gone.
            with urllib.request.urlopen(req, timeout=30) as resp:
                _stderr(f"connected (HTTP {resp.status})")
                backoff = BACKOFF_MIN
                buf = ""
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        _stderr("server closed connection")
                        break
                    bytes_total += len(chunk)
                    buf += chunk.decode("utf-8", errors="replace")
                    buf, frames = _parse_sse(buf)
                    if frames:
                        _maybe_rotate(out_path)
                        with out_path.open("a", encoding="utf-8") as f:
                            for event_name, payload in frames:
                                if event_name != "message":
                                    continue  # only write data frames
                                # Stamp our local receive time so the
                                # analyzer can correlate even if server
                                # ts is missing.
                                payload["_tap_ts"] = datetime.now(timezone.utc).isoformat()
                                f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
                                events_total += 1
                    # Heartbeat.
                    now = time.monotonic()
                    if now - last_heartbeat >= HEARTBEAT_S:
                        _stderr(f"heartbeat: {events_total} events seen, {bytes_total} bytes since start")
                        last_heartbeat = now
                    # One-shot mode terminates after the backfill.
                    if backfill is not None and events_total >= backfill:
                        _stderr(f"backfill done ({events_total} events) — exiting")
                        return 0
        except urllib.error.URLError as e:
            _stderr(f"URL error: {e.reason} — backoff {backoff:.1f}s")
        except KeyboardInterrupt:
            _stderr("stopped by user")
            return 0
        except Exception as e:  # noqa: BLE001
            _stderr(f"unexpected {type(e).__name__}: {e} — backoff {backoff:.1f}s")
        if backfill is not None:
            _stderr("backfill mode: not retrying")
            return 1
        time.sleep(backoff)
        backoff = min(backoff * 2, BACKOFF_MAX)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=DEFAULT_URL,
                    help=f"chat-tee SSE endpoint (default {DEFAULT_URL})")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help=f"JSONL output file (default {DEFAULT_OUT})")
    ap.add_argument("--backfill", type=int, default=None,
                    help="one-shot mode: pull the last N completions and exit")
    args = ap.parse_args()
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _stderr(f"output: {out_path}")
    return stream(args.url, out_path, args.backfill)


if __name__ == "__main__":
    raise SystemExit(main())
