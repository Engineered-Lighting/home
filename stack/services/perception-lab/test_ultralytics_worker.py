import hashlib
from pathlib import Path
from types import SimpleNamespace as Obj
import tempfile
import unittest
from unittest.mock import patch

from ultralytics_worker import UltralyticsWorker, normalize_result, image_shape, load_sdk
import test_paired_runtime as fixtures


class Tensor:
    def __init__(self,value): self.value=value
    def cpu(self): return self
    def tolist(self): return self.value


def result():
    return Obj(orig_shape=(360,640),names={0:"person",1:"book"},
        boxes=Obj(xyxy=Tensor([[10,20,50,90],[90,40,130,60]]),conf=Tensor([.9,.8]),cls=Tensor([0.,1.])),
        masks=Obj(xy=[Tensor([[10,20],[50,20],[50,90]]),Tensor([[90,40],[130,40],[130,60]])]),
        speed={"preprocess":1,"inference":4,"postprocess":2})


class WorkerTests(unittest.TestCase):
    def manifest(self,**kw):
        return {"runtime_version":"8.4.0","task":"detect","checkpoint":"yolo26s",
            "precision":"fp32","sha256":hashlib.sha256(b"fixture artifact, not model weights").hexdigest(),
            "license":"AGPL-3.0","device_id":"fixture-cpu","device":"cpu","engine_epoch":"epoch1",
            "profile_id":"detector-a","model_version":"fixture","format":"pt",
            "preprocessing":{"imgsz":[640,640],"confidence":.25,"iou":.7,"max_det":100,"retina_masks":False},**kw}

    def test_boxes_and_masks_preserve_source_coordinates_and_association(self):
        value=normalize_result(result(),frame_id="f1",width=640,height=360,task="segment")
        self.assertEqual(value["boxes"][0]["w"],40)
        self.assertEqual(value["boxes"][0]["confidence"],.9)
        self.assertEqual(value["masks"][1]["instance_id"],value["objects"][1]["instance_id"])
        self.assertEqual(value["masks"][0]["polygons"][0][0],[10,20])
        self.assertNotIn("keypoints",value)
        self.assertNotIn("activity",value)
        detection=normalize_result(result(),frame_id="f1",width=640,height=360,task="detect")
        self.assertNotIn("masks",detection)

    def test_invalid_masks_do_not_destroy_boxes_or_fake_complete_contours(self):
        raw=result()
        raw.masks.xy[0]=Tensor([[1,2]]*2049)
        value=normalize_result(raw,frame_id="f",width=640,height=360,task="segment")
        self.assertEqual(len(value["boxes"]),2)
        self.assertNotIn("masks",value)
        self.assertIn("masks",value["quality"])

    def test_source_mismatch_and_invalid_class_confidence_rejected(self):
        with self.assertRaises(ValueError):
            normalize_result(result(),frame_id="f",width=1280,height=720,task="detect")
        raw=result(); raw.boxes.conf=Tensor([float("nan"),.8])
        with self.assertRaises(ValueError): normalize_result(raw,frame_id="f",width=640,height=360,task="detect")
        raw=result(); raw.boxes.xyxy=Tensor([[10,20,641,90],[90,40,130,60]])
        with self.assertRaises(ValueError): normalize_result(raw,frame_id="f",width=640,height=360,task="detect")

    def test_construct_and_denied_load_never_import_sdk_or_fetch_weights(self):
        calls=[]
        worker=UltralyticsWorker(self.manifest(),lambda *_:False,loader=lambda *args:calls.append(args))
        self.assertEqual(calls,[])
        with self.assertRaises(ValueError): worker.load("https://example.invalid/model.pt")
        self.assertEqual(calls,[])
        with patch.dict("os.environ",{"YOLO_AUTOINSTALL":"true"}):
            with self.assertRaises(ValueError): load_sdk(Path("model.pt"),"detect")

    def test_real_load_infer_seam_with_fake_sdk_and_explicit_warmup_admission(self):
        calls,admission=[],[]
        class Model:
            def predict(self,**kwargs):
                calls.append(kwargs)
                return [result()]
        def authorize(phase,manifest,request):
            admission.append(phase)
            return True
        worker=UltralyticsWorker(self.manifest(),authorize,loader=lambda *_:Model())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"model.pt"; path.write_bytes(b"fixture artifact, not model weights")
            worker.load(path)
        with patch("ultralytics_worker.image_shape",return_value=(360,640)):
            image=object()  # SDK and image-shape boundary mocked; no inference.
            first=worker.infer(image,frame_id="f1",request_id="r1")
            worker.infer(image,frame_id="f2",request_id="r2")
        self.assertEqual(admission,["load","load","infer_with_warmup","infer"])
        self.assertIs(calls[0]["source"],image)
        self.assertFalse(calls[0]["rect"])
        self.assertFalse(calls[0]["save"])
        self.assertFalse(calls[0]["half"])
        self.assertEqual(first["processing"]["runtime_version"],"8.4.0")
        self.assertEqual(first["timings_ms"]["inference"],4)

    def test_corrupt_artifact_is_not_loaded(self):
        calls=[]
        worker=UltralyticsWorker(self.manifest(),lambda *_:True,loader=lambda *args:calls.append(args))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"model.pt"; path.write_bytes(b"wrong")
            with self.assertRaises(ValueError): worker.load(path)
        self.assertEqual(calls,[])

    def test_urls_and_camera_ids_are_not_input_arrays(self):
        for source in (0,"rtsp://camera","https://example.invalid/image",Path("x.jpg"),None):
            with self.subTest(source=source),self.assertRaises(ValueError): image_shape(source)

    def test_masks_are_quantized_and_only_redundant_vertices_dropped(self):
        from ultralytics_worker import _polygon
        self.assertEqual(_polygon([[1.04,2.06],[1.01,2.1],[3.0,2.1],[5.0,2.1],[5.0,7.0]]),[[1.0,2.1],[5.0,2.1],[5.0,7.0]])
        # A zero-width protrusion changes the outline, so its tip is kept.
        self.assertEqual(_polygon([[0,0],[4,0],[2,0],[2,3]]),[[0.0,0.0],[4.0,0.0],[2.0,0.0],[2.0,3.0]])
        self.assertEqual(len(_polygon([[1,1],[1.01,1.01],[1.02,1.02]])),3)  # Degenerate stays a 3-point outline.
        value=normalize_result(result(),frame_id="f1",width=640,height=360,task="segment")
        self.assertEqual(value["quality"]["mask_coordinate_precision_px"],.1)

    def test_warmup_is_recorded_once_the_model_ran_even_if_normalization_refuses(self):
        admission=[]
        class Model:
            def __init__(self):self.bad=True
            def predict(self,**kwargs):
                value=result()
                if self.bad:value.boxes.conf=Tensor([float("nan"),.8]);self.bad=False
                return [value]
        worker=UltralyticsWorker(self.manifest(),lambda phase,*_:admission.append(phase) or True)
        worker.model=Model()
        with patch("ultralytics_worker.image_shape",return_value=(360,640)):
            with self.assertRaises(ValueError):worker.infer(object(),frame_id="f1",request_id="r1")
            worker.infer(object(),frame_id="f2",request_id="r2")
        self.assertEqual(admission,["infer_with_warmup","infer"])

    def test_common_admission_receipt_binds_profile_and_registered_frame(self):
        worker=UltralyticsWorker(self.manifest(),lambda *_:True)
        frame={"frame_id":"f1","capture_epoch":"c1","site_id":"victoria","camera_id":"den","session_id":"s1","media_ids":["m1"]}
        profile={"artifact_sha256":worker.manifest["sha256"],"engine_epoch":"epoch1","model_version":"fixture","profile_id":"detector-a","device_id":"fixture-cpu"}
        request={"request_id":"r1","profile":profile,"frame":frame}
        with patch.object(worker,"infer",return_value={"objects":[]}) as infer:
            value=worker.execute(request,lambda _:(object(),dict(frame,media_id="m1")))
            self.assertTrue(value["terminal"])
            self.assertEqual(value["request_id"],"r1")
            infer.assert_called_once()
            with self.assertRaises(ValueError):
                worker.execute(request,lambda _:(object(),dict(frame,site_id="echo",media_id="m1")))
            with self.assertRaises(ValueError):
                worker.execute(dict(request,profile=dict(profile,engine_epoch="wrong")),lambda _:None)


class DenseMaskReceiptTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def dense(self):
        import math
        boxes,polygons=[],[]
        for i in range(128):
            x,y=(i%16)*6+1.123456789012345,(i//16)*12+1.987654321098765
            boxes.append([x,y,x+4.5,y+9.5])
            polygons.append(Tensor([[x+2.25+2*math.cos(k*math.pi/32),y+4.75+4*math.sin(k*math.pi/32)] for k in range(64)]))
        return Obj(orig_shape=(100,100),names={0:"person"},masks=Obj(xy=polygons),speed={},
            boxes=Obj(xyxy=Tensor(boxes),conf=Tensor([.5+i/1000 for i in range(128)]),cls=Tensor([0.]*128)))

    def execute(self):
        import base64,io
        import numpy as np
        from PIL import Image
        from reasoning import canonical,digest
        from remote_receiver import RemoteReceiver
        raw=self.dense()
        # Unrounded contours alone exceed the result bound that once made this indeterminate.
        self.assertGreater(len(canonical([p.value for p in raw.masks.xy])),256*1024)
        worker=UltralyticsWorker(WorkerTests().manifest(task="segment",checkpoint="yolo26s-seg"),lambda *_:True)
        worker.model=type("Model",(),{"predict":lambda _self,**kw:[raw]})()
        profile={**worker.manifest,"artifact_sha256":worker.manifest["sha256"]}
        receiver=RemoteReceiver(self.store,worker,profile,lambda *_:True)
        frame={"site_id":"victoria","camera_id":"den","session_id":"source-session","frame_id":"f1",
               "capture_epoch":"c1","valid_until":110,"media_ids":["m1"]}
        pixels=np.zeros((100,100,3),dtype=np.uint8)
        buffer=io.BytesIO();Image.fromarray(pixels).save(buffer,format="PNG");data=buffer.getvalue()
        body={"schema_version":1,"request":{"request_id":"dense1","profile":profile,"frame":frame},
              "image":{"encoding":"png","channel_order":"RGB","decoded_worker_order":"BGR","width":100,"height":100,
                  "source_media_id":"m1","encoded_sha256":hashlib.sha256(data).hexdigest(),
                  "bgr_sha256":hashlib.sha256(pixels.tobytes()).hexdigest(),"data_base64":base64.b64encode(data).decode()}}
        envelope={"request_id":"dense1","site_id":"victoria","camera_id":"den","session_id":"source-session",
                  "profile":profile,"body_sha256":digest(body),"body":body}
        proof=receiver.execute(envelope,source_site="victoria")
        state=self.store.db.execute("SELECT state FROM remote_execution WHERE id='dense1'").fetchone()[0]
        self.assertLessEqual(len(canonical(proof)),256*1024)
        return proof,state

    def test_dense_masks_complete_with_quantized_contours(self):
        proof,state=self.execute()
        self.assertEqual((proof["state"],state),("completed","terminal"))
        result=proof["result"]
        self.assertEqual((len(result["boxes"]),len(result["masks"])),(128,128))
        self.assertEqual(result["masks"][0]["instance_id"],result["boxes"][0]["instance_id"])
        self.assertTrue(all(round(v,1)==v for m in result["masks"] for p in m["polygons"][0] for v in p))
        self.assertEqual(result["boxes"][0]["x"],1.123456789012345)  # Boxes keep full precision.

    def test_masks_over_budget_are_dropped_but_boxes_complete(self):
        with patch("ultralytics_worker.MASK_RESULT_BUDGET",64*1024):
            proof,state=self.execute()
        self.assertEqual((proof["state"],state),("completed","terminal"))
        self.assertNotIn("masks",proof["result"])
        self.assertEqual(proof["result"]["quality"],{"masks":"exceeds_evidence_budget"})
        self.assertEqual(len(proof["result"]["boxes"]),128)


if __name__=="__main__": unittest.main()
