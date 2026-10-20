import hashlib
from pathlib import Path
import tempfile
import unittest

from rtmpose_worker import RTMPoseWorker, normalize_pose, CHECKPOINT, SOURCE_FILES
from reasoning import build_jev_request


class PoseTests(unittest.TestCase):
    def test_export_rgb_order_without_mutating_registered_bgr(self):
        import numpy as np
        seen=[]
        worker=RTMPoseWorker(self.manifest(),lambda *_:True)
        def model(image,bboxes):
            seen.append(image.copy())
            return [[[20,30]]*17],[[.8]*17]
        worker.model=model
        image=np.zeros((100,100,3),dtype=np.uint8)
        image[:]=[10,20,30]
        result=worker.infer(image,self.crops()[:1],frame_id='f1',request_id='rgb-proof')
        self.assertEqual(seen[0][0,0].tolist(),[30,20,10])
        self.assertEqual(image[0,0].tolist(),[10,20,30])
        self.assertTrue(seen[0].flags.c_contiguous)
        self.assertEqual(result['processing']['model_input_channel_order'],'RGB')
        self.assertEqual(result['processing']['preprocessing_revision'],'body7-rgb-v1')
        self.assertEqual(result['processing']['runtime_threads'],{'intra_op':1,'inter_op':1,'execution':'sequential'})

    def manifest(self):
        return {"checkpoint":CHECKPOINT,"runtime_version":"0.0.16","precision":"fp32",
            "input_size":[192,256],"layout":"coco17","format":"onnx",
            "sha256":hashlib.sha256(b"fixture not ONNX").hexdigest(),
            "source_sha256":{p:"a"*64 for p in SOURCE_FILES},"license":"Apache-2.0",
            "model_version":"fixture-body7","profile_id":"pose-a","device_id":"fixture-cpu",
            "engine_epoch":"engine1","onnxruntime_version":"fixture","device":"cpu",
            "score_threshold":.4,"max_people":4}

    def crops(self):
        return [{"id":"person-a","frame_id":"f1","detector_version":"fixture-detect","xyxy":[0,0,50,100]},
                {"id":"person-b","frame_id":"f1","detector_version":"fixture-detect","xyxy":[60,0,100,100]}]

    def test_multi_person_edges_never_cross_instances(self):
        points=[[[20,30] for _ in range(17)],[[80,30] for _ in range(17)]]
        scores=[[1.7]*17,[.8]*17]
        value=normalize_pose(points,scores,self.crops(),frame_id="f1",width=100,height=100,threshold=.4)
        self.assertEqual(len(value["keypoints"]),34)
        for a,b in value["skeleton_edges"]:
            self.assertEqual(value["keypoints"][a]["instance_id"],value["keypoints"][b]["instance_id"])
        self.assertEqual(value["pose_instances"][0]["joints"][0]["score"],1.7)
        self.assertNotIn("confidence",value["keypoints"][0])
        self.assertNotIn("depth",value)

    def test_uncertain_joints_are_not_zero_filled_or_connected(self):
        points=[[[20,30] for _ in range(17)]]
        points[0][5]=[-10,30]
        points[0][9]=[float("nan"),30]
        scores=[[.8]*17]; scores[0][7]=.1
        value=normalize_pose(points,scores,self.crops()[:1],frame_id="f1",width=100,height=100,threshold=.4)
        ids={p["joint_id"] for p in value["keypoints"]}
        self.assertFalse(ids.intersection({5,7,9}))
        joints=value["pose_instances"][0]["joints"]
        self.assertEqual(joints[5]["reason"],"outside_frame")
        self.assertEqual(joints[7]["reason"],"low_response")
        self.assertIsNone(joints[9]["pixel"])

    def test_wrong_layout_and_crop_count_fail(self):
        with self.assertRaises(ValueError):
            normalize_pose([[[1,1]]*133],[[.8]*133],self.crops()[:1],frame_id="f1",width=100,height=100,threshold=.4)
        with self.assertRaises(ValueError):
            normalize_pose([],[],self.crops(),frame_id="f1",width=100,height=100,threshold=.4)

    def test_no_person_crops_does_not_trigger_full_frame_sdk_fallback(self):
        import numpy as np
        calls=[]
        def sdk(image,bboxes):
            calls.append(bboxes)
            return np.zeros((len(bboxes),17,2))+20,np.ones((len(bboxes),17))*.8
        phases=[]
        def admit(phase,*_): phases.append(phase); return True
        worker=RTMPoseWorker(self.manifest(),admit,loader=lambda *_:sdk)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"pose.onnx"; path.write_bytes(b"fixture not ONNX")
            worker.load(path)
        image=np.zeros((100,100,3),dtype=np.uint8)
        empty=worker.infer(image,[],frame_id="f1",request_id="r0")
        self.assertFalse(empty["processing"]["model_invoked"])
        self.assertEqual(calls,[])
        result=worker.infer(image,self.crops(),frame_id="f1",request_id="r1")
        self.assertEqual(calls,[[c["xyxy"] for c in self.crops()]])
        self.assertEqual(len(result["pose_instances"]),2)
        self.assertEqual(phases,["load","load","first_infer"])
        self.assertEqual(result["processing"]["input_size_wh"],[192,256])
        with self.assertRaises(ValueError):
            worker.infer(image,self.crops(),frame_id="other",request_id="r2")

    def test_denied_load_and_unpinned_source_never_call_sdk(self):
        calls=[]
        worker=RTMPoseWorker(self.manifest(),lambda *_:False,loader=lambda *x:calls.append(x))
        with self.assertRaises(ValueError):worker.load("https://example.invalid/weights.zip")
        self.assertEqual(calls,[])
        bad=self.manifest(); bad["source_sha256"]={}
        with self.assertRaises(ValueError):RTMPoseWorker(bad,lambda *_:True)

    def test_pose_evidence_reaches_reasoning_without_human_label_leakage(self):
        observed=normalize_pose([[[20,30]]*17],[[.8]*17],self.crops()[:1],frame_id="f1",width=100,height=100,threshold=.4)
        record={"id":"obs1","site_id":"victoria","session_id":"s1","arm":"a","generation":1,
            "observed_at":100,"available_at":101,"valid_until":110,"pose_instances":observed["pose_instances"],
            "quality":observed["quality"],"labels":["human reading"],"model_version":"body7-fixture",
            "artifact_sha256":"a"*64,"engine_epoch":"engine1","capture_epoch":"capture1"}
        packet,_=build_jev_request([record],model="jev-1.13",site_id="victoria",session_id="s1",arm="a",generation=1,cutoff=101)
        evidence=packet["state"]["evidence"][0]
        self.assertEqual(len(evidence["pose_instances"][0]["joints"]),17)
        self.assertEqual(evidence["quality"]["metric_depth"],"not_measured")
        self.assertNotIn("labels",evidence)
        self.assertEqual(evidence["artifact_sha256"],"a"*64)
        self.assertEqual(evidence["capture_epoch"],"capture1")


if __name__=="__main__":unittest.main()
