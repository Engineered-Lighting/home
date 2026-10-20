import base64
from copy import deepcopy
import hashlib
import io
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image

from fast_tracking_camera import FastCamera,encoded,sha
from fast_tracking_runtime import TrackingPump,TrackingHTTP
from reasoning import digest


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


if __name__=='__main__':unittest.main()
