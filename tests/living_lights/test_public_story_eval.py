"""Offline tests for tools/public_story_eval.py (ladder levels 1 and 2).

Run: python3 -m unittest tests/living_lights/test_public_story_eval.py -v

No network, no SDK: the runner's transport seam is stubbed with a fake that
returns hand-built answers and counts calls. Every run happens inside a
temporary eval root so nothing touches the private experiments directory.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "tools" / "public_story_eval.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("public_story_eval", RUNNER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pse = _load_runner()
from lighting_beliefs import egress as eg  # noqa: E402  (path set by the runner)

CLOCK_T = 1_800_000_000.0
MODEL = "jev-1.13.0"
ROWS = [
    {"row_id": "r1", "scene": "Living room", "actions": "c132 0.00 12.60;c123 0.00 20.00",
     "description": "A person sits on the sofa facing the screen and watches television.",
     "targets": {"tv_attention": True, "eating": False, "food_prep": "unobserved", "settling": False, "rest_state": "no"}},
    {"row_id": "r2", "scene": "Kitchen", "classes": ["c147 Someone is cooking something"],
     "description": "A person cooks food on a stove and stirs a pan.",
     "targets": {"tv_attention": "negative", "eating": False, "food_prep": {"value": True, "k": 2}, "settling": False}},
    {"row_id": "r3", "scene": "Bedroom", "classes": ["c134"],
     "description": "A person lies in bed under a blanket and does not move.",
     "targets": {"tv_attention": None, "eating": "no", "food_prep": False, "settling": True, "rest_state": "positive"}},
]


def _score(p0, p1, p2):
    return {"type": "score", "score": p1 + 2 * p2, "confidence": 0.7,
            "legend": {"0": "a", "1": "b", "2": "c"}, "probabilities": {"0": p0, "1": p1, "2": p2}}


def _noul(p):
    return {"type": "noul", "noul": p}


ANSWERS = {
    "living_room": {"tv_attention": _score(0.1, 0.2, 0.7), "eating": _noul(0.1), "food_prep": _score(0.9, 0.1, 0.0),
                    "settling": _score(0.8, 0.15, 0.05), "rest_state": _score(0.7, 0.25, 0.05)},
    "kitchen": {"tv_attention": _score(0.9, 0.08, 0.02), "eating": _noul(0.2), "food_prep": _score(0.05, 0.15, 0.8),
                "settling": _score(0.95, 0.05, 0.0), "rest_state": _score(0.9, 0.1, 0.0)},
    # r3 is a Bedroom scene with no tv and no food_prep positive: camera other_room
    "other_room": {"tv_attention": _score(0.95, 0.05, 0.0), "eating": _noul(0.05), "food_prep": _score(0.98, 0.02, 0.0),
                   "settling": _score(0.1, 0.3, 0.6), "rest_state": _score(0.1, 0.2, 0.7)},
}


class FakeTransport:
    """Answers by camera name; counts calls; can fail on demand."""

    def __init__(self, fail_with=None):
        self.calls = 0
        self.fail_with = fail_with
        self.closed = False

    def ask(self, state, questions, model):
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        camera = next(iter(state["cameras"]))
        return pse.Answered(model, dict(ANSWERS[camera]), input_tokens=1000, output_tokens=40, request_id="req-x")

    def close(self):
        self.closed = True


class Harness:
    """A temporary eval root with rows, record, toggle and env mapping."""

    def __init__(self, tmp: Path, record_enabled=True, model_id=MODEL):
        self.root = tmp / "eval-root"
        self.root.mkdir()
        self.rows = tmp / "rows.jsonl"
        self.rows.write_text("".join(json.dumps(r) + "\n" for r in ROWS), encoding="utf-8")
        self.record = tmp / "egress-record.json"
        body = {"schema": eg.RECORD_SCHEMA, "enabled": record_enabled, "scopes": ["public_eval"], "model_id": model_id}
        self.record.write_text(json.dumps(body), encoding="utf-8")
        os.chmod(self.record, 0o600)
        self.toggle = tmp / "egress-toggle.json"
        self.toggle.write_text(json.dumps({"state": "on", "received_at": CLOCK_T - 10}), encoding="utf-8")
        self.env = {eg.ENV_FLAG: "1", pse.API_KEY_ENV: "not-a-real-key"}
        self.cache = self.root / "cache"
        self.slept = []

    def argv(self, out: str, *extra):
        return ["--rows", str(self.rows), "--record", str(self.record), "--toggle", str(self.toggle),
                "--out", str(self.root / out), "--eval-root", str(self.root), "--cache", str(self.cache),
                "--username", "testuser", *extra]

    def main(self, argv, transport=None, env=None):
        def factory(model, env_):
            if transport is None:
                raise AssertionError("transport factory must not be called")
            return transport
        return pse.main(argv, env=self.env if env is None else env, transport_factory=factory,
                        clock=lambda: CLOCK_T, sleeper=self.slept.append)

    def receipt(self, out: str) -> dict:
        return json.loads((self.root / out / "run.json").read_text(encoding="utf-8"))


class DryRunTest(unittest.TestCase):
    def test_dry_run_three_rows_no_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            self.assertEqual(h.main(h.argv("dry")), 0)
            receipt = h.receipt("dry")
            self.assertFalse(receipt["executed"])
            self.assertEqual(receipt["status"], "complete")
            self.assertEqual(receipt["counts"]["rows"], 3)
            self.assertEqual(receipt["counts"]["packets_built"], 3)
            self.assertEqual(receipt["counts"]["would_be_calls"], 3)
            self.assertEqual(receipt["counts"]["calls_made"], 0)
            self.assertGreater(receipt["counts"]["bytes_would_send"], 0)
            self.assertEqual(receipt["model_id"], MODEL)
            self.assertEqual(set(receipt["question_digests"]["per_question"]), set(pse.questions_mod.QUESTION_IDS))
            self.assertTrue(receipt["gate"]["preflight"]["allowed"])
            self.assertIsNone(receipt["table"])
            self.assertFalse((h.root / "dry" / "answers.jsonl").exists())
            self.assertFalse((h.root / "dry" / "scores.md").exists())
            packets = [json.loads(l) for l in (h.root / "dry" / "packets.jsonl").read_text().splitlines()]
            self.assertEqual([p["row_id"] for p in packets], ["r1", "r2", "r3"])
            self.assertEqual(packets[0]["packet"]["devices"]["media"], [
                {"role": "tv", "state": "playing", "source_kind": "unknown", "age_s": 0}])
            self.assertEqual(packets[1]["packet"]["devices"]["media"], [])
            self.assertIn("kitchen", packets[1]["packet"]["cameras"])
            # durations only, no absolute timestamps in the receipt
            self.assertNotIn("ts", json.dumps(receipt["durations_s"]))
            self.assertIn("total", receipt["durations_s"])
            # modes
            self.assertEqual(stat.S_IMODE((h.root / "dry").stat().st_mode), 0o700)
            for name in ("run.json", "packets.jsonl", "egress-journal.jsonl"):
                self.assertEqual(stat.S_IMODE((h.root / "dry" / name).stat().st_mode), 0o600, name)
            # the dry run never imported the SDK client
            self.assertNotIn("typesafe_sdk", sys.modules)

    def test_dry_run_reports_disabled_record_without_refusing(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), record_enabled=False)
            self.assertEqual(h.main(h.argv("dry")), 0)
            receipt = h.receipt("dry")
            self.assertFalse(receipt["executed"])
            self.assertEqual(receipt["gate"]["preflight"]["reason"], "record_disabled")

    def test_out_must_be_under_eval_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            argv = h.argv("dry")
            argv[argv.index("--out") + 1] = str(Path(tmp) / "elsewhere")
            self.assertEqual(h.main(argv), pse.EXIT_REFUSED)

    def test_level_two_rows_without_an_observation_are_refused_one_by_one(self):
        # level-1 rows carry no observation: every row is refused and counted,
        # and the file is still read to the end (a partly observed selection is
        # the normal case while the observer's batch is still running)
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            self.assertEqual(h.main(h.argv("dry", "--level", "2")), 0)
            receipt = h.receipt("dry")
            self.assertEqual(receipt["status"], "complete")
            self.assertEqual(receipt["counts"]["rows"], 3)
            self.assertEqual(receipt["counts"]["packets_built"], 0)
            self.assertEqual(receipt["counts"]["observations_missing"], 3)
            self.assertEqual(receipt["counts"]["observations_unnamed"], 3)
            self.assertEqual([f["error"] for f in receipt["packet_failures"]],
                             ["ObservationError"] * 3)

    def test_unimplemented_level_is_rejected_by_the_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            with self.assertRaises(SystemExit):
                h.main(h.argv("dry", "--level", "3"))


class ScoringMathTest(unittest.TestCase):
    def test_row_probabilities_use_cutoffs(self):
        p = pse.row_probabilities(ANSWERS["living_room"], pse.DEFAULT_CUTOFFS)
        self.assertAlmostEqual(p["tv_attention"], 0.9)      # P(level >= 1) = 0.2 + 0.7
        self.assertAlmostEqual(p["eating"], 0.1)            # P(yes)
        self.assertAlmostEqual(p["settling"], 0.2)          # P(level >= 1) = 0.15 + 0.05
        self.assertAlmostEqual(p["rest_state"], 0.3)
        strict = pse.row_probabilities(ANSWERS["living_room"], {**pse.DEFAULT_CUTOFFS, "tv_attention": 2, "rest_state": 2})
        self.assertAlmostEqual(strict["tv_attention"], 0.7)
        self.assertAlmostEqual(strict["rest_state"], 0.05)
        with self.assertRaises(ValueError):
            pse.row_probabilities(ANSWERS["living_room"], {**pse.DEFAULT_CUTOFFS, "tv_attention": 3})

    def test_agreement_ignores_unobserved(self):
        scored = [
            {"row_id": "a", "targets": {"eating": True, "tv_attention": None}, "p": {"eating": 0.9, "tv_attention": 0.9}},
            {"row_id": "b", "targets": {"eating": False, "tv_attention": None}, "p": {"eating": 0.55, "tv_attention": 0.1}},
            {"row_id": "c", "targets": {"eating": None, "tv_attention": None}, "p": {"eating": 0.99, "tv_attention": 0.5}},
        ]
        table = pse.score_table(scored, (0.5, 0.6))
        eating = table["eating"]
        self.assertEqual((eating["observed"], eating["positives"], eating["unobserved"]), (2, 1, 1))
        self.assertAlmostEqual(eating["by_threshold"]["0.50"]["agreement"], 0.5)   # b is a false positive at 0.5
        self.assertEqual(eating["by_threshold"]["0.50"]["fp"], 1)
        self.assertAlmostEqual(eating["by_threshold"]["0.60"]["agreement"], 1.0)
        tv = table["tv_attention"]
        self.assertEqual(tv["observed"], 0)
        self.assertIsNone(tv["by_threshold"]["0.50"]["agreement"])
        self.assertEqual(tv["unobserved"], 3)
        self.assertEqual(table["food_prep"]["observed"], 0)  # question absent from every row

    def test_sensitivity_table_shape(self):
        thresholds = (0.5, 0.6, 0.7, 0.8, 0.9)
        scored = [{"row_id": "a", "targets": {q: True for q in pse.questions_mod.QUESTION_IDS},
                   "p": {q: 0.65 for q in pse.questions_mod.QUESTION_IDS}}]
        table = pse.score_table(scored, thresholds)
        self.assertEqual(list(table), list(pse.questions_mod.QUESTION_IDS))
        for entry in table.values():
            self.assertEqual(list(entry["by_threshold"]), ["0.50", "0.60", "0.70", "0.80", "0.90"])
            self.assertEqual([c["agreement"] for c in entry["by_threshold"].values()], [1.0, 1.0, 0.0, 0.0, 0.0])
        text = pse.render_scores(table, thresholds, pse.DEFAULT_CUTOFFS, pse.APRIORI_THRESHOLDS, ["executed: False"])
        self.assertEqual(text.count("| 0.70 |"), 5)
        self.assertIn("| tv_attention | recall | P(level >= 1) |", text)
        self.assertIn("| eating | recall | P(yes) |", text)
        self.assertIn("| rest_state | precision_recall | P(level >= 1) |", text)
        self.assertTrue(text.isascii())

    def test_recall_and_precision_cells(self):
        # (f) recall = tp / positives, precision = tp / (tp + fp); None when the denominator is 0
        scored = [
            {"row_id": "a", "targets": {"rest_state": True}, "p": {"rest_state": 0.9}},
            {"row_id": "b", "targets": {"rest_state": True}, "p": {"rest_state": 0.55}},
            {"row_id": "c", "targets": {"rest_state": False}, "p": {"rest_state": 0.7}},
            {"row_id": "d", "targets": {"rest_state": False}, "p": {"rest_state": 0.2}},
            {"row_id": "e", "targets": {"eating": True}, "p": {"eating": 0.3}},
        ]
        table = pse.score_table(scored, (0.6,))
        cell = table["rest_state"]["by_threshold"]["0.60"]
        self.assertEqual((cell["tp"], cell["tn"], cell["fp"], cell["fn"]), (1, 1, 1, 1))
        self.assertAlmostEqual(cell["recall"], 0.5)
        self.assertAlmostEqual(cell["precision"], 0.5)
        self.assertAlmostEqual(cell["agreement"], 0.5)
        self.assertEqual(table["rest_state"]["metric"], "precision_recall")
        eating = table["eating"]["by_threshold"]["0.60"]
        self.assertEqual(eating["recall"], 0.0)
        self.assertIsNone(eating["precision"])       # no predicted positives
        self.assertEqual(table["eating"]["metric"], "recall")
        self.assertIsNone(table["food_prep"]["by_threshold"]["0.60"]["recall"])   # no positives at all
        self.assertEqual(pse.LEVEL1_METRIC, {"tv_attention": "recall", "eating": "recall", "food_prep": "recall",
                                             "settling": "recall", "rest_state": "precision_recall"})

    def test_per_scene_recall_at_apriori(self):
        # (a) recall is broken down per Charades scene at the a-priori threshold only
        scored = [
            {"row_id": "a", "scene": "living_room", "targets": {"tv_attention": True}, "p": {"tv_attention": 0.9}},
            {"row_id": "b", "scene": "bedroom", "targets": {"tv_attention": True}, "p": {"tv_attention": 0.3}},
            {"row_id": "c", "scene": "bedroom", "targets": {"tv_attention": True}, "p": {"tv_attention": 0.7}},
            {"row_id": "d", "scene": "bedroom", "targets": {"tv_attention": None}, "p": {"tv_attention": 0.7}},
        ]
        table = pse.score_table(scored, (0.5, 0.6), {"tv_attention": 0.6})
        by_scene = table["tv_attention"]["by_scene"]
        self.assertEqual(list(by_scene), ["bedroom", "living_room"])
        self.assertEqual(by_scene["bedroom"]["positives"], 2)
        self.assertAlmostEqual(by_scene["bedroom"]["recall"], 0.5)
        self.assertAlmostEqual(by_scene["living_room"]["recall"], 1.0)
        self.assertEqual(table["tv_attention"]["apriori_threshold"], 0.6)
        self.assertEqual(table["eating"]["by_scene"], {})        # nothing observed
        text = pse.render_scores(table, (0.5, 0.6), pse.DEFAULT_CUTOFFS, {"tv_attention": 0.6}, [])
        self.assertIn("| bedroom | 2 | 2 | 0.500 |", text)
        self.assertTrue(text.isascii())

    def test_parse_target_forms(self):
        self.assertEqual(pse.parse_target(True), (True, None))
        self.assertEqual(pse.parse_target("unobserved"), (None, None))
        self.assertEqual(pse.parse_target("neg"), (False, None))
        self.assertEqual(pse.parse_target({"value": "yes", "k": 1}), (True, 1))
        with self.assertRaises(ValueError):
            pse.parse_target("maybe")
        with self.assertRaises(ValueError):
            pse.parse_target({"value": True, "k": 0})

    def test_television_detection(self):
        self.assertTrue(pse.television_present({"actions": "c001 0.0 1.0;c132 2.0 3.0"}))
        self.assertTrue(pse.television_present({"classes": ["Laughing at television"]}))
        self.assertFalse(pse.television_present({"classes": ["c147"]}))
        self.assertFalse(pse.television_present({"classes": ["c132"], "tv_present": False}))
        native = [{"class_id": "c097", "class_name": "Walking through a doorway", "start_s": 0.0, "end_s": 4.9},
                  {"class_id": "c132", "class_name": "Watching television", "start_s": 1.0, "end_s": 9.0}]
        self.assertTrue(pse.television_present({"native_classes": native}))
        self.assertFalse(pse.television_present({"native_classes": native[:1]}))

    def test_camera_named_after_question_room(self):
        # (a) the packet camera is the question's room, tv first, then food_prep, then a known scene
        self.assertEqual(pse.camera_for_row("bedroom", True, {}), ("living_room", "tv_row"))
        self.assertEqual(pse.camera_for_row("bedroom", False, {"tv_attention": True}), ("living_room", "tv_row"))
        self.assertEqual(pse.camera_for_row("bedroom", True, {"food_prep": True}), ("living_room", "tv_row"))
        self.assertEqual(pse.camera_for_row("bedroom", False, {"food_prep": True}), ("kitchen", "food_prep_row"))
        self.assertEqual(pse.camera_for_row("living_room", False, {"eating": True}), ("living_room", "scene"))
        self.assertEqual(pse.camera_for_row("kitchen", False, {"food_prep": False}), ("kitchen", "scene"))
        self.assertEqual(pse.camera_for_row("dining_room", False, {}), ("dining_room", "scene"))
        self.assertEqual(pse.camera_for_row("bathroom", False, {"settling": True}), ("other_room", "fallback"))
        self.assertEqual(pse.camera_for_row("entryway_a_hall_that_is_generally_locate", False, {}), ("other_room", "fallback"))
        self.assertEqual(pse.camera_for_row("", False, {}), ("other_room", "fallback"))
        row = pse.parse_row({"id": "x", "scene": "Bedroom", "description": "A person watches television.",
                             "native_classes": [{"class_id": "c132", "class_name": "Watching television"}],
                             "targets": {"tv_attention": "positive"}}, 1)
        self.assertEqual((row.scene, row.camera, row.camera_reason), ("bedroom", "living_room", "tv_row"))
        self.assertIn("living_room", pse.build_row_packet(row)["cameras"])
        row = pse.parse_row({"id": "y", "scene": "Home Office / Study (A room in a house used for work)",
                             "description": "A person eats a sandwich.", "targets": {"eating": "positive"}}, 2)
        self.assertEqual((row.scene, row.camera), ("home_office_study_a_room_in_a_house_used", "other_room"))

    def test_camera_policy_recorded_in_receipt_and_packets(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            rows = ROWS + [{"row_id": "r4", "scene": "Bedroom", "classes": ["c132"],
                            "description": "A person watches television from the bed.",
                            "targets": {"tv_attention": True}},
                           {"row_id": "r5", "scene": "Dining room", "description": "A person sits at the table.",
                            "targets": {"eating": True}}]
            h.rows.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            self.assertEqual(h.main(h.argv("cam")), 0)
            receipt = h.receipt("cam")
            policy = receipt["camera_policy"]
            self.assertEqual(policy["rule"], "question_room")
            self.assertEqual(policy["tv_row"], "living_room")
            self.assertEqual(policy["food_prep_row"], "kitchen")
            self.assertEqual(policy["fallback"], "other_room")
            self.assertEqual(policy["by_camera"], {"living_room": 2, "kitchen": 1, "other_room": 1, "dining_room": 1})
            self.assertEqual(policy["by_reason"], {"tv_row": 2, "food_prep_row": 1, "fallback": 1, "scene": 1})
            packets = [json.loads(l) for l in (h.root / "cam" / "packets.jsonl").read_text().splitlines()]
            by_id = {p["row_id"]: p for p in packets}
            self.assertEqual((by_id["r4"]["scene"], by_id["r4"]["camera"], by_id["r4"]["camera_reason"]),
                             ("bedroom", "living_room", "tv_row"))
            self.assertEqual(list(by_id["r4"]["packet"]["cameras"]), ["living_room"])
            self.assertEqual((by_id["r3"]["scene"], by_id["r3"]["camera"]), ("bedroom", "other_room"))
            self.assertEqual(by_id["r5"]["camera"], "dining_room")
            self.assertEqual(receipt["level1_metric"]["rest_state"], "precision_recall")

    def test_long_description_truncation_and_split(self):
        long_text = " ".join(f"Sentence number {i} describes one more small action in the room." for i in range(1, 9))
        self.assertGreater(len(long_text), 240)
        self.assertTrue(pse.description_truncated(long_text))
        self.assertFalse(pse.description_truncated("A short claim."))
        claims = pse.split_claims(long_text)
        self.assertGreater(len(claims), 1)
        self.assertTrue(all(len(c) <= 240 for c in claims))
        self.assertEqual(" ".join(claims), long_text)
        row = pse.Row("long", long_text, "kitchen", False)
        one = pse.build_row_packet(row)["cameras"]["kitchen"]["claims"]
        self.assertEqual(len(one), 1)
        self.assertEqual(len(one[0]), 240)
        many = pse.build_row_packet(row, split_long=True)["cameras"]["kitchen"]["claims"]
        self.assertEqual(many, claims)


class ExecuteTest(unittest.TestCase):
    def test_gate_denies_execute_when_record_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), record_enabled=False)
            transport = FakeTransport()
            code = h.main(h.argv("exec", "--execute", "--max-calls", "5"), transport=transport)
            self.assertEqual(code, pse.EXIT_REFUSED)
            self.assertEqual(transport.calls, 0)
            self.assertFalse((h.root / "exec" / "run.json").exists())
            journal = [json.loads(l) for l in (h.root / "exec" / "egress-journal.jsonl").read_text().splitlines()]
            self.assertEqual(journal[0]["reason"], "record_disabled")

    def test_gate_denies_without_env_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            code = h.main(h.argv("exec", "--execute", "--max-calls", "5"), transport=FakeTransport(),
                          env={pse.API_KEY_ENV: "not-a-real-key"})
            self.assertEqual(code, pse.EXIT_REFUSED)

    def test_execute_requires_max_calls_and_pinned_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            self.assertEqual(h.main(h.argv("a", "--execute"), transport=FakeTransport()), pse.EXIT_REFUSED)
            self.assertEqual(h.main(h.argv("b", "--execute", "--max-calls", "1", "--model", "jev-latest"),
                                    transport=FakeTransport()), pse.EXIT_REFUSED)
            self.assertEqual(h.main(h.argv("c", "--execute", "--max-calls", "1", "--model", "jev-1.12.0"),
                                    transport=FakeTransport()), pse.EXIT_REFUSED)

    def test_cache_must_be_under_eval_root(self):
        # (b) --cache is confined like --out; nothing is written outside the root
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            outside = Path(tmp) / "fakerepo" / "cache"
            argv = h.argv("c1", "--execute", "--max-calls", "5")
            argv[argv.index("--cache") + 1] = str(outside)
            transport = FakeTransport()
            self.assertEqual(h.main(argv, transport=transport), pse.EXIT_REFUSED)
            self.assertEqual(transport.calls, 0)
            self.assertFalse(outside.exists())
            self.assertFalse((h.root / "c1").exists())
            # a dry run refuses too, and a symlink into the root does not help
            argv = h.argv("c2")
            argv[argv.index("--cache") + 1] = str(outside)
            self.assertEqual(h.main(argv), pse.EXIT_REFUSED)
            link = h.root / "cache-link"
            os.symlink(Path(tmp) / "fakerepo", link)
            argv = h.argv("c3")
            argv[argv.index("--cache") + 1] = str(link / "cache")
            self.assertEqual(h.main(argv), pse.EXIT_REFUSED)
            self.assertFalse(outside.exists())
            # the default and an explicit in-root cache are accepted
            argv = h.argv("c4")
            del argv[argv.index("--cache"):argv.index("--cache") + 2]
            self.assertEqual(h.main(argv), 0)
            self.assertEqual(h.receipt("c4")["files"]["cache_dir"], str(h.root / "cache"))

    def test_unexpected_call_exception_releases_ticket_and_writes_receipt(self):
        # (c) a non-transport exception (a runner bug) frees the gate, aborts, and leaves a receipt without its text
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            transport = FakeTransport(fail_with=KeyError("sekrit-detail"))
            code = h.main(h.argv("bug", "--execute", "--max-calls", "10"), transport=transport)
            self.assertEqual(code, pse.EXIT_ABORTED)
            self.assertEqual(transport.calls, 1)
            self.assertTrue(transport.closed)
            receipt = h.receipt("bug")
            self.assertEqual(receipt["status"], "aborted")
            self.assertEqual(receipt["stop_reason"], "unexpected KeyError during a call")
            self.assertEqual(receipt["counts"]["calls_made"], 1)
            self.assertEqual(receipt["counts"]["calls_failed"], 1)
            self.assertEqual(receipt["rows_scored"], 0)
            for name in ("run.json", "answers.jsonl", "egress-journal.jsonl"):
                self.assertNotIn("sekrit", (h.root / "bug" / name).read_text(), name)
            journal = [json.loads(l) for l in (h.root / "bug" / "egress-journal.jsonl").read_text().splitlines()]
            tickets = [e["ticket"] for e in journal if e["event"] == "decision" and e["allowed"]]
            releases = {e["ticket"]: e for e in journal if e["event"] == "release"}
            self.assertEqual(set(tickets), set(releases))            # every allowed ticket was released
            self.assertEqual(releases[tickets[-1]]["outcome"], "error")
            self.assertEqual(releases[tickets[-1]]["error"], "KeyError")
            self.assertFalse(any(e["event"] == "in_flight_expired" for e in journal))
            # the gate is free again: a second run into a fresh --out is not blocked in flight
            self.assertEqual(h.main(h.argv("bug2", "--execute", "--max-calls", "10"), transport=FakeTransport()), 0)

    def test_permission_denied_stops_like_authentication_error(self):
        # (e) a 403 maps to AuthenticationStop, so it costs one ticket; the SDK is faked, never imported
        import types

        fake = types.SimpleNamespace()
        fake.TypeSafeError = type("TypeSafeError", (Exception,), {})
        fake.TypeSafeAPIError = type("TypeSafeAPIError", (fake.TypeSafeError,), {})
        fake.TypeSafeAuthenticationError = type("TypeSafeAuthenticationError", (fake.TypeSafeAPIError,), {})
        fake.TypeSafePermissionDeniedError = type("TypeSafePermissionDeniedError", (fake.TypeSafeAPIError,), {})
        fake.TypeSafeRateLimitError = type("TypeSafeRateLimitError", (fake.TypeSafeAPIError,), {})
        fake.ScoreAnswer = type("ScoreAnswer", (), {})
        fake.NoulAnswer = type("NoulAnswer", (), {})

        def make(exc):
            transport = pse.SdkTransport.__new__(pse.SdkTransport)
            transport._sdk = fake
            transport._client = types.SimpleNamespace(system_one=lambda *a, **k: (_ for _ in ()).throw(exc))
            return transport

        for cls, status in ((fake.TypeSafePermissionDeniedError, 403), (fake.TypeSafeAuthenticationError, 401)):
            exc = cls("body text that must not leak")
            exc.status, exc.request_id = status, "req-1"
            with self.assertRaises(pse.AuthenticationStop) as ctx:
                make(exc).ask({"cameras": {}}, {}, MODEL)
            self.assertEqual(str(ctx.exception), f"authentication failed (status {status})")
        exc = fake.TypeSafeRateLimitError("still no body in the message")
        exc.status, exc.request_id = 429, "req-2"
        with self.assertRaises(pse.TransportError) as ctx:
            make(exc).ask({"cameras": {}}, {}, MODEL)
        self.assertNotIsInstance(ctx.exception, pse.AuthenticationStop)
        self.assertNotIn("body", str(ctx.exception))
        self.assertNotIn("typesafe_sdk", sys.modules)

    def test_receipt_states_call_policy(self):
        # (e) one ticket is one SDK invocation with up to MAX_RETRIES retries; the receipt says so
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            self.assertEqual(h.main(h.argv("pol")), 0)
            policy = h.receipt("pol")["call_policy"]
            self.assertEqual(policy["sdk_max_retries"], pse.MAX_RETRIES)
            self.assertEqual(policy["max_consecutive_errors"], pse.MAX_CONSECUTIVE_ERRORS)
            self.assertIn("1 + sdk_max_retries HTTP requests", policy["note"])
            self.assertIn("403", policy["note"])

    def test_default_factory_refuses_without_key(self):
        with self.assertRaises(pse.EvalError):
            pse.default_transport_factory(MODEL, {})

    def test_execute_writes_receipts_then_cache_hit_on_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            first = FakeTransport()
            self.assertEqual(h.main(h.argv("run1", "--execute", "--max-calls", "10"), transport=first), 0)
            self.assertEqual(first.calls, 3)
            self.assertTrue(first.closed)
            receipt = h.receipt("run1")
            self.assertTrue(receipt["executed"])
            self.assertEqual(receipt["status"], "complete")
            self.assertEqual(receipt["counts"]["calls_made"], 3)
            self.assertEqual(receipt["counts"]["cache_hits"], 0)
            self.assertEqual(receipt["counts"]["input_tokens"], 3000)
            self.assertAlmostEqual(receipt["spend_estimate"]["usd_estimated"], 3000 * 0.042 / 1e6, places=9)
            self.assertEqual(receipt["rows_scored"], 3)
            self.assertEqual(receipt["gate"]["allowed"], 3)
            table = receipt["table"]
            # hand-checked agreements: every fake answer agrees with its target at 0.5
            self.assertEqual(table["tv_attention"]["observed"], 2)      # r3 unobserved
            self.assertEqual(table["food_prep"]["observed"], 2)         # r1 unobserved
            self.assertEqual(table["rest_state"]["observed"], 2)        # r2 absent -> unobserved
            for qid, entry in table.items():
                self.assertEqual(entry["by_threshold"]["0.50"]["agreement"], 1.0, qid)
            self.assertEqual(table["settling"]["by_threshold"]["0.80"]["agreement"], 1.0)  # r3 settling p = 0.3 + 0.6
            self.assertEqual(table["eating"]["by_threshold"]["0.80"]["agreement"], 1.0)
            self.assertEqual(receipt["counts"]["tv_rows"], 1)
            self.assertEqual(receipt["counts"]["descriptions_truncated"], 0)
            answers = [json.loads(l) for l in (h.root / "run1" / "answers.jsonl").read_text().splitlines()]
            self.assertEqual([a["row_id"] for a in answers], ["r1", "r2", "r3"])
            self.assertTrue(all(a["cached"] is False for a in answers))
            self.assertIn("not-a-real-key", h.env[pse.API_KEY_ENV])
            for name in ("run.json", "answers.jsonl", "scores.md", "egress-journal.jsonl"):
                text = (h.root / "run1" / name).read_text()
                self.assertNotIn("not-a-real-key", text, name)
                self.assertEqual(stat.S_IMODE((h.root / "run1" / name).stat().st_mode), 0o600, name)
            self.assertEqual(len(list(h.cache.glob("*.json"))), 3)
            self.assertEqual(stat.S_IMODE(h.cache.stat().st_mode), 0o700)

            second = FakeTransport()
            self.assertEqual(h.main(h.argv("run2", "--execute", "--max-calls", "10"), transport=second), 0)
            self.assertEqual(second.calls, 0)
            rerun = h.receipt("run2")
            self.assertTrue(rerun["executed"])
            self.assertEqual(rerun["counts"]["cache_hits"], 3)
            self.assertEqual(rerun["counts"]["calls_made"], 0)
            self.assertEqual(rerun["counts"]["would_be_calls"], 0)
            self.assertEqual(rerun["rows_scored"], 3)
            self.assertEqual(rerun["table"], receipt["table"])
            answers2 = [json.loads(l) for l in (h.root / "run2" / "answers.jsonl").read_text().splitlines()]
            self.assertTrue(all(a["cached"] is True for a in answers2))

    def test_second_execute_on_same_out_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            self.assertEqual(h.main(h.argv("same", "--execute", "--max-calls", "10"), transport=FakeTransport()), 0)
            before = (h.root / "same" / "run.json").read_bytes()
            again = FakeTransport()
            self.assertEqual(h.main(h.argv("same", "--execute", "--max-calls", "10"), transport=again), pse.EXIT_REFUSED)
            self.assertEqual(again.calls, 0)
            self.assertEqual((h.root / "same" / "run.json").read_bytes(), before)
            # a dry run over an executed receipt is refused too
            self.assertEqual(h.main(h.argv("same")), pse.EXIT_REFUSED)
            self.assertEqual((h.root / "same" / "run.json").read_bytes(), before)

    def test_dry_run_then_execute_on_same_out_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            self.assertEqual(h.main(h.argv("d")), 0)
            self.assertEqual(h.main(h.argv("d", "--execute", "--max-calls", "10"), transport=FakeTransport()),
                             pse.EXIT_REFUSED)

    def test_max_calls_caps_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            transport = FakeTransport()
            self.assertEqual(h.main(h.argv("cap", "--execute", "--max-calls", "2"), transport=transport), 0)
            self.assertEqual(transport.calls, 2)
            receipt = h.receipt("cap")
            self.assertEqual(receipt["counts"]["not_asked"], 1)
            self.assertEqual(receipt["rows_scored"], 2)

    def test_stops_on_first_authentication_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            transport = FakeTransport(fail_with=pse.AuthenticationStop("authentication failed (status 401)"))
            code = h.main(h.argv("auth", "--execute", "--max-calls", "10"), transport=transport)
            self.assertEqual(code, pse.EXIT_ABORTED)
            self.assertEqual(transport.calls, 1)
            receipt = h.receipt("auth")
            self.assertEqual(receipt["status"], "aborted")
            self.assertIn("authentication", receipt["stop_reason"])
            self.assertEqual(receipt["rows_scored"], 0)
            journal = [json.loads(l) for l in (h.root / "auth" / "egress-journal.jsonl").read_text().splitlines()]
            self.assertIn("auth_error", [e.get("outcome") for e in journal])

    def test_rate_denial_waits_then_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            # a gate clock that never advances: the sixth request in the window is rate-denied
            # until the sleeper is called; simulate the window freeing by advancing the clock in sleep
            state = {"t": CLOCK_T}

            def clock():
                return state["t"]

            def sleeper(seconds):
                state["t"] += eg.RATE_WINDOW_S

            h.rows.write_text("".join(json.dumps({**ROWS[i % 3], "row_id": f"r{i}"}) + "\n" for i in range(8)))
            transport = FakeTransport()

            def factory(model, env_):
                return transport
            code = pse.main(h.argv("rate", "--execute", "--max-calls", "20"), env=h.env, transport_factory=factory,
                            clock=clock, sleeper=sleeper)
            self.assertEqual(code, 0)
            self.assertEqual(transport.calls, 8)
            self.assertEqual(h.receipt("rate")["counts"]["gate_denied"], 0)


class DocumentationTest(unittest.TestCase):
    RECORD_DOC = REPO / "stack" / "services" / "lighting-publisher" / "EGRESS-RECORD.md"

    def test_egress_record_doc_names_the_gate_denial_reasons(self):
        # (d) the documented record denials are the ones the gate reports
        text = self.RECORD_DOC.read_text(encoding="utf-8")
        self.assertTrue(text.isascii())
        self.assertNotIn("record_no_record", text)
        for reason in ("record_invalid", "record_absent", "record_not_a_file", "record_insecure"):
            self.assertIn(f"`{reason}`", text, reason)
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            env = {eg.ENV_FLAG: "1"}
            bad = d / "bad.json"
            bad.write_text(json.dumps({"schema": "nope", "enabled": True, "scopes": ["public_eval"]}))
            os.chmod(bad, 0o600)
            gate = eg.EgressGate(bad, lambda: None, d / "j.jsonl", env=env, clock=lambda: CLOCK_T)
            self.assertEqual(gate.request("public_eval").reason, "record_invalid")
            gate = eg.EgressGate(d / "missing.json", lambda: None, d / "j.jsonl", env=env, clock=lambda: CLOCK_T)
            self.assertEqual(gate.request("public_eval").reason, "record_absent")
            link = d / "link.json"
            os.symlink(bad, link)
            gate = eg.EgressGate(link, lambda: None, d / "j.jsonl", env=env, clock=lambda: CLOCK_T)
            self.assertEqual(gate.request("public_eval").reason, "record_not_a_file")
            loose = d / "loose.json"
            loose.write_text("{}")
            os.chmod(loose, 0o666)
            gate = eg.EgressGate(loose, lambda: None, d / "j.jsonl", env=env, clock=lambda: CLOCK_T)
            self.assertEqual(gate.request("public_eval").reason, "record_insecure")

    def test_egress_record_doc_states_retries_per_ticket(self):
        # (e) the record doc says one ticket is one SDK invocation of up to four requests and that 403 stops
        text = self.RECORD_DOC.read_text(encoding="utf-8")
        self.assertIn(f"max_retries={pse.MAX_RETRIES}", text)
        self.assertIn("TypeSafePermissionDeniedError", text)
        self.assertIn("four HTTP requests", text)

    def test_docstring_lists_execute_preconditions_in_code_order(self):
        # (d) the docstring order matches run(): max-calls, out, cache, roster, rows, model, gate, SDK + key
        doc = " ".join(pse.__doc__.split())
        markers = ["``--max-calls N`` given", "``--out`` below the eval root", "``--cache`` below the eval root",
                   "roster readable", "the rows parse", "pinned model id", "gate preflight", "SDK installed",
                   "API key non-empty"]
        positions = [doc.index(m) for m in markers]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(doc.isascii())
        self.assertIn("RECALL", doc)
        self.assertIn("other_room", doc)


class PacketFailureTest(unittest.TestCase):
    def test_leaky_row_is_skipped_and_reported_without_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            rows = ROWS + [{"row_id": "leak", "scene": "Kitchen",
                            "description": "A person reads the clock, it says 19:25, then cooks."}]
            h.rows.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            self.assertEqual(h.main(h.argv("leak")), 0)
            receipt = h.receipt("leak")
            self.assertEqual(receipt["counts"]["packets_failed"], 1)
            self.assertEqual(receipt["counts"]["packets_built"], 3)
            self.assertEqual(receipt["packet_failures"][0]["row_id"], "leak")
            self.assertNotIn("19:25", json.dumps(receipt))
            self.assertNotIn("19:25", (h.root / "leak" / "packets.jsonl").read_text())


# -- level 2 -------------------------------------------------------------------
def _dimensions(**pairs):
    """``{name: {status, description, frames}}`` from ``name=(status, description)``."""
    return {name: {"status": status, "description": description, "frames": [0, 3]}
            for name, (status, description) in pairs.items()}


def _observation(posture, activities, dimensions, summary="A short free narrative of the window."):
    """One typed observation in the shape ``semantic()`` returns."""
    return {"posture": posture, "activities": list(activities), "summary": summary,
            "dimensions": dimensions, "context": {}, "meaningful": True,
            "signature": json.dumps([posture, list(activities)], sort_keys=True)}


OBSERVATIONS = {
    # living_room: seated and still in front of a lit screen
    "v1.json": _observation("sitting", ["watching_tv"], _dimensions(
        posture=("visible", "The person is seated upright on the couch, facing the lit screen."),
        motion=("visible", "Almost no movement across the window beyond small shifts of the head."),
        location=("visible", "On the couch in the middle of the room."),
        interaction=("uncertain", "A remote may be resting on the armrest."),
        transition=("not_assessed", "No transition was assessed in this window."),
        visibility=("visible", "The view is clear and the whole body is in frame."))),
    # kitchen: standing at the counter, handling a pan
    "v2.json": _observation("standing", ["cooking"], _dimensions(
        posture=("visible", "The person stands square to the counter with both arms raised."),
        motion=("visible", "Repeated arm movement over the hob, the body otherwise planted."),
        location=("visible", "At the counter beside the hob."),
        interaction=("visible", "Hands are on a pan handle and a wooden spoon."),
        visibility=("visible", "Clear view of the upper body, legs out of frame."))),
    # other_room: horizontal and motionless
    "v3.json": _observation("lying_down", ["sleeping"], _dimensions(
        posture=("visible", "The person is horizontal on the mattress with the head on a pillow."),
        motion=("visible", "No movement at all for the whole window."),
        location=("visible", "On the mattress against the far wall."),
        visibility=("occluded", "A quilt covers most of the body."))),
}

VISUAL_ROWS = [
    {"id": "v1", "scene": "Living room", "observation": "v1.json",
     "description": "A person watches television from the couch and does not get up.",
     "native_classes": [{"class_id": "c132", "class_name": "Watching television"}],
     "selected_for": ["tv_attention"],
     "targets": {"tv_attention": "positive", "eating": "negative", "food_prep": "unobserved",
                 "settling": "negative", "rest_state": "negative"}},
    {"id": "v2", "scene": "Kitchen", "observation": "v2.json",
     "description": "A person cooks food on a stove and stirs a pan.",
     "native_classes": [{"class_id": "c147", "class_name": "Someone is cooking something"}],
     "selected_for": ["food_prep"],
     "targets": {"tv_attention": "negative", "eating": "negative", "food_prep": "positive",
                 "settling": "unobserved", "rest_state": "negative"}},
    {"id": "v3", "scene": "Bedroom", "observation": "v3.json",
     "description": "A person lies on a bed without moving.",
     "native_classes": [{"class_id": "c134", "class_name": "Lying on a bed"}],
     "selected_for": ["rest_state"],
     "targets": {"tv_attention": "unobserved", "eating": "negative", "food_prep": "negative",
                 "settling": "positive", "rest_state": "positive"}},
]


class VisualHarness(Harness):
    """A level-2 eval root: rows that name rich-observation files next to them."""

    def __init__(self, tmp: Path, rows=None, observations=None, **kwargs):
        super().__init__(tmp, **kwargs)
        self.observations = tmp / "observations"
        self.observations.mkdir()
        for name, observation in (OBSERVATIONS if observations is None else observations).items():
            self.write_observation(name, observation)
        self.write_rows(VISUAL_ROWS if rows is None else rows)

    def write_observation(self, name, observation):
        path = self.observations / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(observation), encoding="utf-8")

    def write_rows(self, rows):
        self.rows.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def argv(self, out: str, *extra):
        return super().argv(out, "--level", "2", "--observations", str(self.observations), *extra)

    def packets(self, out: str):
        return [json.loads(line) for line in (self.root / out / "packets.jsonl").read_text().splitlines()]


class LevelTwoPacketTest(unittest.TestCase):
    def test_packet_is_built_from_the_observation_alone(self):
        row = pse.parse_row(VISUAL_ROWS[0], 1, pse.LEVEL_VISUAL)
        packet, provenance = pse.build_observation_packet(row, OBSERVATIONS["v1.json"])
        camera = packet["cameras"]["living_room"]
        # dimension descriptions in DIMENSION_ORDER, not_assessed dropped, uncertain prefixed
        self.assertEqual(camera["claims"], [
            "The person is seated upright on the couch, facing the lit screen.",
            "Almost no movement across the window beyond small shifts of the head.",
            "On the couch in the middle of the room.",
            "uncertain: A remote may be resting on the armrest.",
            "The view is clear and the whole body is in frame."])
        # posture and activities are the account
        self.assertEqual(camera["account"], "posture sitting; activity hypotheses watching_tv")
        self.assertTrue(provenance["account"])
        self.assertEqual(provenance["claims"], 5)
        # the dataset caption and the free summary are not in the packet
        blob = json.dumps(packet)
        self.assertNotIn("watches television", blob)
        self.assertNotIn("free narrative", blob)
        self.assertEqual(packet["devices"]["media"],
                         [{"role": "tv", "state": "playing", "source_kind": "unknown", "age_s": 0}])
        self.assertEqual(camera["people"], [])

    def test_summary_only_with_the_flag(self):
        row = pse.parse_row(VISUAL_ROWS[1], 1, pse.LEVEL_VISUAL)
        packet, _ = pse.build_observation_packet(row, OBSERVATIONS["v2.json"], with_summary=True)
        self.assertEqual(packet["cameras"]["kitchen"]["claims"][0],
                         "A short free narrative of the window.")

    def test_observation_without_claim_text_is_refused(self):
        row = pse.parse_row(VISUAL_ROWS[0], 1, pse.LEVEL_VISUAL)
        empty = _observation(None, [], _dimensions(
            posture=("not_assessed", "Nothing was assessed."),
            motion=("not_assessed", "Nothing was assessed.")))
        with self.assertRaises(pse.ObservationError):
            pse.build_observation_packet(row, empty)

    def test_inline_observation_and_nested_key(self):
        row = pse.parse_row({**VISUAL_ROWS[0], "observation": OBSERVATIONS["v1.json"]}, 1, pse.LEVEL_VISUAL)
        inline, digest = pse.load_observation(row, None, Path("/nonexistent"))
        self.assertEqual(inline["posture"], "sitting")
        self.assertEqual(len(digest), 64)
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "n.json"
            nested.write_text(json.dumps({"schema": "x", "observation": OBSERVATIONS["v1.json"]}), encoding="utf-8")
            row = pse.parse_row({**VISUAL_ROWS[0], "observation": "n.json"}, 1, pse.LEVEL_VISUAL)
            loaded, nested_digest = pse.load_observation(row, None, Path(tmp))
            self.assertEqual(loaded, inline)
            self.assertEqual(nested_digest, digest)

    def test_cache_key_separates_levels_and_observations(self):
        base = pse.cache_key("rowdigest", "qdigest", MODEL)
        self.assertEqual(base, pse.cache_key("rowdigest", "qdigest", MODEL, pse.LEVEL_TEXT))
        two = pse.cache_key("rowdigest", "qdigest", MODEL, pse.LEVEL_VISUAL, "obs-a")
        other = pse.cache_key("rowdigest", "qdigest", MODEL, pse.LEVEL_VISUAL, "obs-b")
        self.assertNotIn(base, (two, other))
        self.assertNotEqual(two, other)
        # a level-2 row with no observation digest is still not a level-1 key
        self.assertNotEqual(base, pse.cache_key("rowdigest", "qdigest", MODEL, pse.LEVEL_VISUAL))


class DatasetVocabularyTest(unittest.TestCase):
    def test_vocabulary_terms_of_a_row(self):
        row = pse.parse_row(VISUAL_ROWS[1], 1, pse.LEVEL_VISUAL)
        vocabulary = pse.dataset_vocabulary(row)
        self.assertIn("someone is cooking something", vocabulary["phrases"])
        self.assertIn("cooking something", vocabulary["phrases"])       # generic subject stripped
        self.assertIn("a person cooks food on a stove and stirs a pan", vocabulary["phrases"])
        self.assertIn("tv attention", vocabulary["phrases"])            # multi-word question ids
        self.assertIn("c147", vocabulary["tokens"])
        self.assertIn("unobserved", vocabulary["tokens"])
        self.assertNotIn("cooking", vocabulary["tokens"])               # ordinary English, not by default
        strict = pse.dataset_vocabulary(row, strict=True)
        self.assertIn("eating", strict["tokens"])                       # single-word question id
        # an observer taxonomy label is still a term; it is only exempt on the account
        self.assertIn("cooking", strict["tokens"])
        self.assertIn("cooking", strict["exempt_tokens"])
        self.assertNotIn("eating", strict["exempt_tokens"])
        tv_row = pse.parse_row(VISUAL_ROWS[0], 1, pse.LEVEL_VISUAL)
        self.assertIn("television", pse.dataset_vocabulary(tv_row, strict=True)["tokens"])

    def test_a_class_name_equal_to_a_taxonomy_label_is_exempt_on_the_account_only(self):
        # the manifests name their classes after the observer's own vocabulary;
        # "watching tv" in an account is the publisher's word, not a leak, but
        # the same words in a claim are the model echoing the label
        raw = {**VISUAL_ROWS[0], "description": "",
               "native_classes": [{"class_id": "ia-07", "class_name": "watching tv"}]}
        row = pse.parse_row(raw, 1, pse.LEVEL_VISUAL)
        vocabulary = pse.dataset_vocabulary(row, strict=True)
        self.assertIn("watching tv", vocabulary["phrases"])             # checked, not dropped
        self.assertIn("watching tv", vocabulary["exempt_phrases"])      # except on the account
        self.assertIn("watching", vocabulary["exempt_tokens"])          # its strict word too
        self.assertIn("watching tv", pse.vocabulary_exemptions(vocabulary))
        self.assertIn("ia 07", vocabulary["phrases"])                   # a multi-word id is a phrase
        skip = pse.STRUCTURAL_STRINGS | {row.camera}
        packet, _ = pse.build_observation_packet(row, OBSERVATIONS["v1.json"])
        self.assertEqual(packet["cameras"]["living_room"]["account"],
                         "posture sitting; activity hypotheses watching_tv")
        pse.assert_no_dataset_vocabulary(packet, vocabulary, skip)
        # the same term in a claim is refused, which is what the global
        # exemption used to let through on a third of the real rows
        leaking, _ = pse.build_observation_packet(row, _observation("sitting", ["watching_tv"], _dimensions(
            posture=("visible", "The person is watching tv from the couch."))))
        with self.assertRaises(pse.DatasetVocabularyError) as caught:
            pse.assert_no_dataset_vocabulary(leaking, vocabulary, skip)
        self.assertIn("claims[0]", str(caught.exception))
        self.assertNotIn("watching", str(caught.exception))

    def test_account_exemption_does_not_reach_the_target_words_or_the_caption(self):
        raw = {**VISUAL_ROWS[0], "native_classes": [{"class_id": "ia-07", "class_name": "eating"}]}
        row = pse.parse_row(raw, 1, pse.LEVEL_VISUAL)
        vocabulary = pse.dataset_vocabulary(row)
        # "eating" is one word, so it is exempt as a token: a one-word class
        # name matched as a raw substring fires on ordinary English that only
        # contains it ("eating" inside "seating"), which refused honest
        # observations before. What matters is that the term is exempt on the
        # account and nowhere else, whichever set carries it.
        self.assertEqual(vocabulary["exempt_phrases"], [])
        self.assertIn("eating", vocabulary["exempt_tokens"])
        self.assertIn("eating", vocabulary["tokens"])
        for word in pse.TARGET_WORDS:
            self.assertNotIn(word, vocabulary["exempt_tokens"])
        self.assertNotIn(pse.normalise_term(row.description), vocabulary["exempt_phrases"])

    def test_clean_packet_passes_and_a_leaking_one_is_refused(self):
        row = pse.parse_row(VISUAL_ROWS[1], 1, pse.LEVEL_VISUAL)
        skip = pse.STRUCTURAL_STRINGS | {row.camera}
        clean, _ = pse.build_observation_packet(row, OBSERVATIONS["v2.json"])
        pse.assert_no_dataset_vocabulary(clean, pse.dataset_vocabulary(row), skip)
        leaking = _observation("standing", ["cooking"], _dimensions(
            posture=("visible", "Someone is cooking something at the counter."),
            motion=("visible", "Repeated arm movement over the hob.")))
        packet, _ = pse.build_observation_packet(row, leaking)
        with self.assertRaises(pse.DatasetVocabularyError) as caught:
            pse.assert_no_dataset_vocabulary(packet, pse.dataset_vocabulary(row), skip)
        self.assertIn("claims[0]", str(caught.exception))
        self.assertNotIn("cooking", str(caught.exception))   # the path and the kind, never the term

    def test_class_id_and_target_word_are_refused(self):
        row = pse.parse_row(VISUAL_ROWS[1], 1, pse.LEVEL_VISUAL)
        skip = pse.STRUCTURAL_STRINGS | {row.camera}
        for description in ("The window is labelled c147 in the manifest.",
                            "The target for this window is positive."):
            packet, _ = pse.build_observation_packet(row, _observation(
                "standing", [], _dimensions(posture=("visible", description))))
            with self.assertRaises(pse.DatasetVocabularyError):
                pse.assert_no_dataset_vocabulary(packet, pse.dataset_vocabulary(row), skip)

    def test_camera_name_is_not_read_as_a_class_word(self):
        # strict mode turns "kitchen" into a class word; the camera the question
        # names is the runner's own string, not dataset text
        raw = {**VISUAL_ROWS[1], "native_classes": [{"class_id": "c147", "class_name": "Cooking in the kitchen"}]}
        row = pse.parse_row(raw, 1, pse.LEVEL_VISUAL)
        packet, _ = pse.build_observation_packet(row, _observation(
            "standing", [], _dimensions(posture=("visible", "The person stands at the counter."))))
        pse.assert_no_dataset_vocabulary(packet, pse.dataset_vocabulary(row, strict=True),
                                         pse.STRUCTURAL_STRINGS | {row.camera})


class LevelTwoRunTest(unittest.TestCase):
    def test_dry_run_builds_every_packet_and_makes_no_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = VisualHarness(Path(tmp))
            self.assertEqual(h.main(h.argv("dry")), 0)          # no transport: the factory asserts
            receipt = h.receipt("dry")
            self.assertEqual(receipt["level"], 2)
            self.assertFalse(receipt["executed"])
            self.assertEqual(receipt["status"], "complete")
            self.assertEqual(receipt["counts"]["rows"], 3)
            self.assertEqual(receipt["counts"]["packets_built"], 3)
            self.assertEqual(receipt["counts"]["would_be_calls"], 3)
            self.assertEqual(receipt["counts"]["calls_made"], 0)
            self.assertEqual(receipt["counts"]["observations_missing"], 0)
            self.assertEqual(receipt["counts"]["vocabulary_refusals"], 0)
            self.assertEqual(receipt["counts"]["tv_rows"], 1)
            self.assertTrue(receipt["gate"]["preflight"]["allowed"])
            self.assertEqual(receipt["level2"]["strict_vocabulary"], False)
            self.assertFalse((h.root / "dry" / "answers.jsonl").exists())
            packets = h.packets("dry")
            self.assertEqual([p["camera"] for p in packets], ["living_room", "kitchen", "other_room"])
            self.assertTrue(all(p["account"] for p in packets))
            self.assertTrue(all(len(p["observation_digest"]) == 64 for p in packets))
            # no dataset caption anywhere in the packets file
            body = (h.root / "dry" / "packets.jsonl").read_text()
            for row in VISUAL_ROWS:
                self.assertNotIn(row["description"], body)

    def test_missing_observation_is_one_refused_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = VISUAL_ROWS + [{**VISUAL_ROWS[0], "id": "v4", "observation": "gone.json"}]
            h = VisualHarness(Path(tmp), rows=rows)
            self.assertEqual(h.main(h.argv("miss")), 0)          # reported, not aborted
            receipt = h.receipt("miss")
            self.assertEqual(receipt["status"], "complete")
            self.assertEqual(receipt["counts"]["packets_built"], 3)
            self.assertEqual(receipt["counts"]["packets_failed"], 1)
            self.assertEqual(receipt["counts"]["observations_missing"], 1)
            failure = receipt["packet_failures"][0]
            self.assertEqual(failure["row_id"], "v4")
            self.assertEqual(failure["error"], "ObservationError")
            self.assertIn("gone.json", failure["detail"])

    def test_leaking_row_is_refused_and_the_others_still_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            observations = dict(OBSERVATIONS)
            observations["v2.json"] = _observation("standing", ["cooking"], _dimensions(
                posture=("visible", "Someone is cooking something at the counter.")))
            h = VisualHarness(Path(tmp), observations=observations)
            self.assertEqual(h.main(h.argv("leak")), 0)
            receipt = h.receipt("leak")
            self.assertEqual(receipt["counts"]["packets_built"], 2)
            self.assertEqual(receipt["counts"]["vocabulary_refusals"], 1)
            failure = receipt["packet_failures"][0]
            self.assertEqual(failure["error"], "DatasetVocabularyError")
            self.assertEqual(failure["row_id"], "v2")
            self.assertNotIn("cooking", json.dumps(receipt["packet_failures"]))

    def test_execute_scores_unobserved_targets_consequences_and_calibration(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = VisualHarness(Path(tmp))
            transport = FakeTransport()
            argv = h.argv("run1", "--execute", "--max-calls", "10", "--model", MODEL)
            self.assertEqual(h.main(argv, transport=transport), 0)
            self.assertEqual(transport.calls, 3)
            receipt = h.receipt("run1")
            self.assertEqual(receipt["level"], 2)
            self.assertEqual(receipt["rows_scored"], 3)
            table = receipt["table"]
            # unobserved targets are ignored for their question and counted
            self.assertEqual(table["food_prep"]["unobserved"], 1)
            self.assertEqual(table["food_prep"]["observed"], 2)
            self.assertEqual(table["tv_attention"]["unobserved"], 1)
            self.assertEqual(table["settling"]["unobserved"], 1)
            # these rows carry negatives, so every question is judged on precision and recall
            self.assertEqual(receipt["metrics"]["tv_attention"], "precision_recall")
            self.assertEqual(table["tv_attention"]["metric"], "precision_recall")
            # consequence flags
            consequences = receipt["consequences"]
            self.assertEqual(consequences["labels"], {"active": 2, "inactive": 1, "unknown": 0})
            apriori = consequences["by_threshold"]["apriori"]
            self.assertEqual(apriori["disruptive_brightening"]["eligible"], 1)
            self.assertEqual(apriori["darkness"]["eligible"], 2)
            self.assertEqual(apriori["disruptive_brightening"]["n"], 0)
            self.assertEqual(apriori["darkness"]["n"], 0)
            # calibration bins: ten of them, with n, per question
            bins = receipt["calibration"]["eating"]["bins"]
            self.assertEqual(len(bins), pse.CALIBRATION_BINS)
            self.assertEqual(sum(b["n"] for b in bins), receipt["calibration"]["eating"]["observed"])
            # P(yes) 0.05, 0.1 and 0.2 land in the first three bins, one each
            self.assertEqual([b["n"] for b in bins[:3]], [1, 1, 1])
            self.assertEqual([b["positives"] for b in bins[:3]], [0, 0, 0])
            self.assertAlmostEqual(bins[0]["mean_p"], 0.05)
            scores = (h.root / "run1" / "scores.md").read_text(encoding="utf-8")
            self.assertIn("# Public story eval, level 2", scores)
            self.assertIn("## Consequences", scores)
            self.assertIn("## Calibration", scores)

    def test_rerun_into_a_fresh_out_hits_the_level_two_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = VisualHarness(Path(tmp))
            first = FakeTransport()
            self.assertEqual(h.main(h.argv("run1", "--execute", "--max-calls", "10", "--model", MODEL),
                                    transport=first), 0)
            self.assertEqual(first.calls, 3)
            second = FakeTransport()
            self.assertEqual(h.main(h.argv("run2", "--execute", "--max-calls", "10", "--model", MODEL),
                                    transport=second), 0)
            self.assertEqual(second.calls, 0)
            self.assertEqual(h.receipt("run2")["counts"]["cache_hits"], 3)
            # a level-1 run over the same cache shares nothing with it
            self.assertEqual(h.receipt("run1")["counts"]["cache_hits"], 0)

    def test_consequence_flags_fire_on_the_beliefs(self):
        beliefs = pse.reduce_mod.reduce_answers(ANSWERS["living_room"])
        low = {qid: 0.9 for qid in pse.questions_mod.QUESTION_IDS}
        self.assertEqual(pse.consequence_outcomes(beliefs, low), {"brighten": False, "darken": False})
        loose = {qid: 0.2 for qid in pse.questions_mod.QUESTION_IDS}
        self.assertEqual(pse.consequence_outcomes(beliefs, loose), {"brighten": True, "darken": False})
        settled = pse.reduce_mod.reduce_answers(ANSWERS["other_room"])
        self.assertTrue(pse.consequence_outcomes(settled, {"settling": 0.5})["darken"])

    def test_label_activity_reads_three_way_targets(self):
        self.assertEqual(pse.label_activity({"tv_attention": True}), "active")
        self.assertEqual(pse.label_activity({"settling": False}), "active")
        self.assertEqual(pse.label_activity({"eating": False, "food_prep": False}), "inactive")
        self.assertEqual(pse.label_activity({"rest_state": True}), "inactive")
        self.assertEqual(pse.label_activity({qid: None for qid in pse.questions_mod.QUESTION_IDS}), "unknown")


# -- the observer batch's own rows ---------------------------------------------
# One line per selected window, in the shape the observer's batch runner
# writes: every selection field, plus the cache-relative path of the rich
# observation it filed for that window and the model it used. The batch files
# an observation at <key[:2]>/<key>.json under its cache root, and --observations
# is that root, so the reference below has the same shape as a produced one.
# The batch runner's handoff adds three fields to the selection row, not two:
# ``observation_status`` says whether the file named is real (observed, cached)
# or only planned by a dry run, and the contract paragraph in its docstring
# lists all three.
OBSERVER_ADDED_FIELDS = ("observation", "observation_model", "observation_status")
OBSERVER_ROW_FIELDS = ("id", "dataset", "split", "native_partition", "native_classes", "targets",
                       "stratum", "group_id", "subject_id", "media", "sample_times_s", "digest",
                       *OBSERVER_ADDED_FIELDS)
OBSERVATION_MODEL = "an-observation-model"
OBSERVER_CACHE_REFS = {"w1": "1f/1f9c00.json", "w2": "2a/2a4d10.json", "w3": "3b/3b7e20.json"}


def _observer_row(tag, observation, class_name, scene, stratum, targets, **extra):
    """One produced row; ``observation`` is whatever reference the test needs."""
    row = {
        "id": f"public:window:{tag}",
        "dataset": "public_a",
        "split": "development",
        "native_partition": "train",
        "native_classes": [{"class_id": f"public_a/activity:{class_name}", "class_name": class_name,
                            "start_s": 0.0, "end_s": 1.766667}],
        "targets": targets,
        "stratum": stratum,
        "group_id": f"public_a:session:{tag}",
        "subject_id": f"public_a:subject:{tag}",
        "media": {"kind": "file", "path": f"/nonexistent/{tag}.mp4", "sha256": "0" * 64},
        "sample_times_s": [3.1, 3.37, 3.6, 3.87, 4.1, 4.37, 4.6, 4.87],
        "digest": (tag * 32)[:64],
        "scene": scene,
        "selected_for": [stratum],
        "status": "cached",
        "frame_count": 8,
        "length_s": 1.766667,
        "observation": observation,
        "observation_model": OBSERVATION_MODEL,
        "observation_status": "observed",
    }
    row.update(extra)
    return row


# The three strata carry class names the manifests take from the observer's own
# taxonomy, which is the case the per-field exemption exists for. No row has a
# description: the produced rows carry none.
OBSERVER_ROWS = [
    _observer_row("w1", OBSERVER_CACHE_REFS["w1"], "watching tv", "Living room", "public_a_tv",
                  {"tv_attention": "positive", "eating": "negative", "food_prep": "unobserved",
                   "settling": "negative", "rest_state": "negative"}),
    _observer_row("w2", OBSERVER_CACHE_REFS["w2"], "cooking", "Kitchen", "public_a_cooking",
                  {"tv_attention": "negative", "eating": "negative", "food_prep": "positive",
                   "settling": "unobserved", "rest_state": "negative"}),
    _observer_row("w3", OBSERVER_CACHE_REFS["w3"], "sleeping", "Bedroom", "public_a_rest",
                  {"tv_attention": "unobserved", "eating": "negative", "food_prep": "negative",
                   "settling": "positive", "rest_state": "positive"}),
]
OBSERVER_OBSERVATIONS = {OBSERVER_CACHE_REFS["w1"]: OBSERVATIONS["v1.json"],
                         OBSERVER_CACHE_REFS["w2"]: OBSERVATIONS["v2.json"],
                         OBSERVER_CACHE_REFS["w3"]: OBSERVATIONS["v3.json"]}


class ObserverRowHandoffTest(unittest.TestCase):
    """The two halves join: a produced row runs through --level 2 unchanged."""

    def test_the_fixture_carries_every_contract_field(self):
        for row in OBSERVER_ROWS:
            for field in OBSERVER_ROW_FIELDS:
                self.assertIn(field, row)
            # three added fields, the count the batch runner's docstring states
            self.assertEqual(len(OBSERVER_ADDED_FIELDS), 3)
            self.assertTrue(set(OBSERVER_ADDED_FIELDS) <= set(row))

    def test_cache_relative_observation_resolves_under_the_observations_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = VisualHarness(Path(tmp), rows=OBSERVER_ROWS, observations=OBSERVER_OBSERVATIONS)
            self.assertEqual(h.main(h.argv("dry")), 0)
            receipt = h.receipt("dry")
            self.assertEqual(receipt["counts"]["packets_built"], 3)
            self.assertEqual(receipt["counts"]["observations_missing"], 0)
            self.assertEqual(receipt["counts"]["observations_unnamed"], 0)
            self.assertEqual(receipt["counts"]["vocabulary_refusals"], 0)
            self.assertEqual(receipt["level2"]["observation_models"], [OBSERVATION_MODEL])
            packets = h.packets("dry")
            self.assertEqual([p["row_id"] for p in packets],
                             [row["id"] for row in OBSERVER_ROWS])
            self.assertEqual([p["camera"] for p in packets], ["living_room", "kitchen", "other_room"])
            self.assertTrue(all(len(p["observation_digest"]) == 64 for p in packets))

    def test_an_inline_observation_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [_observer_row("w1", OBSERVATIONS["v1.json"], "watching tv", "Living room",
                                  "public_a_tv", dict(OBSERVER_ROWS[0]["targets"])),
                    _observer_row("w2", OBSERVER_CACHE_REFS["w2"], "cooking", "Kitchen",
                                  "public_a_cooking", dict(OBSERVER_ROWS[1]["targets"]))]
            h = VisualHarness(Path(tmp), rows=rows, observations=OBSERVER_OBSERVATIONS)
            self.assertEqual(h.main(h.argv("dry")), 0)
            self.assertEqual(h.receipt("dry")["counts"]["packets_built"], 2)

    def test_an_absolute_reference_is_contained_by_the_observations_root_too(self):
        # a rows file is data: containment cannot be sidestepped by writing the
        # reference absolutely, which the trusted producer never does
        with tempfile.TemporaryDirectory() as tmp:
            h = VisualHarness(Path(tmp), rows=OBSERVER_ROWS, observations=OBSERVER_OBSERVATIONS)
            inside = h.observations / OBSERVER_CACHE_REFS["w1"]
            outside = Path(tmp) / "elsewhere" / "obs.json"
            outside.parent.mkdir()
            outside.write_text(json.dumps(OBSERVATIONS["v1.json"]), encoding="utf-8")
            self.assertEqual(pse.resolve_observation_path(str(inside), h.observations, Path(tmp)),
                             inside.resolve())
            with self.assertRaises(pse.ObservationError) as caught:
                pse.resolve_observation_path(str(outside), h.observations, Path(tmp))
            self.assertIn("escapes", str(caught.exception))
            # with no --observations there is no root to contain it: unchanged
            self.assertEqual(pse.resolve_observation_path(str(outside), None, Path(tmp)), outside)

    def test_an_absolute_reference_outside_the_root_is_one_refused_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "elsewhere" / "obs.json"
            outside.parent.mkdir()
            outside.write_text(json.dumps(OBSERVATIONS["v1.json"]), encoding="utf-8")
            rows = list(OBSERVER_ROWS) + [_observer_row(
                "w4", str(outside), "walking", "Hallway", "public_a_walk",
                {"tv_attention": "unobserved", "eating": "negative", "food_prep": "negative",
                 "settling": "unobserved", "rest_state": "negative"})]
            h = VisualHarness(Path(tmp), rows=rows, observations=OBSERVER_OBSERVATIONS)
            self.assertEqual(h.main(h.argv("abs")), 0)
            receipt = h.receipt("abs")
            self.assertEqual(receipt["status"], "complete")
            self.assertEqual(receipt["counts"]["packets_built"], 3)
            self.assertEqual(receipt["counts"]["observations_missing"], 1)
            self.assertEqual(receipt["packet_failures"][0]["row_id"], "public:window:w4")
            self.assertIn("escapes", receipt["packet_failures"][0]["detail"])

    def test_a_reference_escaping_the_observations_root_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cache"
            (root / "1f").mkdir(parents=True)
            (root / "1f" / "in.json").write_text(json.dumps(OBSERVATIONS["v1.json"]), encoding="utf-8")
            outside = Path(tmp) / "outside.json"
            outside.write_text(json.dumps(OBSERVATIONS["v1.json"]), encoding="utf-8")
            inside = pse.resolve_observation_path("1f/in.json", root, Path(tmp))
            self.assertEqual(inside, root / "1f" / "in.json")
            for ref in ("../outside.json", "1f/../../outside.json"):
                with self.assertRaises(pse.ObservationError) as caught:
                    pse.resolve_observation_path(ref, root, Path(tmp))
                self.assertIn("escapes", str(caught.exception))

    def test_an_escaping_row_is_one_refused_row_in_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = list(OBSERVER_ROWS) + [_observer_row(
                "w4", "../outside.json", "walking", "Hallway", "public_a_walk",
                {"tv_attention": "unobserved", "eating": "negative", "food_prep": "negative",
                 "settling": "unobserved", "rest_state": "negative"})]
            h = VisualHarness(Path(tmp), rows=rows, observations=OBSERVER_OBSERVATIONS)
            # a real file one directory above the root: the refusal is containment, not absence
            (h.observations.parent / "outside.json").write_text(
                json.dumps(OBSERVATIONS["v1.json"]), encoding="utf-8")
            self.assertEqual(h.main(h.argv("esc")), 0)
            receipt = h.receipt("esc")
            self.assertEqual(receipt["status"], "complete")
            self.assertEqual(receipt["counts"]["packets_built"], 3)
            self.assertEqual(receipt["counts"]["observations_missing"], 1)
            self.assertEqual(receipt["packet_failures"][0]["row_id"], "public:window:w4")
            self.assertIn("escapes", receipt["packet_failures"][0]["detail"])

    def test_an_unobserved_row_is_counted_and_never_aborts_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = dict(OBSERVER_ROWS[2])
            pending.pop("observation")
            rows = OBSERVER_ROWS[:2] + [pending]
            h = VisualHarness(Path(tmp), rows=rows, observations=OBSERVER_OBSERVATIONS)
            self.assertEqual(h.main(h.argv("part")), 0)
            receipt = h.receipt("part")
            self.assertEqual(receipt["status"], "complete")
            self.assertEqual(receipt["counts"]["packets_built"], 2)
            self.assertEqual(receipt["counts"]["observations_unnamed"], 1)
            self.assertEqual(receipt["counts"]["observations_missing"], 1)
            self.assertEqual(receipt["packet_failures"][0]["row_id"], "public:window:w3")

    def test_the_account_exemption_is_measured_over_the_selection(self):
        # every one of these rows is named after a taxonomy term, which is the
        # 37 per cent case: the receipt says so and names the terms
        with tempfile.TemporaryDirectory() as tmp:
            h = VisualHarness(Path(tmp), rows=OBSERVER_ROWS, observations=OBSERVER_OBSERVATIONS)
            self.assertEqual(h.main(h.argv("dry")), 0)
            receipt = h.receipt("dry")
            self.assertEqual(receipt["counts"]["vocabulary_exempt_rows"], 3)
            self.assertEqual(receipt["level2"]["vocabulary_exempt_terms"],
                             ["cooking", "sleeping", "watching tv"])
            self.assertIn("account", receipt["level2"]["account_path"])

    def test_a_claim_echoing_the_class_name_is_still_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            observations = dict(OBSERVER_OBSERVATIONS)
            observations[OBSERVER_CACHE_REFS["w2"]] = _observation("standing", ["cooking"], _dimensions(
                posture=("visible", "The person is cooking at the hob.")))
            h = VisualHarness(Path(tmp), rows=OBSERVER_ROWS, observations=observations)
            self.assertEqual(h.main(h.argv("leak")), 0)
            receipt = h.receipt("leak")
            self.assertEqual(receipt["counts"]["vocabulary_refusals"], 1)
            self.assertEqual(receipt["counts"]["packets_built"], 2)
            failure = receipt["packet_failures"][0]
            self.assertEqual(failure["row_id"], "public:window:w2")
            self.assertEqual(failure["error"], "DatasetVocabularyError")
            self.assertNotIn("cooking", json.dumps(receipt["packet_failures"]))


def _cache_entry(row, observation):
    """One observation as the batch runner files it: the join keys are carried."""
    return {**observation, "schema": "home-v2-rich-observation-batch/v1", "valid": True,
            "id": row["id"], "window_digest": row["digest"], "model": row["observation_model"]}


def _entries_for(rows):
    return {row["observation"]: _cache_entry(row, OBSERVER_OBSERVATIONS[row["observation"]])
            for row in rows}


class ObservationJoinTest(unittest.TestCase):
    """The observation a row names must be the observation of that window."""

    def test_a_matching_entry_is_compared_on_all_three_fields(self):
        row = pse.parse_row(OBSERVER_ROWS[0], 1, pse.LEVEL_VISUAL)
        self.assertEqual(row.window_digest, OBSERVER_ROWS[0]["digest"])
        entry = _cache_entry(OBSERVER_ROWS[0], OBSERVATIONS["v1.json"])
        self.assertEqual(pse.assert_observation_belongs(row, entry),
                         ("id", "window_digest", "model"))

    def test_each_identity_field_refuses_on_its_own(self):
        row = pse.parse_row(OBSERVER_ROWS[0], 1, pse.LEVEL_VISUAL)
        for key, wrong in (("id", "public:window:somewhere-else"), ("window_digest", "f" * 64),
                           ("model", "a-different-model")):
            entry = {**_cache_entry(OBSERVER_ROWS[0], OBSERVATIONS["v1.json"]), key: wrong}
            with self.assertRaises(pse.ObservationMismatchError) as caught:
                pse.assert_observation_belongs(row, entry)
            self.assertIsInstance(caught.exception, pse.ObservationError)
            self.assertIn(key, str(caught.exception))
            self.assertIn(row.row_id, str(caught.exception))

    def test_a_payload_carrying_no_identity_fields_is_left_unchecked(self):
        # an inline observation, or any payload written without the join keys
        row = pse.parse_row(OBSERVER_ROWS[0], 1, pse.LEVEL_VISUAL)
        self.assertEqual(pse.assert_observation_belongs(row, OBSERVATIONS["v1.json"]), ())

    def test_a_correctly_paired_run_builds_every_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = VisualHarness(Path(tmp), rows=OBSERVER_ROWS, observations=_entries_for(OBSERVER_ROWS))
            self.assertEqual(h.main(h.argv("join")), 0)
            counts = h.receipt("join")["counts"]
            self.assertEqual(counts["packets_built"], 3)
            self.assertEqual(counts["observations_mismatched"], 0)
            self.assertEqual(counts["observations_missing"], 0)

    def test_a_mispaired_row_is_refused_and_counted_on_its_own(self):
        # w2 is made to point at w3's cache entry: without the check, one
        # window's observation would be scored against another's targets
        with tempfile.TemporaryDirectory() as tmp:
            rows = [dict(row) for row in OBSERVER_ROWS]
            rows[1]["observation"] = OBSERVER_CACHE_REFS["w3"]
            h = VisualHarness(Path(tmp), rows=rows, observations=_entries_for(OBSERVER_ROWS))
            self.assertEqual(h.main(h.argv("pair")), 0)
            receipt = h.receipt("pair")
            self.assertEqual(receipt["status"], "complete")
            self.assertEqual(receipt["counts"]["packets_built"], 2)
            self.assertEqual(receipt["counts"]["observations_mismatched"], 1)
            self.assertEqual(receipt["counts"]["observations_missing"], 0)
            failure = receipt["packet_failures"][0]
            self.assertEqual(failure["row_id"], "public:window:w2")
            self.assertEqual(failure["error"], "ObservationMismatchError")
            self.assertIn("another window", failure["detail"])
            self.assertEqual([p["row_id"] for p in h.packets("pair")],
                             ["public:window:w1", "public:window:w3"])


class CoverageTest(unittest.TestCase):
    """Refusals shrink the denominator; the receipt reports it and a floor can fail on it."""

    def test_coverage_counts_scored_over_selected(self):
        scored = [{"targets": {"eating": True, "settling": None}, "p": {"eating": 0.9, "settling": 0.1}}]
        selected = {"eating": 4, "settling": 2, "tv_attention": 0}
        coverage = pse.coverage_ratios(scored, selected)
        self.assertEqual(coverage["eating"], {"selected": 4, "scored": 1, "coverage": 0.25})
        self.assertEqual(coverage["settling"], {"selected": 2, "scored": 0, "coverage": 0.0})
        self.assertIsNone(coverage["tv_attention"]["coverage"])       # nothing selected, nothing to miss

    def test_table_and_scores_report_coverage_after_a_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            observations = dict(OBSERVATIONS)
            observations["v2.json"] = _observation("standing", ["cooking"], _dimensions(
                posture=("visible", "Someone is cooking something at the counter.")))
            h = VisualHarness(Path(tmp), observations=observations)
            argv = h.argv("run1", "--execute", "--max-calls", "10", "--model", MODEL)
            self.assertEqual(h.main(argv, transport=FakeTransport()), 0)
            receipt = h.receipt("run1")
            self.assertEqual(receipt["counts"]["vocabulary_refusals"], 1)
            coverage = receipt["coverage"]
            self.assertEqual(coverage["tv_attention"], {"selected": 2, "scored": 1, "coverage": 0.5})
            self.assertEqual(coverage["food_prep"], {"selected": 2, "scored": 1, "coverage": 0.5})
            self.assertEqual(coverage["settling"], {"selected": 2, "scored": 2, "coverage": 1.0})
            self.assertEqual(receipt["table"]["tv_attention"]["selected"], 2)
            self.assertEqual(receipt["table"]["tv_attention"]["coverage"], 0.5)
            self.assertIsNone(receipt["coverage_check"])
            scores = (h.root / "run1" / "scores.md").read_text(encoding="utf-8")
            header = [line for line in scores.splitlines() if line.startswith("| question |")][0]
            self.assertIn("selected", header)
            self.assertIn("coverage", header)
            row = [line for line in scores.splitlines() if line.startswith("| tv_attention |")][0]
            self.assertIn("0.500", row)

    def test_min_coverage_fails_the_run_and_still_writes_the_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            observations = dict(OBSERVATIONS)
            observations["v2.json"] = _observation("standing", ["cooking"], _dimensions(
                posture=("visible", "Someone is cooking something at the counter.")))
            h = VisualHarness(Path(tmp), observations=observations)
            argv = h.argv("low", "--execute", "--max-calls", "10", "--model", MODEL,
                          "--min-coverage", "0.9")
            self.assertEqual(h.main(argv, transport=FakeTransport()), pse.EXIT_REFUSED)
            receipt = h.receipt("low")
            self.assertEqual(receipt["status"], "coverage_below_minimum")
            self.assertIn("min-coverage", receipt["stop_reason"])
            check = receipt["coverage_check"]
            self.assertTrue(check["checked"])
            self.assertFalse(check["passed"])
            self.assertEqual(check["min_coverage"], 0.9)
            self.assertEqual(check["below"], ["eating", "food_prep", "rest_state", "tv_attention"])
            self.assertTrue((h.root / "low" / "scores.md").exists())
            self.assertIn("min coverage: 0.9", (h.root / "low" / "scores.md").read_text(encoding="utf-8"))

    def test_a_met_floor_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = VisualHarness(Path(tmp))
            argv = h.argv("ok", "--execute", "--max-calls", "10", "--model", MODEL,
                          "--min-coverage", "1.0")
            self.assertEqual(h.main(argv, transport=FakeTransport()), 0)
            receipt = h.receipt("ok")
            self.assertEqual(receipt["status"], "complete")
            self.assertTrue(receipt["coverage_check"]["passed"])
            self.assertEqual(receipt["coverage_check"]["below"], [])

    def test_a_dry_run_records_the_floor_as_unchecked(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = VisualHarness(Path(tmp))
            self.assertEqual(h.main(h.argv("dry", "--min-coverage", "0.9")), 0)
            check = h.receipt("dry")["coverage_check"]
            self.assertFalse(check["checked"])
            self.assertEqual(check["below"], [])

    def test_a_floor_outside_zero_to_one_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = VisualHarness(Path(tmp))
            self.assertEqual(h.main(h.argv("bad", "--min-coverage", "1.5")), pse.EXIT_REFUSED)



if __name__ == "__main__":
    unittest.main()
