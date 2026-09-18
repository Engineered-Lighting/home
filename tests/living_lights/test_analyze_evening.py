"""Unit tests for the pure evening metrics in tools/lighting-sim/analyze_evening.py.

Run: python3 -m unittest tests/living_lights/test_analyze_evening.py

Hand-built call lists and timelines only; no Home Assistant, no simulator
venv. Only the pure functions are imported (the report writer needs the
harness objects and is exercised by tools/lighting-sim/test_sim_tv.py).
Covers the M1 verifier's fixes: per-entity service-call targets, route
latency that ignores same-level re-sets, route return latency, flicker
counted on level changes only, the timeline as a forced watching source,
the normalised call and snapshot digests and the validity gate.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("analyze_evening", REPO / "tools" / "lighting-sim" / "analyze_evening.py")
ae = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ae)

TZ = ZoneInfo("America/Los_Angeles")
DAY = dt.date(2026, 9, 13)


def T(hhmm: str, plus_days: int = 0) -> dt.datetime:
    return ae.at_hhmm(DAY + dt.timedelta(days=plus_days), hhmm, TZ)


def call(hhmm: str, entity: str, service: str = "turn_on", pct=None, frm="off", ctx="c1", parent=None) -> dict:
    to = "off" if service == "turn_off" or pct == 0 else "on"
    return {"t": T(hhmm).isoformat(), "service": service, "entity": entity, "from": frm, "to": to,
            "brightness_pct": pct if service == "turn_on" else 0, "context_id": ctx, "context_parent": parent}


WINDOW = (T("18:00"), T("01:00", 1))


class IntervalTests(unittest.TestCase):
    def test_on_intervals_clips_to_window_and_closes_open_tail(self):
        edges = [(T("18:00"), "off"), (T("19:00"), "on"), (T("20:00"), "off"), (T("23:00"), "on")]
        self.assertEqual(ae.on_intervals(edges, *WINDOW), [(T("19:00"), T("20:00")), (T("23:00"), T("01:00", 1))])

    def test_on_intervals_initial_on(self):
        edges = [(T("18:00"), "on"), (T("18:30"), "off")]
        self.assertEqual(ae.on_intervals(edges, *WINDOW), [(T("18:00"), T("18:30"))])

    def test_intersect(self):
        a = [(T("19:00"), T("21:00"))]
        b = [(T("18:30"), T("19:30")), (T("20:30"), T("22:00"))]
        self.assertEqual(ae.intersect(a, b), [(T("19:00"), T("19:30")), (T("20:30"), T("21:00"))])

    def test_watching_from_belief_when_fed(self):
        initial = {ae.TV_WATCHING: "off"}
        events = [(T("20:05"), ae.TV_WATCHING, "on"), (T("23:30"), ae.TV_WATCHING, "off"),
                  (T("20:00"), ae.TV_ENTITY, "on")]
        got, source = ae.watching_intervals(initial, events, *WINDOW)
        self.assertEqual(source, "belief")
        self.assertEqual(got, [(T("20:05"), T("23:30"))])

    def test_watching_from_timeline_when_no_belief(self):
        initial = {ae.TV_ENTITY: "off", ae.SOFA: "off"}
        events = [(T("20:00"), ae.TV_ENTITY, "on"), (T("20:05"), ae.SOFA, "on"),
                  (T("21:00"), ae.SOFA, "off"), (T("21:03"), ae.SOFA, "on"),
                  (T("22:00"), ae.TV_ENTITY, "standby"), (T("22:10"), ae.TV_ENTITY, "playing"),
                  (T("23:30"), ae.SOFA, "off")]
        got, source = ae.watching_intervals(initial, events, *WINDOW)
        self.assertEqual(source, "timeline")
        self.assertEqual(got, [(T("20:05"), T("21:00")), (T("21:03"), T("22:00")), (T("22:10"), T("23:30"))])


class TurnOnTests(unittest.TestCase):
    def test_turn_ons_filter_and_attribution(self):
        calls = [call("19:00", "light.sink", pct=80, ctx="a"),
                 call("19:05", "light.sink", pct=90, frm="on", ctx="a"),      # already on: not a turn-on
                 call("19:10", "light.office", pct=0, ctx="b"),               # brightness 0 is off
                 call("19:20", "light.office", service="turn_off", ctx="b"),
                 call("17:00", "light.office", pct=50, ctx="c"),              # before the window
                 call("22:00", "light.office", pct=50, ctx=None, parent="p")]
        owners = {"a": "automation.sink", "p": "automation.office"}
        got = ae.turn_ons(calls, *WINDOW, owner=lambda c, p: owners.get(c) or owners.get(p))
        self.assertEqual([(d["light"], d["pct"], d["by"]) for d in got],
                         [("light.sink", 80, "automation.sink"), ("light.office", 50, "automation.office")])
        self.assertEqual(ae.attribution(got), {"automation.sink": 1, "automation.office": 1})

    def test_living_room_turn_ons_while_watching(self):
        detail = [{"t": T("20:10").isoformat(), "light": "light.front_left", "pct": 8, "by": None},
                  {"t": T("20:10").isoformat(), "light": "light.sink", "pct": 8, "by": None},
                  {"t": T("19:00").isoformat(), "light": "light.office", "pct": 80, "by": None}]
        got = ae.living_room_turn_ons_while_watching(detail, [(T("20:00"), T("23:00"))])
        self.assertEqual([d["light"] for d in got], ["light.front_left"])

    def test_brighten_while_watching_tracks_levels(self):
        calls = [call("19:00", "light.rear_left", pct=80),
                 call("20:10", "light.rear_left", pct=8, frm="on"),      # dimming: not a raise
                 call("20:20", "light.rear_left", pct=20, frm="on"),     # raise inside the episode
                 call("20:30", "light.sink", pct=100),                    # not a living-room light
                 call("20:40", "light.office", pct=None),                # from off, no level: counts
                 call("23:30", "light.office", pct=90, frm="on")]        # after the episode
        got = ae.brighten_while_watching(calls, [(T("20:00"), T("23:00"))])
        self.assertEqual([(g["light"], g["from_pct"], g["to_pct"]) for g in got],
                         [("light.rear_left", 8, 20), ("light.office", 0, 1)])


class DarkAndRouteTests(unittest.TestCase):
    def test_time_to_dark(self):
        calls = [call("19:00", "light.front_left", pct=80), call("19:00", "light.rear_left", pct=80),
                 call("20:05", "light.front_left", pct=0, frm="on"),
                 call("20:06", "light.rear_left", service="turn_off", frm="on")]
        watching = [(T("20:04"), T("23:00"))]
        sofa = [(T("20:04"), T("23:00"))]
        rows = ae.time_to_dark(calls, watching, sofa, WINDOW[1])
        self.assertEqual(rows[0]["sofa_from"], T("20:04").isoformat())
        self.assertEqual(rows[0]["time_to_dark_s"], 120.0)

    def test_time_to_dark_never_and_already_dark(self):
        calls = [call("19:00", "light.front_left", pct=80)]
        rows = ae.time_to_dark(calls, [(T("20:00"), T("23:00"))], [(T("20:00"), T("23:00"))], WINDOW[1])
        self.assertIsNone(rows[0]["time_to_dark_s"])
        rows = ae.time_to_dark([], [(T("20:00"), T("23:00"))], [(T("20:00"), T("23:00"))], WINDOW[1])
        self.assertEqual(rows[0]["time_to_dark_s"], 0.0)
        rows = ae.time_to_dark([], [(T("20:00"), T("23:00"))], [], WINDOW[1])
        self.assertIsNone(rows[0]["sofa_from"])

    def test_route_metrics(self):
        initial = {"binary_sensor.sink_person_occupancy": "off"}
        events = [(T("21:00"), "binary_sensor.sink_person_occupancy", "on"),
                  (T("21:03"), "binary_sensor.sink_person_occupancy", "off"),
                  (T("00:30", 1), "binary_sensor.sink_person_occupancy", "on"),   # an hour after the episode: not an errand
                  (T("00:35", 1), "binary_sensor.sink_person_occupancy", "off")]
        calls = [call("21:00:20", "light.sink", pct=80),
                 call("21:04", "light.sink", pct=8, frm="on"),
                 call("21:05", "light.sink", pct=0, frm="on")]
        watching = [(T("20:00"), T("23:30"))]
        rows = ae.route_metrics(calls, initial, events, watching, *WINDOW, route_pct=30)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["zone"], "sink")
        self.assertEqual(r["route_latency_s"], 20.0)
        self.assertEqual(r["route_off_latency_s"], 120.0)
        self.assertEqual([o["pct"] for o in r["overshoot"]], [80])

    def test_route_no_light_and_helper_default(self):
        initial = {"binary_sensor.island_left_person_occupancy": "off"}
        events = [(T("21:00"), "binary_sensor.island_left_person_occupancy", "on"),
                  (T("21:10"), "binary_sensor.island_left_person_occupancy", "off")]
        rows = ae.route_metrics([], initial, events, [(T("20:00"), T("23:30"))], *WINDOW)
        self.assertIsNone(rows[0]["route_latency_s"])
        self.assertEqual(rows[0]["route_off_latency_s"], 0.0)
        self.assertEqual(rows[0]["route_pct"], ae.DEFAULT_ROUTE_PCT)


class FlickerAmbientSnapshotTests(unittest.TestCase):
    def test_unavailable_flicker(self):
        initial = {ae.TV_ENTITY: "off"}
        events = [(T("20:00"), ae.TV_ENTITY, "on"), (T("20:30"), ae.TV_ENTITY, "unavailable"),
                  (T("20:30:30"), ae.TV_ENTITY, "on"), (T("23:50"), ae.TV_ENTITY, "unavailable")]
        calls = [call("20:30:40", "light.front_left", pct=20), call("20:33", "light.rear_left", pct=20),
                 call("23:51", "light.office", pct=20)]
        rows = ae.unavailable_flicker(calls, initial, events, [(T("20:05"), T("23:30"))], *WINDOW)
        self.assertEqual(len(rows), 1)              # the 23:50 edge is outside the episode
        self.assertEqual([h["light"] for h in rows[0]["turn_ons"]], ["light.front_left"])

    def test_ambient_calls(self):
        sw = [{"t": T("20:00").isoformat(), "service": "turn_off", "entity": "switch.a"},
              {"t": T("20:00").isoformat(), "service": "turn_off", "entity": "switch.b"},
              {"t": T("23:30").isoformat(), "service": "turn_on", "entity": "switch.a"},
              {"t": T("17:00").isoformat(), "service": "turn_on", "entity": "switch.a"}]
        self.assertEqual(ae.ambient_calls(sw, *WINDOW), {"on": 1, "off": 2})

    def test_snapshot_metrics(self):
        snaps = [{"t": T("19:59").isoformat(), "lights_on": {"light.office": 80}},
                 {"t": T("20:00").isoformat(), "lights_on": {"light.office": 8, "light.sink": 8, "light.rear_left": 8}},
                 {"t": T("21:59").isoformat(), "lights_on": {}},
                 {"t": T("22:30").isoformat(), "lights_on": {"light.sink": 50}}]
        self.assertEqual(ae.max_lights_on_while_watching(snaps, [(T("20:00"), T("23:00"))]), 3)
        self.assertEqual(ae.lights_on_at(snaps, T("22:00")), {})
        self.assertEqual(ae.lights_on_at(snaps, T("19:00")), {})
        self.assertEqual(ae.lights_on_at(snaps, T("19:59")), {"light.office": 80})

    def test_state_changes(self):
        changes = [{"t": T("20:00").isoformat(), "entity": "binary_sensor.living_lights_tv_playing", "from": "off", "to": "on"},
                   {"t": T("17:00").isoformat(), "entity": "binary_sensor.living_lights_tv_playing", "from": "on", "to": "off"},
                   {"t": T("20:00").isoformat(), "entity": "media_player.lg_tv", "from": "off", "to": "on"}]
        self.assertEqual(len(ae.state_changes(changes, "binary_sensor.living_lights_tv_playing", *WINDOW)), 1)
        self.assertEqual(len(ae.state_changes(changes, "binary_sensor.living_lights_tv_playing")), 2)


class AnalyzeCallsTests(unittest.TestCase):
    def test_end_to_end_on_plain_data(self):
        initial = {ae.TV_ENTITY: "off", ae.SOFA: "off", "binary_sensor.sink_person_occupancy": "off"}
        events = [(T("20:00"), ae.TV_ENTITY, "on"), (T("20:05"), ae.SOFA, "on"),
                  (T("21:00"), ae.SOFA, "off"), (T("21:00"), "binary_sensor.sink_person_occupancy", "on"),
                  (T("21:03"), "binary_sensor.sink_person_occupancy", "off"), (T("21:03"), ae.SOFA, "on"),
                  (T("23:30"), ae.TV_ENTITY, "off"), (T("23:30"), ae.SOFA, "off")]
        calls = [call("19:00", "light.front_left", pct=80, ctx="a"),
                 call("20:06", "light.front_left", pct=8, frm="on", ctx="a"),
                 call("21:00:20", "light.sink", pct=80, ctx="b")]
        snaps = [{"t": T("20:00").isoformat(), "lights_on": {"light.front_left": 80}},
                 {"t": T("22:00").isoformat(), "lights_on": {"light.front_left": 8, "light.sink": 80}}]
        r = ae.analyze_calls(calls=calls, switch_calls=[], changes=[], snapshots=snaps, initial=initial,
                             events=events, window=WINDOW, owner=lambda c, p: {"a": "automation.sofa"}.get(c))
        self.assertEqual(r["watching_source"], "timeline")
        self.assertEqual(r["turn_ons"], 2)
        self.assertEqual(r["living_room_turn_ons_while_watching"], 0)
        self.assertIsNone(r["time_to_dark_s"])               # front_left stays at 8 %
        self.assertEqual(r["route_overshoot"], 1)
        self.assertEqual(r["route_latency_s"], [20.0])
        self.assertEqual(r["lights_on_at"]["2200"], {"light.front_left": 8, "light.sink": 80})
        self.assertEqual(r["turn_ons_by_automation"], {"automation.sofa": 1, "unknown": 1})
        self.assertEqual(r["brighten_while_watching"], [])

    def test_default_window(self):
        start, end = ae.default_window(T("22:00"))
        self.assertEqual((start, end), (T("18:00"), T("01:00", 1)))


class WatchingSourceTests(unittest.TestCase):
    INITIAL = {ae.TV_WATCHING: "off", ae.TV_ENTITY: "off", ae.SOFA: "off"}
    # The belief freezes at "on" (a dead publisher); the timeline ends the film at 23:30.
    EVENTS = [(T("20:00"), ae.TV_ENTITY, "on"), (T("20:05"), ae.SOFA, "on"),
              (T("20:05"), ae.TV_WATCHING, "on"),
              (T("23:30"), ae.TV_ENTITY, "off"), (T("23:30"), ae.SOFA, "off")]

    def test_timeline_overrides_a_fed_belief(self):
        got, source = ae.watching_intervals(self.INITIAL, self.EVENTS, *WINDOW)
        self.assertEqual((source, got), ("belief", [(T("20:05"), T("01:00", 1))]))
        got, source = ae.watching_intervals(self.INITIAL, self.EVENTS, *WINDOW, source="timeline")
        self.assertEqual((source, got), ("timeline", [(T("20:05"), T("23:30"))]))

    def test_belief_source_without_a_belief_is_empty(self):
        initial = {k: v for k, v in self.INITIAL.items() if k != ae.TV_WATCHING}
        events = [e for e in self.EVENTS if e[1] != ae.TV_WATCHING]
        got, source = ae.watching_intervals(initial, events, *WINDOW, source="belief")
        self.assertEqual((source, got), ("belief", []))
        with self.assertRaises(ValueError):
            ae.watching_intervals(initial, events, *WINDOW, source="oracle")

    def test_analyze_calls_forces_the_timeline(self):
        calls = [call("19:00", "light.front_left", pct=8),
                 call("23:30", "light.front_left", pct=80, frm="on")]     # the TV-off brightening
        r = ae.analyze_calls(calls=calls, switch_calls=[], changes=[], snapshots=[], initial=self.INITIAL,
                             events=self.EVENTS, window=WINDOW)
        self.assertEqual(r["watching_source"], "belief")
        self.assertEqual(len(r["brighten_while_watching"]), 1)          # the stale belief's artefact
        r = ae.analyze_calls(calls=calls, switch_calls=[], changes=[], snapshots=[], initial=self.INITIAL,
                             events=self.EVENTS, window=WINDOW, watching_source="timeline")
        self.assertEqual(r["watching_source"], "timeline")
        self.assertEqual(r["watching_episodes"], [[T("20:05").isoformat(), T("23:30").isoformat()]])
        self.assertEqual(r["brighten_while_watching"], [])


class RouteLatencyTests(unittest.TestCase):
    """T1's shape on today's packages: the sink sits at the 8 % floor, the
    tick re-sets it to 8 % at the occupancy edge, the route call (80 %)
    comes 20 s later, the floor returns 120 s after vacancy, off at 23:47."""
    INITIAL = {"binary_sensor.sink_person_occupancy": "off"}
    EVENTS = [(T("21:00"), "binary_sensor.sink_person_occupancy", "on"),
              (T("21:03"), "binary_sensor.sink_person_occupancy", "off")]
    WATCHING = [(T("20:05"), T("23:30"))]

    def test_same_level_reset_at_the_edge_does_not_count(self):
        calls = [call("20:00", "light.sink", pct=8),
                 call("21:00:00", "light.sink", pct=8, frm="on"),      # tick re-set, level unchanged
                 call("21:00:00", "light.sink", pct=19, frm="on"),     # the first raise
                 call("21:00:20", "light.sink", pct=80, frm="on"),
                 call("21:04", "light.sink", pct=80, frm="on"),
                 call("21:05", "light.sink", pct=8, frm="on"),         # back to the floor
                 call("23:47", "light.sink", service="turn_off", frm="on")]
        r = ae.route_metrics(calls, self.INITIAL, self.EVENTS, self.WATCHING, *WINDOW)[0]
        self.assertEqual(r["route_latency_s"], 0.0)                     # 19 % at 21:00:00 is a raise
        calls.pop(2)
        r = ae.route_metrics(calls, self.INITIAL, self.EVENTS, self.WATCHING, *WINDOW)[0]
        self.assertEqual(r["route_latency_s"], 20.0)
        self.assertEqual(r["pre_errand_levels"], {"light.sink": 8})
        self.assertEqual(r["route_return_latency_s"], 120.0)
        self.assertEqual(r["route_off_latency_s"], 9840.0)
        self.assertEqual([o["pct"] for o in r["overshoot"]], [80, 80])

    def test_from_off_counts_and_return_equals_off_when_the_floor_was_off(self):
        calls = [call("21:00:00", "light.sink", pct=30),
                 call("21:04", "light.sink", service="turn_off", frm="on")]
        r = ae.route_metrics(calls, self.INITIAL, self.EVENTS, self.WATCHING, *WINDOW)[0]
        self.assertEqual(r["route_latency_s"], 0.0)
        self.assertEqual(r["pre_errand_levels"], {})
        self.assertEqual(r["route_return_latency_s"], 60.0)
        self.assertEqual(r["route_off_latency_s"], 60.0)
        self.assertEqual(r["overshoot"], [])

    def test_never_returned(self):
        calls = [call("20:00", "light.sink", pct=8),
                 call("21:00:00", "light.sink", pct=8, frm="on"),
                 call("21:02", "light.sink", pct=20, frm="on")]        # stays at 20 % after vacancy
        r = ae.route_metrics(calls, self.INITIAL, self.EVENTS, self.WATCHING, *WINDOW)[0]
        self.assertEqual(r["route_latency_s"], 120.0)
        self.assertIsNone(r["route_return_latency_s"])
        self.assertIsNone(r["route_off_latency_s"])

    def test_never_raised(self):
        calls = [call("20:00", "light.sink", pct=8),
                 call("21:00:00", "light.sink", pct=8, frm="on")]      # only the re-set: no route
        r = ae.route_metrics(calls, self.INITIAL, self.EVENTS, self.WATCHING, *WINDOW)[0]
        self.assertIsNone(r["route_latency_s"])
        self.assertEqual(r["route_return_latency_s"], 0.0)              # at the floor when vacated
        self.assertIsNone(r["route_off_latency_s"])

    def test_settled_after_with_a_ceiling(self):
        calls = [call("20:00", "light.sink", pct=80), call("20:10", "light.sink", pct=8, frm="on")]
        self.assertEqual(ae.settled_after(calls, T("20:00"), ["light.sink"], WINDOW[1], ceiling={"light.sink": 8}), 600.0)
        self.assertEqual(ae.settled_after(calls, T("20:00"), ["light.sink"], WINDOW[1], ceiling={"light.sink": 80}), 0.0)
        self.assertIsNone(ae.settled_after(calls, T("20:00"), ["light.sink"], WINDOW[1]))
        self.assertEqual(ae.settled_after(calls, T("20:00"), ["light.office"], WINDOW[1]), 0.0)


class FlickerLevelChangeTests(unittest.TestCase):
    def test_same_level_resets_are_raw_only(self):
        initial = {ae.TV_ENTITY: "off"}
        events = [(T("20:00"), ae.TV_ENTITY, "on"), (T("20:30"), ae.TV_ENTITY, "unavailable"),
                  (T("20:30:30"), ae.TV_ENTITY, "on")]
        calls = [call("19:00", "light.front_left", pct=8), call("19:00", "light.rear_left", pct=8),
                 call("20:30", "light.front_left", pct=8, frm="on"),      # tick re-set: raw only
                 call("20:30", "light.rear_left", pct=20, frm="on"),      # a level change
                 call("20:31", "light.office", pct=20),                   # from off
                 call("20:31", "light.rear_left", service="turn_off", frm="on"),   # not a turn_on
                 call("20:33", "light.front_left", pct=0, frm="on")]      # to off: not a turn_on
        rows = ae.unavailable_flicker(calls, initial, events, [(T("20:05"), T("23:30"))], *WINDOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["turn_ons_raw"], 3)
        self.assertEqual([(h["light"], h["from_pct"], h["to_pct"]) for h in rows[0]["turn_ons"]],
                         [("light.rear_left", 8, 20), ("light.office", 0, 20)])
        # A timeline episode closes on the unavailable edge (the TV is in
        # TV_OFF_STATES), so the blip is inside an episode only when a belief
        # keeps watching on, as T8 feeds it.
        r = ae.analyze_calls(calls=calls, switch_calls=[], changes=[], snapshots=[],
                             initial={**initial, ae.SOFA: "off", ae.TV_WATCHING: "off"},
                             events=events + [(T("20:05"), ae.SOFA, "on"), (T("20:05"), ae.TV_WATCHING, "on")],
                             window=WINDOW)
        self.assertEqual((r["unavailable_flicker"], r["unavailable_flicker_raw"]), (2, 3))
        r = ae.analyze_calls(calls=calls, switch_calls=[], changes=[], snapshots=[],
                             initial={**initial, ae.SOFA: "off"},
                             events=events + [(T("20:05"), ae.SOFA, "on")], window=WINDOW)
        self.assertEqual((r["unavailable_flicker"], r["unavailable_flicker_raw"]), (0, 0))


class DigestTests(unittest.TestCase):
    def test_call_digest_is_order_insensitive_and_keeps_the_final_level(self):
        a = [call("20:00", "light.sink", pct=20), call("20:00", "light.sink", pct=8, frm="on"),
             call("20:00", "light.office", pct=50), call("20:01", "light.office", service="turn_off", frm="on"),
             call("20:02", "light.rear_left", pct=None)]                  # bare turn_on: None
        # Another dispatch order across lights (one light's own calls keep
        # their order: the final level per light is what the digest keeps).
        b = [a[2], a[0], a[1], a[4], a[3]]
        self.assertEqual(ae.call_digest(a), ae.call_digest(b))
        self.assertEqual(ae.call_digest(a), [[T("20:00").isoformat(), "light.office", 50],
                                             [T("20:00").isoformat(), "light.sink", 8],
                                             [T("20:01").isoformat(), "light.office", 0],
                                             [T("20:02").isoformat(), "light.rear_left", None]])
        # A bare turn_on next to a levelled call at the same instant keeps the level.
        c = a + [call("20:00", "light.sink", pct=None, frm="on")]
        self.assertEqual(ae.call_digest(c), ae.call_digest(a))

    def test_call_digest_sees_a_real_difference(self):
        a = [call("20:00", "light.sink", pct=20), call("20:00", "light.sink", pct=50, frm="on")]
        b = [call("20:00", "light.sink", pct=20), call("20:00", "light.sink", pct=8, frm="on")]
        self.assertNotEqual(ae.call_digest(a), ae.call_digest(b))
        d = ae.digest_diff(ae.call_digest(a), ae.call_digest(b))
        self.assertEqual([(x["only_in"], x["row"][2]) for x in d], [("a", 50), ("b", 8)])
        extra = a + [call("22:31", "light.front_left", pct=8, frm="on")]
        self.assertEqual([x["only_in"] for x in ae.digest_diff(ae.call_digest(a), ae.call_digest(extra))], ["b"])
        # The same light's same-instant calls in another order end at another
        # level: that is a real difference, not dispatch noise.
        self.assertEqual(len(ae.digest_diff(ae.call_digest(a), ae.call_digest(list(reversed(a))))), 2)
        # Interleaving across lights is not.
        a2 = a + [call("20:00", "light.office", pct=50)]
        b2 = [a2[2], a2[0], a2[1]]
        self.assertEqual(ae.digest_diff(ae.call_digest(a2), ae.call_digest(b2)), [])

    def test_snapshot_digest(self):
        snaps = [{"t": T("20:00").isoformat(), "lights_on": {"light.sink": 8, "light.office": 50}, "switches_on": ["switch.b", "switch.a"]},
                 {"t": T("20:01").isoformat(), "lights_on": {}, "switches_on": []}]
        self.assertEqual(ae.snapshot_digest(snaps), [
            [T("20:00").isoformat(), [["light.office", 50], ["light.sink", 8]], ["switch.a", "switch.b"]],
            [T("20:01").isoformat(), [], []]])
        other = [dict(snaps[0], lights_on={"light.office": 50, "light.sink": 8}), snaps[1]]
        self.assertEqual(ae.digest_diff(ae.snapshot_digest(snaps), ae.snapshot_digest(other)), [])
        self.assertEqual(len(ae.digest_diff(ae.snapshot_digest(snaps), ae.snapshot_digest(snaps[:1]))), 1)

    def test_analyze_calls_carries_both_digests_inside_the_window(self):
        calls = [call("17:00", "light.sink", pct=20), call("20:00", "light.sink", pct=8, frm="on")]
        snaps = [{"t": T("17:30").isoformat(), "lights_on": {"light.sink": 20}},
                 {"t": T("20:30").isoformat(), "lights_on": {"light.sink": 8}}]
        r = ae.analyze_calls(calls=calls, switch_calls=[], changes=[], snapshots=snaps,
                             initial={}, events=[], window=WINDOW)
        self.assertEqual(r["call_digest"], [[T("20:00").isoformat(), "light.sink", 8]])
        self.assertEqual(r["snapshot_digest"], [[T("20:30").isoformat(), [["light.sink", 8]], []]])


class TargetAndValidityTests(unittest.TestCase):
    def test_target_entities(self):
        self.assertEqual(ae.target_entities({"entity_id": "input_text.a"}), ["input_text.a"])
        self.assertEqual(ae.target_entities({"entity_id": ["input_text.a", "input_text.b"]}),
                         ["input_text.a", "input_text.b"])
        self.assertEqual(ae.target_entities({"target": {"entity_id": ["input_text.a"]}, "value": "x"}), ["input_text.a"])
        self.assertEqual(ae.target_entities({"target": {"entity_id": "input_text.a"}}), ["input_text.a"])
        self.assertEqual(ae.target_entities({"value": "x"}), [])
        self.assertEqual(ae.target_entities({"target": {}}), [])

    def test_override_writes_counted_per_entity(self):
        """The S11 shape: one input_text.set_value per zone through target:,
        each 280 characters, refused by Home Assistant (never accepted)."""
        prefix = "input_text.living_lights_override_text_"
        rows = []
        for zone in ("sofa", "office", "sink", "island_left", "island_right"):
            data = {"target": {"entity_id": [prefix + zone]}, "value": "x" * 280}
            for entity in ae.target_entities(data):
                rows.append({"entity": entity, "length": len(data["value"])})
        overrides = [w for w in rows if str(w["entity"]).startswith(prefix)]
        self.assertEqual(len(overrides), 5)
        self.assertEqual(sum(1 for w in overrides if w["length"] > 255), 5)

    def test_validity_of_and_is_scorable(self):
        self.assertEqual(ae.validity_of(None), {"tv_measurable": True, "tv_source": "synthetic", "notes": []})
        doc = {"validity": {"tv_measurable": False, "tv_source": "ledger_flags", "notes": ["tv unmeasurable"]}}
        self.assertEqual(ae.validity_of(doc), {"tv_measurable": False, "tv_source": "ledger_flags",
                                               "notes": ["tv unmeasurable"]})
        self.assertEqual(ae.validity_of({})["tv_measurable"], False)      # missing block: not measurable
        self.assertTrue(ae.is_scorable({"validity": {"tv_measurable": True}}))
        self.assertFalse(ae.is_scorable({"validity": {"tv_measurable": False}}))
        self.assertFalse(ae.is_scorable({}))

    def test_reports_carry_validity(self):
        base = dict(calls=[], switch_calls=[], changes=[], snapshots=[], initial={}, events=[], window=WINDOW)
        r = ae.analyze_calls(**base)
        self.assertEqual(r["validity"], ae.SYNTHETIC_VALIDITY)
        self.assertTrue(r["scorable"] and ae.is_scorable(r))
        v = {"tv_measurable": False, "tv_source": "ledger_flags", "notes": []}
        r = ae.analyze_calls(**base, validity=v)
        self.assertEqual(r["validity"], v)
        self.assertFalse(r["scorable"] or ae.is_scorable(r))


if __name__ == "__main__":
    unittest.main()
