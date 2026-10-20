import unittest
from keypoint_reference import normalize_reference


class ReferenceTests(unittest.TestCase):
    def raw(self,people=1):
        return {"boxes":[[0,0,90,90] for _ in range(people)],"scores":[.9]*people,"labels":[1]*people,
            "keypoints":[[[20,30,1] for _ in range(17)] for _ in range(people)],
            "keypoints_scores":[[4.2]*17 for _ in range(people)]}

    def normalize(self,raw):
        return normalize_reference(raw,frame_id="frame1",width=100,height=100,person_threshold=.5,joint_threshold=2)

    def test_raw_scores_are_not_confidence_and_edges_do_not_cross_people(self):
        result=self.normalize(self.raw(2))
        self.assertEqual(result["keypoints"][0]["score"],4.2)
        self.assertNotIn("confidence",result["keypoints"][0])
        self.assertEqual(result["objects"][0]["confidence"],.9)
        for a,b in result["skeleton_edges"]:
            self.assertEqual(result["keypoints"][a]["instance_id"],result["keypoints"][b]["instance_id"])

    def test_unsupported_joints_remain_explicit_not_zero_filled(self):
        raw=self.raw()
        raw["keypoints"][0][0]=[1000,30,1]
        raw["keypoints_scores"][0][1]=.2
        raw["keypoints"][0][2]=[20,30,0]
        result=self.normalize(raw)
        self.assertEqual(len(result["keypoints"]),14)
        joints=result["pose_instances"][0]["joints"]
        self.assertEqual(len(joints),17)
        self.assertEqual([j["reason"] for j in joints[:3]],["outside_frame","low_response","not_reported"])
        self.assertNotIn("activity",result)

    def test_missing_scores_and_crowd_overflow_do_not_fake_pose(self):
        raw=self.raw(); del raw["keypoints_scores"]
        with self.assertRaises(KeyError): self.normalize(raw)
        with self.assertRaisesRegex(ValueError,"crowd"): self.normalize(self.raw(17))
        result=self.normalize(self.raw(0))
        self.assertEqual(result["pose_instances"],[])


if __name__=='__main__': unittest.main()
