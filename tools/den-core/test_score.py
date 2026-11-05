"""Tests for score.py, rung 9's gate on a replayed log. Every log and light model here is synthetic.

The scored path needs what no real run has yet: closed slots and rung 8's own predict. So these tests close a copy of
the gate file in a temp folder and stand a model file behind a patched BACKEND, which proves the plumbing and nothing else.
"""
import copy
import json
import shutil
import sys
import tempfile
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
import hold_fixtures as fixtures  # noqa: E402
import incumbent  # noqa: E402
import light_adapter  # noqa: E402
import runner  # noqa: E402
import score  # noqa: E402
import synthetic  # noqa: E402

FL, FR, RL, RR = events.LIGHTS
T0 = synthetic.T0
HAVE_POLICY = incumbent.find_policy() is not None
FIXED = {name: item["value"] for name, item in coordinator.load_gate()["fixed"].items()}


def closed_gate(tmp, **overrides):
    """A copy of the gate file with every slot closed at its proposal, or an override."""
    gate = coordinator.load_gate()
    for name, slot in gate["slots"].items():
        slot.update(status="closed", value=overrides.get(name, slot["proposed"]))
    path = Path(tmp) / "gate.json"
    path.write_text(json.dumps(gate), encoding="utf-8")
    return path


def real_model(tmp, **params):
    """A model file that does not call itself fake. Only a test, with BACKEND patched, can treat it as rung 8's."""
    model = fake_light.build(**params)
    del model["fake"]
    path = Path(tmp) / "light-model.json"
    path.write_text(json.dumps(model), encoding="utf-8")
    return path


def replay_log(tmp, gate_path, model_path, name="log.jsonl", compensation=None, **options):
    """A non-synthetic log: the synthetic evening's inputs, tagged as if they were copied files."""
    items, _, _ = synthetic.evening(unclear_rate=0.0, invalid_rate=0.0, fail_rate=0.0, **options)
    config = {"gate": coordinator.gate_config(coordinator.load_gate(gate_path)),
              "light_model": {"source": "file", "path": str(model_path)}}
    if compensation:
        config["compensation"] = compensation
    tagged = runner.tagged([item for item in items if item[1] != "facts"], "poll_log") + runner.tagged(
        [item for item in items if item[1] == "facts"], "fact_log")
    header, log = runner.run(tagged, runner.load_policies(["need", "incumbent", "coordinator"], {"coordinator": config}),
                             sources=[{"name": "test", "params": options}])
    path = Path(tmp) / name
    events.write(path, header, log)
    return path, header, log


def mark(start, end, surface="armchair-book", band="fine", sd=3, episode="e1"):
    return {"episode_id": episode, "track_id": "room", "band": band, "start": start, "end": end,
            "R_eval": {"surface_id": surface, "placement_sd": sd, "source": "gold_mark"}}


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def text(self, lines):
        return "\n".join(lines)


class RefusalTest(Base):
    def synthetic_log(self):
        items, _, _ = synthetic.evening(unclear_rate=0.0, invalid_rate=0.0, fail_rate=0.0)
        header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(["need", "incumbent", "coordinator"]),
                                 synthetic=True, sources=[])
        path = self.tmp / "log.jsonl"
        events.write(path, header, log)
        return path

    def test_refuses_an_open_slot_a_synthetic_log_and_a_fake_model(self):
        lines, code = score.report(self.synthetic_log())
        self.assertEqual(code, 2)
        text = self.text(lines)
        self.assertIn("refused: not a scored run", text)
        self.assertIn("open slot(s) in the gate file", text)
        self.assertIn("anticipation_floor", text)
        self.assertIn("synthetic: plumbing only, never a gate", text)
        self.assertIn("fake light model: plumbing only, never a gate", text)

    def test_refuses_one_open_slot_on_an_otherwise_scored_run(self):
        gate = coordinator.load_gate(closed_gate(self.tmp))
        gate["slots"]["stability_floor"].update(status="open", value=None)
        open_gate = self.tmp / "open.json"
        open_gate.write_text(json.dumps(gate), encoding="utf-8")
        with mock.patch.object(light_adapter, "BACKEND", "den-light predict"):
            path, _, _ = replay_log(self.tmp, open_gate, real_model(self.tmp))
            lines, code = score.report(path, gate_path=open_gate)
        self.assertEqual(code, 2)
        self.assertIn("1 open slot(s) in the gate file: stability_floor", self.text(lines))

    def test_refuses_a_log_run_under_another_gate_or_a_counterfactual_option(self):
        gate, model = closed_gate(self.tmp), real_model(self.tmp)
        with mock.patch.object(light_adapter, "BACKEND", "den-light predict"):
            path, _, _ = replay_log(self.tmp, gate, model)
            other = self.tmp / "other"
            other.mkdir()
            lines, code = score.report(path, gate_path=closed_gate(other, stability_floor=0.5))
            self.assertEqual(code, 2)
            self.assertIn("another gate file", self.text(lines))
            path, _, _ = replay_log(self.tmp, gate, model, name="p0.jsonl", compensation={"option": "P0"})
            lines, code = score.report(path, gate_path=gate)
            self.assertEqual(code, 2)
            self.assertIn("compensation P0, a counterfactual", self.text(lines))

    def test_refuses_a_log_without_the_coordinator(self):
        items, _, _ = synthetic.evening()
        header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(["incumbent"]), synthetic=True, sources=[])
        path = self.tmp / "log.jsonl"
        events.write(path, header, log)
        lines, code = score.report(path)
        self.assertEqual(code, 2)
        self.assertIn("no coordinator", lines[0])

    def test_refuses_an_unreadable_log(self):
        self.assertEqual(score.report(self.tmp / "missing.jsonl")[1], 2)

    def test_a_preview_runs_and_never_gives_a_verdict(self):
        lines, code = score.report(self.synthetic_log(), preview=True)
        text = self.text(lines)
        self.assertEqual(code, 0)
        self.assertIn("preview: logged proposals", text)
        self.assertIn("fake: plumbing only, never a gate", text)
        self.assertIn("synthetic: plumbing only, never a gate", text)
        self.assertIn("verdict: none (preview)", text)
        self.assertNotIn("PASS", text)
        self.assertNotIn("verdict: FAIL", text)

    def test_slots_lists_every_open_slot_with_its_proposal(self):
        gate = coordinator.load_gate()
        lines = score.open_slot_lines(gate)
        self.assertEqual(len(lines) - 1, len(gate["slots"]))
        self.assertTrue(all(slot["status"] == "open" and slot["proposed"] is not None for slot in gate["slots"].values()))
        self.assertTrue(any("anticipation_floor: proposed 0.8" in line for line in lines))

    def test_the_gate_reads_the_same_with_crlf_and_lf(self):
        raw = coordinator.GATE_FILE.read_bytes().decode("utf-8").replace("\r\n", "\n")
        lf, crlf = self.tmp / "lf.json", self.tmp / "crlf.json"
        lf.write_bytes(raw.encode("utf-8"))
        crlf.write_bytes(raw.replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual(coordinator.gate_config(coordinator.load_gate(lf)), coordinator.gate_config(coordinator.load_gate(crlf)))

    def test_a_closed_slot_with_no_value_is_refused(self):
        gate = coordinator.load_gate()
        gate["slots"]["stability_floor"]["status"] = "closed"
        path = self.tmp / "bad.json"
        path.write_text(json.dumps(gate), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "stability_floor"):
            coordinator.load_gate(path)


class ScoredRunTest(Base):
    def run_it(self, marks=None, floors=None, **options):
        gate = closed_gate(self.tmp, **(floors or {}))
        with mock.patch.object(light_adapter, "BACKEND", "den-light predict"):
            path, header, log = replay_log(self.tmp, gate, real_model(self.tmp), **options)
            marks_path = None
            if marks:
                marks_path = self.tmp / "marks.jsonl"
                marks_path.write_text("".join(json.dumps(m) + "\n" for m in marks), encoding="utf-8")
            return score.report(path, marks_path, gate_path=gate) + (log,)

    def test_a_passing_replay(self):
        lines, code, _ = self.run_it(touch=True)
        text = self.text(lines)
        self.assertEqual(code, 0, text)
        self.assertIn("replay: OK", text)
        self.assertIn("decision time: OK", text)
        self.assertIn("records: OK", text)
        self.assertIn("exclusions: OK", text)
        self.assertIn("steps: OK", text)
        self.assertIn("hard constraints: OK", text)
        self.assertIn("anticipation (replay surrogate): OK (candidate 2 of 2 = 1.000, floor 0.8; incumbent 0 of 2", text)
        self.assertIn("stability (replay surrogate): OK (candidate 4 of 4", text)
        self.assertIn("incumbent 1.000 by construction", text)
        self.assertIn("verdict: PASS on rung 9's replay gate", text)
        self.assertIn("not admitted: the hold fixtures", text)
        self.assertNotIn("preview", text)

    def test_floors_are_read_from_the_gate(self):
        lines, code, _ = self.run_it(floors={"anticipation_rise_min": 5.0})
        self.assertEqual(code, 1)
        self.assertIn("anticipation (replay surrogate): FAIL (candidate 0 of 2", self.text(lines))
        self.assertIn("verdict: FAIL: anticipation", self.text(lines))
        lines, code, _ = self.run_it(floors={"stability_dwell_s": 400}, name="second.jsonl")
        self.assertEqual(code, 1)
        self.assertIn("stability (replay surrogate): FAIL (candidate 1 of 4", self.text(lines))

    def test_no_need_episodes_is_insufficient_not_a_pass_or_a_fail(self):
        gate, model = closed_gate(self.tmp), real_model(self.tmp)
        inputs = [(T0, "light_observed", {"entity_id": e, **fixtures.scene(180), "context": "unknown"}) for e in events.LIGHTS]
        inputs += [(T0 + 1, "occupancy", {"value": "on", "via": "t"})]
        config = {"gate": coordinator.gate_config(coordinator.load_gate(gate)), "light_model": {"source": "file", "path": str(model)}}
        with mock.patch.object(light_adapter, "BACKEND", "den-light predict"):
            header, log = runner.run(runner.tagged(inputs, "poll_log"), runner.load_policies(["incumbent", "coordinator"], {"coordinator": config}),
                                     sources=[], tail_s=10.0)
            path = self.tmp / "log.jsonl"
            events.write(path, header, log)
            lines, code = score.report(path, gate_path=gate)
        self.assertEqual(code, 3)
        self.assertIn("anticipation (replay surrogate): insufficient", self.text(lines))
        self.assertIn("verdict: insufficient: no need episodes", self.text(lines))

    def test_a_slow_decision_fails_the_gate(self):
        gate = closed_gate(self.tmp)
        with mock.patch.object(light_adapter, "BACKEND", "den-light predict"):
            path, _, _ = replay_log(self.tmp, gate, real_model(self.tmp))
            with mock.patch.object(score, "replay_and_time", return_value=(check.check(path), 140.0, 5)):
                lines, code = score.report(path, gate_path=gate)
        self.assertEqual(code, 1)
        self.assertIn("decision time: FAIL (slowest of 5 calls 140.0 ms, limit 100 ms)", self.text(lines))

    def test_a_tampered_log_fails_replay(self):
        gate = closed_gate(self.tmp)
        with mock.patch.object(light_adapter, "BACKEND", "den-light predict"):
            path, _, _ = replay_log(self.tmp, gate, real_model(self.tmp))
            text = path.read_text("ascii").replace('"status":"applied"', '"status":"partial"', 1)
            path.write_bytes(text.encode("ascii"))
            lines, code = score.report(path, gate_path=gate)
        self.assertEqual(code, 1)
        self.assertIn("replay: FAIL", self.text(lines))


class BothArmsTest(Base):
    def test_both_arms_go_through_one_model_even_with_no_marks(self):
        header, log = fixtures.run_case("V6", "P0")
        path = self.tmp / "log.jsonl"
        events.write(path, header, log)
        lines, _ = score.report(path, preview=True)
        means = [line for line in lines if line.startswith("mean predicted light")]
        self.assertEqual(len(means), 2)
        self.assertIn("candidate", means[0])
        self.assertIn("incumbent", means[1])
        self.assertIn("both arms through light model lm-fake-rung8-plan-1", self.text(lines))
        with mock.patch.object(light_adapter, "_predict", wraps=light_adapter._predict) as call:
            light = light_adapter.LightModel(fake_light.build())
            means = {arm: score.mean_light(score.Timeline(header, log), light, arm) for arm in ("candidate", "incumbent")}
        self.assertGreater(call.call_count, 0)  # both arms reach the same one call
        self.assertNotEqual(means["candidate"], means["incumbent"])


class SurrogateTest(Base):
    def test_stability_counts_a_change_followed_too_soon(self):
        slots = {"stability_dwell_s": 120}
        changes = [{"t": 0}, {"t": 300}, {"t": 310}, {"t": 700}]
        self.assertEqual(score.stability(changes, slots), (0.75, 3, 4))
        self.assertEqual(score.stability([], slots), (1.0, 0, 0))  # the incumbent, by construction

    def timeline(self, need_changes, corrections=()):
        line = score.Timeline.__new__(score.Timeline)
        line.need_changes, line.corrections = list(need_changes), list(corrections)
        line.episodes = [{"t": 100.0, "track": "A", "person": fixtures.person_state(100.0, "A", "engaged", "counter-1")}]
        return line

    def test_anticipation_is_a_rise_in_the_window_before_any_correction(self):
        light = light_adapter.LightModel(fake_light.build())
        slots = {"anticipation_window_s": 10, "anticipation_rise_min": 0.05}
        low = {b: fixtures.scene(180) for b in events.LIGHTS}
        high = {b: fixtures.scene(200) for b in events.LIGHTS}
        rise = {"t": 105.0, "before": low, "after": high}
        self.assertEqual(score.anticipation(self.timeline([rise]), light, slots), (1.0, 1, 1, 0))
        late = {**rise, "t": 111.0}
        self.assertEqual(score.anticipation(self.timeline([late]), light, slots), (0.0, 0, 1, 0))
        self.assertEqual(score.anticipation(self.timeline([rise], corrections=[103.0]), light, slots), (0.0, 0, 1, 0))
        small = {**rise, "after": {b: fixtures.scene(181) for b in events.LIGHTS}}
        self.assertEqual(score.anticipation(self.timeline([small]), light, slots), (0.0, 0, 1, 0))
        unknown = {**rise, "before": {FL: {"on": True, "brightness": None, "kelvin": None}}}
        self.assertEqual(score.anticipation(self.timeline([unknown]), light, slots), (0.0, 0, 1, 1))

    def test_the_incumbent_scores_0_and_1_by_construction(self):
        _, log = fixtures.run_case("V6", "P0")
        den = [e["data"] for e in log if e["kind"] == "decision" and e["source"] == "incumbent"]
        self.assertTrue(all(d["trigger"] != "person_state change" for d in den))

    def test_an_episode_needs_occupancy_and_no_game(self):
        header, log = fixtures.run_case("V6", "P0")
        self.assertEqual(len(score.Timeline(header, log).episodes), 1)
        for e in log:
            if e["kind"] == "occupancy":
                e["data"]["value"] = "off"
        self.assertEqual(score.Timeline(header, log).episodes, [])


class ServiceTest(Base):
    def setUp(self):
        super().setUp()
        self.light = light_adapter.LightModel(fake_light.build())
        self.dim = {b: fixtures.scene(120) for b in events.LIGHTS}  # about 80 lx at the book
        self.bright = {b: fixtures.scene(254) for b in events.LIGHTS}  # 470 lx at the book

    def line(self, spans, corrections=()):
        line = score.Timeline.__new__(score.Timeline)
        line.corrections = list(corrections)
        line.spans = [{"start": s, "end": e, "gaming": gaming, "occupied": True, "limited": limited,
                       "scenes": {"candidate": scene, "incumbent": self.dim}} for s, e, scene, gaming, limited in spans]
        return line

    def outcome(self, spans, marks, corrections=(), light=None, arm="candidate"):
        return score.service(self.line(spans, corrections), light or self.light, marks, FIXED, arm)

    def test_served_when_the_floor_stands_for_the_quiet_window(self):
        counts, _ = self.outcome([(0, 200, self.dim, False, []), (200, 400, self.bright, False, [])], [mark(0, 400)])
        self.assertEqual((counts["served"], counts["missed"]), (1, 0))

    def test_served_when_it_stands_to_the_end_of_a_shorter_mark(self):
        counts, _ = self.outcome([(0, 60, self.bright, False, [])], [mark(0, 60)])
        self.assertEqual(counts["served"], 1)

    def test_missed_when_the_floor_is_never_met_or_too_briefly(self):
        counts, _ = self.outcome([(0, 400, self.dim, False, [])], [mark(0, 400)])
        self.assertEqual((counts["served"], counts["missed"]), (0, 1))
        counts, _ = self.outcome([(0, 60, self.dim, False, []), (60, 100, self.bright, False, []), (100, 400, self.dim, False, [])],
                                 [mark(0, 400)])
        self.assertEqual((counts["served"], counts["missed"]), (0, 1))

    def test_a_correction_restarts_the_quiet_window(self):
        counts, _ = self.outcome([(0, 200, self.bright, False, []), (200, 300, self.dim, False, [])], [mark(0, 300)], corrections=[100])
        self.assertEqual((counts["served"], counts["missed"]), (0, 1))
        # a window that had already stood when the person touched a bulb still served
        counts, _ = self.outcome([(0, 200, self.bright, False, []), (200, 300, self.dim, False, [])], [mark(0, 300)], corrections=[150])
        self.assertEqual((counts["served"], counts["missed"]), (1, 0))

    def test_a_last_moment_at_the_floor_does_not_serve_a_long_mark(self):
        counts, _ = self.outcome([(0, 390, self.dim, False, []), (390, 400, self.bright, False, [])], [mark(0, 400)])
        self.assertEqual((counts["served"], counts["missed"]), (0, 1))

    def test_each_arm_is_scored_on_its_own_scene(self):
        spans = [(0, 200, self.bright, False, [])]
        self.assertEqual(self.outcome(spans, [mark(0, 200)])[0]["served"], 1)
        self.assertEqual(self.outcome(spans, [mark(0, 200)], arm="incumbent")[0]["missed"], 1)

    def test_a_band_has_its_own_floor(self):
        between = {b: fixtures.scene(190) for b in events.LIGHTS}  # above 200 lx, below 300 lx at the book
        spans = [(0, 200, between, False, [])]
        self.assertEqual(self.outcome(spans, [mark(0, 200, band="fine")])[0]["missed"], 1)
        self.assertEqual(self.outcome(spans, [mark(0, 200, band="casual")])[0]["served"], 1)

    def test_unknown_reasons_stay_in_the_denominator(self):
        spans = [(0, 200, self.bright, False, [])]
        cases = (([mark(0, 200, sd=9)], None, "geometry"), ([mark(0, 200, surface="nowhere")], None, "no_surface"),
                 ([mark(500, 600)], None, "game_or_outside_the_log"),
                 ([mark(0, 200)], light_adapter.LightModel(fake_light.build(unit="relative")), "relative_model"))
        for marks, light, reason in cases:
            counts, _ = self.outcome(spans, marks, light=light)
            self.assertEqual(counts["unknown"], {reason: 1}, reason)
            self.assertEqual(counts["served"] + counts["missed"], 0)
        blind = {FL: {"on": True, "brightness": None, "kelvin": None}}
        self.assertEqual(self.outcome([(0, 200, blind, False, [])], [mark(0, 200)])[0]["unknown"], {"no_level": 1})

    def test_game_time_leaves_the_comparison(self):
        counts, _ = self.outcome([(0, 200, self.bright, True, [])], [mark(0, 200)])
        self.assertEqual(counts["unknown"], {"game_or_outside_the_log": 1})

    def test_the_owners_area_statistic(self):
        model = fake_light.build()
        patch_scene = {b: fixtures.scene(254) for b in events.LIGHTS}
        light = light_adapter.LightModel(model)
        self.assertTrue(score.serves(light, patch_scene, "armchair-book", 300, FIXED))
        self.assertFalse(score.serves(light, patch_scene, "armchair-book", 500, FIXED))
        # 3 of 4 blocks gone: under the 50% visible share, so no statistic at all
        broken = copy.deepcopy(model)
        for key in broken["basis"]:
            for n in (0, 1, 2):
                broken["basis"][key]["armchair-book/%d" % n] = None
        self.assertIsNone(score.serves(light_adapter.LightModel(broken), patch_scene, "armchair-book", 300, FIXED))
        # one of the four blocks a little under the floor still leaves 75%, above the 70% share; two leave 50%
        values = sorted(light.patch(patch_scene, "armchair-book"))
        self.assertTrue(score.serves(light, patch_scene, "armchair-book", values[1] - 0.001, FIXED))
        self.assertFalse(score.serves(light, patch_scene, "armchair-book", values[2] - 0.001, FIXED))

    def test_hold_limited_minutes_count_at_the_marks(self):
        spans = [(0, 120, self.dim, False, ["armchair-book"]), (120, 400, self.bright, False, [])]
        _, minutes = self.outcome(spans, [mark(0, 400)])
        self.assertAlmostEqual(minutes, 2.0)

    def test_the_report_labels_the_service_lines_and_does_not_gate_them(self):
        header, log = fixtures.run_case("V6", "P1-mode")
        path = self.tmp / "log.jsonl"
        events.write(path, header, log)
        marks_path = self.tmp / "marks.jsonl"
        marks_path.write_text(json.dumps(mark(fixtures.T0 + 10, fixtures.T0 + 60, surface="counter-1")) + "\n", encoding="utf-8")
        lines, code = score.report(path, marks_path, preview=True)
        service_lines = [line for line in lines if line.startswith(
            ("predicted service", "missed service", "hold_limited", "mean predicted light"))]
        self.assertEqual(len(service_lines), 7)
        for line in service_lines:
            self.assertIn("predicted, replay surrogate; reported, not gated", line)
        self.assertEqual(code, 0)

    def test_a_bad_mark_stops_with_its_line_number(self):
        path = self.tmp / "marks.jsonl"
        path.write_text(json.dumps(mark(0, 10)) + "\n" + json.dumps({**mark(0, 10), "band": "huge"}) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "line 2"):
            score.read_marks(path)
        path.write_bytes(b"\xef\xbb\xbf" + (json.dumps(mark(0, 10)) + "\r\n\r\n").encode("utf-8"))
        self.assertEqual(len(score.read_marks(path)), 1)


class StructuralTest(Base):
    def decided(self):
        header, log = fixtures.run_case("V6", "P1-mode")
        return header, log, coordinator.make_policy(next(p for p in header["policies"] if p["module"] == "coordinator")["config"])

    def test_clean_fixture_passes(self):
        _, log, policy = self.decided()
        self.assertTrue(all(ok for ok, _ in score.structural(log, policy.light, policy.slots).values()))

    def test_a_missing_recipients_block_fails_records(self):
        _, log, policy = self.decided()
        for e in log:
            if e["kind"] == "decision" and e["source"] == "decide":
                del e["data"]["recipients"]
                break
        self.assertFalse(score.structural(log, policy.light, policy.slots)["records"][0])

    def test_moved_support_fails_exclusions(self):
        _, log, policy = self.decided()
        for e in log:
            if e["kind"] == "decision" and e["source"] == "decide" and e["data"]["predicted"]["eyes"]:
                e["data"]["predicted"]["eyes"][0]["support_age"] = 99.0
                break
        self.assertFalse(score.structural(log, policy.light, policy.slots)["exclusions"][0])

    def test_a_step_sent_without_its_check_fails(self):
        _, log, policy = self.decided()
        for e in log:
            if e["kind"] == "decision" and e["source"] == "decide":
                for entry in e["data"]["applied_scene"].values():
                    for step in entry["steps"]:
                        step["checks"]["revision_current"] = False
        self.assertFalse(score.structural(log, policy.light, policy.slots)["steps"][0])

    def test_a_free_channel_over_the_limit_or_an_unlogged_conflict_fails(self):
        _, log, policy = self.decided()
        decision = next(e["data"] for e in log if e["kind"] == "decision" and e["source"] == "decide" and e["data"]["predicted"]["eyes"])
        decision["predicted"]["eyes"][0]["excluded"] = True
        self.assertFalse(score.structural(log, policy.light, policy.slots)["hard constraints"][0])


if __name__ == "__main__":
    unittest.main()
