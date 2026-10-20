"""harvest.py on synthetic inputs only: never real logs, dumps or ledgers."""
import contextlib
import hashlib
import importlib.util
import io
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

import harvest

ROOT = Path(__file__).resolve().parents[2]


def by_metric(samples, pipeline=None):
    found = {}
    for s in samples:
        if pipeline is None or s["pipeline"] == pipeline:
            found.setdefault(s["metric"], []).append(s["value_s"])
    return found


class TimeTests(unittest.TestCase):
    def test_offsets_and_fractions(self):
        self.assertEqual(harvest.parse_time("2026-10-08T12:00:00Z"), 1791460800.0)
        self.assertEqual(harvest.parse_time("2026-10-08T05:00:00-0700"), 1791460800.0)
        self.assertEqual(harvest.parse_time("2026-10-08T05:00:00.250000-07:00"), 1791460800.25)
        self.assertEqual(harvest.format_time(1791460800.25), "2026-10-08T12:00:00.250Z")

    def test_naive_time_is_refused(self):
        with self.assertRaises(harvest.InputError):
            harvest.parse_time("2026-10-08T12:00:00")


class LaJournalTests(unittest.TestCase):
    LINES = [
        "-- Boot 0123 --",
        "2026-10-08T05:00:00.100000-07:00 hav-ubuntu python3[10]: live scene backend seconds=0.712 outcome=completed",
        "2026-10-08T05:00:01.200000-07:00 hav-ubuntu python3[10]: live scene backend seconds=0.701 outcome=completed"
        " health_ms=120 exec_ms=575 total_ms=701",
        "2026-10-08T05:00:02.000000-07:00 hav-ubuntu python3[10]: live scene backend seconds=0.002 outcome=preflight_rejected"
        " health_ms=2 exec_ms=0 total_ms=2",
        "2026-10-08T05:00:03.000000-07:00 hav-ubuntu python3[10]: camera pilot backend seconds=1.250 result=ok",
        "2026-10-08T05:00:04.000000-07:00 hav-ubuntu python3[10]: camera pilot backend seconds=0.010 result=BeforeDispatchRejected"
        " health_ms=9 exec_ms=0 total_ms=10",
        "2026-10-08T05:00:05.000000-07:00 hav-ubuntu python3[10]: live scene execution unresolved: TimeoutExpired",
        "2026-10-08T05:00:06.000000-07:00 hav-ubuntu python3[10]: unrelated line seconds=9",
    ]

    def test_current_and_suffixed_lines(self):
        h = harvest.Harvest()
        h.la_journal(self.LINES, "la.txt")
        live = by_metric(h.samples, "qwen_live_la")
        self.assertEqual(live["backend"], [0.712, 0.701])
        self.assertEqual((live["health"], live["exec"], live["total"]), ([0.12], [0.575], [0.701]))
        self.assertEqual(live["backend_other"], [0.002])
        self.assertEqual(live["total_other"], [0.002])
        self.assertEqual(live["outcome:completed"], [1, 1])
        camera = by_metric(h.samples, "camera_chat_la")
        self.assertEqual(camera, {"backend": [1.25], "backend_other": [0.01], "health_other": [0.009],
                                  "exec_other": [0.0], "total_other": [0.01]})
        self.assertEqual(by_metric(h.samples, "la_pilot"), {"event:live_execution_unresolved": [1]})
        first = h.samples[0]
        self.assertEqual(set(first), set(harvest.SAMPLE_KEYS))
        self.assertEqual((first["t"], first["source"]), ("2026-10-08T12:00:00.100Z", "la-journal:la.txt:2"))

    def test_journal_json_export(self):
        message = list(b"live scene backend seconds=0.500 outcome=completed")
        lines = [json.dumps({"__REALTIME_TIMESTAMP": "1791460800000000", "MESSAGE": message}),
                 json.dumps({"__REALTIME_TIMESTAMP": "1791460801000000",
                             "MESSAGE": "camera pilot backend seconds=0.900 result=ok"})]
        h = harvest.Harvest()
        h.la_journal(lines, "la.json")
        self.assertEqual([(s["pipeline"], s["metric"], s["value_s"], s["t"]) for s in h.samples if s["metric"] == "backend"],
                         [("qwen_live_la", "backend", 0.5, "2026-10-08T12:00:00.000Z"),
                          ("camera_chat_la", "backend", 0.9, "2026-10-08T12:00:01.000Z")])

    def test_s04d_alarm_lines_become_events(self):
        lines = [
            "2026-10-08T05:00:00.000000-07:00 hav python3[10]: vLLM StartedAt changed; pilot refusing "
            "old=abc@2026-09-27T21:42:03Z new=def@2026-10-08T11:59:00Z",
            "2026-10-08T05:00:01.000000-07:00 hav python3[10]: camera pilot refusing to start: model-pin.json "
            "unavailable: ValueError",
            "2026-10-08T05:00:02.000000-07:00 hav python3[10]: engine re-pinned old=a new=b engine_epoch=e2",
            "2026-10-08T05:00:03.000000-07:00 hav python3[10]: live scene admission closed: engine_changed",
        ]
        h = harvest.Harvest()
        h.la_journal(lines, "la.txt")
        self.assertEqual(by_metric(h.samples, "la_pilot"), {
            "event:vllm_started_at_changed": [1], "event:pilot_refused_start": [1],
            "event:engine_repinned": [1], "event:live_admission_closed": [1]})

    def test_s18_reopen_and_thermal_lines_become_events(self):
        lines = [
            "2026-10-08T05:00:00.000000-07:00 hav python3[10]: live scene unknown outcome unresolved: exchange_not_ended",
            "2026-10-08T05:00:01.000000-07:00 hav python3[10]: live scene admission stays closed: reopen_limit",
            "2026-10-08T05:00:02.000000-07:00 hav python3[10]: live scene admission reopen deferred: health",
            "2026-10-08T05:00:03.000000-07:00 hav python3[10]: live scene admission reopened: unknown_resolved reopens=1",
            "2026-10-08T05:00:04.000000-07:00 hav python3[10]: live scene thermal_shed level=shed gpu_c=77 tctl_c=None",
            "2026-10-08T05:00:05.000000-07:00 hav python3[10]: live scene thermal_shed cleared gpu_c=70 tctl_c=None",
            "2026-10-08T05:00:06.000000-07:00 hav python3[10]: live scene admission closed: engine_changed",
        ]
        h = harvest.Harvest()
        h.la_journal(lines, "la.txt")
        self.assertEqual(by_metric(h.samples, "la_pilot"), {
            "event:live_unknown_outcome_unresolved": [1], "event:live_admission_stays_closed": [1],
            "event:live_reopen_deferred": [1], "event:live_admission_reopened": [1],
            "event:live_thermal_rung_changed": [1, 1], "event:live_admission_closed": [1]})

    def test_whole_second_times_are_marked(self):
        h = harvest.Harvest()
        h.la_journal(["2026-10-08T05:00:01-0700 hav python3[10]: live scene backend seconds=0.701 outcome=completed"
                      " health_ms=120 exec_ms=575 total_ms=701",
                      "2026-10-08T05:00:02.000000-07:00 hav python3[10]: live scene backend seconds=0.702"
                      " outcome=completed"], "la.txt")
        methods = {(s["metric"], s["value_s"]): s["method"] for s in h.samples}
        self.assertEqual(methods[("total", 0.701)], "la_journal_timing_ms" + harvest.WHOLE_SECOND)
        self.assertEqual(methods[("backend", 0.701)], "la_journal_backend_seconds" + harvest.WHOLE_SECOND)
        self.assertEqual(methods[("backend", 0.702)], "la_journal_backend_seconds")
        h = harvest.Harvest()
        h.la_journal([json.dumps({"__REALTIME_TIMESTAMP": "1791460800000000",
                                  "MESSAGE": "live scene backend seconds=0.500 outcome=completed"})], "la.json")
        self.assertEqual(h.samples[0]["method"], "la_journal_backend_seconds")

    def test_yearless_short_export_is_refused_only_for_timing_lines(self):
        h = harvest.Harvest()
        h.la_journal(["Oct 08 05:00:00 hav-ubuntu systemd[1]: Started something."], "short.txt")
        self.assertEqual(h.samples, [])
        with self.assertRaisesRegex(harvest.InputError, "short-iso-precise"):
            h.la_journal(["Oct 08 05:00:00 hav-ubuntu python3[10]: live scene backend seconds=0.7 outcome=completed"],
                         "short.txt")

    def test_line_formats_match_the_pilot_source(self):
        shared = ROOT / "tools/shared-home/home_shared"
        live, service, pin = shared / "lab_live_pilot.py", shared / "camera_pilot_service.py", shared / "model_pin.py"
        if not live.is_file() or not service.is_file() or not pin.is_file():
            self.skipTest("shared-home sources not present")
        source = live.read_text(encoding="utf-8")
        self.assertIn('"live scene backend seconds=%.3f outcome=%s', source)
        self.assertIn('"%s backend seconds=%.3f result=%s', source)
        self.assertIn("timed_backend(pilot.backend,'camera pilot')", service.read_text(encoding="utf-8"))
        text = source + service.read_text(encoding="utf-8") + (shared / "camera_pilot.py").read_text(encoding="utf-8")
        text += pin.read_text(encoding="utf-8")
        for prefix, _ in harvest.EVENT_LINES:
            self.assertIn(prefix, text)
        # S0.4(d): the StartedAt and refused-start lines exist in the pilot source.
        self.assertIn('"vLLM StartedAt changed; pilot refusing old=%s new=%s"', pin.read_text(encoding="utf-8"))
        self.assertIn("'camera pilot refusing to start: '+reason", service.read_text(encoding="utf-8"))
        rendered = "live scene backend seconds=%.3f outcome=%s" % (0.7123, "completed")
        self.assertTrue(harvest.LIVE_LINE.search(rendered + " health_ms=%d exec_ms=%d total_ms=%d" % (1, 2, 3)))
        self.assertTrue(harvest.CAMERA_LINE.search("%s backend seconds=%.3f result=%s" % ("camera pilot", 1.0, "ok")))
        # Both lines append the pilot's own suffix (home_shared/backend_timing.py).
        self.assertIn('outcome=%s" % (seconds, outcome) + backend_timing.suffix(seconds)', source)
        self.assertIn('result=%s" % (label, seconds, result) + backend_timing.suffix(seconds)', source)

    def test_the_pilot_timing_suffix_parses(self):
        path = ROOT / "tools/shared-home/home_shared/backend_timing.py"
        if not path.is_file():
            self.skipTest("shared-home sources not present")
        spec = importlib.util.spec_from_file_location("la_backend_timing_fixture", path)
        timing = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(timing)
        timing.start()
        with timing.stage("health"):
            pass
        suffix = timing.suffix(0.7123)
        self.assertRegex(suffix, r"^ health_ms=\d+ exec_ms=0 total_ms=712$")
        lines = ["2026-10-08T05:00:00.100000-07:00 hav python3[10]: live scene backend seconds=%.3f outcome=%s%s"
                 % (0.7123, "completed", suffix),
                 "2026-10-08T05:00:01.100000-07:00 hav python3[10]: %s backend seconds=%.3f result=%s%s"
                 % ("camera pilot", 2.5, "ok", timing.suffix(2.5))]
        h = harvest.Harvest()
        h.la_journal(lines, "la.txt")
        self.assertEqual(by_metric(h.samples, "qwen_live_la")["total"], [0.712])
        self.assertEqual(by_metric(h.samples, "qwen_live_la")["exec"], [0.0])
        self.assertEqual(by_metric(h.samples, "camera_chat_la")["total"], [2.5])
        self.assertEqual(h.notes, [])


DUMP_1 = """# harvested_at 2026-10-08T12:00:00Z
# HELP vllm:time_to_first_token_seconds Histogram of time to first token in seconds.
# TYPE vllm:time_to_first_token_seconds histogram
vllm:time_to_first_token_seconds_bucket{engine="0",le="0.1",model_name="Qwen/Qwen3-VL, FP8 {x}"} 2.0
vllm:time_to_first_token_seconds_bucket{engine="0",le="0.25",model_name="Qwen/Qwen3-VL, FP8 {x}"} 5.0
vllm:time_to_first_token_seconds_bucket{engine="0",le="+Inf",model_name="Qwen/Qwen3-VL, FP8 {x}"} 6.0
vllm:time_to_first_token_seconds_sum{engine="0"} 1.0
vllm:num_requests_running{engine="0",model_name="m"} 1.0
vllm:num_requests_waiting{engine="0",model_name="m"} 0.0
"""
DUMP_2 = """# harvested_at 2026-10-08T12:01:00Z
vllm:time_to_first_token_seconds_bucket{engine="0",le="0.1",model_name="Qwen/Qwen3-VL, FP8 {x}"} 3.0
vllm:time_to_first_token_seconds_bucket{engine="0",le="0.25",model_name="Qwen/Qwen3-VL, FP8 {x}"} 7.0
vllm:time_to_first_token_seconds_bucket{engine="0",le="+Inf",model_name="Qwen/Qwen3-VL, FP8 {x}"} 9.0
vllm:e2e_request_latency_seconds_bucket{le="1.0"} 1
vllm:e2e_request_latency_seconds_bucket{le="+Inf"} 1
vllm:num_requests_running{engine="0",model_name="m"} 2.0
vllm:num_requests_waiting{engine="0",model_name="m"} 1.0
"""


class VllmTests(unittest.TestCase):
    def test_delta_between_dumps(self):
        h = harvest.Harvest()
        h.vllm([("b.prom", DUMP_2), ("a.prom", DUMP_1)])
        found = by_metric(h.samples, "vllm")
        # Delta: one request <= 0.1, one in (0.1, 0.25], one overflow at the top finite bound.
        self.assertEqual(sorted(found["ttft"]), [0.1, 0.25, 0.25])
        self.assertEqual(found["num_requests_running"], [1.0, 2.0])
        self.assertEqual(found["num_requests_waiting"], [0.0, 1.0])
        self.assertEqual(found["e2e"], [1.0])  # absent from the first dump: counted from zero
        methods = {s["method"] for s in h.samples if s["metric"] == "ttft"}
        self.assertEqual(methods, {"vllm_histogram_delta_bucket_upper_bound",
                                   "vllm_histogram_delta_bucket_upper_bound_overflow"})
        self.assertTrue(all(s["t"] == "2026-10-08T12:01:00.000Z" for s in h.samples if s["metric"] == "ttft"))

    def test_single_dump_is_cumulative_and_reset_restarts(self):
        h = harvest.Harvest()
        h.vllm([("a.prom", DUMP_1)])
        self.assertEqual(sorted(by_metric(h.samples)["ttft"]), [0.1, 0.1, 0.25, 0.25, 0.25, 0.25])
        h = harvest.Harvest()
        h.vllm([("a.prom", DUMP_2), ("b.prom", DUMP_1.replace("12:00:00Z", "12:02:00Z"))])
        self.assertEqual(len(by_metric(h.samples)["ttft"]), 6)
        self.assertTrue(any("reset" in n for n in h.notes))

    def test_dump_time_is_required(self):
        body = DUMP_1.split("\n", 1)[1]
        with self.assertRaisesRegex(harvest.InputError, "harvested_at"):
            harvest.Harvest().vllm([("a.prom", body)])
        h = harvest.Harvest()
        h.vllm([("a.prom", body)], at="2026-10-08T12:00:00Z")
        self.assertEqual(len(by_metric(h.samples)["ttft"]), 6)

    def test_unknown_families_are_noted(self):
        h = harvest.Harvest()
        h.vllm([("x.prom", "# harvested_at 2026-10-08T12:00:00Z\nother_metric 1\n")])
        self.assertEqual(h.samples, [])
        self.assertTrue(h.notes)


SCHEMA = """
CREATE TABLE sessions(id TEXT PRIMARY KEY, site_id TEXT NOT NULL, camera_id TEXT NOT NULL,
  experiment_id TEXT NOT NULL, name TEXT NOT NULL, state TEXT NOT NULL, started_at REAL, ended_at REAL,
  created_at REAL NOT NULL, revision INTEGER NOT NULL DEFAULT 1, generation INTEGER NOT NULL DEFAULT 0,
  request_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL, reason TEXT, fixture INTEGER NOT NULL DEFAULT 0);
CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id),
  kind TEXT NOT NULL, created_at REAL NOT NULL, payload TEXT NOT NULL);
"""


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "lab copy.sqlite3"
        db = sqlite3.connect(self.path)
        db.executescript(SCHEMA)
        for sid, fixture in (("s1", 0), ("fx", 1)):
            db.execute("INSERT INTO sessions(id,site_id,camera_id,experiment_id,name,state,created_at,request_id,"
                       "request_hash,fixture) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (sid, "victoria", "den_oak", "e", "n", "stopped", 1, "r" + sid, "h", fixture))
        t = 1791460800.0
        events = [
            ("s1", "qwen.observation", {"observed_at": t, "available_at": t + 1.5, "facts": {"x": "words"}}),
            ("fx", "qwen.observation", {"observed_at": t, "available_at": t + 9}),
            ("s1", "vjepa.observation", {"observed_at": t, "available_at": t + .4, "media_ids": list("abcdefgh"),
                                         "result": {"timings_ms": {"total": 100.0}}}),
            ("s1", "vjepa.observation", {"observed_at": t + 1, "available_at": t + 1.5, "media_ids": list("efghijkl"),
                                         "result": None}),
            ("s1", "vjepa.lease", {"request_id": "L1", "state": "requesting", "available_at": t}),
            ("s1", "vjepa.lease", {"request_id": "L1", "state": "ready", "available_at": t + 14}),
            ("s1", "perception.delivery", {"arm": "a", "state": "completed", "current": True, "observed_at": t,
                                           "completed_at": t + .5, "dispatched_at": t + .1, "transport_ms": 300.0}),
            ("s1", "perception.delivery", {"arm": "b", "state": "completed", "current": False, "observed_at": t,
                                           "completed_at": t + .8}),
            ("s1", "perception.delivery", {"arm": "b", "state": "rejected", "current": False, "observed_at": t,
                                           "completed_at": t + .8}),
        ]
        for sid, kind, payload in events:
            db.execute("INSERT INTO events(session_id,kind,created_at,payload) VALUES(?,?,?,?)",
                       (sid, kind, t, json.dumps(payload)))
        db.commit()
        db.close()

    def tearDown(self):
        self.dir.cleanup()

    def test_timings_from_a_snapshot(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        h = harvest.Harvest()
        h.ledger(self.path, la_arm="b")
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertEqual(by_metric(h.samples, "qwen_live_victoria"), {"newest_frame_to_fact": [1.5]})
        vjepa = by_metric(h.samples, "vjepa_live")
        self.assertEqual(vjepa["worker_total"], [0.1])
        self.assertAlmostEqual(vjepa["overhead_upper_bound"][0], 0.3)
        self.assertEqual(vjepa["resent_frame_fraction"], [0.5])
        self.assertEqual(by_metric(h.samples, "vjepa_lease"), {"requesting_to_ready": [14.0]})
        self.assertEqual(by_metric(h.samples, "jetson_detector"),
                         {"capture_to_fact": [0.5], "dispatch_to_fact": [0.4], "transport": [0.3], "stale_flag": [0]})
        self.assertEqual(by_metric(h.samples, "la_detector"), {"capture_to_fact": [0.8], "stale_flag": [1]})
        h = harvest.Harvest()
        h.ledger(self.path)
        self.assertIn("detector_arm_a", {s["pipeline"] for s in h.samples})

    def test_ledger_is_read_only(self):
        db = harvest.open_ledger(self.path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("INSERT INTO events(session_id,kind,created_at,payload) VALUES('s1','x',1,'{}')")
        finally:
            db.close()
        with self.assertRaises(harvest.InputError):
            harvest.open_ledger(Path(self.dir.name) / "missing.sqlite3")

    def test_queries_name_only_schema_columns(self):
        for name, query in harvest.LEDGER_QUERIES.items():
            self.assertNotRegex(query, r"\$\.facts|\$\.observation|description|text", name)
            self.assertRegex(query, r"FROM events e JOIN sessions s", name)


class BrowserTests(unittest.TestCase):
    def test_keyed_and_unkeyed_marks_and_measures(self):
        doc = {"timeOrigin": 1791460800000.0, "entries": [
            {"name": "camera-chat:question:q1", "entryType": "mark", "startTime": 1000.0, "duration": 0},
            {"name": "camera-chat:question", "entryType": "mark", "startTime": 5000.0, "duration": 0},
            {"name": "camera-chat:answer:q1", "entryType": "mark", "startTime": 3100.0, "duration": 0},
            {"name": "camera-chat:answer", "entryType": "mark", "startTime": 7500.0, "duration": 0},
            {"name": "camera-chat:answer", "entryType": "mark", "startTime": 9000.0, "duration": 0},
            {"name": "camera-chat:question-to-answer", "entryType": "measure", "startTime": 10000.0, "duration": 1800.0},
            {"name": "other", "entryType": "mark", "startTime": 1.0},
        ]}
        h = harvest.Harvest()
        h.browser(doc, "marks.json")
        self.assertEqual(sorted(by_metric(h.samples, "camera_chat_browser")["question_to_answer"]), [1.8, 2.1, 2.5])
        self.assertEqual(len(h.notes), 1)  # the second unkeyed answer has no question

    def test_time_origin_required(self):
        with self.assertRaisesRegex(harvest.InputError, "timeOrigin"):
            harvest.Harvest().browser([{"name": "camera-chat:answer", "entryType": "mark", "startTime": 1.0}], "m.json")


class JetsonCaptionTests(unittest.TestCase):
    def test_frame_to_caption_deduplicated(self):
        caption = {"schema_version": 1, "request_id": "r1", "observed_at": 1791460800.0,
                   "generated_at": 1791460801.25, "text": "never read"}
        h = harvest.Harvest()
        h.jetson(harvest._json_objects("\n".join(json.dumps(c) for c in (caption, caption, {"schema_version": 2})),
                                       "c.jsonl"), "c.jsonl")
        self.assertEqual(by_metric(h.samples), {"frame_to_caption": [1.25]})
        self.assertNotIn("never read", json.dumps(h.samples))


class FactLogTests(unittest.TestCase):
    def row(self, **change):
        value = {"schema": "la-fact-log-1", "source_id": "la_qwen_den_oak", "request_id": "a" * 64,
                 "session_id": "b" * 32, "observed_at": "2026-10-08T12:00:00.000Z",
                 "sent_at": "2026-10-08T12:00:00.400Z", "available_at": "2026-10-08T12:00:01.450Z",
                 "latency_s": 1.45, "engine_epoch": "e", "prompt_version": "p", "outcome": "completed",
                 "observation": {"facts": "words"}}
        value.update(change)
        return json.dumps(value, sort_keys=True)

    def test_samples(self):
        h = harvest.Harvest()
        h.fact_log([self.row(), "", self.row(source_id="la_vjepa_den_oak")], "facts.jsonl")
        found = by_metric(h.samples)
        self.assertEqual(found["request_to_fact"], [1.05])
        self.assertEqual(found["newest_frame_to_fact"], [1.45])
        self.assertEqual(found["frame_age_at_send"], [0.4])
        self.assertEqual(found["outcome:completed"], [1])
        self.assertEqual(h.samples[0]["t"], "2026-10-08T12:00:01.450Z")

    def test_bad_rows(self):
        with self.assertRaisesRegex(harvest.InputError, "la-fact-log-1"):
            harvest.Harvest().fact_log([self.row(schema="other")], "f")
        with self.assertRaises(harvest.InputError):
            harvest.Harvest().fact_log([self.row(sent_at="2026-10-08T12:00:00")], "f")
        h = harvest.Harvest()
        h.fact_log([self.row(sent_at="2026-10-08T12:00:02.000Z")], "f")
        self.assertNotIn("request_to_fact", by_metric(h.samples))
        self.assertTrue(any("negative" in n for n in h.notes))


class CliTests(unittest.TestCase):
    def test_writes_sorted_jsonl(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "la.txt", Path(folder) / "out.jsonl"
            source.write_text("\n".join(LaJournalTests.LINES[:3]), encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(harvest.main(["-o", str(output), "la-journal", str(source)]), 0)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(rows and all(list(r) == sorted(harvest.SAMPLE_KEYS) for r in rows))
            self.assertEqual([r["t"] for r in rows], sorted(r["t"] for r in rows))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(harvest.main(["ledger", str(Path(folder) / "none.sqlite3")]), 2)

    def test_python_310_syntax_only(self):
        for path in (Path(harvest.__file__), Path(harvest.__file__).with_name("report.py")):
            source = path.read_text(encoding="utf-8")
            self.assertNotRegex(source, r"except\*|tomllib|typing\.Self|datetime\.UTC|TaskGroup", path.name)
            self.assertIsNone(re.search(r"fromisoformat\(", source), path.name)  # 3.10 rejects 'Z' and '-0700'


if __name__ == "__main__":
    unittest.main()
