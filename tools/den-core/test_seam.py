"""The seam between den-core, den-light and den-verify, as the lighting contract fixes it. From the worktree root:

python -B -m unittest discover -s tools/den-core -p "test_*.py"

A class that needs den-light's own predict.py skips on a branch without it, saying so; set DEN_LIGHT_DIR to its folder,
or run on the merged tree, where it is the sibling. Everything here is synthetic. Nothing reaches a host or a light.
"""
import ast
import copy
import hashlib
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check  # noqa: E402
import coordinator  # noqa: E402
import events  # noqa: E402
import fake_light  # noqa: E402
import hold_fixtures  # noqa: E402
import incumbent  # noqa: E402
import light_adapter  # noqa: E402
import run  # noqa: E402
import score  # noqa: E402
import sources  # noqa: E402
import synthetic  # noqa: E402
import test_coordinator as tc  # noqa: E402
import test_score  # noqa: E402
import test_spine  # noqa: E402
from hold_fixtures import T0, SLOTS, scene  # noqa: E402
from test_score import closed_gate, real_backend, real_model, replay_log  # noqa: E402

FL, FR, RL, RR = events.LIGHTS
HAVE_POLICY = incumbent.find_policy() is not None
NO_SIBLING = HERE / "no-such-folder" / "predict.py"
REAL_PREDICT = test_score.real_predict()
NEEDS_REAL = ("needs den-light's predict.py, which is on its own branch until they merge: these tests run on the merged tree, "
              "or set DEN_LIGHT_DIR to its folder")
GATE = {"id": "test", "sha256": None, "slots": SLOTS, "open": []}


def no_sibling():
    return mock.patch.object(light_adapter, "PREDICT_PATH", NO_SIBLING)


def stage1_shaped(excluded=(), **params):
    """The fake's numbers in the shape Stage 1 writes: no `fake`, no `surfaces`, a block the fit excluded flagged on its
    sample point and None in every basis row."""
    model = fake_light.build(surfaces=False, **params)
    del model["fake"]
    for point in model["sample_points"]:
        point["excluded"] = "mask" if point["point_id"] in excluded else None
    for row in model["basis"].values():
        row.update({p: None for p in excluded})
    return model


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def write_model(self, model, name="light-model.json"):
        path = self.tmp / name
        path.write_text(json.dumps(model), encoding="utf-8")
        return path


class BackendTest(Temp):
    """Which code a model's numbers come from, and that nothing can lie about it."""

    def test_a_model_marked_fake_goes_through_fake_light_beside_a_real_predict(self):
        with real_backend(), mock.patch.object(fake_light, "predict", wraps=fake_light.predict) as fake:
            light = light_adapter.LightModel(fake_light.build())
            light.predict({FL: scene(180)})
        self.assertTrue(light.fake)
        self.assertGreater(fake.call_count, 0)
        self.assertIsNone(light.predict_sha256)
        self.assertIn("marked fake", light.backend)

    def test_a_model_not_marked_fake_goes_through_predict_py(self):
        with real_backend(), mock.patch.object(light_adapter, "_predict", wraps=light_adapter._predict) as call:
            light = light_adapter.LightModel(stage1_shaped())
            light.predict({FL: scene(180)})
        self.assertFalse(light.fake)
        self.assertEqual(light.predict_sha256, events.sha256_file(light.module.__file__))
        self.assertGreater(call.call_count, 0)
        self.assertNotIn("fake_light", light.backend)

    def test_predict_py_is_loaded_by_file_path_under_a_private_name(self):
        before = list(sys.path)
        with real_backend():
            light = light_adapter.LightModel(stage1_shaped())
            where = light_adapter.PREDICT_PATH
        self.assertEqual(sys.path, before)
        self.assertTrue(light.module.__name__.startswith("_"), light.module.__name__)
        self.assertNotIn("predict", sys.modules)
        self.assertEqual(Path(light.module.__file__).resolve(), Path(where).resolve())

    def test_a_run_reads_the_model_once_through_prepare(self):
        with real_backend():
            module, _ = light_adapter._real_predict()
            with mock.patch.object(module, "prepare", wraps=module.prepare) as prepare:
                light = light_adapter.LightModel(stage1_shaped())
                for b in (60, 120, 180):
                    light.predict({FL: scene(b), RR: scene(b)})
        # predict() hands prepare() what it was given, which is a no-op on prepared tables: only the file's dict costs a read.
        self.assertEqual(sum(isinstance(call.args[0], dict) for call in prepare.call_args_list), 1)

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_no_sibling_is_labelled_fake_run_still_works_and_score_says_why(self):
        gate = closed_gate(self.tmp)
        with no_sibling():
            path, header, log = replay_log(self.tmp, gate, real_model(self.tmp))
            self.assertTrue(check.check(path)["ok"])
            lines, code = score.report(path, gate_path=gate)
            preview, _ = score.report(path, preview=True, gate_path=gate)
        entry = next(p for p in header["policies"] if p["module"] == "coordinator")["config"]["light_model"]
        self.assertEqual((entry["fake"], entry["predict_sha256"]), (True, None))
        self.assertIn("no tools/den-light/predict.py", entry["backend"])
        self.assertEqual(code, 2)
        self.assertIn("no tools/den-light/predict.py", "\n".join(lines))
        self.assertIn("plumbing only, never a gate", "\n".join(lines))
        self.assertIn("backend fake_light", "\n".join(preview))
        self.assertTrue([e for e in log if e["kind"] == "decision" and e["source"] == "decide"])

    def test_an_environment_variable_cannot_redirect_the_backend(self):
        with no_sibling(), mock.patch.dict(os.environ, {"DEN_LIGHT_DIR": str(test_score.STAND_IN.parent), "DEN_LIGHT": str(test_score.STAND_IN)}):
            self.assertTrue(light_adapter.LightModel(stage1_shaped()).fake)
        for name in ("light_adapter", "coordinator", "fake_light", "run", "score", "check", "validate", "sources", "events", "runner"):
            tree = ast.parse((HERE / (name + ".py")).read_text(encoding="utf-8"))
            imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            self.assertNotIn("os", imports, name)
            self.assertFalse([n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in ("environ", "getenv")], name)

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_the_real_code_is_pinned_and_check_refuses_when_it_changes(self):
        copy = self.tmp / "predict.py"
        shutil.copyfile(test_score.STAND_IN, copy)
        gate = closed_gate(self.tmp)
        with mock.patch.object(light_adapter, "PREDICT_PATH", copy):
            path, header, _ = replay_log(self.tmp, gate, real_model(self.tmp))
            entry = next(p for p in header["policies"] if p["module"] == "coordinator")["config"]["light_model"]
            self.assertEqual(entry["predict_sha256"], events.sha256_file(copy))
            self.assertFalse(entry["fake"])
            self.assertTrue(check.check(path)["ok"])
            with copy.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("# a change\n")
            result = check.check(path)
            lines, code = score.report(path, gate_path=gate)
        self.assertFalse(result["ok"])
        self.assertIn("code changed: den-light predict.py", result["reason"])
        self.assertEqual(code, 2)
        self.assertIn("code changed: den-light predict.py", "\n".join(lines))

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_a_log_from_a_checkout_with_no_sibling_refuses_once_there_is_one(self):
        with no_sibling():
            path, _, _ = replay_log(self.tmp, closed_gate(self.tmp), real_model(self.tmp))
        with real_backend():
            result = check.check(path)
        self.assertFalse(result["ok"])
        self.assertIn("code changed: den-light predict.py", result["reason"])

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_score_names_the_backend_truthfully(self):
        gate = closed_gate(self.tmp)
        with real_backend():
            path, _, _ = replay_log(self.tmp, gate, real_model(self.tmp), touch=True)
            lines, code = score.report(path, gate_path=gate)
        text = "\n".join(lines)
        self.assertEqual(code, 0, text)
        line = next(row for row in lines if row.startswith("both arms"))
        self.assertIn("real, unit lx; backend den-light predict.py", line)
        self.assertNotIn("fake_light", line)
        self.assertIn("verdict: PASS", text)

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_a_fake_marked_file_stays_refused_beside_a_real_predict(self):
        gate = closed_gate(self.tmp)
        with real_backend():
            path, _, _ = replay_log(self.tmp, gate, self.write_model(fake_light.build()))
            lines, code = score.report(path, gate_path=gate)
        self.assertEqual(code, 2)
        self.assertIn("fake light model: plumbing only, never a gate", "\n".join(lines))

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_what_remains_of_a_refusal_on_a_real_model_is_named(self):
        gate = coordinator.load_gate(closed_gate(self.tmp))
        gate["slots"]["stability_floor"].update(status="open", value=None)
        gate["slots"]["surface_map"].update(status="closed", value={})
        path = self.tmp / "open.json"
        path.write_text(json.dumps(gate), encoding="utf-8")
        with real_backend():
            log_path, _, _ = replay_log(self.tmp, path, real_model(self.tmp, surfaces=False))
            lines, code = score.report(log_path, gate_path=path)
        text = "\n".join(lines)
        self.assertEqual(code, 2)
        self.assertNotIn("fake light model", text)
        self.assertIn("stability_floor", text)
        self.assertIn("surface_map names no 'room'", text)

    def test_predict_py_has_no_aim_so_a_spot_model_is_refused_rather_than_ignored(self):
        spot = {FR: {"near": {"counter-1": 1.0, "armchair-book": 1.0, "wall-west": 1.0}}}
        with real_backend(), self.assertRaisesRegex(ValueError, "spot_model"):
            light_adapter.LightModel(stage1_shaped(spot=spot))
        with no_sibling():  # the fake can evaluate an aim, and is labelled as what it is
            self.assertTrue(light_adapter.LightModel(stage1_shaped(spot=spot)).fake)

    def test_a_predict_py_that_will_not_load_is_a_sentence(self):
        broken = self.tmp / "predict.py"
        for text in ("def broken(:\n", "raise KeyError('x')\n", "def predict(scene, model):\n    return {}\n"):
            broken.write_text(text, encoding="utf-8")
            with self.subTest(text), mock.patch.object(light_adapter, "PREDICT_PATH", broken), \
                    self.assertRaisesRegex(ValueError, "cannot be loaded"):
                light_adapter.LightModel(stage1_shaped())


class SurfaceRuleTest(unittest.TestCase):
    """A block the fit excluded predicts None, and a surface is the mean over its blocks that have a value, None if none do:
    off bulbs included, or a wholly excluded surface would read 0 until a bulb came on."""
    GONE = ["counter-1/%d" % n for n in range(4)]
    SOME = GONE[:2]
    MAP = {"counter-1": GONE, "half": ["armchair-book/0", "armchair-book/1", "wall-west/0", "wall-west/1"]}
    OFF = {"on": False, "brightness": None, "kelvin": None}

    def setUp(self):
        patcher = real_backend()
        patcher.start()
        self.addCleanup(patcher.stop)

    def light(self, excluded):
        return light_adapter.LightModel(stage1_shaped(excluded=excluded), surfaces=self.MAP)

    def test_a_wholly_excluded_surface_reads_no_prediction_not_zero(self):
        light = self.light(self.GONE)
        for name, whole in (("all off", {b: self.OFF for b in events.LIGHTS}), ("lit", {b: scene(180) for b in events.LIGHTS}),
                            ("nothing", {}), ("one off", {FL: scene(180), FR: self.OFF})):
            with self.subTest(name):
                self.assertIsNone(light.predict(whole)["counter-1"])
                self.assertIsNotNone(light.predict(whole)["half"])

    def test_a_partly_excluded_surface_is_the_mean_of_the_blocks_with_a_value(self):
        light = self.light(self.SOME)
        whole = {b: scene(150 + 20 * n) for n, b in enumerate(events.LIGHTS)}
        at_blocks = light.module.predict({light.keys[b]: {"level": s["brightness"] / 254, "cct_k": 2700} for b, s in whole.items()},
                                         light._prepared)["surfaces"]
        kept = [at_blocks[p] for p in self.GONE if at_blocks[p] is not None]
        self.assertEqual(len(kept), 2)
        self.assertAlmostEqual(light.predict(whole)["counter-1"], sum(kept) / 2, places=9)

    def test_a_patch_says_none_at_an_excluded_block_off_bulbs_included(self):
        light = self.light(self.SOME)
        self.assertEqual(light.patch({FL: self.OFF}, "counter-1"), [None, None, 0.0, 0.0])
        lit = light.patch({FL: scene(254)}, "counter-1")
        self.assertEqual(lit[:2], [None, None])
        self.assertGreater(lit[2], 0.0)


@unittest.skipUnless(REAL_PREDICT, NEEDS_REAL)
class RealPredictTest(Temp):
    """den-light's own predict.py behind the adapter."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(light_adapter, "PREDICT_PATH", REAL_PREDICT)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_it_is_the_real_file_with_the_model_prepared(self):
        light = light_adapter.LightModel(stage1_shaped())
        self.assertEqual(Path(light.module.__file__).resolve(), REAL_PREDICT.resolve())
        self.assertEqual(light.predict_sha256, events.sha256_file(REAL_PREDICT))
        self.assertTrue(hasattr(light._prepared, "channels"))  # prepare()'s tables, not the file's dict

    def test_its_numbers_match_the_fakes_on_the_fakes_own_shape(self):
        real = light_adapter.LightModel(stage1_shaped(filler=20))
        fake = light_adapter.LightModel(fake_light.build(surfaces=False, filler=20))
        self.assertFalse(real.fake)
        self.assertTrue(fake.fake)
        rng = random.Random(9)
        for _ in range(200):
            whole = {}
            for b in events.LIGHTS:
                on = rng.random() > 0.2
                whole[b] = {"on": on, "brightness": rng.randint(1, 255) if on else None, "kelvin": rng.randint(1800, 7000) if on else None}
            self.assertEqual(real.predict(whole), fake.predict(whole))
            for b, one in whole.items():
                self.assertEqual(real.row(b, one), fake.row(b, one))

    def test_an_excluded_block_is_none_and_the_surface_means_the_rest(self):
        gone = ["counter-1/%d" % n for n in range(4)]
        light = light_adapter.LightModel(stage1_shaped(excluded=gone[:1]), surfaces={"counter-1": gone})
        whole = {b: scene(200) for b in events.LIGHTS}
        direct = light.module.predict({light.keys[b]: {"level": 200 / 254, "cct_k": 2700} for b in whole}, light._prepared)["surfaces"]
        self.assertIsNone(direct[gone[0]])
        self.assertAlmostEqual(light.predict(whole)["counter-1"], sum(direct[p] for p in gone[1:]) / 3, places=9)

    def test_each_decision_is_under_100_ms_on_the_proposed_grid(self):
        path = self.write_model(stage1_shaped(filler=576 - 12))
        surfaces = {"counter-1": ["counter-1/%d" % n for n in range(4)] + ["other/%d" % n for n in range(140)],
                    "armchair-book": ["armchair-book/%d" % n for n in range(4)] + ["other/%d" % n for n in range(140, 280)]}

        def one_round():
            p = coordinator.make_policy({"gate": GATE, "compensation": {"option": "P2", "form": "strict"},
                                         "light_model": {"source": "file", "path": str(path), "surfaces": surfaces}})
            self.assertFalse(p.light.fake)
            tc.start(p)
            spans = []
            items = [tc.ps(T0 + 10), tc.ps(T0 + 12, "B", "engaged", "armchair-book"), *tc.touch(T0 + 30, FL, 80),
                     *tc.touch(T0 + 42, RR, 240), tc.ps(T0 + 60, "A", "none")]
            for item in items:
                begin = time.perf_counter()
                out = p.handle(item)
                spans.append((time.perf_counter() - begin, bool(out)))
            return spans

        rounds = [one_round() for _ in range(3)]
        self.assertGreaterEqual(sum(made for _, made in rounds[0]), 2)
        fastest = [min(r[n][0] for r in rounds) for n in range(len(rounds[0]))]
        self.assertLess(max(fastest), 0.1, "slowest %.1f ms" % (max(fastest) * 1000))

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_a_scored_run_on_it_reaches_a_verdict(self):
        gate = closed_gate(self.tmp)
        path, _, _ = replay_log(self.tmp, gate, real_model(self.tmp), touch=True)
        lines, code = score.report(path, gate_path=gate)
        text = "\n".join(lines)
        self.assertEqual(code, 0, text)
        self.assertIn("verdict: PASS", text)
        self.assertTrue(check.check(path)["ok"])


class CanonFieldsTest(unittest.TestCase):
    """A canon reader reads level and cct_k. Before these fields it saw every lit bulb at level 0."""
    NO_LEVEL = {"on": True, "brightness": None, "kelvin": None}
    OFF = {"on": False, "brightness": None, "kelvin": None}

    def decided(self, seen):
        """A Decision after bulbs seen as {bulb: scene}, the rest never seen."""
        p = tc.policy()
        for b, one in seen.items():
            p.handle(tc.ev(T0, "light_observed", {"entity_id": b, **one, "context": "unknown"}))
        p.handle(tc.mode())
        p.handle(tc.ev(T0 + 1, "occupancy", {"value": "on", "via": "t"}))
        return p, tc.feed(p, tc.ps(T0 + 10))[-1]

    def test_every_lit_bulb_reads_its_real_level_through_canon_names(self):
        _, d = self.decided({b: scene(60 + 40 * n, 2700 + 100 * n) for n, b in enumerate(events.LIGHTS)})
        for n, b in enumerate(events.LIGHTS):
            self.assertEqual(d["inputs"]["actuator_state"][b],
                             {**scene(60 + 40 * n, 2700 + 100 * n), "level": (60 + 40 * n) / 254, "cct_k": 2700 + 100 * n})

    def test_a_never_seen_bulb_stays_null(self):
        _, d = self.decided({FL: scene(180)})
        self.assertEqual({b: d["inputs"]["actuator_state"][b] for b in (FR, RL, RR)}, {FR: None, RL: None, RR: None})
        self.assertEqual(d["inputs"]["actuator_state"][FL]["level"], 180 / 254)

    def test_an_on_bulb_with_no_brightness_has_level_null_not_0(self):
        _, d = self.decided({FL: self.NO_LEVEL, FR: {"on": True, "brightness": None, "kelvin": 3000}})
        self.assertEqual(d["inputs"]["actuator_state"][FL], {**self.NO_LEVEL, "level": None, "cct_k": None})
        self.assertEqual(d["inputs"]["actuator_state"][FR], {"on": True, "brightness": None, "kelvin": 3000, "level": None, "cct_k": 3000})

    def test_an_off_bulb_is_level_0(self):
        _, d = self.decided({FL: self.OFF})
        self.assertEqual(d["inputs"]["actuator_state"][FL], {**self.OFF, "level": 0.0, "cct_k": None})

    def test_full_is_a_share_of_hues_254(self):
        _, d = self.decided({FL: scene(255)})
        self.assertEqual(d["inputs"]["actuator_state"][FL]["level"], 1.0)

    def test_requested_scene_carries_level_and_cct_k_beside_the_hue_keys(self):
        _, d = self.decided({b: scene(180) for b in events.LIGHTS})
        for b, one in d["requested_scene"].items():
            self.assertEqual(one, {**scene(200), "level": 200 / 254, "cct_k": 2700}, b)

    def test_a_requested_off_is_level_0_and_a_requested_aim_stays(self):
        p = tc.policy()
        tc.start(p)
        tc.feed(p, tc.ps(T0 + 10))
        off = tc.feed(p, tc.ev(T0 + 20, "occupancy", {"value": "off", "via": "t"}), tc.ev(T0 + 200, "tick", {}, "runner"))[-1]
        self.assertEqual(off["requested_scene"][FL], {**self.OFF, "level": 0.0, "cct_k": None})
        spot = {FR: {"near": {"counter-1": 900.0, "armchair-book": 0.0, "wall-west": 0.0},
                     "far": {"counter-1": 0.0, "armchair-book": 0.0, "wall-west": 900.0}}}
        p = tc.policy(light_model={"source": "fake", "params": {"spot": spot}}, compensation={"option": "P0"})
        tc.start(p)
        tc.feed(p, tc.ps(T0 + 10))
        [d] = tc.feed(p, *tc.touch(T0 + 30, FL, 10))
        self.assertEqual(d["requested_scene"][FR]["aim"], "near")
        self.assertEqual(d["requested_scene"][FR]["level"], d["requested_scene"][FR]["brightness"] / 254)

    def held(self, pinned):
        p = tc.policy()
        tc.start(p)
        tc.feed(p, tc.ps(T0 + 10))
        hold = {"entity_id": FL, "pinned": pinned, "since": T0 + 30, "lifetime": "room_vacant_2_minutes"}
        out = tc.feed(p, tc.ev(T0 + 30, "mode", tc.mode(2, [hold])["data"], "incumbent"),
                      tc.ev(T0 + 30, "correction", {"correction_id": "c", "entity_id": FL, "after": pinned, "source": "t"}))
        return p, out[-1], hold

    def test_the_logged_pin_carries_level_pct_cct_k_and_its_source(self):
        _, d, _ = self.held(scene(80))
        [entry] = d["inputs"]["hold_set"]["held"]
        self.assertEqual(entry["pinned"], {**scene(80), "level_pct": 100 * 80 / 254, "cct_k": 2700, "source": "read-back"})
        self.assertEqual(d["requested_scene"][FL], {**scene(80), "level": 80 / 254, "cct_k": 2700})

    def test_a_pin_with_no_level_is_null_and_an_off_pin_is_0(self):
        _, d, _ = self.held(self.NO_LEVEL)
        self.assertEqual(d["inputs"]["hold_set"]["held"][0]["pinned"], {**self.NO_LEVEL, "level_pct": None, "cct_k": None, "source": "read-back"})
        _, d, _ = self.held(self.OFF)
        self.assertEqual(d["inputs"]["hold_set"]["held"][0]["pinned"], {**self.OFF, "level_pct": 0.0, "cct_k": None, "source": "read-back"})

    def test_the_modes_own_pin_does_not_change(self):
        p, _, hold = self.held(scene(80))
        self.assertEqual(p.state["mode"]["holds"][0]["pinned"], scene(80))
        self.assertEqual(hold["pinned"], scene(80))
        self.assertEqual(p.refs[FL]["pinned"], scene(80))

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_only_the_coordinators_records_carry_them(self):
        header, log = hold_fixtures.run_case("V6", "P1-mode")
        modes = [e["data"] for e in log if e["kind"] == "mode" and e["data"]["holds"]]
        self.assertTrue(modes)
        for data in modes:
            for hold in data["holds"]:
                self.assertEqual(set(hold["pinned"]), {"on", "brightness", "kelvin"})
        for e in log:
            if e["kind"] == "decision" and e["source"] == "incumbent":
                self.assertNotIn("level", events.dumps(e["data"]["requested_scene"]))


def before_this_seam(data):
    """A coordinator Decision without what this round added, and without its config hash, which moves with the code."""
    out = copy.deepcopy(data)
    inputs = out["inputs"]
    for key in ("config_sha256", "light_state", "light_state_ts", "light_state_status"):
        inputs.pop(key, None)
    for one in [*inputs["actuator_state"].values(), *out["requested_scene"].values()]:
        if one:
            del one["level"], one["cct_k"]
    for hold in inputs["hold_set"]["held"]:
        del hold["pinned"]["level_pct"], hold["pinned"]["cct_k"], hold["pinned"]["source"]
    return out


def choice_digest(rows):
    return hashlib.sha256("\n".join(events.dumps(before_this_seam(d)) for d in rows).encode("ascii")).hexdigest()


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class ChoiceOfSceneTest(Temp):
    """No hold, solve or glare behaviour moved: everything a Decision said at 19502c47, the head before these fields, it still
    says. Measured there on the fake model, with these same fields left out."""
    BEFORE = {
        (): "e1b3fef68dc7b34bdccb1dc9f5798245595c1d84f1e12f2534aea353548f7d53",
        ("--touch",): "fdf33de10583339d6ed3f1783d70613101e71dd0b615492b2f54ad51138b13e1",
        ("--touch", "--compensation", "P0"): "2b0760df649d78ccaf861294d08d598410c79784b753d1bcc85265f9934cc845",
        ("--touch", "--compensation", "P2", "--compensation-form", "strict"): "73481faaadd19a92803d882f29bb274ce5e59da051b11ec9b1b79b5735f99d65",
        ("--touch", "--compensation", "P3", "--compensation-form", "mode"): "242509b6cd83b55970048c784c287dd7c6a4adcd8fcf7dd7b88f0654965e4148",
        ("--game",): "062ecf93a5006ef1bc388254087866213fbad3d67d160a53a3a93607fd25475c",
        ("--touch", "--game"): "b6a811ce4e802a618dedc8eb84a29fd3fe35ebc4a8f2ab2d52f0f35fe6e03825",
    }
    FIXTURES = "ada76d0ccd4e1fdeff435da244e9d95b9136614661a9a1b3431376e4f98d957e"

    def test_the_evenings_decide_what_they_decided(self):
        for n, (flags, sha) in enumerate(self.BEFORE.items()):
            with self.subTest(flags=flags), mock.patch("builtins.print"):
                run.main(["--synthetic", "evening", *flags, "--policies", "need", "incumbent", "coordinator", "--out", str(self.tmp / str(n))])
                _, log = events.read(self.tmp / str(n) / "log.jsonl")
                self.assertEqual(choice_digest([e["data"] for e in log if e["kind"] == "decision" and e["source"] == "decide"]), sha)

    def test_every_hold_fixture_under_every_option_decides_what_it_decided(self):
        rows = []
        for case in sorted(hold_fixtures.CASES):
            for option in sorted(hold_fixtures.OPTIONS):
                rows += hold_fixtures.decisions(hold_fixtures.run_case(case, option)[1])
        self.assertEqual(choice_digest(rows), self.FIXTURES)


def light_state(ts, **extra):
    """One LightState as den-photometry writes it to light_state.jsonl: no id, and ts present."""
    return {"record": "light_state", "contract": "live-v1", "ts": ts, "camera_id": "den-oak", "room_version": "den-v1",
            "exposure": {"time_ms": 10.0, "gain": 1.0, "black_level": None, "wb_locked": True, "linear": True,
                         "normalisation_version": "meter-norm/v1"},
            "anchored": False, "surface_estimates": [{"surface_id": "counter-1", "value": 0.5, "unit": "relative", "clipped_frac": 0.0,
                                                      "rho_status": "measured", "basis": "measured"}],
            "window_daylight": [], "sun": None, "blinds": "unknown", "residual": [], **extra}


def frame(ts, **extra):
    return {"id": extra.get("id", "ls-%.3f" % ts), "ts": ts}


def without_light_state(decision):
    return {**decision, "inputs": {k: v for k, v in decision["inputs"].items() if not k.startswith("light_state")}}


def coordinator_decisions(log):
    return [e["data"] for e in log if e["kind"] == "decision" and e["source"] == "decide"]


class FrameFieldsTest(unittest.TestCase):
    """inputs.light_state: the newest frame at or before a Decision's ts, never one after it."""

    def decided(self, frames=None, at=T0 + 10):
        p = tc.policy(**({} if frames is None else {"light_states": frames}))
        tc.start(p)
        [d] = tc.feed(p, tc.ps(at))
        return d["inputs"]

    def test_the_newest_frame_at_or_before(self):
        inputs = self.decided([frame(T0 - 10), frame(T0 + 7), frame(T0 + 12)])
        self.assertEqual((inputs["light_state"], inputs["light_state_ts"]), ("ls-%.3f" % (T0 + 7), T0 + 7))
        self.assertNotIn("light_state_status", inputs)

    def test_a_frame_exactly_at_the_decisions_ts(self):
        inputs = self.decided([frame(T0 + 7), frame(T0 + 10), frame(T0 + 10.001)])
        self.assertEqual((inputs["light_state"], inputs["light_state_ts"]), ("ls-%.3f" % (T0 + 10), T0 + 10))
        self.assertNotIn("light_state_status", inputs)

    def test_a_frames_own_id_is_used(self):
        self.assertEqual(self.decided([{"id": "frame-7", "ts": T0 + 7}])["light_state"], "frame-7")

    def test_a_frame_between_the_plan_and_the_send_is_not_named(self):
        p = tc.policy(send_latency_s=5, light_states=[frame(T0 + 7), frame(T0 + 12)])
        tc.start(p)
        tc.feed(p, tc.ps(T0 + 10))
        [d] = tc.feed(p, tc.ev(T0 + 15, "tick", {}, "runner"))
        self.assertEqual((d["ts"], d["inputs"]["light_state_ts"]), (T0 + 10, T0 + 7))

    def test_none_before_the_decision(self):
        inputs = self.decided([frame(T0 + 10.001), frame(T0 + 20)])
        self.assertEqual((inputs["light_state"], inputs["light_state_ts"], inputs["light_state_status"]),
                         (None, None, "no LightState at or before this decision"))

    def test_an_empty_file_is_given_but_has_none_before(self):
        self.assertEqual(self.decided([])["light_state_status"], "no LightState at or before this decision")

    def test_no_file(self):
        inputs = self.decided()
        self.assertEqual((inputs["light_state"], inputs["light_state_ts"], inputs["light_state_status"]),
                         (None, None, "no LightState given"))

    def test_the_config_hash_a_decision_logs_does_not_move_with_the_frames(self):
        self.assertEqual(self.decided([frame(T0)])["config_sha256"], self.decided()["config_sha256"])

    def test_frames_out_of_order_or_at_one_ts_in_a_config_refuse(self):
        for frames in ([frame(T0 + 1), frame(T0)], [frame(T0), frame(T0)]):
            with self.subTest(frames=frames), self.assertRaisesRegex(ValueError, "light_states"):
                tc.policy(light_states=frames)

    def test_room_is_held_on_the_line_for_every_form_of_the_fields(self):
        longest = {"light_state": "x" * events.MAX_FRAME_ID, "light_state_ts": T0 + 0.1234567}
        for fields in (longest, {"light_state": None, "light_state_ts": None, "light_state_status": coordinator.NO_FRAME_BEFORE}):
            self.assertLessEqual(len(events.dumps(fields)), coordinator.LIGHT_STATE_ROOM)
        event = tc.ev(T0 + 1234567.125, "decision", {"actuation": "synthetic"}, "decide")
        event["seq"] = 10 ** 9
        self.assertLessEqual(coordinator.LINE_ROOM + coordinator.LIGHT_STATE_ROOM + len(events.dumps(event)), events.MAX_LINE_BYTES)


class LightStateFileTest(Temp):
    def write(self, *rows, raw=False):
        path = self.tmp / "light_state.jsonl"
        path.write_text("".join((row if raw else json.dumps(row)) + "\n" for row in rows), encoding="utf-8", newline="\n")
        return path

    def test_den_photometrys_shape_reads_as_id_and_ts_only(self):
        self.assertEqual(sources.light_states(self.write(light_state(T0 + 5), light_state(T0 + 1))), [frame(T0 + 1), frame(T0 + 5)])

    def test_an_id_on_the_frame_is_kept(self):
        self.assertEqual(sources.light_states(self.write(light_state(T0, id="f-1"))), [{"id": "f-1", "ts": T0}])

    def test_blank_lines_and_crlf_read(self):
        path = self.tmp / "crlf.jsonl"
        path.write_bytes(("\r\n" + json.dumps(light_state(T0)) + "\r\n\r\n").encode("utf-8"))
        self.assertEqual(sources.light_states(path), [frame(T0)])

    def test_two_frames_at_one_ts_are_refused_naming_both_lines(self):
        with self.assertRaisesRegex(ValueError, "lines 1 and 3 are both at ts"):
            sources.light_states(self.write(light_state(T0), light_state(T0 + 1), light_state(T0)))

    def test_two_frames_verify_could_not_tell_apart_are_refused(self):
        with self.assertRaisesRegex(ValueError, "both at ts"):
            sources.light_states(self.write(light_state(T0), light_state(T0 + 0.0004)))

    def test_bad_lines_are_refused_with_their_number(self):
        for name, line in (("no ts", json.dumps({"record": "light_state"})), ("not json", "{"), ("a list", "[1]"),
                           ("text ts", json.dumps(light_state("soon"))), ("true ts", json.dumps(light_state(True))),
                           ("NaN", '{"ts": NaN}'), ("no ts in an envelope", json.dumps({"schema": "den-event-1", "t": T0})),
                           ("id not text", json.dumps(light_state(T0, id=5))), ("empty id", json.dumps(light_state(T0, id=""))),
                           ("long id", json.dumps(light_state(T0, id="x" * (events.MAX_FRAME_ID + 1))))):
            with self.subTest(name), self.assertRaisesRegex(ValueError, "line 2"):
                sources.light_states(self.mixed(line))

    def mixed(self, line):
        path = self.tmp / "bad.jsonl"
        path.write_text(json.dumps(light_state(T0 - 5)) + "\n" + line + "\n", encoding="utf-8", newline="\n")
        return path


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class LightStatesRunTest(Temp):
    ARGV = ["--synthetic", "evening", "--touch", "--policies", "need", "incumbent", "coordinator"]

    def run_it(self, name, *more):
        with mock.patch("builtins.print") as printed:
            run.main([*self.ARGV, *more, "--out", str(self.tmp / name)])
        path = self.tmp / name / "log.jsonl"
        header, log = events.read(path)
        return path, header, log, "\n".join(str(call.args[0]) for call in printed.call_args_list)

    def frames(self, *offsets, shuffled=False):
        rows = [light_state(synthetic.T0 + offset) for offset in offsets]
        if shuffled:
            random.Random(3).shuffle(rows)
        path = self.tmp / ("frames-%d.jsonl" % len(list(self.tmp.iterdir())))
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8", newline="\n")
        return path

    def test_the_same_decisions_with_and_without_the_file_apart_from_these_fields(self):
        _, _, plain, _ = self.run_it("plain")
        _, _, framed, _ = self.run_it("framed", "--light-states", str(self.frames(*range(-700, 2000, 7))))
        a, b = coordinator_decisions(plain), coordinator_decisions(framed)
        self.assertTrue(a)
        self.assertEqual([without_light_state(d) for d in b], [without_light_state(d) for d in a])
        self.assertTrue(all(d["inputs"]["light_state"] for d in b))
        self.assertTrue(all(d["inputs"]["light_state_status"] == "no LightState given" for d in a))

    def test_each_decision_names_the_newest_frame_at_or_before_it_never_a_later_one(self):
        times = [synthetic.T0 + offset for offset in range(-700, 2000, 7)]
        _, _, log, _ = self.run_it("framed", "--light-states", str(self.frames(*range(-700, 2000, 7), shuffled=True)))
        for d in coordinator_decisions(log):
            before = [t for t in times if t <= d["ts"]]
            self.assertEqual(d["inputs"]["light_state_ts"], before[-1])
            self.assertEqual(d["inputs"]["light_state"], "ls-%.3f" % before[-1])
            self.assertLessEqual(d["inputs"]["light_state_ts"], d["ts"])

    def test_it_replays_byte_for_byte_with_the_file_and_tampering_is_caught(self):
        path, _, _, _ = self.run_it("framed", "--light-states", str(self.frames(0, 100, 200)))
        self.assertTrue(check.check(path)["ok"], check.check(path)["reason"])
        text = path.read_text("ascii")
        moved = text.replace('"light_state_ts":%r' % (synthetic.T0 + 100.0), '"light_state_ts":%r' % (synthetic.T0 + 99.0), 1)
        if moved != text:
            copy_path = self.tmp / "moved.jsonl"
            copy_path.write_bytes(moved.encode("ascii"))
            self.assertFalse(check.check(copy_path)["ok"])

    def test_the_other_policies_events_are_unchanged(self):
        _, _, plain, _ = self.run_it("plain")
        _, _, framed, _ = self.run_it("framed", "--light-states", str(self.frames(0, 100, 200)))
        self.assertEqual([e for e in plain if not (e["kind"] == "decision" and e["source"] == "decide")],
                         [e for e in framed if not (e["kind"] == "decision" and e["source"] == "decide")])
        self.assertEqual(len(plain), len(framed))

    def test_only_each_frames_id_and_ts_reach_the_log(self):
        path, header, _, _ = self.run_it("framed", "--light-states", str(self.frames(0, 100)))
        config = next(p for p in header["policies"] if p["module"] == "coordinator")["config"]
        self.assertEqual(config["light_states"], [frame(synthetic.T0), frame(synthetic.T0 + 100)])
        text = path.read_text("ascii")
        for word in ("surface_estimates", "rho_status", "normalisation_version"):
            self.assertNotIn(word, text)
        self.assertIn("frames-0.jsonl", {row["name"] for row in header["sources"]})

    def test_the_summary_says_how_many_frames(self):
        _, _, _, text = self.run_it("framed", "--light-states", str(self.frames(0, 100)))
        self.assertIn("light_states: 2 frame(s)", text)

    def test_a_score_and_a_validate_read_the_log(self):
        import validate
        path, _, _, _ = self.run_it("framed", "--light-states", str(self.frames(0, 100)))
        self.assertEqual(score.report(path, preview=True)[1], 0)
        self.assertTrue(validate.report(path)[1])

    def test_without_the_coordinator_it_is_refused(self):
        frames = self.frames(0)
        for policies in (["need", "incumbent", "decide"], ["incumbent"]):
            with self.subTest(policies), mock.patch("sys.stderr"), self.assertRaises(SystemExit) as stopped:
                run.main(["--synthetic", "evening", "--policies", *policies, "--light-states", str(frames), "--out", str(self.tmp / "no")])
            self.assertEqual(stopped.exception.code, 2)
        self.assertFalse((self.tmp / "no").exists())

    def test_two_frames_at_one_ts_stop_the_run_with_no_log(self):
        path = self.tmp / "twice.jsonl"
        path.write_text(json.dumps(light_state(synthetic.T0)) + "\n" + json.dumps(light_state(synthetic.T0)) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "both at ts"):
            self.run_it("twice", "--light-states", str(path))
        self.assertFalse((self.tmp / "twice" / "log.jsonl").exists())

    def test_a_line_near_full_keeps_the_same_surfaces_with_and_without_the_file(self):
        names = ["surface-with-a-long-thirty-name-%03d" % n for n in range(400)]
        model = fake_light.build(surfaces=False, filler=576 - 12)
        blocks = [p["point_id"] for p in model["sample_points"]]
        surfaces = self.tmp / "wide.json"
        surfaces.write_text(json.dumps({name: [blocks[n % 576]] for n, name in enumerate(names)}), encoding="utf-8")
        model_path = self.write_model(model)
        wide = ("--light-model", str(model_path), "--surfaces", str(surfaces))
        _, _, plain, _ = self.run_it("plain", *wide)
        _, _, framed, _ = self.run_it("framed", *wide, "--light-states", str(self.frames(*range(-700, 2000, 7))))
        a, b = coordinator_decisions(plain), coordinator_decisions(framed)
        self.assertTrue([d for d in a if d["predicted"].get("surfaces_not_logged")])
        self.assertEqual([without_light_state(d) for d in b], [without_light_state(d) for d in a])


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class OutputsTest(Temp):
    """decisions.jsonl and surfaces.json beside log.jsonl, for rung 10. Only when the coordinator ran."""
    ARGV = ["--synthetic", "evening", "--touch", "--policies", "need", "incumbent", "coordinator"]

    def run_it(self, *argv, name="out"):
        with mock.patch("builtins.print") as printed:
            run.main([*argv, "--out", str(self.tmp / name)])
        return self.tmp / name, "\n".join(str(call.args[0]) for call in printed.call_args_list)

    def test_decisions_jsonl_is_the_coordinators_decisions_in_log_order_with_their_version(self):
        out, text = self.run_it(*self.ARGV)
        _, log = events.read(out / "log.jsonl")
        mine = [e["data"] for e in log if e["kind"] == "decision" and e["source"] == "decide"]
        raw = (out / "decisions.jsonl").read_bytes()
        self.assertTrue(mine)
        self.assertNotIn(b"\r", raw)
        self.assertTrue(raw.endswith(b"\n"))
        lines = raw.decode("utf-8").split("\n")[:-1]
        self.assertEqual(lines, [events.dumps({**d, "version": "decision/v2"}) for d in mine])
        self.assertTrue(all(json.loads(line)["policy"] == coordinator.POLICY for line in lines))
        self.assertLess(max(map(len, lines)), events.MAX_LINE_BYTES)
        self.assertIn("decisions: %s (%d)" % (out / "decisions.jsonl", len(mine)), text)
        self.assertIn("surfaces: %s" % (out / "surfaces.json"), text)

    def test_the_version_is_on_decisions_jsonl_only(self):
        out, _ = self.run_it(*self.ARGV)
        _, log = events.read(out / "log.jsonl")
        self.assertFalse([e for e in log if e["kind"] == "decision" and "version" in e["data"]])

    def test_surfaces_json_is_the_map_the_coordinator_ran_on_with_room_spelled_out(self):
        out, _ = self.run_it(*self.ARGV)
        header, _ = events.read(out / "log.jsonl")
        config = next(p for p in header["policies"] if p["module"] == "coordinator")["config"]
        ran = coordinator.make_policy(config).light
        raw = (out / "surfaces.json").read_bytes()
        self.assertNotIn(b"\r", raw)
        written = json.loads(raw.decode("utf-8"))
        self.assertEqual(written, ran.surfaces)
        self.assertEqual(written["room"], ran.points)

    def test_surfaces_json_round_trips_through_the_coordinators_own_map(self):
        model = self.write_model(stage1_shaped())
        named = self.tmp / "named.json"
        named.write_text(json.dumps({"desk": ["counter-1/0", "counter-1/1"]}), encoding="utf-8")
        out, _ = self.run_it(*self.ARGV, "--light-model", str(model), "--surfaces", str(named), name="first")
        written = json.loads((out / "surfaces.json").read_text("utf-8"))
        self.assertEqual(sorted(written), ["desk", "room"])
        self.assertEqual(len(written["room"]), 12)
        again, _ = self.run_it(*self.ARGV, "--light-model", str(model), "--surfaces", str(out / "surfaces.json"), name="second")
        self.assertEqual(json.loads((again / "surfaces.json").read_text("utf-8")), written)
        header, _ = events.read(again / "log.jsonl")
        config = next(p for p in header["policies"] if p["module"] == "coordinator")["config"]
        self.assertEqual(coordinator.make_policy(config).light.surfaces, written)
        first = (out / "decisions.jsonl").read_text("ascii").split("\n")
        second = (again / "decisions.jsonl").read_text("ascii").split("\n")
        self.assertEqual([json.loads(line)["predicted"]["surfaces"] for line in first if line],
                         [json.loads(line)["predicted"]["surfaces"] for line in second if line])

    def test_with_no_coordinator_nothing_new_is_written(self):
        for policies in (["need", "incumbent", "decide"], ["incumbent"]):
            with self.subTest(policies):
                out, text = self.run_it("--synthetic", "evening", "--policies", *policies, name="-".join(policies))
                self.assertEqual(sorted(p.name for p in out.iterdir() if not p.name.startswith("la-fact-log")), ["log.jsonl", "truth.json"])
                self.assertNotIn("decisions:", text)

    def test_they_are_written_only_after_the_log_is_complete(self):
        real, seen = events.write, []

        def write(path, header, log):
            seen.append(sorted(p.name for p in Path(path).parent.iterdir()))
            real(path, header, log)

        with mock.patch.object(events, "write", write):
            out, _ = self.run_it(*self.ARGV)
        self.assertEqual(seen, [[]])
        self.assertTrue((out / "decisions.jsonl").exists())

    def test_a_run_that_stops_leaves_neither(self):
        with mock.patch.object(run.runner, "run", side_effect=ValueError("decision line over 16384 bytes")), mock.patch("builtins.print"):
            with self.assertRaises(ValueError):
                run.main([*self.ARGV, "--out", str(self.tmp / "out")])
        self.assertFalse((self.tmp / "out").exists())

    def test_an_out_folder_holding_either_is_refused_before_anything_is_written(self):
        for name in ("decisions.jsonl", "surfaces.json"):
            folder = self.tmp / name.split(".")[0]
            folder.mkdir()
            (folder / name).write_text("", encoding="utf-8")
            with self.subTest(name), mock.patch("sys.stderr"), self.assertRaises(SystemExit) as stopped:
                run.main([*self.ARGV, "--out", str(folder)])
            self.assertEqual(stopped.exception.code, 2)
            self.assertFalse((folder / "log.jsonl").exists())

    def test_a_run_with_no_decisions_writes_an_empty_file(self):
        first, _ = self.run_it(*self.ARGV, name="first")
        out, _ = self.run_it("--facts", str(first), "--policies", "need", "coordinator", name="facts-only")
        self.assertEqual((out / "decisions.jsonl").read_bytes(), b"")
        self.assertTrue(json.loads((out / "surfaces.json").read_text("utf-8"))["room"])

    def test_the_lines_carry_the_light_state_fields(self):
        frames = self.tmp / "frames.jsonl"
        frames.write_text(json.dumps(light_state(synthetic.T0 - 700)) + "\n", encoding="utf-8")
        out, _ = self.run_it(*self.ARGV, "--light-states", str(frames))
        for line in (out / "decisions.jsonl").read_text("ascii").splitlines():
            self.assertEqual(json.loads(line)["inputs"]["light_state_ts"], synthetic.T0 - 700)


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class ReadmeTest(Temp):
    """README's account of this seam, run as it says and measured."""

    @staticmethod
    def readme():
        return (HERE / "README.md").read_text("utf-8")

    def block(self, tool, words):
        """The one command in README that runs this tool and has these words."""
        found = [line for line in self.readme().splitlines() if line.startswith("python -B tools/" + tool) and words in line]
        self.assertEqual(len(found), 1, found)
        return found[0].split()[3:]

    def test_its_commands_take_a_stage_1_model_to_decisions_and_surfaces(self):
        with mock.patch("builtins.print"):
            run.main(["--synthetic", "evening", "--touch", "--policies", "incumbent", "--out", str(self.tmp / "evening")])
        facts = self.tmp / "facts"
        facts.mkdir()
        for path in (self.tmp / "evening").glob("la-fact-log-1-*.jsonl"):
            shutil.copyfile(path, facts / path.name)
        journal = self.tmp / "edges.txt"
        journal.write_text("".join(test_spine.edge(synthetic.T0 + dt, a, b) + "\n" for dt, a, b in ((-600, "unknown", "on"), (1400, "on", "off"))),
                           encoding="ascii")
        frames = self.tmp / "light_state.jsonl"
        frames.write_text("".join(json.dumps(light_state(synthetic.T0 + dt)) + "\n" for dt in range(-700, 2000, 7)), encoding="utf-8")
        given = {"C:/tmp/den-core/in/facts": facts, "C:/tmp/den-core/in/occupancy-edges.txt": journal,
                 "C:/den-light/SESSION/light-model.json": self.write_model(stage1_shaped()),
                 "C:/tmp/den-core/in/surfaces.json": self.write_model({"room": ["counter-1/0", "counter-1/1"]}, "surfaces.json"),
                 "C:/tmp/den-core/in/light_state.jsonl": frames, "C:/tmp/den-core-r9/real-1": self.tmp / "real-1"}
        with real_backend():
            with mock.patch("builtins.print"):
                run.main([str(given.get(word, word)) for word in self.block("den-core/run.py", "--light-states")])
            log = str(self.tmp / "real-1" / "log.jsonl")
            self.assertEqual(self.block("den-core/check.py", "den-core-r9/real-1"), ["C:/tmp/den-core-r9/real-1/log.jsonl"])
            self.assertTrue(check.check(log)["ok"])
            lines, _ = score.report(log, preview=True)
        for words in ("backend den-light predict.py", "replay: OK", "records: OK", "verdict: none (preview)"):
            self.assertIn(words, "\n".join(lines))
        written = [json.loads(line) for line in (self.tmp / "real-1" / "decisions.jsonl").read_text("ascii").splitlines()]
        self.assertTrue(written)
        self.assertTrue(all(d["actuation"] == "replay" and d["version"] == "decision/v2" and d["inputs"]["light_state"] for d in written))
        self.assertEqual(sorted(json.loads((self.tmp / "real-1" / "surfaces.json").read_text("utf-8"))), ["room"])
        verify = self.block("den-verify/verify.py", "--decisions")
        self.assertEqual(verify[verify.index("--decisions") + 1], "C:/tmp/den-core-r9/real-1/decisions.jsonl")
        self.assertEqual(verify[verify.index("--surfaces") + 1], "C:/tmp/den-core-r9/real-1/surfaces.json")

    def test_it_says_which_version_decisions_jsonl_adds(self):
        said = " ".join(self.readme().split())
        self.assertIn('plus one key, `version: "decision/v2"`', said)
        self.assertIn("`--light-states FILE`", said)
        self.assertNotIn("Today that is every run", said)
        self.assertNotIn("replace the body of", said)

    def test_the_sizes_it_gives_are_measured(self):
        said = " ".join(self.readme().split())
        at = {block_px: round(tc.longest_decision(tc.GridLimitLineTest.touched(block_px)) / 1000, 1) for block_px in (20, 10, 8)}
        self.assertIn("about %.1f KB at the proposed 20 (576 blocks), %.1f KB at 10 and %.1f KB at 8 (3,600 blocks)" % (at[20], at[10], at[8]), said)

    @staticmethod
    def whole_map(count, width):
        """Whether a map of this many surfaces, with names this long, stays whole on the line, as README counts it: one reader,
        every bulb held, the proposed grid, through runner.run beside the real den_lighting."""
        names = ["s" + str(n).zfill(width - 1) for n in range(count)]
        spec = {"source": "fake", "params": {"filler": 576 - 12}, "surfaces": {name: ["other/%d" % (n % 500)] for n, name in enumerate(names)}}
        case = {"script": [(T0 + 10, "A", "engaged", "counter-1")], "touches": [(30 + 10 * n, b, scene(60 + 20 * n)) for n, b in enumerate(events.LIGHTS)], "inputs": []}
        with mock.patch.dict(hold_fixtures.CASES, {"touched": case}):
            log = hold_fixtures.run_case("touched", "P1-mode", light_model=spec)[1]
        return not any("surfaces_not_logged" in d["predicted"] for d in hold_fixtures.decisions(log))

    def test_the_surface_counts_it_gives_are_measured(self):
        found = re.search(r"about (\d+) surfaces with 10-character names or about (\d+) with 30 fit whole", " ".join(self.readme().split()))
        self.assertTrue(found)
        for count, width in ((int(found.group(1)), 10), (int(found.group(2)), 30)):
            with self.subTest(width=width):
                self.assertTrue(self.whole_map(int(count * 0.95), width), "fewer than README says fit")
                self.assertFalse(self.whole_map(int(count * 1.05), width), "more than README says fit")

    @staticmethod
    def per_track(ids, surfaces, fields=(), reading=False, bulbs=1, **config):
        """The bytes one more track adds to a Decision's line, with a hold so a Decision is made with all of them on it."""
        def line(count):
            p = tc.policy(compensation=config.get("compensation", {"option": "P1", "form": "mode"}), **{k: v for k, v in config.items() if k != "compensation"})
            tc.start(p)
            for k, track in enumerate(ids[:count]):
                person = hold_fixtures.person_state(T0 + 2 + k, track, "engaged" if reading else "none", surfaces[k % len(surfaces)])
                person["field_support"].update({name: {"last_support_capture_ts": T0 + 1 + k, "value_set_ts": T0 + 2 + k, "support_status": "observed"} for name in fields})
                tc.feed(p, tc.ev(T0 + 2 + k, "person_state", person, "need"))
            holds, out = [], []
            for n, bulb in enumerate(events.LIGHTS[:bulbs]):
                holds.append(tc.hold(bulb, 100 + 30 * n, T0 + 100 + n))
                out += tc.feed(p, tc.ev(T0 + 100 + n, "mode", tc.mode(2 + n, holds)["data"], "incumbent"),
                               tc.ev(T0 + 100 + n, "correction", {"correction_id": "c%d" % n, "entity_id": bulb, "after": scene(100 + 30 * n), "source": "t", "who": {"track_id": ids[0]}}))
            assert "tracks_not_logged" not in out[-1]["inputs"]
            return len(events.dumps(out[-1]))
        return line(2) - line(1)

    def test_the_track_sizes_it_gives_are_measured(self):
        found = re.search(r"about ([\d.]+) KB each with need v0's one field, ([\d.]+) KB with every field the schema names, or ([\d.]+) KB in the worst shape",
                          " ".join(self.readme().split()))
        self.assertTrue(found)
        grid = {"source": "fake", "params": {"filler": 576 - 12}}
        every = ("position", "working_region", "focused_engagement", "visual_demand", "attention", "heading", "motion", "posture", "affected")
        ids = ["track-%06d" % k for k in range(4)]
        glare = {b: {name: {"G": 3.0, "sd": 0.01} for name in ids} for b in events.LIGHTS}
        worst = {"source": "fake", "params": {"filler": 576 - 12, "glare": glare, "error": {"median_pct": 8.0, "p90_pct": 20.0}},
                 "surfaces": {"a-surface-with-a-thirty-char-%d" % n: ["other/%d" % (n * 10 + m) for m in range(10)] for n in range(8)}}
        measured = (self.per_track(["T0", "T1"], ["counter-1"], light_model=grid),
                    self.per_track(["T0", "T1"], ["counter-1"], every, light_model=grid),
                    self.per_track(ids, sorted(worst["surfaces"]), every, reading=True, bulbs=4, light_model=worst, compensation={"option": "P3", "form": "strict"}))
        for kilobytes, size in zip(map(float, found.groups()), measured):
            self.assertLess(abs(size / 1000 - kilobytes), 0.15, (found.groups(), measured))

    def test_the_adapters_docstring_says_what_it_calls(self):
        said = " ".join(light_adapter.__doc__.split())
        self.assertIn("That is den-light's own predict.py", said)
        self.assertNotIn("_predict`'s body", said)
        self.assertNotIn("Today", said)

    def test_the_release_fragment_says_what_is_now_true(self):
        fragment = HERE.parents[1] / "changes" / "unreleased" / "den-core-rung-9-coordinator.md"
        if not fragment.exists():  # a release moves it to the archive
            return
        said = " ".join(fragment.read_text("utf-8").split())
        self.assertNotIn("until its `predict.py` is merged", said)
        for words in ("`predict.py`", "`decisions.jsonl`", "`surfaces.json`", "--light-states", "UTF-16", "`cct_k`"):
            self.assertIn(words, said)


if __name__ == "__main__":
    unittest.main()
