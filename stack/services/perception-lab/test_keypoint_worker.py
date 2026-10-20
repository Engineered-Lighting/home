import unittest
import numpy as np
from unittest.mock import patch
from types import SimpleNamespace
from contextlib import nullcontext
from keypoint_worker import KeypointWorker,run_sdk
from keypoint_loader import CHECKPOINT_SHA256,TORCH_VERSION,VISION_VERSION
import test_keypoint_reference as reference_fixtures


class KeypointWorkerTests(unittest.TestCase):
    def setUp(self):
        self.manifest={"checkpoint":"KeypointRCNN_ResNet50_FPN_Weights.COCO_V1","sha256":CHECKPOINT_SHA256,
            "torch_version":TORCH_VERSION,"torchvision_version":VISION_VERSION,"precision":"fp32","device":"cpu",
            "person_threshold":.1,"joint_threshold":2,"license":"fixture-review","model_version":"coco-v1",
            "profile_id":"reference","device_id":"fixture-cpu","engine_epoch":"e1"}
        self.calls=[]
        self.worker=KeypointWorker(self.manifest,lambda *args:True,
            loader=lambda *_:self.fail("unexpected load"),
            invoke=lambda *args:(self.calls.append(args) or reference_fixtures.ReferenceTests().raw()))
        self.image=np.zeros((100,100,3),dtype=np.uint8)

    def test_construction_and_unloaded_calls_never_start_sdk(self):
        self.assertEqual(self.calls,[])
        with self.assertRaisesRegex(ValueError,"not loaded"):
            self.worker.infer(self.image,frame_id="f",request_id="r")
        with self.assertRaises(ValueError):
            KeypointWorker({**self.manifest,"person_threshold":.5},lambda *_:True)
        self.assertEqual(self.calls,[])

    def test_denied_first_inference_does_not_invoke_model(self):
        self.worker.model=object()
        self.worker.authorize=lambda *_:False
        with self.assertRaisesRegex(ValueError,"not admitted"):
            self.worker.infer(self.image,frame_id="f",request_id="r")
        self.assertEqual(self.calls,[])

    def test_registered_execution_keeps_provenance_and_rejects_wrong_frame(self):
        self.worker.model=object()
        frame={"site_id":"victoria","camera_id":"den","session_id":"s","capture_epoch":"c","frame_id":"f","media_ids":["m"]}
        request={"request_id":"r","frame":frame,"profile":{**self.manifest,"artifact_sha256":CHECKPOINT_SHA256}}
        result=self.worker.execute(request,lambda _: (self.image,{**frame,"media_id":"m"}))
        self.assertTrue(result["terminal"])
        self.assertEqual(result["result"]["processing"]["tracking"],"not_applied")
        self.assertEqual(result["result"]["pose_instances"][0]["joints"][0]["score"],4.2)
        with self.assertRaisesRegex(ValueError,"another observation"):
            self.worker.execute(request,lambda _: (self.image,{**frame,"site_id":"echo","media_id":"m"}))
        self.assertEqual(len(self.calls),1)

    def test_sdk_preprocessing_matches_incumbent_rgb_chw_float_contract(self):
        class Tensor:
            def __init__(self,data): self.data=data
            def permute(self,*axes): self.data=self.data.transpose(axes); return self
            def float(self): self.data=self.data.astype(np.float32); return self
            def div_(self,value): self.data/=value; return self
            def to(self,device): self.device=device; return self
        fake=SimpleNamespace(from_numpy=Tensor,inference_mode=nullcontext)
        image=np.zeros((2,3,3),dtype=np.uint8); image[:]=[0,128,255]
        def model(inputs):
            self.assertEqual(len(inputs),1)
            value=inputs[0]
            self.assertEqual(value.data.shape,(3,2,3))
            self.assertEqual(value.data.dtype,np.float32)
            np.testing.assert_array_equal(value.data[0],np.ones((2,3)))
            np.testing.assert_array_equal(value.data[2],np.zeros((2,3)))
            self.assertEqual(value.device,"cpu")
            return [{"fixture":"received"}]
        with patch.dict('sys.modules',{'torch':fake}):
            self.assertEqual(run_sdk(model,image,"cpu"),{"fixture":"received"})
        np.testing.assert_array_equal(image[0,0],[0,128,255])


if __name__=='__main__': unittest.main()
