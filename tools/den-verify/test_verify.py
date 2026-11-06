"""Rung 10's tests. Synthetic only: no camera, Jetson, LA, Home Assistant or network.

    python -m unittest discover -s tools\\den-verify -p "test_*.py"
"""
import contextlib
import io
import json
import math
import random
import re
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import light_model
import method
import synthetic as syn
import verify as cli
from light_model import LightModel
from records import Clock, read_jsonl, schema_keys, schema_record, write_jsonl
from score import Run, within

HERE = Path(__file__).resolve().parent
B0, B1, B2, B3 = syn.HUE


def closed(overrides=None):
    return method.parse(method.closed_copy(method.lf_text(HERE / "VERIFY-V1.md"), overrides)).scored()


def score(decisions, frames, model, overrides=None, **kw):
    """A run as if an occupancy file was given, with nobody in it unless a test says: no file at all is test_rules.py's."""
    kw.setdefault("occupancy", [])
    run = Run(closed(overrides), LightModel(model), decisions, frames, clock=Clock(syn.UTC_OFFSET_S), **kw)
    return run.run()


def logged(model, decision, names):
    """predicted.surfaces as den-core logs it: the model's light for the decision's own read-backs, to 3 places."""
    own = {b: {"level": a["level"], "cct_k": a["cct_k"]} for b, a in decision["inputs"]["actuator_state"].items()}
    own.update({b: dict(a["readback"]) for b, a in decision["applied_scene"].items()})
    light = LightModel(model).predict(own)
    return {**{n: None if light[n] is None else round(light[n], 3) for n in names}, "unit": model["basis_unit"]}


def by_id(records, key, value):
    return next(r for r in records if r.get(key) == value)


def residual(states, ts, surface):
    s = next(s for s in states if s["ts"] == ts)
    return next(e for e in s["residual"] if e["surface_id"] == surface)


def point(decision, pid):
    return next(p for p in decision["verify"]["points"] if p["point_id"] == pid)


def send(t, **extra):
    return [{"kind": "send", "sent_at": t, **extra}]


class Quiet:
    def __enter__(self):
        self._out = contextlib.redirect_stdout(io.StringIO())
        self.buf = self._out.__enter__()
        return self

    def __exit__(self, *exc):
        return self._out.__exit__(*exc)


def write_case(folder, decisions, frames, model, corrections=(), overrides=None, method_text=None):
    folder = Path(folder)
    write_jsonl(folder / "decisions.jsonl", decisions)
    write_jsonl(folder / "light_states.jsonl", frames)
    write_jsonl(folder / "corrections.jsonl", corrections)
    (folder / "light-model.json").write_text(json.dumps(model), encoding="utf-8")
    text = method_text if method_text is not None else method.closed_copy(
        method.lf_text(HERE / "VERIFY-V1.md"), overrides)
    (folder / "VERIFY-V1.md").write_bytes(text.encode("utf-8"))
    return ["--method", str(folder / "VERIFY-V1.md"), "run",
            "--decisions", str(folder / "decisions.jsonl"),
            "--light-states", str(folder / "light_states.jsonl"),
            "--corrections", str(folder / "corrections.jsonl"),
            "--model", str(folder / "light-model.json"),
            "--utc-offset-h", "-7", "--out", str(folder / "out")]


class V11(unittest.TestCase):
    """Case V11 and the walkthrough decision, end to end through the command line."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d, f, c = syn.v11_evening()
        argv = write_case(cls.tmp.name, d, f, syn.v11_model(), c)
        with Quiet():
            cls.code = cli.main(argv)
        out = Path(cls.tmp.name) / "out"
        cls.decisions = read_jsonl(out / "decisions.verified.jsonl")
        cls.states = read_jsonl(out / "light_states.verified.jsonl")
        cls.report = json.loads((out / "report.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_v11_residual_against_the_actual_resulting_state(self):
        self.assertEqual(self.code, 0)
        r = residual(self.states, syn.T0 + 40.0, "counter-1")  # ls-190409
        self.assertAlmostEqual(r["predicted"], 368.0, places=6)
        self.assertAlmostEqual(r["residual"], 357.0 - 368.0, places=6)
        self.assertEqual(r["residual_pct"], -2.99)
        self.assertEqual(math.trunc(r["residual_pct"] * 10) / 10, -2.9)  # the ladder's figure
        self.assertEqual(r["against"], {"decision_id": "d-190330-f1f2", "hold_set_revision": 8})
        self.assertIn("held", r["flags"])
        self.assertNotIn("drift", r["flags"])
        self.assertNotIn("outside", r["flags"])

    def test_v11_frame_is_named_as_the_walkthrough_names_it(self):
        ls = next(s for s in self.states if s["ts"] == syn.T0 + 40.0)
        self.assertNotIn("id", ls)
        self.assertEqual(self.report["frames"]["total"], 4)
        self.assertEqual(by_id(self.decisions, "decision_id", "d-190330-f1f2")["verify"]["light_state"],
                         "ls-190333")

    def test_walkthrough_verify_block(self):
        v = self.decisions[0]["verify"]
        self.assertEqual(v["ts"], syn.T0 + 4.0)
        self.assertTrue(v["anchored"])
        self.assertEqual(v["unit"], "lx")
        self.assertEqual(point(self.decisions[0], "counter-1")["status"], "within")
        self.assertEqual(round(point(self.decisions[0], "counter-1")["residual"], 2), -0.08)
        self.assertEqual(point(self.decisions[0], "armchair-book")["status"], "within")
        self.assertEqual(v["not_verified"], ["eye-A", "eye-B"])
        self.assertEqual([s["track_id"] for s in v["spill"]], ["B"])
        self.assertEqual(v["spill"][0]["spill"], 0)
        inc = {i["point_id"]: i for i in v["increments"]}
        self.assertEqual(inc["counter-1"]["measured_change"], 445.0)
        self.assertAlmostEqual(inc["counter-1"]["predicted_change"], 485.0, places=3)
        self.assertTrue(v["within_tolerance"])
        self.assertIsNone(v["explanation"])
        self.assertEqual(v["tolerance"], 0.15)

    def test_mid_fade_frame_gets_no_residual(self):
        s = next(s for s in self.states if s["ts"] == syn.T0 + 2.0)
        self.assertEqual(s["residual"], [])
        self.assertEqual(s["residual_status"], "not a verify frame: mid_fade")
        first = next(s for s in self.states if s["ts"] == syn.T0)
        self.assertEqual(first["residual_status"], "no decision in force")

    def test_report(self):
        hr = self.report["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["verdict"]), (2, 0, "pass"))
        self.assertTrue(self.report["scored"])
        self.assertEqual(self.report["eyes"]["not_verified"], 2)
        self.assertEqual(self.report["unverifiable_coverage"], {"surfaces": 0.0, "all_targets": 0.5})
        self.assertEqual(self.report["method"]["open"], [])


class FieldNames(unittest.TestCase):
    """What the tool writes uses schemas.json's names, plus the declared extensions."""

    def test_verify_keys_match_the_contract(self):
        d, f, c = syn.v11_evening()
        decisions, states, _ = score(d, f, syn.v11_model(), corrections=c)
        v = decisions[0]["verify"]
        self.assertEqual(list(v), schema_keys("decision", "verify"))
        meaning = next(x["meaning"] for x in schema_record("decision")["fields"] if x["name"] == "verify")

        def item_keys(name):
            inner = re.search(re.escape(name) + r"\[\{(.*?)\}\]", meaning)[1]
            return [k.split(":")[0].strip() for k in inner.split(",") if not k.strip().startswith(("within", "outside", "unverifiable", "prior_only"))]

        self.assertEqual(list(v["points"][0]), item_keys("points"))
        self.assertEqual([k for k in v["spill"][0] if k != "uncertainty"], item_keys("spill"))
        self.assertEqual([k for k in v["increments"][0] if k != "uncertainty"], item_keys("increments"))
        r = residual(states, syn.T0 + 40.0, "counter-1")
        self.assertEqual(schema_keys("light_state", "residual")[0], "surface_id")
        self.assertEqual(list(r)[0], "surface_id")


class HeldAndSuperseded(unittest.TestCase):
    """A held or superseded channel is excluded from misses and never flagged as drift."""

    def setUp(self):
        self.model = syn.hue_model({"desk-1": {B0: 400}, "sofa-1": {B1: 400}, "wall-1": {B2: 400},
                                    "counter-1": {B0: 300}})
        t = syn.T0
        self.t = t
        # 2700 K basis is interpolated in mired: 400 at 2202 K, 440 at 6500 K
        self.k = k = LightModel(self.model).predict({B0: {"level": 1, "cct_k": 2700}})["desk-1"] / 400
        self.ref_values = {"desk-1": 400 * k * 0.2, "sofa-1": 400 * k * 0.3, "wall-1": 400 * k * 0.5,
                           "counter-1": 300 * k * 0.2}
        ref = syn.light_state(t, self.ref_values, anchored=False, ident="ls-ref")
        # front_left goes 0.2 -> 0.8; front_right is held at 0.3; rear_left was superseded at 0.5
        dec = syn.hue_decision("d-held", t + 1, {B0: 0.2, B1: 0.3, B2: 0.5}, {
            B0: {"level": 0.8, "steps": send(t + 1.1)},
            B1: {"status": "held", "level": 0.3},
            B2: {"status": "superseded", "level": 0.5}}, "ls-ref",
            targets={"A": {"surface_id": "desk-1", "lx": 300}, "B": {"surface_id": "sofa-1", "lx": 300}},
            hold_set={"revision": 4, "held": [{"entity_id": B1, "channels": [B1],
                                               "pinned": {"level_pct": 30, "cct_k": 2700, "source": "read-back"}}]})
        dec["predicted"]["surfaces"] = logged(self.model, dec, ("desk-1", "sofa-1", "wall-1", "counter-1"))
        after = syn.light_state(t + 5, {
            "desk-1": 400 * k * 0.8,           # within
            "sofa-1": 400 * k * 0.3 * 0.6,     # 40% low, but the held bulb gives all of it
            "wall-1": 400 * k * 0.5 * 1.4,     # 40% high, all from the superseded bulb
            "counter-1": 300 * k * 0.8 * 0.7}, anchored=False)  # 30% low from a free bulb
        self.decisions, self.states, self.report = score([dec], [ref, after], self.model)

    def test_excluded_from_misses(self):
        hr = self.report["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"]), (1, 1))  # desk within, counter missed
        self.assertEqual(self.report["excluded_held_or_superseded"], 2)
        d = self.decisions[0]
        self.assertEqual(point(d, "sofa-1")["status"], "outside")
        self.assertEqual(point(d, "wall-1")["status"], "outside")
        causes = {e["point_id"]: e["cause"] for e in d["verify"]["explanation"]}
        self.assertEqual(causes["sofa-1"], "held")
        self.assertEqual(causes["wall-1"], "held")
        self.assertFalse(d["verify"]["within_tolerance"])  # the free counter-1 miss

    def test_never_flagged_as_drift(self):
        inc = {i["point_id"]: i for i in self.decisions[0]["verify"]["increments"]}
        # the held point's increment also misses the same way: it would read as drift if free
        self.assertEqual(inc["wall-1"]["status"], "outside")
        for sid in ("sofa-1", "wall-1"):
            r = residual(self.states, self.t + 5, sid)
            self.assertIn("held", r["flags"])
            self.assertNotIn("drift", r["flags"])
        self.assertEqual(self.report["misses_explained"]["drift"] + self.report["misses_explained"]["unexplained"], 1)

    def test_a_later_hold_pins_the_prediction(self):
        t, k = self.t, self.k
        later = syn.light_state(t + 30, {"desk-1": 400 * k * 0.4, "sofa-1": 400 * k * 0.3,
                                         "wall-1": 400 * k * 0.5, "counter-1": 300 * k * 0.4}, anchored=False)
        correction = {"correction_id": "c-1", "ts": t + 20, "action": {"settle_s": 3},
                      "snapshot": {"light_state": ["ls-ref"]},
                      "hold": {"entity_id": B0, "channels": [B0], "since": t + 20, "hold_set_revision": 5,
                               "pinned": {"level_pct": 40, "cct_k": 2700, "source": "read-back"}, "ended": None}}
        dec = self.decisions[0]
        dec.pop("verify")
        ref = syn.light_state(t, self.ref_values, anchored=False, ident="ls-ref")
        _, states, _ = score([dec], [ref, later], self.model, corrections=[correction])
        r = residual(states, t + 30, "desk-1")
        self.assertAlmostEqual(r["residual"], 0.0, places=6)
        self.assertEqual(r["against"]["hold_set_revision"], 5)
        self.assertIn("held", r["flags"])


class HeldPointsCount(unittest.TestCase):
    """A hold neither lifts nor sinks a session: the channel leaves the error, the point still counts."""

    names = [f"p{i:02d}" for i in range(20)]
    K = 300.0
    # B0, B1, B2 go 0.2 -> 0.8 and B3 stays at 0.5: it gives 150 of 870 (17%) at every point
    on_target = K * 2.9

    def setUp(self):
        self.model = syn.hue_model({n: {b: self.K for b in syn.HUE} for n in self.names}, kelvins=(2700,))

    def case(self, did, t, held, values, level=0.5):
        before = {B0: 0.2, B1: 0.2, B2: 0.2, B3: level}
        ref = syn.light_state(t, {n: self.K * sum(before.values()) for n in self.names}, anchored=False, ident=f"ls-{did}")
        applied = {b: {"level": 0.8, "steps": send(t + 0.5)} for b in (B0, B1, B2)}
        applied[B3] = {"status": "held" if held else "sent", "level": level}
        hold_set = None
        if held:
            hold_set = {"revision": 4, "held": [{"entity_id": B3, "channels": [B3],
                                                 "pinned": {"level_pct": level * 100, "cct_k": 2700, "source": "read-back"}}]}
        dec = syn.hue_decision(did, t + 0.5, before, applied, f"ls-{did}", hold_set=hold_set,
                               targets={f"T{i}": {"surface_id": n} for i, n in enumerate(self.names)})
        return [dec], [ref, syn.light_state(t + 5, values, anchored=False, ident=f"ls-{did}-after")]

    def landing(self, n_within, low=0.6):
        return {n: self.on_target if i < n_within else self.on_target * low for i, n in enumerate(self.names)}

    def run_cases(self, cases, overrides=None, **kw):
        decisions = [d for c in cases for d in c[0]]
        frames = [f for c in cases for f in c[1]]
        return score(decisions, frames, self.model, overrides=overrides, **kw)

    def hit_rate(self, cases, overrides=None):
        hr = self.run_cases(cases, overrides)[2]["hit_rate"]
        return hr["hits"], hr["misses"], hr["value"], hr["verdict"]

    def test_a_failing_decision_under_a_hold_fails_like_the_same_one_with_no_hold(self):
        t = syn.T0
        for held in (False, True):
            cases = [self.case("d-1", t, held, self.landing(3))]
            decisions, _, report = self.run_cases(cases)
            hr = report["hit_rate"]
            self.assertEqual((hr["hits"], hr["misses"], hr["value"], hr["verdict"]), (3, 17, 0.15, "fail"), held)
            self.assertIs(decisions[0]["verify"]["within_tolerance"], False, held)
            self.assertEqual(report["excluded_held_or_superseded"], 0, held)

    def test_a_session_failing_on_its_free_decisions_does_not_pass_on_its_held_ones(self):
        t = syn.T0
        cases = [self.case(f"d-{i}", t + 100 * i, i >= 2, self.landing(16)) for i in range(4)]
        self.assertEqual(self.hit_rate(cases), (64, 16, 0.8, "fail"))

    def stuck(self):
        """One free bulb never left 0.2: every point reads 21% low, which 15% + the held 17% would have forgiven."""
        return {n: self.K * (0.2 + 0.8 + 0.8 + 0.5) for n in self.names}

    def test_a_stuck_free_bulb_reads_the_same_with_or_without_a_hold(self):
        t = syn.T0
        for held in (False, True):
            cases = [self.case("d-free", t, False, self.landing(20)), self.case("d-2", t + 600, held, self.stuck())]
            self.assertEqual(self.hit_rate(cases), (20, 20, 0.5, "fail"), held)

    def test_many_held_decisions_with_a_stuck_free_bulb_do_not_vanish(self):
        t = syn.T0
        cases = [self.case("d-free", t, False, self.landing(20))]
        cases += [self.case(f"d-{i}", t + 600 * (i + 1), True, self.stuck()) for i in range(9)]
        self.assertEqual(self.hit_rate(cases), (20, 180, 0.1, "fail"))

    def test_soft_misses_under_a_hold_count(self):
        t = syn.T0
        cases = [self.case("d-free", t, False, self.landing(20))]
        cases += [self.case(f"d-{i}", t + 600 * (i + 1), True, self.landing(8, 0.75)) for i in range(4)]
        self.assertEqual(self.hit_rate(cases), (52, 48, 0.52, "fail"))

    def test_the_only_free_bulb_doing_nothing_beside_an_equal_hold_is_a_miss(self):
        # B3, held at the free bulb's level, gives half the light: 37.5% low was within 15% + 50%
        model = syn.hue_model({n: {B0: self.K, B3: self.K} for n in self.names}, kelvins=(2700,))
        targets = {f"T{i}": {"surface_id": n} for i, n in enumerate(self.names)}

        def scene(did, t, held, reading):
            ref = syn.light_state(t, {n: self.K for n in self.names}, anchored=False, ident=f"ls-{did}")
            applied = {B0: {"level": 0.8, "steps": send(t + 0.5)}, B3: {"status": "held" if held else "sent", "level": 0.8}}
            hold_set = {"revision": 4, "held": [{"entity_id": B3, "channels": [B3], "pinned": {"level_pct": 80, "cct_k": 2700}}]}
            dec = syn.hue_decision(did, t + 0.5, {B0: 0.2, B3: 0.8}, applied, f"ls-{did}", targets=targets,
                                   hold_set=hold_set if held else None)
            return [dec], [ref, syn.light_state(t + 5, {n: reading for n in self.names}, anchored=False, ident=f"ls-{did}-a")]

        a, b = scene("d-free", syn.T0, False, self.K * 1.6), scene("d-hold", syn.T0 + 600, True, self.K)
        hr = score(a[0] + b[0], a[1] + b[1], model)[2]["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["verdict"]), (20, 20, "fail"))

    def test_one_far_point_under_a_hold_costs_one_point(self):
        t = syn.T0
        for far in (0.3, 0.6):  # a shadow at one point of twenty
            values = {**self.landing(20), self.names[0]: self.on_target * far}
            for held in (False, True):
                decisions, _, report = self.run_cases([self.case("d-1", t, held, values)])
                hr = report["hit_rate"]
                self.assertEqual((hr["hits"], hr["misses"], hr["value"], hr["verdict"]), (19, 1, 0.95, "pass"), (far, held))
                self.assertIs(decisions[0]["verify"]["within_tolerance"], False)

    def test_a_shadow_under_a_hold_is_explained_as_a_shadow(self):
        t = syn.T0
        values = {**self.landing(20), self.names[0]: self.on_target * 0.6}
        occupancy = [{"start": t + 1, "end": t + 10, "people": [{"track_id": "A", "xy": [0.0, 0.0]}]}]
        decisions, _, report = self.run_cases([self.case("d-1", t, True, values)], occupancy=occupancy)
        self.assertEqual([e["cause"] for e in decisions[0]["verify"]["explanation"]], ["shadow"])
        self.assertEqual(report["hit_rate"]["hits"], 19)

    def test_sessions_of_held_decisions_read_what_their_points_read(self):
        t = syn.T0
        one_far = {**self.landing(20), self.names[0]: self.on_target * 0.6}
        cases = [self.case("d-free", t, False, self.landing(20))]
        cases += [self.case(f"d-{i}", t + 600 * (i + 1), True, one_far) for i in range(4)]
        self.assertEqual(self.hit_rate(cases), (96, 4, 0.96, "pass"))
        cases = [self.case("d-free", t, False, self.landing(17))]
        cases += [self.case(f"d-{i}", t + 600 * (i + 1), True, one_far) for i in range(3)]
        self.assertEqual(self.hit_rate(cases), (74, 6, 0.925, "pass"))

    def test_a_free_bulbs_fault_under_a_hold_is_a_miss_and_never_drift(self):
        t = syn.T0
        cases = [self.case("d-free", t, False, self.landing(20)), self.case("d-hold", t + 600, True, self.stuck())]
        decisions, states, report = self.run_cases(cases)
        causes = {e["cause"] for e in by_id(decisions, "decision_id", "d-hold")["verify"]["explanation"]}
        self.assertEqual(causes, {"unexplained"})
        r = residual(states, t + 605, "p00")
        self.assertIn("outside", r["flags"])
        self.assertIn("held", r["flags"])
        self.assertNotIn("drift", r["flags"])

    def test_the_share_decides_nothing_where_the_free_bulbs_show(self):
        t = syn.T0
        for share in (0, 0.01, 0.05, 0.15, 0.5):
            self.assertEqual(self.hit_rate([self.case("d-1", t, True, self.landing(3))], {"held_share_min": share}),
                             (3, 17, 0.15, "fail"), share)

    def test_a_share_closed_at_zero_leaves_out_nothing_no_held_light_reaches(self):
        t = syn.T0
        for rule in ("free_bulbs_count", "exclude_both"):
            self.assertEqual(self.hit_rate([self.case("d-1", t, False, self.landing(3))],
                                            {"held_share_min": 0, "held_rule": rule}), (3, 17, 0.15, "fail"), rule)

    def lit_mostly_by_the_hold(self, values, overrides=None, full=(100.0, 900.0)):
        """B0 goes 0.2 -> 0.8, 80 at its brighter; B1, held at full, gives 900 of 980 (92%)."""
        model = syn.hue_model({n: {B0: full[0], B1: full[1]} for n in values}, kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {n: full[0] * 0.2 + full[1] for n in values}, anchored=False, ident="ls-ref")
        dec = syn.hue_decision("d-held", t + 0.5, {B0: 0.2, B1: 1.0},
                               {B0: {"level": 0.8, "steps": send(t + 0.5)}, B1: {"status": "held", "level": 1.0}},
                               "ls-ref", targets={f"T{i}": {"surface_id": n} for i, n in enumerate(values)},
                               hold_set={"revision": 4, "held": [{"entity_id": B1, "channels": [B1],
                                                                  "pinned": {"level_pct": 100, "cct_k": 2700}}]})
        return [dec], [ref, syn.light_state(t + 5, values, anchored=False)], model

    def test_a_point_whose_free_bulbs_cannot_show_is_left_out_whatever_it_reads(self):
        d, f, model = self.lit_mostly_by_the_hold({"a": 980.0, "b": 980.0 * 0.7, "c": 980.0 * 0.1, "d": 980.0 * 2})
        decisions, _, report = score(d, f, model)
        hr = report["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["value"], hr["verdict"]), (0, 0, None, "insufficient"))
        self.assertEqual(report["excluded_held_or_superseded"], 4)
        self.assertEqual(report["excluded_held_by_status"], {"within": 1, "outside": 3})
        v = decisions[0]["verify"]
        self.assertEqual([p["status"] for p in v["points"]], ["within", "outside", "outside", "outside"])  # still written
        self.assertEqual({e["cause"] for e in v["explanation"]}, {"held"})
        self.assertIsNone(v["within_tolerance"])

    def test_a_point_lit_mostly_by_a_hold_counts_where_the_free_bulb_shows(self):
        # B0's 160 is past the 15% of 360 here, though B1 gives 56% of the light
        for reading, status in ((360.0, "within"), (360.0 * 0.7, "outside"), (360.0 * 1.3, "outside")):
            d, f, model = self.lit_mostly_by_the_hold({"a": reading}, full=(200.0, 200.0))
            decisions, _, report = score(d, f, model)
            self.assertEqual(point(decisions[0], "a")["status"], status, reading)
            hr = report["hit_rate"]
            self.assertEqual(hr["hits"] + hr["misses"], 1, reading)
            self.assertEqual(report["excluded_held_or_superseded"], 0, reading)

    def hand_off(self, model_b3=300.0, truth_b3=390.0, own=False):
        """A person turned B3 off and it is held off; the model is 30% low on B3. B0 is untouched unless own."""
        pts = ["a", "b"]
        model = syn.hue_model({n: {B0: 300.0, B3: model_b3} for n in pts}, kelvins=(2700,))
        t = syn.T0
        b0 = 0.2 if own else 0.8
        ref = syn.light_state(t, {n: 300 * b0 + truth_b3 * 0.8 for n in pts}, anchored=False, ident="ls-3")
        dec = syn.hue_decision("d-3", t + 0.5, {B0: b0, B3: 0.8},
                               {B0: {"level": 0.8, "steps": send(t + 0.5)}, B3: {"status": "held", "level": 0.0}},
                               "ls-3", targets={f"T{i}": {"surface_id": n} for i, n in enumerate(pts)},
                               hold_set={"revision": 4, "held": [{"entity_id": B3, "channels": [B3],
                                                                  "pinned": {"level_pct": 0, "cct_k": 2700}}]})
        return score([dec], [ref, syn.light_state(t + 5, {n: 300 * 0.8 for n in pts}, anchored=False)], model)

    def steady_beside_a_hold(self, held, reading, b3=150.0, level_after=0.5):
        """This decision only nudges B1. B0, free and unchanged, gives 300 of 500 at the point; B3 gives 150."""
        model = syn.hue_model({"pt": {B0: 300.0, B1: 100.0, B3: b3}}, kelvins=(2700,))
        t = syn.T0
        before = {B0: 1.0, B1: 0.5, B3: 0.5}
        ref = syn.light_state(t, {"pt": 300 + 50 + b3 * 0.5}, anchored=False, ident="ls-r")
        applied = {B1: {"level": 0.6, "steps": send(t + 0.5)},
                   B3: {"status": "held" if held else "sent", "level": level_after}}
        hold_set = {"revision": 4, "held": [{"entity_id": B3, "channels": [B3],
                                             "pinned": {"level_pct": level_after * 100, "cct_k": 2700}}]}
        dec = syn.hue_decision("d", t + 0.5, before, applied, "ls-r", targets={"A": {"surface_id": "pt"}},
                               hold_set=hold_set if held else None)
        return score([dec], [ref, syn.light_state(t + 5, {"pt": reading}, anchored=False)], model)

    def test_a_free_bulb_that_was_not_asked_to_move_still_answers_beside_a_hold(self):
        # B0 died: 300 of 435 is gone. This decision barely moved anything here, but B0 is not anyone else's
        for reading, status in ((435.0, "within"), (135.0, "outside")):
            for held in (False, True):
                decisions, _, report = self.steady_beside_a_hold(held, reading)
                self.assertEqual(point(decisions[0], "pt")["status"], status, (reading, held))
                hr = report["hit_rate"]
                self.assertEqual(hr["hits"] + hr["misses"], 1, (reading, held))
                self.assertEqual(report["excluded_held_or_superseded"], 0, (reading, held))

    def test_a_free_bulb_stuck_on_after_an_off_beside_a_steady_hold_is_a_miss(self):
        # the free bulb's light now is nothing, but what it answers for is the level it was told to leave
        model = syn.hue_model({n: {B0: self.K, B3: self.K} for n in self.names}, kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {n: self.K * 1.3 for n in self.names}, anchored=False, ident="ls-r")
        dec = syn.hue_decision("d", t + 0.5, {B0: 0.8, B3: 0.5},
                               {B0: {"level": 0.0, "steps": send(t + 0.5)}, B3: {"status": "held", "level": 0.5}}, "ls-r",
                               targets={f"T{i}": {"surface_id": n} for i, n in enumerate(self.names)},
                               hold_set={"revision": 4, "held": [{"entity_id": B3, "channels": [B3],
                                                                  "pinned": {"level_pct": 50, "cct_k": 2700}}]})
        for reading, expected in ((self.K * 1.3, (0, 20, "fail")), (self.K * 0.5, (20, 0, "pass"))):
            after = syn.light_state(t + 5, {n: reading for n in self.names}, anchored=False)
            report = score([dec], [ref, after], model)[2]
            hr = report["hit_rate"]
            self.assertEqual((hr["hits"], hr["misses"], hr["verdict"]), expected, reading)
            self.assertEqual(report["excluded_held_or_superseded"], 0, reading)

    def vacancy_off(self, pin, reading, at=syn.T0, did="d"):
        """A game's vacancy turns all four off and keeps B3's hold: free bulbs at 38/254, every read-back 0."""
        free = 38 / 254
        before = {B0: free, B1: free, B2: free, B3: pin / 100}
        ref = syn.light_state(at, {n: self.K * sum(before.values()) for n in self.names}, anchored=False, ident=f"ls-{did}")
        dec = syn.hue_decision(did, at + 0.5, before, {b: {"level": 0.0, "steps": send(at + 0.5)} for b in syn.HUE},
                               f"ls-{did}", targets={f"T{i}": {"surface_id": n} for i, n in enumerate(self.names)},
                               hold_set={"revision": 4, "held": [{"entity_id": B3, "channels": [B3],
                                                                  "pinned": {"level_pct": pin, "cct_k": 2700}}]})
        return [dec], [ref, syn.light_state(at + 5, {n: reading for n in self.names}, anchored=False, ident=f"ls-{did}-a")]

    def test_a_vacancy_off_that_landed_is_a_hit_though_the_copy_still_holds_the_bulb(self):
        # the read-back says off, and it comes after the copy: pinning B3 back at its hold read 0 of 20 here
        for pin in (5, 10, 20, 50):
            decisions, _, report = self.run_cases([self.vacancy_off(pin, 0.0)])
            hr = report["hit_rate"]
            self.assertEqual((hr["hits"], hr["misses"], hr["verdict"]), (20, 0, "pass"), pin)
            self.assertEqual(report["excluded_held_or_superseded"], 0, pin)
            self.assertIs(decisions[0]["verify"]["within_tolerance"], True, pin)
            self.assertIsNone(decisions[0]["verify"]["explanation"], pin)

    def test_a_held_bulb_left_on_after_a_vacancy_off_is_a_miss(self):
        # pinned back at its hold, this reading was a hit: what lands wrong has to read wrong
        for pin in (10, 20, 50):
            decisions, _, report = self.run_cases([self.vacancy_off(pin, self.K * pin / 100)])
            hr = report["hit_rate"]
            self.assertEqual((hr["hits"], hr["misses"], hr["verdict"]), (0, 20, "fail"), pin)
            self.assertEqual(report["excluded_held_or_superseded"], 0, pin)
            self.assertEqual({e["cause"] for e in decisions[0]["verify"]["explanation"]}, {"unexplained"}, pin)

    def test_a_free_bulb_left_on_after_a_vacancy_off_is_a_miss(self):
        for pin in (5, 10, 20, 50):
            hr = self.run_cases([self.vacancy_off(pin, self.K * 38 / 254)])[2]["hit_rate"]
            self.assertEqual((hr["hits"], hr["misses"], hr["verdict"]), (0, 20, "fail"), pin)

    def test_a_vacancy_off_costs_a_session_of_landed_scenes_nothing(self):
        t = syn.T0
        cases = [self.case(f"d-{i}", t + 100 * i, False, self.landing(20)) for i in range(3)]
        cases.append(self.vacancy_off(50, 0.0, at=t + 400, did="d-off"))
        self.assertEqual(self.hit_rate(cases), (80, 0, 1.0, "pass"))

    def test_the_copy_still_marks_a_bulb_it_no_longer_pins(self):
        # the other named rule leaves out every point a held bulb lights, whatever this decision read back
        for pin, reading in ((50, 0.0), (50, 150.0)):
            hr = self.run_cases([self.vacancy_off(pin, reading)], {"held_rule": "exclude_both"})[2]
            self.assertEqual((hr["hit_rate"]["hits"], hr["hit_rate"]["misses"]), (0, 0), reading)
            self.assertEqual(hr["excluded_held_or_superseded"], 20, reading)
        r = residual(self.run_cases([self.vacancy_off(50, 0.0)])[1], syn.T0 + 5, "p00")
        self.assertEqual((r["predicted"], r["residual"], r["residual_pct"]), (0.0, 0.0, None))
        self.assertEqual(r["against"]["hold_set_revision"], 4)
        self.assertIn("held", r["flags"])

    def test_a_held_bulb_nudged_inside_the_bound_counts_like_a_steady_one(self):
        decisions, _, report = self.steady_beside_a_hold(True, 135.0, level_after=0.52)
        self.assertEqual(point(decisions[0], "pt")["status"], "outside")
        self.assertEqual(report["hit_rate"]["misses"], 1)

    def test_the_share_is_compared_inclusively(self):
        # B1, held at full, gives exactly 25%; B0 is too little to show there
        model = syn.hue_model({"pt": {B0: 30.0, B1: 100.0, B2: 270.0}}, kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {"pt": 400.0}, anchored=False, ident="ls-r")
        dec = syn.hue_decision("d", t + 0.5, {B0: 1.0, B1: 1.0, B2: 1.0},
                               {B0: {"level": 1.0, "steps": send(t + 0.5)}, B1: {"status": "held", "level": 1.0},
                                B2: {"status": "superseded", "level": 1.0}}, "ls-r",
                               targets={"A": {"surface_id": "pt"}})
        after = syn.light_state(t + 5, {"pt": 400.0}, anchored=False)
        left = {0.925: 1, 0.93: 0}  # 370 of 400 is held or superseded
        for share, out in left.items():
            report = score([dec], [ref, after], model, overrides={"held_share_min": share})[2]
            self.assertEqual(report["excluded_held_or_superseded"], out, share)

    def test_a_hold_on_a_room_going_dark_does_not_divide_by_nothing(self):
        model = syn.hue_model({"pt": {B0: 300.0, B3: 300.0}}, kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {"pt": 480.0}, anchored=False, ident="ls-r")
        dec = syn.hue_decision("d", t + 0.5, {B0: 0.8, B3: 0.8},
                               {B0: {"level": 0.0, "steps": send(t + 0.5)}, B3: {"status": "held", "level": 0.0}}, "ls-r",
                               targets={"A": {"surface_id": "pt"}},
                               hold_set={"revision": 4, "held": [{"entity_id": B3, "channels": [B3],
                                                                  "pinned": {"level_pct": 0, "cct_k": 2700}}]})
        for reading, status in ((0.5, "within"), (240.0, "outside")):
            decisions, _, report = score([dec], [ref, syn.light_state(t + 5, {"pt": reading}, anchored=False)], model)
            self.assertEqual(point(decisions[0], "pt")["status"], status, reading)
            self.assertEqual(report["hit_rate"]["hits"] + report["hit_rate"]["misses"], 1, reading)

    def test_a_channel_held_off_is_still_held(self):
        # it gives no light after, but the whole error is its own; this decision changed nothing here
        decisions, states, report = self.hand_off()
        hr = report["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["verdict"]), (0, 0, "insufficient"))
        self.assertEqual(report["excluded_held_or_superseded"], 2)
        v = decisions[0]["verify"]
        self.assertEqual([e["cause"] for e in v["explanation"]], ["held", "held"])
        self.assertIsNone(v["within_tolerance"])
        r = residual(states, syn.T0 + 5, "a")
        self.assertIn("held", r["flags"])
        self.assertNotIn("drift", r["flags"])

    def test_a_held_channels_move_widens_the_bound_of_a_point_that_still_counts(self):
        # B0 went 0.2 -> 0.8 too: its change shows, and the error on B3's fall is inside 15% of what moved
        decisions, _, report = self.hand_off(own=True)
        self.assertEqual(point(decisions[0], "a")["status"], "within")
        self.assertEqual(point(decisions[0], "a")["level_bound"], 72.0)
        self.assertEqual(report["hit_rate"]["hits"], 2)
        self.assertEqual(report["excluded_held_or_superseded"], 0)

    def test_a_hold_from_a_correction_after_the_read_frame_counts_the_same_way(self):
        t = syn.T0
        before = {B0: 0.2, B1: 0.2, B2: 0.2, B3: 0.1}
        ref = syn.light_state(t, {n: self.K * sum(before.values()) for n in self.names}, anchored=False, ident="ls-d")
        dec = syn.hue_decision("d-c", t + 0.5, before, {b: {"level": 0.8, "steps": send(t + 0.5)} for b in (B0, B1, B2)},
                               "ls-d", targets={f"T{i}": {"surface_id": n} for i, n in enumerate(self.names)})
        correction = {"correction_id": "c-1", "ts": t + 2.0, "action": {"settle_s": 0.0}, "snapshot": {"light_state": ["ls-d"]},
                      "hold": {"entity_id": B3, "channels": [B3], "pinned": {"level_pct": 50, "cct_k": 2700},
                               "since": t + 2.0, "ended": None, "hold_set_revision": 5}}
        for values, expected in ((self.landing(20), (20, 0, 1.0, "pass")), (self.landing(3), (3, 17, 0.15, "fail"))):
            after = syn.light_state(t + 8, values, anchored=False)
            hr = score([dec], [ref, after], self.model, corrections=[correction])[2]["hit_rate"]
            self.assertEqual((hr["hits"], hr["misses"], hr["value"], hr["verdict"]), expected)

    def stuck_at_any_hue_level(self, level, pin, held, step=0, listed=True):
        """Three free bulbs go 0.8 -> 0.4 and B0 sticks; B3 stays at a Hue level, pinned at `pin` if held.

        Unlisted, the decision gives no read-back for B3, so the copy's pin is what stands.
        """
        pts = self.names[:2]
        model = syn.hue_model({n: {b: self.K for b in syn.HUE} for n in pts}, kelvins=(2700,))
        t = syn.T0
        before = {B0: 0.8, B1: 0.8, B2: 0.8, B3: level}
        ref = syn.light_state(t, {n: self.K * sum(before.values()) for n in pts}, anchored=False, ident="ls-r")
        applied = {b: {"level": 0.4, "steps": send(t + 0.5)} for b in (B0, B1, B2)}
        if listed:
            applied[B3] = {"status": "held" if held else "sent", "level": level + step}
        hold_set = {"revision": 4, "held": [{"entity_id": B3, "channels": [B3],
                                             "pinned": {"level_pct": pin, "cct_k": 2700}}]} if held else None
        dec = syn.hue_decision("d", t + 0.5, before, applied, "ls-r", hold_set=hold_set,
                               targets={f"T{i}": {"surface_id": n} for i, n in enumerate(pts)})
        after = syn.light_state(t + 5, {n: self.K * (0.8 + 0.4 + 0.4 + level) for n in pts}, anchored=False)
        hr = score([dec], [ref, after], model)[2]["hit_rate"]
        return hr["hits"], hr["misses"], hr["verdict"]

    def test_a_steady_hold_at_any_hue_level_lifts_nothing_through_the_allowance(self):
        # level_pct / 100 misses 39 of the 254 read-backs by a float's last bit; a whole-percent pin misses by up to 0.5%
        for n in range(1, 255):
            level = n / 254
            free = self.stuck_at_any_hue_level(level, None, False)
            self.assertEqual(free, (0, 2, "fail"), n)
            for listed in (True, False):
                self.assertEqual(self.stuck_at_any_hue_level(level, level * 100, True, listed=listed), free, n)
                self.assertEqual(self.stuck_at_any_hue_level(level, round(level * 100), True, listed=listed), free, n)

    def test_the_other_named_rule_leaves_every_held_lit_point_out(self):
        t = syn.T0
        both = {"held_rule": "exclude_both"}
        decisions, _, report = self.run_cases([self.case("d-1", t, True, self.landing(3))], both)
        hr = report["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["verdict"]), (0, 0, "insufficient"))
        self.assertEqual(report["excluded_held_or_superseded"], 20)
        self.assertEqual(report["excluded_held_by_status"], {"within": 3, "outside": 17})
        self.assertIsNone(decisions[0]["verify"]["within_tolerance"])
        cases = [self.case(f"d-{i}", t + 100 * i, i >= 2, self.landing(16)) for i in range(4)]
        self.assertEqual(self.hit_rate(cases, both), (32, 8, 0.8, "fail"))

    def test_what_the_other_rule_costs_is_what_the_note_says(self):
        t = syn.T0
        two_stuck = {n: self.K * (0.2 + 0.2 + 0.8 + 0.5) for n in self.names}  # 41% low
        cases = [self.case("d-free", t, False, self.landing(20)), self.case("d-hold", t + 600, True, two_stuck)]
        self.assertEqual(self.hit_rate(cases, {"held_rule": "exclude_both"}), (20, 0, 1.0, "pass"))
        self.assertEqual(self.hit_rate(cases), (20, 20, 0.5, "fail"))

    def test_the_rule_is_the_owners_and_only_a_named_one_is_read(self):
        text = method.lf_text(HERE / "VERIFY-V1.md")
        m = method.parse(text)
        self.assertEqual(m.open["held_rule"], "free_bulbs_count")
        self.assertEqual(m.open["held_share_min"], 0.05)
        self.assertIsNone(m.scored())
        with self.assertRaises(ValueError):
            method.parse(text.replace('proposed "free_bulbs_count"', 'proposed "hits_kept"'))
        with self.assertRaises(ValueError):
            method.parse(text.replace('proposed "free_bulbs_count"', 'proposed "far_misses_count"'))

    def test_the_owner_is_told_on_the_console(self):
        d, f, model = self.lit_mostly_by_the_hold({"a": 980.0, "b": 980.0 * 0.7})
        with tempfile.TemporaryDirectory() as tmp:
            argv = write_case(tmp, d, f, model)
            with Quiet() as q:
                self.assertEqual(cli.main(argv), 0)
        out = q.buf.getvalue()
        self.assertIn("gate 0.85: insufficient", out)
        self.assertIn("left out of the hit rate: 2 point(s) lit by held or superseded bulbs", out)
        out.encode("ascii")


class HoldRevision(unittest.TestCase):
    """A Correction pins only past the revision the decision copied: that copy already says what an older one became."""

    names = [f"p{i:02d}" for i in range(20)]
    K = 300.0
    t = syn.T0

    def setUp(self):
        self.model = syn.hue_model({n: {b: self.K for b in syn.HUE} for n in self.names}, kelvins=(2700,))

    def correction(self, cid, revision, pct, since):
        hold = {"entity_id": B3, "channels": [B3], "pinned": {"level_pct": pct, "cct_k": 2700},
                "since": since, "ended": None}
        if revision is not None:
            hold["hold_set_revision"] = revision
        return {"correction_id": cid, "ts": since, "action": {"settle_s": 0.0}, "snapshot": {"light_state": []}, "hold": hold}

    def landed(self, corrections, hold_set, b3=0.8):
        """B0 goes 0.2 -> 0.8, B3 reads b3 before and after, and everything lands where it should."""
        t = self.t
        before = {B0: 0.2, B1: 0.2, B2: 0.2, B3: b3}
        ref = syn.light_state(t, {n: self.K * sum(before.values()) for n in self.names}, anchored=False, ident="ls-r")
        applied = {B0: {"level": 0.8, "steps": send(t + 0.5)}}
        dec = syn.hue_decision("d", t + 0.5, before, applied, "ls-r", hold_set=hold_set,
                               targets={f"T{i}": {"surface_id": n} for i, n in enumerate(self.names)})
        after = syn.light_state(t + 8, {n: self.K * (0.8 + 0.2 + 0.2 + b3) for n in self.names}, anchored=False)
        decisions, states, report = score([dec], [ref, after], self.model, corrections=corrections)
        hr = report["hit_rate"]
        return (hr["hits"], hr["misses"], hr["verdict"]), residual(states, t + 8, "p00")

    def test_a_hold_older_than_the_decisions_copy_has_lapsed(self):
        # nothing writes `ended` after capture; the decision's own revision 9 lists nothing, so B3 is free again
        old = self.correction("c-8", 8, 50, self.t - 900)
        no_correction, _ = self.landed([], {"revision": 9, "held": []})
        self.assertEqual(no_correction, (20, 0, "pass"))
        result, r = self.landed([old], {"revision": 9, "held": []})
        self.assertEqual(result, no_correction)
        self.assertEqual(r["against"]["hold_set_revision"], 9)
        self.assertNotIn("held", r["flags"])

    def test_a_correction_at_the_decisions_own_revision_is_in_its_copy(self):
        result, r = self.landed([self.correction("c-9", 9, 50, self.t - 900)], {"revision": 9, "held": []})
        self.assertEqual(result, (20, 0, "pass"))
        self.assertNotIn("held", r["flags"])

    def test_the_decisions_own_pin_stands_over_an_older_correction(self):
        pin = {"revision": 9, "held": [{"entity_id": B3, "channels": [B3], "pinned": {"level_pct": 30, "cct_k": 2700}}]}
        result, r = self.landed([self.correction("c-8", 8, 90, self.t - 900)], pin, b3=0.3)
        self.assertEqual(result, (20, 0, "pass"))
        self.assertEqual(r["against"]["hold_set_revision"], 9)
        self.assertIn("held", r["flags"])

    def test_the_copy_pins_a_channel_the_decision_gives_no_read_back_for(self):
        # B3 is not in the applied scene and read 0.8 before: the copy's 30% is what the prediction takes
        pin = {"revision": 9, "held": [{"entity_id": B3, "channels": [B3], "pinned": {"level_pct": 30, "cct_k": 2700}}]}
        result, r = self.landed([], pin)
        self.assertEqual(result, (0, 20, "fail"))
        self.assertAlmostEqual(r["predicted"], self.K * (0.8 + 0.2 + 0.2 + 0.3), places=6)
        self.assertIn("held", r["flags"])

    def test_a_hold_past_the_copy_pins_over_this_decisions_read_back(self):
        # a person turned B3 back on after the vacancy off: the read-back is older than that
        t = self.t
        before = {B0: 0.2, B1: 0.2, B2: 0.2, B3: 0.5}
        ref = syn.light_state(t, {n: self.K * sum(before.values()) for n in self.names}, anchored=False, ident="ls-r")
        copy = {"revision": 9, "held": [{"entity_id": B3, "channels": [B3], "pinned": {"level_pct": 50, "cct_k": 2700}}]}
        dec = syn.hue_decision("d", t + 0.5, before, {b: {"level": 0.0, "steps": send(t + 0.5)} for b in syn.HUE}, "ls-r",
                               targets={f"T{i}": {"surface_id": n} for i, n in enumerate(self.names)}, hold_set=copy)
        after = syn.light_state(t + 8, {n: self.K * 0.5 for n in self.names}, anchored=False)
        decisions, states, report = score([dec], [ref, after], self.model,
                                          corrections=[self.correction("c-10", 10, 50, t + 5)])
        hr = report["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["verdict"]), (20, 0, "pass"))
        r = residual(states, t + 8, "p00")
        self.assertEqual(r["against"]["hold_set_revision"], 10)
        self.assertAlmostEqual(r["predicted"], self.K * 0.5, places=6)

    def test_later_holds_pin_in_revision_order_whatever_the_file_order(self):
        first = self.correction("c-9", 9, 20, self.t + 1)
        second = self.correction("c-10", 10, 80, self.t + 2)
        for corrections in ([first, second], [second, first]):
            result, r = self.landed(corrections, {"revision": 7, "held": []})
            self.assertEqual(result, (20, 0, "pass"))
            self.assertEqual(r["against"]["hold_set_revision"], 10)
            self.assertIn("held", r["flags"])

    def test_a_hold_past_the_decisions_revision_still_pins(self):
        result, r = self.landed([self.correction("c-10", 10, 20, self.t + 1)], {"revision": 9, "held": []})
        self.assertEqual(result, (0, 20, "fail"))  # pinned at 20% and read at 80%
        self.assertEqual(r["against"]["hold_set_revision"], 10)

    def test_the_revision_decides_where_both_sides_have_one_not_the_time(self):
        # logged just before the decision but past the copy it read: it pins
        result, _ = self.landed([self.correction("c-10", 10, 20, self.t - 1)], {"revision": 9, "held": []})
        self.assertEqual(result, (0, 20, "fail"))
        # dated after the decision but already inside its copy, which does not list it: it does not
        result, _ = self.landed([self.correction("c-9", 9, 20, self.t + 1)], {"revision": 9, "held": []})
        self.assertEqual(result, (20, 0, "pass"))

    def test_with_no_revision_to_compare_the_time_decides(self):
        for hold_set, correction_revision in (({"held": []}, 8), ({"revision": 9, "held": []}, None)):
            earlier, _ = self.landed([self.correction("c", correction_revision, 20, self.t - 900)], hold_set)
            self.assertEqual(earlier, (20, 0, "pass"))
            later, _ = self.landed([self.correction("c", correction_revision, 20, self.t + 1)], hold_set)
            self.assertEqual(later, (0, 20, "fail"))


class WhatCountsIgnoresTheReading(unittest.TestCase):
    """Whether a point counts comes from predictions alone, so no reading can leave a miss out or keep only hits."""

    READINGS = (0.0, 0.01, 0.1, 0.5, 0.85, 1.0, 1.15, 1.5, 2.0, 10.0)

    def scene(self, rng, held, anchored, ref_scale=1.0):
        """One decision on one point: random bulbs, levels and daylight, B3 held or free, read at `reading`."""
        basis = {b: rng.choice((0.0, 5.0, 40.0, 100.0, 300.0)) for b in syn.HUE}
        model = syn.hue_model({"pt": basis}, unit="lx" if anchored else "relative", kelvins=(2700,))
        before = {b: rng.choice((0.0, 0.1, 0.2, 0.5, 0.8, 1.0)) for b in syn.HUE}
        after = {b: rng.choice((0.0, 0.1, 0.2, 0.5, 0.8, 1.0)) for b in syn.HUE}
        after[B3] = before[B3] if rng.random() < 0.7 else after[B3]
        t = syn.T0
        daylight = rng.choice((0.0, 0.0, 20.0, 200.0))
        err = rng.choice((0.9, 1.0, 1.1))
        ref_value = daylight + err * sum(basis[b] * before[b] for b in syn.HUE)
        applied = {b: {"level": after[b], "steps": send(t + 0.5)} for b in (B0, B1, B2)}
        applied[B3] = {"status": "held" if held else "sent", "level": after[B3], "steps": [] if held else send(t + 0.5)}
        hold_set = None
        if held:
            hold_set = {"revision": 4, "held": [{"entity_id": B3, "channels": [B3],
                                                 "pinned": {"level_pct": after[B3] * 100, "cct_k": 2700}}]}
        dec = syn.hue_decision("d", t + 0.5, before, applied, "ls-r", targets={"A": {"surface_id": "pt"}}, hold_set=hold_set)
        ref = syn.light_state(t, {"pt": ref_value}, anchored=anchored, ident="ls-r")
        centre = max(1.0, sum(basis[b] * after[b] for b in syn.HUE) + daylight)
        return model, dec, ref, centre

    def outcomes(self, m, model, dec, ref, centre, anchored):
        seen = set()
        for factor in self.READINGS:
            after = syn.light_state(syn.T0 + 5, {"pt": centre * factor}, anchored=anchored, ident="ls-a")
            _, _, report = Run(m, LightModel(model), [dec], [ref, after], clock=Clock(syn.UTC_OFFSET_S)).run()
            hr = report["hit_rate"]
            seen.add((hr["hits"] + hr["misses"], report["excluded_held_or_superseded"], report["below_meter_noise"]["count"]))
        return seen

    def test_no_reading_changes_whether_a_point_counts(self):
        rng = random.Random(20261105)
        for overrides in ({}, {"background": "zero"}, {"held_rule": "exclude_both"}):
            m = closed(overrides)
            for i in range(120):
                held, anchored = rng.random() < 0.6, rng.random() < 0.5
                model, dec, ref, centre = self.scene(rng, held, anchored)
                seen = self.outcomes(m, model, dec, ref, centre, anchored)
                self.assertEqual(len(seen), 1, (overrides, i, seen))

    def test_a_hold_that_stayed_put_changes_no_hit_or_miss_where_the_point_still_counts(self):
        rng = random.Random(7)
        m = closed()
        compared = 0
        for i in range(200):
            state = rng.getstate()
            model, held_dec, ref, centre = self.scene(rng, True, False)
            rng.setstate(state)
            _, free_dec, _, _ = self.scene(rng, False, False)
            if held_dec["inputs"]["actuator_state"][B3]["level"] != held_dec["applied_scene"][B3]["readback"]["level"]:
                continue
            for factor in self.READINGS:
                after = syn.light_state(syn.T0 + 5, {"pt": centre * factor}, anchored=False, ident="ls-a")
                run = lambda d: Run(m, LightModel(model), [d], [ref, after], clock=Clock(syn.UTC_OFFSET_S)).run()[2]
                with_hold, without = run(held_dec), run(free_dec)
                self.assertEqual(with_hold["points"], without["points"], (i, factor))
                if not with_hold["excluded_held_or_superseded"]:
                    compared += 1
                    self.assertEqual(with_hold["hit_rate"]["hits"], without["hit_rate"]["hits"], (i, factor))
                    self.assertEqual(with_hold["hit_rate"]["misses"], without["hit_rate"]["misses"], (i, factor))
        self.assertGreater(compared, 300)


class MidFade(unittest.TestCase):
    """Frames mid-fade or mid-move are excluded; the settle rule follows den_lighting."""

    def setUp(self):
        self.model = syn.hue_model({"desk-1": {B0: 400}})
        self.t = t = syn.T0
        self.ref = syn.light_state(t, {"desk-1": 40}, anchored=False, ident="ls-ref")

    def frames(self, offsets, value):
        return [self.ref] + [syn.light_state(self.t + o, {"desk-1": value}, anchored=False) for o in offsets]

    def test_den_lighting_two_second_fade(self):
        t = self.t
        dec = syn.hue_decision("d-fade", t + 0.5, {B0: 0.1}, {B0: {"level": 0.5, "steps": send(t + 0.6)}}, "ls-ref",
                               targets={"A": {"surface_id": "desk-1", "lx": 300}})
        # sent at +0.6, 2 s fade, 1 s settle margin: busy until +3.6
        k = LightModel(self.model).predict({B0: {"level": 1, "cct_k": 2700}})["desk-1"]
        frames = self.frames([1.0, 3.0, 3.7], 40 + k * 0.4)
        decisions, states, report = score([dec], frames, self.model)
        self.assertEqual(decisions[0]["verify"]["ts"], t + 3.7)
        self.assertEqual(point(decisions[0], "desk-1")["status"], "within")
        for o in (1.0, 3.0):
            s = next(s for s in states if s["ts"] == t + o)
            self.assertEqual(s["residual_status"], "not a verify frame: mid_fade")
        self.assertEqual(report["frames"]["excluded"], {"mid_fade": 2})

    def test_restore_fades_five_seconds_and_probes_zero(self):
        t = self.t
        restore = syn.hue_decision("d-restore", t + 0.5, {B0: 0.1},
                                   {B0: {"level": 0.5, "steps": [{"kind": "restore", "sent_at": t + 1.0}]}}, "ls-ref")
        decisions, _, _ = score([restore], self.frames([5.0, 6.5, 7.2], 100), self.model)
        self.assertEqual(decisions[0]["verify"]["ts"], t + 7.2)  # 1.0 + 5 + 1
        probe = syn.hue_decision("d-probe", t + 0.5, {B0: 0.1}, {B0: {"level": 0.5, "steps": send(t + 1.0)}},
                                 "ls-ref", policy="den-light-probe-1")
        decisions, _, _ = score([probe], self.frames([1.5, 2.2], 100), self.model)
        self.assertEqual(decisions[0]["verify"]["ts"], t + 2.2)  # 1.0 + 0 + 1

    def test_step_with_unknown_fade_is_not_verified(self):
        t = self.t
        dec = syn.hue_decision("d-coord", t + 0.5, {B0: 0.1}, {B0: {"level": 0.5, "steps": send(t + 1.0)}},
                               "ls-ref", policy="coordinator@k16-v0")
        decisions, _, report = score([dec], self.frames([5.0], 100), self.model)
        self.assertIsNone(decisions[0]["verify"])
        self.assertIn("no known fade", decisions[0]["verify_status"])
        dec["applied_scene"][B0]["steps"][0]["transition_s"] = 0.4
        decisions, _, _ = score([dec], self.frames([5.0], 100), self.model)
        self.assertEqual(decisions[0]["verify"]["ts"], t + 5.0)

    def test_superseded_before_settling_and_sent_mid_transition(self):
        t = self.t
        first = syn.hue_decision("d-1", t + 0.5, {B0: 0.1}, {B0: {"level": 0.5, "steps": send(t + 1.0)}}, "ls-ref")
        second = syn.hue_decision("d-2", t + 2.0, {B0: 0.3}, {B0: {"level": 0.9, "steps": send(t + 2.0)}}, "ls-ref")
        decisions, _, report = score([first, second], self.frames([2.5, 6.0], 100), self.model)
        d1 = by_id(decisions, "decision_id", "d-1")
        self.assertIsNone(d1["verify"])
        self.assertEqual(d1["verify_status"], "superseded before it settled: d-2 sent a step first")
        self.assertEqual(by_id(decisions, "decision_id", "d-2")["verify"]["ts"], t + 6.0)
        # d-2 went out while d-1's fade was still landing: fixture-capabilities is pending
        self.assertEqual(report["decisions"]["not_admitted"],
                         [{"decision_id": "d-2", "pending": "fixture-capabilities",
                           "why": "a step went out while the same channel was still fading"}])
        self.assertEqual(report["hit_rate"]["hits"] + report["hit_rate"]["misses"], 0)


class StaleReadFrame(unittest.TestCase):
    """The read frame has to show the state the decision read: D takes up whatever is wrong with it."""

    names = [f"p{i}" for i in range(6)]
    t = syn.T0
    was = {B0: 0.3, B1: 0.5}
    now = {B0: 0.8, B1: 0.5}
    scenes = ({B0: 0.0, B1: 0.0}, {B0: 0.1, B1: 0.1}, {B0: 0.85})  # an off, a deep dim, a one-bulb nudge

    def setUp(self):
        self.model = syn.hue_model({n: {B0: 300.0, B1: 100.0} for n in self.names}, kelvins=(2700,))
        self.targets = {f"T{i}": {"surface_id": n} for i, n in enumerate(self.names)}

    def frame(self, at, levels, ident=None):
        return syn.light_state(at, {n: 300 * levels[B0] + 100 * levels[B1] for n in self.names}, anchored=False, ident=ident)

    def records(self, first_sent, read_at, read_levels, scene, landed=None):
        """d-1 sends B0 0.3 -> 0.8 at first_sent; d-2, at t + 4.7, shows that target and read the frame at read_at."""
        t = self.t
        frames = [self.frame(t, self.was, "ls-0")]
        if read_at != t:
            frames.append(self.frame(read_at, read_levels, "ls-read"))
        d1 = syn.hue_decision("d-1", first_sent, self.was, {B0: {"level": 0.8, "steps": send(first_sent)}}, "ls-0",
                              targets=self.targets)
        d2 = syn.hue_decision("d-2", t + 4.7, self.now, {b: {"level": lv, "steps": send(t + 4.7)} for b, lv in scene.items()},
                              "ls-0" if read_at == t else "ls-read", targets=self.targets)
        frames += [self.frame(t + 10, landed or {**self.now, **scene}), self.frame(t + 65, landed or {**self.now, **scene})]
        return [d1, d2], frames

    def second(self, *args, overrides=None, **kw):
        decisions, frames = self.records(*args, **kw)
        out = score(decisions, frames, self.model, overrides=overrides)
        return by_id(out[0], "decision_id", "d-2"), out[1], out[2]

    def assert_says_nothing(self, d, report):
        self.assertEqual({p["status"] for p in d["verify"]["points"]}, {"unverifiable"})
        self.assertIsNone(d["verify"]["within_tolerance"])
        self.assertIsNone(d["verify"]["explanation"])
        hr = report["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["verdict"]), (0, 0, "insufficient"))

    def test_a_perfect_landing_on_a_frame_older_than_the_last_landing_is_no_miss(self):
        for scene in self.scenes:
            d, _, report = self.second(self.t + 0.2, self.t, self.was, scene)
            self.assert_says_nothing(d, report)

    def test_a_perfect_landing_on_a_frame_inside_a_fade_is_no_miss(self):
        for scene in self.scenes:
            d, _, report = self.second(self.t - 1.0, self.t, {B0: 0.55, B1: 0.5}, scene)
            self.assert_says_nothing(d, report)

    def test_a_bulb_that_stayed_stuck_does_not_read_as_a_pass(self):
        # d-1's bulb never moved: the old frame matches it, and this read 6 of 6
        d, _, report = self.second(self.t + 0.2, self.t, self.was, {B0: 0.85}, landed=self.was)
        self.assert_says_nothing(d, report)

    def test_a_step_still_landing_when_the_decision_was_made_makes_the_frame_stale_too(self):
        d, _, report = self.second(self.t + 3.0, self.t, self.was, {B1: 0.6})
        self.assert_says_nothing(d, report)
        self.assertEqual(report["decisions"]["not_admitted"], [])  # another channel: the rule here is the only one

    def test_no_reading_changes_it(self):
        for landed in (self.was, self.now, {B0: 0.0, B1: 0.0}, {B0: 1.0, B1: 1.0}):
            d, _, report = self.second(self.t + 0.2, self.t, self.was, {B0: 0.1, B1: 0.1}, landed=landed)
            self.assert_says_nothing(d, report)

    def test_a_frame_taken_after_the_last_landing_counts_both_ways(self):
        # read at t + 3.5, past d-1's end at t + 3.2; d-2's step comes after d-1's decision, so d-1 counts as well
        for scene in self.scenes:
            d, _, report = self.second(self.t + 0.2, self.t + 3.5, self.now, scene)
            self.assertEqual({p["status"] for p in d["verify"]["points"]}, {"within"})
            hr = report["hit_rate"]
            self.assertEqual((hr["hits"], hr["misses"], hr["verdict"]), (12, 0, "pass"))
            self.assertEqual(report["decisions"]["read_frame_stale"], [])
        d, _, report = self.second(self.t + 0.2, self.t + 3.5, self.now, {B0: 0.85}, landed=self.was)
        self.assertEqual({p["status"] for p in d["verify"]["points"]}, {"outside"})

    def test_a_frame_taken_as_the_step_ends_counts(self):
        d, _, report = self.second(self.t + 0.2, self.t + 3.2, self.now, {B0: 0.85})
        self.assertEqual({p["status"] for p in d["verify"]["points"]}, {"within"})
        self.assertEqual(report["decisions"]["read_frame_stale"], [])

    def test_the_decisions_own_step_makes_a_frame_stale_only_if_the_frame_is_inside_it(self):
        t = self.t
        ref = self.frame(t, self.was, "ls-0")
        # logged a little before the decision's own time is still its own; the window's start is in it, as for a verify frame
        for at, sent, status in ((t + 0.5, t + 0.5, "within"), (t + 1.0, t + 0.5, "within"),
                                 (t + 0.5, t, "unverifiable"), (t + 0.5, t - 0.5, "unverifiable")):
            dec = syn.hue_decision("d", at, self.was, {B0: {"level": 0.8, "steps": send(sent)}}, "ls-0", targets=self.targets)
            decisions, _, _ = score([dec], [ref, self.frame(t + 5, self.now)], self.model)
            self.assertEqual({p["status"] for p in decisions[0]["verify"]["points"]}, {status}, (at, sent))

    def test_the_zero_background_takes_nothing_from_the_frame_and_still_judges_the_level(self):
        zero = {"background": "zero"}
        for scene in self.scenes:
            for first_sent, read_levels in ((self.t + 0.2, self.was), (self.t - 1.0, {B0: 0.55, B1: 0.5})):
                d, _, report = self.second(first_sent, self.t, read_levels, scene, overrides=zero)
                self.assertEqual({p["status"] for p in d["verify"]["points"]}, {"within"})
                self.assertEqual((report["hit_rate"]["hits"], report["hit_rate"]["misses"]), (6, 0))
                # the increment does read the frame: d-1's landing is in it and not in this decision's change
                self.assertEqual({i["status"] for i in d["verify"]["increments"]}, {"unverifiable"})
        d, _, report = self.second(self.t + 0.2, self.t, self.was, {B0: 0.85}, landed=self.was, overrides=zero)
        self.assertEqual({p["status"] for p in d["verify"]["points"]}, {"outside"})
        self.assertEqual((report["hit_rate"]["hits"], report["hit_rate"]["misses"]), (0, 6))

    def test_the_residual_says_why_it_has_nothing_while_that_decision_is_in_force(self):
        _, states, _ = self.second(self.t + 0.2, self.t, self.was, {B0: 0.85})
        for at in (self.t + 10, self.t + 65):
            s = next(s for s in states if s["ts"] == at)
            self.assertEqual(s["residual"], [])
            self.assertIn("d-2", s["residual_status"])
            self.assertIn("d-1", s["residual_status"])
        _, states, _ = self.second(self.t + 0.2, self.t, self.was, {B0: 0.85}, overrides={"background": "zero"})
        self.assertAlmostEqual(residual(states, self.t + 10, "p0")["residual"], 0.0, places=6)

    def test_the_owner_is_told(self):
        decisions, frames = self.records(self.t + 0.2, self.t, self.was, {B0: 0.85})
        with tempfile.TemporaryDirectory() as tmp:
            argv = write_case(tmp, decisions, frames, self.model)
            with Quiet() as q:
                self.assertEqual(cli.main(argv), 0)
            report = json.loads((Path(tmp) / "out" / "report.json").read_text(encoding="utf-8"))
        stale = report["decisions"]["read_frame_stale"]
        self.assertEqual([s["decision_id"] for s in stale], ["d-2"])
        self.assertIn("d-1", stale[0]["why"])
        out = q.buf.getvalue()
        self.assertIn("1 decision(s) read a frame from before a step landed", out)
        out.encode("ascii")


class DaylightAndDrift(unittest.TestCase):
    """Daylight is explained as daylight, never as drift; drift and shadow are told apart."""

    def setUp(self):
        self.model = syn.hue_model({"desk-1": {B0: 400}})
        self.k = LightModel(self.model).predict({B0: {"level": 1, "cct_k": 2700}})["desk-1"]

    def case(self, did, t, measured_after, windows=(0.5, 0.5), background=150.0, extra_frames=()):
        """A dim-to-bright change on front_left at t, read before at t and after at t + 4."""
        k = self.k
        ref = syn.light_state(t, {"desk-1": background + 0.2 * k}, anchored=False,
                              windows={"west-1": windows[0]}, ident=f"ls-{did}")
        dec = syn.hue_decision(did, t + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": send(t + 0.5)}},
                               f"ls-{did}", targets={"A": {"surface_id": "desk-1", "lx": 300}})
        after = syn.light_state(t + 4, {"desk-1": measured_after(background, k)}, anchored=False,
                                windows={"west-1": windows[1]})
        return [dec], [ref, after, *extra_frames]

    def test_steady_daylight_cancels_and_falling_daylight_is_daylight(self):
        t = syn.T0
        dusk = syn.light_state(t + 600, {"desk-1": 60 + 0.8 * self.k}, anchored=False, windows={"west-1": 0.2})
        d, f = self.case("d-sun", t, lambda bg, k: bg + 0.8 * k, extra_frames=[dusk])
        decisions, states, report = score(d, f, self.model)
        self.assertEqual(point(decisions[0], "desk-1")["status"], "within")
        self.assertEqual(decisions[0]["verify"]["increments"][0]["status"], "within")
        r = residual(states, t + 600, "desk-1")
        self.assertIn("outside", r["flags"])
        self.assertIn("daylight", r["flags"])
        self.assertNotIn("drift", r["flags"])

    def test_share_of_misses_explained(self):
        t = syn.T0
        cloud = self.case("d-cloud", t, lambda bg, k: 1.3 * (bg + 0.8 * k), windows=(0.2, 0.6))
        drift = self.case("d-drift", t + 100, lambda bg, k: bg + 0.2 * k + 0.6 * k * 0.6)
        shadow = self.case("d-shadow", t + 200, lambda bg, k: 0.5 * (bg + 0.8 * k))
        occupancy = [{"start": t + 200, "end": t + 210, "people": [{"track_id": "A", "xy": [0.2, 0.1]}]}]
        decisions, _, report = score(cloud[0] + drift[0] + shadow[0], cloud[1] + drift[1] + shadow[1],
                                     self.model, occupancy=occupancy)
        cause = {d["decision_id"]: d["verify"]["explanation"][0] for d in decisions}
        self.assertEqual(cause["d-cloud"]["cause"], "daylight")
        self.assertEqual(cause["d-drift"]["cause"], "drift")
        self.assertEqual(cause["d-shadow"]["cause"], "shadow")
        self.assertFalse(cause["d-shadow"]["admitted"])
        self.assertEqual(cause["d-shadow"]["pending"], "envelope-parameters")
        me = report["misses_explained"]
        self.assertEqual((me["daylight"], me["drift"], me["shadow"], me["unexplained"]), (1, 1, 1, 0))
        self.assertEqual(me["share_explained"], 1.0)
        self.assertEqual(me["share_explained_admitted"], round(2 / 3, 4))
        rows = {r["decision_id"]: r for r in report["rows"]}
        self.assertTrue(rows["d-shadow"]["occupied"])
        self.assertFalse(rows["d-drift"]["occupied"])


class DimsAndOffs(unittest.TestCase):
    """Under the read-frame background the prediction inherits the model's error on what changed."""

    names = [f"p{i}" for i in range(6)]
    # the model is 5% off, high at p1, p3, p5 and low at p0, p2, p4: every bulb lands as commanded
    err = {n: 1.05 if i % 2 == 0 else 0.95 for i, n in enumerate(names)}

    def setUp(self):
        self.model = syn.hue_model({n: {B0: 300.0, B1: 100.0} for n in self.names}, kelvins=(2700,))

    def truth(self, levels, floor=0.0):
        return {n: max(floor, self.err[n] * (300 * levels.get(B0, 0) + 100 * levels.get(B1, 0))) for n in self.names}

    def case(self, did, t, before, commanded, landed=None):
        ref = syn.light_state(t, self.truth(before), anchored=False, ident=f"ls-{did}")
        applied = {b: {"level": lv, "steps": send(t + 0.5)} for b, lv in commanded.items()}
        dec = syn.hue_decision(did, t + 0.5, before, applied, f"ls-{did}",
                               targets={f"T{i}": {"surface_id": n} for i, n in enumerate(self.names)})
        after = syn.light_state(t + 5, self.truth(landed if landed is not None else {**before, **commanded}, 0.5),
                                anchored=False)
        return [dec], [ref, after]

    def run_cases(self, cases, overrides=None):
        decisions = [d for c in cases for d in c[0]]
        frames = [f for c in cases for f in c[1]]
        return score(decisions, frames, self.model, overrides=overrides)

    def ons(self):
        return [self.case(f"d-on{i}", syn.T0 + 100 * i, {B0: 0.3, B1: 0.5}, {B0: 0.8}) for i in range(3)]

    def test_a_perfect_off_and_a_deep_dim_count_as_hits(self):
        t = syn.T0
        off = self.case("d-off", t + 400, {B0: 0.8, B1: 0.5}, {B0: 0.0, B1: 0.0})
        dim = self.case("d-dim", t + 500, {B0: 0.8, B1: 0.5}, {B0: 0.1, B1: 0.1})
        decisions, states, report = self.run_cases(self.ons() + [off, dim])
        hr = report["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["value"], hr["verdict"]), (30, 0, 1.0, "pass"))
        self.assertEqual(report["misses_explained"]["misses"], 0)
        for did in ("d-off", "d-dim"):
            v = by_id(decisions, "decision_id", did)["verify"]
            # the same whichever way the model is off; 15% of the 240 and 50 that changed
            self.assertEqual({p["status"] for p in v["points"]}, {"within"}, did)
            self.assertEqual({p["level_bound"] for p in v["points"]}, {43.5}, did)
            self.assertIs(v["within_tolerance"], True, did)
            self.assertIsNone(v["explanation"], did)
        self.assertEqual(report["points"]["unverifiable"], 0)
        # what those hits rest on is said, and what the gate would read without them
        self.assertEqual(report["hits_beyond_level_tolerance"],
                         {"count": 12, "value_if_missed": 0.6, "verdict_if_missed": "fail"})
        r = residual(states, t + 505, "p0")
        self.assertIn("wide_tolerance", r["flags"])
        self.assertNotIn("outside", r["flags"])

    def test_the_owner_is_told_on_the_console(self):
        t = syn.T0
        d, f = self.case("d-dim", t, {B0: 0.8, B1: 0.5}, {B0: 0.1, B1: 0.1})
        with tempfile.TemporaryDirectory() as tmp:
            argv = write_case(tmp, d, f, self.model)
            with Quiet() as q:
                self.assertEqual(cli.main(argv), 0)
        out = q.buf.getvalue()
        self.assertIn("gate 0.85: pass", out)
        self.assertIn("6 hit(s) are beyond 15% of the level; if they had missed, "
                      "the hit rate would be 0.0: fail", out)
        out.encode("ascii")

    def test_a_bulb_that_did_not_dim_is_still_a_miss_and_fails_the_gate(self):
        t = syn.T0
        stuck = {B0: 0.8, B1: 0.5}
        dim = self.case("d-dim", t + 500, stuck, {B0: 0.1, B1: 0.1}, landed=stuck)
        decisions, _, report = self.run_cases(self.ons() + [dim])
        self.assertEqual({p["status"] for p in by_id(decisions, "decision_id", "d-dim")["verify"]["points"]},
                         {"outside"})
        hr = report["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["value"], hr["verdict"]), (18, 6, 0.75, "fail"))

    def test_a_bulb_that_stayed_on_after_an_off_is_a_miss_whichever_way_the_model_is_off(self):
        t = syn.T0
        stuck = {B0: 0.8, B1: 0.5}
        off = self.case("d-off", t, stuck, {B0: 0.0, B1: 0.0}, landed=stuck)
        decisions, _, report = self.run_cases([off])
        d = by_id(decisions, "decision_id", "d-off")
        self.assertEqual([p["status"] for p in d["verify"]["points"]], ["outside"] * 6)
        self.assertEqual(report["hit_rate"]["verdict"], "fail")
        self.assertEqual(len(d["verify"]["explanation"]), 6)
        self.assertIsNone(point(d, "p1")["residual"])  # the model is high there, so the read frame predicts below zero

    def left_on(self, err, overrides=None):
        """B0 is told off and stays on, at err times the model, in the verify frame and a frame 55 s later."""
        pts = self.names[:4]
        model = syn.hue_model({n: {B0: 300.0} for n in pts}, kelvins=(2700,))
        t = syn.T0
        dec = syn.hue_decision("d", t + 0.5, {B0: 0.8}, {B0: {"level": 0.0, "steps": send(t + 0.5)}}, "ls-r",
                               targets={f"T{i}": {"surface_id": n} for i, n in enumerate(pts)})
        frames = [syn.light_state(t + o, {n: 240.0 * err for n in pts}, anchored=False, ident=f"ls-{o}") for o in (5, 60)]
        frames.insert(0, syn.light_state(t, {n: 240.0 * err for n in pts}, anchored=False, ident="ls-r"))
        return score([dec], frames, model, overrides=overrides)

    def test_a_bulb_left_on_after_an_off_is_in_the_residual_whichever_way_the_model_is_off(self):
        zero = {"background": "zero"}
        for err, overrides, predicted, pct in ((0.95, None, -12.0, None), (1.05, None, 12.0, 2000.0),
                                               (0.95, zero, 0.0, None), (1.05, zero, 0.0, None)):
            decisions, states, report = self.left_on(err, overrides)
            self.assertEqual({p["status"] for p in decisions[0]["verify"]["points"]}, {"outside"}, err)
            self.assertEqual(report["frames"]["residual_written"], 2, err)
            for at in (syn.T0 + 5, syn.T0 + 60):
                s = next(s for s in states if s["ts"] == at)
                self.assertNotIn("residual_status", s, err)
                self.assertEqual(len(s["residual"]), 4, (err, overrides, at))
                for e in s["residual"]:
                    self.assertEqual(e["predicted"], predicted, (err, overrides))
                    self.assertEqual(e["residual"], round(240.0 * err - predicted, 3), (err, overrides))
                    self.assertEqual(e["residual_pct"], pct, (err, overrides))
                    self.assertIn("outside", e["flags"], (err, overrides))
                    self.assertIn("drift", e["flags"], (err, overrides))

    def test_an_off_that_landed_is_in_the_residual_with_no_share_of_nothing(self):
        d, f, model = self.one_point(0.8, 0.0, 0.0)
        for overrides in (None, {"background": "zero"}):
            r = residual(score(d, f, model, overrides=overrides)[1], syn.T0 + 5, "desk-1")
            self.assertEqual((r["predicted"], r["residual"], r["residual_pct"]), (0.0, 0.0, None), overrides)
            self.assertIn("wide_tolerance", r["flags"], overrides)
            self.assertNotIn("outside", r["flags"], overrides)

    def test_a_dark_point_nothing_changed_at_is_in_the_residual_and_flagged_nothing(self):
        d, f, model = self.one_point(0.0, 0.0, 5.0)
        decisions, states, _ = score(d, f, model)
        self.assertEqual(point(decisions[0], "desk-1")["status"], "unverifiable")
        r = residual(states, syn.T0 + 5, "desk-1")
        self.assertEqual((r["predicted"], r["residual"], r["residual_pct"], r["flags"]), (0.0, 5.0, None, []))

    def test_a_surface_with_no_prediction_has_no_residual_entry(self):
        # missing from the read frame, so the read-frame background has nothing to predict it from
        model = syn.hue_model({"a": {B0: 300.0}, "b": {B0: 300.0}}, kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {"a": 60.0}, anchored=False, ident="ls-r")
        dec = syn.hue_decision("d", t + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": send(t + 0.5)}}, "ls-r",
                               targets={"A": {"surface_id": "a"}, "B": {"surface_id": "b"}})
        decisions, states, _ = score([dec], [ref, syn.light_state(t + 5, {"a": 240.0, "b": 240.0}, anchored=False)], model)
        self.assertEqual(point(decisions[0], "b")["status"], "unverifiable")
        self.assertEqual([e["surface_id"] for e in states[1]["residual"]], ["a"])

    def test_a_dim_that_landed_short_of_the_change_is_still_a_miss(self):
        t = syn.T0
        # commanded to 0.1 and 0.1, landed at 0.3 and 0.1: the change is about a fifth short
        dim = self.case("d-dim", t, {B0: 0.8, B1: 0.5}, {B0: 0.1, B1: 0.1}, landed={B0: 0.3, B1: 0.1})
        decisions, _, _ = self.run_cases([dim])
        self.assertEqual({p["status"] for p in decisions[0]["verify"]["points"]}, {"outside"})

    def one_point(self, before, after, measured, err_before=0.0):
        model = syn.hue_model({"desk-1": {B0: 400.0}}, kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {"desk-1": 400 * before * (1 + err_before)}, anchored=False, ident="ls-ref")
        dec = syn.hue_decision("d-1", t + 0.5, {B0: before}, {B0: {"level": after, "steps": send(t + 0.5)}}, "ls-ref",
                               targets={"A": {"surface_id": "desk-1"}})
        return [dec], [ref, syn.light_state(t + 5, {"desk-1": measured}, anchored=False)], model

    def test_the_models_error_at_the_brighter_state_is_not_a_miss(self):
        for after, err in ((0.37, 0.10), (0.4, 0.10), (0.36, 0.12), (0.37, -0.10), (0.1, 0.14)):
            d, f, model = self.one_point(0.8, after, 400 * after, err_before=err)
            decisions, _, report = score(d, f, model)
            self.assertEqual(point(decisions[0], "desk-1")["status"], "within", (after, err))
            self.assertEqual(point(decisions[0], "desk-1")["level_bound"], 48.0, (after, err))
            self.assertEqual(report["hit_rate"]["verdict"], "pass", (after, err))

    def test_such_dims_do_not_fail_a_session_whose_bulbs_all_landed(self):
        model = syn.hue_model({n: {B0: 400.0} for n in self.names}, kelvins=(2700,))
        targets = {f"T{i}": {"surface_id": n} for i, n in enumerate(self.names)}

        def scene(did, t, before, after, err_before):
            ref = syn.light_state(t, {n: 400 * before * (1 + err_before) for n in self.names}, anchored=False,
                                  ident=f"ls-{did}")
            dec = syn.hue_decision(did, t + 0.5, {B0: before}, {B0: {"level": after, "steps": send(t + 0.5)}},
                                   f"ls-{did}", targets=targets)
            return [dec], [ref, syn.light_state(t + 5, {n: 400 * after for n in self.names}, anchored=False)]

        t = syn.T0
        cases = [scene(f"d-on{i}", t + 100 * i, 0.3, 0.8, 0.0) for i in range(3)]
        cases += [scene(f"d-dim{i}", t + 500 + 100 * i, 0.8, 0.37, 0.10) for i in range(2)]
        decisions = [d for c in cases for d in c[0]]
        frames = [f for c in cases for f in c[1]]
        hr = score(decisions, frames, model)[2]["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["verdict"]), (30, 0, "pass"))

    def crossfade(self, err):
        """B0 1 to 0 and B1 0 to 1, both landed, the model off by err each way: no net change."""
        model = syn.hue_model({"mid": {B0: 300.0, B1: 300.0}}, kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {"mid": 300 * (1 + err)}, anchored=False, ident="ls-x")
        dec = syn.hue_decision("d-x", t + 0.5, {B0: 1.0, B1: 0.0},
                               {B0: {"level": 0.0, "steps": send(t + 0.5)}, B1: {"level": 1.0, "steps": send(t + 0.5)}},
                               "ls-x", targets={"A": {"surface_id": "mid"}})
        after = syn.light_state(t + 5, {"mid": 300 * (1 - err)}, anchored=False)
        return score([dec], [ref, after], model)

    def test_a_crossfade_whose_bulbs_landed_is_not_a_miss(self):
        for err in (0.08, 0.10, 0.12):
            decisions, _, report = self.crossfade(err)
            self.assertEqual(point(decisions[0], "mid")["status"], "within", err)
            self.assertEqual(report["hit_rate"]["verdict"], "pass", err)

    def test_a_model_beyond_the_allowance_is_still_a_miss(self):
        d, f, model = self.one_point(0.8, 0.1, 40.0, err_before=0.2)
        self.assertEqual(point(score(d, f, model)[0][0], "desk-1")["status"], "outside")
        self.assertEqual(point(self.crossfade(0.2)[0][0], "mid")["status"], "outside")

    def test_a_far_reading_at_a_deep_dim_does_not_cost_the_points_on_target(self):
        t = syn.T0

        def dim(did, tt, far):
            ref = syn.light_state(tt, {n: 300 * 0.8 + 100 * 0.5 for n in self.names}, anchored=False, ident=f"ls-{did}")
            dec = syn.hue_decision(did, tt + 0.5, {B0: 0.8, B1: 0.5},
                                   {B0: {"level": 0.1, "steps": send(tt + 0.5)}, B1: {"level": 0.1, "steps": send(tt + 0.5)}},
                                   f"ls-{did}", targets={f"T{i}": {"surface_id": n} for i, n in enumerate(self.names)})
            values = {n: 40.0 for n in self.names}
            values[self.names[0]] = far
            return [dec], [ref, syn.light_state(tt + 5, values, anchored=False)]

        hr = self.run_cases([dim("d-1", t, 145.0)])[2]["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["value"], hr["verdict"]), (5, 1, 0.8333, "fail"))
        on = self.case("d-on", t, {B0: 0.3, B1: 0.5}, {B0: 0.8})
        cases = [on, dim("d-1", t + 100, 145.0), dim("d-2", t + 200, 145.0)]
        hr = self.run_cases(cases)[2]["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["value"], hr["verdict"]), (16, 2, 0.8889, "pass"))

    def test_a_bright_up_keeps_the_ladders_tolerance_exactly(self):
        for before in (0.0, 0.3, 0.5):
            d, f, model = self.one_point(before, 0.8, 320 * 1.151)
            decisions, _, report = score(d, f, model)
            self.assertEqual(point(decisions[0], "desk-1")["status"], "outside", before)
            self.assertNotIn("level_bound", point(decisions[0], "desk-1"), before)
            d, f, model = self.one_point(before, 0.8, 320 * 1.149)
            decisions, _, report = score(d, f, model)
            self.assertEqual(point(decisions[0], "desk-1")["status"], "within", before)
            self.assertEqual(report["hits_beyond_level_tolerance"]["count"], 0, before)

    def test_an_unchanged_bulbs_light_widens_nothing(self):
        # its error is in both frames and cancels, so 15% of the level stands
        model = syn.hue_model({"desk-1": {B0: 100.0, B1: 300.0}}, kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {"desk-1": 400.0}, anchored=False, ident="ls-ref")
        dec = syn.hue_decision("d-1", t + 0.5, {B0: 1.0, B1: 1.0}, {B0: {"level": 0.1, "steps": send(t + 0.5)}}, "ls-ref",
                               targets={"A": {"surface_id": "desk-1"}})
        decisions, _, _ = score([dec], [ref, syn.light_state(t + 5, {"desk-1": 310 * 1.16}, anchored=False)], model)
        self.assertEqual(point(decisions[0], "desk-1")["status"], "outside")
        self.assertNotIn("level_bound", point(decisions[0], "desk-1"))

    def test_a_hit_past_the_allowance_stands_on_the_ladder_beside_a_steady_bulb(self):
        # B1 stays at 900 and B0 goes 50 to 60 of 960: the allowance and the noise reach about 40, the ladder 144
        model = syn.hue_model({"desk-1": {B0: 100.0, B1: 900.0}}, kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {"desk-1": 950.0}, anchored=False, ident="ls-ref")
        dec = syn.hue_decision("d-1", t + 0.5, {B0: 0.5, B1: 1.0}, {B0: {"level": 0.6, "steps": send(t + 0.5)}}, "ls-ref",
                               targets={"A": {"surface_id": "desk-1"}})
        for high, status in ((1.14, "within"), (0.86, "within"), (1.16, "outside"), (0.84, "outside")):
            after = syn.light_state(t + 5, {"desk-1": 960.0 * high}, anchored=False)
            decisions, _, report = score([dec], [ref, after], model)
            self.assertEqual(point(decisions[0], "desk-1")["status"], status, high)
            self.assertNotIn("level_bound", point(decisions[0], "desk-1"), high)
            self.assertEqual(report["hits_beyond_level_tolerance"]["count"], 0, high)

    def off_with_a_wiggle(self, wiggle, cct_after=2700, kelvins=(2700,), levels=(0.4, 0.8)):
        """B0 goes off and sticks on; B1 to B3 stay put, their read-backs `wiggle` away. Read where B0 stuck."""
        stuck, steady = levels
        pts = self.names[:2]
        model = syn.hue_model({n: {b: 300.0 for b in syn.HUE} for n in pts}, kelvins=kelvins)
        lm = LightModel(model)
        t = syn.T0
        before = {B0: stuck, B1: steady, B2: steady, B3: steady}
        lit = lm.predict({b: {"level": lv, "cct_k": 2700} for b, lv in before.items()})
        ref = syn.light_state(t, {n: lit[n] for n in pts}, anchored=False, ident="ls-r")
        applied = {B0: {"level": 0.0, "steps": send(t + 0.5)}}
        applied.update({b: {"level": steady + wiggle, "steps": []} for b in (B1, B2, B3)})
        dec = syn.hue_decision("d", t + 0.5, before, applied, "ls-r",
                               targets={f"T{i}": {"surface_id": n} for i, n in enumerate(pts)})
        for b in (B1, B2, B3):
            dec["applied_scene"][b]["readback"]["cct_k"] = cct_after
        after = syn.light_state(t + 5, {n: lit[n] for n in pts}, anchored=False)
        hr = score([dec], [ref, after], model)[2]["hit_rate"]
        return hr["hits"], hr["misses"], hr["verdict"]

    def test_a_read_back_step_on_a_bulb_that_stayed_put_widens_nothing(self):
        self.assertEqual(self.off_with_a_wiggle(0.0), (0, 2, "fail"))
        for steps in (1, -1, 2, -2):
            self.assertEqual(self.off_with_a_wiggle(steps / 254), (0, 2, "fail"), steps)

    def test_a_colour_nudge_on_a_bulb_that_stayed_put_widens_nothing(self):
        wide_basis = (2202, 6500)
        self.assertEqual(self.off_with_a_wiggle(0.0, kelvins=wide_basis), (0, 2, "fail"))
        for kelvin in (2650, 2750, 2800):
            self.assertEqual(self.off_with_a_wiggle(0.0, cct_after=kelvin, kelvins=wide_basis), (0, 2, "fail"), kelvin)

    def test_a_small_move_adds_its_own_size_and_a_real_one_the_whole_bulb(self):
        # B1 falls 240 to nothing, which gives 36; B0 at 60 moves by `to`, level_bound says what it added
        model = syn.hue_model({"pt": {B0: 300.0, B1: 300.0}}, kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {"pt": 300.0}, anchored=False, ident="ls-r")

        def bound(to, overrides=None):
            dec = syn.hue_decision("d", t + 0.5, {B0: 0.2, B1: 0.8},
                                   {B0: {"level": to, "steps": send(t + 0.5)}, B1: {"level": 0.0, "steps": send(t + 0.5)}},
                                   "ls-r", targets={"A": {"surface_id": "pt"}})
            after = syn.light_state(t + 5, {"pt": 300.0 * to}, anchored=False)
            return point(score([dec], [ref, after], model, overrides=overrides)[0][0], "pt")["level_bound"]

        self.assertEqual(bound(0.2), 36.0)           # did not move: nothing
        self.assertEqual(bound(0.2 + 1e-15), 36.0)   # a float's last bit: nothing
        self.assertEqual(bound(0.19), 39.0)          # moved 3: adds 3
        self.assertEqual(bound(0.171), 44.7)         # moved 8.7, just inside 15% of its 60: adds 8.7
        self.assertEqual(bound(0.17), 45.0)          # moved 9, its 15%: adds 15% of 60, nothing jumps here
        self.assertEqual(bound(0.1), 45.0)           # past that: the whole bulb, still 15% of 60
        self.assertEqual(bound(0.19, {"change_allowance": 0.3}), 78.0)  # the owner's share of the same ramp

    def test_a_step_off_a_steady_bulb_moves_a_stuck_bulbs_verdict_nowhere(self):
        # the hit may move by the step, not by a whole bulb: no verdict flips in this grid
        flipped = []
        for stuck in (0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0):
            for steady in (0.1, 0.2, 0.3, 0.5, 0.6, 0.8, 1.0):
                flat = self.off_with_a_wiggle(0.0, levels=(stuck, steady))
                for steps in (1, -1):
                    if flat != self.off_with_a_wiggle(steps / 254, levels=(stuck, steady)):
                        flipped.append((stuck, steady, steps))
        self.assertEqual(flipped, [])

    def test_daylight_keeps_the_level_check_on_the_same_dim(self):
        model = syn.hue_model({"desk-1": {B0: 400.0}}, kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {"desk-1": 400 * 0.8 + 500}, anchored=False, ident="ls-ref")
        dec = syn.hue_decision("d-1", t + 0.5, {B0: 0.8}, {B0: {"level": 0.1, "steps": send(t + 0.5)}}, "ls-ref",
                               targets={"A": {"surface_id": "desk-1"}})
        for reading, status in ((40.0 + 500, "within"), ((40.0 + 500) * 1.16, "outside")):
            after = syn.light_state(t + 5, {"desk-1": reading}, anchored=False)
            decisions, _, _ = score([dec], [ref, after], model)
            self.assertEqual(point(decisions[0], "desk-1")["status"], status)
            self.assertNotIn("level_bound", point(decisions[0], "desk-1"))

    def test_the_zero_background_keeps_the_level_check(self):
        d, f, model = self.one_point(0.8, 0.1, 40 * 1.25)
        decisions, _, _ = score(d, f, model)
        self.assertEqual(point(decisions[0], "desk-1")["status"], "within")
        decisions, _, report = score(d, f, model, overrides={"background": "zero"})
        self.assertEqual(decisions[0]["verify"]["increments"][0]["status"], "within")
        self.assertEqual(point(decisions[0], "desk-1")["status"], "outside")
        self.assertEqual(report["hits_beyond_level_tolerance"]["count"], 0)

    def test_an_off_under_the_zero_background_is_judged_too(self):
        zero = {"background": "zero"}
        d, f, model = self.one_point(0.8, 0.0, 0.5)
        self.assertEqual(point(score(d, f, model, overrides=zero)[0][0], "desk-1")["status"], "within")
        d, f, model = self.one_point(0.8, 0.0, 320.0)
        decisions, _, report = score(d, f, model, overrides=zero)
        self.assertEqual(point(decisions[0], "desk-1")["status"], "outside")
        self.assertEqual(report["hit_rate"]["verdict"], "fail")

    def test_nothing_lit_and_nothing_changed_stays_unverifiable(self):
        model = syn.hue_model({"desk-1": {B0: 400.0}}, kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {"desk-1": 0.0}, anchored=False, ident="ls-ref")
        dec = syn.hue_decision("d-1", t + 0.5, {B0: 0.0}, {B0: {"level": 0.0, "steps": send(t + 0.5)}}, "ls-ref",
                               targets={"A": {"surface_id": "desk-1"}})
        decisions, _, report = score([dec], [ref, syn.light_state(t + 5, {"desk-1": 5.0}, anchored=False)], model)
        self.assertEqual(point(decisions[0], "desk-1")["status"], "unverifiable")
        self.assertEqual(report["hit_rate"]["verdict"], "insufficient")
        lux = syn.hue_model({"desk-1": {B0: 400.0}}, unit="lx", kelvins=(2700,))
        ref = syn.light_state(t, {"desk-1": 0.0}, ident="ls-ref")
        decisions, _, report = score([dec], [ref, syn.light_state(t + 5, {"desk-1": 5.0})], lux)
        self.assertEqual(point(decisions[0], "desk-1")["status"], "unverifiable")

    def test_the_allowance_is_the_owners_and_zero_is_the_ladders_level_check(self):
        m = method.load()
        self.assertEqual(m.open["change_allowance"], 0.15)
        d, f, model = self.one_point(0.8, 0.37, 400 * 0.37, err_before=0.10)
        decisions, _, report = score(d, f, model, overrides={"change_allowance": 0})
        self.assertEqual(point(decisions[0], "desk-1")["status"], "outside")
        self.assertEqual(report["hit_rate"]["verdict"], "fail")
        # with the meter's bounds at zero too, an off has no level to take 15% of, and cannot be judged
        literal = {"change_allowance": 0, "uncertainty_rel": 0, "uncertainty_abs_lx": 0}
        d, f, model = self.one_point(0.8, 0.0, 0.5)
        self.assertEqual(point(score(d, f, model, overrides=literal)[0][0], "desk-1")["status"], "unverifiable")

    def room(self, before, after, read, reads, anchored=False, overrides=None):
        """Four equal bulbs, 50 each at full, all commanded from `before` to `after`."""
        unit = "lx" if anchored else "relative"
        model = syn.hue_model({n: {b: 50.0 for b in syn.HUE} for n in self.names}, unit=unit, kelvins=(2700,))
        t = syn.T0
        targets = {f"T{i}": {"surface_id": n} for i, n in enumerate(self.names)}

        def scene(did, tt, b, a, r0, r1):
            ref = syn.light_state(tt, {n: r0 for n in self.names}, anchored=anchored, ident=f"ls-{did}")
            dec = syn.hue_decision(did, tt + 0.5, {x: b for x in syn.HUE},
                                   {x: {"level": a, "steps": send(tt + 0.5)} for x in syn.HUE}, f"ls-{did}",
                                   targets=targets)
            return dec, [ref, syn.light_state(tt + 5, {n: r1 for n in self.names}, anchored=anchored)]

        cases = [scene(f"d-on{i}", t + 100 * i, 0.2, 1.0, 40.0, 200.0) for i in range(3)]
        cases.append(scene("d-dim", t + 400, before, after, read, reads))
        decisions, _, report = score([c[0] for c in cases], [f for c in cases for f in c[1]], model, overrides=overrides)
        return by_id(decisions, "decision_id", "d-dim"), report

    def test_one_bulb_of_four_left_on_a_deep_dim_or_an_off_is_a_miss_in_either_unit(self):
        for anchored in (False, True):
            # 200 to 40 with one of the four stuck reads 80; an off from 202 with one left at full reads 50
            for before, after, read, reads in ((1.0, 0.2, 200.0, 80.0), (1.0, 0.0, 202.0, 50.0), (1.0, 1 / 3, 200.0, 100.0)):
                d, report = self.room(before, after, read, reads, anchored)
                self.assertEqual({p["status"] for p in d["verify"]["points"]}, {"outside"}, (anchored, after))
                hr = report["hit_rate"]
                self.assertEqual((hr["hits"], hr["misses"], hr["verdict"]), (18, 6, "fail"), (anchored, after))

    def test_a_bulb_that_never_moved_in_low_light_is_a_miss_in_either_unit(self):
        for anchored in (False, True):
            unit = "lx" if anchored else "relative"
            model = syn.hue_model({n: {B0: 30.0, B1: 300.0} for n in self.names}, unit=unit, kelvins=(2700,))
            t = syn.T0
            targets = {f"T{i}": {"surface_id": n} for i, n in enumerate(self.names)}

            def scene(did, tt, b, cmd, r0, r1):
                ref = syn.light_state(tt, {n: r0 for n in self.names}, anchored=anchored, ident=f"ls-{did}")
                dec = syn.hue_decision(did, tt + 0.5, b, {x: {"level": lv, "steps": send(tt + 0.5)} for x, lv in cmd.items()},
                                       f"ls-{did}", targets=targets)
                return dec, [ref, syn.light_state(tt + 5, {n: r1 for n in self.names}, anchored=anchored)]

            cases = [scene(f"d-on{i}", t + 100 * i, {B1: 0.3}, {B1: 0.8}, 90.0, 240.0) for i in range(3)]
            cases.append(scene("d-stuck", t + 400, {B0: 1.0}, {B0: 0.2}, 30.0, 30.0))
            decisions, _, report = score([c[0] for c in cases], [f for c in cases for f in c[1]], model)
            self.assertEqual({p["status"] for p in by_id(decisions, "decision_id", "d-stuck")["verify"]["points"]},
                             {"outside"}, anchored)
            hr = report["hit_rate"]
            self.assertEqual((hr["hits"], hr["misses"], hr["value"], hr["verdict"]), (18, 6, 0.75, "fail"), anchored)

    def test_a_stuck_bulb_reads_the_same_on_the_way_up_and_on_the_way_down(self):
        # one of four stuck between 100% and 50%, then between 100% and 30%: the same fault, the same result
        for low, reads_up, reads_down, status in ((0.5, 175.0, 125.0, "within"), (0.3, 165.0, 95.0, "outside")):
            d, _ = self.room(low, 1.0, 200.0 * low, reads_up)
            self.assertEqual(point(d, "p0")["status"], status, ("up", low))
            d, _ = self.room(1.0, low, 200.0, reads_down)
            self.assertEqual(point(d, "p0")["status"], status, ("down", low))

    def test_what_the_allowance_costs_is_what_the_note_says(self):
        # one bulb of four stuck on a 2:1 dim reads 125 for 100: a hit at 15% of the 200 that changed,
        # its increment still outside; a narrower allowance catches it
        d, _ = self.room(1.0, 0.5, 200.0, 125.0)
        self.assertEqual(point(d, "p0")["status"], "within")
        self.assertEqual(d["verify"]["increments"][0]["status"], "outside")
        d, _ = self.room(1.0, 0.5, 200.0, 125.0, overrides={"change_allowance": 0.10})
        self.assertEqual(point(d, "p0")["status"], "outside")


class MeterNoise(unittest.TestCase):
    """The level check carries the fixed measurement bound; a change under it is judged to that bound and said."""

    def case(self, before, after, measured, anchored=True, overrides=None, basis=100.0):
        model = syn.hue_model({"desk-1": {B0: basis}}, unit="lx" if anchored else "relative", kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {"desk-1": basis * before}, anchored=anchored, ident="ls-ref")
        dec = syn.hue_decision("d-1", t + 0.5, {B0: before}, {B0: {"level": after, "steps": send(t + 0.5)}}, "ls-ref",
                               targets={"A": {"surface_id": "desk-1"}})
        return score([dec], [ref, syn.light_state(t + 5, {"desk-1": measured}, anchored=anchored)], model,
                     overrides=overrides)

    def test_a_reading_inside_the_meters_noise_is_not_a_miss_in_low_light(self):
        # 60 to 40 lx at 3% + 10 lx a frame: the two frames together give about 16.3 lx
        for high, status in ((5.0, "within"), (8.0, "within"), (16.0, "within"), (17.0, "outside"), (-17.0, "outside")):
            decisions, _, report = self.case(0.6, 0.4, 40.0 + high)
            self.assertEqual(point(decisions[0], "desk-1")["status"], status, high)
            self.assertEqual(point(decisions[0], "desk-1")["level_bound"], 16.269, high)
        self.assertEqual(report["hit_rate"]["verdict"], "fail")

    def test_the_noise_is_the_owners_and_only_anchored_frames_carry_a_lux_term(self):
        decisions, _, _ = self.case(0.4, 0.25, 33.0, overrides={"uncertainty_abs_lx": 0})
        self.assertEqual(point(decisions[0], "desk-1")["status"], "outside")
        decisions, _, _ = self.case(0.4, 0.25, 33.0, anchored=False)
        self.assertEqual(point(decisions[0], "desk-1")["status"], "outside")

    def test_a_bright_anchored_level_keeps_the_ladders_tolerance_exactly(self):
        for reading, status in ((500 * 1.151, "outside"), (500 * 1.149, "within")):
            decisions, _, report = self.case(0.5, 1.0, reading, basis=500.0)
            self.assertEqual(point(decisions[0], "desk-1")["status"], status)
            self.assertNotIn("level_bound", point(decisions[0], "desk-1"))
            self.assertEqual(report["hits_beyond_level_tolerance"]["count"], 0)

    def test_a_change_under_the_noise_counts_both_ways_judged_to_the_noise(self):
        # a 10 to 1 lx fall or a 40 to 25 lx one: the meter cannot tell them from a bulb that never moved, but it
        # can tell them from a bulb at full; about 14 lx of noise and about 15.5 lx
        for before, after, noise in ((0.1, 0.01, 14.377), (0.4, 0.25, 15.524)):
            predicted = 100.0 * after
            for reading in (0.0, 1.0, 5.0, 10.0, 25.0, 30.0, 33.0, 100.0, 1000.0):
                decisions, states, report = self.case(before, after, reading)
                p = point(decisions[0], "desk-1")
                expected = "within" if abs(reading - predicted) <= noise else "outside"
                self.assertEqual(p["status"], expected, (before, reading))
                self.assertEqual(p["level_bound"], noise, (before, reading))
                hr = report["hit_rate"]
                self.assertEqual(hr["hits"] + hr["misses"], 1, (before, reading))
                self.assertEqual(report["below_meter_noise"]["count"], 1, (before, reading))
                self.assertIn("below_noise", residual(states, syn.T0 + 5, "desk-1")["flags"], (before, reading))

    def good_then_dims(self, factor):
        """One decision that landed, then three 40 to 25 lx dims read at `factor` times the prediction."""
        names = [f"p{i:02d}" for i in range(20)]
        model = syn.hue_model({n: {B0: 100.0} for n in names}, unit="lx", kelvins=(2700,))
        targets = {f"T{i}": {"surface_id": n} for i, n in enumerate(names)}
        t = syn.T0
        decisions = [syn.hue_decision("d-good", t + 0.5, {B0: 0.2}, {B0: {"level": 1.0, "steps": send(t + 0.5)}}, "ls-g",
                                      targets=targets)]
        frames = [syn.light_state(t, {n: 20.0 for n in names}, ident="ls-g"),
                  syn.light_state(t + 5, {n: 100.0 for n in names})]
        for i in range(3):
            at = t + 100 * (i + 1)
            frames.append(syn.light_state(at, {n: 40.0 for n in names}, ident=f"ls-{i}"))
            decisions.append(syn.hue_decision(f"d-{i}", at + 0.5, {B0: 0.4}, {B0: {"level": 0.25, "steps": send(at + 0.5)}},
                                              f"ls-{i}", targets=targets))
            frames.append(syn.light_state(at + 5, {n: 25.0 * factor for n in names}))
        return decisions, frames, model

    def test_a_far_reading_at_a_change_under_the_noise_is_a_miss_and_fails_the_session(self):
        for factor in (5, 25):
            decisions, frames, model = self.good_then_dims(factor)
            report = score(decisions, frames, model)[2]
            hr = report["hit_rate"]
            self.assertEqual((hr["hits"], hr["misses"], hr["value"], hr["verdict"]), (20, 60, 0.25, "fail"), factor)
            self.assertEqual(report["below_meter_noise"],
                             {"count": 60, "hits": 0, "misses": 60, "value_without": 1.0, "verdict_without": "pass"}, factor)

    def test_what_hits_under_the_noise_rest_on_is_said(self):
        # every dim stuck at 40: inside the noise, so a hit the meter could not have told from a landing
        decisions, frames, model = self.good_then_dims(40.0 / 25.0)
        report = score(decisions, frames, model)[2]
        self.assertEqual((report["hit_rate"]["hits"], report["hit_rate"]["verdict"]), (80, "pass"))
        self.assertEqual(report["below_meter_noise"],
                         {"count": 60, "hits": 60, "misses": 0, "value_without": 1.0, "verdict_without": "pass"})
        self.assertEqual(report["hits_beyond_level_tolerance"],
                         {"count": 60, "value_if_missed": 0.25, "verdict_if_missed": "fail"})

    def test_a_session_of_nothing_but_changes_under_the_noise_cannot_pass(self):
        decisions, frames, model = self.good_then_dims(40.0 / 25.0)
        only = [d for d in decisions if d["decision_id"] != "d-good"]
        report = score(only, frames[2:], model)[2]
        hr = report["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["value"], hr["verdict"]), (60, 0, 1.0, "insufficient"))
        self.assertEqual(report["below_meter_noise"],
                         {"count": 60, "hits": 60, "misses": 0, "value_without": None, "verdict_without": "insufficient"})
        # a far reading there can still fail it
        decisions, frames, model = self.good_then_dims(5)
        only = [d for d in decisions if d["decision_id"] != "d-good"]
        hr = score(only, frames[2:], model)[2]["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["verdict"]), (0, 60, "fail"))

    def test_hits_under_the_noise_lift_no_failing_session(self):
        # 16 of 20 where the meter can judge, 80% and a fail; 60 more hits it cannot tell from a stuck bulb make 95%
        decisions, frames, model = self.good_then_dims(1)
        frames[1] = syn.light_state(syn.T0 + 5, {f"p{i:02d}": 100.0 if i < 16 else 60.0 for i in range(20)})
        report = score(decisions, frames, model)[2]
        hr = report["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["value"], hr["verdict"]), (76, 4, 0.95, "fail"))
        self.assertEqual((report["below_meter_noise"]["value_without"], report["below_meter_noise"]["verdict_without"]),
                         (0.8, "fail"))

    def test_a_small_change_where_the_ladders_tolerance_is_the_wider_is_an_ordinary_point(self):
        # 500 to 495 lx: 15% of the level is 74 lx, past the noise, so the ladder's own check stands
        decisions, _, report = self.case(0.5, 0.495, 495.0, basis=1000.0)
        self.assertEqual(point(decisions[0], "desk-1")["status"], "within")
        self.assertEqual(report["below_meter_noise"]["count"], 0)
        self.assertEqual(report["hit_rate"]["verdict"], "pass")

    def test_points_left_out_or_not_admitted_are_not_in_the_noise_count(self):
        model = syn.hue_model({"desk-1": {B0: 100.0, B1: 900.0}}, unit="lx", kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {"desk-1": 40.0 + 90.0}, ident="ls-ref")
        dec = syn.hue_decision("d-1", t + 0.5, {B0: 0.4, B1: 0.1},
                               {B0: {"level": 0.25, "steps": send(t + 0.5)}, B1: {"status": "held", "level": 0.1}}, "ls-ref",
                               targets={"A": {"surface_id": "desk-1"}},
                               hold_set={"revision": 4, "held": [{"entity_id": B1, "channels": [B1],
                                                                  "pinned": {"level_pct": 10, "cct_k": 2700}}]})
        after = syn.light_state(t + 5, {"desk-1": 115.0})
        report = score([dec], [ref, after], model, overrides={"held_rule": "exclude_both"})[2]
        self.assertEqual(report["excluded_held_or_superseded"], 1)
        self.assertEqual(report["below_meter_noise"]["count"], 0)

    def test_a_fault_inside_the_noise_cannot_be_seen(self):
        # as if one bulb of four were stuck on a 60 to 12 lx dim: 12 lx high, inside 15.7 lx of noise
        decisions, _, report = self.case(0.6, 0.12, 24.0)
        self.assertEqual(point(decisions[0], "desk-1")["status"], "within")
        self.assertEqual(report["hits_beyond_level_tolerance"]["count"], 1)

    def test_a_change_past_the_noise_counts_both_ways(self):
        for reading, status in ((20.0, "outside"), (2.0, "within")):  # a 20 to 2 lx fall, stuck or landed
            decisions, _, report = self.case(0.2, 0.02, reading)
            self.assertEqual(point(decisions[0], "desk-1")["status"], status, reading)
            self.assertEqual(report["below_meter_noise"]["count"], 0, reading)

    def test_where_nothing_moved_a_low_level_still_counts_both_ways(self):
        model = syn.hue_model({"desk-1": {B1: 100.0}}, unit="lx", kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {"desk-1": 30.0}, ident="ls-ref")
        dec = syn.hue_decision("d-1", t + 0.5, {B0: 0.2, B1: 0.3}, {B0: {"level": 0.8, "steps": send(t + 0.5)}}, "ls-ref",
                               targets={"A": {"surface_id": "desk-1"}})
        for reading, status in ((30.0, "within"), (40.0, "within"), (100.0, "outside")):
            decisions, _, report = score([dec], [ref, syn.light_state(t + 5, {"desk-1": reading})], model)
            self.assertEqual(point(decisions[0], "desk-1")["status"], status, reading)
            self.assertEqual(report["below_meter_noise"]["count"], 0, reading)

    def test_the_owner_is_told_on_the_console(self):
        model = syn.hue_model({"desk-1": {B0: 100.0}}, unit="lx", kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {"desk-1": 10.0}, ident="ls-ref")
        dec = syn.hue_decision("d-1", t + 0.5, {B0: 0.1}, {B0: {"level": 0.01, "steps": send(t + 0.5)}}, "ls-ref",
                               targets={"A": {"surface_id": "desk-1"}})
        with tempfile.TemporaryDirectory() as tmp:
            argv = write_case(tmp, [dec], [ref, syn.light_state(t + 5, {"desk-1": 1.0})], model)
            with Quiet() as q:
                self.assertEqual(cli.main(argv), 0)
        out = q.buf.getvalue()
        self.assertIn("hit rate 1.0 (1 of 1), gate 0.85: insufficient", out)
        self.assertIn("1 counted point(s) have a change below the meter's noise (1 hit(s), 0 miss(es)); "
                      "without them the hit rate would be None: insufficient; a pass needs both", out)
        self.assertNotIn("left out", out)
        out.encode("ascii")


class HitRateBoundary(unittest.TestCase):
    """Tolerance is inclusive at 15%; the gate passes at exactly 85%."""

    def run_points(self, residuals):
        names = [f"p{i:02d}" for i in range(len(residuals))]
        model = syn.hue_model({n: {B0: 100.0} for n in names}, kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {n: 0.0 for n in names}, anchored=False, ident="ls-ref")
        dec = syn.hue_decision("d-20", t + 0.5, {B0: 0.0}, {B0: {"level": 1.0, "steps": send(t + 0.5)}}, "ls-ref",
                               targets={f"T{i}": {"surface_id": n} for i, n in enumerate(names)})
        after = syn.light_state(t + 4, {n: round(100.0 * (1 + r), 9) for n, r in zip(names, residuals)},
                                anchored=False)
        return score([dec], [ref, after], model)

    def test_boundary_tolerance(self):
        self.assertTrue(within(115, 100, 0.15))
        self.assertTrue(within(85, 100, 0.15))
        self.assertTrue(within(345, 300, 0.15))
        self.assertFalse(within(115.0001, 100, 0.15))

    def test_exactly_85_percent_passes(self):
        res = [0.15] * 9 + [-0.15] * 8 + [0.16, -0.16, 0.3]
        _, _, report = self.run_points(res)
        hr = report["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["value"], hr["verdict"]), (17, 3, 0.85, "pass"))

    def test_one_more_miss_fails(self):
        res = [0.0] * 16 + [0.16] * 4
        _, _, report = self.run_points(res)
        self.assertEqual(report["hit_rate"]["verdict"], "fail")
        hr = Run.hit_rate(type("R", (), {"m": closed()})(), {1: (33, 39)})
        self.assertEqual(hr["verdict"], "fail")  # 84.6%
        hr = Run.hit_rate(type("R", (), {"m": closed()})(), {1: (17, 20), 2: (17, 20)})
        self.assertEqual(hr["verdict"], "pass")
        self.assertEqual(hr["sessions"], 2)
        self.assertEqual(hr["interval"], [0.85, 0.85])

    def test_a_day_with_nothing_counted_is_not_a_session(self):
        fake = type("R", (), {"m": closed()})()
        hr = Run.hit_rate(fake, {1: (17, 20), 2: (0, 0)})
        self.assertEqual((hr["sessions"], hr["interval"], hr["days_with_nothing_counted"]), (1, None, 1))
        self.assertIn("one session", hr["interval_status"])
        hr = Run.hit_rate(fake, {1: (17, 20), 2: (0, 0), 3: (18, 20)})
        self.assertEqual((hr["sessions"], hr["days_with_nothing_counted"]), (2, 1))
        self.assertEqual(hr["interval"], [0.85, 0.9])
        hr = Run.hit_rate(fake, {1: (0, 0)})
        self.assertEqual((hr["sessions"], hr["interval"], hr["verdict"]), (0, None, "insufficient"))

    def test_a_day_left_out_whole_does_not_build_an_interval_end_to_end(self):
        names = [f"p{i:02d}" for i in range(20)]
        model = syn.hue_model({n: {B0: 400.0, B1: 150.0} for n in names}, kelvins=(2700,))
        targets = {f"T{i}": {"surface_id": n} for i, n in enumerate(names)}

        def case(did, t, held, values):
            ref = syn.light_state(t, {n: 400 * 0.2 + 150 * 0.3 for n in names}, anchored=False, ident=f"ls-{did}")
            applied = {B0: {"level": 0.8, "steps": send(t + 0.5)}, B1: {"status": "held" if held else "sent", "level": 0.3}}
            hold_set = {"revision": 4, "held": [{"entity_id": B1, "channels": [B1],
                                                 "pinned": {"level_pct": 30, "cct_k": 2700}}]} if held else None
            dec = syn.hue_decision(did, t + 0.5, {B0: 0.2, B1: 0.3}, applied, f"ls-{did}", targets=targets, hold_set=hold_set)
            return [dec], [ref, syn.light_state(t + 5, values, anchored=False, ident=f"ls-{did}-after")]

        on_target = 400 * 0.8 + 150 * 0.3
        d1, f1 = case("d-1", syn.T0, False, {n: on_target if i < 17 else on_target * 0.6 for i, n in enumerate(names)})
        d2, f2 = case("d-2", syn.T0 + 86400, True, {n: on_target for n in names})
        both = {"held_rule": "exclude_both", "held_share_min": 0.05}
        hr = score(d1 + d2, f1 + f2, model, overrides=both)[2]["hit_rate"]
        self.assertEqual((hr["hits"], hr["misses"], hr["sessions"], hr["interval"]), (17, 3, 1, None))
        self.assertEqual(hr["days_with_nothing_counted"], 1)


class EyesFallbackGold(unittest.TestCase):
    """Eyes are never verified; a fallback patch is a verify aid, never delivered service."""

    def test_eyes_fallback_prior_and_gold(self):
        model = syn.hue_model({"desk-1": {B0: 400}, "sofa-1": {B0: 200}, "wall-1": {B0: 100}}, kelvins=(2700,))
        t = syn.T0
        ref = syn.light_state(t, {"desk-1": 0, "sofa-1": 0, "wall-1": 0}, anchored=False, ident="ls-ref")
        dec = syn.hue_decision("d-eyes", t + 0.5, {B0: 0.0}, {B0: {"level": 1.0, "steps": send(t + 0.5)}}, "ls-ref",
                               targets={"A": {"surface_id": "desk-1"}, "B": {"surface_id": "sofa-1"}},
                               recipients={B0: {"served": ["A"], "exposed": ["B"], "layers": [], "targets": [
                                   {"target_id": "wall-1", "kind": "layer", "layer": "wall"},
                                   {"target_id": "eye-B", "kind": "eye", "track_id": "B"}]}},
                               eyes=("A",))
        frames = [ref]
        for o in (4, 7, 10):
            frames.append(syn.light_state(t + o, {"desk-1": 400, "sofa-1": 200, "wall-1": 100}, anchored=False,
                                          basis={"sofa-1": "fallback_patch", "wall-1": "prior"}))
        gold = [
            {"episode_id": "g1", "track_id": "A", "start": t + 4, "end": t + 20,
             "R_eval": {"surface_id": "desk-1", "placement_sd": 2.0, "source": "gold_mark"}},
            {"episode_id": "g2", "track_id": "B", "start": t + 4, "end": t + 10,
             "R_eval": {"surface_id": "sofa-1", "placement_sd": 2.0, "source": "gold_mark"}},
            {"episode_id": "g3", "track_id": "A", "start": t + 4, "end": t + 6,
             "R_eval": {"surface_id": "desk-1", "placement_sd": 6.0, "source": "gold_mark"}},
            {"episode_id": "g4", "track_id": "A", "start": t + 0, "end": t + 2, "R_eval": None},
        ]
        decisions, _, report = score([dec], frames, model, gold=gold)
        v = decisions[0]["verify"]
        self.assertEqual(v["not_verified"], ["eye-A", "eye-B"])
        self.assertNotIn("eye-A", [p["point_id"] for p in v["points"]])
        self.assertEqual(point(decisions[0], "sofa-1")["status"], "within")      # fallback patch: verify aid
        self.assertEqual(point(decisions[0], "wall-1")["status"], "prior_only")
        self.assertIsNone(point(decisions[0], "wall-1")["residual"])
        self.assertEqual(report["fallback_patch_points_scored"], 1)
        self.assertEqual(report["points"], {"within": 2, "outside": 0, "unverifiable": 0, "prior_only": 1})
        self.assertEqual(report["unverifiable_coverage"], {"surfaces": round(1 / 3, 4), "all_targets": 0.6})
        g = report["gold_need_time"]
        self.assertEqual(g["need_s"], 16 + 6 + 2 + 2)
        # g1: frames at 4, 7, 10 cover 4..15 (5 s gap cap); 15..20 has no frame
        self.assertEqual(g["verifiable_s"], 11.0)
        self.assertEqual(g["unknown_s"], {"geometry": 4.0, "no_frame": 5.0, "mid_fade": 0.0,
                                          "unverifiable": 6.0})  # g2's fallback patch never counts
        self.assertEqual(g["share"], round(11 / 26, 4))
        # B is not served by the changed bulb: spill at B's region, unverifiable in relative units
        self.assertEqual(v["spill"], [{"track_id": "B", "region": "sofa-1", "measured_after": 200.0,
                                       "spill": None, "status": "unverifiable", "uncertainty": None}])


class SpillAndUncertainty(unittest.TestCase):
    """Spill and increments carry their uncertainty from bounds fixed before the run."""

    def test_spill_bands(self):
        d, f, c = syn.v11_evening()
        for value, expect in ((310, "within"), (360, "outside"), (330, "unverifiable")):
            frames = [dict(x) for x in f]
            frames[2] = syn.light_state(syn.T0 + 4.0, {"counter-1": 470, "armchair-book": value})
            decisions, _, _ = score(d, frames, syn.v11_model(), corrections=c)
            s = decisions[0]["verify"]["spill"][0]
            self.assertEqual(s["status"], expect, value)
            self.assertEqual(s["uncertainty"], round(0.03 * value + 10, 3))
            self.assertEqual(s["spill"], round(max(0.0, value - 330), 3))

    def test_increment_uncertainty_and_relative_runs(self):
        d, f, c = syn.v11_evening()
        decisions, _, _ = score(d, f, syn.v11_model(), corrections=c,
                                overrides={"uncertainty_rel": 0.05, "uncertainty_abs_lx": 0})
        inc = {i["point_id"]: i for i in decisions[0]["verify"]["increments"]}
        self.assertEqual(inc["counter-1"]["uncertainty"], round(0.05 * 470 + 0.05 * 25, 3))
        # relative: no lux term, and a relative frame is refused by a lux model
        model = syn.v11_model()
        model["basis_unit"] = "relative"
        rel = [syn.light_state(x["ts"], {e["surface_id"]: e["value"] for e in x["surface_estimates"]},
                               anchored=False) for x in f]
        decisions, _, _ = score(d, rel, model, corrections=c)
        v = decisions[0]["verify"]
        self.assertFalse(v["anchored"])
        self.assertEqual(v["unit"], "relative")
        inc = {i["point_id"]: i for i in v["increments"]}
        self.assertEqual(inc["counter-1"]["status"], "within")
        self.assertEqual(inc["counter-1"]["uncertainty"], round(0.03 * 470 + 0.03 * 25, 3))
        self.assertEqual(v["spill"][0]["status"], "unverifiable")
        decisions, _, _ = score(d, rel, syn.v11_model(), corrections=c)
        self.assertEqual(point(decisions[0], "counter-1")["status"], "unverifiable")


class RealPredict(unittest.TestCase):
    """Every prediction is rung 8 Stage 1's own predict() over a light-model.json in its shape."""

    def setUp(self):
        self.model = syn.hue_model({"desk-1": {B0: 400, B1: 50}, "sofa-1": {B2: 300}, "r00c02": {B0: 9}},
                                   excluded=("r00c02",))
        self.lm = LightModel(self.model)
        self.state = {B0: {"level": 0.6, "cct_k": 3000}, B1: {"level": 0.3, "cct_k": 2400},
                      B2: {"level": 1.0, "cct_k": 6000}}

    def test_backend_is_den_lights_predict(self):
        self.assertEqual(Path(light_model.predict.__file__).resolve().parent, HERE.parent / "den-light")
        self.assertEqual(Path(syn.__file__).resolve().parent, HERE)  # rung 10's synthetic.py stays first
        self.assertEqual(self.lm.backend, light_model.BACKEND)
        self.assertIn("predict.py", light_model.BACKEND)

    def test_values_are_predicts_by_entity_or_channel_and_parts_sum(self):
        ours = self.lm.predict(self.state)
        by_channel = {syn.hue_channel(b): s for b, s in self.state.items()}
        theirs = light_model.predict.predict(by_channel, self.model)["surfaces"]
        self.assertEqual(self.lm.predict(by_channel), ours)
        parts = self.lm.contributions(self.state)
        self.assertIsNone(ours["r00c02"])  # excluded: predict() itself has nothing there
        for sid, value in ours.items():
            if sid == "r00c02":
                continue
            self.assertAlmostEqual(value, theirs[sid], places=9)
            self.assertAlmostEqual(sum(p[sid] for p in parts.values()), value, places=9)

    def test_an_excluded_point_has_no_prediction(self):
        self.assertIsNone(light_model.predict.predict({}, self.model)["surfaces"]["r00c02"])
        self.assertEqual(self.lm.predict({}), {"desk-1": 0.0, "sofa-1": 0.0, "r00c02": None})
        self.assertIsNone(self.lm.predict(self.state)["r00c02"])

    def test_a_decision_naming_an_excluded_point_leaves_it_unverifiable(self):
        t = syn.T0
        k = self.lm.predict({B0: {"level": 1, "cct_k": 2700}})["desk-1"]
        ref = syn.light_state(t, {"desk-1": 0.1 * k, "r00c02": 1}, anchored=False, ident="ls-ref")
        dec = syn.hue_decision("d-ex", t + 0.5, {B0: 0.1}, {B0: {"level": 0.5, "steps": send(t + 0.6)}}, "ls-ref",
                               targets={"A": {"surface_id": "desk-1"}})
        dec["predicted"]["surfaces"] = logged(self.model, dec, ("desk-1", "r00c02"))
        after = syn.light_state(t + 4, {"desk-1": 0.5 * k, "r00c02": 5}, anchored=False)
        decisions, _, report = score([dec], [ref, after], self.model)
        self.assertEqual(point(decisions[0], "desk-1")["status"], "within")
        self.assertEqual(point(decisions[0], "r00c02")["status"], "unverifiable")
        self.assertEqual(report["model"]["backend"], light_model.BACKEND)

    def test_a_state_predict_refuses_leaves_the_decision_unverified(self):
        t = syn.T0
        ref = syn.light_state(t, {"desk-1": 40}, anchored=False, ident="ls-ref")
        dec = syn.hue_decision("d-nocct", t + 0.5, {B0: 0.1}, {B0: {"level": 0.5, "steps": send(t + 0.6)}}, "ls-ref",
                               targets={"A": {"surface_id": "desk-1"}}, cct_k=None)
        after = syn.light_state(t + 4, {"desk-1": 200}, anchored=False)
        decisions, _, _ = score([dec], [ref, after], self.model)
        self.assertIsNone(decisions[0]["verify"])
        self.assertIn("needs cct_k", decisions[0]["verify_status"])
        with self.assertRaisesRegex(KeyError, "no channel 'light.den_attic'"):
            self.lm.predict({"light.den_attic": {"level": 0.5, "cct_k": 2700}})
        self.assertEqual(self.lm.predict({"light.den_attic": {"level": 0, "cct_k": None}}),
                         {"desk-1": 0.0, "sofa-1": 0.0, "r00c02": None})  # a channel that is off needs nothing

    def test_one_captured_temperature_needs_no_cct_k_and_a_level_caps_at_full(self):
        single = LightModel(syn.hue_model({"desk-1": {B0: 400}}, kelvins=(2700,)))
        self.assertEqual(single.predict({B0: {"level": 1, "cct_k": None}})["desk-1"], 400.0)
        full = self.lm.predict({B0: {"level": 1.0, "cct_k": 2700}})
        self.assertEqual(self.lm.predict({B0: {"level": 255 / 254, "cct_k": 2700}}), full)


class MethodAndCommandLine(unittest.TestCase):

    def test_open_slots_listed(self):
        m = method.load()
        self.assertIsNone(m.scored())
        self.assertEqual(sorted(m.open), sorted([
            "settle_margin_s", "background", "change_allowance", "held_share_min", "held_rule",
            "uncertainty_rel", "uncertainty_abs_lx",
            "spill_band_top", "spill_allowance_lx", "daylight_change_min", "shadow_radius_m",
            "max_frame_gap_s", "delta_hit_rate", "record_extensions"]))
        self.assertEqual(m.values["tolerance"], 0.15)
        self.assertEqual(m.values["hit_rate_min"], 0.85)
        self.assertEqual(m.values["fade_s_den_lighting"], 2)
        self.assertEqual(m.values["fade_s_den_lighting_restore_on"], 5)
        self.assertEqual(m.values["fade_s_probe"], 0)
        self.assertEqual(m.open["background"], "reference_frame")

    def test_crlf_and_lf_read_the_same(self):
        text = method.lf_text(HERE / "VERIFY-V1.md")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "VERIFY-V1.md"
            p.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
            crlf = method.load(p)
        lf = method.load()
        self.assertEqual((crlf.values, crlf.open, crlf.sha256), (lf.values, lf.open, lf.sha256))
        rows = [{"a": 1}, {"b": "x"}]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.jsonl"
            p.write_bytes(b'{"a": 1}\r\n{"b": "x"}\r\n')
            self.assertEqual(read_jsonl(p), rows)
            write_jsonl(p, rows)
            self.assertNotIn(b"\r\n", p.read_bytes())

    def test_bad_values_are_refused(self):
        text = method.lf_text(HERE / "VERIFY-V1.md")
        with self.assertRaises(ValueError):
            method.parse(text.replace('proposed "reference_frame"', 'proposed "sideways"'))
        with self.assertRaises(ValueError):
            method.parse(text.replace("tolerance = 0.15", "tolerance = 0.15\ntolerance = 0.2"))

    def test_scored_run_refuses_while_a_slot_is_open(self):
        d, f, c = syn.v11_evening()
        with tempfile.TemporaryDirectory() as tmp:
            argv = write_case(tmp, d, f, syn.v11_model(), c,
                              method_text=method.lf_text(HERE / "VERIFY-V1.md"))
            with Quiet() as q:
                code = cli.main(argv)
            self.assertEqual(code, 2)
            self.assertIn("refused", q.buf.getvalue())
            self.assertFalse((Path(tmp) / "out").exists())
            with Quiet():
                self.assertEqual(cli.main(argv + ["--preview"]), 0)
            out = sorted(p.name for p in (Path(tmp) / "out").iterdir())
            self.assertEqual(out, ["report-preview.json"])
            preview = json.loads((Path(tmp) / "out" / "report-preview.json").read_text(encoding="utf-8"))
            self.assertFalse(preview["scored"])
            self.assertEqual(len(preview["method"]["open"]), 14)

    def test_one_open_slot_is_enough_to_refuse(self):
        text = method.closed_copy(method.lf_text(HERE / "VERIFY-V1.md"))
        text = re.sub(r"^delta_hit_rate = .*$", "delta_hit_rate = open  # open, proposed 0.05", text, flags=re.M)
        self.assertEqual(list(method.parse(text).open), ["delta_hit_rate"])
        self.assertIsNone(method.parse(text).scored())

    def test_the_rules_for_holds_and_dims_each_refuse_a_scored_run_while_open(self):
        d, f, c = syn.v11_evening()
        for slot, proposed in (("change_allowance", "0.15"), ("held_rule", '"free_bulbs_count"'), ("held_share_min", "0.05")):
            text = method.closed_copy(method.lf_text(HERE / "VERIFY-V1.md"))
            text = re.sub(rf"^{slot} = .*$", f"{slot} = open  # open, proposed {proposed}", text, flags=re.M)
            with tempfile.TemporaryDirectory() as tmp:
                argv = write_case(tmp, d, f, syn.v11_model(), c, method_text=text)
                with Quiet() as q:
                    self.assertEqual(cli.main(argv), 2, slot)
                self.assertIn(f"refused: VERIFY-V1.md has 1 open slot(s): {slot}", q.buf.getvalue())
                self.assertFalse((Path(tmp) / "out").exists(), slot)

    def test_slots_command_is_ascii(self):
        with Quiet() as q:
            self.assertEqual(cli.main(["slots"]), 0)
        out = q.buf.getvalue()
        out.encode("ascii")
        self.assertIn("14 open slot(s)", out)
        self.assertIn("pending decision envelope-parameters", out)

    def test_outputs_are_never_overwritten(self):
        d, f, c = syn.v11_evening()
        with tempfile.TemporaryDirectory() as tmp:
            argv = write_case(tmp, d, f, syn.v11_model(), c)
            with Quiet():
                self.assertEqual(cli.main(argv), 0)
            with Quiet() as q:
                self.assertEqual(cli.main(argv), 2)
            self.assertIn("already holds", q.buf.getvalue())


class NoNetwork(unittest.TestCase):
    """The tool opens no socket at all; the run has no host to reach."""

    def test_full_run_with_sockets_forbidden(self):
        real_socket, real_connect = socket.socket, socket.create_connection

        def refuse(*a, **k):
            raise AssertionError("den-verify tried to open a socket")

        socket.socket, socket.create_connection = refuse, refuse
        try:
            d, f, c = syn.v11_evening()
            with tempfile.TemporaryDirectory() as tmp:
                argv = write_case(tmp, d, f, syn.v11_model(), c)
                with Quiet():
                    self.assertEqual(cli.main(argv), 0)
        finally:
            socket.socket, socket.create_connection = real_socket, real_connect

    def test_no_network_imports(self):
        for py in HERE.glob("*.py"):
            if py.name.startswith("test_"):
                continue
            text = py.read_text(encoding="utf-8")
            for mod in ("socket", "http", "urllib", "requests", "ssl", "subprocess"):
                self.assertNotRegex(text, rf"^\s*(import|from)\s+{mod}\b", f"{py.name} imports {mod}")


if __name__ == "__main__":
    unittest.main()
