"""Story T transition tables at the stories.py constants (T1, T3, T5, T6, T8, flips).

Every scenario drives ``TvMachine`` with a scripted timeline ticked every
few seconds; the assertions read the journal, so a change to a constant in
``stories.py`` changes what these fixtures accept.
"""
import datetime as dt
import unittest

from lighting_publisher import stories
from lighting_publisher.beliefs import Beliefs
from lighting_publisher.tv_machine import (AWAY_HOLD, NAPPING, PROVISIONAL, TV_OFF, UNATTENDED,
                                           WATCHING, TvMachine)

TZ = dt.timezone.utc
DAY = dt.datetime(2026, 9, 17, tzinfo=TZ)


def at(hhmm: str, plus_s: int = 0) -> dt.datetime:
    hours, minutes, *rest = (int(x) for x in hhmm.split(":"))
    seconds = rest[0] if rest else 0
    return DAY + dt.timedelta(hours=hours, minutes=minutes, seconds=seconds + plus_s)


def belief(at_time: dt.datetime, request_id: str, attention=(0.1, 0.2, 0.7), rest=(0.9, 0.1, 0.0)) -> Beliefs:
    return Beliefs(at=at_time, request_id=request_id, p_attention=attention, p_eating=0.05,
                   p_food_prep=(1.0, 0.0, 0.0), p_settling=(1.0, 0.0, 0.0), p_rest=rest)


class Timeline:
    """Piecewise-constant inputs: ``tv``, ``room``, ``sofa``, ``track``, ``belief``."""

    def __init__(self):
        self.edges: list[tuple[dt.datetime, str, object]] = []

    def set(self, when: dt.datetime, key: str, value) -> "Timeline":
        self.edges.append((when, key, value))
        return self

    def occupy(self, start: dt.datetime, end: dt.datetime, sofa: bool = False, track: bool = False) -> "Timeline":
        self.set(start, "room", True).set(end, "room", False)
        if sofa:
            self.set(start, "sofa", True).set(end, "sofa", False)
        if track:
            self.set(start, "track", True).set(end, "track", False)
        return self

    def run(self, machine: TvMachine, start: dt.datetime, end: dt.datetime, step_s: int = 5,
            beliefs_fn=None) -> list[tuple[dt.datetime, object]]:
        state = {"tv": "off", "room": False, "sofa": False, "track": False, "belief": None}
        edges = sorted(self.edges, key=lambda e: e[0])
        decisions = []
        now = start
        while now <= end:
            while edges and edges[0][0] <= now:
                _, key, value = edges.pop(0)
                state[key] = value
            b = beliefs_fn(now) if beliefs_fn else state["belief"]
            decisions.append((now, machine.update(now, state["tv"], state["room"], state["sofa"], state["track"], b)))
            now += dt.timedelta(seconds=step_s)
        return decisions


def transitions(machine: TvMachine) -> list[tuple[str, str, str, str]]:
    return [(e["t"][11:19], e["from"], e["to"], e["reason"]) for e in machine.journal if e["event"] == "transition"]


def watching_between(decisions, a: dt.datetime, b: dt.datetime) -> set:
    return {d.tv_watching for t, d in decisions if a <= t <= b}


class T1WatchErrandReturn(unittest.TestCase):
    """TV on 20:00, sofa 20:05-21:00, sink 21:00-21:03, sofa 21:03-23:30, TV off 23:30."""

    def setUp(self):
        self.tl = (Timeline().set(at("20:00"), "tv", "on")
                   .occupy(at("20:05"), at("21:00"), sofa=True)
                   .occupy(at("21:03"), at("23:30"), sofa=True)
                   .set(at("23:30"), "tv", "off"))
        self.m = TvMachine()
        self.d = self.tl.run(self.m, at("19:55"), at("23:40"))

    def test_table(self):
        got = transitions(self.m)
        self.assertEqual(got, [
            ("20:05:00", TV_OFF, PROVISIONAL, "occupied"),
            ("20:05:00", PROVISIONAL, WATCHING, "sofa_stable"),
            ("21:00:00", WATCHING, AWAY_HOLD, "sofa_empty"),
            ("21:03:00", AWAY_HOLD, PROVISIONAL, "return"),
            ("21:03:00", PROVISIONAL, WATCHING, "sofa_stable"),
            ("23:30:00", WATCHING, TV_OFF, "tv_off"),
        ])

    def test_tv_watching_holds_through_the_errand(self):
        self.assertEqual(watching_between(self.d, at("20:05"), at("23:29:55")), {True})
        self.assertEqual(watching_between(self.d, at("19:55"), at("20:04:55")), {False})
        self.assertEqual(watching_between(self.d, at("23:30"), at("23:40")), {False})

    def test_attributes_without_beliefs(self):
        last = self.d[-1][1]
        self.assertIsNone(last.p_attention)
        self.assertIsNone(last.request_id)
        self.assertEqual(last.as_attributes()["state_machine"], TV_OFF)
        self.assertEqual(last.as_attributes()["since"], "2026-09-17T23:30:00+00:00")
        during = [d for t, d in self.d if t == at("21:01")][0]
        self.assertEqual(during.as_attributes()["state_machine"], AWAY_HOLD)
        self.assertEqual(during.as_attributes()["since"], "2026-09-17T21:00:00+00:00")


class T3TvOnNobody(unittest.TestCase):
    def test_stays_off(self):
        tl = Timeline().set(at("20:00"), "tv", "on").set(at("22:00"), "tv", "off")
        m = TvMachine()
        d = tl.run(m, at("19:55"), at("22:05"), step_s=30)
        self.assertEqual(transitions(m), [])
        self.assertEqual({x.tv_watching for _, x in d}, {False})

    def test_zone_occupancy_without_sofa_is_provisional_then_unattended_after_hold(self):
        tl = (Timeline().set(at("20:00"), "tv", "on").occupy(at("20:10"), at("20:12")))
        m = TvMachine()
        d = tl.run(m, at("19:55"), at("21:00"), step_s=10)
        self.assertEqual(transitions(m), [
            ("20:10:00", TV_OFF, PROVISIONAL, "occupied"),
            ("20:12:00", PROVISIONAL, AWAY_HOLD, "room_empty"),
            ("20:42:00", AWAY_HOLD, UNATTENDED, "away_hold_expired"),
        ])
        self.assertEqual(watching_between(d, at("20:10"), at("20:41:50")), {True})
        self.assertEqual(watching_between(d, at("20:42"), at("21:00")), {False})


class T5NapNeedsABelief(unittest.TestCase):
    def _run(self, with_belief: bool):
        tl = Timeline().set(at("20:00"), "tv", "on").occupy(at("20:05"), at("23:30"), sofa=True)
        m = TvMachine()

        def beliefs(now):
            if not with_belief or now < at("20:05"):
                return None
            rest = (0.2, 0.1, 0.7) if at("22:30") <= now < at("23:00") else (0.9, 0.1, 0.0)
            return belief(now, f"req-{int(now.timestamp())}", rest=rest)
        d = tl.run(m, at("19:55"), at("23:35"), step_s=10, beliefs_fn=beliefs)
        return m, d

    def test_no_belief_never_naps(self):
        m, d = self._run(with_belief=False)
        self.assertNotIn(NAPPING, {e["to"] for e in m.journal})
        self.assertEqual(watching_between(d, at("20:05"), at("23:29:50")), {True})

    def test_belief_naps_after_five_minutes_and_returns(self):
        m, d = self._run(with_belief=True)
        got = transitions(m)
        self.assertIn(("22:35:00", WATCHING, NAPPING, "belief_rest"), got)
        self.assertIn(("23:00:00", NAPPING, PROVISIONAL, "belief_rest_ended"), got)
        self.assertIn(("23:00:00", PROVISIONAL, WATCHING, "sofa_stable"), got)
        self.assertEqual(watching_between(d, at("20:05"), at("23:29:50")), {True})
        nap = [x for t, x in d if t == at("22:40")][0]
        self.assertEqual(nap.state, NAPPING)
        self.assertAlmostEqual(nap.p_attention, 0.7)
        self.assertTrue(nap.request_id.startswith("req-"))

    def test_belief_watching_needs_two_commits_without_sofa(self):
        tl = Timeline().set(at("20:00"), "tv", "on").occupy(at("20:05"), at("21:00"), track=True)
        m = TvMachine()
        commits = {at("20:06"): "a", at("20:07"): "b"}
        last = {"b": None}

        def beliefs(now):
            if now in commits:
                last["b"] = belief(now, commits[now])
            return last["b"]
        tl.run(m, at("19:55"), at("20:10"), step_s=10, beliefs_fn=beliefs)
        self.assertEqual(transitions(m), [
            ("20:05:00", TV_OFF, PROVISIONAL, "occupied"),
            ("20:07:00", PROVISIONAL, WATCHING, "belief_attention_commits"),
        ])


class T6ErrandBecomesDeparture(unittest.TestCase):
    def test_hold_then_unattended(self):
        tl = (Timeline().set(at("20:00"), "tv", "on").occupy(at("20:05"), at("21:20"), sofa=True)
              .occupy(at("21:21"), at("21:23")).set(at("23:00"), "tv", "off"))
        m = TvMachine()
        d = tl.run(m, at("19:55"), at("23:05"), step_s=10)
        self.assertEqual(transitions(m), [
            ("20:05:00", TV_OFF, PROVISIONAL, "occupied"),
            ("20:05:00", PROVISIONAL, WATCHING, "sofa_stable"),
            ("21:20:00", WATCHING, AWAY_HOLD, "sofa_empty"),
            ("21:21:00", AWAY_HOLD, PROVISIONAL, "return"),
            ("21:23:00", PROVISIONAL, AWAY_HOLD, "room_empty"),
            ("21:53:00", AWAY_HOLD, UNATTENDED, "away_hold_expired"),
            ("23:00:00", UNATTENDED, TV_OFF, "tv_off"),
        ])
        self.assertEqual(watching_between(d, at("20:05"), at("21:52:50")), {True})
        self.assertEqual(watching_between(d, at("21:53"), at("23:05")), {False})

    def test_unattended_unreachable_while_track_seen(self):
        tl = (Timeline().set(at("20:00"), "tv", "on").occupy(at("20:05"), at("21:20"), sofa=True)
              .occupy(at("21:20"), at("23:00"), track=True))
        m = TvMachine()
        d = tl.run(m, at("19:55"), at("23:00"), step_s=30)
        self.assertNotIn(UNATTENDED, {e["to"] for e in m.journal})
        self.assertEqual(watching_between(d, at("20:05"), at("23:00")), {True})

    def test_unattended_from_belief_never_while_sofa(self):
        tl = Timeline().set(at("20:00"), "tv", "on").occupy(at("20:05"), at("21:00"), sofa=True)
        m = TvMachine()
        d = tl.run(m, at("19:55"), at("21:00"), step_s=10,
                   beliefs_fn=lambda now: belief(now, "x", attention=(0.95, 0.05, 0.0)))
        self.assertNotIn(UNATTENDED, {e["to"] for e in m.journal})
        self.assertEqual(watching_between(d, at("20:05"), at("21:00")), {True})

    def test_unattended_from_belief_in_provisional_after_five_minutes(self):
        tl = Timeline().set(at("20:00"), "tv", "on").occupy(at("20:05"), at("21:00"))
        m = TvMachine()
        d = tl.run(m, at("19:55"), at("20:20"), step_s=10,
                   beliefs_fn=lambda now: belief(now, "x", attention=(0.95, 0.05, 0.0)))
        self.assertIn(("20:10:00", PROVISIONAL, UNATTENDED, "belief_attention_zero"), transitions(m))
        self.assertEqual(watching_between(d, at("20:10"), at("20:20")), {False})


class T8UnavailableGrace(unittest.TestCase):
    def _run(self, blip_s: int):
        tl = (Timeline().set(at("20:00"), "tv", "on").occupy(at("20:05"), at("23:30"), sofa=True)
              .set(at("21:00"), "tv", "unavailable").set(at("21:00", blip_s), "tv", "on"))
        m = TvMachine()
        d = tl.run(m, at("19:55"), at("21:30"), step_s=5)
        return m, d

    def test_thirty_second_blip_keeps_watching(self):
        m, d = self._run(30)
        self.assertEqual([t for t in transitions(m) if t[0] >= "21:00:00"], [])
        self.assertEqual(watching_between(d, at("21:00"), at("21:30")), {True})

    def test_twelve_minute_blip_resets_after_grace_and_rearms(self):
        m, d = self._run(12 * 60)
        got = [t for t in transitions(m) if t[0] >= "21:00:00"]
        self.assertEqual(got, [
            ("21:10:00", WATCHING, TV_OFF, "tv_off"),
            ("21:12:00", TV_OFF, PROVISIONAL, "occupied"),
            ("21:12:00", PROVISIONAL, WATCHING, "sofa_stable"),
        ])
        self.assertEqual(watching_between(d, at("21:00"), at("21:09:55")), {True})
        self.assertEqual(watching_between(d, at("21:10"), at("21:11:55")), {False})
        self.assertEqual(watching_between(d, at("21:12"), at("21:30")), {True})
        self.assertEqual(stories.TV_UNAVAILABLE_GRACE_S, 600)

    def test_unavailable_while_off_stays_off(self):
        tl = Timeline().set(at("20:00"), "tv", "unavailable").occupy(at("20:05"), at("20:30"), sofa=True)
        m = TvMachine()
        d = tl.run(m, at("19:55"), at("20:30"), step_s=30)
        self.assertEqual(transitions(m), [])
        self.assertEqual({x.tv_watching for _, x in d}, {False})


class MinimumFlipInterval(unittest.TestCase):
    def test_no_flip_faster_than_eight_seconds(self):
        m = TvMachine()
        t0 = at("20:00")
        m.update(t0, "on", True, True, False)          # off -> on
        self.assertTrue(m.tv_watching)
        d = m.update(t0 + dt.timedelta(seconds=3), "off", True, True, False)
        self.assertTrue(d.tv_watching)
        self.assertEqual(d.state, WATCHING)
        self.assertEqual(m.journal[-1]["event"], "flip_deferred")
        d = m.update(t0 + dt.timedelta(seconds=stories.MIN_FLIP_INTERVAL_S), "off", True, True, False)
        self.assertFalse(d.tv_watching)
        self.assertEqual(d.state, TV_OFF)
        d = m.update(t0 + dt.timedelta(seconds=12), "on", True, True, False)
        self.assertFalse(d.tv_watching)
        d = m.update(t0 + dt.timedelta(seconds=16), "on", True, True, False)
        self.assertTrue(d.tv_watching)

    def test_state_changes_that_do_not_flip_are_immediate(self):
        m = TvMachine()
        t0 = at("20:00")
        m.update(t0, "on", True, False, False)
        d = m.update(t0 + dt.timedelta(seconds=1), "on", True, True, False)
        self.assertEqual(d.state, WATCHING)
        self.assertTrue(d.changed)


if __name__ == "__main__":
    unittest.main()
