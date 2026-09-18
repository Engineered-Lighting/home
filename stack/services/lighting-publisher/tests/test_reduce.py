"""reduce() against hand-computed probabilities; vendor confidence never gates."""
import unittest

from lighting_beliefs import reduce as r
from lighting_beliefs.questions import QUESTIONS, question_by_id


class ReduceTest(unittest.TestCase):
    def test_score_hand_computed(self):
        answer = {"type": "score", "score": 1.3, "confidence": 0.42,
                  "probabilities": {"0": 0.2, "1": 0.3, "2": 0.5}}
        belief = r.reduce_score(answer, question_by_id("tv_attention"))
        self.assertEqual(belief.p_level, (0.2, 0.3, 0.5))
        self.assertAlmostEqual(belief.p_ge(0), 1.0)
        self.assertAlmostEqual(belief.p_ge(1), 0.8)
        self.assertAlmostEqual(belief.p_ge(2), 0.5)
        self.assertAlmostEqual(belief.p_exactly(0), 0.2)
        self.assertAlmostEqual(belief.expected_level, 0.3 + 1.0)
        self.assertEqual(belief.metadata["vendor_confidence"], 0.42)

    def test_int_keys_and_missing_level(self):
        belief = r.reduce_score({"probabilities": {0: 0.75, 2: 0.25}}, question_by_id("rest_state"))
        self.assertEqual(belief.p_level, (0.75, 0.0, 0.25))
        self.assertAlmostEqual(belief.p_ge(1), 0.25)

    def test_renormalises_small_drift_and_rejects_large(self):
        belief = r.reduce_score({"probabilities": {"0": 0.5, "1": 0.3, "2": 0.21}}, question_by_id("settling"))
        self.assertAlmostEqual(sum(belief.p_level), 1.0)
        with self.assertRaises(r.AnswerError):
            r.reduce_score({"probabilities": {"0": 0.5, "1": 0.5, "2": 0.5}}, question_by_id("settling"))
        with self.assertRaises(r.AnswerError):
            r.reduce_score({"probabilities": {"0": 0.5, "3": 0.5}}, question_by_id("settling"))

    def test_noul(self):
        belief = r.reduce_noul({"type": "noul", "noul": 0.83}, question_by_id("eating"))
        self.assertAlmostEqual(belief.p_yes, 0.83)
        with self.assertRaises(r.AnswerError):
            r.reduce_noul({"noul": 1.4}, question_by_id("eating"))
        with self.assertRaises(r.AnswerError):
            r.reduce_noul({"noul": 0.5}, question_by_id("settling"))

    def test_reduce_answers_full_set(self):
        answers = {
            "tv_attention": {"probabilities": {"0": 0.1, "1": 0.2, "2": 0.7}, "confidence": 0.9},
            "eating": {"noul": 0.05},
            "food_prep": {"probabilities": {"0": 0.9, "1": 0.1, "2": 0.0}},
            "settling": {"probabilities": {"0": 0.6, "1": 0.3, "2": 0.1}},
            "rest_state": {"probabilities": {"0": 0.5, "1": 0.4, "2": 0.1}},
            "speculative_extra": {"noul": 0.5},
        }
        beliefs = r.reduce_answers(answers)
        self.assertEqual(set(beliefs), {q.id for q in QUESTIONS})
        self.assertAlmostEqual(beliefs["tv_attention"].p_ge(2), 0.7)
        self.assertAlmostEqual(beliefs["eating"].p_yes, 0.05)
        self.assertAlmostEqual(beliefs["rest_state"].p_exactly(2), 0.1)
        with self.assertRaises(r.AnswerError):
            r.reduce_answers({k: v for k, v in answers.items() if k != "settling"})

    def test_rejects_booleans_and_duplicate_levels(self):
        q = question_by_id("tv_attention")
        with self.assertRaises(r.AnswerError):
            r.reduce_score({"probabilities": {"0": True, "1": False, "2": False}}, q)
        with self.assertRaises(r.AnswerError):
            r.reduce_score({"probabilities": {"0": 0.5, 0: 0.5, "1": 0.5}}, q)
        with self.assertRaises(r.AnswerError):
            r.reduce_score({"probabilities": {True: 1.0}}, q)
        with self.assertRaises(r.AnswerError):
            r.reduce_score({"probabilities": {"0": float("inf"), "1": 0.0}}, q)
        with self.assertRaises(r.AnswerError):
            r.reduce_noul({"noul": True}, question_by_id("eating"))
        with self.assertRaises(r.AnswerError):
            r.reduce_noul({"noul": False}, question_by_id("eating"))
        with self.assertRaises(r.AnswerError):
            r.reduce_noul({"noul": float("nan")}, question_by_id("eating"))
        self.assertEqual(r.reduce_noul({"noul": 1}, question_by_id("eating")).p_yes, 1.0)

    def test_confidence_is_metadata_only(self):
        low = {"probabilities": {"0": 0.1, "1": 0.2, "2": 0.7}, "confidence": 0.01}
        high = dict(low, confidence=0.99)
        a = r.reduce_score(low, question_by_id("tv_attention"))
        b = r.reduce_score(high, question_by_id("tv_attention"))
        self.assertEqual(a.p_level, b.p_level)
        self.assertEqual(a.p_at_least, b.p_at_least)
        self.assertNotIn("confidence", a.as_dict())
        self.assertEqual(a.as_dict()["metadata"]["vendor_confidence"], 0.01)


if __name__ == "__main__":
    unittest.main()
