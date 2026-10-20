import unittest
import numpy as np
from humanart_worker import HumanArtWorker,CHECKPOINT_SHA256
import test_yolox_worker as fixtures


class HumanArtTests(unittest.TestCase):
    def manifest(self):
        f=fixtures.YoloXWorkerTests();f.setUp()
        return {**f.manifest,'checkpoint':'yolox_m_humanart_detector.onnx',
                'sha256':CHECKPOINT_SHA256,'scope':'full_frame_person_detector','score_threshold':.7}

    def test_person_only_boxes_keep_missing_confidence_and_correct_artifact(self):
        calls=[]
        worker=HumanArtWorker(self.manifest(),lambda *_:True,loader=lambda *_:self.fail('unexpected load'))
        self.assertEqual(calls,[])
        worker.model=lambda image:(calls.append(image.copy()) or np.array([[1,2,90,95]]))
        frame={'site_id':'victoria','camera_id':'den','session_id':'s','frame_id':'f','capture_epoch':'c','media_ids':['m']}
        receipt=worker.execute({'request_id':'r','profile':{**worker.manifest,'artifact_sha256':CHECKPOINT_SHA256},'frame':frame},
            lambda _: (np.zeros((100,100,3),dtype=np.uint8),{**frame,'media_id':'m'}))
        self.assertEqual(receipt['artifact_sha256'],CHECKPOINT_SHA256)
        result=receipt['result']
        self.assertEqual(result['boxes'][0]['label'],'person')
        self.assertNotIn('confidence',result['boxes'][0])
        self.assertEqual(result['processing']['score_threshold'],.7)
        self.assertEqual(result['processing']['detector_mode'],'human')
        self.assertEqual(len(calls),1)

    def test_wrong_weights_threshold_and_unloaded_worker_are_refused(self):
        for changes in ({'sha256':'0'*64},{'score_threshold':.45},{'scope':'full_frame_detector'}):
            with self.assertRaises(ValueError):HumanArtWorker({**self.manifest(),**changes},lambda *_:True)
        worker=HumanArtWorker(self.manifest(),lambda *_:True)
        with self.assertRaisesRegex(ValueError,'not loaded'):
            worker.infer(np.zeros((2,2,3),dtype=np.uint8),frame_id='f',request_id='r')


if __name__=='__main__':unittest.main()
