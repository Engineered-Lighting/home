"""Production resident-model composition exercised with fake SDK I/O only."""
from copy import deepcopy
import io
import threading
import time
from types import SimpleNamespace
import unittest

from PIL import Image
from reasoning import digest
from rtmpose_worker import RTMPoseWorker, CHECKPOINT, SOURCE_FILES
from ultralytics_worker import UltralyticsWorker


class Tensor:
    def __init__(self, value): self.value = value
    def cpu(self): return self
    def tolist(self): return self.value


def model_fixtures():
    detector_manifest = {'runtime_version': '8.4.0', 'task': 'detect', 'checkpoint': 'yolo26s',
        'precision': 'fp32', 'sha256': 'a' * 64, 'license': 'AGPL-3.0', 'device_id': 'fixture-cpu',
        'device': 'cpu', 'engine_epoch': 'detector-epoch', 'profile_id': 'detector-local',
        'model_version': 'fixture-detector', 'format': 'pt',
        'preprocessing': {'imgsz': [640, 640], 'confidence': .25, 'iou': .7, 'max_det': 8, 'retina_masks': False}}
    pose_manifest = {'checkpoint': CHECKPOINT, 'runtime_version': '0.0.16', 'precision': 'fp32',
        'input_size': [192, 256], 'layout': 'coco17', 'format': 'onnx', 'sha256': 'b' * 64,
        'source_sha256': {name: 'c' * 64 for name in SOURCE_FILES}, 'license': 'Apache-2.0',
        'model_version': 'fixture-pose', 'profile_id': 'pose-local', 'device_id': 'fixture-cpu',
        'engine_epoch': 'pose-epoch', 'onnxruntime_version': 'fixture', 'device': 'cpu',
        'score_threshold': .4, 'max_people': 2}
    calls = []
    boxes = [[10, 20, 50, 90]]
    class DetectorSDK:
        def predict(self, **kwargs):
            calls.append(('detector', kwargs['source'].copy()))
            return [SimpleNamespace(orig_shape=(360, 640), names={0: 'person'},
                boxes=SimpleNamespace(xyxy=Tensor(deepcopy(boxes)), conf=Tensor([.9] * len(boxes)),
                                      cls=Tensor([0.] * len(boxes))), speed={})]
    def pose_sdk(image, bboxes):
        calls.append(('pose', image.copy(), deepcopy(bboxes)))
        return [[[20, 30]] * 17 for _ in bboxes], [[.8] * 17 for _ in bboxes]
    detector = UltralyticsWorker(detector_manifest, lambda *_: True)
    pose = RTMPoseWorker(pose_manifest, lambda *_: True)
    # These are external SDK objects only. No artifact file or model is loaded.
    detector.model, pose.model = DetectorSDK(), pose_sdk
    return detector, pose, calls, boxes


def jpeg():
    stream = io.BytesIO()
    Image.new('RGB', (640, 360), (30, 20, 10)).save(stream, format='JPEG')
    return stream.getvalue()


class LocalModelStageTests(unittest.TestCase):
    def stages(self, detector, pose):
        from live_acquisition_models import LocalModelStages
        return LocalModelStages(detector, pose,
            detector_ref={'id': 'detector-local', 'digest': digest(detector.manifest)},
            pose_ref={'id': 'pose-local', 'digest': digest(pose.manifest)}, person_threshold=.5)

    def test_actual_resident_workers_use_same_frame_and_qualified_person_proposal(self):
        from live_acquisition_models import LocalModelStages
        detector, pose, calls, _ = model_fixtures()
        stages = LocalModelStages(detector, pose,
            detector_ref={'id': 'detector-local', 'digest': digest(detector.manifest)},
            pose_ref={'id': 'pose-local', 'digest': digest(pose.manifest)}, person_threshold=.5)
        options = {'authorized': lambda: True, 'deadline': 103, 'cancel': lambda: False, 'clock': lambda: 101}
        detected = stages.detect(jpeg(), frame_id='capture-7', request_id='request-detector', **options)
        result = stages.pose(detected, frame_id='capture-7', request_id='request-pose', **options)
        self.assertEqual([call[0] for call in calls], ['detector', 'pose'])
        self.assertEqual(calls[1][2], [[10, 20, 50, 90]])
        self.assertEqual(result['frame_id'], 'capture-7')
        self.assertEqual(result['available_at'], 101)
        self.assertEqual(result['result']['pose_instances'][0]['instance_id'], 'capture-7:0')
        self.assertEqual(result['result']['pose_instances'][0]['joints'][0]['pixel'], [20, 30])
        self.assertEqual(result['proposals'][0]['detector_artifact_sha256'], 'a' * 64)
        self.assertEqual(result['model_ref'], {'id': 'pose-local', 'digest': digest(pose.manifest)})
        self.assertEqual(calls[0][1][0, 0].tolist(), [10, 20, 30])
        self.assertEqual(calls[1][1][0, 0].tolist(), [30, 20, 10])

    def test_stalled_sdk_refuses_by_deadline_and_does_not_overlap_or_deliver_late(self):
        detector, pose, calls, _ = model_fixtures()
        entered, release = threading.Event(), threading.Event()
        predict = detector.model.predict
        def stalled(**kwargs):
            entered.set()
            release.wait(1)
            return predict(**kwargs)
        detector.model.predict = stalled
        stages = self.stages(detector, pose)
        timer = threading.Timer(.3, release.set)
        timer.start()
        started = time.monotonic()
        options = {'authorized': lambda: True, 'deadline': started + .06, 'cancel': lambda: False, 'clock': time.monotonic}
        try:
            with self.assertRaisesRegex(ValueError, 'model_capture_expired'):
                stages.detect(jpeg(), frame_id='capture-7', request_id='stalled', **options)
            self.assertLess(time.monotonic() - started, .2)
            self.assertTrue(entered.is_set())
            self.assertTrue(stages.busy())
            self.assertFalse(stages.clear())
            with self.assertRaises(ValueError):
                stages.detect(jpeg(), frame_id='capture-8', request_id='overlap', **{**options, 'deadline': time.monotonic()+1})
        finally:
            release.set()
            timer.join()
        for _ in range(50):
            if not stages.busy(): break
            time.sleep(.01)
        self.assertFalse(stages.busy())
        self.assertTrue(stages.clear())
        self.assertEqual([call[0] for call in calls], ['detector'])

    def test_empty_completed_detector_is_not_failure_or_full_frame_pose(self):
        detector, pose, calls, boxes = model_fixtures()
        boxes.clear()
        stages = self.stages(detector, pose)
        options = {'authorized': lambda: True, 'deadline': 103, 'cancel': lambda: False, 'clock': lambda: 101}
        detected = stages.detect(jpeg(), frame_id='empty', request_id='empty-detector', **options)
        result = stages.pose(detected, frame_id='empty', request_id='empty-pose', **options)
        self.assertEqual(result['proposals'], [])
        self.assertEqual(result['result']['pose_instances'], [])
        self.assertFalse(result['result']['processing']['model_invoked'])
        self.assertEqual([call[0] for call in calls], ['detector'])

    def test_revocation_after_detector_blocks_pose_and_clears_pending_image(self):
        detector, pose, calls, _ = model_fixtures()
        stages = self.stages(detector, pose)
        granted = [True]
        options = {'authorized': lambda: granted[0], 'deadline': 103, 'cancel': lambda: False, 'clock': lambda: 101}
        detected = stages.detect(jpeg(), frame_id='one', request_id='one-detector', **options)
        granted[0] = False
        with self.assertRaisesRegex(ValueError, 'local_model_not_authorized'):
            stages.pose(detected, frame_id='one', request_id='one-pose', **options)
        self.assertTrue(stages.clear())
        self.assertEqual([call[0] for call in calls], ['detector'])

    def test_model_failure_is_unavailable_not_empty_and_never_calls_pose(self):
        detector, pose, calls, _ = model_fixtures()
        def broken(**_): raise RuntimeError('fixture SDK failed')
        detector.model.predict = broken
        stages = self.stages(detector, pose)
        options = {'authorized': lambda: True, 'deadline': 103, 'cancel': lambda: False, 'clock': lambda: 101}
        with self.assertRaisesRegex(ValueError, 'resident_model_execution_failed'):
            stages.detect(jpeg(), frame_id='one', request_id='one-detector', **options)
        self.assertTrue(stages.clear())
        self.assertEqual(calls, [])

    def test_frame_handle_cannot_be_reused_or_attached_to_another_frame(self):
        detector, pose, calls, _ = model_fixtures()
        stages = self.stages(detector, pose)
        options = {'authorized': lambda: True, 'deadline': 103, 'cancel': lambda: False, 'clock': lambda: 101}
        detected = stages.detect(jpeg(), frame_id='one', request_id='one-detector', **options)
        with self.assertRaisesRegex(ValueError, 'detector_frame_mismatch'):
            stages.pose(detected, frame_id='other', request_id='other-pose', **options)
        with self.assertRaisesRegex(ValueError, 'detector_result_not_owned'):
            stages.pose(detected, frame_id='one', request_id='one-pose', **options)
        self.assertEqual([call[0] for call in calls], ['detector'])

    def test_people_budget_refuses_crowd_without_truncation(self):
        detector, pose, calls, boxes = model_fixtures()
        boxes.extend([[60, 20, 100, 90], [110, 20, 150, 90]])
        stages = self.stages(detector, pose)
        options = {'authorized': lambda: True, 'deadline': 103, 'cancel': lambda: False, 'clock': lambda: 101}
        with self.assertRaisesRegex(ValueError, 'person_proposal_budget_exceeded'):
            stages.detect(jpeg(), frame_id='crowd', request_id='crowd-detector', **options)
        self.assertEqual([call[0] for call in calls], ['detector'])

    def test_changed_resident_manifest_cannot_dispatch_under_old_reference(self):
        detector, pose, calls, _ = model_fixtures()
        stages = self.stages(detector, pose)
        detector.manifest['preprocessing']['confidence'] = .1
        options = {'authorized': lambda: True, 'deadline': 103, 'cancel': lambda: False, 'clock': lambda: 101}
        with self.assertRaisesRegex(ValueError, 'resident_model_manifest_changed'):
            stages.detect(jpeg(), frame_id='one', request_id='one-detector', **options)
        self.assertEqual(calls, [])

    def test_returned_metadata_cannot_change_original_pose_proposals(self):
        detector, pose, calls, _ = model_fixtures()
        stages = self.stages(detector, pose)
        options = {'authorized': lambda: True, 'deadline': 103, 'cancel': lambda: False, 'clock': lambda: 101}
        detected = stages.detect(jpeg(), frame_id='one', request_id='one-detector', **options)
        changed = detected.metadata()
        changed['proposals'][0]['xyxy'] = [0, 0, 640, 360]
        result = stages.pose(detected, frame_id='one', request_id='one-pose', **options)
        self.assertEqual(calls[1][2], [[10, 20, 50, 90]])
        self.assertEqual(result['proposals'][0]['xyxy'], [10, 20, 50, 90])


if __name__ == '__main__': unittest.main()
