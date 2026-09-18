"""Evenings mode of tools/lighting-sim/replay.py: the observer memory
change-row derivation, the coverage rule that decides whether memory_ha may
vouch for a window, and the tv validity block.

Run: python3 -m unittest tests/living_lights/test_replay_evenings.py

Deterministic and offline: it builds a tiny memory_ha SQLite and a tiny
intelligence ledger in a temp dir and never opens the real snapshot or
ledger.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import pathlib
import sqlite3
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
REPLAY = REPO / "tools" / "lighting-sim" / "replay.py"

spec = importlib.util.spec_from_file_location("ll_replay", REPLAY)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)

TZ = dt.timezone(dt.timedelta(hours=-7))
EVENING = dt.date(2026, 9, 13)
START = dt.datetime(2026, 9, 13, 18, tzinfo=TZ)
END = dt.datetime(2026, 9, 14, 1, tzinfo=TZ)


def at(hour: int, minute: int = 0, day: int = 13) -> float:
    return dt.datetime(2026, 9, day, hour, minute, tzinfo=TZ).timestamp()


def memory_db(path: pathlib.Path, rows) -> sqlite3.Cursor:
    """rows: (observed_epoch, entity, state)."""
    con = sqlite3.connect(path)
    con.execute("create table memory_ha(id text primary key, observed real not null, entity text not null, "
                "room text not null, payload text not null)")
    for i, (observed, entity, state) in enumerate(rows):
        con.execute("insert into memory_ha values (?,?,?,?,?)",
                    (f"r{i}", observed, entity, "living_room", json.dumps({"state": state, "attributes": {}})))
    con.commit()
    return con.cursor()


def heartbeat(start_h, end_h, every_min=2, day_end=14):
    """Filler rows for some other entity so the observer looks alive."""
    t = dt.datetime(2026, 9, 13, start_h, tzinfo=TZ)
    end = dt.datetime(2026, 9, day_end, end_h, tzinfo=TZ)
    out = []
    while t < end:
        out.append((t.timestamp(), "light.sink", "off"))
        t += dt.timedelta(minutes=every_min)
    return out


class ChangeRows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "memory.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_state_changes_become_edges_and_initial_is_last_row_before_start(self):
        tv = replay.TV
        cur = memory_db(self.path, [
            (at(17, 50), tv, "on"), (at(17, 58), tv, "on"),           # before start: initial on
            (at(18, 2), tv, "on"),                                   # snapshot, unchanged
            (at(18, 3), tv, "off"), (at(18, 5), tv, "off"),           # change, then snapshot
            (at(18, 6), tv, "unavailable"),                          # change (non-binary state kept verbatim)
            (at(22, 51), tv, "on"),
            (at(1, 30, day=14), tv, "off"),                          # after end: ignored
        ])
        initial, initial_obs, edges = replay.memory_change_rows(cur, tv, START, END)
        self.assertEqual(initial, "on")
        self.assertEqual(initial_obs, at(17, 58))
        self.assertEqual(edges, [(at(18, 3), "off"), (at(18, 6), "unavailable"), (at(22, 51), "on")])

    def test_first_in_window_row_seeds_initial_when_nothing_precedes_start(self):
        tv = replay.TV
        cur = memory_db(self.path, [(at(19, 0), tv, "off"), (at(19, 2), tv, "off"), (at(20, 0), tv, "on")])
        initial, initial_obs, edges = replay.memory_change_rows(cur, tv, START, END)
        self.assertEqual((initial, initial_obs), ("off", at(19, 0)))
        self.assertEqual(edges, [(at(20, 0), "on")])

    def test_entity_without_rows_is_all_none(self):
        cur = memory_db(self.path, [(at(19, 0), "light.sink", "off")])
        self.assertEqual(replay.memory_change_rows(cur, replay.AT_HOME, START, END), (None, None, []))

    def test_same_state_across_start_boundary_is_not_an_edge(self):
        tv = replay.TV
        cur = memory_db(self.path, [(at(17, 0), tv, "off"), (at(18, 30), tv, "off"), (at(18, 40), tv, "off")])
        self.assertEqual(replay.memory_change_rows(cur, tv, START, END), ("off", at(17, 0), []))


class Coverage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "memory.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def test_continuous_rows_cover_the_window(self):
        cur = memory_db(self.path, heartbeat(17, 2))
        covered, reason = replay.memory_covers(cur, START, END)
        self.assertTrue(covered, reason)

    def test_rows_ending_early_do_not_cover(self):
        cur = memory_db(self.path, heartbeat(17, 18, day_end=13) + [(at(18, 19), "light.sink", "off")])
        covered, reason = replay.memory_covers(cur, START, END)
        self.assertFalse(covered)
        self.assertIn("end at 18:19", reason)

    def test_rows_starting_late_do_not_cover(self):
        cur = memory_db(self.path, heartbeat(21, 2))
        covered, reason = replay.memory_covers(cur, START, END)
        self.assertFalse(covered)
        self.assertIn("start at 21:00", reason)

    def test_long_silence_inside_the_window_breaks_coverage(self):
        rows = [r for r in heartbeat(17, 2) if not (at(20, 0) < r[0] < at(21, 0))]
        cur = memory_db(self.path, rows)
        covered, reason = replay.memory_covers(cur, START, END)
        self.assertFalse(covered)
        self.assertIn("silent", reason)

    def test_empty_snapshot_does_not_cover(self):
        cur = memory_db(self.path, [])
        self.assertFalse(replay.memory_covers(cur, START, END)[0])


class Validity(unittest.TestCase):
    def test_two_edges_measurable_whatever_the_source(self):
        self.assertTrue(replay.tv_measurable("ledger_flags", [("a", "on"), ("b", "off")], False))
        self.assertTrue(replay.tv_measurable("memory_ha", [("a", "on"), ("b", "off")], True))

    def test_known_memory_initial_is_enough(self):
        self.assertTrue(replay.tv_measurable("memory_ha", [], True))

    def test_flag_initial_alone_is_not_enough(self):
        self.assertFalse(replay.tv_measurable("ledger_flags", [], True))
        self.assertFalse(replay.tv_measurable("ledger_flags", [("a", "on")], True))

    def test_none_is_unmeasurable(self):
        self.assertFalse(replay.tv_measurable("none", [], False))


def ledger_db(path: pathlib.Path, decisions=(), flags=(), changes=()) -> sqlite3.Cursor:
    """A ledger with only the columns replay.py reads."""
    con = sqlite3.connect(path)
    con.execute("create table lighting_decisions(ts text, entity_id text, zone text, to_state text)")
    for table in ("override_events", "preference_observations"):
        con.execute(f"create table {table}(ts text, tv_playing integer, asleep integer, user_at_home integer)")
    con.execute("create table lighting_change_events(ts text, light_entity text, from_state text, to_state text, "
                "to_brightness_pct real)")
    con.executemany("insert into lighting_decisions values (?,?,?,?)", decisions)
    con.executemany("insert into override_events values (?,?,?,?)", flags)
    con.executemany("insert into lighting_change_events values (?,?,?,?,?)", changes)
    con.commit()
    return con.cursor()


def iso(hour, minute=0, second=0, day=13):
    return dt.datetime(2026, 9, day, hour, minute, second, tzinfo=TZ).isoformat()


class ExtractEvening(unittest.TestCase):
    """End to end on synthetic data; the window helpers use the host tz, so
    the assertions look at states and sources rather than exact instants."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.start, self.end = replay.evening_window(EVENING)
        tz = self.start.tzinfo
        self.ts = lambda h, m=0, s=0, d=13: dt.datetime(2026, 9, d, h, m, s, tzinfo=tz).isoformat()
        self.ep = lambda h, m=0, d=13: dt.datetime(2026, 9, d, h, m, tzinfo=tz).timestamp()
        sofa = "sensor.living_room_sofa_lighting_state"
        self.cur = ledger_db(
            base / "ledger.sqlite",
            decisions=[(self.ts(18, 0, 5), sofa, "sofa", "vacant"), (self.ts(19, 0), sofa, "sofa", "present"),
                       (self.ts(19, 0, 5), sofa, "sofa", "present"), (self.ts(21, 0), sofa, "sofa", "vacant")],
            flags=[(self.ts(18, 30), 1, 0, 1), (self.ts(20, 0), 0, 0, 1)],
            changes=[(self.ts(19, 0, 1), "light.sofa", "off", "on", 30.0),
                     (self.ts(19, 0, 1), "light.sofa", "off", "on", 30.0),   # duplicate second: deduped
                     (self.ts(2, 0, 0, 14), "light.sofa", "off", "on", 30.0)])  # after 01:00: excluded
        alive = []
        t = self.start - dt.timedelta(minutes=10)
        while t < self.end + dt.timedelta(minutes=1):
            alive.append((t.timestamp(), "light.sink", "off")); t += dt.timedelta(minutes=2)
        self.mcur = memory_db(base / "memory.sqlite3", alive + [
            (self.ep(17, 55), replay.TV, "on"), (self.ep(18, 3), replay.TV, "off"),
            (self.ep(17, 55), replay.ASLEEP, "off"), (self.ep(23, 40), replay.ASLEEP, "on"),
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def test_memory_source_wins_when_covered(self):
        doc = replay.extract_evening(self.cur, EVENING, self.mcur)
        self.assertEqual(doc["schema"], "living-lights-sim-evening/v1")
        v = doc["validity"]
        self.assertEqual((v["tv_source"], v["asleep_source"], v["user_at_home_source"]),
                         ("memory_ha", "memory_ha", "ledger_flags"))
        self.assertTrue(v["tv_measurable"])
        self.assertEqual(doc["initial"][replay.TV], "on")
        self.assertEqual([e["state"] for e in doc["actual"]["tv_edges"]], ["off"])
        self.assertEqual([e["state"] for e in doc["actual"]["asleep_transitions"]], ["on"])
        self.assertEqual(doc["initial"][replay.ASLEEP], "off")
        self.assertEqual(doc["initial"]["binary_sensor.sofa_person_occupancy"], "off")
        self.assertEqual([(e["entity"], e["state"]) for e in doc["events"] if "sofa" in e["entity"]],
                         [("binary_sensor.sofa_person_occupancy", "on"), ("binary_sensor.sofa_person_occupancy", "off")])
        self.assertEqual(doc["actual"]["turn_on_count_18_01"], 1)
        self.assertEqual(v["occupancy_source"], "ledger_classifier")

    def test_without_snapshot_flags_are_used_and_one_edge_is_unmeasurable(self):
        doc = replay.extract_evening(self.cur, EVENING, None)
        v = doc["validity"]
        self.assertEqual(v["tv_source"], "ledger_flags")
        self.assertFalse(v["tv_measurable"])
        self.assertIn("tv unmeasurable", v["notes"])
        self.assertEqual(doc["initial"][replay.TV], "on")
        self.assertEqual([e["state"] for e in doc["actual"]["tv_edges"]], ["off"])
        self.assertIn({"t": self.ts(20, 0), "entity": replay.TV, "state": "off"}, doc["events"])

    def test_no_flags_and_no_snapshot_is_source_none(self):
        empty = ledger_db(pathlib.Path(self.tmp.name) / "empty.sqlite")
        doc = replay.extract_evening(empty, EVENING, None)
        v = doc["validity"]
        self.assertEqual((v["tv_source"], v["tv_measurable"]), ("none", False))
        self.assertEqual(doc["initial"][replay.TV], "unknown")
        self.assertEqual(doc["events"], [])
        self.assertIn("tv unmeasurable", v["notes"])

    def test_snapshot_that_stops_early_falls_back_to_flags(self):
        base = pathlib.Path(self.tmp.name)
        short = memory_db(base / "short.sqlite3", [(self.ep(17, 50), "light.sink", "off"),
                                                   (self.ep(18, 10), "light.sink", "off"),
                                                   (self.ep(17, 55), replay.TV, "on")])
        doc = replay.extract_evening(self.cur, EVENING, short)
        self.assertEqual(doc["validity"]["tv_source"], "ledger_flags")
        self.assertFalse(doc["validity"]["memory_ha_covers_window"])


if __name__ == "__main__":
    unittest.main()
