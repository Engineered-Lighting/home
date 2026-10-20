import unittest
import test_paired_runtime as fixtures
from presentation import arm_snapshot
from service_runtime import ServiceRuntime


class PresentationTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown
    frame=fixtures.PairedRuntimeTests.frame
    receipt=fixtures.PairedRuntimeTests.receipt
    settle=fixtures.PairedRuntimeTests.settle
    runner=fixtures.PairedRuntimeTests.runner

    def request(self):
        self.media=self.store.add_media(self.session["id"],b"fixture only","image/jpeg",{
            "site_id":"victoria","camera_id":"den","frame_id":"frame1","capture_epoch":"capture1","width":640,"height":360,
            "observed_at":100,"clock_mapping_status":"verified","clock_mapping_id":"map1"})
        return {"frame":self.frame(observed_at=100,available_at=100),"profile":self.profiles["a"],"session_id":self.session["id"],"arm":"a"}

    def result(self,**changes):
        return {"schema_version":1,"frame_id":"frame1","coordinate_space":"source_pixels","objects":["book"],
                "boxes":[{"x":10,"y":20,"w":40,"h":60,"label":"book","confidence":.8}],
                "keypoints":[[20,40,.9],[30,50,.8]],"skeleton_edges":[[0,1]],**changes}

    def test_output_primitives_bound_to_real_source_identity_without_activity_claims(self):
        request=self.request()
        arm=arm_snapshot(self.store,request,self.result(),completed_at=100,current=True)
        self.assertTrue(arm["layers"]["boxes"]["available"])
        self.assertTrue(arm["layers"]["skeleton"]["available"])
        self.assertEqual(arm["frame"]["media_id"],self.media["id"])
        self.assertEqual(arm["frame"]["session_time_s"],0)
        self.assertEqual(arm["observations"],["book"])
        self.assertNotIn("judgment",arm)
        self.assertNotIn("masks",arm["layers"])

    def test_wrong_frame_and_unconverted_pixels_disable_overlays(self):
        request=self.request()
        for changes in ({"frame_id":"different"},{"coordinate_space":"letterboxed_model_pixels"}):
            arm=arm_snapshot(self.store,request,self.result(**changes),completed_at=100,current=True)
            self.assertEqual(arm["layers"],{})
            self.assertEqual(arm["observations"],["book"])

    def test_timing_does_not_invent_live_delay_from_unmapped_source(self):
        request=self.request()
        result=self.result(timings_ms={'inference':51.2,'preprocess':2,'postprocess':-1,'upload':9})
        arm=arm_snapshot(self.store,request,result,completed_at=100.5,current=True)
        self.assertEqual(arm['timing']['source_to_controller_ms'],500)
        self.assertEqual(arm['timing']['stages_ms'],{'inference':51.2,'preprocess':2})
        self.assertIsNone(arm['timing']['source_clock_uncertainty_ms'])
        with self.store.transaction() as db:
            db.execute("UPDATE media SET metadata=json_set(metadata,'$.uncertainty_seconds',0.45) WHERE id=?",(self.media['id'],))
        arm=arm_snapshot(self.store,request,result,completed_at=100.5,current=True)
        self.assertEqual(arm['timing']['source_clock_uncertainty_ms'],450)
        with self.store.transaction() as db:
            db.execute("UPDATE media SET metadata=json_set(metadata,'$.clock_mapping_status','unverified') WHERE id=?",(self.media['id'],))
        arm=arm_snapshot(self.store,request,result,completed_at=100.5,current=True)
        self.assertIsNone(arm['timing']['source_to_controller_ms'])
        self.assertTrue(arm['layers']['boxes']['available'])

    def test_pose_raw_score_and_instance_survive_without_fake_confidence(self):
        from rtmpose_worker import normalize_pose
        request=self.request()
        crops=[{"id":"person1","xyxy":[0,0,100,100]}]
        result=normalize_pose([[[20,30]]*17],[[1.7]*17],crops,frame_id="frame1",width=640,height=360,threshold=.4)
        arm=arm_snapshot(self.store,request,result,completed_at=100,current=True)
        self.assertTrue(arm["layers"]["skeleton"]["available"])
        self.assertEqual(arm["keypoints"][0]["score"],1.7)
        self.assertEqual(arm["keypoints"][0]["instance_id"],"person1")
        self.assertNotIn("confidence",arm["keypoints"][0])

    def person_result(self):
        from rtmpose_worker import normalize_pose
        crops=[{"id":"person1","xyxy":[0,0,100,100]},
               {"id":"person2","xyxy":[200,0,300,100]}]
        points=[[[20,30] for _ in range(17)],[[220,30] for _ in range(17)]]
        points[1][10]=[-1,30]
        scores=[[1.7]*17,[1.2]*17]
        scores[0][9]=.1
        result=normalize_pose(points,scores,crops,frame_id="frame1",width=640,height=360,threshold=.4)
        result["boxes"]=[{"x":0,"y":0,"w":100,"h":100,"label":"person","instance_id":"person1","confidence":.8},
                         {"x":200,"y":0,"w":100,"h":100,"label":"person","instance_id":"person2","confidence":.7}]
        return result

    def test_each_frame_person_keeps_its_box_and_every_joint_missingness_slot(self):
        request=self.request()
        arm=arm_snapshot(self.store,request,self.person_result(),completed_at=100,current=True)
        detail=arm["persons2d"]
        self.assertEqual((detail["schema"],detail["version"],detail["frame_id"],detail["coordinate_space"]),
                         ("home-lab-frame-persons-1",1,"frame1","source_pixels"))
        first,second=detail["persons"]
        self.assertEqual((first["instance_id"],second["instance_id"]),("person1","person2"))
        self.assertEqual(first["box"],{"x":0,"y":0,"w":100,"h":100,"confidence":.8})
        self.assertEqual((first["pose_status"],first["pose_reason"],first["layout"]),("available",None,"coco17"))
        self.assertEqual(len(first["joints"]),17)
        self.assertEqual(len(second["joints"]),17)
        self.assertEqual(first["joints"][9],{"joint_id":9,"name":"left_wrist","pixel":[20,30],
                         "score":.1,"status":"uncertain","reason":"low_response"})
        self.assertEqual(second["joints"][10]["reason"],"outside_frame")
        self.assertEqual(second["joints"][10]["pixel"],[-1,30])
        self.assertEqual(first["joints"][0]["score"],1.7)
        self.assertNotIn("confidence",first["joints"][0])
        self.assertEqual(first["score_semantics"],"SimCC_response_not_probability")

    def test_detected_people_without_supplied_pose_do_not_get_invented_joint_slots(self):
        request=self.request()
        result=self.person_result()
        del result["pose_instances"]
        people=arm_snapshot(self.store,request,result,completed_at=100,current=True)["persons2d"]["persons"]
        self.assertEqual([v["instance_id"] for v in people],["person1","person2"])
        for person in people:
            self.assertEqual((person["pose_status"],person["pose_reason"]),("unavailable","pose_not_supplied"))
            self.assertIsNone(person["layout"])
            self.assertIsNone(person["score_semantics"])
            self.assertEqual(person["joints"],[])

    def test_malformed_person_references_refuse_detail_without_breaking_legacy_overlay(self):
        request=self.request()
        for failure in ("duplicate_pose","duplicate_box","orphan_pose","wrong_crop","missing_slot","wrong_slot",
                        "nonfinite_response","supported_outside_frame","invalid_reason","pose_bound"):
            with self.subTest(failure=failure):
                result=self.person_result()
                if failure=="duplicate_pose":result["pose_instances"][1]["instance_id"]="person1"
                elif failure=="duplicate_box":result["boxes"][1]["instance_id"]="person1"
                elif failure=="orphan_pose":result["pose_instances"][1]["instance_id"]="other"
                elif failure=="wrong_crop":result["pose_instances"][0]["crop_xyxy"][2]=99
                elif failure=="missing_slot":result["pose_instances"][0]["joints"].pop()
                elif failure=="wrong_slot":result["pose_instances"][0]["joints"][0]["joint_id"]=1
                elif failure=="nonfinite_response":result["pose_instances"][0]["joints"][0]["score"]=float("nan")
                elif failure=="supported_outside_frame":result["pose_instances"][0]["joints"][0]["pixel"]=[-1,30]
                elif failure=="invalid_reason":result["pose_instances"][0]["joints"][0]["reason"]="low_response"
                elif failure=="pose_bound":result["pose_instances"]*=9
                arm=arm_snapshot(self.store,request,result,completed_at=100,current=True)
                self.assertNotIn("persons2d",arm)
                self.assertIn("invalid_persons2d",arm["validation"])
                self.assertTrue(arm["layers"]["skeleton"]["available"])

    def test_empty_people_and_unavailable_detail_are_distinct_and_bound_to_original_frame(self):
        request=self.request()
        empty=self.result(boxes=[],keypoints=[],skeleton_edges=[])
        self.assertEqual(arm_snapshot(self.store,request,empty,completed_at=100,current=True)["persons2d"]["persons"],[])
        unknown={key:empty[key] for key in ("schema_version","frame_id","coordinate_space")}
        self.assertNotIn("persons2d",arm_snapshot(self.store,request,unknown,completed_at=100,current=True))
        wrong=self.person_result()
        wrong["frame_id"]="different"
        arm=arm_snapshot(self.store,request,wrong,completed_at=100,current=True)
        self.assertNotIn("persons2d",arm)
        self.assertIn("overlay_source_frame_not_verified",arm["validation"])

    def test_malformed_worker_primitives_are_not_rendered(self):
        request=self.request()
        arm=arm_snapshot(self.store,request,self.result(boxes=[{"x":10,"y":20,"w":900,"h":1}],masks=[None],tracks=[{}]),completed_at=100,current=True)
        self.assertEqual(set(arm["validation"]),{"invalid_boxes","invalid_masks","invalid_tracks"})
        self.assertNotIn("boxes",arm)

    def wholebody(self):
        from rtmw3d_worker import RTMW3DWorker
        worker=object.__new__(RTMW3DWorker)
        scores=[1.7]*133
        scores[9]=.1
        return worker.normalize(([[[20,30,1]]*133],[scores],None,[[[20,30]]*133]),
            [{"id":"person1","xyxy":[0,0,100,100]}],frame_id="frame1",width=640,height=360,threshold=.5)

    def test_wholebody_summary_retains_uncertainty_and_depth_limits(self):
        request=self.request()
        result=self.wholebody()
        arm=arm_snapshot(self.store,request,result,completed_at=100,current=True)
        summary=arm["pose_summary"][0]
        self.assertEqual(summary["supported_joints"],132)
        self.assertEqual(summary["joint_count"],133)
        self.assertIn("not stereo",summary["depth"])
        self.assertIn("hand and face edges not drawn",summary["overlay"])
        detail=arm["persons2d"]["persons"][0]
        self.assertEqual((detail["layout"],len(detail["joints"])),("coco_wholebody133",133))
        self.assertEqual(detail["joints"][9]["status"],"uncertain")
        self.assertNotIn("reason",detail["joints"][9])
        self.assertNotIn("relative_z_model_units",detail["joints"][0])
        result["pose_instances"][0]["joints"][0]["joint_id"]=1
        invalid=arm_snapshot(self.store,request,result,completed_at=100,current=True)
        self.assertNotIn("pose_summary",invalid)
        self.assertIn("invalid_pose_summary",invalid["validation"])
        self.assertTrue(invalid["layers"]["skeleton"]["available"])

    def test_pose_summary_reaches_live_and_saved_replay_snapshot(self):
        request=self.request()
        runner=self.runner(lambda req:self.receipt(req,result=self.wholebody()))
        runtime=ServiceRuntime(self.store,{},lambda *_:None,None)
        runtime.runners[self.session["id"]]=runner
        runner.offer(request["frame"])
        self.settle(lambda:all(v is None for v in runner.snapshot()["active"].values()))
        live=runtime.live(self.session)["arms"]["A"]["pose_summary"]
        outputs=[e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.output"]
        self.assertEqual(outputs[0]["arm_snapshot"]["pose_summary"],live)
        self.assertEqual(live[0]["layout"],"coco_wholebody133")

    def test_all_person_detail_reaches_live_and_saved_snapshot_without_source_mutation(self):
        request=self.request()
        result=self.person_result()
        runner=self.runner(lambda req:self.receipt(req,result=result))
        runtime=ServiceRuntime(self.store,{},lambda *_:None,None)
        runtime.runners[self.session["id"]]=runner
        runner.offer(request["frame"])
        self.settle(lambda:all(v is None for v in runner.snapshot()["active"].values()))
        live=runtime.live(self.session)["arms"]["B"]["persons2d"]
        outputs=[e["payload"] for e in self.store.list_events(self.session["id"])
                 if e["kind"]=="perception.output" and e["payload"]["arm"]=="b"]
        self.assertEqual(len(outputs),1)
        self.assertEqual(outputs[0]["arm_snapshot"]["persons2d"],live)
        self.assertEqual(len(live["persons"]),2)
        self.assertEqual(result["pose_instances"][0]["joints"][9]["reason"],"low_response")

    def test_paired_result_reaches_live_and_replay_with_expiry(self):
        request=self.request()
        runner=self.runner(lambda req:self.receipt(req,result=self.result()))
        runtime=ServiceRuntime(self.store,{},lambda *_:None,None)
        runtime.runners[self.session["id"]]=runner
        runner.offer(request["frame"])
        self.settle(lambda:all(v is None for v in runner.snapshot()["active"].values()))
        live=runtime.live(self.session)
        self.assertEqual(set(live["arms"]),{"A","B"})
        self.assertTrue(live["arms"]["A"]["layers"]["boxes"]["available"])
        outputs=[e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.output"]
        self.assertEqual(outputs[0]["source_end_s"],0)
        self.assertEqual(outputs[0]["arm_snapshot"]["frame"]["media_id"],self.media["id"])
        self.store.clock=lambda:102
        self.assertEqual(runtime.live(self.session)["arms"]["A"]["status"],"stale")


if __name__=="__main__":unittest.main()
