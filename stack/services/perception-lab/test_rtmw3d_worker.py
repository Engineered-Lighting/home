import unittest
import numpy as np
from rtmw3d_worker import RTMW3DWorker,CHECKPOINT,SHA256,SOURCE_SHA256
from reasoning import digest,canonical


class RTMWTests(unittest.TestCase):
    def manifest(self):
        return {'checkpoint':CHECKPOINT,'sha256':SHA256,'source_sha256':SOURCE_SHA256,
            'runtime_version':'0.0.16','onnxruntime_version':'1.23.2','precision':'fp32','device':'cpu',
            'input_size':[288,384],'layout':'coco_wholebody133','format':'onnx','score_threshold':.5,
            'max_people':4,'license':'pending-review-fixture','model_version':'rtmw3d-reference',
            'profile_id':'rtmw','device_id':'fixture','engine_epoch':'fixture','input_channel_order':'BGR'}

    def setUp(self):
        self.worker=RTMW3DWorker(self.manifest(),lambda *_:True,loader=lambda *_:self.fail('unexpected SDK load'))
        self.calls=[];self.pixels=[]
        self.xyz=np.full((1,133,3),20.0);self.xyz[0,11,2]=10;self.xyz[0,12,2]=14
        self.xy=np.full((1,133,2),30.0);self.scores=np.full((1,133),1.2)
        def model(image,bboxes):
            self.calls.append(bboxes);self.pixels.append(image)
            return self.xyz,self.scores,np.zeros((1,133,3)),self.xy
        self.worker.model=model
        self.crops=[{'id':'person','frame_id':'f','detector_version':'humanart','xyxy':[0,0,100,100]}]

    def infer(self):return self.worker.infer(np.zeros((100,100,3),dtype=np.uint8),self.crops,frame_id='f',request_id='r')

    def test_preserves_wholebody_without_claiming_measured_geometry(self):
        result=self.infer()
        self.assertEqual(len(result['pose_instances'][0]['joints']),133)
        self.assertEqual(result['pose_instances'][0]['joints'][0]['relative_z_model_units'],8)
        self.assertEqual(result['quality']['metric_depth'],'not_measured')
        self.assertEqual(result['quality']['room_position'],'not_available')
        self.assertEqual(result['processing']['input_size_wh'],[288,384])
        self.assertEqual(result['processing']['checkpoint'],CHECKPOINT)
        self.assertNotIn('confidence',result['pose_instances'][0]['joints'][0])
        canonical(result)

    def test_incumbent_bgr_pixels_and_applied_session_threads_are_reported(self):
        from yolox_loader import construct
        from types import SimpleNamespace
        ort=SimpleNamespace(SessionOptions=SimpleNamespace,get_available_providers=lambda:['CPUExecutionProvider'],
            InferenceSession=lambda path,**kw:SimpleNamespace(get_providers=lambda:['CPUExecutionProvider'],disable_fallback=lambda:None))
        tool=construct(ort,type('Tool',(),{}),'local.onnx','cpu')
        self.worker.model.runtime_threads=tool.runtime_threads
        image=np.zeros((100,100,3),dtype=np.uint8);image[:]=[10,20,30]
        result=self.worker.infer(image,self.crops,frame_id='f',request_id='r')
        self.assertEqual(self.pixels[0][0,0].tolist(),[10,20,30])  # Unconverted BGR, as RTMPose3d expects.
        self.assertIsNot(self.pixels[0],image)
        self.assertTrue(self.pixels[0].flags.c_contiguous)
        processing=result['processing']
        self.assertEqual((processing['input_format'],processing['model_input_channel_order'],processing['preprocessing_revision']),
                         ('BGR_uint8','BGR','rtmw3d-bgr-v1'))
        self.assertEqual(processing['runtime_threads'],{'intra_op':4,'inter_op':1})
        del self.worker.model.runtime_threads
        self.assertIsNone(self.infer()['processing']['runtime_threads'])
        with self.assertRaises(ValueError):
            RTMW3DWorker({**self.manifest(),'input_channel_order':'RGB'},lambda *_:True)
        missing=self.manifest();del missing['input_channel_order']
        with self.assertRaises(ValueError):RTMW3DWorker(missing,lambda *_:True)

    def test_uncertain_hips_and_nonfinite_joints_do_not_make_depth(self):
        self.scores[0,11]=.1;self.xy[0,4,0]=np.nan
        result=self.infer()
        self.assertTrue(all(j['relative_z_model_units'] is None for j in result['pose_instances'][0]['joints']))
        self.assertIsNone(result['pose_instances'][0]['joints'][4]['pixel'])
        canonical(result)

    def test_empty_person_set_never_falls_back_to_full_frame(self):
        self.crops=[];result=self.infer()
        self.assertEqual(self.calls,[])
        self.assertFalse(result['processing']['model_invoked'])
        self.assertEqual(result['pose_instances'],[])

    def test_registered_execution_preserves_crop_digest(self):
        frame={'site_id':'victoria','camera_id':'den','session_id':'s','capture_epoch':'c','frame_id':'f','media_ids':['m']}
        bundle={**frame,'crops':self.crops,'crop_set_digest':digest(self.crops)}
        proof=self.worker.execute({'request_id':'r','profile':{**self.manifest(),'artifact_sha256':SHA256},'frame':frame},
            lambda _: (np.zeros((100,100,3),dtype=np.uint8),{**frame,'media_id':'m'}),lambda _:bundle)
        self.assertEqual(proof['artifact_sha256'],SHA256)
        self.assertEqual(proof['result']['processing']['crop_set_digest'],digest(self.crops))


if __name__=='__main__':unittest.main()
