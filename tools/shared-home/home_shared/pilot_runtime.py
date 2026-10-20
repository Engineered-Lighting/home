"""Optional private/pilot-runtime.json: the LA camera pilot's warm-path switches.

Speed plan S1.1, S1.4 and S1.5. Absent, every default below applies. A present file
is exact: an unknown key, a wrong type or an out-of-bound value refuses the
start, like model-pin.json, so a rollback edit is never silently ignored.

    {"version": 1, "model_worker": "long_lived", "kernel_journal": "sudo",
     "health_max_age_seconds": 2, "kernel_scan_seconds": 30,
     "yield_enabled": true, "yield_running_threshold": 3,
     "live_shed_gpu_c": 77, "live_shed_tctl_c": 88, "live_stop_gpu_c": 79,
     "live_reopens_per_hour": 3, "ingress_requests_per_connection": 100}

- model_worker: "long_lived" (default) keeps one `docker exec -i <pinned>
  python3` worker per consumer (model_worker.py); "per_request" is the
  previous fresh `docker exec` per request, kept as the rollback.
- kernel_journal: "sudo" (default; the previous `sudo -n journalctl -k`) or
  "adm" (`journalctl -k` as the service user, through group adm, no sudo).
  "adm" needs the service user in group adm on the host.
- health_max_age_seconds: 0-3 (default 2). A request refreshes a cached health
  verdict older than this; 0 checks on every request, as before.
- kernel_scan_seconds: 0-30 (default 30). The kernel-fault scan runs at most
  this often; 0 scans on every health check, as before.

Live-scene consumer only (S1.4 yield and thermal ladder, S1.8 reopen); camera
chat never reads these:
- yield_enabled (default true): refuse a live request (429 queue_busy, before
  any ledger write) while vLLM has a waiting request or at least
  yield_running_threshold (1-16, default 3 for --max-num-seqs 4) running, or
  when its counts are older than 1 s and cannot be refreshed. Needs the
  long-lived live worker; with "per_request" it is off.
- live_shed_gpu_c (default 77) and live_shed_tctl_c (default 88): at or above
  either, live requests are refused 503 thermal_shed before any ledger write.
  live_stop_gpu_c (default 79): all live work stops (the same refusal, logged
  as its own rung). Heat never blocks an S1.8 reopen: every live request meets
  the ladder. GPU values are whole degrees, 40 <= shed <= stop <= 80 (above 80
  the unchanged per-request guard refuses camera chat too); Tctl is 50-100.
- live_reopens_per_hour: 0-3 (default 3). When the live worker proves that an
  unknown-outcome exchange ended, its row becomes terminal unknown_resolved
  and the live consumer may reopen at most this often per rolling hour. 0 is
  the previous rule: the row stays indeterminate and live stays closed.

The :9446 ingress (S1.5), camera chat and live alike:
- ingress_requests_per_connection: 1-100 (default 100). A connection is kept
  alive over HTTP/1.1 for at most this many requests (20 s idle). 1 is the
  rollback to one request per connection: every response says Connection:
  close, while a pre-opened camera-chat connection keeps its 20 s to send.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import json
import math
from pathlib import Path
from typing import Any

from .openai_backend import _constant, _object
from .service_config import protected_file

RUNTIME_NAME = "pilot-runtime.json"
MAX_RUNTIME_BYTES = 4096
MODEL_WORKERS = frozenset({"long_lived", "per_request"})
KERNEL_JOURNALS = frozenset({"sudo", "adm"})
MAX_HEALTH_AGE_SECONDS = 3
MAX_KERNEL_SCAN_SECONDS = 30
MAX_RUNNING_THRESHOLD = 16
GPU_BOUNDS_C = (40, 80)       # the unchanged camera guard refuses above 80
TCTL_BOUNDS_C = (50, 100)
MAX_REOPENS_PER_HOUR = 3      # the owner-approved cap (speed plan S1.8)
MAX_INGRESS_REQUESTS = 100    # per kept-alive connection (speed plan S1.5)


@dataclass(frozen=True)
class RuntimeOptions:
    model_worker: str = "long_lived"
    kernel_journal: str = "sudo"
    health_max_age_seconds: float = 2.0
    kernel_scan_seconds: float = 30.0
    yield_enabled: bool = True
    yield_running_threshold: int = 3
    live_shed_gpu_c: int = 77
    live_shed_tctl_c: float = 88.0
    live_stop_gpu_c: int = 79
    live_reopens_per_hour: int = 3
    ingress_requests_per_connection: int = 100


_FIELDS = frozenset(item.name for item in fields(RuntimeOptions))


def _seconds(value: Any, high: float) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= high


def _whole(value: Any, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def load_runtime_options(path: str | Path, *, enforce_linux: bool = False) -> RuntimeOptions:
    """Exact provisioning; the defaults when the file is absent."""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return RuntimeOptions()
    path = protected_file(path, enforce_linux=enforce_linux)
    with path.open("rb") as source:
        raw = source.read(MAX_RUNTIME_BYTES + 1)
    if len(raw) > MAX_RUNTIME_BYTES:
        raise ValueError("pilot runtime options exceed limit")
    value = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    if not isinstance(value, dict) or "version" not in value or set(value) - {"version"} - _FIELDS:
        raise ValueError("pilot runtime fields do not match the versioned schema")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("unsupported pilot runtime options")
    options = RuntimeOptions(**{key: item for key, item in value.items() if key != "version"})
    if (not isinstance(options.model_worker, str) or options.model_worker not in MODEL_WORKERS
            or not isinstance(options.kernel_journal, str) or options.kernel_journal not in KERNEL_JOURNALS
            or not _seconds(options.health_max_age_seconds, MAX_HEALTH_AGE_SECONDS)
            or not _seconds(options.kernel_scan_seconds, MAX_KERNEL_SCAN_SECONDS)
            or type(options.yield_enabled) is not bool
            or not _whole(options.yield_running_threshold, 1, MAX_RUNNING_THRESHOLD)
            or not _whole(options.live_shed_gpu_c, *GPU_BOUNDS_C)
            or not _whole(options.live_stop_gpu_c, *GPU_BOUNDS_C)
            or options.live_shed_gpu_c > options.live_stop_gpu_c
            or type(options.live_shed_tctl_c) not in (int, float) or not math.isfinite(options.live_shed_tctl_c)
            or not TCTL_BOUNDS_C[0] <= options.live_shed_tctl_c <= TCTL_BOUNDS_C[1]
            or not _whole(options.live_reopens_per_hour, 0, MAX_REOPENS_PER_HOUR)
            or not _whole(options.ingress_requests_per_connection, 1, MAX_INGRESS_REQUESTS)):
        raise ValueError("pilot runtime options outside their limits")
    return RuntimeOptions(options.model_worker, options.kernel_journal, float(options.health_max_age_seconds),
                          float(options.kernel_scan_seconds), options.yield_enabled, options.yield_running_threshold,
                          options.live_shed_gpu_c, float(options.live_shed_tctl_c), options.live_stop_gpu_c,
                          options.live_reopens_per_hour, options.ingress_requests_per_connection)
