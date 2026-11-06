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
import test_coordinator as tc  # noqa: E402
import test_score  # noqa: E402
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
        broken.write_text("def broken(:\n", encoding="utf-8")
        with mock.patch.object(light_adapter, "PREDICT_PATH", broken), self.assertRaisesRegex(ValueError, "cannot be loaded"):
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


if __name__ == "__main__":
    unittest.main()
