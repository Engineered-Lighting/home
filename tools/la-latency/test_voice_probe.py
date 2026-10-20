"""voice_probe.py with scripted HTTP bytes and a fake clock: no network, no sleeping."""
import ast
import http.client
import io
import json
import random
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

import harvest
import report
import voice_probe

ROOT = Path(__file__).resolve().parents[2]
HEADERS = b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"


class Clock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


class TimedRaw(io.RawIOBase):
    """Bytes in segments; starting a segment advances the clock by its delay."""

    def __init__(self, clock, segments):
        self.clock, self.segments, self.current = clock, list(segments), b""

    def readable(self):
        return True

    def readinto(self, buffer):
        if not self.current:
            if not self.segments:
                return 0
            delay, self.current = self.segments.pop(0)
            self.clock.now += delay
        n = min(len(buffer), len(self.current))
        buffer[:n] = self.current[:n]
        self.current = self.current[n:]
        return n


class FakeSocket:
    def __init__(self, raw):
        self.raw = raw

    def makefile(self, *args, **kwargs):
        return io.BufferedReader(self.raw)


class Server:
    """http.client.HTTPConnection's surface, answering scripted segments per path."""

    def __init__(self, clock, responses=None, connect_error=None):
        self.clock, self.responses, self.connect_error = clock, dict(responses or {}), connect_error
        self.requests = []

    def __call__(self, host, port, timeout=None):
        return Connection(self, host, port, timeout)


class Connection:
    def __init__(self, server, host, port, timeout):
        self.server, self.host, self.port, self.timeout, self.closed = server, host, port, timeout, False

    def connect(self):
        if self.server.connect_error is not None:
            raise self.server.connect_error

    def request(self, method, path, body=None, headers=None):
        self.method, self.path = method, path
        self.server.requests.append({"method": method, "path": path, "body": body, "headers": dict(headers or {}),
                                     "host": self.host, "port": self.port})

    def getresponse(self):
        segments = self.server.responses[self.path]
        if callable(segments):
            segments = segments()
        response = http.client.HTTPResponse(FakeSocket(TimedRaw(self.server.clock, segments)), method=self.method)
        response.begin()
        return response

    def close(self):
        self.closed = True


def chunk(data):
    return b"%x\r\n%s\r\n" % (len(data), data)


def event(value):
    return chunk(b"data: " + (value if isinstance(value, bytes) else json.dumps(value).encode()) + b"\n\n")


def delta(**fields):
    return {"id": "chatcmpl-1", "object": "chat.completion.chunk", "model": "qwen3-vl-30b",
            "choices": [{"index": 0, "delta": fields, "finish_reason": None}]}


def stream(first=0.040, content="ready", gap=0.002, done=True, extra=()):
    """vLLM's stream for max_tokens 1: role chunk, token, finish, usage, [DONE]."""
    segments = [(0.0, HEADERS), (first, event(delta(role="assistant", content="")))]
    segments.append((gap, event(delta(content=content))))
    segments += list(extra)
    segments.append((0.001, event({"choices": [{"index": 0, "delta": {}, "finish_reason": "length"}]})))
    segments.append((0.001, event({"choices": [], "usage": {"prompt_tokens": 14, "completion_tokens": 1}})))
    if done:
        segments.append((0.001, event(b"[DONE]")))
    segments.append((0.0, b"0\r\n\r\n"))
    return segments


def json_response(value, status=200):
    body = json.dumps(value).encode()
    return [(0.0, b"HTTP/1.1 %d X\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s"
             % (status, len(body), body))]


def complete(server, clock, **kwargs):
    return voice_probe.stream_completion("192.168.0.100", 8000, voice_probe.request_body(), kwargs.pop("timeout", 10),
                                         connect=server, monotonic=clock)


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(100.0)

    def test_ttft_is_the_first_content_chunk_and_e2e_the_done(self):
        server = Server(self.clock, {"/v1/chat/completions": stream()})
        result = complete(server, self.clock)
        self.assertEqual(result["outcome"], "ok")
        self.assertAlmostEqual(result["ttft"], 0.042)
        self.assertEqual(result["ttft_method"], "voice_probe_stream_first_content_chunk")
        self.assertAlmostEqual(result["e2e"], 0.045)
        self.assertEqual(result["answer_bytes"], len(b"ready"))

    def test_the_request_is_ha_voices_shape_at_priority_zero_with_no_credential(self):
        server = Server(self.clock, {"/v1/chat/completions": stream()})
        complete(server, self.clock)
        sent = server.requests[0]
        self.assertEqual((sent["method"], sent["path"], sent["host"], sent["port"]),
                         ("POST", "/v1/chat/completions", "192.168.0.100", 8000))
        body = json.loads(sent["body"])
        self.assertEqual(body, {"model": "qwen3-vl-30b", "messages": [{"role": "user", "content": voice_probe.PROMPT}],
                                "stream": True, "stream_options": {"include_usage": True}, "max_tokens": 1,
                                "temperature": 0})
        self.assertNotIn("priority", body)
        self.assertFalse({k.lower() for k in sent["headers"]} & {"authorization", "api-key"})

    def test_without_content_the_first_choice_chunk_is_used(self):
        server = Server(self.clock, {"/v1/chat/completions": stream(content="")})
        result = complete(server, self.clock)
        self.assertEqual((result["outcome"], result["ttft_method"]), ("ok", "voice_probe_stream_first_choice_chunk"))
        self.assertAlmostEqual(result["ttft"], 0.040)

    def test_http_errors_connect_errors_and_timeouts_are_one_outcome_each(self):
        server = Server(self.clock, {"/v1/chat/completions": json_response({"error": "inference upstream unavailable"},
                                                                           502)})
        self.assertEqual(complete(server, self.clock)["outcome"], "http_502")
        self.assertEqual(complete(Server(self.clock, connect_error=ConnectionRefusedError()), self.clock)["outcome"],
                         "connect_error")
        self.assertEqual(complete(Server(self.clock, connect_error=TimeoutError()), self.clock)["outcome"], "timeout")
        slow = stream(first=11.0)
        result = complete(Server(self.clock, {"/v1/chat/completions": slow}), self.clock, timeout=10)
        self.assertEqual(result["outcome"], "timeout")
        self.assertIsNone(result["e2e"])

    def test_a_cut_stream_keeps_its_ttft_but_is_not_ok(self):
        result = complete(Server(self.clock, {"/v1/chat/completions": stream(done=False)}), self.clock)
        self.assertEqual(result["outcome"], "stream_incomplete")
        self.assertAlmostEqual(result["ttft"], 0.042)
        self.assertIsNone(result["e2e"])

    def test_error_events_and_garbage_are_typed(self):
        error = [(0.0, HEADERS), (0.01, event({"error": {"message": "x", "code": 500}})), (0.0, b"0\r\n\r\n")]
        self.assertEqual(complete(Server(self.clock, {"/v1/chat/completions": error}), self.clock)["outcome"],
                         "stream_error")
        garbage = [(0.0, HEADERS), (0.01, event(b"{not json")), (0.0, b"0\r\n\r\n")]
        self.assertEqual(complete(Server(self.clock, {"/v1/chat/completions": garbage}), self.clock)["outcome"],
                         "stream_invalid")


class GaugeTests(unittest.TestCase):
    TEXT = ('# HELP vllm:num_requests_running Number of requests in model execution batches.\n'
            'vllm:num_requests_running{engine="0",model_name="qwen3-vl-30b"} 2.0\n'
            'vllm:num_requests_running{engine="1",model_name="qwen3-vl-30b"} 1.0\n'
            'vllm:num_requests_running_total 99\n'
            'vllm:num_requests_waiting{engine="0",model_name="qwen3-vl-30b"} 0.0\n')

    def test_gauges_sum_label_sets_like_the_pilot_worker(self):
        self.assertEqual(voice_probe.gauge(self.TEXT, "vllm:num_requests_running"), 3.0)
        self.assertEqual(voice_probe.gauge(self.TEXT, "vllm:num_requests_waiting"), 0.0)
        with self.assertRaises(ValueError):
            voice_probe.gauge(self.TEXT, "vllm:num_requests_swapped")
        worker = ROOT / "tools/shared-home/home_shared/model_worker.py"
        if worker.is_file():
            source = worker.read_text(encoding="utf-8")
            self.assertIn("if line.startswith(name+'{') or line.startswith(name+' '):"
                          "total=(total or 0.0)+float(line.rsplit(' ',1)[1])", source)
            self.assertIn("gauge(text,'vllm:num_requests_running')", source)
            self.assertIn("gauge(text,'vllm:num_requests_waiting')", source)

    def test_container_address_prefers_the_compose_network(self):
        # "bridge" sorts before "homeai-net": only the preference picks the compose network.
        networks = {"bridge": {"IPAddress": "172.17.0.9"}, "homeai-net": {"IPAddress": "172.18.0.5"},
                    "none": {"IPAddress": ""}}
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(stdout=json.dumps(networks))
        self.assertEqual(voice_probe.docker_ip("hav-vllm", run=run), "172.18.0.5")
        self.assertEqual(calls[0], ["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", "hav-vllm"])
        networks = {"bridge": {"IPAddress": "172.17.0.9"}, "stack_homeai": {"IPAddress": "172.18.0.6"}}
        self.assertEqual(voice_probe.docker_ip("hav-vllm", run=run), "172.18.0.6")
        compose = ROOT / "stack/docker-compose.yml"
        if compose.is_file():
            text = compose.read_text(encoding="utf-8")
            block = text[text.index("\nnetworks:\n"):]
            self.assertIn("  homeai:\n    driver: bridge\n    name: %s\n" % voice_probe.COMPOSE_NETWORK, block)

    def test_a_failed_read_resolves_again_at_most_once_a_minute(self):
        clock = Clock(0.0)
        addresses = ["172.18.0.5", "172.18.0.7"]
        resolved = []

        def resolve(container):
            resolved.append(container)
            return addresses[min(len(resolved), 2) - 1]
        server = Server(clock, {"/metrics": lambda: [(0.0, b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s"
                                                     % (len(self.TEXT), self.TEXT.encode()))]})
        gauges = voice_probe.VllmGauges(container="hav-vllm", resolve=resolve, connect=server, monotonic=clock)
        self.assertEqual(gauges.read(1.0), {"num_requests_running": 3.0, "num_requests_waiting": 0.0})
        self.assertEqual(server.requests[-1]["host"], "172.18.0.5")
        server.responses["/metrics"] = [(0.0, b"HTTP/1.1 503 X\r\nContent-Length: 0\r\n\r\n")]
        with self.assertRaises(ValueError):
            gauges.read(1.0)
        clock.now += 10
        with self.assertRaises(ValueError):
            gauges.read(1.0)  # unresolved, and not a minute since the last resolve
        self.assertEqual(len(resolved), 1)
        clock.now += 60
        server.responses["/metrics"] = [(0.0, b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s"
                                         % (len(self.TEXT), self.TEXT.encode()))]
        gauges.read(1.0)
        self.assertEqual((len(resolved), server.requests[-1]["host"]), (2, "172.18.0.7"))
        with self.assertRaises(ValueError):
            voice_probe.VllmGauges()

    def test_tctl_from_k10temp_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "hwmon0").mkdir()
            (root / "hwmon0/name").write_text("nvme\n")
            (root / "hwmon0/temp1_label").write_text("Tctl\n")
            (root / "hwmon3").mkdir()
            (root / "hwmon3/name").write_text("k10temp\n")
            (root / "hwmon3/temp1_label").write_text("Tctl\n")
            (root / "hwmon3/temp1_input").write_text("71250\n")
            (root / "hwmon3/temp3_label").write_text("Tccd1\n")
            path = voice_probe.tctl_path(root)
            self.assertEqual(path, root / "hwmon3/temp1_input")
            self.assertEqual(voice_probe.read_tctl(path), 71.25)
            self.assertIsNone(voice_probe.tctl_path(root / "absent"))
            self.assertIsNone(voice_probe.read_tctl(None))


def args(**changes):
    value = SimpleNamespace(url=voice_probe.DEFAULT_URL, model=voice_probe.MODEL, interval=15.0, jitter=0.5,
                            timeout=10.0, duration=60.0, load_interval=1.0, no_gpu=False)
    value.__dict__.update(changes)
    return value


class ProbeTests(unittest.TestCase):
    def make(self, responses, **changes):
        self.clock, self.wall = Clock(0.0), Clock(1791460800.0)
        self.server = Server(self.clock, responses)
        self.handle = io.StringIO()
        self.lines = []
        return voice_probe.Probe(args(**changes), voice_probe.Samples(self.handle, "voice-probe.jsonl"),
                                 connect=self.server, clock=lambda: self.wall.now + self.clock.now,
                                 monotonic=self.clock, log=self.lines.append, rng=random.Random(7))

    def samples(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "voice-probe.jsonl"
            path.write_text(self.handle.getvalue(), encoding="utf-8")
            return report.load_samples([path])

    def test_samples_are_harvest_format_with_no_prompt_or_answer(self):
        probe = self.make({"/v1/chat/completions": stream, "/metrics": lambda: json_response({"gpu_temp_c": 57})})
        probe.tctl = None
        self.assertEqual(probe.probe(), "ok")
        probe.readings()
        rows = self.samples()
        found = {(r["pipeline"], r["metric"]): r for r in rows}
        self.assertEqual(set(found), {("voice_probe", "ttft"), ("voice_probe", "e2e"), ("voice_probe", "outcome:ok"),
                                      ("la_gpu", "gpu_temp_c")})
        self.assertEqual(found[("voice_probe", "ttft")]["value_s"], 0.042)
        self.assertEqual(found[("voice_probe", "ttft")]["source"], "voice-probe:voice-probe.jsonl:1")
        self.assertEqual(found[("voice_probe", "ttft")]["t"], harvest.format_time(1791460800.042))
        self.assertEqual(found[("voice_probe", "e2e")]["t"], harvest.format_time(1791460800.045))
        self.assertEqual(found[("la_gpu", "gpu_temp_c")]["value_s"], 57)
        text = self.handle.getvalue()
        self.assertNotIn(voice_probe.PROMPT, text)
        self.assertNotIn("ready", text)
        for line in text.splitlines():
            self.assertEqual(list(json.loads(line)), sorted(harvest.SAMPLE_KEYS))

    def test_an_error_is_one_outcome_sample_and_never_a_retry(self):
        probe = self.make({"/v1/chat/completions": lambda: json_response({"error": "x"}, 502)}, no_gpu=True)
        self.assertEqual(probe.probe(), "http_502")
        self.assertEqual(len(self.server.requests), 1)
        rows = self.samples()
        self.assertEqual([(r["metric"], r["value_s"]) for r in rows], [("outcome:http_502", 1)])

    def test_the_schedule_is_jittered_bounded_and_never_bunches(self):
        durations = iter([9.5, 0.5, 0.5])
        sends = []

        def timed():
            sends.append(self.clock.now)
            return stream(first=next(durations))
        probe = self.make({"/v1/chat/completions": timed}, duration=60.0, no_gpu=True)
        offsets = iter([7.0, 1.0, 1.0, 1.0, 1.0])

        class Offsets:
            def uniform(self, low, high):
                value = next(offsets)
                assert low <= value <= high
                return value
        probe.rng = Offsets()

        def wait(seconds):
            self.clock.now += seconds
            return False
        probe.stop = SimpleNamespace(is_set=lambda: False, wait=wait, set=lambda: None)
        self.assertEqual(probe.run(), 3)
        # Slot 0 sends at 7 s and runs to about 16.5 s, past slot 1's moment
        # (16 s), which is skipped rather than sent late; slots 2 and 3 send;
        # slot 4's moment (61 s) is past the 60 s duration.
        self.assertEqual([round(t, 3) for t in sends], [7.0, 31.0, 46.0])
        self.assertIn("voice probe stopped: probes=3", self.lines[-1])
        self.assertEqual(len([r for r in self.samples() if r["metric"] == "outcome:ok"]), 3)

    def test_gauge_reads_share_a_source_and_failures_log_once_per_change(self):
        probe = self.make({}, no_gpu=True)
        reads = iter([{"num_requests_running": 1.0, "num_requests_waiting": 0.0}, ValueError("x"), ValueError("y"),
                      {"num_requests_running": 4.0, "num_requests_waiting": 1.0}])

        class Gauges:
            def read(self, timeout):
                value = next(reads)
                if isinstance(value, Exception):
                    raise value
                return value
        probe.gauges = Gauges()
        ticks = []

        def wait(seconds):
            self.clock.now += seconds
            ticks.append(self.clock.now)
            return len(ticks) > 4
        probe.stop = SimpleNamespace(wait=wait, is_set=lambda: False, set=lambda: None)
        probe.load_loop(end=1000.0)
        rows = self.samples()
        self.assertEqual([(r["metric"], r["value_s"], r["source"]) for r in rows],
                         [("num_requests_running", 1.0, "vllm-load:voice-probe.jsonl:1"),
                          ("num_requests_waiting", 0.0, "vllm-load:voice-probe.jsonl:1"),
                          ("num_requests_running", 4.0, "vllm-load:voice-probe.jsonl:2"),
                          ("num_requests_waiting", 1.0, "vllm-load:voice-probe.jsonl:2")])
        self.assertEqual(self.lines, ["voice probe: vLLM gauges unavailable: ValueError",
                                      "voice probe: vLLM gauges available"])
        self.assertEqual({r["method"] for r in rows}, {"voice_probe_vllm_gauge"})


class CliTests(unittest.TestCase):
    def test_arguments_are_bounded(self):
        parsed = voice_probe.parse_args(["--duration", "600", "--output", "x.jsonl", "--url", "http://h:8000/v1/"])
        self.assertEqual(voice_probe.base_origin(parsed.url), ("h", 8000))
        for bad in (["--output", "x"], ["--duration", "600"], ["--duration", "30", "--output", "x"],
                    ["--duration", "600", "--output", "x", "--timeout", "15"],
                    ["--duration", "600", "--output", "x", "--url", "https://h:8000"],
                    ["--duration", "600", "--output", "x", "--url", "http://h:8000/v1/chat/completions"],
                    ["--check", "--vllm-container", "a", "--vllm-metrics-url", "http://h/metrics"],
                    ["--check", "--vllm-metrics-url", "http://h:8000/metrics?a=1&b=2"],
                    ["--check", "--url", "http://x;touch${IFS}p:8000"]):
            with self.assertRaises(SystemExit, msg=bad):
                with unittest.mock.patch("sys.stderr", io.StringIO()):
                    voice_probe.parse_args(bad)

    def test_urls_carry_nothing_a_shell_would_read(self):
        """The cadence A/B passes these on an ssh command line."""
        for good, origin in (("http://192.168.0.100:8000", ("192.168.0.100", 8000)), ("http://h:8000/v1/", ("h", 8000)),
                             ("http://hav-vllm.lan", ("hav-vllm.lan", 80)), ("http://[::1]:8000", ("::1", 8000))):
            self.assertEqual(voice_probe.base_origin(good), origin)
        for bad in ("http://x;touch${IFS}p:8000", "http://x$(id):8000", "http://x`id`:8000", "http://x y:8000",
                    "http://u@h:8000", "http://h:80x", "http://h:8000/?", "http://h:8000#f", "http://-h:8000"):
            with self.assertRaises(ValueError, msg=bad):
                voice_probe.base_origin(bad)
        self.assertEqual(voice_probe.metrics_url("http://172.18.0.5:8000/metrics"), "http://172.18.0.5:8000/metrics")
        for bad in ("http://h:8000/metrics?a=1&b=2", "http://h;id/metrics", "http://h/me trics", "http://h/m;x",
                    "https://h/metrics", "http://h/$(id)"):
            with self.assertRaises(ValueError, msg=bad):
                voice_probe.metrics_url(bad)

    def test_an_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "voice-probe.jsonl"
            path.write_text("keep\n")
            with unittest.mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(voice_probe.main(["--duration", "600", "--output", str(path)]), 2)
            self.assertEqual(path.read_text(), "keep\n")

    def test_check_sends_no_chat_completion(self):
        clock = Clock(0.0)
        server = Server(clock, {"/healthz": lambda: json_response({"ok": True}),
                                "/metrics": lambda: json_response({"gpu_temp_c": 55})})

        class Gauges:
            def read(self, timeout):
                return {"num_requests_running": 0.0, "num_requests_waiting": 0.0}

            def url(self):
                return "http://172.18.0.5:8000/metrics"
        result = voice_probe.check(args(), connect=server, gauges=Gauges(), tctl=None)
        self.assertTrue(result["sidecar_healthz"])
        self.assertEqual((result["gpu_temp_c"], result["tctl_c"]), (55.0, None))
        self.assertEqual(result["vllm_gauges"], {"num_requests_running": 0.0, "num_requests_waiting": 0.0})
        self.assertEqual([r["method"] for r in server.requests], ["GET", "GET"])
        self.assertNotIn("/v1/chat/completions", [r["path"] for r in server.requests])
        # Stamped first and last, for the plan's clock-offset midpoint.
        self.assertLessEqual(result["time"], result["time_end"])

    def test_python_310_grammar(self):
        ast.parse((Path(__file__).with_name("voice_probe.py")).read_text(encoding="utf-8"), feature_version=(3, 10))


class RouteTests(unittest.TestCase):
    """The route and request shape come from the repo, not a guess."""

    def test_ha_voice_route_and_shape(self):
        compose = ROOT / "stack/docker-compose.yml"
        sidecar = ROOT / "stack/services/metrics-sidecar/main.py"
        entity = ROOT / "ha-config/extended_openai_conversation/entity.py"
        if not (compose.is_file() and sidecar.is_file() and entity.is_file()):
            self.skipTest("stack sources not present")
        text = compose.read_text(encoding="utf-8")
        block = text[text.index("  metrics-sidecar:"):text.index("  metrics-sidecar:") + 1200]
        self.assertIn('"${MODEL_EDGE_BIND_ADDR:-127.0.0.1}:${VLLM_PORT:-8000}:8092"', block)
        self.assertIn("VLLM_UPSTREAM_URL: ${VLLM_UPSTREAM_URL:-http://vllm:8000}", block)
        self.assertIn("container_name: hav-metrics-sidecar", block)
        vllm = text[text.index("  vllm:"):text.index("  vllm:") + 3000]
        self.assertIn("- --served-model-name\n      - qwen3-vl-30b", vllm)
        self.assertNotIn("--api-key", vllm)
        self.assertIn("container_name: hav-vllm", vllm)
        source = sidecar.read_text(encoding="utf-8")
        for route in ('@app.post("/v1/chat/completions")', '@app.get("/metrics")', '@app.get("/healthz")'):
            self.assertIn(route, source)
        self.assertIn('"gpu_temp_c": gpu_temp_c,', source)
        self.assertIn("is_stream = bool(body.get(\"stream\"))", source)
        shape = entity.read_text(encoding="utf-8")
        self.assertIn('"stream": True,', shape)
        self.assertIn('"stream_options": {"include_usage": True},', shape)
        self.assertNotIn('"priority"', shape)


if __name__ == "__main__":
    unittest.main()
