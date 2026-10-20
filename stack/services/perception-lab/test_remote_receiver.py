import base64
import hashlib
import io
import unittest
import numpy as np
from PIL import Image
import test_paired_runtime as fixtures
import test_keypoint_worker as keypoint
from remote_receiver import RemoteReceiver
from reasoning import digest


class ReceiverTests(unittest.TestCase):
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def composite(self,fail=False):
        from staged_specialist import DetectorPoseWorker
        from rtmpose_worker import RTMPoseWorker
        from test_rtmpose_worker import PoseTests
        from test_yolox_worker import YoloXWorkerTests
        detector=YoloXWorkerTests();detector.setUp()
        detector.worker.model=detector.detector
        pose=RTMPoseWorker(PoseTests().manifest(),lambda *_:True)
        calls=[]
        def model(image,bboxes):
            calls.append(bboxes)
            if fail:raise TimeoutError('uncertain pose')
            return [[[20,30]]*17 for _ in bboxes],[[.8]*17 for _ in bboxes]
        pose.model=model
        chain=DetectorPoseWorker(self.store,detector.worker,pose,
            profile_id='remote-chain',engine_epoch='chain1',authorize=lambda *_:True,model_version='detector-pose-v1',
            minimum_confidence=None,max_people=4,policy_revision='fixture',reservation_kind='remote',runtime_revision='fixture-v2')
        profile={**chain.manifest,'artifact_sha256':chain.manifest['sha256']}
        self.envelope['profile']=profile
        self.envelope['body']['request'].update(profile=profile,arm='a',generation=1)
        self.envelope['body_sha256']=digest(self.envelope['body'])
        return RemoteReceiver(self.store,chain,profile,lambda *_:True),calls

    def test_expiry_after_completed_detector_is_terminal_without_dispatching_pose(self):
        receiver,calls=self.composite()
        original=receiver.worker.detector.execute
        def slow_detector(*args):
            receipt=original(*args)
            self.store.clock=lambda:self.envelope['body']['request']['frame']['valid_until']+1
            return receipt
        receiver.worker.detector.execute=slow_detector
        proof=receiver.execute(self.envelope,source_site='victoria')
        self.assertEqual(proof['state'],'cancelled')
        self.assertEqual(calls,[])
        self.assertEqual(self.store.db.execute('SELECT state FROM remote_execution').fetchone()[0],'terminal')
        self.assertEqual(receiver.execute(self.envelope,source_site='victoria'),proof)

    def test_expired_in_transit_has_durable_terminal_receipt_without_model_call(self):
        receiver,calls=self.composite()
        self.store.clock=lambda:self.envelope['body']['request']['frame']['valid_until']+1
        def forbidden(*args):
            self.fail('expired input must not invoke either model')
        receiver.worker.execute=forbidden
        proof=receiver.execute(self.envelope,source_site='victoria')
        self.assertEqual(proof['state'],'cancelled')
        self.assertEqual(proof['result']['reason'],'source_expired_before_execution')
        self.assertEqual(calls,[])
        self.assertEqual(receiver.execute(self.envelope,source_site='victoria'),proof)
        reference={k:v for k,v in self.envelope.items() if k!='body'}
        self.assertEqual(receiver.lookup(reference,source_site='victoria'),proof)

    def test_crowd_budget_rejects_frame_without_poisoning_worker(self):
        from copy import deepcopy
        receiver,calls=self.composite()
        original=receiver.worker.detector.execute
        def crowded(*args):
            receipt=original(*args)
            receipt['result']['boxes']=[deepcopy(receipt['result']['boxes'][0]) for _ in range(5)]
            return receipt
        receiver.worker.detector.execute=crowded
        proof=receiver.execute(self.envelope,source_site='victoria')
        self.assertEqual(proof['state'],'rejected')
        self.assertEqual(proof['result']['reason'],'pose_crowd_budget_exceeded')
        self.assertEqual(calls,[])
        self.assertEqual(receiver.execute(self.envelope,source_site='victoria'),proof)
        receiver.worker.detector.execute=original
        self.envelope['request_id']='next-request'
        self.envelope['body']['request']['request_id']='next-request'
        self.envelope['body_sha256']=digest(self.envelope['body'])
        self.assertEqual(receiver.execute(self.envelope,source_site='victoria')['state'],'completed')
        self.assertEqual(len(calls),1)

    def test_remote_composite_without_copying_household_session_database(self):
        receiver,calls=self.composite()
        first=receiver.execute(self.envelope,source_site='victoria')
        self.assertEqual(first['state'],'completed')
        self.assertNotIn('timings_ms',first['result'])
        self.assertIn('pose',first['result']['stage_timings_ms'])
        self.assertEqual(len(first['result']['pose_instances'][0]['joints']),17)
        self.assertEqual(first['result']['boxes'][0]['instance_id'],first['result']['pose_instances'][0]['instance_id'])
        self.assertEqual(receiver.execute(self.envelope,source_site='victoria'),first)
        self.assertEqual(len(calls),1)
        self.assertIsNone(self.store.db.execute("SELECT id FROM sessions WHERE id='source-session'").fetchone())
        payload=self.store.db.execute('SELECT payload FROM remote_inputs').fetchone()[0]
        self.assertNotIn('data_base64',payload)

    def test_remote_composite_uncertain_pose_cannot_repeat_detector_or_pose(self):
        receiver,calls=self.composite(fail=True)
        with self.assertRaises(TimeoutError):receiver.execute(self.envelope,source_site='victoria')
        with self.assertRaisesRegex(ValueError,'indeterminate'):
            receiver.execute(self.envelope,source_site='victoria')
        self.assertEqual(len(calls),1)

    def test_remote_composite_rejects_unreserved_direct_execution(self):
        receiver,calls=self.composite()
        with self.assertRaisesRegex(ValueError,'reservation'):
            receiver.worker.execute(self.envelope['body']['request'],lambda _:None)
        self.assertEqual(calls,[])

    def test_paired_sender_delivers_composite_skeletons_to_both_arms(self):
        from remote_specialist import RemoteSpecialist
        from runtime import PairedRunner
        receiver,calls=self.composite()
        adapter=RemoteSpecialist(self.store,receiver.profile,
            lambda envelope:receiver.execute(envelope,source_site='victoria'),lambda *_:True)
        frame={**self.envelope['body']['request']['frame'],'session_id':self.session['id'],
               'observed_at':99,'available_at':99}
        data=base64.b64decode(self.envelope['body']['image']['data_base64'])
        media=self.store.add_media(self.session['id'],data,'image/png',{
            **frame,'width':100,'height':100,'stream':'rgb','session_time_s':0})
        frame['media_ids']=[media['id']]
        runner=PairedRunner(self.store,self.session['id'],{'a':receiver.profile,'b':receiver.profile},adapter,clock=self.store.clock)
        self.runners.append(runner);runner.offer(frame)
        fixtures.PairedRuntimeTests.settle(self,lambda:len(calls)==2 and all(v is None for v in runner.snapshot()['active'].values()))
        outputs=[e['payload'] for e in self.store.list_events(self.session['id']) if e['kind']=='perception.output']
        self.assertEqual({o['arm'] for o in outputs},{'a','b'})
        self.assertTrue(all(len(o['result']['keypoints'])==17 for o in outputs))

    def setUp(self):
        fixtures.PairedRuntimeTests.setUp(self)
        self.model=keypoint.KeypointWorkerTests();self.model.setUp()
        self.model.worker.model=object()  # Never loads Torch or executes a model.
        self.profile={**self.model.manifest,'artifact_sha256':self.model.manifest['sha256']}
        self.receiver=RemoteReceiver(self.store,self.model.worker,self.profile,lambda *_:True)
        frame={'site_id':'victoria','camera_id':'den','session_id':'source-session',
               'frame_id':'f1','capture_epoch':'c1','valid_until':110,'media_ids':['m1']}
        pixels=np.zeros((100,100,3),dtype=np.uint8)
        buffer=io.BytesIO();Image.fromarray(pixels).save(buffer,format='PNG');data=buffer.getvalue()
        body={'schema_version':1,'request':{'request_id':'request1','profile':self.profile,'frame':frame},
              'image':{'encoding':'png','channel_order':'RGB','decoded_worker_order':'BGR',
                  'width':100,'height':100,'source_media_id':'m1','encoded_sha256':hashlib.sha256(data).hexdigest(),
                  'bgr_sha256':hashlib.sha256(pixels.tobytes()).hexdigest(),'data_base64':base64.b64encode(data).decode()}}
        self.envelope={'request_id':'request1','site_id':'victoria','camera_id':'den','session_id':'source-session',
                       'profile':self.profile,'body_sha256':digest(body),'body':body}

    def test_exact_pixels_complete_once_and_lookup_survives_receiver_recreation(self):
        first=self.receiver.execute(self.envelope,source_site='victoria')
        self.assertEqual(len(self.model.calls),1)
        self.assertEqual(self.receiver.execute(self.envelope,source_site='victoria'),first)
        restored=RemoteReceiver(self.store,self.model.worker,self.profile,lambda *_:True)
        reference={k:v for k,v in self.envelope.items() if k!='body'}
        self.assertEqual(restored.lookup(reference,source_site='victoria'),first)
        self.assertEqual(len(self.model.calls),1)

    def test_readiness_lookup_miss_is_distinct_only_for_the_exact_resident_profile(self):
        from remote_receiver import ExecutionNotFound
        reference={'site_id':'victoria','camera_id':'den','session_id':'readiness','request_id':'readiness',
                   'body_sha256':'0'*64,'profile':self.profile}
        with self.assertRaises(ExecutionNotFound):self.receiver.lookup(reference,source_site='victoria')
        for changed in ({'profile':{**self.profile,'engine_epoch':'other'}},{'camera_id':'bedroom'}):
            with self.assertRaises(ValueError) as caught:
                RemoteReceiver(self.store,self.model.worker,self.profile,
                    lambda phase,value:value['camera_id']=='den').lookup({**reference,**changed},source_site='victoria')
            self.assertNotIsInstance(caught.exception,ExecutionNotFound)
        def uncertain(*args):raise TimeoutError('fixture')
        self.model.worker.invoke=uncertain
        with self.assertRaises(TimeoutError):self.receiver.execute(self.envelope,source_site='victoria')
        pending={k:v for k,v in self.envelope.items() if k!='body'}
        with self.assertRaises(ValueError) as caught:self.receiver.lookup(pending,source_site='victoria')
        self.assertNotIsInstance(caught.exception,ExecutionNotFound)
        self.assertEqual(self.model.calls,[])

    def test_wrong_site_digest_and_pixels_do_not_reach_worker(self):
        with self.assertRaises(ValueError): self.receiver.execute(self.envelope,source_site='echo')
        with self.assertRaises(ValueError): self.receiver.execute({**self.envelope,'body_sha256':'0'*64},source_site='victoria')
        self.envelope['body']['image']['bgr_sha256']='0'*64
        self.envelope['body_sha256']=digest(self.envelope['body'])
        with self.assertRaises(ValueError): self.receiver.execute(self.envelope,source_site='victoria')
        self.assertEqual(self.model.calls,[])

    def test_uncertain_worker_never_reexecutes_after_restart(self):
        calls=[]
        def uncertain(*args): calls.append(1);raise TimeoutError('fixture')
        self.model.worker.invoke=uncertain
        with self.assertRaises(TimeoutError): self.receiver.execute(self.envelope,source_site='victoria')
        restored=RemoteReceiver(self.store,self.model.worker,self.profile,lambda *_:True)
        with self.assertRaisesRegex(ValueError,'indeterminate'):
            restored.execute(self.envelope,source_site='victoria')
        self.assertEqual(calls,[1])

    def test_sender_receiver_worker_and_paired_output_integrate(self):
        from remote_specialist import RemoteSpecialist
        from specialist_registry import RegisteredSpecialists
        from runtime import PairedRunner
        self.model.worker.manifest['runtime_revision']='fixture-v1'
        profile={**self.model.worker.manifest,'artifact_sha256':self.model.manifest['sha256']}
        receiver=RemoteReceiver(self.store,self.model.worker,profile,lambda *_:True)
        adapter=RemoteSpecialist(self.store,profile,
            lambda envelope:receiver.execute(envelope,source_site='victoria'),lambda *_:True)
        frame={**self.envelope['body']['request']['frame'],'session_id':self.session['id'],
               'observed_at':99,'available_at':99}
        data=base64.b64decode(self.envelope['body']['image']['data_base64'])
        media=self.store.add_media(self.session['id'],data,'image/png',{
            **frame,'width':100,'height':100,'stream':'rgb','session_time_s':0})
        frame['media_ids']=[media['id']]
        runner=PairedRunner(self.store,self.session['id'],{'a':profile,'b':profile},
            RegisteredSpecialists(self.store,[adapter]),clock=self.store.clock)
        self.runners.append(runner);runner.offer(frame)
        fixtures.PairedRuntimeTests.settle(self,lambda:len(self.model.calls)==2 and all(v is None for v in runner.snapshot()['active'].values()))
        outputs=[e for e in self.store.list_events(self.session['id']) if e['kind']=='perception.output']
        self.assertEqual({e['payload']['arm'] for e in outputs},{'a','b'})
        self.assertTrue(all(e['payload']['status']=='completed' for e in outputs))

    def test_local_and_remote_pose_use_identical_pixels_and_person_crops(self):
        from rtmpose_worker import RTMPoseWorker
        from test_rtmpose_worker import PoseTests
        from remote_specialist import RemoteSpecialist
        from specialist_registry import RegisteredSpecialists
        from runtime import PairedRunner
        calls=[];workers=[];profiles={}
        for arm in ('a','b'):
            manifest={**PoseTests().manifest(),'profile_id':'pose-'+arm,'device_id':arm,'runtime_revision':'fixture'}
            worker=RTMPoseWorker(manifest,lambda *_:True)
            def model(image,bboxes,arm=arm):
                calls.append((arm,image.copy(),bboxes))
                return [[[20,30]]*17 for _ in bboxes],[[.8]*17 for _ in bboxes]
            worker.model=model
            workers.append(worker);profiles[arm]={**manifest,'artifact_sha256':manifest['sha256']}
        frame={**self.envelope['body']['request']['frame'],'session_id':self.session['id'],
               'observed_at':99,'available_at':99}
        data=base64.b64decode(self.envelope['body']['image']['data_base64'])
        media=self.store.add_media(self.session['id'],data,'image/png',{
            **frame,'width':100,'height':100,'stream':'rgb','session_time_s':0})
        frame['media_ids']=[media['id']]
        from shared_detector import SharedDetectorCrops
        import test_yolox_worker as detector_fixtures
        detector=detector_fixtures.YoloXWorkerTests();detector.setUp()
        detector.worker.model=detector.detector
        loader=SharedDetectorCrops(self.store,detector.worker,lambda *_:True,
            minimum_confidence=None,max_people=4,policy_revision='fixture-crops-v1')
        receiver=RemoteReceiver(self.store,workers[1],profiles['b'],lambda *_:True)
        remote=RemoteSpecialist(self.store,profiles['b'],
            lambda envelope:receiver.execute(envelope,source_site='victoria'),lambda *_:True,crop_loader=loader)
        runner=PairedRunner(self.store,self.session['id'],profiles,
            RegisteredSpecialists(self.store,[workers[0],remote],crop_loaders={'pose-a':loader}),clock=self.store.clock)
        self.runners.append(runner);runner.offer(frame)
        fixtures.PairedRuntimeTests.settle(self,lambda:len(calls)==2 and all(v is None for v in runner.snapshot()['active'].values()))
        np.testing.assert_array_equal(calls[0][1],calls[1][1])
        self.assertEqual(calls[0][2],calls[1][2])
        outputs=[e['payload']['result'] for e in self.store.list_events(self.session['id']) if e['kind']=='perception.output']
        self.assertEqual(len(outputs),2)
        self.assertEqual(outputs[0]['processing']['crop_set_digest'],outputs[1]['processing']['crop_set_digest'])
        self.assertEqual(len(detector.calls),1)
        self.assertEqual(len([e for e in self.store.list_events(self.session['id']) if e['kind']=='perception.shared_detector']),1)

    def test_remote_crop_contract_rejects_wrong_frame_or_label_fields(self):
        from remote_crops import pose_bundle
        from test_rtmpose_worker import PoseTests
        frame=self.envelope['body']['request']['frame']
        crops=PoseTests().crops()
        bundle={**{k:frame[k] for k in ('site_id','session_id','camera_id','capture_epoch','frame_id')},
                'crops':crops,'crop_set_digest':digest(crops)}
        with self.assertRaises(ValueError):pose_bundle({**bundle,'frame_id':'other'},frame,4)
        crops[0]['human_label']='reading'
        with self.assertRaises(ValueError):pose_bundle({**bundle,'crop_set_digest':digest(crops)},frame,4)
        empty={**bundle,'crops':[],'crop_set_digest':digest([])}
        self.assertEqual(pose_bundle(empty,frame,4)['crops'],[])


if __name__=='__main__':unittest.main()
