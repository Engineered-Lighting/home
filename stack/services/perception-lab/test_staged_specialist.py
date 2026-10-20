import io
import json
import unittest
from PIL import Image
import test_paired_runtime as fixtures
import test_yolox_worker as detector_fixtures
import test_rtmpose_worker as pose_fixtures
from rtmpose_worker import RTMPoseWorker
from staged_specialist import DetectorPoseWorker
from specialist_registry import RegisteredSpecialists
from runtime import PairedRunner


class StagedTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown
    settle=fixtures.PairedRuntimeTests.settle

    def setup_chain(self,*,fail_pose=False,empty=False,model_version='detector-pose-v1'):
        detector=detector_fixtures.YoloXWorkerTests(); detector.setUp()
        detector.worker.model=detector.detector
        if empty:
            class Empty:
                COCO_CLASSES=detector.detector.COCO_CLASSES
                def __call__(self,image): return [],[]
            detector.worker.model=Empty()
        pose=RTMPoseWorker(pose_fixtures.PoseTests().manifest(),lambda *_:True)
        self.pose_calls=[]
        def model(image,bboxes):
            self.pose_calls.append(bboxes)
            if fail_pose: raise RuntimeError('fixture failure after dispatch')
            return [[[20,30]]*17 for _ in bboxes],[[.8]*17 for _ in bboxes]
        pose.model=model
        chain=DetectorPoseWorker(self.store,detector.worker,pose,profile_id='chain',engine_epoch='chain1',
            authorize=lambda *_:True,model_version=model_version,minimum_confidence=None,max_people=4,policy_revision='fixture-v1')
        profile={**chain.manifest,'artifact_sha256':chain.manifest['sha256']}
        frame={'site_id':'victoria','session_id':self.session['id'],'camera_id':'den','capture_epoch':'c1',
            'frame_id':'f1','observed_at':99,'available_at':99,'valid_until':110}
        data=io.BytesIO(); Image.new('RGB',(100,100),(255,128,0)).save(data,format='PNG')
        media=self.store.add_media(self.session['id'],data.getvalue(),'image/png',{
            **frame,'width':100,'height':100,'stream':'rgb','session_time_s':0})
        frame['media_ids']=[media['id']]
        executor=RegisteredSpecialists(self.store,[chain])
        runner=PairedRunner(self.store,self.session['id'],{'a':profile,'b':profile},executor,clock=self.store.clock)
        self.runners.append(runner)
        return chain,runner,frame,executor

    def test_both_arms_complete_ordered_stages_from_the_same_frame(self):
        chain,runner,frame,_=self.setup_chain()
        runner.offer(frame)
        self.settle(lambda:len(self.pose_calls)==2 and all(x is None for x in runner.snapshot()['active'].values()))
        events=self.store.list_events(self.session['id'])
        stages=[e['payload'] for e in events if e['kind']=='perception.stage']
        self.assertEqual(len(stages),4)
        for arm in ('a','b'):
            self.assertEqual([e['stage'] for e in stages if e['arm']==arm],['detector','pose'])
        outputs=[e['payload'] for e in events if e['kind']=='perception.output']
        self.assertEqual(len(outputs),2)
        self.assertTrue(all(e['frame_id']=='f1' and len(e['result']['keypoints'])==17 for e in outputs))
        self.assertEqual(self.pose_calls,[[[1,2,90,95]],[[1,2,90,95]]])
        self.assertNotEqual(outputs[0]['result']['pose_instances'][0]['instance_id'],outputs[1]['result']['pose_instances'][0]['instance_id'])
        self.assertTrue(all(e['result']['processing']['correlated_evidence'] for e in outputs))
        for event in outputs:
            result=event['result']
            self.assertEqual(result['boxes'][0]['instance_id'],result['pose_instances'][0]['instance_id'])
            self.assertEqual(result['objects'][0]['instance_id'],result['pose_instances'][0]['instance_id'])

    def test_completed_result_carries_the_manifest_identity_beside_the_pipeline_label(self):
        chain,runner,frame,_=self.setup_chain(model_version='fixture-composite-v2')
        runner.offer(frame)
        self.settle(lambda:len(self.pose_calls)==2 and all(x is None for x in runner.snapshot()['active'].values()))
        outputs=[e['payload'] for e in self.store.list_events(self.session['id']) if e['kind']=='perception.output']
        self.assertEqual(len(outputs),2)
        for event in outputs:
            processing=event['result']['processing']
            # The structural label stays for existing column filters; it is not the revision.
            self.assertEqual((processing['pipeline'],processing['model_version'],processing['pose_preprocessing_revision'],
                processing['manifest_sha256']),('detector-pose-v1','fixture-composite-v2','body7-rgb-v1',chain.manifest['sha256']))
            self.assertEqual(processing['pose']['preprocessing_revision'],processing['pose_preprocessing_revision'])

    def test_empty_detection_never_poses_the_full_frame(self):
        _,runner,frame,_=self.setup_chain(empty=True)
        runner.offer(frame)
        self.settle(lambda:all(x is None for x in runner.snapshot()['active'].values()))
        outputs=[e['payload'] for e in self.store.list_events(self.session['id']) if e['kind']=='perception.output']
        self.assertEqual(len(outputs),2)
        self.assertEqual(self.pose_calls,[])
        self.assertTrue(all(e['result']['keypoints']==[] for e in outputs))

    def test_pose_failure_retains_parent_and_does_not_replay_detection(self):
        _,runner,frame,executor=self.setup_chain(fail_pose=True)
        runner.offer(frame)
        def failed():
            with self.store.lock:
                return self.store.db.execute("SELECT count(*) FROM paired_requests WHERE state='indeterminate'").fetchone()[0]==2
        self.settle(failed)
        self.assertTrue(all(runner.snapshot()['active'].values()))
        self.assertFalse(any(e['kind']=='perception.output' for e in self.store.list_events(self.session['id'])))
        with self.store.lock:
            request=json.loads(self.store.db.execute('SELECT payload FROM paired_requests LIMIT 1').fetchone()[0])
        with self.assertRaisesRegex(ValueError,'reservation'): executor(request)
        runner.offer(frame)
        self.assertEqual(len(self.pose_calls),2)


class CompositeIdentityTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown
    # LA arm inputs as deployed by pose release c8f8dae29a963bac: the stage from
    # tools/la-detector-trial/live-binding.template.json and the pose manifest from
    # docs/receipts/perception-lab-pose-2026-09-27/artifact.json with the release
    # script's max_people/device_id/engine_epoch overrides.
    DETECTOR={'runtime_version':'8.4.0','task':'detect','checkpoint':'yolo26s','precision':'fp32',
        'sha256':'646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b','license':'AGPL-3.0',
        'device_id':'echo-la-rtx-pro-6000','engine_epoch':'REQUIRES_COORDINATED_EPOCH','profile_id':'yolo26s-fp32-paired-v1',
        'model_version':'ultralytics-assets-v8.4.0','device':'cuda:0','format':'pt',
        'runtime_revision':'la-live-0c41bff61ca9bb8e9c7348e7fcfc699d5b4519b89feb92a8895d7699afbce8fc',
        'preprocessing':{'imgsz':[640,640],'confidence':.25,'iou':.7,'max_det':128,'retina_masks':False}}
    POSE={'schema_version':1,'checkpoint':'rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504',
        'sha256':'5c0a4bf67953e6d2ac43ce15e77dc9d5d354ae18430a47d2c5963a7bc5683e3c','runtime_version':'0.0.16',
        'onnxruntime_version':'1.23.2','source_sha256':{
            'tools/base.py':'0d287088838eee78cc8cb5ef722a53148f7c100bf73eed16457ce3f9f40b272a',
            'tools/pose_estimation/rtmpose.py':'98b37228b3ff2a705d856e139486992d2c1f2124d805cf5db5a97bf295ed4e97',
            'tools/pose_estimation/pre_processings.py':'2bd84ad2300c3bb43a831e18aff30588c6f8e570d41d492826cf540cfe8c0fcb',
            'tools/pose_estimation/post_processings.py':'6c372b34c0021a808c28cbc49206b1908e4cf9c820e859fde18c22a5362bcf05'},
        'wheel_sha256':'ba932b25f7d264d7ec4837545c5e7c392001f48b53e22e514cb1497882389eaa','input_size':[192,256],
        'precision':'fp32','format':'onnx','layout':'coco17','device':'cpu',
        'license':'RTMLib Apache-2.0; upstream model redistribution terms require separate review',
        'model_version':'body7-20230504-onnx','profile_id':'rtmpose-m-body7-cpu-fp32-v1',
        'device_id':'echo-la-rtx-pro-6000-cpu','engine_epoch':'REQUIRES_COORDINATED_EPOCH','score_threshold':.4,'max_people':2}
    RUNTIME='fc51bd30e3052cb8889a89134f51427e747118f9b7007bc45a88c52d985bc779'
    # Recomputed with the unmodified staged_specialist/pose_composition of releases
    # c8f8dae29a963bac and 565cebe6c5f854e3 from the inputs above.
    DEPLOYED='ffbca241382258c0fbf85d81d16802981732817debcc1c5d658a8447aa8a92a0'

    def test_deployed_rtmpose_composite_digest_is_byte_identical(self):
        from unittest.mock import patch
        import pose_composition
        config={'root':'/home/marcelo-lima/home-lab-yolo26-trial/pose-preparation-20260927',
                'manifest':self.POSE,'runtime_revision':self.RUNTIME}
        # Only the host path check is bypassed; it cannot resolve on a workstation.
        with patch.object(pose_composition,'validate_pose',side_effect=lambda value:value):
            value=pose_composition.profile(self.DETECTOR,config)
        self.assertEqual((value['sha256'],value['artifact_sha256']),(self.DEPLOYED,self.DEPLOYED))
        self.assertEqual((value['model_version'],value['profile_id'],value['device_id']),
            ('detector-pose-v1','yolo26s-rtmpose-cpu-v1','echo-la-rtx-pro-6000+echo-la-rtx-pro-6000-cpu'))

    def test_pose_worker_class_names_the_bound_preprocessing(self):
        from staged_specialist import composite_manifest
        from rtmw3d_worker import RTMW3DWorker
        from test_rtmw3d_worker import RTMWTests
        from pose_composition import POLICY
        store=self.store
        detector=detector_fixtures.YoloXWorkerTests();detector.setUp()
        pose=RTMW3DWorker(RTMWTests().manifest(),lambda *_:True)
        chain=DetectorPoseWorker(store,detector.worker,pose,profile_id='humanart-rtmw3d',engine_epoch='e1',
            authorize=lambda *_:True,model_version='humanart-rtmw3d-v1',minimum_confidence=None,max_people=2,policy_revision='p1')
        self.assertEqual(chain.manifest['model_version'],'humanart-rtmw3d-v1')
        options=dict(profile_id='humanart-rtmw3d',engine_epoch='e1',policy=chain.policy,model_version='humanart-rtmw3d-v1')
        self.assertEqual(chain.manifest,composite_manifest(*chain.stages,pose_preprocessing_revision='rtmw3d-bgr-v1',**options))
        self.assertNotEqual(chain.manifest['sha256'],composite_manifest(*chain.stages,pose_preprocessing_revision='body7-rgb-v1',**options)['sha256'])
        body7=RTMPoseWorker(pose_fixtures.PoseTests().manifest(),lambda *_:True)
        self.assertEqual(DetectorPoseWorker(store,detector.worker,body7,profile_id='p',engine_epoch='e1',authorize=lambda *_:True,
            model_version='detector-pose-v1',minimum_confidence=None,max_people=2,policy_revision=POLICY['revision']).identity,
            {'model_version':'detector-pose-v1','pose_preprocessing_revision':'body7-rgb-v1'})
        with self.assertRaises(TypeError):
            composite_manifest(*chain.stages,profile_id='p',engine_epoch='e1',policy=chain.policy)


if __name__=='__main__': unittest.main()
