"""Tests for rung 9's hold fixtures: each named option does what its register entry says.

That is a test of the code against decisions.json and ladder rung 11's V63 text. It is not an admission: the fixtures stay
not admitted as pass or fail until the owner decides `compensation`, and nothing here counts toward any gate.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check  # noqa: E402
import events  # noqa: E402
import hold_fixtures as fixtures  # noqa: E402
import incumbent  # noqa: E402

FL, FR, RL, RR = events.LIGHTS
LOOK = 200
HAVE_POLICY = incumbent.find_policy() is not None


def run(case, option, **kwargs):
    _, log = fixtures.run_case(case, option, **kwargs)
    return fixtures.decisions(log)


def last(case, option, **kwargs):
    return run(case, option, **kwargs)[-1]


def level(decision, bulb):
    s = decision["requested_scene"][bulb]
    return s["brightness"] if s["on"] else 0


def lit(decision, surface):
    return decision["predicted"]["surfaces"][surface]


def limited(decision):
    return [row["point_id"] for row in decision.get("hold_limited", [])]


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class DimAndBrightenTest(unittest.TestCase):
    """V6 and V7: A reads at the counter and changes the bulb that serves it most."""

    def test_v6_p0_free_light_re_serves_the_changers_own_region(self):
        d = last("V6", "P0")
        self.assertEqual(level(d, FL), 80)
        self.assertGreater(level(d, FR), LOOK)
        self.assertGreater(lit(d, "counter-1"), lit(last("V6", "P1-mode"), "counter-1"))
        self.assertEqual(limited(d), [])

    def test_v6_p1_free_light_may_not_rise_at_the_points_the_dim_moved(self):
        for option in ("P1-strict", "P1-mode"):
            d = last("V6", option)
            for bulb in (FR, RL, RR):
                self.assertLessEqual(level(d, bulb), LOOK, option)
            self.assertEqual(limited(d), ["counter-1"])
            self.assertEqual(d["inputs"]["hold_set"]["held"][0]["direction"], "dim")

    def test_v6_p2_is_partial(self):
        for option in ("P2-strict", "P2-mode"):
            middle = lit(last("V6", option), "counter-1")
            self.assertGreater(middle, lit(last("V6", "P1-mode"), "counter-1"), option)
            self.assertLess(middle, lit(last("V6", "P0"), "counter-1"), option)

    def test_v6_p3_binds_at_the_changers_own_points(self):
        d = last("V6", "P3-mode/P1")
        self.assertEqual((level(d, FR), level(d, RR)), (LOOK, LOOK))
        self.assertEqual(limited(d), ["counter-1"])
        self.assertEqual(d["inputs"]["hold_set"]["held"][0]["changer_track"], "A")

    def test_v6_p3_with_an_unknown_changer_takes_its_fallback(self):
        for fallback, same_as in (("P3-mode/P1", "P1-mode"), ("P3-mode/P0", "P0")):
            self.assertEqual(last("V6", fallback, changer=False)["requested_scene"],
                             last("V6", same_as)["requested_scene"], fallback)

    def test_v7_p0_free_light_offsets_the_brighten(self):
        d = last("V7", "P0")
        self.assertEqual(level(d, FL), 254)
        self.assertLess(level(d, FR), LOOK)

    def test_v7_p1_free_light_may_not_fall_at_the_points_the_brighten_moved(self):
        for option in ("P1-strict", "P1-mode"):
            d = last("V7", option)
            for bulb in (FR, RL, RR):
                self.assertGreaterEqual(level(d, bulb), LOOK, option)
            self.assertEqual(d["inputs"]["hold_set"]["held"][0]["direction"], "brighten")

    def test_v7_p2_is_partial_and_nothing_is_hold_limited_above_target(self):
        p0, p1, p2 = (lit(last("V7", option), "counter-1") for option in ("P0", "P1-mode", "P2-mode"))
        self.assertLess(p0, p2)
        self.assertLess(p2, p1)
        self.assertEqual(limited(last("V7", "P1-mode")), [])


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class BetweenSolveAndSendTest(unittest.TestCase):
    """V8: a hold lands inside the send latency. The held bulb's step is dropped under every option; another bulb's goes out
    only if a local checker may complete it, and then only as the compensation option lets it."""

    def old_plan(self, option, **kwargs):
        first, *_ = [d for d in run("V8", option, **kwargs) if d["trigger"] == "person_state change"]
        return first

    def test_the_held_bulbs_step_is_dropped_under_every_option(self):
        for option in fixtures.OPTIONS:
            for completion in ("not-allowed", "allowed"):
                entry = self.old_plan(option, local_completion=completion)["applied_scene"][FL]
                self.assertEqual((entry["status"], entry["steps"]), ("held", []), option)
                self.assertIn("between solve and send", entry["reason"])

    def test_not_allowed_every_other_step_waits_for_the_re_solve(self):
        for option in fixtures.OPTIONS:
            d = self.old_plan(option, local_completion="not-allowed")
            self.assertEqual(d["status"], "superseded", option)
            for bulb in (FR, RL, RR):
                self.assertEqual(d["applied_scene"][bulb]["status"], "superseded", option)
                self.assertEqual(d["applied_scene"][bulb]["steps"], [])

    def test_allowed_depends_on_the_option(self):
        p0 = self.old_plan("P0", local_completion="allowed")
        self.assertEqual(p0["status"], "partial")
        self.assertEqual({p0["applied_scene"][b]["status"] for b in (FR, RL, RR)}, {"completed_after_check"})
        p1 = self.old_plan("P1-mode", local_completion="allowed")  # raising free light moves against the dim
        self.assertEqual({p1["applied_scene"][b]["status"] for b in (FR, RL, RR)}, {"superseded"})
        p3 = self.old_plan("P3-mode/P1", local_completion="allowed")  # rear left never reaches A's counter
        self.assertEqual(p3["applied_scene"][RL]["status"], "completed_after_check")
        self.assertEqual(p3["applied_scene"][FR]["status"], "superseded")

    def test_the_decision_is_logged_when_the_outcome_is_known_with_its_solve_time(self):
        _, log = fixtures.run_case("V8", "P1-mode")
        old = next(e for e in log if e["kind"] == "decision" and e["source"] == "decide"
                   and e["data"]["trigger"] == "person_state change")
        self.assertEqual((old["data"]["ts"], old["t"]), (fixtures.T0 + 10, fixtures.T0 + 11))

    def test_the_re_solve_follows_with_the_bulb_pinned(self):
        d = last("V8", "P1-mode")
        self.assertEqual((d["status"], level(d, FL), d["applied_scene"][FL]["status"]), ("applied", 80, "held"))


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class SharedBulbTest(unittest.TestCase):
    """V62: A dims a bulb both reach; B, reading in the armchair, still needs its light."""

    def test_p0_re_serves_the_other_person(self):
        self.assertEqual(limited(last("V62", "P0")), [])
        self.assertGreater(lit(last("V62", "P0"), "armchair-book"), lit(last("V62", "P1-mode"), "armchair-book"))

    def test_p1_leaves_the_other_person_hold_limited(self):
        for option in ("P1-strict", "P1-mode"):
            self.assertEqual(limited(last("V62", option)), ["armchair-book"], option)

    def test_p3_re_serves_them_with_a_bulb_that_misses_the_changers_points(self):
        d = last("V62", "P3-mode/P1")
        self.assertGreater(level(d, RL), LOOK)
        self.assertGreater(lit(d, "armchair-book"), lit(last("V62", "P1-mode"), "armchair-book"))
        self.assertEqual(limited(d), [])
        # no free bulb that reaches the counter moved up
        self.assertLessEqual(level(d, FR), LOOK)
        self.assertLessEqual(level(d, RR), LOOK)


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class GameUnderAHoldTest(unittest.TestCase):
    """V63, from ladder rung 11's text. A game at +40 s, game-time vacancy off at +220 s, the return at +230 s, its end at
    +330 s. Under non-gaming strata the coordinator is silent throughout, as den_lighting owns game time in both arms."""
    ENTRY, OFF, BACK, EXIT = 40, 220, 230, 330

    def at(self, case, option, dt):
        return next(d for d in run(case, option) if d["ts"] == fixtures.T0 + dt)

    def scenes(self, case, option):
        return {round(d["ts"] - fixtures.T0): d for d in run(case, option)}

    def test_p0_and_the_mode_forms_make_den_lightings_moves(self):
        for case in ("V63-dim", "V63-brighten"):
            for option in ("P0", "P1-mode", "P2-mode", "P3-mode/P1"):
                by_time = self.scenes(case, option)
                self.assertEqual(level(by_time[self.ENTRY], FR), 38, (case, option))
                self.assertEqual(level(by_time[self.OFF], FR), 0, (case, option))
                self.assertEqual(level(by_time[self.BACK], FR), 38, (case, option))

    def test_p1_strict_after_a_dim_free_bulbs_stay_off_from_game_vacancy_until_the_end(self):
        by_time = self.scenes("V63-dim", "P1-strict")
        self.assertEqual(level(by_time[self.OFF], FR), 0)
        self.assertEqual(level(by_time[self.BACK], FR), 0)  # den_lighting turns it back on; the bound does not
        self.assertEqual(level(by_time[self.BACK], FL), 80)  # the held bulb returns to the person's level

    def test_p1_strict_under_a_brighten_no_free_bulb_reaching_the_footprint_turns_off(self):
        by_time = self.scenes("V63-brighten", "P1-strict")
        self.assertNotIn(self.ENTRY, by_time)  # nor does it follow the game's dim
        self.assertEqual(level(by_time[self.OFF], FL), 0)  # game-time vacancy turns the held bulb off
        self.assertEqual(level(by_time[self.OFF], FR), 180)

    def test_p2_strict_makes_those_moves_only_in_part(self):
        by_time = self.scenes("V63-brighten", "P2-strict")
        self.assertEqual(level(by_time[self.ENTRY], FR), 109)  # halfway from 180 to 38
        self.assertEqual(level(by_time[self.OFF], FR), 90)
        by_time = self.scenes("V63-dim", "P2-strict")
        self.assertEqual(level(by_time[self.BACK], FR), 19)  # halfway from off to 38

    def test_p3_strict_acts_as_p1_strict_only_where_free_bulbs_reach_the_changers_points(self):
        by_time = self.scenes("V63-dim", "P3-strict/P1")
        self.assertEqual(level(by_time[self.BACK], FR), 0)  # reaches A's counter
        self.assertEqual(level(by_time[self.BACK], RL), 38)  # does not

    def test_at_game_exit_every_option_matches_the_pre_game_snapshot(self):
        for case, held in (("V63-dim", 80), ("V63-brighten", 254)):
            for option in fixtures.OPTIONS:
                # A free bulb the bound kept at 180 is already there, so that option has no new decision at the exit.
                d = last(case, option)
                self.assertEqual([level(d, b) for b in events.LIGHTS], [held, 180, 180, 180], (case, option))

    def test_non_gaming_strata_stay_silent_through_the_game(self):
        out = run("V63-dim", "P0", gaming="non-gaming-stratified")
        times = [round(d["ts"] - fixtures.T0) for d in out]
        self.assertEqual(times, [1, 20])  # the game's end comes back to the scene already requested


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class OverlappingHoldsTest(unittest.TestCase):
    """V64: a dim, a brighten on another bulb, then a second touch on the first."""

    def test_both_stay_pinned_under_every_option(self):
        for option in fixtures.OPTIONS:
            out = run("V64", option)
            after_both = [d for d in out if d["ts"] >= fixtures.T0 + 40]
            self.assertTrue(after_both, option)
            for d in after_both:
                self.assertEqual({s["channel"] for s in d["skipped"]}, {FL, RR}, option)
                self.assertEqual(level(d, RR), 254, option)
                self.assertIn(level(d, FL), (80, 150), option)

    def test_each_hold_keeps_its_own_direction_and_a_second_touch_resets_it(self):
        out = run("V64", "P1-mode")
        held = {h["entity_id"]: h["direction"] for h in out[-2]["inputs"]["hold_set"]["held"]}
        self.assertEqual(held, {FL: "dim", RR: "brighten"})
        held = {h["entity_id"]: h["direction"] for h in out[-1]["inputs"]["hold_set"]["held"]}
        self.assertEqual(held, {FL: "brighten", RR: "brighten"})


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class NotAdmittedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_the_report_never_says_pass_or_fail(self):
        lines = fixtures.report_lines()
        self.assertTrue(lines[0].startswith("hold fixtures: not admitted as pass or fail"))
        self.assertEqual(lines[1], "synthetic: plumbing only, never a gate")
        for line in lines[2:]:
            self.assertNotRegex(line, r"\b(PASS|FAIL|pass|fail)\b")
        # every case under every named option, V8 again under local-completion allowed, and V63 under non-gaming strata
        self.assertEqual(len(lines) - 2, len(fixtures.CASES) * len(fixtures.OPTIONS) + len(fixtures.OPTIONS) + 1)

    def test_the_named_options_are_the_registers(self):
        self.assertEqual({spec["option"] for spec in fixtures.OPTIONS.values()}, {"P0", "P1", "P2", "P3"})
        self.assertEqual({spec.get("form") for spec in fixtures.OPTIONS.values()}, {None, "strict", "mode"})
        self.assertEqual({spec.get("p3_fallback") for spec in fixtures.OPTIONS.values()}, {None, "P0", "P1"})

    def test_a_fixture_never_moves_with_the_gate_file(self):
        header, _ = fixtures.run_case("V6", "P0")
        entry = next(p for p in header["policies"] if p["module"] == "coordinator")
        self.assertEqual(entry["config"]["gate"]["slots"], fixtures.SLOTS)
        self.assertEqual(entry["config"]["gate"]["open"], [])
        self.assertTrue(header["synthetic"])

    def test_every_case_replays_byte_for_byte_through_check(self):
        for case in fixtures.CASES:
            for option in ("P0", "P2-strict", "P3-mode/P1"):
                header, log = fixtures.run_case(case, option)
                path = self.tmp / ("%s-%s.jsonl" % (case, option.replace("/", "-")))
                events.write(path, header, log)
                self.assertTrue(check.check(path)["ok"], (case, option))

    def test_every_decision_carries_what_the_gate_asks_for(self):
        for case in fixtures.CASES:
            for option in fixtures.OPTIONS:
                for d in run(case, option):
                    self.assertEqual(set(d["applied_scene"]), set(d["requested_scene"]))
                    self.assertIn("recipients", d)
                    self.assertIn("t_exec", d["inputs"])
                    self.assertEqual(d["hard_conflicts"], [])  # the fixtures' model has no glare table
                    self.assertEqual("hold_limited" in d, option != "P0", (case, option))


if __name__ == "__main__":
    unittest.main()
