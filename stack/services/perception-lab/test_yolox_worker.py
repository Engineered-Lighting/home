import unittest
import numpy as np
from yolox_worker import YoloXWorker,normalize_result
from yolox_loader import CHECKPOINT_SHA256


class YoloXWorkerTests(unittest.TestCase):
    def setUp(self):
        self.manifest={'checkpoint':'yolox_l.onnx','sha256':CHECKPOINT_SHA256,
            'runtime_version':'0.0.16','onnxruntime_version':'1.23.2','precision':'fp32','device':'cpu',
            'scope':'full_frame_detector','score_threshold':.45,'nms_threshold':.45,
            'license':'fixture-review','model_version':'yolox-l-reference','profile_id':'reference',
            'device_id':'fixture-cpu','engine_epoch':'e1'}
        self.calls=[]; calls=self.calls
        class Detector:
            COCO_CLASSES=['person']+['class-'+str(i) for i in range(1,80)]
            def __call__(self,image):
                calls.append((self,image.copy()))
                return np.array([[1,2,90,95]]),np.array([0])
        self.detector=Detector()
        self.worker=YoloXWorker(self.manifest,lambda *_:True,loader=lambda *_:self.detector)
        self.image=np.zeros((100,100,3),dtype=np.uint8)

    def test_no_implicit_loading_or_unadmitted_execution(self):
        with self.assertRaisesRegex(ValueError,'not loaded'):
            self.worker.infer(self.image,frame_id='f',request_id='r')
        self.worker.model=self.detector
        self.worker.authorize=lambda *_:False
        with self.assertRaisesRegex(ValueError,'not admitted'):
            self.worker.infer(self.image,frame_id='f',request_id='r')
        self.assertEqual(self.calls,[])
        with self.assertRaisesRegex(ValueError,'manifest'):
            YoloXWorker({**self.manifest,'scope':'complete_incumbent'},lambda *_:True)

    def test_registered_frame_and_profile_identity_before_model_execution(self):
        self.worker.model=self.detector
        frame={'site_id':'victoria','session_id':'s','camera_id':'den','capture_epoch':'c',
               'frame_id':'f','media_ids':['m']}
        request={'request_id':'r','frame':frame,'profile':{**self.manifest,'artifact_sha256':CHECKPOINT_SHA256}}
        loader=lambda _: (self.image,{**frame,'media_id':'m'})
        result=self.worker.execute(request,loader)
        self.assertEqual(result['state'],'completed')
        output=result['result']
        self.assertNotIn('confidence',output['boxes'][0])
        self.assertEqual(output['objects'][0]['label'],'person')
        self.assertEqual(output['processing']['scope'],'full_frame_detector')
        self.assertEqual(output['processing']['crop_scheduling'],'not_applied')
        self.assertIsNone(output['processing']['runtime_threads'])  # Unknown is not assumed.
        self.detector.runtime_threads={'intra_op':4,'inter_op':1}
        request['request_id']='r2'
        self.assertEqual(self.worker.execute(request,loader)['result']['processing']['runtime_threads'],{'intra_op':4,'inter_op':1})
        request['request_id']='r'
        with self.assertRaisesRegex(ValueError,'another observation'):
            self.worker.execute(request,lambda _:(self.image,{**frame,'frame_id':'old','media_id':'m'}))
        request['profile']['engine_epoch']='old'
        with self.assertRaisesRegex(ValueError,'profile mismatch'): self.worker.execute(request,loader)
        self.assertEqual(len(self.calls),2)

    def test_clipping_and_invalid_outputs_are_explicit(self):
        result=normalize_result(([[-5,2,50,110],[120,0,140,30]],[1,2]),
            self.detector.COCO_CLASSES,frame_id='f',width=100,height=100)
        self.assertEqual(result['quality']['clipped_boxes'],1)
        self.assertEqual(result['quality']['outside_frame_boxes'],1)
        self.assertEqual(result['boxes'][0]['x'],0)
        self.assertEqual(result['boxes'][0]['h'],98)
        for raw in [([[0,0,2,2]],[80]),([[0,0,float('nan'),2]],[0]),([[2,0,1,2]],[0]),([], [0])]:
            with self.assertRaises(ValueError):
                normalize_result(raw,self.detector.COCO_CLASSES,frame_id='f',width=100,height=100)
        empty=normalize_result((np.array([]),np.array([])),self.detector.COCO_CLASSES,
            frame_id='f',width=100,height=100)
        self.assertEqual(empty['objects'],[])


if __name__=='__main__': unittest.main()
