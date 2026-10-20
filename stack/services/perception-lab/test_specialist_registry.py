import io
import unittest
from PIL import Image
import test_paired_runtime as fixtures
import test_keypoint_worker as worker_fixtures
import test_yolox_worker as yolox_fixtures
from runtime import PairedRunner
from specialist_registry import RegisteredSpecialists


class SpecialistRegistryTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown
    settle=fixtures.PairedRuntimeTests.settle

    def test_registered_png_reaches_both_paired_arms_through_incumbent_adapter(self):
        f=worker_fixtures.KeypointWorkerTests(); f.setUp()
        f.worker.model=object()  # Explicit test double; never invokes Torch.
        self.paired_png(f)

    def test_registered_png_reaches_both_arms_through_yolox_adapter(self):
        f=yolox_fixtures.YoloXWorkerTests(); f.setUp()
        f.worker.model=f.detector  # Explicit test double; no ONNX execution.
        self.paired_png(f)

    def paired_png(self,f):
        executor=RegisteredSpecialists(self.store,[f.worker])
        self.assertEqual(f.calls,[])
        profile={**f.manifest,"artifact_sha256":f.manifest["sha256"]}
        frame={"site_id":"victoria","session_id":self.session["id"],"camera_id":"den",
            "capture_epoch":"c1","frame_id":"f1","observed_at":99,"available_at":99,"valid_until":110}
        image=io.BytesIO();Image.new("RGB",(100,100),(255,128,0)).save(image,format="PNG")
        media=self.store.add_media(self.session["id"],image.getvalue(),"image/png",{
            **frame,"width":100,"height":100,"stream":"rgb","session_time_s":0})
        frame["media_ids"]=[media["id"]]
        runner=PairedRunner(self.store,self.session["id"],{"a":profile,"b":profile},executor,clock=self.store.clock)
        self.runners.append(runner)
        runner.offer(frame)
        self.settle(lambda:len(f.calls)==2 and all(v is None for v in runner.snapshot()["active"].values()))
        outputs=[e["payload"] for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.output"]
        self.assertEqual({e["arm"] for e in outputs},{"a","b"})
        self.assertTrue(all(e["frame_id"]=="f1" and e["status"]=="completed" for e in outputs))
        self.assertEqual(f.calls[0][1][0,0].tolist(),[0,128,255])
        self.assertEqual(f.calls[1][1][0,0].tolist(),[0,128,255])

    def test_unknown_duplicate_and_changed_bindings_fail_without_inference(self):
        f=worker_fixtures.KeypointWorkerTests(); f.setUp()
        with self.assertRaisesRegex(ValueError,"duplicate"):
            RegisteredSpecialists(self.store,[f.worker,f.worker])
        executor=RegisteredSpecialists(self.store,[f.worker])
        with self.assertRaisesRegex(ValueError,"not provisioned"):
            executor({"profile":{"profile_id":"caller-selected-path"}})
        f.worker.manifest["device"]="cuda:0"
        with self.assertRaisesRegex(ValueError,"manifest changed"):
            executor({"profile":{"profile_id":f.manifest["profile_id"]}})
        self.assertEqual(f.calls,[])


if __name__=='__main__': unittest.main()
