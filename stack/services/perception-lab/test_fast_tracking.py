import base64
import asyncio
from copy import deepcopy
import hashlib
import io
from contextlib import redirect_stdout
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image

from fast_tracking_camera import FastCamera,encoded,sha
from fast_tracking_runtime import TrackingPump,TrackingHTTP
from reasoning import digest
from fast_tracking_depth import DepthProjector


class JointObservationTests(unittest.TestCase):
    def setUp(self):
        matrix=np.eye(3).tolist();intrinsics=[[500,0,320],[0,500,180],[0,0,1]]
        metadata={'frames':{name:dict(width=640,height=height,intrinsics=intrinsics,
            source_distortion_coefficients=[0]*8,transformation_valid=True)
            for name,height in [('rgb',360),('depth',400)]}}
        depth=np.full((400,640),2000,dtype=np.uint16)
        depth[170:191,310:331]=0  # missing stereo support at a confidently detected nose
        self.value=dict(depth=depth,metadata=metadata,identity={'calibration_content_id':'c'*64},
            calibration={'eeprom':{'cameraData':[[0,{}]],'stereoRectificationData':{
                'leftCameraSocket':0,'rectifiedRotationLeft':matrix}}})
        joints=[dict(joint_id=i,pixel=None,score=.1,status='uncertain',reason='low_response') for i in range(17)]
        for i,pixel in [(0,[320,180]),(5,[300,240]),(6,[340,240])]:
            joints[i].update(pixel=pixel,score=1.7,status='supported',reason=None)
        joints[9].update(pixel=[700,380],score=1.7,reason='outside_frame')
        self.pose=dict(instance_id='frame-local-person',layout='coco17',joints=joints)

    def test_exact_joint_reasons_distinguish_image_loss_from_depth_loss(self):
        result=DepthProjector().project(self.value,[self.pose])[0]
        self.assertEqual({j['id'] for j in result['joints']},{5,6})
        evidence=result['joint_observations']
        self.assertEqual([j['id'] for j in evidence],list(range(17)))
        self.assertEqual(evidence[0]['image_status'],'supported')
        self.assertEqual(evidence[0]['response'],1.7)
        self.assertEqual(evidence[0]['response_semantics'],'SimCC_response_not_probability')
        self.assertEqual(evidence[0]['depth_status'],'unknown')
        self.assertEqual(evidence[0]['depth_reason'],'no_depth_support')
        self.assertIsNone(evidence[0]['camera_xyz_m'])
        self.assertEqual(evidence[5]['depth_status'],'measured')
        self.assertEqual(evidence[5]['camera_xyz_m'],result['joints'][0]['camera_xyz_m'])
        self.assertEqual(evidence[9]['image_reason'],'outside_frame')
        self.assertEqual(evidence[9]['depth_status'],'not_attempted')
        self.assertEqual(evidence[11]['image_reason'],'low_response')
        self.assertIsNone(evidence[11]['camera_xyz_m'])

    def test_no_supported_image_joints_retains_omissions_without_inventing_body(self):
        for joint in self.pose['joints']:
            joint.update(status='uncertain',reason='low_response',pixel=None)
        result=DepthProjector().project(self.value,[self.pose])[0]
        self.assertEqual(result['joints'],[])
        self.assertEqual(len(result['joint_observations']),17)
        self.assertTrue(all(j['depth_status']=='not_attempted' and j['camera_xyz_m'] is None
                            for j in result['joint_observations']))

    def test_observations_are_owned_by_the_exact_instance_and_do_not_alias_input(self):
        other=deepcopy(self.pose);other['instance_id']='another-frame-local-person'
        other['joints'][0].update(pixel=None,score=.1,status='uncertain',reason='low_response')
        result=DepthProjector().project(self.value,[other,self.pose])
        self.assertEqual([p['instance_id'] for p in result],[other['instance_id'],self.pose['instance_id']])
        self.assertEqual(result[0]['joint_observations'][0]['image_status'],'uncertain')
        self.assertEqual(result[1]['joint_observations'][0]['image_status'],'supported')
        result[1]['joint_observations'][0]['pixel'][0]=0
        self.assertEqual(self.pose['joints'][0]['pixel'],[320,180])


class CameraTests(unittest.TestCase):
    def setUp(self):
        self.now=[100.];self.owner=SimpleNamespace(lock=threading.Lock(),session='camera-epoch',clock_sample_generation=0,
            calibration={'session':'camera-epoch','eeprom':{}},clock_pairs=[dict(source_before_mono_ns=99_000_000_000,
                source_after_mono_ns=99_000_002_000,source_ns=99_000_000_000,wall_after_mono_ns=99_000_002_000)])
        self.camera=FastCamera(self.owner,lambda f:f.meta,lambda _:b'jpeg',clock=lambda:self.now[0])
    def offer(self,n,depth=1000):
        frames={}
        for name,shape,dtype in [('rgb',(540,640),np.uint8),('depth',(400,640),np.uint16)]:
            array=np.full(shape,depth if name=='depth' else 0,dtype=dtype)
            meta=dict(sequence=n,host_monotonic_timestamp_seconds=self.now[0]-.01,
                device_timestamp_seconds=self.now[0]-.01,width=640,height=360 if name=='rgb' else 400,
                transformation_valid=True,intrinsics=[[1,0,0],[0,1,0],[0,0,1]],source_distortion_coefficients=[0]*8)
            frames[name]=SimpleNamespace(meta=meta,getFrame=lambda a=array:a)
        self.camera.offer(frames);return frames
    def test_latest_only_and_bounded_exact_depth_frames(self):
        first=None
        for n in range(12):
            self.offer(n,1000+n);frame=self.camera.frame();first=first or frame;self.now[0]+=.01
        self.assertEqual(len(self.camera.retained),8)
        with self.assertRaisesRegex(ValueError,'depth_frame_mismatch'):
            self.camera.project(dict(frame_key=first['frame_key'],identity=first['identity'],poses=[]))
        self.assertEqual(frame['identity']['sequence'],11)
        self.assertNotIn('depth',frame);self.assertNotIn('nv12',frame)
    def test_frame_carries_its_immutable_depth_without_a_return_trip(self):
        self.offer(1,1234);frame=self.camera.frame()
        self.assertEqual(frame['schema'],'home-fast-camera-2')
        from fast_tracking_depth import decode_paired_depth_frame
        with patch('bundle_ingest.calibration_content_id',return_value=frame['identity']['calibration_content_id']):
            value=decode_paired_depth_frame(frame)
        self.assertTrue(np.all(value['depth']==1234))
        self.assertFalse(value['depth'].flags.writeable)
        altered=deepcopy(frame);altered['paired_depth']['encoded_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'depth_integrity'):decode_paired_depth_frame(altered)

    def test_decompression_bomb_and_cross_session_depth_are_refused(self):
        import zlib
        from fast_tracking_depth import decode_paired_depth_frame
        self.offer(1);frame=self.camera.frame()
        altered=deepcopy(frame);raw=zlib.compress(bytes(512001),1)
        altered['paired_depth'].update(data_base64=base64.b64encode(raw).decode(),encoded_sha256=sha(raw))
        with self.assertRaisesRegex(ValueError,'depth_integrity'):decode_paired_depth_frame(altered)
        altered=deepcopy(frame);altered['calibration']['session']='different-camera-session'
        with self.assertRaisesRegex(ValueError,'camera_binding'):decode_paired_depth_frame(altered)
        altered=deepcopy(frame);altered['projection_metadata']['frames']['depth']['sequence']+=1
        with self.assertRaisesRegex(ValueError,'camera_binding'):decode_paired_depth_frame(altered)

    def test_source_restart_and_expiry_revoke_old_depth(self):
        self.offer(1);frame=self.camera.frame();self.owner.session='replacement'
        with self.assertRaisesRegex(ValueError,'source_changed'):
            self.camera.project(dict(frame_key=frame['frame_key'],identity=frame['identity'],poses=[]))
        self.owner.session='camera-epoch';self.now[0]+=1
        with self.assertRaisesRegex(ValueError,'expired'):self.camera.frame()
    def test_freezes_arrays_and_rejects_incompatible_sync(self):
        frames=self.offer(1);frames['depth'].getFrame()[:]=42
        self.assertTrue(np.all(self.camera.latest['depth']==1000))
        frames['depth'].meta['device_timestamp_seconds']-=.1;self.camera.offer(frames)
        with self.assertRaisesRegex(ValueError,'unavailable'):self.camera.frame()


class PumpTests(unittest.TestCase):
    def setUp(self):
        self.pack=dict(content_revision='pack',registration_version=1,frame_sha256='a'*64,collision_sha256='b'*64)
        self.scene=dict(siteId='victoria',contentRevision='pack',registrationVersion=1,frameDigest='a'*64,collisionDigest='b'*64,cameraId='den')
        self.doc=dict(pack=self.pack,camera={'calibration_content_id':'c'*64,'capture_epoch':'epoch'},matrix=np.eye(4).tolist())
        self.identity=dict(epoch='epoch',sequence=1,calibration_content_id='c'*64);self.key=digest(self.identity)
        self.frame=dict(schema='home-fast-camera-1',identity=self.identity,frame_key=self.key,source_interval_s=[99.98,99.981],
            camera_reply_s=100.,jpeg_sha256='d'*64,jpeg_b64='eA==')
        self.calls=[]
        class Camera:
            def call(inner,path,value=None):
                self.calls.append((path,value))
                if path.endswith('/frame'):return deepcopy(self.frame),100.,100.04
                return dict(schema='home-fast-depth-1',frame_key=self.key,identity=self.identity,
                    instances=[dict(instance_id='local',joints=[dict(id=5,camera_xyz_m=[1,2,3])])]),100.06,100.10
            def close(inner):pass
        self.receiver=SimpleNamespace(profile={'engine_epoch':'model'},execute=lambda *a,**k:dict(terminal=True,state='completed',
            result={'frame_id':self.key,'pose_instances':[]}))
        config=dict(camera={},read_token_sha256='a'*64,expected_camera_epoch='epoch',calibration_content_id='c'*64,scene=self.scene,alignment={})
        with patch('fast_tracking_runtime.load_tracking_alignment',return_value=({'pack':self.pack},SimpleNamespace(document=self.doc))):
            self.pump=TrackingPump(self.receiver,config,lambda:True,camera=Camera(),clock=lambda:100.11)
    def test_camera_model_depth_publication_without_recording_video_or_llms(self):
        self.assertTrue(self.pump.tick());packet=self.pump.snapshot()
        self.assertEqual([x[0] for x in self.calls],['/tracking/frame','/tracking/project'])
        self.assertEqual(packet['frame_key'],self.key)
        self.assertEqual(packet['people'][0]['joints'][0]['position_m'],[1,2,3])
        self.assertEqual(packet['source_la_monotonic_interval_s'],[99.98,100.021])
        self.assertFalse(packet['physical_permission']);self.assertFalse(packet['lighting_authority'])
        self.assertFalse(self.pump.tick())
        self.assertEqual(len(self.calls),3)  # repeated observation never reexecutes
    def test_paired_frame_projects_on_la_without_a_camera_callback(self):
        self.frame['schema']='home-fast-camera-2'
        value=dict(depth='immutable-depth',metadata={},calibration={},identity=self.identity)
        self.pump.local_projector=SimpleNamespace(project=lambda v,p:[dict(instance_id='local',joints=[dict(id=5,camera_xyz_m=[1,2,3])])])
        with patch('fast_tracking_runtime.decode_paired_depth_frame',return_value=value,create=True):
            self.assertTrue(self.pump.tick())
        self.assertEqual([x[0] for x in self.calls],['/tracking/frame'])
        self.assertEqual(self.pump.snapshot()['people'][0]['joints'][0]['position_m'],[1,2,3])

    def test_optional_joint_diagnostics_are_bound_to_the_original_frame_without_body_authority(self):
        self.frame['schema']='home-fast-camera-2'
        fixture=JointObservationTests();fixture.setUp()
        instances=DepthProjector().project(fixture.value,[fixture.pose])
        self.pump.local_projector=SimpleNamespace(project=lambda v,p:instances)
        with patch('fast_tracking_runtime.decode_paired_depth_frame',return_value=fixture.value), \
                patch.dict('os.environ',{'DEN_FAST_TRACKING_BODY_DIAGNOSTICS':'1'}):
            self.assertTrue(self.pump.tick())
        packet=self.pump.snapshot();evidence=packet['pose_observations']
        self.assertEqual(evidence['schema'],'home-body-observations-1')
        self.assertEqual(evidence['frame_key'],packet['frame_key'])
        self.assertEqual(evidence['association'],'frame_scoped_detection')
        self.assertEqual(evidence['point_semantics'],'visible_depth_surface_not_anatomical_joint_center')
        self.assertEqual(evidence['people'][0]['instance_id'],packet['people'][0]['instance_id'])
        self.assertEqual(len(evidence['people'][0]['joints']),17)
        self.assertEqual(evidence['people'][0]['joints'][0]['depth_reason'],'no_depth_support')
        self.assertNotIn('body_pose',packet);self.assertNotIn('track_ref',evidence['people'][0])
        self.assertEqual({j['id'] for j in packet['people'][0]['joints']},{5,6})
        self.assertEqual(packet['expires_monotonic_s'],packet['source_la_monotonic_interval_s'][0]+.75)
        packet['pose_observations']['people'][0]['joints'][0]['pixel'][0]=0
        self.assertEqual(instances[0]['joint_observations'][0]['pixel'],[320,180])

    def test_joint_diagnostics_are_opt_in_and_do_not_enter_the_journal_trace(self):
        self.frame['schema']='home-fast-camera-2'
        fixture=JointObservationTests();fixture.setUp()
        instances=DepthProjector().project(fixture.value,[fixture.pose])
        self.pump.local_projector=SimpleNamespace(project=lambda v,p:instances)
        with patch('fast_tracking_runtime.decode_paired_depth_frame',return_value=fixture.value), \
                patch.dict('os.environ',{'DEN_FAST_TRACKING_BODY_DIAGNOSTICS':'0'}), \
                patch('fast_tracking_runtime.tracking_trace') as trace:
            self.assertTrue(self.pump.tick())
        self.assertNotIn('pose_observations',self.pump.snapshot())
        # Coordinates are never added to the structured trace's extra fields.
        for call in trace.call_args_list:
            self.assertNotIn('pixel',str(call.kwargs))
            self.assertNotIn('camera_xyz_m',str(call.kwargs))

    def test_enabled_diagnostics_do_not_log_image_or_room_coordinates(self):
        self.frame['schema']='home-fast-camera-2'
        fixture=JointObservationTests();fixture.setUp()
        instances=DepthProjector().project(fixture.value,[fixture.pose])
        self.pump.local_projector=SimpleNamespace(project=lambda v,p:instances)
        captured=io.StringIO()
        with patch('fast_tracking_runtime.decode_paired_depth_frame',return_value=fixture.value), \
                patch.dict('os.environ',{'DEN_FAST_TRACKING_BODY_DIAGNOSTICS':'1','DEN_FAST_TRACKING_TRACE':'1'}), \
                redirect_stdout(captured):
            self.assertTrue(self.pump.tick())
        self.assertIn('pose_observations',self.pump.snapshot())
        self.assertIn('worker_published',captured.getvalue())
        for private_field in ('pixel','camera_xyz_m','position_m','joint_observations','pose_observations'):
            self.assertNotIn(private_field,captured.getvalue())

    def test_corrupt_paired_depth_cannot_invoke_a_model(self):
        self.frame['schema']='home-fast-camera-2';executions=[]
        self.receiver.execute=lambda *a,**k:executions.append(a)
        with patch('fast_tracking_runtime.decode_paired_depth_frame',side_effect=ValueError('tracking_depth_integrity'),create=True):
            with self.assertRaisesRegex(ValueError,'depth_integrity'):self.pump.tick()
        self.assertEqual(executions,[])

    def test_mismatched_depth_and_source_changes_never_publish(self):
        self.frame['identity']['epoch']='other';self.frame['frame_key']=digest(self.frame['identity'])
        with self.assertRaisesRegex(ValueError,'binding_changed'):self.pump.tick()
        self.assertEqual(self.pump.snapshot()['status'],'unavailable')
    def test_capture_prefetch_is_one_latest_slot(self):
        self.pump.capture_thread=object()
        self.pump.offered=(deepcopy(self.frame),100.,100.04)
        self.assertTrue(self.pump.tick());self.assertIsNone(self.pump.offered)
        self.assertFalse(self.pump.tick())
    def test_time_waiting_in_prefetch_cannot_extend_original_expiry_or_invoke_model(self):
        calls=[]
        self.receiver.execute=lambda *a,**k:calls.append(a)
        self.pump.clock=lambda:101.
        with self.assertRaisesRegex(ValueError,'original_expired'):self.pump.tick()
        self.assertEqual(calls,[])


class JPEGTests(unittest.TestCase):
    def test_jpeg_is_opt_in_and_keeps_durable_admitted_exact_frame_execution(self):
        from test_remote_receiver import ReceiverTests
        fixture=ReceiverTests();fixture.setUp()
        try:
            receiver,calls=fixture.composite()
            body=fixture.envelope['body'];buffer=io.BytesIO();Image.new('RGB',(100,100)).save(buffer,format='JPEG');raw=buffer.getvalue()
            body['image'].update(encoding='jpeg',data_base64=base64.b64encode(raw).decode(),encoded_sha256=sha(raw),bgr_sha256=None)
            fixture.envelope['body_sha256']=digest(body)
            with self.assertRaisesRegex(ValueError,'image contract'):receiver.execute(fixture.envelope,source_site='victoria')
            receiver.viewing_jpeg=True
            proof=receiver.execute(fixture.envelope,source_site='victoria')
            self.assertEqual(proof['state'],'completed');self.assertEqual(len(calls),1)
            self.assertEqual(receiver.execute(fixture.envelope,source_site='victoria'),proof);self.assertEqual(len(calls),1)
            payload=fixture.store.db.execute('SELECT payload FROM remote_inputs').fetchone()[0]
            self.assertNotIn('data_base64',payload)
        finally:fixture.tearDown()


class TrackingHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_viewers_do_not_starve_clock_and_short_reads_remain_bounded(self):
        token='a'*40
        pump=SimpleNamespace(config={'read_token_sha256':hashlib.sha256(token.encode()).hexdigest()},attempt='b'*32)
        app=TrackingHTTP(None,pump);app.clients=2
        scope={'type':'http','scheme':'https','method':'GET','path':'/v1/tracking/clock',
            'headers':[(b'authorization',('Bearer '+token).encode())],'query_string':b''}
        out=[]
        async def send(message):out.append(message)
        await app(scope,None,send)
        self.assertEqual(out[0]['status'],200);self.assertEqual(app.readers,0)
        app.readers=2;out.clear();await app(scope,None,send);self.assertEqual(out[0]['status'],429)
        app.readers=0;out.clear();scope['path']='/v1/tracking/stream'
        await app(scope,None,send);self.assertEqual(out[0]['status'],429)


if __name__=='__main__':unittest.main()
