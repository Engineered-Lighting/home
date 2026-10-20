import io
import unittest
from PIL import Image
import test_paired_runtime as fixtures
import test_yolox_worker as detector_fixtures
from shared_detector import SharedDetectorCrops


class SharedDetectorTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown
    runner=fixtures.PairedRuntimeTests.runner
    settle=fixtures.PairedRuntimeTests.settle
    receipt=fixtures.PairedRuntimeTests.receipt

    def test_uncertain_shared_stage_is_not_repeated_by_other_arm_or_new_resolver(self):
        detector=detector_fixtures.YoloXWorkerTests();detector.setUp()
        calls=[]
        def fail(image):calls.append(1);raise TimeoutError('fixture detector outcome unknown')
        detector.worker.model=fail
        def resolver():return SharedDetectorCrops(self.store,detector.worker,lambda *_:True,
            minimum_confidence=None,max_people=4,policy_revision='test')
        cropper=resolver()
        frame={'site_id':'victoria','session_id':self.session['id'],'camera_id':'den',
               'capture_epoch':'c1','frame_id':'f1','observed_at':99,'available_at':99,'valid_until':110}
        image=io.BytesIO();Image.new('RGB',(100,100)).save(image,format='PNG')
        media=self.store.add_media(self.session['id'],image.getvalue(),'image/png',{
            **frame,'width':100,'height':100,'stream':'rgb'})
        frame['media_ids']=[media['id']]
        def execute(request):
            cropper(request['frame']);return self.receipt(request)
        runner=self.runner(execute);runner.offer(frame)
        self.settle(lambda:len(calls)==1 and all(r[0]=='indeterminate' for r in self.store.db.execute('SELECT state FROM paired_requests')))
        # Simulate an explicitly retained parent reservation to exercise the
        # reconstructed resolver's durable stage lookup, not a fresh inference.
        with self.store.transaction() as db:
            db.execute("UPDATE paired_requests SET state='running' WHERE arm='a'")
        with self.assertRaisesRegex(ValueError,'completion unproven'):resolver()(frame)
        self.assertEqual(calls,[1])


if __name__=='__main__':unittest.main()
