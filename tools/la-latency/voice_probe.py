#!/usr/bin/env python3
"""Synthetic voice-latency probe through the LA metrics sidecar (plan S1.4 step 3).

Runs on LA (hav-ubuntu): Python 3.10+, standard library only. Organic voice
traffic is too sparse for a p95, so this sends one minimal request on a fixed
schedule along the route HA voice uses.

Every --interval seconds (default 15) it sends ONE streaming chat completion:
POST <url>/v1/chat/completions, where <url> is HA's Extended OpenAI base URL
(default http://192.168.0.100:8000: host port 8000 -> hav-metrics-sidecar
:8092 -> http://vllm:8000, stack/docker-compose.yml). The body has the shape
HA sends (ha-config/extended_openai_conversation/entity.py: model, stream,
stream_options.include_usage) with model qwen3-vl-30b, max_tokens 1,
temperature 0, one fixed short prompt, and no priority field, so vLLM
schedules it at priority 0 like voice. The sidecar has no auth and vLLM runs
without --api-key, so no credential is sent. Each send lands at a random point
in the first half of its slot, so the probe never locks onto the live pump's
period.

Output: harvest.py samples (JSONL with exactly pipeline, metric, value_s, t,
source, method), flushed line by line:

  voice_probe/ttft          send to the first streamed content chunk, seconds
                            (method ..._first_choice_chunk when no chunk
                            carried content)
  voice_probe/e2e           send to the stream's [DONE]
  voice_probe/outcome:<w>   1 per probe; w is one of OUTCOMES
  la_gpu/gpu_temp_c         the sidecar's GET /metrics, once per slot
  la_cpu/tctl_c             k10temp Tctl from /sys/class/hwmon, once per slot
  vllm/num_requests_running and vllm/num_requests_waiting
                            vLLM's own gauges every --load-interval s (the
                            pilot's S1.4 yield inputs, summed over labels as
                            home_shared/model_worker.py does); only with
                            --vllm-container or --vllm-metrics-url

t is the instant the value was known (first chunk, [DONE], or the read).

Fail-safe: one request at a time and never a retry. An error is one outcome
sample, and the next slot keeps its time (missed slots are skipped, never
caught up). --duration bounds the run; SIGTERM or SIGINT ends it after the
current request. Prompts and answers are never written anywhere: stderr gets
counts, and the total answer bytes when it stops.

--check makes one read-only pass over the inputs (sidecar /healthz and
/metrics, Tctl, the vLLM gauges) and prints one JSON line. It sends no chat
completion.
"""
import argparse
import http.client
import json
import math
import os
import platform
import random
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

SAMPLE_KEYS = ("pipeline", "metric", "value_s", "t", "source", "method")
DEFAULT_URL = "http://192.168.0.100:8000"
MODEL = "qwen3-vl-30b"
PROMPT = "Reply with one word: ready."
CHAT_PATH = "/v1/chat/completions"
PIPELINE = "voice_probe"
OUTCOMES = ("ok", "timeout", "connect_error", "transport_error", "stream_error", "stream_invalid",
            "stream_incomplete")  # plus http_<status>
MAX_LINE = 65536
MAX_METRICS_BYTES = 4 * 1024 * 1024
GAUGES = (("vllm:num_requests_running", "num_requests_running"),
          ("vllm:num_requests_waiting", "num_requests_waiting"))
RESOLVE_SECONDS = 60.0
REPORT_EVERY = 40
# A host name or an IP literal (urlsplit strips an IPv6 literal's brackets).
HOSTNAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?|[0-9a-f:.]{2,45}")
METRICS_PATH = re.compile(r"(?:/[A-Za-z0-9._~-]+)*/?")
# stack/docker-compose.yml names its network explicitly (networks: homeai: name: homeai-net).
COMPOSE_NETWORK = "homeai-net"


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def format_time(epoch):
    """harvest.format_time: ISO-8601 UTC with milliseconds."""
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _http_parts(url):
    """urlsplit of an http:// URL whose host is a plain name or an IP literal
    (letters, digits, '.', '-', or an IPv6 literal's hex digits and ':'), with no
    user, query or fragment; None otherwise. These URLs end up on an ssh
    command line, so nothing else is accepted."""
    parts = urlsplit(url)
    try:
        parts.port  # raises ValueError for a port that is not a number in range
    except ValueError:
        return None
    if (parts.scheme != "http" or not parts.hostname or not HOSTNAME.fullmatch(parts.hostname)
            or parts.username is not None or parts.password is not None or parts.query or parts.fragment
            or "?" in url or "#" in url):
        return None
    return parts


def base_origin(url):
    """(host, port) of an http:// base URL: HA's base URL with or without /v1."""
    parts = _http_parts(url)
    if parts is None or parts.path.rstrip("/") not in ("", "/v1"):
        raise ValueError("an http://host:port base URL is required (HA's base URL, with or without /v1)")
    return parts.hostname, parts.port or 80


def metrics_url(url):
    """url when it is a plain http://host[:port]/path Prometheus URL, else ValueError."""
    parts = _http_parts(url)
    if parts is None or not METRICS_PATH.fullmatch(parts.path):
        raise ValueError("--vllm-metrics-url must be a plain http://host:port/path URL")
    return url


def request_body(model=MODEL, prompt=PROMPT):
    return json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}], "stream": True,
                       "stream_options": {"include_usage": True}, "max_tokens": 1, "temperature": 0},
                      separators=(",", ":")).encode()


def _has_content(delta):
    if not isinstance(delta, dict):
        return False
    return any(isinstance(delta.get(k), str) and delta[k] for k in ("content", "reasoning_content")) or (
        isinstance(delta.get("tool_calls"), list) and bool(delta["tool_calls"]))


def stream_completion(host, port, body, timeout, *, connect=http.client.HTTPConnection, monotonic=time.monotonic):
    """One streamed completion. Returns {'outcome', 'sent', 'ttft',
    'ttft_method', 'e2e', 'answer_bytes'}: sent is the monotonic send time,
    ttft and e2e are seconds after it (the connect is not timed: HA keeps its
    connection pooled)."""
    result = {"outcome": None, "sent": None, "ttft": None, "ttft_method": None, "e2e": None, "answer_bytes": 0}
    connection = connect(host, port, timeout=timeout)
    try:
        try:
            connection.connect()
        except TimeoutError:
            result["outcome"] = "timeout"
            return result
        except OSError:
            result["outcome"] = "connect_error"
            return result
        start = result["sent"] = monotonic()
        connection.request("POST", CHAT_PATH, body=body, headers={
            "Content-Type": "application/json", "Accept": "text/event-stream", "User-Agent": "la-voice-probe/1"})
        response = connection.getresponse()
        if response.status != 200:
            result["outcome"] = "http_%d" % response.status
            return result
        first_choice = None
        while True:
            if monotonic() - start > timeout:
                result["outcome"] = "timeout"
                break
            line = response.readline(MAX_LINE + 1)
            now = monotonic()
            if not line:
                result["outcome"] = "stream_incomplete"
                break
            if len(line) > MAX_LINE:
                result["outcome"] = "stream_invalid"
                break
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                result["outcome"], result["e2e"] = "ok", now - start
                break
            try:
                event = json.loads(data)
            except ValueError:
                result["outcome"] = "stream_invalid"
                break
            if not isinstance(event, dict):
                result["outcome"] = "stream_invalid"
                break
            if event.get("error") is not None:
                result["outcome"] = "stream_error"
                break
            choices = event.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                continue  # the usage chunk
            delta = choices[0].get("delta")
            if first_choice is None:
                first_choice = now
            if result["ttft"] is None and _has_content(delta):
                result["ttft"], result["ttft_method"] = now - start, "voice_probe_stream_first_content_chunk"
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                result["answer_bytes"] += len(delta["content"].encode("utf-8"))
        if result["ttft"] is None and first_choice is not None:
            result["ttft"], result["ttft_method"] = first_choice - start, "voice_probe_stream_first_choice_chunk"
        if result["outcome"] != "ok":
            result["e2e"] = None
        return result
    except TimeoutError:
        result["outcome"] = "timeout"
        return result
    except (OSError, http.client.HTTPException):
        result["outcome"] = "transport_error"
        return result
    finally:
        connection.close()


def http_get(host, port, path, timeout, limit, *, connect=http.client.HTTPConnection):
    connection = connect(host, port, timeout=timeout)
    try:
        connection.request("GET", path, headers={"User-Agent": "la-voice-probe/1"})
        response = connection.getresponse()
        raw = response.read(limit + 1)
        if response.status != 200 or len(raw) > limit:
            raise ValueError("unavailable")
        return raw
    finally:
        connection.close()


def sidecar_gpu_temp(host, port, timeout=5.0, *, connect=http.client.HTTPConnection):
    """gpu_temp_c from the sidecar's GET /metrics (the same app serves port 8000), or None."""
    value = json.loads(http_get(host, port, "/metrics", timeout, 1024 * 1024, connect=connect))
    temp = value.get("gpu_temp_c") if isinstance(value, dict) else None
    return float(temp) if finite(temp) else None


def gauge(text, name):
    """Sum of one gauge over its label sets (model_worker.py's rule)."""
    total = None
    for line in text.splitlines():
        if line.startswith(name + "{") or line.startswith(name + " "):
            total = (total or 0.0) + float(line.rsplit(" ", 1)[1])
    if total is None:
        raise ValueError("metric unavailable")
    return total


def docker_ip(container, *, run=subprocess.run):
    """The container's address, preferring the compose network (homeai-net, or a
    project-prefixed *_homeai if the explicit name is ever dropped)."""
    out = run(["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", container],
              capture_output=True, text=True, timeout=10, check=True).stdout
    networks = json.loads(out)
    if not isinstance(networks, dict):
        raise ValueError("no networks")
    found = sorted((name != COMPOSE_NETWORK and not name.endswith("_homeai"), name, value.get("IPAddress"))
                   for name, value in networks.items() if isinstance(value, dict) and value.get("IPAddress"))
    if not found:
        raise ValueError("container has no address")
    return found[0][2]


class VllmGauges:
    """vLLM's running/waiting gauges from a fixed URL or a container's address
    (resolved again after a failed read, at most once a minute: a recreate
    changes it)."""

    def __init__(self, url=None, container=None, *, resolve=docker_ip, connect=http.client.HTTPConnection,
                 monotonic=time.monotonic):
        if (url is None) == (container is None):
            raise ValueError("exactly one of a metrics URL or a container")
        self.fixed, self.container, self.resolve = url, container, resolve
        self.connect, self.monotonic = connect, monotonic
        self.resolved, self.resolved_at = None, None

    def url(self):
        if self.fixed is not None:
            return self.fixed
        now = self.monotonic()
        if self.resolved is None and (self.resolved_at is None or now - self.resolved_at >= RESOLVE_SECONDS):
            self.resolved_at = now
            self.resolved = "http://%s:8000/metrics" % self.resolve(self.container)
        if self.resolved is None:
            raise ValueError("vLLM address unresolved")
        return self.resolved

    def read(self, timeout):
        try:
            parts = urlsplit(self.url())
            raw = http_get(parts.hostname, parts.port or 80, parts.path or "/metrics", timeout, MAX_METRICS_BYTES,
                           connect=self.connect)
            text = raw.decode("utf-8", "replace")
            return {metric: gauge(text, name) for name, metric in GAUGES}
        except Exception:
            if self.fixed is None:
                self.resolved = None
            raise


def tctl_path(root="/sys/class/hwmon"):
    """The k10temp (or zenpower) Tctl input file, or None."""
    for hwmon in sorted(Path(root).glob("hwmon*")):
        try:
            name = (hwmon / "name").read_text().strip()
        except OSError:
            continue
        if name not in ("k10temp", "zenpower"):
            continue
        for label in sorted(hwmon.glob("temp*_label")):
            try:
                if label.read_text().strip() == "Tctl":
                    return label.with_name(label.name[:-len("_label")] + "_input")
            except OSError:
                continue
    return None


def read_tctl(path):
    if path is None:
        return None
    try:
        value = int(path.read_text().strip()) / 1000
    except (OSError, ValueError):
        return None
    return value if finite(value) else None


class Samples:
    """Thread-safe harvest-format JSONL writer, flushed per line."""

    def __init__(self, handle, name):
        self.handle, self.name, self.lock = handle, name, threading.Lock()
        self.numbers = {}

    def number(self, kind):
        with self.lock:
            self.numbers[kind] = self.numbers.get(kind, 0) + 1
            return self.numbers[kind]

    def add(self, pipeline, metric, value, t, kind, number, method):
        if not finite(value) or not finite(t) or value < 0:
            return False
        row = {"pipeline": pipeline, "metric": metric, "value_s": round(float(value), 6), "t": format_time(t),
               "source": "%s:%s:%d" % (kind, self.name, number), "method": method}
        line = json.dumps({k: row[k] for k in SAMPLE_KEYS}, sort_keys=True) + "\n"
        with self.lock:
            self.handle.write(line)
            self.handle.flush()
        return True


class Probe:
    """The schedule: probes on --interval slots, plus the optional gauge thread."""

    def __init__(self, args, samples, *, connect=http.client.HTTPConnection, gauges=None, tctl=None,
                 clock=time.time, monotonic=time.monotonic, rng=None, log=None):
        self.args, self.samples, self.connect, self.gauges, self.tctl = args, samples, connect, gauges, tctl
        self.clock, self.monotonic = clock, monotonic
        self.rng = rng or random.Random()
        self.log = log or (lambda text: print(text, file=sys.stderr, flush=True))
        self.stop = threading.Event()
        self.host, self.port = base_origin(args.url)
        self.body = request_body(args.model)
        self.outcomes, self.ttfts, self.answer_bytes = {}, [], 0
        self.load_samples, self.load_failure, self.load_failures = 0, None, 0

    def probe(self):
        number = self.samples.number("voice-probe")
        base_mono, base_wall = self.monotonic(), self.clock()
        result = stream_completion(self.host, self.port, self.body, self.args.timeout, connect=self.connect,
                                   monotonic=self.monotonic)
        outcome = result["outcome"] or "stream_incomplete"
        sent_wall = base_wall + ((result["sent"] if result["sent"] is not None else base_mono) - base_mono)
        if result["ttft"] is not None:
            self.samples.add(PIPELINE, "ttft", result["ttft"], sent_wall + result["ttft"], "voice-probe", number,
                             result["ttft_method"])
            if outcome == "ok":
                self.ttfts.append(result["ttft"])
        if result["e2e"] is not None:
            self.samples.add(PIPELINE, "e2e", result["e2e"], sent_wall + result["e2e"], "voice-probe", number,
                             "voice_probe_stream_done")
        self.samples.add(PIPELINE, "outcome:" + outcome, 1, base_wall + (self.monotonic() - base_mono),
                         "voice-probe", number, "voice_probe_outcome")
        self.outcomes[outcome] = self.outcomes.get(outcome, 0) + 1
        self.answer_bytes += result["answer_bytes"]
        return outcome

    def readings(self):
        """GPU and CPU temperature, after the probe so they never delay a send."""
        if not self.args.no_gpu:
            try:
                temp = sidecar_gpu_temp(self.host, self.port, connect=self.connect)
            except Exception:
                temp = None
            if temp is not None:
                self.samples.add("la_gpu", "gpu_temp_c", temp, self.clock(), "sidecar-metrics",
                                 self.samples.number("sidecar-metrics"), "sidecar_metrics_gpu_temp_c")
        value = read_tctl(self.tctl)
        if value is not None:
            self.samples.add("la_cpu", "tctl_c", value, self.clock(), "hwmon", self.samples.number("hwmon"),
                             "hwmon_k10temp_tctl")

    def load_loop(self, end):
        interval = self.args.load_interval
        due = self.monotonic()
        while not self.stop.wait(max(0.0, due - self.monotonic())):
            if self.monotonic() >= end:
                return
            due += interval
            if due < self.monotonic():
                due = self.monotonic() + interval  # never catch up in a burst
            failure = None
            try:
                values = self.gauges.read(timeout=min(1.0, interval))
                at, number = self.clock(), self.samples.number("vllm-load")
                for metric, value in sorted(values.items()):
                    self.samples.add("vllm", metric, value, at, "vllm-load", number, "voice_probe_vllm_gauge")
                self.load_samples += 1
            except Exception as error:
                failure = type(error).__name__
                self.load_failures += 1
            if failure != self.load_failure:
                self.load_failure = failure
                self.log("voice probe: vLLM gauges " + ("available" if failure is None else "unavailable: " + failure))

    def run(self):
        args = self.args
        start = self.monotonic()
        end = start + args.duration
        loader = None
        if self.gauges is not None:
            loader = threading.Thread(target=self.load_loop, args=(end,), name="vllm-gauges", daemon=True)
            loader.start()
        slot, probes, idle_since = start, 0, start
        try:
            while not self.stop.is_set():
                at = slot + self.rng.uniform(0, args.jitter * args.interval)
                slot += args.interval
                if at >= end:
                    break
                if at < idle_since:
                    continue  # this slot's moment passed during a slow probe: skip it, never bunch sends
                if self.stop.wait(max(0.0, at - self.monotonic())):
                    break
                self.probe()
                self.readings()
                idle_since = self.monotonic()
                probes += 1
                if probes % REPORT_EVERY == 0:
                    self.log(self.status(probes))
        finally:
            self.stop.set()
            if loader is not None:
                loader.join(timeout=5)
        self.log("voice probe stopped: " + self.status(probes) + " answer_bytes=%d load_samples=%d load_failures=%d"
                 % (self.answer_bytes, self.load_samples, self.load_failures))
        return probes

    def status(self, probes):
        ordered = sorted(self.ttfts[-REPORT_EVERY:])
        p50 = ordered[max(1, math.ceil(.5 * len(ordered))) - 1] if ordered else None
        return "probes=%d outcomes=%s ttft_p50_recent=%s" % (
            probes, json.dumps(self.outcomes, sort_keys=True), "%.3fs" % p50 if p50 is not None else "n/a")


def check(args, *, connect=http.client.HTTPConnection, gauges=None, tctl=None):
    """One read-only pass over the inputs; no chat completion."""
    host, port = base_origin(args.url)
    result = {"python": platform.python_version(), "time": time.time(), "url": "http://%s:%d" % (host, port)}
    try:
        value = json.loads(http_get(host, port, "/healthz", 5.0, 65536, connect=connect))
        result["sidecar_healthz"] = isinstance(value, dict) and value.get("ok") is True
    except Exception as error:
        result["sidecar_healthz"], result["sidecar_error"] = False, type(error).__name__
    try:
        result["gpu_temp_c"] = sidecar_gpu_temp(host, port, connect=connect)
    except Exception as error:
        result["gpu_temp_c"], result["gpu_error"] = None, type(error).__name__
    result["tctl_c"] = read_tctl(tctl)
    if gauges is None:
        result["vllm_gauges"] = None
    else:
        try:
            result["vllm_gauges"] = gauges.read(timeout=3.0)
            result["vllm_metrics_url"] = gauges.url()
        except Exception as error:
            result["vllm_gauges"] = {"error": type(error).__name__}
    # With 'time' taken first, the midpoint is this host's clock halfway through the check.
    result["time_end"] = time.time()
    return result


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default=DEFAULT_URL, help="HA voice's base URL (default %(default)s)")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--interval", type=float, default=15.0, help="seconds per probe slot (5-300)")
    parser.add_argument("--jitter", type=float, default=0.5, help="send at a random point in this share of the slot")
    parser.add_argument("--timeout", type=float, default=10.0, help="per-request bound, below --interval")
    parser.add_argument("--duration", type=float, help="seconds to run (60 s to 7 days)")
    parser.add_argument("--output", help="new JSONL file (never overwritten)")
    parser.add_argument("--vllm-container", help="sample vLLM's gauges from this container's address")
    parser.add_argument("--vllm-metrics-url", help="sample vLLM's gauges from this Prometheus URL")
    parser.add_argument("--load-interval", type=float, default=1.0, help="seconds between gauge reads (0.5-60)")
    parser.add_argument("--no-gpu", action="store_true", help="skip the sidecar gpu_temp_c read")
    parser.add_argument("--seed", type=int, help="fixed jitter (tests)")
    parser.add_argument("--check", action="store_true", help="one read-only pass; no chat completion")
    args = parser.parse_args(argv)
    try:
        base_origin(args.url)
    except ValueError as error:
        parser.error(str(error))
    if args.vllm_container and args.vllm_metrics_url:
        parser.error("--vllm-container and --vllm-metrics-url are exclusive")
    if args.vllm_metrics_url:
        try:
            metrics_url(args.vllm_metrics_url)
        except ValueError as error:
            parser.error(str(error))
    if not args.check:
        if args.duration is None or args.output is None:
            parser.error("--duration and --output are required (or --check)")
        if not 60 <= args.duration <= 7 * 86400:
            parser.error("--duration must be 60 s to 7 days")
    if not 5 <= args.interval <= 300 or not 0 <= args.jitter <= 1:
        parser.error("--interval must be 5-300 s and --jitter 0-1")
    if not 1 <= args.timeout < args.interval:
        parser.error("--timeout must be at least 1 s and below --interval")
    if not 0.5 <= args.load_interval <= 60:
        parser.error("--load-interval must be 0.5-60 s")
    return args


def main(argv=None):
    args = parse_args(argv)
    gauges = None
    if args.vllm_container or args.vllm_metrics_url:
        gauges = VllmGauges(url=args.vllm_metrics_url, container=args.vllm_container)
    tctl = tctl_path()
    if args.check:
        result = check(args, gauges=gauges, tctl=tctl)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["sidecar_healthz"] else 1
    try:
        handle = open(args.output, "x", encoding="utf-8", newline="\n")
    except OSError as error:
        print("voice probe: cannot create %s (%s)" % (args.output, type(error).__name__), file=sys.stderr)
        return 2
    with handle:
        probe = Probe(args, Samples(handle, os.path.basename(args.output)), gauges=gauges, tctl=tctl,
                      rng=random.Random(args.seed))
        for name in ("SIGTERM", "SIGINT"):
            signal.signal(getattr(signal, name), lambda *_: probe.stop.set())
        host, port = probe.host, probe.port
        probe.log("voice probe: url=http://%s:%d%s model=%s interval=%gs timeout=%gs duration=%gs output=%s "
                  "vllm_gauges=%s tctl=%s pid=%d" % (host, port, CHAT_PATH, args.model, args.interval, args.timeout,
                                                     args.duration, os.path.basename(args.output),
                                                     "on" if gauges else "off", "on" if tctl else "off",
                                                     os.getpid()))
        probe.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
