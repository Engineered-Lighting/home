"""Fixed local resident detector/pose stages without Lab recording storage.

Construction neither loads models nor contacts devices. The acquisition owner
supplies exact qualified JPEG bytes, a current local grant and an original
capture deadline. Worker admission remains independently enforced by the
existing resident adapters.
"""
from copy import deepcopy
import io
import threading
import time

from reasoning import digest, finite
from rtmpose_worker import RTMPoseWorker
from ultralytics_worker import UltralyticsWorker


class ModelStageRefusal(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class DetectionStage:
    """Read-only metadata handle; its original image stays within the owner."""
    def __init__(self, metadata):
        self._metadata = deepcopy(metadata)

    def metadata(self):
        return deepcopy(self._metadata)


class LocalModelStages:
    """One local in-flight native call and one immutable detection handle.

    References use ``reasoning.digest(full_manifest)``. ``available_at`` and
    ``deadline`` share the caller's clock (the acquisition graph supplies a
    monotonic clock and separately records UTC availability). They never
    replace capture observation time. ``clear()`` returning False means a
    native call is still running; its container must remain resource bounded.
    """
    def __init__(self, detector, pose, *, detector_ref, pose_ref, person_threshold, max_people=2):
        if (not isinstance(detector, UltralyticsWorker) or not isinstance(pose, RTMPoseWorker)
                or not finite(person_threshold) or not 0 <= person_threshold <= 1
                or type(max_people) is not int or not 1 <= max_people <= 2
                or max_people > pose.manifest['max_people']):
            raise ValueError('fixed resident detector, pose and bounded proposal policy required')
        self.detector, self.pose_worker = detector, pose
        self.detector_manifest, self.pose_manifest = deepcopy(detector.manifest), deepcopy(pose.manifest)
        self.pose_preprocessing = (pose.model_input_channel_order, pose.preprocessing_revision)
        for ref, manifest in ((detector_ref, self.detector_manifest), (pose_ref, self.pose_manifest)):
            if ref != {'id': manifest['profile_id'], 'digest': digest(manifest)}:
                raise ValueError('model reference must pin its complete resident manifest')
        self.detector_ref, self.pose_ref = deepcopy(detector_ref), deepcopy(pose_ref)
        self.person_threshold, self.max_people = person_threshold, max_people
        self.lock = threading.Lock()
        self.native_lock = threading.Lock()
        self.native = None
        self.pending = None

    def _check(self, *, authorized, deadline, cancel, clock):
        if not callable(authorized) or not callable(clock):
            raise ModelStageRefusal('local_model_authority_unavailable')
        cancelled = cancel() if callable(cancel) else cancel.is_set()
        if cancelled or authorized() is not True:
            raise ModelStageRefusal('local_model_not_authorized')
        now = clock()
        if not finite(now) or not finite(deadline) or now >= deadline:
            raise ModelStageRefusal('model_capture_expired')
        if self.detector.manifest != self.detector_manifest or self.pose_worker.manifest != self.pose_manifest:
            raise ModelStageRefusal('resident_model_manifest_changed')
        if (self.pose_worker.model_input_channel_order, self.pose_worker.preprocessing_revision) != self.pose_preprocessing:
            raise ModelStageRefusal('resident_model_preprocessing_changed')
        return now

    def _invoke(self, function, options):
        """Bound waiting, while retaining the occupied slot until SDK return.

        Native SDK execution cannot be cancelled safely. A timed-out call is
        never retried or overlapped, and its eventual output is discarded.
        The process/container resource limit remains the hard memory boundary.
        """
        remaining = options['deadline'] - self._check(**options)
        wall_deadline = time.monotonic() + min(remaining, 120)
        with self.native_lock:
            if self.native is not None and not self.native['done'].is_set():
                raise ModelStageRefusal('local_model_busy')
            task = {'done': threading.Event(), 'discard': False}
            self.native = task
        def run():
            try:
                self._check(**options)
                result = function()
                with self.native_lock:
                    if not task['discard']: task['result'] = result
            except BaseException as error:
                with self.native_lock:
                    if not task['discard']: task['error'] = error
            finally:
                task['done'].set()
        thread = threading.Thread(target=run, name='home-local-model', daemon=True)
        thread.start()
        try:
            while True:
                self._check(**options)
                if time.monotonic() >= wall_deadline:
                    raise ModelStageRefusal('model_capture_expired')
                if task['done'].wait(.02): break
            self._check(**options)
            if 'error' in task:
                raise ModelStageRefusal('resident_model_execution_failed') from task['error']
            return task['result']
        except BaseException:
            with self.native_lock:
                task['discard'] = True
                task.pop('result', None)
                task.pop('error', None)
            raise
        finally:
            if task['done'].is_set():
                with self.native_lock:
                    if self.native is task: self.native = None

    def _processing(self, result, manifest, kind):
        processing = result.get('processing')
        if kind == 'detector':
            expected = {'runtime': 'ultralytics', 'runtime_version': manifest['runtime_version'],
                        'artifact_sha256': manifest['sha256'], 'checkpoint': manifest['checkpoint'],
                        'precision': manifest['precision'], 'preprocessing': manifest['preprocessing'],
                        'input_format': 'BGR_uint8', 'device_id': manifest['device_id'],
                        'engine_epoch': manifest['engine_epoch']}
        else:
            expected = {'runtime': 'rtmlib', 'runtime_version': manifest['runtime_version'],
                        'artifact_sha256': manifest['sha256'], 'checkpoint': manifest['checkpoint'],
                        'source_sha256': manifest['source_sha256'], 'onnxruntime_version': manifest['onnxruntime_version'],
                        'precision': manifest['precision'], 'input_size_wh': manifest['input_size'],
                        'layout': manifest['layout'], 'device_id': manifest['device_id'],
                        'score_threshold': manifest['score_threshold'],
                        'model_input_channel_order': self.pose_preprocessing[0],
                        'preprocessing_revision': self.pose_preprocessing[1]}
        if not isinstance(processing, dict) or any(processing.get(k) != v for k, v in expected.items()):
            raise ModelStageRefusal(kind + '_processing_identity_mismatch')

    def detect(self, jpeg, *, frame_id, request_id, authorized, deadline, cancel, clock):
        options = dict(authorized=authorized, deadline=deadline, cancel=cancel, clock=clock)
        if not self.lock.acquire(blocking=False):
            raise ModelStageRefusal('local_model_busy')
        try:
            if self.pending is not None:
                raise ModelStageRefusal('detector_result_pending')
            self._check(**options)
            import numpy as np
            from PIL import Image
            if not isinstance(jpeg, bytes) or not 0 < len(jpeg) <= 256 * 1024:
                raise ModelStageRefusal('bounded_capture_jpeg_required')
            with Image.open(io.BytesIO(jpeg)) as decoded:
                if decoded.format != 'JPEG' or decoded.size != (640, 360) or decoded.mode not in {'RGB', 'L'}:
                    raise ModelStageRefusal('capture_jpeg_shape_invalid')
                image = np.ascontiguousarray(np.asarray(decoded.convert('RGB'))[:, :, ::-1])
            self._check(**options)
            result = self._invoke(lambda: self.detector.infer(image, frame_id=frame_id, request_id=request_id), options)
            available_at = self._check(**options)
            if (not isinstance(result, dict) or result.get('schema_version') != 1 or result.get('frame_id') != frame_id
                    or result.get('coordinate_space') != 'source_pixels'
                    or not isinstance(result.get('boxes'), list) or len(result['boxes']) > 128):
                raise ModelStageRefusal('detector_result_invalid')
            self._processing(result, self.detector_manifest, 'detector')
            proposals = []
            instance_ids = set()
            for box in result['boxes']:
                if not isinstance(box, dict):
                    raise ModelStageRefusal('person_proposal_invalid')
                if box.get('label') != 'person':
                    continue
                if (not finite(box.get('confidence')) or not 0 <= box['confidence'] <= 1
                        or not all(finite(box.get(k)) for k in ('x', 'y', 'w', 'h'))):
                    raise ModelStageRefusal('person_proposal_invalid')
                if box['confidence'] < self.person_threshold:
                    continue
                x, y, w, h = (box[k] for k in ('x', 'y', 'w', 'h'))
                if not 0 <= x < x + w <= 640 or not 0 <= y < y + h <= 360:
                    raise ModelStageRefusal('person_proposal_invalid')
                instance_id = box.get('instance_id')
                if not isinstance(instance_id, str) or not 1 <= len(instance_id) <= 128 or instance_id in instance_ids:
                    raise ModelStageRefusal('person_proposal_identity_invalid')
                instance_ids.add(instance_id)
                proposals.append({'id': box['instance_id'], 'frame_id': frame_id, 'xyxy': [x, y, x + w, y + h],
                                  'detector_version': self.detector_manifest['model_version'],
                                  'detector_artifact_sha256': self.detector_manifest['sha256']})
            if len(proposals) > self.max_people:
                raise ModelStageRefusal('person_proposal_budget_exceeded')
            metadata = {'frame_id': frame_id, 'request_id': request_id, 'model_ref': self.detector_ref,
                        'available_at': available_at, 'proposals': proposals, 'result': result}
            handle = DetectionStage(metadata)
            self.pending = (handle, image, deepcopy(metadata))
            return handle
        finally:
            self.lock.release()

    def pose(self, detection, *, frame_id, request_id, authorized, deadline, cancel, clock):
        options = dict(authorized=authorized, deadline=deadline, cancel=cancel, clock=clock)
        if not self.lock.acquire(blocking=False):
            raise ModelStageRefusal('local_model_busy')
        try:
            if self.pending is None or self.pending[0] is not detection:
                raise ModelStageRefusal('detector_result_not_owned')
            _, image, metadata = self.pending
            self.pending = None
            if metadata['frame_id'] != frame_id:
                raise ModelStageRefusal('detector_frame_mismatch')
            self._check(**options)
            result = self._invoke(lambda: self.pose_worker.infer(image, deepcopy(metadata['proposals']), frame_id=frame_id, request_id=request_id), options)
            available_at = self._check(**options)
            if (not isinstance(result, dict) or result.get('schema_version') != 1
                    or result.get('frame_id') != frame_id or result.get('coordinate_space') != 'source_pixels'):
                raise ModelStageRefusal('pose_result_invalid')
            self._processing(result, self.pose_manifest, 'pose')
            proposals = metadata['proposals']
            instances = result.get('pose_instances')
            if (not isinstance(instances, list) or len(instances) != len(proposals)
                    or any(not isinstance(instance, dict) or instance.get('instance_id') != proposal['id']
                           or instance.get('crop_xyxy') != proposal['xyxy']
                           for instance, proposal in zip(instances, proposals))
                    or result['processing'].get('crop_set_digest') != digest(proposals)
                    or result['processing'].get('crop_count') != len(proposals)
                    or result['processing'].get('model_invoked') is not bool(proposals)):
                raise ModelStageRefusal('pose_proposal_identity_mismatch')
            return {'frame_id': frame_id, 'request_id': request_id, 'model_ref': deepcopy(self.pose_ref),
                    'detector_ref': deepcopy(self.detector_ref), 'available_at': available_at,
                    'proposals': deepcopy(metadata['proposals']), 'result': result}
        finally:
            self.lock.release()

    def clear(self):
        """Forget an unconsumed image after the acquisition owner stops dispatch."""
        if not self.lock.acquire(blocking=False):
            return False
        try:
            with self.native_lock:
                if self.native is not None and not self.native['done'].is_set():
                    return False
                self.native = None
            self.pending = None
            return True
        finally:
            self.lock.release()

    def busy(self):
        with self.native_lock:
            return self.lock.locked() or self.native is not None and not self.native['done'].is_set()
