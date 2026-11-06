"""The seam between rungs 8, 9 and 10, with the real tools: a real Stage 1 light-model.json from den-light's own fit, the real
coordinator through run.py, its real decisions.jsonl and surfaces.json through verify.py. The frames come from a truth these tests
control: predict.py's own numbers plus an ambient offset, a frame every few seconds.

It needs den-core and den-light beside this folder, so it runs for real on the merged tree. On a branch without them, set
DEN_CORE_DIR and DEN_LIGHT_DIR to their folders, which only this test reads; else it skips, saying so. Everything is synthetic.

    python -B -m unittest discover -s tools/den-verify -p "test_*.py"

The truth follows each Decision's own claims, because den-core's plant echoes den_lighting and not the coordinator, so what a
Decision reads in inputs.actuator_state is not always where its previous Decision left the bulbs. The frame a Decision reads shows
what it says it read, and the frames after it has settled show its applied_scene, or the fault a test asks for. Each scenario
writes down its expected verdict and the arithmetic behind it, then reads what verify.py says.
"""
import bisect
import copy
import importlib.util
import json
import os
import random
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
import verify as cli  # noqa: E402
from light_model import LightModel  # noqa: E402
from records import Clock, write_jsonl  # noqa: E402
from score import Dec, Run  # noqa: E402
from test_verify import Quiet, by_id, closed  # noqa: E402

CORE = Path(os.environ.get("DEN_CORE_DIR") or HERE.parent / "den-core")
LIGHT = Path(os.environ.get("DEN_LIGHT_DIR") or HERE.parent / "den-light")
WHY_SKIPPED = ("needs real den-core and real den-light beside this folder: these tests run on the merged tree, "
               "or set DEN_CORE_DIR and DEN_LIGHT_DIR to their folders")
FRONT_LEFT, FRONT_RIGHT, REAR_LEFT, REAR_RIGHT = "light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right"
EVERY_S = 3.1
AMBIENT = {"counter-1": 0.02, "armchair-book": 0.05, "wall-west": 0.01, "centre": 0.03, "room": 0.02, "dead": 0.02}
CORE_FLAGS = ("--synthetic", "evening", "--touch", "--clean", "--policies", "need", "incumbent", "coordinator")

FIT = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import model, synthetic, test_stage1 as T
m = T.method1()
den = synthetic.SyntheticDen(block_px=40, noise=1e-4)
r = T.readings_for(den, m)
fitted = model.fit(r, r["temperatures"], m)
scored = model.score(r, fitted, m)
lm = model.light_model(fitted, T.FIXTURES, m, session_id="seam-1", readings_sha256="0" * 64, meter_norm_sha256="1" * 64, score_error=scored["error"])
Path(sys.argv[2]).write_text(json.dumps(lm, ensure_ascii=False, allow_nan=False), encoding="utf-8", newline="\n")
'''
RUN_CORE = r'''
import sys
from pathlib import Path
core, light = sys.argv[1:3]
sys.path.insert(0, core)
import light_adapter, run
light_adapter.PREDICT_PATH = Path(light) / "predict.py"
sys.exit(run.cli(sys.argv[3:]))
'''
HAS_POLICY = r'''
import sys
sys.path.insert(0, sys.argv[1])
import incumbent
sys.exit(0 if incumbent.find_policy() else 1)
'''


def blocks(rows, cols):
    return ["r%02dc%02d" % (r, c) for r in rows for c in cols]


def python(*argv, cwd=None):
    return subprocess.run([sys.executable, "-B", *map(str, argv)], capture_output=True, text=True, cwd=cwd, timeout=300)


def lines_of(path):
    return [json.loads(line) for line in Path(path).read_text("utf-8").splitlines() if line.strip()]


def without_light_state(decision):
    return {**decision, "inputs": {k: v for k, v in decision["inputs"].items() if not k.startswith("light_state")}}


class Truth:
    """What the camera would show, from predict.py alone: each bulb's value at each block, a surface's mean over its blocks with a
    value, an ambient added. It shares nothing with den-verify's adapter but predict.py itself."""

    def __init__(self, model, surfaces):
        spec = importlib.util.spec_from_file_location("_seam_truth_predict", LIGHT / "predict.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.prepared = self.module.prepare(model)
        self.keys = {c["entity_id"]: c["channel_key"] for c in model["channels"]}
        self.surfaces = surfaces
        self.memo = {}

    def part(self, bulb, scene):
        """{surface: this bulb's light}, 0 for a bulb that is off; a surface with no block of a value has none at all."""
        level, kelvin = scene["level"], scene["cct_k"]
        tag = (bulb, level, kelvin)
        if tag not in self.memo:
            at = self.module.predict({} if not level else {self.keys[bulb]: {"level": level, "cct_k": kelvin}}, self.prepared)["surfaces"]
            out = {}
            for name, names in self.surfaces.items():
                known = [at[p] for p in names if at[p] is not None]
                out[name] = sum(known) / len(known) if known else 0.0
            self.memo[tag] = out
        return self.memo[tag]

    def light(self, scene_by_bulb):
        """{surface: ambient + the light of each bulb, times any factor}, from {bulb: (scene, factor)}."""
        out = {}
        for name in self.surfaces:
            out[name] = AMBIENT.get(name, 0.02) + sum(factor * self.part(b, s)[name] for b, (s, factor) in scene_by_bulb.items())
        return out


def light_state(ts, values):
    """One LightState in the shape den-photometry writes to light_state.jsonl: no id, ts present, window_daylight []."""
    return {"record": "light_state", "contract": "live-v1", "ts": ts, "camera_id": "den-oak", "room_version": "den-seam",
            "exposure": {"time_ms": 10.0, "gain": 1.0, "black_level": None, "wb_locked": True, "linear": True, "normalisation_version": "meter-norm/v1"},
            "anchored": False,
            "surface_estimates": [{"surface_id": s, "value": v, "unit": "relative", "clipped_frac": 0.0, "rho_status": "assumed", "basis": "measured"} for s, v in values.items()],
            "window_daylight": [], "sun": None, "blinds": "unknown", "residual": []}


def said(decision, bulb, where):
    """A bulb's canon {level, cct_k}, from what the Decision read or what it applied, with the state it read for a bulb it does not list."""
    if where == "applied" and bulb in decision["applied_scene"]:
        one = decision["applied_scene"][bulb]["readback"]
    else:
        one = decision["inputs"]["actuator_state"][bulb]
    return {"level": one["level"], "cct_k": one["cct_k"]}


class Seam(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ((CORE / "run.py").is_file() and (LIGHT / "predict.py").is_file() and (LIGHT / "model.py").is_file()):
            raise unittest.SkipTest(WHY_SKIPPED)
        if python("-c", HAS_POLICY, CORE).returncode:
            raise unittest.SkipTest("den_lighting's policy.py, which den-core's incumbent runs, is not in this checkout")
        cls.tmp = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, cls.tmp)
        cls.patcher = mock.patch.object(light_model, "PREDICT_PATH", LIGHT / "predict.py")
        cls.patcher.start()
        cls.addClassCleanup(cls.patcher.stop)
        cls.model_path = cls.tmp / "light-model.json"
        fit = python("-c", FIT, LIGHT, cls.model_path, cwd=cls.tmp)
        if fit.returncode:
            raise AssertionError(fit.stderr)
        cls.model = json.loads(cls.model_path.read_text("utf-8"))
        cls.excluded = [p["point_id"] for p in cls.model["sample_points"] if p.get("excluded")]
        assert cls.excluded, "the fit excluded no block: the tests of an excluded block need one"
        named = {"counter-1": blocks(range(3), range(5)), "armchair-book": blocks(range(6, 9), range(11, 16)),
                 "wall-west": blocks(range(3, 6), range(3)), "centre": blocks(range(3, 6), range(6, 10)), "dead": cls.excluded}
        cls.given_map = cls.tmp / "map.json"
        cls.given_map.write_text(json.dumps(named), encoding="utf-8")
        cls.first = cls.run_core("first")
        cls.surfaces_path = cls.first / "surfaces.json"
        cls.surfaces = json.loads(cls.surfaces_path.read_text("utf-8"))
        cls.first_pass = lines_of(cls.first / "decisions.jsonl")
        cls.truth = Truth(cls.model, cls.surfaces)
        assert len(cls.first_pass) >= 8
        cls.times = [d["ts"] for d in cls.first_pass]
        cls.bulbs = list(cls.first_pass[0]["inputs"]["actuator_state"])
        start, end = cls.times[0] - 30.37, cls.times[-1] + 40
        cls.grid = [round(start + n * EVERY_S, 3) for n in range(int((end - start) / EVERY_S))]
        cls.frames_path = cls.tmp / "frames.jsonl"
        write_jsonl(cls.frames_path, cls.frames(cls.first_pass))
        cls.second = cls.run_core("second", "--light-states", cls.frames_path)
        cls.decisions = lines_of(cls.second / "decisions.jsonl")
        cls.decisions_path = cls.second / "decisions.jsonl"
        cls.frames_list = cls.frames(cls.decisions)

    @classmethod
    def run_core(cls, name, *more, model=None, flags=CORE_FLAGS):
        out = cls.tmp / ("core-" + name)
        done = python("-c", RUN_CORE, CORE, LIGHT, *flags, "--light-model", model or cls.model_path, "--surfaces", cls.given_map, *more, "--out", out)
        if done.returncode:
            raise AssertionError(done.stdout + done.stderr)
        return out

    # -- frames from the truth -----------------------------------------------------------------------------------------------

    @classmethod
    def frames(cls, decisions, landed=None, read=None):
        """One frame per grid time. landed(decision, bulb, scene) gives (scene, factor) for how a bulb landed; read(decision, bulb,
        scene) the scene the frame that decision reads shows. Left out, each is what the Decision says."""
        times = [d["ts"] for d in decisions]
        read_by = {}
        for d in decisions:
            read_by[cls.grid[bisect.bisect_right(cls.grid, d["ts"]) - 1]] = d
        out = []
        for t in cls.grid:
            if t in read_by:
                d = read_by[t]
                state = {b: ((read or (lambda _d, _b, s: s))(d, b, said(d, b, "before")), 1.0) for b in cls.bulbs}
            else:
                k = bisect.bisect_right(times, t) - 1
                d = decisions[max(k, 0)]
                state = {}
                for b in cls.bulbs:
                    if k < 0:
                        state[b] = (said(d, b, "before"), 1.0)
                    else:
                        scene = said(d, b, "applied")
                        state[b] = landed(d, b, scene) if landed else (scene, 1.0)
            out.append(light_state(t, cls.truth.light(state)))
        return out

    # -- verify.py -----------------------------------------------------------------------------------------------------------

    def verify(self, frames, decisions=None, surfaces=True, preview=True, corrections=None, model=None, scored_method=False, occupancy=None):
        """(exit code, console, report, the records it wrote or None). A scored run closes every slot at its proposal in a copy."""
        folder = Path(tempfile.mkdtemp(dir=self.tmp))
        frames_path = folder / "frames.jsonl"
        write_jsonl(frames_path, frames)
        if decisions is None:
            decisions_path = self.decisions_path
        elif isinstance(decisions, (str, Path)):
            decisions_path = decisions
        else:
            decisions_path = folder / "decisions.jsonl"
            write_jsonl(decisions_path, decisions)
        argv = []
        if not preview:
            (folder / "VERIFY-V1.md").write_text(method.closed_copy(method.lf_text(HERE / "VERIFY-V1.md")), encoding="utf-8", newline="\n")
            argv += ["--method", str(folder / "VERIFY-V1.md")]
        argv += ["run", "--decisions", str(decisions_path), "--light-states", str(frames_path), "--model", str(model or self.model_path),
                 "--utc-offset-h", "-7", "--out", str(folder / "out")]
        if surfaces is True:
            argv += ["--surfaces", str(self.surfaces_path)]
        elif surfaces:
            argv += ["--surfaces", str(surfaces)]
        if corrections is not None:
            write_jsonl(folder / "corrections.jsonl", corrections)
            argv += ["--corrections", str(folder / "corrections.jsonl")]
        if occupancy is not None:
            write_jsonl(folder / "occupancy.jsonl", occupancy)
            argv += ["--occupancy", str(folder / "occupancy.jsonl")]
        if preview:
            argv.append("--preview")
        with Quiet() as q:
            code = cli.main(argv)
        printed = q.buf.getvalue()
        name = "report-preview.json" if preview else "report.json"
        report = json.loads((folder / "out" / name).read_text("utf-8")) if (folder / "out" / name).exists() else None
        written = lines_of(folder / "out" / "decisions.verified.jsonl") if (folder / "out" / "decisions.verified.jsonl").exists() else None
        return code, printed, report, written

    def scored(self, frames, **kw):
        """A scored run, so each point's record is there to read: (console, report, records). Not refused on this real backend."""
        code, printed, report, written = self.verify(frames, preview=False, **kw)
        self.assertEqual(code, 0, printed)
        return printed, report, written

    def expected(self, decision, landed):
        """{surface: status} by the arithmetic alone, each reading against the light predicted for what the Decision applied: within
        when it is inside the bound the record gives. Where they agree, verify.py read the frame it should have, and judged it."""
        faulty, right = {}, {}
        for b in self.bulbs:
            scene = said(decision, b, "applied")
            faulty[b], right[b] = landed(decision, b, scene), (scene, 1.0)
        return self.truth.light(faulty), self.truth.light(right)

    def assert_reads_as_the_arithmetic_says(self, written, landed):
        """Every point of every decision with a verify frame. Returns (hits, misses) seen, and {decision_id: {surface: status}}."""
        by_decision, hits, misses = {}, 0, 0
        for d, verified in zip(self.decisions, written):
            self.assertEqual(d["decision_id"], verified["decision_id"])
            reading, predicted = self.expected(d, landed)
            statuses = {}
            for p in verified["verify"]["points"]:
                s = p["point_id"]
                if s == "dead":
                    continue
                bound = max(0.15 * predicted[s], p.get("level_bound") or 0.0)
                error = abs(reading[s] - predicted[s])
                if abs(error - bound) > 0.002:  # the record's bound is rounded to 3 places
                    self.assertEqual(p["status"], "within" if error <= bound else "outside", (d["decision_id"], s, error, bound))
                statuses[s] = p["status"]
                hits += p["status"] == "within"
                misses += p["status"] == "outside"
            by_decision[d["decision_id"]] = statuses
        return hits, misses, by_decision

    # -- the second pass matches the first ---------------------------------------------------------------------------------------

    def test_the_frames_change_nothing_a_decision_says_but_which_frame_it_read(self):
        self.assertEqual([without_light_state(d) for d in self.decisions], [without_light_state(d) for d in self.first_pass])
        self.assertTrue(all(d["inputs"]["light_state_ts"] <= d["ts"] for d in self.decisions))
        self.assertTrue(all(d["inputs"]["light_state_status"] == "no LightState given" for d in self.first_pass))

    def test_what_the_tools_ran_on_is_the_real_one(self):
        log = (self.second / "log.jsonl").read_text("ascii").splitlines()[0]
        self.assertIn("den-light predict.py", log)
        self.assertFalse(self.model.get("fake"))
        self.assertNotIn("surfaces", self.model)
        self.assertEqual(sorted(self.surfaces), sorted(["counter-1", "armchair-book", "wall-west", "centre", "dead", "room"]))

    def test_the_prediction_for_a_decisions_applied_state_equals_its_own_to_its_rounding(self):
        light = LightModel(self.model, surfaces=self.surfaces)
        run = Run(closed(), light, self.decisions, self.frames_list, clock=Clock(-7 * 3600))
        compared = 0
        for d in run.decs:
            mine = light.predict(d.state_at(d.ts, ())[0])
            logged = d.rec["predicted"]["surfaces"]
            for s, value in logged.items():
                if s == "unit":
                    continue
                if value is None:
                    self.assertIsNone(mine[s], s)
                    continue
                self.assertEqual(round(mine[s], 3), value, (d.id, s))
                compared += 1
        self.assertGreaterEqual(compared, 9 * 5)

    def test_it_equals_it_under_every_compensation_option_and_with_a_game(self):
        for name, more in (("p0", ("--compensation", "P0")), ("p2", ("--compensation", "P2", "--compensation-form", "strict")),
                           ("p3", ("--compensation", "P3", "--compensation-form", "mode"))):
            with self.subTest(name):
                self.assert_own_prediction_agrees(self.run_core(name, *more))
        self.assert_own_prediction_agrees(self.run_core("game", flags=(*CORE_FLAGS, "--game")))

    def assert_own_prediction_agrees(self, out):
        decisions = lines_of(out / "decisions.jsonl")
        self.assertTrue(decisions)
        light = LightModel(self.model, surfaces=json.loads((out / "surfaces.json").read_text("utf-8")))
        run = Run(closed(), light, decisions, [], clock=Clock(-7 * 3600))
        for d in run.decs:
            mine = light.predict(d.state_at(d.ts, ())[0])
            for s, value in d.rec["predicted"]["surfaces"].items():
                if s != "unit" and value is not None:
                    self.assertEqual(round(mine[s], 3), value, (d.id, s))

    # -- 1. correct execution --------------------------------------------------------------------------------------------------

    def test_1_correct_execution_every_counted_point_hits_verified_n_of_n_and_the_gate_passes(self):
        code, printed, report, _ = self.verify(self.frames_list)
        self.assertEqual(code, 0, printed)
        n = len(self.decisions)
        self.assertEqual((report["decisions"]["verified"], report["decisions"]["total"]), (n, n), report["decisions"]["not_verified"])
        hr = report["hit_rate"]
        self.assertEqual(hr["misses"], 0, report["rows"])
        self.assertEqual(hr["hits"] + report["excluded_held_or_superseded"], 5 * n)  # five surfaces with a value; the hold rules leave a few out
        self.assertLess(report["excluded_held_or_superseded"], 5)
        self.assertEqual(hr["verdict"], "pass")
        self.assertEqual(report["points"]["outside"], 0)
        self.assertEqual(report["backend"], "den-light predict.py")
        self.assertTrue(printed.startswith(f"decisions verified {n} of {n}, 0 not verified; hit rate 1.0 ("), printed)
        self.assertIn("gate 0.85: pass (preview)", printed)

    def test_1_a_scored_run_on_it_reaches_the_same_verdict_and_writes_its_records(self):
        printed, report, written = self.scored(self.frames_list)
        self.assertEqual(report["hit_rate"]["verdict"], "pass")
        self.assertTrue(report["scored"])
        self.assertEqual(len(written), len(self.decisions))
        self.assertTrue(all(d["verify"]["within_tolerance"] is True for d in written))
        self.assertEqual(report["inputs"]["predict_py"], report["predict_sha256"])
        self.assertTrue(report["inputs"]["surfaces"])
        self.assertIn(report["predict_sha256"], (self.second / "log.jsonl").read_text("ascii").splitlines()[0])

    def test_1_each_decision_reads_the_frame_it_names_and_the_first_after_it_settled(self):
        _, _, written = self.scored(self.frames_list)
        for d in written:
            steps = [s for a in d["applied_scene"].values() for s in a["steps"]]
            settled = max((s["sent_at"] + s["transition_s"] + 1 for s in steps), default=d["ts"])
            self.assertEqual(d["verify"]["ts"], self.grid[bisect.bisect_left(self.grid, settled)], d["decision_id"])

    # -- 2. no frames ----------------------------------------------------------------------------------------------------------

    def test_2_no_frames_nothing_counted_insufficient_verified_0(self):
        code, printed, report, _ = self.verify([], decisions=self.first_pass)
        self.assertEqual(code, 0, printed)
        n = len(self.first_pass)
        self.assertEqual(report["decisions"]["verified"], 0)
        self.assertEqual(report["hit_rate"]["verdict"], "insufficient")
        self.assertEqual(report["hit_rate"]["hits"] + report["hit_rate"]["misses"], 0)
        self.assertEqual(report["decisions"]["not_verified_by_kind"], {"no frame after settling": n})
        self.assertIn("no LightState given", report["decisions"]["not_verified"][0]["reason"])
        self.assertTrue(printed.startswith(f"decisions verified 0 of {n}, {n} not verified; hit rate None (0 of 0), gate 0.85: insufficient"), printed)
        self.assertNotIn(f"verified {n} of {n}", printed)

    def test_2_frames_den_core_never_saw_are_no_read_frames(self):
        code, printed, report, _ = self.verify(self.frames_list, decisions=self.first_pass)
        self.assertEqual(code, 0, printed)
        self.assertEqual(report["decisions"]["verified"], 0)
        self.assertEqual(report["decisions"]["not_verified_by_kind"], {"no read frame": len(self.first_pass)})
        self.assertEqual(report["hit_rate"]["verdict"], "insufficient")
        self.assertIn("no LightState given", report["decisions"]["not_verified"][0]["reason"])

    # -- 3 and 4. a bulb that did not do as it was told --------------------------------------------------------------------------

    def fault(self, bulb, kind):
        def landed(d, b, scene):
            if b != bulb:
                return scene, 1.0
            if kind == "stuck":
                return said(d, b, "before"), 1.0
            return scene, {"twice": 2.0, "half": 0.5}[kind]
        return landed

    def test_3_one_bulb_stuck_at_its_old_state_misses_at_the_points_it_lights_and_fails(self):
        landed = self.fault(FRONT_RIGHT, "stuck")
        printed, report, written = self.scored(self.frames(self.decisions, landed=landed))
        hits, misses, statuses = self.assert_reads_as_the_arithmetic_says(written, landed)
        self.assertGreater(misses, 0)
        self.assertGreater(hits, 0)
        self.assertEqual(report["hit_rate"]["verdict"], "fail", report["hit_rate"])
        self.assertIn("gate 0.85: fail", printed)
        # where it was told off and stayed on, it shows where it lights and not where it gives next to nothing
        off = next(d for d in self.decisions if d["applied_scene"][FRONT_RIGHT]["readback"]["on"] is False)
        reading, predicted = self.expected(off, landed)
        missed = {s for s, status in statuses[off["decision_id"]].items() if status == "outside"}
        self.assertTrue(missed, statuses[off["decision_id"]])
        share = {s: self.truth.part(FRONT_RIGHT, said(off, FRONT_RIGHT, "before"))[s] for s in missed}
        self.assertTrue(all(v > 0 for v in share.values()), share)

    def test_3_the_cause_says_what_is_unknown_with_no_window_data_or_occupancy_file(self):
        landed = self.fault(FRONT_RIGHT, "stuck")
        _, report, written = self.scored(self.frames(self.decisions, landed=landed))
        self.assertEqual(report["frames"]["occupied"], "unknown")
        causes = {e["cause"] for d in written for e in d["verify"]["explanation"] or []}
        self.assertEqual(causes, {"unexplained"})
        self.assertEqual(report["misses_explained"]["drift"], 0)
        self.assertEqual(report["misses_explained"]["unknown_inputs"]["window daylight"], report["hit_rate"]["misses"])

    def test_3_with_an_occupancy_file_and_window_data_it_reads_as_drift_and_the_hit_rate_stays(self):
        landed = self.fault(FRONT_RIGHT, "stuck")
        frames = self.frames(self.decisions, landed=landed)
        _, before, _ = self.scored(frames)
        for f in frames:
            f["window_daylight"] = [{"window_id": "west-1", "level": 0.0, "unit": "fraction of noon"}]
        _, after, _ = self.scored(frames, occupancy=[])
        self.assertEqual(after["frames"]["occupied"], 0)
        self.assertGreater(after["misses_explained"]["drift"], 0)
        for key in ("hits", "misses", "value", "verdict"):
            self.assertEqual(after["hit_rate"][key], before["hit_rate"][key], key)

    def test_4_one_bulb_at_twice_its_level_misses_and_fails(self):
        landed = self.fault(REAR_RIGHT, "twice")
        _, report, written = self.scored(self.frames(self.decisions, landed=landed))
        hits, misses, _ = self.assert_reads_as_the_arithmetic_says(written, landed)
        self.assertGreater(misses, 0)
        self.assertEqual(report["hit_rate"]["verdict"], "fail", report["hit_rate"])

    def test_4_one_bulb_at_half_its_level_misses_and_fails(self):
        landed = self.fault(FRONT_LEFT, "half")
        _, report, written = self.scored(self.frames(self.decisions, landed=landed))
        hits, misses, _ = self.assert_reads_as_the_arithmetic_says(written, landed)
        self.assertGreater(misses, 0)
        self.assertEqual(report["hit_rate"]["verdict"], "fail", report["hit_rate"])

    def test_4_every_bulb_stuck_fails_and_is_not_insufficient(self):
        def landed(d, b, scene):
            return said(d, b, "before"), 1.0

        _, report, written = self.scored(self.frames(self.decisions, landed=landed))
        self.assert_reads_as_the_arithmetic_says(written, landed)
        self.assertEqual(report["hit_rate"]["verdict"], "fail", report["hit_rate"])
        self.assertEqual(report["decisions"]["verified"], len(self.decisions))

    # -- 5. a person's change between the read frame and the decision ------------------------------------------------------------

    def person_before(self):
        """A decision with steps, its read frame showing rear_left where it was before a person moved it to what the decision read."""
        target = next(d for d in self.decisions if d["trigger"] == "person_state change")
        read_at = target["inputs"]["light_state_ts"]

        def read(d, b, scene):
            return {**scene, "level": 0.2} if d is target and b == REAR_LEFT else scene

        change = {"correction_id": "c-seam", "ts": target["ts"] - 0.2, "source": "wall switch", "action": {"settle_s": 0.1},
                  "snapshot": {"light_state": [target["inputs"]["light_state"]], "person_state": []}, "hold": None}
        self.assertLess(read_at, change["ts"] - 0.1)
        return target, self.frames(self.decisions, read=read), change

    def test_5_a_persons_change_between_read_frame_and_decision_no_false_miss(self):
        target, frames, change = self.person_before()
        _, report, written = self.scored(frames, corrections=[change])
        self.assertEqual(report["hit_rate"]["misses"], 0, report["hit_rate"])
        self.assertEqual(report["hit_rate"]["verdict"], "pass")
        [stale] = report["decisions"]["read_frame_stale"]
        self.assertEqual(stale["decision_id"], target["decision_id"])
        self.assertIn("c-seam", stale["why"])
        d = by_id(written, "decision_id", target["decision_id"])
        self.assertEqual({p["status"] for p in d["verify"]["points"]}, {"unverifiable"})
        self.assertIsNone(d["verify"]["within_tolerance"])
        self.assertEqual(by_id(report["decisions"]["not_verified"], "decision_id", target["decision_id"])["kind"], "read frame stale")
        self.assertEqual(report["decisions"]["verified"], len(self.decisions) - 1)

    def test_5_with_no_word_of_it_the_same_frames_read_false_misses_which_is_why_the_rule_is_there(self):
        target, frames, _ = self.person_before()
        _, report, written = self.scored(frames)
        self.assertEqual(report["decisions"]["read_frame_stale"], [])
        self.assertGreater(report["hit_rate"]["misses"], 0)
        self.assertIs(by_id(written, "decision_id", target["decision_id"])["verify"]["within_tolerance"], False)

    def test_5_a_change_after_the_decision_does_not_stale_its_frame(self):
        target = next(d for d in self.decisions if d["trigger"] == "person_state change")
        change = {"correction_id": "c-after", "ts": target["ts"] + 0.5, "action": {"settle_s": 0.1}, "snapshot": {"light_state": []}, "hold": None}
        _, report, _ = self.scored(self.frames_list, corrections=[change])
        self.assertEqual(report["decisions"]["read_frame_stale"], [])
        self.assertEqual((report["hit_rate"]["misses"], report["decisions"]["verified"]), (0, len(self.decisions)))

    # -- 6. a held bulb ----------------------------------------------------------------------------------------------------------

    def held_decisions(self):
        held = [d for d in self.decisions if d["inputs"]["hold_set"]["held"]]
        self.assertTrue(held)
        for d in held:
            self.assertEqual(d["applied_scene"][REAR_LEFT]["status"], "held")
        return held

    def test_6_a_held_bulb_at_its_pin_is_no_miss_and_the_free_bulbs_count(self):
        held = self.held_decisions()
        _, report, written = self.scored(self.frames_list)
        self.assertEqual(report["hit_rate"]["misses"], 0)
        for d in held:
            verified = by_id(written, "decision_id", d["decision_id"])["verify"]
            self.assertIs(verified["within_tolerance"], True, d["decision_id"])

    def test_6_a_free_bulbs_fault_still_shows_beside_a_hold(self):
        held = self.held_decisions()
        landed = self.fault(FRONT_LEFT, "twice")
        target = next(d for d in held if d["applied_scene"][FRONT_LEFT]["steps"])

        def only_there(d, b, scene):
            return landed(d, b, scene) if d is target else (scene, 1.0)

        _, report, written = self.scored(self.frames(self.decisions, landed=only_there))
        self.assert_reads_as_the_arithmetic_says(written, only_there)
        self.assertGreater(report["hit_rate"]["misses"], 0)
        for d in written:
            if d["decision_id"] != target["decision_id"]:
                self.assertIs(d["verify"]["within_tolerance"], True, d["decision_id"])

    def person_moves_the_held_bulb(self):
        """After the decision has gone out, a person moves the held bulb to 10%: a Correction past the decision's copy of the holds."""
        target = next(d for d in self.held_decisions() if any(a["steps"] for a in d["applied_scene"].values()))
        revision = target["inputs"]["hold_set"]["revision"]
        change = {"correction_id": "c-held", "ts": target["ts"] + 1.0, "action": {"settle_s": 0.5}, "snapshot": {"light_state": []},
                  "hold": {"entity_id": REAR_LEFT, "pinned": {"level_pct": 10, "cct_k": 2202, "source": "read-back"}, "since": target["ts"] + 1.0,
                           "ended": None, "hold_set_revision": revision + 1}}
        nxt = self.times[self.times.index(target["ts"]) + 1]

        def landed(d, b, scene):
            if d is target and b == REAR_LEFT:
                return {"level": 0.1, "cct_k": 2202}, 1.0
            return scene, 1.0

        frames = self.frames(self.decisions, landed=landed)
        return target, frames, change, nxt

    def test_6_a_person_moving_the_held_bulb_after_the_decision_is_no_false_miss_of_the_free_bulbs(self):
        target, frames, change, _ = self.person_moves_the_held_bulb()
        _, report, written = self.scored(frames, corrections=[change])
        self.assertEqual(report["hit_rate"]["misses"], 0, by_id(written, "decision_id", target["decision_id"])["verify"]["points"])
        self.assertIs(by_id(written, "decision_id", target["decision_id"])["verify"]["within_tolerance"], True)
        self.assertEqual(report["decisions"]["read_frame_stale"], [])

    def test_6_and_a_free_bulbs_fault_beside_that_still_shows(self):
        target, _, change, _ = self.person_moves_the_held_bulb()
        fault = self.fault(FRONT_LEFT, "twice")

        def landed(d, b, scene):
            if d is not target:
                return scene, 1.0
            return ({"level": 0.1, "cct_k": 2202}, 1.0) if b == REAR_LEFT else fault(d, b, scene)

        _, report, written = self.scored(self.frames(self.decisions, landed=landed), corrections=[change])
        self.assertIs(by_id(written, "decision_id", target["decision_id"])["verify"]["within_tolerance"], False)
        causes = {e["cause"] for e in by_id(written, "decision_id", target["decision_id"])["verify"]["explanation"]}
        self.assertNotIn("drift", causes)

    def test_6_a_miss_at_a_point_a_held_bulb_lights_is_never_called_drift(self):
        landed = self.fault(FRONT_LEFT, "twice")
        frames = self.frames(self.decisions, landed=landed)
        for f in frames:
            f["window_daylight"] = [{"window_id": "west-1", "level": 0.0, "unit": "fraction of noon"}]
        _, report, written = self.scored(frames, occupancy=[])
        self.assertGreater(report["misses_explained"]["drift"], 0)  # both inputs given: elsewhere it does read as drift
        for d in self.held_decisions():
            causes = {e["cause"] for e in by_id(written, "decision_id", d["decision_id"])["verify"]["explanation"] or []}
            self.assertNotIn("drift", causes)

    # -- 7. no map -------------------------------------------------------------------------------------------------------------

    def test_7_no_surfaces_not_verified_the_reason_names_surfaces(self):
        code, printed, report, _ = self.verify(self.frames_list, surfaces=False)
        self.assertEqual(code, 0, printed)
        n = len(self.decisions)
        self.assertEqual(report["decisions"]["verified"], 0)
        self.assertEqual(report["decisions"]["not_verified_by_kind"], {"no surface map": n})
        for entry in report["decisions"]["not_verified"]:
            self.assertIn("--surfaces", entry["reason"])
        self.assertEqual(report["hit_rate"]["verdict"], "insufficient")
        self.assertIn("--surfaces", printed)
        self.assertEqual(report["model"]["surfaces"]["source"], "the model's point ids")

    def test_7_a_map_of_other_names_is_not_verified_and_never_a_false_pass(self):
        other = self.tmp / "other-names.json"
        other.write_text(json.dumps({"somewhere-else": self.surfaces["centre"]}), encoding="utf-8")
        code, printed, report, _ = self.verify(self.frames_list, surfaces=other)
        self.assertEqual(code, 0, printed)
        self.assertEqual(report["decisions"]["verified"], 0)
        self.assertEqual(report["hit_rate"]["verdict"], "insufficient")
        self.assertIn("--surfaces", report["decisions"]["not_verified"][0]["reason"])

    def test_7_a_map_with_the_right_names_and_the_wrong_blocks_is_not_verified_and_never_a_false_pass(self):
        wrong = {**self.surfaces, "counter-1": self.surfaces["armchair-book"], "armchair-book": self.surfaces["counter-1"],
                 "wall-west": self.surfaces["centre"], "centre": self.surfaces["wall-west"], "room": self.surfaces["centre"]}
        other = self.tmp / "wrong-blocks.json"
        other.write_text(json.dumps(wrong), encoding="utf-8")
        code, printed, report, _ = self.verify(self.frames_list, surfaces=other)
        self.assertEqual(code, 0, printed)
        self.assertEqual(report["hit_rate"]["hits"] + report["hit_rate"]["misses"], 0, report["hit_rate"])
        self.assertEqual(report["hit_rate"]["verdict"], "insufficient")
        self.assertTrue(all("--surfaces" in e["reason"] and "predicted.surfaces" in e["reason"] for e in report["decisions"]["not_verified"]
                            if e["kind"] == "no surface map"))
        self.assertGreater(report["decisions"]["not_verified_by_kind"]["no surface map"], 0)

    # -- what the reader of the real files gets ----------------------------------------------------------------------------------

    def test_a_surface_wholly_excluded_reads_no_prediction_not_0(self):
        _, report, written = self.scored(self.frames_list)
        for d in written:
            self.assertIsNone(d["predicted"]["surfaces"]["dead"])
            p = next(x for x in d["verify"]["points"] if x["point_id"] == "dead")
            self.assertEqual(p["status"], "unverifiable")
            self.assertIn("excluded", p["why"])
        self.assertEqual(report["points"]["unverifiable"], len(written))

    def test_den_cores_log_jsonl_is_refused_saying_to_pass_decisions_jsonl(self):
        code, printed, report, _ = self.verify(self.frames_list, decisions=self.second / "log.jsonl")
        self.assertEqual(code, 2, printed)
        self.assertIn("line 1", printed)
        self.assertIn("decisions.jsonl", printed)
        self.assertNotIn("Traceback", printed)
        self.assertIsNone(report)

    def test_utf16_of_the_real_files_is_the_one_sentence(self):
        wide = self.tmp / "decisions-16.jsonl"
        wide.write_bytes(b"\xff\xfe" + self.decisions_path.read_text("utf-8").replace("\n", "\r\n").encode("utf-16-le"))
        code, printed, _, _ = self.verify(self.frames_list, decisions=wide)
        self.assertEqual((code, printed.strip()), (2, f"{wide} is UTF-16 (PowerShell 5.1's > writes that): save it as UTF-8"))

    def test_a_bulb_never_seen_in_a_real_decision_is_unknown_and_no_crash(self):
        decisions = copy.deepcopy(self.decisions)
        decisions[3]["inputs"]["actuator_state"][REAR_LEFT] = None
        code, printed, report, _ = self.verify(self.frames_list, decisions=decisions)
        self.assertEqual(code, 0, printed)
        self.assertEqual(report["hit_rate"]["misses"], 0)
        self.assertEqual(by_id(report["decisions"]["not_verified"], "decision_id", decisions[3]["decision_id"])["kind"], "unknown state")
        self.assertEqual(report["decisions"]["unknown_state"][0]["decision_id"], decisions[3]["decision_id"])

    def test_a_real_decision_too_long_for_its_line_is_counted(self):
        decisions = copy.deepcopy(self.decisions)
        decisions[0]["predicted"]["surfaces_not_logged"] = 7
        code, printed, report, _ = self.verify(self.frames_list, decisions=decisions)
        self.assertEqual(code, 0, printed)
        self.assertEqual(report["surfaces_not_logged"], {"surfaces": 7, "decisions": 1})
        self.assertIn("7 surface(s) across 1 decision(s) were not logged, so not verified", printed)

    def test_a_model_marked_fake_previews_labelled_and_is_refused_scored(self):
        marked = self.tmp / "marked-fake.json"
        marked.write_text(json.dumps({**self.model, "fake": True}), encoding="utf-8")
        out = self.run_core("marked", "--light-states", self.frames_path, model=marked)
        code, printed, report, _ = self.verify(self.frames_list, decisions=out / "decisions.jsonl", model=marked)
        self.assertEqual(code, 0, printed)
        self.assertIn("marked fake", report["backend"])
        self.assertIn("plumbing only", printed)
        self.assertEqual(report["hit_rate"]["misses"], 0)
        code, printed, report, _ = self.verify(self.frames_list, decisions=out / "decisions.jsonl", model=marked, preview=False)
        self.assertEqual(code, 2, printed)
        self.assertIn("plumbing only, never a gate", printed)
        self.assertIsNone(report)

    def test_no_sibling_is_refused_on_the_real_model_naming_why(self):
        with mock.patch.object(light_model, "PREDICT_PATH", HERE / "no-such-folder" / "predict.py"):
            code, printed, report, _ = self.verify(self.frames_list)
        self.assertEqual(code, 2, printed)
        self.assertIn("tools/den-light/predict.py", printed)
        self.assertIsNone(report)

    def test_any_value_anywhere_in_a_real_file_is_never_a_traceback(self):
        rng = random.Random(20261106)
        values = (None, 5, -1, 0, 1e300, 1.5, "x", "", "19:03", True, [], [5], [None], {}, {"level": "x"}, {"level": 1.5})

        def mutate(value):
            out = copy.deepcopy(value)
            spots, stack = [], [(out, k) for k in out]
            while stack:
                holder, key = stack.pop()
                spots.append((holder, key))
                inner = holder[key]
                stack += [(inner, k) for k in inner] if isinstance(inner, dict) else [(inner, k) for k in range(len(inner))] if isinstance(inner, list) else []
            holder, key = rng.choice(spots)
            holder[key] = rng.choice(values)
            return out

        frames = [f for f in self.frames_list if any(-4 < f["ts"] - d["ts"] < 12 for d in self.decisions)]
        for n in range(120):
            d, f, m, s = list(self.decisions), list(frames), self.model, self.surfaces
            which = n % 4
            if which == 0:
                d[n % len(d)] = mutate(d[n % len(d)])
            elif which == 1:
                f[n % len(f)] = mutate(f[n % len(f)])
            elif which == 2:
                m = mutate(m)
            else:
                s = mutate(s)
            folder = Path(tempfile.mkdtemp(dir=self.tmp))
            (folder / "m.json").write_text(json.dumps(m), encoding="utf-8")
            (folder / "s.json").write_text(json.dumps(s), encoding="utf-8")
            with self.subTest(n=n, which=which):
                code, printed, _, _ = self.verify(f, decisions=d, model=folder / "m.json", surfaces=folder / "s.json")
                self.assertIn(code, (0, 2), printed)
                self.assertNotIn("stopped on an input", printed)
                self.assertNotIn("Traceback", printed)

    def shifted(self, record, by):
        """A real record a day later, every time verify.py reads moved: what a second evening's run would write."""
        out = copy.deepcopy(record)
        out["ts"] += by
        inputs = out.get("inputs")
        if inputs is not None:
            inputs["light_state_ts"] += by
            inputs["light_state"] = "ls-%.3f" % inputs["light_state_ts"]
            for bulb in out["applied_scene"].values():
                for step in bulb["steps"]:
                    step["sent_at"] += by
                    step["result"]["at"] += by
            out["decision_id"] += "-day2"
        return out

    def test_two_evenings_with_the_same_clock_times_in_one_run(self):
        day = 86400.0
        decisions = self.decisions + [self.shifted(d, day) for d in self.decisions]
        late = [dict(f, surface_estimates=[{**e, "value": e["value"] + 0.5} for e in f["surface_estimates"]]) for f in self.frames_list]
        frames = self.frames_list + [self.shifted(f, day) for f in late]
        code, printed, report, _ = self.verify(frames, decisions=decisions)
        self.assertEqual(code, 0, printed)
        hr = report["hit_rate"]
        self.assertEqual((hr["misses"], hr["verdict"], hr["sessions"]), (0, "pass", 2), hr)  # the other day's frame reads 0.5 away
        self.assertEqual(hr["interval"], [1.0, 1.0])
        self.assertEqual(report["decisions"]["verified"], 2 * len(self.decisions))


if __name__ == "__main__":
    unittest.main()
