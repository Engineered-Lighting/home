import json
from pathlib import Path
import unittest

from geometry import (robust_depth, deproject, transform_point, floor_projection,
                      zone_relation, surface_proximity, TrackBank)
from reasoning import (build_jev_request, causal_evidence, validate_jev_response,
                       diagnostic_patch, score_intervals, qwen_windows, recommendation)


class GeometryTests(unittest.TestCase):
    def test_robust_support_refuses_discontinuity_and_stale_depth(self):
        depth = [[2000]*12 for _ in range(12)]
        depth[4][4] = 5000
        result = robust_depth(depth, [0,0,12,12])
        self.assertEqual(result["depth_m"], 2)
        self.assertTrue(result["surface_only"])
        split = [[1000]*6+[3000]*6 for _ in range(12)]
        self.assertEqual(robust_depth(split,[0,0,12,12])["reason"], "depth_discontinuity")
        self.assertEqual(robust_depth(depth,[0,0,12,12],age_s=1)["status"], "unknown")
        self.assertEqual(robust_depth(depth,[0,0,12,12],visible=False)["reason"], "occluded_surface")

    def test_invalid_pixels_do_not_become_zero_metres(self):
        depth = [[0]*8 for _ in range(8)]
        self.assertIsNone(robust_depth(depth,[0,0,8,8])["depth_m"])
        self.assertEqual(robust_depth(depth,[-1,0,8,8])["reason"], "roi_outside_depth_frame")

    def test_coordinate_provenance_and_metric_transform(self):
        p = deproject([10,20], 2, {"fx":100,"fy":100,"cx":10,"cy":20},
                      source_frame="left",calibration_version="cal-1")
        m = [[1,0,0,1],[0,1,0,0],[0,0,1,3],[0,0,0,1]]
        result = transform_point(p,m,source_frame="left",target_frame="room",version="map-1",
                                 calibration_error_m=.1,source_uncertainty_m=.03)
        self.assertEqual(result["xyz_m"],[1,0,5])
        self.assertAlmostEqual(result["uncertainty_m"],.13)
        with self.assertRaises(ValueError):
            transform_point(p,m,source_frame="rgb",target_frame="room",version="map-1",calibration_error_m=.1)
        m[0][0]=1000
        with self.assertRaises(ValueError):
            transform_point(p,m,source_frame="left",target_frame="room",version="map-1",calibration_error_m=.1)

    def test_room_boundary_and_other_home_are_not_certain_membership(self):
        p={"xyz_m":[.05,0,1],"uncertainty_m":.1}
        poly=[[0,0],[2,0],[2,2],[0,2]]
        self.assertEqual(zone_relation(p,poly,site_id="victoria",map_site_id="victoria",map_version="1")["relation"],"boundary")
        with self.assertRaises(ValueError):
            zone_relation(p,poly,site_id="victoria",map_site_id="echo",map_version="1")
        r=surface_proximity(p,[[0,0,0],[1,1,1]],map_version="1")
        self.assertEqual(r["contact"],"unobserved")

    def test_rgb_floor_is_explicit_estimate(self):
        r=floor_projection([10,20],[[.1,0,0],[0,.1,0],[0,0,1]],calibration_version="1",reference_error_m=.2)
        self.assertEqual(r["xyz_m"],[1,0,2])
        self.assertEqual(r["method"],"rgb_floor_contact")

    def test_lost_track_creates_new_id_without_identity(self):
        tracker=TrackBank("victoria","den","epoch1")
        first=tracker.update([[0,0,1],[3,0,1]],1)
        second=tracker.update([[3.1,0,1],[.1,0,1]],2)
        self.assertEqual(first[0]["track_id"],second[1]["track_id"])
        with self.assertRaises(ValueError): tracker.update([[0,0,1]],2)
        third=tracker.update([[.1,0,1]],10)
        self.assertNotEqual(first[0]["track_id"],third[0]["track_id"])
        self.assertIsNone(third[0]["identity_hypothesis"])


class ReasoningTests(unittest.TestCase):
    def record(self, **kw):
        return {"id":"obs1","site_id":"victoria","session_id":"s1","arm":"a",
                "generation":1,"observed_at":9,"available_at":10,"valid_until":12,
                "objects":[{"label":"book"}],"annotations":["secret-label"],**kw}

    def test_causal_window_blocks_future_other_arm_site_and_labels(self):
        records=[self.record(),self.record(id="future",available_at=11),
                 self.record(id="b",arm="b"),self.record(id="echo",site_id="echo"),
                 self.record(id="stale",valid_until=9),self.record(id="old",generation=0)]
        params=dict(site_id="victoria",session_id="s1",arm="a",generation=1,cutoff=10)
        result=causal_evidence(records,**params)
        self.assertEqual([r["id"] for r in result],["obs1"])
        self.assertNotIn("annotations",result[0])
        body,receipt=build_jev_request(records,model="jev-1.13",**params)
        self.assertEqual(receipt["evidence_ids"],["obs1"])
        self.assertNotIn("secret-label",json.dumps(body))

    def test_jev_never_invents_noul_confidence_or_accepts_version_drift(self):
        q={"yes":{"type":"noul","instructions":"Evidence sufficient?"}}
        body={"model":"jev-1.13.0","answers":{"yes":{"type":"noul","noul":.4}}}
        self.assertEqual(validate_jev_response(body,q,expected_version="jev-1.13.0"),body)
        body["answers"]["yes"]["confidence"]=.9
        with self.assertRaises(ValueError): validate_jev_response(body,q,expected_version="jev-1.13.0")
        with self.assertRaises(ValueError): validate_jev_response(body,q,expected_version="different")

    def test_diagnostics_do_not_overwrite_or_change_authority(self):
        original=[self.record()]
        fixed,receipt=diagnostic_patch(original,"obs1","posture","seated")
        self.assertNotIn("posture",original[0])
        self.assertEqual(fixed[0]["posture"],"seated")
        self.assertFalse(receipt["scored"])
        with self.assertRaises(ValueError): diagnostic_patch(original,"obs1","site_id","echo")

    def test_empty_evaluation_and_multilabel_quality_are_honest(self):
        self.assertIsNone(score_intervals([],[])["exact_match_at_coverage"])
        truth=[{"start":0,"end":10,"activities":["reading","resting"],"reviewed":True}]
        predictions=[{"start":0,"end":10,"available_at":2,"valid_until":10,
                      "activities":["reading"],"confidence":.9}]
        result=score_intervals(truth,predictions)
        self.assertEqual(result["coverage"],.8)
        self.assertEqual(result["exact_match_at_coverage"],0)
        self.assertEqual(result["jaccard_at_coverage"],.5)
        self.assertEqual(result["false_confident_seconds"],8)

    def test_missed_transitions_and_offline_latency(self):
        truth=[{"start":0,"end":5,"activities":["reading"],"reviewed":True},
               {"start":5,"end":10,"activities":["phone_use"],"reviewed":True}]
        prediction=[{"start":0,"end":10,"available_at":0,"valid_until":10,"activities":["reading"]}]
        self.assertEqual(score_intervals(truth,prediction)["missed_transitions"],1)
        offline=score_intervals(truth,prediction,run_mode="causal_offline")
        self.assertIsNone(offline["transitions"][0]["delay_s"])
        self.assertEqual(offline["latency_kind"],"offline_not_live")
        self.assertEqual(score_intervals(truth,[{**prediction[0],"mode":"diagnostic"}])["coverage"],0)

    def test_sequential_windows_do_not_sample_future(self):
        windows=list(qwen_windows(12))
        self.assertEqual([w["cutoff"] for w in windows],[5,10,12])
        self.assertTrue(all(max(w["sample_times"])<=w["cutoff"] for w in windows))

    def test_recommendation_requires_review_and_cannot_actuate(self):
        r=recommendation({"activity":"reading"},{},current_time=1,valid_until=5)
        self.assertEqual(r["status"],"needs_preference")
        self.assertFalse(r["actuation"])

    def test_catalog_has_all_recipes_and_no_fake_hashes(self):
        data=json.loads(Path(__file__).with_name("experiments.json").read_text())
        self.assertEqual(len(data["families"]),8)
        self.assertEqual(len(data["experiments"]),19)
        self.assertEqual(len({e["id"] for e in data["experiments"]}),19)
        self.assertNotIn("sha256",json.dumps(data))


if __name__ == "__main__":
    unittest.main()
