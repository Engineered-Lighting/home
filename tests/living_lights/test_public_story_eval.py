"""Offline tests for tools/public_story_eval.py (ladder level 1 runner).

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

    def test_level_two_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            self.assertEqual(h.main(h.argv("dry", "--level", "2")), pse.EXIT_REFUSED)


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


if __name__ == "__main__":
    unittest.main()
