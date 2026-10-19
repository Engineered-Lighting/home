import io
from pathlib import Path
import unittest
import test_paired_runtime as fixtures
import test_rtmpose_worker as pose_fixtures

from registered_crops import RegisteredPersonCrops
from registered_image import RegisteredImage
from rtmpose_worker import RTMPoseWorker
from runtime import PairedRunner


class CropTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown
    frame=fixtures.PairedRuntimeTests.frame
    receipt=fixtures.PairedRuntimeTests.receipt
    runner=fixtures.PairedRuntimeTests.runner
    settle=fixtures.PairedRuntimeTests.settle

    def source(self):
        from PIL import Image
        data=io.BytesIO();Image.new("RGB",(2,2),(100,10,20)).save(data,format="PNG")
        source=self.frame()
        meta={k:source[k] for k in ("frame_id","capture_epoch","site_id","camera_id","observed_at")}
        self.media=self.store.add_media(self.session["id"],data.getvalue(),"image/png",dict(meta,stream="rgb",width=2,height=2))
        return self.frame()

    def boxes(self):
        return [{"x":0,"y":0,"w":1,"h":2,"label":"person","confidence":.8},
                {"x":1,"y":0,"w":1,"h":1,"label":"book","confidence":.9}]

    def evidence(self,frame,boxes=None,**changes):
        payload={"arm":"a","generation":0,"run_id":"a","model_version":"fixture-1",
            "artifact_sha256":"a"*64,"engine_epoch":"engine1","status":"completed",
            **{k:frame[k] for k in ("frame_id","capture_epoch","site_id","camera_id","observed_at","media_ids","valid_until")},
            "available_at":100,"result":{"schema_version":1,"frame_id":frame["frame_id"],
                "coordinate_space":"source_pixels","boxes":self.boxes() if boxes is None else boxes},**changes}
        return self.store.append_event(self.session["id"],"perception.output",payload)

    def resolver(self,**changes):
        reference={"arm":"a","generation":0,"profile_id":"a","model_version":"fixture-1",
            "artifact_sha256":"a"*64,"engine_epoch":"engine1"}
        return RegisteredPersonCrops(self.store,reference,**{**dict(minimum_confidence=.5,max_people=4,policy_revision="crop1"),**changes})

    def test_shared_crop_selection_is_identical_and_provenance_bound(self):
        frame=self.source(); event=self.evidence(frame)
        resolver=self.resolver()
        a,b=resolver(frame),resolver(frame)
        self.assertEqual(a,b)
        self.assertEqual(len(a["crops"]),1)
        self.assertEqual(a["crops"][0]["xyxy"],[0,0,1,2])
        self.assertEqual(a["crops"][0]["source_event_id"],event)
        self.assertEqual(a["crops"][0]["detector_artifact_sha256"],"a"*64)
        self.assertEqual(a["identity"],"not_inferred")

    def test_missing_is_not_empty_and_empty_does_not_fall_back(self):
        frame=self.source(); resolver=self.resolver()
        with self.assertRaisesRegex(ValueError,"missing"):resolver(frame)
        self.evidence(frame,boxes=[])
        self.assertEqual(resolver(frame)["observation"],"empty_person_crop_set")
        self.assertEqual(resolver(frame)["crops"],[])

    def test_duplicate_reference_results_are_not_silently_selected(self):
        frame=self.source();self.evidence(frame);self.evidence(frame,boxes=[])
        with self.assertRaisesRegex(ValueError,"ambiguous"):self.resolver()(frame)

    def test_stale_reference_and_wrong_coordinate_frame_rejected(self):
        frame=self.source()
        event=self.evidence(frame,valid_until=99)
        with self.assertRaisesRegex(ValueError,"stale"):self.resolver()(frame)
        with self.store.transaction() as db:
            db.execute("UPDATE events SET payload=json_set(payload,'$.valid_until',101,'$.result.coordinate_space','letterbox') WHERE id=?",(event,))
        with self.assertRaisesRegex(ValueError,"source-pixel"):self.resolver()(frame)

    def test_scoreless_humanart_boxes_need_explicit_none_threshold(self):
        from yolox_worker import normalize_result
        frame=self.source()
        # HumanArt/YOLOX normalization omits confidence rather than inventing one.
        boxes=normalize_result(([[0,0,1,2],[1,0,2,1]],[0,0]),['person']+['unused']*79,
                               frame_id=frame["frame_id"],width=2,height=2)["boxes"]
        self.evidence(frame,boxes=boxes)
        with self.assertRaisesRegex(ValueError,"model score"):self.resolver()(frame)
        bundle=self.resolver(minimum_confidence=None)(frame)
        self.assertEqual([c["xyxy"] for c in bundle["crops"]],[[0,0,1,2],[1,0,2,1]])
        self.assertIsNone(bundle["policy"]["minimum_confidence"])
        with self.assertRaises(ValueError):self.resolver(minimum_confidence=float("nan"))
        with self.assertRaises(ValueError):self.resolver(minimum_confidence=1.5)

    def test_none_threshold_never_ignores_returned_scores(self):
        frame=self.source();self.evidence(frame)
        with self.assertRaisesRegex(ValueError,"explicit threshold"):self.resolver(minimum_confidence=None)(frame)

    def test_crowd_budget_never_silently_truncates_people(self):
        frame=self.source();self.evidence(frame,boxes=[self.boxes()[0]]*5)
        with self.assertRaisesRegex(ValueError,"budget"):self.resolver()(frame)

    def test_paired_detector_then_shared_crop_pose_reaches_stored_overlay(self):
        import numpy as np
        frame=self.source()
        detector=self.runner(lambda request:self.receipt(request,result={"schema_version":1,
            "frame_id":frame["frame_id"],"coordinate_space":"source_pixels","boxes":self.boxes(),"objects":[]}))
        detector.offer(frame)
        self.settle(lambda:all(v is None for v in detector.snapshot()["active"].values()))
        crops=self.resolver()
        image=RegisteredImage(self.store)
        workers,profiles={},{}
        path=Path(self.directory.name)/"pose.onnx";path.write_bytes(b"fixture not ONNX")
        calls=[]
        def fake_pose(pixels,bboxes):
            calls.append(bboxes)
            return np.ones((len(bboxes),17,2))*.5,np.ones((len(bboxes),17))*.8
        for arm in ("a","b"):
            manifest=pose_fixtures.PoseTests().manifest();manifest["profile_id"]="pose-"+arm
            workers[arm]=RTMPoseWorker(manifest,lambda *_:True,loader=lambda *_:fake_pose)
            workers[arm].load(path)
            profiles[arm]={k:manifest[k] for k in ("model_version","profile_id","engine_epoch","device_id")}
            profiles[arm]["artifact_sha256"]=manifest["sha256"]
        runner=PairedRunner(self.store,self.session["id"],profiles,
            lambda req:workers[req["arm"]].execute(req,image,crops),clock=self.store.clock,experiment_generation=1)
        self.runners.append(runner);runner.offer(frame)
        self.settle(lambda:all(v is None for v in runner.snapshot()["active"].values()))
        results=[e["payload"] for e in self.store.list_events(self.session["id"])
                 if e["kind"]=="perception.output" and e["payload"]["generation"]==1]
        self.assertEqual(len(results),2)
        self.assertEqual(calls,[[[0,0,1,2]],[[0,0,1,2]]])
        self.assertEqual(results[0]["result"]["processing"]["crop_set_digest"],results[1]["result"]["processing"]["crop_set_digest"])
        for value in results:
            self.assertTrue(value["arm_snapshot"]["layers"]["skeleton"]["available"])
            self.assertEqual(len(value["arm_snapshot"]["keypoints"]),17)


if __name__=="__main__":unittest.main()
