"""Tests for tools/tv-evening-postmortem.py, the story T evening post-mortem.

Run: python3 -m unittest tests/living_lights/test_tv_evening_postmortem.py -v

Offline by construction: the Home Assistant getter is injected (a fake that
answers history/period and states/ out of a hand-built recorder), the ledger
is a temporary SQLite file with the real column names, and the publisher
journal is a temporary directory. Nothing here opens a socket and nothing
writes outside a temporary directory.

Covered: episode detection from the deterministic path and from the belief,
the 30 s darkness metric including an episode that never darkens, the errand
metrics including a zone that overshoots the route cap and one whose lights
never go off, writer attribution excluding a person's own command, the two
unscorable cases (no recorder coverage, no episode) exiting 2, the receipt's
shape and refusal to overwrite, and that a token never reaches any output.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "tv-evening-postmortem.py"

_spec = importlib.util.spec_from_file_location("tv_evening_postmortem", TOOL)
pm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pm)

TZ = ZoneInfo("America/Los_Angeles")
DAY = dt.date(2026, 9, 17)
TOKEN = "eyJhbGciOiJIUzI1NiJ9.FAKE-TOKEN-FOR-TESTS.signature"

LR_LIGHTS = list(pm.LIVING_ROOM_LIGHTS)
"""Every living-room light, taken from the tool itself. A fixture room missing
one light silently exercises the subset rule instead of the metric."""
SINK_OCC = "binary_sensor.kitchen_sink_person_occupancy_stable"
ISLAND_OCC = "binary_sensor.kitchen_island_left_person_occupancy_stable"
DINING_OCC = "binary_sensor.dining_room_dining_left_person_occupancy_stable"


def T(hhmm: str, plus_days: int = 0) -> dt.datetime:
    """A local time on the evening under test (18:00 belongs to 2026-09-17)."""
    hour, minute, second = (list(int(p) for p in hhmm.split(":")) + [0])[:3]
    return dt.datetime.combine(DAY + dt.timedelta(days=plus_days),
                               dt.time(hour, minute, second), tzinfo=TZ)


WINDOW = (T("18:00"), T("01:00", 1))


class FakeHa:
    """A GET-only stand-in for the Home Assistant REST API.

    It answers history/period out of {entity: [(t, state, attrs)]} the way the
    real API does -- one list per entity, with entity_id and attributes on the
    first row only when minimal_response was asked for -- and states/<entity>
    with a fixed value. Every path it is given is recorded, so a test can
    assert no secret travelled in a URL.
    """

    def __init__(self, history, route_state="30"):
        self.history = history
        self.route_state = route_state
        self.paths = []

    def __call__(self, path):
        self.paths.append(path)
        if path.startswith("states/"):
            return {"entity_id": path.split("/", 1)[1], "state": self.route_state}
        _, _, query = path.partition("?")
        params = urllib.parse.parse_qs(query, keep_blank_values=True)
        wanted = params["filter_entity_id"][0].split(",")
        minimal = "minimal_response" in params
        out = []
        for entity in wanted:
            rows = self.history.get(entity)
            if not rows:
                continue
            series = []
            for index, (when, state, attrs) in enumerate(rows):
                row = {"state": state, "last_changed": when.isoformat()}
                if index == 0 or not minimal:
                    row["entity_id"] = entity
                    row["attributes"] = dict(attrs or {})
                series.append(row)
            out.append(series)
        return out


def states(*pairs, attrs=None):
    """[(t, state, attrs)] from (hhmm, state) pairs, days rolled at midnight."""
    rows, day = [], 0
    previous = None
    for hhmm, state in pairs:
        if previous is not None and hhmm < previous:
            day += 1
        previous = hhmm
        rows.append((T(hhmm, day), state, attrs or {}))
    return rows


def base_history(**overrides):
    """A quiet evening: the TV plays 20:00-22:00 with the sofa occupied, and
    the living room is dark throughout. Tests override single entities."""
    history = {
        pm.TV_PLAYING: states(("18:00", "off"), ("20:00", "on"), ("22:00", "off")),
        pm.SOFA_STABLE: states(("18:00", "off"), ("20:00", "on"), ("22:00", "off")),
        pm.ASLEEP: states(("18:00", "off")),
        pm.ROUTE_HELPER: states(("18:00", "30.0")),
        pm.ANY_OCCUPIED: states(("18:00", "on")),
    }
    for light in LR_LIGHTS:
        history[light] = states(("18:00", "off"))
    history.update(overrides)
    return history


LEDGER_DDL = """
CREATE TABLE lighting_change_events (
  id TEXT PRIMARY KEY, raw_event_id TEXT, event_id TEXT, ts TEXT, entity_id TEXT,
  light_entity TEXT, zone TEXT, from_state TEXT, to_state TEXT, from_brightness_pct REAL,
  to_brightness_pct REAL, from_color_temp_kelvin REAL, to_color_temp_kelvin REAL,
  command_id TEXT, source_hint TEXT, source_confidence TEXT, trigger_attribute TEXT,
  ha_context_json TEXT NOT NULL DEFAULT '{}', context_json TEXT NOT NULL DEFAULT '{}',
  payload_json TEXT NOT NULL DEFAULT '{}');
CREATE INDEX idx_lce_zone_ts ON lighting_change_events(zone, ts);
CREATE TABLE lighting_decisions (
  id TEXT PRIMARY KEY, raw_event_id TEXT, ts TEXT, entity_id TEXT, zone TEXT, from_state TEXT,
  to_state TEXT, predicted_brightness_pct REAL, predicted_color_temp_kelvin REAL,
  shadow_mode INTEGER DEFAULT 0, payload_json TEXT NOT NULL DEFAULT '{}');
CREATE INDEX idx_ld_zone_ts ON lighting_decisions(zone, ts);
"""


def make_ledger(path, changes=(), decisions=()):
    """A ledger with the real column names. `changes` rows are
    (t, light, zone, from_state, to_state, to_pct, context, hint, command_id)."""
    conn = sqlite3.connect(path)
    conn.executescript(LEDGER_DDL)
    for index, row in enumerate(changes):
        when, light, zone, from_state, to_state, to_pct = row[:6]
        context = row[6] if len(row) > 6 else '{"parent_id":"auto1"}'
        hint = row[7] if len(row) > 7 else "automation_or_script"
        command_id = row[8] if len(row) > 8 else None
        from_pct = row[9] if len(row) > 9 else None
        conn.execute(
            "INSERT INTO lighting_change_events (id, raw_event_id, event_id, ts, entity_id, "
            "light_entity, zone, from_state, to_state, from_brightness_pct, to_brightness_pct, "
            "command_id, source_hint, trigger_attribute, ha_context_json, context_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"e{index}", "r", f"ev{index}", when.isoformat(), light, light, zone, from_state,
             to_state, from_pct, to_pct, command_id, hint, "brightness", context, "{}"))
    for index, (when, zone, to_state, predicted) in enumerate(decisions):
        conn.execute(
            "INSERT INTO lighting_decisions (id, raw_event_id, ts, entity_id, zone, from_state, "
            "to_state, predicted_brightness_pct) VALUES (?,?,?,?,?,?,?,?)",
            (f"d{index}", "r", when.isoformat(), f"sensor.{zone}_lighting_state", zone, "vacant",
             to_state, predicted))
    conn.commit()
    conn.close()


def args_for(tmp, *, no_ledger=True, db=None, journal=None, ha_url="http://ha.invalid:8123",
             ignore_missing_lights=False, assume_route_pct=None, rescore=False):
    argv = ["--since", "2026-09-17", "--until", "2026-09-18", "--out", str(Path(tmp) / "out"),
            "--ha-url", ha_url, "--timezone", "America/Los_Angeles",
            "--journal-dir", journal or str(Path(tmp) / "no-journal")]
    if no_ledger:
        argv.append("--no-ledger")
    else:
        argv += ["--db", db]
    if ignore_missing_lights:
        argv.append("--ignore-missing-lights")
    if assume_route_pct is not None:
        argv += ["--assume-route-pct", str(assume_route_pct)]
    if rescore:
        argv.append("--rescore")
    return pm.parse_args(argv)


def run_report(tmp, history, **kwargs):
    getter = FakeHa(history)
    args = args_for(tmp, **kwargs)
    report = pm.run(args, getter=getter, now=T("09:00", 1), secrets=(TOKEN,))
    return report, getter


def metric_named(report, name):
    for row in report["metrics"]:
        if row["metric"] == name:
            return row
    raise AssertionError(f"no metric {name} in {[r['metric'] for r in report['metrics']]}")


class EpisodeTests(unittest.TestCase):
    """Episodes come from tv_playing and the sofa, or from the belief."""

    def test_deterministic_episode_is_tv_playing_and_sofa(self):
        history = base_history(
            **{pm.SOFA_STABLE: states(("18:00", "off"), ("20:30", "on"), ("21:00", "off"))})
        episodes, _, source, _ = pm.watching_episodes(self.parsed(history), *WINDOW)
        self.assertEqual(source, "tv_playing_and_sofa")
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["from"], T("20:30"))
        self.assertEqual(episodes[0]["to"], T("21:00"))
        self.assertEqual(episodes[0]["source"], "tv_playing_and_sofa")
        self.assertEqual(episodes[0]["sofa_from"], T("20:30"))

    def test_lg_tv_stands_in_when_tv_playing_never_existed(self):
        history = {
            pm.LG_TV: states(("18:00", "off"), ("20:00", "on"), ("22:00", "standby")),
            pm.SOFA_STABLE: states(("18:00", "off"), ("20:00", "on"), ("23:00", "off")),
        }
        episodes, _, source, _ = pm.watching_episodes(self.parsed(history), *WINDOW)
        self.assertEqual(source, "lg_tv_and_sofa")
        self.assertEqual([(e["from"], e["to"]) for e in episodes], [(T("20:00"), T("22:00"))])

    def test_belief_wins_once_tv_watching_has_a_real_state(self):
        history = base_history()
        history[pm.TV_WATCHING] = states(("18:00", "unavailable"), ("20:05", "on"),
                                         ("21:30", "off"))
        episodes, _, _, _ = pm.watching_episodes(self.parsed(history), *WINDOW)
        sources = {e["source"] for e in episodes}
        self.assertIn("tv_watching", sources)
        belief = [e for e in episodes if e["source"] == "tv_watching"]
        self.assertEqual([(e["from"], e["to"]) for e in belief], [(T("20:05"), T("21:30"))])
        # The deterministic episode is kept only for the part of the evening
        # before the belief existed, so the two sources cannot double-count.
        for episode in episodes:
            if episode["source"] != "tv_watching":
                self.assertLessEqual(episode["to"], T("20:05"))

    def test_belief_episode_records_when_the_sofa_was_occupied(self):
        history = base_history(
            **{pm.SOFA_STABLE: states(("18:00", "off"), ("20:30", "on"), ("21:00", "off"))})
        history[pm.TV_WATCHING] = states(("18:00", "off"), ("20:00", "on"), ("22:00", "off"))
        episodes, _, _, _ = pm.watching_episodes(self.parsed(history), *WINDOW)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["source"], "tv_watching")
        self.assertEqual(episodes[0]["sofa_from"], T("20:30"))

    def parsed(self, history):
        getter = FakeHa(history)
        return pm.parse_history(getter(pm.history_path(*WINDOW, sorted(history))))


class DarknessTests(unittest.TestCase):
    """Living room dark within 30 s of an episode opening."""

    def test_dark_in_time_passes_with_its_denominator(self):
        history = base_history()
        history["light.front_left"] = states(("18:00", "on"), ("20:00:20", "off"))
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        row = metric_named(report, "living_room_dark_within_30s")
        self.assertEqual(row["verdict"], "pass")
        self.assertEqual((row["numerator"], row["denominator"]), (1, 1))
        self.assertEqual(row["detail"][0]["time_to_dark_s"], 20.0)

    def test_an_episode_that_never_darkens_fails_the_fraction(self):
        history = base_history(**{
            pm.TV_PLAYING: states(("18:00", "off"), ("19:00", "on"), ("19:30", "off"),
                                  ("20:00", "on"), ("22:00", "off")),
            pm.SOFA_STABLE: states(("18:00", "off"), ("19:00", "on"), ("19:30", "off"),
                                   ("20:00", "on"), ("22:00", "off")),
        })
        # First episode goes dark at once; the second leaves a light on all evening.
        history["light.front_left"] = states(("18:00", "off"), ("20:00", "on"))
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        row = metric_named(report, "living_room_dark_within_30s")
        self.assertEqual((row["numerator"], row["denominator"]), (1, 2))
        self.assertEqual(row["verdict"], "fail")
        self.assertEqual(row["fraction"], 0.5)
        self.assertIsNone(row["detail"][1]["time_to_dark_s"])
        self.assertEqual(report["exit_code"], pm.EXIT_FAILED_BAR)

    def test_a_light_with_no_recorder_row_is_named_and_not_assumed_off(self):
        """Darkness over part of a room is not darkness.

        A light the recorder has nothing for may have burned through the whole
        film. Scoring the episode anyway would let the missing light grant the
        pass, so the default refuses and names it.
        """
        history = base_history()
        history.pop("light.rear_left")
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        row = metric_named(report, "living_room_dark_within_30s")
        self.assertIn("light.rear_left", row["detail"][0]["lights_without_recorder_data"])
        self.assertFalse(row["detail"][0]["scored"])
        self.assertIn("light.rear_left", row["detail"][0]["note"])
        self.assertEqual(row["verdict"], "skipped")
        self.assertEqual(report["verdict"], "unscorable")
        self.assertEqual(report["exit_code"], pm.EXIT_UNSCORABLE)

    def test_the_subset_can_be_scored_only_as_a_stated_assumption(self):
        history = base_history()
        history.pop("light.rear_left")
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history, ignore_missing_lights=True)
        row = metric_named(report, "living_room_dark_within_30s")
        self.assertTrue(row["detail"][0]["scored"])
        self.assertIn("light.rear_left", row["detail"][0]["assumed"])
        self.assertTrue(report["inputs"]["ignore_missing_lights"])

    def test_a_light_unavailable_when_the_room_reads_dark_is_not_darkness(self):
        """An integration blip must not grant a story.

        ``unavailable`` is not off; it is unknown. Counting it as off reports
        story T working on the strength of a dropped connection.
        """
        history = base_history()
        history["light.front_left"] = states(("18:00", "on"), ("20:00:10", "unavailable"))
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        row = metric_named(report, "living_room_dark_within_30s")
        self.assertIn("light.front_left", row["detail"][0]["unavailable_at_dark"])
        self.assertFalse(row["detail"][0]["scored"])
        self.assertEqual(row["verdict"], "skipped")


class ErrandTests(unittest.TestCase):
    """Route light on an errand, and off again when the zone clears."""

    def evening(self, tmp, occupancy, changes, lights):
        history = base_history(**occupancy)
        history.update(lights)
        db = str(Path(tmp) / "ledger.sqlite")
        make_ledger(db, changes=changes,
                    decisions=[(T("20:29"), "sink", "present", 30.0)])
        return run_report(tmp, history, no_ledger=False, db=db)[0]

    def test_route_light_within_60s_and_off_within_150s_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self.evening(
                tmp,
                {SINK_OCC: states(("18:00", "off"), ("20:30", "on"), ("20:40", "off"))},
                [(T("20:30:10"), "light.sink", "sink", "off", "on", 30.0)],
                {"light.sink": states(("18:00", "off"), ("20:30:10", "on"), ("20:41", "off"))})
        route = metric_named(report, "errand_lit_at_or_below_route_pct_within_60s")
        off = metric_named(report, "errand_off_within_150s_of_clearing")
        self.assertEqual((route["numerator"], route["denominator"]), (1, 1))
        self.assertEqual(route["verdict"], "pass")
        self.assertEqual((off["numerator"], off["denominator"]), (1, 1))
        self.assertEqual(off["verdict"], "pass")
        self.assertEqual(report["errands"][0]["lit_after_s"], 10.0)
        self.assertEqual(report["errands"][0]["off_after_s"], 60.0)

    def test_a_zone_already_lit_at_the_floor_is_not_a_route_pass(self):
        """A light that sat at the movie floor answered nothing.

        The simulator requires a call that RAISED the zone's level. Scoring an
        already-lit zone as lit in 0.0 s made the very defect this metric
        exists to catch read as a pass: the kitchen never responded to the
        errand at all.
        """
        with tempfile.TemporaryDirectory() as tmp:
            report = self.evening(
                tmp,
                {SINK_OCC: states(("18:00", "off"), ("20:30", "on"), ("20:40", "off"))},
                [(T("19:00"), "light.sink", "sink", "off", "on", 8.0)],
                {"light.sink": states(("18:00", "off"), ("19:00", "on"), ("20:41", "off"))})
        route = metric_named(report, "errand_lit_at_or_below_route_pct_within_60s")
        errand = report["errands"][0]
        self.assertFalse(errand["route_scored"])
        self.assertIsNone(errand["lit_after_s"])
        self.assertIn("already lit at or below the route cap", errand["note"])
        self.assertEqual(route["verdict"], "skipped")
        self.assertTrue(route["unscored"])

    def test_a_zone_already_lit_above_the_cap_is_a_failure(self):
        """Brighter than the route cap during a film is the defect itself."""
        with tempfile.TemporaryDirectory() as tmp:
            report = self.evening(
                tmp,
                {SINK_OCC: states(("18:00", "off"), ("20:30", "on"), ("20:40", "off"))},
                [(T("19:00"), "light.sink", "sink", "off", "on", 80.0)],
                {"light.sink": states(("18:00", "off"), ("19:00", "on"), ("20:41", "off"))})
        route = metric_named(report, "errand_lit_at_or_below_route_pct_within_60s")
        errand = report["errands"][0]
        self.assertTrue(errand["route_scored"])
        self.assertFalse(errand["route_ok"])
        self.assertEqual(errand["lit_pct"], 80.0)
        self.assertIn("already lit above the route cap", errand["note"])
        self.assertEqual(route["verdict"], "fail")

    def test_a_re_set_to_the_same_level_is_not_a_route_response(self):
        """A tick re-issuing the same brightness raised nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            report = self.evening(
                tmp,
                {SINK_OCC: states(("18:00", "off"), ("20:30", "on"), ("20:40", "off"))},
                [(T("19:00"), "light.sink", "sink", "off", "on", 8.0),
                 (T("20:30:10"), "light.sink", "sink", "on", "on", 8.0)],
                {"light.sink": states(("18:00", "off"), ("19:00", "on"), ("20:41", "off"))})
        errand = report["errands"][0]
        self.assertFalse(errand["route_scored"])
        self.assertIsNone(errand["lit_after_s"])

    def test_an_errand_opening_in_the_hold_after_an_episode_still_counts(self):
        """Someone getting up as the credits roll is still on an errand.

        The simulator holds an errand eligible for the oracle's AWAY_HOLD after
        an episode ends. Requiring the errand to open strictly inside an
        episode made the same evening score differently in the two tools.
        """
        with tempfile.TemporaryDirectory() as tmp:
            report = self.evening(
                tmp,
                {SINK_OCC: states(("18:00", "off"), ("22:05", "on"), ("22:15", "off"))},
                [(T("22:05:10"), "light.sink", "sink", "off", "on", 30.0)],
                {"light.sink": states(("18:00", "off"), ("22:05:10", "on"), ("22:16", "off"))})
        route = metric_named(report, "errand_lit_at_or_below_route_pct_within_60s")
        self.assertEqual((route["numerator"], route["denominator"]), (1, 1))
        self.assertEqual(report["errands"][0]["opened_in"], "hold")

    def test_an_errand_long_after_the_hold_is_not_an_errand(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self.evening(
                tmp,
                {SINK_OCC: states(("18:00", "off"), ("18:10", "on"), ("18:20", "off"))},
                [(T("18:10:10"), "light.sink", "sink", "off", "on", 30.0)],
                {"light.sink": states(("18:00", "off"), ("18:10:10", "on"), ("18:21", "off"))})
        self.assertEqual(report["errands"], [])

    def test_occupancy_chatter_is_one_errand_not_four(self):
        """A flickering stable-occupancy sensor must not multiply the bar.

        Four visits seconds apart are one trip to the sink. Counted raw they
        put three unanswerable errands in the denominator and make a 90 % bar
        unreachable for reasons that have nothing to do with lighting.
        """
        with tempfile.TemporaryDirectory() as tmp:
            report = self.evening(
                tmp,
                {SINK_OCC: states(("18:00", "off"), ("20:30", "on"), ("20:30:20", "off"),
                                  ("20:30:40", "on"), ("20:31", "off"), ("20:31:20", "on"),
                                  ("20:40", "off"))},
                [(T("20:30:10"), "light.sink", "sink", "off", "on", 30.0)],
                {"light.sink": states(("18:00", "off"), ("20:30:10", "on"), ("20:41", "off"))})
        route = metric_named(report, "errand_lit_at_or_below_route_pct_within_60s")
        self.assertEqual(route["errands_raw"], 3)
        self.assertEqual(route["errands_merged"], 1)
        self.assertEqual((route["numerator"], route["denominator"]), (1, 1))

    def test_a_route_cap_absent_from_the_recorder_is_not_taken_from_today(self):
        """Today's helper is not evidence about a past evening."""
        occupancy = {SINK_OCC: states(("18:00", "off"), ("20:30", "on"), ("20:40", "off"))}
        changes = [(T("20:30:10"), "light.sink", "sink", "off", "on", 30.0)]
        lights = {"light.sink": states(("18:00", "off"), ("20:30:10", "on"), ("20:41", "off"))}
        with tempfile.TemporaryDirectory() as tmp:
            history = base_history(**occupancy)
            history.update(lights)
            history.pop(pm.ROUTE_HELPER)
            db = str(Path(tmp) / "ledger.sqlite")
            make_ledger(db, changes=changes)
            report, _ = run_report(tmp, history, no_ledger=False, db=db)
        errand = report["errands"][0]
        self.assertIsNone(errand["route_pct"])
        self.assertFalse(errand["route_scored"])
        self.assertIn("no recorded route cap", errand["note"])

    def test_an_assumed_route_cap_is_recorded_as_an_assumption(self):
        occupancy = {SINK_OCC: states(("18:00", "off"), ("20:30", "on"), ("20:40", "off"))}
        changes = [(T("20:30:10"), "light.sink", "sink", "off", "on", 30.0)]
        lights = {"light.sink": states(("18:00", "off"), ("20:30:10", "on"), ("20:41", "off"))}
        with tempfile.TemporaryDirectory() as tmp:
            history = base_history(**occupancy)
            history.update(lights)
            history.pop(pm.ROUTE_HELPER)
            db = str(Path(tmp) / "ledger.sqlite")
            make_ledger(db, changes=changes)
            report, _ = run_report(tmp, history, no_ledger=False, db=db, assume_route_pct=30)
        errand = report["errands"][0]
        self.assertEqual(errand["route_pct"], 30)
        self.assertEqual(errand["route_pct_source"], "assumed")
        self.assertTrue(errand["route_scored"])
        self.assertEqual(report["inputs"]["assumed_route_pct"], 30)

    def test_a_zone_lit_above_the_route_cap_fails_the_route_metric(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self.evening(
                tmp,
                {SINK_OCC: states(("18:00", "off"), ("20:30", "on"), ("20:40", "off")),
                 ISLAND_OCC: states(("18:00", "off"), ("20:32", "on"), ("20:38", "off"))},
                [(T("20:30:10"), "light.sink", "sink", "off", "on", 30.0),
                 (T("20:32:05"), "light.island_left", "island_left", "off", "on", 80.0)],
                {"light.sink": states(("18:00", "off"), ("20:30:10", "on"), ("20:41", "off")),
                 "light.island_left": states(("18:00", "off"), ("20:32:05", "on"),
                                             ("20:39", "off"))})
        route = metric_named(report, "errand_lit_at_or_below_route_pct_within_60s")
        self.assertEqual((route["numerator"], route["denominator"]), (1, 2))
        self.assertEqual(route["verdict"], "fail")
        failed = route["detail"][0]
        self.assertEqual(failed["zone"], "island_left")
        self.assertEqual(failed["lit_pct"], 80.0)
        self.assertTrue(failed["overshoot"])
        self.assertEqual(failed["route_pct"], 30)

    def test_a_zone_whose_lights_never_go_off_fails_the_off_metric(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self.evening(
                tmp,
                {SINK_OCC: states(("18:00", "off"), ("20:30", "on"), ("20:40", "off")),
                 DINING_OCC: states(("18:00", "off"), ("20:34", "on"), ("20:36", "off"))},
                [(T("20:30:10"), "light.sink", "sink", "off", "on", 30.0),
                 (T("20:34:05"), "light.dining_table_left", "dining_left", "off", "on", 25.0)],
                {"light.sink": states(("18:00", "off"), ("20:30:10", "on"), ("20:41", "off")),
                 "light.dining_table_left": states(("18:00", "off"), ("20:34:05", "on"))})
        off = metric_named(report, "errand_off_within_150s_of_clearing")
        self.assertEqual((off["numerator"], off["denominator"]), (1, 2))
        self.assertEqual(off["verdict"], "fail")
        self.assertEqual(off["detail"][0]["zone"], "dining_left")
        self.assertIsNone(off["detail"][0]["off_after_s"])

    def test_an_errand_still_occupied_at_the_window_end_is_not_scored_for_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self.evening(
                tmp,
                {SINK_OCC: states(("18:00", "off"), ("20:30", "on"))},
                [(T("20:30:10"), "light.sink", "sink", "off", "on", 30.0)],
                {"light.sink": states(("18:00", "off"), ("20:30:10", "on"))})
        off = metric_named(report, "errand_off_within_150s_of_clearing")
        self.assertEqual(off["denominator"], 0)
        self.assertEqual(off["verdict"], "skipped")
        self.assertEqual(report["errands"][0]["off_note"],
                         "the zone had not cleared when the window ended")

    def test_occupancy_outside_an_episode_is_not_an_errand(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self.evening(
                tmp,
                {SINK_OCC: states(("18:00", "off"), ("19:00", "on"), ("19:10", "off"))},
                [(T("19:00:10"), "light.sink", "sink", "off", "on", 80.0)],
                {"light.sink": states(("18:00", "off"), ("19:00:10", "on"), ("19:11", "off"))})
        self.assertEqual(report["errands"], [])
        self.assertEqual(metric_named(report, "errand_lit_at_or_below_route_pct_within_60s")["verdict"],
                         "skipped")

    def test_without_the_ledger_the_route_cap_is_skipped_not_guessed(self):
        history = base_history(
            **{SINK_OCC: states(("18:00", "off"), ("20:30", "on"), ("20:40", "off"))})
        history["light.sink"] = states(("18:00", "off"), ("20:30:10", "on"), ("20:41", "off"))
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        route = metric_named(report, "errand_lit_at_or_below_route_pct_within_60s")
        self.assertEqual(route["verdict"], "skipped")
        self.assertEqual(route["denominator"], 0)
        self.assertEqual(metric_named(report, "errand_off_within_150s_of_clearing")["verdict"], "pass")


class WriterAttributionTests(unittest.TestCase):
    """A person's own command is not a defect; an automation's write is."""

    def evening(self, tmp, changes):
        history = base_history()
        history["light.front_left"] = states(("18:00", "off"), ("20:05", "on"), ("20:06", "off"))
        db = str(Path(tmp) / "ledger.sqlite")
        make_ledger(db, changes=changes)
        return run_report(tmp, history, no_ledger=False, db=db)[0]

    def test_a_human_turn_on_while_watching_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self.evening(tmp, [
                (T("20:05"), "light.front_left", "sofa", "off", "on", 60.0,
                 '{"user_id":"u1","parent_id":null}', "home_app_or_ha_user", None)])
        row = metric_named(report, "living_room_turn_ons_while_watching")
        self.assertEqual(row["verdict"], "pass")
        self.assertEqual((row["numerator"], row["denominator"]), (0, 1))
        self.assertEqual(row["human_writes"], 1)

    def test_an_explicit_command_with_a_person_behind_it_is_excluded(self):
        """A command the owner gave, with Home Assistant's own evidence of it."""
        with tempfile.TemporaryDirectory() as tmp:
            report = self.evening(tmp, [
                (T("20:05"), "light.front_left", "sofa", "off", "on", 60.0,
                 '{"user_id":"u1"}', "home_app_or_ha_user", "cmd-1")])
        row = metric_named(report, "living_room_turn_ons_while_watching")
        self.assertEqual(row["verdict"], "pass")
        self.assertEqual((row["numerator"], row["denominator"]), (0, 1))
        self.assertEqual(row["human_writes"], 1)

    def test_a_command_with_no_person_behind_it_stays_a_defect(self):
        """An automation writing through the override channel is not a person.

        The good-morning energize writes a command id. Testing the command id
        before the user id let any automation using that channel launder
        itself out of the defect count, which is the one number this metric
        exists to produce.
        """
        with tempfile.TemporaryDirectory() as tmp:
            report = self.evening(tmp, [
                (T("20:05"), "light.front_left", "sofa", "off", "on", 60.0, "{}",
                 "automation_or_script", "cmd-1")])
        row = metric_named(report, "living_room_turn_ons_while_watching")
        self.assertEqual(row["verdict"], "fail")
        self.assertEqual((row["numerator"], row["denominator"]), (1, 1))
        self.assertEqual(row["detail"][0]["writer"], "explicit_command_automation")
        self.assertEqual(row["human_writes"], 0)

    def test_an_automation_turn_on_while_watching_is_a_defect(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self.evening(tmp, [
                (T("20:05"), "light.front_left", "sofa", "off", "on", 60.0,
                 '{"parent_id":"auto1"}', "automation_or_script", None)])
        row = metric_named(report, "living_room_turn_ons_while_watching")
        self.assertEqual(row["verdict"], "fail")
        self.assertEqual((row["numerator"], row["denominator"]), (1, 1))
        self.assertEqual(row["detail"][0]["writer"], "automation")
        self.assertEqual(report["exit_code"], pm.EXIT_FAILED_BAR)

    def test_a_turn_on_at_zero_percent_is_a_turn_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self.evening(tmp, [
                (T("20:05"), "light.front_left", "sofa", "off", "on", 0.0,
                 '{"parent_id":"auto1"}', "automation_or_script", None)])
        row = metric_named(report, "living_room_turn_ons_while_watching")
        self.assertEqual((row["numerator"], row["denominator"]), (0, 0))
        self.assertEqual(row["verdict"], "pass")

    def test_without_the_ledger_attribution_is_skipped(self):
        history = base_history()
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        row = metric_named(report, "living_room_turn_ons_while_watching")
        self.assertEqual(row["verdict"], "skipped")
        self.assertIn("ledger", row["source"])


class AsleepAndActivityTests(unittest.TestCase):
    """Story S on the same evening, and the activity sensors when they exist."""

    def test_a_light_on_while_asleep_in_a_vacant_zone_is_a_defect(self):
        history = base_history(**{pm.ASLEEP: states(("18:00", "off"), ("23:00", "on"))})
        history["light.sink"] = states(("18:00", "off"), ("23:30", "on"))
        history[SINK_OCC] = states(("18:00", "off"))
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "ledger.sqlite")
            make_ledger(db, changes=[(T("23:30"), "light.sink", "sink", "off", "on", 20.0)])
            report, _ = run_report(tmp, history, no_ledger=False, db=db)
        row = metric_named(report, "lights_on_while_asleep_in_a_vacant_zone")
        self.assertEqual(row["verdict"], "fail")
        self.assertEqual(row["numerator"], 1)
        self.assertEqual(row["detail"][0]["zone"], "sink")

    def test_a_light_on_while_asleep_with_a_person_there_is_not_a_defect(self):
        history = base_history(**{pm.ASLEEP: states(("18:00", "off"), ("23:00", "on"))})
        history["light.sink"] = states(("18:00", "off"), ("23:30", "on"))
        history[SINK_OCC] = states(("18:00", "off"), ("23:29", "on"), ("23:45", "off"))
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "ledger.sqlite")
            make_ledger(db, changes=[(T("23:30"), "light.sink", "sink", "off", "on", 20.0)])
            report, _ = run_report(tmp, history, no_ledger=False, db=db)
        self.assertEqual(metric_named(report, "lights_on_while_asleep_in_a_vacant_zone")["verdict"],
                         "pass")

    def test_a_latch_that_was_never_on_is_skipped_not_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "ledger.sqlite")
            make_ledger(db, changes=[])
            report, _ = run_report(tmp, base_history(), no_ledger=False, db=db)
        row = metric_named(report, "lights_on_while_asleep_in_a_vacant_zone")
        self.assertEqual(row["verdict"], "skipped")
        self.assertEqual(row["source"], "the asleep latch was never on in this window")

    def test_activity_is_skipped_while_the_sensors_do_not_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, base_history())
        row = metric_named(report, "activity_other_than_idle_in_a_vacant_zone")
        self.assertEqual(row["verdict"], "skipped")
        self.assertIn("activity sensor", row["source"])

    def test_activity_other_than_idle_in_a_vacant_zone_fails(self):
        history = base_history()
        history[pm.activity_sensor("sink")] = states(("18:00", "idle"), ("21:00", "cooking"))
        history[SINK_OCC] = states(("18:00", "off"))
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        row = metric_named(report, "activity_other_than_idle_in_a_vacant_zone")
        self.assertEqual(row["verdict"], "fail")
        self.assertEqual(row["detail"], [{"t": T("21:00").isoformat(), "zone": "sink",
                                          "activity": "cooking"}])


class TimeZoneTests(unittest.TestCase):
    """Every reported timestamp is in the evening's own zone, whatever the
    source answered in."""

    def test_recorder_utc_rows_are_reported_locally(self):
        utc = dt.timezone.utc
        history = {
            pm.TV_PLAYING: [(T("18:00").astimezone(utc), "off", {}),
                            (T("20:00").astimezone(utc), "on", {}),
                            (T("22:00").astimezone(utc), "off", {})],
            pm.SOFA_STABLE: [(T("18:00").astimezone(utc), "off", {}),
                             (T("20:00").astimezone(utc), "on", {}),
                             (T("22:00").astimezone(utc), "off", {})],
        }
        for light in LR_LIGHTS:
            history[light] = [(T("18:00").astimezone(utc), "off", {})]
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        self.assertEqual(report["episodes"][0]["from"], T("20:00").isoformat())
        self.assertTrue(report["episodes"][0]["from"].endswith("-07:00"))


class JournalTests(unittest.TestCase):
    """UNATTENDED while the sofa is occupied, from the publisher journal."""

    def write_journal(self, tmp, rows):
        directory = Path(tmp) / "journal"
        directory.mkdir()
        path = directory / f"decisions-{DAY.isoformat()}.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return str(directory)

    def test_unattended_while_the_sofa_is_occupied_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = self.write_journal(tmp, [
                {"t": T("20:10").isoformat(), "event": "transition", "from": "PROVISIONAL",
                 "to": "UNATTENDED", "reason": "belief"},
                {"t": T("20:40").isoformat(), "event": "transition", "from": "UNATTENDED",
                 "to": "WATCHING", "reason": "sofa"}])
            report, _ = run_report(tmp, base_history(), journal=journal)
        row = metric_named(report, "unattended_while_sofa_occupied")
        self.assertEqual(row["verdict"], "fail")
        self.assertEqual(row["numerator"], 1)
        self.assertEqual(row["detail"][0]["from"], T("20:10").isoformat())

    def test_a_malformed_line_is_counted_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "journal"
            directory.mkdir()
            (directory / f"decisions-{DAY.isoformat()}.jsonl").write_text(
                json.dumps({"t": T("20:10").isoformat(), "event": "transition",
                            "from": "TV_OFF", "to": "WATCHING"}) + "\n{ half written",
                encoding="utf-8")
            report, _ = run_report(tmp, base_history(), journal=str(directory))
        self.assertEqual(metric_named(report, "unattended_while_sofa_occupied")["verdict"], "pass")
        self.assertTrue(report["sources"]["publisher_journal"]["available"])

    def test_no_journal_is_skipped_with_its_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, base_history())
        row = metric_named(report, "unattended_while_sofa_occupied")
        self.assertEqual(row["verdict"], "skipped")
        self.assertTrue(row["note"])
        self.assertFalse(report["sources"]["publisher_journal"]["available"])


class NothingMeasuredTests(unittest.TestCase):
    """A pass has to have measured something.

    These are the false-pass paths an adversarial review found in the first
    build: an evening where every bar skipped, and an evening whose recorder
    stopped while the film was still running. Both printed PASS and exit 0.
    """

    def test_an_evening_where_every_bar_skips_is_unscorable_not_a_pass(self):
        """The recorder holds the sofa and the television but no light at all.

        Every bar skips for want of data. The old rule read an empty failure
        list as a pass, so the tool announced story T working on an evening it
        had not measured.
        """
        history = {
            pm.TV_PLAYING: states(("18:00", "off"), ("20:00", "on"), ("22:00", "off")),
            pm.SOFA_STABLE: states(("18:00", "off"), ("20:00", "on"), ("22:00", "off")),
            pm.ANY_OCCUPIED: states(("18:00", "on")),
        }
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        self.assertEqual(report["verdict"], "unscorable")
        self.assertEqual(report["exit_code"], pm.EXIT_UNSCORABLE)
        self.assertEqual(report["counts"]["measured"], 0)
        self.assertIn("no metric could be scored", report["unscorable_reason"])

    def test_the_darkness_bar_is_required_for_a_pass(self):
        """Other bars passing says nothing about story T.

        The living room going dark IS story T. An evening that could not score
        it has not tested the story, whatever else came out clean.
        """
        history = base_history()
        history.pop("light.rear_left")
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        self.assertEqual(report["verdict"], "unscorable")
        self.assertIn(pm.REQUIRED_BAR, report["skipped_metrics"])

    def test_a_recorder_that_stops_inside_an_episode_is_unscorable(self):
        """The signature of a recorder that died, not of a quiet house.

        The television and the sofa come on at 18:30 and nothing is recorded
        again. Checking only each series' first row called that fully covered,
        and the lights' last known state -- off, at 18:00 -- then granted
        darkness in 0.0 s for a film nobody recorded.
        """
        history = {
            pm.TV_PLAYING: states(("18:00", "off"), ("18:30", "on")),
            pm.SOFA_STABLE: states(("18:00", "off"), ("18:30", "on")),
            pm.ANY_OCCUPIED: states(("18:00", "on")),
            pm.ROUTE_HELPER: states(("18:00", "30.0")),
        }
        for light in LR_LIGHTS:
            history[light] = states(("18:00", "off"))
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        self.assertEqual(report["verdict"], "unscorable")
        self.assertEqual(report["exit_code"], pm.EXIT_UNSCORABLE)
        self.assertIn("the recorder stopped", report["unscorable_reason"])
        self.assertTrue(report["coverage"]["quiet_gaps_inside_an_episode"])

    def test_silence_after_the_film_ended_is_an_ordinary_quiet_night(self):
        """The same gap, outside an episode, must not condemn the evening.

        Everyone goes to bed at 22:00 and nothing changes until 01:00. That is
        a house at rest, not a broken recorder, and the evening still scores.
        """
        history = base_history()
        history["light.front_left"] = states(("18:00", "on"), ("20:00:20", "off"))
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        self.assertEqual(report["verdict"], "pass")
        self.assertTrue(report["coverage"]["quiet_gaps"], "the quiet stretch is still reported")
        self.assertEqual(report["coverage"]["quiet_gaps_inside_an_episode"], [])


class ReceiptNamingTests(unittest.TestCase):
    """An unscorable run must not take the name a scored evening will want."""

    def test_an_unscorable_run_is_filed_apart(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, {})
            written = pm.write_receipt(Path(tmp) / "out", report["evening"], report)
        self.assertIn("unscorable", Path(written["json"]).name)
        self.assertNotEqual(Path(written["json"]).name, f"evening-{report['evening']}.json")

    def test_an_unscorable_run_leaves_the_evening_scorable_later(self):
        """The whole point: a run that measured nothing must not block the
        evening being scored properly afterwards."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            bad, _ = run_report(tmp, {})
            pm.write_receipt(out, bad["evening"], bad)
            history = base_history()
            history["light.front_left"] = states(("18:00", "on"), ("20:00:20", "off"))
            good, _ = run_report(tmp, history)
            written = pm.write_receipt(out, good["evening"], good)
        self.assertEqual(Path(written["json"]).name, f"evening-{good['evening']}.json")

    def test_a_second_scored_run_needs_rescore_and_is_stamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            history = base_history()
            history["light.front_left"] = states(("18:00", "on"), ("20:00:20", "off"))
            report, _ = run_report(tmp, history)
            first = pm.write_receipt(out, report["evening"], report)
            with self.assertRaises(pm.PostmortemError):
                pm.write_receipt(out, report["evening"], report)
            second = pm.write_receipt(out, report["evening"], report, rescore=True)
        self.assertNotEqual(first["json"], second["json"])
        self.assertIn("rescore", Path(second["json"]).name)


class ReportingTests(unittest.TestCase):
    """What the receipt tells a reader who was not there."""

    def test_an_evening_before_the_m2_deploy_says_so_in_words(self):
        history = base_history(**{
            pm.TV_PLAYING: states(("18:00", "unknown")),
            pm.LG_TV: states(("18:00", "off"), ("20:00", "playing"), ("22:00", "off")),
        })
        history["light.front_left"] = states(("18:00", "on"), ("20:00:20", "off"))
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        self.assertIn(pm.TV_PLAYING, report["packages"]["m2_entities_absent"])
        self.assertIn("pre-M2 packages", report["packages"]["note"])
        self.assertIn("pre-M2 packages", pm.render_markdown(report))

    def test_a_deployed_evening_carries_no_pre_m2_banner(self):
        history = base_history()
        history["light.front_left"] = states(("18:00", "on"), ("20:00:20", "off"))
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        self.assertEqual(report["packages"]["m2_entities_absent"], [])
        self.assertIsNone(report["packages"]["note"])

    def test_the_receipt_names_the_population_each_metric_used(self):
        history = base_history()
        history["light.front_left"] = states(("18:00", "on"), ("20:00:20", "off"))
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        self.assertIn(pm.REQUIRED_BAR, report["populations"])
        self.assertIn("watching_minutes_with_sofa", report)
        self.assertIn("Populations", pm.render_markdown(report))

    def test_the_counts_say_how_much_was_measured(self):
        history = base_history()
        history["light.front_left"] = states(("18:00", "on"), ("20:00:20", "off"))
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        counts = report["counts"]
        self.assertEqual(counts["bars"], counts["measured"] + counts["skipped"])
        self.assertGreaterEqual(counts["measured"], 1)
        self.assertIn("measured", pm.render_markdown(report))

    def test_the_attribute_query_is_not_significant_changes_only(self):
        """predicted_brightness_pct moves without the sensor's state moving.

        Left on the recorder's default the evidence printed beside a turn-on
        failure could be hours stale, describing a different moment entirely.
        """
        path = pm.history_path(*WINDOW, ["sensor.x"], minimal=False)
        self.assertIn("significant_changes_only=0", path)
        self.assertNotIn("minimal_response", path)
        minimal = pm.history_path(*WINDOW, ["sensor.x"], minimal=True)
        self.assertIn("minimal_response", minimal)
        self.assertNotIn("significant_changes_only", minimal)


class UnscorableTests(unittest.TestCase):
    """The two cases the plan says must not be scored."""

    def test_no_recorder_coverage_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, {})
        self.assertEqual(report["verdict"], "unscorable")
        self.assertEqual(report["exit_code"], pm.EXIT_UNSCORABLE)
        self.assertIn("the recorder does not cover this window", report["unscorable_reason"])
        self.assertEqual(report["metrics"], [])

    def test_a_recorder_that_starts_late_is_not_a_scored_evening(self):
        history = {
            pm.TV_PLAYING: states(("23:00", "on"), ("23:30", "off")),
            pm.SOFA_STABLE: states(("23:00", "on"), ("23:30", "off")),
        }
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        self.assertEqual(report["exit_code"], pm.EXIT_UNSCORABLE)
        self.assertIn(pm.SOFA_STABLE, report["coverage"]["starts_late"])

    def test_an_evening_with_no_episode_exits_2(self):
        history = base_history(**{
            pm.TV_PLAYING: states(("18:00", "off")),
            pm.SOFA_STABLE: states(("18:00", "off"), ("20:00", "on"), ("21:00", "off")),
        })
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        self.assertEqual(report["verdict"], "unscorable")
        self.assertEqual(report["exit_code"], pm.EXIT_UNSCORABLE)
        self.assertEqual(report["unscorable_reason"], "no watching episode in this window")

    def test_a_tv_that_played_with_nobody_on_the_sofa_is_no_episode(self):
        history = base_history(**{pm.SOFA_STABLE: states(("18:00", "off"))})
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, history)
        self.assertEqual(report["exit_code"], pm.EXIT_UNSCORABLE)


class ReceiptTests(unittest.TestCase):
    """The receipt's shape, its modes, and that it is never overwritten."""

    def test_receipt_shape_and_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, base_history())
            written = pm.write_receipt(Path(tmp) / "out", report["evening"], report)
            self.assertTrue(written["json"].endswith("evening-2026-09-17.json"))
            self.assertTrue(written["md"].endswith("evening-2026-09-17.md"))
            saved = json.loads(Path(written["json"]).read_text(encoding="utf-8"))
            for key in ("schema", "generated_at", "evening", "window", "timezone", "inputs",
                        "coverage", "sources", "episodes", "metrics", "verdict", "exit_code"):
                self.assertIn(key, saved)
            self.assertEqual(saved["schema"], pm.SCHEMA)
            self.assertEqual(saved["window"], [T("18:00").isoformat(), T("01:00", 1).isoformat()])
            for row in saved["metrics"]:
                self.assertIn(row["verdict"], ("pass", "fail", "skipped", "info"))
                self.assertIn("bar", row)
                self.assertIn("denominator", row)
                self.assertIn("source", row)
            names = [row["metric"] for row in saved["metrics"]]
            self.assertEqual(names, [
                "living_room_dark_within_30s", "living_room_turn_ons_while_watching",
                "errand_lit_at_or_below_route_pct_within_60s",
                "errand_off_within_150s_of_clearing", "unattended_while_sofa_occupied",
                "activity_other_than_idle_in_a_vacant_zone",
                "lights_on_while_asleep_in_a_vacant_zone", "evening_cost_in_light_changes"])
            self.assertEqual(os.stat(written["json"]).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(written["md"]).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(Path(tmp) / "out").st_mode & 0o777, 0o700)
            page = Path(written["md"]).read_text(encoding="utf-8")
            self.assertIn("# TV evening post-mortem (2026-09-17)", page)
            self.assertIn("living_room_dark_within_30s", page)

    def test_the_page_caps_a_long_failure_list_and_says_so(self):
        report = {"evening": "2026-09-17", "window": ["a", "b"], "timezone": "America/Los_Angeles",
                  "verdict": "fail", "exit_code": 1, "episodes": [],
                  "sources": {"recorder": {"available": True}},
                  "coverage": {"covered": True, "tv_source": pm.LG_TV},
                  "metrics": [{"metric": "errand_off_within_150s_of_clearing", "verdict": "fail",
                               "bar": ">= 90%", "numerator": 1, "denominator": 40,
                               "fraction": 0.025, "source": "recorder",
                               "detail": [{"zone": f"z{i}"} for i in range(40)]}]}
        page = pm.render_markdown(report)
        self.assertIn("... and 30 more, in evening-2026-09-17.json", page)
        self.assertEqual(page.count('{"zone"'), pm.MD_DETAIL_LIMIT)

    def test_a_receipt_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = run_report(tmp, base_history())
            pm.write_receipt(Path(tmp) / "out", report["evening"], report)
            with self.assertRaises(pm.PostmortemError):
                pm.write_receipt(Path(tmp) / "out", report["evening"], report)

    def test_the_cost_of_the_evening_is_reported_not_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "ledger.sqlite")
            make_ledger(db, changes=[
                (T("20:05"), "light.sink", "sink", "off", "on", 30.0),
                (T("20:09"), "light.sink", "sink", "on", "off", 0.0)])
            report, _ = run_report(tmp, base_history(), no_ledger=False, db=db)
        row = metric_named(report, "evening_cost_in_light_changes")
        self.assertEqual(row["verdict"], "info")
        self.assertEqual(row["numerator"], 2)
        self.assertEqual(row["by_zone"], {"sink": 2})


class SecretTests(unittest.TestCase):
    """The token reaches the Authorization header and nothing else."""

    def test_no_token_in_the_report_the_page_or_the_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = base_history()
            getter = FakeHa(history)
            args = args_for(tmp, ha_url=f"http://ha.invalid:8123/?token={TOKEN}")
            report = pm.run(args, getter=getter, now=T("09:00", 1), secrets=(TOKEN,))
            written = pm.write_receipt(Path(tmp) / "out", report["evening"], report)
            blob = json.dumps(report) + Path(written["json"]).read_text(encoding="utf-8") \
                + Path(written["md"]).read_text(encoding="utf-8") + pm.render_markdown(report)
        self.assertNotIn(TOKEN, blob)
        self.assertIn(pm.REDACTED, report["inputs"]["ha_url"])
        for path in getter.paths:
            self.assertNotIn(TOKEN, path)

    def test_scrub_reaches_nested_values(self):
        payload = {"a": [f"bearer {TOKEN}"], "b": {"c": TOKEN}, "d": 3}
        cleaned = pm.scrub(payload, (TOKEN,))
        self.assertEqual(cleaned, {"a": [f"bearer {pm.REDACTED}"], "b": {"c": pm.REDACTED}, "d": 3})

    def test_read_token_handles_quotes_and_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "env"
            path.write_text(f'OTHER=1\nHA_TOKEN="{TOKEN}"\n', encoding="utf-8")
            self.assertEqual(pm.read_token(path), TOKEN)
            path.write_text("OTHER=1\n", encoding="utf-8")
            self.assertIsNone(pm.read_token(path))
            self.assertIsNone(pm.read_token(Path(tmp) / "missing"))

    def test_the_getter_only_builds_get_requests(self):
        get = pm.http_getter("http://ha.invalid:8123", TOKEN)
        self.assertTrue(callable(get))
        # The closure's only caller-facing argument is a path: there is no
        # method and no body, so no caller can reach a service call.
        self.assertEqual(get.__code__.co_varnames[:1], ("path",))


class WindowTests(unittest.TestCase):
    """--since/--until, and the default evening."""

    def test_dates_become_the_evening_window(self):
        self.assertEqual(pm.parse_when("2026-09-17", TZ, 18), T("18:00"))
        self.assertEqual(pm.parse_when("2026-09-18", TZ, 1), T("01:00", 1))
        self.assertEqual(pm.parse_when("2026-09-17T19:30", TZ, 18), T("19:30"))

    def test_default_window_is_the_most_recent_evening(self):
        morning = dt.datetime(2026, 9, 18, 9, 30, tzinfo=TZ)
        self.assertEqual(pm.default_window(morning, TZ), (T("18:00"), T("01:00", 1)))
        evening = dt.datetime(2026, 9, 17, 19, 0, tzinfo=TZ)
        self.assertEqual(pm.default_window(evening, TZ), (T("18:00"), T("01:00", 1)))

    def test_history_path_is_a_get_query_over_the_window(self):
        path = pm.history_path(*WINDOW, [pm.TV_PLAYING, pm.SOFA_STABLE])
        self.assertTrue(path.startswith("history/period/"))
        self.assertIn("minimal_response", path)
        self.assertIn(urllib.parse.quote(pm.TV_PLAYING), path)
        self.assertNotIn("minimal_response", pm.history_path(*WINDOW, [pm.TV_PLAYING], minimal=False))


if __name__ == "__main__":
    unittest.main()
