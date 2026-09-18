"""The five questions are the ones the plan froze, in both shapes."""
import unittest

from lighting_beliefs import questions as q


class QuestionShapeTest(unittest.TestCase):
    def test_ids_and_primitives(self):
        self.assertEqual(q.QUESTION_IDS, ("tv_attention", "eating", "food_prep", "settling", "rest_state"))
        by_id = {x.id: x for x in q.build_questions()}
        self.assertEqual(by_id["eating"].primitive, q.NOUL)
        for qid in ("tv_attention", "food_prep", "settling", "rest_state"):
            self.assertEqual(by_id[qid].primitive, q.SCORE)
            self.assertEqual(by_id[qid].levels, 3)

    def test_dict_shape_matches_v1_endpoint(self):
        dicts = q.questions_as_dicts()
        self.assertEqual(set(dicts), set(q.QUESTION_IDS))
        self.assertEqual(dicts["tv_attention"]["type"], "score")
        self.assertEqual(len(dicts["tv_attention"]["criteria"]), 3)
        self.assertEqual(set(dicts["eating"]["criteria"]), {"true", "false"})
        for body in dicts.values():
            self.assertTrue(body["instructions"].strip())

    def test_levels_are_concrete_situations(self):
        # Each level must describe a situation, not just a degree word.
        for question in q.build_questions():
            if question.primitive != q.SCORE:
                continue
            for level in question.criteria:
                self.assertGreater(len(level.split()), 8, level)

    def test_ascii_only(self):
        for question in q.build_questions():
            question.instructions.encode("ascii")
            for text in (question.criteria if isinstance(question.criteria, list) else question.criteria.values()):
                text.encode("ascii")


if __name__ == "__main__":
    unittest.main()
