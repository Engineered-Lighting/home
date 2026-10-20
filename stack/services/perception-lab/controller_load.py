"""Content-free controller load sampling for live tests.

Process CPU (time.process_time deltas, all threads, as a percentage of one
core), resident memory, and bounded server-side timing samples for a fixed set
of named phases. No payload, evidence, path or identifier is ever recorded:
only the fixed names below and numbers.
"""
from collections import deque
import math
import os
import threading
import time

# Fixed, controlled phase names (milliseconds, server side).
PHASES = (
    "live_handler_ms",      # Controller.live, whole handler
    "live_runtime_ms",      # its runtime.live() snapshot
    "live_room_ms",         # its room_at() lookup
    "graph_handler_ms",     # Controller.graph (sessions.graph)
    "reasoning_offer_ms",   # scheduler tick for one session: evidence read, keys, offer
    "reasoning_build_ms",   # JEV packet projection, build and egress admission
    "reasoning_send_ms",    # JEV provider transport round trip
)
MAX_SAMPLES = 512  # per phase per interval; the newest are kept
# Cadence of the 'controller.load' receipt while a session records.
LOAD_INTERVAL_S = 5.0


def _rss_bytes():
    try:
        with open("/proc/self/statm", "rb") as handle:
            pages = int(handle.read().split()[1])
        return pages*os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def _percentile(ordered, fraction):
    if not ordered:
        return None
    index = min(len(ordered)-1, max(0, math.ceil(fraction*len(ordered))-1))
    return round(ordered[index], 3)


class _Timer:
    """Context manager recording its elapsed milliseconds under one phase name."""
    __slots__ = ("monitor", "name", "started")

    def __init__(self, monitor, name):
        self.monitor, self.name, self.started = monitor, name, None

    def __enter__(self):
        self.started = time.perf_counter()
        return self

    def __exit__(self, *_):
        self.monitor.observe(self.name, (time.perf_counter()-self.started)*1000)
        return False


class LoadMonitor:
    def __init__(self, *, process_time=time.process_time, monotonic=time.monotonic, rss=_rss_bytes):
        self.process_time, self.monotonic, self.rss = process_time, monotonic, rss
        self.lock = threading.Lock()
        self.samples = {name: deque(maxlen=MAX_SAMPLES) for name in PHASES}
        self.counts = {name: 0 for name in PHASES}
        self._cpu, self._at = process_time(), monotonic()
        self.latest = None

    def observe(self, name, ms):
        if name not in self.samples or type(ms) not in (int, float) or not math.isfinite(ms) or ms < 0:
            return
        with self.lock:
            self.samples[name].append(float(ms))
            self.counts[name] += 1

    def timed(self, name):
        return _Timer(self, name)

    def sample(self):
        """Summarize the interval since the previous sample and start a new one."""
        cpu, at = self.process_time(), self.monotonic()
        with self.lock:
            samples = {name: sorted(values) for name, values in self.samples.items()}
            counts = dict(self.counts)
            for values in self.samples.values():
                values.clear()
            for name in self.counts:
                self.counts[name] = 0
            elapsed, used = at-self._at, cpu-self._cpu
            self._cpu, self._at = cpu, at
        rss = self.rss()
        summary = {
            "interval_s": round(elapsed, 3),
            "cpu_s": round(max(used, 0.0), 4),
            "cpu_pct_1core": round(100*max(used, 0.0)/elapsed, 1) if elapsed > 0 else None,
            "rss_mb": round(rss/1024**2, 1) if isinstance(rss, int) else None,
            "phases": {name: {"n": counts[name], "p50": _percentile(values, .5), "p95": _percentile(values, .95),
                              "max": round(values[-1], 3) if values else None}
                       for name, values in samples.items()},
        }
        self.latest = summary
        return summary

    def live_view(self):
        """The latest sample, flattened to the fields live() exposes."""
        latest = self.latest
        if latest is None:
            return None
        phases = latest["phases"]
        return {"cpu_pct_1core": latest["cpu_pct_1core"], "rss_mb": latest["rss_mb"],
                "live_handler_ms_p50": phases["live_handler_ms"]["p50"],
                "graph_handler_ms_p50": phases["graph_handler_ms"]["p50"],
                "reasoning_build_ms_p50": phases["reasoning_build_ms"]["p50"]}


# One controller process, one monitor; components record into it by name.
MONITOR = LoadMonitor()


def observe(name, ms):
    MONITOR.observe(name, ms)


def timed(name):
    return MONITOR.timed(name)
