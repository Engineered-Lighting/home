#!/usr/bin/env python3
"""Normalise exported Victoria -> LA timings into latency samples (plan S0.2).

Workstation only: Python 3.10+, standard library only. Every input is a file
the owner already exported from a host. Nothing here opens a network
connection, and the optional Lab ledger is opened read-only and immutable.

Output is JSONL, one sample per line, with exactly these keys:

    pipeline, metric, value_s, t, source, method

value_s is in seconds, except for COUNT_METRICS (a dimensionless count or
fraction) and flag metrics (FLAG_METRICS, or any metric starting "event:" or
"outcome:"; the value is 1 or 0). t is ISO-8601 UTC with milliseconds. source
is "<kind>:<file basename>:<line or row>"; method says how the value was
derived. Negative durations are dropped and counted as notes.

Inputs, one subcommand each:

  la-journal  LA journalctl exports (-o json, -o short-iso, -o short-iso-precise).
              "live scene backend seconds=%.3f outcome=%s" (lab_live_pilot.py:384)
              and "camera pilot backend seconds=%.3f result=%s" (lab_live_pilot.py:500,
              labelled at camera_pilot_service.py:176), with or without the S0.4
              suffix " health_ms=<int> exec_ms=<int> total_ms=<int>"; plus the
              pilot's alarm lines as "event:" flags. A timing line whose journal
              time has no fraction (-o short-iso) gets the method suffix
              WHOLE_SECOND: its time can be up to 1 s early, so report.py leaves
              it out of per-request pairing.
  vllm        vLLM Prometheus text-exposition dumps, each starting with a
              "# harvested_at <ISO-8601>" comment (or --at for a single dump).
  ledger      A Lab ledger snapshot (lab.sqlite3), opened only with
              mode=ro&immutable=1. Queries: LEDGER_QUERIES.
  browser     Camera-chat performance marks exported from the den browser.
  jetson      Published caption JSON (caption_publication.py) from the Jetson:
              frame observed to caption published, on the Jetson clock.
  fact-log    la-fact-log-1 JSONL from the always-on Qwen unit (plan section 15).

voice_probe.py (S1.4, run on LA) writes this same format directly: voice_probe
ttft/e2e/outcome, la_gpu gpu_temp_c, la_cpu tctl_c and vllm gauges.
"""
import argparse
import json
import math
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SAMPLE_KEYS = ("pipeline", "metric", "value_s", "t", "source", "method")
COUNT_METRICS = frozenset({"num_requests_running", "num_requests_waiting", "resent_frame_fraction",
                           "body_bytes", "upstream_mbps", "gpu_temp_c", "tctl_c"})
FLAG_METRICS = frozenset({"stale_flag", "queue_busy_flag", "la_supplied_flag"})

# LA pilot lines, exact current text. The S0.4 timing suffix is optional.
_SUFFIX = r"(?: health_ms=(?P<health>\d+) exec_ms=(?P<exec>\d+) total_ms=(?P<total>\d+))?\s*$"
LIVE_LINE = re.compile(r"live scene backend seconds=(?P<seconds>\d+(?:\.\d+)?) outcome=(?P<outcome>\S+)" + _SUFFIX)
CAMERA_LINE = re.compile(r"camera pilot backend seconds=(?P<seconds>\d+(?:\.\d+)?) result=(?P<result>\S+)" + _SUFFIX)
LIVE_COMPLETED = frozenset({"completed", "completed_after_deadline"})
WHOLE_SECOND = "_whole_second_time"
EVENT_LINES = (
    ("live scene execution unresolved: ", "event:live_execution_unresolved"),   # lab_live_pilot.py:582
    ("live scene ledger unavailable: ", "event:live_ledger_unavailable"),       # lab_live_pilot.py:603, :641
    ("live scene consumer disabled: ", "event:live_consumer_disabled"),         # lab_live_pilot.py:947
    ("live scene preflight rejected: ", "event:live_preflight_rejected"),       # lab_live_pilot.py:556, :826, :856
    ("live scene admission closed: ", "event:live_admission_closed"),           # lab_live_pilot.py:332
    # S1.8: an unknown outcome the reconcile could not prove ended, a reopen
    # refused (hourly limit or another close reason), a reopen and its deferral.
    ("live scene unknown outcome unresolved: ", "event:live_unknown_outcome_unresolved"),  # lab_live_pilot.py:632
    ("live scene admission stays closed: ", "event:live_admission_stays_closed"),          # lab_live_pilot.py:708
    ("live scene admission reopened: ", "event:live_admission_reopened"),                  # lab_live_pilot.py:710
    ("live scene admission reopen deferred: ", "event:live_reopen_deferred"),              # lab_live_pilot.py:706
    # S1.4: a change of thermal rung, logged once per change ("level=shed",
    # "level=stop" or "cleared").
    ("live scene thermal_shed ", "event:live_thermal_rung_changed"),           # lab_live_pilot.py:502
    ("camera pilot execution unresolved: ", "event:camera_execution_unresolved"),  # camera_pilot.py:189
    ("camera pilot preflight rejected: ", "event:camera_preflight_rejected"),   # camera_pilot_service.py:210, :247
    # S0.4(d): the pilot is down until vLLM is re-pinned, or it refused to start.
    ("vLLM StartedAt changed; pilot refusing", "event:vllm_started_at_changed"),  # model_pin.py:265
    ("camera pilot refusing to start: ", "event:pilot_refused_start"),          # camera_pilot_service.py:252
    ("engine re-pinned ", "event:engine_repinned"),                             # model_pin.py:257
)

# vLLM V1 metric names. Only the TTFT family is referenced in this repo
# (stack/services/metrics-sidecar/main.py:237-239); the rest are vLLM's
# documented V1 names, to be confirmed on a dump from the pinned image.
VLLM_HISTOGRAMS = {"vllm:time_to_first_token_seconds": "ttft",
                   "vllm:e2e_request_latency_seconds": "e2e",
                   "vllm:request_queue_time_seconds": "queue_time"}
VLLM_GAUGES = {"vllm:num_requests_running": "num_requests_running",
               "vllm:num_requests_waiting": "num_requests_waiting"}
MAX_EXPANDED = 200000

# Read-only queries over the perception-lab schema: events/sessions
# (store.py:117-127); qwen.observation (qwen_scene.py:268-287);
# vjepa.observation (vjepa_scene.py:254-276); vjepa.lease (vjepa_lease.py:85-87);
# perception.delivery (runtime.py:305-323, remote_specialist.py:107).
# Only timing fields are selected; fixture sessions are excluded.
LEDGER_QUERIES = {
    "qwen": """SELECT e.id, json_extract(e.payload,'$.observed_at'), json_extract(e.payload,'$.available_at')
        FROM events e JOIN sessions s ON s.id=e.session_id
        WHERE e.kind='qwen.observation' AND s.fixture=0 ORDER BY e.id""",
    "vjepa": """SELECT e.id, e.session_id, json_extract(e.payload,'$.observed_at'),
        json_extract(e.payload,'$.available_at'), json_extract(e.payload,'$.result.timings_ms.total'),
        json_extract(e.payload,'$.media_ids')
        FROM events e JOIN sessions s ON s.id=e.session_id
        WHERE e.kind='vjepa.observation' AND s.fixture=0 ORDER BY e.session_id, e.id""",
    "lease": """SELECT e.id, json_extract(e.payload,'$.request_id'), json_extract(e.payload,'$.state'),
        json_extract(e.payload,'$.available_at')
        FROM events e JOIN sessions s ON s.id=e.session_id
        WHERE e.kind='vjepa.lease' AND s.fixture=0 ORDER BY e.id""",
    "delivery": """SELECT e.id, json_extract(e.payload,'$.arm'), json_extract(e.payload,'$.state'),
        json_extract(e.payload,'$.current'), json_extract(e.payload,'$.observed_at'),
        json_extract(e.payload,'$.completed_at'), json_extract(e.payload,'$.dispatched_at'),
        json_extract(e.payload,'$.transport_ms')
        FROM events e JOIN sessions s ON s.id=e.session_id
        WHERE e.kind='perception.delivery' AND s.fixture=0 ORDER BY e.id""",
}

DEFAULT_START_MARK = "camera-chat:question"
DEFAULT_END_MARK = "camera-chat:answer"
DEFAULT_MEASURE = "camera-chat:question-to-answer"

_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:[.,](\d{1,9}))?(Z|[+-]\d{2}:?\d{2})?")
_JOURNAL_ISO = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?(?:Z|[+-]\d{2}:?\d{2}))\s")


class InputError(ValueError):
    """An input this tool cannot read without guessing."""


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def parse_time(text):
    """Epoch seconds from ISO-8601 with an explicit offset (3.10-safe: no 'Z'
    or '-0700' reliance on datetime.fromisoformat)."""
    match = _ISO.fullmatch(text.strip()) if isinstance(text, str) else None
    if match is None:
        raise InputError("ISO-8601 timestamp required: %r" % (text,))
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    micro = int(((match.group(7) or "") + "000000")[:6])
    zone = match.group(8)
    if zone is None:
        raise InputError("timestamp needs a UTC offset: %r" % (text,))
    offset = 0
    if zone != "Z":
        digits = zone[1:].replace(":", "")
        offset = (int(digits[:2]) * 3600 + int(digits[2:]) * 60) * (-1 if zone[0] == "-" else 1)
    return datetime(year, month, day, hour, minute, second, micro,
                    tzinfo=timezone(timedelta(seconds=offset))).timestamp()


def format_time(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Harvest:
    """Collects samples and notes (dropped or unreadable inputs)."""

    def __init__(self):
        self.samples, self.notes = [], []

    def add(self, pipeline, metric, value, t, source, method):
        if not finite(value) or not finite(t):
            self.notes.append("%s: non-finite %s/%s dropped" % (source, pipeline, metric))
            return
        if value < 0:
            self.notes.append("%s: negative %s/%s dropped (clock order)" % (source, pipeline, metric))
            return
        self.samples.append({"pipeline": pipeline, "metric": metric, "value_s": round(float(value), 6),
                             "t": format_time(t), "source": source, "method": method})

    # -- LA journal -------------------------------------------------------

    def la_journal(self, lines, name):
        for t, message, number, whole in journal_records(lines, name):
            source = "la-journal:%s:%d" % (name, number)
            live, camera = LIVE_LINE.search(message), CAMERA_LINE.search(message)
            suffix = WHOLE_SECOND if whole else ""
            if live:
                self._backend("qwen_live_la", live, live.group("outcome") in LIVE_COMPLETED, t, source, suffix)
                self.add("qwen_live_la", "outcome:" + live.group("outcome"), 1, t, source, "la_journal_line")
            elif camera:
                self._backend("camera_chat_la", camera, camera.group("result") == "ok", t, source, suffix)
            else:
                for prefix, metric in EVENT_LINES:
                    if prefix in message:
                        self.add("la_pilot", metric, 1, t, source, "la_journal_line")
                        break

    def _backend(self, pipeline, match, ok, t, source, suffix=""):
        self.add(pipeline, "backend" if ok else "backend_other", float(match.group("seconds")), t, source,
                 "la_journal_backend_seconds" + suffix)
        if match.group("total") is not None:
            for metric in ("health", "exec", "total"):
                self.add(pipeline, metric if ok else metric + "_other", int(match.group(metric)) / 1000, t, source,
                         "la_journal_timing_ms" + suffix)

    # -- vLLM Prometheus dumps -------------------------------------------

    def vllm(self, dumps, at=None):
        """dumps: [(name, text)]. Histograms become per-request samples at their
        bucket's upper bound, from the delta between consecutive dumps (or the
        cumulative counts when there is a single dump)."""
        parsed = []
        for name, text in dumps:
            stamp, buckets, gauges = parse_exposition(text)
            if stamp is None:
                if at is None or len(dumps) != 1:
                    raise InputError("%s: '# harvested_at <ISO-8601>' header required (--at only for one dump)" % name)
                stamp = parse_time(at)
            if not buckets and not gauges:
                self.notes.append("%s: no known vLLM metric families (expected %s)" % (
                    name, ", ".join(sorted(VLLM_HISTOGRAMS) + sorted(VLLM_GAUGES))))
            parsed.append((stamp, name, buckets, gauges))
        parsed.sort(key=lambda item: item[0])
        for stamp, name, _, gauges in parsed:
            for family, metric in VLLM_GAUGES.items():
                if family in gauges:
                    self.add("vllm", metric, gauges[family], stamp, "vllm:%s:%s" % (name, family), "vllm_gauge")
        if len(parsed) == 1:
            stamp, name, buckets, _ = parsed[0]
            self._expand(buckets, {}, stamp, name, "vllm_histogram_cumulative_bucket_upper_bound")
        for previous, current in zip(parsed, parsed[1:]):
            self._expand(current[2], previous[2], current[0], current[1], "vllm_histogram_delta_bucket_upper_bound")

    def _expand(self, buckets, before, stamp, name, method):
        for family, metric in VLLM_HISTOGRAMS.items():
            now = buckets.get(family)
            if not now:
                continue
            old = before.get(family) or {}
            if any(now.get(le, 0) < old.get(le, 0) for le in old):
                old = {}  # counter reset: vLLM restarted between dumps
                self.notes.append("%s: %s counters reset; counted from zero" % (name, family))
            bounds = sorted(now, key=lambda le: math.inf if le == "+Inf" else float(le))
            finite_bounds = [le for le in bounds if le != "+Inf"]
            if now[bounds[-1]] - old.get(bounds[-1], 0) > MAX_EXPANDED:
                raise InputError("%s: more than %d %s requests; harvest consecutive dumps" % (name, MAX_EXPANDED, family))
            below = 0
            for le in bounds:
                cumulative = now[le] - old.get(le, 0)
                count, below = int(round(cumulative - below)), cumulative
                if count <= 0:
                    continue
                if le == "+Inf":
                    if not finite_bounds:
                        continue
                    value, how = float(finite_bounds[-1]), method + "_overflow"
                else:
                    value, how = float(le), method
                for _ in range(count):
                    self.add("vllm", metric, value, stamp, "vllm:%s:%s" % (name, family), how)

    # -- Lab ledger -------------------------------------------------------

    def ledger(self, path, la_arm=None):
        name = Path(path).name
        db = open_ledger(path)
        try:
            for row_id, observed, available in db.execute(LEDGER_QUERIES["qwen"]):
                if finite(observed) and finite(available):
                    self.add("qwen_live_victoria", "newest_frame_to_fact", available - observed, available,
                             "ledger:%s:events/%d" % (name, row_id), "lab_ledger_qwen_observation_available_minus_observed")
            last = {}
            for row_id, session, observed, available, worker_ms, media in db.execute(LEDGER_QUERIES["vjepa"]):
                source = "ledger:%s:events/%d" % (name, row_id)
                if not (finite(observed) and finite(available)):
                    continue
                age = available - observed
                self.add("vjepa_live", "newest_frame_to_fact", age, available, source,
                         "lab_ledger_vjepa_observation_available_minus_observed")
                if finite(worker_ms):
                    self.add("vjepa_live", "worker_total", worker_ms / 1000, available, source,
                             "lab_ledger_vjepa_timings_ms_total")
                    self.add("vjepa_live", "overhead_upper_bound", age - worker_ms / 1000, available, source,
                             "newest_frame_to_fact_minus_worker_total_includes_frame_age_at_send")
                frames = _json_list(media)
                if frames is not None and frames:
                    if session in last:
                        shared = len(set(frames) & set(last[session]))
                        self.add("vjepa_live", "resent_frame_fraction", shared / len(frames), available, source,
                                 "lab_ledger_vjepa_media_ids_shared_with_previous_clip_frames_as_bytes_proxy")
                    last[session] = frames
            started = {}
            for row_id, request_id, state, available in db.execute(LEDGER_QUERIES["lease"]):
                if not isinstance(request_id, str) or not finite(available):
                    continue
                if state == "requesting":
                    started[request_id] = available
                elif state == "ready" and request_id in started:
                    self.add("vjepa_lease", "requesting_to_ready", available - started.pop(request_id), available,
                             "ledger:%s:events/%d" % (name, row_id), "lab_ledger_vjepa_lease_ready_minus_requesting")
            for row in db.execute(LEDGER_QUERIES["delivery"]):
                self._delivery(row, name, la_arm)
        finally:
            db.close()

    def _delivery(self, row, name, la_arm):
        row_id, arm, state, current, observed, completed, dispatched, transport_ms = row
        if not isinstance(arm, str) or state != "completed" or not finite(completed):
            return
        arm = arm.lower()
        pipeline = ("detector_arm_" + arm) if la_arm is None else ("la_detector" if arm == la_arm else "jetson_detector")
        source = "ledger:%s:events/%d" % (name, row_id)
        if finite(observed):
            self.add(pipeline, "capture_to_fact", completed - observed, completed, source,
                     "lab_ledger_perception_delivery_completed_minus_observed")
        if finite(dispatched):
            self.add(pipeline, "dispatch_to_fact", completed - dispatched, completed, source,
                     "lab_ledger_perception_delivery_completed_minus_dispatched")
        if finite(transport_ms):
            self.add(pipeline, "transport", transport_ms / 1000, completed, source,
                     "lab_ledger_perception_delivery_transport_ms")
        if current in (0, 1, True, False):
            self.add(pipeline, "stale_flag", 0 if current else 1, completed, source,
                     "lab_ledger_perception_delivery_not_current")

    # -- Browser marks ----------------------------------------------------

    def browser(self, document, name, start=DEFAULT_START_MARK, end=DEFAULT_END_MARK, measure=DEFAULT_MEASURE):
        if isinstance(document, list):
            origin, entries = None, document
        elif isinstance(document, dict) and isinstance(document.get("entries"), list):
            origin, entries = document.get("timeOrigin"), document["entries"]
        else:
            raise InputError("%s: expected a list of performance entries or {timeOrigin, entries}" % name)
        if not finite(origin):
            raise InputError("%s: timeOrigin (ms since the epoch) required to date the marks" % name)
        starts, ends, open_starts = {}, [], []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not finite(entry.get("startTime")):
                continue
            label, kind, at = entry["name"], entry.get("entryType"), entry["startTime"]
            if kind == "measure" and label == measure and finite(entry.get("duration")):
                self.add("camera_chat_browser", "question_to_answer", entry["duration"] / 1000,
                         (origin + at + entry["duration"]) / 1000, "browser:%s:%d" % (name, index),
                         "browser_performance_measure")
            elif kind == "mark":
                key = _mark_key(label, start)
                if key:
                    starts[key] = at
                elif key == "":
                    open_starts.append(at)
                key = _mark_key(label, end)
                if key is not None:
                    ends.append((at, key, index))
        for at, key, index in sorted(ends):
            if key:
                began = starts.pop(key, None)
            else:
                prior = [s for s in open_starts if s <= at]
                began = prior[-1] if prior else None
                if began is not None:
                    open_starts.remove(began)
            if began is None:
                self.notes.append("browser:%s:%d: answer mark without a question mark" % (name, index))
                continue
            self.add("camera_chat_browser", "question_to_answer", (at - began) / 1000, (origin + at) / 1000,
                     "browser:%s:%d" % (name, index), "browser_performance_mark_pair")

    # -- Jetson caption publication ---------------------------------------

    def jetson(self, objects, name, seen=None):
        """objects: [(index, dict)] of published captions; text is never read."""
        seen = set() if seen is None else seen
        for index, value in objects:
            if not isinstance(value, dict) or value.get("schema_version") != 1:
                continue
            observed, generated, request = value.get("observed_at"), value.get("generated_at"), value.get("request_id")
            if not (finite(observed) and finite(generated)) or (request, observed) in seen:
                continue
            seen.add((request, observed))
            self.add("camera_chat_jetson", "frame_to_caption", generated - observed, generated,
                     "jetson:%s:%d" % (name, index), "caption_publication_generated_minus_observed")

    # -- la-fact-log-1 ----------------------------------------------------

    def fact_log(self, lines, name):
        for number, raw in enumerate(lines, 1):
            if not raw.strip():
                continue
            source = "fact-log:%s:%d" % (name, number)
            try:
                row = json.loads(raw)
            except ValueError:
                raise InputError("%s: not JSON" % source) from None
            if not isinstance(row, dict) or row.get("schema") != "la-fact-log-1":
                raise InputError("%s: schema la-fact-log-1 required" % source)
            if row.get("source_id") != "la_qwen_den_oak":
                continue
            observed, sent, available = (parse_time(row.get(k)) for k in ("observed_at", "sent_at", "available_at"))
            latency = row.get("latency_s")
            if finite(latency) and abs(latency - (available - observed)) > 0.002:
                self.notes.append("%s: latency_s differs from available_at - observed_at; timestamps used" % source)
            self.add("qwen_live_victoria", "request_to_fact", available - sent, available, source,
                     "fact_log_available_minus_sent_jetson_clock")
            self.add("qwen_live_victoria", "newest_frame_to_fact", available - observed, available, source,
                     "fact_log_available_minus_observed_jetson_clock")
            self.add("qwen_live_victoria", "frame_age_at_send", sent - observed, available, source,
                     "fact_log_sent_minus_observed_jetson_clock")
            if isinstance(row.get("outcome"), str):
                self.add("qwen_live_victoria", "outcome:" + row["outcome"], 1, available, source, "fact_log_outcome")


def _mark_key(label, base):
    """'' for an unkeyed mark, the id for 'base:<id>', None for another mark."""
    if label == base:
        return ""
    if label.startswith(base + ":") and len(label) > len(base) + 1:
        return label[len(base) + 1:]
    return None


def _json_list(value):
    try:
        items = json.loads(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return [i for i in items if isinstance(i, str)] if isinstance(items, list) else None


def journal_records(lines, name):
    """(epoch, message, line number, whole_second) from a journalctl export.
    whole_second is True for a time without a fraction (-o short-iso).
    Year-less '-o short' lines that carry a timing or alarm line are refused:
    their time cannot be placed without guessing."""
    for number, raw in enumerate(lines, 1):
        line = raw.rstrip("\r\n")
        if not line.strip() or line.startswith("-- "):
            continue
        if line.lstrip().startswith("{"):
            try:
                entry = json.loads(line)
            except ValueError:
                raise InputError("%s:%d: unreadable journal JSON" % (name, number)) from None
            message, stamp = entry.get("MESSAGE"), entry.get("__REALTIME_TIMESTAMP")
            if isinstance(message, list) and all(isinstance(b, int) and 0 <= b < 256 for b in message):
                message = bytes(message).decode("utf-8", "replace")
            if not isinstance(message, str) or not str(stamp).isdigit():
                continue
            yield int(stamp) / 1e6, message, number, False
            continue
        match = _JOURNAL_ISO.match(line)
        if match is None:
            if LIVE_LINE.search(line) or CAMERA_LINE.search(line) or any(p in line for p, _ in EVENT_LINES):
                raise InputError("%s:%d: export with journalctl -o short-iso-precise or -o json" % (name, number))
            continue
        stamp = match.group(1)
        yield parse_time(stamp), line[match.end():], number, re.search(r":\d{2}[.,]\d", stamp) is None


def parse_exposition(text):
    """(harvested_at or None, {histogram family: {le: count}}, {gauge: value}),
    summed over label sets other than 'le'."""
    stamp, buckets, gauges = None, {}, {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            header = re.fullmatch(r"#\s*harvested_at[:\s]+(\S+)", line)
            if header:
                stamp = parse_time(header.group(1))
            continue
        match = re.fullmatch(r"([A-Za-z_:][A-Za-z0-9_:]*)(?:\{(.*)\})?\s+(\S+)(?:\s+-?\d+)?", line)
        if match is None:
            continue
        family, labels, raw = match.groups()
        try:
            value = float(raw)
        except ValueError:
            continue
        if not math.isfinite(value):
            continue
        if family.endswith("_bucket") and family[:-len("_bucket")] in VLLM_HISTOGRAMS:
            le = dict(re.findall(r'([A-Za-z_][A-Za-z0-9_]*)="((?:[^"\\]|\\.)*)"', labels or "")).get("le")
            if le is None:
                continue
            le = "+Inf" if le in ("+Inf", "Inf") else repr(float(le))
            series = buckets.setdefault(family[:-len("_bucket")], {})
            series[le] = series.get(le, 0) + value
        elif family in VLLM_GAUGES:
            gauges[family] = gauges.get(family, 0) + value
    return stamp, buckets, gauges


def open_ledger(path):
    """A Lab ledger snapshot, read-only and immutable. Never the live file:
    immutable=1 ignores a -wal file, so export with sqlite3 .backup first."""
    file = Path(path).resolve()
    if not file.is_file():
        raise InputError("%s: ledger snapshot not found" % path)
    db = sqlite3.connect(file.as_uri() + "?mode=ro&immutable=1", uri=True)
    db.execute("PRAGMA query_only=1")
    return db


def _read_text(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _json_objects(text, name):
    """One JSON document, or JSONL."""
    try:
        value = json.loads(text)
        return list(enumerate(value if isinstance(value, list) else [value], 1))
    except ValueError:
        pass
    objects = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip():
            try:
                objects.append((number, json.loads(line)))
            except ValueError:
                raise InputError("%s:%d: not JSON" % (name, number)) from None
    return objects


def write_samples(samples, handle):
    for sample in sorted(samples, key=lambda s: (s["t"], s["pipeline"], s["metric"], s["source"])):
        handle.write(json.dumps({k: sample[k] for k in SAMPLE_KEYS}, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-o", "--output", default="-", help="JSONL output path (default stdout)")
    sub = parser.add_subparsers(dest="kind", required=True)
    for kind in ("la-journal", "vllm", "browser", "jetson", "fact-log"):
        command = sub.add_parser(kind)
        command.add_argument("files", nargs="+")
        if kind == "vllm":
            command.add_argument("--at", help="dump time (ISO-8601 with offset) for a single dump without a header")
        if kind == "browser":
            command.add_argument("--start-mark", default=DEFAULT_START_MARK)
            command.add_argument("--end-mark", default=DEFAULT_END_MARK)
            command.add_argument("--measure", default=DEFAULT_MEASURE)
    command = sub.add_parser("ledger")
    command.add_argument("path", help="a lab.sqlite3 snapshot (sqlite3 .backup), never the live ledger")
    command.add_argument("--la-arm", choices=("a", "b"), help="the detector arm served by LA")
    args = parser.parse_args(argv)
    harvest = Harvest()
    try:
        if args.kind == "la-journal":
            for path in args.files:
                harvest.la_journal(_read_text(path).splitlines(), os.path.basename(path))
        elif args.kind == "vllm":
            harvest.vllm([(os.path.basename(p), _read_text(p)) for p in args.files], at=args.at)
        elif args.kind == "ledger":
            harvest.ledger(args.path, la_arm=args.la_arm)
        elif args.kind == "browser":
            for path in args.files:
                name = os.path.basename(path)
                try:
                    document = json.loads(_read_text(path))
                except ValueError:
                    raise InputError("%s: not JSON" % name) from None
                harvest.browser(document, name, args.start_mark, args.end_mark, args.measure)
        elif args.kind == "jetson":
            seen = set()
            for path in args.files:
                name = os.path.basename(path)
                harvest.jetson(_json_objects(_read_text(path), name), name, seen)
        elif args.kind == "fact-log":
            for path in args.files:
                harvest.fact_log(_read_text(path).splitlines(), os.path.basename(path))
    except (InputError, sqlite3.Error, OSError) as error:
        print("harvest: " + str(error), file=sys.stderr)
        return 2
    if args.output == "-":
        write_samples(harvest.samples, sys.stdout)
    else:
        with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
            write_samples(harvest.samples, handle)
    for note in harvest.notes:
        print("note: " + note, file=sys.stderr)
    print("harvest: %d samples" % len(harvest.samples), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
