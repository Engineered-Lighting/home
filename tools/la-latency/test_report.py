"""report.py on synthetic samples only."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import harvest
import report

SLO = json.loads(Path(__file__).with_name("slo.json").read_text(encoding="utf-8"))
T0 = 1791460800.0  # 2026-10-08T12:00:00Z


def sample(pipeline, metric, value, t, method="synthetic"):
    return {"pipeline": pipeline, "metric": metric, "value_s": value, "t": harvest.format_time(t),
            "source": "test:%s" % pipeline, "method": method, "_t": harvest.parse_time(harvest.format_time(t))}


def week(n=600, days=8.0, overhead=0.08, la_total=0.7, method="synthetic"):
    """n occupied Qwen requests over `days`, each with its LA timing line."""
    rows, step = [], days * report.DAY / (n - 1)
    for i in range(n):
        sent = T0 + i * step
        rows.append(sample("qwen_live_la", "total", la_total, sent + 0.05 + la_total, method))
        rows.append(sample("qwen_live_victoria", "request_to_fact", la_total + overhead, sent + la_total + overhead))
    return rows


def back_to_back(n, cadence, line_before_fact, send_after_fact, lead=0.05, start=T0):
    """One request in flight: each send follows the previous fact, at most
    every `cadence` s. The LA line is logged `line_before_fact` s before the
    fact; the call started `lead` s after the send. LA totals vary 0.70-0.90 s."""
    rows, sent, expected = [], start, []
    for i in range(n):
        total = 0.70 + (i * 37 % 21) / 100
        end = sent + lead + total
        available = end + line_before_fact
        rows.append(sample("qwen_live_la", "total", total, end))
        rows.append(sample("qwen_live_victoria", "request_to_fact", available - sent, available))
        expected.append(round(available - sent - total, 3))
        sent = max(sent + cadence, available + send_after_fact)
    return rows, expected


class StatisticsTests(unittest.TestCase):
    def test_nearest_rank(self):
        values = list(range(1, 101))
        self.assertEqual((report.percentile(values, .5), report.percentile(values, .95)), (50, 95))
        self.assertEqual(report.percentile([3.0], .95), 3.0)
        self.assertEqual(report.percentile([1, 2, 3, 4], .5), 2)

    def test_samples_are_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "s.jsonl"
            good = {k: v for k, v in sample("vllm", "ttft", .1, T0).items() if k != "_t"}
            path.write_text(json.dumps(good) + "\n\n", encoding="utf-8")
            self.assertEqual(report.load_samples([path])[0]["_t"], T0)
            path.write_text(json.dumps({**good, "outcome": "x"}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(report.ReportError, "exactly"):
                report.load_samples([path])
            path.write_text(json.dumps({**good, "value_s": "1"}) + "\n", encoding="utf-8")
            with self.assertRaises(report.ReportError):
                report.load_samples([path])


class BaselineTests(unittest.TestCase):
    def test_every_plan_row_is_listed_with_n_p50_p95_date_and_method(self):
        rows = [sample("camera_chat_jetson", "frame_to_caption", v, T0 + i * report.DAY, "caption")
                for i, v in enumerate((3.04, 3.10, 3.17))]
        rows += [sample("la_detector", "stale_flag", v, T0) for v in (0, 0, 0, 1)]
        text = report.render(rows, SLO, generated="2026-10-08")
        for entry in SLO["pipelines"]:
            self.assertIn("| " + entry["label"] + " |", text)
        self.assertIn("| Camera chat, frame observed to caption published (Jetson clock) | 3 | 3.100 s | 3.170 s "
                      "| 2026-10-08 to 2026-10-10 | caption |", text)
        self.assertIn("| Camera chat, question to answer on screen | 0 | no data | no data | no data | no data |", text)
        self.assertIn("rate 25.0%", text)
        self.assertIn("p50 ≤ 1.800 s, p95 ≤ 2.600 s", text)

    def test_rows_without_an_s02_source_say_so(self):
        text = report.render([], SLO, generated="2026-10-08")
        self.assertIn("| Qwen live, request sent to fact recorded | 0 | no data | no data | no data "
                      "| no source before S1.3: the Lab ledger has no send time; only la-fact-log-1 has sent_at |", text)
        self.assertIn("| Guard: voice TTFT (synthetic voice probe) | 0 | no data | no data | no data "
                      "| no samples: run tools/la-latency/voice_probe.py on LA (S1.4; cadence_ab.py runs it for the "
                      "A/B) |", text)

    def test_voice_probe_samples_feed_the_d2_guard_and_supporting_rows(self):
        rows = []
        for i in range(30):
            before, after = T0 + i * 60, T0 + 86400 + i * 60
            rows += [sample("voice_probe", "ttft", .050, before, "voice_probe_stream_first_content_chunk"),
                     sample("voice_probe", "ttft", .054, after, "voice_probe_stream_first_content_chunk"),
                     sample("voice_probe", "e2e", .060, after, "voice_probe_stream_done"),
                     sample("la_gpu", "gpu_temp_c", 58, after, "sidecar_metrics_gpu_temp_c"),
                     sample("la_cpu", "tctl_c", 71.5, after, "hwmon_k10temp_tctl")]
        index = report.Index(rows, SLO["min_samples_per_statistic"])
        decided = {row[1]: row for row in report.decision_rows(index, SLO, T0 + 86400)}
        voice = decided["voice TTFT p95 with live ≤ 1.10 × no-live"]
        self.assertEqual((voice[2], voice[3]), ("1.08× (0.054 s against 0.050 s)", report.HOLDS))
        self.assertEqual(decided["GPU temperature p95 ≤ 70 °C"][3], report.HOLDS)
        text = report.render(rows, SLO, generated="2026-10-09")
        self.assertIn("| Synthetic voice probe, send to end of stream (max_tokens 1) | 30 | 0.060 s | 0.060 s |", text)
        self.assertIn("| LA GPU temperature, °C (sidecar, per probe slot) | 30 | 58 | 58 |", text)
        self.assertIn("| LA CPU Tctl, °C (k10temp, per probe slot) | 30 | 71.5 | 71.5 |", text)

    def test_events_and_alarms(self):
        rows = [sample("la_pilot", "event:live_execution_unresolved", 1, T0),
                sample("qwen_live_la", "outcome:completed", 1, T0)]
        text = report.render(rows, SLO, generated="2026-10-08")
        self.assertIn("| la_pilot | event:live_execution_unresolved | 1 | 2026-10-08T12:00:00.000Z |", text)
        self.assertIn("**Alarm:** event:live_execution_unresolved", text)

    def test_s04d_alarms(self):
        for metric, meaning in (("event:vllm_started_at_changed", "re-pinned (S0.4(d))"),
                                ("event:pilot_refused_start", "camera chat and LA facts are down"),
                                ("event:live_admission_closed", "runtime_revision")):
            text = report.render([sample("la_pilot", metric, 1, T0)], SLO, generated="2026-10-08")
            alarm = [line for line in text.splitlines() if line.startswith("**Alarm:**")]
            self.assertEqual(len(alarm), 1, metric)
            self.assertIn(metric + ": ", alarm[0])
            self.assertIn(meaning, alarm[0])
        text = report.render([sample("la_pilot", "event:engine_repinned", 1, T0)], SLO, generated="2026-10-08")
        self.assertIn("| la_pilot | event:engine_repinned | 1 |", text)
        self.assertNotIn("**Alarm:**", text)

    def test_s18_alarms(self):
        for metric, meaning in (("event:live_admission_stays_closed", "reopen was refused"),
                                ("event:live_unknown_outcome_unresolved", "until reconciled")):
            text = report.render([sample("la_pilot", metric, 1, T0)], SLO, generated="2026-10-08")
            alarm = [line for line in text.splitlines() if line.startswith("**Alarm:**")]
            self.assertEqual(len(alarm), 1, metric)
            self.assertIn(metric + ": ", alarm[0])
            self.assertIn(meaning, alarm[0])
        for metric in ("event:live_admission_reopened", "event:live_reopen_deferred", "event:live_thermal_rung_changed"):
            text = report.render([sample("la_pilot", metric, 1, T0)], SLO, generated="2026-10-08")
            self.assertIn("| la_pilot | %s | 1 |" % metric, text)
            self.assertNotIn("**Alarm:**", text)

    def test_window_filters(self):
        rows = [sample("vllm", "ttft", .1, T0), sample("vllm", "ttft", .2, T0 + report.DAY)]
        text = report.render(rows, SLO, since=T0 + 1, generated="2026-10-09")
        self.assertIn("from 1 samples", text)


class DecisionTests(unittest.TestCase):
    def record(self, rows, **kwargs):
        text = report.render(rows, SLO, generated="2026-10-16", **kwargs)
        return text.split("## S2.1 decision record", 1)[1]

    def test_short_window_writes_no_record(self):
        record = self.record(week(n=600, days=3))
        self.assertIn("No decision record", record)
        self.assertNotIn("| Gate |", record)
        record = self.record(week(n=100, days=8))
        self.assertIn("No decision record", record)

    def test_every_criterion_listed_with_insufficient_data(self):
        record = self.record(week())
        criteria = [line for line in record.splitlines() if line.startswith("| D")]
        self.assertEqual(len(criteria), 16)
        self.assertIn("| D1 stay | Qwen overhead p50 ≤ 120 ms | 80 ms (600 of 600 requests paired) | holds |", record)
        self.assertIn("| D1 stay | gate-to-send p95 ≤ 0.500 s | insufficient data (no samples) | insufficient data |",
                      record)
        self.assertIn("(no --live-since split)", record)
        # Nothing produces these samples yet: a longer window cannot decide D1.
        self.assertIn("- **D1:** Undecided: insufficient data. A longer window cannot help until a source or input "
                      "exists: V-JEPA overhead p50 ≤ 100 ms (no samples); gate-to-send p95 ≤ 0.500 s (no samples);",
                      record)
        self.assertNotIn("A longer window helps", record)

    def test_a_longer_window_is_advised_only_when_it_can_help(self):
        rows = week()
        rows += [sample("vjepa_live", "overhead_upper_bound", .09, T0 + i) for i in range(30)]
        rows += [sample("occupancy_gate", "edge_to_first_send", .3, T0 + i) for i in range(5)]
        rows += [sample("home_context", "la_supplied_flag", 1, T0 + i) for i in range(100)]
        record = self.record(rows)
        self.assertIn("| D1 stay | gate-to-send p95 ≤ 0.500 s | insufficient data (n=5, needs 20) | insufficient data |",
                      record)
        verdict = [line for line in record.splitlines() if line.startswith("- **D1:**")][0]
        self.assertIn("A longer window helps: gate-to-send p95 ≤ 0.500 s (n=5, needs 20).", verdict)
        self.assertIn("A longer window cannot help until a source or input exists: upload > 30% of overhead "
                      "(no samples);", verdict)

    def test_vjepa_upper_bound_above_the_limit_cannot_be_extended_away(self):
        rows = week()
        rows += [sample("vjepa_live", "overhead_upper_bound", .13, T0 + i) for i in range(30)]
        record = self.record(rows)
        self.assertIn("| D1 stay | V-JEPA overhead p50 ≤ 100 ms | insufficient data (upper bound 130 ms only; "
                      "transport_overhead has no samples) | insufficient data |", record)
        verdict = [line for line in record.splitlines() if line.startswith("- **D1:**")][0]
        cannot = verdict.split("A longer window cannot help", 1)[1]
        self.assertIn("V-JEPA overhead p50 ≤ 100 ms (upper bound 130 ms only;", cannot)

    def test_d1_stay_and_build(self):
        rows = week()
        rows += [sample("vjepa_live", "overhead_upper_bound", .09, T0 + i) for i in range(30)]
        rows += [sample("occupancy_gate", "edge_to_first_send", .3, T0 + i) for i in range(30)]
        rows += [sample("home_context", "la_supplied_flag", 1 if i % 50 else 0, T0 + i) for i in range(100)]
        record = self.record(rows)
        self.assertIn("90 ms (upper bound) | holds", record)
        self.assertIn("- **D1:** Stay with request/response.", record)
        record = self.record(week(overhead=0.2))
        self.assertIn("| D1 build | Qwen overhead p50 > 150 ms | 200 ms (600 of 600 requests paired) | holds |", record)
        self.assertIn("- **D1:** Build S2.2 (a build criterion holds: Qwen overhead p50 > 150 ms).", record)

    def test_d1_gray_zone_between_stay_and_build(self):
        rows = week(overhead=0.13)
        record = self.record(rows)
        self.assertIn("| D1 stay | Qwen overhead p50 ≤ 120 ms | 130 ms (600 of 600 requests paired) | does not hold |",
                      record)
        self.assertIn("| D1 build | Qwen overhead p50 > 150 ms | 130 ms (600 of 600 requests paired) | does not hold |",
                      record)
        self.assertIn("- **D1:** Not stay (fails on Qwen overhead p50 ≤ 120 ms); build is still open. Undecided: "
                      "insufficient data. A longer window cannot help until a source or input exists: upload > 30% "
                      "of overhead (no samples);", record)
        rows += [sample("qwen_live_victoria", "upload", .01, T0 + i) for i in range(30)]
        rows += [sample("qwen_live_victoria", "body_bytes", 100000, T0 + i) for i in range(30)]
        rows += [sample("link", "upstream_mbps", 50.0, T0 + i) for i in range(30)]
        rows += [sample("vjepa_live", "resent_frame_fraction", .1, T0 + i) for i in range(30)]
        record = self.record(rows)
        self.assertNotIn("| D1 build |", "\n".join(l for l in record.splitlines() if "insufficient data |" in l))
        self.assertIn("- **D1:** Neither stay nor build: stay fails on Qwen overhead p50 ≤ 120 ms, and no build "
                      "criterion holds. The plan has no rule for this gap; an owner decision is needed.", record)
        self.assertNotIn("Undecided", [l for l in record.splitlines() if l.startswith("- **D1:**")][0])

    def test_d1_upstream_and_resend(self):
        rows = week()
        rows += [sample("qwen_live_victoria", "body_bytes", 200000, T0 + i) for i in range(30)]
        rows += [sample("link", "upstream_mbps", 5.0, T0 + i) for i in range(30)]
        rows += [sample("vjepa_live", "resent_frame_fraction", .5, T0 + i) for i in range(30)]
        record = self.record(rows)
        self.assertIn("uses > 25% of Victoria upstream | 32.0% | holds", record)
        self.assertIn("(frames as proxy) | 50% | holds", record)

    def test_d2_guard_split(self):
        live = T0 + 4 * report.DAY
        rows = week()
        rows += [sample("voice_probe", "ttft", .100, T0 + i) for i in range(30)]
        rows += [sample("voice_probe", "ttft", .105, live + i) for i in range(30)]
        rows += [sample("camera_chat_la", "backend", 1.0, T0 + i) for i in range(30)]
        rows += [sample("camera_chat_la", "backend", 1.2, live + i) for i in range(30)]
        rows += [sample("la_gpu", "gpu_temp_c", 65, live + i) for i in range(30)]
        rows += [sample("qwen_live_victoria", "queue_busy_flag", 1 if i % 10 == 0 else 0, live + i) for i in range(30)]
        record = self.record(rows, live_since=live)
        self.assertIn("| D2 guard | voice TTFT p95 with live ≤ 1.10 × no-live | 1.05× (0.105 s against 0.100 s) | holds |",
                      record)
        self.assertIn("camera-chat backend p95 with live ≤ 1.10 × no-live | 1.20× (1.200 s against 1.000 s) | does not hold |",
                      record)
        self.assertIn("- **D2:** Do not raise the cadence: the S1.4 guard does not hold (camera-chat backend p95", record)

    def test_d2_baseline_comes_from_before_live_since_whatever_since_says(self):
        # S2.1 runs with --since at S1.5 activation; live Qwen started earlier (S1.3).
        live = T0 - 10 * report.DAY
        rows = week()
        rows += [sample("voice_probe", "ttft", .500, live - 5 * report.DAY + i) for i in range(30)]
        rows += [sample("voice_probe", "ttft", .100, live - report.DAY + i) for i in range(30)]
        rows += [sample("voice_probe", "ttft", 9.9, live + report.DAY + i) for i in range(30)]  # live, before --since
        rows += [sample("voice_probe", "ttft", .105, T0 + i) for i in range(30)]
        rows += [sample("camera_chat_la", "backend", 1.0, live - report.DAY + i) for i in range(30)]
        rows += [sample("camera_chat_la", "backend", 1.05, T0 + i) for i in range(30)]
        record = self.record(rows, since=T0, live_since=live, baseline_since=live - 2 * report.DAY)
        self.assertIn("| D2 guard | voice TTFT p95 with live ≤ 1.10 × no-live | 1.05× (0.105 s against 0.100 s) | holds |",
                      record)
        self.assertIn("| D2 guard | camera-chat backend p95 with live ≤ 1.10 × no-live | 1.05× (1.050 s against 1.000 s) "
                      "| holds |", record)
        self.assertIn("the no-live baseline is every sample before 2026-09-28T12:00:00.000Z from "
                      "2026-09-26T12:00:00.000Z, whatever --since says", record)
        record = self.record(rows, since=T0, live_since=live)  # no --baseline-since: the older 0.5 s samples count
        self.assertIn("| D2 guard | voice TTFT p95 with live ≤ 1.10 × no-live | 0.21× (0.105 s against 0.500 s) | holds |",
                      record)

    def test_vllm_ttft_never_decides_the_voice_guard(self):
        live = T0 + 4 * report.DAY
        rows = week()
        rows += [sample("vllm", "ttft", .1, T0 + i) for i in range(30)]
        rows += [sample("vllm", "ttft", .5, live + i) for i in range(30)]
        record = self.record(rows, live_since=live)
        self.assertIn("| D2 guard | voice TTFT p95 with live ≤ 1.10 × no-live | insufficient data (0 no-live and 0 live "
                      "samples, needs 20 each) | insufficient data |", record)
        self.assertNotIn("Do not raise the cadence", record)
        self.assertIn("| vLLM TTFT, all clients (bucket upper bounds; not the voice guard) | 60 |",
                      report.render(rows, SLO, generated="2026-10-16"))

    def test_d2_short_baseline_is_not_fixed_by_a_longer_window(self):
        live = T0 + 4 * report.DAY
        rows = week()
        rows += [sample("voice_probe", "ttft", .100, T0 + i) for i in range(5)]
        rows += [sample("voice_probe", "ttft", .105, live + i) for i in range(30)]
        verdict = [l for l in self.record(rows, live_since=live).splitlines() if l.startswith("- **D2:**")][0]
        self.assertIn("A longer window cannot help until a source or input exists: voice TTFT p95 with live ≤ 1.10 × "
                      "no-live (5 no-live and 30 live samples, needs 20 each)", verdict)

    def test_d3(self):
        rows = week()
        rows += [sample("la_detector", "capture_to_fact", .40, T0 + i) for i in range(30)]
        rows += [sample("jetson_detector", "capture_to_fact", .45, T0 + i) for i in range(30)]
        rows += [sample("la_detector", "stale_flag", 0, T0 + i) for i in range(100)]
        self.assertIn("- **D3:** Keep detector and pose on LA", self.record(rows))
        rows += [sample("la_detector", "stale_flag", 1, T0 + 200 + i) for i in range(5)]
        self.assertIn("- **D3:** Retire the LA detector and pose (S2.3).", self.record(rows))

    def test_pairing_skips_ambiguous_matches(self):
        rows = [sample("qwen_live_victoria", "request_to_fact", 1.0, T0 + 1.0),
                sample("qwen_live_la", "total", .7, T0 + .8), sample("qwen_live_la", "total", .7, T0 + .9),
                sample("qwen_live_victoria", "request_to_fact", 1.0, T0 + 11.0),
                sample("qwen_live_la", "total", .9, T0 + 10.95)]
        index = report.Index(rows, 1)
        self.assertEqual([round(v, 3) for v in report.paired_overhead(index, .25)], [0.1])

    def test_pairing_needs_the_whole_la_call_inside_the_request(self):
        # The LA line falls inside [sent - tol, available + tol], but the call began 0.4 s before the send.
        rows = [sample("qwen_live_victoria", "request_to_fact", 1.0, T0 + 1.0), sample("qwen_live_la", "total", .9, T0 + .5)]
        self.assertEqual(report.paired_overhead(report.Index(rows, 1), .25), [])

    def test_back_to_back_requests_all_pair(self):
        # S1.4 cadence: 1.0 s interval, one request in flight. The previous LA line lands
        # within the tolerance before the next send; it must not make the window ambiguous.
        for line_before_fact, send_after_fact in ((0.08, 0.05), (0.15, 0.05), (0.08, 0.0)):
            rows, expected = back_to_back(2000, 1.0, line_before_fact, send_after_fact)
            paired = report.paired_overhead(report.Index(rows, 1), .25)
            self.assertEqual([round(v, 3) for v in paired], expected, (line_before_fact, send_after_fact))
        rows, expected = back_to_back(2000, 1.1, 0.08, 0.05)  # plan target: about 1.05 s per request
        self.assertEqual(len(report.paired_overhead(report.Index(rows, 1), .25)), 2000)

    def test_back_to_back_window_decides_d1(self):
        rows = []
        for d in range(9):  # one occupied burst of 75 back-to-back requests a day
            rows += back_to_back(75, 1.0, 0.08, 0.05, start=T0 + d * report.DAY)[0]
        record = self.record(rows)
        self.assertIn("| D1 stay | Qwen overhead p50 ≤ 120 ms | 130 ms (675 of 675 requests paired) | does not hold |",
                      record)

    def test_whole_second_la_lines_are_left_out_of_pairing(self):
        record = self.record(week(method="la_journal_timing_ms" + harvest.WHOLE_SECOND))
        self.assertIn("600 LA timing lines have whole-second journal times", record)
        self.assertIn("| D1 stay | Qwen overhead p50 ≤ 120 ms | insufficient data (0 of 600 requests paired) |", record)


class SloTests(unittest.TestCase):
    def test_plan_numbers(self):
        targets = {e["id"]: e for e in SLO["pipelines"]}
        self.assertEqual(targets["camera_chat_question_to_answer"]["targets"][0], {"stage": "after Stage 1", "p50_s": 1.8, "p95_s": 2.6})
        self.assertEqual(targets["qwen_live_request_to_fact"]["targets"][1], {"stage": "after Stage 1", "p50_s": 1.05, "p95_s": 1.4})
        self.assertEqual(targets["occupancy_edge_to_first_send"]["targets"][0]["p95_s"], 0.9)
        self.assertEqual(targets["ha_occupancy_read"]["targets"][0]["p95_s"], 0.030)
        d = SLO["decision"]
        self.assertEqual((d["window"]["min_days"], d["window"]["min_occupied_qwen_requests"]), (7, 500))
        self.assertEqual(d["D1"]["stay"], {"qwen_overhead_p50_max_s": 0.120, "qwen_overhead_p95_max_s": 0.250,
                                           "vjepa_overhead_p50_max_s": 0.100, "gate_to_send_p95_max_s": 0.5,
                                           "la_supplied_min_rate": 0.95})
        self.assertEqual(d["D1"]["build"], {"qwen_overhead_p50_over_s": 0.150, "qwen_overhead_p95_over_s": 0.400,
                                            "upload_share_over": 0.30, "upstream_share_over": 0.25,
                                            "vjepa_resend_share_over": 0.40})
        self.assertEqual((d["D2"]["guard_ratio_max"], d["D3"]["stale_rate_max"]), (1.10, 0.02))
        self.assertEqual(len({e["id"] for e in SLO["pipelines"] + SLO["supporting"]}),
                         len(SLO["pipelines"]) + len(SLO["supporting"]))


class CliTests(unittest.TestCase):
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as folder:
            journal, samples, out = (Path(folder) / n for n in ("la.txt", "s.jsonl", "baseline.md"))
            journal.write_text("2026-10-08T12:00:00.000Z hav python3[1]: live scene backend seconds=0.700 outcome=completed"
                               " health_ms=100 exec_ms=600 total_ms=700\n", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(harvest.main(["-o", str(samples), "la-journal", str(journal)]), 0)
            self.assertEqual(report.main([str(samples), "--date", "2026-10-08", "-o", str(out)]), 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("| LA live-scene total | 1 | 0.700 s | 0.700 s | 2026-10-08 | la_journal_timing_ms |", text)
            self.assertEqual(report.main([str(samples), "--since", "2026-10-08T00:00:00Z", "--live-since",
                                          "2026-10-01T00:00:00Z", "--baseline-since", "2026-09-30T00:00:00Z",
                                          "-o", str(out)]), 0)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(report.main([str(samples), "--since", "yesterday"]), 2)
                self.assertEqual(report.main([str(samples), "--baseline-since", "2026-09-30"]), 2)


if __name__ == "__main__":
    unittest.main()
