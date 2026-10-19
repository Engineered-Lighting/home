"""la-fact-log-1 (speed plan section 15): the exact line shape, words only,
UTC-day partitioning, 14-day retention pruned daily, buffered appends with a
periodic fsync, torn-line repair and the day bound. Temp directories only."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import la_fact_log
import qwen_scene as scene
from la_fact_log import FactLog
from test_qwen_live import FACTS, Clock

REPO = Path(__file__).resolve().parents[3]
ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
T0 = datetime(2026, 10, 8, 23, 59, 50, tzinfo=timezone.utc).timestamp()
SESSION = "5e" * 16


def request(index, observed, *, sent=None, epoch="la-epoch"):
    request_id = scene.digest({"fixture": index})
    reference = {"request_id": request_id, "site_id": "victoria", "session_id": SESSION, "profile": {},
                 "body_sha256": scene.digest({"body": index}), "prompt_version": scene.PROMPT_VERSION,
                 "task": scene.TASK, "deadline": observed + 8}
    frames = [{"media_id": "ring-" + "%064x" % (index * 10 + k), "frame_id": f"cap-1-0-{index * 10 + k}",
               "capture_epoch": "cap-1", "observed_at": observed - 3 + 1.5 * k} for k in range(3)]
    return {"reference": reference, "frames": frames, "sent_at": observed + .1 if sent is None else sent,
            "profile": {"engine_epoch": epoch}, "context": scene.model_context("evening 19:40"),
            "prior_supplied": False}


def draft(req, generated, *, text=None, epoch="la-epoch"):
    return scene.build_observation(
        site_id="victoria", camera_id="den", session_id=req["reference"]["session_id"],
        request_id=req["reference"]["request_id"], frames=req["frames"], generated_at=generated,
        prompt_version=scene.PROMPT_VERSION, context=req["context"], body_sha256=req["reference"]["body_sha256"],
        engine_epoch=epoch, artifact_sha256="a" * 64, content=json.dumps(FACTS) if text is None else text,
        finish_reason="stop", prior_supplied=False)


class LogFixture:
    def build(self, **kwargs):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.clock, self.mono = Clock(T0), Clock(100.0)
        self.log = FactLog(self.directory.name, clock=self.clock, monotonic=self.mono, private=False, **kwargs)
        self.addCleanup(self.log.close)
        return self.log

    def files(self):
        return sorted(p.name for p in Path(self.directory.name).iterdir())

    def lines(self, name=None):
        name = name or self.files()[-1]
        return [json.loads(line) for line in (Path(self.directory.name) / name).read_text("ascii").splitlines()]

    def write(self, index, *, available_after=.8, outcome="completed", **kwargs):
        req = request(index, self.clock.now, **kwargs)
        self.clock.now += available_after
        return req, self.log.terminal(req, outcome, draft(req, self.clock.now) if outcome == "completed" else None)


class ShapeTests(LogFixture, unittest.TestCase):
    def setUp(self):
        self.build()

    def test_a_line_is_exactly_the_section_15_object_with_sorted_keys(self):
        req, result = self.write(1)
        self.assertEqual(result, {"state": "available", "event_id": None})
        raw = (Path(self.directory.name) / self.files()[0]).read_text("ascii")
        [value] = self.lines()
        self.assertEqual(list(value), sorted(value))
        self.assertEqual(tuple(value), la_fact_log.KEYS)
        self.assertEqual(raw, json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
        self.assertEqual((value["schema"], value["source_id"], value["outcome"]), ("la-fact-log-1", "la_qwen_den_oak", "completed"))
        self.assertEqual((value["request_id"], value["session_id"]), (req["reference"]["request_id"], SESSION))
        self.assertEqual((value["engine_epoch"], value["prompt_version"]), ("la-epoch", scene.PROMPT_VERSION))
        for key in ("observed_at", "sent_at", "available_at"):
            self.assertRegex(value[key], ISO)
        newest = req["frames"][-1]["observed_at"]
        self.assertEqual(value["observed_at"], la_fact_log.iso(la_fact_log.milliseconds(newest)))
        self.assertEqual(value["available_at"], la_fact_log.iso(la_fact_log.milliseconds(self.clock.now)))
        seconds = lambda text: datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()
        self.assertAlmostEqual(value["latency_s"], seconds(value["available_at"]) - seconds(value["observed_at"]), places=6)
        receipt = value["observation"]
        self.assertEqual(set(receipt), la_fact_log.RECEIPT_KEYS)
        self.assertEqual((receipt["schema"], receipt["status"], receipt["facts"]), ("qwen-observation-1", "valid", FACTS))
        self.assertEqual(receipt["available_at"], self.clock.now)  # first_receipt on the Jetson clock

    def test_words_only_no_image_base64_or_reply_text(self):
        req = request(2, self.clock.now)
        self.clock.now += .5
        self.log.terminal(req, "completed", draft(req, self.clock.now, text="The person is reading a book."))
        raw = (Path(self.directory.name) / self.files()[0]).read_text("ascii")
        for forbidden in ("data:image", "base64", "/9j/", "reading a book", "image_url"):
            self.assertNotIn(forbidden, raw)
        [value] = self.lines()
        self.assertEqual((value["observation"]["status"], value["observation"]["facts"]), ("invalid", None))
        good = draft(req, self.clock.now)
        receipt = scene.first_receipt(good, self.clock.now)
        common = dict(request_id=req["reference"]["request_id"], session_id=SESSION, observed_at=T0, sent_at=T0,
                      available_at=T0 + 1, engine_epoch="e", prompt_version=scene.PROMPT_VERSION, outcome="completed")
        la_fact_log.line(**common, observation=receipt)
        for bad in ({**receipt, "image_base64": "/9j/4AAQ"}, {**receipt, "reply": "free text"},
                    {k: v for k, v in receipt.items() if k != "facts"}, {**receipt, "schema": "qwen-observation-2"},
                    {**receipt, "evidence_roots": ["media:" + "x" * 9000]}):
            with self.assertRaises(ValueError):
                la_fact_log.line(**common, observation=bad)
        with self.assertRaises(ValueError):
            la_fact_log.line(**{**common, "outcome": "failed"}, observation=receipt)  # only an answer carries one
        for edit in ({"request_id": "short"}, {"session_id": "not-hex"}, {"outcome": "Completed!"}, {"engine_epoch": None},
                     {"observed_at": float("nan")}):
            with self.assertRaises(ValueError):
                la_fact_log.line(**{**common, **edit}, observation=None)

    def test_outcomes_without_an_answer_are_lines_with_a_null_observation(self):
        for index, outcome in enumerate(("unknown_resolved", "failed", "queue_expired"), 3):
            _req, result = self.write(index, outcome=outcome, epoch="sent-epoch")
            self.assertEqual(result["state"], "available")
        values = self.lines()
        self.assertEqual([v["outcome"] for v in values], ["unknown_resolved", "failed", "queue_expired"])
        self.assertEqual({v["observation"] is None for v in values}, {True})
        self.assertEqual({v["engine_epoch"] for v in values}, {"sent-epoch"})  # the epoch it was sent under

    def test_times_keep_their_order_and_a_request_is_written_once(self):
        req = request(9, self.clock.now, sent=self.clock.now - 5)  # a wall-clock step back
        self.log.terminal(req, "unknown_resolved", None)
        [value] = self.lines()
        self.assertLessEqual(value["observed_at"], value["sent_at"])
        self.assertLessEqual(value["sent_at"], value["available_at"])
        self.assertGreaterEqual(value["latency_s"], 0)
        self.assertEqual(self.log.terminal(req, "unknown_resolved", None)["state"], "duplicate")
        self.assertEqual(len(self.lines()), 1)

    def test_a_late_answer_is_written_but_reported_expired_on_receipt(self):
        _req, result = self.write(11, available_after=12)
        self.assertEqual(result["state"], "expired_on_receipt")
        self.assertEqual(len(self.lines()), 1)

    def test_the_workstation_harvest_tool_reads_the_lines(self):
        for index in range(3):
            self.write(20 + index)
        spec = importlib.util.spec_from_file_location("la_harvest_fixture", REPO / "tools" / "la-latency" / "harvest.py")
        harvest = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harvest)
        result = harvest.Harvest()
        text = (Path(self.directory.name) / self.files()[0]).read_text("ascii")
        result.fact_log(text.splitlines(), self.files()[0])
        self.assertEqual(result.notes, [])  # latency_s agrees with the timestamps

    def test_the_home_context_converter_reads_the_lines(self):
        # The other reader of this log (tools/convert-legacy-den-context.js
        # --la-facts) refuses the whole file on any line it cannot read.
        converter = REPO / "tools" / "convert-legacy-den-context.js"
        node = shutil.which("node")
        if node is None or not converter.is_file():
            self.skipTest("node or the home-context converter not present")
        self.write(30)                                   # a valid answer
        self.write(31, outcome="unknown_resolved")       # LA's reconcile: no answer
        self.write(32, outcome="failed")
        req = request(33, self.clock.now)
        self.clock.now += .5
        self.log.terminal(req, "completed", draft(req, self.clock.now, text="free words"))  # an invalid receipt
        self.write(34, available_after=12)               # completed, expired on receipt (the next UTC day)
        self.assertEqual(len(self.files()), 2)
        paths = [str(Path(self.directory.name) / name) for name in self.files()]
        script = ("const c=require(process.argv[1]),fs=require('fs');"
                  "const lines=[].concat(...process.argv.slice(2).map(p=>fs.readFileSync(p,'ascii').split(/\\r?\\n/)));"
                  "console.log(JSON.stringify(c.parseLaFactLog(lines).map(f=>[f.request_id,f.usable])));")
        result = subprocess.run([node, "-e", script, str(converter), *paths], capture_output=True, text=True,
                                timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        usable = dict(json.loads(result.stdout))
        expected = {request(i, 0)["reference"]["request_id"]: ok
                    for i, ok in ((30, True), (31, False), (32, False), (33, False), (34, True))}
        self.assertEqual(usable, expected)


class FileTests(LogFixture, unittest.TestCase):
    def test_lines_are_partitioned_by_the_utc_day_of_available_at(self):
        self.build()
        self.write(1)               # 23:59:50.8 on 10-08
        self.clock.now = T0 + 9.5
        self.write(2)               # 00:00:00.3 on 10-09
        self.assertEqual(self.files(), ["la-fact-log-1-2026-10-08.jsonl", "la-fact-log-1-2026-10-09.jsonl"])
        for name in self.files():
            day = name[len("la-fact-log-1-"):-len(".jsonl")]
            self.assertEqual({v["available_at"][:10] for v in self.lines(name)}, {day})

    def test_retention_prunes_old_day_files_once_a_day_and_nothing_else(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        today = datetime.fromtimestamp(T0, timezone.utc).date()
        names = [f"la-fact-log-1-{today - timedelta(days=age)}.jsonl" for age in (1, 13, 14, 20)]
        others = ["notes.txt", f"la-fact-log-2-{today - timedelta(days=30)}.jsonl",
                  f"la-fact-log-1-{today - timedelta(days=30)}.jsonl.bak"]
        for name in names + others:
            (Path(directory.name) / name).write_text("{}\n")
        clock, mono = Clock(T0), Clock(100.0)
        log = FactLog(directory.name, clock=clock, monotonic=mono, private=False)
        self.addCleanup(log.close)
        log.maintain()
        kept = sorted(p.name for p in Path(directory.name).iterdir())
        self.assertEqual(kept, sorted(names[:2] + others))  # 14 days old and older are gone
        self.assertEqual(log.snapshot()["counts"]["pruned"], 2)
        (Path(directory.name) / f"la-fact-log-1-{today - timedelta(days=15)}.jsonl").write_text("{}\n")
        log.maintain()  # same UTC day: no second scan
        self.assertEqual(log.snapshot()["counts"]["pruned"], 2)
        clock.now += 86400
        log.maintain()  # the next day prunes again (13 -> 14 days old, and the new 15)
        self.assertEqual(sorted(p.name for p in Path(directory.name).iterdir()), sorted(names[:1] + others))
        with self.assertRaises(ValueError):
            la_fact_log.settings({"retention_days": 0})

    def test_appends_are_flushed_per_line_but_fsynced_periodically(self):
        self.build(fsync_seconds=10)
        with patch("la_fact_log.os.fsync") as fsync:
            for index in range(30):
                self.write(100 + index, available_after=.2)
                self.mono.now += 1
                self.assertEqual(len(self.lines()), index + 1)  # already on the OS side
            self.assertLessEqual(fsync.call_count, 3)
            self.assertGreaterEqual(fsync.call_count, 2)
            before = fsync.call_count
            self.mono.now += 11
            self.log.maintain()  # the periodic fsync without a new line
            self.assertEqual(fsync.call_count, before + 1)
            self.log.maintain()
            self.assertEqual(fsync.call_count, before + 1)  # nothing new to sync
            self.write(200)
            self.log.close()
            self.assertEqual(fsync.call_count, before + 2)  # and on close
        with self.assertRaises(ValueError):
            self.log.terminal(request(201, self.clock.now), "failed", None)

    def test_a_torn_last_line_is_cut_when_the_file_reopens(self):
        self.build()
        self.write(1)
        self.write(2)
        self.log.close()
        path = Path(self.directory.name) / self.files()[0]
        good = path.read_bytes()
        path.write_bytes(good + b'{"schema":"la-fact-lo')
        log = FactLog(self.directory.name, clock=self.clock, monotonic=self.mono, private=False)
        self.addCleanup(log.close)
        req = request(3, self.clock.now)
        with self.assertLogs("perception_lab.la_live", "WARNING") as logged:
            log.terminal(req, "unknown_resolved", None)
        self.assertEqual(logged.output, ["WARNING:perception_lab.la_live:la-fact-log: cut a torn last line (21 bytes) on reopen"])
        log.close()
        values = self.lines()
        self.assertEqual(len(values), 3)
        self.assertTrue(path.read_bytes().startswith(good))

    def test_a_full_day_refuses_further_lines(self):
        self.build()
        self.log.max_day_bytes = 5000
        with self.assertLogs("perception_lab.la_live", "WARNING") as logged:
            states = [self.write(300 + index)[1]["state"] for index in range(4)]
        self.assertEqual(len(logged.output), 1)  # once per day, not per refused line
        self.assertEqual(states[:1], ["available"])
        self.assertIn("log_full", states)
        self.assertLessEqual(os.path.getsize(Path(self.directory.name) / self.files()[0]), 5000)
        self.assertGreaterEqual(self.log.snapshot()["counts"]["refused_full"], 1)

    def test_the_directory_must_be_private(self):
        if not hasattr(os, "geteuid"):
            directory = tempfile.TemporaryDirectory()
            self.addCleanup(directory.cleanup)
            with self.assertRaisesRegex(ValueError, "POSIX"):
                FactLog(directory.name)
            return
        # /tmp is world-writable (sticky), which private_directory rightly refuses as a
        # parent, so the accepted case needs a home-rooted directory (found on the Jetson).
        directory = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(directory.cleanup)
        os.chmod(directory.name, 0o755)
        with self.assertRaises(ValueError):
            FactLog(directory.name)
        os.chmod(directory.name, 0o700)
        FactLog(directory.name).close()
        with self.assertRaises(ValueError):
            FactLog("relative/dir")


class WritableTests(LogFixture, unittest.TestCase):
    """No LA inference whose answer cannot be recorded: the start-time check
    and writable() (review: an unwritable directory lost every fact silently)."""

    def test_the_start_check_opens_todays_file_and_refuses_a_directory_it_cannot_write(self):
        import errno
        from types import SimpleNamespace
        self.build()
        with patch("la_fact_log.os.statvfs", create=True, return_value=SimpleNamespace(f_flag=getattr(os, "ST_RDONLY", 1))), \
                self.assertRaisesRegex(ValueError, r"\(EROFS\); it must be inside the controller unit's ReadWritePaths"):
            self.log.check_writable()  # ProtectSystem=strict outside ReadWritePaths: a read-only mount
        with patch("la_fact_log.os.open", side_effect=PermissionError(errno.EACCES, "Permission denied")), \
                self.assertRaisesRegex(ValueError, r"writable fact log directory required \(EACCES\)"):
            self.log.check_writable()
        self.assertEqual(self.files(), [])
        self.log.check_writable()
        self.assertEqual(self.files(), ["la-fact-log-1-2026-10-08.jsonl"])  # today's file, opened at start
        self.write(1)
        self.assertEqual(len(self.lines()), 1)

    def test_failed_writes_stop_new_requests_until_a_retry_and_a_success_resets(self):
        import errno
        self.build()
        self.write(1)
        self.log._close_file()
        self.assertTrue(self.log.writable())
        with patch.object(self.log, "_open", side_effect=OSError(errno.ENOSPC, "No space left on device")):
            for failure in range(1, la_fact_log.MAX_WRITE_FAILURES + 1):
                with self.assertRaises(OSError):
                    self.write(1 + failure)
                self.assertEqual(self.log.writable(), failure < la_fact_log.MAX_WRITE_FAILURES)
            snapshot = self.log.snapshot()
            self.assertEqual((snapshot["failures_in_a_row"], snapshot["last_failure"], snapshot["counts"]["failed"]),
                             (3, "ENOSPC", 3))
            self.mono.now += la_fact_log.RETRY_SECONDS - 1
            self.assertFalse(self.log.writable())
            self.mono.now += 1
            self.assertTrue(self.log.writable())  # one more request may try
            with self.assertRaises(OSError):
                self.write(10)
            self.assertFalse(self.log.writable())  # failed again: another minute
        self.mono.now += la_fact_log.RETRY_SECONDS
        self.assertEqual(self.write(11)[1]["state"], "available")
        self.assertEqual(self.log.snapshot()["failures_in_a_row"], 0)
        self.assertTrue(self.log.writable())
        self.assertEqual(len(self.lines()), 2)

    def test_a_write_that_fails_closes_the_file_and_the_next_one_reopens_it(self):
        import errno
        self.build()
        self.write(1)
        real = self.log.file

        class Broken:
            def write(self, data):
                raise OSError(errno.EIO, "Input/output error")

            def flush(self):
                real.flush()

            def fileno(self):
                return real.fileno()

            def close(self):
                real.close()
        self.log.file = Broken()
        with self.assertRaises(OSError):
            self.write(2)
        self.assertTrue(real.closed)
        self.assertIsNone(self.log.file)
        self.assertEqual(self.log.snapshot()["last_failure"], "EIO")
        self.assertEqual(self.write(3)[1]["state"], "available")
        self.assertEqual(len(self.lines()), 2)  # the failed line was never half-written

    def test_a_full_day_is_unwritable_until_the_next_utc_day(self):
        self.build()
        self.log.max_day_bytes = 5000
        with self.assertLogs("perception_lab.la_live", "WARNING"):
            states = [self.write(400 + index, available_after=.1)[1]["state"] for index in range(4)]
        self.assertIn("log_full", states)
        self.assertFalse(self.log.writable())
        self.clock.now = datetime(2026, 10, 9, 0, 0, 1, tzinfo=timezone.utc).timestamp()
        self.assertTrue(self.log.writable())
        self.log.close()
        self.assertFalse(self.log.writable())


class SettingsTests(unittest.TestCase):
    def test_defaults_and_bounds(self):
        self.assertEqual(la_fact_log.settings(), {"retention_days": 14, "fsync_seconds": 10.0, "max_day_bytes": 64 * 1024 * 1024})
        for bad in ({"retention_days": 31}, {"retention_days": 14.0}, {"fsync_seconds": 0}, {"fsync_seconds": 61},
                    {"max_day_bytes": 1000}, {"surprise": 1}):
            with self.assertRaises(ValueError):
                la_fact_log.settings(bad)


if __name__ == "__main__":
    unittest.main()
