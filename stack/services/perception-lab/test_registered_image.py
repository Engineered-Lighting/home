import io
import unittest
import test_paired_runtime as fixtures
from registered_image import RegisteredImage
from ultralytics_worker import image_shape
from ultralytics_worker import UltralyticsWorker
from runtime import PairedRunner
from test_ultralytics_worker import Tensor
import test_ultralytics_worker as worker_fixtures
from types import SimpleNamespace as Obj
from pathlib import Path


class RegisteredImageTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown
    frame=fixtures.PairedRuntimeTests.frame
    settle=fixtures.PairedRuntimeTests.settle

    def image(self,**changes):
        from PIL import Image
        encoded=io.BytesIO()
        Image.new("RGB",(2,2),(255,20,5)).save(encoded,format="PNG")
        frame=self.frame()
        meta={k:frame[k] for k in ("site_id","camera_id","frame_id","capture_epoch","observed_at")}
        item=self.store.add_media(self.session["id"],encoded.getvalue(),"image/png",dict(meta,stream="rgb",width=2,height=2,**changes))
        return dict(frame,media_ids=[item["id"]]),item

    def test_real_png_decode_and_rgb_to_bgr(self):
        frame,item=self.image()
        image,meta=RegisteredImage(self.store)(frame)
        self.assertEqual(image_shape(image),(2,2))
        self.assertEqual(image[0,0].tolist(),[5,20,255])
        self.assertEqual(meta["media_id"],item["id"])

    def test_wrong_frame_and_corrupt_object_rejected(self):
        frame,item=self.image()
        with self.assertRaises(ValueError): RegisteredImage(self.store)(dict(frame,frame_id="another"))
        self.store.media_path(self.session["id"],item["id"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError,"integrity"): RegisteredImage(self.store)(frame)

    def test_encoded_dimensions_and_ambiguous_selection_rejected(self):
        frame,item=self.image()
        second=self.store.add_media(self.session["id"],b"fixture","image/jpeg",item["metadata"])
        with self.assertRaisesRegex(ValueError,"unambiguous"):
            RegisteredImage(self.store)(dict(frame,media_ids=[item["id"],second["id"]]))
        with self.store.transaction() as db:
            db.execute("UPDATE media SET metadata=json_set(metadata,'$.width',10) WHERE id=?",(item["id"],))
        with self.assertRaisesRegex(ValueError,"dimensions"):
            RegisteredImage(self.store)(frame)

    def test_registered_pixels_through_paired_workers_to_stored_overlays(self):
        frame,_=self.image()
        path=Path(self.directory.name)/"model.pt"
        path.write_bytes(b"fixture artifact, not model weights")
        class Model:
            def predict(self,**kwargs):
                image=kwargs["source"]
                return [Obj(orig_shape=image.shape[:2],names={0:"book"},
                    boxes=Obj(xyxy=Tensor([[0,0,1,1]]),conf=Tensor([.8]),cls=Tensor([0])),
                    masks=None,speed={})]
        workers={}
        profiles={}
        for arm in ("a","b"):
            manifest=worker_fixtures.WorkerTests().manifest(profile_id=arm)
            workers[arm]=UltralyticsWorker(manifest,lambda *_:True,loader=lambda *_:Model())
            workers[arm].load(path)
            profiles[arm]={k:manifest[k] for k in ("profile_id","model_version","engine_epoch","device_id")}
            profiles[arm]["artifact_sha256"]=manifest["sha256"]
        resolver=RegisteredImage(self.store)
        runner=PairedRunner(self.store,self.session["id"],profiles,
            lambda request:workers[request["arm"]].execute(request,resolver),clock=self.store.clock)
        self.runners.append(runner)
        runner.offer(frame)
        self.settle(lambda:all(v is None for v in runner.snapshot()["active"].values()))
        outputs=[e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.output"]
        self.assertEqual(len(outputs),2)
        for value in outputs:
            self.assertEqual(value["arm_snapshot"]["observations"],["book"])
            self.assertTrue(value["arm_snapshot"]["layers"]["boxes"]["available"])
            self.assertNotIn("judgment",value)


if __name__=="__main__": unittest.main()
