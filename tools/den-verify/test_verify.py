"""Rung 10's tests. Synthetic only: no camera, Jetson, LA, Home Assistant or network.

    python -m unittest discover -s tools\\den-verify -p "test_*.py"
"""
import contextlib
import io
import json
import math
import re
import socket
import tempfile
import unittest
from pathlib import Path

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
    run = Run(closed(overrides), LightModel(model), decisions, frames, clock=Clock(syn.UTC_OFFSET_S), **kw)
    return run.run()


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
        dec["predicted"]["surfaces"] = {"desk-1": 0, "sofa-1": 0, "wall-1": 0, "counter-1": 0, "unit": "relative"}
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


class MethodAndCommandLine(unittest.TestCase):

    def test_open_slots_listed(self):
        m = method.load()
        self.assertIsNone(m.scored())
        self.assertEqual(sorted(m.open), sorted([
            "settle_margin_s", "background", "held_share_min", "uncertainty_rel", "uncertainty_abs_lx",
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
            self.assertEqual(len(preview["method"]["open"]), 12)

    def test_one_open_slot_is_enough_to_refuse(self):
        text = method.closed_copy(method.lf_text(HERE / "VERIFY-V1.md"))
        text = re.sub(r"^delta_hit_rate = .*$", "delta_hit_rate = open  # open, proposed 0.05", text, flags=re.M)
        self.assertEqual(list(method.parse(text).open), ["delta_hit_rate"])
        self.assertIsNone(method.parse(text).scored())

    def test_slots_command_is_ascii(self):
        with Quiet() as q:
            self.assertEqual(cli.main(["slots"]), 0)
        out = q.buf.getvalue()
        out.encode("ascii")
        self.assertIn("12 open slot(s)", out)
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
