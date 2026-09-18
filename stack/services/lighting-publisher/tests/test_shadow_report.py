"""The shadow report, scored against a journal a real publisher wrote.

``tools/shadow-report.py`` carries the acceptance verdict for M6's seven
shadow nights, and it reads a record shape that ``Publisher._decide_and_publish``
writes: ``record["t"]``, ``record["asleep"]["state"]`` and
``record["asleep"]["evidence"]["reason"]``. Nothing but this file pins the two
ends together, so the journal here is not hand-written: a real ``Publisher``
is ticked with a fake clock, a fake MQTT client and a fake observer transport
until its estimator latches ``likely_asleep`` and wakes again, and the tool is
run over the file that publisher actually produced. A change to either end --
a renamed key, a moved timestamp, a different evidence shape -- fails here.

The night driven below (offsets from ``T0``, one tick every ``TICK_S``):

    0 .. 1200    quiet, healthy       -> latch at 900 s (15 quiet minutes)
    1200 .. 1860 a credible person    -> exit at 1800 s (10 min of occupancy)
    1860 .. 2100 quiet again
    2100 .. 2200 MQTT down            -> gated ticks, no belief at all
    2200 .. 2320 reconnected, quiet

Person evidence is written by the tests, never by the publisher: the whole
point of the tool is to score the estimator against evidence it did not use.

No socket is opened here, by the publisher or by the tool.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
import io
import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from lighting_publisher.activity import load_zones
from lighting_publisher.inputs.mirror import MIRROR_HEARTBEAT_TOPIC
from lighting_publisher.inputs.observer import ObserverClient
from lighting_publisher.journal import Journal
from lighting_publisher.main import Config, Publisher

TOOL_PATH = pathlib.Path(__file__).resolve().parents[4] / "tools" / "shadow-report.py"

T0 = dt.datetime(2026, 9, 18, 1, 0, tzinfo=dt.timezone.utc)
"""01:00 local (the publisher runs on TZ=UTC here), inside the night window."""

TICK_S = 10
LATCH_OFFSET_S = 900
EXIT_OFFSET_S = 1800
PERSON_FROM_S, PERSON_TO_S = 1200, 1860
GATED_FROM_S, GATED_TO_S = 2100, 2200
NIGHT_END_S = 2320
CAMERA = "living_room"


def load_tool():
    """Import the hyphenated script by path, the way an operator runs it."""
    spec = importlib.util.spec_from_file_location("shadow_report_tool", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = load_tool()


def at(offset_s: int) -> dt.datetime:
    return T0 + dt.timedelta(seconds=offset_s)


def stamp(offset_s: int) -> str:
    return at(offset_s).isoformat(timespec="seconds")


# -- the fakes the publisher is driven with ------------------------------------

class FakeClient:
    def __init__(self) -> None:
        self.published: list[tuple] = []

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))

    def subscribe(self, topic, qos=0):
        pass

    def will_set(self, topic, payload=None, qos=0, retain=False):
        pass


class FakeTransport:
    """The observer's typed presence, switched by the test."""

    def __init__(self) -> None:
        self.present = False

    def fetch(self, url: str) -> bytes:
        body = {"cameras": {CAMERA: {"person_present": bool(self.present)}}}
        return json.dumps(body).encode("utf-8")


class Message:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


def drive_night(directory: pathlib.Path) -> None:
    """Tick a real publisher through the night above, journalling to disk."""
    clock = {"now": T0}
    transport = FakeTransport()
    publisher = Publisher(
        Config(mode="shadow", journal_dir=str(directory), timezone="UTC"),
        FakeClient(),
        ObserverClient("http://observer.invalid:8767/api/state", transport=transport),
        load_zones(), journal=Journal(directory), clock=lambda: clock["now"])
    publisher.on_connect()
    person = False
    connected = True
    for offset in range(0, NIGHT_END_S + TICK_S, TICK_S):
        now = at(offset)
        clock["now"] = now
        wanted_person = PERSON_FROM_S <= offset < PERSON_TO_S
        if wanted_person != person:
            person = wanted_person
            transport.present = person
            publisher.on_message(message=Message(
                "frigate/%s/person" % CAMERA, b"1" if person else b"0"))
        wanted_connected = not (GATED_FROM_S <= offset < GATED_TO_S)
        if wanted_connected != connected:
            connected = wanted_connected
            if connected:
                publisher.on_connect()
            else:
                publisher.on_disconnect()
        if connected:
            publisher.on_message(message=Message(MIRROR_HEARTBEAT_TOPIC,
                                                 now.isoformat().encode("utf-8")))
        publisher.tick(now)


def journal_lines(directory: pathlib.Path) -> list[str]:
    lines: list[str] = []
    for path in sorted(directory.glob("decisions-*.jsonl")):
        lines.extend(path.read_text(encoding="ascii").splitlines())
    return lines


def journal_records(directory: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in journal_lines(directory)]


class ShadowReportTest(unittest.TestCase):
    """One shadow night, written once by a real publisher, scored many ways."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.night = pathlib.Path(cls._tmp.name) / "journal"
        cls.night.mkdir()
        drive_night(cls.night)
        cls.files = sorted(p.name for p in cls.night.glob("decisions-*.jsonl"))
        assert cls.files, "the publisher journalled nothing"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def setUp(self) -> None:
        patcher = mock.patch("socket.socket",
                             side_effect=AssertionError("the report opens no socket"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.here = pathlib.Path(self.tmp.name)

    # --- helpers --------------------------------------------------------------

    def evidence(self, *offsets_s: int, label: str = "person",
                 name: str = "frigate.jsonl") -> str:
        """A raw Frigate export with one person row per offset."""
        path = self.here / name
        rows = [json.dumps({"t": stamp(offset), "camera": CAMERA, "label": label})
                for offset in offsets_s]
        path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="ascii")
        return str(path)

    def own_journal(self) -> pathlib.Path:
        """A private copy of the night, for tests that damage the file."""
        copy = self.here / "journal"
        shutil.copytree(self.night, copy)
        return copy

    def run_tool(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tool.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def run_json(self, *argv: str) -> tuple[int, dict]:
        code, out, err = self.run_tool(*argv, "--json")
        self.assertEqual(err, "", "the tool complained: " + err)
        return code, json.loads(out)

    def scored(self, *offsets_s: int) -> tuple[int, dict]:
        """The night scored against person rows at these offsets."""
        return self.run_json("--journal-dir", str(self.night),
                             "--frigate-jsonl", self.evidence(*offsets_s))

    # --- the record-shape contract --------------------------------------------

    def test_the_journal_is_real_publisher_output_the_tool_can_read(self):
        records = journal_records(self.night)
        latch = [r for r in records
                 if (r.get("asleep") or {}).get("state") == "likely_asleep"
                 and r["asleep"]["changed"]]
        self.assertEqual(len(latch), 1, "the night should latch exactly once")
        record = latch[0]
        self.assertEqual(record["event"], "decision")
        self.assertEqual(record["mode"], "shadow")
        self.assertEqual(record["t"], stamp(LATCH_OFFSET_S))
        evidence = record["asleep"]["evidence"]
        self.assertEqual(evidence["reason"], "quiet_window")
        self.assertEqual(evidence["quiet_s"], float(LATCH_OFFSET_S))
        self.assertTrue(evidence["window"])
        found = tool.latches(records)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["at"], at(LATCH_OFFSET_S))
        self.assertEqual(found[0]["from"], "awake")
        self.assertEqual(found[0]["quiet_s"], evidence["quiet_s"])
        self.assertEqual(found[0]["window"], evidence["window"])
        self.assertEqual(found[0]["tv_rule"], evidence["tv_rule"])
        self.assertEqual(found[0]["mode"], record["mode"])

    def test_the_exit_and_its_reason_survive_the_round_trip(self):
        records = journal_records(self.night)
        found = tool.exits(records)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["at"], at(EXIT_OFFSET_S))
        self.assertEqual(found[0]["to"], "awake")
        self.assertEqual(found[0]["reason"], "occupancy")

    def test_a_gated_run_carries_no_belief_and_invents_no_latch(self):
        records = journal_records(self.night)
        gated = [r for r in records if r.get("event") == "gated"]
        self.assertTrue(gated, "the night should contain gated ticks")
        for record in gated:
            self.assertNotIn("asleep", record)
            self.assertIn("mqtt_down", record["health"]["reasons"])
        self.assertEqual(len(tool.latches(records)), 1)

    # --- scoring --------------------------------------------------------------

    def test_a_person_inside_the_window_leaves_the_latch_unexplained(self):
        code, report = self.scored(LATCH_OFFSET_S - 300)
        self.assertEqual(code, tool.EXIT_UNEXPLAINED)
        self.assertEqual(report["unexplained_latches"], 1)
        self.assertEqual(report["inconclusive_latches"], 0)
        row = report["latches"][0]
        self.assertEqual(row["verdict"], "unexplained")
        self.assertFalse(row["explained"])
        self.assertEqual(row["frigate_people_in_window"], 1)
        self.assertEqual(row["cameras"], [CAMERA])
        self.assertEqual(row["last_person_at"], stamp(LATCH_OFFSET_S - 300))

    def test_a_person_before_the_window_leaves_the_latch_explained(self):
        code, report = self.scored(LATCH_OFFSET_S - 1800)
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(report["unexplained_latches"], 0)
        self.assertEqual(report["inconclusive_latches"], 0)
        row = report["latches"][0]
        self.assertEqual(row["verdict"], "explained")
        self.assertTrue(row["explained"])
        self.assertEqual(row["frigate_people_in_window"], 0)
        self.assertIsNone(row["last_person_at"])

    def test_the_window_edge_is_inclusive_and_the_second_past_it_is_not(self):
        code, report = self.scored(LATCH_OFFSET_S - 15 * 60)
        self.assertEqual(code, tool.EXIT_UNEXPLAINED)
        code, report = self.scored(LATCH_OFFSET_S - 15 * 60 - 1)
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(report["latches"][0]["verdict"], "explained")

    def test_a_non_person_label_is_not_person_evidence(self):
        path = self.evidence(LATCH_OFFSET_S - 300, label="car")
        code, out, err = self.run_tool("--journal-dir", str(self.night),
                                       "--frigate-jsonl", path, "--json")
        self.assertEqual(code, tool.EXIT_USAGE, out)
        self.assertIn("no person row", err)

    # --- the vacuous run ------------------------------------------------------

    def test_a_run_with_no_person_evidence_is_a_usage_error(self):
        code, out, err = self.run_tool("--journal-dir", str(self.night), "--json")
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertEqual(out, "", "a refused run must not print a report")
        self.assertIn("proves nothing", err)

    def test_an_evidence_file_with_no_person_row_is_a_usage_error(self):
        path = self.evidence(name="empty.jsonl")
        code, out, err = self.run_tool("--journal-dir", str(self.night),
                                       "--frigate-jsonl", path)
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertEqual(out, "")
        self.assertIn("1 file(s) read", err)

    def test_an_acknowledged_unscored_run_is_inconclusive_never_explained(self):
        code, report = self.run_json("--journal-dir", str(self.night), "--no-evidence")
        self.assertEqual(code, tool.EXIT_OK)
        self.assertTrue(report["inconclusive"])
        self.assertEqual(report["evidence_sources"], 0)
        self.assertEqual(report["frigate_person_rows"], 0)
        self.assertEqual(report["inconclusive_latches"], 1)
        self.assertEqual(report["unexplained_latches"], 0)
        row = report["latches"][0]
        self.assertEqual(row["verdict"], "inconclusive")
        self.assertFalse(row["explained"])

    def test_acknowledging_a_file_that_carried_no_person_row_is_still_unscored(self):
        path = self.evidence(name="empty.jsonl")
        code, report = self.run_json("--journal-dir", str(self.night),
                                     "--frigate-jsonl", path, "--no-evidence")
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(report["evidence_sources"], 1)
        self.assertTrue(report["inconclusive"], "a file is not a person row")
        self.assertEqual(report["inconclusive_latches"], 1)

    # --- the counts a reader checks -------------------------------------------

    def test_the_counts_say_what_was_actually_scored(self):
        records = journal_records(self.night)
        path = self.evidence(LATCH_OFFSET_S - 300, LATCH_OFFSET_S - 1800)
        code, report = self.run_json("--journal-dir", str(self.night),
                                     "--frigate-jsonl", path)
        self.assertEqual(code, tool.EXIT_UNEXPLAINED)
        self.assertEqual(report["schema"], "lighting-shadow-report/v1")
        self.assertEqual(report["journal_files"], self.files)
        self.assertEqual(report["records"], len(records))
        self.assertEqual(report["decisions"],
                         sum(1 for r in records if r.get("event") == "decision"))
        self.assertEqual(report["gated_ticks"],
                         sum(1 for r in records if r.get("event") == "gated"))
        self.assertEqual(report["window_min"], 15)
        self.assertEqual(report["evidence_sources"], 1)
        self.assertEqual(report["frigate_person_rows"], 2)
        self.assertFalse(report["inconclusive"])
        self.assertEqual(len(report["latches"]), 1)
        self.assertEqual(len(report["exits"]), 1)
        self.assertEqual(report["malformed_lines"], 0)

    def test_two_evidence_files_are_both_counted_and_both_scored(self):
        near = self.evidence(LATCH_OFFSET_S - 300, name="near.jsonl")
        far = self.evidence(LATCH_OFFSET_S - 1800, name="far.jsonl")
        code, report = self.run_json("--journal-dir", str(self.night),
                                     "--frigate-jsonl", near, "--frigate-jsonl", far)
        self.assertEqual(code, tool.EXIT_UNEXPLAINED)
        self.assertEqual(report["evidence_sources"], 2)
        self.assertEqual(report["frigate_person_rows"], 2)

    def test_a_range_outside_the_night_scores_no_latch(self):
        day = dt.date.fromisoformat(self.files[0][len("decisions-"):-len(".jsonl")])
        after = (day + dt.timedelta(days=1)).isoformat()
        code, out, err = self.run_tool("--journal-dir", str(self.night),
                                       "--frigate-jsonl", self.evidence(LATCH_OFFSET_S - 300),
                                       "--since", after)
        self.assertEqual(code, tool.EXIT_OK, err)
        self.assertIn("No likely_asleep latch in this range.", out)

    # --- the rendered receipt --------------------------------------------------

    def test_the_rendered_report_names_the_verdict_the_counts_and_the_exit(self):
        code, out, err = self.run_tool("--journal-dir", str(self.night),
                                       "--frigate-jsonl", self.evidence(LATCH_OFFSET_S - 300))
        self.assertEqual(code, tool.EXIT_UNEXPLAINED, err)
        self.assertIn("Living Lights shadow report", out)
        self.assertIn("evidence files: 1", out)
        self.assertIn("frigate rows  : 1 person observations", out)
        self.assertIn("window        : 15 min before each latch", out)
        self.assertIn("%s  UNEXPLAINED" % stamp(LATCH_OFFSET_S), out)
        self.assertIn("1 person rows on %s" % CAMERA, out)
        self.assertIn("%s -> awake (occupancy)" % stamp(EXIT_OFFSET_S), out)
        self.assertIn("unexplained latches: 1", out)
        self.assertIn("inconclusive latches: 0", out)

    def test_the_rendered_report_says_when_nothing_was_scored(self):
        code, out, err = self.run_tool("--journal-dir", str(self.night), "--no-evidence")
        self.assertEqual(code, tool.EXIT_OK, err)
        self.assertIn("NOTHING SCORED", out)
        self.assertIn("no person evidence was read, so nothing was checked", out)
        self.assertIn("This run proves nothing", out)
        self.assertIn("inconclusive latches: 1", out)
        self.assertNotIn("UNEXPLAINED", out)

    # --- damaged input ---------------------------------------------------------

    def test_a_malformed_journal_line_is_reported_not_fatal(self):
        journal = self.own_journal()
        path = journal / self.files[-1]
        with open(path, "a", encoding="ascii") as handle:
            handle.write('{"t": "2026-09-18T01:40:00+00:00", "event": "deci\n')
            handle.write('[1, 2, 3]\n')
        good = len(journal_lines(self.night))
        code, out, err = self.run_tool("--journal-dir", str(journal),
                                       "--frigate-jsonl", self.evidence(LATCH_OFFSET_S - 300),
                                       "--json")
        self.assertEqual(code, tool.EXIT_UNEXPLAINED, err)
        report = json.loads(out)
        self.assertEqual(report["records"], good, "a bad line must not become a record")
        self.assertEqual(report["malformed_lines"], 2)
        self.assertEqual(report["malformed_where"],
                         ["%s:%d" % (self.files[-1], good + 1),
                          "%s:%d" % (self.files[-1], good + 2)])
        self.assertEqual(report["unexplained_latches"], 1)
        code, out, err = self.run_tool("--journal-dir", str(journal),
                                       "--frigate-jsonl", self.evidence(LATCH_OFFSET_S - 300))
        self.assertIn("malformed     : 2 unreadable lines", out)

    def test_a_malformed_evidence_line_is_reported_not_fatal(self):
        path = pathlib.Path(self.evidence(LATCH_OFFSET_S - 300))
        with open(path, "a", encoding="ascii") as handle:
            handle.write("not json at all\n")
        code, report = self.run_json("--journal-dir", str(self.night),
                                     "--frigate-jsonl", str(path))
        self.assertEqual(code, tool.EXIT_UNEXPLAINED)
        self.assertEqual(report["malformed_lines"], 1)
        self.assertEqual(report["frigate_person_rows"], 1)

    def test_a_missing_journal_directory_is_a_usage_error(self):
        code, out, err = self.run_tool("--journal-dir", str(self.here / "absent"),
                                       "--frigate-jsonl", self.evidence(LATCH_OFFSET_S - 300))
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertEqual(out, "")
        self.assertIn("journal directory not found", err)

    def test_a_missing_evidence_file_is_a_usage_error(self):
        code, out, err = self.run_tool("--journal-dir", str(self.night),
                                       "--frigate-jsonl", str(self.here / "absent.jsonl"))
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertEqual(out, "")
        self.assertIn("frigate evidence not found", err)

    def test_a_bad_date_or_window_is_a_usage_error(self):
        evidence = self.evidence(LATCH_OFFSET_S - 300)
        code, _, err = self.run_tool("--journal-dir", str(self.night),
                                     "--frigate-jsonl", evidence, "--since", "yesterday")
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertIn("--since must be YYYY-MM-DD", err)
        code, _, err = self.run_tool("--journal-dir", str(self.night),
                                     "--frigate-jsonl", evidence, "--window-min", "0")
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertIn("--window-min must be positive", err)


if __name__ == "__main__":
    unittest.main()
