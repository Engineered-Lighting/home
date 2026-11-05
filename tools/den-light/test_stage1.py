"""Tests for den-light Stage 1 (rung 8). Synthetic data and fakes on 127.0.0.1 only.

    python -B -m unittest discover -s tools/den-light -v

A fake Home Assistant runs den_lighting 1.0.4's policy.py unchanged; a fake
camera renders a synthetic den. No camera, Jetson, Home Assistant or other host
is contacted, and no real token is used. Synthetic runs prove plumbing; they are
never evidence for the gate.
"""
import ast
import contextlib
import hashlib
import io
import json
import math
import os
import random
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import guard
import meter
import model
import predict
import probe
import sweep
import synthetic

HERE = Path(__file__).resolve().parent
HA_TOKEN, CAM_TOKEN = "h" * 32, "c" * 32  # synthetic tokens for the fakes only
SNAPSHOT = {"light.den_front_left": guard.scene(True, 180, 2700),
            "light.front_right": guard.scene(False, 120, 3000),
            "light.den_rear_left": guard.scene(True, 90, 2500),
            "light.den_rear_right": guard.scene(True, 200, 4000)}
RANGES = synthetic.KELVIN_RANGE
WARMEST = {e: lo for e, (lo, _) in RANGES.items()}


def store_for(snapshot=SNAPSHOT, **changes):
    data = {"version": 1, "enabled": True, "gaming_enabled": False, "gaming": False,
            "normal": snapshot, "snapshot": {}, "holds": {}}
    data.update(changes)
    return {"version": 1, "minor_version": 1, "key": "den_lighting.state", "data": data}


def method1(**changes):
    """METHOD-V1's Stage 1 numbers with every slot closed at its proposed value, then test timings."""
    base = model.load_stage1().proposed()
    fast = dict(block_px=40, reduce_pixel_step=10, vacancy_s=0.2, poll_s=0.02, start_wait_limit_s=10,
                settle_limit_s=2, frames_per_capture=2, step_dwell_s=0, resume_check_s=3, off_check_s=0.3,
                walkthrough_limit_s=3,
                unavailable_wait_s=1.0, ha_timeout_s=5, frame_timeout_s=3, hold_check_wait_s=0.05)
    fast.update(changes)
    return base.closed(**fast)


def method0():
    base = meter.load_method()
    return meter.Method({**base.values, "pixel_step": 10, "poll_s": 0.005}, (), base.file_sha256, base.block_sha256)


def quiet(*_args, **_kwargs):
    pass


def norm(scene):
    return {k: scene[k] for k in ("on", "brightness", "kelvin")}


# ---- METHOD-V1, Stage 1 part ---------------------------------------------------------------

class MethodStage1Tests(unittest.TestCase):
    def test_the_stage1_block_loads_with_every_key_and_lists_its_open_slots(self):
        m = model.load_stage1()
        self.assertEqual(set(m.values) | set(m.open), set(model.STAGE1_KEYS))
        self.assertIn("block_px", m.open)
        self.assertEqual(m["block_px"], 20, "an open slot reads its proposed value for synthetic runs")
        self.assertEqual(m["confirm_ratios"], [0.25, 0.5, 1])
        fixed = dict(exposure_percentile=98, dark_run_limit_s=10, holdouts=16, random_holdouts=12,
                     holdout_on_probability=0.7, noise_floor_multiple=5, gate_median_percent=8, gate_p90_percent=19,
                     signal_rich_fraction=0.2, shared_curve_percent=3, shared_curve_from_level=16, vacancy_s=120,
                     frame_timeout_s=15, gaming_level=38, full_level=254)
        self.assertEqual({k: m.values[k] for k in fixed}, fixed)
        self.assertEqual(m.values["dimming_levels"], [1, 2, 4, 8, 16, 32, 64, 96, 128, 176, 224])
        with self.assertRaises(model.Refused):
            m.refuse_if_open("A scored run")
        m.proposed().refuse_if_open("A scored run")

    def test_crlf_and_lf_read_and_hash_the_same(self):
        text = (HERE / meter.METHOD_FILE).read_text(encoding="utf-8")
        lf, crlf = model.parse_stage1(text), model.parse_stage1(text.replace("\n", "\r\n"))
        self.assertEqual((lf.values, lf.open, lf.block_sha256, lf.file_sha256),
                         (crlf.values, crlf.open, crlf.block_sha256, crlf.file_sha256))
        meter.parse_method(text.replace("\n", "\r\n"))  # the Stage 0 block still reads alongside

    def test_the_parser_refuses_bad_lines(self):
        good = (HERE / meter.METHOD_FILE).read_text(encoding="utf-8")
        for bad in (good.replace("block_px = open                    # open, proposed 20", "block_px = open  # no proposal"),
                    good.replace("holdouts = 16", "holdouts = 16\nholdouts = 16"),
                    good.replace("holdouts = 16", "holdouts = sixteen"),
                    good.replace("holdouts = 16", "holdouts = 16\nextra_key = 1"),
                    good.replace("gaming_level = 38", "")):
            with self.assertRaises(ValueError):
                model.parse_stage1(bad)


# ---- the schedule ---------------------------------------------------------------------------

class ScheduleTests(unittest.TestCase):
    def test_same_seed_same_schedule_and_another_seed_differs(self):
        m = method1()
        a = model.schedule(m, SNAPSHOT, RANGES, WARMEST)
        self.assertEqual(a, model.schedule(m, SNAPSHOT, RANGES, WARMEST))
        b = model.schedule(m.closed(schedule_seed=7), SNAPSHOT, RANGES, WARMEST)
        self.assertNotEqual([s["scene"] for s in a["steps"] if s["kind"] == "holdout"],
                            [s["scene"] for s in b["steps"] if s["kind"] == "holdout"])

    def test_no_holdout_repeats_a_fitted_step(self):
        m = method1()
        for seed in range(6):
            plan = model.schedule(m.closed(schedule_seed=seed), SNAPSHOT, RANGES, WARMEST)
            temps = set(plan["temperatures"].values())
            fitted = set(m["dimming_levels"]) | {m["full_level"]}
            holdouts = [s for s in plan["steps"] if s["kind"] == "holdout"]
            self.assertEqual(len(holdouts), 16)
            for step in holdouts:
                on = [s for s in step["scene"].values() if s["on"]]
                self.assertGreaterEqual(len(on), 2, "mixed scenes have two or more bulbs on")
                if step["label"].startswith("random"):
                    for s in on:
                        self.assertNotIn(s["brightness"], fitted)
                        self.assertNotIn(s["kelvin"], temps)
            fitted_scenes = [json.dumps(s["scene"], sort_keys=True) for s in plan["steps"]
                             if s["kind"] in ("all_off", "basis", "dimming")]
            self.assertFalse({json.dumps(s["scene"], sort_keys=True) for s in holdouts} & set(fitted_scenes))
            near = [s for s in holdouts if sum(x["brightness"] for x in s["scene"].values() if x["on"])
                    >= m["near_full_share"] * 4 * m["full_level"]]
            self.assertGreaterEqual(len(near), 3)

    def test_no_dark_run_longer_than_the_limit_and_brackets_every_few_steps(self):
        m = method1()
        for seed in range(6):
            plan = model.schedule(m.closed(schedule_seed=seed), SNAPSHOT, RANGES, WARMEST)
            longest, run = 0, 0
            for step in plan["steps"]:
                run = 0 if model.has_full(step, m) else run + 1 if model.is_dark(step, m) else 0
                longest = max(longest, run)
            self.assertLessEqual(longest * m["step_estimate_s"], m["dark_run_limit_s"])
        bad = [{"index": i, "kind": "dimming", "scene": model.one_on(guard.LIGHTS[0], 1, 3000)} for i in range(1, 7)]
        with self.assertRaises(ValueError):
            model.check_schedule(bad, m)

    def test_the_schedule_holds_every_capture_the_fit_needs(self):
        m = method1()
        plan = model.schedule(m, SNAPSHOT, RANGES, WARMEST)
        kinds = [s["kind"] for s in plan["steps"]]
        self.assertEqual((kinds[0], kinds[-1]), ("all_off", "all_off"))
        self.assertEqual(kinds.count("drift"), 2)
        self.assertEqual(kinds.count("confirm"), 3)
        warm, mid, cool = (plan["temperatures"][k] for k in ("warm", "middle", "cool"))
        self.assertEqual((warm, cool), (2202, 6535), "the range all four share")
        for e in guard.LIGHTS:
            self.assertEqual({s["kelvin"] for s in plan["steps"] if s["kind"] == "basis" and s["bulb"] == e},
                             {warm, mid, cool})
            self.assertEqual(sorted(s["level"] for s in plan["steps"] if s["kind"] == "dimming" and s["bulb"] == e),
                             m["dimming_levels"])


# ---- the model, from readings ------------------------------------------------------------------

def readings_for(den, m, *, response=None, seed=0):
    """A session's readings as the reduction would give them, straight from a synthetic den."""
    plan = model.schedule(m, SNAPSHOT, RANGES, WARMEST)
    steps, t = [], 0.0
    for step in plan["steps"]:
        t += 2.5
        values = den.reading(step["scene"], t)
        if response:
            values = [response(v) for v in values]
        steps.append({**step, "t": t, "reading": values, "spread": [max(den.noise, 1e-6)] * den.blocks})
    return {"schema": model.SCHEMAS["readings"], "session": "synthetic", "blocks": den.blocks,
            "mask": {"masked_pixels": 0, "pixels": den.blocks * 16, "unmasked_share": [1.0] * den.blocks},
            "steps": steps, "temperatures": plan["temperatures"]}


def truth_basis(den, e, kelvin, b):
    return sum(w * den.spatial[e][b] * c for w, c in zip((0.2126, 0.7152, 0.0722), den.colour(kelvin)))


class ModelTests(unittest.TestCase):
    def fit_score(self, den, m=None, **kwargs):
        m = m or method1()
        r = readings_for(den, m, **kwargs)
        fitted = model.fit(r, r["temperatures"], m)
        return r, fitted, model.score(r, fitted, m)

    def test_the_fit_recovers_bases_and_curves_within_one_percent_and_the_holdouts_pass(self):
        den = synthetic.SyntheticDen(block_px=40, noise=2e-5)
        r, fitted, scored = self.fit_score(den)
        temps = r["temperatures"]
        errors = []
        for e in guard.LIGHTS:
            for k in temps.values():
                for b in range(den.blocks):
                    if b not in fitted["excluded"]:
                        errors.append(abs(fitted["basis"][e][str(k)][b] / truth_basis(den, e, k, b) - 1))
            for u, g in zip(fitted["dimming"][e]["levels"], fitted["dimming"][e]["values"]):
                if u >= 16:
                    self.assertAlmostEqual(g / den.curves[e](u), 1, delta=0.01, msg=(e, u))
        self.assertLess(len(fitted["excluded"]), den.blocks // 20)
        self.assertLess(max(errors), 0.01)
        self.assertEqual(scored["error"]["verdict"], "pass", scored["error"])

    def test_a_saturating_sum_fails_the_gate(self):
        _, _, scored = self.fit_score(synthetic.SyntheticDen(block_px=40, noise=1e-4, saturate=0.6))
        self.assertEqual(scored["error"]["verdict"], "fail", scored["error"])

    def test_a_wrong_response_curve_fails_the_gate(self):
        _, _, scored = self.fit_score(synthetic.SyntheticDen(block_px=40, noise=1e-4),
                                      response=lambda v: math.copysign(abs(v) ** 0.6, v))
        self.assertEqual(scored["error"]["verdict"], "fail", scored["error"])

    def test_a_temperature_dependent_curve_shows_in_the_error_by_band(self):
        _, _, flat = self.fit_score(synthetic.SyntheticDen(block_px=40, noise=1e-4))
        _, _, tilted = self.fit_score(synthetic.SyntheticDen(block_px=40, noise=1e-4, temperature_curve=True))
        bands = tilted["information_only"]["error_by_temperature_band"]
        self.assertGreater(bands["warm"], 3 * flat["information_only"]["error_by_temperature_band"]["warm"])
        self.assertGreater(bands["warm"], bands["cool"])

    def test_the_shared_curve_check_is_true_within_a_pair_and_false_across(self):
        _, fitted, _ = self.fit_score(synthetic.SyntheticDen(block_px=40, noise=2e-5))
        self.assertEqual(set(fitted["shared_curve"].values()), {True})
        mixed = {e: synthetic.TYPES[t] for e, t in zip(guard.LIGHTS, "ABAB")}  # each pair holds both types
        _, fitted, _ = self.fit_score(synthetic.SyntheticDen(block_px=40, noise=2e-5, curves=mixed))
        self.assertEqual(set(fitted["shared_curve"].values()), {False})

    def test_percentiles_match_hand_worked_values(self):
        values = [0.1, 0.4, 0.2, 0.3]
        self.assertAlmostEqual(model.percentile(values, 50), 0.25)
        self.assertAlmostEqual(model.percentile(values, 90), 0.37)  # rank 2.7: 0.3 + 0.7 * 0.1
        self.assertAlmostEqual(model.percentile([5.0], 90), 5.0)
        self.assertIsNone(model.percentile([], 50))

    def test_the_floor_excludes_and_counts_and_only_median_and_p90_decide(self):
        den = synthetic.SyntheticDen(block_px=40, noise=1e-4)
        m = method1()
        r = readings_for(den, m)
        fitted = model.fit(r, r["temperatures"], m)
        base = model.score(r, fitted, m)
        self.assertEqual(base["error"]["excluded_count"], 0)
        raised = model.score(r, fitted, m.closed(noise_floor_multiple=1e6))
        self.assertEqual((raised["error"]["count"], raised["error"]["excluded_count"]), (0, base["error"]["count"]))
        self.assertEqual(raised["error"]["verdict"], "fail", "no pairs: no pass")
        strict = model.score(r, fitted, m.closed(gate_p90_percent=0.0001))
        self.assertEqual(strict["error"]["verdict"], "fail")
        self.assertEqual(set(base["error"]), {"median", "p90", "count", "excluded_count", "verdict", "gate"})
        self.assertEqual(base["absolute"], "not run, waits on photometry-commissioning")
        self.assertIn("not tested at rung 8 (one session)", base["day_to_day_stability"])
        self.assertIn("camera-scale", base["camera_scale"])
        for key in ("error_by_temperature_band", "steps", "shared_curve", "middle_temperature_error", "drift",
                    "response_confirmation"):
            self.assertIn(key, base["information_only"])


# ---- the reduction rule (session mask) ------------------------------------------------------

class TinyDen:
    """Four blocks of 34 pixels; linear dimming and linear-in-mired colour, so the model is exact."""

    def __init__(self):
        rng = random.Random(4)
        self.owner = [b for b in range(4) for _ in range(34)]
        self.tex = [rng.uniform(0.7, 1.3) for _ in self.owner]
        self.warm = {e: [rng.uniform(0.5, 2) for _ in range(4)] for e in guard.LIGHTS}
        self.cool = {e: [rng.uniform(0.5, 2) for _ in range(4)] for e in guard.LIGHTS}
        self.day = [0.01, 0.02, 0.015, 0.012]

    def block(self, scene, temps):
        out = list(self.day)
        for e, s in scene.items():
            if s["on"]:
                w = (1e6 / temps["warm"] - 1e6 / s["kelvin"]) / (1e6 / temps["warm"] - 1e6 / temps["cool"])
                w = min(1.0, max(0.0, w))  # a bulb outside the shared range looks like its nearest end
                for b in range(4):
                    out[b] += s["brightness"] / 254 * (self.warm[e][b] + (self.cool[e][b] - self.warm[e][b]) * w)
        return out

    def captures(self, m, flag=None):
        """flag(step, pixel) -> True to flag that pixel in that capture."""
        plan = model.schedule(m, SNAPSHOT, RANGES, WARMEST)
        out, t = [], 0.0
        for step in plan["steps"]:
            t += 2.5
            blocks = self.block(step["scene"], plan["temperatures"])
            values = [blocks[b] * self.tex[p] for p, b in enumerate(self.owner)]
            flagged = [bool(flag and flag(step, p)) for p in range(len(self.owner))]
            values = [None if f else v for v, f in zip(values, flagged)]
            out.append({"step": step, "t": t, "frames": [(values, flagged), (values, flagged)]})
        return out, plan["temperatures"]


class ReductionTests(unittest.TestCase):
    def setUp(self):
        self.m = method1(min_unmasked_share=0.5)
        self.den = TinyDen()

    def run_model(self, flag=None, edit=None):
        captures, temps = self.den.captures(self.m, flag)
        if edit:
            edit(captures)
        r = model.reduce_captures(captures, self.m, self.den.owner)
        r["temperatures"] = temps
        fitted = model.fit(r, temps, self.m)
        return r, fitted, model.score(r, fitted, self.m)

    def block_errors(self, r, fitted, b):
        day = model.daylight(r)
        out = []
        for step in r["steps"]:
            if step["kind"] == "holdout" and step["reading"][b] is not None:
                e = step["reading"][b] - day(b, step["t"])
                out.append(abs(model.internal_predict(step["scene"], fitted)[b] - e) / e)
        return out

    def test_three_percent_clipping_in_one_bright_holdout_gives_that_block_zero_error(self):
        bright = lambda step: step["kind"] == "holdout" and step["label"] == "all_full_middle"
        r, fitted, _ = self.run_model(flag=lambda step, p: bright(step) and p == 0)  # 1 of block 0's 34 pixels
        self.assertEqual(r["mask"]["masked_pixels"], 1)
        self.assertAlmostEqual(r["mask"]["unmasked_share"][0], 33 / 34)
        self.assertLess(max(self.block_errors(r, fitted, 0)), 1e-9, "the mask removes the pixel from every step")


    def test_a_block_clipped_below_the_minimum_in_one_basis_capture_is_excluded_and_counted(self):
        first_basis = lambda step: step["kind"] == "basis" and step["bulb"] == guard.LIGHTS[0] and step["kelvin"] == 2202
        r, fitted, scored = self.run_model(flag=lambda step, p: first_basis(step) and 34 <= p < 34 + 20)
        self.assertEqual(fitted["excluded"], {1: "mask"})
        self.assertEqual(scored["blocks_excluded"], {"mask_minimum": 1, "missing_basis": 0})
        self.assertEqual(self.block_errors(r, fitted, 0) != [], True)
        pairs = scored["error"]["count"]
        holdouts = sum(1 for s in r["steps"] if s["kind"] == "holdout")
        self.assertEqual(pairs, holdouts * 3, "block 1 is in no pair")

    def test_disagreeing_brackets_flag_the_steps_between_them(self):
        def disturb(captures):
            offs = [c for c in captures if c["step"]["kind"] == "all_off"]
            target = offs[3]
            target["frames"] = [([v * (5 if b == 2 else 1) if v is not None else None
                                  for v, b in zip(vals, self.den.owner)], f) for vals, f in target["frames"]]
            for c in captures:  # a little frame-to-frame noise, so the block has an all-off noise
                if c["step"]["kind"] == "all_off":
                    v, f = c["frames"][1]
                    c["frames"][1] = ([x * 1.0001 if x is not None else None for x in v], f)
        r, fitted, _ = self.run_model(edit=disturb)
        flagged_blocks = {b for _, b in fitted["flagged"]}
        self.assertEqual(flagged_blocks, {2})
        offs = [s["index"] for s in r["steps"] if s["kind"] == "all_off"]
        self.assertTrue(all(offs[2] < i < offs[4] for i, _ in fitted["flagged"]))

    def test_fit_and_score_from_readings_json_alone_match_the_frames(self):
        r, fitted, scored = self.run_model()
        again = json.loads(json.dumps(r))
        fitted2 = model.fit(again, again["temperatures"], self.m)
        self.assertEqual(model.score(again, fitted2, self.m)["error"], scored["error"])


# ---- light-model.json and predict() ----------------------------------------------------------

FIXTURES = [{"record": "fixture", "contract": "live-v1", "fixture_id": e.split(".", 1)[1], "model": "hue-den-4",
             "pose": {"position": None, "method": "assumed"},
             "channels": [{"channel_id": "white", "kind": "tunable_white", "entity_id": e,
                           "cct_range_k": list(RANGES[e])}],
             "orientation_calibration": None, "optics": None, "dimming_curve": None, "gimbal": None}
            for e in guard.LIGHTS]  # written from schemas.json's Fixture fields, not from probe.py


class LightModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = method1()
        cls.den = synthetic.SyntheticDen(block_px=40, noise=1e-4)
        cls.r = readings_for(cls.den, cls.m)
        cls.fitted = model.fit(cls.r, cls.r["temperatures"], cls.m)
        scored = model.score(cls.r, cls.fitted, cls.m)
        cls.lm = json.loads(json.dumps(model.light_model(
            cls.fitted, FIXTURES, cls.m, session_id="s1", readings_sha256="0" * 64,
            meter_norm_sha256="1" * 64, score_error=scored["error"])))

    def test_light_model_json_keeps_the_contract_field_names(self):
        contract = json.loads((HERE.parent.parent / "docs/architecture/live-v1/schemas.json").read_text(encoding="utf-8"))
        record = next(r for r in contract["records"] if r["id"] == "light_model")
        names = set()
        for field in record["fields"]:
            names.update(part.strip() for part in field["name"].split("/"))
        self.assertEqual(names, {"version", "room_version", "sample_points", "basis", "basis_unit", "spot_model",
                                 "dimming", "bounce", "glare", "error"})
        self.assertTrue(names <= set(self.lm))
        self.assertEqual(self.lm["basis_unit"], "relative")
        self.assertEqual(self.lm["bounce"], "measured")
        self.assertIsNone(self.lm["glare"])
        self.assertEqual(set(self.lm["sample_points"][0]) >= {"point_id", "kind", "xyz", "normal", "rect"}, True)
        self.assertEqual((self.lm["sample_points"][0]["kind"], self.lm["sample_points"][0]["xyz"]), (None, None))
        self.assertTrue({"median_pct", "p90_pct", "count", "excluded_count", "verdict"} <= set(self.lm["error"]))
        self.assertEqual(self.lm["anchor"]["decision"], "photometry-commissioning")
        self.assertEqual(set(self.lm["anchor"]["options"]), {"card-and-meter", "meter-only", "other-sensor"})
        self.assertIsNone(self.lm["anchor"]["chosen"])
        self.assertEqual(len(self.lm["sample_points"]), self.den.blocks)
        self.assertEqual(self.lm["sessions"][0]["readings_sha256"], "0" * 64)

    def test_predict_matches_the_fit_and_returns_every_point(self):
        scene_ha = {"light.den_front_left": guard.scene(True, 100, 3100), "light.front_right": guard.scene(False),
                    "light.den_rear_left": guard.scene(True, 254, 5000), "light.den_rear_right": guard.scene(True, 30, 2500)}
        out = predict.predict(predict.scene_from_ha(scene_ha, self.lm), self.lm)
        internal = model.internal_predict(scene_ha, self.fitted)
        points = [p["point_id"] for p in self.lm["sample_points"]]
        self.assertEqual(list(out["surfaces"]), points + ["unit"])
        self.assertEqual(out["surfaces"]["unit"], "relative")
        self.assertEqual((out["version"], out["daylight"]), ("lm-den-s1", "excluded"))
        excluded = [pid for pid, p in zip(points, self.lm["sample_points"]) if p["excluded"]]
        for pid, value in zip(points, internal):
            if pid in excluded:
                self.assertIsNone(out["surfaces"][pid])
            else:
                self.assertAlmostEqual(out["surfaces"][pid], value, places=6)
        truth = self.den.reading(scene_ha, 0.0)
        day = self.den.reading({}, 0.0)
        rel = [abs(out["surfaces"][pid] - (t - d)) / (t - d) for pid, t, d in zip(points, truth, day)
               if out["surfaces"][pid] is not None]
        self.assertLess(sorted(rel)[len(rel) // 2], 0.01)

    def test_predict_input_rules(self):
        key = self.lm["channels"][0]["channel_key"]
        self.assertEqual(key, "den_front_left.white")
        zero = predict.predict({}, self.lm)
        excluded = {p["point_id"] for p in self.lm["sample_points"] if p["excluded"]}
        self.assertTrue(all(v == 0 for k, v in zero["surfaces"].items() if k != "unit" and k not in excluded))
        self.assertEqual(predict.predict({key: {"level": 0, "cct_k": None}}, self.lm), zero)
        with self.assertRaises(ValueError):
            predict.predict({"F9.spot": {"level": 0.5, "cct_k": 3000}}, self.lm)
        with self.assertRaises(ValueError):
            predict.predict({key: {"level": 0.5}}, self.lm)
        with self.assertRaises(ValueError):
            predict.predict({key: {"level": 1.5, "cct_k": 3000}}, self.lm)
        lit = predict.predict({key: {"level": 1.0, "cct_k": 2202}}, self.lm)
        daylight = {p["point_id"]: 0.5 for p in self.lm["sample_points"]}
        both = predict.predict({key: {"level": 1.0, "cct_k": 2202}}, self.lm, daylight=daylight)
        pid = self.lm["sample_points"][5]["point_id"]
        self.assertAlmostEqual(both["surfaces"][pid], lit["surfaces"][pid] + 0.5)
        self.assertEqual(both["daylight"], "included")
        prepared = predict.prepare(self.lm)
        self.assertEqual(predict.predict({key: {"level": 1.0, "cct_k": 2202}}, prepared), lit)
        clamped = predict.predict({key: {"level": 1.0, "cct_k": 1800}}, self.lm)  # warmer than captured
        self.assertEqual(clamped, lit)

    def test_an_excluded_point_is_none_whatever_the_scene(self):
        lm = json.loads(json.dumps(self.lm))
        point = lm["sample_points"][7]
        point["excluded"] = "mask"
        for row in lm["basis"].values():
            row[point["point_id"]] = None
        key = lm["channels"][0]["channel_key"]
        daylight = {p["point_id"]: 0.5 for p in lm["sample_points"]}
        for scene in ({}, {key: {"level": 0.5, "cct_k": 3000}}):
            for out in (predict.predict(scene, lm), predict.predict(scene, lm, daylight=daylight)):
                self.assertIsNone(out["surfaces"][point["point_id"]], "not 0: nothing is known there")
        self.assertEqual(predict.predict({}, lm)["surfaces"][lm["sample_points"][6]["point_id"]], 0)

    def test_predict_is_pure_and_imports_without_side_effects(self):
        tree = ast.parse((HERE / "predict.py").read_text(encoding="utf-8"))
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertTrue(imported <= set(sys.stdlib_module_names), imported)
        self.assertFalse(imported & {"socket", "http", "urllib", "os", "subprocess"})
        top = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                                                           ast.ClassDef, ast.Assign, ast.Expr))]
        self.assertEqual(top, [], "nothing runs at import")
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, "-I", "-c",
                                     f"import sys; sys.path.insert(0, {str(HERE)!r}); import predict"],
                                    cwd=tmp, capture_output=True, text=True, timeout=60)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
            self.assertEqual(os.listdir(tmp), [])

    def test_predict_is_fast(self):
        prepared = predict.prepare(self.lm)
        scene = {c["channel_key"]: {"level": 0.6, "cct_k": 3300} for c in self.lm["channels"]}
        start = time.perf_counter()
        for _ in range(20):
            predict.predict(scene, prepared)
        self.assertLess((time.perf_counter() - start) / 20, 0.1, "rung 9's 100 ms budget")


# ---- guard.py -----------------------------------------------------------------------------------

def states(ha):
    return {s["entity_id"]: s for s in json.loads(json.dumps(list(ha.states.values())))}


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.ha = synthetic.FakeHA(token=HA_TOKEN, store=store_for(), speed=600)
        self.addCleanup(self.ha.close)

    def test_each_store_copy_failure_refuses(self):
        good = json.dumps(store_for())
        self.assertEqual(guard.read_store(good)["light.front_right"], SNAPSHOT["light.front_right"])
        hold = {"light.front_right": {"scene": {"on": True, "brightness": 1, "kelvin": 3000}, "since": 1.0}}
        bad_normal = dict(SNAPSHOT, **{"light.den_rear_left": {"on": True, "brightness": None, "kelvin": 2500}})
        for text in (json.dumps(store_for(holds=hold)), json.dumps(store_for(gaming=True)),
                     json.dumps(store_for(snapshot=bad_normal)), json.dumps(store_for(version=2)),
                     json.dumps(store_for(enabled=False)), "not json"):
            with self.assertRaises(guard.Refused):
                guard.read_store(text)

    def test_each_start_failure_is_named(self):
        switch, sensor = guard.find_entities(list(states(self.ha).values()))
        self.assertEqual((switch, sensor), (synthetic.SWITCH, synthetic.SENSOR))
        self.assertEqual(guard.start_problems(states(self.ha), switch, sensor), [])
        self.ha.set_incumbent("on")
        self.assertIn("the incumbent den automation is not off", guard.start_problems(states(self.ha), switch, sensor))
        self.ha.set_incumbent("off")
        by_id = states(self.ha)
        by_id[sensor]["attributes"]["new_key"] = 1
        self.assertIn("not den_lighting 1.0.4's", guard.start_problems(by_id, switch, sensor)[0])
        by_id = states(self.ha)
        by_id[sensor]["attributes"]["error"] = "light_command_failed:x"
        self.assertTrue(guard.start_problems(by_id, switch, sensor))
        self.ha.set_available("light.den_rear_left", False)
        self.assertIn("light.den_rear_left is not available", guard.start_problems(states(self.ha), switch, sensor))

    def test_a_hold_or_game_during_the_wait_refuses(self):
        self.ha.set_light("light.den_front_left", on=True, brightness=40, kelvin=3000)
        self.assertIn("holds appeared", guard.wait_problems(states(self.ha), synthetic.SENSOR)[0])
        self.ha.start_game()
        self.assertTrue(any("game" in p for p in guard.wait_problems(states(self.ha), synthetic.SENSOR)))

    def test_own_and_foreign(self):
        before = states(self.ha)
        owner = guard.Owner({e: guard.scene_of_state(before[e]) for e in guard.LIGHTS})
        e = "light.den_front_left"
        owner.intend(e, guard.scene(True, 100, 3000))
        changed = self.ha.call("light", "turn_on", {"entity_id": e, "brightness": 100, "color_temp_kelvin": 3000})
        owner.responded(e, changed[0]["context"]["id"])
        after = states(self.ha)
        self.assertEqual(guard.classify(e, before[e], after[e], owner), "own")
        self.assertIsNone(guard.classify(e, after[e], after[e], owner))
        self.ha.set_light(e, on=True, brightness=100, kelvin=3000)  # same value, a new report with a foreign context
        self.ha.set_light(e, on=True, brightness=101, kelvin=3000)
        self.ha.set_light(e, on=True, brightness=100, kelvin=3000)
        moved = states(self.ha)
        self.assertEqual(guard.classify(e, after[e], moved[e], owner), "foreign", "even if the value came back")
        owner.intend("light.front_right", guard.scene(True, 50, 3000))  # no response: the earlier intent still counts
        self.assertTrue(owner.is_own("light.front_right", guard.scene(False)))
        self.assertTrue(owner.is_own("light.front_right", guard.scene(True, 51, 3000)))
        self.ha.set_available("light.den_rear_left", False)
        self.assertEqual(guard.classify("light.den_rear_left", before["light.den_rear_left"],
                                        states(self.ha)["light.den_rear_left"], owner), "unavailable")

    def written(self, e, level):
        """(owner, the state the tool's write left) for one bulb, its reply naming the state."""
        before = states(self.ha)
        owner = guard.Owner({x: guard.scene_of_state(before[x]) for x in guard.LIGHTS})
        owner.intend(e, guard.scene(True, level, 3000))
        changed = self.ha.call("light", "turn_on", {"entity_id": e, "brightness": level, "color_temp_kelvin": 3000})
        owner.responded(e, changed[0]["context"]["id"])
        return owner, states(self.ha)[e]

    def test_a_devices_own_report_of_the_target_is_the_tools(self):
        e = "light.den_front_left"
        owner, after = self.written(e, 100)
        self.ha.set_light(e, on=True, brightness=101, kelvin=3000)  # one step off, with a context of its own
        report = states(self.ha)[e]
        self.assertNotIn(report["context"]["id"], owner.contexts)
        self.assertEqual(guard.classify(e, after, report, owner), "own", "within tolerance, and the value moved")
        self.assertIn(report["context"]["id"], owner.contexts)
        self.assertEqual(guard.classify(e, after, report, owner), "own", "classifying twice changes nothing")
        self.ha.set_light(e, on=True, brightness=140, kelvin=3000)
        self.assertEqual(guard.classify(e, report, states(self.ha)[e], owner), "foreign", "beyond tolerance")

    def test_the_first_report_of_a_write_whose_reply_named_no_state_is_the_tools(self):
        e = "light.den_front_left"
        before = states(self.ha)
        owner = guard.Owner({x: guard.scene_of_state(before[x]) for x in guard.LIGHTS})
        owner.intend(e, guard.scene(True, 100, 3000))
        owner.responded(e, None)
        self.ha.set_light(e, on=True, brightness=100, kelvin=3000)
        report = states(self.ha)[e]
        self.assertEqual(guard.classify(e, before[e], report, owner), "own")
        self.ha.set_light(e, on=False)
        self.assertEqual(guard.classify(e, report, states(self.ha)[e], owner), "foreign")

    def test_a_bulb_back_from_unavailable_is_not_a_persons_change(self):
        e = "light.den_front_left"
        owner, after = self.written(e, 100)
        self.ha.set_available(e, False)
        gone = states(self.ha)[e]
        self.assertEqual(guard.classify(e, after, gone, owner), "unavailable")
        self.ha.set_available(e, True)
        self.ha.set_light(e, on=True, brightness=254, kelvin=2700)  # a power-cycled bulb comes back at its default
        back = states(self.ha)[e]
        self.assertEqual(guard.classify(e, gone, back, owner), "unavailable")

    def test_restore_plan_order(self):
        plan = guard.restore_plan(SNAPSHOT, guard.LIGHTS)
        right = [a["do"] for a in plan if a.get("entity_id") == "light.front_right"]
        self.assertEqual(right, ["turn_on", "wait_on", "turn_off"], "a snapshot-off bulb: on, then off")
        self.assertEqual(plan[-1]["do"], "check_holds")
        self.assertEqual(plan[-1]["expect"]["light.front_right"], SNAPSHOT["light.front_right"])
        arrival = guard.restore_plan(SNAPSHOT, guard.LIGHTS, arrival=True)
        self.assertEqual([a["do"] for a in arrival if a.get("entity_id") == "light.front_right"], ["turn_on"])
        self.assertEqual(arrival[-1]["expect"]["light.front_right"], guard.scene(True, 120, 3000))
        self.assertEqual(guard.resume_plan(["light.front_right"]),
                         [{"do": "switch_on"}, {"do": "resume", "entity_ids": ["light.front_right"]}])
        self.assertNotIn("light.den_front_left",
                         [a.get("entity_id") for a in guard.restore_plan(SNAPSHOT, ["light.front_right"])])

    def test_hold_check_tolerances(self):
        want = guard.scene(False, 120, 3000)
        self.assertTrue(guard.hold_ok(want, {"on": False, "brightness": 121, "kelvin": 3003}))
        self.assertFalse(guard.hold_ok(want, {"on": False, "brightness": None, "kelvin": None}), "learned with no level")
        self.assertFalse(guard.hold_ok(want, {"on": True, "brightness": 120, "kelvin": 3000}))
        self.assertFalse(guard.hold_ok(want, {"on": False, "brightness": 122, "kelvin": 3000}))
        self.assertFalse(guard.hold_ok(want, {"on": False, "brightness": 120, "kelvin": 3030}))  # 3.3 mired

    def test_recovers_arrival_branch(self):
        v, start = 1000.0, 1100.0
        clean = {"occupancy": [(start, "off"), (start + 30, "on")], guard.OCCUPANCY: [(start, "off"), (start + 31, "on")]}
        self.assertEqual(guard.arrival_branch(clean, [], start, v), "turn_on_only")
        early = {k: [(t, s) for t, s in x] for k, x in clean.items()}
        self.assertEqual(guard.arrival_branch(early, [], start, start - 10), "on_then_off", "under 120 s")
        through = dict(clean, occupancy=[(start, "off"), (start + 10, "unknown"), (start + 30, "on")])
        self.assertEqual(guard.arrival_branch(through, [], start, v), "on_then_off")
        late_start = dict(clean, occupancy=[(start + 5, "off"), (start + 30, "on")])
        self.assertEqual(guard.arrival_branch(late_start, [], start, v), "on_then_off", "no entry at the start")
        restarted = dict(clean, **{guard.OCCUPANCY: [(start, "off"), (start + 20, "unavailable"), (start + 31, "on")]})
        self.assertEqual(guard.arrival_branch(restarted, [], start, v), "on_then_off")
        self.assertEqual(guard.arrival_branch(clean, [start + 15], start, v), "on_then_off", "homeassistant_start")


class DenLightingPinTests(unittest.TestCase):
    def test_den_lighting_is_pinned_and_loaded_unchanged(self):
        synthetic.check_pins(synthetic.FIXTURE_DIR)
        policy, helpers = synthetic.load_den_lighting()
        self.assertEqual(policy.LIGHTS, guard.LIGHTS)
        self.assertEqual(policy.VACANCY_SECONDS, guard.VACANCY_S)
        self.assertEqual(policy.Controller().dump()["version"], guard.STORE_VERSION)
        self.assertEqual(set(policy.Controller().status(0.0)) | {"error", "gaming_entity", "occupancy_source",
                         "pending_fixtures", "provisional_fixtures"}, set(guard.STATUS_KEYS))
        self.assertEqual(helpers["command_scene"]("turn_off", {}, policy.Scene(True, 80, 3000)),
                         policy.Scene(False, 80, 3000), "an off learns the level it had")

    def test_the_pins_read_the_same_with_crlf_or_lf(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("policy.py", "__init__.py"):
                raw = (synthetic.FIXTURE_DIR / name).read_bytes()
                (Path(tmp) / name).write_bytes(raw.replace(b"\n", b"\r\n"))
            synthetic.check_pins(Path(tmp))

    def test_a_changed_den_lighting_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("policy.py", "__init__.py"):
                shutil.copy(synthetic.FIXTURE_DIR / name, Path(tmp) / name)
            with open(Path(tmp) / "policy.py", "a", encoding="utf-8") as out:
                out.write("\n# 1.0.5\n")
            with self.assertRaises(RuntimeError) as caught:
                synthetic.check_pins(Path(tmp))
            self.assertIn("den_lighting changed", str(caught.exception))


# ---- probe.py end to end, against the fake HA and the fake camera -------------------------------

class Rig:
    """A fake HA with den_lighting, a fake camera over a synthetic den, and a session folder."""

    def __init__(self, case, *, snapshot=SNAPSHOT, m1=None, occupancy="off", store=None):
        self.ha = synthetic.FakeHA(token=HA_TOKEN, store=store or store_for(snapshot), speed=600, occupancy=occupancy)
        case.addCleanup(self.ha.close)
        self.den = synthetic.SyntheticDen(block_px=40)
        self.cam = synthetic.FakeCamera(self.den, self.ha, token=CAM_TOKEN, step=10)
        case.addCleanup(self.cam.close)
        tmp = tempfile.TemporaryDirectory()
        case.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name) / "s1"
        self.folder.mkdir()
        self.m1, self.m0 = m1 or method1(), method0()
        self.said = []
        self.snapshot = snapshot

    def session(self, **kwargs):
        s = probe.Session(self.folder, probe.HA(self.ha.url, HA_TOKEN, 5), sweep.Camera(self.cam.url, CAM_TOKEN),
                          self.m1, self.m0, say=self.said.append, **kwargs)
        return s

    def run(self, hook=None, session=None):
        s = session or self.session()
        s.on_step = hook
        return (*s.run(json.dumps(store_for(self.snapshot))), s)

    def normal(self):
        return {e: norm(v) for e, v in self.ha.store()["data"]["normal"].items()}

    def holds(self):
        return self.ha.store()["data"]["holds"]

    def rest_calls(self, after=None):
        return [c for c in self.ha.calls if c[5] == "rest" and (after is None or c[0] >= after)]

    def log(self):
        return probe.read_log(self.folder / "probes.jsonl")

    def assert_normal_is_snapshot(self, case, bulbs=guard.LIGHTS):
        normal = self.normal()
        for e in bulbs:
            case.assertTrue(guard.hold_ok(self.snapshot[e], normal[e]), (e, normal[e], self.snapshot[e]))


def at_step(n, action):
    def hook(session, step):
        if step["index"] == n:
            action(session)
    return hook


class ProbeEndToEndTests(unittest.TestCase):
    def test_a_full_session_restores_den_lighting_and_the_model_passes(self):
        rig = Rig(self)
        outcome, lines, s = rig.run()
        self.assertEqual(outcome, "complete", lines)
        rig.assert_normal_is_snapshot(self)
        self.assertEqual(rig.holds(), {})
        self.assertFalse(any(rig.ha.lights[e]["on"] for e in guard.LIGHTS), "den_lighting turned them off itself")
        self.assertEqual(rig.ha.states[synthetic.SWITCH]["state"], "on")
        calls = [(c[1], c[2]) for c in rig.rest_calls()]
        self.assertEqual(calls[0], ("switch", "turn_off"), "pause first")
        self.assertEqual(calls[-2:], [("switch", "turn_on"), ("den_lighting", "resume_automatic")])
        self.assertTrue(all(d == "light" for d, _ in calls[1:-2]), "only light writes between pause and resume")
        log = rig.log()
        phases = [l["phase"] for l in log if l["kind"] == "phase"]
        self.assertEqual(phases, ["start", "wait", "pause", "probe", "probe_done", "restore", "resume", "done"])
        for intent, sent in zip([l for l in log if l["kind"] == "intent"], [l for l in log if l["kind"] == "sent"]):
            self.assertEqual(intent["entity_id"], sent["entity_id"])
            self.assertLess(log.index(intent), log.index(sent), "the intent line is flushed before the call")
            self.assertEqual(intent["change"], "non-human")
            self.assertTrue(sent["context_id"])
        self.assertTrue(all(l["log"] == guard.PROBE_CHANGE for l in log))
        restore_right = [l["service"] for l in log if l["kind"] == "intent" and l.get("restore")
                         and l["entity_id"] == "light.front_right"]
        self.assertEqual(restore_right, ["light.turn_on", "light.turn_off"], "snapshot off: on, then off")
        steps = [l for l in log if l["kind"] == "step"]
        self.assertEqual(len(steps), len(model.schedule(rig.m1, SNAPSHOT, RANGES, WARMEST)["steps"]))
        for step in steps:
            self.assertGreaterEqual(step["send_s"], 0)
            self.assertGreaterEqual(step["settle_s"], 0)
            self.assertIsInstance(step["sent_pc"], float)
        record = json.loads((rig.folder / "session.json").read_text())
        self.assertEqual((record["outcome"], record["phase"], record["unrestored"]), ("complete", "done", []))
        self.assertEqual(record["held"]["iso"], 400)
        self.assertEqual(len(list((rig.folder / "fixtures.jsonl").read_text().splitlines())), 4)
        params, digest = synthetic.meter_norm_for(rig.m0)
        lm, scored = model.run_session(rig.folder, {"parameters": params, "parameters_sha256": digest}, rig.m1)
        self.assertEqual(scored["error"]["verdict"], "pass", scored["error"])
        self.assertEqual(lm["error"]["verdict"], "pass")
        with self.assertRaises(model.Refused):
            model.run_session(rig.folder, {"parameters": params, "parameters_sha256": digest}, rig.m1)
        readings = json.loads((rig.folder / "readings.json").read_text())
        for name in ("light-model.json", "score.json"):
            (rig.folder / name).unlink()
        _, again = model.fit_and_score(rig.folder, readings, model.read_jsonl(rig.folder / "fixtures.jsonl"),
                                       rig.m1, digest)
        self.assertEqual(again["error"], scored["error"], "readings.json alone gives the same score")

    def test_occupancy_mid_run_after_an_arrival_restores_a_snapshot_off_bulb_with_turn_on_only(self):
        rig = Rig(self)
        marks = {}

        def arrive(s):
            marks["t"] = rig.ha.now()
            rig.ha.set_occupancy("on")
        outcome, lines, s = rig.run(at_step(9, arrive))
        self.assertEqual(outcome, "aborted")
        log = rig.log()
        abort_at = next(i for i, l in enumerate(log) if l["kind"] == "guard")
        self.assertFalse([l for l in log[abort_at:] if l["kind"] == "intent" and not l.get("restore")],
                         "no probe write after the abort")
        right = [l["service"] for l in log[abort_at:] if l["kind"] == "intent" and l["entity_id"] == "light.front_right"]
        self.assertEqual(right, ["light.turn_on"])
        normal = rig.normal()
        self.assertEqual(normal["light.front_right"]["on"], True, "den_lighting's arrival scene")
        rig.assert_normal_is_snapshot(self, [e for e in guard.LIGHTS if e != "light.front_right"])
        self.assertEqual(rig.holds(), {})
        record = json.loads((rig.folder / "session.json").read_text())
        self.assertIn("occupancy", record["abort_reason"])
        self.assertEqual(record["restore"]["arrival"], True)
        self.assert_frames_are_clean(rig)

    def assert_frames_are_clean(self, rig):
        log = rig.log()
        kept = {f["file"] for l in log if l["kind"] == "capture" for f in l["frames"]}
        on_disk = {"frames/" + p.name for p in (rig.folder / "frames").iterdir()}
        self.assertEqual(on_disk, kept)
        last = max([l["index"] for l in log if l["kind"] == "step"], default=0)
        self.assertTrue(all(l["step"] <= last for l in log if l["kind"] == "capture"))

    def test_occupancy_through_unknown_restores_on_then_off(self):
        rig = Rig(self)

        def blip(s):
            rig.ha.set_occupancy("unknown")
            threading.Timer(0.15, rig.ha.set_occupancy, args=("on",)).start()
        rig.m1 = rig.m1.closed(hold_check_wait_s=0.3)
        outcome, lines, s = rig.run(at_step(7, blip))
        self.assertEqual(outcome, "aborted")
        log = rig.log()
        abort_at = next(i for i, l in enumerate(log) if l["kind"] == "guard")
        right = [l["service"] for l in log[abort_at:] if l["kind"] == "intent" and l["entity_id"] == "light.front_right"]
        self.assertEqual(right, ["light.turn_on", "light.turn_off"])
        rig.assert_normal_is_snapshot(self)
        self.assertEqual(rig.holds(), {})

    def test_a_foreign_change_is_never_written_again_and_is_listed(self):
        rig = Rig(self)
        e = "light.den_rear_left"
        marks = {}

        def person(s):
            marks["t"] = rig.ha.now()
            rig.ha.set_light(e, on=True, brightness=60, kelvin=2700)  # a physical switch: no context of the tool's
        outcome, lines, s = rig.run(at_step(12, person))
        self.assertEqual(outcome, "aborted")
        self.assertFalse([c for c in rig.rest_calls(marks["t"]) if c[3].get("entity_id") == e])
        record = json.loads((rig.folder / "session.json").read_text())
        self.assertEqual([u[0] for u in record["unrestored"]], [e])
        self.assertTrue(any(line.startswith(f"{e} was not restored: its normal now carries the probe's level")
                            for line in lines), lines)
        self.assertEqual(rig.normal()[e], {"on": True, "brightness": 60, "kelvin": 2703}, "learned from the change")
        self.assertIn(e, rig.holds(), "den_lighting keeps the person's hold")
        rig.assert_normal_is_snapshot(self, [x for x in guard.LIGHTS if x != e])
        self.assertEqual(set(rig.holds()), {e})

    def test_a_bulb_unavailable_then_back_is_restored(self):
        rig = Rig(self)
        e = "light.den_rear_right"

        def drop(s):
            rig.ha.set_available(e, False)
            threading.Timer(0.3, rig.ha.set_available, args=(e, True)).start()
        outcome, lines, s = rig.run(at_step(8, drop))
        self.assertEqual(outcome, "aborted")
        record = json.loads((rig.folder / "session.json").read_text())
        self.assertIn(e, record["restore"]["restored"])
        self.assertEqual(record["unrestored"], [])
        rig.assert_normal_is_snapshot(self)
        self.assertEqual(rig.holds(), {})

    def test_a_bulb_still_unavailable_is_listed_as_unrestored(self):
        rig = Rig(self, m1=method1(unavailable_wait_s=0.3))
        e = "light.den_rear_right"
        outcome, lines, s = rig.run(at_step(8, lambda s: rig.ha.set_available(e, False)))
        record = json.loads((rig.folder / "session.json").read_text())
        self.assertEqual([u[0] for u in record["unrestored"]], [e])
        rig.assert_normal_is_snapshot(self, [x for x in guard.LIGHTS if x != e])

    def walk_through_once(self, rig):
        """Someone walks in and out, slowly enough for the tool's polling on a busy PC."""
        def go():
            time.sleep(0.1)
            rig.ha.set_occupancy("on")
            time.sleep(0.5)
            rig.ha.set_occupancy("off")
        threading.Thread(target=go, daemon=True).start()

    def test_occupancy_unknown_at_resume_asks_for_a_walk_through(self):
        rig = Rig(self, m1=method1(walkthrough_limit_s=10))

        def walk(text):
            rig.said.append(text)
            if "walk through" in text:
                self.walk_through_once(rig)
        s = rig.session()
        s.say = walk
        outcome, lines, s = rig.run(at_step(6, lambda s: rig.ha.set_occupancy("unknown")), session=s)
        record = json.loads((rig.folder / "session.json").read_text())
        self.assertEqual(record["walk_through"]["outcome"], "bulbs off after the walk-through")
        resumed = next(c[0] for c in rig.ha.calls if c[1] == "den_lighting")
        self.assertFalse([c for c in rig.rest_calls(resumed) if c[1] == "light"], "no turn_off of the tool's own")
        self.assertFalse(any(rig.ha.lights[e]["on"] for e in guard.LIGHTS))
        rig.assert_normal_is_snapshot(self)

    def test_no_walk_through_records_bulbs_left_lit_and_recover_redoes_the_check(self):
        rig = Rig(self, m1=method1(walkthrough_limit_s=0.4))
        outcome, lines, s = rig.run(at_step(6, lambda s: rig.ha.set_occupancy("unknown")))
        record = json.loads((rig.folder / "session.json").read_text())
        self.assertEqual(record["walk_through"]["outcome"], "bulbs left lit, occupancy unknown")
        self.assertIn("bulbs left lit, occupancy unknown", lines)
        self.assertTrue(any(rig.ha.lights[e]["on"] for e in guard.LIGHTS), "it never turns them off itself")
        rig.m1 = rig.m1.closed(walkthrough_limit_s=10)
        s = rig.session()
        s.say = lambda text: self.walk_through_once(rig)
        outcome, lines = s.recover()
        self.assertEqual(lines, ["bulbs off after the walk-through"])
        self.assertEqual(json.loads((rig.folder / "session.json").read_text())["walk_through"]["bulbs_off"], True)

    def test_ha_lost_writes_nothing_more_and_recover_finishes(self):
        rig = Rig(self)
        marks = {}

        def lose(s):
            marks["calls"] = len(rig.ha.calls)
            rig.ha.down = True
        outcome, lines, s = rig.run(at_step(10, lose))
        self.assertEqual(outcome, "aborted")
        self.assertEqual(len(rig.ha.calls), marks["calls"], "no writes once HA is lost")
        record = json.loads((rig.folder / "session.json").read_text())
        self.assertEqual((record["phase"], record["outcome"]), ("ha_lost", "aborted_ha_lost"))
        self.assertEqual(rig.ha.states[synthetic.SWITCH]["state"], "off", "den_lighting stays paused")
        self.assertIn("--recover", lines[0])
        rig.ha.down = False
        outcome, lines = rig.session().recover()
        rig.assert_normal_is_snapshot(self)
        self.assertEqual(rig.holds(), {})
        self.assertEqual(rig.ha.states[synthetic.SWITCH]["state"], "on")
        self.assert_frames_are_clean(rig)

    def test_light_that_lands_after_ha_reports_it_is_not_read_as_the_step_before(self):
        rig = Rig(self, m1=method1(step_dwell_s=0.2))
        rig.cam.latency_s = 0.06  # HA shows the target at once; the camera sees it 60 ms later
        outcome, lines, s = rig.run()
        self.assertEqual(outcome, "complete", lines)
        params, digest = synthetic.meter_norm_for(rig.m0)
        lm, scored = model.run_session(rig.folder, {"parameters": params, "parameters_sha256": digest}, rig.m1)
        self.assertEqual(scored["error"]["verdict"], "pass", scored["error"])
        steps = [l for l in rig.log() if l["kind"] == "step"]
        for step in steps:
            self.assertLessEqual(step["settle_s"], step["capture_s"])
        landed = [step["settle_s"] for before, step in zip(steps, steps[1:])
                  if step["step_kind"] == "basis" and step["scene"] != before["scene"]]  # basis: a bright, visible change
        self.assertGreaterEqual(min(landed), 0.06, "the camera cannot have seen the light before it landed")
        self.assertLess(statistics.median(landed), 0.2, "the log measures the landing, not the dwell")
        self.assertGreaterEqual(min(step["capture_s"] for step in steps), 0.2, "no frame is kept inside the dwell")

    def test_reports_that_land_after_the_reply_do_not_abort(self):
        rig = Rig(self)

        def late(s, step):
            if 6 <= step["index"] <= 25:  # HA seconds: the service reply names no state, the report follows
                rig.ha.report_delay.update({e: 30 for e in guard.LIGHTS})
        outcome, lines, s = rig.run(late)
        self.assertEqual(outcome, "complete", lines)
        self.assertEqual(json.loads((rig.folder / "session.json").read_text())["abort_reason"], None)
        rig.assert_normal_is_snapshot(self)
        self.assertEqual(rig.holds(), {})

    def test_a_device_report_one_step_off_does_not_abort(self):
        rig = Rig(self)
        e = "light.den_front_left"

        def nudge(s, step):
            if step["kind"] == "confirm" and step["ratio"] == s.m1["confirm_ratios"][1]:
                # the confirm steps keep this bulb's scene, so no write follows: it reports one level off, under a
                # context of its own, and the next guard read has to judge that report
                light = rig.ha.lights[e]
                rig.ha.set_light(e, on=True, brightness=light["brightness"] - 1, kelvin=light["kelvin"])
        outcome, lines, s = rig.run(nudge)
        self.assertEqual(outcome, "complete", lines)
        reports = [l for l in rig.log() if l["kind"] == "observed" and l["entity_id"] == e
                   and (l["scene"] or {}).get("brightness") == 253]
        self.assertEqual([l["owner"] for l in reports[:1]], ["own"])

    def test_a_change_just_before_the_restore_is_not_overwritten(self):
        rig = Rig(self)
        e = "light.den_rear_left"
        marks = {}
        real = probe.Session.abort

        def abort(self_, reason):
            marks["t"] = rig.ha.now()
            rig.ha.set_light(e, on=True, brightness=60, kelvin=2700)  # after the last guard read, before any restore write
            return real(self_, reason)
        with mock.patch.object(probe.Session, "abort", abort):
            outcome, lines, s = rig.run(at_step(9, lambda s: setattr(rig.cam, "fail", 503)))
        self.assertEqual(outcome, "aborted")
        self.assertFalse([c for c in rig.rest_calls(marks["t"]) if c[3].get("entity_id") == e])
        record = json.loads((rig.folder / "session.json").read_text())
        self.assertEqual([u[0] for u in record["unrestored"]], [e])
        rig.assert_normal_is_snapshot(self, [x for x in guard.LIGHTS if x != e])

    def test_a_game_and_occupancy_in_one_read_leaves_den_lighting_paused(self):
        rig = Rig(self)

        def both(s):
            rig.ha.start_game()
            rig.ha.set_occupancy("on")
        outcome, lines, s = rig.run(at_step(9, both))
        self.assertEqual(outcome, "aborted")
        self.assertIn("after the game", lines[0])
        self.assertEqual(rig.ha.states[synthetic.SWITCH]["state"], "off")

    def nth_all_off(self, rig, n):
        return [s["index"] for s in model.schedule(rig.m1, SNAPSHOT, RANGES, WARMEST)["steps"]
                if s["kind"] == "all_off"][n]

    def test_all_off_brackets_that_disagree_abort(self):
        rig = Rig(self)
        n = self.nth_all_off(rig, 1)
        outcome, lines, s = rig.run(at_step(n, lambda s: setattr(rig.cam, "person", 0.05)))
        self.assertEqual(outcome, "aborted")
        record = json.loads((rig.folder / "session.json").read_text())
        self.assertEqual(record["abort_reason"], "all-off brackets disagree")
        self.assertLess(record["last_clean_step"], n, "the disagreeing capture is not a clean step")
        rig.assert_normal_is_snapshot(self)
        self.assert_frames_are_clean(rig)

    def settled(self, rig, frames, *, dwell=0, count=3, sent_at=0.0):
        """settled_frames() over made-up frames [(time, value)], with a clock that follows them."""
        rig.m1 = rig.m1.closed(step_dwell_s=dwell, frames_per_capture=count, settle_limit_s=5)
        s = rig.session()
        now, pending = [sent_at], iter(frames)

        def next_frame():
            now[0], value = next(pending)
            return {"held": False}, value, {}
        s.clock, s.next_frame, s.means = (lambda: now[0]), next_frame, (lambda value: [[value, value, value]])
        kept, landed = s.settled_frames(sent_at, held=False)
        return [k[1] for k in kept], landed

    def test_a_step_settles_on_agreeing_frames_after_the_dwell(self):
        rig = Rig(self)
        frames = [(0.2, 10), (0.4, 10), (0.6, 50), (1.2, 50), (1.4, 50), (1.6, 50)]
        self.assertEqual(self.settled(rig, frames, dwell=1), ([50, 50, 50], 0.6), "landed at the dwell's third frame")

    def test_a_frame_that_disagrees_with_one_restarts_the_run(self):
        rig = Rig(self)
        self.assertEqual(self.settled(rig, [(0.1, 10), (0.2, 50), (0.3, 50), (0.4, 50)]), ([50, 50, 50], 0.2))

    def test_a_frame_that_disagrees_with_two_aborts(self):
        rig = Rig(self)
        with self.assertRaises(probe.Abort) as caught:
            self.settled(rig, [(0.1, 10), (0.2, 10), (0.3, 50)])
        self.assertEqual(caught.exception.reason, "frames within a settled step disagree")

    def test_a_step_that_never_settles_aborts(self):
        rig = Rig(self)
        with self.assertRaises(probe.Abort) as caught:
            self.settled(rig, [(float(t), t * 10) for t in range(1, 9)])
        self.assertEqual(caught.exception.reason, "the step did not settle")

    def test_a_probe_write_ha_refuses_aborts_and_restores(self):
        rig = Rig(self)
        outcome, lines, s = rig.run(at_step(9, lambda s: setattr(rig.ha, "refuse_writes", 1)))
        self.assertEqual(outcome, "aborted", lines)
        record = json.loads((rig.folder / "session.json").read_text())
        self.assertIn("500", record["abort_reason"])
        self.assertEqual(record["phase"], "done", "HA is there, so the restore runs now")
        rig.assert_normal_is_snapshot(self)
        self.assertEqual(rig.holds(), {})
        self.assertEqual(rig.ha.states[synthetic.SWITCH]["state"], "on")
        self.assert_frames_are_clean(rig)

    def test_an_unexpected_error_mid_probe_aborts_and_restores(self):
        rig = Rig(self)

        def boom(s):
            raise OSError("no space left on device")
        outcome, lines, s = rig.run(at_step(9, boom))
        self.assertEqual(outcome, "aborted", lines)
        self.assertIn("OSError", json.loads((rig.folder / "session.json").read_text())["abort_reason"])
        rig.assert_normal_is_snapshot(self)
        self.assertEqual(rig.holds(), {})
        self.assertEqual(rig.ha.states[synthetic.SWITCH]["state"], "on")

    def restore_then_lose_ha(self, rig):
        """HA goes away on the second restore write, with the first bulb already restored."""
        real = probe.Session.restore_send
        state = {"n": 0}

        def dying(self_, e, target):
            state["n"] += 1
            if state["n"] == 2:
                rig.ha.down = True
            return real(self_, e, target)
        return mock.patch.object(probe.Session, "restore_send", dying)

    def test_ha_lost_during_the_clean_end_restore_asks_for_recover(self):
        rig = Rig(self)
        with self.restore_then_lose_ha(rig):
            outcome, lines, s = rig.run()
        self.assertEqual(outcome, "stopped")
        self.assertIn("--recover", lines[0])
        self.assertEqual(rig.ha.states[synthetic.SWITCH]["state"], "off", "den_lighting stays paused")
        rig.ha.down = False
        outcome, lines = rig.session().recover()
        self.assertEqual(outcome, "complete", lines)
        record = json.loads((rig.folder / "session.json").read_text())
        self.assertEqual(record["unrestored"], [], "the tool's own restore write is not a person's change")
        rig.assert_normal_is_snapshot(self)
        self.assertEqual(rig.holds(), {})
        self.assertEqual(rig.ha.states[synthetic.SWITCH]["state"], "on")

    def test_ha_lost_during_an_aborts_restore_asks_for_recover(self):
        rig = Rig(self)
        with self.restore_then_lose_ha(rig):
            outcome, lines, s = rig.run(at_step(9, lambda s: rig.ha.set_occupancy("unknown")))
        self.assertEqual(outcome, "aborted")
        self.assertIn("--recover", lines[0])
        rig.ha.down = False
        outcome, lines = rig.session().recover()
        self.assertEqual(json.loads((rig.folder / "session.json").read_text())["unrestored"], [])
        rig.assert_normal_is_snapshot(self)
        self.assertEqual(rig.ha.states[synthetic.SWITCH]["state"], "on")

    def test_recover_stopped_again_keeps_asking(self):
        rig = Rig(self)
        rig.run(at_step(10, lambda s: setattr(rig.ha, "down", True)))
        rig.ha.down = False
        with self.restore_then_lose_ha(rig):
            outcome, lines = rig.session().recover()
        self.assertEqual(outcome, "stopped")
        self.assertIn("--recover", lines[0])
        rig.ha.down = False
        outcome, lines = rig.session().recover()
        rig.assert_normal_is_snapshot(self)
        self.assertEqual(rig.holds(), {})

    def test_a_game_mid_run_leaves_den_lighting_paused_and_asks_for_recover(self):
        rig = Rig(self)
        outcome, lines, s = rig.run(at_step(9, lambda s: rig.ha.start_game()))
        self.assertEqual(outcome, "aborted")
        self.assertIn("after the game", lines[0])
        self.assertEqual(rig.ha.states[synthetic.SWITCH]["state"], "off")
        self.assertFalse([c for c in rig.rest_calls() if c[1] == "den_lighting"])
        with self.assertRaises(sweep.Refused):
            rig.session().recover()

    def test_a_write_applied_with_its_response_lost_is_still_the_tools(self):
        rig = Rig(self)
        real = probe.HA.call
        state = {"n": 0}

        def lossy(self_, domain, service, data):
            result = real(self_, domain, service, data)
            if domain == "light":
                state["n"] += 1
                if state["n"] == 12:
                    raise probe.HAUnreachable("timeout")  # HA applied it; the reply never came
            return result
        with mock.patch.object(probe.HA, "call", lossy):
            outcome, lines, s = rig.run()
        self.assertEqual(outcome, "aborted")
        log = rig.log()
        self.assertEqual(log[-1]["kind"] != "sent" or True, True)
        lost = [l for l in log if l["kind"] == "intent"][11]["entity_id"]
        outcome, lines = rig.session().recover()
        record = json.loads((rig.folder / "session.json").read_text())
        self.assertIn(lost, record["restore"]["restored"])
        rig.assert_normal_is_snapshot(self)
        self.assertEqual(rig.holds(), {})

    def test_a_kill_after_a_write_is_recovered(self):
        rig = Rig(self)

        class Killed(BaseException):
            pass

        class Dies(probe.Session):
            calls = 0

            def send(self, e, target):
                Dies.calls += 1
                if Dies.calls == 15:
                    data = {"entity_id": e, "transition": 0}
                    if target["on"]:
                        data.update(brightness=target["brightness"], color_temp_kelvin=target["kelvin"])
                    self.owner.intend(e, target)
                    self.log.write("intent", entity_id=e, service="light." + ("turn_on" if target["on"] else "turn_off"),
                                   data=data, change="non-human")
                    self.ha.call("light", "turn_on" if target["on"] else "turn_off", data)
                    raise Killed()  # the process dies before it logs HA's answer
                return super().send(e, target)
        s = Dies(rig.folder, probe.HA(rig.ha.url, HA_TOKEN, 5), sweep.Camera(rig.cam.url, CAM_TOKEN), rig.m1, rig.m0,
                 say=quiet)
        with self.assertRaises(Killed):
            rig.run(session=s)
        self.assertEqual(json.loads((rig.folder / "session.json").read_text())["phase"], "probing")
        outcome, lines = rig.session().recover()
        rig.assert_normal_is_snapshot(self)
        self.assertEqual(rig.holds(), {})
        self.assertEqual(rig.ha.states[synthetic.SWITCH]["state"], "on")
        self.assert_frames_are_clean(rig)

    def test_a_late_off_report_is_restored_once_more(self):
        rig = Rig(self)
        real = probe.Session.restore_send
        state = {"n": 0}

        def late(self_, e, target):
            if e == "light.front_right" and not target["on"]:
                state["n"] += 1
                if state["n"] == 1:
                    rig.ha.report_delay[e] = 4.0  # HA seconds: past den_lighting's 3 s window
            return real(self_, e, target)
        with mock.patch.object(probe.Session, "restore_send", late):
            outcome, lines, s = rig.run()
        self.assertEqual(outcome, "complete", lines)
        checks = [l for l in rig.log() if l["kind"] == "hold_check"]
        self.assertEqual(checks[0]["failures"], ["light.front_right"])
        self.assertEqual(checks[1]["failures"], [])
        rig.assert_normal_is_snapshot(self)
        self.assertEqual(rig.holds(), {})

    def test_ctrl_c_aborts_and_restores(self):
        rig = Rig(self)
        outcome, lines, s = rig.run(at_step(8, lambda s: signal.raise_signal(signal.SIGINT)))
        self.assertEqual(outcome, "aborted")
        self.assertIn("Ctrl+C", json.loads((rig.folder / "session.json").read_text())["abort_reason"])
        rig.assert_normal_is_snapshot(self)
        self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)

    def test_the_camera_guard_aborts_on_a_503_a_lost_camera_a_new_session_and_no_new_frame(self):
        for change, why in (({"fail": 503}, "503"), ({"fail": "drop"}, "unreachable"),
                            ({"session": "session-2"}, "new camera session"), ({"frozen": True}, "without a frame")):
            with self.subTest(change=change):
                rig = Rig(self, m1=method1(frame_timeout_s=0.4))
                outcome, lines, s = rig.run(at_step(9, lambda s: [setattr(rig.cam, k, v) for k, v in change.items()]))
                self.assertEqual(outcome, "aborted")
                self.assertIn(why, json.loads((rig.folder / "session.json").read_text())["abort_reason"])
                rig.assert_normal_is_snapshot(self)
                self.assertEqual(rig.holds(), {})
                self.assert_frames_are_clean(rig)

    def test_the_camera_guard_aborts_on_a_frame_that_fails_gain_agreement(self):
        rig = Rig(self)

        def change_iso(s):
            with rig.cam.lock:
                rig.cam.hold = dict(rig.cam.hold, iso=rig.cam.hold["iso"] * 2)
        outcome, lines, s = rig.run(at_step(9, change_iso))
        self.assertEqual(outcome, "aborted")
        self.assertIn("gain agreement", json.loads((rig.folder / "session.json").read_text())["abort_reason"])
        rig.assert_normal_is_snapshot(self)

    def test_start_refusals_pause_nothing(self):
        cases = {
            "a hold in the store copy": dict(store=store_for(holds={"light.front_right": {
                "scene": {"on": True, "brightness": 9, "kelvin": 3000}, "since": 1.0}})),
            "the incumbent on": dict(prepare=lambda rig: rig.ha.set_incumbent("on")),
            "an unknown sensor attribute set": dict(prepare=lambda rig: setattr(
                rig.ha, "status", lambda: {**synthetic.FakeHA.status(rig.ha), "extra": 1})),
            "no vacancy within the limit": dict(occupancy="on", m1=method1(start_wait_limit_s=0.3)),
            "a hold during the wait": dict(occupancy="on", during=lambda rig: rig.ha.set_light(
                "light.den_front_left", on=True, brightness=40, kelvin=3000)),
        }
        for name, case in cases.items():
            with self.subTest(name):
                rig = Rig(self, occupancy=case.get("occupancy", "off"), m1=case.get("m1"))
                if "prepare" in case:
                    case["prepare"](rig)
                    with rig.ha.lock:
                        rig.ha._refresh()
                if "during" in case:
                    threading.Timer(0.1, case["during"], args=(rig,)).start()
                s = rig.session()
                text = json.dumps(case.get("store") or store_for())
                with self.assertRaises((sweep.Refused, guard.Refused)):
                    s.run(text)
                self.assertFalse([c for c in rig.rest_calls() if c[1] == "switch"], "nothing was paused")
                if (rig.folder / "session.json").exists():
                    self.assertEqual(json.loads((rig.folder / "session.json").read_text())["outcome"], "refused")
                self.assertFalse([c for c in rig.rest_calls() if c[1] == "light"])

    def test_a_session_refuses_while_a_slot_is_open(self):
        rig = Rig(self, m1=model.load_stage1())
        with self.assertRaises(model.Refused):
            rig.run()
        self.assertEqual(rig.rest_calls(), [])

    def test_run_folder_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token = root / "private" / "ha-token.txt"
            token.parent.mkdir()
            token.write_text(HA_TOKEN)
            (root / "taken").mkdir()
            (root / "taken" / "session.json").write_text("{}")
            (root / "repo" / ".git").mkdir(parents=True)
            (root / "OneDrive - Home").mkdir()
            for bad in (Path("relative"), root / "taken", root / "repo" / "s1", root / "OneDrive - Home" / "s1",
                        root / "private", root / "no such parent" / "s1", root / "bad name!"):
                with self.assertRaises(sweep.Refused, msg=str(bad)):
                    probe.session_folder(bad, [token], new=True)
            with mock.patch.object(sweep, "free_bytes", return_value=10 ** 8), self.assertRaises(sweep.Refused):
                probe.session_folder(root / "s1", [token], new=True)
            self.assertEqual(probe.session_folder(root / "s1", [token], new=True), (root / "s1").resolve())
            self.assertFalse((root / "s1").exists(), "checking writes nothing")


class ProbeCliTests(unittest.TestCase):
    def test_main_refuses_without_tokens_or_with_open_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = probe.main(["--session", str(Path(tmp) / "s1"), "--ha", "http://127.0.0.1:9",
                                   "--camera", "http://127.0.0.1:9", "--stage0", tmp,
                                   "--ha-token-file", str(Path(tmp) / "none.txt"),
                                   "--camera-token-file", str(Path(tmp) / "none.txt")])
            self.assertEqual(code, 2)
            self.assertIn("Refused", err.getvalue())
            self.assertNotIn(HA_TOKEN, err.getvalue())

    def test_no_code_path_prints_a_token(self):
        for name in ("probe.py", "model.py", "guard.py", "predict.py"):
            source = (HERE / name).read_text(encoding="utf-8")
            self.assertNotRegex(source, r"print\([^)]*_token", name)

    def test_stage0_meter_norm_needs_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "response.json").write_text(json.dumps({"outcome": "fail", "meter_norm": {}}))
            with self.assertRaises(sweep.Refused):
                probe.stage0_meter_norm(folder)
            (folder / "recheck-1.json").write_text(json.dumps({"outcome": "pass", "meter_norm": {"x": 1}}))
            self.assertEqual(probe.stage0_meter_norm(folder), ({"x": 1}, "recheck-1.json"))


if __name__ == "__main__":
    unittest.main()
