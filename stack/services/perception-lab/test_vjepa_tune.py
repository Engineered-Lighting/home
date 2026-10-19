"""AL-T den V-JEPA tuning tools: weak labels, clip export selection, probes,
calibration, smoothing, buckets and the leave-one-session-out evaluator.
Synthetic data only; no model, GPU, network or real ledger."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

import vjepa_live
import vjepa_scene as scene
import vjepa_tune_eval as tune_eval
import vjepa_tune_export as export
import vjepa_tune_labels as labels
import vjepa_tune_probe as probe
import vjepa_tune_synth as synth
from test_lab_regression import LedgerFixture

WEAK = json.loads(Path(__file__).with_name("vjepa_tune_config.json").read_text())["weak"]
CONFIG = json.loads(Path(__file__).with_name("vjepa_tune_config.json").read_text())


class WeakLabelTests(unittest.TestCase):
    def test_computer_work_needs_pc_input_and_a_visual_vote(self):
        self.assertEqual(labels.weak_label(["computer_work"], [], ["under_15s"], WEAK),
                         ("computer_work", "pc_input_active+qwen", True))
        self.assertEqual(labels.weak_label([], [("computer_work", .8)], ["15_to_60s"], WEAK)[0], "computer_work")
        self.assertEqual(labels.weak_label(["computer_work"], [("computer_work", .9)], ["over_5_min"], WEAK)[0], None)
        # PC input with a visual vote for something else is a conflict, not computer work.
        self.assertEqual(labels.weak_label(["phone_use"], [("computer_work", .9)], ["under_15s"], WEAK)[0], None)

    def test_visual_classes_need_qwen_and_jev_to_agree_without_pc_input(self):
        self.assertEqual(labels.weak_label(["reading", "reading"], [("reading", .7)], ["over_5_min"], WEAK),
                         ("reading", "qwen_jev_agree+pc_inactive", False))
        self.assertEqual(labels.weak_label(["reading"], [("reading", .7)], [], WEAK)[1], "qwen_jev_agree")
        self.assertIsNone(labels.weak_label(["reading"], [("reading", .5)], [], WEAK)[0])  # JEV not confident
        self.assertIsNone(labels.weak_label(["reading", "phone_use"], [("reading", .9)], [], WEAK)[0])  # Qwen split
        self.assertIsNone(labels.weak_label(["reading"], [("reading", .9), ("phone_use", .9)], [], WEAK)[0])
        self.assertIsNone(labels.weak_label(["reading"], [("reading", .9)], ["under_15s"], WEAK)[0])
        self.assertIsNone(labels.weak_label(["unclear"], [(None, .9)], [], WEAK)[0])

    def test_covering_needs_the_fraction_and_one_den_class(self):
        iv = [{"start": 0, "end": 10, "activity": "reading"}, {"start": 10, "end": 20, "activity": "phone_use"}]
        self.assertEqual(labels.covering(iv, 2, 6, .8), "reading")
        self.assertIsNone(labels.covering(iv, 8, 12, .8))
        self.assertIsNone(labels.covering([{"start": 0, "end": 9, "activity": "empty_room"}], 1, 5, .8))

    def test_label_clip_tiers_and_qwen_annotation_fallback(self):
        context = {"started_at": 1000.0, "vjepa_in_session": True, "qwen": [(1004.0, "phone_use")], "jev": [], "pc": [],
                   "intervals": [{"start": 0, "end": 30, "activity": "reading", "provenance": "owner_statement"},
                                 {"start": 0, "end": 30, "activity": "phone_use", "provenance": "qwen_proposed"}]}
        label = labels.label_clip(context, 1001.0, 1004.5, WEAK)
        self.assertEqual((label["gold"], label["owner"], label["weak"], label["weak_basis"]),
                         (None, "reading", "phone_use", "qwen_proposed_annotation"))
        self.assertTrue(label["vjepa_in_session"] and label["qwen_consumed_by_jev"])


class LedgerLabelTests(LedgerFixture):
    def test_session_contexts_read_qwen_jev_and_pc_input(self):
        sid, start = self.session("weak", 60)
        self.label(sid, "a1", 0, 30, "reading", reviewed=True, notes="owner confirmed")
        self.store.append_event(sid, "qwen.observation", {"status": "valid", "observed_at": start + 5,
                                                          "facts": {"activity_hypothesis": "reading"}})
        self.store.append_event(sid, "reasoning.output", {"mode": "live", "arm": "a", "prediction": {
            "start": 3, "valid_until": 9, "activities": ["reading"], "confidence": .8, "mode": "live"}})
        self.store.append_event(sid, "ha.observation", {"observed_at": start + 5, "ha": {
            labels.PC_FACT: {"state": "over_5_min", "status": "known"}}})
        db = self.ledger()
        try:
            contexts = labels.session_contexts(db, {})
        finally:
            db.close()
        label = labels.label_clip(contexts[sid], start + 2.5, start + 6.0, WEAK)
        self.assertEqual((label["gold"], label["weak"], label["vjepa_in_session"]), ("reading", "reading", False))


def frame(i, t, epoch="e1", available=None):
    return {"media_id": "m%03d" % i, "frame_id": "f%d" % i, "capture_epoch": epoch, "observed_at": t,
            "available_at": t + .2 if available is None else available, "size": 1, "sha256": "0" * 64}


class ExportSelectionTests(unittest.TestCase):
    def test_half_second_variant_equals_the_live_clip_search(self):
        rng = np.random.default_rng(3)
        live = vjepa_live.ClipFrames.__new__(vjepa_live.ClipFrames)
        live.slot_tolerance = .4
        for _ in range(40):
            times = np.cumsum(rng.uniform(.3, .7, 14))
            frames = [frame(i, 100 + t) for i, t in enumerate(times)]
            ours = export.select_clip(frames[-1], frames[:-1], .5, .4)
            theirs = live._search(frames[-1], frames[:-1])
            self.assertEqual(ours and [f["media_id"] for f in ours], theirs and [f["media_id"] for f in theirs])

    def test_clip_at_is_causal_and_stays_in_the_capture_epoch(self):
        frames = [frame(i, 100 + .5 * i) for i in range(12)]
        clip = export.clip_at(frames, 104.9, .5, .4)
        self.assertEqual(clip[-1]["observed_at"], 104.5)  # 105.0 is not available until 105.2
        self.assertEqual(len(clip), 8)
        split = [frame(i, 100 + .5 * i, epoch="e1" if i < 6 else "e2") for i in range(12)]
        self.assertIsNone(export.clip_at(split, 105.6, .5, .4))
        seven = [frame(i, 100 + 1.0 * i) for i in range(10)]
        self.assertEqual(len(export.clip_at(seven, 109.5, 1.0, .5)), 8)
        self.assertEqual(export.anchors(frames, 2.0, 3.5)[:2], [103.5, 105.5])


def toy(n=240, d=8, c=3, seed=0):
    rng = np.random.default_rng(seed)
    y = rng.integers(c, size=n)
    centres = rng.normal(0, 3, (c, d))
    return centres[y] + rng.normal(0, 1, (n, d)), y


class ProbeTests(unittest.TestCase):
    def test_linear_probe_separates_clusters(self):
        x, y = toy()
        model = probe.LinearProbe(3).fit(x, y)
        self.assertGreater((model.logits(x).argmax(1) == y).mean(), .9)

    def test_attentive_gradients_match_finite_differences(self):
        rng = np.random.default_rng(1)
        p = probe.AttentiveProbe(3, heads=2, key_dim=3, l2=.01)
        params = {k: v.astype(np.float64) for k, v in p._init(5).items()}
        params["Wo"] = rng.normal(0, .3, params["Wo"].shape)
        params["q"] = rng.normal(0, .5, params["q"].shape)
        x = rng.normal(0, 1, (4, 6, 5))
        Y = np.eye(3)[[0, 1, 2, 1]]
        w = np.array([1.0, .5, 2.0, 1.0])
        _, grads = p.gradients(params, x, Y, w)
        for key in ("Wk", "q", "Wo", "bo"):
            for index in [tuple(rng.integers(s) for s in params[key].shape) for _ in range(4)]:
                saved = params[key][index]
                params[key][index] = saved + 1e-5
                up = p.gradients(params, x, Y, w)[0]
                params[key][index] = saved - 1e-5
                down = p.gradients(params, x, Y, w)[0]
                params[key][index] = saved
                self.assertAlmostEqual(grads[key][index], (up - down) / 2e-5, places=4, msg=key)

    def test_attentive_probe_learns_a_localized_signal(self):
        header, rows, tokens = synth.make(sessions=2, clips=150, dim=64, grid=(4, 3, 3), session_shift=0.0, seed=1)
        y = np.array([probe.CLASSES.index(r["label"]["owner"]) for r in rows])
        train, test = np.arange(len(y)) % 2 == 0, np.arange(len(y)) % 2 == 1
        att = probe.AttentiveProbe(5, epochs=20, seed=0).fit(tokens[train], y[train])
        lin = probe.LinearProbe(5).fit(probe.pooled_features(tokens[train], header["grid"], "mean"), y[train])
        acc_att = (att.logits(tokens[test]).argmax(1) == y[test]).mean()
        acc_lin = (lin.logits(probe.pooled_features(tokens[test], header["grid"], "mean")).argmax(1) == y[test]).mean()
        self.assertGreater(acc_att, .85)
        self.assertGreaterEqual(acc_att, acc_lin - .02)

    def test_subset_reads_rows_in_the_requested_order(self):
        array = np.arange(20).reshape(10, 2)
        subset = probe.Subset(array, [7, 2, 5])
        self.assertEqual(subset[np.array([2, 0])].tolist(), [[10, 11], [14, 15]])
        self.assertEqual(subset[0:2].tolist(), [[14, 15], [4, 5]])
        self.assertEqual(len(subset), 3)

    def test_temperature_softens_overconfident_logits(self):
        rng = np.random.default_rng(0)
        y = rng.integers(3, size=300)
        logits = rng.normal(0, 1, (300, 3))
        logits[np.arange(300), y] += 1.0
        logits *= 4  # overconfident
        t = probe.fit_temperature(logits, y)
        self.assertGreater(t, 1.5)
        self.assertLess(probe.nll(logits, y, t), probe.nll(logits, y, 1.0))
        self.assertEqual(probe.fit_temperature(logits[:0], y[:0]), 1.0)


class SmoothingTests(unittest.TestCase):
    def test_causal_gap_reset_and_rules(self):
        p = np.array([[1, 0], [0, 1], [0, 1], [1, 0]], dtype=float)
        times = [0, 2, 4, 30]
        ema = probe.smooth(p, times, {"kind": "ema", "alpha": .5})
        np.testing.assert_allclose(ema[:3], [[1, 0], [.5, .5], [.25, .75]])
        np.testing.assert_allclose(ema[3], [1, 0])  # 26 s gap resets
        window = probe.smooth(p, times, {"kind": "window", "k": 2})
        np.testing.assert_allclose(window[1], [.5, .5])
        changed = p.copy()
        changed[3] = [0, 1]
        for spec in ({"kind": "ema"}, {"kind": "window", "k": 3}, {"kind": "hmm", "stay": .9}):
            np.testing.assert_allclose(probe.smooth(p, times, spec)[:3], probe.smooth(changed, times, spec)[:3])
        hmm = probe.smooth(np.array([[.6, .4]] * 5), [0, 2, 4, 6, 8], {"kind": "hmm", "stay": .9})
        self.assertTrue(np.all(np.diff(hmm[:, 0]) > 0))  # repeated weak evidence accumulates
        shuffled = probe.smooth(p[[2, 0, 3, 1]], [4, 0, 30, 2], {"kind": "ema", "alpha": .5})
        np.testing.assert_allclose(shuffled, ema[[2, 0, 3, 1]])


class BucketTests(unittest.TestCase):
    def test_thresholds_reach_targets_and_medium_never_exceeds_high(self):
        scores = np.linspace(0, 1, 200)
        positives = scores > .5
        b = probe.choose_buckets(scores, positives, {"high": .9, "medium": .7}, min_support=15)
        self.assertLessEqual(b["medium"], b["high"])
        precision, n = probe.precision_at(scores, positives, b["high"])
        self.assertGreaterEqual(precision, .9)
        self.assertGreaterEqual(n, 15)
        none = probe.choose_buckets(scores, np.zeros(200, bool), {"high": .9, "medium": .7}, min_support=15)
        self.assertEqual(none, {"high": None, "medium": None})
        strict = probe.choose_buckets(scores, positives, {"high": .9, "medium": .7}, min_support=15, effective_n=1 / 6)
        # The Wilson bound is never more lenient (None: unreachable with so few independent trials).
        self.assertTrue(strict["high"] is None or strict["high"] >= b["high"])

    def test_den_facts_emit_only_allowlisted_words(self):
        buckets = {"reading": {"high": .8, "medium": .5}, "phone_use": {"high": None, "medium": .4}}
        p = {"reading": .85, "computer_work": .02, "phone_use": .45, "resting": .05, "transition": .03}
        facts = probe.den_facts(p, buckets, calibration="den_cv_v1")
        self.assertEqual(facts["activity"], {"reading": "high", "computer_work": "not_calibrated", "phone_use": "medium",
                                             "resting": "not_calibrated", "transition": "not_calibrated"})
        self.assertEqual(facts["top_high"], "reading")
        words = set()

        def walk(value):
            if isinstance(value, dict):
                for k, v in value.items():
                    words.add(k)
                    walk(v)
            else:
                self.assertIsInstance(value, str)
                words.add(value)
        walk(facts)
        self.assertLessEqual(words, probe.facts_tokens())


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.header, self.rows, self.tokens = synth.make(sessions=4, clips=60, dim=12, grid=(2, 2, 2), seed=2)
        self.config = json.loads(json.dumps(CONFIG))
        self.config["probes"] = [dict(CONFIG["probes"][0], seeds=[0], steps=150),
                                 dict(CONFIG["probes"][2], seeds=[0], epochs=5)]
        self.config["smoothing"] = CONFIG["smoothing"][:2]
        self.config["buckets"]["min_support"] = 5
        self.config["buckets"]["min_positive"] = 3

    def test_folds_never_train_on_the_held_out_session(self):
        seen = []
        original = tune_eval.fit_predict

        def spy(spec, header, tokens, train, apply, tiers):
            held = {r["session_id"] for r in apply}
            self.assertEqual(len(held), 1)
            self.assertFalse(held & {r["session_id"] for r in train})
            seen.append(held.pop())
            return original(spec, header, tokens, train, apply, tiers)
        tune_eval.fit_predict = spy
        try:
            tune_eval.cross_validate(self.config["probes"][0], self.header, self.rows, self.tokens, self.config)
        finally:
            tune_eval.fit_predict = original
        self.assertEqual(sorted(seen), sorted({r["session_id"] for r in self.rows}))

    def test_weak_labels_train_but_never_score(self):
        rows = [dict(r, label={"weak": r["label"]["weak"]}) for r in self.rows]
        rows[0]["label"]["gold"] = self.rows[0]["label"]["owner"]
        chosen, _, truth = tune_eval.scored_rows(rows, {r["row"]: np.full(5, .2) for r in rows},
                                                 self.config["protocol"]["eval_tiers"])
        self.assertEqual([r["row"] for r in chosen], [0])

    def test_report_on_a_synthetic_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            synth.write(directory, self.header, self.rows, self.tokens)
            header, rows, tokens = tune_eval.load_cache(directory)
            report = tune_eval.evaluate(header, rows, tokens, self.config)
            head = tune_eval.fit_final(header, rows, tokens, self.config, "linear_mean", Path(directory) / "head.npz")
            with np.load(Path(directory) / "head.npz") as saved:
                meta = json.loads(str(saved["meta"]))
            del tokens  # release the memmap before the directory is removed (Windows)
        self.assertTrue(report["synthetic"])
        self.assertEqual(len(report["results"]), 4)
        self.assertIsNotNone(report["best"])
        self.assertIn("reading", report["labels"]["owner"])
        self.assertIsNotNone(report["baseline_pinned_readout"])
        self.assertEqual(meta["schema"], tune_eval.HEAD_SCHEMA)
        self.assertEqual(head["features"], "mean")
        for result in report["results"]:
            self.assertGreater(result["metrics"]["macro_f1"], .2)


if __name__ == "__main__":
    unittest.main()
