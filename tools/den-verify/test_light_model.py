"""Stage 1's light model through den-light's own predict.py, and --surfaces.

den-light sits beside this folder on this branch, so every test here runs against its predict.py. A broken or missing
predict.py is tried in a copy of the two folders, in a subprocess, since the adapter imports it once. Everything here is
synthetic.

    python -B -m unittest discover -s tools/den-verify -p "test_*.py"
"""
import ast
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import light_model  # noqa: E402
import method  # noqa: E402
import synthetic as syn  # noqa: E402
import verify as cli  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock, BadFile, read_jsonl, write_jsonl  # noqa: E402
from score import Run  # noqa: E402
from test_verify import Quiet, by_id, closed, point, residual, send  # noqa: E402

B0, B1, B2, B3 = syn.HUE
KELVINS = (2202, 3294, 6535)
NEEDS_REAL = "needs tools/den-light/predict.py beside this folder"
REAL_PREDICT = light_model.PREDICT_PATH if light_model.PREDICT_PATH.is_file() else None


def real_backend():
    """den-light's predict module, as the adapter imported it: a patch that changes nothing."""
    return mock.patch.object(light_model, "predict", light_model.predict)


def no_sibling():
    """`import predict` found nothing."""
    return mock.patch.object(light_model, "predict", None)


def key_of(entity_id):
    return entity_id.replace("light.", "") + ".white"


def stage1_model(points, excluded=(), version="lm-stage1-test", surfaces=None, unit="relative", **extra):
    """A light-model.json in the shape model.light_model writes: `points` is {point_id: {entity_id: value at full, at every
    temperature but 10% up at the coolest}}, dimming linear in the command, an excluded block None in every row and flagged."""
    channels, basis, cct, dimming = [], {}, {}, {}
    for e in syn.HUE:
        key = key_of(e)
        channels.append({"channel_key": key, "fixture_id": key.split(".")[0], "channel_id": "white", "entity_id": e,
                         "kind": "tunable_white", "cct_range_k": [2000, 6535]})
        keys = []
        for n, k in enumerate(KELVINS):
            keys.append(f"{key}@{k}K")
            basis[keys[-1]] = {p: None if p in excluded else float(v.get(e, 0.0)) * (1.1 if n == 2 else 1.0) for p, v in points.items()}
        cct[key] = {"kelvin": list(KELVINS), "basis_keys": keys, "entity_id": e}
        dimming[key] = {"levels": [0, 254], "g": [0.0, 1.0], "shared_with": None}
    model = {"record": "light_model", "contract": "live-v1", "version": version, "room_version": "den-test",
             "sample_points": [{"point_id": p, "kind": None, "xyz": None, "normal": None, "rect": None, "excluded": "basis" if p in excluded else None} for p in points],
             "channels": channels, "basis": basis, "basis_unit": unit, "spot_model": None, "dimming": dimming, "cct": cct,
             "bounce": "measured", "glare": None, "error": {"median_pct": 2.0, "p90_pct": 10.0}, **extra}
    if surfaces is not None:
        model["surfaces"] = surfaces
    return model


FOUR = {"p0": {B0: 100.0, B1: 10.0}, "p1": {B0: 50.0, B1: 30.0}, "p2": {B1: 80.0, B2: 40.0}, "p3": {B2: 60.0, B3: 20.0}}


def lit(level, cct_k=2202):
    return {"level": level, "cct_k": cct_k}


def decision(did, t, ref_ts, before, applied, targets, model="lm-stage1-test", **extra):
    """A Decision in entity ids, with read-backs, as den-core writes one."""
    rec = syn.hue_decision(did, t, before, applied, None, model=model, targets={f"T{n}": {"surface_id": s} for n, s in enumerate(targets)}, cct_k=2202, **extra)
    rec["inputs"].update(light_state_ts=ref_ts, light_state=f"ls-{ref_ts:.3f}")
    return rec


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def write(self, name, value):
        path = self.tmp / name
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        return path

    def main(self, *argv):
        with Quiet() as q:
            code = cli.main([str(a) for a in argv])
        return code, q.buf.getvalue()

    def case(self, model, decisions, frames, name="case", surfaces=None, closed_method=False):
        """argv for a run in a new folder."""
        folder = self.tmp / name
        folder.mkdir()
        write_jsonl(folder / "decisions.jsonl", decisions)
        write_jsonl(folder / "light_states.jsonl", frames)
        (folder / "light-model.json").write_text(json.dumps(model), encoding="utf-8")
        argv = []
        if closed_method:
            (folder / "VERIFY-V1.md").write_text(method.closed_copy(method.lf_text(HERE / "VERIFY-V1.md")), encoding="utf-8", newline="\n")
            argv += ["--method", folder / "VERIFY-V1.md"]
        argv += ["run", "--decisions", folder / "decisions.jsonl", "--light-states", folder / "light_states.jsonl",
                 "--model", folder / "light-model.json", "--utc-offset-h", "-7", "--out", folder / "out"]
        if surfaces is not None:
            (folder / "surfaces.json").write_text(json.dumps(surfaces), encoding="utf-8")
            argv += ["--surfaces", folder / "surfaces.json"]
        return argv, folder


def values_at(truth, state, surfaces):
    """A frame's values from the same arithmetic done by hand: each channel's value at each block, the mean over a surface's blocks."""
    out = {}
    for surface, blocks in surfaces.items():
        known = [p for p in blocks if truth.get(p) is not None]
        out[surface] = sum(sum(s["level"] * truth[p].get(e, 0.0) for e, s in state.items()) for p in known) / len(known)
    return out


@unittest.skipUnless(REAL_PREDICT, NEEDS_REAL)
class Stage1Backend(Temp):
    """den-light's own predict.py behind the adapter."""

    def setUp(self):
        super().setUp()
        patcher = real_backend()
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_it_is_den_lights_own_file_found_last_on_the_path_and_pinned(self):
        model = LightModel(stage1_model(FOUR))
        self.assertEqual(Path(model.module.__file__).resolve(), REAL_PREDICT.resolve())
        self.assertGreater(sys.path.index(str(light_model.DEN_LIGHT)), sys.path.index(str(HERE)))
        self.assertEqual(Path(syn.__file__).resolve().parent, HERE)  # den-light's synthetic.py never answers for this one
        self.assertFalse(model.fake)
        self.assertEqual(model.backend, light_model.BACKEND)
        self.assertEqual(model.predict_sha256, hashlib.sha256(REAL_PREDICT.read_bytes()).hexdigest())

    def test_the_model_is_prepared_once_not_on_every_call(self):
        module = LightModel(stage1_model(FOUR)).module
        with mock.patch.object(module, "prepare", wraps=module.prepare) as prepare:
            model = LightModel(stage1_model(FOUR))
            for level in (0.2, 0.4, 0.6):
                model.predict({B0: lit(level), B1: lit(level)})
        # predict() hands prepare() what it was given, a no-op on prepared tables: only the file's dict costs a read
        self.assertEqual(sum(isinstance(call.args[0], dict) for call in prepare.call_args_list), 1)

    def test_its_numbers_are_predict_pys_at_every_block(self):
        data = stage1_model(FOUR)
        model = LightModel(data)
        scene = {key_of(B0): lit(0.5), key_of(B2): lit(0.25, 6535)}
        direct = model.module.predict(scene, data)["surfaces"]
        got = model.predict({B0: lit(0.5), B2: lit(0.25, 6535)})
        for p in FOUR:
            self.assertAlmostEqual(got[p], direct[p], places=9)
        self.assertAlmostEqual(got["p3"], 0.25 * 60 * 1.1, places=9)  # the cool end's own basis: not the fake's rule by accident

    def test_entity_ids_and_channel_keys_are_both_channel_names(self):
        model = LightModel(stage1_model(FOUR))
        by_entity, by_key = model.predict({B0: lit(0.5)}), model.predict({key_of(B0): lit(0.5)})
        self.assertEqual(by_entity, by_key)
        self.assertEqual(by_entity["p0"], 50.0)
        self.assertEqual(model.contributions({B0: lit(0.5)})[B0], model.contributions({key_of(B0): lit(0.5)})[key_of(B0)])

    def test_one_channel_under_both_names_in_a_state_counts_once(self):
        model = LightModel(stage1_model(FOUR))
        self.assertEqual(model.predict({B0: lit(0.5), key_of(B0): lit(0.5)})["p0"], 50.0)

    def test_a_basis_key_is_not_a_channel(self):
        model = LightModel(stage1_model(FOUR))
        with self.assertRaisesRegex(KeyError, "no channel"):
            model.predict({f"{key_of(B0)}@2202K": lit(0.5)})

    def test_a_channel_the_model_lacks_is_a_key_error_and_off_it_is_nothing(self):
        model = LightModel(stage1_model(FOUR))
        with self.assertRaisesRegex(KeyError, "no channel"):
            model.predict({"light.other": lit(0.5)})
        self.assertEqual(model.predict({"light.other": lit(0.0)})["p0"], 0.0)

    def test_a_lit_channel_with_no_kelvin_is_a_key_error_as_predict_py_says(self):
        model = LightModel(stage1_model(FOUR))
        with self.assertRaisesRegex(KeyError, "cct_k"):
            model.predict({B0: {"level": 0.5, "cct_k": None}})

    def test_a_channel_captured_at_one_temperature_needs_no_kelvin(self):
        data = stage1_model(FOUR)
        key = key_of(B0)
        data["cct"][key].update(kelvin=[2202], basis_keys=[f"{key}@2202K"])
        self.assertEqual(LightModel(data).predict({B0: {"level": 0.5, "cct_k": None}})["p0"], 50.0)

    def test_a_block_the_fit_excluded_is_left_out_of_a_surfaces_mean(self):
        surfaces = {"desk": ["p0", "p1"], "gone": ["p1"]}
        model = LightModel(stage1_model(FOUR, excluded=("p1",)), surfaces=surfaces)
        self.assertEqual(model.predict({B0: lit(0.5)})["desk"], 50.0)  # p0's 50, never (50 + 25) / 2 or (50 + 0) / 2

    def test_a_surface_wholly_excluded_reads_no_prediction_not_zero_off_bulbs_included(self):
        surfaces = {"desk": ["p0", "p1"], "gone": ["p1"]}
        model = LightModel(stage1_model(FOUR, excluded=("p1",)), surfaces=surfaces)
        for state in ({}, {B0: lit(0.0)}, {B0: lit(0.5)}, {B0: lit(0.5), B1: lit(0.0)}):
            with self.subTest(state=state):
                got = model.predict(state)
                self.assertIsNone(got["gone"])
                self.assertIsNotNone(got["desk"])
        self.assertIsNone(model.contributions({B0: lit(0.5)})[B0]["gone"])

    def test_a_scene_is_the_sum_of_its_channels_parts(self):
        model = LightModel(stage1_model(FOUR, excluded=("p3",)), surfaces={"a": ["p0", "p2", "p3"]})
        state = {B0: lit(0.5), B1: lit(0.25), B2: lit(1.0)}
        parts = model.contributions(state)
        self.assertAlmostEqual(model.predict(state)["a"], sum(parts[c]["a"] for c in state), places=9)

    def test_a_level_past_full_is_full_as_scene_from_ha_caps_it(self):
        model = LightModel(stage1_model(FOUR))
        self.assertEqual(model.predict({B0: lit(255 / 254)}), model.predict({B0: lit(1.0)}))

    def test_a_stage_1_model_with_its_own_surfaces(self):
        model = LightModel(stage1_model(FOUR, surfaces={"desk": ["p0", "p1"]}))
        self.assertEqual(model.predict({B0: lit(1.0)}), {"desk": 75.0})

    def test_no_map_means_the_point_ids(self):
        self.assertEqual(sorted(LightModel(stage1_model(FOUR)).predict({})), sorted(FOUR))

    def landing(self, reading=None, surfaces=("p0", "p1"), model_surfaces=None, closed_method=False, surfaces_file=None, name="case"):
        """B0 0.2 to 0.8 and everything else off, in entity ids; frames from the same arithmetic plus 5 of ambient."""
        t = syn.T0
        data = stage1_model(FOUR, surfaces=model_surfaces)
        where = surfaces_file or model_surfaces or {p: [p] for p in FOUR}
        before, after = values_at(FOUR, {B0: {"level": 0.2}}, where), values_at(FOUR, {B0: {"level": 0.8}}, where)
        ref = syn.light_state(t, {s: v + 5 for s, v in before.items()}, anchored=False)
        verify_frame = syn.light_state(t + 5, {s: (reading or (lambda v: v + 5))(v) for s, v in after.items()}, anchored=False)
        dec = decision("d", t + 0.5, t, {B0: 0.2}, {B0: {"level": 0.8, "steps": send(t + 0.5)}}, surfaces)
        return self.case(data, [dec], [ref, verify_frame], name=name, surfaces=surfaces_file, closed_method=closed_method)

    def test_a_scored_run_on_it_is_not_refused_and_names_what_it_ran_on(self):
        argv, folder = self.landing(closed_method=True)
        code, printed = self.main(*argv)
        self.assertEqual(code, 0, printed)
        self.assertIn("gate 0.85: pass", printed)
        report = json.loads((folder / "out" / "report.json").read_text("utf-8"))
        self.assertTrue(report["scored"])
        self.assertEqual(report["backend"], light_model.BACKEND)
        self.assertEqual(report["model"]["backend"], light_model.BACKEND)
        self.assertIs(report["model"]["fake"], False)
        self.assertEqual(report["predict_sha256"], hashlib.sha256(REAL_PREDICT.read_bytes()).hexdigest())
        self.assertEqual(report["inputs"]["predict_py"], report["predict_sha256"])
        self.assertEqual(report["hit_rate"]["hits"], 2)
        self.assertIn(light_model.BACKEND, printed)

    def test_a_wrong_landing_on_it_reads_wrong(self):
        argv, folder = self.landing(reading=lambda v: 0.5 * v + 5, closed_method=True)
        code, printed = self.main(*argv)
        self.assertEqual(code, 0, printed)
        self.assertIn("gate 0.85: fail", printed)

    def test_a_model_marked_fake_is_labelled_previewed_and_refused_scored(self):
        t = syn.T0
        data = stage1_model(FOUR, fake=True)
        dec = decision("d", t + 0.5, t, {B0: 0.2}, {B0: {"level": 0.8, "steps": send(t + 0.5)}}, ("p0",))
        frames = [syn.light_state(t, {"p0": 20.0}, anchored=False), syn.light_state(t + 5, {"p0": 80.0}, anchored=False)]
        argv, folder = self.case(data, [dec], frames, closed_method=True)
        code, printed = self.main(*argv)
        self.assertEqual(code, 2, printed)
        self.assertIn("refused", printed)
        self.assertIn("fake", printed)
        self.assertFalse((folder / "out").exists())
        code, printed = self.main(*argv, "--preview")
        self.assertEqual(code, 0, printed)
        report = json.loads((folder / "out" / "report-preview.json").read_text("utf-8"))
        self.assertTrue(report["model"]["fake"])
        self.assertIn("marked fake", report["backend"])
        self.assertIn("plumbing only", printed)
        self.assertEqual(report["hit_rate"]["hits"], 1)

    def test_a_spot_model_is_refused_rather_than_ignored(self):
        path = self.write("spot.json", stage1_model(FOUR, spot_model={key_of(B0): {"near": {"p0": 1.0}}}))
        with self.assertRaisesRegex(BadFile, "spot_model"):
            LightModel.load(path)

    def test_surfaces_file_goes_in_the_report_with_its_hash(self):
        where = {"desk": ["p0", "p1"]}
        argv, folder = self.landing(surfaces=("desk",), surfaces_file=where, closed_method=True)
        code, printed = self.main(*argv)
        self.assertEqual(code, 0, printed)
        report = json.loads((folder / "out" / "report.json").read_text("utf-8"))
        raw = (folder / "surfaces.json").read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(report["inputs"]["surfaces"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(report["model"]["surfaces"], {"source": "--surfaces", "count": 1, "wrong": []})
        self.assertEqual(report["hit_rate"]["hits"], 1)

    def test_surfaces_file_stands_over_the_models_own(self):
        argv, _ = self.landing(surfaces=("desk",), surfaces_file={"desk": ["p0"]}, model_surfaces={"desk": ["p1"]})
        code, printed = self.main(*argv, "--preview")
        self.assertEqual(code, 0, printed)
        self.assertIn("(1 of 1)", printed)

    def test_a_decision_surface_outside_the_point_ids_is_not_verified_naming_surfaces(self):
        argv, folder = self.landing(surfaces=("room",))
        code, printed = self.main(*argv, "--preview")
        self.assertEqual(code, 0, printed)
        self.assertIn("insufficient", printed)
        self.assertIn("--surfaces", printed)
        report = json.loads((folder / "out" / "report-preview.json").read_text("utf-8"))
        [entry] = report["decisions"]["not_verified"]
        self.assertEqual(entry["kind"], "no surface map")
        self.assertIn("--surfaces", entry["reason"])
        self.assertIn("room", entry["reason"])
        self.assertEqual(report["decisions"]["verified"], 0)
        self.assertEqual(report["model"]["surfaces"]["source"], "the model's point ids")

    def test_a_map_that_lacks_the_surface_says_so_too(self):
        argv, folder = self.landing(surfaces=("room",), surfaces_file={"desk": ["p0"]})
        code, printed = self.main(*argv, "--preview")
        self.assertEqual(code, 0, printed)
        [entry] = json.loads((folder / "out" / "report-preview.json").read_text("utf-8"))["decisions"]["not_verified"]
        self.assertIn("--surfaces", entry["reason"])

    def test_a_surface_with_no_block_or_a_block_the_model_lacks_is_not_verified_and_never_a_false_pass(self):
        for where in ({"room": []}, {"room": ["nope"]}, {"room": ["p0", "nope"]}):
            with self.subTest(where=where):
                t = syn.T0
                data = stage1_model(FOUR)
                ref = syn.light_state(t, {"room": 20.0}, anchored=False)
                after = syn.light_state(t + 5, {"room": 80.0}, anchored=False)
                dec = decision("d", t + 0.5, t, {B0: 0.2}, {B0: {"level": 0.8, "steps": send(t + 0.5)}}, ("room",))
                argv, folder = self.case(data, [dec], [ref, after], name="m" + str(len(list(self.tmp.iterdir()))), surfaces=where)
                code, printed = self.main(*argv, "--preview")
                self.assertEqual(code, 0, printed)
                report = json.loads((folder / "out" / "report-preview.json").read_text("utf-8"))
                self.assertEqual(report["decisions"]["verified"], 0)
                self.assertIn("--surfaces", report["decisions"]["not_verified"][0]["reason"])
                self.assertEqual(report["model"]["surfaces"]["wrong"], ["room"])

    def own_prediction(self, logged, surfaces_file):
        """B0 0.2 to 0.8 at `desk`; the Decision says predicted.surfaces is `logged`, and frames land where the map given says."""
        t = syn.T0
        data = stage1_model(FOUR)
        ref = syn.light_state(t, {"desk": 20.0}, anchored=False)
        after = syn.light_state(t + 5, {"desk": 80.0}, anchored=False)
        dec = decision("d", t + 0.5, t, {B0: 0.2}, {B0: {"level": 0.8, "steps": send(t + 0.5)}}, ("desk",))
        dec["predicted"]["surfaces"] = {"desk": logged, "unit": "relative"}
        argv, folder = self.case(data, [dec], [ref, after], name="own" + str(len(list(self.tmp.iterdir()))), surfaces=surfaces_file)
        code, printed = self.main(*argv, "--preview")
        self.assertEqual(code, 0, printed)
        return json.loads((folder / "out" / "report-preview.json").read_text("utf-8")), printed

    def test_a_map_that_does_not_give_the_decisions_own_prediction_is_not_verified(self):
        # the map's name is right and its blocks are not those the decision ran on: p0's 80, the decision said p1's 40
        report, _ = self.own_prediction(40.0, {"desk": ["p0"]})
        self.assertEqual(report["decisions"]["verified"], 0)
        self.assertEqual((report["hit_rate"]["hits"], report["hit_rate"]["misses"]), (0, 0))
        [entry] = report["decisions"]["not_verified"]
        self.assertEqual(entry["kind"], "no surface map")
        self.assertIn("--surfaces", entry["reason"])
        self.assertIn("predicted.surfaces", entry["reason"])

    def test_one_decision_showing_the_map_wrong_speaks_for_the_run_since_an_off_passes_through_any_map(self):
        t = syn.T0
        data = stage1_model(FOUR)
        on = decision("d-on", t + 0.5, t, {B0: 0.2}, {B0: {"level": 0.8, "steps": send(t + 0.5)}}, ("desk",))
        on["predicted"]["surfaces"] = {"desk": 40.0, "unit": "relative"}  # p1's: the map below gives p0's 80
        off = decision("d-off", t + 100.5, t + 100, {B0: 0.8}, {B0: {"level": 0.0, "steps": send(t + 100.5)}}, ("desk",))
        off["predicted"]["surfaces"] = {"desk": 0.0, "unit": "relative"}
        frames = [syn.light_state(t, {"desk": 10.0}, anchored=False), syn.light_state(t + 5, {"desk": 40.0}, anchored=False),
                  syn.light_state(t + 100, {"desk": 40.0}, anchored=False), syn.light_state(t + 105, {"desk": 0.5}, anchored=False)]
        argv, folder = self.case(data, [on, off], frames, surfaces={"desk": ["p0"]})
        code, printed = self.main(*argv, "--preview")
        self.assertEqual(code, 0, printed)
        report = json.loads((folder / "out" / "report-preview.json").read_text("utf-8"))
        self.assertEqual(report["decisions"]["verified"], 0)
        self.assertEqual(report["hit_rate"]["verdict"], "insufficient")
        self.assertEqual(report["decisions"]["not_verified_by_kind"], {"no surface map": 2})
        self.assertEqual(report["model"]["surfaces"]["wrong"], ["desk"])
        self.assertIn("d-on", by_id(report["decisions"]["not_verified"], "decision_id", "d-off")["reason"])

    def test_a_decision_the_model_cannot_predict_is_not_held_against_the_map(self):
        t = syn.T0
        data = stage1_model(FOUR)
        bad = decision("d-bad", t + 0.5, t, {B0: 0.2}, {B0: {"level": 0.8, "steps": send(t + 0.5)}}, ("p0",))
        bad["applied_scene"][B0]["readback"]["cct_k"] = None
        bad["predicted"]["surfaces"] = {"p0": 1.0, "unit": "relative"}
        good = decision("d-good", t + 100.5, t + 100, {B0: 0.2}, {B0: {"level": 0.8, "steps": send(t + 100.5)}}, ("p0",))
        good["predicted"]["surfaces"] = {"p0": 80.0, "unit": "relative"}
        frames = [syn.light_state(t, {"p0": 20.0}, anchored=False), syn.light_state(t + 5, {"p0": 80.0}, anchored=False),
                  syn.light_state(t + 100, {"p0": 20.0}, anchored=False), syn.light_state(t + 105, {"p0": 80.0}, anchored=False)]
        _, _, report = Run(closed(), LightModel(data), [bad, good], frames, clock=Clock(syn.UTC_OFFSET_S)).run()
        self.assertEqual(report["decisions"]["verified"], 1)
        self.assertEqual(by_id(report["decisions"]["not_verified"], "decision_id", "d-bad")["kind"], "model error")
        self.assertIn("cct_k", by_id(report["decisions"]["not_verified"], "decision_id", "d-bad")["reason"])

    def test_the_decisions_own_prediction_agrees_to_its_rounding(self):
        for logged in (80.0, 80.0004, 79.9996, 80.0):
            report, _ = self.own_prediction(logged, {"desk": ["p0"]})
            self.assertEqual(report["decisions"]["verified"], 1, logged)
        report, _ = self.own_prediction(80.002, {"desk": ["p0"]})
        self.assertEqual(report["decisions"]["verified"], 0)

    def test_a_null_prediction_in_the_decision_is_nothing_to_compare(self):
        report, _ = self.own_prediction(None, {"desk": ["p0"]})
        self.assertEqual(report["decisions"]["verified"], 1)

    def test_a_later_hold_does_not_read_as_a_map_that_disagrees(self):
        # the decision's prediction is for its own read-backs; a person's hold past its copy moves the verify, not the comparison
        t = syn.T0
        data = stage1_model(FOUR)
        ref = syn.light_state(t, {"p0": 25.0}, anchored=False)
        after = syn.light_state(t + 9, {"p0": 84.0}, anchored=False)
        dec = decision("d", t + 0.5, t, {B0: 0.2, B1: 0.5}, {B0: {"level": 0.8, "steps": send(t + 0.5)}, B1: {"level": 0.5, "steps": []}}, ("p0",))
        dec["predicted"]["surfaces"] = {"p0": 85.0, "unit": "relative"}
        correction = {"correction_id": "c", "ts": t + 4, "action": {"settle_s": 0.0}, "snapshot": {"light_state": []},
                      "hold": {"entity_id": B1, "channels": [B1], "pinned": {"level_pct": 40, "cct_k": 2202}, "since": t + 4, "ended": None, "hold_set_revision": 9}}
        decisions, _, report = Run(closed(), LightModel(data), [dec], [ref, after], corrections=[correction], clock=Clock(syn.UTC_OFFSET_S)).run()
        self.assertEqual(report["decisions"]["verified"], 1, report["decisions"])
        self.assertEqual(point(decisions[0], "p0")["status"], "within")
        self.assertEqual(report["hit_rate"]["hits"], 1)

    def test_a_synthetic_fixture_is_held_to_it_too(self):
        # every model here is Stage 1's shape, the synthetic ones included: a placeholder in predicted.surfaces is a wrong map
        t = syn.T0
        model = syn.hue_model({"p0": {B0: 100.0}}, kelvins=(2700,))
        dec = syn.hue_decision("d", t + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": send(t + 0.5)}}, "ls-r", targets={"A": {"surface_id": "p0"}})
        frames = [syn.light_state(t, {"p0": 20.0}, anchored=False, ident="ls-r"), syn.light_state(t + 5, {"p0": 80.0}, anchored=False)]
        for logged, hits in ((0, 0), (80.0, 1)):
            dec["predicted"]["surfaces"] = {"p0": logged, "unit": "relative"}
            _, _, report = Run(closed(), LightModel(model), [copy.deepcopy(dec)], frames, clock=Clock(syn.UTC_OFFSET_S)).run()
            self.assertEqual(report["hit_rate"]["hits"], hits, logged)


class Walkthrough(Temp):
    """The walkthrough's model, in Stage 1's shape: its numbers, and a copy marked fake, which plumbing never gates on."""

    def evening(self, closed_method=True, fake=True):
        d, f, c = syn.v11_evening()
        return self.case({**syn.v11_model(), "fake": fake}, d, f, closed_method=closed_method)

    def test_a_scored_run_on_it_marked_fake_is_refused_naming_why_with_nothing_written(self):
        argv, folder = self.evening()
        code, printed = self.main(*argv)
        self.assertEqual(code, 2, printed)
        self.assertIn("refused", printed)
        self.assertIn("marked fake", printed)
        self.assertIn("plumbing only, never a gate", printed)
        self.assertIn("--preview", printed)
        self.assertFalse((folder / "out").exists())

    def test_a_preview_on_it_marked_fake_runs_and_says_what_it_ran_on(self):
        argv, folder = self.evening()
        code, printed = self.main(*argv, "--preview")
        self.assertEqual(code, 0, printed)
        self.assertIn("plumbing only", printed)
        report = json.loads((folder / "out" / "report-preview.json").read_text("utf-8"))
        self.assertIn("marked fake", report["backend"])
        self.assertTrue(report["model"]["fake"])
        self.assertEqual(report["predict_sha256"], hashlib.sha256(REAL_PREDICT.read_bytes()).hexdigest())

    def test_an_open_slot_is_said_first(self):
        argv, _ = self.evening(closed_method=False)
        code, printed = self.main(*argv)
        self.assertEqual(code, 2)
        self.assertIn("open slot", printed)

    def test_its_numbers_do_not_move(self):
        model = LightModel(syn.v11_model())
        self.assertAlmostEqual(model.predict({"F1.spot": {"level": 0.5, "cct_k": 3500}})["counter-1"], 343.0, places=6)
        self.assertAlmostEqual(model.predict({"F1.spot": {"level": 0.7, "cct_k": 3500}})["counter-1"], 485.0, places=6)
        with self.assertRaisesRegex(KeyError, "no channel"):
            model.predict({"F9": {"level": 0.5, "cct_k": 3500}})


class NoSibling(Temp):
    """A model when `import predict` found no predict.py: refused, in one sentence."""

    def setUp(self):
        super().setUp()
        patcher = no_sibling()
        patcher.start()
        self.addCleanup(patcher.stop)

    def stage1_case(self, *flags, closed_method=False):
        t = syn.T0
        dec = decision("d", t + 0.5, t, {B0: 0.2}, {B0: {"level": 0.8, "steps": send(t + 0.5)}}, ("p0",))
        frames = [syn.light_state(t, {"p0": 20.0}, anchored=False), syn.light_state(t + 5, {"p0": 80.0}, anchored=False)]
        argv, folder = self.case(stage1_model(FOUR), [dec], frames, closed_method=closed_method)
        return self.main(*argv, *flags), folder

    def test_refused_with_exit_2_naming_why_scored_or_previewed(self):
        for name, flags, closed_method in (("preview", ("--preview",), False), ("scored", (), True)):
            with self.subTest(name):
                (code, printed), folder = self.stage1_case(*flags, closed_method=closed_method)
                self.assertEqual(code, 2, printed)
                self.assertIn("rung 8's", printed)
                self.assertIn(str(light_model.PREDICT_PATH), printed)
                self.assertNotIn("Traceback", printed)
                self.assertFalse((folder / "out").exists())
                shutil.rmtree(folder)

    def test_there_is_no_fake_to_fall_back_to(self):
        self.assertFalse((HERE / "fake_model.py").exists())
        with self.assertRaisesRegex(BadFile, "predict.py"):
            LightModel(stage1_model(FOUR))

    def test_another_predict_py_answering_the_import_is_refused_by_name(self):
        (self.tmp / "predict.py").write_text("", encoding="utf-8")
        other = mock.Mock(__file__=str(self.tmp / "predict.py"))
        with mock.patch.object(light_model, "predict", other), self.assertRaisesRegex(BadFile, "finds .*predict.py"):
            LightModel(stage1_model(FOUR))

    def test_no_environment_variable_redirects_the_backend(self):
        env = {"DEN_LIGHT_DIR": str(self.tmp), "DEN_LIGHT": str(self.tmp), "PREDICT_PATH": str(self.tmp)}
        with mock.patch.dict(os.environ, env), self.assertRaises(BadFile):
            LightModel(stage1_model(FOUR))
        for name in ("light_model", "verify", "score", "records", "settle", "method"):
            tree = ast.parse((HERE / (name + ".py")).read_text(encoding="utf-8"))
            imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            self.assertNotIn("os", imports, name)
            self.assertFalse([n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in ("environ", "getenv")], name)


class BrokenPredict(Temp):
    """den-light's predict.py broken or missing, in a copy of the two folders: verify.py says so in one sentence."""

    def run_copy(self, predict_text):
        tools = self.tmp / "tools"
        shutil.copytree(HERE, tools / "den-verify", ignore=shutil.ignore_patterns("test_*", "__pycache__"))
        (tools / "den-light").mkdir()
        if predict_text is not None:
            (tools / "den-light" / "predict.py").write_text(predict_text, encoding="utf-8")
        d, f, c = syn.v11_evening()
        argv, folder = self.case(syn.v11_model(), d, f, name="run")
        command = [sys.executable, str(tools / "den-verify" / "verify.py"), *map(str, argv), "--preview"]
        return tools, folder, subprocess.run(command, capture_output=True, text=True, timeout=120)

    def test_a_predict_py_that_will_not_load_is_a_sentence(self):
        for text in ("def broken(:\n", "raise KeyError('x')\n", "def predict(scene, model):\n    return {}\n", None):
            with self.subTest(text):
                tools, folder, done = self.run_copy(text)
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertEqual(len(done.stdout.strip().splitlines()), 1, done.stdout)
                self.assertIn("predict.py", done.stdout)
                self.assertNotIn("Traceback", done.stdout + done.stderr)
                self.assertFalse((folder / "out").exists())
                shutil.rmtree(tools)
                shutil.rmtree(folder)

    def test_it_leaves_no_pycache_beside_the_file(self):
        tools, _, done = self.run_copy(REAL_PREDICT.read_text(encoding="utf-8"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse((tools / "den-light" / "__pycache__").exists())


class WrongStage1Model(Temp):
    """Refused at the file by what is wrong, before any backend is looked for, so these hold with or without a sibling."""
    KEY = key_of(B0)
    WRONG = (
        ("version not text", {"version": 5}), ("no unit", {"basis_unit": None}), ("unit not text", {"basis_unit": 5}), ("fake not a flag", {"fake": "no"}),
        ("no points", {"sample_points": None}), ("points not a list", {"sample_points": {}}), ("no points in it", {"sample_points": []}), ("a point not an object", {"sample_points": [5]}),
        ("a point with no id", {"sample_points": [{"kind": None}]}), ("two points one id", {"sample_points": [{"point_id": "p0"}, {"point_id": "p0"}]}),
        ("an empty channel list", {"channels": []}), ("a channel not an object", {"channels": [5]}), ("a channel with no key", {"channels": [{"entity_id": B0}]}),
        ("a channel with no entity", {"channels": [{"channel_key": "k"}]}), ("a key not text", {"channels": [{"channel_key": 5, "entity_id": B0}]}),
        ("two channels one key", {"channels": [{"channel_key": "k", "entity_id": B0}, {"channel_key": "k", "entity_id": B1}]}),
        ("two channels one entity", {"channels": [{"channel_key": "k", "entity_id": B0}, {"channel_key": "j", "entity_id": B0}]}),
        ("no cct", {"cct": None}), ("cct not an object", {"cct": 5}), ("no dimming", {"dimming": None}), ("dimming not an object", {"dimming": 5}),
        ("no basis", {"basis": None}), ("basis not an object", {"basis": 5}), ("a channel with no cct", {"cct": {}}), ("a channel with no dimming", {"dimming": {}}),
        ("surfaces not a map", {"surfaces": 5}), ("a surface not a list", {"surfaces": {"a": 5}}), ("a block not text", {"surfaces": {"a": [5]}}),
    )

    def change_channel(self, model, name, value):
        at = {"kelvin": ("cct", "kelvin"), "basis_keys": ("cct", "basis_keys"), "levels": ("dimming", "levels"), "g": ("dimming", "g")}[name]
        model[at[0]][self.KEY][at[1]] = value
        return model

    def assert_refused(self, model, name):
        path = self.write("wrong.json", model)
        with self.subTest(name), no_sibling():
            with self.assertRaisesRegex(BadFile, "not a light model"):
                LightModel.load(path)

    def test_each_is_a_sentence_saying_it_is_not_a_light_model(self):
        for name, change in self.WRONG:
            model = {**stage1_model(FOUR), **change}
            self.assert_refused({k: v for k, v in model.items() if v is not None or k not in change}, name)

    def test_inside_a_channels_tables(self):
        for name, key, value in (("kelvin text", "kelvin", ["warm", 3294, 6535]), ("kelvin 0", "kelvin", [0, 3294, 6535]), ("a kelvin twice", "kelvin", [2202, 2202, 6535]),
                                 ("no kelvin", "kelvin", []), ("keys not a list", "basis_keys", 5), ("keys too few", "basis_keys", ["x"]),
                                 ("levels not rising", "levels", [254, 0]), ("levels text", "levels", ["a", "b"]), ("no levels", "levels", []),
                                 ("g text", "g", ["a", "b"]), ("g too short", "g", [0.0])):
            self.assert_refused(self.change_channel(stage1_model(FOUR), key, value), name)

    def test_inside_a_basis_row(self):
        for name, row in (("not an object", 5), ("a value not a number", {"p0": "x", "p1": 1.0, "p2": 1.0, "p3": 1.0}), ("a block left out", {"p0": 1.0})):
            model = stage1_model(FOUR)
            model["basis"][f"{self.KEY}@2202K"] = row
            self.assert_refused(model, name)
        model = stage1_model(FOUR)
        del model["basis"][f"{self.KEY}@2202K"]
        self.assert_refused(model, "a basis key that names nothing")

    def test_a_null_block_in_a_row_is_how_stage_1_writes_excluded_and_is_read(self):
        model = stage1_model(FOUR, excluded=("p1",))
        light_model.check_shape(model, "the model")

    def test_real_fields_nothing_here_reads_are_not_looked_at(self):
        model = stage1_model(FOUR)
        model["error"] = "anything"
        model["glare"] = 5
        light_model.check_shape(model, "the model")

    def test_nothing_refused_here_would_have_got_through_to_the_backend(self):
        with real_backend():
            LightModel(stage1_model(FOUR))
            for name, change in self.WRONG:
                model = {**stage1_model(FOUR), **change}
                with self.subTest(name), self.assertRaises(BadFile):
                    LightModel({k: v for k, v in model.items() if v is not None or k not in change})


class SurfaceMapFile(Temp):
    def argv(self, surfaces):
        d, f, c = syn.v11_evening()
        argv, _ = self.case(syn.v11_model(), d, f, name="c" + str(len(list(self.tmp.iterdir()))))
        return argv + ["--surfaces", surfaces, "--preview"]

    def test_utf16_gets_the_one_sentence(self):
        path = self.tmp / "s16.json"
        path.write_bytes(b"\xff\xfe" + '{"a": ["p0"]}\r\n'.encode("utf-16-le"))
        code, printed = self.main(*self.argv(path))
        self.assertEqual((code, printed.strip()), (2, "%s is UTF-16 (PowerShell 5.1's > writes that): save it as UTF-8" % path))

    def test_a_bom_and_crlf_read(self):
        path = self.tmp / "bom.json"
        path.write_bytes(b"\xef\xbb\xbf" + '{"counter-1": ["counter-1"],\r\n "armchair-book": ["armchair-book"]}\r\n'.encode("utf-8"))
        self.assertEqual(self.main(*self.argv(path))[0], 0)

    def test_wrong_shape_missing_or_malformed_is_a_sentence(self):
        for name, text in (("not json", "{"), ("a list", "[]"), ("a number", "5"), ("a value not a list", '{"a": 5}'), ("a block not text", '{"a": [5]}'), ("nan", '{"a": NaN}')):
            path = self.write(name.replace(" ", "-") + ".json", text)
            with self.subTest(name):
                code, printed = self.main(*self.argv(path))
                self.assertEqual(code, 2, printed)
                self.assertIn(str(path), printed)
                self.assertNotIn("Traceback", printed)
        code, printed = self.main(*self.argv(self.tmp / "no-such.json"))
        self.assertEqual(code, 2, printed)
        self.assertIn("no-such.json", printed)

    def test_an_empty_map_is_read_and_every_surface_is_outside_it(self):
        model = LightModel(syn.v11_model(), surfaces={})
        self.assertEqual(model.surfaces, {})
        self.assertIn("--surfaces", model.surface_problem("counter-1"))


class Docs(unittest.TestCase):
    def test_the_release_fragment_says_what_is_now_true(self):
        fragment = HERE.parents[1] / "changes" / "unreleased" / "den-verify-rung-10.md"
        if not fragment.exists():  # a release moves it to the archive
            return
        said = " ".join(fragment.read_text("utf-8").split())
        for words in ("`predict.py`", "`decisions.jsonl`", "`surfaces.json`", "--surfaces", "a scored run on a model marked fake is refused",
                      "A decision counts as verified only when something was judged", "unknown, never 0", "UTF-16", "from before a person changed a light"):
            self.assertIn(words, said)
        self.assertNotIn("Stage 1 not built", said)

    def test_the_note_says_where_a_surface_comes_from(self):
        said = " ".join((HERE / "VERIFY-V1.md").read_text("utf-8").split())
        self.assertIn("A surface's value is the mean over its blocks that have a value, with none if none do", said)
        self.assertIn("through rung 8's own `predict.py`", said)

    def test_the_docstrings_say_what_is_called(self):
        said = " ".join(light_model.__doc__.split())
        self.assertIn("tools/den-light/predict.py", said)
        self.assertNotIn("_backend_predict's body", said)
        self.assertNotIn("did not exist when", said)
        self.assertNotIn("fake_model", said)

    def test_readme_says_what_the_backend_is(self):
        said = " ".join((HERE / "README.md").read_text("utf-8").split())
        self.assertIn("`--surfaces`", said)
        self.assertNotIn("until Stage 1's `predict()` exists", said)


if __name__ == "__main__":
    unittest.main()
